r"""m1_download.py

Milestone: M1 - Crawling
Mục đích: tải mã nguồn LaTeX của một version bài báo từ arXiv và chỉ giữ file .tex/.bib.
          Version không có TeX (chỉ có PDF...) được trả về trạng thái "empty" kèm lý do,
          để run_m1.py ghi thành bản ghi rỗng thay vì bỏ qua.

Module này là thư viện cho src/run_m1.py (pipeline chính), gồm:
  - fetch_source(session, arxiv_id, version): tải archive, có chờ lịch sự và thử lại.
  - save_source_with_stats(data, out_dir): phân loại nội dung tải về, lưu .tex/.bib,
    trả về (status, reason, files, archive_bytes, bytes_before, bytes_after).

Quy tắc với arXiv: chỉ dùng export.arxiv.org (arxiv.org sẽ bị chặn 403), một kết nối,
khoảng cách >= 3 giây giữa hai request, thử lại có chờ lâu dần khi lỗi mạng/429/5xx.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import time
import zlib
from pathlib import Path, PurePosixPath

import requests

# ----------------------------------------------------------------------------
# CẤU HÌNH
# ----------------------------------------------------------------------------
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
        # Ghi mốc ngay TRƯỚC khi gửi: khoảng cách >= MIN_DELAY_SECONDS được tính từ
        # lúc bắt đầu request này đến lúc bắt đầu request kế tiếp, nên một lần tải
        # chậm không bị cộng thêm thời gian chờ thừa.
        _touch()
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as err:
            last_problem = f"lỗi mạng: {err}"
        else:
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


def _collect_tex_bib(tar: tarfile.TarFile) -> tuple[dict[str, bytes], int]:
    """Đọc CHỈ các file .tex/.bib từ file tar vào bộ nhớ (chưa ghi ra đĩa).
    Giữ nguyên đường dẫn con (vì \\input{sections/intro} cần đúng đường dẫn).
    Đọc hết rồi mới ghi để file nén hỏng không để lại file lưu dở dang."""
    found: dict[str, bytes] = {}
    before_bytes = 0
    for member in tar.getmembers():
        if not member.isfile():
            continue
        before_bytes += member.size
        rel = _safe_relative_path(member.name)
        if rel is None or rel.suffix.lower() not in KEEP_SUFFIXES:
            continue
        if member.size > MAX_FILE_BYTES:
            continue
        fileobj = tar.extractfile(member)
        if fileobj is None:
            continue
        found[rel.as_posix()] = fileobj.read()  # giữ nguyên bytes, không đổi mã hóa
    return found, before_bytes


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


def save_source_with_stats(
    data: bytes, out_dir: Path
) -> tuple[str, str, list[str], int, int, int]:
    """Phân tích nội dung tải về và lưu file TeX/BibTeX vào out_dir.

    Trả về (status, reason, files, archive_bytes, bytes_before, bytes_after):
      status = "ok"    -> có ít nhất một file .tex
      status = "empty" -> không có TeX; reason là một trong: pdf_only, corrupt_archive,
                          no_tex_in_archive, single_file_not_tex, unknown_format
      bytes_before = tổng kích thước mọi file trong archive (trước khi bỏ hình)
      bytes_after  = tổng kích thước .tex/.bib đã giữ (sau khi bỏ hình)
    """
    archive_bytes = len(data)

    if data[:4] == b"%PDF":
        return "empty", "pdf_only", [], archive_bytes, archive_bytes, 0

    found: dict[str, bytes] | None
    before_bytes = archive_bytes
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            found, before_bytes = _collect_tex_bib(tar)
    except tarfile.ReadError:
        found = None
    except (EOFError, OSError, zlib.error):
        return "empty", "corrupt_archive", [], archive_bytes, archive_bytes, 0

    if found is not None:
        after_bytes = sum(len(content) for content in found.values())
        if any(name.lower().endswith(".tex") for name in found):
            files = _write_files(found, out_dir)
            return "ok", "", files, archive_bytes, before_bytes, after_bytes
        return "empty", "no_tex_in_archive", [], archive_bytes, before_bytes, 0

    if data[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(data)
        except (OSError, EOFError, zlib.error):
            return "empty", "corrupt_archive", [], archive_bytes, archive_bytes, 0
        if _looks_like_tex(content):
            files = _write_main_tex(content, out_dir)
            return "ok", "", files, archive_bytes, len(content), len(content)
        return "empty", "single_file_not_tex", [], archive_bytes, len(content), 0

    if _looks_like_tex(data):
        files = _write_main_tex(data, out_dir)
        return "ok", "", files, archive_bytes, archive_bytes, archive_bytes

    return "empty", "unknown_format", [], archive_bytes, archive_bytes, 0
