r"""m2_gather.py

Milestone: M2 - Multi-file Gathering
Người phụ trách: P1
Mục đích: 1) find_root_file: tìm FILE GỐC của một version (file .tex chứa \documentclass).
          2) collect_compilation_files: từ file gốc, đi theo \input / \include đệ quy
             để gom mọi file nằm trên đường biên dịch (các file còn lại là file thừa).

Đầu vào:  data/<paper_key>/vN/*.tex  (do m1_download tạo)
Đầu ra:   RootResult(root, candidates, reason, n_tex_files)
          - root:       đường dẫn file gốc (tương đối so với thư mục vN) hoặc None
          - candidates: các file ứng viên, xếp từ khả năng cao nhất xuống thấp nhất
          - reason:     độ tin cậy của kết quả (xem bảng REASONS bên dưới)

Cách dùng trong code:
    from parser.m2_gather import find_root_file
    result = find_root_file(version_dir("2512.23231", 1))
    print(result.root, result.reason)

Cách chạy thử trên dữ liệu thật (từ thư mục gốc của repo):
    python src/parser/m2_gather.py --range 2512.23231-2601.03155 --month-last 2512=25075 --limit 30

Ý tưởng chọn file gốc (theo đề M2: "file .tex chứa \documentclass"):
  1. Chỉ xét file .tex. Bỏ phần comment (sau dấu % chưa bị escape), vì một dòng
     "% \documentclass{article}" chỉ là chú thích, không phải lệnh thật.
  2. Ứng viên = file có \documentclass (hoặc \documentstyle của LaTeX 2.09 cũ).
  3. Nếu có NHIỀU ứng viên, xếp hạng theo thứ tự ưu tiên:
        a. có \begin{document}  (file gốc thật phải có phần thân tài liệu)
        b. KHÔNG phải lớp "standalone"/"subfiles" (đó là file hình/phụ, không phải bài chính)
        c. KHÔNG bị file khác \input/\include (file bị include thì không phải gốc)
        d. có nhiều lệnh \input/\include nhất (file chính thường gom các phần)
        e. tên quen thuộc (main, ms, paper, article, manuscript...)
        f. nằm nông nhất (ít thư mục cha nhất), rồi file lớn hơn, rồi theo tên
  4. Ghi lại độ tin cậy trong `reason` để sau này thống kê được tỉ lệ chọn đúng.

Hạn chế đã biết (ghi nhận để xử lý sau nếu còn thời gian):
  - Gốc "đội lốt": main.tex chỉ có \input{preamble} + \begin{document}, còn
    \documentclass nằm trong preamble.tex. Khi đó ta chọn preamble.tex và gắn
    reason = "root_without_begin_document" để biết mà kiểm tra.
  - Chưa hiểu gói `import` (\import{dir/}{file}), \includeonly, khối \iffalse...\fi
    và file 00README.XXX của arXiv.
  - Crawler chỉ giữ .tex và .bib, nên \input của file khác (.tikz, .pgf, .txt...) sẽ
    nằm trong `unresolved` - đó là hệ quả của quy tắc M1, không phải lỗi parser.
"""

from __future__ import annotations

import argparse
import collections
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.config import iter_id_range, parse_id_range, parse_month_last, paper_dir  # noqa: E402

# ----------------------------------------------------------------------------
# HẰNG SỐ
# ----------------------------------------------------------------------------
# reason -> ý nghĩa
REASONS = {
    "ok": "Chọn được 1 file gốc rõ ràng",
    "ambiguous": "Có nhiều ứng viên ngang nhau, đã chọn theo thứ tự ưu tiên (nên kiểm tra mẫu)",
    "weak_candidate": "Chỉ có ứng viên yếu (lớp standalone/subfiles hoặc bị file khác include)",
    "root_without_begin_document": "Có \\documentclass nhưng không có \\begin{document}",
    "fallback_begin_document": "Không có \\documentclass nhưng có file chứa \\begin{document}",
    "no_documentclass": "Có file .tex nhưng không file nào có \\documentclass/\\begin{document}",
    "no_tex_files": "Không có file .tex nào (bản ghi rỗng)",
}

SUB_CLASSES = {"standalone", "subfiles"}                      # lớp tài liệu phụ
PREFERRED_STEMS = {"main", "ms", "paper", "article", "manuscript", "root", "arxiv"}

