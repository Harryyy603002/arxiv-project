# P2 Progress Checklist — arXiv Project (M1 & M2)

> **Owner:** P2 — References  
> **Scope:** Milestone 1 (Data Crawling) and Milestone 2 (Parsing & Data Processing)  
> **Purpose:** Give GitHub Copilot a repository-audit checklist. Copilot should inspect the existing code, configuration, tests, data samples, logs, and documentation, then report evidence-based progress.  
> **Important:** This file is an audit specification, not proof that any task is complete. A checkbox should be checked only after repository evidence has been found and verified.

## Current implementation status — 2026-11-05

Checklist work is being completed on branch `checklist/m1-m2-completion` in
small commits. The current verified test baseline is `19 passed` with
`pytest src/tests`.

Completed stages:

- `3cb1c34` — raw Semantic Scholar response checkpoints and provenance.
- `e3bfa3d` — filtered `references.csv` export and reproducible statistics.
- `0255b26` — BibTeX/candidate M2 orchestration.
- `7822308` — M1/M2 output schema validation.
- `5160b95` — usage and handoff documentation.

Full live-sample validation, assigned-dataset statistics, broader cleaning
coverage, annotation handoff, and review/PR evidence remain open.

### Evidence-based audit summary

- **M1 verified:** bounded retries, `Retry-After`, adaptive delay, raw
  response preservation, atomic checkpoints, filtered `references.csv`, and
  schema validation are implemented and covered by `src/tests`.
- **M1 partial:** the API-policy quota cannot be verified offline; failed
  records are surfaced in logs but do not yet have a structured failure
  ledger; the integrated `run_m1.py` path writes version-level reference
  files while the standalone crawler writes paper-level `refs.json`.
- **M2 verified:** BibTeX parsing with source/version provenance,
  deterministic title/author/year deduplication, field merging, citation
  rewriting, within-paper candidate generation, atomic outputs, and basic
  per-paper statistics.
- **M2 partial:** normalization is intentionally lightweight, similarity
  uses `difflib` rather than RapidFuzz, candidate statistics do not yet
  include exact/fuzzy or skipped/failed distributions, and no annotation
  contract or live sample validation has been approved.

Highest-priority follow-up actions:

1. Reconcile paper-level and version-level M1 reference handoff and preserve
   citing-paper provenance on every normalized row.
2. Add a structured failure ledger with paper ID, timestamp, attempts, and
   error category.
3. Expand title/author normalization and fixture tests for accents, braces,
   LaTeX commands, initials, and compound names.
4. Add deterministic candidate distributions and zero-candidate reporting to
   aggregate M2 statistics.
5. Agree and document the M3 annotation schema and run a controlled fixture
   validation before any live crawl.

---

## Instructions for GitHub Copilot

When asked to audit this checklist:

1. Scan the repository before making conclusions. Inspect relevant source files, notebooks, configuration, tests, sample data, generated outputs, and documentation.
2. Map each checklist item to concrete evidence:
   - file path;
   - function/class/notebook cell or line range where possible;
   - test name or command and its result;
   - sample output or schema evidence where applicable.
3. Use these statuses in the audit report:
   - `[x] VERIFIED` — implementation and evidence are present and consistent.
   - `[~] PARTIAL` — some parts exist, but requirements are incomplete or not demonstrated.
   - `[ ] NOT FOUND` — no supporting implementation/evidence was found.
   - `[?] CANNOT VERIFY` — evidence requires credentials, network access, the original assigned-ID list, or execution unavailable in the current environment.
   - `[!] ISSUE` — implementation exists but appears incorrect, unsafe, inconsistent, or contrary to the requirement.
4. Do not mark an item complete merely because a filename, TODO, comment, function stub, or README claim exists. Verify behavior from implementation, tests, or actual output.
5. Do not execute destructive operations, make network requests, crawl the full dataset, overwrite data, install packages, or modify files unless explicitly asked. Prefer static inspection and small, safe tests.
6. Do not expose API keys, tokens, credentials, or private environment values in the report. Confirm only whether configuration is present.
7. Do not invent assigned arXiv IDs, expected row counts, API quotas, schema fields, or successful results. If an input is unavailable, mark the relevant check `CANNOT VERIFY`.
8. Distinguish **raw API response data**, **locally normalized data**, and **the final published table**. Do not treat them as interchangeable.
9. Keep all original raw reference records, including references without arXiv IDs, unless the official project specification explicitly says otherwise. The published M1 `references` table is restricted to references with an arXiv ID.
10. Do not silently change the project design or schema. Flag discrepancies and recommend a change separately.
11. At the end, produce:
    - a status summary by section;
    - a table of findings with evidence paths;
    - bugs/data-quality risks;
    - missing tests;
    - the five highest-priority next actions;
    - any items blocked by credentials, network access, or missing instructor-provided inputs.

---

# Part A — M1: Data Crawling (P2: Semantic Scholar References)

## A0. Inputs, ownership, and interface

