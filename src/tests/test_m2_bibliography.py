"""Kiểm thử phần BibTeX của M2 và run_m2.py trên corpus giả đúng định dạng đầu ra của M1.

Bài 2602.04870 có 3 version, mô phỏng ví dụ trong đề (một tài liệu đổi khóa giữa các version):
  v1  .bib: vaswani2017 (title, author, year)       + file unused.bib KHÔNG được \\bibliography nhắc tới
  v2  .bib: vit_transformer (thêm doi)
  v3  \\bibitem: vit_transformer (mất year), không có .bib
Cùng một câu xuất hiện ở cả ba version nhưng \\cite dùng khóa khác nhau.

Chạy:  pytest src/tests/test_m2_bibliography.py -v
Nếu repo chưa có matching.m3_candidates thì dùng một bản giả đơn giản chỉ để chạy test.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]
for extra in (SRC, SRC / "parser", Path(__file__).resolve().parent):
    sys.path.insert(0, str(extra))

try:  # dùng bản thật nếu có
    import matching.m3_candidates  # noqa: F401
except ImportError:  # bản giả tối thiểu, đúng giao diện run_m2.py cần
    def normalize_text(text):
        return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text or "").lower()).split())

    def normalize_authors(raw):
        return tuple(sorted(normalize_text(raw).split()))

    def generate_candidates(entries, references, diagnostics=None):
        rows = []
        for entry in entries:
            for index, ref in enumerate(references):
                ratio = difflib.SequenceMatcher(None, normalize_text(entry.get("title")),
                                                normalize_text(ref.get("ref_title"))).ratio()
                if ratio < 0.5:
                    continue
                rows.append({
                    "candidate_id": f"{entry['arxiv_id']}|{entry['citation_key']}|{ref['ref_arxiv_id']}",
                    "arxiv_id": entry["arxiv_id"], "citation_key": entry["citation_key"],
                    "ref_s2_id": ref.get("ref_s2_id"), "ref_arxiv_id": ref["ref_arxiv_id"],
                    "reference_index": index, "title_similarity": ratio, "author_similarity": 0.0,
                    "bib_title": entry.get("title"), "ref_title": ref.get("ref_title"),
                    "title_exact": ratio == 1.0})
        return rows

    package = types.ModuleType("matching")
    package.__path__ = []
    module = types.ModuleType("matching.m3_candidates")
    module.normalize_text, module.normalize_authors, module.generate_candidates = (
        normalize_text, normalize_authors, generate_candidates)
    sys.modules["matching"], sys.modules["matching.m3_candidates"] = package, module

import run_m2  # noqa: E402
from test_m2_pipeline import _tar, _write_paper, _write_version  # noqa: E402
from utils import config as C  # noqa: E402
from parser import m2_bibitem as B  # noqa: E402
from parser import m2_bib_dedup as D  # noqa: E402

SENT_V1 = r"We follow the standard attention model \cite{vaswani2017}."
SENT_V2 = SENT_V1.replace("vaswani2017", "vit_transformer")
TEMPLATE = r"""\documentclass{article}
\begin{document}
\section{Intro}
%s
Unique sentence number %d is here.
%s
\end{document}
"""
BIB_V1 = """@inproceedings{vaswani2017,
  title={Attention Is All You Need},
  author={Ashish Vaswani and Noam Shazeer},
  year={2017}
}
"""
BIB_V2 = """@inproceedings{vit_transformer,
  title={Attention is all you need},
  author={Vaswani, Ashish and Shazeer, Noam},
  year={2017},
  doi={10.5555/3295222.3295349}
}
"""
UNUSED_BIB = "@article{unused_key, title={Never referenced}, author={Nobody}, year={1999}}"
BIBITEMS_V3 = r"""\begin{thebibliography}{9}
\bibitem{vit_transformer} Ashish Vaswani and Noam Shazeer.
\newblock Attention is all you need.
\newblock In \emph{NeurIPS}.
\end{thebibliography}
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    arxiv_id = "2602.04870"
    _write_paper(arxiv_id, 3)
    _write_version(arxiv_id, 1, _tar({"main.tex": TEMPLATE % (SENT_V1, 1, r"\bibliography{refs}"),
                                      "refs.bib": BIB_V1, "unused.bib": UNUSED_BIB}))
    _write_version(arxiv_id, 2, _tar({"main.tex": TEMPLATE % (SENT_V2, 2, r"\bibliography{refs}"),
                                      "refs.bib": BIB_V2}))
    _write_version(arxiv_id, 3, _tar({"main.tex": TEMPLATE % (SENT_V2, 3, BIBITEMS_V3)}))
    pdir = C.paper_dir(arxiv_id)
    (pdir / "refs.json").write_text(json.dumps({"arxiv_id": arxiv_id, "references": [
        {"ref_arxiv_id": "1706.03762", "ref_title": "Attention is all you need", "ref_authors": ["A. Vaswani"],
         "ref_submission_date": "2017-06-12", "ref_s2_id": "s2a"},
        {"ref_arxiv_id": None, "ref_title": "Attention is all you need (journal)", "ref_authors": [],
         "ref_submission_date": None, "ref_s2_id": "s2b"}]}), encoding="utf-8")
    # bài không có tài liệu tham khảo, bài chỉ có PDF
    _write_paper("hep-th/9901001", 1)
    _write_version("hep-th/9901001", 1, _tar({"p.tex": TEMPLATE % ("Only text here.", 1, "")}))
    _write_paper("2602.00003", 1)
    _write_version("2602.00003", 1, b"%PDF-1.5 fake")
    return tmp_path


