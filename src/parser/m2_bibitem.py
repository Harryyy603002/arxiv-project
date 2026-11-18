"""Parse BibTeX entries from source archives into stable dictionaries."""

from __future__ import annotations

import re
from typing import Iterable

_ENTRY_START = re.compile(r"@(?P<entry_type>[A-Za-z]+)\s*[{(]\s*(?P<key>[^,\s]+)\s*,")
_ENTRY_OPEN = re.compile(r"@[A-Za-z]+\s*[{(]")
_NEXT_ENTRY_LINE = re.compile(r"^[ \t]*@[A-Za-z]+\s*[{(]", re.M)
_FIELD_START = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9_-]*)\s*=\s*")
_BRACE_TOKEN = re.compile(r"\\.|[{}]", re.S)          # ký tự escape (\\", \\{...) được bỏ qua
_QUOTE_TOKEN = re.compile(r"\\.|[{}\"]", re.S)
_WS = re.compile(r"\s*")


def _balanced_entries(text: str) -> Iterable[str]:
    """Tách từng entry @type{...} trong MỘT lượt quét (tuyến tính theo độ dài văn bản).

    Chỉ đếm ngoặc nhọn (dấu " bên trong {...} không có nghĩa, và \\" là dấu thanh LaTeX chứ không
    phải ký tự mở chuỗi). Entry mất cân bằng ngoặc không được phép "nuốt" phần còn lại của file:
    nó bị cắt tại dòng bắt đầu bằng @type{ kế tiếp. Quét tiếp từ cuối entry, không quét lại từ đầu.
    """
    pos, n = 0, len(text)
    while True:
        match = _ENTRY_OPEN.search(text, pos)
        if not match:
            return
        opening = text[match.end() - 1]
        nxt = _NEXT_ENTRY_LINE.search(text, match.end())
        limit = nxt.start() if nxt else n
        depth = 1 if opening == "{" else 0      # entry kiểu @x( ... ) kết thúc ở ")" ngoài mọi {}
        end = None
        for token in _BRACE_TOKEN.finditer(text, match.end(), limit):
            char = token.group(0)
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0 and opening == "{":
                    end = token.end()
                    break
        if opening == "(" and end is None:
            close = text.find(")", match.end(), limit)
            end = close + 1 if close != -1 else None
        if end is not None:
            yield text[match.start():end]
            pos = end
        else:                                    # entry hỏng: lấy đến trước entry kế, tự đóng ngoặc
            yield text[match.start():limit].rstrip() + ("}" if opening == "{" else ")")
            pos = max(limit, match.end())


def _value_end(text: str, start: int) -> int:
    """Vị trí kết thúc của giá trị bắt đầu tại start (hỗ trợ {..}, "..", số/macro và nối bằng #)."""
    n = len(text)
    pos = start
    while pos < n:
        pos = _WS.match(text, pos).end()
        if pos >= n:
            return n
        if text[pos] == "{":
            depth = 0
            for token in _BRACE_TOKEN.finditer(text, pos):
                char = token.group(0)
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        pos = token.end()
                        break
            else:
                return n
        elif text[pos] == '"':
            depth = 0
            for token in _QUOTE_TOKEN.finditer(text, pos + 1):
                char = token.group(0)
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                elif char == '"' and depth <= 0:
                    pos = token.end()
                    break
            else:
                return n
        else:                                    # giá trị trần: 2020, jan, macro
            comma = text.find(",", pos)
            return comma if comma != -1 else n
        after = _WS.match(text, pos).end()
        if after < n and text[after] == "#":     # "a" # {b}: nối tiếp
            pos = after + 1
            continue
        return pos
    return n


