"""Reference candidate generation and lightweight matching features."""

from __future__ import annotations

import re
import unicodedata
import hashlib
import json
from difflib import SequenceMatcher


def _strip_latex(text: str) -> str:
    text = re.sub(r"\\([A-Za-z]+)", r" \1 ", text)
    return re.sub(r"\\([^A-Za-z\s])", r"\1", text)


def normalize_text(value: object) -> str:
    """Normalize comparison text while leaving source fields untouched."""
    text = _strip_latex(str(value or ""))
    text = text.replace("{", "").replace("}", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_authors(value: object) -> str:
    if isinstance(value, list):
        names = [item.get("name", "") if isinstance(item, dict) else str(item) for item in value]
    else:
        names = re.split(r"\s+\band\b\s+", str(value or ""), flags=re.IGNORECASE)

    normalized = []
    for name in names:
        name = normalize_text(name)
        if "," in name:
            last, first = (part.strip() for part in name.split(",", 1))
            name = f"{first} {last}".strip()
        normalized.append(name)
    return " ".join(name for name in normalized if name)


def similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def generate_candidates(bib_entries: list[dict], references: list[dict],
                        min_title_similarity: float = 0.35,
                        diagnostics: dict[str, int] | None = None) -> list[dict]:
    """Generate within-paper BibTeX/reference pairs with comparable features."""
    candidates = []
    for bib in bib_entries:
        bib_title = bib.get("title", "")
        bib_authors = normalize_authors(bib.get("author", bib.get("authors", "")))
        for reference_index, reference in enumerate(references):
            reference_title = reference.get("ref_title", reference.get("title", ""))
            reference_authors = normalize_authors(
                reference.get("ref_authors", reference.get("authors", ""))
            )
            title_score = similarity(bib_title, reference_title)
            if title_score < min_title_similarity:
                if diagnostics is not None:
                    reason = "missing_title" if not normalize_text(bib_title) or not normalize_text(
                        reference_title
                    ) else "title_below_threshold"
                    diagnostics[reason] = diagnostics.get(reason, 0) + 1
                continue
            candidate = {
                "arxiv_id": bib.get("arxiv_id", reference.get("arxiv_id")),
                "citation_key": bib.get("citation_key"),
                "ref_s2_id": reference.get("ref_s2_id", reference.get("paperId")),
                "ref_arxiv_id": reference.get("ref_arxiv_id"),
                "reference_index": reference_index,
                "title_similarity": title_score,
                "author_similarity": similarity(bib_authors, reference_authors),
                "bib_title_normalized": normalize_text(bib_title),
                "ref_title_normalized": normalize_text(reference_title),
                "bib_authors_normalized": bib_authors,
                "ref_authors_normalized": reference_authors,
                "title_exact": normalize_text(bib_title) == normalize_text(reference_title),
                "bib_title": bib_title,
                "ref_title": reference_title,
            }
            identity = {
                "arxiv_id": candidate["arxiv_id"],
                "citation_key": candidate["citation_key"],
                "ref_s2_id": candidate["ref_s2_id"],
                "ref_arxiv_id": candidate["ref_arxiv_id"],
                "reference_index": candidate["reference_index"],
            }
            candidate["candidate_id"] = hashlib.sha256(
                json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")
            ).hexdigest()[:16]
            candidates.append(candidate)
    return candidates
