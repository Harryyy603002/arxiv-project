r"""m2_hierarchy.py

Milestone: M2
Người phụ trách: P1
Mục đích: Dựng cây Document > Section > ... > lá (câu, công thức khối, hình/bảng); xử lý section có dấu *, appendix; bỏ phần References

Đầu vào: Nội dung các file từ m2_gather
Đầu ra:  data/<paper_key>/vN/tree_raw.json

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.

Cách làm (4 bước, mỗi bước là một hàm):
  1. expand_includes: mở rộng \input/\include TẠI CHỖ, bắt đầu từ file gốc (m2_gather) -> một chuỗi LaTeX
     duy nhất đúng thứ tự đọc. Comment (%) và \begin{comment} đã bị bỏ.
  2. extract_body: lấy phần giữa \begin{document} và \end{document}.
  3. scan_blocks: quét thân tài liệu thành DANH SÁCH PHẲNG các khối: heading, paragraph, equation,
     figure, itemize, container (abstract). Đoạn văn chỉ dừng ở mức paragraph (P3 sẽ tách câu).
  4. build_tree: lồng các heading thành cây. Cấp (level) được xác định TỰ ĐỘNG theo các lệnh phân mục
     mà bài thực sự dùng (bài dùng \chapter thì chapter = 1, bài chỉ có \section/\paragraph thì = 1, 2).
     Mục References bị bỏ, Acknowledgements/Appendix được giữ.

Cách chạy thử (từ thư mục gốc của repo):
    python src/parser/m2_hierarchy.py --ids 2512.23231 --outline
    python src/parser/m2_hierarchy.py --range 2512.23231-2601.03155 --limit 30
"""

from __future__ import annotations

import argparse
import collections
import json
import posixpath
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # src/    (để import utils)
sys.path.insert(0, str(Path(__file__).resolve().parent))       # parser/ (để import m2_gather)
from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: F401
from utils.config import iter_id_range, parse_id_range, parse_month_last  # noqa: E402
import m2_gather as G  # noqa: E402

# ----------------------------------------------------------------------------
# HẰNG SỐ
# ----------------------------------------------------------------------------
# Lệnh phân mục, xếp từ cấp cao xuống thấp (thứ tự này quyết định ai là cha của ai)
SECTION_CMDS = ("part", "chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph")
SECTION_RANK = {c: i for i, c in enumerate(SECTION_CMDS)}

# Môi trường (đã bỏ dấu *) -> loại lá. Môi trường lạ (theorem, proof, center...) được "mở ra":
# bỏ \begin/\end và quét tiếp nội dung bên trong.
MATH_ENVS = {"equation", "align", "alignat", "flalign", "gather", "multline", "eqnarray", "displaymath", "dmath"}
FIGURE_ENVS = {"figure", "table", "wrapfigure", "wraptable", "sidewaysfigure", "sidewaystable",
               "tabular", "tabularx", "longtable", "tikzpicture", "algorithm", "algorithmic",
               "verbatim", "lstlisting", "minted"}
LIST_ENVS = {"itemize", "enumerate", "description"}
SKIP_ENVS = {"thebibliography"}

# Tiêu đề mục References (đã bỏ lệnh định dạng, không phân biệt hoa thường)
REFERENCES_TITLE_RE = re.compile(
    r"^\W*(references?|bibliography|literature cited|references and notes|works cited)\W*$", re.I)

# Các "điểm đánh dấu" cần dừng lại khi quét thân tài liệu
MARK_RE = re.compile(
    r"\\(?P<cmd>part|chapter|section|subsection|subsubsection|paragraph|subparagraph)(?P<star>\*?)(?![A-Za-z@])"
    r"|\\begin\s*\{(?P<env>[^}]+)\}"
    r"|(?P<dd>(?<!\\)\$\$)"
    r"|(?P<inl>(?<!\\)\$)"
    r"|(?P<br>(?<!\\)\\\[)"
    r"|(?P<paren>(?<!\\)\\\()"
    r"|(?P<drop>\\(?:maketitle|tableofcontents|listoffigures|listoftables|newpage|clearpage|appendix)(?![A-Za-z@])"
    r"|\\bibliography(?:style)?\s*\{[^}]*\})"
)
ITEM_TOKEN_RE = re.compile(r"\\item(?![A-Za-z@])|\\(begin|end)\s*\{(?:itemize|enumerate|description)\}")
END_DOCUMENT_RE = re.compile(r"\\end\s*\{\s*document\s*\}")
# Đoạn chỉ gồm lệnh vô nghĩa (\label{..}, \vspace{..}, \noindent...) thì không tạo paragraph
NOISE_ONLY_RE = re.compile(
    r"^(?:\\(?:label|vspace\*?|hspace\*?|noindent|centering|bigskip|medskip|smallskip|newline|par|linebreak)"
    r"(?![A-Za-z@])\s*(?:\{[^{}]*\})?\s*|\\(?:itemsep|parsep|topsep|parskip)[^\s\\]*\s*|\\\\\s*)+$")


