"""Kiểm thử luật gán nhãn tự động và feature chuỗi của M3 (chạy: pytest tests/test_m3_labeler_features.py)."""
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from matching import m3_auto_labeler as AL  # noqa: E402
from matching import m3_evaluate as EV  # noqa: E402
from matching.m3_candidates import generate_candidates  # noqa: E402
from matching.m3_string_features import compute_string_features, enrich_candidate  # noqa: E402
from utils import config as C  # noqa: E402

PID = "2602.04870"


def _ref(rid, title, authors=("Wei Zhang",), date="2020-01-01", doi=None):
    return {"ref_arxiv_id": rid, "ref_title": title, "ref_authors": list(authors),
            "ref_submission_date": date, "ref_s2_id": f"s2_{rid}", "ref_venue": "",
            "external_ids": {"ArXiv": rid, "DOI": doi}}


def _bib(key, title="", **kw):
    return {"citation_key": key, "arxiv_id": PID, "title": title, "author": kw.pop("author", ""), **kw}


def _write_paper(root: Path, bib, refs) -> Path:
    pdir = root / C.paper_key(PID)
    pdir.mkdir(parents=True)
    (pdir / "bibtex_merged.json").write_text(json.dumps(bib))
    (pdir / "refs.json").write_text(json.dumps({"arxiv_id": PID, "references": refs}))
    (pdir / "paper.json").write_text(json.dumps({"arxiv_id": PID}))
    (pdir / "candidates.json").write_text(json.dumps(generate_candidates(bib, refs)))
    return pdir


def _labels(root, pdir):
    AL.label_corpus(root, [pdir], paper_id_of=lambda p: PID)
    path = C.labels_path(PID, root)
    return json.loads(path.read_text())["labels"] if path.exists() else {}


# ---------------------------------------------------------------- feature
def test_missing_ref_year_is_nan_not_borrowed_from_bib():
    cand = {"bib_title": "a b", "ref_title": "a b", "year": 2020}      # không có ref_year
    assert math.isnan(compute_string_features(cand)["year_exact"])


def test_year_exact_when_both_known():
    f = compute_string_features({"bib_title": "x", "ref_title": "x", "year": 2020, "ref_year": "2015-03-01"})
    assert f["year_exact"] == 0.0 and f["year_close"] == 0.0


def test_first_author_uses_family_name():
    base = {"bib_title": "x", "ref_title": "x", "bib_author_raw": "Smith, John and Zhang, Wei"}
    assert compute_string_features({**base, "ref_authors_raw": ["John Doe"]})["first_author_match"] == 0.0
    assert compute_string_features({**base, "ref_authors_raw": ["Jane Smith"]})["first_author_match"] == 1.0
    assert compute_string_features({**base, "ref_authors_raw": ["J. Smith", "A. B"]})["first_author_match"] == 1.0


def test_eprint_doi_features_tristate():
    bib_with = _bib("k", "t", eprint="2001.00001")
    ref = _ref("2001.00001", "t")
    enriched = enrich_candidate({"bib_title": "t", "ref_title": "t"}, bib_with, ref)
    assert compute_string_features(enriched)["eprint_match"] == 1.0
    enriched = enrich_candidate({"bib_title": "t", "ref_title": "t"}, bib_with, _ref("2002.00002", "t"))
    assert compute_string_features(enriched)["eprint_match"] == 0.0
    enriched = enrich_candidate({"bib_title": "t", "ref_title": "t"}, _bib("k", "t"), ref)
    assert math.isnan(compute_string_features(enriched)["eprint_match"])
    assert math.isnan(compute_string_features(enriched)["doi_match"])


# ---------------------------------------------------------------- labeler
def test_bibitem_without_title_labeled_by_eprint(tmp_path):
    bib = [_bib("b1", "", raw_text="W. Zhang. Some paper. arXiv:2001.00001, 2020.")]
    pdir = _write_paper(tmp_path, bib, [_ref("2001.00001", "Deep Graph Networks")])
    assert _labels(tmp_path, pdir) == {"b1": "2001.00001"}


