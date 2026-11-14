r"""m1_references.py

Milestone: M1 - Crawling
Người phụ trách: P2
Mục đích: lấy reference của từng bài từ Semantic Scholar (endpoint /paper/batch), giữ
          toàn bộ reference, và dựng bảng references (chỉ giữ reference có externalIds.ArXiv).

Module này là thư viện cho src/run_m1.py:
  - fetch_batch_group(session, ids): gọi một batch, tự chia nhỏ khi server trả 400/413.
  - write_refs / write_raw_refs: ghi <paper_key>/refs.json và refs_raw.json.
  - collect_published_rows / write_published_csv: gộp refs.json thành bảng references.
  - record_failure: ghi bài lỗi vào <DATA_DIR>/refs_failures.jsonl.

Chống rate limit (HTTP 429):
  - Tôn trọng header Retry-After nếu server gửi.
  - Giãn cách thích ứng: gặp 429 thì tăng khoảng cách giữa các request, thành công thì giảm dần.
  - Ngắt mạch: nếu nhiều batch liên tiếp thất bại vì 429 thì nghỉ dài (COOLDOWN_SECONDS)
    rồi mới thử tiếp, thay vì đốt thời gian cho từng batch.

ref_submission_date lấy từ publicationDate của Semantic Scholar (không có trường
submissionDate); thiếu thì dùng năm xuất bản (YYYY).
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402
from utils.schemas import validate_reference_rows

log = logging.getLogger("m1_references")

REQUEST_TIMEOUT = 60
MAX_RETRIES = 5
BACKOFF_SECONDS = 15
MAX_BACKOFF_SECONDS = 120
RETRY_STATUS = {429, 500, 502, 503, 504}
BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"

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


def record_failure(arxiv_id: str, attempts: int, error: Exception | str,
                   category: str) -> None:
    """Append a structured failure record without exposing request headers."""
    C.ensure_dir(C.refs_failures_path().parent)
    record = {
        "arxiv_id": C.normalize_arxiv_id(arxiv_id),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts,
        "category": category,
        "error": str(error),
    }
    with C.refs_failures_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


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


def write_raw_refs(arxiv_id: str, payload: dict) -> None:
    """Write the raw paper response with the citing ID and retrieval envelope."""
    path = C.raw_refs_path(arxiv_id)
    C.ensure_dir(path.parent)
    record = {
        "arxiv_id": C.split_version(arxiv_id)[0],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": C.S2_API_URL.format(arxiv_id=C.split_version(arxiv_id)[0]),
        "response": payload,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


PUBLISHED_REFERENCE_FIELDS = (
    "arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors",
    "ref_submission_date", "ref_venue", "ref_s2_id", "external_ids",
)


def normalize_reference(citing_id: str, reference: dict) -> dict:
    external_ids = reference.get("externalIds")
    if not isinstance(external_ids, dict):
        external_ids = reference.get("external_ids")
    external_ids = external_ids if isinstance(external_ids, dict) else {}
    authors = reference.get("authors")
    if authors is None:
        authors = reference.get("ref_authors")
    if isinstance(authors, list):
        authors = [
            author.get("name", "") if isinstance(author, dict) else str(author)
            for author in authors
        ]
    return {
        "arxiv_id": C.normalize_arxiv_id(citing_id),
        "ref_arxiv_id": C.normalize_arxiv_id(
            reference.get("ref_arxiv_id") or external_ids.get("ArXiv")
        ),
        "ref_title": reference.get("ref_title") or reference.get("title"),
        "ref_authors": authors or [],
        "ref_submission_date": (
            reference.get("ref_submission_date")
            or reference.get("publicationDate")
            or (str(reference["year"]) if reference.get("year") else None)
        ),
        "ref_venue": reference.get("ref_venue") or reference.get("venue"),
        "ref_s2_id": reference.get("ref_s2_id") or reference.get("paperId"),
        "external_ids": external_ids,
    }


def deduplicate_references(rows: list[dict]) -> tuple[list[dict], int]:
    """Keep the first occurrence of each reference identity deterministically."""
    unique = []
    seen = set()
    duplicates = 0
    for row in rows:
        identity = (
            row.get("ref_s2_id")
            or row.get("ref_arxiv_id")
            or (
                str(row.get("ref_title") or "").strip().casefold(),
                tuple(str(author).strip().casefold()
                      for author in row.get("ref_authors") or []),
                row.get("ref_submission_date"),
            )
        )
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        unique.append(row)
    return unique, duplicates


def collect_published_rows() -> tuple[list[dict], dict[str, int]]:
    """Read every refs.json, keep references that carry an arXiv ID.

    Returns (rows, stats). Nothing is written to disk here, so callers can
    export the same rows to CSV and Parquet.
    """
    rows = []
    total = 0
    with_arxiv_id = 0
    duplicate_rows = 0
    for refs_path in sorted(C.DATA_DIR.glob("*/refs.json")):
        payload = json.loads(refs_path.read_text(encoding="utf-8"))
        citing_id = payload.get("arxiv_id")
        normalized = []
        for reference in payload.get("references", []):
            if not isinstance(reference, dict):
                continue
            total += 1
            normalized.append(normalize_reference(citing_id, reference))
        normalized, duplicates = deduplicate_references(normalized)
        duplicate_rows += duplicates
        for row in normalized:
            if row["ref_arxiv_id"]:
                with_arxiv_id += 1
                rows.append(row)

    validate_reference_rows(rows)
    stats = {
        "total_local_references": total,
        "references_with_arxiv_id": with_arxiv_id,
        "references_without_arxiv_id": total - with_arxiv_id,
        "published_rows": len(rows),
        "duplicate_rows": duplicate_rows,
    }
    return rows, stats


def write_published_csv(rows: list[dict], stats: dict[str, int]) -> None:
    """Write references.csv and references_stats.json."""
    output = C.references_csv_path()
    C.ensure_dir(output.parent)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PUBLISHED_REFERENCE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **row,
                "ref_authors": json.dumps(row["ref_authors"], ensure_ascii=False),
                "external_ids": json.dumps(row["external_ids"], ensure_ascii=False),
            })
    temporary.replace(output)
    C.ensure_dir(C.DATA_DIR)
    (C.DATA_DIR / "references_stats.json").write_text(
        json.dumps(stats, indent=2), encoding="utf-8"
    )