def _clean_value(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    if len(value) >= 2 and (
        (value[0] == "{" and value[-1] == "}")
        or (value[0] == '"' and value[-1] == '"')
    ):
        value = value[1:-1]
    return re.sub(r"\s+", " ", value).strip()


def parse_bibtex_entries(text: str, arxiv_id: str | None = None,
                         version: int | None = None) -> list[dict]:
    """Parse entries while preserving source and version provenance (độ phức tạp tuyến tính)."""
    entries = []
    for raw in _balanced_entries(text):
        header = _ENTRY_START.search(raw)
        if not header:
            continue
        fields = {}
        body = raw[:-1]
        pos = header.end()
        while pos < len(body):                   # đi tuần tự: mỗi trường bắt đầu SAU giá trị trước
            field = _FIELD_START.search(body, pos)
            if not field:
                break
            value_end = _value_end(body, field.end())
            fields[field.group("name").lower()] = _clean_value(body[field.end():value_end])
            pos = max(value_end, field.end())
        entry = {
            "citation_key": header.group("key").strip(),
            "entry_type": header.group("entry_type").lower(),
            **fields,
        }
        if arxiv_id is not None:
            entry["arxiv_id"] = arxiv_id
        if version is not None:
            entry["version"] = version
        entries.append(entry)
    return entries


# ----------------------------------------------------------------------------
# \bibitem -> BibTeX (đề M2: chuyển \bibitem trong .tex thành entry BibTeX chuẩn bằng regex)
# ----------------------------------------------------------------------------
_THEBIB_RE = re.compile(
    r"\\begin\s*\{thebibliography\}\s*(?:\{[^{}]*\})?(.*?)\\end\s*\{thebibliography\}", re.S)
_BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[(.*?)\])?\s*\{([^{}]+)\}", re.S)
_ARXIV_RE = re.compile(r"arxiv\s*:?\s*((?:\d{4}\.\d{4,5})|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?", re.I)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;{}\\]+)")
_URL_RE = re.compile(r"\\(?:url|href)\s*\{([^{}]+)\}|(https?://[^\s,{}\\]+)")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})[a-z]?(?!\d)")
_ACCENT_RE = re.compile(r"\\[`'\"^~=.]\s*\{?([A-Za-z])\}?")
_INITIALS_RE = re.compile(r"^(?:[A-Z]\.?\s*-?){1,3}$")
_QUOTED_RE = re.compile(r"(?:``|“|\")(.+?)(?:''|”|\")", re.S)
_SENT_BOUNDARY = re.compile(r"(?<![A-Z])\.\s+(?=[A-Z0-9(\[“\"`]|arXiv)")
_PROC_RE = re.compile(r"\b(proceedings|proc\.|conference|workshop|symposium|in:)|^\s*in\b", re.I)
_JOURNAL_RE = re.compile(r"\b(journal|transactions|trans\.|letters|review|rev\.|magazine|annals)\b", re.I)


