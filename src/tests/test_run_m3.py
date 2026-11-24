from __future__ import annotations

import json
import shutil
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
    """Bổ sung 5 bảng M1/M2 và bibtex_merged.json/refs.json hợp lệ cho 4 bài trong m3_corpus."""
    paper_ids = ["2603.00580", "2603.00581", "2603.00582", "2603.00583"]
    papers_df = pd.DataFrame([
        {
            "arxiv_id": aid,
            "title": f"Paper {aid}",
            "authors": ["Author A."],
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

    bib_keys_by_paper = {
        "2603.00580": ["cite_a", "cite_b", "cite_null"],
        "2603.00581": ["cite_c", "cite_d"],
        "2603.00582": ["cite_v1", "cite_v2"],
        "2603.00583": ["cite_t1", "cite_missing_cand"],
    }
    bib_rows = []
    for aid, keys in bib_keys_by_paper.items():
        pdir = data_dir / C.paper_key(aid)
        pdir.mkdir(parents=True, exist_ok=True)

        # Bổ sung evidence cho file nhãn auto để đáp ứng validate_label_data (P1)
        lpath = C.labels_path(aid, data_dir)
        label_refs: set[str] = set()
        if lpath.exists():
            ldata = json.loads(lpath.read_text(encoding="utf-8"))
            for v in (ldata.get("labels") or {}).values():
                if v:
                    label_refs.add(str(v))
            if ldata.get("source") == "auto" and "evidence" not in ldata:
                ldata["evidence"] = {
                    k: "title_exact" for k, v in (ldata.get("labels") or {}).items() if v is not None
                }
                lpath.write_text(json.dumps(ldata, ensure_ascii=False, indent=2), encoding="utf-8")

        cands = json.loads((pdir / "candidates.json").read_text(encoding="utf-8"))
        for c in cands:
            if c.get("title_exact"):
                c["bib_title"] = "Attention Is All You Need"
                c["ref_title"] = "Attention Is All You Need"
                c["bib_title_normalized"] = "attention is all you need"
                c["ref_title_normalized"] = "attention is all you need"
            else:
                c["bib_title"] = "Attention Is All You Need"
                c["ref_title"] = "Unrelated Reference Title"
                c["bib_title_normalized"] = "attention is all you need"
                c["ref_title_normalized"] = "unrelated reference title"
        (pdir / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")

        refs = [
            {
                "ref_arxiv_id": c["ref_arxiv_id"],
                "ref_s2_id": c["ref_s2_id"],
                "ref_title": c["ref_title"],
                "ref_authors": ["Vaswani, Ashish"],
                "ref_submission_date": "2025-01-01",
                "ref_venue": "arXiv",
            }
            for c in cands
        ]
        seen_rids = {r["ref_arxiv_id"] for r in refs}
        for extra_rid in sorted(label_refs - seen_rids):
            refs.append({
                "ref_arxiv_id": extra_rid,
                "ref_s2_id": f"s2_{extra_rid}",
                "ref_title": f"Extra Ref {extra_rid}",
                "ref_authors": ["Author"],
                "ref_submission_date": "2025-01-01",
                "ref_venue": "arXiv",
            })
        (pdir / "refs.json").write_text(
            json.dumps({"arxiv_id": aid, "references": refs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        per_paper_bibs = []
        for ckey in keys:
            row = {
                "arxiv_id": aid,
                "citation_key": ckey,
                "entry_type": "article",
                "title": f"Title {ckey}",
                "authors": ["Author"],
                "year": 2025,
                "doi": None,
                "journal": "arXiv",
                "url": None,
                "source_versions": ["v1"],
            }
            bib_rows.append(row)
            per_paper_bibs.append(row)
        (pdir / "bibtex_merged.json").write_text(
            json.dumps(per_paper_bibs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (pdir / "elements.json").write_text("[]", encoding="utf-8")
        (pdir / "hierarchy.json").write_text("[]", encoding="utf-8")

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


def test_pick_stage_builds_split_from_m2_stats(tmp_path: Path):
    """Kiểm tra stage 'pick' gọi trực tiếp m3_pick_papers.build_split để tạo m3/split.json."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stats = []
    # Tạo 7 bài trong range 3 (Khai): 5 bài đủ điều kiện manual (refs >= 22) + 2 bài auto
    for i in range(7):
        aid = f"2603.{580 + i:05d}"
        pdir = data_dir / C.paper_key(aid)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "paper.json").write_text(json.dumps({"arxiv_id": aid}), encoding="utf-8")
        (pdir / "bibtex_merged.json").write_text(
            json.dumps([{"citation_key": "k1", "title": "Paper T"}]), encoding="utf-8"
        )
        (pdir / "refs.json").write_text(
            json.dumps({"arxiv_id": aid, "references": [{"ref_arxiv_id": "2501.00001"}]}),
            encoding="utf-8",
        )
        stats.append({
            "arxiv_id": aid,
            "canonical_entries": 25 if i < 5 else 10,
            "references_with_arxiv_id": 25 if i < 5 else 5,
            "entries_with_candidates": 20 if i < 5 else 5,
            "candidate_pairs": 40 if i < 5 else 10,
        })
    (data_dir / "m2_stats.json").write_text(json.dumps(stats), encoding="utf-8")

    rc = run_m3.main([
        "--data-dir", str(data_dir),
        "--stage", "pick",
        "--member", "3",
        "--allow-insufficient-auto",
    ])
    assert rc == 0

    split_file = C.split_path(data_dir)
    assert split_file.exists()
    split_data = json.loads(split_file.read_text(encoding="utf-8"))
    assert "Khai" in split_data["members"]
    assert len(split_data["members"]["Khai"]["manual"]) == 5
    assert len(split_data["members"]["Khai"]["auto"]) == 2


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

    assert C.predictions_path(m3_corpus).exists()
    assert (C.m3_dir(m3_corpus) / "evaluation_report.json").exists()
    assert (C.m3_dir(m3_corpus) / "hf_export" / "khai" / "README.md").exists()


def test_full_p1_p2_p3_p4_integration(m3_corpus: Path):
    """Kiểm tra tích hợp thật P1 (m3_manual_labeler/predictions/evaluate) + P2 (auto_labeler/string_features) + P3 (context_features) + P4 (model/publish)."""
    _seed_m1_m2_tables_for_m3_corpus(m3_corpus)

    # Xóa sẵn features/ và file nhãn auto 2603.00581 để stage label + stage features tự sinh mới & vượt qua validate_label_data
    shutil.rmtree(C.m3_dir(m3_corpus) / "features", ignore_errors=True)
    C.labels_path("2603.00581", m3_corpus).unlink(missing_ok=True)

    rc = run_m3.main([
        "--data-dir", str(m3_corpus),
        "--stage", "all",
        "--member", "3",
        "--verify-streaming",
        "--strict-stages",
    ])
    assert rc == 0

    # Stage label sinh lại 2603-00581.json kèm object evidence hợp lệ theo m3_manual_labeler.validate_label_data
    auto_label_file = C.labels_path("2603.00581", m3_corpus)
    assert auto_label_file.exists()
    auto_label_data = json.loads(auto_label_file.read_text(encoding="utf-8"))
    assert auto_label_data["source"] == "auto"
    assert isinstance(auto_label_data.get("evidence"), dict)
    assert (m3_corpus / "labels" / "auto_labels.csv").exists()

    # Stage features sinh đủ 4 cột khóa + 9 cột str_* + 18 cột ctx_* = 31 cột
    feat_df = pq.read_table(C.features_path("2603.00580", m3_corpus)).to_pandas()
    str_cols = [c for c in feat_df.columns if c.startswith("str_")]
    ctx_cols = [c for c in feat_df.columns if c.startswith("ctx_")]
    assert len(str_cols) == 9
    assert len(ctx_cols) == 18

    # Stage evaluate chạy trực tiếp trên scores.parquet + labels (luồng P1)
    eval_report = json.loads((C.m3_dir(m3_corpus) / "evaluation_report.json").read_text(encoding="utf-8"))
    assert eval_report["evaluation_source"] == "scores.parquet + labels"
    assert eval_report["overall"]["q"] == 8
    assert 0.0 <= eval_report["overall"]["mrr@5"] <= 1.0
