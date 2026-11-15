# Development Log

> **Audience.** A student joining the project mid-way, or an AI picking up where the last session stopped. This file answers "why is the code like this?", "what changed in recent refactoring?", and "what is still broken?".
>
> **Rule.** Every claim here is tied to a commit, a file, or a test. If you cannot point at evidence, it goes in §6 (open decisions), not §3 (design decisions).

---

## 1. Timeline and Major Milestones

`main` is currently at commit `01b5bb0` (86 commits). Milestone work has proceeded through several distinct phases:

| Date | Phase | What Landed | Key Commits / PRs |
|---|---|---|---|
| **19/10** | Skeleton | Repo layout, `.gitignore`, initial `config.py` path helpers | `d852db5`, `438a4c1` |
| **20–23/10** | M1 v1 & M2 v1 | Metadata crawler, versioned downloader, Semantic Scholar crawler, first `pytest.ini` | PR #1, PR #3, PR #4, PR #5 |
| **24–28/10** | M2 Core | `m2_gather` root detection and include expansion with 55 tests (P1) | `af57163`, `36b184c` |
| **29/10–04/11**| M1 Hardening | Non-blocking Drive scan, 429 backoff, batch reference crawl, thread overlap | PR #9, `865abf9`, `cd07e42` |
| **05/11** | Consolidation | Raw reference provenance, published CSV export, BibTeX merge, hierarchy tree | `97c5ebc`, `5cac703`, PR #13 |
| **08–10/11**| M2 Parquet & Merge | Parquet export schemas, text cleaning merge, docs baseline | PR #15, PR #16, `cf5750d` |
| **Current** | Collaborator Rework (PR #18 & #19) | Complete restructuring of M1 and M2 pipelines, Colab multi-session logging, 3-step M2 runner (`bib,tree,content`), two-layer LaTeX cleaning | `0498368` (PR #18: `m1_aggregate`, `m1_tables`), `01b5bb0` (PR #19: `run_m2`, `m2_inspect`, `README_M2`, `CHANGELOG_M1_M2`) |

---

## 2. Milestone Status (Reality as of Commit `01b5bb0`)

| Milestone | Code State | Test Suite Status | What Still Needs Fixing |
|---|---|---|---|
| **M1** | Functionally complete with multi-session Colab logging, parallel reference crawling, and Parquet table generation (`m1_tables.py`). | 1 pass, 3 fail (`test_run_m1_downloader.py`). `test_m1_metadata` and `test_m1_references` fail at import. | Update test imports to match refactored functions; fix mock coverage dictionary in downloader test. |
| **M2** | Three-step pipeline (`bib`, `tree`, `content`) implemented in `run_m2.py`. Rich text cleaning in `m2_cleanup.py`. Parquet export integrated into `utils/schemas.py`. | 76 pass, 1 fail (`test_m2_text.py`). 5 test files fail at import due to missing `m2_macro_expansion` and renamed functions. | Fix `m2_hierarchy.py:45` import (`m2_macro_expansion` → `m2_cleanup.expand_user_macros`); update test imports. |
| **M3** | Candidate generation implemented in `src/matching/m3_candidates.py`. Remaining 7 modules are skeleton stubs raising `NotImplementedError`. | **0 tests**. | Labeling, feature extraction, ranking models, and MRR@5 evaluation. |

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
| 1 | **M2 Hierarchy** | `m2_hierarchy.py:45` imports non-existent `m2_macro_expansion` | Breaks `run_m2.py --steps tree` and crashes 3 test files during collection | In `src/parser/m2_hierarchy.py`, replace `import m2_macro_expansion as ME` with `from parser.m2_cleanup import expand_user_macros` |
| 2 | **Test Suite** | 7 test files fail during pytest collection | Blocks standard `pytest` run | Update imports in `test_m1_metadata.py`, `test_m1_references.py`, `test_m2_export_parquet.py`, `test_m2_parser.py`, and fix `m2_macro_expansion` |
| 3 | **M1 Coverage Mock** | `test_run_m1_downloader.py` misses `cached_refs_papers` key | Causes `KeyError` in test | Add `'cached_refs_papers': 0` to the test fixture dictionary |
| 4 | **Figure Cleaning Test** | `test_clean_figure_keeps_structure_drops_placement` fails | `m2_cleanup.py` strips `\linewidth` as a dimension | Adjust regex in `m2_cleanup.py` to preserve dimension tokens inside `\includegraphics` bracket options |
| 5 | **M1 Corpus Redundancy** | `v{N}/references.json` duplicated per version | Wastes disk space across thousands of versions | Clarified in [02_data_contracts.md](./02_data_contracts.md); canonical location is `<paper>/refs.json` |
| 6 | **M2 Counter Reset** | `unknown_commands` counter never cleared between runs | Counts accumulate if multiple `run_m2.run()` calls execute in the same process | Call `reset_unknown_commands()` at the start of `run_m2.run()` |
| 7 | **M2 Duplicate Functions** | 4 bracket/argument helpers defined twice in `m2_cleanup.py` | Code bloat and maintenance risk | Deduplicate helper definitions in `src/parser/m2_cleanup.py` |
| 8 | **M3 Predictions Schema** | `PREDICTIONS_SCHEMA` defines `predicted_arxiv_ids` as a list | Lab statement requires 5 separate columns: `cand_1`, `cand_2`, `cand_3`, `cand_4`, `cand_5` | Rewrite `PREDICTIONS_SCHEMA` in `src/utils/schemas.py` |

---

## 5. Team Decisions and Open Questions

1. **Test Suite Modernization:** The test suite needs a surgical update to align test assertions and imports with the new M1/M2 codebases so `python -m pytest` passes 100% cleanly.
2. **Macro Expansion Placement:** The changelog referred to `m2_macro_expansion.py`, but the functions were implemented in `m2_cleanup.py`. The team should decide whether to keep them in `m2_cleanup.py` or split them into a dedicated module.
3. **M3 Implementation Strategy:** With M1 and M2 pipelines complete and stabilized, M3 feature extraction and ranking model development can begin using `elements.parquet`, `hierarchy.parquet`, `bibtex.parquet`, and `references.parquet`.
4. **Jupyter Book Report Integration:** Performance numbers from `benchmark_m1_total.json` and `m2_benchmark.json` must be committed as tabular markdown into the report documentation.

---

## 6. Review & Verification

- **Current Repository HEAD:** `01b5bb0` (Merge PR #19)
- **Empirical Test State:** `4 failed, 77 passed, 7 errors` (with `--continue-on-collection-errors`)
- **Documentation Suite:** Fully refreshed and verified against the actual source code across all documents `00` through `08` and `README.md`.
