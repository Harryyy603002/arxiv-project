"""M2 - P3: tách câu cho văn bản LaTeX đã làm sạch.

Không tách trong: toán inline, viết tắt (i.e., e.g., et al., Fig. 1, Eq. (2)...),
URL, số thập phân. Chỉ tách ở . ! ? khi câu sau bắt đầu bằng chữ hoa, ngoặc mở,
dấu trích, lệnh LaTeX (\\cite...) hoặc toán $...$.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2_cleanup import find_inline_math  # noqa: E402

_DOT, _EXC, _Q = "\ue002", "\ue003", "\ue004"
_MASK = str.maketrans({".": _DOT, "!": _EXC, "?": _Q})
_UNMASK = str.maketrans({_DOT: ".", _EXC: "!", _Q: "?"})

# viết tắt luôn che dấu chấm (không phân biệt hoa/thường)
_ABBR_CI = re.compile(
    r"(?<![A-Za-z])(?:e\.g|i\.e|w\.r\.t|a\.k\.a|s\.t|cf|vs|resp|approx|et al"
    r"|Figs?|Eqs?|Secs?|Tabs?|Tbl|Refs?|Thms?|Lems?|Props?|Cors?|Defs?|Algs?|Ch|Appx"
    r"|Dr|Mr|Mrs|Ms|Prof|Inc|Ltd|Jr|Sr|Vols?|Eds?)\.",
    re.I,
)
# No. 5, p. 3, pp. 1-5: chỉ che khi theo sau là số/ngoặc
_ABBR_NUM = re.compile(r"(?<![A-Za-z])(?:Nos?|pp?)\.(?=\s*[\d(\[])")
# U.S., i.i.d.: che các dấu chấm bên trong, chừa dấu cuối
_ACRONYM = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]\.){2,}")
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_URL = re.compile(r"https?://[^\s{}]+|www\.[^\s{}]+|\\(?:url|href)\s*\{[^{}]*\}")

_BOUNDARY = re.compile(r"([.!?]+[\"')\]}\u201d\u2019]*)\s+(?=[A-Z(\[\u201c\"'`\\$])")


def _mask_all(m: "re.Match") -> str:
    return m.group(0).translate(_MASK)


def _mask(text: str) -> str:
    # toán inline
    out, last = [], 0
    for a, b in find_inline_math(text):
        out.append(text[last:a])
        out.append(text[a:b].translate(_MASK))
        last = b
    out.append(text[last:])
    s = "".join(out)
    s = _URL.sub(_mask_all, s)
    s = _ABBR_CI.sub(_mask_all, s)
    s = _ABBR_NUM.sub(_mask_all, s)
    s = _ACRONYM.sub(lambda m: m.group(0)[:-1].translate(_MASK) + m.group(0)[-1], s)
    s = _DECIMAL.sub(_DOT, s)
    return s


def split_sentences(text: str) -> List[str]:
    """Tách văn bản (đã clean_text) thành danh sách câu."""
    s = _mask(text)
    parts, last = [], 0
    for m in _BOUNDARY.finditer(s):
        parts.append(s[last: m.end(1)])
        last = m.end()
    parts.append(s[last:])
    out = [p.translate(_UNMASK).strip() for p in parts]
    return [p for p in out if re.search(r"\w", p)]