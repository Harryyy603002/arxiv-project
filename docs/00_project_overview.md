# Project Overview

## What this project is

You are building a **citation-matching dataset** from a range of arXiv papers assigned by your instructor. The end product is a public Hugging Face dataset with six Parquet tables and a Jupyter Book report.

The pipeline runs in three milestones:

| Milestone | What you do | Key output |
|-----------|-------------|------------|
| **M1 — Crawling** | Discover, download, and collect reference metadata | `papers`, `references` tables |
| **M2 — Parsing** | Parse LaTeX into a hierarchy, standardize, deduplicate | `elements`, `hierarchy`, `bibtex` tables |
| **M3 — Matching** | Label, engineer features, train a model, evaluate | `predictions` table |

Each milestone builds on the previous one. M2 reads M1's `.tex`/`.bib` files and `refs.json`. M3 reads M2's `bibtex_merged.json` and `candidates.json` from M2.

---

## Schedule (from team planning PDF)

| Day | Date | Milestone target |
|-----|------|-----------------|
| 1 | 19/10/2026 | Kick-off, environment setup |
| 6 | 24/10/2026 | **M1 complete** |
| 12 | 30/10/2026 | **M2 complete** |
| 18 | 05/11/2026 | **M3 + poster complete** |
| — | 06/11/2026 | Formal submission |

Work intentionally overlaps: M2 can start parsing papers as soon as M1 produces `.tex` files for the first batch. M3 labeling can start as soon as M2 produces `candidates.json` for a few papers.

---

## Team ownership

| Person | M1 role | M2 role | M3 role |
|--------|---------|---------|---------|
| **P1** | Source download (`m1_download.py`) | Multi-file gather + hierarchy (`m2_gather.py`, `m2_hierarchy.py`) | — |
| **P2** | Semantic Scholar references (`m1_references.py`) | — | Auto-labeler (`m3_auto_labeler.py`) |
| **P3** | Metadata + version discovery (`m1_metadata.py`) | Cleanup, sentence splitting, content dedup (`m2_cleanup.py`, `m2_sentences.py`, `m2_content_dedup.py`) | — |
| **P4** | Benchmarking skeleton + run script | BibTeX dedup + export (`m2_bib_dedup.py`, `m2_export_parquet.py`), schemas | Pipeline orchestration (`run_m3.py`), predictions, evaluation |

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
|-----------|--------|------|
| M1 metadata discovery | ✅ Implemented (`m1_metadata.py`) | |
| M1 source download | ✅ Implemented (`m1_download.py`) | |
| M1 reference crawl | ✅ Implemented (`m1_references.py`) | |
| M1 integrated runner | ✅ Implemented (`run_m1.py`) | |
| M1 `papers.parquet` | ⚠️ Written, wrong schema | bypasses `PAPERS_SCHEMA` |
| M1 `references.parquet` | ❌ Never written | CSV only |
| M2 multi-file gather | ✅ Implemented (`m2_gather.py`) | |
| M2 hierarchy builder | ✅ Implemented (`m2_hierarchy.py`) | **not called by `run_m2`** |
| M2 LaTeX cleanup | ✅ Implemented (`m2_cleanup.py`, `m2_sentences.py`) | |
| M2 BibTeX parser + dedup | ⚠️ Partial (`m2_bibitem.py`, `m2_bib_dedup.py`) | `.bib` files only, no inline `\bibitem` |
| M2 content dedup | ✅ Implemented (`m2_content_dedup.py`) | **not called by `run_m2`** |
| M2 Parquet export | ✅ Implemented (`m2_export_parquet.py`) | writes 3 tables from existing JSON |
| M2 integrated runner | ⚠️ Partial (`run_m2.py`) | see [06 Integration](./06_integration.md) §4.3 |
| M3 candidate generation | ✅ Implemented (`m3_candidates.py`) | |
| M3 auto-labeler | ⚠️ Skeleton (`raise NotImplementedError`) | |
| M3 string/context features | ⚠️ Skeleton | |
| M3 model + evaluation | ⚠️ Skeleton | |
| M3 predictions + HF publish | ⚠️ Skeleton | |
| M3 pipeline runner | ⚠️ Skeleton (`run_m3.py`) | |

**Read the Notes column.** A module being implemented does not mean the pipeline calls it. `run_m2.py`'s only downstream-consumable outputs are `bibtex_merged.json` and `candidates.json`; `elements.json` and `hierarchy.json` are never produced by it, so they require running those modules directly until §4.3 of the integration doc is fixed.

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
