"""Cấu hình dùng chung (tuần 1 / M1).

- paper_key = arXiv ID với "." và "/" đổi thành "-" (2310.12345 -> 2310-12345).
  Chỉ dùng paper_key để đặt tên thư mục; trong file dữ liệu luôn ghi arXiv ID gốc.
- Mọi đường dẫn phải tạo qua các hàm trong file này.

Biến môi trường (tùy chọn): ARXIV_DATA_DIR, S2_API_KEY, CONTACT_EMAIL.
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

# Đảm bảo stdout/stderr luôn dùng UTF-8 trên mọi nền tảng (đặc biệt Windows cp1252)
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, io.UnsupportedOperation):
            pass

# --- Tự động nạp biến môi trường từ file .env nếu có ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
# ---------------------------------------------------------------- thư mục
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]  # src/utils/config.py
DATA_DIR: Path = Path(os.environ.get("ARXIV_DATA_DIR", PROJECT_ROOT / "data")).expanduser()
LOGS_DIR: Path = DATA_DIR / "logs"

# ---------------------------------------------------------------- arXiv
ARXIV_SRC_URL = "https://export.arxiv.org/src/{id_with_version}"  # chỉ dùng mirror export.
ARXIV_DELAY_SECONDS = 3.0   # tối thiểu 3s giữa các request
ARXIV_API_BATCH = 100       # số ID mỗi lần gọi id_list
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "your-email@example.com")
USER_AGENT = f"arxiv-course-project/1.0 (student crawler; contact: {CONTACT_EMAIL})"
HTTP_HEADERS = {"User-Agent": USER_AGENT}

KEEP_EXTENSIONS: Tuple[str, ...] = (".tex", ".bib")  # chỉ giữ 2 loại, bỏ hình/khác

# ---------------------------------------------------------------- Semantic Scholar
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
S2_REF_FIELDS = ",".join(  # truyền phẳng, không dùng authors.name
    [
        "references.paperId",
        "references.title",
        "references.authors",
        "references.year",
        "references.publicationDate",
        "references.venue",
        "references.externalIds",
    ]
)
S2_API_KEY: Optional[str] = (os.environ.get("S2_API_KEY") or "").strip() or None
if S2_API_KEY is None:  # Colab Secrets
    try:
        from google.colab import userdata  # type: ignore

        S2_API_KEY = (userdata.get("S2_API_KEY") or "").strip() or None
    except Exception:
        pass
S2_DELAY_SECONDS = 1.1 if S2_API_KEY else 3.2  # có key: 1 req/s; không key: ~100 req/5 phút


def s2_headers() -> Dict[str, str]:
    h = dict(HTTP_HEADERS)
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


# ---------------------------------------------------------------- xử lý ID
_VERSION_RE = re.compile(r"^(?P<base>.+?)(?:v(?P<ver>\d+))?$")
_NEW_ID_RE = re.compile(r"^(?P<yymm>\d{4})\.(?P<num>\d{4,5})$")


def split_version(arxiv_id: str) -> Tuple[str, Optional[int]]:
    """'2310.12345v2' -> ('2310.12345', 2); '2310.12345' -> ('2310.12345', None)."""
    m = _VERSION_RE.match(arxiv_id.strip())
    ver = m.group("ver")
    return m.group("base"), (int(ver) if ver else None)


def normalize_arxiv_id(arxiv_id: str | None) -> str | None:
    """Return a base arXiv identifier without URL, namespace, or version."""
    if not arxiv_id:
        return None
    value = str(arxiv_id).strip()
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value,
                   flags=re.IGNORECASE)
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.IGNORECASE)
    value = value.removesuffix(".pdf").strip()
    return split_version(value)[0] or None


def paper_key(arxiv_id: str) -> str:
    return split_version(arxiv_id)[0].replace("/", "-").replace(".", "-")


def parse_id_range(spec: str) -> Tuple[str, str]:
    """'2602.04870-2602.09869' -> ('2602.04870', '2602.09869')."""
    start, end = spec.strip().split("-")
    return start, end


def parse_month_last(items) -> Dict[str, int]:
    """['2512=25000'] hoặc ['2512=2512.25000'] -> {'2512': 25000} (cho --month-last)."""
    out: Dict[str, int] = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k.strip()] = int(v.strip().split(".")[-1])
    return out


def iter_id_range(start: str, end: str, month_last: Optional[Dict[str, int]] = None) -> Iterator[str]:
    """Sinh ID YYMM.NNNNN từ start đến end (gồm hai đầu).

    Khoảng qua nhiều tháng cần month_last = {yymm: số cuối của tháng đó} cho mọi tháng trừ tháng cuối.
    """
    ms, me = _NEW_ID_RE.match(start), _NEW_ID_RE.match(end)
    if not ms or not me:
        raise ValueError(f"ID phải dạng YYMM.NNNNN: {start!r}, {end!r}")
    month_last = month_last or {}
    y, m = int(ms["yymm"][:2]), int(ms["yymm"][2:])
    first = True
    while True:
        yymm = f"{y:02d}{m:02d}"
        lo = int(ms["num"]) if first else 1
        if yymm == me["yymm"]:
            hi = int(me["num"])
        elif yymm in month_last:
            hi = month_last[yymm]
        else:
            raise ValueError(f"Thiếu ID cuối của tháng {yymm} (dùng --month-last {yymm}=<số>)")
        for n in range(lo, hi + 1):
            yield f"{yymm}.{n:05d}"
        if yymm == me["yymm"]:
            return
        first = False
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ---------------------------------------------------------------- đường dẫn
def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def papers_csv_path() -> Path:
    return DATA_DIR / "papers.csv"


def papers_parquet_path() -> Path:
    return DATA_DIR / "papers.parquet"


def failed_ids_path() -> Path:
    return DATA_DIR / "failed_ids.txt"


def checkpoint_path(name: str) -> Path:
    """File checkpoint để chạy tiếp khi bị ngắt, vd. checkpoint_path('m1_crawl')."""
    return LOGS_DIR / f"{name}.checkpoint.json"


def paper_dir(arxiv_id: str) -> Path:
    return DATA_DIR / paper_key(arxiv_id)


def versions_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "versions.json"


def meta_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "meta.json"


def refs_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "refs.json"


def raw_refs_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "refs_raw.json"


def refs_failures_path() -> Path:
    return DATA_DIR / "refs_failures.jsonl"


def references_table_path() -> Path:
    return DATA_DIR / "references.jsonl"


def references_csv_path() -> Path:
    return DATA_DIR / "references.csv"


def version_dir(arxiv_id: str, version) -> Path:
    """version: 'v2' hoặc 2."""
    label = version if isinstance(version, str) else f"v{int(version)}"
    return paper_dir(arxiv_id) / label


# ---------------------------------------------------------------- đường dẫn M2 (P3)
def tree_raw_path(arxiv_id: str, version) -> Path:
    return version_dir(arxiv_id, version) / "tree_raw.json"


def cite_map_path(arxiv_id: str) -> Path:
    """{khóa_cũ: khóa_đã_chọn} do Tri xuất sau khi gộp BibTeX (tùy chọn)."""
    return paper_dir(arxiv_id) / "cite_map.json"


def elements_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "elements.json"


def hierarchy_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "hierarchy.json"


def parse_status_path(arxiv_id: str) -> Path:
    return paper_dir(arxiv_id) / "parse_status.json"


# ---------------------------------------------------------------- M3: member ranges & artifact paths
# Shared by M1/M2/M3.
MEMBERS = ["Quoc", "Nguyen", "Khai", "Tri"]

MEMBER_RANGES: Dict[str, Tuple[str, str]] = {
    "Quoc": ("2512.23231", "2601.03155"),
    "Nguyen": ("2602.04870", "2602.09869"),
    "Khai": ("2603.00580", "2603.05579"),
    "Tri": ("2606.20734", "2606.25733"),
}

# Backward compatibility with the original M3 implementation.
M3_DIR: Path = DATA_DIR / "m3"

_RANGE_DOTTED_RE = re.compile(
    r"^(\d{4}\.\d{4,5})\s*-\s*(\d{4}\.\d{4,5})$"
)
_RANGE_COMPACT_RE = re.compile(
    r"^(\d{4})(\d{4,5})\s*-\s*(\d{4})(\d{4,5})$"
)


def _parse_range(raw: str) -> Optional[Tuple[str, str]]:
    """Parse dotted or compact ID ranges."""
    value = str(raw).strip()

    match = _RANGE_DOTTED_RE.fullmatch(value)
    if match:
        return match.group(1), match.group(2)

    match = _RANGE_COMPACT_RE.fullmatch(value)
    if match:
        return (
            f"{match.group(1)}.{match.group(2)}",
            f"{match.group(3)}.{match.group(4)}",
        )

    return None


def id_key(arxiv_id: str) -> Tuple[int, int]:
    """Convert an arXiv ID into a sortable (YYMM, numeric suffix) pair."""
    base = normalize_arxiv_id(arxiv_id)

    if base is None:
        raise ValueError(f"arXiv ID không hợp lệ: {arxiv_id!r}")

    # Support compact IDs such as 260204870.
    if re.fullmatch(r"\d{8,9}", base):
        base = f"{base[:4]}.{base[4:]}"

    match = _NEW_ID_RE.fullmatch(base)
    if not match:
        raise ValueError(
            f"ID không phải dạng YYMM.NNNN[N]: {arxiv_id!r}"
        )

    return int(match["yymm"]), int(match["num"])


def in_range(arxiv_id: str, member: str) -> bool:
    """Check whether an arXiv ID belongs to a member's inclusive range."""
    if member not in MEMBER_RANGES:
        raise KeyError(
            f"Thành viên không hợp lệ: {member!r}. "
            f"Chọn trong {MEMBERS}"
        )

    try:
        key = id_key(arxiv_id)
    except (TypeError, ValueError):
        return False

    start, end = MEMBER_RANGES[member]
    return id_key(start) <= key <= id_key(end)