# ----------------------------------------------------------------------------- \bibitem
def test_bibitem_extraction_styles():
    text = r"""\begin{thebibliography}{9}
\bibitem{a} A.~Vaswani, N.~Shazeer, and J.~Uszkoreit. \newblock Attention is all you need.
  \newblock In \emph{NeurIPS}, 2017.
\bibitem[Devlin et~al.(2019)]{b} Jacob Devlin, Ming-Wei Chang. 2019. BERT: Pre-training of deep nets.
  In Proceedings of NAACL. https://doi.org/10.18653/v1/N19-1423
\bibitem{c} K. He, X. Zhang, and J. Sun, ``Deep residual learning,'' in CVPR, 2016. arXiv:1512.03385
\end{thebibliography}"""
    entries = {e["citation_key"]: e for e in B.parse_bibitems(text, "X", 1)}
    assert set(entries) == {"a", "b", "c"}
    assert entries["a"]["title"] == "Attention is all you need" and entries["a"]["year"] == "2017"
    assert B.split_authors(entries["a"]["author"]) == ["A. Vaswani", "N. Shazeer", "J. Uszkoreit"]
    assert entries["b"]["doi"] == "10.18653/v1/N19-1423" and entries["b"]["title"].startswith("BERT")
    assert entries["c"]["title"] == "Deep residual learning"
    assert entries["c"]["url"] == "https://arxiv.org/abs/1512.03385" and entries["c"]["year"] == "2016"
    assert all(e["source"] == "bibitem" and e["arxiv_id"] == "X" and e["version"] == 1 for e in entries.values())


# ----------------------------------------------------------------------------- khử trùng (ví dụ của đề)
def test_dedup_matches_spec_example():
    entries = [
        {"citation_key": "vaswani2017", "title": "Attention Is All You Need", "author": "A. Vaswani and N. Shazeer",
         "year": "2017", "version": 1, "arxiv_id": "X"},
        {"citation_key": "vit_transformer", "title": "Attention is all you need",
         "author": "Vaswani, A. and Shazeer, N.", "year": "2017", "doi": "10.1/abc", "version": 2, "arxiv_id": "X"},
        {"citation_key": "vit_transformer", "title": "Attention is all you need",
         "author": "Vaswani, A. and Shazeer, N.", "doi": "10.1/abc", "version": 3, "arxiv_id": "X"},  # mất year
        {"citation_key": "different", "title": "Attention is all you need", "author": "Zed Someone",
         "version": 1, "arxiv_id": "X"},  # cùng tiêu đề nhưng tác giả khác: KHÔNG gộp
    ]
    merged, replacements, _ = D.deduplicate_keys(entries)
    by_key = {e["citation_key"]: e for e in merged}
    assert set(by_key) == {"vit_transformer", "different"} and replacements == {"vaswani2017": "vit_transformer"}
    unified = by_key["vit_transformer"]
    assert unified["year"] == "2017" and unified["doi"] == "10.1/abc" and unified["source_versions"] == [1, 2, 3]
    row = D.to_bibtex_row(unified)
    assert row["year"] == 2017 and row["authors"] == ["Vaswani, A.", "Shazeer, N."] and row["doi"] == "10.1/abc"


