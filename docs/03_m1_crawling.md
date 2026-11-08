# M1 — Data Crawling

## 1. Overview

M1 has standalone modules under `src/crawler/` and an integrated `src/run_m1.py`. It resolves assigned IDs, discovers versions, downloads every discovered source version, preserves PDF-only records, retrieves reference metadata, and records crawl metrics. Live completeness and a 20-hour result are `[TO BE VERIFIED]`.

## 2. Requirements

The official requirement is targeted arXiv discovery, full-text LaTeX collection for every version, metadata, Semantic Scholar references, organized raw outputs, and corpus analysis. The implementation uses one serial arXiv source stream with a three-second delay. It does not use IP switching or parallel source workers.

The team schedule starts the long-running crawl on Day 2 so the three-second
arXiv delay and Semantic Scholar rate limits do not block later parsing work.
This is a planning decision, not a measured runtime result.

## 3. Repository modules

- `crawler/m1_metadata.py`: metadata, versions, failed IDs, papers table.
- `crawler/m1_download.py`: source requests, archive extraction, checkpoints.
- `crawler/m1_references.py`: Semantic Scholar requests, retries, batching.
- `run_m1.py`: integrated discovery, source/reference overlap, metrics.
- `utils/config.py`: identifiers, paths, API settings.
- `utils/benchmark.py`: timing, memory, disk, byte accounting.

## 4. Architecture and workflow

1. Read and deduplicate base IDs and strip supplied version suffixes.
2. Resolve latest metadata/version count, then query versioned IDs for dates.
3. Start one Semantic Scholar batch future for the group.
4. Fetch each source version serially through the canonical downloader.
5. Save only `.tex` and `.bib`, retaining an empty record for PDF-only source.
6. Join the reference future, write raw/normalized references, and record metrics.

## 5. Function documentation

### `config.split_version`

- **Purpose/location:** split a base arXiv ID and optional `vN`; `src/utils/config.py`.
- **Parameters:** `arxiv_id: str`.
- **Returns:** `(base_id, Optional[int])`.
- **Dependencies:** regular expression only.
- **Side effects:** none.
- **Edge cases:** whitespace is stripped; the helper assumes the suffix pattern is at the end.

### `config.paper_key`

- **Purpose:** create filesystem key from the base ID.
- **Returns:** base ID with `/` and `.` replaced by `-`.
- **Side effects:** none.
- **Compatibility:** do not use for data-field IDs.

### `m1_metadata.parse_entry_id`

- **Purpose:** parse `/abs/<base>vN` returned by arXiv.
- **Returns:** `(base, version)` or `None` for API error/nonmatching entries.
- **Tests:** old-style and new-style IDs plus invalid API error are covered.

### `m1_metadata.build_versions`

- **Purpose:** create a version manifest.
- **Inputs:** base ID, latest version, v1 date, latest date.
- **Output:** `{arxiv_id, latest_version, versions[]}`; intermediate dates are `null` because the API path only supplies v1/latest dates.

### `m1_metadata.build_meta`

- **Purpose:** normalize an arXiv result into JSON metadata.
- **Transformations:** collapses title/abstract whitespace; extracts author names, dates, categories, journal, DOI, and comment.

### `m1_metadata.store_results`

- **Purpose:** map API results to requested IDs, write `meta.json` and `versions.json`, and return `(ok_count, missing_ids)`.
- **Error behavior:** malformed result IDs are ignored; missing requested IDs are returned for `failed_ids.txt`.

### `m1_download.fetch_source`

- **Purpose:** download one version from `https://export.arxiv.org/src/<id>vN`.
- **Parameters:** `requests.Session`, base ID, integer version.
- **Returns:** source bytes.
- **Retries:** network errors and HTTP 429/500/502/503/504, up to four attempts with increasing waits.
- **Failure:** raises `DownloadError`; failed versions are not successful checkpoints.

### `m1_download.save_source_with_stats`

- **Purpose:** classify and extract source bytes.
- **Output:** `(status, reason, files, archive_bytes, before_bytes, after_bytes)`.
- **Behavior:** recognizes PDF-only, safely rejects absolute/parent traversal paths, retains `.tex`/`.bib`, skips files over 50 MB, and calculates uncompressed before/kept after bytes.
- **Side effects:** writes extracted files under the supplied version directory.

### `m1_references.fetch_references`

- **Purpose:** retrieve all references for one arXiv paper.
- **API:** Semantic Scholar Graph endpoint with configured fields and limit 1000.
- **Retries:** 429/5xx, numeric `Retry-After`, exponential backoff, adaptive delay, and cooldown after consecutive 429 failures.
- **Failure:** raises `ReferenceCrawlError` for invalid JSON, missing reference list, or exhausted retries.

### `m1_references.fetch_batch_group`

- **Purpose:** POST several `ARXIV:<id>` identifiers to the batch endpoint.
- **Validation:** response must be a list with the same length/order as the request.
- **Edge case:** HTTP 413 triggers recursive/smaller-group handling.

### `run_m1.discover`

- **Purpose:** discover latest version count and per-version dates for a list.
- **Output:** mapping from base ID to title, authors, venue, version count, and date map.
- **External calls:** arXiv API in batches.

### `run_m1.crawl_batch`

- **Purpose:** overlap one reference batch with serial source processing.
- **Side effects:** writes corpus files and updates `PerfRecorder`/coverage counters.
- **Tests:** overlap, checkpoint reuse, and source/reference coordination are covered.

## 6. API integration

- arXiv metadata uses `arxiv.Client` and `arxiv.Search(id_list=...)` with a three-second delay.
- Source uses `https://export.arxiv.org/src/{id_with_version}`.
- `CONTACT_EMAIL` is used in User-Agent; it does not increase throughput.
- Semantic Scholar uses `/graph/v1/paper/arXiv:{id}` or `/graph/v1/paper/batch`.
- `S2_API_KEY`, when present, is sent as `x-api-key`.
- Requested reference fields are paper ID, title, authors, year, publication date, venue, and external IDs.

## 7. Data flow

`ID list → arXiv Result → meta.json/versions.json → versioned source → .tex/.bib and metadata → refs_raw.json/refs.json → planned M2/M3 consumers`.

## 8. Error handling and edge cases

Network failures are retried; non-retryable source responses stop the version attempt. Metadata transport failures are not marked as missing. Reference batches split on 413; malformed response counts and invalid JSON fail explicitly. API headers are not written to outputs.

## 9. Testing and validation

The verified baseline is `pytest src/tests` with 14 passing tests. Tests cover metadata parsing/writes, reference batching/cache/splitting, source extraction/byte accounting, checkpoints, and overlap. No live API or assigned-range validation was run.

## 10. Known issues

- `run_m1.build_references_table()` is a no-op compatibility hook.
- Standalone and integrated path/output names differ.
- No assigned-ID completeness, runtime, disk, or memory result is committed.

## 11. Team review

- Author: `[TEAM INPUT REQUIRED]`
- Reviewer: `[TEAM INPUT REQUIRED]`
- Design decisions to confirm: single compliant arXiv stream; bounded reference overlap; API-key configuration.
- Additional notes: `[TEAM INPUT REQUIRED]`

## 12. Ownership and schedule

P1 owns source download and source/structure work. P2 owns Semantic Scholar
references and reference filtering. P3 owns metadata/version discovery. P4
owns the M1 skeleton, report, statistics, and performance measurement. P1 and
P3 are the planned buddy-review pair; P2 and P4 form the other pair.


---