def member_of(arxiv_id: str) -> Optional[str]:
    """Return the member responsible for an arXiv ID."""
    for member in MEMBERS:
        if in_range(arxiv_id, member):
            return member
    return None


def m3_dir(data_dir: Optional[Path] = None) -> Path:
    """Return M3 artifact directory."""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    return root / "m3"


def m3_labels_dir(data_dir: Optional[Path] = None) -> Path:
    """Return the M3 labels directory."""
    return m3_dir(data_dir) / "labels"


def labels_path(
    arxiv_id: str,
    data_dir: Optional[Path] = None
) -> Path:
    return (
        m3_labels_dir(data_dir)
        / f"{paper_key(arxiv_id)}.json"
    )


def str_features_path(
    arxiv_id: str,
    data_dir: Optional[Path] = None
) -> Path:
    return (
        m3_dir(data_dir)
        / "features"
        / f"{paper_key(arxiv_id)}.str.parquet"
    )


def features_path(
    arxiv_id: str,
    data_dir: Optional[Path] = None
) -> Path:
    return (
        m3_dir(data_dir)
        / "features"
        / f"{paper_key(arxiv_id)}.parquet"
    )


def split_path(data_dir: Optional[Path] = None) -> Path:
    return m3_dir(data_dir) / "split.json"


def scores_path(data_dir: Optional[Path] = None) -> Path:
    return m3_dir(data_dir) / "scores.parquet"


def predictions_path(data_dir: Optional[Path] = None) -> Path:
    return m3_dir(data_dir) / "predictions.parquet"


def poster_inputs_dir(data_dir: Optional[Path] = None) -> Path:
    return m3_dir(data_dir) / "poster_inputs"


if __name__ == "__main__":
    ids = list(
        iter_id_range(
            *parse_id_range("2602.04870-2602.09869")
        )
    )
    print(
        len(ids),
        ids[0],
        ids[-1],
        paper_key("hep-th/9901001"),
        version_dir("2310.12345", 2)
    )

