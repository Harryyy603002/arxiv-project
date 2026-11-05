"""Run the deterministic M2 BibTeX and candidate pipeline."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from matching.m3_candidates import generate_candidates
from parser.m2_bib_dedup import deduplicate_keys, rewrite_citations
from parser.m2_bibitem import parse_bibtex_entries
from utils import config as C
from utils.schemas import validate_candidate_rows


def _corpus_dir() -> Path:
    candidate = C.DATA_DIR / "corpus"
    return candidate if candidate.exists() else C.DATA_DIR


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_references(paper_dir: Path) -> list[dict]:
    path = paper_dir / "refs.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("references", []) if isinstance(payload, dict) else []
    version_path = next(iter(sorted(paper_dir.glob("v*/references.json"))), None)
    if version_path is None:
        return []
    payload = json.loads(version_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else []


def process_paper(paper_dir: Path) -> dict:
    """Process one paper directory and return auditable M2 counts."""
    entries = []
    bib_files = sorted(paper_dir.glob("v*/*.bib"))
    for bib_file in bib_files:
        version_text = bib_file.parent.name.removeprefix("v")
        version = int(version_text) if version_text.isdigit() else None
        entries.extend(parse_bibtex_entries(
            bib_file.read_text(encoding="utf-8", errors="replace"),
            paper_dir.name,
            version,
        ))

    merged, replacements, conflicts = deduplicate_keys(entries)
    for tex_file in sorted(paper_dir.glob("v*/*.tex")):
        text = tex_file.read_text(encoding="utf-8", errors="replace")
        rewritten = rewrite_citations(text, replacements)
        if rewritten != text:
            tex_file.write_text(rewritten, encoding="utf-8")

    references = _load_references(paper_dir)
    candidates = generate_candidates(merged, references)
    validate_candidate_rows(candidates)
    candidates_per_entry = {
        entry.get("citation_key"): sum(
            candidate["citation_key"] == entry.get("citation_key")
            for candidate in candidates
        )
        for entry in merged
    }
    candidate_counts = list(candidates_per_entry.values())
    exact_title_candidates = sum(candidate["title_exact"] for candidate in candidates)
    _atomic_json(paper_dir / "bibtex_merged.json", merged)
    _atomic_json(paper_dir / "candidates.json", candidates)
    stats = {
        "bibtex_entries_ingested": len(entries),
        "canonical_entries": len(merged),
        "merged_keys": len(replacements),
        "citation_keys_rewritten": len(replacements),
        "bibtex_conflicts": len(conflicts),
        "references_ingested": len(references),
        "candidate_pairs": len(candidates),
        "zero_candidate_entries": sum(
            count == 0 for count in candidates_per_entry.values()
        ),
        "candidates_per_entry": candidates_per_entry,
        "candidate_count_min": min(candidates_per_entry.values(), default=0),
        "candidate_count_max": max(candidates_per_entry.values(), default=0),
        "candidate_count_average": (
            round(statistics.fmean(candidate_counts), 3) if candidate_counts else 0.0
        ),
        "candidate_count_median": (
            statistics.median(candidate_counts) if candidate_counts else 0
        ),
        "exact_title_candidates": exact_title_candidates,
        "fuzzy_title_candidates": len(candidates) - exact_title_candidates,
    }
    _atomic_json(paper_dir / "m2_stats.json", stats)
    return stats


def run(corpus_dir: Path | None = None) -> list[dict]:
    root = corpus_dir or _corpus_dir()
    results = []
    for paper_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if any(paper_dir.glob("v*/*.bib")):
            results.append({"paper": paper_dir.name, **process_paper(paper_dir)})
    _atomic_json(C.DATA_DIR / "m2_stats.json", results)
    return results


def main() -> None:
    run()


if __name__ == "__main__":
    main()
