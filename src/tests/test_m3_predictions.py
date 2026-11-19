from matching.m3_predictions import build_predictions, top_k_by_entry


def cand(entry, ref, score, arxiv="p1"):
    return {"arxiv_id": arxiv, "citation_key": entry, "ref_arxiv_id": ref, "score": score}


def test_sorted_and_truncated_to_five():
    scored = [cand("k", f"r{i}", i / 10) for i in range(8)]
    assert top_k_by_entry(scored)[("p1", "k")] == ["r7", "r6", "r5", "r4", "r3"]


def test_empty_ref_id_is_dropped():
    scored = [cand("k", "", 1.0), cand("k", "r1", 0.2)]
    assert top_k_by_entry(scored)[("p1", "k")] == ["r1"]


def test_duplicate_refs_collapsed_and_ties_stable():
    scored = [cand("k", "b", 0.5), cand("k", "a", 0.5), cand("k", "a", 0.4)]
    assert top_k_by_entry(scored)[("p1", "k")] == ["a", "b"]


def test_entry_without_candidates_gets_empty_list_and_truth():
    rows = build_predictions(
        [cand("k1", "r1", 0.9)],
        entries=[("p1", "k1"), ("p1", "k2")],
        truth={("p1", "k1"): "r1", ("p1", "k2"): None},
    )
    assert rows[0]["predicted_arxiv_ids"] == ["r1"] and rows[0]["groundtruth_arxiv_id"] == "r1"
    assert rows[1]["predicted_arxiv_ids"] == [] and rows[1]["groundtruth_arxiv_id"] is None

def test_predictions_feed_evaluator():
    from matching.m3_evaluate import mrr_at_k
    rows = build_predictions(
        [cand("k", "r2", 0.9), cand("k", "r1", 0.8)],
        entries=[("p1", "k")], truth={("p1", "k"): "r1"},
    )
    assert mrr_at_k(rows)["mrr@5"] == 0.5