def _strip_latex(text: str) -> str:
    """Bỏ lệnh định dạng/dấu thanh, giữ chữ cái; gọn khoảng trắng."""
    text = _ACCENT_RE.sub(r"\1", text)
    text = re.sub(r"\\(?:emph|textit|textbf|textsc|textsl|textrm|mbox|url)\s*", "", text)
    text = re.sub(r"\\(?:bf|it|em|sc|rm)(?![A-Za-z])\s*", "", text)
    text = text.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("~", " ")
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def split_authors(raw) -> list[str]:
    """Chuỗi tác giả (BibTeX "A and B" hoặc "A, B, and C") hoặc list -> list từng tác giả.
    Phần "Last, F." được giữ nguyên là một tác giả; "others"/"et al." bị bỏ."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(a).strip() for a in raw]
    else:
        text = _strip_latex(str(raw))
        if re.search(r"\band\b", text) and text.count(",") <= text.count(" and ") + 1 and " and " in text \
                and not re.search(r"\b\w+,\s+and\b", text):
            parts = [p.strip() for p in re.split(r"\s+and\s+", text)]  # kiểu BibTeX
        else:
            tokens = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", text) if p.strip()]
            parts = []
            for token in tokens:
                if parts and _INITIALS_RE.match(token):  # "Vaswani, A." -> một tác giả
                    parts[-1] = f"{parts[-1]}, {token}"
                else:
                    parts.append(token)
    return [p for p in parts if p and p.lower().rstrip(".") not in {"others", "et al"}]


def _guess_type(venue: str, has_arxiv: bool) -> str:
    if _PROC_RE.search(venue):
        return "inproceedings"
    if _JOURNAL_RE.search(venue):
        return "article"
    return "misc" if has_arxiv or not venue else "misc"


def _bibitem_fields(raw: str) -> dict:
    """Rút title/author/year/doi/url/venue từ nội dung một \\bibitem (heuristic, kèm raw_text)."""
    fields: dict = {}
    doi = _DOI_RE.search(raw)
    arxiv = _ARXIV_RE.search(raw)
    url = next((g1 or g2 for g1, g2 in _URL_RE.findall(raw)), None)
    if doi:
        fields["doi"] = doi.group(1).rstrip(".")
    if url and "doi.org/10." not in url:
        fields["url"] = url.rstrip(".")
    elif arxiv:
        fields["url"] = f"https://arxiv.org/abs/{arxiv.group(1)}"

    body = _ARXIV_RE.sub(" ", _DOI_RE.sub(" ", _URL_RE.sub(" ", raw)))
    blocks = [_strip_latex(b).strip(" .,;") for b in re.split(r"\\newblock", body)]
    blocks = [b for b in blocks if b]
    year = _YEAR_RE.findall(" ".join(blocks))
    if year:
        fields["year"] = year[-1]

    authors = title = ""
    venue_parts: list[str] = []
    if len(blocks) >= 2:                                        # kiểu bst chuẩn: Authors \newblock Title \newblock Venue
        authors, title, venue_parts = blocks[0], blocks[1], blocks[2:]
    elif blocks:
        text = blocks[0]
        quoted = _QUOTED_RE.search(_strip_keep_quotes(body))
        if quoted:                                              # IEEE: Authors, "Title," Venue, year.
            title = _strip_latex(quoted.group(1)).strip(" ,.")
            authors = _strip_latex(_strip_keep_quotes(body)[:quoted.start()]).strip(" ,.")
            venue_parts = [_strip_latex(_strip_keep_quotes(body)[quoted.end():]).strip(" ,.")]
        else:
            segments = [s.strip(" .,;") for s in _SENT_BOUNDARY.split(text) if s.strip(" .,;")]
            if len(segments) >= 3 and re.fullmatch(r"\(?(?:19|20)\d{2}[a-z]?\)?", segments[1]):
                authors, title, venue_parts = segments[0], segments[2], segments[3:]
            elif len(segments) >= 2:
                authors, title, venue_parts = segments[0], segments[1], segments[2:]
            else:
                title = text
    venue = ", ".join(v for v in venue_parts if v)
    if title:
        fields["title"] = title
    if authors:
        fields["author"] = " and ".join(split_authors(authors))
    entry_type = _guess_type(venue, bool(arxiv))
    fields["entry_type"] = entry_type
    if venue:
        fields["booktitle" if entry_type == "inproceedings" else "journal"] = venue
    return fields


def _strip_keep_quotes(text: str) -> str:
    """Như _strip_latex nhưng giữ dấu trích để tìm tiêu đề trong ngoặc kép."""
    text = _ACCENT_RE.sub(r"\1", text)
    text = re.sub(r"\\(?:emph|textit|textbf|textsc|url)\s*", "", text)
    text = re.sub(r"\\[A-Za-z]+\*?", " ", text.replace("~", " ").replace("\\&", "&"))
    return re.sub(r"\s+", " ", re.sub(r"[{}]", "", text)).strip()


def parse_bibitems(text: str, arxiv_id: str | None = None, version: int | None = None) -> list[dict]:
    r"""Chuyển các \bibitem trong thebibliography thành entry BibTeX (cùng dạng với parse_bibtex_entries).

    Mỗi entry có citation_key, entry_type, title, author ("A and B"), year, doi, url, journal/booktitle
    (nếu rút được), cộng raw_text (nội dung \bibitem gốc, hữu ích cho bước ghép ở M3) và source="bibitem".
    Việc tách tác giả/tiêu đề bằng quy tắc nên không hoàn hảo với mọi kiểu trích dẫn.
    """
    entries = []
    for block in _THEBIB_RE.findall(text or ""):
        matches = list(_BIBITEM_RE.finditer(block))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
            raw = block[m.end():end].strip()
            entry = {"citation_key": m.group(2).strip(), **_bibitem_fields(raw),
                     "raw_text": _strip_latex(raw), "source": "bibitem"}
            entry.setdefault("entry_type", "misc")
            if arxiv_id is not None:
                entry["arxiv_id"] = arxiv_id
            if version is not None:
                entry["version"] = version
            entries.append(entry)
    return entries