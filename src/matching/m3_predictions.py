"""Build the M3 Top-5 predictions table from model scores and labels.

This module combines:
- the robust, per-member label/reference validation from the P1 implementation;
- the stable score ranking and pure helper API used by the main branch;
- the run_predictions_pipeline(data_dir=...) interface required by src/run_m3.py.

The persisted Parquet table contains exactly the 9 columns in
utils.schemas.PREDICTIONS_SCHEMA. Null/unannotated labels are excluded, while a
positive label with no scored candidate is retained with cand_1..cand_5 = null.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    from m3_pick_papers import MEMBERS, in_range, paper_key, partition_map
except ImportError:  # pragma: no cover - package import path
    from .m3_pick_papers import MEMBERS, in_range, paper_key, partition_map

try:
    from m3_candidates import normalize_arxiv_id
except ImportError:  # pragma: no cover - package import path
    from .m3_candidates import normalize_arxiv_id

try:
    from m3_manual_labeler import validate_label_data, _read_json_unique_keys
except ImportError:  # pragma: no cover - package import path
    from .m3_manual_labeler import validate_label_data, _read_json_unique_keys

from utils import config as C  # noqa: E402

try:
    from utils.schemas import (
        PREDICTIONS_SCHEMA,
        validate_predictions as _validate_schema_predictions,
        write_parquet as _write_schema_parquet,
    )
except Exception:  # pragma: no cover - gives a clear error when project deps are missing
    PREDICTIONS_SCHEMA = None
    _validate_schema_predictions = None
    _write_schema_parquet = None


K = 5
Key = Tuple[str, str]  # (arxiv_id, citation_key)

PREDICTIONS_COLUMNS = [
    "arxiv_id",
    "partition",
    "citation_key",
    "groundtruth_arxiv_id",
    "cand_1",
    "cand_2",
    "cand_3",
    "cand_4",
    "cand_5",
]
SCORE_COLUMNS = {"arxiv_id", "citation_key", "ref_arxiv_id", "score"}


def _read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được JSON {path}: {exc}") from exc


def _norm_id(value: Any) -> Optional[str]:
    """Normalize an arXiv ID; preserve a non-empty unknown identifier as text."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return normalize_arxiv_id(text) or text


def _read_refs(path: Path) -> List[dict[str, Any]]:
    data = _read_json(path, [])
    if isinstance(data, dict):
        data = data.get("references", [])
    return data if isinstance(data, list) else []


