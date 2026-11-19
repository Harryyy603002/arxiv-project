from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from matching import m3_publish_hf as P
from utils import config as C
from utils import schemas as S


@pytest.fixture
def publish_corpus(tmp_path: Path) -> Path:
    """Tạo corpus gồm 6 bảng Parquet chứa bài của cả Khai (range 3) và Nguyen (range 2)."""
    data_dir = tmp_path / "data"

    # 3 bài của Khai (train, valid, test) + 1 bài của Nguyen (2602.04912)
    papers_df = pd.DataFrame([
        {
            "arxiv_id": "2603.00580",
            "title": "Khai Train Paper",
            "authors": ["Khai A."],
            "submission_date": "2026-03-01",
            "revised_dates": ["2026-03-01"],
            "venue": "ICML",
        },
        {
            "arxiv_id": "2603.00581",
            "title": "Khai Valid Paper",
            "authors": ["Khai B."],
            "submission_date": "2026-03-02",
            "revised_dates": ["2026-03-02"],
            "venue": "NeurIPS",
        },
        {
            "arxiv_id": "2603.00582",
            "title": "Khai Test Paper",
            "authors": ["Khai C."],
            "submission_date": "2026-03-03",
            "revised_dates": ["2026-03-03"],
            "venue": "ICLR",
        },
        {
            "arxiv_id": "2602.04912",
            "title": "Nguyen Paper",
            "authors": ["Nguyen D."],
            "submission_date": "2026-02-10",
            "revised_dates": ["2026-02-10"],
            "venue": "ACL",
        },
    ])
    S.write_parquet(papers_df, "papers", data_dir / "papers.parquet")

    # ref_arxiv_id nằm ngoài range 3 (1706.03762) vẫn hợp lệ miễn arxiv_id thuộc range 3 (§2.3)
    refs_df = pd.DataFrame([
        {
            "arxiv_id": "2603.00580",
            "ref_arxiv_id": "1706.03762",
            "ref_title": "Attention Is All You Need",
            "ref_authors": ["Vaswani"],
            "ref_submission_date": "2017-06-12",
            "ref_s2_id": "s2_1",
        },
        {
            "arxiv_id": "2603.00581",
            "ref_arxiv_id": "1810.04805",
            "ref_title": "BERT",
            "ref_authors": ["Devlin"],
            "ref_submission_date": "2018-10-11",
            "ref_s2_id": "s2_2",
        },
        {
            "arxiv_id": "2603.00582",
            "ref_arxiv_id": "2005.14165",
            "ref_title": "GPT-3",
            "ref_authors": ["Brown"],
            "ref_submission_date": "2020-05-28",
            "ref_s2_id": "s2_3",
        },
        {
            "arxiv_id": "2602.04912",
            "ref_arxiv_id": "2305.14251",
            "ref_title": "FActScore",
            "ref_authors": ["Min"],
            "ref_submission_date": "2023-05-23",
            "ref_s2_id": "s2_4",
        },
    ])
    S.write_parquet(refs_df, "references", data_dir / "references.parquet")

    elements_df = pd.DataFrame([
        {"element_id": "2603-00580#h1", "arxiv_id": "2603.00580", "kind": "heading", "content": "Intro"},
        {"element_id": "2603-00580#s1", "arxiv_id": "2603.00580", "kind": "sentence", "content": "See \\cite{vas17}."},
        {"element_id": "2603-00581#h1", "arxiv_id": "2603.00581", "kind": "heading", "content": "Intro"},
        {"element_id": "2603-00582#h1", "arxiv_id": "2603.00582", "kind": "heading", "content": "Intro"},
        {"element_id": "2602-04912#h1", "arxiv_id": "2602.04912", "kind": "heading", "content": "Other"},
    ])
    S.write_parquet(elements_df, "elements", data_dir / "export" / "elements.parquet")

    hierarchy_df = pd.DataFrame([
        {"arxiv_id": "2603.00580", "version": 1, "element_id": "2603-00580#s1", "parent_id": "2603-00580#h1"},
        {"arxiv_id": "2602.04912", "version": 1, "element_id": "2602-04912#h1", "parent_id": "2602-04912#root1"},
    ])
    S.write_parquet(hierarchy_df, "hierarchy", data_dir / "export" / "hierarchy.parquet")

    bibtex_df = pd.DataFrame([
        {
            "arxiv_id": "2603.00580",
            "citation_key": "vas17",
            "entry_type": "inproceedings",
            "title": "Attention Is All You Need",
            "authors": ["Vaswani"],
            "year": 2017,
            "doi": None,
            "journal": "NeurIPS",
            "url": None,
        },
        {
            "arxiv_id": "2603.00581",
            "citation_key": "dev18",
            "entry_type": "article",
            "title": "BERT",
            "authors": ["Devlin"],
            "year": 2018,
            "doi": None,
            "journal": "NAACL",
            "url": None,
        },
        {
            "arxiv_id": "2603.00582",
            "citation_key": "bro20",
            "entry_type": "article",
            "title": "GPT-3",
            "authors": ["Brown"],
            "year": 2020,
            "doi": None,
            "journal": "NeurIPS",
            "url": None,
        },
        {
            "arxiv_id": "2602.04912",
            "citation_key": "min23",
            "entry_type": "article",
            "title": "FActScore",
            "authors": ["Min"],
            "year": 2023,
            "doi": None,
            "journal": "EMNLP",
            "url": None,
        },
    ])
    S.write_parquet(bibtex_df, "bibtex", data_dir / "export" / "bibtex.parquet")

    predictions_df = pd.DataFrame([
        {
            "arxiv_id": "2603.00580",
            "partition": "train",
            "citation_key": "vas17",
            "groundtruth_arxiv_id": "1706.03762",
            "cand_1": "1706.03762",
            "cand_2": "1810.04805",
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
        {
            "arxiv_id": "2603.00581",
            "partition": "valid",
            "citation_key": "dev18",
            "groundtruth_arxiv_id": "1810.04805",
            "cand_1": "1810.04805",
            "cand_2": None,
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
        {
            "arxiv_id": "2603.00582",
            "partition": "test",
            "citation_key": "bro20",
            "groundtruth_arxiv_id": "2005.14165",
            "cand_1": "2005.14165",
            "cand_2": None,
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
        {
            "arxiv_id": "2602.04912",
            "partition": "train",
            "citation_key": "min23",
            "groundtruth_arxiv_id": "2305.14251",
            "cand_1": "2305.14251",
            "cand_2": None,
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
    ])
    S.write_parquet(predictions_df, "predictions", C.predictions_path(data_dir))
    return data_dir


def test_resolve_member_by_index_and_name():
    assert P.resolve_member("1") == "Quoc"
    assert P.resolve_member("2") == "Nguyen"
    assert P.resolve_member("3") == "Khai"
    assert P.resolve_member("4") == "Tri"
    assert P.resolve_member("khai") == "Khai"
    assert P.resolve_member(None) is None
    assert P.resolve_member("all") is None
    with pytest.raises(ValueError):
        P.resolve_member("5")
    with pytest.raises(ValueError):
        P.resolve_member("Unknown")


def test_validate_predictions_rules():
    good = pd.DataFrame([
        {
            "arxiv_id": "2603.00580",
            "partition": "test",
            "citation_key": "k1",
            "groundtruth_arxiv_id": "1706.03762",
            "cand_1": "1706.03762",
            "cand_2": "1810.04805",
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        }
    ])
    assert S.validate_predictions(good, split_ids=["2603.00580"]) == []

    # Lỗi trùng cand_1 và cand_2 + lỗi arxiv_id ngoài split.json + lỗi partition sai
    bad = good.copy()
    bad["cand_2"] = "1706.03762"
    bad["partition"] = "holdout"
    errs = S.validate_predictions(bad, split_ids=["2603.99999"])
    assert any("partition không hợp lệ" in e for e in errs)
    assert any("trùng ứng viên" in e for e in errs)
    assert any("không nằm trong split.json" in e for e in errs)


def test_build_hf_package_filters_member_shards_and_streams(publish_corpus: Path, tmp_path: Path):
    out_dir = tmp_path / "hf_khai"
    pkg = P.build_hf_package(
        data_dir=publish_corpus,
        out_dir=out_dir,
        member="3",  # Khai (range 3: 2603.00580 - 2603.05579)
        max_shard_rows=2,  # ép chia 3 dòng thành 2 shard Parquet
        hf_repo="khai/arxiv-m3",
        require_all_partitions=True,
    )

    assert pkg["member"] == "Khai"
    assert pkg["readme_path"].exists()
    card = pkg["readme_path"].read_text(encoding="utf-8")
    assert "configs:" in card
    for name in P.TABLE_ORDER:
        assert f"config_name: {name}" in card
        assert f"path: data/{name}/*.parquet" in card

    # Bảng papers của Khai có đúng 3 bài (bài 2602.04912 của Nguyen đã bị lọc ra) và được chia thành 2 shard
    assert pkg["tables"]["papers"]["n_rows"] == 3
    assert pkg["tables"]["papers"]["n_shards"] == 2
    assert (out_dir / "data" / "papers" / "part-00000-of-00002.parquet").exists()
    assert (out_dir / "data" / "papers" / "part-00001-of-00002.parquet").exists()

    # Bảng references của Khai giữ nguyên 3 dòng (dù ref_arxiv_id ngoài range 3)
    assert pkg["tables"]["references"]["n_rows"] == 3
    assert pkg["tables"]["predictions"]["n_rows"] == 3

    # Kiểm tra đọc streaming trên cả 6 bảng đã shard
    stream_counts = P.verify_streaming(out_dir)
    assert stream_counts == {
        "papers": 3,
        "references": 3,
        "elements": 4,
        "hierarchy": 1,
        "bibtex": 3,
        "predictions": 3,
    }


def test_cli_end_to_end(publish_corpus: Path, tmp_path: Path):
    out_dir = tmp_path / "cli_hf_khai"
    rc = P.main([
        "--data-dir", str(publish_corpus),
        "--member", "3",
        "--out-dir", str(out_dir),
        "--max-shard-rows", "2",
        "--require-all-partitions",
        "--verify-streaming",
    ])
    assert rc == 0
    assert (out_dir / "README.md").exists()
