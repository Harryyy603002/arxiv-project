"""Đo hiệu năng pipeline arXiv (M1 crawling, M2 parsing, M3 matching).

Các chỉ số theo đề:
  Thời gian : tổng thời gian xử lý từng bài, trung bình mỗi bài, tổng thời gian
              từng giai đoạn, tổng thời gian end-to-end.
  Bộ nhớ    : RAM tối đa, RAM trung bình, dung lượng đĩa tối đa,
              dung lượng đầu ra cuối cùng.
  M1        : dung lượng bài báo trước và sau khi loại bỏ hình ảnh; tỉ lệ thành công.
  M2        : số element / bibtex trước và sau deduplication; tỉ lệ parse thành công.
  M3        : đề không yêu cầu benchmark riêng; dùng stage() để đo từng bước
              (cleaning, labelling, features, training, evaluation) nếu cần cho poster.

Định nghĩa kích thước M1:
  before_bytes  = tổng kích thước KHÔNG NÉN của mọi file trong archive source (gồm hình).
  after_bytes   = tổng kích thước các file .tex và .bib được giữ lại.
  archive_bytes = kích thước file .tar.gz tạm tải về.
  Thống kê theo bài = cộng dồn các version TeX của bài đó.

RAM được tính theo cả cây tiến trình (tiến trình chính + mọi tiến trình con),
đúng yêu cầu "tổng bộ nhớ của toàn hệ thống" khi có nhiều chương trình.

Cách 1: nhúng vào code (mỗi milestone một file kết quả riêng)
  M1:
    from utils.benchmark import PerfRecorder

    with PerfRecorder("m1", watch_dirs=["data"]) as perf:
        with perf.stage("discovery"):
            versions = resolve_ids(ids)
        for aid in ids:
            with perf.paper(aid):
                ok = True
                for v in versions[aid]:
                    archive_b, raw_b, clean_b, is_pdf = download_and_extract(aid, v)
                    perf.record_paper_size(aid, before_bytes=raw_b, after_bytes=clean_b,
                                           archive_bytes=archive_b, is_pdf_only=is_pdf)
                perf.record_result(aid, ok)
    perf.save()                      # -> data/benchmark_m1.json

  M2:
    with PerfRecorder("m2", watch_dirs=["data"]) as perf:
        for aid in ids:
            with perf.paper(aid):
                n_el_before, n_el_after, n_bib_before, n_bib_after = parse_one(aid)
                perf.record_dedup(aid, "elements", n_el_before, n_el_after)
                perf.record_dedup(aid, "bibtex", n_bib_before, n_bib_after)
                perf.record_result(aid, True)
    perf.save()                      # -> data/benchmark_m2.json

  M3 (tùy chọn):
    with PerfRecorder("m3") as perf:
        with perf.stage("features"): ...
        with perf.stage("training"): ...
    perf.save()                      # -> data/benchmark_m3.json

Cách 2: đo end-to-end một lệnh bất kỳ
    python src/utils/benchmark.py --milestone m1 --watch data -- python src/run_m1.py
    python src/utils/benchmark.py --milestone m2 --watch data -- python src/run_m2.py
  (Cách 2 chỉ có wall time, RAM, đĩa; không có thời gian từng bài hay thống kê khác.)

Lưu ý: khi chạy trên Colab, --watch nên trỏ vào thư mục local (/content/...),
không trỏ vào Google Drive mount vì quét rất chậm.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import psutil

MB = 1024 * 1024


def dir_size(path: Path | str) -> int:
    """Tổng dung lượng (byte) của mọi file trong thư mục, bỏ qua file lỗi/biến mất."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _watched_size(dirs: Iterable[Path]) -> int:
    return sum(dir_size(d) for d in dirs if Path(d).exists())


