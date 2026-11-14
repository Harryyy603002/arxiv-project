r"""m2_inspect.py

Milestone: M2
Mục đích: in tóm tắt đầu ra M2 của một bài để kiểm tra bằng mắt: trạng thái từng version,
          số element theo kind, phần dùng chung giữa các version, mẫu câu/công thức/hình,
          dấu hiệu lỗi (còn sót lệnh LaTeX, câu quá dài/quá ngắn) và BibTeX/ứng viên.

Chạy (từ thư mục gốc repo), sau khi đã chạy src/run_m2.py:
    python src/parser/m2_inspect.py --corpus-dir data_m2 --ids 2601.00424
    python src/parser/m2_inspect.py --corpus-dir data_m2 --ids 2601.00424 --n 15
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402

# Lệnh LaTeX được phép còn trong câu (đề giữ nguyên \cite và \ref)
_ALLOWED = {"cite", "citep", "citet", "citealp", "citeauthor", "citeyear", "ref", "eqref", "cref", "Cref",
            "autoref", "label", "url", "href", "footnote", "emph", "textit", "textbf"}
_CMD_RE = re.compile(r"\\([A-Za-z]+)")
_MATH_RE = re.compile(r"\$[^$]*\$")


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _short(text: str, width: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 1] + "…"


def inspect_paper(pdir: Path, n: int) -> None:
    paper = _load(pdir / "paper.json") or {}
    arxiv_id = paper.get("arxiv_id") or pdir.name
    print(f"\n{'=' * 78}\n{arxiv_id}   (thư mục {pdir.name})\n{'=' * 78}")

    status = _load(pdir / "parse_status.json")
    elements = _load(pdir / "elements.json")
    edges = _load(pdir / "hierarchy.json")
    if status is None or elements is None or edges is None:
        print("THIẾU elements/hierarchy/parse_status.json: chưa chạy run_m2.py cho bài này?")
        return

    print("\n[Trạng thái từng version]")
    for label, info in status["versions"].items():
        extra = f"  ({info['reason']})" if info.get("reason") else ""
        print(f"  {label}: {info['status']:8} nút={info['n_nodes']}{extra}")
    print(f"  trước dedup={status['n_nodes_before_dedup']}  sau dedup={status['n_elements_after_dedup']}")

    by_id = {e["element_id"]: e for e in elements}
    kinds = Counter(e["kind"] for e in elements)
    print("\n[Element theo kind]  " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))

    versions_of = defaultdict(set)
    for edge in edges:
        versions_of[edge["element_id"]].add(edge["version"])
    shared = [eid for eid, vs in versions_of.items() if len(vs) > 1]
    print(f"[Dùng chung giữa các version] {len(shared)}/{len(versions_of)} element xuất hiện ở từ 2 version trở lên")
    dangling = [e for e in edges if e["element_id"] not in by_id or e["parent_id"] not in by_id]
    print(f"[Toàn vẹn] cạnh trỏ tới element không tồn tại: {len(dangling)}")

    sentences = [e["content"] for e in elements if e["kind"] == "sentence"]
    leftovers = []
    for text in sentences:
        plain = _MATH_RE.sub(" ", text)
        bad = {c for c in _CMD_RE.findall(plain) if c not in _ALLOWED}
        if bad:
            leftovers.append((sorted(bad), text))
    print("\n[Dấu hiệu lỗi trong câu]")
    print(f"  còn sót lệnh LaTeX lạ: {len(leftovers)}/{len(sentences)} câu")
    freq = Counter(cmd for bad, _ in leftovers for cmd in bad)
    if freq:
        print("  lệnh sót nhiều nhất: " + ", ".join(f"\\{c} ({k})" for c, k in freq.most_common(10)))
    for bad, text in leftovers[:5]:
        print(f"     {bad}: {_short(text, 90)}")
    long_sentences = [t for t in sentences if len(t) > 600]
    print(f"  câu quá dài (>600 ký tự): {len(long_sentences)}   "
          f"quá ngắn (<12 ký tự): {sum(len(t) < 12 for t in sentences)}")
    for text in long_sentences[:2]:
        print(f"     dài {len(text)}: {_short(text, 100)}")

    def show(kind: str, count: int) -> None:
        rows = [e for e in elements if e["kind"] == kind][:count]
        print(f"\n[{kind} đầu tiên]")
        for e in rows:
            print(f"  {e['element_id']:24} {_short(e['content'])}")

    show("heading", n)
    show("sentence", n)
    show("formula", 3)
    show("figure", 2)

    rows = _load(pdir / "bibtex_merged.json") or []
    print(f"\n[BibTeX] {len(rows)} entry sau khử trùng", end="")
    if rows:
        pct = lambda f: round(100 * sum(bool(r.get(f)) for r in rows) / len(rows))  # noqa: E731
        print(f"  | có title {pct('title')}%  authors {pct('authors')}%  year {pct('year')}%  doi {pct('doi')}%")
        for r in rows[:min(n, 5)]:
            print(f"  {r['citation_key']:24} {r['entry_type']:13} {_short(r.get('title') or '(không có title)', 60)}"
                  f" | {len(r.get('authors') or [])} tác giả | {r.get('year')}")
    else:
        print(" (bài không có tài liệu tham khảo trong .bib/\\bibitem)")
    cite_map = _load(pdir / "cite_map.json") or {}
    stats = _load(pdir / "m2_stats.json") or {}
    print(f"  khóa bị gộp (cite_map): {len(cite_map)}   nguồn: "
          f"{stats.get('bibtex_from_bib_files', '?')} từ .bib, {stats.get('bibtex_from_bibitem', '?')} từ \\bibitem"
          f" (thô, trước khử trùng)")
    if stats:
        print(f"[Ứng viên] {stats['candidate_pairs']} cặp | entry không có ứng viên: "
              f"{stats['zero_candidate_entries']}/{stats['canonical_entries']} | "
              f"reference có arXiv ID: {stats['references_with_arxiv_id']}/{stats['references_ingested']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Xem nhanh đầu ra M2 của một hoặc vài bài.")
    ap.add_argument("--corpus-dir", type=Path, default=C.DATA_DIR)
    ap.add_argument("--ids", nargs="+", required=True, help="arXiv ID (vd. 2601.00424, hep-th/9901001)")
    ap.add_argument("--n", type=int, default=8, help="số mẫu mỗi loại")
    args = ap.parse_args()
    root = args.corpus_dir.expanduser().resolve()
    for arxiv_id in args.ids:
        pdir = root / C.paper_key(arxiv_id)
        if not pdir.is_dir():
            print(f"\nKHÔNG thấy thư mục {pdir.name} trong {root}: kiểm tra lại ID (đã gõ: {arxiv_id})")
            continue
        inspect_paper(pdir, args.n)


if __name__ == "__main__":
    main()
