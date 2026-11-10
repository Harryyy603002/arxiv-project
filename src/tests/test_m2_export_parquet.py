"""Test offline cho m2_export_parquet (không gọi mạng): pytest src/tests/test_m2_export_parquet.py"""
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from parser import m2_export_parquet as E  # noqa: E402
from utils import schemas as S  # noqa: E402


def write_paper(corpus, key, arxiv_id, sentences=1):
    paper = corpus / key
    paper.mkdir(parents=True)
    elements = [
        {"element_id": f"{key}#root", "arxiv_id": arxiv_id, "kind": "document", "content": None},
        {"element_id": f"{key}#h1", "arxiv_id": arxiv_id, "kind": "heading", "content": "Introduction"},
    ]
    hierarchy = [
        {"arxiv_id": arxiv_id, "version": "v1", "element_id": f"{key}#root", "parent_id": None},
        {"arxiv_id": arxiv_id, "version": "v1", "element_id": f"{key}#h1", "parent_id": f"{key}#root"},
    ]
    for i in range(sentences):
        elements.append({"element_id": f"{key}#s{i}", "arxiv_id": arxiv_id, "kind": "sentence",
                         "content": "We follow \\cite{vaswani2017}."})
        hierarchy.append({"arxiv_id": arxiv_id, "version": "v1", "element_id": f"{key}#s{i}",
                          "parent_id": f"{key}#h1"})
    bibtex = [{"arxiv_id": arxiv_id, "citation_key": "vaswani2017", "entry_type": "inproceedings",
               "title": "Attention Is All You Need", "authors": ["Ashish Vaswani", "Noam Shazeer"],
               "year": "2017", "doi": None, "journal": "NeurIPS", "url": None}]
    (paper / "elements.json").write_text(json.dumps(elements), encoding="utf-8")
    (paper / "hierarchy.json").write_text(json.dumps(hierarchy), encoding="utf-8")
    (paper / "bibtex_merged.json").write_text(json.dumps(bibtex), encoding="utf-8")


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    write_paper(root, "2310-12345", "2310.12345", sentences=2)
    write_paper(root, "2603-00581", "2603.00581", sentences=1)
    return root


def frames():
    elements = pd.DataFrame([
        {"element_id": "r", "arxiv_id": "1", "kind": "document", "content": None},
        {"element_id": "s", "arxiv_id": "1", "kind": "sentence", "content": "x"},
    ])
    hierarchy = pd.DataFrame([
        {"arxiv_id": "1", "version": "v1", "element_id": "r", "parent_id": None},
        {"arxiv_id": "1", "version": "v1", "element_id": "s", "parent_id": "r"},
    ])
    bibtex = pd.DataFrame([{"arxiv_id": "1", "citation_key": "k"}])
    return elements, hierarchy, bibtex


def test_export_writes_three_tables_with_exact_schema(corpus, tmp_path):
    out = tmp_path / "export"

    counts = S.export_m2_parquet(corpus_dir=corpus, out_dir=out)

    assert counts == {"elements": 7, "hierarchy": 5, "bibtex": 2}
    for name in ("elements", "hierarchy", "bibtex"):
        assert pq.read_table(out / f"{name}.parquet").schema.equals(S.ALL_SCHEMAS[name])


def test_export_roundtrips_lists_nulls_and_cite_commands(corpus, tmp_path):
    S.export_m2_parquet(corpus_dir=corpus, out_dir=tmp_path)

    bib = pq.read_table(tmp_path / "bibtex.parquet").to_pandas()
    row = bib[bib["arxiv_id"] == "2310.12345"].iloc[0]
    assert list(row["authors"]) == ["Ashish Vaswani", "Noam Shazeer"]
    assert pd.isna(row["doi"])
    elements = pq.read_table(tmp_path / "elements.parquet").to_pandas()
    sentence = elements[elements["element_id"] == "2310-12345#s0"].iloc[0]
    assert "\\cite{vaswani2017}" in sentence["content"]
    hierarchy = pq.read_table(tmp_path / "hierarchy.parquet").to_pandas()
    assert "2310-12345#root" not in set(hierarchy["element_id"])
    assert hierarchy["parent_id"].notna().all()


def test_export_twice_overwrites_instead_of_duplicating(corpus, tmp_path):
    out = tmp_path / "a" / "export"

    first = S.export_m2_parquet(corpus_dir=corpus, out_dir=out)
    second = S.export_m2_parquet(corpus_dir=corpus, out_dir=out)

    assert first == second
    assert pq.read_table(out / "elements.parquet").num_rows == first["elements"]


def test_export_missing_corpus_writes_empty_tables_with_schema(tmp_path):
    counts = S.export_m2_parquet(corpus_dir=tmp_path / "missing", out_dir=tmp_path / "export")

    assert counts == {"elements": 0, "hierarchy": 0, "bibtex": 0}
    for name in ("elements", "hierarchy", "bibtex"):
        table = pq.read_table(tmp_path / "export" / f"{name}.parquet")
        assert table.num_rows == 0
        assert table.schema.equals(S.ALL_SCHEMAS[name])