class ResourceMonitor:
    """Hai luồng nền: một lấy mẫu RAM (cây tiến trình), một quét dung lượng đĩa.

    Tách luồng để việc quét đĩa chậm không làm trễ các mẫu RAM.
    """

    def __init__(
        self,
        watch_dirs: Iterable = (),
        interval: float = 0.5,
        disk_interval: float = 5.0,
        pid: Optional[int] = None,
    ):
        self.watch_dirs = [Path(d) for d in watch_dirs]
        self.interval = interval
        self.disk_interval = disk_interval
        self._proc = psutil.Process(pid or os.getpid())
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self.rss_samples: List[int] = []
        self.disk_baseline = 0
        self.disk_peak = 0
        self.disk_final = 0
        self._tracked_added_bytes = 0

    def _tree_rss(self) -> int:
        total = 0
        try:
            if not self._proc.is_running() or self._proc.status() == psutil.STATUS_ZOMBIE:
                return 0
            procs = [self._proc] + self._proc.children(recursive=True)
        except psutil.Error:
            return 0
        for p in procs:
            try:
                total += p.memory_info().rss
            except psutil.Error:
                pass
        return total

    def _add_rss_sample(self) -> None:
        rss = self._tree_rss()
        if rss > 0:
            with self._lock:
                self.rss_samples.append(rss)

    def _sample_disk(self) -> int:
        size = _watched_size(self.watch_dirs)
        with self._lock:
            self.disk_peak = max(self.disk_peak, size)
            self.disk_final = size
        return size

    def record_archive_cycle(self, temp_archive_bytes: int, kept_extracted_bytes: int) -> None:
        """Ghi một chu kỳ: tải archive tạm -> giải nén giữ .tex/.bib -> xóa archive tạm.

        Đỉnh đĩa tức thời = baseline + dữ liệu đã giữ + archive tạm hiện tại + phần vừa giữ.
        """
        with self._lock:
            peak_now = (
                self.disk_baseline
                + self._tracked_added_bytes
                + max(0, temp_archive_bytes)
                + max(0, kept_extracted_bytes)
            )
            self.disk_peak = max(self.disk_peak, peak_now)
            self._tracked_added_bytes += max(0, kept_extracted_bytes)

    def _ram_loop(self) -> None:
        while not self._stop.is_set():
            self._add_rss_sample()
            self._stop.wait(self.interval)

    def _disk_loop(self) -> None:
        while not self._stop.wait(self.disk_interval):
            self._sample_disk()

    def start(self) -> "ResourceMonitor":
        self._add_rss_sample()
        if self.watch_dirs:
            self.disk_baseline = self._sample_disk()
        self._threads = [threading.Thread(target=self._ram_loop, daemon=True)]
        if self.watch_dirs:
            self._threads.append(threading.Thread(target=self._disk_loop, daemon=True))
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join()
        self._add_rss_sample()
        if self.watch_dirs:
            self._sample_disk()

    def summary(self) -> Dict[str, float]:
        s = self.rss_samples or [0]
        return {
            "peak_ram_mb": round(max(s) / MB, 1),
            "avg_ram_mb": round(statistics.fmean(s) / MB, 1),
            "ram_samples": len(self.rss_samples),
            "disk_baseline_mb": round(self.disk_baseline / MB, 1),
            "peak_disk_mb": round(self.disk_peak / MB, 1),
            "final_output_mb": round(self.disk_final / MB, 1),
            "final_output_bytes": self.disk_final,
        }


