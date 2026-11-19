r"""run_m3.py

Milestone: M3
Người phụ trách: Khai (P4)
Mục đích:
  - Điều phối toàn bộ pipeline Milestone 3 theo từng bước (`--stage`):
      pick | label | features | train | predict | evaluate | publish | all
  - Hỗ trợ truyền một bước (`--stage train`) hoặc chuỗi bước (`--stage train,predict,evaluate,publish`).
  - Hỗ trợ `--member 1..4` (hoặc `Quoc|Nguyen|Khai|Tri`) cho bước `publish`.
  - Đo hiệu năng (thời gian từng stage, RAM đỉnh, dung lượng đĩa) bằng `utils.benchmark.PerfRecorder`
    và lưu tại `<data-dir>/benchmark/m3_benchmark.json`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matching import (  # noqa: E402
    m3_auto_labeler,
    m3_context_features,
    m3_evaluate,
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


def parse_stages(stage_spec: str) -> Tuple[str, ...]:
    """Chuyển chuỗi --stage ('train', 'train,predict,evaluate', 'all') thành tuple các stage hợp lệ."""
    raw_items = [s.strip().lower() for s in (stage_spec or "all").split(",") if s.strip()]
    if not raw_items or "all" in raw_items:
        return ALL_STAGES
    invalid = [s for s in raw_items if s not in ALL_STAGES]
    if invalid:
        raise ValueError(f"Stage không hợp lệ: {invalid}. Chọn trong {ALL_STAGES} hoặc 'all'.")
    return tuple(raw_items)


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


def _call_optional_module(module: Any, stage_name: str, strict: bool = False) -> str:
    """Gọi module của thành viên khác; nếu còn là skeleton NotImplementedError thì ghi nhận rõ."""
    try:
        module.main()
        return "completed"
    except NotImplementedError:
        if strict:
            raise
        print(f"[m3] stage '{stage_name}': {module.__name__} đang là skeleton (bỏ qua, dùng dữ liệu/fallback sẵn có).")
        return "skipped_skeleton"


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
    bench_out: Optional[Path] = None,
) -> Dict[str, Any]:
    """Chạy các bước M3 được chọn và ghi benchmark vào <data_dir>/benchmark/m3_benchmark.json."""
    data_dir = Path(data_dir).expanduser().resolve()
    C.DATA_DIR = data_dir
    C.LOGS_DIR = data_dir / "logs"

    stage_results: Dict[str, Any] = {}
    rec = PerfRecorder(milestone="m3", watch_dirs=[data_dir])

    with rec:
        for stage in stages:
            with rec.stage(stage):
                if stage == "pick":
                    m2_stats_file = data_dir / "m2_stats.json"
                    if m2_stats_file.exists():
                        m3_pick_papers.main(["--corpus-dir", str(data_dir)])
                        stage_results["pick"] = {"status": "completed"}
                    else:
                        print(f"[m3] stage 'pick': chưa có {m2_stats_file}, giữ nguyên m3/split.json hiện có.")
                        stage_results["pick"] = {"status": "skipped_no_m2_stats"}

                elif stage == "label":
                    status = _call_optional_module(m3_auto_labeler, "label", strict=strict_stages)
                    stage_results["label"] = {"status": status}

                elif stage == "features":
                    str_status = _call_optional_module(m3_string_features, "features:str", strict=strict_stages)
                    ctx_status = _call_optional_module(m3_context_features, "features:ctx", strict=strict_stages)
                    n_merged = merge_str_and_ctx_features(data_dir)
                    stage_results["features"] = {
                        "str_status": str_status,
                        "ctx_status": ctx_status,
                        "n_papers_merged": n_merged,
                    }

                elif stage == "train":
                    train_res = m3_model.run_training_pipeline(
                        data_dir=data_dir,
                        model_type=model_type,
                        manual_weight=manual_weight,
                        auto_weight=auto_weight,
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
                    pred_res = m3_predictions.run_predictions_pipeline(data_dir=data_dir)
                    print(f"[m3] stage 'predict': đã ghi {pred_res['n_rows']} dòng vào {pred_res['out_path']}")
                    stage_results["predict"] = {
                        "n_rows": pred_res["n_rows"],
                        "predictions_path": str(pred_res["out_path"]),
                    }

                elif stage == "evaluate":
                    eval_res = m3_evaluate.run_evaluation_pipeline(data_dir=data_dir)
                    print(f"[m3] stage 'evaluate': overall MRR@5={eval_res['overall']['mrr@5']:.4f}")
                    stage_results["evaluate"] = eval_res

                elif stage == "publish":
                    pkg = m3_publish_hf.build_hf_package(
                        data_dir=data_dir,
                        member=member,
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
            "member": m3_publish_hf.resolve_member(member),
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
             "(có thể truyền nhiều bước cách nhau dấu phẩy, vd: train,predict,evaluate,publish)",
    )
    parser.add_argument(
        "--member",
        default=None,
        help="Thành viên cho bước publish: 1..4 hoặc Quoc|Nguyen|Khai|Tri",
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
    parser.add_argument("--hf-repo", default=None, help="Repo Hugging Face đích khi chạy bước publish")
    parser.add_argument("--verify-streaming", action="store_true")
    parser.add_argument("--require-all-partitions", action="store_true")
    parser.add_argument("--strict-stages", action="store_true",
                        help="Báo lỗi nếu gặp stage của thành viên khác còn là skeleton")
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
        bench_out=args.bench_out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
