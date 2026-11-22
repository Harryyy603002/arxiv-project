import pytest
from matching.m3_evaluate import mrr_at_k, reciprocal_rank


def test_plan_example():
    rows = [
        {"groundtruth_arxiv_id": "a", "predicted_arxiv_ids": ["a", "b", "c"]},
        {"groundtruth_arxiv_id": "a", "predicted_arxiv_ids": ["x", "y", "a"]},
        {"groundtruth_arxiv_id": "a", "predicted_arxiv_ids": ["1", "2", "3", "4", "5"]},
    ]
    result = mrr_at_k(rows)
    assert result["mrr@5"] == pytest.approx((1 + 1 / 3 + 0) / 3)  # 0.444
    assert result["n_evaluated"] == 3


def test_rank_beyond_five_is_zero():
    assert reciprocal_rank(["1", "2", "3", "4", "5", "a"], "a") == 0.0


def test_no_answer_skipped_or_counted():
    rows = [
        {"groundtruth_arxiv_id": "a", "predicted_arxiv_ids": ["a"]},
        {"groundtruth_arxiv_id": "", "predicted_arxiv_ids": ["z"]},
    ]
    assert mrr_at_k(rows)["mrr@5"] == 1.0
    assert mrr_at_k(rows, skip_no_answer=False)["mrr@5"] == 0.5


def test_empty_input():
    assert mrr_at_k([])["mrr@5"] == 0.0