r"""m1_download.py

Milestone: M1 - Crawling
Người phụ trách: P1
Mục đích: Tải mã nguồn LaTeX của từng version bài báo từ arXiv, chỉ giữ file
          .tex và .bib. Version không có TeX (chỉ có PDF...) vẫn được ghi lại
          dưới dạng "bản ghi rỗng" thay vì bị bỏ qua.

Đầu vào:  data/<paper_key>/versions.json (do P3 tạo) và danh sách arXiv ID
Đầu ra:   data/<paper_key>/vN/*.tex, *.bib và vN/source_status.json

Cách chạy (từ thư mục gốc của repo):
    # Thử nhanh 3 bài, chỉ version 1:
    python src/m1_download.py --ids 1706.03762 1810.04805 1512.03385 --versions 1

    # Chạy thật, đọc danh sách ID từ file (mỗi dòng một ID):
    python src/m1_download.py --ids-file data/ids.txt

Đặc điểm:
  - Lịch sự với server arXiv: chờ >= 3 giây giữa 2 request, chỉ 1 kết nối,
    có User-Agent mô tả rõ.
  - Checkpoint: version nào đã có source_status.json thì lần chạy sau bỏ qua,
    nên bị ngắt giữa chừng vẫn chạy tiếp được.
  - Gặp lỗi mạng/429/5xx thì tự thử lại, chờ lâu dần.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import tarfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import requests

from src.utils.config import paper_dir, version_dir

# ----------------------------------------------------------------------------
# CẤU HÌNH
# ----------------------------------------------------------------------------
CONTACT_EMAIL = "thienquocface06@gmail.com" 
USER_AGENT = (
    "arxiv-course-project-crawler/1.0 "
    f"(student project; contact: {CONTACT_EMAIL})"
)
BASE_URL = "https://export.arxiv.org/src"  # KHÔNG dùng arxiv.org (bị chặn 403)
MIN_DELAY_SECONDS = 3.0                    # arXiv yêu cầu >= 3 giây/request
REQUEST_TIMEOUT = 60                       # giây
MAX_RETRIES = 4                            # số lần thử tối đa cho 1 version
BACKOFF_SECONDS = 30                       # lần thử thứ k sẽ chờ 30*k giây
RETRY_STATUS = {429, 500, 502, 503, 504}   # các mã lỗi đáng để thử lại
KEEP_SUFFIXES = {".tex", ".bib"}           # chỉ giữ 2 loại file này
MAX_FILE_BYTES = 50 * 1024 * 1024          # bỏ qua file đơn lẻ > 50 MB


class DownloadError(Exception):
    """Không tải được version (sau khi đã thử lại). Version này KHÔNG được
    ghi checkpoint, nên lần chạy sau sẽ thử lại."""


# ----------------------------------------------------------------------------
# 1. TẢI VỀ (có chờ lịch sự + thử lại)
# ----------------------------------------------------------------------------
_last_request_time = 0.0  # thời điểm của request gần nhất


def _touch() -> None:
    global _last_request_time
    _last_request_time = time.monotonic()


def polite_wait() -> None:
    """Đảm bảo giữa 2 request cách nhau ít nhất MIN_DELAY_SECONDS."""
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_DELAY_SECONDS:
        time.sleep(MIN_DELAY_SECONDS - elapsed)


def fetch_source(session: requests.Session, arxiv_id: str, version: int) -> bytes:
    """Tải file nguồn của một version, trả về nội dung dạng bytes."""
    url = f"{BASE_URL}/{arxiv_id}v{version}"
    last_problem = "không rõ"

    for attempt in range(1, MAX_RETRIES + 1):
        polite_wait()
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as err:
            _touch()
            last_problem = f"lỗi mạng: {err}"
        else:
            _touch()
            if resp.status_code == 200:
                return resp.content
            last_problem = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUS:
                break  # lỗi kiểu 404/403: thử lại cũng vô ích

        if attempt < MAX_RETRIES:
            wait = BACKOFF_SECONDS * attempt
            print(f"    ! {last_problem} - chờ {wait}s rồi thử lại "
                  f"({attempt}/{MAX_RETRIES})")
            time.sleep(wait)

    raise DownloadError(f"{arxiv_id}v{version}: {last_problem}")


# ----------------------------------------------------------------------------
# 2. LƯU FILE: chỉ giữ .tex và .bib
# ----------------------------------------------------------------------------
def _safe_relative_path(name: str) -> PurePosixPath | None:
    """Kiểm tra đường dẫn trong file nén có an toàn không.
    Trả về None nếu nên bỏ qua (đường dẫn tuyệt đối, chứa '..', file rác Mac)."""
    path = PurePosixPath(name)
    if path.is_absolute():
        return None
    parts = [p for p in path.parts if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    if parts[0] == "__MACOSX" or parts[-1].startswith("._"):
        return None
    return PurePosixPath(*parts)


def _collect_tex_bib(tar: tarfile.TarFile) -> dict[str, bytes]:
    """Đọc CHỈ các file .tex/.bib từ file tar vào bộ nhớ (chưa ghi ra đĩa).
    Giữ nguyên đường dẫn con (vì \\input{sections/intro} cần đúng đường dẫn).
    Đọc hết rồi mới ghi để file nén hỏng không để lại file lưu dở dang."""
    found: dict[str, bytes] = {}
    for member in tar.getmembers():
        if not member.isfile():
            continue
        rel = _safe_relative_path(member.name)
        if rel is None or rel.suffix.lower() not in KEEP_SUFFIXES:
            continue
        if member.size > MAX_FILE_BYTES:
            continue
        fileobj = tar.extractfile(member)
        if fileobj is None:
            continue
        found[rel.as_posix()] = fileobj.read()  # giữ nguyên bytes, không đổi mã hóa
    return found


def _write_files(found: dict[str, bytes], out_dir: Path) -> list[str]:
    """Ghi các file đã đọc ra thư mục out_dir, trả về danh sách đường dẫn."""
    for rel, content in found.items():
        target = out_dir / Path(*PurePosixPath(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return sorted(found)


def _looks_like_tex(content: bytes) -> bool:
    text = content[:200_000].decode("latin-1", errors="ignore")
    return "\\documentclass" in text or "\\begin{document}" in text


def _write_main_tex(content: bytes, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "main.tex").write_bytes(content)
    return ["main.tex"]


def save_source(data: bytes, out_dir: Path) -> tuple[str, str, list[str]]:
    """Phân tích nội dung tải về và lưu file TeX/BibTeX.

    Trả về (status, reason, files):
      status = "ok"    -> có ít nhất một file .tex
      status = "empty" -> không có TeX (bản ghi rỗng); reason cho biết lý do
    """
    # Trường hợp 1: chỉ có PDF (đầu file là "%PDF")
    if data[:4] == b"%PDF":
        return "empty", "pdf_only", []

    # Trường hợp 2: file nén tar (.tar.gz) - phổ biến nhất
    found: dict[str, bytes] | None
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            found = _collect_tex_bib(tar)
    except tarfile.ReadError:
        found = None  # không phải tar, thử tiếp các trường hợp dưới
    except (EOFError, OSError, zlib.error):
        return "empty", "corrupt_archive", []  # file nén bị hỏng/cụt

    if found is not None:
        if any(name.lower().endswith(".tex") for name in found):
            return "ok", "", _write_files(found, out_dir)
        return "empty", "no_tex_in_archive", []

    # Trường hợp 3: một file .gz đơn lẻ (bài chỉ có đúng 1 file .tex)
    if data[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(data)
        except (OSError, EOFError, zlib.error):
            return "empty", "corrupt_archive", []
        if _looks_like_tex(content):
            return "ok", "", _write_main_tex(content, out_dir)
        return "empty", "single_file_not_tex", []

    # Trường hợp 4: file TeX thô, không nén
    if _looks_like_tex(data):
        return "ok", "", _write_main_tex(data, out_dir)

    return "empty", "unknown_format", []


# ----------------------------------------------------------------------------
# 3. XỬ LÝ MỘT VERSION (kèm checkpoint)
# ----------------------------------------------------------------------------
def process_version(session: requests.Session, arxiv_id: str, version: int) -> str:
    """Xử lý một version. Trả về "ok", "empty" hoặc "skipped".
    Ném DownloadError nếu tải thất bại."""
    out_dir = version_dir(arxiv_id, version)
    status_file = out_dir / "source_status.json"

    if status_file.exists():  # đã làm rồi -> bỏ qua (checkpoint)
        return "skipped"

    data = fetch_source(session, arxiv_id, version)
    status, reason, files = save_source(data, out_dir)

    record = {
        "arxiv_id": arxiv_id,
        "version": version,
        "status": status,        # "ok" hoặc "empty"
        "reason": reason,        # lý do nếu rỗng
        "files": files,          # các file .tex/.bib đã lưu
        "source_url": f"{BASE_URL}/{arxiv_id}v{version}",
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    # Ghi status SAU CÙNG: có file này nghĩa là version đã xong hoàn toàn.
    status_file.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return status


# ----------------------------------------------------------------------------
# 4. ĐỌC DANH SÁCH ID, DANH SÁCH VERSION VÀ CHẠY
# ----------------------------------------------------------------------------
def normalize_id(raw: str) -> str:
    """'arXiv:2310.12345v2' -> '2310.12345' (bỏ tiền tố và đuôi version)."""
    arxiv_id = raw.strip()
    arxiv_id = re.sub(r"^arxiv:", "", arxiv_id, flags=re.IGNORECASE)
    return re.sub(r"v\d+$", "", arxiv_id)


def load_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = list(args.ids or [])
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    cleaned = [normalize_id(x) for x in ids if x.strip()]
    return list(dict.fromkeys(cleaned))  # bỏ trùng, giữ thứ tự


def read_versions(arxiv_id: str, fallback_n: int | None) -> list[int]:
    """Đọc danh sách version từ versions.json (do P3 tạo).
    Nếu chưa có thì dùng --versions N (1..N)."""
    f = paper_dir(arxiv_id) / "versions.json"
    if f.exists():
        info = json.loads(f.read_text(encoding="utf-8"))
        return [int(v) for v in info["versions"]]
    if fallback_n:
        return list(range(1, fallback_n + 1))
    return []


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tải source LaTeX từ arXiv (M1).")
    p.add_argument("--ids", nargs="*", help="Một hoặc nhiều arXiv ID")
    p.add_argument("--ids-file", help="File text, mỗi dòng một arXiv ID")
    p.add_argument("--versions", type=int, default=None,
                   help="Số version (dùng khi chưa có versions.json)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ids = load_ids(args)
    if not ids:
        print("Chưa có arXiv ID nào. Dùng --ids hoặc --ids-file.")
        return
    if "example.com" in CONTACT_EMAIL:
        print("CẢNH BÁO: hãy sửa CONTACT_EMAIL ở đầu file thành email thật.")

    session = requests.Session()  # 1 session = 1 kết nối, đúng yêu cầu
    session.headers.update({"User-Agent": USER_AGENT})

    counts = {"ok": 0, "empty": 0, "skipped": 0, "failed": 0}
    failed: list[str] = []

    for i, arxiv_id in enumerate(ids, start=1):
        versions = read_versions(arxiv_id, args.versions)
        if not versions:
            print(f"[{i}/{len(ids)}] {arxiv_id}: chưa có versions.json "
                  f"và không có --versions -> bỏ qua")
            continue
        for v in versions:
            try:
                result = process_version(session, arxiv_id, v)
            except DownloadError as err:
                counts["failed"] += 1
                failed.append(f"{arxiv_id}v{v}")
                print(f"[{i}/{len(ids)}] {arxiv_id} v{v}: THẤT BẠI ({err})")
                continue
            counts[result] += 1
            print(f"[{i}/{len(ids)}] {arxiv_id} v{v}: {result}")

    print("\n=== TỔNG KẾT ===")
    print(counts)
    if failed:
        print("Version thất bại (chạy lại lệnh để thử lại):", ", ".join(failed))


if __name__ == "__main__":
    main()
