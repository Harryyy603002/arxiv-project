# Testing and Performance

> **Audience.** A student who has to run the test suite, understand what is broken and what is protected after the collaborator's refactoring, and produce the benchmark numbers the M1/M2 reports are graded on.
>
> **Sources.** `src/tests/`, `pytest.ini`, `src/utils/benchmark.py`, `src/run_m1.py`, `src/run_m2.py`, `src/crawler/m1_aggregate.py`, `src/crawler/m1_tables.py`, and the official statement (`hcmus-cs-ta.github.io/lab-book/intro-ds/statement/`).

---

## 1. The Test Suite Status (Empirical Reality)

### 1.1 Running the suite

```powershell
cd C:\Users\truon\Projects\arxiv-project
python -m pytest src/tests -q --no-header
```

If you run that default command right now, **it crashes at collection with 7 errors and exits with code 2 without running most tests.**

To run every test that actually collects despite those errors, pass `--continue-on-collection-errors`:

```powershell
python -m pytest src/tests -q --no-header --continue-on-collection-errors
```

**Empirical result (commit `01b5bb0`):**

```text
4 failed, 77 passed, 7 errors in ~5s
```

- **134 raw `def test_` functions** exist across 10 test files.
- **81 test cases collect** (from only 3 files: `test_m2_gather`, `test_m2_text`, `test_run_m1_downloader`).
- **77 pass.**
- **4 fail at runtime.**
- **7 test files fail at import/collection.**
- **M3 has 0 tests.**

### 1.2 Configuration

`pytest.ini` at the repository root:

```ini
[pytest]
pythonpath = src
```

This single line puts `src/` on `sys.path`. Tests can do `from utils import config as C` and `from parser import m2_gather`. If you run pytest from outside the root, or delete this file, every import breaks. There is no `conftest.py`; fixtures (like `tmp_path` and local `corpus` trees) are per-file or built into pytest.

### 1.3 Per-File Test Inventory and Status

| Test File | `def test_` | Collected | Pass | Fail | Collection Error | Root Cause |
|---|---|---|---|---|---|---|
| `test_m1_metadata.py` | 4 | 0 | 0 | 0 | 🔴 `ModuleNotFoundError: No module named 'm1_metadata'` | Collaborator deleted `src/crawler/m1_metadata.py` (logic moved to `run_m1.py::discover` and `m1_tables.py`) |
| `test_m1_references.py` | 10 | 0 | 0 | 0 | 🔴 `ImportError: cannot import name 'crawl_references' from 'run_m1'` | Function was renamed to `crawl_batch` in `run_m1.py` |
| `test_run_m1_downloader.py` | 4 | 4 | 1 | 3 | 🟢 (collects) | 3 runtime failures: `cached_refs_papers` key missing in test dict; `wait(timeout=1)` race in fake thread test; call to deleted `m1_download.version_dir` |
| `test_m2_gather.py` | 40 | 55 | 55 | 0 | 🟢 (collects) | Fully healthy (55 collected due to `@pytest.mark.parametrize`) |
| `test_m2_hierarchy.py` | 33 | 0 | 0 | 0 | 🔴 `ModuleNotFoundError: No module named 'm2_macro_expansion'` | `m2_hierarchy.py:45` imports a file that was never committed (logic lives in `m2_cleanup.py`) |
| `test_m2_text.py` | 10 | 22 | 21 | 1 | 🟢 (collects) | 1 runtime failure: `test_clean_figure_keeps_structure_drops_placement` asserts `width=\linewidth` but `m2_cleanup.py` strips `\linewidth` as a dimension |
| `test_m2_bibliography.py` | 4 | 0 | 0 | 0 | 🔴 `ModuleNotFoundError: No module named 'm2_macro_expansion'` | Imports `run_m2` which imports `m2_hierarchy` which fails |
| `test_m2_parser.py` | 5 | 0 | 0 | 0 | 🔴 `ImportError: cannot import name 'rewrite_citations' from 'parser.m2_bib_dedup'` | Function was renamed to `rewrite_citations_with_count` |
| `test_m2_pipeline.py` | 7 | 0 | 0 | 0 | 🔴 `ModuleNotFoundError: No module named 'm2_macro_expansion'` | Imports `m2_hierarchy` which fails |
| `test_m2_export_parquet.py` | 17 | 0 | 0 | 0 | 🔴 `ImportError: cannot import name 'm2_export_parquet' from 'parser'` | Collaborator deleted `src/parser/m2_export_parquet.py` (exporter now lives in `src/utils/schemas.py::export_m2_parquet`) |
| **Total** | **134** | **81** | **77** | **4** | **7 files broken** | **Only 3 files currently execute** |

