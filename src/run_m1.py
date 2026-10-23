"""Chạy pipeline M1 và đo hiệu năng: discovery -> tải source -> reference.

Chạy từ thư mục gốc repo:
    python src/run_m1.py --range 2602.04870-2602.09869
    python src/run_m1.py --range 2602.04870-2602.09869 --limit 20     # chạy thử
    python src/run_m1.py --ids 2310.12345 hep-th/9901001

Kết quả đo: data/benchmark_m1.json
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawler import m1_metadata
from utils import config as C
from utils.benchmark import PerfRecorder

log = logging.getLogger("run_m1")

def download_sources(ids, perf: PerfRecorder) -> None:
    """TODO : tải source từng version, chỉ giữ .tex/.bib, rồi gọi với MỖI version:"""


def crawl_references(ids, perf: PerfRecorder) -> None:
    """TODO (Tri): crawl reference qua Semantic Scholar, mỗi bài bọc trong `with perf.paper(aid):`."""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", help="START-END, vd. 2602.04870-2602.09869")
    ap.add_argument("--month-last", nargs="*", default=[])
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch-size", type=int, default=C.ARXIV_API_BATCH)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default=None, help="mặc định: data/benchmark_m1.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.ids:
        ids = args.ids
    elif args.range:
        ids = list(C.iter_id_range(*C.parse_id_range(args.range), C.parse_month_last(args.month_last)))
    else:
        ap.error("cần --range hoặc --ids")
    if args.limit:
        ids = ids[:args.limit]

    C.ensure_dir(C.DATA_DIR)
    out = args.out or str(C.DATA_DIR / "benchmark_m1.json")

    with PerfRecorder("m1", watch_dirs=[C.DATA_DIR]) as perf:
        with perf.stage("discovery"):
            m1_metadata.crawl(ids, args.batch_size, args.force)
            m1_metadata.build_papers_table()
        per_paper = perf.stage_times["discovery"] / max(len(ids), 1)
        for aid in ids:
            perf.add_paper_time(aid, per_paper)
        with perf.stage("download"):
            download_sources(ids, perf)
        with perf.stage("references"):
            crawl_references(ids, perf)

        for aid in ids:
            perf.record_result(aid, C.meta_path(aid).exists())

    summary = perf.save(out)
    log.info("Benchmark -> %s | wall=%.1fs, peak RAM=%.0fMB, success=%s",
             out, summary["total_wall_s"], summary["peak_ram_mb"], summary.get("success_rate"))


if __name__ == "__main__":
    main()