def test_eprint_conflict_vetoes_title_exact(tmp_path):
    bib = [_bib("b1", "Deep Graph Networks", eprint="1999.99999")]
    pdir = _write_paper(tmp_path, bib, [_ref("2001.00001", "Deep Graph Networks")])
    assert _labels(tmp_path, pdir) == {}


def test_two_refs_same_title_is_ambiguous(tmp_path):
    bib = [_bib("b1", "Deep Graph Networks")]
    refs = [_ref("2001.00001", "Deep Graph Networks"), _ref("2002.00002", "Deep Graph Networks")]
    pdir = _write_paper(tmp_path, bib, refs)
    assert _labels(tmp_path, pdir) == {}


def test_year_rule_needs_year_on_both_sides(tmp_path):
    t_bib, t_ref = "Deep Graph Networks For Vision", "Deep Graph Networks For Vision Tasks In Practice"  # sim ~0.79: chỉ luật 5
    with_year = tmp_path / "with_year"
    pdir = _write_paper(with_year, [_bib("b1", t_bib, year=2020, author="Zhang, Wei")], [_ref("2001.00001", t_ref, date="2020-05-05")])
    assert _labels(with_year, pdir) == {"b1": "2001.00001"}            # luật 5: title_sim>=0.70, năm khớp và họ tác giả đầu khớp
    no_year = tmp_path / "no_year"
    pdir = _write_paper(no_year, [_bib("b1", t_bib, year=2020, author="Zhang, Wei")], [_ref("2001.00001", t_ref, date=None)])
    assert _labels(no_year, pdir) == {}                                # thiếu năm reference: không được coi là khớp


def test_one_ref_goes_to_strongest_entry(tmp_path):
    bib = [_bib("a_title", "Deep Graph Networks"), _bib("b_eprint", "", raw_text="arXiv:2001.00001")]
    pdir = _write_paper(tmp_path, bib, [_ref("2001.00001", "Deep Graph Networks")])
    assert _labels(tmp_path, pdir) == {"b_eprint": "2001.00001"}


def test_relabel_overwrites_auto_and_never_touches_manual(tmp_path):
    bib = [_bib("b1", "Deep Graph Networks")]
    pdir = _write_paper(tmp_path, bib, [_ref("2001.00001", "Deep Graph Networks")])
    path = C.labels_path(PID, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"arxiv_id": PID, "source": "auto", "labeler": "old",
                                "labels": {"b1": "0000.00000"}, "evidence": {"b1": "title_exact"}}))
    assert _labels(tmp_path, pdir) == {"b1": "2001.00001"}             # nhãn auto cũ bị ghi đè
    manual = {"arxiv_id": PID, "source": "manual", "labeler": "me", "labels": {"b1": None}}
    path.write_text(json.dumps(manual))
    AL.label_corpus(tmp_path, [pdir], paper_id_of=lambda p: PID)
    assert json.loads(path.read_text()) == manual                      # nhãn tay giữ nguyên


# ---------------------------------------------------------------- evaluate
def test_grouped_ranking_matches_full_scan(tmp_path):
    rows = [("2602.00001", f"k{i % 3}", f"1901.{j:05d}", (i * 7 + j * 3) % 11 / 10)
            for i in range(9) for j in range(4)]
    df = pd.DataFrame(rows, columns=["arxiv_id", "citation_key", "ref_arxiv_id", "score"])
    df["candidate_id"] = [str(i) for i in range(len(df))]
    df["model_version"] = "v"
    scores_file = tmp_path / "scores.parquet"
    df.to_parquet(scores_file)
    scores = EV.load_scores(scores_file)
    groups = EV._group_scores(scores)
    for key in ("k0", "k1", "k2", "missing"):
        assert EV._ranking_for_entry(scores, "2602.00001", key, groups) == EV._ranking_for_entry(scores, "2602.00001", key)


# ---------------------------------------------------------------- bài thật 2602.08695 (lỗi ID giả từ URL/DOI)
from matching.m3_candidates import bib_arxiv_ids, canonical_arxiv_id, reference_arxiv_ids  # noqa: E402


@pytest.mark.parametrize("url", [
    "https://doi.org/10.1145/792538.792543",
    "https://ieeexplore.ieee.org/document/5128907",
    "http://www.jstor.org/stable/2032342",
])
def test_urls_do_not_produce_fake_arxiv_ids(url):
    assert bib_arxiv_ids({"url": url}) == set()


