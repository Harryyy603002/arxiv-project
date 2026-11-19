r"""m3_model.py

Milestone: M3
Người phụ trách: Khai (P4)
Mục đích:
  - Đọc split (m3/split.json), nhãn (m3/labels/<paper_key>.json) và feature
    (m3/features/<paper_key>.parquet, tự động fallback về candidates.json khi chạy baseline).
  - Huấn luyện baseline (LogisticRegression) và mô hình chính (LightGBM / HistGradientBoosting),
    ưu tiên trọng số nhãn tay cao hơn nhãn tự động, xử lý mất cân bằng 1 : (n-1) và giá trị null.
  - Chọn mô hình / siêu tham số tốt nhất trên tập validation theo MRR@5.
  - Lưu model, báo cáo số liệu (m3/model_metrics.json) và xuất m3/scores.parquet
    cho mọi cặp ứng viên của các bài trong split.json.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from matching.m3_evaluate import K, mrr_at_k, reciprocal_rank  # noqa: E402
from utils import config as C  # noqa: E402
from utils.schemas import SCORES_SCHEMA  # noqa: E402

try:
    import lightgbm as lgb  # type: ignore

    HAS_LIGHTGBM = True
except ImportError:
    lgb = None
    HAS_LIGHTGBM = False


NON_FEATURE_COLS = {
    "candidate_id",
    "arxiv_id",
    "citation_key",
    "ref_arxiv_id",
    "ref_s2_id",
    "reference_index",
    "bib_title",
    "ref_title",
    "bib_title_normalized",
    "ref_title_normalized",
    "bib_authors_normalized",
    "ref_authors_normalized",
    "partition",
    "label_source",
    "groundtruth_arxiv_id",
    "label",
    "in_query_set",
    "use_for_train",
    "base_weight",
    "sample_weight",
    "score",
    "model_version",
}


# ---------------------------------------------------------------------------
# 1. Đọc split.json, labels/<paper_key>.json, features/<paper_key>.parquet
# ---------------------------------------------------------------------------

def parse_split_data(data: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Phân tích cấu trúc split.json (chuẩn §4.4 theo từng member hoặc dạng phẳng).

    Trả về:
      - partitions: {arxiv_id: 'train' | 'valid' | 'test'}
      - sources:    {arxiv_id: 'manual' | 'auto'}
    """
    partitions: Dict[str, str] = {}
    sources: Dict[str, str] = {}

    if "members" in data and isinstance(data["members"], dict):
        for _member, info in data["members"].items():
            if not isinstance(info, dict):
                continue
            manual_ids = [C.normalize_arxiv_id(x) or str(x) for x in info.get("manual", [])]
            auto_ids = [C.normalize_arxiv_id(x) or str(x) for x in info.get("auto", [])]
            for aid in manual_ids:
                sources[aid] = "manual"
                partitions.setdefault(aid, "train")
            for aid in auto_ids:
                sources.setdefault(aid, "auto")
                partitions.setdefault(aid, "train")

            for part_name in ("valid", "test"):
                part_spec = info.get(part_name, {})
                if isinstance(part_spec, dict):
                    for src_kind in ("manual", "auto"):
                        for raw_id in part_spec.get(src_kind, []) or []:
                            aid = C.normalize_arxiv_id(raw_id) or str(raw_id)
                            partitions[aid] = part_name
                            sources.setdefault(aid, src_kind)
                elif isinstance(part_spec, list):
                    for raw_id in part_spec:
                        aid = C.normalize_arxiv_id(raw_id) or str(raw_id)
                        partitions[aid] = part_name
                        sources.setdefault(aid, "auto")
        return partitions, sources

    # Fallback: dạng phẳng {"train": [...], "valid": [...], "test": [...]}
    for part_name in ("train", "valid", "test"):
        for raw_id in data.get(part_name, []) or []:
            aid = C.normalize_arxiv_id(raw_id) or str(raw_id)
            partitions[aid] = part_name
            sources.setdefault(aid, "manual")
    return partitions, sources


