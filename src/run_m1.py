"""Milestone 1: crawl arXiv end-to-end (mọi version) và đo hiệu năng.

Chạy từ thư mục gốc repo (ID.txt chứa các khoảng ID, mỗi dòng một khoảng):
    python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 --corpus-dir /content/corpus
    python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 --sample 50 --corpus-dir /content/data_sample

Các bước: discovery (arXiv API) -> tải source từng version (chỉ giữ .tex/.bib) song song với
lấy reference (Semantic Scholar, theo batch) -> dựng bảng papers/references (Parquet).

Bố cục dữ liệu (một cây thư mục duy nhất; --corpus-dir chính là DATA_DIR của utils/config.py):
    <corpus>/<paper_key>/paper.json
    <corpus>/<paper_key>/vN/*.tex, *.bib, metadata.json, references.json
    <corpus>/<paper_key>/refs.json, refs_raw.json
    <corpus>/papers.parquet, references.parquet (+ .csv), discovery_cache.json
    <corpus>/benchmark/session_NNN.json, benchmark_m1_total.json, sessions.csv

Resume / nhiều phiên Colab: version đã có metadata.json, bài đã có refs.json + refs_raw.json
và ID đã discovery (discovery_cache.json) đều được bỏ qua, nên có thể chia crawl thành nhiều
phiên. Mỗi phiên ghi một log riêng benchmark/session_NNN.json (cập nhật sau mỗi batch, nên
phiên bị ngắt vẫn để lại log); benchmark_m1_total.json là bản gộp mọi phiên
(xem crawler/m1_aggregate.py). KHÔNG xóa các file session_*.json.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import arxiv
import psutil
import requests

# Cho phép import các module nội bộ (crawler, utils) khi chạy `python src/run_m1.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawler import m1_aggregate, m1_download, m1_references, m1_tables  # noqa: E402
from utils import config as C  # noqa: E402
from utils.benchmark import PerfRecorder  # noqa: E402

CACHE_NAME = "discovery_cache.json"

VERSION_RE = re.compile(r"v(\d+)$")


def log(msg: str) -> None:
    print(msg, flush=True)


def folder_name(arxiv_id: str) -> str:
    """Tên thư mục bài báo, dùng chung với config.paper_key:
    2310.12345 -> 2310-12345 ; hep-th/9901001 -> hep-th-9901001."""
    return C.paper_key(arxiv_id)


def split_version(short_id: str):
    m = VERSION_RE.search(short_id)
    return (short_id[: m.start()], int(m.group(1))) if m else (short_id, None)


NEW_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")            # 2310.12345
OLD_ID_RE = re.compile(r"^[a-z\-]+(\.[A-Z]{2})?/\d{7}$")   # hep-th/9901001
# Khoảng ID: dạng chấm "2602.04870-2602.09869" hoặc viết liền "260204870-260209869" (YYMM + 5 chữ số)
RANGE_DOTTED_RE = re.compile(r"^(\d{4}\.\d{4,5})\s*-\s*(\d{4}\.\d{4,5})$")
RANGE_COMPACT_RE = re.compile(r"^(\d{4})(\d{5})\s*-\s*(\d{4})(\d{5})$")


def _parse_range(raw: str):
    """'260204870-260209869' hoặc '2602.04870-2602.09869' -> ('2602.04870', '2602.09869'); không phải khoảng -> None."""
    m = RANGE_DOTTED_RE.match(raw)
    if m:
        return m.group(1), m.group(2)
    m = RANGE_COMPACT_RE.match(raw)
    if m:
        return f"{m.group(1)}.{m.group(2)}", f"{m.group(3)}.{m.group(4)}"
    return None


def read_ids(path: Path, month_last: Optional[Dict[str, int]] = None) -> List[str]:
    """Đọc danh sách ID. Mỗi dòng là MỘT ID hoặc MỘT KHOẢNG ID (xem _parse_range).

    Chịu được BOM, CRLF, tiền tố arXiv:/URL, hậu tố vN. Khoảng đi qua nhiều tháng cần
    month_last = {"2512": 25075} (ID cuối của các tháng giữa). Dòng không hợp lệ bị bỏ qua
    và báo rõ số dòng."""
    ids, seen = [], set()

    def add(aid: str) -> None:
        if aid not in seen:
            seen.add(aid)
            ids.append(aid)

    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        bounds = _parse_range(raw)
        if bounds:
            try:
                for aid in C.iter_id_range(*bounds, month_last):
                    add(aid)
            except ValueError as error:
                raise SystemExit(f"[ids] dòng {number} ({raw}): {error}")
            continue
        aid = C.normalize_arxiv_id(raw)
        if not aid or not (NEW_ID_RE.match(aid) or OLD_ID_RE.match(aid)):
            log(f"[ids] BỎ QUA dòng {number}: {raw!r} không phải arXiv ID hợp lệ")
            continue
        add(aid)
    return ids


def select_ids(ids: List[str], limit: Optional[int], sample: Optional[int]) -> List[str]:
    """--limit N: N ID đầu; --sample N: N ID cách đều nhau trên toàn danh sách (để chạy thử đại diện)."""
    if sample and 0 < sample < len(ids):
        step = len(ids) / sample
        return [ids[int(i * step)] for i in range(sample)]
    if limit and 0 < limit < len(ids):
        return ids[:limit]
    return ids


# ----------------------------------------------------------------------------- discovery
def _is_http_400(error: Exception) -> bool:
    return getattr(error, "status", None) == 400 or "HTTP 400" in str(error)


def _query_isolating(client: arxiv.Client, id_list: List[str]):
    """Như _query, nhưng khi arXiv trả HTTP 400 (có ID bị từ chối trong nhóm) thì chia đôi
    nhóm để cô lập ID lỗi và bỏ qua riêng nó, thay vì làm hỏng cả batch."""
    try:
        return _query(client, id_list)
    except Exception as error:  # noqa: BLE001
        if not _is_http_400(error):
            raise
        if len(id_list) == 1:
            log(f"[discovery] arXiv từ chối ID {id_list[0]!r} (HTTP 400), bỏ qua")
            return []
        middle = len(id_list) // 2
        return (_query_isolating(client, id_list[:middle])
                + _query_isolating(client, id_list[middle:]))


def _query(client: arxiv.Client, id_list: List[str], attempts: int = 5):
    """Gọi arXiv API, thử lại khi lỗi mạng/HTTP/trang rỗng (chờ lâu dần)."""
    search = arxiv.Search(id_list=id_list, max_results=len(id_list))
    for attempt in range(1, attempts + 1):
        try:
            return list(client.results(search))
        except Exception as error:  # noqa: BLE001 - lỗi của thư viện arxiv rất đa dạng
            if attempt == attempts or _is_http_400(error):  # 400 là lỗi do yêu cầu, thử lại vô ích
                raise
            wait = (30 if getattr(error, "status", None) == 429 else 10) * attempt
            log(f"[discovery] {type(error).__name__}: {error}; chờ {wait}s rồi thử lại "
                f"({attempt}/{attempts})")
            time.sleep(wait)


def _load_cache(path: Optional[Path]):
    if path is None or not path.exists():
        return {}, set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        info = {}
        for base, d in raw["info"].items():
            d = dict(d)
            d["version_dates"] = {int(k): v for k, v in d["version_dates"].items()}
            info[base] = d
        return info, set(raw.get("not_found", []))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        log(f"[cache] WARNING: không load được cache từ {path}: {type(e).__name__}: {e}")
        # Backup cache hỏng để debug sau
        if path and path.exists():
            backup_path = path.with_suffix(f".json.broken_{int(time.time())}")
            try:
                shutil.copy2(path, backup_path)
                log(f"[cache] đã backup cache hỏng vào {backup_path}")
            except Exception:
                pass
        return {}, set()


def _save_cache(path: Optional[Path], info: Dict[str, dict], not_found: set) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"info": info, "not_found": sorted(not_found)}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def discover(ids: List[str], batch: int = 50, cache_path: Optional[Path] = None):
    """Dùng arXiv API: (1) ID gốc -> số version, (2) ID có version -> ngày của từng version.

    Ngày của v1 lấy từ `published` ở bước (1) nên bước (2) chỉ hỏi v2 trở đi.
    Tiến độ được lưu vào cache sau mỗi batch, nên chạy lại không phải làm lại từ đầu.
    Trả về (info, số ID lấy từ cache).
    """
    client = arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)
    info, not_found = _load_cache(cache_path)
    todo = [i for i in ids if i not in info and i not in not_found]
    n_cached = len(ids) - len(todo)

    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        found = set()
        for r in _query_isolating(client, chunk):
            base, n = split_version(r.get_short_id())
            info[base] = {
                "title": " ".join(r.title.split()),
                "authors": [a.name for a in r.authors],
                "venue": r.journal_ref,
                "n_versions": n or 1,
                "version_dates": {1: r.published.isoformat()},
            }
            found.add(base)
        not_found.update(x for x in chunk if x not in found)
        _save_cache(cache_path, info, not_found)
        log(f"[discovery] latest-version lookup {min(i + batch, len(todo))}/{len(todo)}")

    wanted = [b for b in ids if b in info]
    versioned = [f"{b}v{v}" for b in wanted
                 for v in range(2, info[b]["n_versions"] + 1)
                 if v not in info[b]["version_dates"]]
    for i in range(0, len(versioned), batch):
        for r in _query_isolating(client, versioned[i:i + batch]):
            base, v = split_version(r.get_short_id())
            if base in info and v and v > 1:
                info[base]["version_dates"][v] = r.updated.isoformat()
        _save_cache(cache_path, info, not_found)
        log(f"[discovery] per-version dates {min(i + batch, len(versioned))}/{len(versioned)}")
    return info, n_cached


# ----------------------------------------------------------------------------- references
def normalize_references(raw_refs: list) -> list:
    """Chuẩn hóa cấu trúc JSON từ Semantic Scholar về đúng format yêu cầu."""
    return [
        {
            key: value
            for key, value in m1_references.normalize_reference("", ref).items()
            if key != "arxiv_id"
        }
        for ref in raw_refs
        if isinstance(ref, dict)
    ]


# ----------------------------------------------------------------------------- crawl batch
def _fetch_reference_batch(group: List[str]) -> tuple[list, float, Optional[str]]:
    """Lấy reference của một batch từ Semantic Scholar (chạy ở luồng riêng).

    Trả về (kết quả theo thứ tự group, số giây trung bình mỗi bài, lỗi cả batch nếu có).
    Khi cả batch lỗi, kết quả là None cho từng bài.
    """
    reference_session = requests.Session()
    started = time.perf_counter()
    error_text: Optional[str] = None
    try:
        batch_data = m1_references.fetch_batch_group(reference_session, group)
    except m1_references.ReferenceCrawlError as err:
        error_text = str(err)
        log(f"[batch] ERROR lấy reference: {err}")
        batch_data = [None] * len(group)
    finally:
        reference_session.close()
    seconds_per_paper = (time.perf_counter() - started) / len(group) if group else 0.0
    return batch_data, seconds_per_paper, error_text


def _refs_cached(aid: str) -> bool:
    return C.refs_path(aid).exists() and C.raw_refs_path(aid).exists()


def _load_cached_refs(aid: str) -> Optional[list]:
    try:
        payload = json.loads(C.refs_path(aid).read_text(encoding="utf-8"))
        refs = payload["references"]
        return refs if isinstance(refs, list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def backup_critical_files(corpus: Path, backup_dir: Path) -> None:
    """Sao lưu NHANH các file cache và log quan trọng (discovery_cache.json, benchmark logs).
    
    Dùng cho checkpoint thường xuyên vì rất nhẹ (~1-5 MB).
    """
    import tarfile
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    critical_tar = backup_dir / "critical_cache.tar.gz"
    
    # Tạo tarball chứa cache và benchmark logs
    with tarfile.open(critical_tar, "w:gz") as tar:
        cache = corpus / CACHE_NAME
        if cache.exists():
            tar.add(cache, arcname=CACHE_NAME)
        
        bench_dir = corpus / "benchmark"
        if bench_dir.exists():
            for f in bench_dir.glob("*.json"):
                tar.add(f, arcname=f"benchmark/{f.name}")


def backup_papers(group: List[str], corpus: Path, backup_dir: Path) -> None:
    """Sao lưu thư mục các bài vừa xong (vd. ra Google Drive) để không mất khi Colab ngắt.
    
    Dùng tarball thay vì copy từng file để nhanh hơn 10-20 lần khi restore.
    """
    import tarfile
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    # Tạo tarball chứa papers của batch này
    # Tên file theo timestamp để tránh ghi đè
    timestamp = int(time.time())
    batch_tar = backup_dir / f"papers_batch_{timestamp}.tar.gz"
    
    with tarfile.open(batch_tar, "w:gz") as tar:
        for aid in group:
            src = corpus / folder_name(aid)
            if src.exists():
                tar.add(src, arcname=src.name)


def crawl_batch(group: List[str], info: Dict[str, dict], rec: PerfRecorder, corpus: Path,
                session: requests.Session, coverage: dict, force_refs: bool = False) -> None:
    """Tải source của cả batch (tuần tự, một luồng tới arXiv) trong khi reference của batch
    được lấy song song từ Semantic Scholar (máy chủ khác nên không tranh giới hạn của arXiv)."""
    executor = ThreadPoolExecutor(max_workers=1)
    # Chỉ gọi Semantic Scholar cho bài tồn tại và chưa có refs (checkpoint cho reference).
    need = [aid for aid in group
            if aid in info and (force_refs or not _refs_cached(aid))]
    reference_future = executor.submit(_fetch_reference_batch, need) if need else None

    try:
        # Source tải tuần tự: chỉ một luồng request tới arXiv.
        processed = []
        for aid in group:
            paper_info = info.get(aid)
            if not paper_info:
                processed.append((aid, None, False))
                log(f"[{aid}] NOT FOUND in arXiv API")
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

            with rec.paper(aid):
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
                            coverage["cached_versions"] += 1
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
                        coverage["crawled_versions"] += 1
                    except (m1_download.DownloadError, OSError, ValueError, TypeError) as e:
                        all_ok = False
                        coverage["failed_versions"] += 1
                        log(f"[{vid}] ERROR tải source: {e}")

            processed.append((aid, pdir, all_ok))

        # Nhận kết quả reference sau khi tải source xong, rồi ghi references.json cho từng version
        # (không thêm request mạng nào).
        fetched: Dict[str, object] = {}
        seconds_per_paper = 0.0
        batch_error: Optional[str] = None
        if reference_future is not None:
            batch_data, seconds_per_paper, batch_error = reference_future.result()
            fetched = dict(zip(need, batch_data))
        for aid, pdir, all_ok in processed:
            if pdir is None:
                rec.add_paper_time(aid, seconds_per_paper)
                rec.record_result(aid, False)
                rec.record_references(aid, False)
                continue

            if aid in fetched:
                rec.add_paper_time(aid, seconds_per_paper)
                paper_s2 = fetched[aid]
                if isinstance(paper_s2, dict):
                    m1_references.write_raw_refs(aid, paper_s2)
                raw_refs = paper_s2.get("references") if isinstance(paper_s2, dict) else None
                if isinstance(raw_refs, list):
                    refs = normalize_references(raw_refs)
                    ref_ok = True
                else:
                    refs = []
                    ref_ok = False
                if ref_ok:
                    m1_references.write_refs(aid, refs)
                else:  # sẽ được gọi lại ở lần chạy sau vì chưa có refs.json
                    m1_references.record_failure(
                        aid, m1_references.MAX_RETRIES,
                        batch_error or "paper not found or no references in Semantic Scholar response",
                        "request_failure" if batch_error else "invalid_response",
                    )
            else:  # đã có refs.json từ lượt chạy trước
                cached_refs = _load_cached_refs(aid)
                coverage["cached_refs_papers"] += 1
                ref_ok = cached_refs is not None
                refs = cached_refs or []
            arxiv_ref_count = sum(1 for x in refs if x.get("ref_arxiv_id"))
            rec.record_references(aid, ref_ok, len(refs), arxiv_ref_count)
            for v in range(1, info[aid]["n_versions"] + 1):
                vdir = pdir / f"v{v}"
                if vdir.exists():
                    (vdir / "references.json").write_text(
                        json.dumps(refs, ensure_ascii=False), encoding="utf-8"
                    )
            rec.record_result(aid, all_ok)
            log(f"[{aid}] versions={info[aid]['n_versions']} refs={len(refs)} "
                f"ref_ok={ref_ok} ok={all_ok}")
    finally:
        executor.shutdown(wait=True)


# ----------------------------------------------------------------------------- main
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _environment() -> dict:
    """Thông tin máy chạy, để báo cáo chứng minh số liệu đo trên Google Colab."""
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "ram_total_gb": round(psutil.virtual_memory().total / 2 ** 30, 1),
        "is_colab": "google.colab" in sys.modules or bool(os.environ.get("COLAB_RELEASE_TAG")),
    }


def _next_session(bench_dir: Path):
    """Chọn số thứ tự phiên kế tiếp dựa trên các session_NNN.json đã có."""
    bench_dir.mkdir(parents=True, exist_ok=True)
    numbers = [int(m.group(1)) for p in bench_dir.glob("session_*.json")
               if (m := re.match(r"session_(\d+)\.json$", p.name))]
    index = max(numbers, default=0) + 1
    return index, bench_dir / f"session_{index:03d}.json"


def backup_benchmark(corpus: Path, backup_dir: Path) -> None:
    """Sao lưu log các phiên: mất log nghĩa là mất bằng chứng thời gian chạy.
    
    Dùng tarball để nhanh hơn, nhưng vì file nhỏ nên không cải thiện nhiều.
    """
    import tarfile
    
    source = corpus / "benchmark"
    if not source.exists():
        return
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    bench_tar = backup_dir / "benchmark_logs.tar.gz"
    
    with tarfile.open(bench_tar, "w:gz") as tar:
        for f in source.glob("*.json"):
            tar.add(f, arcname=f"benchmark/{f.name}")
        for f in source.glob("*.csv"):
            tar.add(f, arcname=f"benchmark/{f.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Milestone 1: crawl arXiv (mọi version) + reference Semantic Scholar + benchmark")
    ap.add_argument("--ids-file", required=True, type=Path,
                    help="mỗi dòng một arXiv ID hoặc một khoảng ID (vd. 260204870-260209869)")
    ap.add_argument("--month-last", nargs="*", default=[], metavar="YYMM=N",
                    help="ID cuối của tháng, cho khoảng đi qua nhiều tháng (vd. 2512=25075)")
    ap.add_argument("--limit", type=int, default=None, help="chỉ lấy N ID đầu (chạy thử)")
    ap.add_argument("--sample", type=int, default=None,
                    help="lấy N ID cách đều trên toàn danh sách (chạy thử, đại diện hơn --limit)")
    ap.add_argument("--corpus-dir", default=C.DATA_DIR, type=Path,
                    help="thư mục dữ liệu (= DATA_DIR; mặc định lấy từ ARXIV_DATA_DIR hoặc <repo>/data)")
    ap.add_argument("--bench-dir", type=Path, default=None,
                    help="thư mục log từng phiên (mặc định <corpus-dir>/benchmark)")
    ap.add_argument("--user-agent", default=C.USER_AGENT)
    ap.add_argument("--batch-size", type=int, default=25, help="Số lượng ID gộp lại cho một nhịp gọi API Semantic Scholar")
    ap.add_argument("--no-discovery-cache", action="store_true",
                    help="không đọc/ghi discovery_cache.json (mất khả năng resume discovery)")
    ap.add_argument("--force-refs", action="store_true", help="gọi lại Semantic Scholar kể cả bài đã có refs")
    ap.add_argument("--backup-dir", type=Path, default=None,
                    help="sao lưu từng batch và log phiên vào thư mục này (vd. Google Drive)")
    ap.add_argument("--skip-tables", action="store_true", help="không dựng papers/references.parquet")
    args = ap.parse_args()

    # Một cây dữ liệu duy nhất: source, paper.json và refs.json đều nằm dưới corpus-dir.
    corpus = args.corpus_dir.expanduser().resolve()
    C.DATA_DIR = corpus
    C.LOGS_DIR = corpus / "logs"
    corpus.mkdir(parents=True, exist_ok=True)

    if "example.com" in C.CONTACT_EMAIL:
        log("CẢNH BÁO: CONTACT_EMAIL còn là giá trị mặc định; hãy `export CONTACT_EMAIL=email-that`.")
    if not C.S2_API_KEY:
        log("CẢNH BÁO: chưa có S2_API_KEY, Semantic Scholar sẽ chậm và dễ bị 429.")

    ids = select_ids(read_ids(args.ids_file, C.parse_month_last(args.month_last)), args.limit, args.sample)
    log(f"[ids] {len(ids)} ID sẽ được xử lý")
    session = requests.Session()
    session.headers.update({"User-Agent": args.user_agent})
    coverage = {"expected_versions": 0, "crawled_versions": 0, "failed_versions": 0, "not_found_papers": 0,
                "cached_versions": 0, "cached_refs_papers": 0, "discovery_cached_ids": 0}
    cache_path = None if args.no_discovery_cache else corpus / CACHE_NAME
    table_stats: dict = {}

    bench_dir = (args.bench_dir or corpus / "benchmark").expanduser().resolve()
    session_index, session_path = _next_session(bench_dir)
    started_at = _utcnow()
    log(f"=== PHIÊN {session_index}: log tại {session_path} ===")

    def session_extra(status: str, ended: bool = False) -> dict:
        crawled, expected = coverage["crawled_versions"], coverage["expected_versions"]
        resumed = bool(coverage["cached_versions"] or coverage["cached_refs_papers"]
                       or coverage["discovery_cached_ids"])
        extra = {
            "session_index": session_index,
            "status": status,  # running (đang chạy / bị kill đột ngột) | interrupted | completed
            "started_at_utc": started_at,
            "updated_at_utc": _utcnow(),
            "environment": _environment(),
            "ids_file": str(args.ids_file),
            "n_ids": len(ids),
            "batch_size": args.batch_size,
            "resumed": resumed,
            "new_versions_this_session": crawled - coverage["cached_versions"],
            "coverage": {**coverage, "version_coverage": round(crawled / expected, 4) if expected else None},
            "tables": table_stats,
        }
        if ended:
            extra["ended_at_utc"] = extra["updated_at_utc"]
        return extra

    last_checkpoint = 0.0

    def checkpoint(force: bool = False) -> None:
        """Ghi log phiên giữa chừng (tối đa 1 lần/phút) để phiên bị ngắt vẫn còn bằng chứng."""
        nonlocal last_checkpoint
        if force or time.monotonic() - last_checkpoint >= 60:
            rec.checkpoint(session_path, extra=session_extra("running"))
            if args.backup_dir:
                backup_benchmark(corpus, args.backup_dir)
            last_checkpoint = time.monotonic()

    with PerfRecorder("m1", watch_dirs=[corpus]) as rec:
        try:
            checkpoint(force=True)
            with rec.stage("discovery"):
                info, coverage["discovery_cached_ids"] = discover(ids, cache_path=cache_path)
            checkpoint(force=True)
            # Backup cache ngay sau discovery để tránh mất cache nếu ngắt trong batch đầu tiên
            if args.backup_dir:
                log("[backup] Đang backup discovery cache...")
                backup_critical_files(corpus, args.backup_dir)
                log("[backup] ✓ Discovery cache đã được backup")
            for i in range(0, len(ids), args.batch_size):
                group = ids[i:i + args.batch_size]
                log(f"\n=== Đang xử lý Batch {i // args.batch_size + 1} ({len(group)} papers) ===")
                started = time.perf_counter()
                crawl_batch(group, info, rec, corpus, session, coverage, force_refs=args.force_refs)
                rec.add_stage_time("crawl", time.perf_counter() - started)  # ghi dần theo batch
                if args.backup_dir:
                    started = time.perf_counter()
                    backup_papers(group, corpus, args.backup_dir)
                    rec.add_stage_time("backup", time.perf_counter() - started)
                checkpoint()
            if not args.skip_tables:
                with rec.stage("tables"):
                    table_stats = m1_tables.build_all()
        except BaseException:  # lỗi bất ngờ hoặc Ctrl+C: vẫn để lại log đầy đủ đến thời điểm này
            rec.checkpoint(session_path, extra=session_extra("interrupted", ended=True))
            if args.backup_dir:
                backup_benchmark(corpus, args.backup_dir)
            m1_aggregate.aggregate(corpus)
            raise

    data = rec.save(session_path, extra=session_extra("completed", ended=True))
    m1_aggregate.aggregate(corpus)  # cập nhật benchmark/benchmark_m1_total.json
    if args.backup_dir:
        backup_benchmark(corpus, args.backup_dir)
    if data["resumed"]:
        log("LƯU Ý: phiên này dùng dữ liệu đã crawl từ phiên trước; số liệu thời gian toàn cục "
            "nằm ở benchmark/benchmark_m1_total.json (đã cộng dồn các phiên).")
    print(json.dumps({k: v for k, v in data.items() if not k.startswith("paper_")}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
