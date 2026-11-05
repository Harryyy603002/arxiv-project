"""Reference candidate generation and lightweight matching features."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def normalize_text(value: object) -> str:
    """Normalize comparison text while leaving source fields untouched."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[{}\\]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_authors(value: object) -> str:
    if isinstance(value, list):
        names = [item.get("name", "") if isinstance(item, dict) else str(item) for item in value]
        return " ".join(normalize_text(name) for name in names if name)
    return normalize_text(value)


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def generate_candidates(bib_entries: list[dict], references: list[dict],
                        min_title_similarity: float = 0.35) -> list[dict]:
    """Generate within-paper BibTeX/reference pairs with comparable features."""
    candidates = []
    for bib in bib_entries:
        bib_title = bib.get("title", "")
        bib_authors = normalize_authors(bib.get("author", bib.get("authors", "")))
        for reference in references:
            reference_title = reference.get("ref_title", reference.get("title", ""))
            reference_authors = normalize_authors(
                reference.get("ref_authors", reference.get("authors", ""))
            )
            title_score = similarity(bib_title, reference_title)
            if title_score < min_title_similarity:
                continue
            candidates.append({
                "arxiv_id": bib.get("arxiv_id", reference.get("arxiv_id")),
                "citation_key": bib.get("citation_key"),
                "ref_s2_id": reference.get("ref_s2_id", reference.get("paperId")),
                "ref_arxiv_id": reference.get("ref_arxiv_id"),
                "title_similarity": title_score,
                "author_similarity": similarity(bib_authors, reference_authors),
                "bib_title": bib_title,
                "ref_title": reference_title,
            })
    return candidates
