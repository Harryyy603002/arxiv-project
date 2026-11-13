"""M2: khử trùng BibTeX giữa các version, chọn một khóa chung và đổi \\cite.

Theo đề M2: nếu nhiều khóa trích dẫn cùng chỉ một tài liệu thì chọn MỘT khóa, các khóa còn lại
được đổi tên trong \\cite{} (ở đây trả về dưới dạng bản đồ khóa cũ -> khóa chọn), và các trường
của các entry trùng được HỢP NHẤT (union) chứ không chọn một bản bỏ bản khác.

Hai entry được coi là cùng một tài liệu khi (liên kết bắc cầu):
  - trùng citation_key (cùng khóa ở các version khác nhau), hoặc
  - trùng DOI, hoặc
  - trùng tiêu đề chuẩn hóa và tác giả không mâu thuẫn (một bên thiếu tác giả, hoặc chung họ).
Entry thiếu một trường (ví dụ v3 mất year) vẫn được gộp vào nhóm nhờ tiêu đề; năm KHÔNG dùng
để loại trừ vì cùng một bài thường đổi năm giữa các version (arXiv rồi bản chính thức).

Khóa được chọn: khóa của entry thuộc version MỚI NHẤT (đúng với văn bản cuối cùng), hòa thì
theo thứ tự chữ cái, nên kết quả ổn định giữa các lần chạy.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

try:  # chạy như package (from parser.m2_bib_dedup ...) hoặc như script trong thư mục parser/
    from .m2_bibitem import split_authors
except ImportError:  # pragma: no cover
    from m2_bibitem import split_authors

_SKIP_FIELDS = {"citation_key", "arxiv_id", "version"}


# ----------------------------------------------------------------------------- chuẩn hóa
def _norm_title(title) -> str:
    text = re.sub(r"\\[A-Za-z]+\*?", " ", str(title or ""))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.lower()).split())


def _norm_doi(doi) -> str:
    text = str(doi or "").strip().lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)


def _last_names(raw) -> set[str]:
    names = set()
    for author in split_authors(raw):
        author = unicodedata.normalize("NFKD", author)
        author = "".join(c for c in author if not unicodedata.combining(c))
        last = author.split(",")[0] if "," in author else (author.split() or [""])[-1]
        last = re.sub(r"[^0-9a-z]", "", last.lower())
        if last:
            names.add(last)
    return names


def _authors_compatible(a, b) -> bool:
    la, lb = _last_names(a), _last_names(b)
    return not la or not lb or bool(la & lb)


def _author_field(entry: dict):
    return entry.get("author", entry.get("authors", ""))


# ----------------------------------------------------------------------------- gộp trường
def _merge_value(current: str | None, incoming: str | None) -> tuple[str | None, bool]:
    if current:
        return current, bool(incoming and incoming != current)
    return incoming or None, False


def _same_value(field: str, a, b) -> bool:
    """Khác nhau chỉ về hoa/thường, dấu câu hay cách viết tên tác giả thì không tính là xung đột."""
    if field == "title":
        return _norm_title(a) == _norm_title(b)
    if field in {"author", "authors"}:
        return _last_names(a) == _last_names(b)
    if field == "doi":
        return _norm_doi(a) == _norm_doi(b)
    return str(a).strip().lower() == str(b).strip().lower()


# ----------------------------------------------------------------------------- khử trùng
def _clusters(entries: list[dict]) -> list[list[int]]:
    """Nhóm các entry cùng một tài liệu bằng union-find (liên kết bắc cầu)."""
    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    first_by_key, first_by_doi = {}, {}
    by_title = defaultdict(list)
    for index, entry in enumerate(entries):
        key = entry["citation_key"]
        if key in first_by_key:
            union(index, first_by_key[key])
        first_by_key.setdefault(key, index)
        doi = _norm_doi(entry.get("doi"))
        if doi:
            if doi in first_by_doi:
                union(index, first_by_doi[doi])
            first_by_doi.setdefault(doi, index)
        title = _norm_title(entry.get("title"))
        if title:
            by_title[title].append(index)
    for indices in by_title.values():
        for pos, i in enumerate(indices):
            for j in indices[pos + 1:]:
                if find(i) != find(j) and _authors_compatible(_author_field(entries[i]),
                                                              _author_field(entries[j])):
                    union(i, j)

    groups = defaultdict(list)
    for index in range(len(entries)):
        groups[find(index)].append(index)
    return list(groups.values())


def deduplicate_keys(entries: list[dict]) -> tuple[list[dict], dict[str, str], list[dict]]:
    """Khử trùng entry (trong một version và giữa các version của một bài).

    Trả về (entries chuẩn, replacements {khóa_bị_bỏ: khóa_chọn}, conflicts).
    Mỗi entry chuẩn có thêm "source_versions" (các version có entry này).
    """
    canonical_entries, replacements, conflicts = [], {}, []
    for indices in _clusters(entries):
        group = [entries[i] for i in indices]
        # mới nhất trước (version giảm dần), hòa thì theo khóa
        group.sort(key=lambda item: (-(item.get("version") or 0), item["citation_key"]))
        canonical = group[0]["citation_key"]
        merged: dict = {}
        for entry in group:
            for field, value in entry.items():
                if field in {"version"} or not value:
                    continue
                if field == "citation_key":
                    continue
                chosen, conflict = _merge_value(merged.get(field), value)
                merged[field] = chosen
                if conflict and not _same_value(field, chosen, value):
                    conflicts.append({"citation_key": canonical, "field": field,
                                      "kept": chosen, "discarded": value})
        merged["citation_key"] = canonical
        merged["source_versions"] = sorted({e["version"] for e in group if e.get("version") is not None})
        for entry in group:
            if entry["citation_key"] != canonical:
                replacements[entry["citation_key"]] = canonical
        canonical_entries.append(merged)
    canonical_entries.sort(key=lambda e: e["citation_key"])
    return canonical_entries, dict(sorted(replacements.items())), conflicts


# ----------------------------------------------------------------------------- dòng cho bảng bibtex
def _clean_title(title) -> str | None:
    text = re.sub(r"\\[A-Za-z]+\*?\s*", "", str(title or ""))
    text = re.sub(r"[{}]", "", text)
    return " ".join(text.split()) or None


def _year(value) -> int | None:
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else None


def to_bibtex_row(entry: dict) -> dict:
    """Entry chuẩn -> dòng của bảng bibtex (đúng cột BIBTEX_SCHEMA).

    Giữ nguyên các trường gốc (ví dụ "author" dạng chuỗi) để bước ghép M3 dùng được; khi xuất
    Parquet chỉ các cột của schema được ghi. `journal` lấy từ journal, không có thì booktitle.
    """
    authors = split_authors(_author_field(entry))
    eprint = str(entry.get("eprint") or "").strip()
    url = entry.get("url") or (f"https://arxiv.org/abs/{eprint}" if eprint else None)
    doi = _norm_doi(entry.get("doi")) or None
    row = {k: v for k, v in entry.items() if k != "version"}
    row.update({
        "arxiv_id": entry.get("arxiv_id"),
        "citation_key": entry["citation_key"],
        "entry_type": (entry.get("entry_type") or "misc").lower(),
        "title": _clean_title(entry.get("title")),
        "authors": authors or None,
        "year": _year(entry.get("year")),
        "doi": doi,
        "journal": entry.get("journal") or entry.get("booktitle") or None,
        "url": url,
    })
    return row


# ----------------------------------------------------------------------------- viết lại \cite
_CITE_RE = re.compile(r"(\\(?:no)?cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*){0,2})\{([^{}]*)\}")


def _split_comment(line: str) -> tuple[str, str]:
    """Tách một dòng thành (phần đang hoạt động, phần comment bắt đầu từ % không escape)."""
    match = re.search(r"(?<!\\)%", line)
    return (line[:match.start()], line[match.start():]) if match else (line, "")


def rewrite_citations_with_count(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    """Đổi khóa trong các lệnh \\cite* đang hoạt động; trả về (văn bản mới, số khóa đã đổi).

    Phần sau dấu % (comment) giữ nguyên, kể cả comment ở cuối dòng. Hỗ trợ \\cite[tùy chọn]{khóa}.
    """
    lines = []
    rewritten_count = 0
    for line in text.splitlines(keepends=True):
        active, comment = _split_comment(line)

        def replace(match: re.Match[str]) -> str:
            nonlocal rewritten_count
            keys = [key.strip() for key in match.group(2).split(",")]
            rewritten_count += sum(key in replacements for key in keys)
            new_keys = []
            for key in keys:
                key = replacements.get(key, key)
                if key and key not in new_keys:
                    new_keys.append(key)
            return f"{match.group(1)}{{{','.join(new_keys)}}}"

        lines.append(_CITE_RE.sub(replace, active) + comment)
    return "".join(lines), rewritten_count
