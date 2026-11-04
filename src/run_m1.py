"""Milestone 1 - crawl arXiv end-to-end và đo hiệu năng bằng PerfRecorder.

Chạy MỘT lượt duy nhất trên toàn bộ danh sách ID của nhóm (đã gộp), xuất ra dạng corpus:
    python src/run_m1.py --ids-file ids.txt --corpus-dir /content/corpus \
        --user-agent "ten-nhom-crawler/1.0 (mailto:email@domain)"

Phiên bản này đã được tích hợp cơ chế CRAWL REFERENCE BATCH từ m1_references.py
để tăng tốc độ truy xuất Semantic Scholar mà vẫn giữ nguyên format đầu ra.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

import arxiv
import requests

# Khai báo đường dẫn để import các module nội bộ (crawler, utils)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawler import m1_download, m1_references
from utils import config as C
from utils.benchmark import PerfRecorder

VERSION_RE = re.compile(r"v(\d+)$")

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


def build_references_table() -> int:
    """Compatibility hook for callers that build the references table separately."""
    return 0


def crawl_references(
    ids: List[str],
    perf: PerfRecorder,
    force: bool = False,
    batch_size: int = m1_references.DEFAULT_BATCH_SIZE,
) -> None:
    """Fetch references in batches for the legacy run_m1 API."""
    session = requests.Session()
    pending = []
    seen = set()

    for raw_id in ids:
        aid = C.split_version(raw_id)[0]
        if aid in seen or not C.meta_path(aid).exists():
            continue
        seen.add(aid)
        if not force and C.refs_path(aid).exists():
            continue
        pending.append(aid)

    for offset in range(0, len(pending), batch_size):
        group = pending[offset:offset + batch_size]
        started = time.perf_counter()
        try:
            papers = m1_references.fetch_batch_group(session, group)
        except m1_references.ReferenceCrawlError:
            papers = [None] * len(group)
        seconds_per_paper = (time.perf_counter() - started) / len(group)

        for aid, paper in zip(group, papers):
            perf.add_paper_time(aid, seconds_per_paper)
            refs = paper.get("references") if isinstance(paper, dict) else None
            if not isinstance(refs, list):
                perf.record_references(aid, False)
                continue
            m1_references.write_refs(aid, refs)
            perf.record_references(
                aid,
                True,
                total_refs=len(refs),
                arxiv_refs=sum(
                    1 for ref in refs
                    if isinstance(ref, dict)
                    and isinstance(ref.get("externalIds"), dict)
                    and (ref["externalIds"].get("ArXiv") or ref["externalIds"].get("arxiv"))
                ),
            )

    build_references_table()


# ----------------------------------------------------------------------------- crawl batch
def crawl_batch(group: List[str], info: Dict[str, dict], rec: PerfRecorder, corpus: Path,
                session: requests.Session, coverage: dict) -> None:

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
                checkpoint = vdir / "metadata.json"
                if checkpoint.exists():
                    try:
                        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
                        archive_bytes = int(cached["archive_bytes"])
                        before = int(cached["bytes_before"])
                        after = int(cached["bytes_after"])
                    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                        pass
                    else:
                        rec.record_paper_size(
                            aid, before, after, archive_bytes=0, is_pdf_only=after == 0
                        )
                        coverage["crawled_versions"] += 1
                        continue
                try:
                    data = m1_download.fetch_source(session, aid, v)
                    (
                        status,
                        reason,
                        files,
                        archive_bytes,
                        before,
                        after,
                    ) = m1_download.save_source_with_stats(data, vdir)
                    no_tex = after == 0
                    rec.record_paper_size(aid, before, after, archive_bytes, is_pdf_only=no_tex)
                    record = {
                        "arxiv_id": vid, "version": v, "date": dates.get(v), "title": paper_info["title"],
                        "authors": paper_info["authors"], "venue": paper_info["venue"],
                        "status": status, "reason": reason, "files": files,
                        "archive_bytes": archive_bytes, "bytes_before": before, "bytes_after": after,
                    }
                    vdir.mkdir(parents=True, exist_ok=True)
                    temporary = checkpoint.with_suffix(".json.tmp")
                    temporary.write_text(
                        json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    temporary.replace(checkpoint)
                    (vdir / "references.json").write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
                    coverage["crawled_versions"] += 1
                except (m1_download.DownloadError, OSError, ValueError, TypeError) as e:
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
    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent})
    coverage = {"expected_versions": 0, "crawled_versions": 0, "failed_versions": 0, "not_found_papers": 0}

    args.corpus_dir.mkdir(parents=True, exist_ok=True)
    with PerfRecorder("m1", watch_dirs=[args.corpus_dir]) as rec:
        with rec.stage("discovery"):
            info = discover(ids)
        with rec.stage("crawl"):
            for i in range(0, len(ids), args.batch_size):
                group = ids[i:i + args.batch_size]
                log(f"\n=== Đang xử lý Batch {i // args.batch_size + 1} ({len(group)} papers) ===")
                crawl_batch(group, info, rec, args.corpus_dir, session, coverage)

    data = rec.save(args.bench_out)
    data["coverage"] = {**coverage,
                        "version_coverage": round(coverage["crawled_versions"] / coverage["expected_versions"], 4)
                        if coverage["expected_versions"] else None}
    args.bench_out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in data.items() if not k.startswith("paper_")}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())