# ----------------------------------------------------------------------------
# 1. GỘP FILE: mở rộng \input/\include tại chỗ
# ----------------------------------------------------------------------------
def expand_includes(vdir: Path, root: str) -> str:
    r"""Thay mỗi \input/\include bằng nội dung file đó, ngay tại vị trí lệnh (đệ quy).

    Dùng lại resolve_include của m2_gather nên quy tắc tìm file giống hệt. Lệnh không tìm thấy
    file hoặc tạo vòng lặp thì bị bỏ (m2_gather đã thống kê các trường hợp này).
    """
    vdir = Path(vdir)
    tex_set = {p.relative_to(vdir).as_posix() for p in G.list_tex_files(vdir)}
    root_dir = posixpath.dirname(root)

    def expand(rel: str, stack: list[str]) -> str:
        text = G.prepare_for_includes(G.read_tex(vdir / rel))

        def repl(m: re.Match) -> str:
            target = G.normalize_include_target(m.group(1) or m.group(2) or "")
            child = G.resolve_include(target, rel, root_dir, tex_set) if target else None
            if child is None or child in stack:
                return ""
            return "\n" + expand(child, stack + [child]) + "\n"

        return G.INCLUDE_RE.sub(repl, text)

    return expand(root, [root])


def extract_body(text: str) -> str:
    r"""Phần giữa \begin{document} và \end{document} (không có \begin{document} thì lấy cả text)."""
    m = G.BEGIN_DOC_RE.search(text)
    start = m.end() if m else 0
    e = END_DOCUMENT_RE.search(text, start)
    return text[start:e.start() if e else len(text)]


# ----------------------------------------------------------------------------
# 2. HÀM PHỤ CHO VIỆC QUÉT
# ----------------------------------------------------------------------------
def skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def read_braced(text: str, pos: int):
    """text[pos] phải là '{'. Trả về (nội dung bên trong, vị trí sau '}') hoặc None nếu không cân ngoặc."""
    if pos >= len(text) or text[pos] != "{":
        return None
    depth, i = 0, pos
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[pos + 1:i], i + 1
        i += 1
    return None


def read_optional(text: str, pos: int) -> int:
    """Nếu text[pos] là '[' thì trả về vị trí sau ']' tương ứng, ngược lại giữ nguyên pos."""
    if pos >= len(text) or text[pos] != "[":
        return pos
    depth, i = 0, pos + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "]" and depth == 0:
            return i + 1
        i += 1
    return pos


def find_unescaped(text: str, token: str, start: int) -> int:
    r"""Vị trí đầu tiên của token từ `start` mà KHÔNG đứng sau một dấu \ thoát (\$ không phải $)."""
    i = text.find(token, start)
    while i != -1:
        k, n = i, 0
        while k > 0 and text[k - 1] == "\\":
            n += 1
            k -= 1
        if n % 2 == 0:
            return i
        i = text.find(token, i + 1)
    return -1


def find_inline_close(text: str, start: int) -> int:
    """Tìm '$' đóng của toán inline. Gặp dòng trống trước khi đóng -> coi '$' mở là lẻ loi (trả -1)."""
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "$":
            return i
        if ch == "\n" and re.match(r"[ \t]*\n", text[i + 1:i + 80]):
            return -1
        i += 1
    return -1


def find_env_end(text: str, start: int, name: str):
    r"""Tìm \end{name} khớp với \begin{name} ngay trước `start` (đếm lồng nhau). Trả (đầu, cuối) hoặc None."""
    pat = re.compile(r"\\(begin|end)\s*\{" + re.escape(name) + r"\}")
    depth = 1
    for m in pat.finditer(text, start):
        if m.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.start(), m.end()
    return None


