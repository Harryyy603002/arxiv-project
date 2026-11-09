"""Test offline cho phần P3 của M2: pytest src/tests/test_m2_text.py"""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "parser"))
from utils import config as C  # noqa: E402
from m2_cleanup import (clean_figure, clean_text, normalize_block_math,  # noqa: E402
                        normalize_inline_math, split_segments)
from m2_sentences import split_sentences  # noqa: E402
import m2_content_dedup as D  # noqa: E402


# ------------------------------------------------------------------ cleanup
def test_clean_text_removes_formatting_but_keeps_cite():
    s = r"\centering We follow \textbf{Vaswani}~\cite{vaswani2017}. % comment" + "\n" + r"\small Next \emph{step}.\\"
    assert clean_text(s) == r"We follow Vaswani \cite{vaswani2017}. Next step."


def test_clean_figure_keeps_structure_drops_placement():
    s = (r"\begin{figure}[htpb] \centering \includegraphics[width=\linewidth]{a.png}"
         r" \caption{A \textbf{plot}.} \label{fig:a} \end{figure}")
    out = clean_figure(s)
    assert out == (r"\begin{figure} \includegraphics[width=\linewidth]{a.png}"
                   r" \caption{A plot.} \end{figure}")


def test_table_rules_removed():
    out = clean_figure(r"\begin{tabular}{lc} \toprule A & B \\ \midrule 1 & 2 \\ \bottomrule \end{tabular}")
    assert "midrule" not in out and "toprule" not in out and r"A & B \\" in out


def test_inline_math_unified_and_untouched_by_cleaning():
    assert normalize_inline_math(r"Let \(x  +  y\) and $ z $ and \$5") == r"Let $x + y$ and $z$ and \$5"
    assert clean_text(r"Let $\textbf{x}~y$ hold.") == r"Let $\textbf{x}~y$ hold."


@pytest.mark.parametrize("raw,expected", [
    ("E = mc^2", r"\begin{equation} E = mc^2 \end{equation}"),
    (r"$$a+b$$", r"\begin{equation} a+b \end{equation}"),
    (r"\[ a = b \]", r"\begin{equation} a = b \end{equation}"),
    (r"\begin{equation*} a \label{eq:1} \end{equation*}", r"\begin{equation} a \end{equation}"),
    (r"\begin{align} a &= b \\ c &= d \nonumber \end{align}",
     r"\begin{equation} \begin{aligned} a &= b \\ c &= d \end{aligned} \end{equation}"),
    (r"\begin{align*} a &= b \end{align*}", r"\begin{equation} \begin{aligned} a &= b \end{aligned} \end{equation}"),
])
def test_block_math(raw, expected):
    assert normalize_block_math(raw) == expected


def test_split_segments_order():
    segs = split_segments(r"Before. $$x=1$$ Middle. \begin{equation}y\end{equation} End.")
    assert [k for k, _ in segs] == ["text", "formula", "text", "formula", "text"]


# ------------------------------------------------------------------ sentences
@pytest.mark.parametrize("text,n", [
    ("We study X. Then we test it.", 2),
    ("See Fig. 1 and Eq. (2) for details. Next part.", 2),
    ("Methods, e.g. SVM, work. This is another, i.e. a second one.", 2),
    ("Zhang et al. proposed a model. It works.", 2),
    ("The score is 3.14 on https://example.com/a.b data. Done.", 2),
    (r"We have $x = 1.5. y$ here. Then more \cite{a.b}. Last.", 3),
    ("Is it true? Yes! It is.", 3),
    ("No split here without end", 1),
])
def test_split_sentences_count(text, n):
    assert len(split_sentences(text)) == n


def test_split_sentences_content():
    out = split_sentences("See Fig. 1 for results. Next, we use $a.b$ and 2.5 units.")
    assert out == ["See Fig. 1 for results.", "Next, we use $a.b$ and 2.5 units."]


