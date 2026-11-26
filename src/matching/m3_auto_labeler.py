r"""m3_auto_labeler.py

Milestone: M3
Người phụ trách: P2
Mục đích: Matcher tự động (luật xác định, không dùng ML) gán nhãn cho các bài ngoài nhóm gán tay.
          Cần phủ >= 10% số bài ngoài nhóm gán tay và tối thiểu 2 bài.

Luật cho MỘT BibTeX entry, xét mọi candidate của entry, lấy candidate có bằng chứng mạnh nhất:
    1. eprint : arXiv ID trong BibTeX (eprint/url/raw_text) trùng arXiv ID của reference
    2. doi    : DOI của BibTeX trùng DOI của reference
    3. title_exact : title chuẩn hóa giống hệt
    4. title_fuzzy : title_sim >= 0.85 VÀ (author_sim >= 0.60 HOẶC họ tác giả đầu trùng)
    5. title_fuzzy : title_sim >= 0.70 VÀ năm khớp (cả hai phải có năm) VÀ họ tác giả đầu trùng
  (title_exact tính sau khi thống nhất dấu gạch/nháy và bỏ ghi chú cuối kiểu "[Special Issue ...]")
  Luật 3-5 bị VETO khi BibTeX khai báo một arXiv ID (eprint/url) khác với reference đó.
  Nếu nhiều reference KHÁC NHAU cùng đạt mức bằng chứng cao nhất ở luật 3-5 -> mơ hồ, bỏ qua entry.
  Nếu hai entry cùng nhận một reference, giữ entry có bằng chứng mạnh hơn (rồi title_sim cao hơn).
  Không luật nào khớp -> không gán nhãn (entry vắng mặt trong labels JSON).

Đầu vào (mỗi bài): candidates.json, bibtex_merged.json, refs.json
Đầu ra:
  - <corpus_dir>/labels/auto_labels.csv  (arxiv_id, citation_key, groundtruth_arxiv_id)
  - <corpus_dir>/m3/labels/<paper_key>.json  (source="auto", labels, evidence) cho bài chưa có nhãn tay

`run_m3.py --stage label` gọi label_corpus() trong file này; CLI dưới đây gọi cùng hàm đó,
nên hai đường chạy cho cùng kết quả.

Chạy:
    python src/matching/m3_auto_labeler.py --corpus-dir data
    python src/matching/m3_auto_labeler.py --corpus-dir data --no-json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_candidates import (  # noqa: E402
    eprint_conflict,
    generate_candidates,
    identity_evidence,
    normalize_arxiv_id,
    normalize_text,
    reference_arxiv_ids,
)
from matching.m3_manual_labeler import validate_label_data  # noqa: E402
from matching.m3_string_features import (  # noqa: E402
    compute_string_features,
    enrich_candidate,
    find_ref,
    load_paper_context,
)
from utils import config as C  # noqa: E402

# ── ngưỡng ───────────────────────────────────────────────────────────────────
_HIGH_TITLE = 0.85   # luật 4
_HIGH_AUTHOR = 0.60  # luật 4
_MED_TITLE = 0.70    # luật 5 (kèm năm khớp)

EVIDENCE_RANK = {"eprint": 4, "doi": 3, "title_exact": 2, "title_fuzzy": 1}
_RESERVED_DIRS = {"export", "m3", "labels", "benchmark", "logs", "predictions"}
AUTO_LABELER_NAME = "auto_v2"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"


def _title_key(text: object) -> str:
    """Title đã chuẩn hóa để so 'giống hệt': thống nhất dấu gạch/nháy Unicode và bỏ ghi chú cuối.

    Semantic Scholar hay thêm ghi chú như "[Special Issue on ...]" vào cuối title, và BibTeX/reference
    hay khác nhau ở dấu gạch ngang (– so với -); hai khác biệt đó không làm hai title thành hai bài khác nhau.
    """
    t = normalize_text(text)
    t = re.sub(f"[{_DASHES}]", "-", t)
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"\s*\[[^\]]*\]\s*$", "", t)
    t = re.sub(r"[\s.]+$", "", t)
    return re.sub(r"\s+", " ", t)


def classify_candidate(cand: dict, bib: dict, ref: dict) -> Optional[str]:
    """Bằng chứng mạnh nhất cho một cặp (BibTeX entry, reference), hoặc None."""
    if bib and ref:
        evidence = identity_evidence(bib, ref)
        if evidence:
            return evidence
        if eprint_conflict(bib, ref):
            return None  # BibTeX ghi rõ một arXiv ID khác: title trùng cũng không đủ

    feats = compute_string_features(enrich_candidate(cand, bib, ref))
    bib_key, ref_key = _title_key(cand.get("bib_title")), _title_key(cand.get("ref_title"))
    if feats["title_exact"] or (bib_key and bib_key == ref_key):
        return "title_exact"
    title_sim, author_sim = feats["title_sim"], feats["author_sim"]
    first_author = feats["first_author_match"] == 1.0  # NaN (thiếu dữ liệu) -> False
    # Luật 4. Họ tác giả đầu trùng cũng đủ "hỗ trợ tác giả": bibitem kiểu IEEE chỉ có "A. Smith et al"
    # nên author_sim luôn thấp dù đúng bài.
    if title_sim >= _HIGH_TITLE and (author_sim >= _HIGH_AUTHOR or first_author):
        return "title_fuzzy"
    # Luật 5. Cần cả năm khớp VÀ họ tác giả đầu khớp. Chỉ title_sim >= 0.70 là không đủ: các bài cùng
    # lĩnh vực chung nhiều từ ("... Affine Frequency Division Multiplexing ...") nên dễ gán nhầm.
    if title_sim >= _MED_TITLE and feats["year_exact"] == 1.0 and first_author:
        return "title_fuzzy"
    return None


def match_entry(
    entry_candidates: List[dict],
    bib: dict,
    ref_index: Tuple[dict, dict],
) -> Tuple[str, Optional[str], float, bool]:
    """Trả về (ref_arxiv_id, evidence, title_sim, ambiguous) cho một BibTeX entry."""
    hits: List[Tuple[int, float, str, str]] = []  # (rank, title_sim, ref_id, evidence)
    for cand in entry_candidates:
        ref_id = normalize_arxiv_id(cand.get("ref_arxiv_id")) or ""
        if not ref_id:
            continue
        evidence = classify_candidate(cand, bib, find_ref(cand, ref_index))
        if evidence:
            hits.append((EVIDENCE_RANK[evidence], float(cand.get("title_similarity") or 0.0), ref_id, evidence))
    if not hits:
        return "", None, 0.0, False

    best_rank = max(h[0] for h in hits)
    top = sorted((h for h in hits if h[0] == best_rank), key=lambda h: h[1], reverse=True)
    distinct_refs = {h[2] for h in top}
    if len(distinct_refs) > 1 and best_rank < EVIDENCE_RANK["doi"]:
        return "", None, 0.0, True  # nhiều reference cùng đạt mức title -> mơ hồ
    best = top[0]
    return best[2], best[3], best[1], False


def _candidates_for_paper(pdir: Path, min_title_sim: float = 0.35) -> List[dict]:
    """Đọc candidates.json; nếu thiếu thì sinh lại từ bibtex_merged.json + refs.json."""
    data = _read_json(pdir / "candidates.json")
    if isinstance(data, list):
        return data
    bib_rows = _read_json(pdir / "bibtex_merged.json")
    refs_payload = _read_json(pdir / "refs.json")
    refs = refs_payload.get("references", []) if isinstance(refs_payload, dict) else refs_payload
    if not isinstance(bib_rows, list) or not isinstance(refs, list):
        return []
    arxiv_refs = [r for r in refs if isinstance(r, dict) and reference_arxiv_ids(r)]
    return generate_candidates(bib_rows, arxiv_refs, min_title_similarity=min_title_sim)


def label_paper_entries(pdir: Path, arxiv_id: Optional[str] = None, min_title_sim: float = 0.35) -> Dict[str, Any]:
    """Gán nhãn mọi BibTeX entry có candidate của một bài.

    Trả về {"arxiv_id", "rows" (mọi entry có candidate, cho CSV), "labels", "evidence", "n_ambiguous"}.
    """
    candidates = _candidates_for_paper(pdir, min_title_sim)
    out: Dict[str, Any] = {"arxiv_id": arxiv_id, "rows": [], "labels": {}, "evidence": {}, "n_ambiguous": 0}
    if not candidates:
        return out
    arxiv_id = arxiv_id or str(candidates[0].get("arxiv_id") or pdir.name.replace("-", "."))
    out["arxiv_id"] = arxiv_id

    groups: Dict[str, List[dict]] = {}
    for cand in candidates:
        key = str(cand.get("citation_key") or "").strip()
        if key:
            groups.setdefault(key, []).append(cand)
    if not groups:
        return out

    bib_by_key, ref_index = load_paper_context(pdir)
    picks: Dict[str, Tuple[str, str, float]] = {}
    for key, cands in sorted(groups.items()):
        ref_id, evidence, tsim, ambiguous = match_entry(cands, bib_by_key.get(key, {}), ref_index)
        out["n_ambiguous"] += int(ambiguous)
        if ref_id and evidence:
            picks[key] = (ref_id, evidence, tsim)

    # Mỗi reference chỉ gán cho một entry/bài: giữ entry có bằng chứng mạnh nhất.
    claimed: Dict[str, str] = {}
    for key, (ref_id, evidence, tsim) in sorted(
        picks.items(), key=lambda kv: (EVIDENCE_RANK[kv[1][1]], kv[1][2], kv[0]), reverse=True
    ):
        if ref_id in claimed:
            continue
        claimed[ref_id] = key
        out["labels"][key] = ref_id
        out["evidence"][key] = evidence

    out["labels"] = dict(sorted(out["labels"].items()))
    out["evidence"] = {k: out["evidence"][k] for k in out["labels"]}
    out["rows"] = [
        {"arxiv_id": arxiv_id, "citation_key": key, "groundtruth_arxiv_id": out["labels"].get(key, "")}
        for key in sorted(groups)
    ]
    return out


def list_paper_dirs(corpus_dir: Path) -> List[Path]:
    return sorted(
        d for d in corpus_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in _RESERVED_DIRS
    ) if corpus_dir.exists() else []


def default_paper_id(pdir: Path) -> str:
    data = _read_json(pdir / "paper.json")
    if isinstance(data, dict) and data.get("arxiv_id"):
        return str(data["arxiv_id"])
    return pdir.name.replace("-", ".")


def _existing_source(label_path: Path) -> Optional[str]:
    if not label_path.exists():
        return None
    data = _read_json(label_path)
    return str(data.get("source") or "").lower() if isinstance(data, dict) else "unreadable"


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def label_corpus(
    data_dir: Path,
    paper_dirs: Optional[List[Path]] = None,
    paper_id_of: Callable[[Path], str] = default_paper_id,
    min_title_sim: float = 0.35,
    write_json: bool = True,
) -> Dict[str, Any]:
    """Gán nhãn tự động cho cả corpus; ghi auto_labels.csv và (tùy chọn) m3/labels/*.json.

    - Không bao giờ đụng vào file nhãn source="manual" (và bài có nhãn tay bị bỏ qua hoàn toàn).
    - Bài đã có nhãn auto cũ được GHI ĐÈ bằng kết quả mới (đổi luật rồi chạy lại phải có tác dụng);
      nếu lần này không còn nhãn nào thì xóa file auto cũ.
    """
    data_dir = Path(data_dir)
    paper_dirs = list(paper_dirs) if paper_dirs is not None else list_paper_dirs(data_dir)
    csv_rows: List[dict] = []
    stats = {"n_papers_scanned": len(paper_dirs), "n_papers_labeled": 0, "n_manual_skipped": 0,
             "n_matched_entries": 0, "n_ambiguous_entries": 0, "n_json_written": 0,
             "n_json_removed": 0, "n_invalid_skipped": 0, "n_foreign_label_skipped": 0,
             "evidence_counts": {}}

    for pdir in paper_dirs:
        arxiv_id = paper_id_of(pdir)
        label_path = C.labels_path(arxiv_id, data_dir)
        source = _existing_source(label_path)
        if source == "manual":
            stats["n_manual_skipped"] += 1
            continue
        if source not in (None, "auto"):
            stats["n_foreign_label_skipped"] += 1  # file lạ/hỏng: không ghi đè
            continue

        result = label_paper_entries(pdir, arxiv_id, min_title_sim)
        if not result["rows"]:
            continue
        stats["n_papers_labeled"] += 1
        stats["n_ambiguous_entries"] += result["n_ambiguous"]
        csv_rows.extend(result["rows"])
        labels, evidence = result["labels"], result["evidence"]
        stats["n_matched_entries"] += len(labels)
        for ev in evidence.values():
            stats["evidence_counts"][ev] = stats["evidence_counts"].get(ev, 0) + 1

        if not write_json:
            continue
        if not labels:
            if source == "auto":
                label_path.unlink()
                stats["n_json_removed"] += 1
            continue
        payload = {"arxiv_id": arxiv_id, "source": "auto", "labeler": AUTO_LABELER_NAME,
                   "labels": labels, "evidence": evidence}
        bib_rows = _read_json(pdir / "bibtex_merged.json")
        refs_payload = _read_json(pdir / "refs.json")
        refs = refs_payload.get("references", []) if isinstance(refs_payload, dict) else refs_payload
        if isinstance(bib_rows, list) and isinstance(refs, list):
            errors = validate_label_data(payload, bib_rows, refs)
            if errors:
                print(f"[auto_labeler] bỏ qua {arxiv_id}: nhãn không qua kiểm tra: {errors[:3]}")
                stats["n_invalid_skipped"] += 1
                continue
        _atomic_write_json(label_path, payload)
        stats["n_json_written"] += 1

    csv_out = data_dir / "labels" / "auto_labels.csv"
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["arxiv_id", "citation_key", "groundtruth_arxiv_id"])
        writer.writeheader()
        writer.writerows(csv_rows)
    stats["csv_path"] = str(csv_out)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Tự động gán nhãn BibTeX<->reference (M3).")
    ap.add_argument("--corpus-dir", type=Path, default=C.DATA_DIR, help="Thư mục corpus (data/)")
    ap.add_argument("--min-title-sim", type=float, default=0.35,
                    help="Ngưỡng title_sim khi phải sinh lại candidate vì thiếu candidates.json")
    ap.add_argument("--no-json", action="store_true", help="Chỉ ghi auto_labels.csv, không ghi m3/labels/*.json")
    args = ap.parse_args()

    corpus_dir: Path = args.corpus_dir
    C.DATA_DIR = corpus_dir
    stats = label_corpus(corpus_dir, min_title_sim=args.min_title_sim, write_json=not args.no_json)
    outside = stats["n_papers_scanned"] - stats["n_manual_skipped"]
    need = max(2, -(-outside // 10))  # ceil(10%)
    print(f"Bài ngoài nhóm gán tay: {outside} | cần gán nhãn tự động >= {need} | "
          f"đã gán {stats['n_papers_labeled']} bài, {stats['n_matched_entries']} entry "
          f"(bằng chứng: {stats['evidence_counts']}, mơ hồ bỏ qua: {stats['n_ambiguous_entries']}).")
    if stats["n_papers_labeled"] < need:
        print(f"CẢNH BÁO: chưa đủ quota {need} bài. Kiểm tra candidates.json.", file=sys.stderr)
    print(f"Output -> {stats['csv_path']}")


if __name__ == "__main__":
    main()