- [ ] Confirm the authoritative list of assigned arXiv IDs is available in the repository or supplied input.
- [x] Confirm the list's format and the expected identifier convention (base arXiv ID versus versioned ID). Evidence: `src/utils/config.py`, `src/run_m1.py`.
- [ ] Confirm P3's output/interface for the paper list and version metadata.
- [ ] Confirm the agreed handoff location and file format with P4.
- [x] Confirm the project uses a single canonical representation for arXiv IDs. Evidence: `src/utils/config.py`.
- [x] Confirm P2's code is isolated in a clearly identifiable module, notebook, or package. Evidence: `src/crawler/m1_references.py`.
- [x] Confirm the implementation can be run independently of P1's source-download process. Evidence: `src/crawler/m1_references.py` CLI.
- [x] Confirm there is documentation explaining how to run P2's crawler. Evidence: `README.md`.

**Audit notes**
- The project plan assigns P2 to Semantic Scholar reference crawling in M1.
- The M1 handoff should preserve the relationship between a citing paper and each reference it cites.
- Do not infer the assigned ID list from sample data.

## A1. Semantic Scholar API client and configuration

- [x] Identify the Semantic Scholar Graph API client implementation. Evidence: `src/crawler/m1_references.py`.
- [x] Confirm the endpoint targets a paper using its arXiv identifier. Evidence: `src/utils/config.py`.
- [x] Confirm the request asks for the reference fields required by the project:
  - [x] reference title;
  - [x] reference authors;
  - [x] reference year;
  - [x] reference publication date;
  - [x] reference venue;
  - [x] reference external identifiers.
- [x] Confirm field selection is explicit and documented. Evidence: `src/utils/config.py:S2_REF_FIELDS`.
- [x] Confirm the API key is read from an environment variable or secure configuration mechanism, if used. Evidence: `src/utils/config.py:S2_API_KEY`.
- [x] Confirm no API key or token is hard-coded in source code, notebooks, logs, or committed configuration. Evidence: repository search and `.gitignore`.
- [x] Confirm the client has configurable timeout behavior. Evidence: `src/crawler/m1_references.py:REQUEST_TIMEOUT`.
- [x] Confirm HTTP status codes and API error responses are handled. Evidence: `_post_batch()` and `fetch_references()`.
- [x] Confirm response JSON is validated before downstream processing. Evidence: `validate_reference_payload()`.
- [x] Confirm missing or null `references` is handled without crashing. Evidence: `test_invalid_reference_shape_is_rejected_without_writing`.
- [x] Confirm missing or null nested fields are handled without crashing. Evidence: `_reference_row()`.
- [x] Confirm unexpected response shapes are logged and surfaced. Evidence: `ReferenceCrawlError` and crawl warnings.