def _valid_ref_ids(refs: List[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for ref in refs:
        value = ref.get("ref_arxiv_id") or ref.get("arxiv_id")
        if not value:
            external = ref.get("external_ids") or ref.get("externalIds") or {}
            if isinstance(external, dict):
                value = external.get("ArXiv") or external.get("arxiv")
        normalized = _norm_id(value) if value else None
        if normalized:
            result.add(normalized)
    return result


def load_scores(path: Path, model_version: Optional[str] = None) -> pd.DataFrame:
    """Load and normalize scores.parquet, rejecting ambiguous model versions."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy scores.parquet: {path}")

    frame = pd.read_parquet(path)
    missing = SCORE_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"scores.parquet thiếu cột {sorted(missing)}")

    frame = frame.copy()
    frame["arxiv_id"] = frame["arxiv_id"].map(_norm_id).astype("string")
    frame["citation_key"] = frame["citation_key"].astype("string").str.strip()
    frame["ref_arxiv_id"] = frame["ref_arxiv_id"].map(_norm_id).astype("string")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")

    if "model_version" in frame.columns:
        versions = sorted(
            frame["model_version"].dropna().astype(str).unique().tolist()
        )
        if model_version is None and len(versions) > 1:
            raise ValueError(
                f"scores.parquet có nhiều model_version {versions}; "
                "hãy truyền --model-version"
            )
        if model_version is not None:
            frame = frame[
                frame["model_version"].astype(str) == str(model_version)
            ]
    elif model_version is not None:
        raise ValueError(
            "scores.parquet không có cột model_version để lọc"
        )

    frame["_ref_norm"] = frame["ref_arxiv_id"].map(_norm_id)
    return frame


# ---------------------------------------------------------------------------
# Backward-compatible pure helpers from the main branch.
# These are used by existing unit tests and can also be used independently.
# ---------------------------------------------------------------------------

def load_labels(*paths: Path) -> Dict[Key, Optional[str]]:
    """Load legacy manual_labels.csv / auto_labels.csv files."""
    truth: Dict[Key, Optional[str]] = {}
    for path in paths:
        with Path(path).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (
                    str(row["arxiv_id"]).strip(),
                    str(row["citation_key"]).strip(),
                )
                truth[key] = (
                    str(row.get("groundtruth_arxiv_id") or "").strip() or None
                )
    return truth


def top_k_by_entry(
    scored: Iterable[dict],
    k: int = K,
) -> Dict[Key, List[str]]:
    """Rank scores per BibTeX entry, collapse duplicate references, return top-k."""
    if k < 1:
        raise ValueError("k phải >= 1")

    groups: Dict[Key, List[dict]] = defaultdict(list)
    for row in scored:
        arxiv_id = str(row.get("arxiv_id") or "").strip()
        citation_key = str(row.get("citation_key") or "").strip()
        ref_id = row.get("ref_arxiv_id")
        if not arxiv_id or not citation_key or _norm_id(ref_id) is None:
            continue
        groups[(arxiv_id, citation_key)].append(row)

    result: Dict[Key, List[str]] = {}
    for key, rows in groups.items():
        rows.sort(
            key=lambda row: (
                -float(row["score"]),
                str(_norm_id(row["ref_arxiv_id"])),
            )
        )
        picked: List[str] = []
        for row in rows:
            ref_id = _norm_id(row["ref_arxiv_id"])
            if ref_id and ref_id not in picked:
                picked.append(ref_id)
            if len(picked) >= k:
                break
        result[key] = picked
    return result


def build_predictions(
    scored: Iterable[dict],
    entries: Iterable[Key],
    truth: Optional[Dict[Key, Optional[str]]] = None,
    k: int = K,
    partitions: Optional[Dict[str, str]] = None,
    drop_no_answer: bool = False,
) -> List[dict]:
    """Backward-compatible pure builder used by the main-branch tests.

    Returned rows include predicted_arxiv_ids for the evaluator. That helper
    field is deliberately removed by write_predictions_parquet before saving.
    """
    truth = truth or {}
    partitions = partitions or {}
    top = top_k_by_entry(scored, k)
    rows: List[dict] = []

    for arxiv_id, citation_key in sorted(set(entries)):
        key = (arxiv_id, citation_key)
        groundtruth = truth.get(key)
        if drop_no_answer and not groundtruth:
            continue

        predictions = top.get(key, [])
        row = {
            "arxiv_id": arxiv_id,
            "partition": partitions.get(arxiv_id, "train"),
            "citation_key": citation_key,
            "groundtruth_arxiv_id": groundtruth,
            "predicted_arxiv_ids": predictions,
        }
        for index in range(1, K + 1):
            row[f"cand_{index}"] = (
                predictions[index - 1] if index <= len(predictions) else None
            )
        rows.append(row)

    return rows


def write_predictions_parquet(rows: List[dict], out_path: Path) -> int:
    """Write exactly the 9 schema columns; ignore evaluator-only helper fields."""
    if _write_schema_parquet is None or PREDICTIONS_SCHEMA is None:
        raise RuntimeError(
            "Không nạp được utils.schemas. Hãy chạy từ project root "
            "trong môi trường đã cài pyarrow."
        )

    frame = (
        pd.DataFrame(rows)
        if rows
        else pd.DataFrame(columns=PREDICTIONS_SCHEMA.names)
    )
    frame = frame.reindex(columns=PREDICTIONS_SCHEMA.names)
    for column in PREDICTIONS_SCHEMA.names:
        frame[column] = frame[column].astype("string")

    _write_schema_parquet(frame, "predictions", Path(out_path))
    return len(frame)


# ---------------------------------------------------------------------------
# P1 corpus-aware builder. Keep this separate from build_predictions() so the
# public pure-helper API above remains compatible with the main branch.
# ---------------------------------------------------------------------------

def validate_predictions(
    predictions: pd.DataFrame | List[dict[str, Any]],
    partitions_by_id: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Validate the exact 9-column prediction contract; return all errors."""
    if isinstance(predictions, pd.DataFrame):
        columns = list(predictions.columns)
        records = predictions.to_dict("records")
    else:
        records = list(predictions)
        columns = list(records[0].keys()) if records else PREDICTIONS_COLUMNS

    errors: List[str] = []
    missing = [column for column in PREDICTIONS_COLUMNS if column not in columns]
    extra = [column for column in columns if column not in PREDICTIONS_COLUMNS]
    if missing:
        errors.append(f"Thiếu cột: {missing}")
    if extra:
        errors.append(f"Cột ngoài schema: {extra}")

    seen_rows: set[Key] = set()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            errors.append(f"Dòng {index} không phải dict")
            continue

        arxiv_id = _norm_id(row.get("arxiv_id"))
        partition = str(row.get("partition") or "").strip()
        citation_key = str(row.get("citation_key") or "").strip()
        groundtruth = _norm_id(row.get("groundtruth_arxiv_id"))

        if not arxiv_id or not citation_key or not groundtruth:
            errors.append(
                f"Dòng {index}: arxiv_id, citation_key và "
                "groundtruth_arxiv_id phải có giá trị"
            )

        if partition not in {"train", "valid", "test"}:
            errors.append(
                f"Dòng {index}: partition không hợp lệ: {partition!r}"
            )

        if partitions_by_id is not None and arxiv_id:
            expected_partition = partitions_by_id.get(arxiv_id)
            if expected_partition != partition:
                errors.append(
                    f"Dòng {index}: partition {partition!r} không khớp "
                    f"split của {arxiv_id}"
                )

        if arxiv_id and citation_key:
            key = (arxiv_id, citation_key)
            if key in seen_rows:
                errors.append(f"Dòng {index}: duplicate prediction entry {key}")
            seen_rows.add(key)

        candidates = [
            _norm_id(row.get(f"cand_{column}"))
            for column in range(1, K + 1)
        ]
        candidates = [value for value in candidates if value]
        if len(candidates) != len(set(candidates)):
            errors.append(
                f"Dòng {index}: cand_1..cand_5 bị trùng ứng viên"
            )

    return errors


def build_predictions_from_corpus(
    corpus_dir: Path,
    scores_path: Path,
    split_path: Path,
    member: str = "Quoc",
    labels_dir: Optional[Path] = None,
    model_version: Optional[str] = None,
    allow_missing_label_files: bool = False,
    scores_df: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build predictions for one member, validating labels against raw M1/M2 data.

    If both per-paper bibtex_merged.json and refs.json exist, full cross-file
    label/reference validation is performed. Small Parquet-only test fixtures
    may omit both raw files; in that case basic label shape and ID validation
    is still enforced, and the report records that cross-file validation was
    unavailable.
    """
    corpus_dir = Path(corpus_dir)
    split_path = Path(split_path)

    split = _read_json(split_path)
    if not isinstance(split, dict) or not isinstance(split.get("members"), dict):
        raise ValueError("split.json không đúng cấu trúc members")

    member_data = split["members"].get(member)
    if not isinstance(member_data, dict):
        raise ValueError(f"Không có member {member} trong split.json")
    if member not in MEMBERS:
        raise ValueError(f"Member không hợp lệ: {member}")

    labels_dir = (
        Path(labels_dir)
        if labels_dir is not None
        else corpus_dir / "m3" / "labels"
    )
    scores = (
        scores_df.copy()
        if scores_df is not None
        else load_scores(scores_path, model_version=model_version)
    )
    raw_partitions = partition_map(split, member)
    partitions: Dict[str, str] = {}
    for raw_id, partition in raw_partitions.items():
        normalized_id = _norm_id(raw_id)
        if normalized_id is None:
            raise ValueError(f"split.json chứa arxiv_id không hợp lệ: {raw_id!r}")
        previous = partitions.get(normalized_id)
        if previous is not None and previous != partition:
            raise ValueError(
                f"Paper {normalized_id} bị gán vào nhiều partition khác nhau"
            )
        partitions[normalized_id] = partition

    selected_ids = sorted(
        {
            normalized_id
            for raw_id in (
                list(member_data.get("manual", []) or [])
                + list(member_data.get("auto", []) or [])
            )
            if (normalized_id := _norm_id(raw_id)) is not None
        }
    )
    rows: List[dict[str, Any]] = []
    missing_label_files: List[str] = []
    papers_without_raw_validation: List[str] = []
    null_entries = 0
    positive_entries = 0

    for raw_arxiv_id in selected_ids:
        arxiv_id = str(raw_arxiv_id)
        if not in_range(arxiv_id, member):
            raise ValueError(f"Paper {arxiv_id} nằm ngoài range {member}")

        partition = partitions.get(arxiv_id)
        if partition not in {"train", "valid", "test"}:
            raise ValueError(
                f"Paper {arxiv_id} chưa được gán partition hợp lệ"
            )

        label_path = labels_dir / f"{paper_key(arxiv_id)}.json"
        label_data = _read_json_unique_keys(label_path)
        if not isinstance(label_data, dict):
            missing_label_files.append(arxiv_id)
            continue

        if _norm_id(label_data.get("arxiv_id")) != _norm_id(arxiv_id):
            raise ValueError(
                f"arxiv_id trong file nhãn {label_path} "
                f"không khớp paper {arxiv_id}"
            )

        labels = label_data.get("labels")
        if not isinstance(labels, dict):
            raise ValueError(f"{label_path}: labels phải là object/dict")
        if label_data.get("source") not in {"manual", "auto"}:
            raise ValueError(
                f"{label_path}: source phải là 'manual' hoặc 'auto'"
            )

        paper_dir = corpus_dir / paper_key(arxiv_id)
        bib_path = paper_dir / "bibtex_merged.json"
        refs_path = paper_dir / "refs.json"
        has_bib = bib_path.is_file()
        has_refs = refs_path.is_file()

        if has_bib != has_refs:
            raise FileNotFoundError(
                f"{arxiv_id}: cần có đồng thời bibtex_merged.json và refs.json "
                "để kiểm tra đối chiếu nhãn"
            )

        valid_refs: Optional[set[str]] = None
        if has_bib and has_refs:
            bib = _read_json(bib_path, [])
            refs = _read_refs(refs_path)
            if not isinstance(bib, list):
                raise ValueError(f"{bib_path} không phải JSON list")

            label_errors = validate_label_data(label_data, bib, refs)
            if label_errors:
                raise ValueError(
                    f"File nhãn không hợp lệ ({label_path}):\n- "
                    + "\n- ".join(label_errors[:30])
                )
            valid_refs = _valid_ref_ids(refs)
        else:
            # Parquet-only fixtures do not contain the per-paper source files.
            # Keep their use possible, but make the reduced validation visible.
            papers_without_raw_validation.append(arxiv_id)

        for citation_key, groundtruth in labels.items():
            citation_key = str(citation_key)
            if groundtruth is None:
                null_entries += 1
                continue

            positive_entries += 1
            groundtruth_id = _norm_id(groundtruth)
            if not groundtruth_id:
                raise ValueError(
                    f"{arxiv_id}/{citation_key}: groundtruth_arxiv_id rỗng hoặc không hợp lệ"
                )

            if valid_refs is not None and groundtruth_id not in valid_refs:
                raise ValueError(
                    f"{arxiv_id}/{citation_key}: groundtruth {groundtruth_id} "
                    "không thuộc refs.json"
                )

            normalized_paper_id = _norm_id(arxiv_id)
            subset = scores[
                (scores["arxiv_id"] == normalized_paper_id)
                & (scores["citation_key"] == citation_key)
            ].copy()

            if not subset.empty:
                subset = subset[
                    subset["_ref_norm"].notna()
                    & (subset["_ref_norm"].astype(str).str.strip() != "")
                ].copy()

            if not subset.empty:
                sort_columns = ["score", "_ref_norm"]
                ascending = [False, True]
                if "candidate_id" in subset.columns:
                    sort_columns.append("candidate_id")
                    ascending.append(True)

                subset = subset.sort_values(
                    by=sort_columns,
                    ascending=ascending,
                    na_position="last",
                    kind="mergesort",
                )
                subset = subset.drop_duplicates(
                    subset=["_ref_norm"], keep="first"
                )

            ranked: List[str] = []
            if not subset.empty:
                for candidate_id in subset["_ref_norm"].tolist():
                    normalized_candidate = _norm_id(candidate_id)
                    if normalized_candidate and normalized_candidate not in ranked:
                        ranked.append(normalized_candidate)
                    if len(ranked) >= K:
                        break

            if valid_refs is not None:
                invalid_candidates = [
                    candidate_id
                    for candidate_id in ranked
                    if candidate_id not in valid_refs
                ]
                if invalid_candidates:
                    raise ValueError(
                        f"{arxiv_id}/{citation_key}: candidates không thuộc "
                        f"refs.json: {invalid_candidates[:5]}"
                    )

            row = {
                "arxiv_id": normalized_paper_id,
                "partition": partition,
                "citation_key": citation_key,
                "groundtruth_arxiv_id": groundtruth_id,
            }
            for index in range(1, K + 1):
                row[f"cand_{index}"] = (
                    ranked[index - 1] if index <= len(ranked) else None
                )
            rows.append(row)

    if missing_label_files and not allow_missing_label_files:
        raise FileNotFoundError(
            "Thiếu file nhãn cho các paper trong split: "
            + ", ".join(missing_label_files[:20])
            + (" ..." if len(missing_label_files) > 20 else "")
            + ". Dùng --allow-missing-label-files chỉ khi đang chạy thử từng phần."
        )

    frame = pd.DataFrame(rows, columns=PREDICTIONS_COLUMNS)
    for column in PREDICTIONS_COLUMNS:
        frame[column] = frame[column].astype("string")

    errors = validate_predictions(frame, partitions_by_id=partitions)
    if errors:
        raise ValueError(
            "Predictions validation failed:\n- "
            + "\n- ".join(errors[:40])
        )

    if _validate_schema_predictions is not None:
        _validate_schema_predictions(
            frame,
            allowed_arxiv_ids=set(selected_ids),
            partitions_by_id=partitions,
        )

    report = {
        "member": member,
        "selected_papers": len(selected_ids),
        "papers_missing_label_files": missing_label_files,
        "papers_without_raw_validation": papers_without_raw_validation,
        "positive_entries_written": len(frame),
        "positive_entries_seen": positive_entries,
        "null_entries_excluded": null_entries,
        "partition_counts": (
            frame["partition"].value_counts(dropna=False).to_dict()
            if not frame.empty
            else {}
        ),
        "columns": PREDICTIONS_COLUMNS,
        "model_version": model_version,
    }
    return frame, report


def run_predictions_pipeline(
    data_dir: Path,
    scores_file: Optional[Path] = None,
    split_file: Optional[Path] = None,
    out_path: Optional[Path] = None,
    member: Optional[str] = "all",
    labels_dir: Optional[Path] = None,
    model_version: Optional[str] = None,
    allow_missing_label_files: bool = True,
) -> Dict[str, Any]:
    """Pipeline interface required by src/run_m3.py; processes all split members by default."""
    import pyarrow.parquet as pq

    data_dir = Path(data_dir)
    scores_file = (
        Path(scores_file)
        if scores_file is not None
        else C.scores_path(data_dir)
    )
    split_file = (
        Path(split_file)
        if split_file is not None
        else C.split_path(data_dir)
    )
    out_path = (
        Path(out_path)
        if out_path is not None
        else C.predictions_path(data_dir)
    )
    labels_dir = (
        Path(labels_dir)
        if labels_dir is not None
        else C.m3_labels_dir(data_dir)
    )

    split = _read_json(split_file)
    if not isinstance(split, dict) or not isinstance(split.get("members"), dict):
        raise ValueError(f"split.json không đúng cấu trúc members: {split_file}")

    split_members = split["members"]
    if member is None or member == "all":
        unknown_members = set(split_members) - set(MEMBERS)
        if unknown_members:
            raise ValueError(
                f"split.json chứa member không được hỗ trợ: {sorted(unknown_members)}"
            )
        selected_members = [name for name in MEMBERS if name in split_members]
    else:
        if member not in MEMBERS:
            raise ValueError(f"Member không hợp lệ: {member}")
        if member not in split_members:
            raise ValueError(f"Không có member {member} trong split.json")
        selected_members = [member]

    if not selected_members:
        raise ValueError("split.json không có member nào để tạo predictions")

    scores = load_scores(scores_file, model_version=model_version)
    frames: List[pd.DataFrame] = []
    member_reports: Dict[str, dict[str, Any]] = {}
    combined_partitions: Dict[str, str] = {}
    selected_ids: set[str] = set()

    for member_name in selected_members:
        member_data = split_members[member_name]
        member_ids = {
            normalized_id
            for raw_id in (
                list(member_data.get("manual", []) or [])
                + list(member_data.get("auto", []) or [])
            )
            if (normalized_id := _norm_id(raw_id)) is not None
        }

        overlap = selected_ids & member_ids
        if overlap:
            raise ValueError(
                "Một paper xuất hiện trong split của nhiều member: "
                f"{sorted(overlap)[:10]}"
            )
        selected_ids.update(member_ids)

        member_partitions = {
            normalized_id: partition
            for raw_id, partition in partition_map(split, member_name).items()
            if (normalized_id := _norm_id(raw_id)) is not None
        }
        partition_overlap = set(combined_partitions) & set(member_partitions)
        if partition_overlap:
            raise ValueError(
                "ID bị trùng giữa partition map của các member: "
                f"{sorted(partition_overlap)[:10]}"
            )
        combined_partitions.update(member_partitions)

        frame, report = build_predictions_from_corpus(
            corpus_dir=data_dir,
            scores_path=scores_file,
            split_path=split_file,
            member=member_name,
            labels_dir=labels_dir,
            model_version=model_version,
            allow_missing_label_files=allow_missing_label_files,
            scores_df=scores,
        )
        frames.append(frame)
        member_reports[member_name] = report

    frame = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=PREDICTIONS_COLUMNS)
    )
    frame = frame.reindex(columns=PREDICTIONS_COLUMNS)
    for column in PREDICTIONS_COLUMNS:
        frame[column] = frame[column].astype("string")

    errors = validate_predictions(frame, partitions_by_id=combined_partitions)
    if errors:
        raise ValueError(
            "Predictions tổng hợp không hợp lệ:\n- "
            + "\n- ".join(errors[:50])
        )

    if _validate_schema_predictions is None or _write_schema_parquet is None:
        raise RuntimeError(
            "Không nạp được utils.schemas. Hãy chạy từ project root "
            "trong môi trường đã cài pyarrow."
        )

    _validate_schema_predictions(
        frame,
        allowed_arxiv_ids=selected_ids,
        partitions_by_id=combined_partitions,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    if temp_path.exists():
        temp_path.unlink()

    try:
        _write_schema_parquet(frame, "predictions", temp_path)
        written_table = pq.read_table(temp_path)
        _validate_schema_predictions(
            written_table,
            allowed_arxiv_ids=selected_ids,
            partitions_by_id=combined_partitions,
        )
        temp_path.replace(out_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    all_missing = [
        paper_id
        for report in member_reports.values()
        for paper_id in report["papers_missing_label_files"]
    ]
    all_without_raw = [
        paper_id
        for report in member_reports.values()
        for paper_id in report["papers_without_raw_validation"]
    ]
    report = {
        "member": member or "all",
        "members_processed": selected_members,
        "selected_papers": len(selected_ids),
        "papers_missing_label_files": all_missing,
        "papers_without_raw_validation": all_without_raw,
        "positive_entries_written": len(frame),
        "positive_entries_seen": sum(
            item["positive_entries_seen"] for item in member_reports.values()
        ),
        "null_entries_excluded": sum(
            item["null_entries_excluded"] for item in member_reports.values()
        ),
        "partition_counts": (
            frame["partition"].value_counts(dropna=False).to_dict()
            if not frame.empty
            else {}
        ),
        "columns": PREDICTIONS_COLUMNS,
        "model_version": model_version,
        "per_member": member_reports,
    }

    return {
        "out_path": out_path,
        "n_rows": len(frame),
        "rows": frame.to_dict("records"),
        "report": report,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tạo m3/predictions.parquet từ scores + labels + split"
    )
    parser.add_argument(
        "--data-dir",
        "--corpus-dir",
        dest="data_dir",
        type=Path,
        default=C.DATA_DIR,
    )
    parser.add_argument("--scores", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--labels-dir", type=Path)
    parser.add_argument("--member", choices=list(MEMBERS) + ["all"], default="Quoc")
    parser.add_argument("--model-version")
    parser.add_argument("--allow-missing-label-files", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    result = run_predictions_pipeline(
        data_dir=args.data_dir,
        scores_file=args.scores,
        split_file=args.split,
        out_path=args.output,
        member=args.member,
        labels_dir=args.labels_dir,
        model_version=args.model_version,
        allow_missing_label_files=args.allow_missing_label_files,
    )

    report_path = (
        args.report
        if args.report is not None
        else result["out_path"].with_suffix(".report.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result["report"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"[m3_predictions] Đã ghi {result['n_rows']} dòng "
        f"vào {result['out_path']}"
    )
    print(f"[m3_predictions] Báo cáo: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