---

## 2. Detailed Root-Cause Breakdown of Failures

### 2.1 The 7 Collection Errors

1. **`src/tests/test_m1_metadata.py`**
   - Line 11: `import m1_metadata as M`
   - **Why it broke:** `src/crawler/m1_metadata.py` was removed in PR #18. Entry ID parsing and discovery are now in `src/run_m1.py::discover` and `read_ids`; table generation is in `src/crawler/m1_tables.py::build_papers`.
   - **Fix:** Update imports to `from run_m1 import parse_entry_id, discover` and `from crawler import m1_tables`.

2. **`src/tests/test_m1_references.py`**
   - Line 12: `from run_m1 import crawl_references`
   - **Why it broke:** `run_m1.py` refactored the crawler into `crawl_batch(group, info, rec, corpus, session, coverage, force_refs=False)`.
   - **Fix:** Update test to call `crawl_batch` or test `crawler.m1_references.fetch_batch_group` directly.

3. **`src/tests/test_m2_hierarchy.py`**
   - Line 18: `import m2_hierarchy as H` → `src/parser/m2_hierarchy.py:45` does `import m2_macro_expansion as ME`
   - **Why it broke:** `m2_macro_expansion.py` was never committed to git. Macro expansion logic was implemented directly inside `src/parser/m2_cleanup.py` (`collect_macros` and `expand_user_macros`).
   - **Fix:** In `src/parser/m2_hierarchy.py:45`, remove `import m2_macro_expansion as ME` and use `from parser.m2_cleanup import expand_user_macros`.

4. **`src/tests/test_m2_bibliography.py`**
   - Line 61: `import run_m2` → imports `parser.m2_hierarchy` → fails on `m2_macro_expansion`.
   - **Fix:** Resolved automatically once `m2_hierarchy.py` import is fixed.

5. **`src/tests/test_m2_pipeline.py`**
   - Line 29: `import m2_hierarchy as H` → fails on `m2_macro_expansion`.
   - **Fix:** Resolved automatically once `m2_hierarchy.py` import is fixed.

6. **`src/tests/test_m2_parser.py`**
   - Line 3: `from parser.m2_bib_dedup import rewrite_citations`
   - **Why it broke:** In `src/parser/m2_bib_dedup.py`, the function was renamed to `rewrite_citations_with_count(text, replacements)` returning `(new_text, count)`.
   - **Fix:** Update test import to `from parser.m2_bib_dedup import rewrite_citations_with_count`.

7. **`src/tests/test_m2_export_parquet.py`**
   - Line 9: `from parser import m2_export_parquet as E`
   - **Why it broke:** `src/parser/m2_export_parquet.py` was deleted in PR #19. Parquet export logic is now in `src/utils/schemas.py` (`export_m2_parquet`, `validate_m2_parquet_integrity`, `to_arrow_table`, `write_parquet`).
   - **Fix:** Update test import to `from utils import schemas as E`.

### 2.2 The 4 Runtime Failures

1. **`test_m2_text.py::test_clean_figure_keeps_structure_drops_placement`**
   - **Failure:** AssertionError: expected `\includegraphics[width=\linewidth]{a.png}`, got `\includegraphics[width= ]{a.png}`.
   - **Cause:** In `m2_cleanup.py`, `_DIM_RE` regex strips LaTeX dimensions (`\linewidth`, `\textwidth`, `\columnwidth`, `0.5\textwidth`). The test expects `\linewidth` to remain inside `\includegraphics` bracket options.