# ------------------------------------------------------------------ dedup xuyên version
def _tree(sent_a, sent_b, extra=""):
    return {"arxiv_id": "2310.12345", "version": "vX", "root": {"type": "document", "children": [
        {"type": "section", "level": 1, "title": r"\textbf{Introduction}", "children": [
            {"type": "paragraph", "text": sent_a + " " + sent_b + extra},
            {"type": "equation", "text": "E = mc^2"},
            {"type": "itemize", "children": [{"type": "item", "text": "First point."}]},
        ]},
        {"type": "section", "level": 1, "title": "References", "children": [
            {"type": "paragraph", "text": "Should be skipped."}]},
    ]}}


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    aid = "2310.12345"
    C.ensure_dir(C.paper_dir(aid))
    C.versions_path(aid).write_text(json.dumps({"arxiv_id": aid, "latest_version": 3, "versions": [
        {"version": "v1"}, {"version": "v2"}, {"version": "v3"}]}))
    for v, tree in (("v1", _tree(r"We follow \cite{vaswani2017}.", "Old sentence.")),
                    ("v2", _tree(r"We follow \cite{vit_transformer}.", "New sentence."))):
        C.ensure_dir(C.version_dir(aid, v))
        C.tree_raw_path(aid, v).write_text(json.dumps(tree))
    C.ensure_dir(C.version_dir(aid, "v3"))  # PDF-only: không có tree_raw.json
    C.cite_map_path(aid).write_text(json.dumps({"vaswani2017": "vit_transformer"}))
    return aid


def test_process_paper_dedup_across_versions(paper):
    s = D.process_paper(paper)
    assert s["versions"]["v3"]["status"] == "empty" and s["n_versions_ok"] == 2

    elements = json.loads(C.elements_path(paper).read_text())
    edges = json.loads(C.hierarchy_path(paper).read_text())
    by_content = {e["content"]: e for e in elements}

    # câu có \cite đổi tên trước khi dedup -> v1 và v2 dùng chung một element
    shared = by_content[r"We follow \cite{vit_transformer}."]
    assert sum(e["content"] == shared["content"] for e in elements) == 1
    assert shared["element_id"].startswith("2310-12345#s")
    sid = shared["element_id"]
    assert {e["version"] for e in edges if e["element_id"] == sid} == {1, 2}

    # công thức + heading cũng gộp; câu khác nhau thì tách; References bị bỏ
    assert sum(e["kind"] == "formula" for e in elements) == 1
    assert "Old sentence." in by_content and "New sentence." in by_content
    assert "Should be skipped." not in by_content
    assert by_content["Introduction"]["kind"] == "heading"

    # document riêng từng version, không có edge cho root; version rỗng không có edge
    roots = [e for e in elements if e["kind"] == "document"]
    assert [r["content"] for r in roots] == ["Version 1 root", "Version 2 root"]
    assert not any(e["version"] == 3 for e in edges)
    assert all(e["element_id"] not in {r["element_id"] for r in roots} for e in edges)

    # itemize -> item -> sentence
    item = next(e for e in elements if e["kind"] == "item" and e["element_id"].endswith("li1_1"))
    first = by_content["First point."]["element_id"]
    assert any(e["version"] == 1 and e["element_id"] == first and e["parent_id"] == item["element_id"] for e in edges)

    assert s["n_elements_after_dedup"] < s["n_nodes_before_dedup"]


def test_same_sentence_twice_in_one_version_stays_unambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    aid = "2310.99999"
    C.ensure_dir(C.version_dir(aid, "v1"))
    C.versions_path(aid).write_text(json.dumps({"arxiv_id": aid, "versions": [{"version": "v1"}]}))
    C.tree_raw_path(aid, "v1").write_text(json.dumps({"root": {"type": "document", "children": [
        {"type": "section", "title": "A", "children": [{"type": "paragraph", "text": "Same. Same."}]}]}}))
    D.process_paper(aid)
    ids = [e["element_id"] for e in json.loads(C.hierarchy_path(aid).read_text())]
    assert len(ids) == len(set(ids))