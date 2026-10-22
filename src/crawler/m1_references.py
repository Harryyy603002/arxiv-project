r"""m1_references.py

Milestone: M1
Người phụ trách: P2
Mục đích: Crawl reference từ Semantic Scholar, giữ reference có externalIds.ArXiv; tạo bảng references

Đầu vào: arXiv ID của các bài
Đầu ra:  data/<paper_key>/refs.json

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402

log = logging.getLogger("m1_references")

REQUEST_TIMEOUT = 60
MAX_RETRIES = 4
BACKOFF_SECONDS = 10
RETRY_STATUS = {429, 500, 502, 503, 504}
_last_request_time = 0.0


class ReferenceCrawlError(Exception):
    """Semantic Scholar did not return references after the retry budget."""


def polite_wait() -> None:
    """Keep requests at least C.S2_DELAY_SECONDS apart."""
    elapsed = time.monotonic() - _last_request_time
    if elapsed < C.S2_DELAY_SECONDS:
        time.sleep(C.S2_DELAY_SECONDS - elapsed)


def _touch() -> None:
    global _last_request_time
    _last_request_time = time.monotonic()


def fetch_references(session: requests.Session, arxiv_id: str,
                     attempts: int = MAX_RETRIES) -> list[dict]:
    """Fetch all references, retaining non-arXiv references for later filtering."""
    base_id = C.split_version(arxiv_id)[0]
    url = C.S2_API_URL.format(arxiv_id=base_id)
    params = {"fields": C.S2_REF_FIELDS, "limit": 1000}
    last_problem = "unknown error"

    for attempt in range(1, attempts + 1):
        polite_wait()
        try:
            response = session.get(
                url, headers=C.s2_headers(), params=params, timeout=REQUEST_TIMEOUT
            )
            _touch()
        except requests.RequestException as error:
            _touch()
            last_problem = f"network error: {error}"
        else:
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as error:
                    raise ReferenceCrawlError(f"{base_id}: invalid JSON: {error}") from error
                references = payload.get("references")
                if not isinstance(references, list):
                    raise ReferenceCrawlError(f"{base_id}: response has no references list")
                return references

            last_problem = f"HTTP {response.status_code}"
            if response.status_code not in RETRY_STATUS:
                break

        if attempt < attempts:
            wait = BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning("%s - retrying in %ss (%d/%d)", last_problem, wait, attempt, attempts)
            time.sleep(wait)

    raise ReferenceCrawlError(f"{base_id}: {last_problem}")


def write_refs(arxiv_id: str, references: list[dict]) -> None:
    """Write the stable exchange format consumed by later pipeline stages."""
    path = C.refs_path(arxiv_id)
    C.ensure_dir(path.parent)
    payload = {"arxiv_id": C.split_version(arxiv_id)[0], "references": references}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def crawl(ids: list[str], force: bool = False,
          attempts: int = MAX_RETRIES, session: requests.Session | None = None) -> tuple[int, int]:
    """Crawl IDs and return ``(written, failed)`` without persisting transient failures."""
    client = session or requests.Session()
    written = failed = 0
    for arxiv_id in ids:
        if not force and C.refs_path(arxiv_id).exists():
            log.info("skip %s (refs.json already exists)", arxiv_id)
            continue
        try:
            write_refs(arxiv_id, fetch_references(client, arxiv_id, attempts))
        except ReferenceCrawlError as error:
            failed += 1
            log.error("%s", error)
            continue
        written += 1
        log.info("saved %s", C.refs_path(arxiv_id))
    return written, failed


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", nargs="+", help="arXiv IDs, optionally including a version")
    group.add_argument("--ids-file", type=Path, help="file containing one arXiv ID per line")
    group.add_argument("--range", dest="id_range", help="ID range, e.g. 2606.20734-2606.25733")
    parser.add_argument("--month-last", nargs="*", default=[],
                        help="last ID for intermediate months, e.g. 2512=25000")
    parser.add_argument("--limit", type=int, help="process only the first N IDs")
    parser.add_argument("--attempts", type=int, default=MAX_RETRIES)
    parser.add_argument("--force", action="store_true", help="recrawl existing refs.json files")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be zero or greater")

    ids = args.ids
    if args.id_range:
        try:
            start, end = C.parse_id_range(args.id_range)
            ids = list(C.iter_id_range(start, end, C.parse_month_last(args.month_last)))
        except (ValueError, KeyError) as error:
            parser.error(str(error))
    elif args.ids_file:
        ids = [line.strip() for line in args.ids_file.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    if args.limit is not None:
        ids = ids[:args.limit]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Processing %d IDs", len(ids))
    written, failed = crawl(ids, args.force, args.attempts)
    log.info("DONE: written=%d failed=%d", written, failed)


if __name__ == "__main__":
    main()
