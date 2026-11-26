r"""run_m3.py

Milestone: M3
Người phụ trách: P4
Mục đích:
  - Điều phối toàn bộ pipeline Milestone 3 end-to-end theo từng bước (`--stage`):
      pick | label | features | train | predict | evaluate | publish | all
  - Kết nối đầy đủ toàn bộ các module M3 của cả 4 thành viên:
      * `pick`:     `matching.m3_pick_papers.build_split` (P1 - Quoc) -> tạo/cập nhật `m3/split.json`
                    theo từng ID range từ `m2_stats.json`.
      * `label`:    `matching.m3_auto_labeler` (P2 - Tri) + kiểm tra bằng `m3_manual_labeler.validate_label_data`
                    -> xuất `labels/auto_labels.csv` và `m3/labels/<paper_key>.json` (kèm `evidence`,
                    không ghi đè nhãn `source="manual"`).
      * `features`: `matching.m3_string_features` (P2 - Tri) -> xuất `m3/features/<paper_key>.str.parquet`
                    (9 cột `str_*`), sau đó gọi `matching.m3_context_features.run_stage` (P3 - Nguyen)
                    tính 18 cột `ctx_*`, kiểm tra hợp đồng `validate_features` và ghi
                    `m3/features/<paper_key>.parquet` (§4.5).
      * `train`:    `matching.m3_model.run_training_pipeline` (P4 - Khai) -> huấn luyện, chọn model
                    trên `valid` theo MRR@5, ghi `m3/scores.parquet`, `m3/model.pkl`, `m3/model_metrics.json`.
      * `predict`:  `matching.m3_predictions.run_predictions_pipeline` (P1/P4) -> kiểm tra nhãn,
                    ghi `m3/predictions.parquet` đúng 9 cột `PREDICTIONS_SCHEMA` (§4.7).
      * `evaluate`: `matching.m3_evaluate.run_evaluation_pipeline` (P1/P4) -> tính MRR@5 tổng,
                    theo partition, theo nguồn nhãn (manual/auto) và theo thành viên -> `m3/evaluation_report.json`.
      * `publish`:  `matching.m3_publish_hf.build_hf_package` (P4 - Khai) -> lọc 6 bảng theo `--member`,
                    chia shard Parquet, sinh Dataset Card `README.md`, kiểm tra streaming & upload HF.
  - Đo hiệu năng (thời gian từng stage, RAM đỉnh, dung lượng đĩa) bằng `utils.benchmark.PerfRecorder`
    và lưu tại `<data-dir>/benchmark/m3_benchmark.json`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matching import (  # noqa: E402
    m3_auto_labeler,
    m3_context_features,
    m3_evaluate,
    m3_manual_labeler,
    m3_model,
    m3_pick_papers,
    m3_predictions,
    m3_publish_hf,
    m3_string_features,
)
from utils import config as C  # noqa: E402
from utils.benchmark import PerfRecorder  # noqa: E402

ALL_STAGES: Tuple[str, ...] = (
    "pick",
    "label",
    "features",
    "train",
    "predict",
    "evaluate",
    "publish",
)

_RESERVED_DIRS = {"export", "m3", "labels", "benchmark", "logs", "predictions"}


def parse_stages(stage_spec: str) -> Tuple[str, ...]:
    """Chuyển chuỗi --stage ('train', 'train,predict,evaluate', 'all') thành tuple các stage hợp lệ."""
    raw_items = [s.strip().lower() for s in (stage_spec or "all").split(",") if s.strip()]
    if not raw_items or "all" in raw_items:
        return ALL_STAGES
    invalid = [s for s in raw_items if s not in ALL_STAGES]
    if invalid:
        raise ValueError(f"Stage không hợp lệ: {invalid}. Chọn trong {ALL_STAGES} hoặc 'all'.")
    return tuple(raw_items)


def _list_paper_dirs(data_dir: Path) -> List[Path]:
    """Liệt kê các thư mục bài báo trong `data_dir` có chứa `candidates.json` hoặc `paper.json`."""
    if not data_dir.exists():
        return []
    out: List[Path] = []
    for d in sorted(data_dir.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in _RESERVED_DIRS:
            continue
        if (d / "candidates.json").exists() or (d / "paper.json").exists():
            out.append(d)
    return out


def _paper_arxiv_id(pdir: Path) -> str:
    paper_json = pdir / "paper.json"
    if paper_json.exists():
        try:
            data = json.loads(paper_json.read_text(encoding="utf-8"))
            if data.get("arxiv_id"):
                return str(data["arxiv_id"])
        except (OSError, ValueError):
            pass
    cand_json = pdir / "candidates.json"
    if cand_json.exists():
        try:
            rows = json.loads(cand_json.read_text(encoding="utf-8"))
            if isinstance(rows, list) and rows and rows[0].get("arxiv_id"):
                return str(rows[0]["arxiv_id"])
        except (OSError, ValueError):
            pass
    return pdir.name.replace("-", ".")


# ---------------------------------------------------------------------------
# Stage 1: pick (tích hợp m3_pick_papers.build_split của Quoc - P1)
# ---------------------------------------------------------------------------

def run_pick_stage(
    data_dir: Path,
    member: Optional[str] = None,
    seed: int = 42,
    auto_buffer: float = 0.05,
    manual_min_refs: int = 22,
    rebuild_split: bool = False,
    allow_insufficient_auto: bool = False,
) -> Dict[str, Any]:
    """Chạy `m3_pick_papers.build_split` để tạo `m3/split.json` khi chưa có (hoặc khi `--rebuild-split`)."""
    split_file = C.split_path(data_dir)
    m2_stats_file = data_dir / "m2_stats.json"

    if split_file.exists() and not rebuild_split:
        existing = json.loads(split_file.read_text(encoding="utf-8"))
        members_in_split = sorted((existing.get("members") or {}).keys())
        print(f"[m3] stage 'pick': giữ nguyên {split_file} (members={members_in_split}).")
        return {
            "status": "kept_existing_split",
            "split_path": str(split_file),
            "members": members_in_split,
        }

    if not m2_stats_file.exists():
        print(f"[m3] stage 'pick': chưa có {m2_stats_file}, không thể tạo mới split.json.")
        return {"status": "skipped_no_m2_stats"}

    resolved_member = m3_publish_hf.resolve_member(member)
    target_members = [resolved_member] if resolved_member else list(C.MEMBERS)

    all_stats = m3_pick_papers.load_m2_stats(data_dir)
    combined_members: Dict[str, Any] = {}
    skipped_members: Dict[str, str] = {}

    for m_name in target_members:
        has_any = any(m3_pick_papers.in_range(str(r["arxiv_id"]), m_name) for r in all_stats)
        if not has_any and resolved_member is None:
            skipped_members[m_name] = "no_papers_in_range"
            continue
        split_one = m3_pick_papers.build_split(
            corpus_dir=data_dir,
            member=m_name,
            seed=seed,
            auto_buffer=auto_buffer,
            manual_min_refs=manual_min_refs,
            allow_insufficient_auto=allow_insufficient_auto,
        )
        combined_members[m_name] = split_one["members"][m_name]

    if not combined_members:
        print("[m3] stage 'pick': không tìm thấy bài nào thuộc range yêu cầu trong m2_stats.json.")
        return {"status": "empty", "skipped_members": skipped_members}

    if resolved_member is not None and split_file.exists():
        # --rebuild-split --member X chỉ thay phần của X, giữ nguyên các thành viên khác.
        try:
            previous = json.loads(split_file.read_text(encoding="utf-8")).get("members") or {}
        except (OSError, ValueError):
            previous = {}
        combined_members = {**previous, **combined_members}
    merged_split = {"seed": int(seed), "members": combined_members}
    m3_pick_papers._write_json_atomic(split_file, merged_split)
    print(f"[m3] stage 'pick': đã ghi {split_file} cho members={sorted(combined_members.keys())}.")
    return {
        "status": "completed",
        "split_path": str(split_file),
        "members": sorted(combined_members.keys()),
        "skipped_members": skipped_members,
    }


# ---------------------------------------------------------------------------
# Stage 2: label (tích hợp m3_auto_labeler của Tri + kiểm tra m3_manual_labeler của Quoc)
# ---------------------------------------------------------------------------

def run_label_stage(data_dir: Path) -> Dict[str, Any]:
    """Chạy bộ gán nhãn tự động (luật nằm trong `m3_auto_labeler.label_corpus`, dùng chung với CLI).

    - Ghi `<data_dir>/labels/auto_labels.csv` và `<data_dir>/m3/labels/<paper_key>.json` (source="auto").
    - Không bao giờ đụng vào nhãn source="manual"; nhãn auto cũ được ghi đè bằng kết quả mới.
    - Sau khi gán xong, kiểm tra các bài auto/valid/test trong split.json có nhãn dương hay chưa.
    """
    stats = m3_auto_labeler.label_corpus(
        data_dir,
        paper_dirs=_list_paper_dirs(data_dir),
        paper_id_of=_paper_arxiv_id,
    )
    coverage = check_split_label_coverage(data_dir)
    print(
        f"[m3] stage 'label': quét {stats['n_papers_scanned']} bài, bỏ qua {stats['n_manual_skipped']} bài nhãn tay; "
        f"gán nhãn tự động {stats['n_papers_labeled']} bài ({stats['n_matched_entries']} entry; "
        f"bằng chứng={stats['evidence_counts']}; mơ hồ bỏ qua={stats['n_ambiguous_entries']}); "
        f"ghi {stats['n_json_written']} file m3/labels/*.json, xóa {stats['n_json_removed']} file auto cũ."
    )
    for line in coverage["warnings"]:
        print(f"[m3] CẢNH BÁO split: {line}")
    return {"status": "completed", **stats, "split_label_coverage": coverage}


def check_split_label_coverage(data_dir: Path) -> Dict[str, Any]:
    """Báo các bài trong split.json (manual/auto/valid/test) chưa có nhãn dương nào.

    `pick` chạy trước `label` nên có thể chọn trúng bài auto không gán được nhãn; phải biết sớm
    để chọn lại (xem `--rebuild-split`), thay vì phát hiện ở bước evaluate.
    """
    split_file = C.split_path(data_dir)
    if not split_file.exists():
        return {"checked": 0, "missing": [], "warnings": []}
    try:
        members = (json.loads(split_file.read_text(encoding="utf-8")).get("members") or {})
    except (OSError, ValueError):
        return {"checked": 0, "missing": [], "warnings": ["không đọc được split.json"]}
    missing: List[str] = []
    checked = 0
    for m_name, info in members.items():
        ids = set(map(str, info.get("manual") or [])) | set(map(str, info.get("auto") or []))
        for sub in ("valid", "test"):
            for ids_sub in (info.get(sub) or {}).values():
                ids |= set(map(str, ids_sub if isinstance(ids_sub, list) else [ids_sub]))
        for aid in sorted(ids):
            checked += 1
            label_path = C.labels_path(aid, data_dir)
            positives = 0
            if label_path.exists():
                try:
                    labels = (json.loads(label_path.read_text(encoding="utf-8")).get("labels") or {})
                    positives = sum(v is not None for v in labels.values())
                except (OSError, ValueError):
                    positives = 0
            if positives == 0:
                missing.append(f"{m_name}:{aid}")
    warnings = []
    if missing:
        warnings.append(f"{len(missing)}/{checked} bài trong split chưa có nhãn dương: {missing[:10]}"
                        + (" ..." if len(missing) > 10 else ""))
    return {"checked": checked, "missing": missing, "warnings": warnings}


# ---------------------------------------------------------------------------
# Stage 3: features (tích hợp m3_string_features của Tri + m3_context_features của Nguyen)
# ---------------------------------------------------------------------------

def build_paper_str_features_parquet(pdir: Path, data_dir: Path) -> Optional[Path]:
    """Tính các cột `str_*` (m3_string_features.STR_FEATURE_NAMES) và ghi `.str.parquet`.

    Giá trị thiếu giữ nguyên là NaN (null trong Parquet), không điền 0.0.
    """
    cand_file = pdir / "candidates.json"
    if not cand_file.exists():
        return None
    try:
        candidates = json.loads(cand_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(candidates, list):
        return None

    bib_by_key, ref_index = m3_string_features.load_paper_context(pdir)
    names = m3_string_features.STR_FEATURE_NAMES

    rows: List[dict] = []
    for cand in candidates:
        enriched = m3_string_features.enrich_candidate(
            cand,
            bib_by_key.get(str(cand.get("citation_key")), {}),
            m3_string_features.find_ref(cand, ref_index),
        )
        feats = m3_string_features.compute_string_features(enriched)
        rows.append({
            "candidate_id": cand.get("candidate_id"),
            "arxiv_id": cand.get("arxiv_id"),
            "citation_key": cand.get("citation_key"),
            "ref_arxiv_id": cand.get("ref_arxiv_id"),
            **{f"str_{k}": float(feats[k]) for k in names},
        })

    key_cols = {
        "candidate_id": pd.Series(dtype="string"),
        "arxiv_id": pd.Series(dtype="string"),
        "citation_key": pd.Series(dtype="string"),
        "ref_arxiv_id": pd.Series(dtype="string"),
    }
    if rows:
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame({**key_cols, **{f"str_{k}": pd.Series(dtype="float32") for k in names}})
    for col in df.columns:
        if col.startswith("str_"):
            df[col] = df[col].astype("float32")

    out_path = C.str_features_path(_paper_arxiv_id(pdir), data_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp_path, index=False)
    tmp_path.replace(out_path)
    return out_path


def merge_str_and_ctx_features(data_dir: Path) -> int:
    """Ghép m3/features/<paper_key>.str.parquet (Tri) và .ctx.parquet (Nguyen) -> <paper_key>.parquet (§4.5)."""
    feat_dir = C.m3_dir(data_dir) / "features"
    if not feat_dir.exists():
        return 0
    merged_count = 0
    for str_file in sorted(feat_dir.glob("*.str.parquet")):
        paper_stem = str_file.name[: -len(".str.parquet")]
        ctx_file = feat_dir / f"{paper_stem}.ctx.parquet"
        out_file = feat_dir / f"{paper_stem}.parquet"
        if out_file.exists() and not ctx_file.exists() and out_file.stat().st_mtime >= str_file.stat().st_mtime:
            continue
        str_df = pq.read_table(str_file).to_pandas()
        if ctx_file.exists():
            ctx_df = pq.read_table(ctx_file).to_pandas()
            extra_cols = [c for c in ctx_df.columns if c not in str_df.columns or c == "candidate_id"]
            merged_df = str_df.merge(ctx_df[extra_cols], on="candidate_id", how="left")
        else:
            merged_df = str_df
        tmp_file = out_file.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pandas(merged_df, preserve_index=False), tmp_file)
        tmp_file.replace(out_file)
        merged_count += 1
    return merged_count


def _str_features_stale(pdir: Path, str_pq: Path) -> bool:
    """True khi .str.parquet chưa có hoặc cũ hơn dữ liệu đầu vào / mã tính feature."""
    if not str_pq.exists():
        return True
    mtime = str_pq.stat().st_mtime
    inputs = [pdir / "candidates.json", pdir / "bibtex_merged.json", pdir / "refs.json",
              Path(m3_string_features.__file__)]
    return any(p.exists() and p.stat().st_mtime > mtime for p in inputs)


def run_features_stage(data_dir: Path, workers: int = 1, strict: bool = False,
                       rebuild: bool = False) -> Dict[str, Any]:
    """Chạy bước features:
      1. Tính `str_*` của Tri (`m3_string_features`) -> `<paper_key>.str.parquet`.
      2. Với các bài có đủ `candidates.json` + `refs.json`, gọi `m3_context_features.run_stage`
         của Nguyen để tính 18 cột `ctx_*`, ghép 9 cột `str_*` + 18 cột `ctx_*`, kiểm tra
         `validate_features` và ghi `<paper_key>.parquet`.
      3. Với các fixture tối giản không có `refs.json`, dùng `merge_str_and_ctx_features`.
    """
    C.DATA_DIR = data_dir
    paper_dirs = _list_paper_dirs(data_dir)
    n_str_built = 0
    ctx_eligible_ids: List[str] = []

    for pdir in paper_dirs:
        arxiv_id = _paper_arxiv_id(pdir)
        str_pq = C.str_features_path(arxiv_id, data_dir)
        if (pdir / "candidates.json").exists() and (rebuild or _str_features_stale(pdir, str_pq)):
            if build_paper_str_features_parquet(pdir, data_dir) is not None:
                n_str_built += 1
        if (pdir / "candidates.json").exists() and (pdir / "refs.json").exists():
            ctx_eligible_ids.append(arxiv_id)

    ctx_summary: Dict[str, Any] = {}
    if ctx_eligible_ids:
        ctx_summary = m3_context_features.run_stage(
            ids=ctx_eligible_ids,
            workers=workers,
            require_str=True,
            allow_errors=not strict,
        )

    n_fallback_merged = merge_str_and_ctx_features(data_dir)
    print(
        f"[m3] stage 'features': đã tạo {n_str_built} file .str.parquet | "
        f"ghép ctx_* + str_* cho {ctx_summary.get('ok', 0)}/{len(ctx_eligible_ids)} bài "
        f"(fallback merge={n_fallback_merged})."
    )
    return {
        "n_str_built": n_str_built,
        "n_ctx_eligible": len(ctx_eligible_ids),
        "ctx_summary": ctx_summary,
        "n_papers_merged": ctx_summary.get("ok", 0) + n_fallback_merged,
    }


# ---------------------------------------------------------------------------
# Điều phối toàn bộ pipeline M3
# ---------------------------------------------------------------------------

def run_pipeline(
    data_dir: Path,
    stages: Sequence[str] = ALL_STAGES,
    member: Optional[str] = None,
    model_type: str = "auto",
    manual_weight: float = 3.0,
    auto_weight: float = 1.0,
    max_shard_mb: float = 100.0,
    max_shard_rows: Optional[int] = None,
    hf_repo: Optional[str] = None,
    verify_streaming: bool = False,
    require_all_partitions: bool = False,
    strict_stages: bool = False,
    rebuild_split: bool = False,
    allow_insufficient_auto: bool = False,
    manual_min_refs: int = 22,
    seed: int = 42,
    workers: int = 1,
    rebuild_features: bool = False,
    bench_out: Optional[Path] = None,
) -> Dict[str, Any]:
    """Chạy các bước M3 được chọn và ghi benchmark vào <data_dir>/benchmark/m3_benchmark.json."""
    data_dir = Path(data_dir).expanduser().resolve()
    C.DATA_DIR = data_dir
    C.LOGS_DIR = data_dir / "logs"

    resolved_member = m3_publish_hf.resolve_member(member)
    eval_member_scope = resolved_member or "all"

    stage_results: Dict[str, Any] = {}
    rec = PerfRecorder(milestone="m3", watch_dirs=[data_dir])

    with rec:
        for stage in stages:
            with rec.stage(stage):
                if stage == "pick":
                    stage_results["pick"] = run_pick_stage(
                        data_dir=data_dir,
                        member=resolved_member,
                        seed=seed,
                        manual_min_refs=manual_min_refs,
                        rebuild_split=rebuild_split,
                        allow_insufficient_auto=allow_insufficient_auto,
                    )

                elif stage == "label":
                    stage_results["label"] = run_label_stage(data_dir)

                elif stage == "features":
                    stage_results["features"] = run_features_stage(
                        data_dir=data_dir,
                        workers=workers,
                        strict=strict_stages,
                        rebuild=rebuild_features,
                    )

                elif stage == "train":
                    train_res = m3_model.run_training_pipeline(
                        data_dir=data_dir,
                        model_type=model_type,
                        manual_weight=manual_weight,
                        auto_weight=auto_weight,
                        seed=seed,
                    )
                    summary = train_res["summary"]
                    print(
                        f"[m3] stage 'train': model={summary['model_version']} | "
                        f"valid MRR@5={summary['metrics']['valid']['mrr@5']:.4f} | "
                        f"test MRR@5={summary['metrics']['test']['mrr@5']:.4f}"
                    )
                    stage_results["train"] = {
                        "model_version": summary["model_version"],
                        "n_candidates_scored": summary["n_candidates_scored"],
                        "metrics": summary["metrics"],
                        "scores_path": str(train_res["scores_path"]),
                    }

                elif stage == "predict":
                    pred_res = m3_predictions.run_predictions_pipeline(
                        data_dir=data_dir,
                        member=eval_member_scope,
                        allow_missing_label_files=not strict_stages,
                    )
                    print(f"[m3] stage 'predict': đã ghi {pred_res['n_rows']} dòng vào {pred_res['out_path']}")
                    stage_results["predict"] = {
                        "n_rows": pred_res["n_rows"],
                        "predictions_path": str(pred_res["out_path"]),
                        "report": pred_res.get("report"),
                    }

                elif stage == "evaluate":
                    eval_res = m3_evaluate.run_evaluation_pipeline(
                        data_dir=data_dir,
                        member=eval_member_scope,
                        partition="all",
                    )
                    by_part = eval_res.get("by_partition") or {}
                    overall_mrr = (eval_res.get("overall") or {}).get("mrr@5", 0.0)
                    parts = " | ".join(
                        f"{name} MRR@5={(by_part.get(name) or {}).get('mrr@5', float('nan')):.4f} "
                        f"(Q={(by_part.get(name) or {}).get('q', 0)})"
                        for name in ("test", "valid", "train")
                    )
                    print(
                        f"[m3] stage 'evaluate': {parts} | "
                        f"tất cả partition gộp (gồm train, KHÔNG dùng làm số báo cáo)={overall_mrr:.4f} "
                        f"(source={eval_res.get('evaluation_source', 'unknown')})"
                    )
                    stage_results["evaluate"] = eval_res

                elif stage == "publish":
                    pkg = m3_publish_hf.build_hf_package(
                        data_dir=data_dir,
                        member=resolved_member,
                        max_shard_mb=max_shard_mb,
                        max_shard_rows=max_shard_rows,
                        hf_repo=hf_repo,
                        require_all_partitions=require_all_partitions,
                    )
                    pub_info: Dict[str, Any] = {
                        "out_dir": str(pkg["out_dir"]),
                        "member": pkg["member"],
                        "tables": {k: v["n_rows"] for k, v in pkg["tables"].items()},
                    }
                    if verify_streaming:
                        pub_info["streaming_counts"] = m3_publish_hf.verify_streaming(pkg["out_dir"])
                    if hf_repo:
                        m3_publish_hf.upload_to_hf(pkg["out_dir"], hf_repo)
                        pub_info["uploaded_to"] = hf_repo
                    print(f"[m3] stage 'publish': đã đóng gói vào {pkg['out_dir']} (member={pkg['member'] or 'all'})")
                    stage_results["publish"] = pub_info

    bench_path = Path(bench_out) if bench_out is not None else (data_dir / "benchmark" / "m3_benchmark.json")
    bench_payload = rec.save(
        bench_path,
        extra={
            "stages_executed": list(stages),
            "member": resolved_member,
            "stage_results": stage_results,
        },
    )
    print(f"[m3] Hoàn tất ({', '.join(stages)}) trong {bench_payload['total_wall_s']:.2f}s. Benchmark: {bench_path}")
    return {
        "benchmark_path": bench_path,
        "benchmark": bench_payload,
        "stage_results": stage_results,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Milestone 3: Chạy pipeline Reference Matching theo --stage và đo hiệu năng."
    )
    parser.add_argument(
        "--data-dir",
        "--corpus-dir",
        dest="data_dir",
        type=Path,
        default=C.DATA_DIR,
        help="Thư mục dữ liệu gốc (mặc định: ./data)",
    )
    parser.add_argument(
        "--stage",
        default="all",
        help="Bước cần chạy: pick | label | features | train | predict | evaluate | publish | all "
             "(có thể truyền nhiều bước cách nhau dấu phẩy, vd: label,features,train,predict,evaluate,publish)",
    )
    parser.add_argument(
        "--member",
        default=None,
        help="Thành viên cần xử lý/publish: 1..4 hoặc Quoc|Nguyen|Khai|Tri",
    )
    parser.add_argument(
        "--model-type",
        choices=("auto", "logreg", "gbdt", "lgbm"),
        default="auto",
        help="Loại mô hình cho bước train (mặc định: auto)",
    )
    parser.add_argument("--manual-weight", type=float, default=3.0)
    parser.add_argument("--auto-weight", type=float, default=1.0)
    parser.add_argument("--max-shard-mb", type=float, default=100.0)
    parser.add_argument("--max-shard-rows", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1,
                        help="Số tiến trình song song cho bước features (mặc định: 1)")
    parser.add_argument("--rebuild-features", action="store_true",
                        help="Tính lại .str.parquet cho mọi bài (mặc định chỉ tính lại khi file cũ hơn dữ liệu/mã)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--manual-min-refs", type=int, default=22,
                        help="Số reference có arXiv ID tối thiểu khi sơ tuyển bài tay ở bước pick (mặc định: 22)")
    parser.add_argument("--rebuild-split", action="store_true",
                        help="Tạo lại m3/split.json ở bước pick ngay cả khi file đã tồn tại")
    parser.add_argument("--allow-insufficient-auto", action="store_true",
                        help="Cho phép bước pick tạo split thử nghiệm khi số bài auto chưa đạt quota 10%%")
    parser.add_argument("--hf-repo", default=None, help="Repo Hugging Face đích khi chạy bước publish")
    parser.add_argument("--verify-streaming", action="store_true")
    parser.add_argument("--require-all-partitions", action="store_true")
    parser.add_argument("--strict-stages", action="store_true",
                        help="Báo lỗi dừng ngay nếu có bài lỗi ở bước features hoặc thiếu file nhãn ở bước predict")
    parser.add_argument("--bench-out", type=Path, default=None,
                        help="Đường dẫn file benchmark JSON (mặc định: <data-dir>/benchmark/m3_benchmark.json)")
    args = parser.parse_args(argv)

    try:
        stages = parse_stages(args.stage)
    except ValueError as exc:
        print("[LỖI]", exc)
        return 1

    run_pipeline(
        data_dir=args.data_dir,
        stages=stages,
        member=args.member,
        model_type=args.model_type,
        manual_weight=args.manual_weight,
        auto_weight=args.auto_weight,
        max_shard_mb=args.max_shard_mb,
        max_shard_rows=args.max_shard_rows,
        hf_repo=args.hf_repo,
        verify_streaming=args.verify_streaming,
        require_all_partitions=args.require_all_partitions,
        strict_stages=args.strict_stages,
        rebuild_split=args.rebuild_split,
        allow_insufficient_auto=args.allow_insufficient_auto,
        manual_min_refs=args.manual_min_refs,
        seed=args.seed,
        workers=args.workers,
        rebuild_features=args.rebuild_features,
        bench_out=args.bench_out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())