class PerfRecorder:
    """Đo thời gian (tổng, từng giai đoạn, từng bài), kích thước, dedup và tài nguyên của một lần chạy."""

    def __init__(
        self,
        milestone: str = "m1",
        watch_dirs: Iterable = (),
        interval: float = 0.5,
        disk_interval: float = 5.0,
    ):
        self.milestone = milestone
        self.monitor = ResourceMonitor(watch_dirs, interval, disk_interval)
        self.stage_times: Dict[str, float] = {}
        self.paper_times: Dict[str, float] = {}

        # Kết quả thành công / thất bại theo bài (M1: crawl, M2: parse)
        self.results: Dict[str, bool] = {}
        # Dedup (M2): {kind: {arxiv_id: [before, after]}}, cộng dồn nếu gọi lại
        self.dedup: Dict[str, Dict[str, List[int]]] = {}

        # Kích thước (M1), cộng dồn các version TeX theo từng arxiv_id
        self.paper_sizes_before: Dict[str, int] = {}
        self.paper_sizes_after: Dict[str, int] = {}
        self.paper_tex_versions: Dict[str, int] = {}
        self.papers_seen: set[str] = set()
        self.papers_with_pdf_only_version: set[str] = set()
        self.n_pdf_only_versions = 0
        self.n_tex_versions = 0

        self._lock = threading.Lock()
        self._t0 = 0.0
        self.wall_s = 0.0

    def start(self) -> "PerfRecorder":
        self._t0 = time.perf_counter()
        self.monitor.start()
        return self

    def stop(self) -> None:
        self.wall_s = time.perf_counter() - self._t0
        self.monitor.stop()

    def __enter__(self) -> "PerfRecorder":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @contextmanager
    def stage(self, name: str):
        """Đo một giai đoạn. Gọi nhiều lần thì cộng dồn."""
        t = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t
            with self._lock:
                self.stage_times[name] = self.stage_times.get(name, 0.0) + dt

    @contextmanager
    def paper(self, arxiv_id: str):
        """Đo tổng thời gian xử lý một bài (mọi version). Gọi lại cùng ID thì cộng dồn."""
        t = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t
            with self._lock:
                self.paper_times[arxiv_id] = self.paper_times.get(arxiv_id, 0.0) + dt

    def add_paper_time(self, arxiv_id: str, seconds: float) -> None:
        """Cộng thêm thời gian đã đo ngoài khối paper() vào một bài (vd. chia đều thời gian batch)."""
        with self._lock:
            self.paper_times[arxiv_id] = self.paper_times.get(arxiv_id, 0.0) + seconds

    def record_paper_size(
        self,
        arxiv_id: str,
        before_bytes: int,
        after_bytes: int,
        archive_bytes: int = 0,
        is_pdf_only: Optional[bool] = None,
        track_disk: bool = True,
    ) -> None:
        """Ghi kích thước MỘT version.

        before_bytes  : tổng kích thước không nén của mọi file trong archive (gồm hình).
        after_bytes   : tổng kích thước .tex và .bib giữ lại.
        archive_bytes : kích thước .tar.gz tạm tải về (để tính đỉnh đĩa); 0 nếu không có.
        is_pdf_only   : True nếu version không có source TeX. Mặc định: after_bytes == 0.
        track_disk    : False nếu không có chu kỳ archive tạm (tránh tính đỉnh đĩa sai).
        """
        pdf_flag = (after_bytes == 0) if is_pdf_only is None else is_pdf_only
        with self._lock:
            if track_disk:
                self.monitor.record_archive_cycle(archive_bytes, 0 if pdf_flag else after_bytes)
            self.papers_seen.add(arxiv_id)
            if pdf_flag:
                self.n_pdf_only_versions += 1
                self.papers_with_pdf_only_version.add(arxiv_id)
                return
            self.n_tex_versions += 1
            self.paper_sizes_before[arxiv_id] = self.paper_sizes_before.get(arxiv_id, 0) + before_bytes
            self.paper_sizes_after[arxiv_id] = self.paper_sizes_after.get(arxiv_id, 0) + after_bytes
            self.paper_tex_versions[arxiv_id] = self.paper_tex_versions.get(arxiv_id, 0) + 1

    def record_result(self, arxiv_id: str, ok: bool) -> None:
        """Ghi bài xử lý thành công (True) hay thất bại (False); gọi lại thì ghi đè."""
        with self._lock:
            self.results[arxiv_id] = bool(ok)

    def record_dedup(self, arxiv_id: str, kind: str, before: int, after: int) -> None:
        """Ghi số lượng trước/sau deduplication của một bài (M2), vd kind='elements' hoặc 'bibtex'."""
        with self._lock:
            cur = self.dedup.setdefault(kind, {}).setdefault(arxiv_id, [0, 0])
            cur[0] += before
            cur[1] += after

    def summary(self) -> Dict:
        times = list(self.paper_times.values())
        before = list(self.paper_sizes_before.values())
        after = list(self.paper_sizes_after.values())
        nv = self.n_tex_versions

        def mean(xs):
            return round(statistics.fmean(xs)) if xs else None

        out = {
            "milestone": self.milestone,
            "total_wall_s": round(self.wall_s, 2),
            "stages_s": {k: round(v, 2) for k, v in self.stage_times.items()},
            "n_papers_timed": len(times),
            "avg_paper_s": round(statistics.fmean(times), 3) if times else None,
            "max_paper_s": round(max(times), 3) if times else None,
        }
        if "discovery" in self.stage_times:
            out["discovery_s"] = round(self.stage_times["discovery"], 2)

        if self.results:
            n_ok = sum(self.results.values())
            out.update(
                n_ok=n_ok,
                n_failed=len(self.results) - n_ok,
                success_rate=round(n_ok / len(self.results), 4),
            )

        if self.dedup:
            out["dedup"] = {}
            for kind, per in self.dedup.items():
                bef = sum(v[0] for v in per.values())
                aft = sum(v[1] for v in per.values())
                out["dedup"][kind] = {
                    "n_papers": len(per),
                    "before": bef,
                    "after": aft,
                    "reduction_pct": round(100 * (1 - aft / bef), 2) if bef else None,
                }

        if self.papers_seen:  # kích thước M1
            out.update(
                # Cấp bài
                n_papers_recorded=len(self.papers_seen),
                n_tex_papers=len(before),
                avg_paper_size_before_bytes=mean(before),
                avg_paper_size_after_bytes=mean(after),
                # Cấp version
                n_tex_versions=nv,
                n_pdf_only_versions=self.n_pdf_only_versions,
                n_papers_with_pdf_only_version=len(self.papers_with_pdf_only_version),
                avg_version_size_before_bytes=round(sum(before) / nv) if nv else None,
                avg_version_size_after_bytes=round(sum(after) / nv) if nv else None,
            )
        out.update(self.monitor.summary())
        return out

    def save(self, path: Path | str | None = None, include_per_paper: bool = True) -> Dict:
        """Lưu JSON; mặc định data/benchmark_<milestone>.json."""
        path = path or f"data/benchmark_{self.milestone}.json"
        data = self.summary()
        if include_per_paper:
            data["paper_times_s"] = {k: round(v, 3) for k, v in self.paper_times.items()}
            if self.paper_sizes_before:
                data["paper_sizes_bytes"] = {
                    k: {
                        "before": self.paper_sizes_before[k],
                        "after": self.paper_sizes_after[k],
                        "tex_versions": self.paper_tex_versions[k],
                    }
                    for k in self.paper_sizes_before
                }
            if self.results:
                data["paper_results"] = dict(self.results)
            if self.dedup:
                data["paper_dedup"] = {
                    kind: {k: {"before": v[0], "after": v[1]} for k, v in per.items()}
                    for kind, per in self.dedup.items()
                }
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data


