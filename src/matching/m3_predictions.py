"""Build the M3 predictions table from model scores and non-null labels.

Output columns follow M3_PLAN.md / schemas.PREDICTIONS_SCHEMA exactly:
``arxiv_id, partition, citation_key, groundtruth_arxiv_id, cand_1..cand_5``.
One row is emitted for each non-null labeled entry in a paper from this
member's split; null/unannotated labels are excluded.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

try:
    from m3_pick_papers import MEMBERS, in_range, paper_key, partition_map
except ImportError:  # pragma: no cover
    from .m3_pick_papers import MEMBERS, in_range, paper_key, partition_map
try:
    from m3_candidates import normalize_arxiv_id
except ImportError:  # pragma: no cover
    from .m3_candidates import normalize_arxiv_id
try:
    from m3_manual_labeler import validate_label_data, _read_json_unique_keys
except ImportError:  # pragma: no cover
    from .m3_manual_labeler import validate_label_data, _read_json_unique_keys

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
try:  # project integration: use the official Arrow schema when available
    from utils.schemas import validate_predictions as _validate_schema_predictions, write_parquet as _write_schema_parquet
except Exception:  # pragma: no cover - allows logic tests without pyarrow
    _validate_schema_predictions = None
    _write_schema_parquet = None

PREDICTIONS_COLUMNS = [
    "arxiv_id", "partition", "citation_key", "groundtruth_arxiv_id",
    "cand_1", "cand_2", "cand_3", "cand_4", "cand_5",
]
SCORE_COLUMNS = {"arxiv_id", "citation_key", "ref_arxiv_id", "score"}


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được JSON {path}: {exc}") from exc


def _norm_id(value: Any) -> str | None:
    return normalize_arxiv_id(value) or (str(value).strip() if value is not None else None)


def _read_refs(path: Path) -> list[dict[str, Any]]:
    data = _read_json(path, [])
    if isinstance(data, dict):
        data = data.get("references", [])
    return data if isinstance(data, list) else []


def _valid_ref_ids(refs: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for ref in refs:
        value = ref.get("ref_arxiv_id") or ref.get("arxiv_id")
        if not value:
            ext = ref.get("external_ids") or ref.get("externalIds") or {}
            if isinstance(ext, dict):
                value = ext.get("ArXiv") or ext.get("arxiv")
        normalized = _norm_id(value) if value else None
        if normalized:
            result.add(normalized)
    return result


def load_scores(path: Path, model_version: str | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy scores.parquet: {path}")
    frame = pd.read_parquet(path)
    missing = SCORE_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"scores.parquet thiếu cột {sorted(missing)}")
    frame = frame.copy()
    for col in ("arxiv_id", "citation_key", "ref_arxiv_id"):
        frame[col] = frame[col].astype("string")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    if "model_version" in frame.columns:
        versions = sorted(frame["model_version"].dropna().astype(str).unique().tolist())
        if model_version is None and len(versions) > 1:
            raise ValueError(f"scores.parquet có nhiều model_version {versions}; hãy truyền --model-version")
        if model_version is not None:
            frame = frame[frame["model_version"].astype(str) == model_version]
    elif model_version is not None:
        raise ValueError("scores.parquet không có cột model_version để lọc")
    frame["_ref_norm"] = frame["ref_arxiv_id"].map(_norm_id)
    return frame


def build_predictions(
    corpus_dir: Path,
    scores_path: Path,
    split_path: Path,
    member: str = "Quoc",
    labels_dir: Path | None = None,
    model_version: str | None = None,
    allow_missing_label_files: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    split = _read_json(split_path)
    if not isinstance(split, dict) or not isinstance(split.get("members"), dict):
        raise ValueError("split.json không đúng cấu trúc members")
    member_data = split["members"].get(member)
    if not isinstance(member_data, dict):
        raise ValueError(f"Không có member {member} trong split.json")
    if member not in MEMBERS:
        raise ValueError(f"Member không hợp lệ: {member}")
    labels_dir = labels_dir or (corpus_dir / "m3" / "labels")
    scores = load_scores(scores_path, model_version=model_version)
    partitions = partition_map(split, member)
    selected_ids = sorted(set(member_data.get("manual", [])) | set(member_data.get("auto", [])))
    rows: list[dict[str, Any]] = []
    missing_label_files: list[str] = []
    null_entries = 0
    positive_entries = 0

    for arxiv_id in selected_ids:
        if not in_range(arxiv_id, member):
            raise ValueError(f"Paper {arxiv_id} nằm ngoài range {member}")
        partition = partitions.get(arxiv_id)
        if partition not in {"train", "valid", "test"}:
            raise ValueError(f"Paper {arxiv_id} chưa được gán partition")
        label_path = labels_dir / f"{paper_key(arxiv_id)}.json"
        label_data = _read_json_unique_keys(label_path)
        if not isinstance(label_data, dict):
            missing_label_files.append(arxiv_id)
            continue
        paper_dir = corpus_dir / paper_key(arxiv_id)
        bib = _read_json(paper_dir / "bibtex_merged.json", [])
        refs = _read_refs(paper_dir / "refs.json")
        if not isinstance(bib, list):
            raise ValueError(f"{paper_dir / 'bibtex_merged.json'} không phải list")
        errors = validate_label_data(label_data, bib, refs)
        if str(label_data.get("arxiv_id")) != str(arxiv_id):
            errors.append(f"arxiv_id trong file nhãn không khớp paper {arxiv_id}")
        if errors:
            raise ValueError(f"File nhãn không hợp lệ ({label_path}):\n- " + "\n- ".join(errors))
        valid_refs = _valid_ref_ids(refs)
        labels = label_data.get("labels", {})
        for citation_key, groundtruth in labels.items():
            if groundtruth is None:
                null_entries += 1
                continue
            positive_entries += 1
            groundtruth_id = _norm_id(groundtruth)
            if groundtruth_id not in valid_refs:
                raise ValueError(f"{arxiv_id}/{citation_key}: groundtruth {groundtruth_id} không thuộc refs.json")
            subset = scores[
                (scores["arxiv_id"] == arxiv_id)
                & (scores["citation_key"] == str(citation_key))
            ].copy()
            if not subset.empty:
                subset = subset.sort_values(
                    by=["score", "_ref_norm", "candidate_id"] if "candidate_id" in subset.columns else ["score", "_ref_norm"],
                    ascending=[False, True, True] if "candidate_id" in subset.columns else [False, True],
                    na_position="last",
                    kind="mergesort",
                )
                subset = subset.drop_duplicates(subset=["_ref_norm"], keep="first")
            ranked: list[str] = []
            for candidate_id in subset["_ref_norm"].tolist() if not subset.empty else []:
                if candidate_id and candidate_id not in ranked:
                    ranked.append(candidate_id)
                if len(ranked) >= 5:
                    break
            row = {
                "arxiv_id": arxiv_id,
                "partition": partition,
                "citation_key": str(citation_key),
                "groundtruth_arxiv_id": groundtruth_id,
            }
            invalid_candidates = [candidate_id for candidate_id in ranked if candidate_id not in valid_refs]
            if invalid_candidates:
                raise ValueError(
                    f"{arxiv_id}/{citation_key}: candidates không thuộc refs.json: {invalid_candidates[:5]}"
                )
            for index in range(5):
                row[f"cand_{index + 1}"] = ranked[index] if index < len(ranked) else None
            rows.append(row)

    if missing_label_files and not allow_missing_label_files:
        raise FileNotFoundError(
            "Thiếu file nhãn cho các paper trong split: " + ", ".join(missing_label_files[:20])
            + (" ..." if len(missing_label_files) > 20 else "")
            + ". Dùng --allow-missing-label-files chỉ khi đang chạy thử từng phần."
        )
    frame = pd.DataFrame(rows, columns=PREDICTIONS_COLUMNS)
    for column in PREDICTIONS_COLUMNS:
        frame[column] = frame[column].astype("string")
    errors = validate_predictions(frame, partitions_by_id=partitions)
    if errors:
        raise ValueError("Predictions validation failed:\n- " + "\n- ".join(errors[:40]))
    if _validate_schema_predictions is not None:
        allowed_ids = set(selected_ids)
        _validate_schema_predictions(
            frame, allowed_arxiv_ids=allowed_ids, partitions_by_id=partitions
        )
    report = {
        "member": member,
        "selected_papers": len(selected_ids),
        "papers_missing_label_files": missing_label_files,
        "positive_entries_written": len(frame),
        "positive_entries_seen": positive_entries,
        "null_entries_excluded": null_entries,
        "partition_counts": frame["partition"].value_counts(dropna=False).to_dict() if not frame.empty else {},
        "columns": PREDICTIONS_COLUMNS,
        "model_version": model_version,
    }
    return frame, report


def validate_predictions(
    predictions: pd.DataFrame | list[dict[str, Any]],
    partitions_by_id: dict[str, str] | None = None,
) -> list[str]:
    """Validate the exact predictions contract. Returns a list of errors."""
    errors: list[str] = []
    if isinstance(predictions, pd.DataFrame):
        columns = list(predictions.columns)
        records = predictions.to_dict("records")
    else:
        records = list(predictions)
        columns = list(records[0].keys()) if records else PREDICTIONS_COLUMNS
    missing = [column for column in PREDICTIONS_COLUMNS if column not in columns]
    extra = [column for column in columns if column not in PREDICTIONS_COLUMNS]
    if missing:
        errors.append(f"Thiếu cột: {missing}")
    if extra:
        errors.append(f"Cột ngoài schema: {extra}")
    seen_rows: set[tuple[str, str]] = set()
    for index, row in enumerate(records):
        arxiv_id = str(row.get("arxiv_id") or "")
        partition = str(row.get("partition") or "")
        citation_key = str(row.get("citation_key") or "")
        groundtruth = row.get("groundtruth_arxiv_id")
        if not arxiv_id or not citation_key or not groundtruth:
            errors.append(f"Dòng {index}: arxiv_id, citation_key và groundtruth_arxiv_id phải có giá trị")
        if partition not in {"train", "valid", "test"}:
            errors.append(f"Dòng {index}: partition không hợp lệ: {partition!r}")
        if partitions_by_id is not None and partitions_by_id.get(arxiv_id) != partition:
            errors.append(f"Dòng {index}: partition {partition} không khớp split của {arxiv_id}")
        key = (arxiv_id, citation_key)
        if key in seen_rows:
            errors.append(f"Dòng {index}: duplicate prediction entry {key}")
        seen_rows.add(key)
        candidates = [str(row.get(f"cand_{k}")) for k in range(1, 6) if row.get(f"cand_{k}") is not None and not pd.isna(row.get(f"cand_{k}"))]
        normalized = [_norm_id(value) for value in candidates]
        if len(normalized) != len(set(normalized)):
            errors.append(f"Dòng {index}: cand_1..cand_5 bị trùng")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo predictions.parquet từ scores + labels + split")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--scores", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--labels-dir", type=Path)
    parser.add_argument("--member", choices=MEMBERS, default="Quoc")
    parser.add_argument("--model-version")
    parser.add_argument("--allow-missing-label-files", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    scores_path = args.scores or (args.corpus_dir / "m3" / "scores.parquet")
    split_path = args.split or (args.corpus_dir / "m3" / "split.json")
    frame, report = build_predictions(
        args.corpus_dir, scores_path, split_path, args.member, args.labels_dir,
        args.model_version, args.allow_missing_label_files,
    )
    output = args.output or (args.corpus_dir / "m3" / "predictions.parquet")
    output.parent.mkdir(parents=True, exist_ok=True)
    if _write_schema_parquet is None:
        raise RuntimeError(
            "Không nạp được utils.schemas.write_parquet. Hãy chạy từ project root "
            "với môi trường đã cài pyarrow và sử dụng src/utils/schemas.py đã cập nhật."
        )
    _write_schema_parquet(frame, "predictions", output)
    # Read the written file back and validate the physical Parquet schema too.
    try:
        import pyarrow.parquet as pq
        from utils.schemas import validate_predictions as validate_arrow_predictions
        arrow_table = pq.read_table(output)
        split_data = _read_json(split_path)
        partition_lookup = partition_map(split_data, args.member)
        selected_ids = set(split_data["members"][args.member].get("manual", [])) | set(
            split_data["members"][args.member].get("auto", [])
        )
        validate_arrow_predictions(
            arrow_table,
            allowed_arxiv_ids=selected_ids,
            partitions_by_id=partition_lookup,
        )
    except Exception as exc:
        raise RuntimeError(f"Parquet predictions ghi xong nhưng kiểm tra đọc lại thất bại: {exc}") from exc
    report_path = args.report or output.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Đã ghi {len(frame)} rows: {output}")


if __name__ == "__main__":
    main()
