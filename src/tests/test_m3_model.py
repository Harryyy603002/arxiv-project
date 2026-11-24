from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from matching import m3_model as M
from utils import config as C


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def m3_corpus(tmp_path: Path) -> Path:
    """Tạo corpus giả có split.json (§4.4), labels/*.json (§4.3), candidates.json & features/*.parquet (§4.5)."""
    data_dir = tmp_path / "data"

    # 3 bài trong range của Khai: 1 train (manual), 1 train (auto), 1 valid (manual), 1 test (auto)
    p_train_m = "2603.00580"
    p_train_a = "2603.00581"
    p_valid_m = "2603.00582"
    p_test_a = "2603.00583"

    split_payload = {
        "seed": 42,
        "members": {
            "Khai": {
                "range": "2603.00580-2603.05579",
                "manual": [p_train_m, p_valid_m],
                "auto": [p_train_a, p_test_a],
                "valid": {"manual": [p_valid_m], "auto": []},
                "test": {"manual": [], "auto": [p_test_a]},
            }
        },
    }
    _write_json(C.split_path(data_dir), split_payload)

    papers_spec = [
        (p_train_m, "manual", {"cite_a": "2501.00001", "cite_b": "2501.00002", "cite_null": None}),
        (p_train_a, "auto", {"cite_c": "2501.00003", "cite_d": "2501.00004"}),
        (p_valid_m, "manual", {"cite_v1": "2501.00005", "cite_v2": "2501.00006"}),
        (p_test_a, "auto", {"cite_t1": "2501.00007", "cite_missing_cand": "2501.99999"}),
    ]

    for arxiv_id, src, labels in papers_spec:
        _write_json(
            C.labels_path(arxiv_id, data_dir),
            {"arxiv_id": arxiv_id, "source": src, "labeler": "Khai", "labels": labels},
        )
        cands = []
        feat_rows = []
        for c_idx, (ckey, truth_ref) in enumerate(labels.items()):
            if ckey == "cite_missing_cand":
                continue  # cố tình không sinh candidate để kiểm tra entry không có ứng viên vẫn vào Q
            # Sinh 1 ứng viên đúng (nếu truth_ref != None) và 3 ứng viên nhiễu
            refs = [truth_ref] if truth_ref else []
            refs += [f"2401.100{c_idx}{j}" for j in range(3)]
            for r_idx, ref_id in enumerate(refs):
                is_pos = (truth_ref is not None) and (ref_id == truth_ref)
                cid = f"{C.paper_key(arxiv_id)}_{ckey}_{r_idx}"
                t_sim = 0.96 if is_pos else 0.40 + 0.05 * r_idx
                a_sim = 0.90 if is_pos else 0.20 + 0.05 * r_idx
                cands.append({
                    "candidate_id": cid,
                    "arxiv_id": arxiv_id,
                    "citation_key": ckey,
                    "ref_s2_id": f"s2_{cid}",
                    "ref_arxiv_id": ref_id,
                    "reference_index": r_idx,
                    "title_similarity": t_sim,
                    "author_similarity": a_sim,
                    "title_exact": bool(is_pos),
                })
                feat_rows.append({
                    "candidate_id": cid,
                    "arxiv_id": arxiv_id,
                    "citation_key": ckey,
                    "ref_arxiv_id": ref_id,
                    "str_title_sim": t_sim,
                    "str_author_sim": a_sim,
                    # Cố tình để null ở một số dòng để kiểm tra xử lý null (§4.5)
                    "ctx_year_diff": 0.0 if is_pos else (np.nan if r_idx == 1 else 3.0),
                    "ctx_rank_in_entry": 1.0 if is_pos else float(r_idx + 1),
                })
        _write_json(data_dir / C.paper_key(arxiv_id) / "candidates.json", cands)
        feat_path = C.features_path(arxiv_id, data_dir)
        feat_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pandas(pd.DataFrame(feat_rows), preserve_index=False),
            feat_path,
        )

    return data_dir


def test_parse_split_data_member_format():
    split = {
        "seed": 42,
        "members": {
            "Khai": {
                "range": "2603.00580-2603.05579",
                "manual": ["2603.00580", "2603.00581", "2603.00582"],
                "auto": ["2603.00600", "2603.00601"],
                "valid": {"manual": ["2603.00581"], "auto": ["2603.00600"]},
                "test": {"manual": ["2603.00582"], "auto": []},
            }
        },
    }
    partitions, sources = M.parse_split_data(split)
    assert partitions["2603.00580"] == "train"
    assert partitions["2603.00601"] == "train"
    assert partitions["2603.00581"] == "valid"
    assert partitions["2603.00600"] == "valid"
    assert partitions["2603.00582"] == "test"
    assert sources["2603.00580"] == "manual"
    assert sources["2603.00601"] == "auto"