**Expected request shape (illustrative; adapt to the project's actual client):**

```text
https://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}
```

The implementation may use a different but equivalent URL construction. Verify the actual request rather than requiring this exact string.

## A2. Rate limiting, retries, and resilience

- [ ] Confirm requests are rate-limited in accordance with the applicable Semantic Scholar API policy and the project's agreed limit.
- [ ] Confirm the rate limiter applies across the full crawl, not just within one helper call.
- [x] Confirm HTTP 429 is handled explicitly. Evidence: `fetch_references()` and `_post_batch()`.
- [x] Confirm `Retry-After` is respected when returned. Evidence: `_retry_after_seconds()`.
- [x] Confirm transient server errors (for example, 5xx) can be retried. Evidence: `RETRY_STATUS`.
- [x] Confirm connection errors and timeouts are handled. Evidence: `requests.RequestException` handling.
- [x] Confirm retries are bounded; requests cannot loop indefinitely. Evidence: `MAX_RETRIES`.
- [x] Confirm backoff behavior is implemented and documented. Evidence: `BACKOFF_SECONDS` and module docstring.
- [x] Confirm permanent errors are not retried endlessly. Evidence: non-retry status handling.
- [~] Confirm failed requests are recorded with the paper ID, timestamp, attempt count, and error category. The crawler logs paper IDs and error categories, but does not yet emit a structured failure ledger.
- [x] Confirm the crawler can resume after interruption. Evidence: `refs.json`/`refs_raw.json` checkpoint checks.
- [x] Confirm successful records are not needlessly re-requested on resume. Evidence: `crawl()` todo filtering.
- [x] Confirm checkpoint updates are written safely (avoid corrupting the checkpoint if the process stops during a write). Evidence: atomic `.tmp` replacement.
- [x] Confirm progress logging reports attempted, successful, skipped, and failed papers. Evidence: `crawl()` progress logging.

**Do not assume a quota from a stale comment.** The audit should identify the configured rate and compare it with the current project documentation or the limit explicitly adopted by the team. If that policy cannot be verified offline, report it as `CANNOT VERIFY`.

## A3. Raw response preservation

- [x] Confirm the raw Semantic Scholar response is saved for each successfully queried citing paper. Evidence: `write_raw_refs()`.
- [x] Confirm raw responses are stored separately from normalized/filtered output. Evidence: `refs_raw.json` versus `refs.json`.
- [x] Confirm the raw record can be traced to its input arXiv ID. Evidence: raw record `arxiv_id`.
- [x] Confirm the raw response preserves references both with and without arXiv IDs. Evidence: raw response is stored before filtering.
- [x] Confirm the raw response is not overwritten by a filtered representation. Evidence: separate atomic paths.
- [x] Confirm the file naming convention is deterministic and safe for arXiv IDs. Evidence: `raw_refs_path()`.
- [x] Confirm partial or failed responses are not mislabeled as complete. Evidence: validation before writes.
- [x] Confirm provenance metadata is recorded where practical (query time, endpoint/API version if known, request status). Evidence: `retrieved_at` and `endpoint`.
- [ ] Confirm raw data is excluded from Git commits if repository policy requires data to remain outside source control.
- [ ] Confirm no secrets or authorization headers are written into raw files or logs.

## A4. Reference extraction and normalization

For each citing paper:

- [x] Extract the `references` collection from the API response. Evidence: `validate_reference_payload()`.
- [x] Preserve the parent/citing paper's arXiv ID on every extracted reference record. Evidence: `_reference_row()`.
- [x] Extract the reference title when available. Evidence: `_reference_row()`.
- [x] Extract the reference authors when available. Evidence: `_reference_row()`.
- [x] Extract publication year when available. Evidence: `_reference_row()`.
- [x] Extract publication date when available. Evidence: `_reference_row()`.
- [x] Extract venue when available. Evidence: `_reference_row()`.
- [x] Extract external identifiers when available. Evidence: `_reference_row()`.
- [x] Extract the Semantic Scholar paper ID when available. Evidence: `_reference_row()`.
- [x] Extract the arXiv identifier from the external-ID mapping when present. Evidence: `_reference_row()`.
- [x] Handle references whose `externalIds` field is absent or null. Evidence: `_reference_row()`.
- [x] Handle references whose arXiv identifier is absent or null. Evidence: raw/local response preservation.
- [ ] Normalize arXiv identifiers consistently (including legacy identifier formats if they occur).
- [ ] Avoid accidentally retaining URL prefixes, whitespace, or version suffixes if the agreed canonical ID is unversioned.
- [x] Preserve the original identifier in raw data; do not destroy provenance during normalization. Evidence: `refs_raw.json`.
- [x] Do not fabricate an arXiv ID from a title, DOI, or Semantic Scholar ID. Evidence: `_reference_row()`.
- [x] Do not discard a reference merely because it has no arXiv ID. Evidence: raw/local response preservation.
- [x] Preserve incomplete references with the fields that are available. Evidence: `_reference_row()`.
- [ ] Distinguish an absent field from an empty string and from a failed API request.

**Relationship semantics — verify carefully**

- [ ] `arxiv_id` means the paper that cites the reference (the citing/source paper).
- [ ] `ref_arxiv_id` means the paper identified by the reference (the cited/target paper).
- [ ] The implementation does not reverse these fields.
- [ ] Each reference row retains the correct parent/citing paper ID.
- [ ] A paper with multiple references produces multiple reference records, not one overwritten record.

## A5. Deduplication and record identity

- [ ] Identify the deduplication key used for reference records.
- [ ] Confirm deduplication does not merge references belonging to different citing papers.
- [ ] Confirm repeated API results for the same citing paper do not multiply its reference rows on rerun.
- [ ] Confirm distinct references are not merged solely because their titles are similar.
- [ ] Confirm records with missing arXiv IDs are retained in raw/local reference data.
- [ ] Confirm duplicate handling is deterministic and documented.
- [ ] Confirm versioned source papers are handled consistently with the project's publication-level `arxiv_id` convention.
- [ ] Confirm the design does not silently collapse different versions' reference lists if the project needs version-level provenance.

**Audit warning:** Do not assume that `(arxiv_id, ref_arxiv_id)` is sufficient as the identity key for every local raw record. Inspect whether multiple versions, duplicate references, or missing target IDs require additional provenance.

## A6. M1 published `references` table

The project plan and statement describe a published M1 references table containing arXiv-linked references. Verify the actual official schema used by the repository and flag any mismatch rather than silently rewriting it.

Expected fields in the project materials:

| Field | Meaning |
|---|---|
| `arxiv_id` | Citing paper's arXiv ID |
| `ref_arxiv_id` | Cited paper's arXiv ID |
| `ref_title` | Cited reference title |
| `ref_authors` | Cited reference authors |
| `ref_submission_date` | Cited paper's submission date, where available |
| `ref_s2_id` | Semantic Scholar ID |

Checklist:

- [x] Locate the schema definition or table-construction code. Evidence: `src/utils/schemas.py`, `build_published_references()`.
- [x] Confirm the output has the required column names. Evidence: `PUBLISHED_REFERENCE_FIELDS`.
- [x] Confirm `arxiv_id` is populated with the citing paper ID. Evidence: `_reference_row()`.
- [x] Confirm `ref_arxiv_id` is populated with the cited paper ID. Evidence: `_reference_row()`.
- [x] Confirm only references with a usable arXiv ID enter the published table, as specified. Evidence: `build_published_references()`.
- [x] Confirm references without arXiv IDs remain available in local raw/reference data. Evidence: `refs_raw.json` and `refs.json`.
- [x] Confirm missing optional metadata is represented consistently. Evidence: `_reference_row()`.
- [x] Confirm `ref_authors` has a stable list/array representation. Evidence: CSV JSON encoding.
- [x] Confirm dates use a consistent documented format. Evidence: `ref_submission_date` mapping.
- [x] Confirm identifier columns use consistent string types. Evidence: CSV writer and schema validation.
- [x] Confirm no accidental nulls in required identifier columns. Evidence: `validate_reference_rows()`.
- [ ] Confirm duplicate rows are measured and handled.
- [ ] Confirm the output format is compatible with the team's agreed handoff and final dataset pipeline.
- [x] Confirm row counts can be reproduced from raw responses. Evidence: `references_stats.json`.
- [x] Confirm a validation script or test checks the schema. Evidence: `validate_reference_rows()` and reference tests.

## A7. M1 tests and validation

- [ ] Unit test: successful response with multiple references.
- [ ] Unit test: response with an empty reference list.
- [ ] Unit test: missing `references` field.
- [ ] Unit test: reference with no `externalIds`.
- [ ] Unit test: reference with no arXiv identifier.
- [ ] Unit test: reference with missing title/authors/date/venue.
- [ ] Unit test: malformed or unexpected response.
- [ ] Unit test: HTTP 429 and retry behavior.
- [ ] Unit test: transient 5xx response.
- [ ] Unit test: timeout/connection failure.
- [ ] Unit test: retry limit is respected.
- [ ] Unit test: checkpoint/resume behavior.
- [ ] Unit test: duplicate rerun does not duplicate output.
- [ ] Unit test: citing/cited IDs are not reversed.
- [ ] Integration test: a small, explicitly selected sample of assigned papers.
- [ ] Validation: compare selected records against the corresponding Semantic Scholar response.
- [ ] Validation: verify raw reference count equals extracted count before filtering, accounting for explicitly documented exclusions.
- [ ] Validation: verify published-table row count equals the number of eligible arXiv-linked references after documented deduplication.

Do not run live API tests during a static audit unless explicitly authorized. If no saved fixtures exist, mark live behavior as `CANNOT VERIFY`.

## A8. M1 handoff to P4 and the team

- [ ] Provide the raw-response directory or documented storage location.
- [ ] Provide the complete local reference collection, including references without arXiv IDs.
- [ ] Provide the filtered M1 `references` table.
- [ ] Provide a schema/data dictionary.
- [ ] Provide crawl logs and failure records.
- [ ] Provide reproducible reference statistics.
- [ ] Document the exact command/configuration needed to rerun the crawler.
- [ ] Document known limitations and unresolved API failures.
- [ ] Confirm P4 can consume the output without manual column renaming.
- [ ] Confirm P1/P3 know the identifier convention and handoff location.

## A9. M1 report statistics to provide

- [ ] Number of assigned/candidate citing papers supplied to P2.
- [ ] Number of unique citing papers attempted.
- [ ] Number of successful API responses.
- [ ] Number of failed API responses.
- [ ] Number of papers skipped and the reason.
- [ ] Total raw references returned.
- [ ] Total references retained in local data.
- [ ] Number of references with arXiv IDs.
- [ ] Number of references without arXiv IDs.
- [ ] Number of references with missing/incomplete metadata.
- [ ] Average references per successfully queried paper.
- [ ] API success rate, with denominator explicitly stated.
- [ ] Metadata completeness rate, with fields and denominator explicitly stated.
- [ ] Duplicate count before and after deduplication.
- [ ] Total crawl duration and average request/paper duration.
- [ ] Number of retries and rate-limit responses.
- [ ] Final local output size, if measured by the team.

---

# Part B — M2: Parsing & Data Processing (P2: Reference Cleaning and Candidate Generation)

## B0. M2 ownership and dependencies

The project plan assigns P2 the following M2 work:

1. Clean reference/BibTeX title and author data.
2. Deduplicate BibTeX keys.
3. Replace citation keys in `\\cite{...}` commands when keys are merged.
4. Merge fields from duplicate BibTeX entries using the agreed policy.
5. Generate candidate pairs between BibTeX entries and Semantic Scholar references.
6. Implement an automatic matcher and string-similarity features.
7. Prepare the candidate file for manual annotation in M3.

Checklist:

- [ ] Locate the M1 reference output and document its schema.
- [ ] Locate P1's parsed BibTeX output and document its schema.
- [ ] Confirm P1's stable paper identifier and BibTeX-entry identifier.
- [ ] Confirm P1's parser output preserves the source publication (`arxiv_id`).
- [ ] Confirm P1's parser output preserves citation keys and source/version provenance.
- [ ] Agree on the input/output contract with P1 before implementing matching.
- [ ] Confirm candidate-generation output can be consumed by the team's M3 annotation workflow.
- [ ] Confirm P2's M2 work is separated into cleaning, deduplication, candidate generation, and matching modules.

## B1. Reference and BibTeX input audit

- [ ] Identify the exact M1 references file consumed by M2.
- [ ] Identify the exact P1 BibTeX output consumed by M2.
- [ ] Confirm both inputs use the same canonical arXiv ID convention.
- [ ] Confirm each BibTeX entry is associated with its citing/source paper.
- [ ] Confirm each Semantic Scholar reference is associated with its citing/source paper.
- [ ] Confirm input schemas are validated before processing.
- [ ] Confirm missing required columns produce a clear error.
- [ ] Confirm null titles/authors are handled safely.
- [ ] Confirm raw input files are not modified in place.
- [ ] Confirm the pipeline can be rerun deterministically.

## B2. Title cleaning and normalization

- [x] Locate the title-cleaning implementation. Evidence: `src/matching/m3_candidates.py:normalize_text`.
- [x] Remove or normalize markup that is irrelevant to title comparison. Evidence: `normalize_text()`.
- [x] Normalize whitespace consistently. Evidence: `normalize_text()`.
- [~] Handle LaTeX escapes and common title-formatting commands where applicable. Basic slash/braces handling exists; command semantics are not comprehensive.
- [x] Handle braces used for capitalization protection without destroying meaningful text. Evidence: original fields remain unchanged.
- [x] Normalize Unicode consistently. Evidence: `unicodedata.normalize()`.
- [x] Apply case normalization for comparison without losing the original title. Evidence: `casefold()` and exported source fields.
- [x] Preserve punctuation information in the original field. Evidence: candidate rows retain `bib_title` and `ref_title`.
- [ ] Avoid deleting meaningful numbers, acronyms, symbols, or technical terms.
- [ ] Avoid over-aggressive stemming or token removal unless explicitly justified.
- [x] Store original and normalized titles separately, or otherwise preserve the original value. Evidence: candidate rows retain original title fields.
- [~] Add tests for titles with braces, accents, punctuation, math, acronyms, and LaTeX commands. Basic braces/LaTeX coverage exists; the full matrix remains open.
- [x] Confirm the same normalization policy is applied to BibTeX titles and Semantic Scholar titles. Evidence: `generate_candidates()`.

## B3. Author cleaning and normalization

- [x] Locate the author-cleaning implementation. Evidence: `src/matching/m3_candidates.py:normalize_authors`.
- [x] Handle BibTeX author strings and Semantic Scholar author lists correctly. Evidence: list/dict handling in `normalize_authors()`.
- [ ] Parse BibTeX `and` separators without splitting names incorrectly.
- [ ] Handle `Last, First` and `First Last` forms where present.
- [x] Normalize whitespace and Unicode. Evidence: `normalize_authors()` delegates to `normalize_text()`.
- [ ] Preserve hyphenated, compound, accented, and multi-part names.
- [ ] Avoid treating initials as full names.
- [ ] Avoid changing author order.
- [x] Preserve the original author representation. Evidence: parsed BibTeX/reference fields are not mutated.
- [x] Produce a documented normalized representation for comparison. Evidence: `normalize_authors()`.
- [~] Add tests for single authors, multiple authors, corporate authors, initials, accents, and compound surnames. The broad normalization matrix remains open.
- [x] Confirm missing author data does not crash candidate generation. Evidence: default empty values in `generate_candidates()`.

## B4. BibTeX key deduplication and field merging

- [x] Locate the BibTeX deduplication implementation. Evidence: `src/parser/m2_bib_dedup.py`.
- [x] Identify the exact duplicate-key/duplicate-entry detection rule. Evidence: normalized title/author/year identity.
- [x] Confirm duplicate detection is scoped to the correct source paper and bibliography. Evidence: `process_paper()` passes one paper's entries.
- [x] Confirm the canonical key-selection rule is deterministic. Evidence: sorted group order.
- [x] Confirm fields from duplicate entries are merged according to a documented policy. Evidence: first non-empty value wins and conflicts are recorded.
- [x] Confirm non-empty values are not discarded merely because another duplicate entry is encountered first. Evidence: `_merge_value()`.
- [x] Confirm conflicting field values are handled deterministically and logged or flagged. Evidence: `conflicts` output and `m2_stats.json`.
- [x] Confirm the merged entry preserves useful fields such as title, author, year, journal, booktitle, DOI, URL, and arXiv identifier when present. Evidence: field-wise merge.
- [ ] Confirm the original entries remain recoverable or are preserved in raw/intermediate data.
- [x] Confirm all references to merged keys are redirected to the selected canonical key. Evidence: `rewrite_citations()`.
- [x] Confirm `\\cite{key1,key2}` lists are handled correctly. Evidence: citation regex and tests.
- [x] Confirm whitespace around comma-separated citation keys is handled. Evidence: per-key stripping.
- [x] Confirm multiple citation commands in one document are handled. Evidence: line-wide substitution.
- [x] Confirm commented-out LaTeX commands are not rewritten as active citations. Evidence: comment guard and tests.
- [x] Confirm keys that are not duplicates remain unchanged. Evidence: replacement lookup.
- [x] Confirm a citation key is not replaced inside unrelated text or a longer key by substring accident. Evidence: key-list parsing.
- [~] Confirm the rewritten `.tex` or parsed representation remains syntactically valid. Citation syntax is preserved, but no parser validation is present.
- [~] Add tests for duplicate keys, conflicting fields, multi-key citations, comments, and overlapping key names. Core cases are tested; overlapping-name coverage remains open.

**Important:** The project plan explicitly calls for deduplicating keys, merging fields, and changing `\\cite` references. Do not implement key deduplication as merely dropping duplicate rows.

## B5. Candidate generation

- [x] Locate the candidate-generation implementation. Evidence: `src/matching/m3_candidates.py`.
- [x] Define the candidate unit clearly: one BibTeX entry paired with one Semantic Scholar reference. Evidence: one output row per retained pair.
- [x] Confirm candidate generation is performed within the correct citing paper (`arxiv_id`). Evidence: `process_paper()` scopes inputs by paper.
- [x] Confirm candidates are not paired across unrelated citing papers unless the official specification explicitly requires it. Evidence: per-paper orchestration.
- [x] Confirm BibTeX entries and Semantic Scholar references use compatible identifier and normalization conventions. Evidence: shared `normalize_text()`.
- [x] Confirm exact identifier matches are retained as strong candidate signals where available. Evidence: matching does not discard exact-title pairs.
- [x] Confirm title-based candidate retrieval is implemented. Evidence: `title_similarity` threshold.
- [x] Confirm author-based information is available to the candidate-generation/matching stage. Evidence: `author_similarity`.
- [x] Confirm the candidate-generation strategy is deterministic. Evidence: stable nested input order.
- [x] Confirm candidate generation handles empty BibTeX or reference lists. Evidence: empty result behavior.
- [x] Confirm candidate generation handles missing titles and authors. Evidence: default empty values.
- [~] Confirm the number of candidates per BibTeX entry is measured. Aggregate counts exist; a per-entry distribution is not exported.
- [x] Confirm entries with zero candidates are recorded for inspection. Evidence: `zero_candidate_entries` in `m2_stats.json`.
- [x] Confirm candidate rows preserve the source BibTeX key and target reference identity. Evidence: `citation_key`, `ref_s2_id`, and `ref_arxiv_id`.
- [x] Confirm candidate output can be traced back to both source records. Evidence: source paper and citation/reference identifiers.
- [x] Confirm candidate output is saved in a documented format. Evidence: `candidates.json`, schema validation, and README.
- [ ] Confirm a small sample can be manually inspected.

## B6. String-similarity features and automatic matcher

- [x] Locate the string-similarity/matcher implementation. Evidence: `src/matching/m3_candidates.py`.
- [!] Confirm the implementation uses the agreed library/approach (the plan mentions RapidFuzz). Current implementation uses `difflib.SequenceMatcher`; this is an explicit checklist gap.
- [x] Confirm title similarity is computed from normalized titles. Evidence: `similarity()`.
- [x] Confirm author similarity is computed from normalized author data where available. Evidence: `normalize_authors()` and `author_similarity`.
- [ ] Confirm missing values are handled without producing misleading perfect scores.
- [x] Confirm feature values have documented names and meanings. Evidence: candidate schema fields.
- [x] Confirm feature ranges and direction are understood (for example, whether higher means more similar). Evidence: `SequenceMatcher.ratio()` and schema range validation.
- [ ] Confirm exact-match features are distinguished from fuzzy similarity.
- [x] Confirm the automatic matcher does not use manual ground-truth labels as input features. Evidence: candidate generation inputs.
- [x] Confirm thresholds or ranking rules are documented. Evidence: `min_title_similarity` parameter.
- [x] Confirm ties are handled deterministically. Evidence: stable input order.
- [x] Confirm the matcher can return no match when evidence is insufficient, if the design supports that outcome. Evidence: threshold can produce an empty candidate set.
- [x] Confirm predictions preserve candidate identities and source-paper IDs. Evidence: candidate output fields.
- [x] Confirm matcher output is reproducible on the same inputs. Evidence: deterministic implementation.
- [ ] Add unit tests for exact matches, near matches, unrelated titles, missing metadata, and ties.

## B7. Candidate file for M3 manual annotation

- [ ] Locate the candidate export used for annotation.
- [ ] Confirm each row identifies the citing paper.
- [ ] Confirm each row identifies the BibTeX citation key/entry.
- [ ] Confirm each row identifies the candidate target arXiv ID or target reference.
- [ ] Confirm candidate title/author fields are available to annotators where needed.
- [ ] Confirm similarity features/scores are included or accessible for review.
- [ ] Confirm the file format matches the team's agreed annotation contract.
- [ ] Confirm annotation output can record the ground-truth target arXiv ID.
- [ ] Confirm an empty ground-truth value has a documented meaning (for example, no valid target among the candidates / unresolved case); do not assume without team agreement.
- [ ] Confirm candidate rows are stable across reruns or have stable IDs.
- [ ] Confirm candidate exports do not silently omit BibTeX entries with zero candidates.
- [ ] Confirm candidate data is sufficiently documented for other team members to label consistently.
- [ ] Confirm the annotation sample is not accidentally drawn from a held-out evaluation set in a way that leaks labels into training.

## B8. M2 tests and validation

- [ ] Unit test: title normalization on representative BibTeX titles.
- [ ] Unit test: title normalization on Semantic Scholar titles.
- [ ] Unit test: author parsing and normalization.
- [ ] Unit test: missing/null title and author fields.
- [ ] Unit test: duplicate BibTeX entries with complementary fields.
- [ ] Unit test: duplicate BibTeX entries with conflicting fields.
- [ ] Unit test: canonical citation-key selection.
- [ ] Unit test: rewriting one citation key.
- [ ] Unit test: rewriting multiple keys in one `\\cite{...}`.
- [ ] Unit test: comments and unrelated text are not modified.
- [ ] Unit test: candidate generation is scoped to the correct citing paper.
- [ ] Unit test: exact and fuzzy title matches.
- [ ] Unit test: missing metadata and empty candidate sets.
- [ ] Unit test: deterministic candidate ordering.
- [ ] Unit test: stable candidate IDs across reruns.
- [ ] Integration test: one complete paper from P1 BibTeX output through candidate export.
- [ ] Validation: inspect a sample of candidate pairs manually.
- [ ] Validation: quantify entries with at least one candidate and entries with no candidates.
- [ ] Validation: quantify duplicate BibTeX entries before and after deduplication.
- [ ] Validation: verify every rewritten citation key resolves to a retained BibTeX entry.
- [ ] Validation: verify candidate rows retain source-paper and BibTeX provenance.

## B9. M2 handoff and documentation

- [ ] Provide cleaned/normalized reference data or documented normalized fields.
- [ ] Provide deduplicated BibTeX data or the agreed canonical-key mapping.
- [ ] Provide a record of merged BibTeX keys and field conflicts.
- [ ] Provide the citation-key replacement mapping or equivalent audit trail.
- [ ] Provide candidate pairs for M3 annotation.
- [ ] Provide the candidate schema/data dictionary.
- [ ] Provide feature definitions and score direction/range.
- [ ] Provide run instructions and dependencies.
- [ ] Provide tests and reproducible validation commands.
- [ ] Document known limitations, unresolved entries, and zero-candidate cases.
- [ ] Confirm P3 can consume the candidate/feature output for the model pipeline.
- [ ] Confirm P4 can report M2 statistics and include the outputs in the agreed dataset pipeline.

## B10. M2 statistics to provide

- [x] Number of BibTeX entries ingested. Evidence: `bibtex_entries_ingested`.
- [x] Number of Semantic Scholar references ingested. Evidence: `references_ingested`.
- [ ] Number of entries/references with usable titles.
- [ ] Number of entries/references with usable author data.
- [~] Number of duplicate BibTeX entries detected. `merged_keys` is available, but a separate pre-dedup duplicate count is not.
- [x] Number of canonical BibTeX entries after deduplication. Evidence: `canonical_entries`.
- [x] Number of merged keys. Evidence: `merged_keys`.
- [~] Number of citation-key occurrences rewritten. Current statistic counts replacement keys, not every occurrence in source text.
- [x] Number of unresolved/conflicting BibTeX fields. Evidence: `bibtex_conflicts`.
- [x] Number of BibTeX entries considered for candidate generation. Evidence: `canonical_entries`.
- [x] Number of candidate pairs generated. Evidence: `candidate_pairs`.
- [ ] Average, median, minimum, and maximum candidates per BibTeX entry.
- [x] Number and percentage of entries with zero candidates. Evidence: `zero_candidate_entries`; percentage is not currently emitted.
- [ ] Distribution of exact and fuzzy matches, if measured.
- [ ] Runtime for cleaning, deduplication, candidate generation, and matching.
- [ ] Number of failed or skipped records, with reasons.
- [ ] Distribution of exact and fuzzy matches, if measured.
- [ ] Runtime for cleaning, deduplication, candidate generation, and matching.
- [ ] Number of failed or skipped records, with reasons.

---

# Part C — Cross-Milestone Engineering and Quality

## C1. Reproducibility and code quality

- [x] P2 logic is implemented in reusable functions/modules rather than only one-off notebook cells. Evidence: `src/crawler`, `src/parser`, and `src/matching`.
- [x] Inputs and outputs are configurable; paths are not hard-coded to one machine. Evidence: `src/utils/config.py`.
- [x] Functions have clear names, docstrings, and expected input/output types. Evidence: public pipeline functions and type annotations.
- [ ] Dependencies are declared in the project dependency file.
- [ ] Randomness, if any, is seeded and documented.
- [x] Logging is used instead of relying only on print statements. Evidence: M1 crawler logger.
- [x] Errors include enough context to identify the affected paper/reference. Evidence: `ReferenceCrawlError` messages include the arXiv ID.
- [x] The pipeline does not silently swallow exceptions. Evidence: validation and explicit error paths.
- [x] Data-writing operations are safe to rerun. Evidence: atomic output writes and checkpoints.
- [x] Intermediate outputs are separated from final deliverables. Evidence: raw refs, normalized refs, and published CSV.
- [x] Tests can run without live API credentials by using saved fixtures/mocks. Evidence: `pytest src/tests`.
- [x] No secrets, personal tokens, or private local paths are committed. Evidence: environment-based configuration and repository search.
- [x] P2 changes are committed on the assigned branch. Evidence: `checklist/m1-m2-completion`.
- [ ] Changes are submitted through a reviewable pull request.
- [ ] Tests and a short usage note accompany the pull request.

## C2. Data integrity checks

- [ ] Every normalized reference can be traced to a raw Semantic Scholar response.
- [ ] Every candidate can be traced to one BibTeX entry and one reference record.
- [ ] Every candidate retains the citing paper's arXiv ID.
- [ ] The cited target ID is never confused with the citing paper ID.
- [ ] No rows are dropped without a recorded rule/reason.
- [ ] Null handling is consistent across raw, normalized, and exported data.
- [ ] Deduplication rules are documented and testable.
- [ ] Output row counts reconcile with input counts and documented filters.
- [ ] No train/validation/test labels are accidentally used during M1/M2 candidate generation.

---

# Part D — Copilot Audit Report Template

When auditing this file, append or return a report using this structure. Do not modify the checklist's checkboxes automatically unless explicitly asked.

## D1. Executive summary

| Area | Verified | Partial | Not found | Cannot verify | Issues |
|---|---:|---:|---:|---:|---:|
| M1 API client | | | | | |
| M1 resilience | | | | | |
| M1 raw preservation | | | | | |
| M1 extraction/schema | | | | | |
| M1 tests/reporting | | | | | |
| M2 cleaning | | | | | |
| M2 BibTeX dedup/key rewrite | | | | | |
| M2 candidate generation | | | | | |
| M2 matcher/features | | | | | |
| M2 tests/handoff | | | | | |

## D2. Evidence table

| Checklist ID | Status | Evidence (path + function/test/line) | Finding / gap |
|---|---|---|---|
| A1.1 | | | |
| A2.1 | | | |
| A3.1 | | | |
| A4.1 | | | |
| A6.1 | | | |
| A7.1 | | | |
| B2.1 | | | |
| B4.1 | | | |
| B5.1 | | | |
| B6.1 | | | |
| B7.1 | | | |
| B8.1 | | | |

Add rows for every audited item; the examples above are not the full audit scope.

## D3. Bugs and risks

For each finding, report:

- Severity: Critical / High / Medium / Low.
- Affected checklist ID.
- Evidence path and relevant code location.
- Why it is a problem.
- Reproduction steps or test evidence, if available.
- Recommended fix.

## D4. Top five next actions

Rank the five most important actions by dependency and impact. For each action, identify:

- action;
- reason/requirement;
- suggested owner (P2 or another member);
- relevant file/module;
- acceptance test.

## D5. Blockers and unverifiable items

List any checks that require:
- Semantic Scholar API credentials;
- network access;
- the instructor-provided assigned arXiv ID list;
- P1's parser/BibTeX output;
- P3's version/paper metadata;
- P4's final schema or report integration;
- execution on the intended runtime/environment.

---

# Part E — Suggested Copilot Prompts

## Prompt 1: Read-only progress audit

```text
Read `checklist.md` completely and audit the repository for P2's M1 and M2 responsibilities.

Do not modify files, run a full crawl, access external APIs, install packages, or expose secrets.
Inspect source code, tests, notebooks, configuration, sample data, generated outputs, and documentation.

For every checklist item, assign VERIFIED, PARTIAL, NOT FOUND, CANNOT VERIFY, or ISSUE.
Cite concrete repository evidence (path and function/class/test/line where possible).
A TODO, stub, filename, or README statement is not proof of implementation.
Do not assume an item is complete without behavioral or output evidence.

Return the report using Part D of `checklist.md`, including the five highest-priority next actions.
Distinguish M1 raw reference preservation from the filtered published references table.
Distinguish the citing paper ID from the cited reference ID.
```

## Prompt 2: Audit one section

```text
Audit section [A1/A2/A3/A4/A5/A6/A7/A8/A9/B1/B2/B3/B4/B5/B6/B7/B8/B9/B10]
of `checklist.md` against the current repository.

Use read-only inspection. Report every item with status and concrete evidence.
Identify incorrect behavior and missing tests. Do not change code or checklist checkboxes.
```

## Prompt 3: Implement a verified gap

```text
Using the latest audit findings, implement only checklist item(s) [IDs].
First explain the files to be changed and the intended behavior.
Preserve existing project architecture and interfaces.
Add or update focused tests, run only safe relevant tests, and report the results.
Do not alter unrelated modules, raw datasets, credentials, or project schemas without approval.
Do not mark the checklist item complete until the implementation and test evidence support it.
```

---

## Scope and interpretation notes

- This checklist is tailored to **P2**, not the full group's responsibilities.
- M1 P2 scope comes from the group's work-allocation plan: Semantic Scholar reference crawling, data cleaning, and reference-table preparation.
- M2 P2 scope comes from the group's plan: title/name cleaning, BibTeX deduplication and citation-key replacement, candidate generation, and string-similarity matching.
- The checklist adds engineering verification practices so Copilot can audit the implementation. These practices are not all separate grading requirements.
- Where the official statement, the group's agreed schema, or the repository differ, Copilot must report the discrepancy rather than silently choosing one.
- The checklist does not assume that the current repository already contains any implementation.