2. **`test_run_m1_downloader.py::test_run_m1_uses_canonical_downloader_and_checkpoints_versions`**
   - **Failure:** `KeyError: 'cached_refs_papers'` at `src/run_m1.py:429`.
   - **Cause:** `run_m1.crawl_batch` expects `coverage` dict to include the key `"cached_refs_papers"`, but the test's mock `coverage` dict only provided 4 keys (`expected_versions`, `crawled_versions`, `failed_versions`, `not_found_papers`).

3. **`test_run_m1_downloader.py::test_crawl_batch_overlaps_reference_and_source_work`**
   - **Failure:** `assert False` at `src/tests/test_run_m1_downloader.py:130` (`source_started.wait(timeout=1)` timed out).
   - **Cause:** Thread timing race condition in the unit test mock under Windows.

4. **`test_run_m1_downloader.py::test_standalone_process_writes_atomic_stats_checkpoint`**
   - **Failure:** `AttributeError: module 'crawler.m1_download' has no attribute 'version_dir'`.
   - **Cause:** In `m1_download.py`, `version_dir` was deleted. `m1_download` now takes an explicit `out_dir: Path` in `save_source_with_stats`. `version_dir` only lives in `utils.config.version_dir`.

---

## 3. Coverage Gaps: What the Suite Does Not Test

| Subsystem / Area | Uncovered Behavior | Consequence / Risk | Recommended Test |
|---|---|---|---|
| **M1 Downloader** (`m1_download.py`) | Backoff / retry on HTTP 429/500/503 | `MAX_RETRIES=4`, `BACKOFF_SECONDS=30` never tested against a failing mock; infinite wait or improper retry could break multi-hour runs | Mock `session.get` returning 503 × 3 then 200, verifying sleep intervals and eventual success |
| **M1 References** (`m1_references.py`) | Circuit breaker cooldown | `BREAKER_THRESHOLD=3`, `COOLDOWN_SECONDS=300` path never executed in tests | Simulate 3 consecutive 429 errors and verify 300s sleep / exception behavior |
| **M1 Table Generation** (`m1_tables.py`) | `build_papers()` and `build_references()` | Table building from corpus files has no unit test | Run `build_papers()` on a mock `data/` and validate Parquet schema against `PAPERS_SCHEMA` |
| **M1 Multi-Session Aggregator** (`m1_aggregate.py`) | `aggregate()` across multiple `session_*.json` | Aggregation logic, weighted average RAM, and `sessions.csv` export are completely untested | Create 2 mock session JSONs and assert `benchmark_m1_total.json` sums wall times and takes max RAM |
| **M2 Integrated Pipeline** (`run_m2.py`) | Execution of `--steps bib,tree,content` end-to-end | `test_m2_bibliography` tests part of bib, but end-to-end runner is blocked by `m2_macro_expansion` import | Test with 1 sample paper once macro import is fixed |
| **M2 Content Deduplication** (`m2_content_dedup.py`) | Cross-version sentence and element deduplication | Tested partially in `test_m2_text.py`, but integration with `elements.json` schema is unverified | Add test verifying `elements.json` contains `node_id`, `type`, `raw_text`, and cross-version mappings |
| **M3 Matching & Candidates** (`matching/`) | All candidate generation and feature extraction | **Zero unit tests in the entire codebase.** `m3_candidates.py` is called once indirectly from `test_m2_parser.py` | Add `src/tests/test_m3_candidates.py` verifying candidate generation, similarity threshold (0.35), and author normalization |
| **Benchmark Utilities** (`benchmark.py`) | `ResourceMonitor` and `PerfRecorder` | Thread safety, RSS tree calculation, archive byte accounting, and summary serialization have zero direct tests | Add tests for `inspect_archive_bytes` on tar/gz/pdf, `record_paper_size`, and `summary()` |

---

## 4. Performance Measurement Architecture

### 4.1 Official Requirements (Lab Statement)

The official HCMUS lab statement specifies:

