# Integration

## 1. Milestone communication

M1-to-M2 hands off `meta.json`, `versions.json`, per-version metadata, and `.tex`/`.bib` files. M1-to-M3 hands off `refs.json` and normalized `ref_*` fields. M2-to-M3 is intended to provide unified BibTeX rows and hierarchy-derived context, but no complete producer exists on `main`.

## 2. Execution order

```text
run_m1 → gather → hierarchy → cleanup → sentences
        → BibTeX extraction/dedup → content dedup → Parquet
        → labels/features/model → predictions/evaluation → publish
```

Only the M1 portion is verified executable on the current checkout.

The team plan intentionally overlaps the stages: M2 coding starts while M1
crawls, and M3 labeling starts once M2 produces candidate files. The planned
handoff is therefore incremental rather than a single end-of-milestone batch.

## 3. Interfaces and payloads

Data fields retain original arXiv IDs; `paper_key` is filesystem-only. Version paths use `vN`; parser provenance uses integer versions where implemented. Reference payloads use `ref_s2_id`, `ref_arxiv_id`, `ref_title`, `ref_authors`, `ref_submission_date`, `ref_venue`, and `external_ids`.

## 4. Cross-module dependencies and risks

- `run_m1.folder_name` and `config.paper_key` produce different slash substitutions.
- README describes `paper.json`/`tree_raw.json`, while implemented M1 paths use `meta.json`/`refs.json`.
- `run_m1.build_references_table()` returns `0` and is not an exporter.
- `utils.schemas` does not validate the final six tables.
- Candidate generation accepts multiple reference field spellings but no final-table adapter exists.
- No implemented M2 producer guarantees citation rewriting before content identity.

## 5. Integration review

- Owner for final schema reconciliation: `[TEAM INPUT REQUIRED]`
- Owner for end-to-end runner: `[TEAM INPUT REQUIRED]`
- Required compatibility tests: `[TODO]`
- Additional notes: `[TEAM INPUT REQUIRED]`

## 6. Buddy review and ownership

The planned pull-request review pairs are P1 ↔ P3 and P2 ↔ P4. P4 also
supports P1 with parser tests because parser edge cases are expected to be the
highest-risk part of M2.


---