def load_split_info(split_file: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    data = json.loads(Path(split_file).read_text(encoding="utf-8"))
    return parse_split_data(data)


def load_paper_label_file(path: Path) -> Tuple[str, Dict[str, Optional[str]]]:
    """Đọc m3/labels/<paper_key>.json -> (source, {citation_key: normalized_ref_arxiv_id | None})."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    source = str(data.get("source") or "auto").strip().lower()
    raw_labels = data.get("labels") or {}
    labels: Dict[str, Optional[str]] = {}
    for cite_key, val in raw_labels.items():
        if val is None or str(val).strip() == "":
            labels[str(cite_key)] = None
        else:
            labels[str(cite_key)] = C.normalize_arxiv_id(str(val))
    return source, labels


def load_paper_features(arxiv_id: str, data_dir: Path) -> pd.DataFrame:
    """Đọc bảng ứng viên + feature của 1 bài.

    Ưu tiên:
      1. m3/features/<paper_key>.parquet (bản đã ghép str_* + ctx_*)
      2. m3/features/<paper_key>.str.parquet (bản feature chuỗi của Tri)
      3. <data_dir>/<paper_key>/candidates.json (fallback baseline dùng title/author similarity)
    Nếu đọc từ Parquet và có candidates.json, tự động bổ sung các cột cơ bản còn thiếu
    theo candidate_id để luôn giữ đúng 1 dòng / ứng viên.
    """
    p_key = C.paper_key(arxiv_id)
    feat_pq = C.features_path(arxiv_id, data_dir)
    str_pq = C.str_features_path(arxiv_id, data_dir)
    cand_json = Path(data_dir) / p_key / "candidates.json"

    df: Optional[pd.DataFrame] = None
    if feat_pq.exists():
        df = pq.read_table(feat_pq).to_pandas()
    elif str_pq.exists():
        df = pq.read_table(str_pq).to_pandas()

    cand_df: Optional[pd.DataFrame] = None
    if cand_json.exists():
        raw_cands = json.loads(cand_json.read_text(encoding="utf-8"))
        if isinstance(raw_cands, list) and raw_cands:
            cand_df = pd.DataFrame(raw_cands)
        else:
            cand_df = pd.DataFrame(columns=[
                "candidate_id", "arxiv_id", "citation_key", "ref_arxiv_id",
                "title_similarity", "author_similarity", "title_exact",
            ])

    if df is None:
        if cand_df is None:
            return pd.DataFrame()
        df = cand_df
    elif cand_df is not None and not cand_df.empty and "candidate_id" in df.columns:
        extra_cols = [
            c for c in ("arxiv_id", "citation_key", "ref_arxiv_id",
                        "title_similarity", "author_similarity", "title_exact")
            if c in cand_df.columns and c not in df.columns
        ]
        if extra_cols:
            df = df.merge(cand_df[["candidate_id", *extra_cols]], on="candidate_id", how="left")

    if df.empty:
        return df

    if "arxiv_id" not in df.columns:
        df["arxiv_id"] = C.normalize_arxiv_id(arxiv_id) or arxiv_id
    else:
        df["arxiv_id"] = df["arxiv_id"].map(lambda x: C.normalize_arxiv_id(str(x)) or str(x))

    if "ref_arxiv_id" in df.columns:
        df["ref_arxiv_id"] = df["ref_arxiv_id"].map(
            lambda x: C.normalize_arxiv_id(str(x)) if pd.notna(x) and str(x).strip() else ""
        )
    return df


def select_feature_columns(df: pd.DataFrame) -> List[str]:
    """Lấy danh sách cột feature số/bool (str_*, ctx_*, title_similarity, ...)."""
    feature_cols: List[str] = []
    for col in df.columns:
        if col in NON_FEATURE_COLS:
            continue
        dtype = df[col].dtype
        if (
            pd.api.types.is_numeric_dtype(dtype)
            or pd.api.types.is_bool_dtype(dtype)
            or col.startswith(("str_", "ctx_"))
        ):
            feature_cols.append(col)
    return sorted(feature_cols)


def prepare_dataset(
    data_dir: Path,
    split_file: Optional[Path] = None,
    include_null_negatives: bool = False,
    manual_weight: float = 3.0,
    auto_weight: float = 1.0,
) -> Tuple[pd.DataFrame, Dict[str, Dict[Tuple[str, str], str]]]:
    """Gom toàn bộ ứng viên + nhãn của các bài trong split.json.

    Trả về:
      - DataFrame chứa mọi ứng viên của các bài trong split, kèm cột:
        partition, label_source, groundtruth_arxiv_id, label, in_query_set,
        use_for_train, base_weight.
      - queries_by_partition: {partition: {(arxiv_id, citation_key): truth_ref_arxiv_id}}
        chứa MỌI entry có nhãn khác null (kể cả entry không có ứng viên nào trong candidates.json,
        để tính MRR@5 đúng chuẩn tập Q của đề).
    """
    data_dir = Path(data_dir)
    split_file = Path(split_file) if split_file is not None else C.split_path(data_dir)

    if split_file.exists():
        partitions, split_sources = load_split_info(split_file)
    else:
        # Nếu chưa có split.json, tự động quét các bài đã có trong m3/labels/
        partitions, split_sources = {}, {}
        labels_dir = C.m3_dir(data_dir) / "labels"
        if labels_dir.exists():
            for lf in sorted(labels_dir.glob("*.json")):
                raw = json.loads(lf.read_text(encoding="utf-8"))
                aid = C.normalize_arxiv_id(raw.get("arxiv_id") or lf.stem) or lf.stem
                partitions[aid] = "train"
                split_sources[aid] = str(raw.get("source") or "auto").lower()

    frames: List[pd.DataFrame] = []
    queries_by_partition: Dict[str, Dict[Tuple[str, str], str]] = {
        "train": {},
        "valid": {},
        "test": {},
    }

    for arxiv_id, partition in sorted(partitions.items()):
        label_file = C.labels_path(arxiv_id, data_dir)
        file_source = split_sources.get(arxiv_id, "auto")
        paper_labels: Dict[str, Optional[str]] = {}
        if label_file.exists():
            file_source, paper_labels = load_paper_label_file(label_file)

        for cite_key, truth_ref in paper_labels.items():
            if truth_ref is not None:
                queries_by_partition.setdefault(partition, {})[(arxiv_id, cite_key)] = truth_ref

        df = load_paper_features(arxiv_id, data_dir)
        if df.empty:
            continue

        df = df.copy()
        df["arxiv_id"] = arxiv_id
        df["partition"] = partition
        df["label_source"] = file_source

        has_key = df["citation_key"].isin(paper_labels.keys())
        df["groundtruth_arxiv_id"] = df["citation_key"].map(paper_labels)
        df["in_query_set"] = df["groundtruth_arxiv_id"].notna()
        if include_null_negatives:
            df["use_for_train"] = has_key
        else:
            df["use_for_train"] = df["in_query_set"]

        df["label"] = np.where(
            df["in_query_set"] & (df["ref_arxiv_id"] == df["groundtruth_arxiv_id"]),
            1,
            0,
        ).astype(np.int32)
        df["base_weight"] = np.where(df["label_source"] == "manual", manual_weight, auto_weight)
        frames.append(df)

    if not frames:
        return pd.DataFrame(), queries_by_partition

    full_df = pd.concat(frames, ignore_index=True)
    return full_df, queries_by_partition


# ---------------------------------------------------------------------------
# 2. Biến đổi ma trận X (xử lý null), trọng số mẫu & đánh giá MRR@5
# ---------------------------------------------------------------------------

def _to_numeric_matrix(df: pd.DataFrame, feature_cols: Sequence[str]) -> np.ndarray:
    """Chuyển các cột feature sang ma trận float64, giữ nguyên np.nan cho giá trị null."""
    cols = []
    for col in feature_cols:
        if col not in df.columns:
            cols.append(np.full(len(df), np.nan, dtype=np.float64))
        else:
            s = df[col]
            if pd.api.types.is_bool_dtype(s.dtype):
                cols.append(s.astype(np.float64).to_numpy())
            else:
                cols.append(pd.to_numeric(s, errors="coerce").to_numpy(dtype=np.float64))
    if not cols:
        return np.zeros((len(df), 0), dtype=np.float64)
    return np.column_stack(cols)


def compute_sample_weights(
    y: np.ndarray,
    base_weights: np.ndarray,
    balance_classes: bool = True,
) -> np.ndarray:
    """Kết hợp trọng số nguồn nhãn (manual > auto) với cân bằng lớp dương/âm (1 : n-1)."""
    weights = np.asarray(base_weights, dtype=np.float64).copy()
    if balance_classes:
        n_pos = int(np.sum(y == 1))
        n_neg = int(np.sum(y == 0))
        if n_pos > 0 and n_neg > 0:
            pos_multiplier = float(n_neg) / float(n_pos)
            weights = np.where(y == 1, weights * pos_multiplier, weights)
    return weights


def evaluate_scores_mrr(
    df: pd.DataFrame,
    scores: Sequence[float],
    queries: Optional[Dict[Tuple[str, str], str]] = None,
    k: int = K,
) -> Dict[str, Any]:
    """Tính MRR@5 trên tập Q (chỉ gồm các entry có groundtruth_arxiv_id != None).

    Nếu truyền `queries` ({(arxiv_id, citation_key): truth_ref_arxiv_id}), các entry có nhãn
    nhưng không có ứng viên nào trong `df` vẫn được tính điểm 0 trong mẫu số |Q| (đúng mục 1 & 4.6).
    """
    if df.empty and not queries:
        return {f"mrr@{k}": 0.0, "n_evaluated": 0, "hit_rate": 0.0, "candidate_recall": 0.0}

    work = df.copy()
    work["_score"] = np.asarray(scores, dtype=np.float64)

    if queries is None:
        queries = {}
        labeled = work[work["groundtruth_arxiv_id"].notna()]
        for row in labeled[["arxiv_id", "citation_key", "groundtruth_arxiv_id"]].drop_duplicates().itertuples(index=False):
            queries[(row.arxiv_id, row.citation_key)] = str(row.groundtruth_arxiv_id)

    if not queries:
        return {f"mrr@{k}": 0.0, "n_evaluated": 0, "hit_rate": 0.0, "candidate_recall": 0.0}

    grouped_preds: Dict[Tuple[str, str], List[str]] = {}
    grouped_pool: Dict[Tuple[str, str], set] = {}

    if not work.empty:
        work_sorted = work.sort_values(
            by=["arxiv_id", "citation_key", "_score", "ref_arxiv_id"],
            ascending=[True, True, False, True],
            kind="mergesort",
        )
        for (aid, ckey), grp in work_sorted.groupby(["arxiv_id", "citation_key"], sort=False):
            seen: List[str] = []
            pool: set = set()
            for ref_id in grp["ref_arxiv_id"]:
                if not ref_id:
                    continue
                pool.add(ref_id)
                if ref_id not in seen and len(seen) < k:
                    seen.append(ref_id)
            grouped_preds[(aid, ckey)] = seen
            grouped_pool[(aid, ckey)] = pool

    eval_rows = []
    in_pool_hits = 0
    for (aid, ckey), truth_ref in sorted(queries.items()):
        preds = grouped_preds.get((aid, ckey), [])
        eval_rows.append({
            "arxiv_id": aid,
            "citation_key": ckey,
            "groundtruth_arxiv_id": truth_ref,
            "predicted_arxiv_ids": preds,
        })
        if truth_ref in grouped_pool.get((aid, ckey), set()):
            in_pool_hits += 1

    res = mrr_at_k(eval_rows, k=k, skip_no_answer=True)
    res["candidate_recall"] = in_pool_hits / len(queries) if queries else 0.0
    return res


# ---------------------------------------------------------------------------
# 3. Các bộ xếp hạng: Baseline (LogisticRegression) & Main (GBDT / LightGBM)
# ---------------------------------------------------------------------------

def build_logreg_pipeline(c_val: float = 1.0, seed: int = 42) -> Pipeline:
    """Baseline Logistic Regression: điền null = 0.0, chuẩn hóa z-score, huấn luyện có trọng số."""
    return Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value=0.0, keep_empty_features=True)),
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(C=c_val, max_iter=1000, random_state=seed)),
    ])


def build_gbdt_estimator(
    model_kind: str = "gbdt",
    max_depth: int = 4,
    learning_rate: float = 0.05,
    n_estimators: int = 150,
    l2_regularization: float = 1.0,
    min_samples_leaf: int = 5,
    seed: int = 42,
) -> Any:
    """Mô hình chính: LightGBM (nếu có cài và chọn 'lgbm') hoặc HistGradientBoostingClassifier.

    Cả hai đều xử lý trực tiếp giá trị null (NaN) không cần điền 0/-1.
    """
    if model_kind == "lgbm" and HAS_LIGHTGBM:
        return lgb.LGBMClassifier(
            max_depth=max_depth,
            num_leaves=max(3, min(31, (2 ** max_depth) - 1)),
            learning_rate=learning_rate,
            n_estimators=n_estimators,
            reg_lambda=l2_regularization,
            min_child_samples=min_samples_leaf,
            random_state=seed,
            verbose=-1,
        )
    return HistGradientBoostingClassifier(
        max_depth=max_depth,
        learning_rate=learning_rate,
        max_iter=n_estimators,
        l2_regularization=l2_regularization,
        min_samples_leaf=min_samples_leaf,
        random_state=seed,
    )


def fit_estimator(estimator: Any, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray) -> Any:
    if isinstance(estimator, Pipeline):
        estimator.fit(X, y, clf__sample_weight=sample_weight)
    else:
        estimator.fit(X, y, sample_weight=sample_weight)
    return estimator


def predict_raw_scores(estimator: Any, X: np.ndarray) -> np.ndarray:
    if len(X) == 0:
        return np.zeros(0, dtype=np.float64)
    if hasattr(estimator, "predict_proba"):
        proba = estimator.predict_proba(X)
        if proba.ndim == 2 and proba.shape[1] >= 2:
            return proba[:, 1].astype(np.float64)
        return proba.ravel().astype(np.float64)
    if hasattr(estimator, "decision_function"):
        return np.asarray(estimator.decision_function(X), dtype=np.float64).ravel()
    return np.asarray(estimator.predict(X), dtype=np.float64).ravel()


def _candidate_configs(
    model_type: str,
    seed: int = 42,
    min_samples_leaf: int = 5,
) -> List[Dict[str, Any]]:
    """Danh sách cấu hình thử nghiệm để chọn siêu tham số trên tập validation."""
    configs: List[Dict[str, Any]] = []
    if model_type in ("logreg", "auto"):
        for c_val in (0.2, 1.0, 5.0):
            configs.append({
                "family": "logreg",
                "model_version": f"logreg_c{str(c_val).replace('.', 'p')}",
                "params": {"C": c_val},
                "builder": lambda c=c_val: build_logreg_pipeline(c_val=c, seed=seed),
            })

    gbdt_kind = "lgbm" if (model_type == "lgbm" or (model_type == "auto" and HAS_LIGHTGBM)) else "gbdt"
    if model_type in ("gbdt", "lgbm", "auto"):
        grid = [
            {"max_depth": 3, "learning_rate": 0.05, "n_estimators": 100, "l2_regularization": 1.0},
            {"max_depth": 4, "learning_rate": 0.05, "n_estimators": 150, "l2_regularization": 1.0},
            {"max_depth": 5, "learning_rate": 0.08, "n_estimators": 200, "l2_regularization": 2.0},
        ]
        for idx, params in enumerate(grid, start=1):
            configs.append({
                "family": gbdt_kind,
                "model_version": f"{gbdt_kind}_v{idx}",
                "params": {**params, "min_samples_leaf": min_samples_leaf},
                "builder": lambda p=params, k=gbdt_kind, msl=min_samples_leaf: build_gbdt_estimator(
                    model_kind=k,
                    max_depth=p["max_depth"],
                    learning_rate=p["learning_rate"],
                    n_estimators=p["n_estimators"],
                    l2_regularization=p["l2_regularization"],
                    min_samples_leaf=msl,
                    seed=seed,
                ),
            })
    return configs


def compute_feature_importances(
    estimator: Any,
    X: np.ndarray,
    y: np.ndarray,
    feature_cols: Sequence[str],
    seed: int = 42,
) -> Dict[str, float]:
    """Trích xuất độ quan trọng của từng feature (hệ số tuyến tính hoặc permutation importance)."""
    if not feature_cols or len(X) == 0:
        return {}
    if isinstance(estimator, Pipeline) and "clf" in estimator.named_steps:
        clf = estimator.named_steps["clf"]
        if hasattr(clf, "coef_"):
            coefs = np.asarray(clf.coef_).ravel()
            return {col: float(coefs[i]) for i, col in enumerate(feature_cols)}
    if hasattr(estimator, "feature_importances_"):
        imps = np.asarray(estimator.feature_importances_, dtype=np.float64).ravel()
        return {col: float(imps[i]) for i, col in enumerate(feature_cols)}
    if len(np.unique(y)) >= 2 and len(X) >= 6:
        perm = permutation_importance(estimator, X, y, n_repeats=3, random_state=seed)
        return {col: float(perm.importances_mean[i]) for i, col in enumerate(feature_cols)}
    return {col: 0.0 for col in feature_cols}


def train_and_select_model(
    df: pd.DataFrame,
    queries_by_partition: Optional[Dict[str, Dict[Tuple[str, str], str]]] = None,
    feature_cols: Optional[Sequence[str]] = None,
    model_type: str = "auto",
    seed: int = 42,
) -> Dict[str, Any]:
    """Huấn luyện trên tập train, chọn cấu hình có MRR@5 cao nhất trên tập valid.

    Trả về dict bundle gồm:
      - estimator: mô hình tốt nhất đã fit
      - feature_cols: danh sách tên cột feature
      - model_version: tên phiên bản model được chọn
      - best_params: siêu tham số được chọn
      - metrics: số liệu MRR@5 trên train / valid / test (cùng baseline_logreg để đối chiếu)
      - trials: danh sách kết quả mọi cấu hình đã thử
      - feature_importances: độ quan trọng của từng feature
    """
    if df.empty:
        raise ValueError("DataFrame rỗng: không có dữ liệu ứng viên để huấn luyện.")

    feature_cols = list(feature_cols) if feature_cols is not None else select_feature_columns(df)
    if not feature_cols:
        raise ValueError("Không tìm thấy cột feature số nào trong dữ liệu.")

    train_df = df[(df["partition"] == "train") & df["use_for_train"]].copy()
    if train_df.empty:
        # Fallback khi chạy trên tập nhỏ chưa tách riêng train
        train_df = df[df["use_for_train"]].copy()
    if train_df.empty:
        raise ValueError("Không có dòng nào có nhãn để huấn luyện.")

    valid_df = df[(df["partition"] == "valid") & df["in_query_set"]].copy()
    test_df = df[(df["partition"] == "test") & df["in_query_set"]].copy()

    X_train = _to_numeric_matrix(train_df, feature_cols)
    y_train = train_df["label"].to_numpy(dtype=np.int32)
    if len(np.unique(y_train)) < 2:
        raise ValueError("Tập huấn luyện cần có cả mẫu dương (label=1) và mẫu âm (label=0).")

    w_train = compute_sample_weights(
        y_train,
        train_df["base_weight"].to_numpy(dtype=np.float64),
        balance_classes=True,
    )

    train_queries = (queries_by_partition or {}).get("train")
    valid_queries = (queries_by_partition or {}).get("valid")
    test_queries = (queries_by_partition or {}).get("test")

    n_pos_train = int(np.sum(y_train == 1))
    adaptive_min_leaf = max(1, min(10, n_pos_train // 2))
    configs = _candidate_configs(model_type, seed=seed, min_samples_leaf=adaptive_min_leaf)
    if not configs:
        raise ValueError(f"model_type không hợp lệ: {model_type!r}")

    trials: List[Dict[str, Any]] = []
    best_bundle: Optional[Dict[str, Any]] = None
    best_key: Tuple[float, float, int] = (-1.0, -1.0, -1)

    for idx, cfg in enumerate(configs):
        est = cfg["builder"]()
        fit_estimator(est, X_train, y_train, w_train)

        train_scores = predict_raw_scores(est, X_train)
        train_eval = evaluate_scores_mrr(train_df, train_scores, queries=train_queries)

        if not valid_df.empty or valid_queries:
            X_valid = _to_numeric_matrix(valid_df, feature_cols)
            valid_scores = predict_raw_scores(est, X_valid)
            valid_eval = evaluate_scores_mrr(valid_df, valid_scores, queries=valid_queries)
            primary_mrr = float(valid_eval["mrr@5"])
        else:
            valid_eval = train_eval
            primary_mrr = float(train_eval["mrr@5"])

        trial_record = {
            "model_version": cfg["model_version"],
            "family": cfg["family"],
            "params": cfg["params"],
            "train_mrr@5": float(train_eval["mrr@5"]),
            "valid_mrr@5": float(valid_eval["mrr@5"]),
        }
        trials.append(trial_record)

        # Khi hòa điểm trên valid: ưu tiên train_mrr@5 cao hơn, rồi ưu tiên model chính (gbdt/lgbm)
        family_priority = 1 if cfg["family"] in ("gbdt", "lgbm") else 0
        cand_key = (primary_mrr, float(train_eval["mrr@5"]), family_priority)
        if cand_key > best_key:
            best_key = cand_key
            best_bundle = {
                "estimator": est,
                "config": cfg,
                "train_eval": train_eval,
                "valid_eval": valid_eval,
            }

    assert best_bundle is not None
    best_est = best_bundle["estimator"]
    best_cfg = best_bundle["config"]

    if not test_df.empty or test_queries:
        X_test = _to_numeric_matrix(test_df, feature_cols)
        test_scores = predict_raw_scores(best_est, X_test)
        test_eval = evaluate_scores_mrr(test_df, test_scores, queries=test_queries)
    else:
        test_eval = {f"mrr@{K}": 0.0, "n_evaluated": 0, "hit_rate": 0.0, "candidate_recall": 0.0}

    eval_target_df = valid_df if not valid_df.empty else train_df
    X_imp = _to_numeric_matrix(eval_target_df, feature_cols)
    y_imp = eval_target_df["label"].to_numpy(dtype=np.int32)
    importances = compute_feature_importances(best_est, X_imp, y_imp, feature_cols, seed=seed)

    # Ghi nhận thêm baseline_logreg để đối chiếu trên poster
    logreg_trials = [t for t in trials if t["family"] == "logreg"]
    best_logreg = max(logreg_trials, key=lambda t: (t["valid_mrr@5"], t["train_mrr@5"])) if logreg_trials else None

    return {
        "estimator": best_est,
        "feature_cols": feature_cols,
        "model_version": best_cfg["model_version"],
        "family": best_cfg["family"],
        "best_params": best_cfg["params"],
        "metrics": {
            "train": best_bundle["train_eval"],
            "valid": best_bundle["valid_eval"],
            "test": test_eval,
            "baseline_logreg": best_logreg,
        },
        "trials": trials,
        "feature_importances": importances,
    }


# ---------------------------------------------------------------------------
# 4. Chấm điểm, xuất m3/scores.parquet & lưu/nạp model
# ---------------------------------------------------------------------------

def score_candidates(df: pd.DataFrame, model_bundle: Dict[str, Any]) -> pd.DataFrame:
    """Chấm điểm mọi cặp ứng viên trong `df`, trả về DataFrame đúng 6 cột của scores.parquet."""
    cols = ["candidate_id", "arxiv_id", "citation_key", "ref_arxiv_id", "score", "model_version"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    X = _to_numeric_matrix(df, model_bundle["feature_cols"])
    scores = predict_raw_scores(model_bundle["estimator"], X)

    out = pd.DataFrame({
        "candidate_id": df["candidate_id"].astype(str),
        "arxiv_id": df["arxiv_id"].astype(str),
        "citation_key": df["citation_key"].astype(str),
        "ref_arxiv_id": df["ref_arxiv_id"].astype(str),
        "score": scores.astype(np.float64),
        "model_version": str(model_bundle["model_version"]),
    })
    return out[cols]


def write_scores_parquet(scores_df: pd.DataFrame, out_path: Path) -> Path:
    """Ghi nguyên tử m3/scores.parquet theo đúng SCORES_SCHEMA (§4.6)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    aligned = scores_df.reindex(columns=SCORES_SCHEMA.names)
    table = pa.Table.from_pandas(aligned, schema=SCORES_SCHEMA, preserve_index=False)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    pq.write_table(table, tmp_path)
    tmp_path.replace(out_path)
    return out_path


def save_model(model_bundle: Dict[str, Any], out_path: Path) -> Path:
    """Lưu model bundle ra đĩa (atomic write)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("wb") as f:
        pickle.dump(model_bundle, f)
    tmp_path.replace(out_path)
    return out_path


def load_model(model_path: Path) -> Dict[str, Any]:
    """Nạp lại model bundle đã lưu."""
    with Path(model_path).open("rb") as f:
        return pickle.load(f)


def run_training_pipeline(
    data_dir: Path,
    split_file: Optional[Path] = None,
    scores_out: Optional[Path] = None,
    model_out: Optional[Path] = None,
    metrics_out: Optional[Path] = None,
    model_type: str = "auto",
    manual_weight: float = 3.0,
    auto_weight: float = 1.0,
    include_null_negatives: bool = False,
    seed: int = 42,
) -> Dict[str, Any]:
    """Chạy trọn vẹn bước train + chấm điểm + ghi scores.parquet + model.pkl + model_metrics.json."""
    data_dir = Path(data_dir)
    m3_root = C.m3_dir(data_dir)
    scores_out = Path(scores_out) if scores_out is not None else C.scores_path(data_dir)
    model_out = Path(model_out) if model_out is not None else (m3_root / "model.pkl")
    metrics_out = Path(metrics_out) if metrics_out is not None else (m3_root / "model_metrics.json")

    df, queries_by_part = prepare_dataset(
        data_dir=data_dir,
        split_file=split_file,
        include_null_negatives=include_null_negatives,
        manual_weight=manual_weight,
        auto_weight=auto_weight,
    )
    bundle = train_and_select_model(
        df=df,
        queries_by_partition=queries_by_part,
        model_type=model_type,
        seed=seed,
    )
    scores_df = score_candidates(df, bundle)
    write_scores_parquet(scores_df, scores_out)
    save_model(bundle, model_out)

    metrics_payload = {
        "model_version": bundle["model_version"],
        "family": bundle["family"],
        "best_params": bundle["best_params"],
        "feature_cols": bundle["feature_cols"],
        "feature_importances": bundle["feature_importances"],
        "metrics": bundle["metrics"],
        "trials": bundle["trials"],
        "n_candidates_scored": int(len(scores_df)),
        "n_papers_scored": int(scores_df["arxiv_id"].nunique()) if not scores_df.empty else 0,
    }
    metrics_out.parent.mkdir(parents=True, exist_ok=True)
    tmp_metrics = metrics_out.with_suffix(".json.tmp")
    tmp_metrics.write_text(json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_metrics.replace(metrics_out)

    return {
        "bundle": bundle,
        "scores_df": scores_df,
        "scores_path": scores_out,
        "model_path": model_out,
        "metrics_path": metrics_out,
        "summary": metrics_payload,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Huấn luyện mô hình xếp hạng M3 và xuất m3/scores.parquet."
    )
    parser.add_argument("--data-dir", "--corpus-dir", dest="data_dir", type=Path, default=C.DATA_DIR,
                        help="Thư mục dữ liệu gốc (mặc định: ./data)")
    parser.add_argument("--split-file", type=Path, default=None,
                        help="Đường dẫn split.json (mặc định: <data-dir>/m3/split.json)")
    parser.add_argument("--scores-out", type=Path, default=None,
                        help="File scores.parquet đầu ra (mặc định: <data-dir>/m3/scores.parquet)")
    parser.add_argument("--model-out", type=Path, default=None,
                        help="File lưu model (mặc định: <data-dir>/m3/model.pkl)")
    parser.add_argument("--metrics-out", type=Path, default=None,
                        help="File lưu metrics JSON (mặc định: <data-dir>/m3/model_metrics.json)")
    parser.add_argument("--model-type", choices=("auto", "logreg", "gbdt", "lgbm"), default="auto",
                        help="Loại mô hình: auto (thử cả baseline logreg + gbdt/lgbm), logreg, gbdt, lgbm")
    parser.add_argument("--manual-weight", type=float, default=3.0,
                        help="Trọng số cho mẫu thuộc bài gán nhãn tay (mặc định: 3.0)")
    parser.add_argument("--auto-weight", type=float, default=1.0,
                        help="Trọng số cho mẫu thuộc bài gán nhãn tự động (mặc định: 1.0)")
    parser.add_argument("--include-null-negatives", action="store_true",
                        help="Đưa cả các entry được xác nhận null làm mẫu âm khi train")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (mặc định: 42)")
    args = parser.parse_args(argv)

    result = run_training_pipeline(
        data_dir=args.data_dir,
        split_file=args.split_file,
        scores_out=args.scores_out,
        model_out=args.model_out,
        metrics_out=args.metrics_out,
        model_type=args.model_type,
        manual_weight=args.manual_weight,
        auto_weight=args.auto_weight,
        include_null_negatives=args.include_null_negatives,
        seed=args.seed,
    )
    summary = result["summary"]
    print(f"[m3_model] Đã chọn model: {summary['model_version']} (features={len(summary['feature_cols'])})")
    print(f"[m3_model] MRR@5 -> train={summary['metrics']['train']['mrr@5']:.4f} | "
          f"valid={summary['metrics']['valid']['mrr@5']:.4f} | "
          f"test={summary['metrics']['test']['mrr@5']:.4f}")
    print(f"[m3_model] Đã ghi {summary['n_candidates_scored']} dòng điểm vào {result['scores_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