> The scraper's performance should also be illustrated through two main points, **running time** and **memory footprint**. Running time counts the total time to process one paper, the average time to process each of the required papers, and the total time for entry discovery, while memory footprint counts the **maximum RAM** used during the scraping, the **average RAM** consumption, the **maximum disk storage** required, and the **final output's storage size**. Measure both points in an auditable way, for example peak RAM through `/usr/bin/time -v` or `psutil`, average RAM through periodic sampling, and disk usage through `du`. These tools are examples, not requirements. If the system consists of several programs, the runtime is counted as the wall time taken to run the system in an end-to-end way, while the memory footprint is considered as the total memory consumed by the full system. The testbed for the final benchmarking is a **Google Colab instance running on CPU-only mode**.

Additionally, M1 reporting requires these corpus statistics:
> - Number of papers scraped successfully and overall success rate
> - Average paper size in bytes before and after removing figures
> - Average number of references per paper and average success rate for scraping reference metadata
> - Version coverage (crawled versions vs. expected versions)

### 4.2 Requirement to Metric Mapping

| Lab Statement Requirement | Field in `session_NNN.json` / `benchmark_m1_total.json` | Produced By |
|---|---|---|
| Total time to process one paper | `paper_times_s[arxiv_id]` | `PerfRecorder.paper()` context manager |
| Average time to process each paper | `avg_paper_s` | `PerfRecorder.summary()` |
| Maximum time to process a paper | `max_paper_s` | `PerfRecorder.summary()` |
| Total time for entry discovery | `discovery_s`, `stages_s.discovery` | `rec.stage("discovery")` in `run_m1.main` |
| Total end-to-end wall time | `total_wall_s` | `PerfRecorder.stop()` / `m1_aggregate.py` |
| Maximum RAM consumed | `peak_ram_mb` | `ResourceMonitor.summary()` (process-tree RSS peak) |
| Average RAM consumed | `avg_ram_mb` | `ResourceMonitor.summary()` (periodic sample mean) |
| Maximum disk storage required | `peak_disk_mb` | `ResourceMonitor.summary()` (tracks archive cycles) |
| Final output storage size | `final_output_mb`, `final_output_bytes` | `ResourceMonitor.summary()` / `dir_size(corpus)` |
| Successfully scraped papers & rate | `n_ok`, `n_failed`, `success_rate` | `PerfRecorder.record_result()` |
| Avg size before/after figure removal | `avg_paper_size_before_bytes`, `avg_paper_size_after_bytes` | `PerfRecorder.record_paper_size()` |
| Avg version size before/after | `avg_version_size_before_bytes`, `avg_version_size_after_bytes` | `PerfRecorder.record_paper_size()` |
| PDF-only paper/version counts | `n_pdf_only_versions`, `n_papers_with_pdf_only_version` | `PerfRecorder.record_paper_size(is_pdf_only=True)` |
| Avg references per paper | `avg_refs_per_paper`, `avg_arxiv_refs_per_paper` | `PerfRecorder.record_references()` |
| Reference crawl success rate | `ref_n_ok`, `ref_n_failed`, `ref_success_rate` | `PerfRecorder.record_references()` |
| Version coverage | `coverage.version_coverage` | `session_extra()` in `run_m1.py` |
| Audit environment (Colab verification) | `environment.is_colab`, `platform`, `cpu_count`, `ram_total_gb` | `_environment()` in `run_m1.py` |

### 4.3 `ResourceMonitor` — Two-Thread Sampling Architecture

`src/utils/benchmark.py` implements `ResourceMonitor`, which runs two independent daemon threads:

```
                          ┌──────────────────────────────────────────┐
                          │         ResourceMonitor (Daemon)         │
                          └────────────────────┬─────────────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       ▼                                               ▼
         ┌──────────────────────────┐                    ┌──────────────────────────┐
         │   RAM Thread (_ram_loop) │                    │ Disk Thread (_disk_loop) │
         │   interval = 0.5s        │                    │ disk_interval = 5.0s     │
         └─────────────┬────────────┘                    └─────────────┬────────────┘
                       │                                               │
           Calls _tree_rss():                                Scans watch_dirs via
           sum(p.memory_info().rss                           os.walk() (dir_size)
           for p in [proc] + children)                                 │
                       │                                               ▼
                       ▼                                    Baseline scan > 10.0s?
           Appends to rss_samples                           ├── Yes: Slow storage (Drive)
           under thread lock                                │        disk_estimated = True
                                                            └── No:  Fast storage (/content)
                                                                     Periodic re-scan
```

