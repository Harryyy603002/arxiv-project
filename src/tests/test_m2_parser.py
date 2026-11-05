from parser.m2_bib_dedup import deduplicate_keys, rewrite_citations
from parser.m2_bibitem import parse_bibtex_entries
from matching.m3_candidates import generate_candidates, normalize_text


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