def run_command(
    cmd: List[str],
    milestone: str = "m1",
    watch_dirs: Iterable = (),
    interval: float = 0.5,
    disk_interval: float = 5.0,
):
    """Chạy một lệnh end-to-end, trả về (summary dict, PerfRecorder)."""
    rec = PerfRecorder(milestone, watch_dirs, interval, disk_interval)
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd)
    rec.monitor = ResourceMonitor(watch_dirs, interval, disk_interval, pid=proc.pid)
    rec.monitor.start()
    code = proc.wait()
    rec.wall_s = time.perf_counter() - t0
    rec.monitor.stop()
    result = rec.summary()
    result["returncode"] = code
    result["command"] = cmd
    return result, rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Đo hiệu năng end-to-end của một lệnh (M1/M2/M3).")
    ap.add_argument("--watch", nargs="*", default=[], help="thư mục theo dõi dung lượng đĩa")
    ap.add_argument("--milestone", choices=["m1", "m2", "m3"], default="m1",
                    help="milestone đang đo; quyết định tên file mặc định (tránh ghi đè giữa các milestone)")
    ap.add_argument("--out", default=None, help="file JSON đầu ra (mặc định: data/benchmark_<milestone>.json)")
    ap.add_argument("--interval", type=float, default=0.5, help="chu kỳ lấy mẫu RAM (giây)")
    ap.add_argument("--disk-interval", type=float, default=5.0, help="chu kỳ quét ổ đĩa (giây)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- lệnh cần đo")
    args = ap.parse_args()

    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        ap.error("thiếu lệnh cần đo, EX: ... -- python src/run_m1.py")

    result, _ = run_command(cmd, args.milestone, args.watch, args.interval, args.disk_interval)
    out = Path(args.out or f"data/benchmark_{args.milestone}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result["returncode"]


if __name__ == "__main__":
    sys.exit(main())