- **Process-Tree RSS:** Unlike a naive single-process check, `_tree_rss()` queries `[self._proc] + self._proc.children(recursive=True)` using `psutil`. This accounts for `ThreadPoolExecutor` worker threads and child processes, satisfying the statement's mandate: *"the memory footprint is considered as the total memory consumed by the full system."*
- **Colab Drive Protection (`max_scan_s = 10.0`):** Scanning thousands of files over Google Drive via FUSE can take 30–60 seconds per pass. If the initial baseline scan exceeds 10s, the disk thread terminates periodic scanning and falls back to mathematical estimation:
  $$\text{Disk Peak} = \text{Baseline} + \text{Kept Bytes} + \text{Transient Archive Bytes}$$
  When active, `disk_estimated: true` is emitted in the output JSON. **Students should run on `/content` (local SSD) rather than `/content/drive` to obtain unestimated disk measurements.**
- **Transient Peak Accounting (`record_archive_cycle`):** When downloading a 40 MB `.tar.gz` and extracting 2 MB of `.tex`, the high-water disk usage occurs while both the archive and the extracted files temporarily coexist on disk. `record_archive_cycle()` tracks this transient spike so `peak_disk_mb` reflects the true maximum footprint, not just the post-cleanup settled state.

### 4.4 `PerfRecorder` — Stage and Paper Timing

`PerfRecorder` wraps execution phases and paper processing:

- `with rec.stage("discovery"):` — Accumulates wall time for named pipeline stages (`discovery`, `crawl`, `backup`, `tables`, `bib`, `tree`, `content`, `export`).
- `with rec.paper(aid):` — Context manager measuring end-to-end processing duration for a paper across all its versions.
- `rec.add_paper_time(aid, seconds):` — Adds distributed background work (e.g., Semantic Scholar batch query time divided by batch size) to the paper's total.
- `rec.record_paper_size(aid, before, after, archive_bytes, is_pdf_only):` — Records per-version byte counters. If `is_pdf_only=True`, increments PDF counters without corrupting TeX size averages.
- `rec.checkpoint(session_path, extra):` — Atomically serializes progress every 60 seconds so an interrupted Colab run retains auditable timing records.
- `rec.save(session_path, extra):` — Finalizes and writes `session_NNN.json` or `m2_benchmark.json`.

---

## 5. Multi-Session Benchmark System (M1)

### 5.1 Why Multi-Session is Necessary

Scraping 40,000 papers with arXiv's mandatory $\ge 3.0$-second delay takes at least 33 hours. Google Colab free-tier runtimes disconnect after 12 hours (and frequently after 4–6 hours of idle or continuous execution). 

To satisfy the grading requirement on Colab, `run_m1.py` and `m1_aggregate.py` implement an auditable **multi-session logging protocol**:

1. Each invocation of `run_m1.py` detects existing `session_*.json` files in `<corpus>/benchmark/` and creates the next session log (e.g., `session_001.json`, `session_002.json`).
2. Every 60 seconds (and upon graceful exit or `Ctrl+C` / `BaseException`), `run_m1.py` updates the session log with `status="running"` or `status="interrupted"`.
3. When the full list completes, the final session writes `status="completed"`.
4. `run_m1.py` automatically invokes `m1_aggregate.aggregate()`, producing:
   - `<corpus>/benchmark/benchmark_m1_total.json` — Consolidated multi-session report.
   - `<corpus>/benchmark/sessions.csv` — Per-session table suitable for pasting into the lab report.

### 5.2 Multi-Session Consolidation Rules

`m1_aggregate.py` aggregates across sessions according to strict mathematical rules:

