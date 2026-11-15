# Development Log

> **Audience.** A student joining the project mid-way, or an AI picking up where the last session stopped. This file answers "why is the code like this?", "what changed in recent refactoring?", and "what is still broken?".
>
> **Rule.** Every claim here is tied to a commit, a file, or a test. If you cannot point at evidence, it goes in §6 (open decisions), not §3 (design decisions).

---

## 1. Timeline and Major Milestones

Milestone work has proceeded through several distinct phases:

| Date | Phase | What Landed | Key Commits / PRs |
|---|---|---|---|
| **19/10** | Skeleton | Repo layout, `.gitignore`, initial `config.py` path helpers | `d852db5`, `438a4c1` |
| **20–23/10** | M1 v1 & M2 v1 | Metadata crawler, versioned downloader, Semantic Scholar crawler, first `pytest.ini` | PR #1, PR #3, PR #4, PR #5 |
| **24–28/10** | M2 Core | `m2_gather` root detection and include expansion with 55 tests (P1) | `af57163`, `36b184c` |
| **29/10–04/11**| M1 Hardening | Non-blocking Drive scan, 429 backoff, batch reference crawl, thread overlap | PR #9, `865abf9`, `cd07e42` |
| **05/11** | Consolidation | Raw reference provenance, published CSV export, BibTeX merge, hierarchy tree | `97c5ebc`, `5cac703`, PR #13 |
| **08–10/11**| M2 Parquet & Merge | Parquet export schemas, text cleaning merge, docs baseline | PR #15, PR #16, `cf5750d` |
| **01b5bb0** | Collaborator Rework (PR #18 & #19) | Complete restructuring of M1 and M2 pipelines, Colab multi-session logging, 3-step M2 runner (`bib,tree,content`), two-layer LaTeX cleaning | `0498368` (PR #18: `m1_aggregate`, `m1_tables`), `01b5bb0` (PR #19: `run_m2`, `m2_inspect`, `README_M2`, `CHANGELOG_M1_M2`) |
| **Current** | Stabilization & Docs Sync | Fixed `m2_hierarchy` import (`m2_cleanup.expand_user_macros`), modernized all 10 test files (164/164 pass), consolidated root `requirements.txt`, updated `PREDICTIONS_SCHEMA` to 9 columns (`partition` + `cand_1`..`cand_5`), synced `README*` and `docs/00`–`08` | Local working tree |

---

## 2. Milestone Status (Current Reality)

| Milestone | Code State | Test Suite Status | What Still Needs Work |
|---|---|---|---|
| **M1** | Functionally complete with multi-session Colab logging, parallel reference crawling, and Parquet table generation (`m1_tables.py`). | 20/20 pass (`test_m1_metadata`, `test_m1_references`, `test_run_m1_downloader`). | Full-corpus Colab crawl & report benchmark aggregation. |
| **M2** | Three-step pipeline (`bib`, `tree`, `content`) + Parquet export implemented and operational in `run_m2.py`. Rich two-layer LaTeX cleaning in `m2_cleanup.py`. | 144/144 pass across all 7 M2 test files (incl. `test_m2_parser` covering `m3_candidates`). | Reset `unknown_commands` per `run_m2.run()` call; full-corpus run & benchmark. |
| **M3** | Candidate generation (`m3_candidates.py`), paper picker (`m3_pick_papers.py`), and 9-column `PREDICTIONS_SCHEMA` (`schemas.py`) ready. Remaining 7 modules are skeleton stubs raising `NotImplementedError`. | Candidate generation + `PREDICTIONS_SCHEMA` covered in `test_m2_parser.py` & `test_m2_export_parquet.py`. | Labeling, feature extraction, ranking models, `m3_predictions`, `m3_publish_hf`, MRR@5 evaluation. |

---

## 3. Key Design Decisions

### 3.1 Filesystem as the Integration Layer
There is no database or inter-process broker. Every milestone writes agreed JSON files to `<corpus>/<paper_key>/...` and downstream milestones read them. `src/utils/config.py` provides canonical path helpers (`paper_dir`, `version_dir`, `paper_path`, `refs_path`, `elements_path`, `hierarchy_path`, `cite_map_path`, `parse_status_path`).

### 3.2 Atomic File Writes (`.tmp` → Replace)
Because crawling and parsing 40,000 papers takes tens of hours on unstable environments (Google Colab), writers always write to a temporary `.tmp` file and rename atomically via `Path.replace()`. Interrupted writes never leave truncated or corrupted JSON.

### 3.3 Colab Multi-Session Logging Protocol (M1)
Google Colab limits free executions to 4–12 hours. `run_m1.py` logs each execution as `benchmark/session_001.json`, `session_002.json`, checkpointing every 60 seconds. `src/crawler/m1_aggregate.py` aggregates these sessions into a consolidated `benchmark_m1_total.json` and `sessions.csv`, summing active wall clock time, computing sample-weighted average RAM, and taking global peak RAM and disk.

### 3.4 Rate Limits as Strict Physics
- **arXiv (`m1_download.py`):** Uses `export.arxiv.org/src` exclusively. Mandatory $\ge 3.0$-second delay between requests (`MIN_DELAY_SECONDS = 3.0`). 4 retries with exponential backoff (`30 * attempt`).
- **Semantic Scholar (`m1_references.py`):** Batch queries (`/paper/batch`) with 25 papers per request. Circuit breaker trips after 3 consecutive failures (`BREAKER_THRESHOLD = 3`) and sleeps 300 seconds (`COOLDOWN_SECONDS = 300`).

### 3.5 Bounded Parallelism (1 Background Reference Worker)
In `run_m1.py`, arXiv downloading is strictly serial to comply with rate limits. Concurrently, a background thread fetches references for the batch from Semantic Scholar using a separate `requests.Session()`. Reference network latency is hidden behind arXiv source download delays without exceeding rate limits on either service.

### 3.6 Two-Layer LaTeX Architecture (M2)
- **Layer 1 (Macro Expansion):** User-defined macros (`\newcommand`, `\def`) are discovered via `collect_macros()` and expanded across documents via `expand_user_macros()` in `m2_cleanup.py`.
- **Layer 2 (Syntax Cleaning & Classification):** Regex and state-machine tables categorize commands into `DROP_NO_ARGS`, `DROP_WITH_ARGS`, `UNWRAP_COMMANDS`, and `SPACE_COMMANDS`. Unknown commands are parsed and classified dynamically (`no_arg`, `dimension`, `text`) and tallied in `unknown_commands.json`.

---

## 4. Known Bugs and Deficiencies

| # | Subsystem | Issue / Bug | Impact | Fix Location |
|---|---|---|---|---|
| 1 | **M1 Corpus Redundancy** | `v{N}/references.json` duplicated per version | Wastes disk space across thousands of versions | Clarified in [02_data_contracts.md](./02_data_contracts.md); canonical location is `<paper>/refs.json` |
| 2 | **M2 Counter Reset** | `unknown_commands` counter never cleared between runs | Counts accumulate if multiple `run_m2.run()` calls execute in the same process | Call `reset_unknown_commands()` at the start of `run_m2.run()` |
| 3 | **M2 Duplicate Functions** | 4 bracket/argument helpers defined twice in `m2_cleanup.py` | Code bloat and maintenance risk | Deduplicate helper definitions in `src/parser/m2_cleanup.py` |

---

## 5. Team Decisions and Open Questions

1. **Test Suite Modernization (Done):** All 10 test files now align with the current M1/M2 codebases and `PREDICTIONS_SCHEMA`, with `python -m pytest src/tests -q` passing 164/164 tests.
2. **Macro Expansion Placement (Done):** Kept inside `m2_cleanup.py` (`collect_macros`, `expand_user_macros`), imported by `m2_hierarchy.py:45`, and documented consistently across `README.md`, `README_M2.md`, `docs/CHANGELOG_M1_M2.md`, and `docs/00`–`08`.
3. **M3 Implementation Strategy:** With M1, M2, and `PREDICTIONS_SCHEMA` stabilized, M3 labeling, feature extraction, and ranking model development can build directly on `elements.parquet`, `hierarchy.parquet`, `bibtex.parquet`, `references.parquet`, and `candidates.json`.
4. **Jupyter Book Report Integration:** Performance numbers from `benchmark_m1_total.json` and `m2_benchmark.json` must be recorded in the final report.

---

## 6. Review & Verification

- **Empirical Test State:** `164 passed` (`python -m pytest src/tests -q`), plus verified end-to-end runs of `run_m1.py`, `run_m2.py --export`, `schemas.py`, `m1_tables.py`, and `m1_aggregate.py`.
- **Documentation Suite:** Synced across `README.md`, `README_M2.md`, `docs/CHANGELOG_M1_M2.md`, `docs/README.md`, and `docs/00` through `docs/08`.
