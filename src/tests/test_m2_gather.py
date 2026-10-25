"""Test offline cho m2_gather.find_root_file: pytest src/tests/test_m2_gather.py"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
import m2_gather as G  # noqa: E402

ARTICLE = "\\documentclass[11pt]{article}\n\\begin{document}\nHello.\n\\end{document}\n"


def write(root: Path, files: dict) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return root


# ---------------------------------------------------------------- strip_comments
@pytest.mark.parametrize("src, expected", [
    ("% hello", ""),
    ("text % note", "text "),
    ("50\\% done", "50\\% done"),            # \% là ký tự % thật
    ("a \\\\% note", "a \\\\"),              # \\ rồi % -> % là comment
    ("a \\% b % c", "a \\% b "),             # % thật, sau đó mới tới comment
    ("line1\n% line2\nline3", "line1\n\nline3"),
    ("no comment here", "no comment here"),
])
def test_strip_comments(src, expected):
    assert G.strip_comments(src) == expected


# ---------------------------------------------------------------- các trường hợp cơ bản
def test_single_main_tex(tmp_path):
    write(tmp_path, {"main.tex": ARTICLE})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason, r.n_tex_files) == ("main.tex", "ok", 1)


def test_root_name_is_not_main(tmp_path):                 # như bài 2512.23231 có ms.tex
    write(tmp_path, {"ms.tex": ARTICLE, "ref.bib": "@article{a}"})
    assert G.find_root_file(tmp_path).root == "ms.tex"


def test_root_inside_subdirectory(tmp_path):
    write(tmp_path, {"paper/main.tex": ARTICLE})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == ("paper/main.tex", "ok")


def test_uppercase_extension(tmp_path):
    write(tmp_path, {"PAPER.TEX": ARTICLE})
    assert G.find_root_file(tmp_path).root == "PAPER.TEX"


def test_old_documentstyle(tmp_path):
    write(tmp_path, {"old.tex": "\\documentstyle[12pt]{article}\n\\begin{document}x\\end{document}"})
    assert G.find_root_file(tmp_path).root == "old.tex"


def test_no_tex_files_and_missing_dir(tmp_path):
    write(tmp_path, {"fig.pdf": b"%PDF"})
    assert G.find_root_file(tmp_path).reason == "no_tex_files"
    assert G.find_root_file(tmp_path / "khong_ton_tai").reason == "no_tex_files"


# ---------------------------------------------------------------- comment
def test_commented_documentclass_is_ignored(tmp_path):
    write(tmp_path, {
        "old_version.tex": "% \\documentclass{article}\n% \\begin{document}\nchỉ là chú thích\n",
        "paper.tex": ARTICLE,
    })
    r = G.find_root_file(tmp_path)
    assert r.root == "paper.tex" and r.candidates == ["paper.tex"]


def test_only_commented_documentclass(tmp_path):
    write(tmp_path, {"a.tex": "% \\documentclass{article}\nplain text\n"})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == (None, "no_documentclass")


# ---------------------------------------------------------------- nhiều ứng viên
def test_standalone_figure_is_not_root(tmp_path):
    write(tmp_path, {
        "main.tex": ARTICLE,
        "figures/fig1.tex": "\\documentclass{standalone}\n\\begin{document}\\tikz\\end{document}",
    })
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == ("main.tex", "ok")
    assert r.candidates == ["main.tex", "figures/fig1.tex"]


def test_file_with_more_includes_wins(tmp_path):
    main = ("\\documentclass{article}\n\\begin{document}\n"
            "\\input{sections/intro}\n\\include{sections/method}\n\\end{document}")
    write(tmp_path, {
        "main.tex": main,
        "supplement.tex": ARTICLE,
        "sections/intro.tex": "Intro.", "sections/method.tex": "Method.",
    })
    r = G.find_root_file(tmp_path)
    assert r.root == "main.tex" and r.reason == "ambiguous"   # có 2 ứng viên mạnh
    assert r.candidates == ["main.tex", "supplement.tex"]


def test_file_included_by_another_is_not_root(tmp_path):
    write(tmp_path, {
        "a.tex": "\\documentclass{article}\n\\begin{document}\n\\input{b}\n\\end{document}",
        "b.tex": ARTICLE,
    })
    r = G.find_root_file(tmp_path)
    assert r.root == "a.tex" and r.candidates[1] == "b.tex"


def test_commented_input_does_not_count_as_include(tmp_path):
    write(tmp_path, {
        "a.tex": "\\documentclass{article}\n\\begin{document}\n% \\input{b}\n\\end{document}",
        "b.tex": ARTICLE,
    })
    r = G.find_root_file(tmp_path)
    # \input{b} nằm trong comment -> b KHÔNG bị coi là file được include,
    # nên cả hai đều là ứng viên mạnh ngang nhau (reason = ambiguous)
    assert r.reason == "ambiguous"
    assert set(r.candidates) == {"a.tex", "b.tex"}


def test_preferred_name_breaks_tie(tmp_path):
    write(tmp_path, {"cover_letter.tex": ARTICLE, "main.tex": ARTICLE})
    assert G.find_root_file(tmp_path).root == "main.tex"


def test_shallower_file_wins(tmp_path):
    write(tmp_path, {"top.tex": ARTICLE, "supp/extra.tex": ARTICLE})
    assert G.find_root_file(tmp_path).root == "top.tex"


def test_input_without_braces_is_understood(tmp_path):
    write(tmp_path, {
        "paper.tex": "\\documentclass{article}\n\\begin{document}\n\\input intro\n\\end{document}",
        "intro.tex": "Intro.",
    })
    text = G.strip_comments((tmp_path / "paper.tex").read_text())
    assert [g1 or g2 for g1, g2 in G.INCLUDE_RE.findall(text)] == ["intro"]


# ---------------------------------------------------------------- ca khó / dự phòng
def test_documentclass_without_begin_document(tmp_path):
    write(tmp_path, {"preamble.tex": "\\documentclass{article}\n\\usepackage{amsmath}\n"})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == ("preamble.tex", "root_without_begin_document")


def test_fallback_when_only_begin_document(tmp_path):
    write(tmp_path, {"body.tex": "\\begin{document}\nhi\n\\end{document}", "intro.tex": "x"})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == ("body.tex", "fallback_begin_document")


def test_only_fragments(tmp_path):
    write(tmp_path, {"intro.tex": "Just text.", "sec/a.tex": "More text."})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason, r.n_tex_files) == (None, "no_documentclass", 2)


def test_only_standalone_is_weak(tmp_path):
    write(tmp_path, {"fig.tex": "\\documentclass{standalone}\n\\begin{document}x\\end{document}"})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == ("fig.tex", "weak_candidate")


def test_non_utf8_file_does_not_crash(tmp_path):
    content = "\\documentclass{article}\n% Résumé\n\\begin{document}é\\end{document}".encode("latin-1")
    write(tmp_path, {"main.tex": content})
    assert G.find_root_file(tmp_path).root == "main.tex"


def test_documentclass_with_multiline_options(tmp_path):
    write(tmp_path, {"main.tex": "\\documentclass[11pt,\n  a4paper]{article}\n\\begin{document}x\\end{document}"})
    r = G.find_root_file(tmp_path)
    assert (r.root, r.reason) == ("main.tex", "ok")


def test_result_is_deterministic(tmp_path):
    write(tmp_path, {"b.tex": ARTICLE, "a.tex": ARTICLE, "c.tex": ARTICLE})
    first = G.find_root_file(tmp_path)
    for _ in range(3):
        assert G.find_root_file(tmp_path) == first
    assert first.root == "a.tex"


# ---------------------------------------------------------------- scan (CLI)
def test_scan_counts_reasons(tmp_path, monkeypatch, capsys):
    from utils import config as C
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    write(tmp_path, {
        "2512-23231/v1/main.tex": ARTICLE,
        "2512-23231/v2/ms.tex": ARTICLE,
        "2512-23232/v1/fig.pdf": b"%PDF",           # không có .tex
        "2512-23233/versions.json": "{}",            # chưa crawl source
    })
    out = G.scan(["2512.23231", "2512.23232", "2512.23233"])
    assert out["n_versions"] == 3 and out["papers_without_source"] == 1
    assert out["counts"] == {"ok": 2, "no_tex_files": 1}
    assert "TỔNG KẾT" in capsys.readouterr().out


# ============================================================================
# Lệnh include: không nhận nhầm \includegraphics, \includeonly, \inputencoding
# ============================================================================
@pytest.mark.parametrize("src, expected", [
    ("\\input{intro}", ["intro"]),
    ("\\include{sec/a}", ["sec/a"]),
    ("\\input intro", ["intro"]),
    ("\\input{./sections/a.tex}", ["sections/a"]),
    ("\\subfile{part1}", ["part1"]),
    ("\\includegraphics[width=0.5\\textwidth]{fig1}", []),
    ("\\includeonly{a,b}", []),
    ("\\inputencoding{utf8}", []),
    ("% \\input{hidden}\n\\input{shown}", ["shown"]),
    ("\\begin{comment}\n\\input{hidden}\n\\end{comment}\n\\input{shown}", ["shown"]),
])
def test_find_include_targets(src, expected):
    assert G.find_include_targets(src) == expected


def test_figures_do_not_inflate_include_count(tmp_path):
    # supp.tex có nhiều \includegraphics nhưng 0 lệnh include thật; main.tex có 1 \input thật
    figs = "".join(f"\\includegraphics{{f{i}}}\n" for i in range(5))
    write(tmp_path, {
        "main.tex": "\\documentclass{article}\n\\begin{document}\n\\input{a}\n\\end{document}",
        "supp.tex": "\\documentclass{article}\n\\begin{document}\n" + figs + "\\end{document}",
        "a.tex": "A",
    })
    assert G.find_root_file(tmp_path).root == "main.tex"


# ============================================================================
# collect_compilation_files
# ============================================================================
def doc(body):
    return "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"


def test_collect_follows_nested_includes_in_order(tmp_path):
    # mô phỏng bài 2512.23232: main -> sections -> figure, và phụ lục -> figure khác
    write(tmp_path, {
        "main.tex": doc("\\input{1_Intro}\n\\input{4_Exp}\n\\input{X_Appendix}"),
        "1_Intro.tex": "Intro \\input{Figure_1}",
        "4_Exp.tex": "\\input{Figure_3}\n\\input{main_table}",
        "X_Appendix.tex": "\\input{Figure_5}\n\\input{appendix_ode}",
        "Figure_1.tex": "f1", "Figure_3.tex": "f3", "Figure_5.tex": "f5",
        "main_table.tex": "table", "appendix_ode.tex": "ode",
    })
    c = G.collect_compilation_files(tmp_path)
    assert c.root == "main.tex"
    assert c.files == ["main.tex", "1_Intro.tex", "Figure_1.tex", "4_Exp.tex", "Figure_3.tex",
                       "main_table.tex", "X_Appendix.tex", "Figure_5.tex", "appendix_ode.tex"]
    assert (c.unresolved, c.cycles, c.unused) == ([], [], [])
    assert ("1_Intro.tex", "Figure_1.tex") in c.edges


def test_collect_ignores_commented_and_comment_env_includes(tmp_path):
    write(tmp_path, {
        "main.tex": doc("% \\input{old}\n\\input{new}\n\\begin{comment}\n\\input{draft}\n\\end{comment}"),
        "new.tex": "n", "old.tex": "o", "draft.tex": "d",
    })
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex", "new.tex"]
    assert sorted(c.unused) == ["draft.tex", "old.tex"]     # file thừa, không bị parse


def test_collect_reports_unresolved_includes(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{exists}\n\\input{plots/fig.tikz}\n\\input{missing}"),
                     "exists.tex": "x"})
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex", "exists.tex"]
    assert c.unresolved == [("main.tex", "plots/fig.tikz"), ("main.tex", "missing")]


def test_collect_detects_cycles_without_infinite_loop(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{a}"),
                     "a.tex": "\\input{b}", "b.tex": "\\input{a}"})
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex", "a.tex", "b.tex"]
    assert c.cycles == [("b.tex", "a.tex")]


def test_collect_self_include_is_a_cycle(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{main}")})
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex"] and c.cycles == [("main.tex", "main.tex")]


def test_collect_file_included_twice_listed_once(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{fig}\n\\input{fig}"), "fig.tex": "f"})
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex", "fig.tex"]
    assert c.edges == [("main.tex", "fig.tex"), ("main.tex", "fig.tex")]   # nhưng cạnh ghi cả hai lần


def test_collect_paths_relative_to_root_directory(tmp_path):
    # LaTeX tính đường dẫn từ thư mục chạy (thư mục file gốc), kể cả khi \input nằm trong thư mục con
    write(tmp_path, {
        "main.tex": doc("\\input{sections/intro}"),
        "sections/intro.tex": "\\input{sections/fig}",      # tính từ thư mục gốc
        "sections/fig.tex": "f",
    })
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex", "sections/intro.tex", "sections/fig.tex"]


def test_collect_falls_back_to_including_file_directory(tmp_path):
    write(tmp_path, {
        "main.tex": doc("\\input{sections/intro}"),
        "sections/intro.tex": "\\input{fig}",                # chỉ đúng nếu tính từ thư mục của intro.tex
        "sections/fig.tex": "f",
    })
    assert G.collect_compilation_files(tmp_path).files[-1] == "sections/fig.tex"


def test_collect_with_root_in_subdirectory(tmp_path):
    write(tmp_path, {"paper/main.tex": doc("\\input{intro}"), "paper/intro.tex": "i"})
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["paper/main.tex", "paper/intro.tex"]


def test_collect_with_explicit_root_and_explicit_extension(tmp_path):
    write(tmp_path, {"a.tex": doc("\\input{b.tex}"), "b.tex": "b", "c.tex": doc("")})
    c = G.collect_compilation_files(tmp_path, root="a.tex")
    assert c.files == ["a.tex", "b.tex"] and c.unused == ["c.tex"]


def test_collect_macro_targets_are_unresolved(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{\\figdir/a}"), "a.tex": "x"})
    c = G.collect_compilation_files(tmp_path)
    assert c.files == ["main.tex"] and c.unresolved == [("main.tex", "\\figdir/a")]


def test_collect_without_root_marks_everything_unused(tmp_path):
    write(tmp_path, {"intro.tex": "just text", "b.tex": "more"})
    c = G.collect_compilation_files(tmp_path)
    assert c.root is None and c.files == [] and c.unused == ["b.tex", "intro.tex"]


def test_collect_does_not_escape_version_directory(tmp_path):
    write(tmp_path, {"main.tex": doc("\\input{../secret}"), "secret.tex": "s"})
    c = G.collect_compilation_files(tmp_path / "")
    assert c.files == ["main.tex"] and c.unresolved == [("main.tex", "../secret")]


def test_scan_with_gather_reports_statistics(tmp_path, monkeypatch, capsys):
    from utils import config as C
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    write(tmp_path, {
        "2512-23232/v1/main.tex": doc("\\input{a}\n\\input{lost}"),
        "2512-23232/v1/a.tex": "a",
        "2512-23232/v1/unused.tex": "u",
    })
    out = G.scan(["2512.23232"], gather=True)
    assert out["gather"]["versions"] == 1 and out["gather"]["files"] == 2
    assert out["gather"]["with_unresolved"] == 1 and out["gather"]["with_unused"] == 1
    assert "TỔNG KẾT GOM FILE" in capsys.readouterr().out