| Metric Category | Aggregation Rule | Rationale |
|---|---|---|
| **Wall Time (`total_wall_s`)** | $\sum \text{session.total\_wall\_s}$ | Excludes idle time between Colab disconnects; sums active scraping time |
| **Stage Times (`stages_s`)** | $\sum \text{session.stages\_s}[stage]$ | Sums active discovery, crawl, backup, and table generation across sessions |
| **Paper Times (`paper_times_s`)** | $\sum_{\text{sessions}} \text{paper\_times\_s}[aid]$ | Accurately handles papers whose versions were split across session boundaries |
| **Peak RAM (`peak_ram_mb`)** | $\max(\text{session.peak\_ram\_mb})$ | True global maximum process-tree memory across all sessions |
| **Average RAM (`avg_ram_mb`)** | $\frac{\sum (\text{avg\_ram} \times \text{samples})}{\sum \text{samples}}$ | Exact weighted average across all RAM samples taken in all sessions |
| **Peak Disk (`peak_disk_mb`)** | $\max(\text{session.peak\_disk\_mb})$ | True maximum disk footprint recorded |
| **Final Output (`final_output_bytes`)** | `dir_size(corpus)` | Measured on disk from the actual final state of the corpus directory |
| **Corpus Outcomes (`n_ok`, `coverage`...)** | Taken from the **last completed session** | The final session scans all checkpoints and contains the authoritative corpus state |

---

## 6. How to Run Benchmarks and Reproduce Numbers

### 6.1 Running M1 Benchmark

**Standard Colab execution:**

```powershell
python src/run_m1.py `
  --ids-file ids.txt `
  --corpus-dir data/corpus `
  --bench-dir data/corpus/benchmark `
  --batch-size 25
```

If the session disconnects, simply re-run the exact same command. `run_m1.py` detects existing `paper.json` and `v{N}/metadata.json` checkpoints, resumes seamlessly, and writes `session_002.json`.

**Manual aggregation (if needed):**

```powershell
python src/crawler/m1_aggregate.py --data-dir data/corpus
```

### 6.2 Running M2 Benchmark

**Integrated M2 pipeline benchmark:**

```powershell
python src/run_m2.py `
  --corpus-dir data/corpus `
  --steps bib,tree,content `
  --export
```

Output is written to `<corpus>/benchmark/m2_benchmark.json` and `<corpus>/benchmark/unknown_commands.json`.

**External wrapper benchmark (alternative via `benchmark.py` CLI):**

```powershell
python src/utils/benchmark.py --milestone m2 --watch data/corpus -- python src/run_m2.py --corpus-dir data/corpus
```

---

## 7. Reading Guide for Students

- **To run unit tests:** Use `python -m pytest src/tests -q --no-header --continue-on-collection-errors` until imports are fixed.
- **To see why 7 test files fail:** Read §2.1.
- **To understand what the lab report requires:** Read §4.1 and §4.2.
- **To understand RAM and Disk measurement:** Read §4.3.
- **To explain Colab multi-session numbers in your report:** Read §5.1 and §5.2.
- **To understand before/after paper size definitions:** See [04_m2_parsing.md](./04_m2_parsing.md) and `inspect_archive_bytes()` in `benchmark.py`.

---

## 8. Team Review & Sign-Off

- **Authors:** P4 (benchmark and schemas lead) & P1 (crawler and test lead)
- **Review Status:** Re-verified against repository commit `01b5bb0`.
- **Action Items for Team:**
  1. Fix `src/parser/m2_hierarchy.py:45` by replacing `import m2_macro_expansion as ME` with `from parser.m2_cleanup import expand_user_macros`.
  2. Fix `src/tests/test_m1_metadata.py` imports (`m1_metadata` → `run_m1` / `m1_tables`).
  3. Fix `src/tests/test_m1_references.py` (`crawl_references` → `crawl_batch`).
  4. Fix `src/tests/test_m2_export_parquet.py` (`parser.m2_export_parquet` → `utils.schemas`).
  5. Fix `src/tests/test_m2_parser.py` (`rewrite_citations` → `rewrite_citations_with_count`).
  6. Add unit tests for `src/matching/m3_candidates.py` and `src/utils/benchmark.py`.