def test_config_member_ranges_and_helpers():
    assert C.in_range("2512.24000", "Quoc")
    assert C.in_range("2601.01000", "Quoc")
    assert not C.in_range("2601.04000", "Quoc")
    assert C.member_of("2603.01000") == "Khai"
    assert C.member_of("2602.04912") == "Nguyen"
    assert C.member_of("2606.22000") == "Tri"
    assert C.member_of("1706.03762") is None


def test_prepare_dataset_weights_and_null_labels(m3_corpus: Path):
    df, queries = M.prepare_dataset(
        m3_corpus,
        include_null_negatives=False,
        manual_weight=3.0,
        auto_weight=1.0,
    )
    assert not df.empty
    # Entry cite_null có nhãn null: không nằm trong Q, không dùng để train khi include_null_negatives=False,
    # nhưng các ứng viên của nó vẫn nằm trong df để chấm điểm xuất scores.parquet
    null_rows = df[df["citation_key"] == "cite_null"]
    assert len(null_rows) == 3
    assert not null_rows["in_query_set"].any()
    assert not null_rows["use_for_train"].any()

    # Trọng số bài manual (3.0) > bài auto (1.0)
    manual_w = df[df["label_source"] == "manual"]["base_weight"].unique()
    auto_w = df[df["label_source"] == "auto"]["base_weight"].unique()
    assert list(manual_w) == [3.0]
    assert list(auto_w) == [1.0]

    # Tập Q của test có cả entry không có ứng viên (cite_missing_cand)
    assert ("2603.00583", "cite_missing_cand") in queries["test"]


def test_train_score_and_cli_end_to_end(m3_corpus: Path):
    result = M.run_training_pipeline(
        data_dir=m3_corpus,
        model_type="auto",
        manual_weight=3.0,
        auto_weight=1.0,
    )
    scores_path = result["scores_path"]
    model_path = result["model_path"]
    metrics_path = result["metrics_path"]

    assert scores_path.exists()
    assert model_path.exists()
    assert metrics_path.exists()

    # Kiểm tra schema của scores.parquet khớp đúng §4.6
    table = pq.read_table(scores_path)
    assert table.schema.equals(M.SCORES_SCHEMA)
    scores_df = table.to_pandas()

    # Mọi ứng viên trong split (kể cả cite_null) đều được chấm điểm và không có điểm NaN
    assert len(scores_df) == 31
    assert not scores_df["score"].isna().any()

    # Trên tập valid, ứng viên đúng được xếp hạng 1 -> MRR@5 = 1.0
    summary = result["summary"]
    assert summary["metrics"]["valid"]["mrr@5"] == pytest.approx(1.0)
    # Trên tập test: có 2 query trong Q (1 query đứng top-1 đạt 1.0, 1 query không có candidate đạt 0.0) -> MRR@5 = 0.5
    assert summary["metrics"]["test"]["n_evaluated"] == 2
    assert summary["metrics"]["test"]["mrr@5"] == pytest.approx(0.5)
    assert summary["metrics"]["test"]["candidate_recall"] == pytest.approx(0.5)

    # Kiểm tra nạp lại model và chạy CLI
    loaded = M.load_model(model_path)
    rescore = M.score_candidates(
        M.prepare_dataset(m3_corpus)[0],
        loaded,
    )
    assert np.allclose(rescore["score"].to_numpy(), scores_df["score"].to_numpy())
    assert M.main(["--data-dir", str(m3_corpus), "--model-type", "logreg"]) == 0


def test_fallback_to_candidates_json_when_no_features_parquet(m3_corpus: Path):
    import shutil

    shutil.rmtree(C.m3_dir(m3_corpus) / "features")
    result = M.run_training_pipeline(data_dir=m3_corpus, model_type="logreg")
    summary = result["summary"]
    assert set(summary["feature_cols"]) == {"author_similarity", "title_exact", "title_similarity"}
    assert summary["metrics"]["valid"]["mrr@5"] == pytest.approx(1.0)
    assert summary["metrics"]["test"]["mrr@5"] == pytest.approx(0.5)

