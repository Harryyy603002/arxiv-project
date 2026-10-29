"""Milestone 1 - crawl arXiv end-to-end và đo hiệu năng bằng PerfRecorder.

Chạy MỘT lượt duy nhất trên toàn bộ danh sách ID của nhóm (đã gộp), xuất ra dạng corpus:
    python src/run_m1.py --ids-file ids.txt --corpus-dir /content/corpus \
        --user-agent "ten-nhom-crawler/1.0 (mailto:email@domain)"

Phiên bản này đã được tích hợp cơ chế CRAWL REFERENCE BATCH từ m1_references.py
để tăng tốc độ truy xuất Semantic Scholar mà vẫn giữ nguyên format đầu ra.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import posixpath
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Dict, List, Optional

import arxiv
import requests

# Khai báo đường dẫn để import các module nội bộ (crawler, utils)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawler import m1_references
from utils.benchmark import PerfRecorder, inspect_archive_bytes

VERSION_RE = re.compile(r"v(\d+)$")

# ----------------------------------------------------------------------------- tiện ích
class RateLimiter:
    """Đảm bảo khoảng cách tối thiểu giữa hai request liên tiếp (dành cho arXiv export)."""
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last = time.monotonic()


def log(msg: str) -> None:
    print(msg, flush=True)


def folder_name(arxiv_id: str) -> str:
    """2310.12345 -> 2310-12345 ; hep-th/9901001 -> hep-th_9901001."""
    return arxiv_id.replace("/", "_").replace(".", "-")


def split_version(short_id: str):
    m = VERSION_RE.search(short_id)
    return (short_id[: m.start()], int(m.group(1))) if m else (short_id, None)


def read_ids(path: Path) -> List[str]:
    ids, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s, _ = split_version(s)  # bỏ hậu tố version nếu có
        if s not in seen:
            seen.add(s)
            ids.append(s)
    return ids


# ----------------------------------------------------------------------------- discovery
def _query(client: arxiv.Client, id_list: List[str]):
    search = arxiv.Search(id_list=id_list, max_results=len(id_list))
    return list(client.results(search))


def discover(ids: List[str], batch: int = 50) -> Dict[str, dict]:
    """Dùng arXiv API: (1) ID gốc -> số version, (2) ID có version -> ngày của từng version."""
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    info: Dict[str, dict] = {}

    for i in range(0, len(ids), batch):
        for r in _query(client, ids[i:i + batch]):
            base, n = split_version(r.get_short_id())
            info[base] = {
                "title": " ".join(r.title.split()),
                "authors": [a.name for a in r.authors],
                "venue": r.journal_ref,
                "n_versions": n or 1,
                "version_dates": {1: r.published.isoformat()},
            }
        log(f"[discovery] latest-version lookup {min(i + batch, len(ids))}/{len(ids)}")

    versioned = [f"{b}v{v}" for b, d in info.items() for v in range(1, d["n_versions"] + 1)]
    for i in range(0, len(versioned), batch):
        for r in _query(client, versioned[i:i + batch]):
            base, v = split_version(r.get_short_id())
            if base in info and v and v > 1:
                info[base]["version_dates"][v] = r.updated.isoformat()
        log(f"[discovery] per-version dates {min(i + batch, len(versioned))}/{len(versioned)}")
    return info


# ----------------------------------------------------------------------------- download
def fetch_source(url: str, user_agent: str, limiter: RateLimiter, retries: int = 4) -> bytes:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        limiter.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = e.headers.get("Retry-After") if e.headers else None
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 10 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} attempts: {url} ({last_err})")


def looks_like_text(raw: bytes) -> bool:
    return b"\x00" not in raw[:4096]


def extract_tex_bib(data: bytes, vdir: Path) -> str:
    """Chỉ ghi .tex và .bib vào vdir. Trả về loại: tar | gz-tex | text-tex | pdf | other."""
    vdir.mkdir(parents=True, exist_ok=True)
    if data[:4] == b"%PDF":
        return "pdf"

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for m in tar.getmembers():
                if not m.isfile() or not m.name.lower().endswith((".tex", ".bib")):
                    continue
                rel = posixpath.normpath(m.name).lstrip("/")
                if rel.startswith("..") or "/../" in rel:
                    continue
                f = tar.extractfile(m)
                if f is None:
                    continue
                dest = vdir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read())
        return "tar"
    except (tarfile.ReadError, EOFError, OSError, zlib.error):
        pass

    raw, kind = data, "text-tex"
    if data[:2] == b"\x1f\x8b":
        try:
            raw, kind = gzip.decompress(data), "gz-tex"
        except (OSError, EOFError, zlib.error):
            return "other"
        if raw[:4] == b"%PDF":
            return "pdf"
    if looks_like_text(raw):
        (vdir / "main.tex").write_bytes(raw)
        return kind
    return "other"


# ----------------------------------------------------------------------------- references
def normalize_references(raw_refs: list) -> list:
    """Chuẩn hóa cấu trúc JSON từ Semantic Scholar về đúng format yêu cầu."""
    refs = []
    for ref in raw_refs:
        ext = ref.get("externalIds") or {}
        refs.append({
            "ref_s2_id": ref.get("paperId"),
            "ref_arxiv_id": ext.get("ArXiv"),
            "ref_title": ref.get("title"),
            "ref_authors": [a.get("name") for a in (ref.get("authors") or [])],
            "ref_submission_date": ref.get("publicationDate") or (str(ref["year"]) if ref.get("year") else None),
            "ref_venue": ref.get("venue"),
            "external_ids": ext,
        })
    return refs


# ----------------------------------------------------------------------------- crawl batch
def crawl_batch(group: List[str], info: Dict[str, dict], rec: PerfRecorder, corpus: Path,
                user_agent: str, src_limiter: RateLimiter, session: requests.Session, coverage: dict) -> None:

    # 1. Fetch batch reference trước
    started = time.perf_counter()
    try:
        batch_data = m1_references.fetch_batch_group(session, group)
    except m1_references.ReferenceCrawlError as err:
        log(f"[batch] ERROR lấy reference: {err}")
        batch_data = [None] * len(group)

    seconds_per_paper = (time.perf_counter() - started) / len(group)

    # 2. Xử lý tải và ghi file cho từng bài trong batch
    for aid, paper_s2 in zip(group, batch_data):
        rec.add_paper_time(aid, seconds_per_paper)

        with rec.paper(aid):
            paper_info = info.get(aid)
            if not paper_info:
                log(f"[{aid}] NOT FOUND in arXiv API")
                rec.record_result(aid, False)
                rec.record_references(aid, False)
                coverage["not_found_papers"] += 1
                continue

            pdir = corpus / folder_name(aid)
            pdir.mkdir(parents=True, exist_ok=True)
            n = paper_info["n_versions"]
            dates = paper_info["version_dates"]
            coverage["expected_versions"] += n
            all_ok = True

            if len(dates) != n:
                log(f"[{aid}] WARNING: thiếu ngày của {n - len(dates)} version")

            # --- Ghi metadata cấp bài
            paper_row = {
                "arxiv_id": aid,
                "title": paper_info["title"],
                "authors": paper_info["authors"],
                "submission_date": dates.get(1),
                "revised_dates": [dates[v] for v in range(2, n + 1) if v in dates],
                "venue": paper_info["venue"],
                "n_versions": n,
            }
            (pdir / "paper.json").write_text(json.dumps(paper_row, ensure_ascii=False, indent=2), encoding="utf-8")

            # --- Ghi chuẩn hóa references
            raw_refs = paper_s2.get("references") if isinstance(paper_s2, dict) else None
            if isinstance(raw_refs, list):
                refs = normalize_references(raw_refs)
                ref_ok = True
            else:
                refs = []
                ref_ok = False
            rec.record_references(aid, ref_ok, len(refs), sum(1 for x in refs if x["ref_arxiv_id"]))

            # --- Từng version
            for v in range(1, n + 1):
                vid = f"{aid}v{v}"
                vdir = pdir / f"v{v}"
                try:
                    data = fetch_source(f"https://export.arxiv.org/src/{vid}", user_agent, src_limiter)
                    kind = extract_tex_bib(data, vdir)
                    archive_bytes, before, after = inspect_archive_bytes(data, vdir)
                    no_tex = after == 0
                    rec.record_paper_size(aid, before, after, archive_bytes, is_pdf_only=no_tex)
                    (vdir / "metadata.json").write_text(json.dumps({
                        "arxiv_id": vid, "version": v, "date": dates.get(v), "title": paper_info["title"],
                        "authors": paper_info["authors"], "venue": paper_info["venue"], "source_kind": kind,
                        "archive_bytes": archive_bytes, "bytes_before": before, "bytes_after": after,
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    (vdir / "references.json").write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
                    coverage["crawled_versions"] += 1
                except Exception as e:
                    all_ok = False
                    coverage["failed_versions"] += 1
                    log(f"[{vid}] ERROR tải source: {e}")

            rec.record_result(aid, all_ok)
            log(f"[{aid}] versions={n} refs={len(refs)} ref_ok={ref_ok} ok={all_ok}")


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Milestone 1: crawl arXiv + benchmark end-to-end (Batch Ref Mode)")
    ap.add_argument("--ids-file", required=True, type=Path, help="mỗi dòng một arXiv ID gốc")
    ap.add_argument("--corpus-dir", default=Path("/content/corpus"), type=Path)
    ap.add_argument("--bench-out", default=Path("data/benchmark_m1.json"), type=Path)
    ap.add_argument("--user-agent", default="arxiv-course-crawler/1.0")
    ap.add_argument("--batch-size", type=int, default=25, help="Số lượng ID gộp lại cho một nhịp gọi API Semantic Scholar")
    args = ap.parse_args()

    ids = read_ids(args.ids_file)
    src_limiter = RateLimiter(3.0)
    session = requests.Session()
    coverage = {"expected_versions": 0, "crawled_versions": 0, "failed_versions": 0, "not_found_papers": 0}

    args.corpus_dir.mkdir(parents=True, exist_ok=True)
    with PerfRecorder("m1", watch_dirs=[args.corpus_dir]) as rec:
        with rec.stage("discovery"):
            info = discover(ids)
        with rec.stage("crawl"):
            for i in range(0, len(ids), args.batch_size):
                group = ids[i:i + args.batch_size]
                log(f"\n=== Đang xử lý Batch {i // args.batch_size + 1} ({len(group)} papers) ===")
                crawl_batch(group, info, rec, args.corpus_dir, args.user_agent, src_limiter, session, coverage)

    data = rec.save(args.bench_out)
    data["coverage"] = {**coverage,
                        "version_coverage": round(coverage["crawled_versions"] / coverage["expected_versions"], 4)
                        if coverage["expected_versions"] else None}
    args.bench_out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if not k.startswith("paper_")}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())