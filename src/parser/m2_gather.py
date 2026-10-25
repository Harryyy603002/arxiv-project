r"""m2_gather.py

Milestone: M2 - Multi-file Gathering
Người phụ trách: P1
Mục đích: Tìm FILE GỐC của một version bài báo (file .tex chứa \documentclass).
          (Bước 2, làm sau: đi theo \input / \include để gom các file nằm trên
          đường biên dịch - xem phần TODO ở cuối file.)

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
  - Chưa hiểu gói `import` (\import{dir/}{file}) và file 00README.XXX của arXiv.
"""

from __future__ import annotations

import argparse
import collections
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
INCLUDE_RE = re.compile(
    r"\\(?:input|include|subfile|subfileinclude)\s*(?:\{\s*([^}]*?)\s*\}|([^\s{}\\%]+))"
)


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
    t = raw.strip().replace("\\", "/")
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


def scan(ids: list[str], verbose: bool = False, show: int = 5) -> dict:
    """Chạy find_root_file cho mọi version đã tải của các bài trong `ids`."""
    counts: collections.Counter = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    papers_without_source = n_versions = 0

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
    return {"counts": dict(counts), "n_versions": n_versions,
            "papers_without_source": papers_without_source}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tìm file gốc (\\documentclass) của các version đã tải (M2).")
    p.add_argument("--range", help="START-END, vd. 2512.23231-2601.03155")
    p.add_argument("--month-last", nargs="*", default=[],
                   help="vd. 2512=25075 (khi khoảng qua nhiều tháng)")
    p.add_argument("--ids", nargs="*", help="Một hoặc nhiều arXiv ID")
    p.add_argument("--limit", type=int, help="chỉ quét N ID đầu")
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
    scan(ids, verbose=args.verbose, show=args.show)


# TODO(P1, bước 2): collect_compilation_files(root_path)
#   - đi theo \input / \include đệ quy bắt đầu từ file gốc,
#   - bỏ qua lệnh đã bị comment (dùng strip_comments ở trên),
#   - chống vòng lặp (A include B, B include A),
#   - trả về danh sách file nằm trên đường biên dịch.

if __name__ == "__main__":
    main()
