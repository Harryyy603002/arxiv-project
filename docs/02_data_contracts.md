# Data Contracts

This document lists every file format that passes between modules. If you change a format, update this document and the matching PyArrow schema in `src/utils/schemas.py`.

---

## M1 outputs (per paper)

### `data/<paper_key>/meta.json`

Written by `m1_metadata.store_results`. Read by `run_m1.crawl_batch` to populate `paper.json`.

```json
{
  "arxiv_id": "2310.12345",
  "title": "Attention Is All You Need",
  "authors": ["Ashish Vaswani", "Noam Shazeer"],
  "abstract": "We propose a new simple network architecture ...",
  "submission_date": "2017-06-12T17:00:00+00:00",
  "latest_update": "2023-08-02T17:00:00+00:00",
  "latest_version": 7,
  "primary_category": "cs.CL",
  "categories": ["cs.CL", "cs.LG"],
  "journal_ref": "Advances in Neural Information Processing Systems 30",
  "doi": null,
  "comment": "15 pages, 5 figures"
}
```

### `data/<paper_key>/versions.json`

Written by `m1_metadata.store_results` via `build_versions`. Dates for intermediate versions are `null` because the arXiv API only returns the v1 date and the latest-version date.

```json
{
  "arxiv_id": "2310.12345",
  "latest_version": 3,
  "versions": [
    {"version": "v1", "id_with_version": "2310.12345v1", "date": "2023-10-18T17:00:00+00:00"},
    {"version": "v2", "id_with_version": "2310.12345v2", "date": null},
    {"version": "v3", "id_with_version": "2310.12345v3", "date": "2024-01-10T17:00:00+00:00"}
  ]
}
```

### `data/<paper_key>/v{N}/metadata.json`

Written by `m1_download.process_version` (standalone path) or by `run_m1.crawl_batch` (integrated path). This file acts as a **checkpoint**: if it exists, the version is skipped on the next run.

```json
{
  "arxiv_id": "2310.12345v1",
  "version": 1,
  "status": "ok",
  "reason": "",
  "files": ["main.tex", "sections/intro.tex", "refs.bib"],
  "archive_bytes": 1048576,
  "bytes_before": 2097152,
  "bytes_after": 32768,
  "source_url": "https://export.arxiv.org/src/2310.12345v1",
  "downloaded_at_utc": "2026-10-20T08:00:00+00:00"
}
```

`status` is `"ok"` (has `.tex` files) or `"empty"` (no TeX source). `reason` explains empty: `"pdf_only"`, `"no_tex_in_archive"`, `"corrupt_archive"`, `"single_file_not_tex"`, `"unknown_format"`.

### `data/<paper_key>/refs.json`

Normalized references. Written by `m1_references.write_refs`. This is the **M2/M3 input** for reference matching.

```json
{
  "arxiv_id": "2310.12345",
  "references": [
    {
      "ref_arxiv_id": "1706.03762",
      "ref_title": "Attention Is All You Need",
      "ref_authors": ["Ashish Vaswani", "Noam Shazeer"],
      "ref_submission_date": "2017-06-12",
      "ref_venue": "NeurIPS",
      "ref_s2_id": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
      "external_ids": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222.3295349"}
    }
  ]
}
```

> **Note:** `refs.json` stores **all** crawled references, not just those with an arXiv ID. Only references where `ref_arxiv_id` is non-null go into the final published `references` table. Keep everything locally.

### `data/<paper_key>/refs_raw.json`

The raw Semantic Scholar API response, with an envelope. Written by `m1_references.write_raw_refs`. Useful for debugging.

```json
{
  "arxiv_id": "2310.12345",
  "retrieved_at": "2026-10-20T08:01:00+00:00",
  "endpoint": "https://api.semanticscholar.org/graph/v1/paper/arXiv:2310.12345",
  "response": { "...raw S2 paper object..." }
}
```

### `data/<paper_key>/v{N}/*.tex`, `*.bib`

The extracted source files. Only `.tex` and `.bib` are kept (configured in `config.KEEP_EXTENSIONS`). Figure binaries and other files are discarded at extraction time.

---

## M2 outputs (per paper)

### `data/<paper_key>/v{N}/tree_raw.json`

Written by `m2_hierarchy.py`. The raw parsed tree before cleanup and deduplication. Structure:

```json
{
  "root": {
    "type": "document",
    "children": [
      {
        "type": "section",
        "title": "Introduction",
        "children": [
          {"type": "paragraph", "text": "We study citation matching ..."},
          {"type": "equation", "text": "\\begin{equation} E = mc^2 \\end{equation}"}
        ]
      }
    ]
  }
}
```

### `data/<paper_key>/elements.json`

Written by `m2_content_dedup.process_paper`. One row per **distinct** element across all versions. Identical content across versions shares one `element_id`.

```json
[
  {"element_id": "2310-12345#root1", "arxiv_id": "2310.12345", "kind": "document", "content": "Version 1 root"},
  {"element_id": "2310-12345#h1",    "arxiv_id": "2310.12345", "kind": "heading",  "content": "Introduction"},
  {"element_id": "2310-12345#s1",    "arxiv_id": "2310.12345", "kind": "sentence", "content": "We study citation matching."}
]
```

