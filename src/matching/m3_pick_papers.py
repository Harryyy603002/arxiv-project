"""Select M3 papers and create a per-member train/valid/test split.

Selection rules are taken from M3_PLAN.md:
* A manual candidate has >= 22 references with arXiv IDs (an upper bound,
  not proof, that it will yield >=20 correct matches).
* The final manual papers must be verified after human labeling: >=20 positive
  entries per paper and >=100 positives in total.
* Auto papers are selected from the complete range using the initial M2
  eligibility rule canonical_entries > 0 and references_with_arxiv_id > 0.
* Auto quota = ceil(10% * (number_of_papers_in_range - 5)), with a 5% buffer.
* Exactly one manual + one auto paper are reserved for test and validation.

The generated ``manual`` list is provisional until labels have been checked.
Use ``--manual-ids`` after manual annotation to lock and verify the five IDs.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# Use the project-wide config range helpers when available. The local fallback
# keeps this module directly testable in isolation, but the integrated project
# now centralizes these constants and comparisons in utils/config.py.
_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
try:
    from utils import config as C  # type: ignore
except ImportError:  # pragma: no cover - standalone unit tests without project src
    C = None

try:
    from m3_candidates import normalize_arxiv_id, normalize_doi
except ImportError:  # pragma: no cover - package import path
    from .m3_candidates import normalize_arxiv_id, normalize_doi

MEMBERS = getattr(C, "MEMBERS", ["Quoc", "Nguyen", "Khai", "Tri"])
MEMBER_RANGES = getattr(C, "MEMBER_RANGES", {
    "Quoc": ("2512.23231", "2601.03155"),
    "Nguyen": ("2602.04870", "2602.09869"),
    "Khai": ("2603.00580", "2603.05579"),
    "Tri": ("2606.20734", "2606.25733"),
})


def _base_arxiv_id(value: object) -> str:
    """Normalize the arXiv ID portion; preserve old-style IDs where possible."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", text, flags=re.I)
    text = re.sub(r"^arxiv\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"v\d+$", "", text, flags=re.I)
    return text.rstrip(".,;:)")


def id_key(arxiv_id: str) -> tuple[int, int]:
    """Comparable key for new-style IDs: ``(YYMM, numeric suffix)``."""
    if C is not None and hasattr(C, "id_key"):
        return C.id_key(arxiv_id)
    value = _base_arxiv_id(arxiv_id)
    if re.fullmatch(r"\d{9}", value):
        value = value[:4] + "." + value[4:]
    match = re.fullmatch(r"(\d{4})\.(\d{4,5})", value)
    if not match:
        raise ValueError(f"ID không ở dạng YYMM.NNNN[N]: {arxiv_id!r}")
    return int(match.group(1)), int(match.group(2))


def _range_key(value: str) -> tuple[int, int]:
    """Convert dotted or compact range endpoint to comparable key.

    M3_PLAN says to reuse M1's ``_parse_range`` for ID.txt parsing. This module
    consumes the already-normalized dotted constants from that contract; it
    deliberately does not replace the M1 ID.txt parser.
    """
    value = str(value).strip()
    if re.fullmatch(r"\d{9}", value):
        value = value[:4] + "." + value[4:]
    return id_key(value)


def in_range(arxiv_id: str, member: str) -> bool:
    if C is not None and hasattr(C, "in_range"):
        return C.in_range(arxiv_id, member)
    if member not in MEMBER_RANGES:
        raise KeyError(f"Member không hợp lệ: {member}. Chọn một trong {MEMBERS}")
    lo, hi = MEMBER_RANGES[member]
    key = id_key(arxiv_id)
    return _range_key(lo) <= key <= _range_key(hi)


def member_of(arxiv_id: str) -> str | None:
    for member in MEMBERS:
        try:
            if in_range(arxiv_id, member):
                return member
        except ValueError:
            return None
    return None


def paper_key(arxiv_id: str) -> str:
    return _base_arxiv_id(arxiv_id).replace(".", "-").replace("/", "-")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được JSON {path}: {exc}") from exc


def load_m2_stats(corpus_dir: Path) -> list[dict[str, Any]]:
    path = corpus_dir / "m2_stats.json"
    data = _read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} phải là JSON list các thống kê M2 của paper")
    return [row for row in data if isinstance(row, dict) and row.get("arxiv_id")]


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _read_refs(paper_dir: Path) -> list[dict[str, Any]]:
    data = _read_json(paper_dir / "refs.json", [])
    if isinstance(data, dict):
        data = data.get("references", [])
    return data if isinstance(data, list) else []