# \documentclass[tùy chọn]{tên}   hoặc   \documentstyle[tùy chọn]{tên}
CLASS_RE = re.compile(r"\\document(?:class|style)\s*(?:\[[^\]]*\])?\s*\{\s*([^}\s]+)\s*\}")
BEGIN_DOC_RE = re.compile(r"\\begin\s*\{\s*document\s*\}")
# \input{file}, \include{file}, \subfile{file}  hoặc  \input file  (không ngoặc)
# (?![A-Za-z@]) = ngay sau tên lệnh KHÔNG được là chữ cái, để không nhận nhầm
# \includegraphics, \includeonly, \inputencoding... là lệnh include.
INCLUDE_RE = re.compile(
    r"\\(?:input|include|subfile|subfileinclude)(?![A-Za-z@])\s*"
    r"(?:\{\s*([^}]*?)\s*\}|([^\s{}\\%]+))"
)
# Môi trường comment của gói `comment`: \begin{comment} ... \end{comment} không được biên dịch
COMMENT_ENV_RE = re.compile(r"\\begin\s*\{comment\}.*?\\end\s*\{comment\}", re.DOTALL)


# ----------------------------------------------------------------------------
# 1. XỬ LÝ TEXT
# ----------------------------------------------------------------------------
def strip_comments(text: str) -> str:
    r"""Bỏ phần comment của LaTeX: từ dấu % (chưa bị escape) đến hết dòng.

    - "50\% done"   -> giữ nguyên (\% là ký tự % thật, không phải comment)
    - "a \\% note"  -> "a \\"       (\\ là xuống dòng, % sau đó MỚI là comment)
    - "% hello"     -> ""           (cả dòng là comment)

    Cách làm: đi từng ký tự. Gặp "\" thì bỏ qua luôn ký tự đứng sau nó (cặp
    "\x" là một lệnh/ký tự thoát). Gặp "%" lẻ loi thì cắt phần còn lại của dòng.
    """
    out_lines = []
    for line in text.split("\n"):
        i, n, cut = 0, len(line), None
        while i < n:
            ch = line[i]
            if ch == "\\":
                i += 2          # bỏ qua cả "\" và ký tự ngay sau nó
                continue
            if ch == "%":
                cut = i
                break
            i += 1
        out_lines.append(line if cut is None else line[:cut])
    return "\n".join(out_lines)


def read_tex(path: Path) -> str:
    """Đọc file .tex thành text. File arXiv có thể không phải UTF-8 (vd. latin-1),
    nên dùng errors="replace" để không bao giờ bị lỗi giải mã."""
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_include_target(raw: str) -> str:
    """'./sections/intro.tex' -> 'sections/intro' (để so sánh với đường dẫn file)."""
    t = raw.strip()   # không đổi "\" thành "/": trên Linux "\" không phải dấu phân cách, và \macro cần giữ nguyên
    while t.startswith("./"):
        t = t[2:]
    if t.lower().endswith(".tex"):
        t = t[:-4]
    return t


# ----------------------------------------------------------------------------
# 2. PHÂN TÍCH TỪNG FILE .tex
# ----------------------------------------------------------------------------
@dataclass
class TexInfo:
    rel: str                      # đường dẫn tương đối, dạng posix: "sections/intro.tex"
    size: int
    class_name: Optional[str]     # tên lớp trong \documentclass{...}, None nếu không có
    has_begin_document: bool
    includes: list[str] = field(default_factory=list)   # các mục tiêu \input/\include (đã chuẩn hóa)

    @property
    def stem(self) -> str:
        return Path(self.rel).stem.lower()

    @property
    def depth(self) -> int:
        return self.rel.count("/")

    @property
    def rel_noext(self) -> str:
        return self.rel[:-4] if self.rel.lower().endswith(".tex") else self.rel


def analyze_tex(path: Path, version_path: Path) -> TexInfo:
    """Đọc một file .tex và rút ra các thông tin cần để chọn file gốc."""
    text = strip_comments(read_tex(path))
    m = CLASS_RE.search(text)
    includes = []
    for g1, g2 in INCLUDE_RE.findall(text):
        target = normalize_include_target(g1 or g2)
        if target:
            includes.append(target)
    return TexInfo(
        rel=path.relative_to(version_path).as_posix(),
        size=path.stat().st_size,
        class_name=m.group(1).lower() if m else None,
        has_begin_document=bool(BEGIN_DOC_RE.search(text)),
        includes=includes,
    )


