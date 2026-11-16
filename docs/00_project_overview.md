# Project Overview

## What this project is

You are building a **citation-matching dataset** from a range of arXiv papers assigned by your instructor. The end product is a public Hugging Face dataset with six Parquet tables and a Jupyter Book report.

The pipeline runs in three milestones:

| Milestone | What you do | Key output |
| ----------- | ------------- | ------------ |
| **M1 — Crawling** | Discover, download, and collect reference metadata | `papers`, `references` tables |
| **M2 — Parsing** | Parse LaTeX into a hierarchy, standardize, deduplicate | `elements`, `hierarchy`, `bibtex` tables |
| **M3 — Matching** | Label, engineer features, train a model, evaluate | `predictions` table |

Each milestone builds on the previous one. M2 reads M1's `.tex`/`.bib` files and `refs.json`. M3 reads M2's `bibtex_merged.json` and `candidates.json` from M2.

---

## Schedule (from team planning PDF)

| Day | Date | Milestone target |
| ----- | ------ | ----------------- |
| 1 | 19/10/2026 | Kick-off, environment setup |
| 6 | 24/10/2026 | **M1 complete** |
| 12 | 30/10/2026 | **M2 complete** |
| 18 | 05/11/2026 | **M3 + poster complete** |
| — | 06/11/2026 | Formal submission |

Work intentionally overlaps: M2 can start parsing papers as soon as M1 produces `.tex` files for the first batch. M3 labeling can start as soon as M2 produces `candidates.json` for a few papers.

---

## Team ownership

| Person | M1 role | M2 role | M3 role |
| -------- | --------- | --------- | --------- |
| **P1** | Source download (`m1_download.py`) | Multi-file gather + hierarchy (`m2_gather.py`, `m2_hierarchy.py`) | — |
| **P2** | Semantic Scholar references (`m1_references.py`) | — | Auto-labeler (`m3_auto_labeler.py`) |
| **P3** | Metadata + version discovery (`run_m1.py`: `read_ids`, `discover`) | Cleanup, sentence splitting, content dedup (`m2_cleanup.py`, `m2_sentences.py`, `m2_content_dedup.py`) | — |
| **P4** | Benchmarking + run script (`utils/benchmark.py`, `crawler/m1_aggregate.py`, `crawler/m1_tables.py`) | BibTeX dedup + export (`m2_bib_dedup.py`, `utils/schemas.py`), schemas | Pipeline orchestration (`run_m3.py`), predictions, evaluation |

Buddy review pairs: **P1 ↔ P3** and **P2 ↔ P4**.

---

## Deliverables

1. **Code repository** — `github.com/Harryyy603002/arxiv-project`, cumulative commits per milestone.
2. **Jupyter Book** — hosted on GitHub Pages, one section per milestone, figures generated at build time.
3. **Hugging Face dataset** — sharded Parquet, six tables (`papers`, `references`, `elements`, `hierarchy`, `bibtex`, `predictions`).
4. **A2 portrait poster** — submitted as `.pptx`, presented at poster session.

---

## Implementation status (as of this writing)

| Component | Status | Note |
| ----------- | -------- | ------ |
| M1 metadata discovery | ✅ Implemented (`run_m1.py`: `read_ids`, `discover`) | `m1_metadata.py` was deleted upstream |
| M1 source download | ✅ Implemented (`m1_download.py`) | `save_source_with_stats` is the only writer |
| M1 reference crawl | ✅ Implemented (`m1_references.py`) | batch API + circuit breaker |
| M1 integrated runner | ✅ Implemented (`run_m1.py`) | multi-session logging in `benchmark/session_NNN.json` |
| M1 `paper.json` / `v{N}/metadata.json` | ✅ Implemented (`run_m1.crawl_batch`) | replaced the old `meta.json`/`versions.json` |
| M1 published tables | ✅ Implemented (`m1_tables.py`, `--skip-tables` to disable) | `papers.parquet` + `references.parquet` + CSVs |
| M1 multi-session aggregation | ✅ Implemented (`m1_aggregate.py`) | `benchmark_m1_total.json` + `sessions.csv` |
| M2 multi-file gather | ✅ Implemented (`m2_gather.py`) | 55 tests, all green |
| M2 macro expansion + cleanup | ✅ Implemented (`m2_cleanup.py`) | `collect_macros`, `expand_user_macros`; **no** `m2_macro_expansion.py` exists |
| M2 hierarchy builder | ✅ Implemented (`m2_hierarchy.py`) | calls `m2_cleanup.expand_user_macros`; 33 tests passing |
| M2 BibTeX parser + dedup | ✅ Implemented (`m2_bibitem.py`, `m2_bib_dedup.py`) | `.bib` **and** inline `\bibitem` |
| M2 content dedup | ✅ Implemented (`m2_content_dedup.py`) | aligned with `run_m2 --steps content` |
| M2 Parquet export | ✅ Implemented (`utils/schemas.py::export_m2_parquet`) | `m2_export_parquet.py` was deleted; use `run_m2 --export` |
| M2 integrated runner | ✅ Implemented (`run_m2.py`) | 3 steps `bib,tree,content` + `--export` all operational |
| M3 candidate generation | ✅ Implemented (`m3_candidates.py`) | tested via `test_m2_parser.py` |
| M3 auto-labeler | ⚠️ Skeleton (`raise NotImplementedError`) | |
| M3 string/context features | ⚠️ Skeleton | |
| M3 model + evaluation | ⚠️ Skeleton | |
| M3 predictions + HF publish | ⚠️ Skeleton | `PREDICTIONS_SCHEMA` updated to `partition` + `cand_1`…`cand_5` |
| M3 pipeline runner | ⚠️ Skeleton (`run_m3.py`) | |
| Test suite | ✅ **164 passed** | all 10 test files execute; see [07 Testing](./07_testing_and_performance.md) §1 |

**Read the Note column.** Both M1 (`run_m1.py`) and M2 (`run_m2.py --steps bib,tree,content --export`) run end-to-end and all 164 unit tests pass. M3 remains skeleton stubs raising `NotImplementedError` aside from `m3_candidates.py` and `m3_pick_papers.py`.

---

## How to read these docs

Start with **[README.md](README.md)** — it has the status vocabulary and the defects-at-a-glance table.

- **[01_system_architecture.md](01_system_architecture.md)** — file structure and data flow diagram
- **[02_data_contracts.md](02_data_contracts.md)** — exact JSON/Parquet schemas for every file
- **[03_m1_crawling.md](03_m1_crawling.md)** — M1 functions, run commands, issues
- **[04_m2_parsing.md](04_m2_parsing.md)** — M2 functions, run commands, issues
- **[05_m3_matching.md](05_m3_matching.md)** — M3 plan and what still needs building
- **[06_integration.md](06_integration.md)** — how milestones hand off data to each other
- **[07_testing_and_performance.md](07_testing_and_performance.md)** — tests, benchmarks, known bugs
- **[08_development_log.md](08_development_log.md)** — decisions and history
