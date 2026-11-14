"""Kiểm thử M2 trên một corpus giả có đúng định dạng đầu ra của M1 (run_m1.py).

Corpus giả gồm 3 bài:
  2602.04870      2 version, nhiều file (\\input), có phần bị comment, file thừa, phụ lục,
                  \\section*, itemize, công thức, hình, References; câu "Shared" giữ nguyên ở v2
  hep-th/9901001  ID kiểu cũ (thư mục hep-th-9901001), v2 chưa tải được (M1 lỗi) -> "missing"
  2602.00003      version chỉ có PDF -> bản ghi rỗng của M1 -> "empty"

Chạy từ thư mục gốc repo:  pytest src/tests/test_m2_pipeline.py -v
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "parser"))

from crawler import m1_download  # noqa: E402
from utils import config as C  # noqa: E402
import m2_content_dedup as D  # noqa: E402
import m2_gather as G  # noqa: E402
import m2_hierarchy as H  # noqa: E402

MAIN_V1 = r"""\documentclass{article}
\usepackage{amsmath}
\begin{document}
\title{A Test Paper}
\author{Ann Lee\thanks{Corresp. author.} \and Bob Ray \\ Uni X}
\date{\today}
\maketitle
\begin{abstract}
We study matching. It works well.
\end{abstract}
\section{Introduction}
Shared sentence about citation matching \cite{vaswani2017}. Prior work by Smith et al. shows 3.5 points gain (see Fig. 1).
% \input{sections/draft}
\input{sections/method}
\begin{itemize}
\item First point is simple.
\item Second point uses $x_i$ and \(y\).
\end{itemize}
$$E = mc^2$$
\begin{figure}[htbp]
\centering
\includegraphics{fig1.png}
\caption{A figure.}
\end{figure}
\section*{Acknowledgements}
We thank everyone.
\begin{thebibliography}{9}
\bibitem{vaswani2017} Vaswani. Attention is all you need.
\end{thebibliography}
\appendix
\section{Proofs}
Proof text here.
\end{document}
"""
METHOD_V1 = r"""\subsection{Model}
The model is trained with SGD. Results are in Table 1.
"""
DRAFT = r"""\section{Draft that is commented out}
This must never appear.
"""
OLD_UNUSED = r"""\section{Old unused file}
Never compiled.
"""
MAIN_V2 = MAIN_V1.replace("Results are in Table 1.", "Results are in Table 2.")
METHOD_V2 = METHOD_V1.replace("Results are in Table 1.", "Results are in Table 2.")
OLD_MAIN = r"""\documentclass{article}
\begin{document}
\section{Intro}
Old style paper. It has one section.
\bibliography{refs}
\end{document}
"""


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            data = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _write_version(arxiv_id: str, n: int, archive: bytes) -> None:
    """Ghi một version đúng như run_m1.py: dùng chính hàm lưu của M1 + metadata.json."""
    vdir = C.version_dir(arxiv_id, n)
    status, reason, files, raw, before, after = m1_download.save_source_with_stats(archive, vdir)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "metadata.json").write_text(json.dumps(
        {"arxiv_id": f"{arxiv_id}v{n}", "version": n, "status": status, "reason": reason, "files": files,
         "archive_bytes": raw, "bytes_before": before, "bytes_after": after}), encoding="utf-8")


def _write_paper(arxiv_id: str, n_versions: int) -> None:
    pdir = C.paper_dir(arxiv_id)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "paper.json").write_text(json.dumps(
        {"arxiv_id": arxiv_id, "title": "T", "authors": ["A"], "submission_date": "2026-02-01T00:00:00+00:00",
         "revised_dates": [], "venue": None, "n_versions": n_versions}), encoding="utf-8")


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(H, "DATA_DIR", tmp_path)
    figure_png = b"\x89PNG" * 2000  # hình: M1 phải bỏ, M2 không thấy
    _write_paper("2602.04870", 2)
    _write_version("2602.04870", 1, _tar({"main.tex": MAIN_V1, "sections/method.tex": METHOD_V1,
                                          "sections/draft.tex": DRAFT, "old.tex": OLD_UNUSED,
                                          "refs.bib": "@article{k,title={T}}", "fig1.png": figure_png}))
    _write_version("2602.04870", 2, _tar({"main.tex": MAIN_V2, "sections/method.tex": METHOD_V2,
                                          "refs.bib": "@article{k,title={T}}"}))
    _write_paper("hep-th/9901001", 2)  # v2 M1 không tải được -> không có thư mục v2
    _write_version("hep-th/9901001", 1, _tar({"paper.tex": OLD_MAIN}))
    _write_paper("2602.00003", 1)
    _write_version("2602.00003", 1, b"%PDF-1.5 fake pdf")
    return tmp_path


def _load(arxiv_id: str, name: str):
    return json.loads((C.paper_dir(arxiv_id) / name).read_text(encoding="utf-8"))


def _run_all():
    ids = H.list_all_ids()
    for aid in ids:
        H.process_paper(aid)
    for aid in ids:
        D.process_paper(aid)
    return ids


def test_m1_layout_is_what_m2_expects(corpus):
    ids = H.list_all_ids()
    assert sorted(ids) == ["2602.00003", "2602.04870", "hep-th/9901001"]  # đọc từ paper.json
    assert D.discover_ids() == sorted(ids)
    assert C.paper_dir("hep-th/9901001").name == "hep-th-9901001"
    assert not list(C.paper_dir("2602.04870").glob("v1/*.png"))  # hình đã bị M1 loại


def test_gather_follows_input_and_ignores_comments_and_unused(corpus):
    vdir = C.version_dir("2602.04870", 1)
    root = G.find_root_file(vdir)
    assert root.root == "main.tex" and root.reason == "ok"
    comp = G.collect_compilation_files(vdir, root=root.root)
    assert comp.files == ["main.tex", "sections/method.tex"]
    assert "sections/draft.tex" in comp.unused and "old.tex" in comp.unused


def test_tree_structure(corpus):
    _run_all()
    tree = _load("2602.04870", "v1/tree_raw.json")
    assert tree["status"] == "ok" and tree["arxiv_id"] == "2602.04870"
    titles = []

    def walk(n):
        if n["type"] == "section":
            titles.append(n["title"])
        for c in n.get("children", []):
            walk(c)

    walk(tree["root"])
    assert "Introduction" in titles and "Model" in titles          # subsection lồng trong method.tex
    assert "Acknowledgements" in titles and "Proofs" in titles     # \section* và phụ lục được giữ
    assert not any("Draft" in t or "Old unused" in t for t in titles)
    assert not any("bibliography" in t.lower() or t.lower() == "references" for t in titles)


def test_elements_and_hierarchy(corpus):
    _run_all()
    elements = _load("2602.04870", "elements.json")
    edges = _load("2602.04870", "hierarchy.json")
    by_id = {e["element_id"]: e for e in elements}
    texts = [e["content"] for e in elements]

    assert all(e["arxiv_id"] == "2602.04870" for e in elements)
    assert all(e["element_id"].startswith("2602-04870#") for e in elements)
    assert not any("must never appear" in t or "Never compiled" in t for t in texts)
    assert not any("Attention is all you need" in t for t in texts)          # References bị bỏ
    assert any(e["kind"] == "formula" and e["content"] == r"\begin{equation} E = mc^2 \end{equation}"
               for e in elements)
    assert any(e["kind"] == "figure" and "includegraphics" in e["content"] for e in elements)
    assert not any("centering" in e["content"] for e in elements)            # lệnh định dạng đã bỏ
    assert not any("\\title" in t or "\\author" in t or "Ann Lee" in t or "\\date" in t for t in texts)  # front matter
    assert any("$y$" in t for t in texts)                                    # \( y \) -> $y$

    # tách câu: không cắt ở "et al.", "3.5", "Fig. 1"
    sentences = [e["content"] for e in elements if e["kind"] == "sentence"]
    assert any(s.startswith("Prior work by Smith et al.") and "3.5" in s and "Fig. 1" in s for s in sentences)

    # dedup xuyên version: câu không đổi dùng chung một element_id, câu đổi thì có id riêng
    shared = [e for e in elements if e["content"].startswith("Shared sentence")]
    assert len(shared) == 1
    sid = shared[0]["element_id"]
    assert sorted(ed["version"] for ed in edges if ed["element_id"] == sid) == [1, 2]
    t1 = [e for e in elements if e["content"].endswith("Table 1.")]
    t2 = [e for e in elements if e["content"].endswith("Table 2.")]
    assert len(t1) == 1 and len(t2) == 1 and t1[0]["element_id"] != t2[0]["element_id"]

    # mọi cạnh trỏ tới element tồn tại; nút gốc mỗi version không có cạnh riêng
    assert all(ed["element_id"] in by_id and ed["parent_id"] in by_id for ed in edges)
    roots = [e for e in elements if e["kind"] == "document"]
    assert sorted(e["element_id"] for e in roots) == ["2602-04870#root1", "2602-04870#root2"]
    assert not any(ed["element_id"] in {r["element_id"] for r in roots} for ed in edges)
    # itemize: mục là nút con của itemize
    items = [e["element_id"] for e in elements if e["kind"] == "item"]
    parents = {ed["parent_id"] for ed in edges if ed["element_id"] in items}
    assert len(parents) == 2 or len(parents) == 1  # 2 version hoặc 1 itemize chung
    assert len(items) >= 2

    # số lượng trước/sau dedup
    status = _load("2602.04870", "parse_status.json")
    assert status["n_versions"] == 2 and status["n_versions_ok"] == 2
    assert status["n_elements_after_dedup"] < status["n_nodes_before_dedup"]


def test_old_style_id_keeps_original_id_and_marks_missing_version(corpus):
    _run_all()
    elements = _load("hep-th/9901001", "elements.json")
    assert elements and all(e["arxiv_id"] == "hep-th/9901001" for e in elements)  # không phải "hep-th-9901001"
    assert all(e["element_id"].startswith("hep-th-9901001#") for e in elements)
    status = _load("hep-th/9901001", "parse_status.json")
    assert status["versions"]["v1"]["status"] == "ok"
    assert status["versions"]["v2"] == {"status": "missing", "n_nodes": 0, "reason": "not_downloaded"}


def test_pdf_only_version_is_recorded_as_empty_not_skipped(corpus):
    _run_all()
    tree = _load("2602.00003", "v1/tree_raw.json")
    assert tree["status"] == "empty" and tree["source_reason"] == "pdf_only"
    status = _load("2602.00003", "parse_status.json")
    assert status["versions"]["v1"]["status"] == "empty"
    assert status["versions"]["v1"]["reason"] == "pdf_only"
    assert status["n_versions"] == 1 and status["n_versions_ok"] == 0
    assert _load("2602.00003", "elements.json") == []


def test_export_parquet_matches_schema(corpus, tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    from utils.schemas import ALL_SCHEMAS, export_m2_parquet

    _run_all()
    out = tmp_path / "export"
    counts = export_m2_parquet(corpus_dir=corpus, out_dir=out)
    assert counts["elements"] > 0 and counts["hierarchy"] > 0
    for name in ("elements", "hierarchy"):
        table = pq.read_table(out / f"{name}.parquet")
        assert table.schema.equals(ALL_SCHEMAS[name])
    hier = pq.read_table(out / "hierarchy.parquet").to_pandas()
    assert set(hier["version"]) <= {1, 2}
    assert hier["parent_id"].notna().all()
