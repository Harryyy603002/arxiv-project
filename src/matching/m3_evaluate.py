"""M3 ranking evaluation: legacy MRR@5 API + score/label evaluation.

This module deliberately preserves the public functions imported by
m3_model.py on main (K, extract_predicted_ids, reciprocal_rank, mrr_at_k,
load_split, mrr_by_group, run_evaluation_pipeline) while incorporating the
more detailed score/ground-truth evaluation from the P1 implementation.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

_SRC_DIR = Path(__file__).resolve().parents[1]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils import config as C  # noqa: E402

try:
    from m3_pick_papers import MEMBER_RANGES, member_of, paper_key, partition_map
except ImportError:  # pragma: no cover - package import path
    from .m3_pick_papers import MEMBER_RANGES, member_of, paper_key, partition_map

K = 5
REQUIRED_SCORE_COLUMNS = {"arxiv_id", "citation_key", "ref_arxiv_id", "score"}
VALID_PARTITIONS = {"train", "valid", "test"}


def _is_null(value: Any) -> bool:
    """Safely recognize None, NaN and pandas nullable scalars."""
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def _clean_text(value: Any) -> str:
    return "" if _is_null(value) else str(value).strip()


def _normalize_id(value: Any) -> Optional[str]:
    """Normalize arXiv URLs, versions and IDs through the shared project config."""
    text = _clean_text(value)
    if not text:
        return None
    return C.normalize_arxiv_id(text) or text


# ---------------------------------------------------------------------------
# Legacy prediction-row API. m3_model.py imports these functions directly.
# ---------------------------------------------------------------------------

def extract_predicted_ids(row: dict, k: int = K) -> List[str]:
    """Extract top-k IDs from predicted_arxiv_ids or cand_1..cand_5 columns."""
    if k < 1:
        raise ValueError("k phải >= 1")

    raw = row.get("predicted_arxiv_ids")
    if raw is not None and not _is_null(raw):
        if isinstance(raw, (list, tuple)):
            return [
                str(value).strip()
                for value in raw
                if not _is_null(value) and str(value).strip()
            ][:k]

    predictions: List[str] = []
    for index in range(1, k + 1):
        value = row.get(f"cand_{index}")
        text = _clean_text(value)
        if text and text.lower() != "nan":
            predictions.append(text)
    return predictions


def reciprocal_rank(predicted: list[str], truth: Optional[str], k: int = K) -> float:
    """Return 1/rank when the truth is in the first k positions; otherwise 0."""
    if k < 1:
        raise ValueError("k phải >= 1")
    if truth is None or not str(truth).strip() or str(truth).lower() == "nan":
        return 0.0
    truth_text = str(truth).strip()
    for rank, reference_id in enumerate(predicted[:k], start=1):
        if str(reference_id).strip() == truth_text:
            return 1.0 / rank
    return 0.0


def mrr_at_k(
    rows: Iterable[dict],
    k: int = K,
    skip_no_answer: bool = True,
) -> dict:
    """Compute MRR@k for prediction rows.

    With skip_no_answer=True, rows whose groundtruth is null/empty are excluded
    from the denominator. This is the legacy interface used during training.
    """
    if k < 1:
        raise ValueError("k phải >= 1")

    scores: List[float] = []
    skipped = 0
    for row in rows:
        raw_truth = row.get("groundtruth_arxiv_id")
        truth_text = _clean_text(raw_truth)
        truth = truth_text if truth_text and truth_text.lower() != "nan" else None
        if truth is None and skip_no_answer:
            skipped += 1
            continue
        scores.append(reciprocal_rank(extract_predicted_ids(row, k=k), truth, k))

    n_evaluated = len(scores)
    return {
        f"mrr@{k}": sum(scores) / n_evaluated if n_evaluated else 0.0,
        "n_evaluated": n_evaluated,
        "n_skipped_no_answer": skipped,
        "hit_rate": sum(score > 0 for score in scores) / n_evaluated if n_evaluated else 0.0,
    }


def _parse_split_data(data: dict) -> tuple[Dict[str, str], Dict[str, str]]:
    """Parse member-based M3 split.json and the older flat split format.

    Returns (partition_by_arxiv_id, label_source_by_arxiv_id). IDs are
    normalized so the same paper is not split across URL/version spellings.
    """
    partitions: Dict[str, str] = {}
    sources: Dict[str, str] = {}

    members = data.get("members") if isinstance(data, dict) else None
    if isinstance(members, dict):
        for member_data in members.values():
            if not isinstance(member_data, dict):
                continue

            for source in ("manual", "auto"):
                for raw_id in member_data.get(source, []) or []:
                    aid = _normalize_id(raw_id)
                    if aid:
                        partitions.setdefault(aid, "train")
                        sources.setdefault(aid, source)

            for partition in ("valid", "test"):
                part_data = member_data.get(partition, {})
                if isinstance(part_data, dict):
                    for source in ("manual", "auto"):
                        for raw_id in part_data.get(source, []) or []:
                            aid = _normalize_id(raw_id)
                            if aid:
                                partitions[aid] = partition
                                sources.setdefault(aid, source)
                elif isinstance(part_data, list):
                    for raw_id in part_data:
                        aid = _normalize_id(raw_id)
                        if aid:
                            partitions[aid] = partition
                            sources.setdefault(aid, "auto")
        return partitions, sources

    # Backward-compatible flat structure: {train: [...], valid: [...], test: [...]}.
    if isinstance(data, dict):
        for partition in ("train", "valid", "test"):
            for raw_id in data.get(partition, []) or []:
                aid = _normalize_id(raw_id)
                if aid:
                    partitions[aid] = partition
                    sources.setdefault(aid, "manual")
    return partitions, sources


def load_split(path: Path) -> dict:
    """Load split.json and return {arxiv_id: partition}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    partitions, _ = _parse_split_data(data)
    return partitions


