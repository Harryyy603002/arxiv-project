"""M3 - P4: đánh giá xếp hạng bằng MRR@5."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

K = 5


def reciprocal_rank(predicted: list[str], truth: Optional[str], k: int = K) -> float:
    """1/rank nếu đáp án nằm trong top-k, ngược lại 0."""
    if not truth:
        return 0.0
    for rank, ref_id in enumerate(predicted[:k], start=1):
        if ref_id == truth:
            return 1.0 / rank
    return 0.0


def mrr_at_k(
    rows: Iterable[dict],
    k: int = K,
    skip_no_answer: bool = True,
) -> dict:
    """rows: mỗi dòng có groundtruth_arxiv_id (có thể rỗng) và predicted_arxiv_ids (list).

    skip_no_answer=True: bỏ entry không có đáp án khỏi mẫu số (|Q| chỉ gồm entry có nhãn).
    """
    scores, skipped = [], 0
    for row in rows:
        truth = row.get("groundtruth_arxiv_id") or None
        if truth is None and skip_no_answer:
            skipped += 1
            continue
        scores.append(reciprocal_rank(list(row.get("predicted_arxiv_ids") or []), truth, k))
    n = len(scores)
    return {
        f"mrr@{k}": sum(scores) / n if n else 0.0,
        "n_evaluated": n,
        "n_skipped_no_answer": skipped,
        "hit_rate": sum(s > 0 for s in scores) / n if n else 0.0,
    }


def load_split(path: Path) -> dict:
    """Đọc {"train": [arxiv_id...], "valid": [...], "test": [...]} -> {arxiv_id: tên tập}.

    ĐỊNH DẠNG NÀY LÀ GIẢ ĐỊNH: xác nhận lại với P3 (labels_metadata.json).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {aid: name for name, ids in data.items() for aid in ids}


def mrr_by_group(rows, group_of, k: int = K) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[group_of(row)].append(row)
    return {name: mrr_at_k(items, k) for name, items in sorted(groups.items())}


def main() -> int:
    import pandas as pd

    parser = argparse.ArgumentParser(description="Tính MRR@5 từ predictions.parquet")
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--split-file", type=Path, help="JSON chia train/valid/test")
    parser.add_argument("--keep-no-answer", action="store_true",
                        help="tính cả entry không có đáp án (điểm 0)")
    args = parser.parse_args()

    rows = pd.read_parquet(args.predictions).to_dict("records")
    for row in rows:
        row["predicted_arxiv_ids"] = list(row["predicted_arxiv_ids"])
        row["groundtruth_arxiv_id"] = row["groundtruth_arxiv_id"] or None

    print("TỔNG:", mrr_at_k(rows, skip_no_answer=not args.keep_no_answer))
    if args.split_file:
        split = load_split(args.split_file)
        by_split = mrr_by_group(
            [r for r in rows if r["arxiv_id"] in split],
            lambda r: split[r["arxiv_id"]],
        )
        for name, result in by_split.items():
            print(f"{name:>6}:", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())