r"""m1_references.py

Milestone: M1
Người phụ trách: P2
Mục đích: Crawl reference từ Semantic Scholar, giữ reference có externalIds.ArXiv; tạo bảng references

Đầu vào: arXiv ID của các bài
Đầu ra:  data/<paper_key>/refs.json

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.

Chống rate limit (HTTP 429):
  - Tôn trọng header Retry-After nếu server gửi.
  - Giãn cách thích ứng: gặp 429 thì tăng khoảng cách giữa các request, thành công thì giảm dần.
  - Ngắt mạch: nếu nhiều bài liên tiếp thất bại vì 429 thì nghỉ dài (COOLDOWN_SECONDS)
    rồi mới thử tiếp, thay vì đốt thời gian cho từng bài.
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
MAX_RETRIES = 5
BACKOFF_SECONDS = 15
MAX_BACKOFF_SECONDS = 120
RETRY_STATUS = {429, 500, 502, 503, 504}
BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
DEFAULT_BATCH_SIZE = 25
MAX_BATCH_SIZE = 500

# Giãn cách thích ứng
MAX_EXTRA_DELAY = 30.0
# Ngắt mạch
BREAKER_THRESHOLD = 3        # số bài liên tiếp thất bại vì 429 thì nghỉ dài
COOLDOWN_SECONDS = 300

_last_request_time = 0.0
_extra_delay = 0.0
_consecutive_429_failures = 0
_warned_no_key = False


class ReferenceCrawlError(Exception):
    """Semantic Scholar did not return references after the retry budget."""


def polite_wait() -> None:
    """Keep requests at least C.S2_DELAY_SECONDS (+ giãn cách thích ứng) apart."""
    gap = C.S2_DELAY_SECONDS + _extra_delay
    elapsed = time.monotonic() - _last_request_time
    if elapsed < gap:
        time.sleep(gap - elapsed)


def _touch() -> None:
    global _last_request_time
    _last_request_time = time.monotonic()


def _on_rate_limited() -> None:
    global _extra_delay
    _extra_delay = min(MAX_EXTRA_DELAY, max(2.0, _extra_delay * 2))


def _on_success() -> None:
    global _extra_delay, _consecutive_429_failures
    _extra_delay = _extra_delay * 0.8 if _extra_delay > 0.5 else 0.0
    _consecutive_429_failures = 0


def _retry_after_seconds(response: requests.Response) -> float | None:
    """Đọc Retry-After (dạng số giây). Bỏ qua nếu không có/không phải số."""
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _warn_if_no_api_key() -> None:
    global _warned_no_key
    if _warned_no_key:
        return
    _warned_no_key = True
    try:
        headers = {k.lower() for k in (C.s2_headers() or {})}
    except Exception:  # noqa: BLE001
        headers = set()
    if "x-api-key" not in headers:
        log.warning(
            "Không thấy x-api-key trong C.s2_headers(): đang dùng pool chung của Semantic Scholar, "
            "rất dễ bị 429. Hãy đặt API key (xem utils/config.py)."
        )


def _circuit_breaker() -> None:
    """Nếu nhiều bài liên tiếp thất bại vì 429 thì nghỉ dài trước khi thử bài tiếp theo."""
    global _consecutive_429_failures
    if _consecutive_429_failures >= BREAKER_THRESHOLD:
        log.warning(
            "%d bài liên tiếp bị 429 - nghỉ %ds rồi thử tiếp",
            _consecutive_429_failures, COOLDOWN_SECONDS,
        )
        time.sleep(COOLDOWN_SECONDS)
        _consecutive_429_failures = 0


def fetch_references(session: requests.Session, arxiv_id: str,
                     attempts: int = MAX_RETRIES) -> list[dict]:
    """Fetch all references, retaining non-arXiv references for later filtering."""
    global _consecutive_429_failures
    _warn_if_no_api_key()
    _circuit_breaker()

    base_id = C.split_version(arxiv_id)[0]
    url = C.S2_API_URL.format(arxiv_id=base_id)
    params = {"fields": C.S2_REF_FIELDS, "limit": 1000}
    last_problem = "unknown error"
    rate_limited = False

    for attempt in range(1, attempts + 1):
        polite_wait()
        retry_after: float | None = None
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
                _on_success()
                return references

            last_problem = f"HTTP {response.status_code}"
            if response.status_code == 429:
                rate_limited = True
                _on_rate_limited()
                retry_after = _retry_after_seconds(response)
            if response.status_code not in RETRY_STATUS:
                break

        if attempt < attempts:
            wait = min(MAX_BACKOFF_SECONDS, BACKOFF_SECONDS * (2 ** (attempt - 1)))
            if retry_after is not None:
                wait = max(wait, retry_after)
            log.warning("%s - retrying in %ss (%d/%d)", last_problem, int(wait), attempt, attempts)
            time.sleep(wait)

    if rate_limited:
        _consecutive_429_failures += 1
    raise ReferenceCrawlError(f"{base_id}: {last_problem}")


def _post_batch(session: requests.Session, ids: list[str],
                attempts: int = MAX_RETRIES) -> list:
    """Fetch several papers with one POST; response entries follow request order."""
    global _consecutive_429_failures
    _warn_if_no_api_key()
    _circuit_breaker()
    body = {"ids": [f"ARXIV:{C.split_version(arxiv_id)[0]}" for arxiv_id in ids]}
    params = {"fields": C.S2_REF_FIELDS}
    last_problem = "unknown error"
    rate_limited = False

    for attempt in range(1, attempts + 1):
        polite_wait()
        retry_after = None
        try:
            response = session.post(
                BATCH_URL, headers=C.s2_headers(), params=params, json=body,
                timeout=REQUEST_TIMEOUT,
            )
            _touch()
        except requests.RequestException as error:
            _touch()
            last_problem = f"network error: {error}"
        else:
            if response.status_code == 200:
                try:
                    papers = response.json()
                except ValueError as error:
                    raise ReferenceCrawlError(f"batch: invalid JSON: {error}") from error
                if not isinstance(papers, list) or len(papers) != len(ids):
                    raise ReferenceCrawlError("batch: response count does not match request count")
                _on_success()
                return papers

            last_problem = f"HTTP {response.status_code}"
            if response.status_code == 429:
                rate_limited = True
                _on_rate_limited()
                retry_after = _retry_after_seconds(response)
            if response.status_code in (400, 413):
                raise ReferenceCrawlError(last_problem)
            if response.status_code not in RETRY_STATUS:
                break

        if attempt < attempts:
            wait = min(MAX_BACKOFF_SECONDS, BACKOFF_SECONDS * (2 ** (attempt - 1)))
            if retry_after is not None:
                wait = max(wait, retry_after)
            log.warning("%s - batch retry in %ds (%d/%d)", last_problem, int(wait), attempt, attempts)
            time.sleep(wait)

    if rate_limited:
        _consecutive_429_failures += 1
    raise ReferenceCrawlError(last_problem)


def fetch_batch_group(session: requests.Session, ids: list[str], attempts: int = MAX_RETRIES) -> list:
    """Split rejected groups to isolate oversized responses or invalid IDs."""
    try:
        return _post_batch(session, ids, attempts)
    except ReferenceCrawlError as error:
        if str(error) in ("HTTP 400", "HTTP 413") and len(ids) > 1:
            midpoint = len(ids) // 2
            log.warning("%s for %d IDs; splitting group", error, len(ids))
            return (fetch_batch_group(session, ids[:midpoint], attempts)
                    + fetch_batch_group(session, ids[midpoint:], attempts))
        if str(error) in ("HTTP 400", "HTTP 413"):
            log.error("Cannot retrieve references for %s: %s", ids[0], error)
            return [None]
        raise


def write_refs(arxiv_id: str, references: list[dict]) -> None:
    """Write the stable exchange format consumed by later pipeline stages."""
    path = C.refs_path(arxiv_id)
    C.ensure_dir(path.parent)
    payload = {"arxiv_id": C.split_version(arxiv_id)[0], "references": references}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def crawl(ids: list[str], force: bool = False,
          attempts: int = MAX_RETRIES, session: requests.Session | None = None,
          batch_size: int | None = None) -> tuple[int, int]:
    """Crawl IDs and return ``(written, failed)`` without persisting failed requests."""
    client = session or requests.Session()
    todo = list(dict.fromkeys(C.split_version(arxiv_id)[0] for arxiv_id in ids))
    if not force:
        todo = [arxiv_id for arxiv_id in todo if not C.refs_path(arxiv_id).exists()]
    log.info("Need to crawl %d papers", len(todo))

    if batch_size:
        written = failed = 0
        for offset in range(0, len(todo), batch_size):
            group = todo[offset:offset + batch_size]
            try:
                papers = fetch_batch_group(client, group, attempts)
            except ReferenceCrawlError as error:
                failed += len(group)
                log.error("batch %d-%d failed: %s", offset + 1, offset + len(group), error)
                continue
            for arxiv_id, paper in zip(group, papers):
                references = paper.get("references") if isinstance(paper, dict) else None
                if not isinstance(references, list):
                    failed += 1
                    log.warning("%s: Semantic Scholar returned no references", arxiv_id)
                    continue
                write_refs(arxiv_id, references)
                written += 1
            log.info("Progress %d/%d | written=%d failed=%d",
                     min(offset + len(group), len(todo)), len(todo), written, failed)
        return written, failed

    written = failed = 0
    for arxiv_id in todo:
        try:
            references = fetch_references(client, arxiv_id, attempts)
        except ReferenceCrawlError as error:
            failed += 1
            log.error("%s", error)
            continue
        write_refs(arxiv_id, references)
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
    parser.add_argument("--batch", action="store_true", help="use Semantic Scholar's paper batch endpoint")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"papers per request (1-{MAX_BATCH_SIZE}; default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--attempts", type=int, default=MAX_RETRIES)
    parser.add_argument("--force", action="store_true", help="recrawl existing refs.json files")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be zero or greater")
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        parser.error(f"--batch-size must be between 1 and {MAX_BATCH_SIZE}")

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
    batch_size = args.batch_size if args.batch else None
    mode = f"batch (size {batch_size})" if batch_size else "individual"
    log.info("Processing %d IDs using %s requests", len(ids), mode)
    written, failed = crawl(ids, args.force, args.attempts, batch_size=batch_size)
    log.info("DONE: written=%d failed=%d", written, failed)


if __name__ == "__main__":
    main()