def mrr_by_group(rows: Iterable[dict], group_of, k: int = K) -> dict:
    groups = defaultdict(list)
    for row in rows:
        group = group_of(row)
        if group is not None:
            groups[str(group)].append(row)
    return {name: mrr_at_k(items, k=k) for name, items in sorted(groups.items())}


# ---------------------------------------------------------------------------
# Score/label based evaluation from the P1 implementation.
# ---------------------------------------------------------------------------

def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được JSON {path}: {exc}") from exc


def load_scores(path: Path) -> pd.DataFrame:
    """Load and normalize scores.parquet produced by m3_model."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy scores.parquet: {path}")

    frame = pd.read_parquet(path)
    missing = REQUIRED_SCORE_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"scores.parquet thiếu cột: {sorted(missing)}")

    frame = frame.copy()
    for column in ("arxiv_id", "citation_key", "ref_arxiv_id"):
        frame[column] = frame[column].map(_clean_text).astype("string")

    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    frame["_arxiv_norm"] = frame["arxiv_id"].map(_normalize_id)
    frame["_ref_norm"] = frame["ref_arxiv_id"].map(_normalize_id)

    if "model_version" not in frame.columns:
        frame["model_version"] = "default"
    frame["model_version"] = frame["model_version"].fillna("default").astype(str)

    # Non-finite values must never win the ranking. Keep them as NaN so they
    # sort last and are excluded from candidate rows below.
    frame.loc[~frame["score"].map(lambda value: math.isfinite(float(value)) if pd.notna(value) else False), "score"] = float("nan")
    return frame


def load_ground_truths(
    labels_dir: Path,
    split: dict[str, Any],
    member: str = "all",
    corpus_dir: Path | None = None,
    include_null: bool = False,
) -> list[dict[str, Any]]:
    """Read validated positive labels; optionally include explicit null labels.

    A positive label remains in Q even when its entry has no scored candidate.
    Null labels are excluded by default, matching the M3 plan. Set
    include_null=True only for compatibility with the legacy --keep-no-answer
    switch; those rows contribute reciprocal rank zero.
    """
    if not isinstance(split, dict) or not isinstance(split.get("members"), dict):
        raise ValueError("split.json không đúng cấu trúc members")

    labels_dir = Path(labels_dir)
    corpus_dir = Path(corpus_dir) if corpus_dir is not None else labels_dir.parent.parent
    member_names = list(split["members"].keys()) if member == "all" else [member]

    try:
        from m3_manual_labeler import _read_json_unique_keys, validate_label_data
    except ImportError:  # pragma: no cover
        from .m3_manual_labeler import _read_json_unique_keys, validate_label_data

    records: list[dict[str, Any]] = []
    seen_entries: set[tuple[str, str]] = set()

    for member_name in member_names:
        member_data = split["members"].get(member_name)
        if not isinstance(member_data, dict):
            if member == "all":
                continue
            raise ValueError(f"Member {member_name} không có trong split.json")

        partitions = partition_map(split, member_name)
        paper_ids = sorted(
            {
                _normalize_id(value)
                for value in (list(member_data.get("manual", []) or []) + list(member_data.get("auto", []) or []))
                if _normalize_id(value)
            }
        )

        for arxiv_id in paper_ids:
            partition = partitions.get(arxiv_id)
            if partition not in VALID_PARTITIONS:
                raise ValueError(f"{arxiv_id}: partition không hợp lệ hoặc chưa được gán")

            label_path = labels_dir / f"{paper_key(arxiv_id)}.json"
            label_data = _read_json_unique_keys(label_path)
            if not isinstance(label_data, dict):
                # The prediction stage reports missing label files separately.
                # Evaluation cannot create ground-truth entries without labels.
                continue

            source = label_data.get("source")
            if source not in {"manual", "auto"}:
                raise ValueError(f"{label_path}: source phải là manual hoặc auto")

            stored_id = _normalize_id(label_data.get("arxiv_id"))
            if stored_id != arxiv_id:
                raise ValueError(
                    f"arxiv_id trong label file ({label_data.get('arxiv_id')}) không khớp {arxiv_id}"
                )

            paper_dir = corpus_dir / paper_key(arxiv_id)
            bib = _read_json(paper_dir / "bibtex_merged.json", [])
            refs_data = _read_json(paper_dir / "refs.json", [])
            refs = refs_data.get("references", []) if isinstance(refs_data, dict) else refs_data
            if not isinstance(bib, list) or not isinstance(refs, list):
                raise ValueError(
                    f"{arxiv_id}: thiếu hoặc sai định dạng bibtex_merged.json/refs.json"
                )

            label_errors = validate_label_data(label_data, bib, refs)
            if label_errors:
                raise ValueError(
                    f"Label file không hợp lệ ({label_path}):\n- "
                    + "\n- ".join(label_errors[:20])
                )

            labels = label_data.get("labels", {})
            if not isinstance(labels, dict):
                raise ValueError(f"{label_path}: labels phải là object/dict")

            for citation_key, groundtruth in labels.items():
                if groundtruth is None and not include_null:
                    continue
                normalized_truth = _normalize_id(groundtruth)
                if groundtruth is not None and not normalized_truth:
                    raise ValueError(
                        f"{arxiv_id}/{citation_key}: groundtruth_arxiv_id không hợp lệ"
                    )

                key = (arxiv_id, str(citation_key))
                if key in seen_entries:
                    raise ValueError(f"Entry bị lặp trong split/labels: {key}")
                seen_entries.add(key)

                records.append({
                    "arxiv_id": arxiv_id,
                    "member": member_name,
                    "partition": partition,
                    "source": str(source),
                    "citation_key": str(citation_key),
                    "groundtruth_arxiv_id": normalized_truth,
                })

    return records


def _group_scores(scores: pd.DataFrame) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Xếp hạng sẵn MỌI entry bằng một lần sort toàn bảng.

    Kết quả: {(arxiv_id chuẩn hóa, citation_key): [{"_ref_norm", "score"}, ...]} (tối đa 100 dòng),
    đã loại ref rỗng / score NaN, sắp theo score giảm dần rồi _ref_norm, candidate_id tăng dần,
    và mỗi ref chỉ giữ dòng điểm cao nhất, đúng như cách xếp hạng từng entry trước đây.
    Trước đây mỗi entry tự lọc + sort lại cả bảng scores (hơn 1 giờ cho 60k entry); cách này
    chỉ sort một lần.
    """
    valid = scores[
        scores["_ref_norm"].notna()
        & (scores["_ref_norm"].astype(str).str.strip() != "")
        & scores["score"].notna()
    ]
    frame = pd.DataFrame({
        "_aid": valid["_arxiv_norm"].astype(str).to_numpy(),
        "_ck": valid["citation_key"].astype(str).to_numpy(),
        "_ref_norm": valid["_ref_norm"].astype(str).to_numpy(),
        "score": valid["score"].to_numpy(),
    })
    sort_columns = ["_aid", "_ck", "score", "_ref_norm"]
    ascending = [True, True, False, True]
    if "candidate_id" in valid.columns:
        frame["candidate_id"] = valid["candidate_id"].astype(str).to_numpy()
        sort_columns.append("candidate_id")
        ascending.append(True)
    frame = frame.sort_values(by=sort_columns, ascending=ascending, kind="mergesort")
    frame = frame.drop_duplicates(subset=["_aid", "_ck", "_ref_norm"], keep="first")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for aid, ck, ref, score in zip(
        frame["_aid"].tolist(), frame["_ck"].tolist(), frame["_ref_norm"].tolist(), frame["score"].tolist()
    ):
        bucket = groups.setdefault((aid, ck), [])
        if len(bucket) < 100:
            bucket.append({"_ref_norm": ref, "score": score})
    return groups


