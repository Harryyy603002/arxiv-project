"""M3 - P4: phân loại lỗi của bộ xếp hạng."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Set, Tuple

Key = Tuple[str, str]

CATEGORIES = (
    "hit_rank1",             # đúng ở vị trí 1
    "hit_rank2_5",           # đúng nhưng xếp 2-5
    "miss_ranker",           # đáp án có trong candidates nhưng không vào top-5
    "miss_not_candidate",    # đáp án KHÔNG có trong candidates (lỗi sinh ứng viên)
    "no_candidates",         # entry không có candidate nào
    "no_answer",             # entry không có đáp án (không tính lỗi)
)


def candidate_index(candidates: Iterable[dict]) -> Dict[Key, Set[str]]:
    index: Dict[Key, Set[str]] = defaultdict(set)
    for row in candidates:
        if row.get("ref_arxiv_id"):
            index[(row["arxiv_id"], row["citation_key"])].add(row["ref_arxiv_id"])
    return index


def classify(row: dict, index: Dict[Key, Set[str]]) -> str:
    truth = row.get("groundtruth_arxiv_id")
    if not truth:
        return "no_answer"
    predicted: List[str] = list(row.get("predicted_arxiv_ids") or [])
    if predicted[:1] == [truth]:
        return "hit_rank1"
    if truth in predicted[:5]:
        return "hit_rank2_5"
    pool = index.get((row["arxiv_id"], row["citation_key"]), set())
    if not pool:
        return "no_candidates"
    return "miss_ranker" if truth in pool else "miss_not_candidate"


def breakdown(rows: Iterable[dict], candidates: Iterable[dict], examples: int = 5) -> dict:
    index = candidate_index(candidates)
    counts: Counter = Counter()
    samples: Dict[str, list] = defaultdict(list)
    for row in rows:
        category = classify(row, index)
        counts[category] += 1
        if len(samples[category]) < examples:
            samples[category].append((row["arxiv_id"], row["citation_key"]))
    labeled = sum(v for k, v in counts.items() if k != "no_answer")
    return {
        "counts": {c: counts.get(c, 0) for c in CATEGORIES},
        "n_labeled": labeled,
        "candidate_recall": (
            (counts["hit_rank1"] + counts["hit_rank2_5"] + counts["miss_ranker"]) / labeled
            if labeled else 0.0
        ),
        "examples": dict(samples),
    }