def test_real_arxiv_forms_are_recognised():
    assert bib_arxiv_ids({"url": "https://doi.org/10.48550/arXiv.2501.08203"}) == {"2501.08203"}
    assert bib_arxiv_ids({"eprint": "arXiv:2001.00001v2"}) == {"2001.00001"}
    assert bib_arxiv_ids({"url": "http://arxiv.org/abs/hep-th/9901001"}) == {"hep-th/9901001"}


def test_old_style_subject_class_is_ignored_when_comparing():
    assert canonical_arxiv_id("math.GT/0309136") == "math/0309136"
    ref = {"ref_arxiv_id": "math/0309136", "ref_title": "t"}
    assert bib_arxiv_ids({"eprint": "math.GT/0309136"}) & reference_arxiv_ids(ref)


def test_doi_url_does_not_veto_exact_title(tmp_path):
    """Bài thật: url là doi.org/10.1145/792538.792543 từng bị coi là 'eprint 2538.79254' và chặn nhãn đúng."""
    bib = [_bib("lpn2003", "Noise-tolerant learning, the parity problem, and the statistical query model",
                url="https://doi.org/10.1145/792538.792543", doi="10.1145/792538.792543", year=2003)]
    ref = _ref("cs/0010022", "Noise-tolerant learning, the parity problem, and the statistical query model",
               doi="10.1145/335305.335355")
    pdir = _write_paper(tmp_path, bib, [ref])
    assert _labels(tmp_path, pdir) == {"lpn2003": "cs/0010022"}


# ---------------------------------------------------------------- bài thật 2602.08163 (bibitem kiểu IEEE, "et al")
def test_et_al_does_not_become_family_name():
    base = {"bib_title": "x", "ref_title": "x", "ref_authors_raw": ["Zhiyuan Li", "Wei Zhang"]}
    for raw in ("Z. Li et al", "Z. Li et al.", "Z. Li and others", "Li, Z. and others"):
        assert compute_string_features({**base, "bib_author_raw": raw})["first_author_match"] == 1.0, raw
    assert compute_string_features({**base, "bib_author_raw": "C. X. Wang et al"})["first_author_match"] == 0.0
    assert math.isnan(compute_string_features({**base, "bib_author_raw": "others"})["first_author_match"])


def test_title_note_and_dash_variants_are_exact(tmp_path):
    bib = [_bib("b1", "From Orthogonal Time-Frequency Space to Affine Frequency-Division Multiplexing: "
                      "A comparative study of waveforms", author="H. Rou et al", year=2024)]
    ref = _ref("2401.07700", "From Orthogonal Time\u2013Frequency Space to Affine Frequency-Division Multiplexing: "
                             "A comparative study of waveforms [Special Issue on Signal Processing for ISAC]")
    pdir = _write_paper(tmp_path, bib, [ref])
    assert _labels(tmp_path, pdir) == {"b1": "2401.07700"}


def test_similar_title_same_year_without_author_support_is_not_labeled(tmp_path):
    """Ca thật: 'Chirp parameter selection for AFDM with MMSE equalization' vs 'Parameter Design for Secure AFDM'."""
    bib = [_bib("b1", "Chirp parameter selection for affine frequency division multiplexing with MMSE equalization",
                author="Z. Li et al", year=2025)]
    ref = _ref("2503.19364", "Parameter Design for Secure Affine Frequency Division Multiplexing Waveform",
               authors=("Xiao Wu", "Yan Chen"), date="2025-03-24")
    pdir = _write_paper(tmp_path, bib, [ref])
    assert _labels(tmp_path, pdir) == {}


def test_high_title_sim_with_first_author_match_is_labeled(tmp_path):
    bib = [_bib("b1", "Deep graph networks for vision tasks", author="W. Zhang et al", year=2020)]
    ref = _ref("2001.00001", "Deep Graph Networks for Vision Tasks!", authors=("Wei Zhang", "Anna Kim"))
    pdir = _write_paper(tmp_path, bib, [ref])
    assert _labels(tmp_path, pdir) == {"b1": "2001.00001"}