def _read_bibtex(paper_dir: Path) -> list[dict[str, Any]]:
    # M2's actual output; do not fall back to the obsolete bibtex.json name.
    data = _read_json(paper_dir / "bibtex_merged.json", [])
    return data if isinstance(data, list) else []


def _read_labels(labels_dir: Path, arxiv_id: str) -> dict[str, Any] | None:
    path = labels_dir / f"{paper_key(arxiv_id)}.json"
    data = _read_json(path)
    return data if isinstance(data, dict) else None


def _has_m3_inputs(corpus_dir: Path, arxiv_id: str) -> bool:
    """M3 annotation requires both the M2 BibTeX list and M1 references."""
    pdir = corpus_dir / paper_key(arxiv_id)
    bib_path = pdir / "bibtex_merged.json"
    refs_path = pdir / "refs.json"
    if not bib_path.is_file() or not refs_path.is_file():
        return False
    return bool(_read_bibtex(pdir)) and bool(_read_refs(pdir))


def _positive_label_count(label_data: dict[str, Any] | None) -> int:
    labels = (label_data or {}).get("labels", {})
    return sum(value is not None for value in labels.values()) if isinstance(labels, dict) else 0


def _identity_coverage(corpus_dir: Path, arxiv_id: str, label_data: dict[str, Any] | None = None) -> tuple[int, float]:
    """Estimate exact-ID/DOI evidence coverage for choosing auto eval papers."""
    if label_data and isinstance(label_data.get("evidence"), dict):
        labels = label_data.get("labels", {})
        evidence = label_data["evidence"]
        positive_keys = [key for key, value in labels.items() if value is not None]
        exact = sum(evidence.get(key) in {"eprint", "doi"} for key in positive_keys)
        return exact, exact / max(len(positive_keys), 1)

    pdir = corpus_dir / paper_key(arxiv_id)
    bibs = _read_bibtex(pdir)
    refs = _read_refs(pdir)
    ref_ids: set[str] = set()
    ref_dois: set[str] = set()
    for ref in refs:
        rid = ref.get("ref_arxiv_id") or (ref.get("external_ids") or ref.get("externalIds") or {}).get("ArXiv")
        normalized_rid = normalize_arxiv_id(rid) if rid else None
        if normalized_rid:
            ref_ids.add(normalized_rid)
        ext = ref.get("external_ids") or ref.get("externalIds") or {}
        doi = ref.get("doi") or (ext.get("DOI") if isinstance(ext, dict) else None)
        normalized_doi = normalize_doi(doi) if doi else None
        if normalized_doi:
            ref_dois.add(normalized_doi)

    exact = 0
    for bib in bibs:
        raw_values = [bib.get("eprint"), bib.get("url"), bib.get("raw_text"), bib.get("howpublished"), bib.get("note")]
        bib_ids: set[str] = set()
        for raw in raw_values:
            if not raw:
                continue
            text = str(raw)
            for match in re.finditer(
                r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)?((?:\d{4}\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})(?:v\d+)?)",
                text,
                re.I,
            ):
                normalized = normalize_arxiv_id(match.group(0))
                if normalized:
                    bib_ids.add(normalized)
            direct = normalize_arxiv_id(raw)
            if direct:
                bib_ids.add(direct)
        bib_dois = {value for raw in [bib.get("doi"), *raw_values] if (value := normalize_doi(raw))}
        if (bib_ids & ref_ids) or (bib_dois & ref_dois):
            exact += 1
    return exact, exact / max(len(bibs), 1)


def paper_json_ids(corpus_dir: Path, member: str) -> list[str]:
    """Count actual M1 papers using <paper_dir>/paper.json as M3_PLAN requires."""
    found: set[str] = set()
    if not corpus_dir.is_dir():
        return []
    for pdir in corpus_dir.iterdir():
        if not pdir.is_dir() or not (pdir / "paper.json").is_file():
            continue
        meta = _read_json(pdir / "paper.json", {})
        raw_id = None
        if isinstance(meta, dict):
            raw_id = meta.get("arxiv_id") or meta.get("id") or meta.get("paper_id")
        if not raw_id:
            match = re.fullmatch(r"(\d{4})-(\d{4,5})", pdir.name)
            if match:
                raw_id = f"{match.group(1)}.{match.group(2)}"
        if raw_id:
            normalized = _base_arxiv_id(raw_id)
            try:
                if in_range(normalized, member):
                    found.add(normalized)
            except ValueError:
                continue
    return sorted(found, key=id_key)


def _row_score(row: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        _int(row, "references_with_arxiv_id"),
        _int(row, "canonical_entries"),
        _int(row, "entries_with_candidates"),
        _int(row, "candidate_pairs"),
    )


