"""Milestone 2: parse LaTeX -> hierarchy, chuẩn hóa, khử trùng, BibTeX; đo hiệu năng.

Chạy từ thư mục gốc repo, sau khi M1 đã crawl xong (corpus ở --corpus-dir):
    python src/run_m2.py --corpus-dir /content/corpus --export

Mỗi bài (đọc từ <paper_key>/paper.json) đi qua 3 bước, theo thứ tự:
  1. bib     BibTeX: đọc .bib được \\bibliography/\\addbibresource nhắc tới và các \\bibitem trên đường biên dịch
             của từng version, khử trùng giữa các version (hợp nhất trường, chọn một khóa) và ghi
             bibtex_entries_raw.json, cite_map.json, bibtex_merged.json, candidates.json, m2_stats.json.
  2. tree    dựng <paper_key>/vN/tree_raw.json (m2_hierarchy.py).
  3. content làm sạch + khử trùng nội dung xuyên version -> elements.json, hierarchy.json,
             parse_status.json (m2_content_dedup.py). Dùng cite_map.json của bước 1 để đổi \\cite.
--export ghi thêm elements/hierarchy/bibtex.parquet vào <corpus>/export/; --export-only chỉ xuất Parquet
từ kết quả đã có (không parse lại, không ghi đè benchmark).

Lưu ý: file nguồn .tex/.bib của M1 KHÔNG bị sửa (trước đây \\cite bị ghi đè thẳng vào .tex).
Log hiệu năng: <corpus>/benchmark/m2_benchmark.json.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matching.m3_candidates import generate_candidates, normalize_authors, normalize_text  # noqa: E402
from parser import m2_content_dedup as D  # noqa: E402
from parser import m2_gather as G  # noqa: E402
from parser import m2_hierarchy as H  # noqa: E402
from parser.m2_bib_dedup import deduplicate_keys, rewrite_citations_with_count, to_bibtex_row  # noqa: E402
from parser.m2_bibitem import parse_bibitems, parse_bibtex_entries  # noqa: E402
from utils import config as C  # noqa: E402
from utils.benchmark import PerfRecorder  # noqa: E402
from utils.schemas import export_m2_parquet, validate_candidate_rows  # noqa: E402

ALL_STEPS = ("bib", "tree", "content")
_VERSION_DIR_RE = re.compile(r"^v(\d+)$")
_BIB_NAMES_RE = re.compile(r"\\bibliography\s*\{([^}]*)\}|\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def log(message: str) -> None:
    print(message, flush=True)


# ----------------------------------------------------------------------------- tiện ích
def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _corpus_dir() -> Path:
    candidate = C.DATA_DIR / "corpus"
    return candidate if candidate.exists() else C.DATA_DIR


def paper_arxiv_id(paper_dir: Path) -> str:
    """arXiv ID GỐC của bài (đọc paper.json). Tên thư mục (2602-04870, hep-th-9901001) không dùng làm ID
    vì không khôi phục được dấu '.' và '/'."""
    data = _read_json(paper_dir / "paper.json") or {}
    return data.get("arxiv_id") or paper_dir.name


def version_dirs(paper_dir: Path) -> List[Path]:
    dirs = [d for d in paper_dir.iterdir() if d.is_dir() and _VERSION_DIR_RE.match(d.name)]
    return sorted(dirs, key=lambda d: int(d.name[1:]))


def list_paper_dirs(root: Path) -> List[Path]:
    """Thư mục bài báo: có paper.json (M1) hoặc có thư mục vN."""
    return [d for d in sorted(root.iterdir())
            if d.is_dir() and ((d / "paper.json").exists() or any(_VERSION_DIR_RE.match(c.name)
                                                                  for c in d.iterdir() if c.is_dir()))]


def _load_references(paper_dir: Path) -> list[dict]:
    path = paper_dir / "refs.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("references", []) if isinstance(payload, dict) else []
    version_path = next(iter(sorted(paper_dir.glob("v*/references.json"))), None)
    if version_path is None:
        return []
    payload = json.loads(version_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


# ----------------------------------------------------------------------------- bước 1: BibTeX
def _compiled_text(vdir: Path) -> str:
    """Văn bản các file nằm trên đường biên dịch (đã bỏ comment); không tìm được gốc thì lấy mọi .tex."""
    root = G.find_root_file(vdir).root
    if root is not None:
        return H.expand_includes(vdir, root)
    return "\n".join(G.prepare_for_includes(G.read_tex(p)) for p in G.list_tex_files(vdir))


def _bib_files(vdir: Path, text: str) -> tuple[list[Path], bool]:
    """File .bib được \\bibliography / \\addbibresource nhắc tới. Không có gì khớp thì lấy mọi .bib
    của version (cờ fallback = True) vì crawler M1 chỉ giữ .tex/.bib."""
    available = {p.relative_to(vdir).as_posix(): p for p in sorted(vdir.rglob("*.bib"))}
    chosen: List[Path] = []
    for g1, g2 in _BIB_NAMES_RE.findall(text):
        for name in (g1 or g2).split(","):
            name = name.strip()
            while name.startswith("./"):
                name = name[2:]
            if not name:
                continue
            name = name if name.lower().endswith(".bib") else name + ".bib"
            match = available.get(name) or next(
                (p for rel, p in available.items() if rel.endswith("/" + name)), None)
            if match is not None and match not in chosen:
                chosen.append(match)
    if chosen:
        return chosen, False
    return list(available.values()), bool(available)


def collect_entries(paper_dir: Path, arxiv_id: str) -> tuple[list[dict], dict]:
    """Entry BibTeX của mọi version (từ .bib và \\bibitem) + văn bản biên dịch từng version."""
    entries: List[dict] = []
    info = {"texts": {}, "bib_entries": 0, "bibitem_entries": 0,
            "versions_with_bibliography": 0, "bib_fallback_versions": 0}
    for vdir in version_dirs(paper_dir):
        version = int(vdir.name[1:])
        if not any(vdir.rglob("*.tex")):  # version rỗng (M1: PDF-only...)
            continue
        text = _compiled_text(vdir)
        info["texts"][version] = text
        found: List[dict] = []
        bib_files, fallback = _bib_files(vdir, text)
        for bib_file in bib_files:
            found.extend(parse_bibtex_entries(bib_file.read_text(encoding="utf-8", errors="replace"),
                                              arxiv_id, version))
        info["bib_entries"] += len(found)
        bibitems = parse_bibitems(text, arxiv_id, version)
        info["bibitem_entries"] += len(bibitems)
        found.extend(bibitems)
        info["versions_with_bibliography"] += bool(found)
        info["bib_fallback_versions"] += int(fallback)
        entries.extend(found)
    return entries, info


def process_paper(paper_dir: Path) -> dict:
    """Xử lý BibTeX + sinh ứng viên cho một bài và ghi các file kết quả; trả về số liệu kiểm chứng được."""
    started = time.perf_counter()
    paper_dir = Path(paper_dir)
    arxiv_id = paper_arxiv_id(paper_dir)

    parse_started = time.perf_counter()
    entries, info = collect_entries(paper_dir, arxiv_id)
    merged, replacements, conflicts = deduplicate_keys(entries)
    rows = [to_bibtex_row(entry) for entry in merged]
    dedup_seconds = time.perf_counter() - parse_started
    if any(target not in {row["citation_key"] for row in rows} for target in replacements.values()):
        raise ValueError("citation replacement points to a missing canonical key")

    citation_occurrences = sum(
        rewrite_citations_with_count(text, replacements)[1] for text in info["texts"].values())

    # Chỉ reference có arXiv ID là ứng viên (đề M3: ma trận m x n với n = số reference có arXiv ID).
    references_all = _load_references(paper_dir)
    references = [ref for ref in references_all if ref.get("ref_arxiv_id")]
    candidate_started = time.perf_counter()
    candidate_diagnostics: dict[str, int] = {}
    candidates = generate_candidates(rows, references, diagnostics=candidate_diagnostics)
    validate_candidate_rows(candidates)
    candidate_seconds = time.perf_counter() - candidate_started

    per_entry: Dict[str, List[str]] = {row["citation_key"]: [] for row in rows}
    for candidate in candidates:
        per_entry.setdefault(candidate["citation_key"], []).append(candidate["candidate_id"])
    candidates_per_entry = {key: len(ids) for key, ids in per_entry.items()}
    candidate_counts = list(candidates_per_entry.values())
    exact_title_candidates = sum(bool(candidate.get("title_exact")) for candidate in candidates)

    _atomic_json(paper_dir / "bibtex_entries_raw.json", entries)
    _atomic_json(paper_dir / "cite_map.json", replacements)  # m2_content_dedup đọc file này để đổi \cite
    _atomic_json(paper_dir / "bibtex_conflicts.json", conflicts)
    _atomic_json(paper_dir / "bibtex_merged.json", rows)
    _atomic_json(paper_dir / "candidates.json", candidates)
    _atomic_json(paper_dir / "candidate_entry_index.json", {
        key: {"candidate_count": len(ids), "candidate_ids": ids} for key, ids in per_entry.items()})

    stats = {
        "arxiv_id": arxiv_id,
        "bibtex_entries_ingested": len(entries),
        "bibtex_from_bib_files": info["bib_entries"],
        "bibtex_from_bibitem": info["bibitem_entries"],
        "versions_with_bibliography": info["versions_with_bibliography"],
        "bib_fallback_versions": info["bib_fallback_versions"],
        "bibtex_entries_with_title": sum(bool(normalize_text(entry.get("title", ""))) for entry in entries),
        "bibtex_entries_with_authors": sum(bool(normalize_authors(
            entry.get("author", entry.get("authors", "")))) for entry in entries),
        "canonical_entries": len(rows),
        "duplicate_bibtex_entries": len(entries) - len(rows),
        "merged_keys": len(replacements),
        "citation_keys_rewritten": citation_occurrences,
        "citation_key_replacements": replacements,
        "bibtex_conflicts": len(conflicts),
        "references_ingested": len(references_all),
        "references_with_arxiv_id": len(references),
        "candidate_pairs": len(candidates),
        "zero_candidate_entries": sum(count == 0 for count in candidate_counts),
        "zero_candidate_percentage": (
            round(100 * sum(count == 0 for count in candidate_counts) / len(candidate_counts), 3)
            if candidate_counts else 0.0),
        "candidates_per_entry": candidates_per_entry,
        "candidate_count_min": min(candidate_counts, default=0),
        "candidate_count_max": max(candidate_counts, default=0),
        "candidate_count_average": round(statistics.fmean(candidate_counts), 3) if candidate_counts else 0.0,
        "candidate_count_median": statistics.median(candidate_counts) if candidate_counts else 0,
        "exact_title_candidates": exact_title_candidates,
        "fuzzy_title_candidates": len(candidates) - exact_title_candidates,
        "entries_with_candidates": sum(count > 0 for count in candidate_counts),
        "candidate_generation_skipped": len(rows) * len(references) - len(candidates),
        "candidate_generation_skipped_by_reason": candidate_diagnostics,
        "deduplication_seconds": round(dedup_seconds, 6),
        "candidate_generation_seconds": round(candidate_seconds, 6),
        "total_seconds": round(time.perf_counter() - started, 6),
    }
    _atomic_json(paper_dir / "m2_stats.json", stats)
    return stats


# ----------------------------------------------------------------------------- dung lượng đầu ra
M2_OUTPUT_FILES = {
    "tree_raw.json", "elements.json", "hierarchy.json", "parse_status.json", "bibtex_entries_raw.json",
    "bibtex_conflicts.json", "cite_map.json", "bibtex_merged.json", "candidates.json",
    "candidate_entry_index.json", "m2_stats.json",
}


def m2_output_bytes(root: Path) -> dict:
    """Dung lượng RIÊNG của đầu ra M2 (không tính mã nguồn .tex/.bib của M1 nằm cùng thư mục):
    các file JSON trung gian của từng bài và các bảng Parquet trong export/."""
    per_paper = sum(p.stat().st_size for p in root.rglob("*.json") if p.name in M2_OUTPUT_FILES)
    export_dir = root / "export"
    parquet = sum(p.stat().st_size for p in export_dir.glob("*.parquet")) if export_dir.is_dir() else 0
    return {"m2_json_bytes": per_paper, "export_parquet_bytes": parquet, "m2_output_bytes": per_paper + parquet}


# ----------------------------------------------------------------------------- điều phối
def _select_papers(root: Path, ids: Optional[List[str]], limit: Optional[int]) -> List[Path]:
    papers = list_paper_dirs(root)
    if ids:
        wanted = {i for i in ids} | {C.paper_key(i) for i in ids}
        papers = [p for p in papers if paper_arxiv_id(p) in wanted or p.name in wanted]
    return papers[:limit] if limit else papers


def run(corpus_dir: Path | None = None, steps=ALL_STEPS, ids: Optional[List[str]] = None,
        limit: Optional[int] = None, skip_existing: bool = False, export: bool = False,
        bench_dir: Optional[Path] = None) -> list[dict]:
    root = Path(corpus_dir or _corpus_dir()).expanduser().resolve()
    C.DATA_DIR = root  # các module parser đọc đường dẫn qua utils.config
    C.LOGS_DIR = root / "logs"
    papers = _select_papers(root, ids, limit)
    log(f"[m2] {len(papers)} bài, các bước: {', '.join(steps)}")
    results: List[dict] = []
    export_counts: dict = {}

    with PerfRecorder("m2", watch_dirs=[root]) as rec:
        for index, paper_dir in enumerate(papers, start=1):
            arxiv_id = paper_arxiv_id(paper_dir)
            if skip_existing and (paper_dir / "m2_stats.json").exists() and (paper_dir / "elements.json").exists():
                continue
            entry: dict = {"paper": paper_dir.name, "arxiv_id": arxiv_id}
            ok = True
            with rec.paper(arxiv_id):
                try:
                    if "bib" in steps:
                        started = time.perf_counter()
                        stats = process_paper(paper_dir)
                        rec.add_stage_time("bib", time.perf_counter() - started)
                        entry.update(stats)
                        rec.record_dedup(arxiv_id, "bibtex", stats["bibtex_entries_ingested"],
                                         stats["canonical_entries"])
                    if "tree" in steps:
                        started = time.perf_counter()
                        statuses = H.process_paper(arxiv_id)
                        rec.add_stage_time("tree", time.perf_counter() - started)
                        entry["tree_statuses"] = dict(statuses)
                        ok = not any(s in {"no_root", "error"} for _, s in statuses)
                    if "content" in steps:
                        started = time.perf_counter()
                        summary = D.process_paper(arxiv_id)
                        rec.add_stage_time("content", time.perf_counter() - started)
                        entry["parse"] = {k: summary[k] for k in (
                            "n_versions", "n_versions_ok", "n_nodes_before_dedup", "n_elements_after_dedup")}
                        entry["version_status"] = {k: v["status"] for k, v in summary["versions"].items()}
                        rec.record_dedup(arxiv_id, "elements", summary["n_nodes_before_dedup"],
                                         summary["n_elements_after_dedup"])
                except Exception as error:  # noqa: BLE001 - một bài lỗi không làm dừng cả lượt
                    ok = False
                    entry["error"] = f"{type(error).__name__}: {error}"
                    log(f"[m2] LỖI {arxiv_id}: {entry['error']}")
            rec.record_result(arxiv_id, ok)
            results.append(entry)
            if index % 100 == 0 or index == len(papers):
                log(f"[m2] {index}/{len(papers)}")
        if export:
            with rec.stage("export"):
                export_counts = export_m2_parquet(corpus_dir=root, out_dir=root / "export")

    _atomic_json(root / "m2_stats.json", results)
    out_dir = Path(bench_dir) if bench_dir else root / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    rec.save(out_dir / "m2_benchmark.json", extra={
        "n_papers": len(results), "steps": list(steps), "export": export_counts,
        "parse_success_rate": rec.summary().get("success_rate"), **m2_output_bytes(root)})
    
    # Lưu bộ đếm unknown_commands
    from parser import m2_cleanup as cleanup
    num_cmds, num_total = cleanup.save_unknown_commands_stats(str(out_dir / "unknown_commands.json"))
    if num_cmds > 0:
        log(f"[m2] Đã ghi {num_cmds} lệnh lạ ({num_total} lần xuất hiện) vào {out_dir / 'unknown_commands.json'}")
    
    return results


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Milestone 2: parse + chuẩn hóa + BibTeX + benchmark")
    ap.add_argument("--corpus-dir", type=Path, default=None, help="thư mục dữ liệu M1 (mặc định DATA_DIR)")
    ap.add_argument("--steps", default=",".join(ALL_STEPS), help="bib,tree,content (mặc định cả ba, theo thứ tự này)")
    ap.add_argument("--ids", nargs="*", help="chỉ xử lý các arXiv ID này")
    ap.add_argument("--limit", type=int, help="chỉ xử lý N bài đầu")
    ap.add_argument("--skip-existing", action="store_true", help="bỏ qua bài đã có m2_stats.json và elements.json")
    ap.add_argument("--export", action="store_true", help="xuất elements/hierarchy/bibtex.parquet vào <corpus>/export")
    ap.add_argument("--export-only", action="store_true",
                    help="chỉ xuất Parquet từ kết quả đã có (không parse lại, không ghi đè benchmark)")
    args = ap.parse_args(argv)
    if args.export_only:
        root = Path(args.corpus_dir or _corpus_dir()).expanduser().resolve()
        counts = export_m2_parquet(corpus_dir=root, out_dir=root / "export")
        log(f"[m2] đã xuất vào {root / 'export'}: {counts}")
        return
    steps = tuple(s for s in ALL_STEPS if s in {x.strip() for x in args.steps.split(",")})
    run(args.corpus_dir, steps, args.ids, args.limit, args.skip_existing, args.export)


if __name__ == "__main__":
    main()