def list_tex_files(version_path: Path) -> list[Path]:
    """Mọi file .tex (kể cả trong thư mục con), sắp xếp để kết quả ổn định."""
    return sorted(p for p in version_path.rglob("*") if p.is_file() and p.suffix.lower() == ".tex")


# ----------------------------------------------------------------------------
# 3. CHỌN FILE GỐC
# ----------------------------------------------------------------------------
@dataclass
class RootResult:
    root: Optional[str]            # file gốc (tương đối so với thư mục vN) hoặc None
    candidates: list[str]          # ứng viên đã xếp hạng, tốt nhất trước
    reason: str                    # xem REASONS
    n_tex_files: int = 0


def _is_included_by_other(info: TexInfo, all_infos: list[TexInfo]) -> bool:
    """File này có bị một file KHÁC \\input/\\include không?"""
    for other in all_infos:
        if other.rel == info.rel:
            continue
        for target in other.includes:
            # khớp đường dẫn đầy đủ, hoặc đường dẫn tính từ một thư mục con
            if target == info.rel_noext or info.rel_noext.endswith("/" + target):
                return True
    return False


def find_root_file(version_path: Path) -> RootResult:
    """Tìm file gốc của một version. Xem giải thích ở đầu file."""
    version_path = Path(version_path)
    tex_paths = list_tex_files(version_path) if version_path.is_dir() else []
    if not tex_paths:
        return RootResult(None, [], "no_tex_files", 0)

    infos = [analyze_tex(p, version_path) for p in tex_paths]
    candidates = [i for i in infos if i.class_name is not None]

    # Không file nào có \documentclass -> thử file có \begin{document}
    if not candidates:
        with_body = [i for i in infos if i.has_begin_document]
        if not with_body:
            return RootResult(None, [], "no_documentclass", len(infos))
        with_body.sort(key=lambda i: (-len(i.includes), i.depth, -i.size, i.rel))
        names = [i.rel for i in with_body]
        return RootResult(names[0], names, "fallback_begin_document", len(infos))

    included = {i.rel: _is_included_by_other(i, infos) for i in candidates}

    def rank_key(i: TexInfo):
        return (
            0 if i.has_begin_document else 1,        # a. có \begin{document}
            1 if i.class_name in SUB_CLASSES else 0,  # b. không phải standalone/subfiles
            1 if included[i.rel] else 0,              # c. không bị file khác include
            -len(i.includes),                         # d. nhiều \input/\include hơn
            0 if i.stem in PREFERRED_STEMS else 1,    # e. tên quen thuộc
            i.depth,                                  # f. nằm nông hơn
            -i.size,                                  #    file lớn hơn
            i.rel,                                    #    cuối cùng: theo tên (ổn định)
        )

    candidates.sort(key=rank_key)
    best = candidates[0]
    names = [i.rel for i in candidates]

    strong = [i for i in candidates
              if i.has_begin_document and i.class_name not in SUB_CLASSES and not included[i.rel]]
    if not best.has_begin_document:
        reason = "root_without_begin_document"
    elif best.class_name in SUB_CLASSES or included[best.rel]:
        reason = "weak_candidate"
    elif len(strong) > 1:
        reason = "ambiguous"
    else:
        reason = "ok"
    return RootResult(best.rel, names, reason, len(infos))


# ----------------------------------------------------------------------------
# 3b. ĐI THEO \input / \include ĐỆ QUY (gom file nằm trên đường biên dịch)
# ----------------------------------------------------------------------------
@dataclass
class CompilationResult:
    root: Optional[str]                         # file gốc (None nếu không tìm được)
    files: list[str] = field(default_factory=list)      # file nằm trên đường biên dịch, theo thứ tự gặp lần đầu
    edges: list[tuple[str, str]] = field(default_factory=list)       # (file_chứa_lệnh, file_được_include)
    unresolved: list[tuple[str, str]] = field(default_factory=list)  # (file_chứa_lệnh, mục tiêu KHÔNG tìm thấy)
    cycles: list[tuple[str, str]] = field(default_factory=list)      # (file, file bị include ngược lại -> vòng lặp)
    unused: list[str] = field(default_factory=list)     # file .tex có trong thư mục nhưng KHÔNG được biên dịch


