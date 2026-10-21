"""M1 - P3: lấy metadata + danh sách version qua arXiv API, dựng bảng papers.

Với mỗi ID ghi:
    data/<paper_key>/versions.json   (cho Quoc tải source)
    data/<paper_key>/meta.json
Ghi ID không tồn tại vào data/failed_ids.txt, rồi gộp bảng data/papers.csv + papers.parquet.

Chạy từ thư mục gốc repo:
    python src/crawler/m1_metadata.py --range 2602.04870-2602.09869
    python src/crawler/m1_metadata.py --ids 2310.12345 hep-th/9901001      # thử vài ID
    python src/crawler/m1_metadata.py --range 2602.04870-2602.09869 --limit 20
    python src/crawler/m1_metadata.py --table-only                           # chỉ dựng lại bảng

Chạy lại sẽ bỏ qua ID đã có meta.json hoặc đã nằm trong failed_ids.txt (resume).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402

log = logging.getLogger("m1_metadata")

# Chuẩn hóa: .../abs/2310.12345v4  hoặc  .../abs/hep-th/9901001v2
_ENTRY_RE = re.compile(r"/abs/(?P<base>.+?)v(?P<ver>\d+)$")

META_FIELDS = [
    "arxiv_id", "title", "authors", "abstract", "submission_date", "latest_update",
    "latest_version", "primary_category", "categories", "journal_ref", "doi", "comment",
]
LIST_FIELDS = ("authors", "categories")


# --------------------------------------------------------------------------- #
# Hàm thuần (dễ test)
# --------------------------------------------------------------------------- #
def parse_entry_id(entry_id: str) -> Optional[Tuple[str, int]]:
    """'http://arxiv.org/abs/2310.12345v4' -> ('2310.12345', 4). Không hợp lệ -> None."""
    m = _ENTRY_RE.search(entry_id or "")
    return (m["base"], int(m["ver"])) if m else None


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def build_versions(base: str, latest: int, published: Optional[str], updated: Optional[str]) -> dict:
    """arXiv API chỉ cho ngày của v1 và của bản mới nhất; version giữa để null."""
    versions = []
    for n in range(1, latest + 1):
        date = published if n == 1 else (updated if n == latest else None)
        versions.append({"version": f"v{n}", "id_with_version": f"{base}v{n}", "date": date})
    return {"arxiv_id": base, "latest_version": latest, "versions": versions}


def build_meta(result, base: str, latest: int) -> dict:
    return {
        "arxiv_id": base,
        "title": " ".join((result.title or "").split()),
        "authors": [getattr(a, "name", str(a)) for a in (result.authors or [])],
        "abstract": " ".join((result.summary or "").split()),
        "submission_date": _iso(result.published),
        "latest_update": _iso(result.updated),
        "latest_version": latest,
        "primary_category": result.primary_category,
        "categories": list(result.categories or []),
        "journal_ref": result.journal_ref,
        "doi": result.doi,
        "comment": result.comment,
    }


def write_json(path: Path, obj) -> None:
    C.ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def store_results(requested: Iterable[str], results: Iterable) -> Tuple[int, List[str]]:
    """Ghi versions.json/meta.json cho kết quả trả về. Trả (số ghi được, danh sách ID không có)."""
    found: Dict[str, Tuple[object, int]] = {}
    for r in results:
        parsed = parse_entry_id(getattr(r, "entry_id", ""))
        if parsed:  # bỏ qua entry lỗi của API
            found[parsed[0]] = (r, parsed[1])

    ok, missing = 0, []
    for rid in requested:
        base = C.split_version(rid)[0]
        if base not in found:
            missing.append(rid)
            continue
        r, latest = found[base]
        meta = build_meta(r, base, latest)
        write_json(C.versions_path(base),
                   build_versions(base, latest, meta["submission_date"], meta["latest_update"]))
        write_json(C.meta_path(base), meta)
        ok += 1
    return ok, missing


# --------------------------------------------------------------------------- #
# Gọi API
# --------------------------------------------------------------------------- #
def fetch_batch(client, ids: List[str], attempts: int = 3) -> Optional[list]:
    import arxiv  # import trễ để test không cần mạng/thư viện

    for i in range(1, attempts + 1):
        try:
            search = arxiv.Search(id_list=ids, max_results=len(ids))
            return list(client.results(search))
        except Exception as e:  # noqa: BLE001
            log.warning("Lỗi batch (lần %d/%d): %s", i, attempts, e)
            time.sleep(10 * i)
    return None


def load_failed() -> set:
    p = C.failed_ids_path()
    return set(p.read_text().split()) if p.exists() else set()


def append_failed(ids: List[str]) -> None:
    if ids:
        C.ensure_dir(C.failed_ids_path().parent)
        with open(C.failed_ids_path(), "a", encoding="utf-8") as f:
            f.write("\n".join(ids) + "\n")


def crawl(ids: List[str], batch_size: int, force: bool = False) -> None:
    import arxiv

    failed = load_failed()
    todo = [i for i in ids if force or (not C.meta_path(i).exists() and i not in failed)]
    log.info("Tổng %d ID, cần crawl %d (bỏ qua %d đã có/đã lỗi)", len(ids), len(todo), len(ids) - len(todo))

    client = arxiv.Client(page_size=batch_size, delay_seconds=C.ARXIV_DELAY_SECONDS, num_retries=5)
    n_ok = n_missing = n_error = 0
    t0 = time.time()
    for s in range(0, len(todo), batch_size):
        batch = todo[s:s + batch_size]
        results = fetch_batch(client, batch)
        if results is None:  # lỗi mạng: KHÔNG đánh dấu failed, chạy lại sẽ thử tiếp
            n_error += len(batch)
            continue
        ok, missing = store_results(batch, results)
        append_failed(missing)
        n_ok, n_missing = n_ok + ok, n_missing + len(missing)
        log.info("[%d/%d] ok=%d không tồn tại=%d (%.0fs)", s + len(batch), len(todo), n_ok, n_missing,
                 time.time() - t0)
    log.info("XONG: ok=%d, không tồn tại=%d, lỗi tạm thời=%d (chạy lại để thử các ID lỗi)",
             n_ok, n_missing, n_error)


# --------------------------------------------------------------------------- #
# Bảng papers
# --------------------------------------------------------------------------- #
def build_papers_table() -> int:
    import pandas as pd

    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(C.DATA_DIR.glob("*/meta.json"))]
    if not rows:
        log.warning("Chưa có meta.json nào trong %s", C.DATA_DIR)
        return 0
    df = pd.DataFrame(rows).reindex(columns=META_FIELDS)

    csv_df = df.copy()  # CSV không có kiểu list -> lưu chuỗi JSON
    for col in LIST_FIELDS:
        csv_df[col] = csv_df[col].map(lambda v: json.dumps(v, ensure_ascii=False))
    csv_df.to_csv(C.papers_csv_path(), index=False, encoding="utf-8")
    try:
        df.to_parquet(C.papers_parquet_path(), index=False)
    except ImportError:
        log.warning("Thiếu pyarrow nên chưa ghi parquet (pip install pyarrow)")
    log.info("Bảng papers: %d dòng -> %s", len(df), C.papers_csv_path())
    return len(df)


# --------------------------------------------------------------------------- #
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--range", help="START-END, vd. 2602.04870-2602.09869")
    ap.add_argument("--month-last", nargs="*", default=[], help="vd. 2512=25999 (khoảng qua nhiều tháng)")
    ap.add_argument("--ids", nargs="*", help="danh sách ID cụ thể (kể cả ID cũ hep-th/9901001)")
    ap.add_argument("--limit", type=int, help="chỉ lấy N ID đầu (để thử)")
    ap.add_argument("--batch-size", type=int, default=C.ARXIV_API_BATCH)
    ap.add_argument("--force", action="store_true", help="crawl lại cả ID đã có")
    ap.add_argument("--table-only", action="store_true", help="chỉ dựng lại papers.csv/parquet")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    C.ensure_dir(C.DATA_DIR)

    if not args.table_only:
        if args.ids:
            ids = args.ids
        elif args.range:
            ids = list(C.iter_id_range(*C.parse_id_range(args.range), C.parse_month_last(args.month_last)))
        else:
            ap.error("cần --range hoặc --ids (hoặc --table-only)")
        if args.limit:
            ids = ids[:args.limit]
        crawl(ids, args.batch_size, args.force)
    build_papers_table()


if __name__ == "__main__":
    main()