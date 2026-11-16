r"""m3_evaluate.py

Milestone: M3
Người phụ trách: P3
Mục đích: Tính MRR@5 trên tập test

Đầu vào: Top-5 ứng viên và nhãn thật
Đầu ra:  Điểm MRR@5

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from __future__ import annotations

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