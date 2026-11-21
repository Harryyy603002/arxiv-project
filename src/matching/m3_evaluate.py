"""M3 ranking evaluation: MRR@5 over entries with a non-null ground truth.

The evaluation universe Q contains only labeled entries whose correct
reference exists (labels[citation_key] != null). Such an entry remains in Q
and receives reciprocal rank 0 even if it has no scored candidate or the
correct reference is ranked below 5.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from m3_pick_papers import MEMBERS, MEMBER_RANGES, member_of, paper_key, partition_map
except ImportError:  # pragma: no cover
    from .m3_pick_papers import MEMBERS, MEMBER_RANGES, member_of, paper_key, partition_map
try:
    from m3_candidates import normalize_arxiv_id
except ImportError:  # pragma: no cover
    from .m3_candidates import normalize_arxiv_id

REQUIRED_SCORE_COLUMNS = {"arxiv_id", "citation_key", "ref_arxiv_id", "score"}


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được JSON {path}: {exc}") from exc


def _normalize_id(value: Any) -> str:
    return normalize_arxiv_id(value) or str(value or "").strip()


def load_scores(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Không tìm thấy scores.parquet: {path}")
    frame = pd.read_parquet(path)
    missing = REQUIRED_SCORE_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"scores.parquet thiếu cột: {sorted(missing)}")
    frame = frame.copy()
    for col in ("arxiv_id", "citation_key", "ref_arxiv_id"):
        frame[col] = frame[col].astype("string")
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce")
    if "model_version" not in frame.columns:
        frame["model_version"] = "default"
    frame["model_version"] = frame["model_version"].fillna("default").astype(str)
    frame["_ref_norm"] = frame["ref_arxiv_id"].map(_normalize_id)
    return frame


def load_ground_truths(
    labels_dir: Path,
    split: dict[str, Any],
    member: str = "all",
    corpus_dir: Path | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    corpus_dir = corpus_dir or labels_dir.parent.parent
    member_names = list(split.get("members", {}).keys()) if member == "all" else [member]
    for member_name in member_names:
        member_data = split.get("members", {}).get(member_name)
        if not isinstance(member_data, dict):
            continue
        partitions = partition_map(split, member_name)
        paper_ids = sorted(set(member_data.get("manual", [])) | set(member_data.get("auto", [])))
        for arxiv_id in paper_ids:
            partition = partitions.get(arxiv_id)
            if not partition:
                continue
            label_path = labels_dir / f"{paper_key(arxiv_id)}.json"
            try:
                from m3_manual_labeler import _read_json_unique_keys, validate_label_data
            except ImportError:  # pragma: no cover
                from .m3_manual_labeler import _read_json_unique_keys, validate_label_data
            label_data = _read_json_unique_keys(label_path)
            if not isinstance(label_data, dict):
                continue
            if label_data.get("source") not in {"manual", "auto"}:
                raise ValueError(f"{label_path}: source phải là manual hoặc auto")
            paper_dir = corpus_dir / paper_key(arxiv_id)
            bib = _read_json(paper_dir / "bibtex_merged.json", [])
            refs_data = _read_json(paper_dir / "refs.json", [])
            refs = refs_data.get("references", []) if isinstance(refs_data, dict) else refs_data
            if not isinstance(bib, list) or not isinstance(refs, list):
                raise ValueError(f"{arxiv_id}: thiếu hoặc sai định dạng bibtex_merged.json/refs.json")
            label_errors = validate_label_data(label_data, bib, refs)
            if str(label_data.get("arxiv_id")) != str(arxiv_id):
                label_errors.append(f"arxiv_id trong label file không khớp {arxiv_id}")
            if label_errors:
                raise ValueError(f"Label file không hợp lệ ({label_path}):\n- " + "\n- ".join(label_errors[:20]))
            labels = label_data.get("labels", {})
            if not isinstance(labels, dict):
                continue
            source = str(label_data.get("source") or "unknown")
            for citation_key, groundtruth in labels.items():
                if groundtruth is None:
                    continue  # null and unannotated entries are not in Q
                normalized = _normalize_id(groundtruth)
                records.append({
                    "arxiv_id": arxiv_id,
                    "member": member_name,
                    "partition": partition,
                    "source": source,
                    "citation_key": str(citation_key),
                    "groundtruth_arxiv_id": normalized,
                })
    return records


def _ranking_for_entry(scores: pd.DataFrame, arxiv_id: str, citation_key: str) -> list[dict[str, Any]]:
    subset = scores[(scores["arxiv_id"] == arxiv_id) & (scores["citation_key"] == citation_key)].copy()
    if subset.empty:
        return []
    subset = subset.sort_values(
        by=["score", "_ref_norm", "candidate_id"] if "candidate_id" in subset.columns else ["score", "_ref_norm"],
        ascending=[False, True, True] if "candidate_id" in subset.columns else [False, True],
        na_position="last",
        kind="mergesort",
    )
    # A reference ID should be unique within an entry; if model artifacts
    # accidentally repeat it, retain only its highest-scoring row.
    subset = subset.drop_duplicates(subset=["_ref_norm"], keep="first")
    return subset[["_ref_norm", "score"]].head(100).to_dict("records")


def evaluate_records(
    scores: pd.DataFrame,
    ground_truths: list[dict[str, Any]],
    partition: str = "test",
) -> dict[str, Any]:
    selected = [row for row in ground_truths if partition == "all" or row["partition"] == partition]
    if not selected:
        return {"q": 0, "mrr@5": None, "hits@1": 0, "hits@5": 0, "no_scored_candidates": 0}
    reciprocal_ranks: list[float] = []
    hit1 = hit5 = no_candidates = 0
    errors: list[dict[str, Any]] = []
    for truth in selected:
        ranking = _ranking_for_entry(scores, truth["arxiv_id"], truth["citation_key"])
        if not ranking:
            no_candidates += 1
        rank = next((index for index, row in enumerate(ranking, 1)
                     if row["_ref_norm"] == truth["groundtruth_arxiv_id"]), None)
        reciprocal = 1.0 / rank if rank is not None and rank <= 5 else 0.0
        reciprocal_ranks.append(reciprocal)
        if rank == 1:
            hit1 += 1
        if rank is not None and rank <= 5:
            hit5 += 1
        if reciprocal == 0.0:
            errors.append({
                **truth,
                "groundtruth_rank": rank,
                "top5": [row["_ref_norm"] for row in ranking[:5]],
            })
    return {
        "q": len(selected),
        "mrr@5": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 8),
        "hits@1": hit1,
        "hits@5": hit5,
        "no_scored_candidates": no_candidates,
        "hit_rate@1": round(hit1 / len(selected), 8),
        "hit_rate@5": round(hit5 / len(selected), 8),
        "zero_reciprocal_rank": len(errors),
        "error_examples": errors[:30],
    }


def candidate_recall(corpus_dir: Path, ground_truths: list[dict[str, Any]], partition: str = "test") -> dict[str, Any]:
    """Recall of the candidate-generation step on manually labeled positive entries."""
    manual = [row for row in ground_truths
              if row["source"] == "manual" and (partition == "all" or row["partition"] == partition)]
    total = hits = missing_files = 0
    for truth in manual:
        path = corpus_dir / paper_key(truth["arxiv_id"]) / "candidates.json"
        data = _read_json(path)
        if not isinstance(data, list):
            missing_files += 1
            total += 1
            continue
        total += 1
        matched = any(
            str(candidate.get("citation_key")) == truth["citation_key"]
            and _normalize_id(candidate.get("ref_arxiv_id")) == truth["groundtruth_arxiv_id"]
            for candidate in data if isinstance(candidate, dict)
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
    member: str = "Quoc",
    partition: str = "test",
    model_version: str | None = None,
) -> dict[str, Any]:
    split = _read_json(split_path)
    if not isinstance(split, dict) or not isinstance(split.get("members"), dict):
        raise ValueError("split.json không đúng cấu trúc members")
    if member != "all" and member not in split["members"]:
        raise ValueError(f"Member {member} không có trong split.json")
    labels_dir = labels_dir or (corpus_dir / "m3" / "labels")
    scores_all = load_scores(scores_path)
    versions = sorted(scores_all["model_version"].unique().tolist())
    if model_version is not None:
        if model_version not in versions:
            raise ValueError(f"Không tìm thấy model_version={model_version!r}; có: {versions}")
        versions_to_report = [model_version]
    else:
        versions_to_report = versions
    truths = load_ground_truths(labels_dir, split, member, corpus_dir=corpus_dir)
    if partition not in {"train", "valid", "test", "all"}:
        raise ValueError("partition phải là train, valid, test hoặc all")

    def bundle(scores_for_model: pd.DataFrame) -> dict[str, Any]:
        selected = [row for row in truths if partition == "all" or row["partition"] == partition]
        by_source: dict[str, Any] = {}
        for source in sorted({row["source"] for row in selected}):
            by_source[source] = evaluate_records(
                scores_for_model, [r for r in selected if r["source"] == source], "all"
            )
        by_member: dict[str, Any] = {}
        for member_name in sorted({row["member"] for row in selected}):
            by_member[member_name] = {
                "range": MEMBER_RANGES.get(member_name),
                "metrics": evaluate_records(
                    scores_for_model, [r for r in selected if r["member"] == member_name], "all"
                ),
            }
        return {
            "overall": evaluate_records(scores_for_model, truths, partition),
            "by_label_source": by_source,
            "by_member_range": by_member,
        }

    by_model_version = {
        version: bundle(scores_all[scores_all["model_version"] == version])
        for version in versions_to_report
    }
    selected_version = model_version or (versions[0] if len(versions) == 1 else None)
    primary = by_model_version[selected_version] if selected_version is not None else None

    # Candidate recall is independent of scoring model and is measured on
    # manually labeled positive entries only.
    recall_by_member: dict[str, Any] = {}
    for member_name in sorted({row["member"] for row in truths}):
        # Candidate-generation recall is measured on all manually labeled
        # positives, not only the test fold; it estimates the ranking model's
        # recall ceiling across the full manual set.
        recall_by_member[member_name] = candidate_recall(
            corpus_dir, [row for row in truths if row["member"] == member_name], "all"
        )

    ablation_comparison = {
        version: {
            "q": (bundle_data["overall"] or {}).get("q"),
            "mrr@5": (bundle_data["overall"] or {}).get("mrr@5"),
            "hits@1": (bundle_data["overall"] or {}).get("hits@1"),
            "hits@5": (bundle_data["overall"] or {}).get("hits@5"),
        }
        for version, bundle_data in by_model_version.items()
    }

    return {
        "metric": "MRR@5",
        "definition": "Q = non-null labels only; reciprocal rank is 1/rank for rank<=5, else 0; missing correct candidate remains in Q with 0.",
        "partition": partition,
        "member": member,
        "selected_model_version": selected_version,
        "overall": primary["overall"] if primary else None,
        "by_label_source": primary["by_label_source"] if primary else None,
        "by_member_range": primary["by_member_range"] if primary else None,
        "by_model_version": by_model_version,
        "ablation_comparison_by_model_version": ablation_comparison,
        "candidate_generation_recall_manual": recall_by_member,
        "ground_truth_positive_entries_loaded": len(truths),
        "null_and_unannotated_entries_excluded": True,
        "note": ("Có nhiều model_version; xem từng bundle trong by_model_version, không trộn điểm giữa các model." if len(versions) > 1 and model_version is None else None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Đánh giá MRR@5 cho M3")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--scores", type=Path)
    parser.add_argument("--split", type=Path)
    parser.add_argument("--labels-dir", type=Path)
    parser.add_argument("--member", choices=MEMBERS + ["all"], default="Quoc")
    parser.add_argument("--partition", choices=["train", "valid", "test", "all"], default="test")
    parser.add_argument("--model-version", help="Chỉ đánh giá một model_version; mặc định báo cáo riêng từng version")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    scores_path = args.scores or (args.corpus_dir / "m3" / "scores.parquet")
    split_path = args.split or (args.corpus_dir / "m3" / "split.json")
    report = evaluate(args.corpus_dir, scores_path, split_path, args.labels_dir, args.member, args.partition, args.model_version)
    output = args.output or (args.corpus_dir / "m3" / f"evaluation_{args.member}_{args.partition}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nĐã ghi báo cáo: {output}")


if __name__ == "__main__":
    main()
