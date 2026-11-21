"""Candidate generation for M3 reference matching.

Compatibility contract
----------------------
The public names/signatures ``normalize_text``, ``normalize_authors``,
``similarity`` and ``generate_candidates`` are kept compatible with M2.
The output rows intentionally retain the existing candidate fields; no new
column is added, so existing ``CANDIDATE_FIELDS`` / validators remain valid.

Improvement over the original implementation: a pair is retained when its
arXiv identifier OR DOI matches exactly, even if the title is missing or the
title similarity is below ``min_title_similarity``. This closes the recall
hole for ``\\bibitem`` / incomplete BibTeX entries.

IMPORTANT: ``bibtex_merged.json[*].arxiv_id`` is the ID of the citing paper,
not of the cited work. Therefore it is deliberately NOT used as an arXiv
identifier for the BibTeX entry. Use ``eprint``, an arXiv URL, or raw text.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable

_ARXIV_RE = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)?"
    r"((?:\d{4}\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})(?:v\d+)?)",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\"{}]+", re.IGNORECASE)


def _strip_latex(text: str) -> str:
    text = re.sub(r"\\([A-Za-z]+)", r" \1 ", text)
    return re.sub(r"\\([^A-Za-z\s])", r"\1", text)


def normalize_text(value: object) -> str:
    """Normalize comparison text without mutating source fields."""
    if value is None:
        return ""
    text = _strip_latex(str(value))
    text = text.replace("{", "").replace("}", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_authors(value: object) -> str:
    """Normalize author lists or BibTeX's ``A and B`` author string."""
    if isinstance(value, list):
        names = [item.get("name", "") if isinstance(item, dict) else str(item) for item in value]
    else:
        names = re.split(r"\s+\band\b\s+", str(value or ""), flags=re.IGNORECASE)
    normalized: list[str] = []
    for raw_name in names:
        name = normalize_text(raw_name)
        if "," in name:
            last, first = (part.strip() for part in name.split(",", 1))
            name = f"{first} {last}".strip()
        if name:
            normalized.append(name)
    return " ".join(normalized)


def similarity(left: str, right: str) -> float:
    """SequenceMatcher similarity in [0, 1]."""
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    return float(SequenceMatcher(None, a, b).ratio())


def normalize_arxiv_id(value: object) -> str | None:
    """Return a canonical arXiv ID without ``arXiv:``, URL or version suffix."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\\", "")
    matches = list(_ARXIV_RE.finditer(text))
    if not matches:
        # A plain ID can appear embedded after punctuation.
        candidate = text.strip().rstrip(".,;:)]}")
        m = re.fullmatch(r"(?:arxiv:)?((?:\d{4}\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})(?:v\d+)?)", candidate, re.I)
        if not m:
            return None
        candidate = m.group(1)
    else:
        candidate = matches[0].group(1)
    candidate = re.sub(r"v\d+$", "", candidate, flags=re.I)
    return candidate.lower()


def normalize_doi(value: object) -> str | None:
    """Normalize DOI text, accepting doi.org URLs and ``doi:`` prefixes."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi\s*:\s*", "", text, flags=re.I)
    match = _DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;:)]}").casefold()


def _extract_arxiv_ids(*values: object) -> set[str]:
    found: set[str] = set()
    for value in values:
        if value is None:
            continue
        # eprint is often a bare ID; raw_text/url may contain URLs or prefixes.
        for match in _ARXIV_RE.finditer(str(value)):
            normalized = normalize_arxiv_id(match.group(0))
            if normalized:
                found.add(normalized)
        direct = normalize_arxiv_id(value)
        if direct:
            found.add(direct)
    return found


def _bib_arxiv_ids(entry: dict[str, Any]) -> set[str]:
    # Never use entry['arxiv_id']: M2 sets that to the citing paper's ID.
    return _extract_arxiv_ids(
        entry.get("eprint"), entry.get("url"), entry.get("raw_text"),
        entry.get("howpublished"), entry.get("note"),
    )


def _reference_arxiv_ids(reference: dict[str, Any]) -> set[str]:
    values: list[object] = [reference.get("ref_arxiv_id"), reference.get("arxiv_id")]
    external = reference.get("external_ids") or reference.get("externalIds") or {}
    if isinstance(external, dict):
        values.extend([external.get("ArXiv"), external.get("arxiv")])
    return _extract_arxiv_ids(*values)


def _bib_dois(entry: dict[str, Any]) -> set[str]:
    values = [entry.get("doi"), entry.get("url"), entry.get("raw_text"), entry.get("note")]
    return {doi for value in values if (doi := normalize_doi(value))}


def _reference_dois(reference: dict[str, Any]) -> set[str]:
    values: list[object] = [reference.get("doi")]
    external = reference.get("external_ids") or reference.get("externalIds") or {}
    if isinstance(external, dict):
        values.extend([external.get("DOI"), external.get("doi")])
    return {doi for value in values if (doi := normalize_doi(value))}