def prepare_for_includes(text: str) -> str:
    r"""Chuẩn bị text để tìm lệnh include: bỏ comment bằng %, rồi bỏ khối
    \begin{comment}...\end{comment}. Lệnh nằm trong phần bị bỏ chỉ là văn bản thường."""
    return COMMENT_ENV_RE.sub("", strip_comments(text))


def find_include_targets(text: str) -> list[str]:
    r"""Các mục tiêu \input/\include trong text (đã bỏ comment), theo thứ tự xuất hiện."""
    targets = []
    for g1, g2 in INCLUDE_RE.findall(prepare_for_includes(text)):
        t = normalize_include_target(g1 or g2)
        if t:
            targets.append(t)
    return targets


def resolve_include(target: str, including_rel: str, root_dir: str, tex_set: set) -> Optional[str]:
    r"""Tìm file .tex ứng với một mục tiêu \input/\include.

    LaTeX tìm đường dẫn tính từ thư mục đang chạy (thư mục của file gốc), nên thử
    lần lượt: (1) thư mục của file gốc, (2) thư mục của file chứa lệnh, (3) thư mục
    ngoài cùng của version. Mục tiêu có macro (\\path, #1, $) thì không giải được.
    """
    if not target or any(ch in target for ch in "\\#$~"):
        return None
    bases = []
    for b in (root_dir, posixpath.dirname(including_rel), ""):
        if b not in bases:
            bases.append(b)
    for base in bases:
        path = posixpath.normpath(posixpath.join(base, target))
        if path.startswith("..") or path.startswith("/"):
            continue
        for candidate in (path + ".tex", path):
            if candidate in tex_set:
                return candidate
    return None


def collect_compilation_files(version_path: Path, root: Optional[str] = None) -> CompilationResult:
    r"""Từ file gốc, đi theo \input/\include đệ quy và trả về các file trên đường biên dịch.

    - Lệnh bị comment (%...) hoặc nằm trong \begin{comment}...\end{comment} bị bỏ qua.
    - Mỗi file chỉ được liệt kê một lần (kể cả khi được include nhiều nơi).
    - Chống vòng lặp: A include B, B include A -> ghi vào `cycles`, không lặp vô hạn.
    - File .tex không được tới từ file gốc -> `unused` (file thừa, bị bỏ qua theo đề).
    - Mục tiêu không tìm thấy (vd. file .tikz không được lưu vì crawler chỉ giữ .tex/.bib)
      -> `unresolved`.
    """
    version_path = Path(version_path)
    tex_rels = [p.relative_to(version_path).as_posix() for p in list_tex_files(version_path)]
    if root is None:
        root = find_root_file(version_path).root
    if root is None:
        return CompilationResult(root=None, unused=tex_rels)

    tex_set = set(tex_rels)
    root_dir = posixpath.dirname(root)
    result = CompilationResult(root=root)
    visited: set[str] = set()
    stack: list[str] = []

    def visit(rel: str) -> None:
        visited.add(rel)
        stack.append(rel)
        result.files.append(rel)
        for target in find_include_targets(read_tex(version_path / rel)):
            child = resolve_include(target, rel, root_dir, tex_set)
            if child is None:
                result.unresolved.append((rel, target))
                continue
            result.edges.append((rel, child))
            if child in stack:
                result.cycles.append((rel, child))
            elif child not in visited:
                visit(child)
        stack.pop()

    visit(root)
    result.unused = [t for t in tex_rels if t not in visited]
    return result


# ----------------------------------------------------------------------------
# 4. CHẠY THỬ TRÊN DỮ LIỆU THẬT
# ----------------------------------------------------------------------------
_VERSION_DIR_RE = re.compile(r"^v\d+$")


def version_dirs(arxiv_id: str) -> list[Path]:
    """Các thư mục v1, v2, ... đã có của một bài (theo thứ tự số)."""
    pdir = paper_dir(arxiv_id)
    if not pdir.is_dir():
        return []
    vs = [d for d in pdir.iterdir() if d.is_dir() and _VERSION_DIR_RE.match(d.name)]
    return sorted(vs, key=lambda d: int(d.name[1:]))


