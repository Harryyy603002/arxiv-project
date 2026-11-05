"""Shared validation schemas for M1 references and M2 candidate outputs."""

from __future__ import annotations

REFERENCE_FIELDS = (
    "arxiv_id",
    "ref_arxiv_id",
    "ref_title",
    "ref_authors",
    "ref_submission_date",
    "ref_venue",
    "ref_s2_id",
    "external_ids",
)
CANDIDATE_FIELDS = (
    "candidate_id",
    "arxiv_id",
    "citation_key",
    "ref_s2_id",
    "ref_arxiv_id",
    "reference_index",
    "title_similarity",
    "author_similarity",
    "bib_title",
    "ref_title",
)


def validate_rows(rows: list[dict], required_fields: tuple[str, ...]) -> None:
    """Raise a descriptive error when exported rows violate a shared schema."""
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"row {index} is not an object")
        missing = [field for field in required_fields if field not in row]
        if missing:
            raise ValueError(f"row {index} is missing fields: {', '.join(missing)}")


def validate_reference_rows(rows: list[dict]) -> None:
    validate_rows(rows, REFERENCE_FIELDS)
    for index, row in enumerate(rows):
        if not row["arxiv_id"] or not row["ref_arxiv_id"]:
            raise ValueError(f"reference row {index} has an empty identifier")


def validate_candidate_rows(rows: list[dict]) -> None:
    validate_rows(rows, CANDIDATE_FIELDS)
    candidate_ids = [row["candidate_id"] for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be unique")
    for index, row in enumerate(rows):
        if not row["candidate_id"]:
            raise ValueError(f"candidate row {index} has an empty candidate_id")
        for field in ("title_similarity", "author_similarity"):
            if not 0 <= float(row[field]) <= 1:
                raise ValueError(f"candidate row {index} has invalid {field}")