def split_items(inner: str):
    r"""Tách thân của itemize/enumerate thành (phần trước \item đầu tiên, [text từng item]).
    Chỉ cắt ở \item cấp ngoài cùng, \item của danh sách lồng bên trong được giữ lại trong item cha."""
    cuts, depth = [], 0
    for m in ITEM_TOKEN_RE.finditer(inner):
        g = m.group(1)
        if g == "begin":
            depth += 1
        elif g == "end":
            depth -= 1
        elif depth == 0:
            cuts.append(m)
    if not cuts:
        return inner, []
    items = []
    for k, m in enumerate(cuts):
        end = cuts[k + 1].start() if k + 1 < len(cuts) else len(inner)
        items.append(inner[m.end():end])
    return inner[:cuts[0].start()], items


def split_paragraphs(chunk: str) -> list[str]:
    """Tách đoạn theo dòng trống; bỏ đoạn rỗng hoặc chỉ gồm lệnh vô nghĩa."""
    out = []
    for p in re.split(r"\n[ \t]*\n", chunk):
        p = p.strip()
        if p and not NOISE_ONLY_RE.match(p):
            out.append(p)
    return out


# ----------------------------------------------------------------------------
# 3. QUÉT THÂN TÀI LIỆU -> DANH SÁCH KHỐI PHẲNG
# ----------------------------------------------------------------------------
def scan_blocks(text: str) -> list[tuple]:
    r"""Quét text thành danh sách phẳng các khối:
        ("heading", cmd, title)            ("paragraph", text)
        ("equation", raw)                  ("figure", raw)
        ("itemize", [khối_của_item1, ...]) ("container", "Abstract", khối_bên_trong)
    `raw` là LaTeX nguyên bản của khối (kèm \begin..\end hoặc $$..$$) để P3 còn biết dạng gốc khi chuẩn hóa toán."""
    blocks: list[tuple] = []
    pos = start = 0          # pos: nơi tìm marker kế tiếp; start: đầu phần text chưa đưa vào paragraph

    def flush(upto: int) -> None:
        blocks.extend(("paragraph", p) for p in split_paragraphs(text[start:upto]))

    while True:
        m = MARK_RE.search(text, pos)
        if m is None:
            break

        if m.group("cmd"):                                         # \section{...}, \section*{...}, \paragraph{...}
            p = skip_ws(text, read_optional(text, skip_ws(text, m.end())))
            got = read_braced(text, p)
            if got is None:                                        # vd. \newcommand{\x}{\section}: không phải heading
                pos = m.end()
                continue
            title, p_end = got
            flush(m.start())
            blocks.append(("heading", m.group("cmd"), title.strip()))
            start = pos = p_end

        elif m.group("env") is not None:                           # \begin{...}
            name = m.group("env").strip()
            base = name.rstrip("*")
            end = find_env_end(text, m.end(), name)
            if end is None:
                pos = m.end()
                continue
            e_start, e_end = end
            inner, raw = text[m.end():e_start], text[m.start():e_end]
            flush(m.start())
            if base in SKIP_ENVS:
                pass
            elif base in MATH_ENVS:
                blocks.append(("equation", raw))
            elif base in FIGURE_ENVS:
                blocks.append(("figure", raw))
            elif base in LIST_ENVS:
                preface, items = split_items(inner[read_optional(inner, skip_ws(inner, 0)):])  # bỏ [label=...]
                blocks.extend(("paragraph", p) for p in split_paragraphs(preface))
                blocks.append(("itemize", [scan_blocks(it) for it in items]))
            elif base == "abstract":
                blocks.append(("container", "Abstract", scan_blocks(inner)))
            else:                                                  # theorem, proof, center, minipage...: mở ra
                blocks.extend(scan_blocks(inner))
            start = pos = e_end

        elif m.group("dd"):                                        # $$ ... $$
            close = find_unescaped(text, "$$", m.end())
            if close < 0:
                pos = m.end()
                continue
            flush(m.start())
            blocks.append(("equation", text[m.start():close + 2]))
            start = pos = close + 2

        elif m.group("br"):                                        # \[ ... \]
            close = find_unescaped(text, "\\]", m.end())
            if close < 0:
                pos = m.end()
                continue
            flush(m.start())
            blocks.append(("equation", text[m.start():close + 2]))
            start = pos = close + 2

        elif m.group("inl"):                                       # $ ... $ : nhảy qua, không cắt đoạn
            close = find_inline_close(text, m.end())
            pos = close + 1 if close >= 0 else m.end()

        elif m.group("paren"):                                     # \( ... \)
            close = find_unescaped(text, "\\)", m.end())
            pos = close + 2 if close >= 0 else m.end()

        else:                                                      # \maketitle, \appendix, \bibliography{..}...
            flush(m.start())
            start = pos = m.end()

    flush(len(text))
    return blocks


