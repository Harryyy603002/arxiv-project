"""m1_aggregate.py

Milestone: M1 - Crawling
Mục đích: gộp log benchmark của NHIỀU phiên Colab thành một bản tổng hợp.
          Thầy cho phép chia crawl thành nhiều phiên, với điều kiện giữ log thời gian
          từng phiên để đối chiếu; file này tạo bảng từng phiên và số liệu tổng.

Đầu vào:  <DATA_DIR>/benchmark/session_001.json, session_002.json, ...
          (mỗi phiên do run_m1.py ghi; phiên bị ngắt vẫn có log gần nhất, status="running")
Đầu ra:   <DATA_DIR>/benchmark/benchmark_m1_total.json   (số liệu tổng hợp)
          <DATA_DIR>/benchmark/sessions.csv              (một dòng mỗi phiên, dùng cho báo cáo)

Quy tắc gộp:
  - Chi phí cộng dồn:  total_wall_s, stages_s, thời gian từng bài (cộng qua các phiên).
  - Chi phí lấy cực đại: peak_ram_mb, peak_disk_mb. avg_ram_mb: trung bình có trọng số theo số mẫu.
  - Kết quả (tỉ lệ thành công, kích thước, số reference...): lấy từ phiên HOÀN TẤT cuối cùng,
    vì phiên đó duyệt lại toàn bộ danh sách ID (phần đã xong được đọc lại từ checkpoint).
  - total_wall_s chỉ cộng thời gian chạy của các phiên, không tính khoảng nghỉ giữa các phiên.

Chạy:
    python src/crawler/m1_aggregate.py --data-dir ./data
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402
from utils.benchmark import dir_size  # noqa: E402

MB = 1024 * 1024
TOTAL_NAME = "benchmark_m1_total.json"
SESSIONS_CSV = "sessions.csv"

# Các khóa kết quả lấy từ phiên hoàn tất cuối cùng.
RESULT_KEYS = (
    "n_ok", "n_failed", "success_rate",
    "ref_n_ok", "ref_n_failed", "ref_success_rate",
    "avg_refs_per_paper", "avg_arxiv_refs_per_paper",
    "n_papers_recorded", "n_tex_papers",
    "avg_paper_size_before_bytes", "avg_paper_size_after_bytes",
    "n_tex_versions", "n_pdf_only_versions", "n_papers_with_pdf_only_version",
    "avg_version_size_before_bytes", "avg_version_size_after_bytes",
    "coverage", "tables",
)


def load_sessions(bench_dir: Path) -> List[dict]:
    sessions = []
    for path in sorted(bench_dir.glob("session_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"[WARN] Bỏ qua log hỏng: {path.name}")
            continue
        data.setdefault("session_index", int(path.stem.split("_")[1]))
        sessions.append(data)
    return sorted(sessions, key=lambda s: s["session_index"])


def _session_row(s: dict) -> dict:
    cov = s.get("coverage") or {}
    env = s.get("environment") or {}
    stages = s.get("stages_s") or {}
    return {
        "session": s["session_index"],
        "status": s.get("status", "unknown"),
        "started_at_utc": s.get("started_at_utc"),
        "last_update_utc": s.get("ended_at_utc") or s.get("updated_at_utc"),
        "wall_s": s.get("total_wall_s", 0.0),
        "discovery_s": stages.get("discovery", 0.0),
        "crawl_s": stages.get("crawl", 0.0),
        "new_versions": s.get("new_versions_this_session"),
        "failed_versions": cov.get("failed_versions"),
        "peak_ram_mb": s.get("peak_ram_mb"),
        "avg_ram_mb": s.get("avg_ram_mb"),
        "peak_disk_mb": s.get("peak_disk_mb"),
        "is_colab": env.get("is_colab"),
        "cpu_count": env.get("cpu_count"),
        "ram_total_gb": env.get("ram_total_gb"),
    }


def aggregate(data_dir: Optional[Path] = None) -> Dict:
    data_dir = Path(data_dir or C.DATA_DIR)
    bench_dir = data_dir / "benchmark"
    sessions = load_sessions(bench_dir)
    if not sessions:
        print(f"[WARN] Không có session_*.json trong {bench_dir}")
        return {}

    rows = [_session_row(s) for s in sessions]

    # Thời gian từng bài: cộng qua các phiên (bài xong ở phiên 1 chỉ cộng thêm ~0 ở các phiên sau).
    paper_times: Dict[str, float] = {}
    for s in sessions:
        for aid, sec in (s.get("paper_times_s") or {}).items():
            paper_times[aid] = paper_times.get(aid, 0.0) + sec

    stage_totals: Dict[str, float] = {}
    for s in sessions:
        for name, sec in (s.get("stages_s") or {}).items():
            stage_totals[name] = stage_totals.get(name, 0.0) + sec

    samples = sum(int(s.get("ram_samples") or 0) for s in sessions)
    avg_ram = (sum(float(s.get("avg_ram_mb") or 0) * int(s.get("ram_samples") or 0) for s in sessions)
               / samples) if samples else None

    completed = [s for s in sessions if s.get("status") == "completed"]
    reference = completed[-1] if completed else sessions[-1]
    total_wall = sum(float(s.get("total_wall_s") or 0) for s in sessions)

    final_bytes = dir_size(data_dir) if data_dir.exists() else reference.get("final_output_bytes", 0)
    out: Dict = {
        "milestone": "m1",
        "n_sessions": len(sessions),
        "n_completed_sessions": len(completed),
        "n_interrupted_sessions": len(sessions) - len(completed),
        "last_session_completed": sessions[-1].get("status") == "completed",
        "total_wall_s": round(total_wall, 2),
        "total_wall_h": round(total_wall / 3600, 2),
        "stages_s": {k: round(v, 2) for k, v in stage_totals.items()},
        "discovery_s": round(stage_totals.get("discovery", 0.0), 2),
        "n_papers_timed": len(paper_times),
        "avg_paper_s": round(statistics.fmean(paper_times.values()), 3) if paper_times else None,
        "max_paper_s": round(max(paper_times.values()), 3) if paper_times else None,
        "peak_ram_mb": max((float(s.get("peak_ram_mb") or 0) for s in sessions), default=None),
        "avg_ram_mb": round(avg_ram, 1) if avg_ram is not None else None,
        "peak_disk_mb": max((float(s.get("peak_disk_mb") or 0) for s in sessions), default=None),
        "final_output_bytes": final_bytes,
        "final_output_mb": round(final_bytes / MB, 1),
        "environments": sorted({json.dumps(s.get("environment"), sort_keys=True)
                                for s in sessions if s.get("environment")}),
        "results_taken_from_session": reference["session_index"],
        "sessions": rows,
    }
    for key in RESULT_KEYS:
        if key in reference:
            out[key] = reference[key]

    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / TOTAL_NAME).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    with (bench_dir / SESSIONS_CSV).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Gộp log benchmark các phiên (M1).")
    ap.add_argument("--data-dir", type=Path, default=C.DATA_DIR)
    args = ap.parse_args()
    result = aggregate(args.data_dir.expanduser().resolve())
    print(json.dumps({k: v for k, v in result.items() if k not in ("sessions", "environments")},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