def _stable_candidate_id(candidate: dict[str, Any]) -> str:
    identity = {
        "arxiv_id": candidate.get("arxiv_id"),
        "citation_key": candidate.get("citation_key"),
        "ref_s2_id": candidate.get("ref_s2_id"),
        "ref_arxiv_id": candidate.get("ref_arxiv_id"),
        "reference_index": candidate.get("reference_index"),
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def generate_candidates(
    bib_entries: list[dict],
    references: list[dict],
    min_title_similarity: float = 0.35,
    diagnostics: dict[str, int] | None = None,
) -> list[dict]:
    """Generate within-paper candidate pairs.

    A pair is retained if any one of the following holds:
    * normalized title similarity >= ``min_title_similarity``;
    * exact normalized arXiv ID match extracted from eprint/url/raw_text;
    * exact normalized DOI match.

    ``references`` may be the arXiv-only subset used by ``run_m2.py``. The
    ``reference_index`` remains the index in that supplied list, exactly as in
    the old implementation. Never use it to index the unfiltered refs.json.
    """
    if not 0.0 <= min_title_similarity <= 1.0:
        raise ValueError("min_title_similarity phải thuộc [0, 1]")

    prepared_refs: list[tuple[int, dict[str, Any], str, str, set[str], set[str]]] = []
    for ref_index, ref in enumerate(references):
        ref_title = ref.get("ref_title", ref.get("title", "")) or ""
        ref_authors = normalize_authors(ref.get("ref_authors", ref.get("authors", "")))
        prepared_refs.append((
            ref_index, ref, str(ref_title), ref_authors,
            _reference_arxiv_ids(ref), _reference_dois(ref),
        ))

    candidates: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for bib in bib_entries:
        bib_title = bib.get("title", "") or ""
        bib_authors = normalize_authors(bib.get("author", bib.get("authors", "")))
        bib_ids = _bib_arxiv_ids(bib)
        bib_dois = _bib_dois(bib)
        for ref_index, reference, reference_title, reference_authors, ref_ids, ref_dois in prepared_refs:
            title_score = similarity(bib_title, reference_title)
            exact_arxiv = bool(bib_ids & ref_ids)
            exact_doi = bool(bib_dois & ref_dois)
            if title_score < min_title_similarity and not exact_arxiv and not exact_doi:
                if diagnostics is not None:
                    reason = "missing_title" if not normalize_text(bib_title) or not normalize_text(reference_title) else "title_below_threshold"
                    diagnostics[reason] = diagnostics.get(reason, 0) + 1
                continue

            ref_arxiv_id = reference.get("ref_arxiv_id")
            if not ref_arxiv_id:
                ext = reference.get("external_ids") or reference.get("externalIds") or {}
                if isinstance(ext, dict):
                    ref_arxiv_id = ext.get("ArXiv") or ext.get("arxiv")
            if ref_arxiv_id:
                ref_arxiv_id = normalize_arxiv_id(ref_arxiv_id) or str(ref_arxiv_id)

            candidate = {
                "arxiv_id": bib.get("arxiv_id", reference.get("arxiv_id")),
                "citation_key": bib.get("citation_key"),
                "ref_s2_id": reference.get("ref_s2_id", reference.get("paperId")),
                "ref_arxiv_id": ref_arxiv_id,
                "reference_index": ref_index,
                "title_similarity": float(title_score),
                "author_similarity": float(similarity(bib_authors, reference_authors)),
                "bib_title_normalized": normalize_text(bib_title),
                "ref_title_normalized": normalize_text(reference_title),
                "bib_authors_normalized": bib_authors,
                "ref_authors_normalized": reference_authors,
                "title_exact": normalize_text(bib_title) == normalize_text(reference_title) and bool(normalize_text(bib_title)),
                "bib_title": bib_title,
                "ref_title": reference_title,
            }
            candidate["candidate_id"] = _stable_candidate_id(candidate)
            dedupe_key = (str(candidate.get("citation_key") or ""), str(candidate.get("ref_arxiv_id") or ""), ref_index)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            candidates.append(candidate)
            if diagnostics is not None:
                diagnostics["kept_title"] = diagnostics.get("kept_title", 0) + int(title_score >= min_title_similarity)
                diagnostics["kept_arxiv_id"] = diagnostics.get("kept_arxiv_id", 0) + int(exact_arxiv)
                diagnostics["kept_doi"] = diagnostics.get("kept_doi", 0) + int(exact_doi)
                diagnostics["kept_identity_only"] = diagnostics.get("kept_identity_only", 0) + int((exact_arxiv or exact_doi) and title_score < min_title_similarity)

    return candidates