# ----------------------------------------------------------------------------
# 4. DỰNG CÂY
# ----------------------------------------------------------------------------
def is_references_title(title: str) -> bool:
    plain = re.sub(r"\\[A-Za-z]+\*?|[{}]", "", title).strip()
    return bool(REFERENCES_TITLE_RE.match(plain))


def compute_levels(blocks: list[tuple]) -> dict[str, int]:
    """Đánh số cấp theo các lệnh phân mục bài THỰC SỰ dùng: cấp cao nhất = 1, rồi 2, 3...
    (bài có \\chapter: chapter=1, section=2; bài chỉ có \\section và \\paragraph: 1 và 2)."""
    used = sorted({b[1] for b in blocks if b[0] == "heading"}, key=SECTION_RANK.get)
    return {cmd: i + 1 for i, cmd in enumerate(used)}


def to_node(b: tuple, level: int = 1) -> dict:
    """Đổi một khối (không phải heading) thành nút của tree_raw.json."""
    kind = b[0]
    if kind == "paragraph":
        return {"type": "paragraph", "text": b[1]}
    if kind in ("equation", "figure"):
        return {"type": kind, "text": b[1]}
    if kind == "itemize":
        return {"type": "itemize",
                "children": [{"type": "item", "children": nested_nodes(item, level)} for item in b[1]]}
    if kind == "container":                                        # abstract -> một section
        return {"type": "section", "level": level, "title": b[1], "children": nested_nodes(b[2], level)}
    raise ValueError(f"khối không hiểu: {kind}")


def nested_nodes(blocks: list[tuple], level: int) -> list[dict]:
    """Nút con bên trong item/abstract. Heading hiếm gặp ở đây nên chỉ giữ lại tiêu đề như một đoạn văn."""
    return [{"type": "paragraph", "text": b[2]} if b[0] == "heading" else to_node(b, level + 1) for b in blocks]


def build_tree(blocks: list[tuple]) -> dict:
    r"""Lồng các heading thành cây; bỏ mục References cho đến khi gặp heading cùng cấp hoặc cao hơn
    (vd. \section{Proofs} của phụ lục)."""
    levels = compute_levels(blocks)
    root = {"type": "document", "children": []}
    stack = [(0, root)]                  # (cấp, nút) - nút đang mở ở từng độ sâu
    skip_level = None                    # đang bỏ mục References ở cấp này
    for b in blocks:
        if b[0] == "heading":
            lvl = levels[b[1]]
            if skip_level is not None:
                if lvl > skip_level:
                    continue
                skip_level = None
            if is_references_title(b[2]):
                skip_level = lvl
                continue
            while stack[-1][0] >= lvl:
                stack.pop()
            node = {"type": "section", "level": lvl, "title": b[2], "children": []}
            stack[-1][1]["children"].append(node)
            stack.append((lvl, node))
        elif skip_level is None:
            stack[-1][1]["children"].append(to_node(b, stack[-1][0] + 1))
    return root


# ----------------------------------------------------------------------------
# 5. MỘT VERSION / MỘT BÀI / NHIỀU BÀI
# ----------------------------------------------------------------------------
def parse_version(vdir: Path, arxiv_id: str | None = None) -> dict:
    """Dựng tree_raw cho một thư mục vN. Luôn trả về một dict hợp lệ, kể cả khi version rỗng/lỗi.

    status: "ok" | "empty" (không có .tex, bản rỗng của M1) | "no_root" (có .tex nhưng không tìm được file gốc)
            | "error" (parser gặp lỗi, kèm khóa "error")
    """
    vdir = Path(vdir)
    out = {"arxiv_id": arxiv_id, "version": vdir.name, "status": "ok", "root_file": None, "reason": None,
           "n_unused": 0, "n_unresolved": 0, "root": {"type": "document", "children": []}}
    try:
        res = G.find_root_file(vdir)
        out["root_file"], out["reason"] = res.root, res.reason
        if res.root is None:
            out["status"] = "empty" if res.reason == "no_tex_files" else "no_root"
            return out
        comp = G.collect_compilation_files(vdir, root=res.root)
        out["n_unused"], out["n_unresolved"] = len(comp.unused), len(comp.unresolved)
        body = extract_body(expand_includes(vdir, res.root))
        out["root"] = build_tree(scan_blocks(body))
    except Exception as exc:  # noqa: BLE001 - một bài lỗi không được làm hỏng cả lượt chạy
        out["status"], out["error"] = "error", f"{type(exc).__name__}: {exc}"
        out["root"] = {"type": "document", "children": []}
    return out


