from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import run_m3
from tests.test_m3_model import m3_corpus  # noqa: F401
from utils import config as C
from utils import schemas as S


def _seed_m1_m2_tables_for_m3_corpus(data_dir: Path) -> None:
    """Bổ sung 5 bảng M1/M2 (papers, references, elements, hierarchy, bibtex) khớp với m3_corpus."""
    paper_ids = ["2603.00580", "2603.00581", "2603.00582", "2603.00583"]
    papers_df = pd.DataFrame([
        {
            "arxiv_id": aid,
            "title": f"Paper {aid}",
            "authors": ["Khai"],
            "submission_date": "2026-03-01",
            "revised_dates": ["2026-03-01"],
            "venue": "ICML",
        }
        for aid in paper_ids
    ])
    S.write_parquet(papers_df, "papers", data_dir / "papers.parquet")

    refs_df = pd.DataFrame([
        {
            "arxiv_id": "2603.00580",
            "ref_arxiv_id": "2501.00001",
            "ref_title": "Ref 1",
            "ref_authors": ["Author"],
            "ref_submission_date": "2025-01-01",
            "ref_s2_id": "s2_1",
        }
    ])
    S.write_parquet(refs_df, "references", data_dir / "references.parquet")

    elements_df = pd.DataFrame([
        {"element_id": "2603-00580#h1", "arxiv_id": "2603.00580", "kind": "heading", "content": "Intro"},
        {"element_id": "2603-00580#s1", "arxiv_id": "2603.00580", "kind": "sentence", "content": "Cite \\cite{cite_a}."},
    ])
    S.write_parquet(elements_df, "elements", data_dir / "export" / "elements.parquet")

    hierarchy_df = pd.DataFrame([
        {"arxiv_id": "2603.00580", "version": 1, "element_id": "2603-00580#s1", "parent_id": "2603-00580#h1"},
    ])
    S.write_parquet(hierarchy_df, "hierarchy", data_dir / "export" / "hierarchy.parquet")

    bib_rows = []
    for aid, keys in [
        ("2603.00580", ["cite_a", "cite_b", "cite_null"]),
        ("2603.00581", ["cite_c", "cite_d"]),
        ("2603.00582", ["cite_v1", "cite_v2"]),
        ("2603.00583", ["cite_t1", "cite_missing_cand"]),
    ]:
        for ckey in keys:
            bib_rows.append({
                "arxiv_id": aid,
                "citation_key": ckey,
                "entry_type": "article",
                "title": f"Title {ckey}",
                "authors": ["Author"],
                "year": 2025,
                "doi": None,
                "journal": "ArXiv",
                "url": None,
            })
    S.write_parquet(pd.DataFrame(bib_rows), "bibtex", data_dir / "export" / "bibtex.parquet")


def test_parse_stages():
    assert run_m3.parse_stages("train") == ("train",)
    assert run_m3.parse_stages("train,predict,evaluate") == ("train", "predict", "evaluate")
    assert run_m3.parse_stages("all") == run_m3.ALL_STAGES
    with pytest.raises(ValueError):
        run_m3.parse_stages("unknown_stage")


def test_merge_str_and_ctx_features(tmp_path: Path):
    data_dir = tmp_path / "data"
    feat_dir = C.m3_dir(data_dir) / "features"
    feat_dir.mkdir(parents=True, exist_ok=True)

    str_df = pd.DataFrame([
        {"candidate_id": "c1", "arxiv_id": "2603.00580", "citation_key": "k1", "ref_arxiv_id": "r1", "str_title": 0.9},
        {"candidate_id": "c2", "arxiv_id": "2603.00580", "citation_key": "k1", "ref_arxiv_id": "r2", "str_title": 0.4},
    ])
    ctx_df = pd.DataFrame([
        {"candidate_id": "c1", "ctx_year_diff": 0.0},
        {"candidate_id": "c2", "ctx_year_diff": 2.0},
    ])
    pq.write_table(pa.Table.from_pandas(str_df, preserve_index=False), feat_dir / "2603-00580.str.parquet")
    pq.write_table(pa.Table.from_pandas(ctx_df, preserve_index=False), feat_dir / "2603-00580.ctx.parquet")

    n_merged = run_m3.merge_str_and_ctx_features(data_dir)
    assert n_merged == 1
    merged = pq.read_table(feat_dir / "2603-00580.parquet").to_pandas()
    assert "str_title" in merged.columns and "ctx_year_diff" in merged.columns
    assert len(merged) == 2


def test_run_m3_end_to_end_with_benchmark(m3_corpus: Path):
    _seed_m1_m2_tables_for_m3_corpus(m3_corpus)

    rc = run_m3.main([
        "--data-dir", str(m3_corpus),
        "--stage", "all",
        "--member", "3",
        "--verify-streaming",
        "--require-all-partitions",
    ])
    assert rc == 0

    bench_file = m3_corpus / "benchmark" / "m3_benchmark.json"
    assert bench_file.exists()
    bench = json.loads(bench_file.read_text(encoding="utf-8"))
    assert bench["milestone"] == "m3"
    assert set(bench["stages_executed"]) == set(run_m3.ALL_STAGES)
    assert "train" in bench["stages_s"] and "publish" in bench["stages_s"]

    # Kiểm tra predictions.parquet được bước predict sinh ra và bước publish đóng gói thành công
    assert C.predictions_path(m3_corpus).exists()
    assert (C.m3_dir(m3_corpus) / "hf_export" / "khai" / "README.md").exists()
