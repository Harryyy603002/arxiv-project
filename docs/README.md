# arXiv Project — Technical Documentation

Working documentation for the HCMUS *Introduction to Data Science* arXiv project: crawl → parse → match.

These docs are written **for a student reading the code for the first time**. Every claim is grounded in either the official statement or a file in `src/`. Where the code and the statement disagree, the disagreement is called out explicitly rather than smoothed over.

---

## Start here

If you have ten minutes:

1. [00 Project overview](./00_project_overview.md) — what the project is and who owns what.
2. [01 System architecture](./01_system_architecture.md) — the repo tree and the data-flow diagram.
3. Then jump to whichever milestone you are working on.

If you are about to run something, read the "run order" section of [06 Integration](./06_integration.md) first (always invoke scripts from the repository root, e.g. `python src/run_m1.py` and `python src/run_m2.py`).

---

## The nine documents

| Doc | What it answers |
| --- | --- |
| [00 Project overview](./00_project_overview.md) | Objectives, official requirements, 18-day schedule, P1–P4 ownership matrix, implementation status |
| [01 System architecture](./01_system_architecture.md) | Repository layout, module responsibilities, Mermaid data-flow diagram, design principles, external APIs |
| [02 Data contracts](./02_data_contracts.md) | Every intermediate JSON payload with a real example, plus all six final Parquet schemas |
| [03 M1 crawling](./03_m1_crawling.md) | Metadata discovery, versioned source download, Semantic Scholar references, config values, error handling |
| [04 M2 parsing](./04_m2_parsing.md) | Root detection, include expansion, hierarchy tree, text cleaning, sentence splitting, bibtex, dedup, Parquet export |
| [05 M3 matching](./05_m3_matching.md) | What exists (candidate generation) and what still needs building (labelling, features, model, MRR@5) |
| [06 Integration](./06_integration.md) | How milestones hand off to each other, end-to-end run order, and the integration defects |
| [07 Testing and performance](./07_testing_and_performance.md) | The 164-test suite, the benchmark API, and how to produce the M1 report's required numbers |
| [08 Development log](./08_development_log.md) | Commit timeline, design rationale, known defects, debugging notes, open decisions |

Planning documents (external PDFs not stored in the repository):

- `Tổng-quan-Đồ-án-arXiv.pdf` (Project overview)
- `Ke_hoach_phan_chia_cong_viec_do_an_arXiv.pdf` (Work-allocation plan)

---

## Official statement

The source of truth for all requirements. These docs cite it throughout.

- [Project statement](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/)
- [Milestone 1 — Crawling](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling/)
- [Milestone 2 — Parsing](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing/)
- [Milestone 3 — Matching](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/matching/)

---

## Status vocabulary

Used consistently across all nine documents:

- **Implemented** — the code exists and a test or direct source evidence supports it.
- **Partial** — some logic exists but the stage is unfinished.
- **Skeleton** — the entry point raises `NotImplementedError`.
- **Missing** — nothing exists.
- **Unverified** — the repository cannot prove the claim.

A feature being *implemented* does not mean the full milestone is wired up. `m3_candidates` is implemented, while `run_m3.py` still raises `NotImplementedError`. Read [05 M3 matching](./05_m3_matching.md) and [06 Integration](./06_integration.md) before assuming a module runs.

---

## Known defects at a glance

Full detail in [06 Integration](./06_integration.md) and [08 Development log](./08_development_log.md) §4.

| # | Defect | Severity | Impact |
| --- | --- | --- | --- |
| 1 | Redundant `v{N}/references.json` duplicated per version | 🟡 Medium | Disk space waste across thousands of versions |
| 2 | `unknown_commands` counter in `m2_cleanup.py` never reset | 🟡 Low | Cumulative counts across multiple runs in same process |
| 3 | M3 pipeline modules (`run_m3.py`, `m3_model.py`, …) are stubs | 🟡 Medium | M3 raises `NotImplementedError` until implemented |

---

## Verify your environment

```powershell
python -m pytest src/tests -q
```

Current empirical baseline: `164 passed` across all 10 test files in `src/tests/`.

Dependencies: `pip install -r requirements.txt` (kept as the single requirements file at repository root).

---

## Conventions

- Paths in these docs are relative to the repository root unless absolute.
- `data/` is gitignored; nothing under it is committed.
- `paper_key` (e.g. `2310-12345`) is for **folder names only**. Inside JSON, always store the raw arXiv ID (`2310.12345`).
- Version folders are `v1`, `v2`, … as strings, but `hierarchy.json` stores `version` as an integer. `schemas.to_arrow_table` handles both via `_parse_version`.