def process_paper(arxiv_id: str, skip_existing: bool = False) -> list[tuple[str, str]]:
    """Dựng và ghi tree_raw.json cho mọi version đã có của một bài. Trả về [(vN, status), ...]."""
    results = []
    for vdir in G.version_dirs(arxiv_id):
        target = vdir / "tree_raw.json"
        if skip_existing and target.exists():
            continue
        tree = parse_version(vdir, arxiv_id)
        target.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        results.append((vdir.name, tree["status"]))
    return results


def read_arxiv_id(pdir: Path) -> str | None:
    """arXiv ID gốc của một thư mục <paper_key> (đọc từ versions.json hoặc meta.json)."""
    for name in ("versions.json", "meta.json"):
        try:
            aid = json.loads((pdir / name).read_text(encoding="utf-8")).get("arxiv_id")
        except (OSError, ValueError):
            continue
        if aid:
            return aid
    return None


def list_all_ids() -> list[str]:
    """arXiv ID của mọi bài đang có trong data/."""
    ids = []
    for d in sorted(Path(DATA_DIR).iterdir()) if Path(DATA_DIR).is_dir() else []:
        if d.is_dir():
            aid = read_arxiv_id(d)
            if aid:
                ids.append(aid)
    return ids


def print_outline(node: dict, depth: int = 0, width: int = 70) -> None:
    """In cây dạng thụt lề để mắt kiểm tra nhanh."""
    label = node["type"] + (f"[{node['level']}]" if "level" in node else "")
    text = node.get("title") or node.get("text") or ""
    text = " ".join(text.split())
    print("  " * depth + label + (f": {text[:width]}" if text else ""))
    for c in node.get("children", []):
        print_outline(c, depth + 1, width)


# ----------------------------------------------------------------------------
# 6. DÒNG LỆNH
# ----------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dựng tree_raw.json cho các version đã tải (M2).")
    p.add_argument("--range", help="START-END, vd. 2512.23231-2601.03155")
    p.add_argument("--month-last", nargs="*", default=[], help="vd. 2512=25075")
    p.add_argument("--ids", nargs="*", help="Một hoặc nhiều arXiv ID")
    p.add_argument("--all", action="store_true", help="mọi bài đang có trong data/")
    p.add_argument("--limit", type=int, help="chỉ xử lý N ID đầu")
    p.add_argument("--skip-existing", action="store_true", help="bỏ qua version đã có tree_raw.json")
    p.add_argument("--outline", action="store_true", help="in cây thụt lề của từng version (để kiểm tra bằng mắt)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ids: list[str] = []
    if args.range:
        ids += list(iter_id_range(*parse_id_range(args.range), parse_month_last(args.month_last)))
    ids += list(args.ids or [])
    if args.all:
        ids += list_all_ids()
    ids = list(dict.fromkeys(ids))
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        print("Chưa có arXiv ID nào. Dùng --range, --ids hoặc --all.")
        return

    t0 = time.time()
    counts: collections.Counter = collections.Counter()
    for aid in ids:
        for vname, status in process_paper(aid, skip_existing=args.skip_existing):
            counts[status] += 1
            if args.outline:
                tree = json.loads((paper_dir(aid) / vname / "tree_raw.json").read_text(encoding="utf-8"))
                print(f"\n=== {aid} {vname} [{status}] root={tree['root_file']} ===")
                print_outline(tree["root"])
    n = sum(counts.values())
    print("\n=== TỔNG KẾT DỰNG CÂY ===")
    print(f"Số bài: {len(ids)} | số version: {n} | thời gian: {time.time() - t0:.1f}s")
    for status, c in counts.most_common():
        print(f"  {status:<10} {c:>6}  ({100 * c / n if n else 0:4.1f}%)")


if __name__ == "__main__":
    main()
