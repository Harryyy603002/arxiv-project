"""M2 - P3: làm sạch LaTeX và chuẩn hóa toán.

Hàm chính:
    clean_text(text)            làm sạch văn bản thường (bỏ lệnh định dạng, comment, ~, \\\\ ...)
    clean_figure(text)          làm sạch môi trường figure/table (giữ cấu trúc, \\includegraphics...)
    normalize_inline_math(t)    \\( \\) , \\begin{math} -> $...$
    normalize_block_math(raw)   $$..$$, \\[..\\], equation*, align... -> \\begin{equation}...\\end{equation}
    split_segments(text)        tách đoạn văn thành [text | formula | figure] theo thứ tự

Lệnh \\cite{...} và \\ref{...} luôn được giữ nguyên.
"""
from __future__ import annotations

import re
from typing import List, Tuple

PH_OPEN, PH_CLOSE = "\ue000", "\ue001"  # ký tự giữ chỗ cho toán inline

# ----------------------------------------------------------------------------- hằng số
# Lệnh định dạng không mang nghĩa -> bỏ hẳn
DROP_NOARG = [
    "centering", "raggedright", "raggedleft", "noindent", "indent", "clearpage", "newpage",
    "pagebreak", "linebreak", "nopagebreak", "midrule", "toprule", "bottomrule", "hline",
    "small", "footnotesize", "scriptsize", "tiny", "large", "Large", "LARGE", "huge", "Huge",
    "normalsize", "medskip", "smallskip", "bigskip", "vfill", "bfseries", "itshape",
    "rmfamily", "sffamily", "normalfont",
]
# Lệnh khoảng trắng -> thành dấu cách
SPACE_CMDS = ["quad", "qquad", "hfill", "enspace", "thinspace"]
# Lệnh bọc chữ: bỏ lệnh, giữ nội dung. Muốn giữ \emph... thì xóa tên khỏi danh sách này.
UNWRAP = ["textbf", "textit", "emph", "textsc", "textsl", "underline"]

_DROP_NOARG_RE = re.compile(r"\\(?:%s)(?![A-Za-z])" % "|".join(DROP_NOARG))
_SPACE_CMD_RE = re.compile(r"\\(?:%s)(?![A-Za-z])" % "|".join(SPACE_CMDS))
_DROP_ARG_RE = re.compile(
    r"\\(?:label|vspace\*?|hspace\*?|cline|thispagestyle|pagestyle)\s*\{[^{}]*\}"
    r"|\\cmidrule\s*(?:\([^)]*\))?\s*\{[^{}]*\}"
)
_PLACEMENT_RE = re.compile(
    r"(\\begin\{(?:figure|table|wrapfigure|wraptable|sidewaysfigure|sidewaystable)\*?\})\s*\[[!htbpH]*\]"
)
_LINEBREAK_RE = re.compile(r"\\\\(?:\s*\[[^\]]*\])?")
_SPACE_SYM_RE = re.compile(r"(?<!\\)\\[,;: ]")


# ----------------------------------------------------------------------------- tiện ích
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


# ----------------------------------------------------------------------------- làm sạch chính
def clean_text(text: str, *, figure: bool = False) -> str:
    """Làm sạch một đoạn LaTeX. figure=True: giữ nguyên \\\\, ~ ... của bảng/hình."""
    s = strip_comments(text or "")
    s = normalize_inline_math(s)
    s, store = protect_math(s)
    s = _PLACEMENT_RE.sub(r"\1", s)
    s = _DROP_ARG_RE.sub(" ", s)
    s = _DROP_NOARG_RE.sub(" ", s)
    s = _SPACE_CMD_RE.sub(" ", s)
    s = unwrap_commands(s, UNWRAP)
    if not figure:
        s = _LINEBREAK_RE.sub(" ", s)
        s = re.sub(r"(?<!\\)~", " ", s)
        s = _SPACE_SYM_RE.sub(" ", s)
        s = s.replace("\\!", "")
    s = " ".join(s.split())
    return restore_math(s, store)


def clean_figure(text: str) -> str:
    return clean_text(text, figure=True)


def clean_heading(text: str) -> str:
    return clean_text(text)