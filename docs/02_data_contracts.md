# Data contracts

This document separates actual producer/consumer payloads from intended final-table schemas. No final dataset is committed.

## Shared identifier rules

Data fields retain original arXiv IDs, including old-style IDs such as `hep-th/9901001`. Filesystem directories use `paper_key`; `utils.config.paper_key` replaces `/` and `.` with `-`. `run_m1.folder_name` uses `_` for `/`, which is a compatibility risk.

## Interface documentation rule

For each boundary, record producer, consumer, payload, validation, example, and compatibility risk. The tables below summarize the verified boundaries; unfinished producers are explicitly marked.

## Synthetic candidate payload

```json
{
  "arxiv_id": "1234.56789",
  "citation_key": "key",
  "ref_s2_id": "s2-id",
  "ref_arxiv_id": "9876.54321",
  "title_similarity": 1.0,
  "author_similarity": 0.8,
  "bib_title": "A Study",
  "ref_title": "A Study"
}
```

## Final-table boundary

The official statements define `papers`, `references`, `elements`, `hierarchy`, `bibtex`, and `predictions`. `utils.schemas.py` is still a stub, so final types must be verified against the official statements when export is implemented.

## Team-agreed handoff contracts

The work-allocation document records these interfaces:

- `data/<paper_key>/v1/` contains source files plus `meta.json` and
  `refs.json`.
- P3 → P1: `versions.json` lists versions to download.
- P1 → P3: raw hierarchy JSON contains document/section/paragraph-like nodes
  with uncleaned text.
- P2 → P4: merged BibTeX follows the assignment's `bibtex` columns.
- Labels use CSV columns `arxiv_id,citation_key,groundtruth_arxiv_id`;
  `groundtruth_arxiv_id` is empty when no answer exists.

These agreed contracts differ from some current implementation names and are
therefore compatibility checkpoints, not proof that the current branch
produces every contract.


---