def build_split(
    corpus_dir: Path,
    member: str = "Quoc",
    seed: int = 42,
    auto_buffer: float = 0.05,
    manual_min_refs: int = 22,
    manual_ids: list[str] | None = None,
    require_manual_labels: bool = False,
    require_auto_labels: bool = False,
    allow_insufficient_auto: bool = False,
) -> dict[str, Any]:
    if member not in MEMBER_RANGES:
        raise ValueError(f"Member không hợp lệ: {member}")
    if auto_buffer < 0:
        raise ValueError("auto_buffer không được âm")

    all_rows = load_m2_stats(corpus_dir)
    stats_rows_in_range = [row for row in all_rows if in_range(str(row["arxiv_id"]), member)]
    actual_ids = paper_json_ids(corpus_dir, member)
    if actual_ids:
        actual_id_set = set(actual_ids)
        rows = [row for row in stats_rows_in_range if _base_arxiv_id(row["arxiv_id"]) in actual_id_set]
        total_papers = len(actual_ids)
        missing_m2_stats = sorted(actual_id_set - {_base_arxiv_id(row["arxiv_id"]) for row in rows}, key=id_key)
        total_source = "paper.json"
    else:
        # Fallback only for unit fixtures / partially copied corpora with no M1 metadata files.
        rows = stats_rows_in_range
        total_papers = len(rows)
        missing_m2_stats = []
        total_source = "m2_stats.json fallback (no paper.json directories found)"
    if not rows:
        raise RuntimeError(f"Không có dòng m2_stats.json dùng được thuộc range {member}: {MEMBER_RANGES[member]}")
    labels_dir = corpus_dir / "m3" / "labels"
    by_id = {_base_arxiv_id(row["arxiv_id"]): row for row in rows}
    manual_target = 5
    auto_minimum = math.ceil(0.10 * max(total_papers - manual_target, 0))
    auto_target = math.ceil(auto_minimum * (1.0 + auto_buffer))

    manual_pool = [row for row in rows
                   if _int(row, "canonical_entries") > 0
                   and _int(row, "references_with_arxiv_id") >= manual_min_refs
                   and _has_m3_inputs(corpus_dir, _base_arxiv_id(row["arxiv_id"]))]
    manual_pool.sort(key=_row_score, reverse=True)

    if manual_ids:
        chosen_manual = [_base_arxiv_id(item) for item in manual_ids]
        if len(chosen_manual) != manual_target or len(set(chosen_manual)) != manual_target:
            raise ValueError("--manual-ids phải cung cấp đúng 5 ID khác nhau")
        for arxiv_id in chosen_manual:
            if arxiv_id not in by_id:
                raise ValueError(f"Manual ID {arxiv_id} không có trong m2_stats của {member}")
            if not in_range(arxiv_id, member):
                raise ValueError(f"Manual ID {arxiv_id} nằm ngoài range {member}")
            if by_id[arxiv_id] not in manual_pool:
                raise ValueError(f"Manual ID {arxiv_id} không đạt điều kiện refs_with_arxiv_id >= {manual_min_refs}")
    else:
        chosen_manual = [str(row["arxiv_id"]) for row in manual_pool[:manual_target]]

    if len(chosen_manual) < manual_target:
        raise RuntimeError(
            f"Range {member} chỉ có {len(chosen_manual)} paper sơ tuyển manual; cần 5. "
            f"Tiêu chí: canonical_entries>0 và references_with_arxiv_id>={manual_min_refs}."
        )

    # If labels already exist, actual count is authoritative. For initial
    # picking, the five selected IDs are explicitly marked provisional.
    manual_validation: dict[str, int | None] = {}
    manual_backup_ids = [str(row["arxiv_id"]) for row in manual_pool if str(row["arxiv_id"]) not in set(chosen_manual)][:10]
    for arxiv_id in chosen_manual:
        label_data = _read_labels(labels_dir, arxiv_id)
        count = _positive_label_count(label_data)
        label_path = labels_dir / f"{paper_key(arxiv_id)}.json"
        manual_validation[arxiv_id] = count if label_path.exists() else None
        if require_manual_labels:
            if not isinstance(label_data, dict) or label_data.get("source") != "manual":
                raise RuntimeError(f"{arxiv_id} thiếu file nhãn source='manual'")
            try:
                from m3_manual_labeler import validate_label_file
            except ImportError:  # pragma: no cover
                from .m3_manual_labeler import validate_label_file
            valid, errors, checked_count = validate_label_file(corpus_dir, arxiv_id, min_positive=20)
            if not valid:
                raise RuntimeError(f"Manual paper {arxiv_id} không đạt validation: {'; '.join(errors[:5])}")
            manual_validation[arxiv_id] = checked_count

    selected_manual_set = set(chosen_manual)
    auto_pool: list[dict[str, Any]] = []
    auto_validation: dict[str, int] = {}
    auto_label_errors: dict[str, list[str]] = {}
    for row in rows:
        arxiv_id = _base_arxiv_id(row["arxiv_id"])
        if arxiv_id in selected_manual_set:
            continue
        if _int(row, "canonical_entries") <= 0 or _int(row, "references_with_arxiv_id") <= 0:
            continue
        if not _has_m3_inputs(corpus_dir, arxiv_id):
            continue
        label_data = _read_labels(labels_dir, arxiv_id)
        if require_auto_labels:
            if not isinstance(label_data, dict) or label_data.get("source") != "auto":
                continue
            try:
                from m3_manual_labeler import validate_label_file
            except ImportError:  # pragma: no cover
                from .m3_manual_labeler import validate_label_file
            valid, errors, checked_count = validate_label_file(corpus_dir, arxiv_id, min_positive=1)
            if not valid:
                auto_label_errors[arxiv_id] = errors
                continue
            auto_validation[arxiv_id] = checked_count
        auto_pool.append(row)

    auto_pool.sort(key=lambda row: (
        _identity_coverage(corpus_dir, _base_arxiv_id(row["arxiv_id"]),
                           _read_labels(labels_dir, _base_arxiv_id(row["arxiv_id"]))),
        _row_score(row),
    ), reverse=True)
    if len(auto_pool) < auto_target and not allow_insufficient_auto:
        raise RuntimeError(
            f"Range {member}: auto pool đủ điều kiện chỉ có {len(auto_pool)} bài, "
            f"cần tối thiểu {auto_target} (10% của {total_papers}-5, buffer {auto_buffer:.0%}). "
            "Dùng --allow-insufficient-auto chỉ để tạo split thử nghiệm, không phải xác nhận đạt yêu cầu."
        )
    selected_auto_rows = auto_pool[:auto_target]
    selected_auto = [_base_arxiv_id(row["arxiv_id"]) for row in selected_auto_rows]
    if len(selected_auto) < 2:
        raise RuntimeError("Cần ít nhất 2 bài auto để dành một bài test và một bài validation")

    # Keep the test/valid fold random and reproducible within the selected
    # auto pool. Selecting only the two highest-evidence papers would bias the
    # evaluation toward the easiest automatically labeled examples.
    rng = random.Random(seed)
    manual_test_valid = list(chosen_manual)
    rng.shuffle(manual_test_valid)
    auto_test_valid_ids = list(selected_auto)
    rng.shuffle(auto_test_valid_ids)
    manual_test, manual_valid = manual_test_valid[:2]
    auto_test, auto_valid = auto_test_valid_ids[:2]

    result = {
        "seed": int(seed),
        "members": {
            member: {
                "range": f"{MEMBER_RANGES[member][0]}-{MEMBER_RANGES[member][1]}",
                "manual": chosen_manual,
                "manual_backup_candidates": manual_backup_ids,
                "auto": selected_auto,
                "test": {"manual": [manual_test], "auto": [auto_test]},
                "valid": {"manual": [manual_valid], "auto": [auto_valid]},
                "selection_metadata": {
                    "total_papers_in_range": total_papers,
                    "total_papers_source": total_source,
                    "papers_without_m2_stats": missing_m2_stats,
                    "m2_stats_rows_in_range": len(stats_rows_in_range),
                    "manual_min_refs_with_arxiv_id": manual_min_refs,
                    "manual_positive_counts_if_labeled": manual_validation,
                    "manual_labels_verified": bool(require_manual_labels),
                    "manual_pool_size": len(manual_pool),
                    "manual_backup_candidates_count": len(manual_backup_ids),
                    "auto_minimum_by_plan": auto_minimum,
                    "auto_selected_with_buffer": len(selected_auto),
                    "auto_pool_size": len(auto_pool),
                    "auto_buffer": auto_buffer,
                    "auto_labels_verified": bool(require_auto_labels),
                    "auto_positive_counts_if_labeled": auto_validation,
                    "auto_invalid_label_files": auto_label_errors,
                    "provisional_until_labels_checked": not (require_manual_labels and require_auto_labels),
                },
            }
        },
    }
    return result


