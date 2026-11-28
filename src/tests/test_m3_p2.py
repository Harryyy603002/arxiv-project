"""Tests for P2's M3 modules: m3_string_features and m3_auto_labeler.

Chạy:  pytest src/tests/test_m3_p2.py -v
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_string_features import STR_FEATURE_NAMES, compute_string_features, _year
from matching.m3_auto_labeler import match_entry, label_paper_entries


# ── helpers ───────────────────────────────────────────────────────────────────

def _cand(bib_title="", ref_title="", bib_authors="", ref_authors="",
          ref_arxiv_id="2301.00001", year=None, ref_year=None,
          title_similarity=None) -> dict:
    from matching.m3_candidates import normalize_text, normalize_authors, similarity
    bt = normalize_text(bib_title)
    rt = normalize_text(ref_title)
    ba = normalize_authors(bib_authors)
    ra = normalize_authors(ref_authors)
    tsim = title_similarity if title_similarity is not None else similarity(bt, rt)
    return {
        "bib_title": bib_title,
        "ref_title": ref_title,
        "bib_title_normalized": bt,
        "ref_title_normalized": rt,
        "bib_authors_normalized": ba,
        "ref_authors_normalized": ra,
        "title_similarity": tsim,
        "author_similarity": similarity(ba, ra),
        "title_exact": bt == rt and bool(bt),
        "ref_arxiv_id": ref_arxiv_id,
        "year": year,
        "ref_year": ref_year,
    }


def _nan(v) -> bool:
    try:
        return math.isnan(v)
    except TypeError:
        return False


# ── compute_string_features ───────────────────────────────────────────────────

EXPECTED_KEYS = set(STR_FEATURE_NAMES)

def test_feature_keys_present():
    feats = compute_string_features(_cand("Attention Is All You Need", "Attention Is All You Need"))
    assert EXPECTED_KEYS == set(feats.keys())

def test_non_nan_features_in_range():
    feats = compute_string_features(_cand("Deep Learning", "Deep Learning Models"))
    for k, v in feats.items():
        if not _nan(v):
            assert 0.0 <= v <= 1.0, f"{k}={v} out of [0,1]"

def test_exact_title_match():
    title = "Transformers for Natural Language Processing"
    feats = compute_string_features(_cand(title, title))
    assert feats["title_exact"] == 1.0
    assert feats["title_sim"] > 0.99
    assert feats["title_jaccard"] == 1.0

def test_different_titles():
    feats = compute_string_features(_cand("Attention Is All You Need",
                                          "BERT Pre-training of Deep Bidirectional Transformers"))
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

def test_year_missing_gives_nan():
    # Both sides missing -> NaN (not 0.0); model handles NaN separately
    feats = compute_string_features(_cand())
    assert _nan(feats["year_exact"])
    assert _nan(feats["year_close"])

def test_year_helper():
    assert _year("2017") == 2017
    assert _year("Published in 2020.") == 2020
    assert _year("") is None
    assert _year(None) is None


# ── match_entry (auto-labeler rules) ─────────────────────────────────────────
# match_entry(entry_candidates, bib, ref_index) -> (ref_id, evidence, tsim, ambiguous)
# ref_index is a tuple of two dicts; pass empty ones for unit tests.

_EMPTY_REF_INDEX = ({}, {})

def test_match_exact_title():
    title = "Attention Is All You Need"
    cands = [_cand(title, title, ref_arxiv_id="1706.03762")]
    ref_id, evidence, _tsim, ambiguous = match_entry(cands, {}, _EMPTY_REF_INDEX)
    assert ref_id == "1706.03762"
    assert evidence == "title_exact"
    assert not ambiguous

def test_match_rule_high_sim():
    # title_sim >= 0.85, author_sim >= 0.60
    cands = [_cand(
        "Attention Is All You Need",
        "Attention Is All You Need!",
        bib_authors="Vaswani, Ashish",
        ref_authors="ashish vaswani",
        ref_arxiv_id="1706.03762",
    )]
    ref_id, evidence, _tsim, ambiguous = match_entry(cands, {}, _EMPTY_REF_INDEX)
    assert ref_id == "1706.03762"
    assert evidence in ("title_exact", "title_fuzzy")
    assert not ambiguous

def test_no_match_low_sim():
    cands = [_cand("Deep Learning", "Reinforcement Learning Overview", ref_arxiv_id="1234.56789")]
    ref_id, evidence, _tsim, ambiguous = match_entry(cands, {}, _EMPTY_REF_INDEX)
    assert ref_id == ""
    assert evidence is None
    assert not ambiguous

def test_no_match_empty_candidates():
    ref_id, evidence, _tsim, ambiguous = match_entry([], {}, _EMPTY_REF_INDEX)
    assert ref_id == ""
    assert evidence is None

def test_match_returns_empty_when_ref_arxiv_id_absent():
    title = "Exact Title Match"
    cands = [_cand(title, title, ref_arxiv_id="")]
    ref_id, _ev, _tsim, _amb = match_entry(cands, {}, _EMPTY_REF_INDEX)
    assert ref_id == ""


# ── label_paper_entries integration (tmp_path, no real corpus) ───────────────

def test_label_paper_no_candidates(tmp_path):
    pdir = tmp_path / "2301-00001"
    pdir.mkdir()
    result = label_paper_entries(pdir)
    assert result["rows"] == []
    assert result["labels"] == {}

def test_label_paper_from_candidates_json(tmp_path):
    pdir = tmp_path / "2301-00001"
    pdir.mkdir()
    title = "Attention Is All You Need"
    cands = [_cand(title, title, ref_arxiv_id="1706.03762")]
    cands[0]["arxiv_id"] = "2301.00001"
    cands[0]["citation_key"] = "vaswani2017"
    (pdir / "candidates.json").write_text(json.dumps(cands), encoding="utf-8")
    result = label_paper_entries(pdir, arxiv_id="2301.00001")
    assert result["labels"].get("vaswani2017") == "1706.03762"
    rows = result["rows"]
    assert len(rows) == 1
    assert rows[0]["citation_key"] == "vaswani2017"
    assert rows[0]["groundtruth_arxiv_id"] == "1706.03762"

def test_label_paper_no_match(tmp_path):
    pdir = tmp_path / "2301-00002"
    pdir.mkdir()
    cands = [_cand("Quantum Gravity Theory", "Stochastic Gradient Descent", ref_arxiv_id="9999.99999")]
    cands[0]["arxiv_id"] = "2301.00002"
    cands[0]["citation_key"] = "somekey"
    (pdir / "candidates.json").write_text(json.dumps(cands), encoding="utf-8")
    result = label_paper_entries(pdir, arxiv_id="2301.00002")
    assert result["labels"].get("somekey") is None  # no match -> absent, not ''
    row = next(r for r in result["rows"] if r["citation_key"] == "somekey")
    assert row["groundtruth_arxiv_id"] == ""


def test_label_paper_entries_no_candidates(tmp_path):
    pdir = tmp_path / "empty_paper"
    pdir.mkdir()
    result = label_paper_entries(pdir, arxiv_id="2301.00003")
    assert result["rows"] == []
    assert result["labels"] == {}
    assert result["evidence"] == {}
    assert result["n_ambiguous"] == 0

