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