def _ranking_for_entry(
    scores: pd.DataFrame,
    arxiv_id: str,
    citation_key: str,
    groups: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    aid = _normalize_id(arxiv_id)
    if groups is not None:
        return groups.get((str(aid), str(citation_key)), [])
    subset = scores[
        (scores["_arxiv_norm"] == aid)
        & (scores["citation_key"].astype(str) == str(citation_key))
    ].copy()

    if subset.empty:
        return []

    subset = subset[subset["_ref_norm"].notna() & (subset["_ref_norm"].astype(str).str.strip() != "")]
    subset = subset[subset["score"].notna()]
    if subset.empty:
        return []

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
    subset = subset.drop_duplicates(subset=["_ref_norm"], keep="first")
    return subset[["_ref_norm", "score"]].head(100).to_dict("records")


def evaluate_records(
    scores: pd.DataFrame,
    ground_truths: list[dict[str, Any]],
    partition: str = "test",
) -> dict[str, Any]:
    """Evaluate the Q set; a positive entry with no candidates receives zero."""
    selected = [
        row for row in ground_truths
        if partition == "all" or row["partition"] == partition
    ]
    if not selected:
        return {
            "q": 0,
            "mrr@5": None,
            "hits@1": 0,
            "hits@5": 0,
            "no_scored_candidates": 0,
            "hit_rate@1": None,
            "hit_rate@5": None,
            "zero_reciprocal_rank": 0,
            "error_examples": [],
        }

    reciprocal_ranks: list[float] = []
    hit1 = hit5 = no_candidates = 0
    errors: list[dict[str, Any]] = []

    groups = _group_scores(scores)
    for truth in selected:
        ranking = _ranking_for_entry(
            scores, truth["arxiv_id"], truth["citation_key"], groups
        )
        if not ranking:
            no_candidates += 1

        rank = next(
            (
                index
                for index, row in enumerate(ranking, start=1)
                if row["_ref_norm"] == truth["groundtruth_arxiv_id"]
            ),
            None,
        )
        reciprocal = 1.0 / rank if rank is not None and rank <= K else 0.0
        reciprocal_ranks.append(reciprocal)

        if rank == 1:
            hit1 += 1
        if rank is not None and rank <= K:
            hit5 += 1
        if reciprocal == 0.0:
            errors.append({
                **truth,
                "groundtruth_rank": rank,
                "top5": [row["_ref_norm"] for row in ranking[:K]],
            })

    q = len(selected)
    return {
        "q": q,
        "mrr@5": round(sum(reciprocal_ranks) / q, 8),
        "hits@1": hit1,
        "hits@5": hit5,
        "no_scored_candidates": no_candidates,
        "hit_rate@1": round(hit1 / q, 8),
        "hit_rate@5": round(hit5 / q, 8),
        "zero_reciprocal_rank": len(errors),
        "error_examples": errors[:30],
    }


def candidate_recall(
    corpus_dir: Path,
    ground_truths: list[dict[str, Any]],
    partition: str = "test",
) -> dict[str, Any]:
    """Measure candidate-generation recall on manual positive labels."""
    manual = [
        row for row in ground_truths
        if row["source"] == "manual"
        and row["groundtruth_arxiv_id"] is not None
        and (partition == "all" or row["partition"] == partition)
    ]

    total = hits = missing_files = 0
    for truth in manual:
        path = Path(corpus_dir) / paper_key(truth["arxiv_id"]) / "candidates.json"
        data = _read_json(path)
        if not isinstance(data, list):
            missing_files += 1
            total += 1
            continue

        total += 1
        matched = any(
            str(candidate.get("citation_key")) == truth["citation_key"]
            and _normalize_id(candidate.get("ref_arxiv_id")) == truth["groundtruth_arxiv_id"]
            for candidate in data
            if isinstance(candidate, dict)
        )
        hits += int(matched)

    return {
        "q_manual_positive_entries": total,
        "candidate_recall_hits": hits,
        "missing_candidate_files": missing_files,
        "candidate_recall": round(hits / total, 8) if total else None,
    }


def evaluate(
    corpus_dir: Path,
    scores_path: Path,
    split_path: Path,
    labels_dir: Path | None = None,
    member: str = "all",
    partition: str = "all",
    model_version: str | None = None,
    keep_no_answer: bool = False,
) -> dict[str, Any]:
    """Evaluate scores against validated labels, with per-model and per-group reports."""
    corpus_dir = Path(corpus_dir)
    split = _read_json(Path(split_path))
    if not isinstance(split, dict) or not isinstance(split.get("members"), dict):
        raise ValueError("split.json không đúng cấu trúc members")
    if member != "all" and member not in split["members"]:
        raise ValueError(f"Member {member} không có trong split.json")
    if partition not in {"train", "valid", "test", "all"}:
        raise ValueError("partition phải là train, valid, test hoặc all")

    labels_dir = Path(labels_dir) if labels_dir is not None else corpus_dir / "m3" / "labels"
    scores_all = load_scores(Path(scores_path))
    versions = sorted(scores_all["model_version"].unique().tolist())
    if not versions:
        versions = ["default"]

    if model_version is not None:
        if model_version not in versions:
            raise ValueError(
                f"Không tìm thấy model_version={model_version!r}; có: {versions}"
            )
        versions_to_report = [model_version]
    else:
        versions_to_report = versions

    truths = load_ground_truths(
        labels_dir,
        split,
        member=member,
        corpus_dir=corpus_dir,
        include_null=keep_no_answer,
    )
    selected_truths = [
        row for row in truths
        if partition == "all" or row["partition"] == partition
    ]

    def bundle(scores_for_model: pd.DataFrame) -> dict[str, Any]:
        by_source: dict[str, Any] = {}
        for source in sorted({row["source"] for row in selected_truths}):
            group = [row for row in selected_truths if row["source"] == source]
            by_source[source] = evaluate_records(scores_for_model, group, "all")

        by_partition: dict[str, Any] = {}
        for part in ("train", "valid", "test"):
            group = [row for row in selected_truths if row["partition"] == part]
            if group:
                by_partition[part] = evaluate_records(scores_for_model, group, "all")

        by_member: dict[str, Any] = {}
        for member_name in sorted({row["member"] for row in selected_truths}):
            group = [row for row in selected_truths if row["member"] == member_name]
            by_member[member_name] = {
                "range": MEMBER_RANGES.get(member_name),
                "metrics": evaluate_records(scores_for_model, group, "all"),
            }

        test_truths = [row for row in selected_truths if row["partition"] == "test"]
        test_by_source: dict[str, Any] = {}
        for source in sorted({row["source"] for row in test_truths}):
            group = [row for row in test_truths if row["source"] == source]
            test_by_source[source] = evaluate_records(scores_for_model, group, "all")

        return {
            "overall": evaluate_records(scores_for_model, selected_truths, "all"),
            "by_partition": by_partition,
            "by_label_source": by_source,
            "by_member_range": by_member,
            "test_by_source": test_by_source,
        }

    by_model_version = {
        version: bundle(
            scores_all[scores_all["model_version"] == version]
            if "model_version" in scores_all.columns
            else scores_all
        )
        for version in versions_to_report
    }

    selected_version = model_version or (versions[0] if len(versions) == 1 else None)
    primary = by_model_version[selected_version] if selected_version is not None else None

    recall_by_member: dict[str, Any] = {}
    for member_name in sorted({row["member"] for row in truths}):
        recall_by_member[member_name] = candidate_recall(
            corpus_dir,
            [row for row in truths if row["member"] == member_name],
            "all",
        )

    ablation_comparison = {
        version: {
            "q": data["overall"]["q"],
            "mrr@5": data["overall"]["mrr@5"],
            "hits@1": data["overall"]["hits@1"],
            "hits@5": data["overall"]["hits@5"],
        }
        for version, data in by_model_version.items()
    }

    return {
        "metric": "MRR@5",
        "definition": (
            "Q contains non-null labels by default; reciprocal rank is 1/rank "
            "for rank<=5 and 0 otherwise. Positive labels without scored "
            "candidates remain in Q with score 0."
        ),
        "partition": partition,
        "member": member,
        "selected_model_version": selected_version,
        "overall": primary["overall"] if primary else None,
        "by_partition": primary["by_partition"] if primary else {},
        "by_label_source": primary["by_label_source"] if primary else {},
        "by_member_range": primary["by_member_range"] if primary else {},
        "by_member": {
            name: value["metrics"]
            for name, value in (primary["by_member_range"] if primary else {}).items()
        },
        "test_by_source": primary["test_by_source"] if primary else {},
        "candidate_generation_recall_manual": recall_by_member,
        "by_model_version": by_model_version,
        "ablation_comparison_by_model_version": ablation_comparison,
        "ground_truth_entries_loaded": len(truths),
        "ground_truth_entries_in_report": len(selected_truths),
        "null_and_unannotated_entries_excluded": not keep_no_answer,
        "note": (
            "Có nhiều model_version; cần truyền --model-version để chọn một "
            "phiên bản trước khi sử dụng báo cáo overall."
            if len(versions) > 1 and model_version is None
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Pipeline adapter. Prefer scores+labels (P1 approach); retain predictions-
# parquet fallback for the original main pipeline and its --keep-no-answer.
# ---------------------------------------------------------------------------

def _legacy_predictions_report(
    predictions_file: Path,
    split_file: Optional[Path],
    keep_no_answer: bool,
    data_dir: Path,
) -> Dict[str, Any]:
    import pyarrow.parquet as pq

    if not predictions_file.exists():
        raise FileNotFoundError(f"Chưa có file predictions: {predictions_file}")

    rows = pq.read_table(predictions_file).to_pandas().to_dict("records")
    for row in rows:
        row["predicted_arxiv_ids"] = extract_predicted_ids(row)
        raw_truth = row.get("groundtruth_arxiv_id")
        truth_text = _clean_text(raw_truth)
        row["groundtruth_arxiv_id"] = (
            truth_text if truth_text and truth_text.lower() != "nan" else None
        )

    split_parts: Dict[str, str] = {}
    split_sources: Dict[str, str] = {}
    if split_file is not None and Path(split_file).exists():
        split_data = _read_json(Path(split_file))
        split_parts, split_sources = _parse_split_data(split_data)

    overall = mrr_at_k(rows, skip_no_answer=not keep_no_answer)
    by_partition = mrr_by_group(
        rows,
        lambda row: (
            _clean_text(row.get("partition"))
            or split_parts.get(_normalize_id(row.get("arxiv_id")) or "")
        ),
    )

    test_rows = [
        row for row in rows
        if (
            _clean_text(row.get("partition"))
            or split_parts.get(_normalize_id(row.get("arxiv_id")) or "")
        ) == "test"
    ]
    test_by_source = mrr_by_group(
        test_rows,
        lambda row: split_sources.get(_normalize_id(row.get("arxiv_id")) or "", "unknown"),
    )
    by_member = mrr_by_group(
        rows,
        lambda row: C.member_of(_normalize_id(row.get("arxiv_id")) or "") or "other",
    )

    return {
        "overall": overall,
        "by_partition": by_partition,
        "test_by_source": test_by_source,
        "by_member": by_member,
        "evaluation_source": "predictions.parquet",
    }


def run_evaluation_pipeline(
    data_dir: Path,
    predictions_file: Optional[Path] = None,
    split_file: Optional[Path] = None,
    keep_no_answer: bool = False,
    *,
    scores_file: Optional[Path] = None,
    labels_dir: Optional[Path] = None,
    member: str = "all",
    partition: str = "all",
    model_version: Optional[str] = None,
    output_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run M3 evaluation while preserving the original run_m3.py interface.

    If scores.parquet, split.json and label JSON files are available, evaluate
    directly against validated ground truths (P1 approach). Otherwise, fall
    back to predictions.parquet (main approach). Supplying predictions_file
    explicitly always selects the legacy predictions-based route.
    """
    data_dir = Path(data_dir)
    split_file = Path(split_file) if split_file is not None else C.split_path(data_dir)
    scores_file = Path(scores_file) if scores_file is not None else C.scores_path(data_dir)
    labels_dir = Path(labels_dir) if labels_dir is not None else C.m3_dir(data_dir) / "labels"
    predictions_path = (
        Path(predictions_file)
        if predictions_file is not None
        else C.predictions_path(data_dir)
    )

    can_use_score_evaluation = (
        predictions_file is None
        and scores_file.exists()
        and split_file.exists()
        and labels_dir.is_dir()
        and any(labels_dir.glob("*.json"))
    )

    if can_use_score_evaluation:
        report = evaluate(
            corpus_dir=data_dir,
            scores_path=scores_file,
            split_path=split_file,
            labels_dir=labels_dir,
            member=member,
            partition=partition,
            model_version=model_version,
            keep_no_answer=keep_no_answer,
        )
        if report["overall"] is None:
            raise ValueError(
                "scores.parquet có nhiều model_version. Hãy chạy m3_evaluate.py "
                "với --model-version để chọn một model trước khi đánh giá pipeline."
            )
        report["evaluation_source"] = "scores.parquet + labels"
    else:
        report = _legacy_predictions_report(
            predictions_path,
            split_file if split_file.exists() else None,
            keep_no_answer,
            data_dir,
        )

    out_report = (
        Path(output_file)
        if output_file is not None
        else C.m3_dir(data_dir) / "evaluation_report.json"
    )
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report["report_path"] = str(out_report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Đánh giá MRR@5 cho M3")
    parser.add_argument(
        "--data-dir", "--corpus-dir", dest="data_dir",
        type=Path, default=C.DATA_DIR,
        help="Thư mục corpus (mặc định theo config.DATA_DIR)",
    )
    parser.add_argument("--predictions", type=Path, default=None)
    parser.add_argument("--scores", type=Path, default=None)
    parser.add_argument("--split-file", "--split", dest="split_file", type=Path, default=None)
    parser.add_argument("--labels-dir", type=Path, default=None)
    parser.add_argument("--member", default="all")
    parser.add_argument("--partition", choices=["train", "valid", "test", "all"], default="test")
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--keep-no-answer", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    report = run_evaluation_pipeline(
        data_dir=args.data_dir,
        predictions_file=args.predictions,
        split_file=args.split_file,
        keep_no_answer=args.keep_no_answer,
        scores_file=args.scores,
        labels_dir=args.labels_dir,
        member=args.member,
        partition=args.partition,
        model_version=args.model_version,
        output_file=args.output,
    )

    print("TỔNG:", report["overall"])
    for name, result in report.get("by_partition", {}).items():
        print(f"{name:>8}:", result)
    if report.get("test_by_source"):
        print("TEST THEO NGUỒN NHÃN:", report["test_by_source"])
    if report.get("by_member"):
        print(
            "THEO THÀNH VIÊN:",
            {name: metrics.get("mrr@5") for name, metrics in report["by_member"].items()},
        )
    print("Báo cáo:", report.get("report_path"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())