r"""m1_references_batch.py

Lấp nhanh các data/<paper_key>/refs.json còn thiếu bằng endpoint batch của Semantic Scholar
(POST /graph/v1/paper/batch), nhiều bài trong một request thay vì mỗi bài một request.

Định dạng đầu ra GIỐNG HỆT m1_references.write_refs, nên run_m1.py chạy sau đó sẽ coi các bài
này là đã có checkpoint và chỉ đọc lại để đếm.

Chạy từ thư mục gốc repo:
    python src/crawler/m1_references_batch.py --range 2603.00580-2603.05579 --limit 50    # thử
    python src/crawler/m1_references_batch.py --range 2603.00580-2603.05579               # chạy hết

Cần S2_API_KEY (xem utils/config.py).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import m1_references as R  # noqa: E402
from utils import config as C  # noqa: E402

log = logging.getLogger("m1_references_batch")

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
REQUEST_TIMEOUT = 120
MAX_RETRIES = 6
MIN_BATCH = 5
COOLDOWN_SECONDS = 600   # nghỉ khi một nhóm bị 429 hết lượt thử
MAX_COOLDOWNS = 6        # quá số chu kỳ nghỉ này thì dừng hẳn (chạy lại sau)


def _post_batch(session: requests.Session, ids: list[str]) -> list:
    """Gọi batch cho một nhóm ID; trả về list cùng độ dài (phần tử None = không tìm thấy)."""
    body = {"ids": [f"ARXIV:{i}" for i in ids]}
    params = {"fields": C.S2_REF_FIELDS}
    last = "unknown error"
    for attempt in range(1, MAX_RETRIES + 1):
        R.polite_wait()
        retry_after = None
        try:
            resp = session.post(BATCH_URL, headers=C.s2_headers(), params=params,
                                json=body, timeout=REQUEST_TIMEOUT)
            R._touch()
        except requests.RequestException as err:
            R._touch()
            last = f"network error: {err}"
        else:
            if resp.status_code == 200:
                data = resp.json()
                if not isinstance(data, list) or len(data) != len(ids):
                    raise R.ReferenceCrawlError("batch: phản hồi không khớp số ID gửi lên")
                R._on_success()
                return data
            last = f"HTTP {resp.status_code}"
            if resp.status_code in (400, 413):       # nhóm quá lớn/lỗi tham số: để bên ngoài chia nhỏ
                raise R.ReferenceCrawlError(last)
            if resp.status_code == 429:
                R._on_rate_limited()
                retry_after = R._retry_after_seconds(resp)
            if resp.status_code not in R.RETRY_STATUS:
                raise R.ReferenceCrawlError(last)
        if attempt < MAX_RETRIES:
            wait = min(R.MAX_BACKOFF_SECONDS, R.BACKOFF_SECONDS * (2 ** (attempt - 1)))
            if retry_after is not None:
                wait = max(wait, retry_after)
            log.warning("%s - retry sau %ds (%d/%d)", last, int(wait), attempt, MAX_RETRIES)
            time.sleep(wait)
    raise R.ReferenceCrawlError(last)


def fetch_group(session: requests.Session, ids: list[str]) -> list:
    """Như _post_batch nhưng nếu bị 400/413 thì chia đôi nhóm và gọi lại."""
    try:
        return _post_batch(session, ids)
    except R.ReferenceCrawlError as err:
        if str(err) in ("HTTP 400", "HTTP 413") and len(ids) > MIN_BATCH:
            mid = len(ids) // 2
            log.warning("%s với %d ID - chia đôi nhóm", err, len(ids))
            return fetch_group(session, ids[:mid]) + fetch_group(session, ids[mid:])
        raise


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", required=True, help="START-END, vd. 2603.00580-2603.05579")
    ap.add_argument("--month-last", nargs="*", default=[])
    ap.add_argument("--batch-size", type=int, default=50, help="số bài mỗi request (mặc định 50)")
    ap.add_argument("--limit", type=int, help="chỉ xử lý N bài thiếu đầu tiên (để thử)")
    ap.add_argument("--force", action="store_true", help="cào lại cả bài đã có refs.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not C.S2_API_KEY:
        log.warning("Không có S2_API_KEY: rất dễ bị 429, nên đặt key trước khi chạy.")

    ids = list(C.iter_id_range(*C.parse_id_range(args.range), C.parse_month_last(args.month_last)))
    todo = []
    for raw in ids:
        aid = C.split_version(raw)[0]
        if not C.meta_path(aid).exists():
            continue
        if not args.force and C.refs_path(aid).exists():
            continue
        todo.append(aid)
    if args.limit:
        todo = todo[:args.limit]
    log.info("Cần cào %d bài (batch-size=%d)", len(todo), args.batch_size)

    session = requests.Session()
    written = not_found = failed = 0
    t0 = time.monotonic()
    for start in range(0, len(todo), args.batch_size):
        group = todo[start:start + args.batch_size]
        results = None
        for cooldown in range(MAX_COOLDOWNS + 1):
            try:
                results = fetch_group(session, group)
                break
            except R.ReferenceCrawlError as err:
                if "429" in str(err) and cooldown < MAX_COOLDOWNS:
                    log.warning("nhóm %d-%d bị 429 hết lượt thử - nghỉ %ds rồi thử lại nhóm này (%d/%d)",
                                start, start + len(group), COOLDOWN_SECONDS, cooldown + 1, MAX_COOLDOWNS)
                    time.sleep(COOLDOWN_SECONDS)
                    continue
                log.error("nhóm %d-%d thất bại: %s", start, start + len(group), err)
                break
        if results is None:
            if cooldown >= MAX_COOLDOWNS:
                log.error("Bị chặn quá lâu - dừng. Chạy lại cùng lệnh sau để lấy các bài còn thiếu.")
                break
            failed += len(group)
            continue
        for aid, item in zip(group, results):
            refs = item.get("references") if isinstance(item, dict) else None
            if not isinstance(refs, list):
                not_found += 1
                log.warning("%s: Semantic Scholar không có dữ liệu references", aid)
                continue
            R.write_refs(aid, refs)
            written += 1
        done = start + len(group)
        rate = done / max(time.monotonic() - t0, 1e-9)
        log.info("tiến độ %d/%d | ghi=%d không_có=%d lỗi=%d | %.2f bài/s",
                 done, len(todo), written, not_found, failed, rate)

    log.info("XONG: ghi=%d không_có=%d lỗi=%d (chạy lại để thử các bài lỗi)", written, not_found, failed)


if __name__ == "__main__":
    main()