def partition_map(split: dict[str, Any], member: str) -> dict[str, str]:
    data = split.get("members", {}).get(member)
    if not isinstance(data, dict):
        raise KeyError(f"Không có member {member} trong split.json")
    manual = list(data.get("manual", []) or [])
    auto = list(data.get("auto", []) or [])
    if len(manual) != len(set(manual)) or len(auto) != len(set(auto)):
        raise ValueError("split.json có ID bị lặp trong danh sách manual hoặc auto")
    overlap = set(manual) & set(auto)
    if overlap:
        raise ValueError(f"Paper xuất hiện đồng thời trong manual và auto: {sorted(overlap)[:10]}")
    result: dict[str, str] = {}
    for partition in ("test", "valid"):
        values = data.get(partition, {})
        if not isinstance(values, dict):
            raise ValueError(f"split.json[{member!r}][{partition!r}] phải là object")
        for source, allowed in (("manual", set(manual)), ("auto", set(auto))):
            partition_ids = list(values.get(source, []) or [])
            if len(partition_ids) != len(set(partition_ids)):
                raise ValueError(f"split.json có ID lặp trong {partition}.{source}")
            for arxiv_id in partition_ids:
                if arxiv_id not in allowed:
                    raise ValueError(f"Paper {arxiv_id} trong {partition}.{source} không thuộc danh sách {source}")
                if arxiv_id in result:
                    raise ValueError(f"Paper {arxiv_id} xuất hiện ở nhiều partition")
                result[arxiv_id] = partition
    selected = set(manual) | set(auto)
    for arxiv_id in selected:
        result.setdefault(arxiv_id, "train")
    return result