def test_export_skips_corrupt_json_non_list_and_loose_files(corpus, tmp_path):
    bad = corpus / "2999-00001"
    bad.mkdir()
    (bad / "elements.json").write_text("{not json", encoding="utf-8")
    (bad / "hierarchy.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    (corpus / "notes.txt").write_text("not a paper", encoding="utf-8")

    counts = S.export_m2_parquet(corpus_dir=corpus, out_dir=tmp_path / "export")

    assert counts == {"elements": 7, "hierarchy": 5, "bibtex": 2}


def test_export_drops_root_edges_and_reports_them(corpus, tmp_path, capsys):
    S.export_m2_parquet(corpus_dir=corpus, out_dir=tmp_path / "export")

    assert "[INFO] Bỏ 2 cạnh nút gốc" in capsys.readouterr().out


def test_hierarchy_version_and_bibtex_year_are_ints(tmp_path):
    hierarchy = pd.DataFrame([
        {"arxiv_id": "1", "version": "v2", "element_id": "e", "parent_id": "p"},
        {"arxiv_id": "1", "version": 3, "element_id": "f", "parent_id": "p"},
    ])
    bibtex = pd.DataFrame([
        {"arxiv_id": "1", "citation_key": "a", "year": "2020"},
        {"arxiv_id": "1", "citation_key": "b", "year": "2019a"},
        {"arxiv_id": "1", "citation_key": "c", "year": None},
        {"arxiv_id": "1", "citation_key": "d", "year": "n.d."},
    ])

    assert S.to_arrow_table(hierarchy, "hierarchy").column("version").to_pylist() == [2, 3]
    assert S.to_arrow_table(bibtex, "bibtex").column("year").to_pylist() == [2020, 2019, None, None]


def test_kind_and_entry_type_are_dictionary_columns():
    assert S.ELEMENTS_SCHEMA.field("kind").type == S.DICT_STRING
    assert S.BIBTEX_SCHEMA.field("entry_type").type == S.DICT_STRING


def test_export_warns_about_orphan_hierarchy_edges(corpus, tmp_path, capsys):
    path = corpus / "2310-12345" / "hierarchy.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows.append({"arxiv_id": "2310.12345", "version": "v1", "element_id": "x", "parent_id": "y"})
    path.write_text(json.dumps(rows), encoding="utf-8")

    S.export_m2_parquet(corpus_dir=corpus, out_dir=tmp_path / "export")

    out = capsys.readouterr().out
    assert "[INTEGRITY WARNING]" in out
    assert "element_id không tồn tại" in out
    assert "parent_id không tồn tại" in out


def test_export_defaults_to_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path / "data")

    assert S.export_m2_parquet() == {"elements": 0, "hierarchy": 0, "bibtex": 0}
    assert (tmp_path / "data" / "elements.parquet").exists()


def test_to_arrow_table_fills_missing_nullable_columns():
    df = pd.DataFrame([{"kind": "sentence", "arxiv_id": "1", "element_id": "e1"}])

    table = S.to_arrow_table(df, "elements")

    assert table.schema.equals(S.ELEMENTS_SCHEMA)
    assert table.column("content").to_pylist() == [None]


def test_write_parquet_creates_parent_dirs(tmp_path):
    df = pd.DataFrame([{"arxiv_id": "1", "citation_key": "k", "authors": ["A", "B"]}])
    path = tmp_path / "x" / "y" / "bibtex.parquet"

    table = S.write_parquet(df, "bibtex", path)

    assert path.exists()
    assert table.column("authors").to_pylist() == [["A", "B"]]


def test_integrity_accepts_valid_and_empty_frames():
    assert S.validate_m2_integrity(*frames()) == []
    empty = [pd.DataFrame(columns=s.names) for s in (S.ELEMENTS_SCHEMA, S.HIERARCHY_SCHEMA, S.BIBTEX_SCHEMA)]
    assert S.validate_m2_integrity(*empty) == []


def test_integrity_detects_null_duplicate_and_dangling_keys():
    elements, hierarchy, bibtex = frames()
    nulled = elements.copy()
    nulled.loc[0, "element_id"] = None
    assert any("NULL" in e for e in S.validate_m2_integrity(nulled, hierarchy, bibtex))

    duplicated = pd.concat([elements, elements.iloc[[1]]], ignore_index=True)
    assert any("elements" in e and "trùng khóa" in e
               for e in S.validate_m2_integrity(duplicated, hierarchy, bibtex))
    assert any("bibtex" in e and "trùng khóa" in e
               for e in S.validate_m2_integrity(elements, hierarchy, pd.concat([bibtex, bibtex])))

    hierarchy.loc[1, ["element_id", "parent_id"]] = ["missing", "missing_parent"]
    errors = S.validate_m2_integrity(elements, hierarchy, bibtex)
    assert any("element_id không tồn tại" in e for e in errors)
    assert any("parent_id không tồn tại" in e for e in errors)


def test_validate_candidate_rows_rules():
    row = {field: "x" for field in S.CANDIDATE_FIELDS}
    row.update(candidate_id="c1", title_similarity=0.9, author_similarity=0.5)

    S.validate_candidate_rows([row])
    with pytest.raises(ValueError, match="unique"):
        S.validate_candidate_rows([row, row])
    with pytest.raises(ValueError, match="invalid title_similarity"):
        S.validate_candidate_rows([{**row, "candidate_id": "c2", "title_similarity": 1.5}])


def test_cli_exports_and_prints_row_counts(corpus, tmp_path, monkeypatch, capsys):
    out = tmp_path / "export"
    monkeypatch.setattr(sys, "argv", ["m2_export_parquet.py", "--corpus-dir", str(corpus),
                                      "--out-dir", str(out)])

    E.main()

    printed = capsys.readouterr().out
    assert "elements.parquet: 7 dòng" in printed
    assert "bibtex.parquet: 2 dòng" in printed
    assert (out / "hierarchy.parquet").exists()


def test_cli_default_out_dir_is_data_export(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(sys, "argv", ["m2_export_parquet.py", "--corpus-dir", str(tmp_path / "empty")])

    E.main()

    assert (tmp_path / "data" / "export" / "elements.parquet").exists()