`kind` values: `document`, `heading`, `sentence`, `formula`, `figure`, `itemize`, `item`.

### `data/<paper_key>/hierarchy.json`

Written by `m2_content_dedup.process_paper`. One row per node-parent edge, per version.

```json
[
  {"arxiv_id": "2310.12345", "version": 1, "element_id": "2310-12345#h1", "parent_id": "2310-12345#root1"},
  {"arxiv_id": "2310.12345", "version": 1, "element_id": "2310-12345#s1", "parent_id": "2310-12345#h1"},
  {"arxiv_id": "2310.12345", "version": 2, "element_id": "2310-12345#s1", "parent_id": "2310-12345#h2"}
]
```

### `data/<paper_key>/bibtex_merged.json`

Written by `run_m2.process_paper`. The deduplicated, field-merged BibTeX entries.

```json
[
  {
    "citation_key": "vaswani2017attention",
    "entry_type": "inproceedings",
    "arxiv_id": "2310.12345",
    "title": "Attention Is All You Need",
    "author": "Ashish Vaswani and Noam Shazeer",
    "year": "2017",
    "journal": "NeurIPS",
    "doi": null,
    "url": null
  }
]
```

### `data/<paper_key>/candidates.json`

Written by `run_m2.process_paper`. BibTeX ↔ reference candidate pairs for M3.

```json
[
  {
    "candidate_id": "a3f8e2b1c4d5",
    "arxiv_id": "2310.12345",
    "citation_key": "vaswani2017attention",
    "ref_s2_id": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "ref_arxiv_id": "1706.03762",
    "reference_index": 0,
    "title_similarity": 1.0,
    "author_similarity": 0.95,
    "bib_title": "Attention Is All You Need",
    "ref_title": "Attention Is All You Need",
    "title_exact": true
  }
]
```

---

## Final published tables (Parquet)

These are the six tables uploaded to Hugging Face. Schemas are defined in `src/utils/schemas.py`.

### `papers`

One row per publication (not per version).

| Column | Type | Notes |
|--------|------|-------|
| `arxiv_id` | string, non-null | base ID without version |
| `title` | string | |
| `authors` | list of strings | |
| `submission_date` | string | ISO 8601, v1 date |
| `revised_dates` | list of strings | v2, v3, ... dates |
| `venue` | string, nullable | |

### `references`

One row per crawled reference **that has an arXiv ID**. References without `ref_arxiv_id` are stored locally but not published.

| Column | Type | Notes |
|--------|------|-------|
| `arxiv_id` | string, non-null | citing paper |
| `ref_arxiv_id` | string, non-null | cited paper |
| `ref_title` | string | |
| `ref_authors` | list of strings | |
| `ref_submission_date` | string | |
| `ref_s2_id` | string | Semantic Scholar paper ID |

### `elements`

One row per distinct element across all versions. See `m2_content_dedup.py`.

| Column | Type | Notes |
|--------|------|-------|
| `element_id` | string, non-null | e.g. `2310-12345#s1` |
| `arxiv_id` | string, non-null | |
| `kind` | dictionary string | `sentence`, `heading`, `formula`, `figure`, `document`, `itemize`, `item` |
| `content` | string | cleaned text |

### `hierarchy`

One row per node-parent edge per version.

| Column | Type | Notes |
|--------|------|-------|
| `arxiv_id` | string, non-null | |
| `version` | int32, non-null | |
| `element_id` | string, non-null | |
| `parent_id` | string, non-null | root node has no row in this table |

### `bibtex`

Unified BibTeX entries after deduplication.

| Column | Type | Notes |
|--------|------|-------|
| `arxiv_id` | string, non-null | |
| `citation_key` | string, non-null | canonical key after dedup |
| `entry_type` | dictionary string | `article`, `inproceedings`, etc. |
| `title` | string | |
| `authors` | list of strings | |
| `year` | int32, nullable | |
| `doi` | string | |
| `journal` | string | |
| `url` | string | |

### `predictions`

Output of M3 matching.

| Column | Type | Notes |
|--------|------|-------|
| `arxiv_id` | string, non-null | |
| `partition` | dictionary string | `train`, `valid`, or `test` |
| `citation_key` | string, non-null | |
| `groundtruth_arxiv_id` | string, nullable | null when no match exists |
| `cand_1` to `cand_5` | string, nullable | ranked top-5 predictions |

---

## Schema validation

```python
from utils.schemas import validate_reference_rows, validate_candidate_rows, validate_m2_integrity
# validate_reference_rows(rows)  — raises ValueError on bad refs.json rows
# validate_candidate_rows(rows)  — raises ValueError on bad candidate rows
# validate_m2_integrity(el_df, hier_df, bib_df)  — returns list of error strings
```

Run `python src/utils/schemas.py` for a self-test of all six PyArrow schemas.

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