def partition_of(arxiv_id: str, member: str = "Quoc", split_file: Path | None = None) -> str | None:
    """Return train/valid/test for a selected paper; otherwise ``None``."""
    path = split_file or Path("data/m3/split.json")
    if not path.exists():
        return None
    split = _read_json(path)
    mapping = partition_map(split, member)
    return mapping.get(_base_arxiv_id(arxiv_id))


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chọn papers M3 theo member range và tạo split.json")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--member", choices=MEMBERS, default="Quoc")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--auto-buffer", type=float, default=0.05, help="Buffer tương đối; mặc định 0.05 = +5%%")
    parser.add_argument("--manual-min-refs", type=int, default=22)
    parser.add_argument("--manual-ids", help="5 arXiv ID đã gán tay và kiểm tra, cách nhau bởi dấu phẩy")
    parser.add_argument("--require-manual-labels", action="store_true", help="Bắt buộc mỗi manual label file có >=20 nhãn dương")
    parser.add_argument("--require-auto-labels", action="store_true", help="Chỉ chọn auto paper đã có ít nhất một positive auto label")
    parser.add_argument("--allow-insufficient-auto", action="store_true", help="Chỉ dùng để tạo split thử; không xác nhận đạt quota")
    parser.add_argument("--output", type=Path, help="Mặc định: <corpus>/m3/split.json")
    parser.add_argument("--csv", type=Path, help="Xuất sơ bộ pool manual/auto để kiểm tra")
    args = parser.parse_args()

    manual_ids = [item.strip() for item in args.manual_ids.split(",") if item.strip()] if args.manual_ids else None
    split = build_split(
        args.corpus_dir, member=args.member, seed=args.seed,
        auto_buffer=args.auto_buffer, manual_min_refs=args.manual_min_refs,
        manual_ids=manual_ids, require_manual_labels=args.require_manual_labels,
        require_auto_labels=args.require_auto_labels,
        allow_insufficient_auto=args.allow_insufficient_auto,
    )
    output = args.output or (args.corpus_dir / "m3" / "split.json")
    _write_json_atomic(output, split)
    member_data = split["members"][args.member]
    meta = member_data["selection_metadata"]
    print(f"Member: {args.member} | range: {member_data['range']}")
    print(f"Manual: {len(member_data['manual'])}/5 (sơ tuyển refs >= {args.manual_min_refs})")
    print(f"Auto: {len(member_data['auto'])} | pool={meta['auto_pool_size']} | quota tối thiểu={meta['auto_minimum_by_plan']}")
    print(f"Test: manual={member_data['test']['manual'][0]}, auto={member_data['test']['auto'][0]}")
    print(f"Valid: manual={member_data['valid']['manual'][0]}, auto={member_data['valid']['auto'][0]}")
    print(f"Đã ghi split: {output}")
    if meta["provisional_until_labels_checked"]:
        print("CẢNH BÁO: split còn provisional. Cần xác nhận mỗi manual paper có >=20 positive label; refs>=22 chỉ là cận trên.")
    if args.csv:
        rows = []
        for source in ("manual", "auto"):
            ids = member_data[source]
            for aid in ids:
                rows.append({"arxiv_id": aid, "source": source, "partition": partition_map(split, args.member).get(aid)})
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["arxiv_id", "source", "partition"])
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
