from parser.m2_bib_dedup import (
    deduplicate_keys,
    rewrite_citations,
    rewrite_citations_with_count,
)
from parser.m2_bibitem import parse_bibtex_entries
from matching.m3_candidates import generate_candidates, normalize_authors, normalize_text
from run_m2 import process_paper
import json


def test_parse_bibtex_entries_preserves_provenance_and_fields():
    entries = parse_bibtex_entries(
        '@article{KeyA, title = {A {Title}}, author = "Doe, Jane and Smith, John", year=2020}',
        "1234.56789",
        2,
    )

    assert entries == [{
        "citation_key": "KeyA",
        "entry_type": "article",
        "title": "A {Title}",
        "author": "Doe, Jane and Smith, John",
        "year": "2020",
        "arxiv_id": "1234.56789",
        "version": 2,
    }]


def test_deduplicate_keys_merges_fields_and_rewrites_only_active_citations():
    entries = [
        {"citation_key": "first", "title": "Same title", "author": "A", "year": "2020",
         "doi": "10/example"},
        {"citation_key": "second", "title": "Same title", "author": "A", "year": "2020",
         "url": "https://example.test"},
    ]

    merged, replacements, conflicts = deduplicate_keys(entries)

    assert len(merged) == 1
    assert replacements == {"second": "first"}
    assert merged[0]["url"] == "https://example.test"
    assert conflicts == []
    assert rewrite_citations("\\cite{first, second}\n% \\cite{second}\n", replacements) == (
        "\\cite{first,first}\n% \\cite{second}\n"
    )
    rewritten, count = rewrite_citations_with_count(
        "\\cite{first, second}\n% \\cite{second}\n", replacements
    )
    assert rewritten.startswith("\\cite{first,first}")
    assert count == 1


def test_candidate_generation_stays_within_citing_paper_and_keeps_features():
    candidates = generate_candidates(
        [{"arxiv_id": "1234.56789", "citation_key": "key",
          "title": "A Study of Models", "author": "Doe, Jane"}],
        [{"ref_arxiv_id": "9876.54321", "ref_s2_id": "s2",
          "ref_title": "A Study of Models", "ref_authors": ["Jane Doe"]}],
    )

    assert len(candidates) == 1
    assert candidates[0]["arxiv_id"] == "1234.56789"
    assert candidates[0]["ref_arxiv_id"] == "9876.54321"
    assert candidates[0]["title_similarity"] == 1.0
    assert normalize_text(r"{A \LaTeX}  Study") == "a latex study"
    assert candidates[0]["title_exact"] is True
    assert candidates[0]["bib_title_normalized"] == "a study of models"
    assert len(candidates[0]["candidate_id"]) == 16
    assert candidates[0]["candidate_id"] == generate_candidates(
        [{"arxiv_id": "1234.56789", "citation_key": "key",
          "title": "A Study of Models", "author": "Doe, Jane"}],
        [{"ref_arxiv_id": "9876.54321", "ref_s2_id": "s2",
          "ref_title": "A Study of Models", "ref_authors": ["Jane Doe"]}],
    )[0]["candidate_id"]


def test_normalization_handles_accents_latex_and_author_forms():
    assert normalize_text(r"{Étude} \& {\LaTeX}") == "etude & latex"
    assert normalize_authors("Doe, Jane and García Márquez, Gabriel") == (
        "jane doe gabriel garcia marquez"
    )
    assert normalize_authors([{"name": "Jane Doe"}, {"name": "García Márquez"}]) == (
        "jane doe garcia marquez"
    )
    assert generate_candidates(
        [{"arxiv_id": "1234.56789", "citation_key": "empty", "title": ""}],
        [{"ref_s2_id": "s2", "ref_title": ""}],
    ) == []


def test_process_paper_writes_merged_candidates_and_stats(tmp_path):
    paper = tmp_path / "1234-56789"
    version = paper / "v1"
    version.mkdir(parents=True)
    (version / "main.bib").write_text(
        "@article{first, title={A Study}, author={Doe}, year=2020}\n",
        encoding="utf-8",
    )
    (version / "main.tex").write_text("\\cite{first}\n", encoding="utf-8")
    (paper / "refs.json").write_text(json.dumps({
        "arxiv_id": "1234.56789",
        "references": [{"paperId": "s2", "title": "A Study",
                        "externalIds": {"ArXiv": "9876.54321"}}],
    }), encoding="utf-8")

    stats = process_paper(paper)

    assert stats["bibtex_entries_ingested"] == 1
    assert stats["candidate_pairs"] == 1
    assert stats["candidates_per_entry"] == {"first": 1}
    assert stats["candidate_count_min"] == 1
    assert stats["candidate_count_max"] == 1
    assert (paper / "bibtex_merged.json").exists()
    assert (paper / "bibtex_entries_raw.json").exists()
    assert (paper / "citation_key_replacements.json").exists()
    assert (paper / "candidate_entry_index.json").exists()
    assert stats["bibtex_entries_with_title"] == 1
    assert stats["bibtex_entries_with_authors"] == 1
    assert stats["duplicate_bibtex_entries"] == 0
    assert stats["zero_candidate_percentage"] == 0.0
    assert stats["candidate_generation_skipped"] == 0
    assert (paper / "candidates.json").exists()
    assert (paper / "m2_stats.json").exists()