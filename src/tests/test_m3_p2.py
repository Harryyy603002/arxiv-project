"""Tests for P2's M3 modules: m3_string_features and m3_auto_labeler.

Chạy:  pytest src/tests/test_m3_p2.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_string_features import compute_string_features, _year
from matching.m3_auto_labeler import _match_entry, label_paper


# ── helpers ───────────────────────────────────────────────────────────────────

def _cand(bib_title="", ref_title="", bib_authors="", ref_authors="",
          ref_arxiv_id="2301.00001", year=None, ref_year=None) -> dict:
    from matching.m3_candidates import normalize_text, normalize_authors, similarity
    bt = normalize_text(bib_title)
    rt = normalize_text(ref_title)
    ba = normalize_authors(bib_authors)
    ra = normalize_authors(ref_authors)
    return {
        "bib_title": bib_title,
        "ref_title": ref_title,
        "bib_title_normalized": bt,
        "ref_title_normalized": rt,
        "bib_authors_normalized": ba,
        "ref_authors_normalized": ra,
        "title_similarity": similarity(bt, rt),
        "author_similarity": similarity(ba, ra),
        "title_exact": bt == rt and bool(bt),
        "ref_arxiv_id": ref_arxiv_id,
        "year": year,
        "ref_year": ref_year,
    }


# ── compute_string_features ───────────────────────────────────────────────────

EXPECTED_KEYS = {
    "title_sim", "author_sim", "title_exact", "title_jaccard",
    "title_len_ratio", "year_exact", "year_close",
    "first_author_match", "venue_sim",
}

def test_feature_keys_present():
    feats = compute_string_features(_cand("Attention Is All You Need", "Attention Is All You Need"))
    assert EXPECTED_KEYS == set(feats.keys())

def test_feature_values_in_range():
    feats = compute_string_features(_cand("Deep Learning", "Deep Learning Models"))
    for k, v in feats.items():
        assert 0.0 <= v <= 1.0, f"{k}={v} out of [0,1]"

def test_exact_title_match():
    title = "Transformers for Natural Language Processing"
    feats = compute_string_features(_cand(title, title))
    assert feats["title_exact"] == 1.0
    assert feats["title_sim"] > 0.99
    assert feats["title_jaccard"] == 1.0

def test_different_titles():
    feats = compute_string_features(_cand("Attention Is All You Need", "BERT: Pre-training of Deep Bidirectional Transformers"))
    assert feats["title_exact"] == 0.0
    assert feats["title_sim"] < 0.5

def test_year_exact():
    feats = compute_string_features(_cand(year="2017", ref_year="2017"))
    assert feats["year_exact"] == 1.0
    assert feats["year_close"] == 1.0

def test_year_close_not_exact():
    feats = compute_string_features(_cand(year="2017", ref_year="2016"))
    assert feats["year_exact"] == 0.0
    assert feats["year_close"] == 1.0

def test_year_far():
    feats = compute_string_features(_cand(year="2017", ref_year="2010"))
    assert feats["year_exact"] == 0.0
    assert feats["year_close"] == 0.0

def test_year_missing():
    feats = compute_string_features(_cand())
    assert feats["year_exact"] == 0.0
    assert feats["year_close"] == 0.0

def test_first_author_match():
    feats = compute_string_features(_cand(bib_authors="Vaswani, Ashish", ref_authors="ashish vaswani noam shazeer"))
    assert feats["first_author_match"] == 1.0

def test_first_author_no_match():
    feats = compute_string_features(_cand(bib_authors="LeCun, Yann", ref_authors="ashish vaswani noam shazeer"))
    assert feats["first_author_match"] == 0.0

def test_year_helper():
    assert _year("2017") == 2017
    assert _year("Published in 2020.") == 2020
    assert _year("") is None
    assert _year(None) is None


# ── _match_entry (auto-labeler rules) ─────────────────────────────────────────

def test_match_exact_title():
    title = "Attention Is All You Need"
    cands = [_cand(title, title, ref_arxiv_id="1706.03762")]
    assert _match_entry(cands) == "1706.03762"

def test_match_rule2_high_sim():
    # title_sim >= 0.85, author_sim >= 0.60
    cands = [_cand(
        "Attention Is All You Need",
        "Attention Is All You Need!",          # near-exact title
        bib_authors="Vaswani, Ashish",
        ref_authors="ashish vaswani",
        ref_arxiv_id="1706.03762",
    )]
    assert _match_entry(cands) == "1706.03762"

def test_no_match_low_sim():
    cands = [_cand("Deep Learning", "Reinforcement Learning Overview", ref_arxiv_id="1234.56789")]
    assert _match_entry(cands) == ""

def test_no_match_empty_candidates():
    assert _match_entry([]) == ""

def test_match_returns_empty_when_ref_arxiv_id_absent():
    # Even if title matches, no ref_arxiv_id -> groundtruth is ''
    title = "Exact Title Match"
    cands = [_cand(title, title, ref_arxiv_id="")]
    assert _match_entry(cands) == ""

def test_match_rule3_moderate_title_with_year():
    # title_sim >= 0.70 and year_exact=1.0
    cands = [_cand(
        "Deep Residual Learning for Image Recognition",
        "Deep Residual Learning for Image Recognitions",   # ~0.97 sim
        year="2016", ref_year="2016",
        ref_arxiv_id="1512.03385",
    )]
    assert _match_entry(cands) == "1512.03385"


# ── label_paper integration (no disk I/O, using tmp_path fixture) ─────────────

def test_label_paper_no_candidates(tmp_path):
    pdir = tmp_path / "2301-00001"
    pdir.mkdir()
    # No candidates.json, no bibtex/refs -> empty result
    rows = label_paper(pdir)
    assert rows == []

def test_label_paper_from_candidates_json(tmp_path):
    import json as _json
    pdir = tmp_path / "2301-00001"
    pdir.mkdir()
    title = "Attention Is All You Need"
    cands = [_cand(title, title, ref_arxiv_id="1706.03762")]
    cands[0]["arxiv_id"] = "2301.00001"
    cands[0]["citation_key"] = "vaswani2017"
    (pdir / "candidates.json").write_text(_json.dumps(cands), encoding="utf-8")
    rows = label_paper(pdir)
    assert len(rows) == 1
    assert rows[0]["citation_key"] == "vaswani2017"
    assert rows[0]["groundtruth_arxiv_id"] == "1706.03762"

def test_label_paper_no_match(tmp_path):
    import json as _json
    pdir = tmp_path / "2301-00002"
    pdir.mkdir()
    cands = [_cand("Quantum Gravity Theory", "Stochastic Gradient Descent", ref_arxiv_id="9999.99999")]
    cands[0]["arxiv_id"] = "2301.00002"
    cands[0]["citation_key"] = "somekey"
    (pdir / "candidates.json").write_text(_json.dumps(cands), encoding="utf-8")
    rows = label_paper(pdir)
    assert len(rows) == 1
    assert rows[0]["groundtruth_arxiv_id"] == ""
