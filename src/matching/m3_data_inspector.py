"""Inspect M1/M2 data for M3 selection, annotation and candidate-recall checks.

Key correction: BibTeX is loaded from ``bibtex_merged.json`` (the actual M2
output), and ``refs.json`` may be either a list or an object containing a
``references`` list. Manual suitability uses ``references_with_arxiv_id`` as
an upper-bound signal, never BibTeX entry count or candidate-entry count.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

try:  # script execution and package execution are both supported
    from m3_pick_papers import MEMBERS, MEMBER_RANGES, in_range, paper_key
except ImportError:  # pragma: no cover - package import path
    from .m3_pick_papers import MEMBERS, MEMBER_RANGES, in_range, paper_key

try:
    from m3_candidates import normalize_arxiv_id, normalize_doi
except ImportError:  # pragma: no cover
    from .m3_candidates import normalize_arxiv_id, normalize_doi


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được {path}: {exc}") from exc


def _paper_dir(corpus_dir: Path, arxiv_id: str) -> Path:
    return corpus_dir / paper_key(arxiv_id)


def _load_references(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, [])
    if isinstance(data, dict):
        data = data.get("references", [])
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _load_list(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, [])
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _ref_arxiv_id(ref: dict[str, Any]) -> str | None:
    value = ref.get("ref_arxiv_id") or ref.get("arxiv_id")
    ext = ref.get("external_ids") or ref.get("externalIds") or {}
    if not value and isinstance(ext, dict):
        value = ext.get("ArXiv") or ext.get("arxiv")
    return normalize_arxiv_id(value) if value else None


def _ref_doi(ref: dict[str, Any]) -> str | None:
    ext = ref.get("external_ids") or ref.get("externalIds") or {}
    value = ref.get("doi")
    if not value and isinstance(ext, dict):
        value = ext.get("DOI") or ext.get("doi")
    return normalize_doi(value)


def _bib_identity_sets(bib: dict[str, Any]) -> tuple[set[str], set[str]]:
    # `arxiv_id` on a BibTeX row is the citing paper ID; intentionally ignored.
    raw_values = [bib.get("eprint"), bib.get("url"), bib.get("raw_text"), bib.get("howpublished"), bib.get("note")]
    ids: set[str] = set()
    import re
    pattern = re.compile(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)?((?:\d{4}\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})(?:v\d+)?)", re.I)
    for value in raw_values:
        if not value:
            continue
        for match in pattern.finditer(str(value)):
            normalized = normalize_arxiv_id(match.group(0))
            if normalized:
                ids.add(normalized)
        direct = normalize_arxiv_id(value)
        if direct:
            ids.add(direct)
    dois = {value for raw in [bib.get("doi"), bib.get("url"), bib.get("raw_text")] if (value := normalize_doi(raw))}
    return ids, dois


def load_paper_data(arxiv_id: str, corpus_dir: Path) -> dict[str, Any]:
    """Read all M1/M2 artifacts for one paper and derive M3 diagnostics."""
    pdir = _paper_dir(corpus_dir, arxiv_id)
    data: dict[str, Any] = {
        "arxiv_id": arxiv_id,
        "paper_key": pdir.name,
        "exists": pdir.is_dir(),
        "has_elements": False,
        "has_hierarchy": False,
        "has_refs": False,
        "has_bibtex": False,
        "has_candidates": False,
        "elements_count": 0,
        "hierarchy_rows": 0,
        "versions": [],
        "bibtex_entries": [],
        "references": [],
        "refs_with_arxiv": [],
        "candidate_rows": [],
        "candidate_pairs": 0,
        "entries_with_candidates": 0,
        "bibtex_entry_count": 0,
        "bibitem_entries": 0,
        "entries_without_title": 0,
        "entries_without_eprint": 0,
        "entries_with_eprint": 0,
        "entries_eprint_exact_match": 0,
        "entries_with_doi": 0,
        "entries_doi_exact_match": 0,
        "entries_any_identity_match": 0,
        "source_counts": {},
        "source_diagnostics": {},
        "manual_positive_labels": None,
        "candidate_recall": None,
        "candidate_recall_by_source": {},
        "missing_identity_pairs": 0,
        "errors": [],
    }
    if not pdir.is_dir():
        data["errors"].append(f"Không tồn tại thư mục bài: {pdir}")
        return data

    elem_path = pdir / "elements.json"
    if elem_path.exists():
        data["has_elements"] = True
        try:
            elements = _load_list(elem_path)
            data["elements_count"] = len(elements)
        except Exception as exc:
            data["errors"].append(f"elements.json: {exc}")

    hierarchy_path = pdir / "hierarchy.json"
    if hierarchy_path.exists():
        data["has_hierarchy"] = True
        try:
            hierarchy = _load_list(hierarchy_path)
            data["hierarchy_rows"] = len(hierarchy)
            data["versions"] = sorted({str(row.get("version")) for row in hierarchy if row.get("version") is not None})
        except Exception as exc:
            data["errors"].append(f"hierarchy.json: {exc}")

    refs_path = pdir / "refs.json"
    if refs_path.exists():
        data["has_refs"] = True
        try:
            refs = _load_references(refs_path)
            data["references"] = refs
            data["refs_with_arxiv"] = [ref for ref in refs if _ref_arxiv_id(ref)]
        except Exception as exc:
            data["errors"].append(f"refs.json: {exc}")

    # This is the actual M2 artifact. bibtex.json was an incorrect assumption.
    bib_path = pdir / "bibtex_merged.json"
    if bib_path.exists():
        data["has_bibtex"] = True
        try:
            bibs = _load_list(bib_path)
            data["bibtex_entries"] = bibs
            data["bibtex_entry_count"] = len(bibs)
            data["bibitem_entries"] = sum(str(row.get("source", "")).casefold() == "bibitem" for row in bibs)
            data["entries_without_title"] = sum(not str(row.get("title") or "").strip() for row in bibs)
            data["entries_without_eprint"] = sum(not str(row.get("eprint") or "").strip() for row in bibs)
        except Exception as exc:
            data["errors"].append(f"bibtex_merged.json: {exc}")
    else:
        data["errors"].append("Thiếu bibtex_merged.json")

    cand_path = pdir / "candidates.json"
    if cand_path.exists():
        data["has_candidates"] = True
        try:
            candidates = _load_list(cand_path)
            data["candidate_rows"] = candidates
            data["candidate_pairs"] = len(candidates)
            data["entries_with_candidates"] = len({str(row.get("citation_key")) for row in candidates if row.get("citation_key")})
        except Exception as exc:
            data["errors"].append(f"candidates.json: {exc}")

    # Quantify B1 signals by source: exact eprint/DOI links independent of
    # title similarity. This is evidence of identity coverage, not a gold
    # precision estimate; manual labels remain the source of truth.
    reference_ids = {_ref_arxiv_id(ref) for ref in data["refs_with_arxiv"]}
    reference_ids.discard(None)
    reference_dois = {_ref_doi(ref) for ref in data["refs_with_arxiv"]}
    reference_dois.discard(None)
    source_counts: Counter[str] = Counter()
    source_diagnostics: dict[str, Counter[str]] = {}
    for bib in data["bibtex_entries"]:
        source_name = str(bib.get("source") or "bib").casefold()
        source_counts[source_name] += 1
        source_diag = source_diagnostics.setdefault(source_name, Counter())
        source_diag["entries"] += 1
        source_diag["entries_without_title"] += int(not str(bib.get("title") or "").strip())
        bib_ids, bib_dois = _bib_identity_sets(bib)
        has_eprint = bool(bib.get("eprint") or any("arxiv.org/" in str(bib.get(field) or "").casefold() for field in ("url", "raw_text")))
        has_doi = bool(bib.get("doi") or any("10." in str(bib.get(field) or "") for field in ("url", "raw_text")))
        if has_eprint:
            data["entries_with_eprint"] += 1
            source_diag["entries_with_eprint"] += 1
        if has_doi:
            data["entries_with_doi"] += 1
            source_diag["entries_with_doi"] += 1
        id_match = bool(bib_ids & reference_ids)
        doi_match = bool(bib_dois & reference_dois)
        data["entries_eprint_exact_match"] += int(id_match)
        data["entries_doi_exact_match"] += int(doi_match)
        data["entries_any_identity_match"] += int(id_match or doi_match)
        source_diag["entries_eprint_exact_match"] += int(id_match)
        source_diag["entries_doi_exact_match"] += int(doi_match)
        source_diag["entries_any_identity_match"] += int(id_match or doi_match)
    data["source_counts"] = dict(source_counts)
    data["source_diagnostics"] = {name: dict(counts) for name, counts in source_diagnostics.items()}

    # Detect exact ID/DOI pairs that the current candidate generation missed.
    existing_pairs = {
        (str(row.get("citation_key") or ""), normalize_arxiv_id(row.get("ref_arxiv_id")) or str(row.get("ref_arxiv_id") or ""))
        for row in data["candidate_rows"]
    }
    for bib in data["bibtex_entries"]:
        ids, dois = _bib_identity_sets(bib)
        for ref in data["refs_with_arxiv"]:
            rid = _ref_arxiv_id(ref)
            if not rid:
                continue
            doi = _ref_doi(ref)
            if (rid in ids or (doi and doi in dois)) and (str(bib.get("citation_key") or ""), rid) not in existing_pairs:
                data["missing_identity_pairs"] += 1

    labels_path = corpus_dir / "m3" / "labels" / f"{pdir.name}.json"
    label_data = read_json(labels_path)
    if isinstance(label_data, dict) and isinstance(label_data.get("labels"), dict):
        labels = label_data["labels"]
        positives = {key: value for key, value in labels.items() if value is not None}
        data["manual_positive_labels"] = len(positives) if label_data.get("source") == "manual" else None
        if positives and label_data.get("source") == "manual":
            candidate_by_entry: dict[str, set[str]] = {}
            bib_source_by_key = {
                str(row.get("citation_key")): str(row.get("source") or "bib").casefold()
                for row in data["bibtex_entries"] if row.get("citation_key")
            }
            source_recall: dict[str, Counter[str]] = {}
            for row in data["candidate_rows"]:
                key = str(row.get("citation_key") or "")
                rid = normalize_arxiv_id(row.get("ref_arxiv_id")) or str(row.get("ref_arxiv_id") or "")
                candidate_by_entry.setdefault(key, set()).add(rid)
            hits = 0
            for key, value in positives.items():
                hit = normalize_arxiv_id(value) in candidate_by_entry.get(key, set())
                hits += int(hit)
                source_name = bib_source_by_key.get(str(key), "unknown")
                source_recall.setdefault(source_name, Counter())["q"] += 1
                source_recall[source_name]["hits"] += int(hit)
            data["candidate_recall"] = hits / len(positives)
            data["candidate_recall_hits"] = hits
            data["candidate_recall_q"] = len(positives)
            data["candidate_recall_by_source"] = {
                name: {"q": counts["q"], "hits": counts["hits"], "recall": counts["hits"] / counts["q"]}
                for name, counts in source_recall.items()
            }

    # Drop bulky raw contents from the top-level report; callers needing them
    # still get the lists from load_paper_data().
    return data


def _ids_from_stats(corpus_dir: Path) -> list[str]:
    stats = read_json(corpus_dir / "m2_stats.json", [])
    if not isinstance(stats, list):
        raise ValueError("m2_stats.json phải là list")
    return [str(row["arxiv_id"]) for row in stats if isinstance(row, dict) and row.get("arxiv_id")]


def _ids_from_paper_json(corpus_dir: Path) -> list[str]:
    """Discover the actual M1 paper universe from directories containing paper.json."""
    import re
    found: set[str] = set()
    for pdir in corpus_dir.iterdir() if corpus_dir.is_dir() else []:
        if not pdir.is_dir() or not (pdir / "paper.json").is_file():
            continue
        meta = read_json(pdir / "paper.json", {})
        raw_id = None
        if isinstance(meta, dict):
            raw_id = meta.get("arxiv_id") or meta.get("id") or meta.get("paper_id")
        if not raw_id:
            match = re.fullmatch(r"(\d{4})-(\d{4,5})", pdir.name)
            if match:
                raw_id = f"{match.group(1)}.{match.group(2)}"
        if raw_id:
            found.add(str(raw_id).strip())
    return sorted(found)


def _describe(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 3),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _stat_int(row: dict[str, Any], name: str) -> int:
    try:
        return int(row.get(name) or 0)
    except (TypeError, ValueError):
        return 0


def inspect_corpus(
    corpus_dir: Path,
    member: str = "Quoc",
    limit: int | None = None,
    ids: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stats_ids = _ids_from_stats(corpus_dir)
    paper_ids = _ids_from_paper_json(corpus_dir)
    all_ids = ids or (paper_ids if paper_ids else stats_ids)
    scope_ids = sorted({aid for aid in all_ids if member == "all" or in_range(aid, member)})
    selected_ids = list(scope_ids)
    if limit is not None:
        if limit < 0:
            raise ValueError("limit không được âm")
        selected_ids = selected_ids[:limit]

    papers = [load_paper_data(aid, corpus_dir) for aid in selected_ids]
    present = [row for row in papers if row["exists"]]
    refs_counts = [len(row["refs_with_arxiv"]) for row in present]
    bib_counts = [row["bibtex_entry_count"] for row in present]
    candidate_counts = [row["candidate_pairs"] for row in present]
    recall_rows = [row for row in present if row.get("candidate_recall") is not None]
    stats_map = {
        (normalize_arxiv_id(row["arxiv_id"]) or str(row["arxiv_id"])): row
        for row in read_json(corpus_dir / "m2_stats.json", [])
        if isinstance(row, dict) and row.get("arxiv_id")
    }
    ready_manual = [
        row for row in present
        if row["has_bibtex"] and row["has_refs"]
        and _stat_int(stats_map.get(normalize_arxiv_id(row["arxiv_id"]) or row["arxiv_id"], {}), "canonical_entries") > 0
        and len(row["refs_with_arxiv"]) >= 22
    ]
    ready_auto = [
        row for row in present
        if row["has_bibtex"] and row["has_refs"]
        and _stat_int(stats_map.get(normalize_arxiv_id(row["arxiv_id"]) or row["arxiv_id"], {}), "canonical_entries") > 0
        and _stat_int(stats_map.get(normalize_arxiv_id(row["arxiv_id"]) or row["arxiv_id"], {}), "references_with_arxiv_id") > 0
    ]
    total = len(scope_ids)
    manual_required = 5
    auto_required = math.ceil(0.10 * max(total - manual_required, 0))

    aggregate_source_diagnostics: dict[str, Counter[str]] = {}
    aggregate_source_recall: dict[str, Counter[str]] = {}
    for paper in present:
        for source_name, counts in paper.get("source_diagnostics", {}).items():
            aggregate_source_diagnostics.setdefault(source_name, Counter()).update(counts)
        for source_name, counts in paper.get("candidate_recall_by_source", {}).items():
            aggregate_source_recall.setdefault(source_name, Counter()).update({"q": counts.get("q", 0), "hits": counts.get("hits", 0)})

    summary = {
        "member": member,
        "range": MEMBER_RANGES.get(member) if member != "all" else "all member ranges",
        "total_papers_in_range": total,
        "papers_inspected": len(selected_ids),
        "inspection_limited": limit is not None and len(selected_ids) < len(scope_ids),
        "paper_count_source": "explicit IDs" if ids else ("paper.json" if paper_ids else "m2_stats.json fallback"),
        "total_ids_in_m2_stats": sum(1 for aid in stats_ids if member == "all" or in_range(aid, member)),
        "papers_without_m2_stats": sorted(set(selected_ids) - set(stats_map)),
        "paper_directories_found": len(present),
        "has_elements": sum(row["has_elements"] for row in present),
        "has_hierarchy": sum(row["has_hierarchy"] for row in present),
        "has_refs": sum(row["has_refs"] for row in present),
        "has_bibtex_merged": sum(row["has_bibtex"] for row in present),
        "has_candidates": sum(row["has_candidates"] for row in present),
        "bibtex_entries": _describe(bib_counts),
        "references_with_arxiv_id": _describe(refs_counts),
        "candidate_pairs": _describe(candidate_counts),
        "manual_pool_refs_ge_22_and_canonical_entries_gt_0_in_inspected_set": len(ready_manual),
        "manual_minimum_required": manual_required,
        "auto_initial_eligible": len(ready_auto),
        "auto_minimum_required_by_plan": auto_required,
        "candidate_recall_on_labeled_papers": {
            "papers_with_labels": len(recall_rows),
            "q": sum(row.get("candidate_recall_q", 0) for row in recall_rows),
            "hits": sum(row.get("candidate_recall_hits", 0) for row in recall_rows),
            "recall": (
                sum(row.get("candidate_recall_hits", 0) for row in recall_rows)
                / max(sum(row.get("candidate_recall_q", 0) for row in recall_rows), 1)
            ) if recall_rows else None,
        },
        "bibitem_entries": sum(row["bibitem_entries"] for row in present),
        "entries_without_title": sum(row["entries_without_title"] for row in present),
        "entries_without_eprint": sum(row["entries_without_eprint"] for row in present),
        "entries_with_eprint": sum(row["entries_with_eprint"] for row in present),
        "entries_eprint_exact_match": sum(row["entries_eprint_exact_match"] for row in present),
        "entries_with_doi": sum(row["entries_with_doi"] for row in present),
        "entries_doi_exact_match": sum(row["entries_doi_exact_match"] for row in present),
        "entries_any_identity_match": sum(row["entries_any_identity_match"] for row in present),
        "source_distribution": dict(sum((Counter(row["source_counts"]) for row in present), Counter())),
        "source_diagnostics": {name: dict(counts) for name, counts in aggregate_source_diagnostics.items()},
        "candidate_recall_by_source": {
            name: {"q": counts["q"], "hits": counts["hits"], "recall": counts["hits"] / counts["q"] if counts["q"] else None}
            for name, counts in aggregate_source_recall.items()
        },
        "identity_pairs_missing_from_candidates": sum(row["missing_identity_pairs"] for row in present),
        "papers_missing_directory": [row["arxiv_id"] for row in papers if not row["exists"]],
        "papers_with_errors": [
            {"arxiv_id": row["arxiv_id"], "errors": row["errors"]}
            for row in present if row["errors"]
        ],
    }
    detail = [{key: value for key, value in row.items() if key not in {"bibtex_entries", "references", "refs_with_arxiv", "candidate_rows"}} for row in papers]
    return summary, detail


def main() -> None:
    parser = argparse.ArgumentParser(description="Kiểm tra dữ liệu M1/M2, nguồn cung M3 và candidate recall")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--member", choices=MEMBERS + ["all"], default="Quoc")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", help="Danh sách arXiv ID cách nhau bởi dấu phẩy để kiểm tra mẫu")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.corpus_dir.is_dir():
        parser.error(f"Không tìm thấy corpus: {args.corpus_dir}")
    ids = [item.strip() for item in args.ids.split(",") if item.strip()] if args.ids else None
    summary, detail = inspect_corpus(args.corpus_dir, args.member, args.limit, ids)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = args.output or (args.corpus_dir / f"m3_inspection_{args.member}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "papers": detail}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã ghi báo cáo: {out}")
    if summary["manual_pool_refs_ge_22_and_canonical_entries_gt_0_in_inspected_set"] < 5 and not summary["inspection_limited"]:
        print("CẢNH BÁO: nguồn manual sơ tuyển dưới 5 bài.")
    if summary["auto_initial_eligible"] < summary["auto_minimum_required_by_plan"] and not summary["inspection_limited"]:
        print("CẢNH BÁO: nguồn auto ban đầu thấp hơn quota 10% (chưa tính buffer).")


if __name__ == "__main__":
    main()
