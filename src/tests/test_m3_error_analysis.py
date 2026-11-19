from matching.m3_error_analysis import breakdown


def pred(key, truth, predicted):
    return {"arxiv_id": "p", "citation_key": key,
            "groundtruth_arxiv_id": truth, "predicted_arxiv_ids": predicted}


def cand(key, ref):
    return {"arxiv_id": "p", "citation_key": key, "ref_arxiv_id": ref}


def test_every_category():
    rows = [
        pred("a", "r1", ["r1"]),
        pred("b", "r2", ["x", "r2"]),
        pred("c", "r3", ["1", "2", "3", "4", "5"]),
        pred("d", "r4", ["x"]),
        pred("e", "r5", []),
        pred("f", None, ["x"]),
    ]
    cands = [cand("c", "r3"), cand("d", "x")]
    result = breakdown(rows, cands)
    assert result["counts"] == {
        "hit_rank1": 1, "hit_rank2_5": 1, "miss_ranker": 1,
        "miss_not_candidate": 1, "no_candidates": 1, "no_answer": 1,
    }
    assert result["n_labeled"] == 5
    assert result["candidate_recall"] == 3 / 5