# Development log

This log records repository evidence, not reconstructed personal contributions. The current `main` tip is `395c16b`. Branches visible in the checkout include `p2/m1-completion`, `p2/m2-completion`, `p1/m2-hierarchy`, and `p4/m2-parquet`. Completed-looking work on another branch must not be treated as current `main` behavior without checking out that branch.

## Current implementation history

The history contains merged M1 downloader/metadata/reference work, M2 hierarchy/parquet branches, and later M2 completion commits on separate branches. The checked-out source still contains M2/M3 skeletons.

## Design decisions evidenced by code

- One serial arXiv source stream and approximately three seconds between requests.
- Bounded overlap of one Semantic Scholar reference worker with source processing.
- API-key support and adaptive 429 handling for Semantic Scholar.
- Raw reference preservation separate from normalized references.
- Atomic source checkpoints in the downloader path.
- Filesystem data exchange instead of a database.

## Debugging notes

- Metadata transport failures are not recorded as nonexistent IDs.
- PDF-only source responses remain represented as empty versions.
- Reference batch requests split when Semantic Scholar rejects a group as too large.
- Comment-only LaTeX lines are preserved by the implemented citation rewriter.

## Newly available project-plan evidence

The project overview and work-allocation PDFs are now present under `docs/`.
They establish the original 18-day schedule, cross-milestone ownership,
buddy-review pairs, handoff contracts, classical M3 approach, and final
deliverables. They are planning evidence; current implementation status still
comes from the checked-out source and tests.

## Decisions still requiring team input

- Reconcile `paper.json` versus `meta.json` and `tree_raw.json` versus actual output names.
- Decide whether to use RapidFuzz instead of `SequenceMatcher`.
- Define final schema ownership and M2/M3 orchestration.
- Record live benchmark and assigned-range results.
- Reconcile the planned handoff contracts with the current output names.

## Change log template

| Date | Contributor | Change | Evidence/commit | Review notes |
|---|---|---|---|---|
| `[TEAM INPUT REQUIRED]` | `[TEAM INPUT REQUIRED]` | `[TODO]` | `[TODO]` | `[TEAM INPUT REQUIRED]` |

## Review

- Maintainer: `[TEAM INPUT REQUIRED]`
- Last verified test run: `pytest src/tests` → 14 passed.


---
