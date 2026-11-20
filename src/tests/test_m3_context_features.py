"""Test m3_context_features: dữ liệu giả nhỏ, tính tay được kết quả mong đợi.

Chạy (từ thư mục gốc repo):  python -m pytest src/tests/test_m3_context_features.py -q
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from matching import m3_context_features as F  # noqa: E402
from utils import config as C  # noqa: E402


def cand(cid, key, ref, title_sim, author_sim=0.5, arxiv_id="2602.00001"):
    return {"candidate_id": cid, "arxiv_id": arxiv_id, "citation_key": key, "ref_arxiv_id": ref,
            "title_similarity": title_sim, "author_similarity": author_sim}


def ref(arxiv, title="Some Title", authors=("Jane Doe",), date="2020-05-01", venue="arXiv.org"):
    return {"ref_arxiv_id": arxiv, "ref_title": title, "ref_authors": list(authors),
            "ref_submission_date": date, "ref_venue": venue}


def get(frame, cid, column):
    value = frame.loc[frame["candidate_id"] == cid, column].iloc[0]
    return None if pd.isna(value) else float(value)


# ----------------------------------------------------------------------------- cite
def test_cite_keys_handles_options_and_multiple_keys():
    text = r"As shown \citep[see][p.~2]{a, b} and \citet{c} and \nocite{d}, but not \ref{e}."
    assert F.cite_keys(text) == ["a", "b", "c", "d"]
    assert "cite" not in F.strip_cites(text)


# ----------------------------------------------------------------------------- nhóm
def make_group_frame():
    # entry e1: r1 (0.9) r2 (0.5); entry e2: r1 (0.6) r3 (0.6); entry e3: r2 (0.4)
    candidates = [cand("c1", "e1", "1", 0.9), cand("c2", "e1", "2", 0.5),
                  cand("c3", "e2", "1", 0.6), cand("c4", "e2", "3", 0.6),
                  cand("c5", "e3", "2", 0.4)]
    return F.compute_features(candidates, [], [], [], []), candidates


def test_rank_and_margin_within_entry():
    frame, _ = make_group_frame()
    assert get(frame, "c1", "ctx_rank_in_entry") == 1 and get(frame, "c2", "ctx_rank_in_entry") == 2
    assert get(frame, "c1", "ctx_n_cand_entry") == 2 and get(frame, "c5", "ctx_n_cand_entry") == 1
    assert get(frame, "c1", "ctx_title_margin") == pytest.approx(0.4)    # 0.9 - 0.5
    assert get(frame, "c2", "ctx_title_margin") == pytest.approx(-0.4)   # 0.5 - 0.9
    assert get(frame, "c5", "ctx_title_margin") is None                  # entry chỉ có một ứng viên


def test_tie_gives_equal_rank_and_zero_margin():
    frame, _ = make_group_frame()
    assert get(frame, "c3", "ctx_rank_in_entry") == 1 and get(frame, "c4", "ctx_rank_in_entry") == 1
    assert get(frame, "c3", "ctx_title_margin") == pytest.approx(0.0)


def test_reverse_rank_and_mutual_best():
    frame, _ = make_group_frame()
    # reference "1": e1 (0.9) và e2 (0.6) -> c1 hạng 1, c3 hạng 2
    assert get(frame, "c1", "ctx_rank_in_ref") == 1 and get(frame, "c3", "ctx_rank_in_ref") == 2
    assert get(frame, "c1", "ctx_n_cand_ref") == 2
    assert get(frame, "c1", "ctx_ref_margin") == pytest.approx(0.3)
    assert get(frame, "c1", "ctx_mutual_best") == 1
    assert get(frame, "c3", "ctx_mutual_best") == 0                      # tốt nhất trong entry, không tốt nhất với reference
    assert get(frame, "c4", "ctx_mutual_best") == 1                      # reference "3" chỉ có e2


# ----------------------------------------------------------------------------- theo cặp
def test_year_venue_and_arxiv_flag():
    candidates = [cand("c1", "e1", "1", 0.9), cand("c2", "e1", "2", 0.9), cand("c3", "e1", "3", 0.9)]
    bib = [{"citation_key": "e1", "year": 2022, "journal": "Journal of Testing", "source_versions": [1, 2]}]
    refs = [ref("1", date="2020-05-01", venue="Journal of Testing"),
            ref("2", date=None, venue="arXiv.org"),
            ref("3", venue="")]
    frame = F.compute_features(candidates, bib, refs, [], [])
    assert get(frame, "c1", "ctx_year_diff") == 2 and get(frame, "c1", "ctx_year_signed") == -2
    assert get(frame, "c1", "ctx_venue_sim") == pytest.approx(1.0)
    assert get(frame, "c1", "ctx_ref_venue_is_arxiv") == 0
    assert get(frame, "c2", "ctx_year_diff") is None                     # thiếu ngày -> null, không phải 0
    assert get(frame, "c2", "ctx_venue_sim") < 0.5                       # venue khác hẳn
    assert get(frame, "c2", "ctx_ref_venue_is_arxiv") == 1
    assert get(frame, "c3", "ctx_venue_sim") is None                     # reference thiếu venue -> null
    assert get(frame, "c3", "ctx_ref_venue_is_arxiv") is None
    assert get(frame, "c1", "ctx_n_source_versions") == 2


def test_citation_context_overlap_sections_and_author_mention():
    elements = [
        {"element_id": "p#root1", "arxiv_id": "p", "kind": "document", "content": "Version 1 root"},
        {"element_id": "p#h1", "arxiv_id": "p", "kind": "heading", "content": "Intro"},
        {"element_id": "p#h2", "arxiv_id": "p", "kind": "heading", "content": "Method"},
        {"element_id": "p#s1", "arxiv_id": "p", "kind": "sentence",
         "content": r"Doe et al. proposed graph planning agents \cite{e1}."},
        {"element_id": "p#s2", "arxiv_id": "p", "kind": "sentence",
         "content": r"We reuse graph planning \cite{e1,e2}."},
        {"element_id": "p#s3", "arxiv_id": "p", "kind": "sentence", "content": "No citation here."},
    ]
    edges = [
        {"arxiv_id": "p", "version": 1, "element_id": "p#h1", "parent_id": "p#root1"},
        {"arxiv_id": "p", "version": 1, "element_id": "p#h2", "parent_id": "p#root1"},
        {"arxiv_id": "p", "version": 1, "element_id": "p#s1", "parent_id": "p#h1"},
        {"arxiv_id": "p", "version": 1, "element_id": "p#s2", "parent_id": "p#h2"},
        {"arxiv_id": "p", "version": 1, "element_id": "p#s3", "parent_id": "p#h2"},
    ]
    contexts = F.build_cite_contexts(elements, edges)
    assert len(contexts["e1"]["texts"]) == 2 and contexts["e1"]["sections"] == {"p#h1", "p#h2"}
    assert len(contexts["e2"]["texts"]) == 1

    candidates = [cand("c1", "e1", "1", 0.8), cand("c2", "e1", "2", 0.8)]
    refs = [ref("1", title="Graph Planning for Agents", authors=("Jane Doe",)),
            ref("2", title="Quantum Chromodynamics Lattice", authors=("Max Mustermann",))]
    frame = F.compute_features(candidates, [{"citation_key": "e1"}], refs, elements, edges)
    # từ khóa của "Graph Planning for Agents" = {graph, planning, agents}; cả ba nằm trong ngữ cảnh
    assert get(frame, "c1", "ctx_cite_title_overlap") == pytest.approx(1.0)
    assert get(frame, "c2", "ctx_cite_title_overlap") == pytest.approx(0.0)
    assert get(frame, "c1", "ctx_cite_author_mentioned") == 1            # "Doe" xuất hiện trong câu
    assert get(frame, "c2", "ctx_cite_author_mentioned") == 0
    assert get(frame, "c1", "ctx_n_cite_sentences") == 2 and get(frame, "c1", "ctx_n_cite_sections") == 2


def test_entry_without_citing_sentences_gets_null_not_zero():
    frame = F.compute_features([cand("c1", "e1", "1", 0.8)], [], [ref("1")], [], [])
    assert get(frame, "c1", "ctx_cite_title_overlap") is None
    assert get(frame, "c1", "ctx_cite_author_mentioned") is None
    assert get(frame, "c1", "ctx_n_cite_sentences") == 0                 # đếm được thì là 0 thật


# ----------------------------------------------------------------------------- hợp đồng
def test_one_row_per_candidate_same_order_and_numeric_columns():
    frame, candidates = make_group_frame()
    F.validate_features(frame, candidates)
    assert list(frame["candidate_id"]) == [c["candidate_id"] for c in candidates]
    assert tuple(frame.columns) == F.KEY_COLUMNS + F.CTX_COLUMNS
    for name in F.CTX_COLUMNS:
        assert pd.api.types.is_numeric_dtype(frame[name]), name
    with pytest.raises(ValueError):
        F.validate_features(frame.iloc[:-1], candidates)                 # thiếu dòng


def write_paper(root: Path, candidates, bib=None, refs=None, elements=None, edges=None,
                arxiv_id="2602.00001"):
    pdir = root / C.paper_key(arxiv_id)
    pdir.mkdir(parents=True)
    for name, payload in (("candidates.json", candidates), ("bibtex_merged.json", bib or []),
                          ("refs.json", {"arxiv_id": arxiv_id, "references": refs or []}),
                          ("elements.json", elements or []), ("hierarchy.json", edges or []),
                          ("paper.json", {"arxiv_id": arxiv_id})):
        (pdir / name).write_text(json.dumps(payload), encoding="utf-8")


def test_process_paper_writes_parquet_and_merges_str_features(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    candidates = [cand("c1", "e1", "1", 0.9), cand("c2", "e1", "2", 0.4)]
    write_paper(tmp_path, candidates, bib=[{"citation_key": "e1", "year": 2021}],
                refs=[ref("1"), ref("2")])

    result = F.process_paper("2602.00001")
    assert result["rows"] == 2 and result["has_str"] is False
    out = pd.read_parquet(F.features_path("2602.00001"))
    assert list(out["candidate_id"]) == ["c1", "c2"]

    # Tri ghi file .str.parquet (thứ tự dòng khác cũng được ghép đúng theo candidate_id)
    str_frame = pd.DataFrame({"candidate_id": ["c2", "c1"], "str_title_jaccard": [0.1, 0.9]})
    str_frame.to_parquet(F.features_path("2602.00001", ".str"), index=False)
    result = F.process_paper("2602.00001")
    assert result["has_str"] is True
    out = pd.read_parquet(F.features_path("2602.00001"))
    assert list(out.columns) == list(F.KEY_COLUMNS) + ["str_title_jaccard"] + list(F.CTX_COLUMNS)
    assert out.loc[out.candidate_id == "c1", "str_title_jaccard"].iloc[0] == pytest.approx(0.9)


def test_mismatched_str_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    write_paper(tmp_path, [cand("c1", "e1", "1", 0.9)], refs=[ref("1")])
    F.features_dir().mkdir(parents=True)
    pd.DataFrame({"candidate_id": ["c1", "zzz"], "str_x": [1.0, 2.0]}).to_parquet(
        F.features_path("2602.00001", ".str"), index=False)
    result = F._safe_process("2602.00001")
    assert "error" in result                                             # lỗi được báo, không nuốt im lặng


def test_paper_without_candidates_writes_empty_valid_table(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    write_paper(tmp_path, [])
    assert F.process_paper("2602.00001")["rows"] == 0
    out = pd.read_parquet(F.features_path("2602.00001"))
    assert len(out) == 0 and tuple(out.columns) == F.KEY_COLUMNS + F.CTX_COLUMNS
    assert F.discover_ids() == ["2602.00001"]


def test_no_nan_leaks_into_integer_columns():
    frame, _ = make_group_frame()
    for name, code in F.CTX_DTYPES.items():
        if code != "f":
            assert str(frame[name].dtype) in {"Int8", "Int16"}, name
        else:
            assert not any(math.isinf(v) for v in frame[name].dropna()), name