"""M2 - Tầng 2: Bảo vệ, phân loại, và bóc vỏ dự phòng.

Hàm chính:
    clean_text(text)            làm sạch văn bản thường (bỏ lệnh định dạng, comment, ~, \\\\ ...)
    clean_figure(text)          làm sạch môi trường figure/table (giữ cấu trúc, \\includegraphics...)
    normalize_inline_math(t)    \\( \\) , \\begin{math} -> $...$
    normalize_block_math(raw)   $$..$$, \\[..\\], equation*, align... -> \\begin{equation}...\\end{equation}
    split_segments(text)        tách đoạn văn thành [text | formula | figure] theo thứ tự

Kiến trúc tầng 2:
    1. Bảo vệ (mask): toán, \\cite{}, \\ref{}, URL, verbatim
    2. Phân loại theo bảng: DROP (có/không đối số), UNWRAP, KÝ HIỆU
    3. Bóc vỏ dự phòng: lệnh chưa biết -> xét đối số (kích thước? chữ? lạ?) -> quyết định
    4. Ghi lại lệnh lạ vào bộ đếm unknown_commands
    5. Trả lại token đã bảo vệ

Lệnh \\cite{...} và \\ref{...} luôn được giữ nguyên.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

PH_OPEN, PH_CLOSE = "\ue000", "\ue001"  # ký tự giữ chỗ cho toán inline
PH_CITE, PH_REF = "\ue002", "\ue003"  # ký tự giữ chỗ cho cite/ref
PH_URL, PH_VERB = "\ue004", "\ue005"  # ký tự giữ chỗ cho URL và verbatim

# Bộ đếm toàn cục cho lệnh lạ (có thể xuất ra file)
unknown_commands: Counter = Counter()

# ----------------------------------------------------------------------------- Bảng phân loại lệnh (Tầng 2)
# Nhóm DROP - xóa cả lệnh và đối số
DROP_WITH_ARGS = {
    # Khoảng trắng và bố cục
    "vspace": 1, "hspace": 1, "vskip": 1, "hskip": 1, "addvspace": 1,
    "setlength": 2, "addtolength": 2, "enlargethispage": 1,
    # Bảng và đường kẻ
    "cline": 1, "multicolumn": 3, "multirow": 3,
    # Định dạng và màu sắc
    "definecolor": 3, "colorbox": 2, "fcolorbox": 3,
    # Siêu dữ liệu và thiết lập
    "bibliographystyle": 1, "bibliography": 1, "hypersetup": 1, "captionsetup": 1,
    "thispagestyle": 1, "pagestyle": 1, "pagenumbering": 1,
    # Chỉ mục và ghi chú
    "index": 1, "glossary": 1, "todo": 1, "marginpar": 1, "footnotemark": 0, "footnotetext": 1,
    # Định nghĩa (đã được tầng 1 xử lý nhưng phòng trường hợp sót)
    "newcommand": 2, "renewcommand": 2, "providecommand": 2, "DeclareRobustCommand": 2,
    "newenvironment": 3, "renewenvironment": 3, "newtheorem": 2, "theoremstyle": 1,
    "newcounter": 1, "setcounter": 2, "addtocounter": 2, "stepcounter": 1,
    # ACM và metadata bài báo
    "ccsdesc": 1, "shortauthors": 1, "copyrightyear": 1, "acmYear": 1, "setcopyright": 1,
    "acmDOI": 1, "acmISBN": 1, "acmConference": 3, "acmBooktitle": 1, "acmPrice": 1,
    "keywords": 1, "terms": 1, "category": 3,
    # IEEE
    "IEEEoverridecommandlockouts": 0, "IEEEpubid": 1, "IEEEspecialpapernotice": 1,
    # Khác
    "graphicspath": 1, "fnsymbol": 1, "arabic": 1, "roman": 1, "Roman": 1, "alph": 1, "Alph": 1,
}

# Nhóm DROP không đối số - xóa lệnh
DROP_NO_ARGS = [
    "centering", "raggedright", "raggedleft", "noindent", "indent", "clearpage", "newpage",
    "pagebreak", "linebreak", "nopagebreak", "nolinebreak",
    # Đường kẻ bảng
    "midrule", "toprule", "bottomrule", "hline", "cmidrule",
    # Cỡ chữ
    "small", "footnotesize", "scriptsize", "tiny", "large", "Large", "LARGE", "huge", "Huge", "normalsize",
    # Khoảng trắng dọc
    "medskip", "smallskip", "bigskip", "vfill", "hrule", "vrule",
    # Font switches
    "bfseries", "itshape", "mdseries", "upshape", "slshape", "scshape",
    "rmfamily", "sffamily", "ttfamily", "normalfont",
    # Font kiểu cũ
    "bf", "it", "em", "sc", "rm", "sf", "tt", "sl", "md",
    # Layout
    "par", "nobreak", "allowbreak", "linebreak", "newline",
    "balance", "FloatBarrier", "afterpage", "clearpage", "cleardoublepage",
    "begingroup", "endgroup", "printbibliography",
    # Khác
    "maketitle", "tableofcontents", "listoffigures", "listoftables",
    "appendix", "frontmatter", "mainmatter", "backmatter",
    "protect", "relax", "ignorespaces", "xspace", "sloppy", "fussy", "frenchspacing",
    "hfil", "hfill", "vfil", "maketitlesupplementary",
]

# Nhóm SPACE - đổi thành khoảng trắng
SPACE_COMMANDS = [
    "quad", "qquad", "enspace", "thinspace", "space", "negspace",
]

# Nhóm UNWRAP - giữ đối số cuối
UNWRAP_COMMANDS = [
    "textbf", "textit", "emph", "textsc", "textsl", "underline", 
    "textsuperscript", "textsubscript", "textrm", "textsf", "texttt",
    "textup", "textmd", "textnormal", "uline", "sout", "dashuline",
    "mbox", "text", "textrm", "mathrm", "mathbf", "mathit", "mathsf", "mathtt",
    "mathcal", "mathbb", "mathfrak", "boldsymbol", "bm",
]

# Nhóm UNWRAP đối số thứ 2 - lệnh có 2 đối số, giữ đối số thứ 2
UNWRAP_SECOND_ARG = {
    "textcolor": 2,  # \textcolor{color}{text} -> text
    "colorbox": 2,   # \colorbox{color}{text} -> text (nếu không ở DROP)
    "href": 2,       # \href{url}{text} -> text
    "texorpdfstring": 2,  # \texorpdfstring{tex}{pdf} -> pdf (đơn giản hóa)
}

# Nhóm KÝ HIỆU - ký tự đặc biệt
SYMBOL_MAP = {
    r"\S": "§", r"\P": "¶", r"\dag": "†", r"\ddag": "‡",
    r"\ldots": "...", r"\dots": "...", r"\textellipsis": "...",
    r"\textdegree": "°", r"\texttimes": "×", r"\textpm": "±", r"\textmu": "µ",
    r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\aa": "å", r"\AA": "Å",
    r"\ae": "æ", r"\AE": "Æ", r"\oe": "œ", r"\OE": "Œ",
    r"\pounds": "£", r"\euro": "€", r"\copyright": "©", r"\registered": "®",
}

# Tạo regex patterns từ bảng
_DROP_NO_ARGS_RE = re.compile(r"\\(?:%s)(?![A-Za-z])" % "|".join(DROP_NO_ARGS))
_SPACE_CMD_RE = re.compile(r"\\(?:%s)(?![A-Za-z])" % "|".join(SPACE_COMMANDS))
_DIM_RE = re.compile(r"\\(?:linewidth|textwidth|columnwidth|textheight|paperwidth|paperheight)(?![A-Za-z])")
_UNWRAP_RE = re.compile(r"\\(?:%s)\s*\{" % "|".join(UNWRAP_COMMANDS))
_PLACEMENT_RE = re.compile(
    r"(\\begin\{(?:figure|table|wrapfigure|wraptable|sidewaysfigure|sidewaystable)\*?\})\s*\[[!htbpH]*\]"
)
_LINEBREAK_RE = re.compile(r"\\\\(?:\s*\[[^\]]*\])?")
_SPACE_SYM_RE = re.compile(r"(?<!\\)\\[,;: ]")

# Pattern cho cite và ref
_CITE_RE = re.compile(r"\\(?:cite[tpalsn]*|citeyear|citeauthor)\*?\s*(?:\[[^\]]*\])?\s*(?:\[[^\]]*\])?\s*\{[^}]*\}")
_REF_RE = re.compile(r"\\(?:ref|eqref|autoref|pageref|cref|Cref)\*?\s*\{[^}]*\}")
_LABEL_RE = re.compile(r"\\label\s*\{[^}]*\}")

# Pattern cho URL và verbatim
_URL_RE = re.compile(r"\\url\s*\{[^}]*\}|\\href\s*\{[^}]*\}\s*\{[^}]*\}")
_VERB_RE = re.compile(r"\\verb([^A-Za-z])(.*?)\1")


# ----------------------------------------------------------------------------- Tầng 2: Bảo vệ
def protect_citations_and_refs(text: str) -> Tuple[str, List[str]]:
    """Bảo vệ \\cite{...}, \\ref{...}, \\label{...} khỏi bị xử lý."""
    store: List[str] = []
    
    def replace_fn(match):
        idx = len(store)
        store.append(match.group(0))
        return f"{PH_CITE}{idx}{PH_CLOSE}"
    
    text = _CITE_RE.sub(replace_fn, text)
    text = _REF_RE.sub(replace_fn, text)
    text = _LABEL_RE.sub(replace_fn, text)
    return text, store


def protect_urls(text: str) -> Tuple[str, List[str]]:
    """Bảo vệ URL và \\url{...}."""
    store: List[str] = []
    
    def replace_fn(match):
        idx = len(store)
        store.append(match.group(0))
        return f"{PH_URL}{idx}{PH_CLOSE}"
    
    text = _URL_RE.sub(replace_fn, text)
    return text, store


def protect_verbatim(text: str) -> Tuple[str, List[str]]:
    """Bảo vệ \\verb|...|."""
    store: List[str] = []
    
    def replace_fn(match):
        idx = len(store)
        store.append(match.group(0))
        return f"{PH_VERB}{idx}{PH_CLOSE}"
    
    text = _VERB_RE.sub(replace_fn, text)
    return text, store


def restore_protected(text: str, cite_store: List[str], url_store: List[str], verb_store: List[str]) -> str:
    """Trả lại các token đã bảo vệ."""
    text = re.sub(PH_CITE + r"(\d+)" + PH_CLOSE, lambda m: cite_store[int(m.group(1))], text)
    text = re.sub(PH_URL + r"(\d+)" + PH_CLOSE, lambda m: url_store[int(m.group(1))], text)
    text = re.sub(PH_VERB + r"(\d+)" + PH_CLOSE, lambda m: verb_store[int(m.group(1))], text)
    return text


# ----------------------------------------------------------------------------- tiện ích
def _balanced_end(text: str, start: int) -> Optional[int]:
    """Chỉ số ngay sau dấu } khớp với { tại text[start]; None nếu không cân."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _skip_argument(text: str, i: int) -> int:
    """Bỏ một đối số bắt buộc tại i: {..}, một control sequence (\\thefootnote) hoặc một ký tự."""
    i = _skip_ws(text, i)
    if i >= len(text):
        return i
    if text[i] == "{":
        return _balanced_end(text, i) or len(text)
    if text[i] == "\\":
        m = re.match(r"\\(?:[A-Za-z@]+|.)", text[i:])
        return i + (m.end() if m else 1)
    return i + 1


def _skip_optionals(text: str, i: int) -> int:
    while True:
        j = _skip_ws(text, i)
        if j < len(text) and text[j] == "[":
            close = text.find("]", j)
            if close == -1:
                return i
            i = close + 1
        else:
            return i


def _extract_argument(text: str, pos: int) -> Tuple[Optional[str], int]:
    """Trích đối số {content} từ pos. Trả về (content, vị trí sau })."""
    pos = _skip_ws(text, pos)
    if pos >= len(text) or text[pos] != "{":
        return None, pos
    end = _balanced_end(text, pos)
    if end is None:
        return None, pos
    return text[pos + 1 : end - 1], end


# ----------------------------------------------------------------------------- Tầng 2: Bóc vỏ dự phòng
def is_dimension_like(arg: str) -> bool:
    """Kiểm tra xem đối số có phải là kích thước không (3pt, 2em, 0.5\\textwidth...)."""
    if not arg:
        return False
    arg = arg.strip()
    # Các đơn vị LaTeX
    if re.match(r"^[-+]?[\d.]+\s*(pt|em|ex|cm|mm|in|bp|pc|sp|dd|cc)$", arg, re.IGNORECASE):
        return True
    # Chứa \textwidth, \linewidth...
    if re.search(r"\\(?:textwidth|linewidth|columnwidth|textheight|paperwidth|paperheight)", arg):
        return True
    return False


def is_text_only(arg: str) -> bool:
    """Kiểm tra xem đối số có phải toàn chữ và khoảng trắng không."""
    if not arg:
        return False
    arg = arg.strip()
    # Chỉ chữ cái, số, khoảng trắng, dấu chấm, dấu phẩy
    return bool(re.match(r"^[\w\s.,;:!?'-]+$", arg, re.UNICODE))


def unwrap_unknown_command(cmd_name: str, text: str, pos: int) -> Tuple[str, int, str]:
    """Bóc vỏ lệnh chưa biết. Trả về (replacement, new_pos, action).
    
    Quy tắc:
    - Nếu đối số giống kích thước -> xóa cả lệnh (action='drop_dimension')
    - Nếu đối số toàn chữ -> giữ chữ (action='unwrap_text')
    - Nếu không rơi vào hai trường hợp trên -> xóa cả lệnh (action='drop_complex')
    - Lệnh không có đối số -> xóa (action='drop_noarg')
    """
    # Bỏ qua đối số tùy chọn
    pos = _skip_optionals(text, pos)
    
    # Thử trích đối số bắt buộc
    arg, new_pos = _extract_argument(text, pos)
    
    if arg is None:
        # Không có đối số -> xóa lệnh
        unknown_commands[cmd_name + "(no_arg)"] += 1
        return " ", pos, "drop_noarg"
    
    # Có đối số -> xét loại
    if is_dimension_like(arg):
        unknown_commands[cmd_name + "(dimension)"] += 1
        return " ", new_pos, "drop_dimension"
    
    if is_text_only(arg):
        unknown_commands[cmd_name + "(text)"] += 1
        return arg, new_pos, "unwrap_text"
    
    # Đối số phức tạp (có ký tự lạ, nhiều dấu \) -> xóa
    unknown_commands[cmd_name + "(complex)"] += 1
    return " ", new_pos, "drop_complex"


# ----------------------------------------------------------------------------- tiện ích cũ
def strip_comments(text: str) -> str:
    """Bỏ phần comment bắt đầu bằng % (không phải \\%)."""
    return re.sub(r"(?<!\\)%.*", "", text)


def unwrap_commands(text: str, names: List[str]) -> str:
    """\\textbf{x} -> x, xử lý đúng ngoặc lồng nhau."""
    pat = re.compile(r"\\(?:%s)\s*\{" % "|".join(names))
    while True:
        m = pat.search(text)
        if not m:
            return text
        depth, i = 1, m.end()
        while i < len(text) and depth:
            c = text[i]
            if c == "\\":
                i += 2
                continue
            depth += (c == "{") - (c == "}")
            i += 1
        if depth:  # ngoặc không cân: chỉ bỏ lệnh
            text = text[: m.start()] + text[m.end():]
        else:
            text = text[: m.start()] + text[m.end(): i - 1] + text[i:]


# ----------------------------------------------------------------------------- toán inline
def find_inline_math(text: str) -> List[Tuple[int, int]]:
    """Các đoạn $...$ (không tính $$...$$ và \\$). Trả về (start, end) gồm cả dấu $."""
    spans, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "$":
            if text.startswith("$$", i):
                j = text.find("$$", i + 2)
                if j == -1:
                    break
                i = j + 2
                continue
            j = i + 1
            while j < n and text[j] != "$":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                break
            spans.append((i, j + 1))
            i = j + 1
            continue
        i += 1
    return spans


def normalize_inline_math(text: str) -> str:
    """Đưa toán inline về dạng $...$ (gọn khoảng trắng bên trong)."""
    text = re.sub(r"\\\((.+?)\\\)", lambda m: "$" + m.group(1) + "$", text, flags=re.S)
    text = re.sub(r"\\begin\{math\}(.+?)\\end\{math\}", lambda m: "$" + m.group(1) + "$", text, flags=re.S)
    out, last = [], 0
    for a, b in find_inline_math(text):
        out.append(text[last:a])
        out.append("$" + " ".join(text[a + 1: b - 1].split()) + "$")
        last = b
    out.append(text[last:])
    return "".join(out)


def protect_math(text: str) -> Tuple[str, List[str]]:
    store, out, last = [], [], 0
    for a, b in find_inline_math(text):
        out.append(text[last:a])
        out.append(f"{PH_OPEN}{len(store)}{PH_CLOSE}")
        store.append(text[a:b])
        last = b
    out.append(text[last:])
    return "".join(out), store


def restore_math(text: str, store: List[str]) -> str:
    return re.sub(PH_OPEN + r"(\d+)" + PH_CLOSE, lambda m: store[int(m.group(1))], text)


# ----------------------------------------------------------------------------- toán block
_MULTI_ENV = {"align": "aligned", "alignat": "aligned", "flalign": "aligned",
              "eqnarray": "aligned", "gather": "gathered", "multline": "gathered"}


def normalize_block_math(raw: str) -> str:
    """Mọi dạng toán block -> \\begin{equation} ... \\end{equation} (một dòng).

    Nhận cả chuỗi có bọc ($$, \\[ \\], \\begin{env}) lẫn thân công thức trần ("E = mc^2").
    align/gather/multline có nhiều dòng được bọc thêm aligned/gathered bên trong equation.
    """
    s = raw.strip()
    env = None
    m = re.fullmatch(r"\$\$(.*)\$\$", s, re.S) or re.fullmatch(r"\\\[(.*)\\\]", s, re.S)
    if m:
        body = m.group(1)
    else:
        m = re.fullmatch(r"\\begin\{([A-Za-z]+)\*?\}(.*)\\end\{\1\*?\}", s, re.S)
        env, body = (m.group(1), m.group(2)) if m else (None, s)

    body = strip_comments(body)
    body = re.sub(r"\\(?:label|tag)\s*\{[^{}]*\}", " ", body)
    body = re.sub(r"\\(?:nonumber|notag)(?![A-Za-z])", " ", body)
    if env == "alignat":
        body = re.sub(r"^\s*\{\d+\}", "", body)
    body = " ".join(body.split())
    body = re.sub(r"(\\\\\s*)+$", "", body).strip()
    if not body:
        return ""

    target = _MULTI_ENV.get(env or "")
    if target and ("&" in body or "\\\\" in body):
        if env == "eqnarray":
            body = re.sub(r"&\s*(=|<|>|\\leq?|\\geq?|\\approx|\\sim|\\neq)\s*&", r"&\1", body)
        body = f"\\begin{{{target}}} {body} \\end{{{target}}}"
    return f"\\begin{{equation}} {body} \\end{{equation}}"


_SEG_RE = re.compile(
    r"(?P<dd>\$\$.+?\$\$)"
    r"|(?P<br>\\\[.+?\\\])"
    r"|(?P<env>\\begin\{(?P<ename>equation\*?|displaymath|align\*?|alignat\*?|flalign\*?"
    r"|eqnarray\*?|gather\*?|multline\*?)\}.+?\\end\{(?P=ename)\})"
    r"|(?P<fig>\\begin\{(?P<fname>figure\*?|table\*?|wrapfigure|wraptable|sidewaysfigure"
    r"|sidewaystable)\}.+?\\end\{(?P=fname)\})",
    re.S,
)


def split_segments(text: str) -> List[Tuple[str, str]]:
    """Tách văn bản thành [('text'|'formula'|'figure', chuỗi)] theo đúng thứ tự xuất hiện."""
    segs, last = [], 0
    for m in _SEG_RE.finditer(text):
        if m.start() > last:
            segs.append(("text", text[last: m.start()]))
        segs.append(("figure" if m.group("fig") else "formula", m.group(0)))
        last = m.end()
    if last < len(text):
        segs.append(("text", text[last:]))
    return segs


# ----------------------------------------------------------------------------- siunitx
# \SI{300}{\kelvin} -> "300 kelvin": các lệnh số/đơn vị mang nghĩa (giá trị và đơn vị) nên được đổi
# thành chữ thường thay vì để lại mã LaTeX trong câu.
_SI_PREFIXES = {"yocto", "zepto", "atto", "femto", "pico", "nano", "micro", "milli", "centi", "deci",
                "deca", "hecto", "kilo", "mega", "giga", "tera", "peta", "exa"}
_OPT = r"(?:\s*\[[^\]]*\])?"
_SI_RE = re.compile(r"\\(?:SI|qty)" + _OPT + r"\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SIRANGE_RE = re.compile(r"\\(?:SIrange|qtyrange)" + _OPT + r"\s*\{([^{}]*)\}\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_NUMRANGE_RE = re.compile(r"\\numrange" + _OPT + r"\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_NUM_RE = re.compile(r"\\num" + _OPT + r"\s*\{([^{}]*)\}")
_UNIT_RE = re.compile(r"\\(?:si|unit)" + _OPT + r"\s*\{([^{}]*)\}")
_ANG_RE = re.compile(r"\\ang" + _OPT + r"\s*\{([^{}]*)\}")


def _unit_text(unit: str) -> str:
    """\\kilo\\meter\\per\\second -> 'kilometer per second' (tiền tố SI dính liền đơn vị đi sau)."""
    out: List[str] = []
    glue = False
    for token in re.findall(r"\\[A-Za-z]+|[^\\\s{}]+", unit):
        word = token.lstrip("\\")
        if out and glue:
            out[-1] += word
        else:
            out.append(word)
        glue = token.startswith("\\") and word in _SI_PREFIXES
    return " ".join(out)


def normalize_units(text: str) -> str:
    """Đổi \\SI, \\qty, \\SIrange, \\qtyrange, \\num, \\numrange, \\si, \\unit, \\ang thành chữ thường."""
    text = _SIRANGE_RE.sub(lambda m: f"{m.group(1)} to {m.group(2)} {_unit_text(m.group(3))}".strip(), text)
    text = _SI_RE.sub(lambda m: f"{m.group(1)} {_unit_text(m.group(2))}".strip(), text)
    text = _NUMRANGE_RE.sub(lambda m: f"{m.group(1)} to {m.group(2)}", text)
    text = _NUM_RE.sub(lambda m: m.group(1), text)
    text = _UNIT_RE.sub(lambda m: _unit_text(m.group(1)), text)
    return _ANG_RE.sub(lambda m: f"{m.group(1)} degrees", text)


# ----------------------------------------------------------------------------- lệnh có đối số cần bỏ hẳn
# Lệnh -> số đối số {..} đi kèm (đối số tùy chọn [..] được bỏ theo). Toàn bộ lệnh và đối số bị xóa vì
# chỉ là bố cục / siêu dữ liệu / định nghĩa, không phải nội dung văn bản.
ARG_DROP = {
    "renewcommand": 2, "newcommand": 2, "providecommand": 2, "setcounter": 2, "addtocounter": 2,
    "setlength": 2, "definecolor": 3, "ccsdesc": 1, "shortauthors": 1, "footnotetext": 1, "footnotemark": 0,
    "captionsetup": 1, "hypersetup": 1, "graphicspath": 1, "pagestyle": 1, "thispagestyle": 1,
    "bibliographystyle": 1, "color": 1, "fnsymbol": 1, "arabic": 1, "pagenumbering": 1, "newtheorem": 2,
    "theoremstyle": 1, "copyrightyear": 1, "acmYear": 1, "setcopyright": 1, "acmDOI": 1, "acmISBN": 1,
    "acmConference": 1, "acmBooktitle": 1, "acmPrice": 1, "thefootnote": 0,
}
_ARG_DROP_RE = re.compile(r"\\(%s)(?![A-Za-z@])\*?" % "|".join(sorted(ARG_DROP, key=len, reverse=True)))
_TEXTCOLOR_RE = re.compile(r"\\(?:textcolor|colorbox)\s*(?:\[[^\]]*\])?\s*\{[^{}]*\}\s*\{")
_IEEEPARSTART_RE = re.compile(r"\\IEEEPARstart\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SYMBOLS = {r"\S": "§", r"\P": "¶", r"\dag": "†", r"\ddag": "‡", r"\ldots": "...", r"\dots": "...",
            r"\textellipsis": "...", r"\textdegree": "°", r"\texttimes": "×", r"\textpm": "±", r"\textmu": "µ",
            r"\ss": "ß", r"\o": "ø", r"\O": "Ø", r"\aa": "å", r"\AA": "Å", r"\ae": "æ", r"\AE": "Æ"}
_SYMBOL_RE = re.compile(r"(%s)(?![A-Za-z@])" % "|".join(re.escape(k) for k in sorted(_SYMBOLS, key=len, reverse=True)))
_ESCAPED_RE = re.compile(r"\\([&%#_])")
_ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304",
            ".": "\u0307", "c": "\u0327", "v": "\u030c", "u": "\u0306", "H": "\u030b"}
_ACCENT_RE = re.compile(r"\\(?:([\"'`^~=.])\s*\{?\s*(\\?[A-Za-z])\s*\}?|([cvuH])\s*\{\s*([A-Za-z])\s*\})")


def _balanced_end(text: str, start: int) -> int | None:
    """Chỉ số ngay sau dấu } khớp với { tại text[start]; None nếu không cân."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _skip_argument(text: str, i: int) -> int:
    """Bỏ một đối số bắt buộc tại i: {..}, một control sequence (\\thefootnote) hoặc một ký tự."""
    i = _skip_ws(text, i)
    if i >= len(text):
        return i
    if text[i] == "{":
        return _balanced_end(text, i) or len(text)
    if text[i] == "\\":
        m = re.match(r"\\(?:[A-Za-z@]+|.)", text[i:])
        return i + (m.end() if m else 1)
    return i + 1


def _skip_optionals(text: str, i: int) -> int:
    while True:
        j = _skip_ws(text, i)
        if j < len(text) and text[j] == "[":
            close = text.find("]", j)
            if close == -1:
                return i
            i = close + 1
        else:
            return i


def strip_layout_commands(text: str) -> str:
    """Xóa các lệnh trong ARG_DROP cùng đối số của chúng (khớp ngoặc đúng, kể cả lồng nhau)."""
    out, last = [], 0
    for m in _ARG_DROP_RE.finditer(text):
        if m.start() < last:
            continue
        i = m.end()
        for _ in range(ARG_DROP[m.group(1)]):
            i = _skip_argument(text, _skip_optionals(text, i))
        i = _skip_optionals(text, i) if ARG_DROP[m.group(1)] == 0 else i
        out.append(text[last:m.start()])
        out.append(" ")
        last = i
    out.append(text[last:])
    return "".join(out)


def normalize_accents(text: str) -> str:
    """{\\"o}, \\'e, \\c{c}, \\v{s}... -> ký tự Unicode (ö, é, ç, š)."""
    import unicodedata

    def repl(m: "re.Match") -> str:
        accent, letter = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        letter = {"\\i": "i", "\\j": "j"}.get(letter, letter)
        return unicodedata.normalize("NFC", letter + _ACCENTS[accent])
    return _ACCENT_RE.sub(repl, text)


def strip_group_braces(text: str) -> str:
    """Bỏ cặp ngoặc nhóm { } không thuộc đối số của lệnh nào (ví dụ "{ Bold}" sau khi bỏ \\bf).
    Ngoặc ngay sau tên lệnh (\\cite{a}, \\href{u}{t}, \\footnote{...}) được giữ nguyên."""
    out: List[str] = []
    stack: List[bool] = []
    last_closed_kept = False
    for i, c in enumerate(text):
        if c == "{" and (i == 0 or text[i - 1] != "\\"):
            tail = "".join(out[-80:])
            keep = bool(re.search(r"\\[A-Za-z@]+\*?(?:\s*\[[^\]]*\])*$", tail)) or (last_closed_kept and tail.endswith("}"))
            stack.append(keep)
            if keep:
                out.append(c)
            last_closed_kept = False
        elif c == "}" and (i == 0 or text[i - 1] != "\\"):
            keep = stack.pop() if stack else False
            if keep:
                out.append(c)
            last_closed_kept = keep
        else:
            out.append(c)
            last_closed_kept = False
    return "".join(out)


# ----------------------------------------------------------------------------- macro tự định nghĩa
# M1 chỉ giữ .tex/.bib nên các macro định nghĩa trong file .sty/.cls (\ie, \eg, \etal...) không còn;
# macro định nghĩa trong chính .tex (\newcommand, \def) được mở rộng, các viết tắt phổ biến có sẵn bên dưới.
_MACRO_DEF_RE = re.compile(r"\\(?P<kind>(?:re)?newcommand|providecommand|DeclareRobustCommand|[gex]?def)(?![A-Za-z@])\*?")
_PROTECTED_MACROS = {
    "begin", "end", "item", "cite", "citep", "citet", "ref", "eqref", "label", "caption", "input", "include",
    "section", "subsection", "subsubsection", "paragraph", "subparagraph", "chapter", "part", "title", "author",
    "date", "maketitle", "bibitem", "bibliography", "footnote", "includegraphics", "documentclass", "usepackage",
    "newcommand", "renewcommand", "def", "let", "relax", "protect", "par", "relax", "centering",
}
BUILTIN_ABBREVS = {"ie": "i.e.", "eg": "e.g.", "etal": "et al.", "cf": "cf.", "vs": "vs.", "wrt": "w.r.t.", "etc": "etc."}
_ABBREV_KEYS = {"ie": "ie", "eg": "eg", "etal": "etal", "cf": "cf", "vs": "vs", "wrt": "wrt", "etc": "etc"}


def collect_macros(text: str) -> tuple[dict, str]:
    """Tìm các macro \\newcommand/\\renewcommand/\\providecommand/\\def, trả về ({tên: (số đối số, mặc định, thân)},
    văn bản đã bỏ các câu lệnh định nghĩa). Chỉ nhận macro có thân trong {...}."""
    macros: dict = {}
    spans: List[tuple[int, int]] = []
    last_end = 0
    for m in _MACRO_DEF_RE.finditer(text):
        if m.start() < last_end:
            continue
        i = _skip_ws(text, m.end())
        kind = m.group("kind")
        name_match = re.match(r"\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+)", text[i:i + 120])
        if not name_match:
            continue
        name = name_match.group(1) or name_match.group(2)
        i += name_match.end()
        nargs, default = 0, None
        if kind.endswith("def"):
            params = re.match(r"(?:#\d)+", text[i:])
            if params:
                nargs = len(params.group(0)) // 2
                i += params.end()
        else:
            count = re.match(r"\s*\[(\d)\]", text[i:])
            if count:
                nargs = int(count.group(1))
                i += count.end()
                opt = re.match(r"\s*\[([^\]]*)\]", text[i:])
                if opt:
                    default = opt.group(1)
                    i += opt.end()
        i = _skip_ws(text, i)
        if i >= len(text) or text[i] != "{":
            continue
        end = _balanced_end(text, i)
        if end is None:
            continue
        spans.append((m.start(), end))
        last_end = end
        body = text[i + 1:end - 1]
        if name in _PROTECTED_MACROS or "\\csname" in body or (nargs == 0 and "#" in body):
            continue
        body = re.sub(r"\\(?:xspace|@|relax)(?![A-Za-z])", "", body).strip()
        letters = re.sub(r"[^a-z]", "", re.sub(r"\\[A-Za-z]+", "", body).lower())
        if name in BUILTIN_ABBREVS and letters.startswith(_ABBREV_KEYS[name]):
            body = BUILTIN_ABBREVS[name]   # \def\eg{\emph{e.g}\onedot} -> "e.g." (dấu phẩy do người viết gõ)
        macros[name] = (nargs, default, body)
    for name, body in BUILTIN_ABBREVS.items():
        macros.setdefault(name, (0, None, body))
    pieces, pos = [], 0
    for a, b in spans:
        pieces.append(text[pos:a])
        pos = b
    pieces.append(text[pos:])
    return macros, "".join(pieces)


def _expand_once(text: str, macros: dict) -> tuple[str, bool]:
    names = sorted(macros, key=len, reverse=True)
    pattern = re.compile(r"\\(%s)(?![A-Za-z@])" % "|".join(re.escape(n) for n in names))
    out, last, changed = [], 0, False
    for m in pattern.finditer(text):
        if m.start() < last:
            continue
        nargs, default, body = macros[m.group(1)]
        i, args, ok = m.end(), [], True
        if nargs:
            required = nargs
            if default is not None:
                j = i
                if j < len(text) and text[j] == "[" and text.find("]", j) != -1:
                    close = text.find("]", j)
                    args.append(text[j + 1:close])
                    i = close + 1
                else:
                    args.append(default)
                required = nargs - 1
            for _ in range(required):
                j = _skip_ws(text, i)
                if j < len(text) and text[j] == "{":
                    end = _balanced_end(text, j)
                    if end is None:
                        ok = False
                        break
                    args.append(text[j + 1:end - 1])
                    i = end
                elif j < len(text) and text[j] not in "\\}":
                    args.append(text[j])
                    i = j + 1
                else:
                    ok = False
                    break
        if not ok:
            continue
        replacement = body
        for index, arg in enumerate(args, start=1):
            replacement = replacement.replace(f"#{index}", arg)
        out.append(text[last:m.start()])
        out.append(replacement)
        last, changed = i, True
    out.append(text[last:])
    return "".join(out), changed


def expand_user_macros(text: str, max_rounds: int = 4) -> str:
    """Mở rộng macro tự định nghĩa (không/ có đối số) và các viết tắt \\ie, \\eg, \\etal... trong toàn văn bản,
    rồi bỏ các câu lệnh định nghĩa. Lặp tối đa max_rounds lần cho macro lồng nhau; có chặn phình văn bản."""
    macros, text = collect_macros(text)
    limit = 3 * len(text) + 20000
    for _ in range(max_rounds):
        text, changed = _expand_once(text, macros)
        if not changed or len(text) > limit:
            break
    return text


def strip_drop_with_args_commands(text: str) -> str:
    """Xóa các lệnh trong DROP_WITH_ARGS cùng đối số."""
    cmd_names = sorted(DROP_WITH_ARGS.keys(), key=len, reverse=True)
    pattern = re.compile(r"\\(%s)(?![A-Za-z@])\*?" % "|".join(re.escape(n) for n in cmd_names))
    
    out, last = [], 0
    for m in pattern.finditer(text):
        if m.start() < last:
            continue
        cmd_name = m.group(1)
        num_args = DROP_WITH_ARGS[cmd_name]
        
        i = m.end()
        for _ in range(num_args):
            i = _skip_argument(text, _skip_optionals(text, i))
        i = _skip_optionals(text, i) if num_args == 0 else i
        
        out.append(text[last:m.start()])
        out.append(" ")
        last = i
    out.append(text[last:])
    return "".join(out)


def unwrap_second_arg_commands(text: str) -> str:
    """Xử lý lệnh UNWRAP_SECOND_ARG: giữ đối số thứ 2."""
    cmd_names = sorted(UNWRAP_SECOND_ARG.keys(), key=len, reverse=True)
    pattern = re.compile(r"\\(%s)(?![A-Za-z@])\*?" % "|".join(re.escape(n) for n in cmd_names))
    
    out, last = [], 0
    for m in pattern.finditer(text):
        if m.start() < last:
            continue
        
        i = _skip_optionals(text, m.end())
        
        # Bỏ qua đối số thứ 1
        _, i = _extract_argument(text, i)
        
        # Lấy đối số thứ 2
        arg2, i = _extract_argument(text, i)
        
        out.append(text[last:m.start()])
        out.append(arg2 if arg2 is not None else " ")
        last = i
    out.append(text[last:])
    return "".join(out)


def process_unknown_commands(text: str) -> str:
    """Xử lý các lệnh chưa biết bằng bóc vỏ dự phòng."""
    # Tìm tất cả các lệnh LaTeX chưa được xử lý
    # Lệnh LaTeX: \cmdname (không phải số, không phải ký tự đặc biệt đơn)
    pattern = re.compile(r"\\([A-Za-z@]+)(?![A-Za-z@])")
    
    # Tạo tập hợp các lệnh đã biết
    known_commands = set(DROP_WITH_ARGS.keys()) | set(DROP_NO_ARGS) | set(SPACE_COMMANDS) | \
                     set(UNWRAP_COMMANDS) | set(UNWRAP_SECOND_ARG.keys()) | \
                     {"begin", "end", "item", "cite", "ref", "label", "includegraphics"}
    
    out, last = [], 0
    for m in pattern.finditer(text):
        if m.start() < last:
            continue
        
        cmd_name = m.group(1)
        
        # Bỏ qua lệnh đã biết
        if cmd_name in known_commands:
            continue
        
        # Bóc vỏ dự phòng
        replacement, new_pos, action = unwrap_unknown_command(cmd_name, text, m.end())
        
        out.append(text[last:m.start()])
        out.append(replacement)
        last = new_pos
    
    out.append(text[last:])
    return "".join(out)


# ----------------------------------------------------------------------------- làm sạch chính (Tầng 2 đầy đủ)
def clean_text(text: str, *, figure: bool = False) -> str:
    """Làm sạch một đoạn LaTeX với kiến trúc Tầng 2.
    
    Quy trình:
        1. Bảo vệ: toán inline, cite/ref/label, URL, verbatim
        2. Phân loại: DROP, UNWRAP, SPACE, KÝ HIỆU
        3. Bóc vỏ dự phòng: lệnh chưa biết
        4. Trả lại token đã bảo vệ
    
    figure=True: giữ nguyên \\\\, ~ ... của bảng/hình.
    """
    s = strip_comments(text or "")
    
    # Chuẩn hóa toán
    s = normalize_inline_math(s)
    
    # Bước 1: Bảo vệ (mask)
    s, math_store = protect_math(s)
    s, cite_store = protect_citations_and_refs(s)
    s, url_store = protect_urls(s)
    s, verb_store = protect_verbatim(s)
    
    # Bước 2: Phân loại theo bảng
    # - Đơn vị và số
    s = normalize_units(s)
    # - Accents
    s = normalize_accents(s)
    # - Lệnh đặc biệt
    s = _IEEEPARSTART_RE.sub(r"\1\2", s)
    # - DROP có đối số
    s = strip_drop_with_args_commands(s)
    s = strip_layout_commands(s)  # ARG_DROP cũ (để tương thích)
    # - DROP không đối số
    s = _DROP_NO_ARGS_RE.sub(" ", s)
    # - SPACE
    s = _SPACE_CMD_RE.sub(" ", s)
    # - UNWRAP
    s = unwrap_commands(s, UNWRAP_COMMANDS)
    s = unwrap_second_arg_commands(s)
    s = _TEXTCOLOR_RE.sub("{", s)  # Xử lý \textcolor đặc biệt
    # - Placement options
    s = _PLACEMENT_RE.sub(r"\1", s)
    
    # Bước 3: Bóc vỏ dự phòng cho lệnh chưa biết
    s = process_unknown_commands(s)
    
    # Bước 4: Xử lý văn bản thường (không phải figure)
    if not figure:
        s = _DIM_RE.sub(" ", s)
        s = _LINEBREAK_RE.sub(" ", s)
        s = re.sub(r"(?<!\\)~", " ", s)
        s = _SPACE_SYM_RE.sub(" ", s)
        s = s.replace("\\!", "")
        # Ký hiệu
        for latex_sym, unicode_sym in SYMBOL_MAP.items():
            s = s.replace(latex_sym, unicode_sym)
        s = _SYMBOL_RE.sub(lambda m: _SYMBOLS[m.group(1)], s)
        s = _ESCAPED_RE.sub(r"\1", s)
        s = strip_group_braces(s)
    
    # Gọn khoảng trắng
    s = " ".join(s.split())
    
    # Bước 5: Trả lại token đã bảo vệ
    s = restore_math(s, math_store)
    s = restore_protected(s, cite_store, url_store, verb_store)
    
    return s


def clean_figure(text: str) -> str:
    return clean_text(text, figure=True)


def clean_heading(text: str) -> str:
    return clean_text(text)


def get_unknown_commands_stats() -> Dict[str, int]:
    """Trả về bộ đếm lệnh lạ để xuất ra file."""
    return dict(unknown_commands)


def reset_unknown_commands():
    """Reset bộ đếm lệnh lạ."""
    unknown_commands.clear()


def save_unknown_commands_stats(output_path: str):
    """Lưu bộ đếm vào file JSON."""
    import json
    from pathlib import Path
    
    stats = dict(unknown_commands)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    
    with output.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    
    return len(stats), sum(stats.values())


def load_unknown_commands_stats(input_path: str) -> Dict[str, int]:
    """Đọc bộ đếm từ file JSON."""
    import json
    from pathlib import Path
    
    input_file = Path(input_path)
    if not input_file.exists():
        return {}
    
    with input_file.open("r", encoding="utf-8") as f:
        return json.load(f)