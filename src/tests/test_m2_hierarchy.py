r"""test_m2_parser.py

Milestone: M2
Người phụ trách: P4 (hỗ trợ P1)
Mục đích: Test parser trên các bài mẫu và các trường hợp biên

Đầu vào: Bài mẫu
Đầu ra:  Kết quả test

Chạy: pytest src/tests/test_m2_hierarchy.py
Test offline: tự tạo thư mục version giả trong tmp_path, không cần dữ liệu thật.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
import m2_hierarchy as H  # noqa: E402


def write(root: Path, files: dict) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def doc(body: str) -> str:
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


def tree_of(body: str) -> dict:
    """Cây của một đoạn thân tài liệu (bỏ qua bước gom file)."""
    return H.build_tree(H.scan_blocks(body))


def kinds(node: dict) -> list:
    return [(c["type"], c.get("title") or c.get("text", "")[:20]) for c in node["children"]]


# ---------------------------------------------------------------- đoạn văn
def test_paragraphs_split_on_blank_line():
    t = tree_of("First paragraph.\nSame paragraph.\n\nSecond paragraph.")
    assert [c["text"] for c in t["children"]] == ["First paragraph.\nSame paragraph.", "Second paragraph."]


def test_noise_only_chunks_are_dropped():
    t = tree_of("\\label{sec:a}\n\n\\vspace{2mm}\n\nReal text.")
    assert [c["text"] for c in t["children"]] == ["Real text."]


def test_cite_is_kept_verbatim():
    t = tree_of("We follow \\cite{vaswani2017}. Next.")
    assert t["children"][0]["text"] == "We follow \\cite{vaswani2017}. Next."


# ---------------------------------------------------------------- phân cấp
def test_section_subsection_nesting():
    t = tree_of("\\section{A}\ntext a\n\\subsection{A1}\ntext a1\n\\section{B}\ntext b")
    a, b = t["children"]
    assert (a["title"], a["level"], b["title"], b["level"]) == ("A", 1, "B", 1)
    assert a["children"][0]["text"] == "text a"
    a1 = a["children"][1]
    assert (a1["title"], a1["level"], a1["children"][0]["text"]) == ("A1", 2, "text a1")


def test_text_before_first_section_goes_under_document():
    t = tree_of("Preface text.\n\n\\section{A}\nx")
    assert kinds(t) == [("paragraph", "Preface text."), ("section", "A")]


def test_levels_are_detected_automatically_with_chapters():
    t = tree_of("\\chapter{C}\n\\section{S}\n\\subsection{SS}\nx")
    c = t["children"][0]
    assert c["level"] == 1 and c["children"][0]["level"] == 2 and c["children"][0]["children"][0]["level"] == 3


def test_levels_when_subsection_follows_section_directly_and_paragraph_used():
    # bài chỉ dùng \section và \paragraph -> cấp 1 và 2
    t = tree_of("\\section{S}\n\\paragraph{P}\ntext")
    s = t["children"][0]
    assert s["level"] == 1 and s["children"][0]["level"] == 2 and s["children"][0]["title"] == "P"


def test_optional_argument_and_nested_braces_in_title():
    t = tree_of("\\section[Short]{A \\textbf{bold {nested}} title}\nx")
    assert t["children"][0]["title"] == "A \\textbf{bold {nested}} title"


def test_section_inside_newcommand_is_not_a_heading():
    t = tree_of("\\newcommand{\\mysec}{\\section}\n\nText.")
    assert all(c["type"] != "section" for c in t["children"])


# ---------------------------------------------------------------- *, appendix, References
def test_starred_section_and_acknowledgements_are_kept():
    t = tree_of("\\section{A}\nx\n\\section*{Acknowledgements}\nThanks.")
    assert [c["title"] for c in t["children"]] == ["A", "Acknowledgements"]


def test_references_section_is_dropped_but_appendix_after_it_is_kept():
    body = ("\\section{A}\nx\n\\section*{References}\nignored text\n\\subsection{ignored sub}\nmore\n"
            "\\appendix\n\\section{Proofs}\nproof text")
    t = tree_of(body)
    assert [c["title"] for c in t["children"]] == ["A", "Proofs"]
    assert t["children"][1]["children"][0]["text"] == "proof text"


def test_thebibliography_and_bibliography_command_are_dropped():
    body = ("\\section{A}\nx\n\\begin{thebibliography}{9}\n\\bibitem{a} Foo.\n\\end{thebibliography}\n"
            "\\bibliographystyle{plain}\n\\bibliography{refs}")
    t = tree_of(body)
    assert len(t["children"]) == 1 and t["children"][0]["children"][0]["text"] == "x"


def test_appendix_command_is_not_text():
    t = tree_of("\\section{A}\nx\n\\appendix\n\\section{B}\ny")
    assert [c["title"] for c in t["children"]] == ["A", "B"]
    assert "appendix" not in json.dumps(t)


def test_abstract_becomes_a_section():
    t = tree_of("\\begin{abstract}\nWe study X.\n\\end{abstract}\n\\section{Intro}\nx")
    assert kinds(t) == [("section", "Abstract"), ("section", "Intro")]
    assert t["children"][0]["children"][0]["text"] == "We study X."


# ---------------------------------------------------------------- công thức, hình, bảng
def test_equation_environment_is_a_leaf_with_raw_text():
    t = tree_of("Before.\n\\begin{equation}\nE=mc^2\n\\end{equation}\nAfter.")
    assert [c["type"] for c in t["children"]] == ["paragraph", "equation", "paragraph"]
    assert t["children"][1]["text"] == "\\begin{equation}\nE=mc^2\n\\end{equation}"


def test_double_dollar_and_bracket_blocks():
    t = tree_of("a\n$$x^2$$\nb\n\\[y^2\\]\nc")
    assert [c["type"] for c in t["children"]] == ["paragraph", "equation", "paragraph", "equation", "paragraph"]
    assert t["children"][1]["text"] == "$$x^2$$" and t["children"][3]["text"] == "\\[y^2\\]"


def test_align_and_starred_math_are_equations():
    t = tree_of("\\begin{align*}a&=b\\\\c&=d\\end{align*}")
    assert [c["type"] for c in t["children"]] == ["equation"]


def test_inline_math_with_environment_is_not_split():
    t = tree_of("Take $A=\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}$ and go. Cost is \\$5 and $x$.")
    assert [c["type"] for c in t["children"]] == ["paragraph"]


def test_figure_and_table_are_leaves_including_starred():
    t = tree_of("\\begin{figure}[htpb]\n\\centering\n\\caption{c}\n\\end{figure}\n"
                "\\begin{table*}\n\\begin{tabular}{cc}a&b\\end{tabular}\n\\end{table*}")
    assert [c["type"] for c in t["children"]] == ["figure", "figure"]
    assert "\\begin{tabular}" in t["children"][1]["text"]


def test_equation_inside_theorem_is_found():
    t = tree_of("\\begin{theorem}\nLet \\begin{equation}a=b\\end{equation} hold.\n\\end{theorem}")
    assert [c["type"] for c in t["children"]] == ["paragraph", "equation", "paragraph"]


# ---------------------------------------------------------------- itemize
def test_itemize_has_item_children():
    t = tree_of("\\begin{itemize}\n\\item First.\n\\item Second.\n\\end{itemize}")
    it = t["children"][0]
    assert it["type"] == "itemize" and [c["type"] for c in it["children"]] == ["item", "item"]
    assert it["children"][0]["children"][0]["text"] == "First."


def test_nested_itemize_is_kept_inside_parent_item():
    body = ("\\begin{enumerate}\n\\item A\n\\begin{itemize}\n\\item A1\n\\item A2\n\\end{itemize}\n\\item B\n"
            "\\end{enumerate}")
    it = tree_of(body)["children"][0]
    assert len(it["children"]) == 2
    inner = it["children"][0]["children"][1]
    assert inner["type"] == "itemize" and len(inner["children"]) == 2


def test_itemsep_is_not_an_item():
    it = tree_of("\\begin{itemize}\\itemsep0pt\n\\item only\n\\end{itemize}")["children"][0]
    assert len(it["children"]) == 1


# ---------------------------------------------------------------- nhiều file, comment
def test_includes_are_expanded_in_place_and_in_order(tmp_path):
    write(tmp_path, {
        "main.tex": doc("\\section{A}\n\\input{a}\n\\section{B}\n\\input{b}"),
        "a.tex": "text from a", "b.tex": "text from b",
        "unused.tex": "NEVER SEEN",
    })
    tree = H.parse_version(tmp_path, "2512.00001")
    assert tree["status"] == "ok" and tree["root_file"] == "main.tex" and tree["n_unused"] == 1
    secs = tree["root"]["children"]
    assert [s["title"] for s in secs] == ["A", "B"]
    assert secs[0]["children"][0]["text"] == "text from a" and secs[1]["children"][0]["text"] == "text from b"
    assert "NEVER SEEN" not in json.dumps(tree)


def test_commented_content_never_appears(tmp_path):
    write(tmp_path, {"main.tex": doc("% \\input{old}\nVisible. % hidden tail\n"
                                     "\\begin{comment}\n\\section{Hidden}\n\\end{comment}"),
                     "old.tex": "OLD"})
    s = json.dumps(H.parse_version(tmp_path)["root"])
    assert "Visible." in s and "OLD" not in s and "hidden tail" not in s and "Hidden" not in s


def test_section_defined_in_included_file_is_nested_correctly(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{s1}\n\\input{s2}"),
                     "s1.tex": "\\section{One}\nx", "s2.tex": "\\section{Two}\ny\\subsection{Two-a}\nz"})
    secs = H.parse_version(tmp_path)["root"]["children"]
    assert [s["title"] for s in secs] == ["One", "Two"] and secs[1]["children"][1]["title"] == "Two-a"


def test_unresolved_and_cyclic_includes_do_not_crash(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{missing}\n\\input{a}\nEnd."),
                     "a.tex": "A text \\input{a}"})
    tree = H.parse_version(tmp_path)
    assert tree["status"] == "ok" and tree["n_unresolved"] == 1
    assert "End." in json.dumps(tree["root"])


# ---------------------------------------------------------------- version rỗng / lỗi
def test_empty_version_is_recorded_not_skipped(tmp_path):
    (tmp_path / "v1").mkdir()
    tree = H.parse_version(tmp_path / "v1", "2512.00002")
    assert tree["status"] == "empty" and tree["root"] == {"type": "document", "children": []}
    assert tree["arxiv_id"] == "2512.00002" and tree["version"] == "v1"


def test_tex_without_root_is_no_root(tmp_path):
    write(tmp_path, {"intro.tex": "just text"})
    tree = H.parse_version(tmp_path)
    assert tree["status"] == "no_root" and tree["root"]["children"] == []


def test_unbalanced_markers_do_not_crash():
    t = tree_of("Costs $5 and more.\n\n\\begin{foo} never closed\n\n$$ never closed")
    assert len(t["children"]) >= 1


# ---------------------------------------------------------------- ghi file
def test_process_paper_writes_tree_raw_for_every_version(tmp_path, monkeypatch):
    from utils import config as C
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    write(tmp_path, {
        "2512-00003/v1/main.tex": doc("\\section{A}\nx"),
        "2512-00003/v2/main.tex": doc("\\section{A}\nx\n\\section{B}\ny"),
        "2512-00003/v3/fig.pdf": "%PDF",
    })
    res = H.process_paper("2512.00003")
    assert res == [("v1", "ok"), ("v2", "ok"), ("v3", "empty")]
    data = json.loads((tmp_path / "2512-00003" / "v2" / "tree_raw.json").read_text(encoding="utf-8"))
    assert data["arxiv_id"] == "2512.00003" and data["version"] == "v2"
    assert [s["title"] for s in data["root"]["children"]] == ["A", "B"]
    assert (tmp_path / "2512-00003" / "v3" / "tree_raw.json").exists()      # bản rỗng vẫn có file


def test_skip_existing(tmp_path, monkeypatch):
    from utils import config as C
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    write(tmp_path, {"2512-00004/v1/main.tex": doc("x")})
    assert H.process_paper("2512.00004") == [("v1", "ok")]
    assert H.process_paper("2512.00004", skip_existing=True) == []


def test_list_optional_argument_is_not_text():
    it = tree_of("\\begin{enumerate}[label=(\\alph*)]\n\\item a\n\\item b\n\\end{enumerate}")
    assert [c["type"] for c in it["children"]] == ["itemize"] and len(it["children"][0]["children"]) == 2