def scan(ids: list[str], verbose: bool = False, show: int = 5, gather: bool = False) -> dict:
    """Chạy find_root_file cho mọi version đã tải của các bài trong `ids`."""
    counts: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    papers_without_source = n_versions = 0
    g = collections.Counter()   # thống kê phần gom file (khi gather=True)

    for aid in ids:
        vdirs = version_dirs(aid)
        if not vdirs:
            papers_without_source += 1      # bài chưa được crawl source
            continue
        for vdir in vdirs:
            n_versions += 1
            res = find_root_file(vdir)
            counts[res.reason] += 1
            line = f"{aid} {vdir.name}: root={res.root}  candidates={res.candidates[:4]}"
            if verbose:
                print(f"[{res.reason}] {line}")
            if gather and res.root is not None:
                comp = collect_compilation_files(vdir, root=res.root)
                g["versions"] += 1
                g["files"] += len(comp.files)
                g["unresolved_total"] += len(comp.unresolved)
                g["unused_total"] += len(comp.unused)
                g["with_unresolved"] += bool(comp.unresolved)
                g["with_unused"] += bool(comp.unused)
                g["with_cycles"] += bool(comp.cycles)
                if verbose:
                    print(f"      gom {len(comp.files)} file | không tìm thấy: {len(comp.unresolved)}"
                          f" | file thừa: {len(comp.unused)} | vòng lặp: {len(comp.cycles)}")
                    print(f"      files: {comp.files[:12]}{' ...' if len(comp.files) > 12 else ''}")
                    if comp.unresolved:
                        print(f"      KHÔNG TÌM THẤY: {comp.unresolved[:8]}")
                    if comp.unused:
                        print(f"      FILE THỪA: {comp.unused[:8]}")
            if res.reason != "ok" and len(examples[res.reason]) < show:
                examples[res.reason].append(line)

    print("\n=== TỔNG KẾT TÌM FILE GỐC ===")
    print(f"Số bài trong danh sách: {len(ids)} | chưa có source: {papers_without_source}"
          f" | số version đã quét: {n_versions}")
    for reason, c in counts.most_common():
        pct = 100 * c / n_versions if n_versions else 0
        print(f"  {reason:<30} {c:>6}  ({pct:4.1f}%)  - {REASONS.get(reason, '')}")
    for reason, lines in examples.items():
        print(f"\nVí dụ [{reason}]:")
        for ln in lines:
            print("  ", ln)
    if gather and g["versions"]:
        n = g["versions"]
        print("\n=== TỔNG KẾT GOM FILE (theo \\input/\\include) ===")
        print(f"Số version đã gom: {n} | số file trung bình mỗi version: {g['files'] / n:.1f}")
        print(f"  Version có lệnh include KHÔNG tìm thấy file: {g['with_unresolved']} ({100 * g['with_unresolved'] / n:.1f}%)"
              f" | tổng {g['unresolved_total']} lệnh")
        print(f"  Version có file .tex thừa (không được biên dịch): {g['with_unused']} ({100 * g['with_unused'] / n:.1f}%)"
              f" | tổng {g['unused_total']} file")
        print(f"  Version có vòng lặp include: {g['with_cycles']}")
    return {"counts": dict(counts), "n_versions": n_versions,
            "papers_without_source": papers_without_source, "gather": dict(g)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tìm file gốc (\\documentclass) của các version đã tải (M2).")
    p.add_argument("--range", help="START-END, vd. 2512.23231-2601.03155")
    p.add_argument("--month-last", nargs="*", default=[],
                   help="vd. 2512=25075 (khi khoảng qua nhiều tháng)")
    p.add_argument("--ids", nargs="*", help="Một hoặc nhiều arXiv ID")
    p.add_argument("--limit", type=int, help="chỉ quét N ID đầu")
    p.add_argument("--gather", action="store_true",
                   help="sau khi tìm file gốc, đi theo \\input/\\include và thống kê các file gom được")
    p.add_argument("--verbose", action="store_true", help="in kết quả của từng version")
    p.add_argument("--show", type=int, default=5, help="số ví dụ in cho mỗi loại reason khác 'ok'")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ids: list[str] = []
    if args.range:
        ids += list(iter_id_range(*parse_id_range(args.range), parse_month_last(args.month_last)))
    ids += list(args.ids or [])
    ids = list(dict.fromkeys(ids))
    if args.limit:
        ids = ids[:args.limit]
    if not ids:
        print("Chưa có arXiv ID nào. Dùng --range hoặc --ids.")
        return
    scan(ids, verbose=args.verbose, show=args.show, gather=args.gather)


if __name__ == "__main__":
    main()