def test_rewrite_citations_keeps_comments_and_optional_args():
    text = "see \\cite[p.~3]{old,keep} and \\citep{old} % \\cite{old}\n"
    new, count = D.rewrite_citations_with_count(text, {"old": "new"})
    assert count == 2 and "\\cite[p.~3]{new,keep}" in new and "\\citep{new}" in new
    assert new.rstrip().endswith("% \\cite{old}")


# ----------------------------------------------------------------------------- run_m2 end-to-end
def test_run_m2_end_to_end(corpus):
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    from utils.schemas import ALL_SCHEMAS

    pdir = C.paper_dir("2602.04870")
    before = {p.relative_to(corpus).as_posix(): _sha(p) for p in corpus.rglob("*") if p.suffix in {".tex", ".bib"}}
    results = run_m2.run(corpus, export=True)
    after = {p.relative_to(corpus).as_posix(): _sha(p) for p in corpus.rglob("*") if p.suffix in {".tex", ".bib"}}
    assert before == after, "run_m2 không được sửa file nguồn .tex/.bib của M1"

    stats = next(r for r in results if r["arxiv_id"] == "2602.04870")
    assert stats["paper"] == "2602-04870"
    assert stats["bibtex_from_bib_files"] == 2 and stats["bibtex_from_bibitem"] == 1   # unused.bib bị bỏ qua
    assert stats["canonical_entries"] == 1 and stats["merged_keys"] == 1
    assert stats["citation_keys_rewritten"] >= 1
    assert stats["references_ingested"] == 2 and stats["references_with_arxiv_id"] == 1

    raw = json.loads((pdir / "bibtex_entries_raw.json").read_text())
    assert "unused_key" not in {e["citation_key"] for e in raw}
    assert json.loads((pdir / "cite_map.json").read_text()) == {"vaswani2017": "vit_transformer"}
    rows = json.loads((pdir / "bibtex_merged.json").read_text())
    assert len(rows) == 1 and rows[0]["arxiv_id"] == "2602.04870" and rows[0]["year"] == 2017
    assert rows[0]["doi"] == "10.5555/3295222.3295349" and rows[0]["authors"]
    candidates = json.loads((pdir / "candidates.json").read_text())
    assert candidates and {c["ref_arxiv_id"] for c in candidates} == {"1706.03762"}   # chỉ reference có arXiv ID

    # \cite đổi khóa => câu giống nhau giữa các version chỉ còn MỘT element
    elements = json.loads((pdir / "elements.json").read_text())
    shared = [e for e in elements if e["content"].startswith("We follow the standard attention model")]
    assert len(shared) == 1 and "\\cite{vit_transformer}" in shared[0]["content"]
    edges = json.loads((pdir / "hierarchy.json").read_text())
    assert sorted({e["version"] for e in edges if e["element_id"] == shared[0]["element_id"]}) == [1, 2, 3]

    # bài không có tài liệu tham khảo / bài PDF-only: không lỗi, không có dòng bibtex
    for key in ("hep-th/9901001", "2602.00003"):
        other = next(r for r in results if r["arxiv_id"] == key)
        assert "error" not in other and other.get("canonical_entries", 0) == 0
    assert next(r for r in results if r["arxiv_id"] == "hep-th/9901001")["arxiv_id"] == "hep-th/9901001"

    # Parquet khớp schema, bibtex có đúng 1 dòng
    for name in ("elements", "hierarchy", "bibtex"):
        assert pq.read_table(corpus / "export" / f"{name}.parquet").schema.equals(ALL_SCHEMAS[name])
    bib = pq.read_table(corpus / "export" / "bibtex.parquet").to_pandas()
    assert len(bib) == 1 and bib.loc[0, "arxiv_id"] == "2602.04870" and list(bib.loc[0, "authors"])

    bench = json.loads((corpus / "benchmark" / "m2_benchmark.json").read_text())
    assert bench["n_papers"] == 3 and bench["milestone"] == "m2" and "peak_ram_mb" in bench
    assert bench["export_parquet_bytes"] > 0 and bench["m2_output_bytes"] >= bench["export_parquet_bytes"]
    from utils.benchmark import dir_size
    assert 0 < bench["m2_output_bytes"] < dir_size(corpus)  # chỉ phần M2, không gồm .tex/.bib/paper.json của M1
