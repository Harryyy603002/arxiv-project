"""Chạy pipeline M1 và đo hiệu năng: discovery -> tải source -> reference.

Chạy từ thư mục gốc repo:
    python src/run_m1.py --range 2602.04870-2602.09869
    python src/run_m1.py --range 2602.04870-2602.09869 --limit 20     # chạy thử
    python src/run_m1.py --ids 2310.12345 hep-th/9901001

Kết quả đo: data/benchmark_m1.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import sys
from pathlib import Path
from typing import List, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawler import m1_download, m1_metadata, m1_references
from utils import config as C
from utils.benchmark import PerfRecorder, inspect_archive_bytes

log = logging.getLogger("run_m1")

REF_FIELDS = [
    "arxiv_id",
    "ref_arxiv_id",
    "ref_title",
    "ref_authors",
    "ref_submission_date",
    "ref_s2_id",
]


def _extract_arxiv_id_from_ref(ref: dict) -> str | None:
    """Lấy externalIds.ArXiv từ một mục reference của Semantic Scholar."""
    ext = ref.get("externalIds")
    if isinstance(ext, dict):
        aid = ext.get("ArXiv") or ext.get("arxiv")
        if aid and isinstance(aid, str) and aid.strip():
            return aid.strip()
    return None


def _count_refs(refs: list[dict]) -> Tuple[int, int]:
    """Trả về (tổng số references, số references có externalIds.ArXiv)."""
    total = len(refs)
    with_arxiv = sum(1 for r in refs if isinstance(r, dict) and _extract_arxiv_id_from_ref(r))
    return total, with_arxiv


def download_sources(ids: List[str], perf: PerfRecorder, force: bool = False) -> None:
    """Tải source từng version, chỉ giữ .tex/.bib, và ghi nhận kích thước vào PerfRecorder."""
    session = requests.Session()
    session.headers.update({"User-Agent": m1_download.USER_AGENT})

    for i, raw_id in enumerate(ids, start=1):
        aid = C.split_version(raw_id)[0]
        if not C.meta_path(aid).exists():
            continue

        versions = m1_download.read_versions(aid, fallback_n=None)
        if not versions:
            continue

        with perf.paper(aid):
            for v in versions:
                out_dir = C.version_dir(aid, v)
                status_file = out_dir / "source_status.json"

                # Checkpoint: nếu đã tải từ lần chạy trước và không bật --force
                if not force and status_file.exists():
                    try:
                        st = json.loads(status_file.read_text(encoding="utf-8"))
                        is_pdf = st.get("status") == "empty"
                        raw_b = int(st.get("uncompressed_bytes") or st.get("raw_bytes") or 0)
                        clean_b = int(st.get("extracted_bytes") or 0)
                        if clean_b == 0 and not is_pdf:
                            _, _, clean_b = inspect_archive_bytes(b"", out_dir)
                            raw_b = max(raw_b, clean_b)
                        perf.record_paper_size(
                            aid,
                            before_bytes=raw_b,
                            after_bytes=clean_b,
                            archive_bytes=0,
                            is_pdf_only=is_pdf,
                            track_disk=False,
                        )
                        continue
                    except (OSError, ValueError):
                        pass

                try:
                    data = m1_download.fetch_source(session, aid, v)
                except m1_download.DownloadError as err:
                    log.warning("[%d/%d] %s v%d: tải source thất bại (%s)", i, len(ids), aid, v, err)
                    continue

                status, reason, files = m1_download.save_source(data, out_dir)
                archive_b, raw_b, clean_b = inspect_archive_bytes(data, out_dir)
                is_pdf = status == "empty"

                record = {
                    "arxiv_id": aid,
                    "version": v,
                    "status": status,
                    "reason": reason,
                    "files": files,
                    "raw_bytes": archive_b,
                    "uncompressed_bytes": raw_b,
                    "extracted_bytes": clean_b,
                    "source_url": f"{m1_download.BASE_URL}/{aid}v{v}",
                    "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                out_dir.mkdir(parents=True, exist_ok=True)
                status_file.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                perf.record_paper_size(
                    aid,
                    before_bytes=raw_b,
                    after_bytes=clean_b,
                    archive_bytes=archive_b,
                    is_pdf_only=is_pdf,
                    track_disk=True,
                )
                log.info(
                    "[%d/%d] %s v%d: %s (raw=%dB, clean=%dB)",
                    i, len(ids), aid, v, status, raw_b, clean_b,
                )


def build_references_table() -> int:
    """Gộp tất cả data/<paper_key>/refs.json thành data/references.csv và data/references.parquet.
    Chỉ giữ các reference có externalIds.ArXiv theo đúng quy định của đề bài M1.
    """
    import pandas as pd

    rows = []
    for ref_file in sorted(C.DATA_DIR.glob("*/refs.json")):
        try:
            payload = json.loads(ref_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        citing_id = payload.get("arxiv_id")
        for ref in payload.get("references") or []:
            if not isinstance(ref, dict):
                continue
            ref_arxiv_id = _extract_arxiv_id_from_ref(ref)
            if not ref_arxiv_id:
                continue
            raw_authors = ref.get("authors") or []
            authors = [
                a.get("name", str(a)) if isinstance(a, dict) else str(a)
                for a in raw_authors
                if a
            ]
            sub_date = ref.get("publicationDate") or (
                str(ref["year"]) if ref.get("year") is not None else None
            )
            rows.append({
                "arxiv_id": citing_id,
                "ref_arxiv_id": ref_arxiv_id,
                "ref_title": ref.get("title"),
                "ref_authors": authors,
                "ref_submission_date": sub_date,
                "ref_s2_id": ref.get("paperId"),
            })

    if not rows:
        log.warning("Chưa có reference nào chứa arXiv ID trong %s", C.DATA_DIR)
        return 0

    df = pd.DataFrame(rows).reindex(columns=REF_FIELDS)
    csv_path = getattr(C, "references_csv_path", lambda: C.DATA_DIR / "references.csv")()
    parquet_path = getattr(C, "references_parquet_path", lambda: C.DATA_DIR / "references.parquet")()

    csv_df = df.copy()
    csv_df["ref_authors"] = csv_df["ref_authors"].map(lambda v: json.dumps(v, ensure_ascii=False))
    csv_df.to_csv(csv_path, index=False, encoding="utf-8")
    try:
        df.to_parquet(parquet_path, index=False)
    except ImportError:
        log.warning("Thiếu pyarrow nên chưa ghi parquet (pip install pyarrow)")
    log.info("Bảng references: %d dòng -> %s", len(df), csv_path)
    return len(df)


def crawl_references(ids: List[str], perf: PerfRecorder, force: bool = False) -> None:
    """Crawl reference qua Semantic Scholar, mỗi bài bọc trong `with perf.paper(aid):`."""
    session = requests.Session()

    for raw_id in ids:
        aid = C.split_version(raw_id)[0]
        if not C.meta_path(aid).exists():
            continue

        ref_path = C.refs_path(aid)
        with perf.paper(aid):
            if not force and ref_path.exists():
                try:
                    data = json.loads(ref_path.read_text(encoding="utf-8"))
                    refs = data.get("references") or []
                    total, with_arxiv = _count_refs(refs)
                    perf.record_references(aid, True, total_refs=total, arxiv_refs=with_arxiv)
                    continue
                except (OSError, ValueError):
                    pass

            try:
                refs = m1_references.fetch_references(session, aid)
                m1_references.write_refs(aid, refs)
                total, with_arxiv = _count_refs(refs)
                perf.record_references(aid, True, total_refs=total, arxiv_refs=with_arxiv)
                log.info("saved %s (total=%d, arxiv=%d)", ref_path, total, with_arxiv)
            except m1_references.ReferenceCrawlError as err:
                perf.record_references(aid, False)
                log.error("%s", err)

    build_references_table()


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
            base_id = C.split_version(aid)[0]
            perf.add_paper_time(base_id, per_paper)

        with perf.stage("download"):
            download_sources(ids, perf, force=args.force)

        with perf.stage("references"):
            crawl_references(ids, perf, force=args.force)

        for aid in ids:
            base_id = C.split_version(aid)[0]
            ok = C.meta_path(base_id).exists()
            if ok:
                vers = m1_download.read_versions(base_id, fallback_n=None)
                ok = bool(vers) and all(
                    (C.version_dir(base_id, v) / "source_status.json").exists() for v in vers
                )
            perf.record_result(base_id, ok)

    summary = perf.save(out)
    log.info(
        "Benchmark -> %s | wall=%.1fs, peak RAM=%.0fMB, paper_success=%s, ref_success=%s",
        out,
        summary["total_wall_s"],
        summary["peak_ram_mb"],
        summary.get("success_rate"),
        summary.get("ref_success_rate"),
    )

if __name__ == "__main__":
    main()