# Testing and Performance

> **Audience.** A student who has to run the test suite, understand what it protects, and then produce the performance numbers the M1 report is graded on.
>
> **Sources.** `src/tests/`, `pytest.ini`, `src/utils/benchmark.py`, `src/run_m1.py`, and the official M1 statement (`hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling`).

---

## 1. The Test Suite

### 1.1 How to run it

```powershell
cd C:\Users\truon\Projects\arxiv-project
python -m pytest src/tests -q
```

Expected output:

```text
150 passed in ~15s
```

**150 tests.** The suite is entirely offline — no network calls, no API keys, no `data/` directory required. Everything that would touch arXiv or Semantic Scholar is monkeypatched.

### 1.2 Configuration

`pytest.ini` at the repository root is the whole config:

```ini
[pytest]
pythonpath = src
```

That single line is why tests can write `from utils import config as C` and `from parser import m2_gather` without any `sys.path` juggling in the test files themselves (several still add `sys.path.insert(0, ...)` defensively, which is harmless). If you move `pytest.ini` or run pytest from a different rootdir, every import in `src/tests/` breaks.

There is no `conftest.py`. Fixtures are defined locally in the test files that need them — mostly a `corpus` fixture that builds a throwaway `data/` tree under pytest's `tmp_path`.

### 1.3 What each file covers

| File | Tests | Guards |
|---|---|---|
| `test_m1_metadata.py` | 4 | `parse_entry_id` (old and new ID schemes), `build_versions`, `store_results` + missing-ID bookkeeping, `build_papers_table` |
| `test_m1_references.py` | 10 | Batch crawling against a fake API, 400-splitting, cache reuse, raw checkpoint requirement, reference normalisation, published-reference filtering and dedup, schema rejection of empty identifiers, `run_m1` batch integration |
| `test_run_m1_downloader.py` | 4 | `run_m1` uses the canonical downloader, version checkpointing, `save_source_with_stats` accounting, **thread overlap** between reference batch and source download, standalone atomic stats checkpoint |
| `test_m2_gather.py` | 55 | Comment stripping, include-target parsing, root detection and tie-breaks, `collect` (nesting, cycles, unresolved, escapes), scan reasons |
| `test_m2_hierarchy.py` | 33 | Heading nesting, abstract→section, math blocks, figures/tables as leaves, `itemize` children, references-section dropping, include expansion, unbalanced markers |
| `test_m2_text.py` | 22 | Text cleaning, `\cite` preservation, math unification, sentence splitting (abbreviations, decimals, URLs), cross-version dedup, ambiguous-repeat handling |
| `test_m2_parser.py` | 5 | BibTeX entry parsing, key dedup + citation rewriting, candidate generation scoping, author/title normalisation, `process_paper` end-to-end |
| `test_m2_export_parquet.py` | 17 | All six schemas round-trip, list/null/dictionary columns, idempotent overwrite, corrupt-JSON skipping, orphan-edge warnings, integrity checks, candidate row validation, CLI |

Counts add to 150 (M1 = 18, M2 = 132). Note the skew: **132 of 150 tests are M2**. M1 has 18, and M3 has **zero**.

### 1.4 The coverage gap you should know about

`src/matching/m3_candidates.py` has no tests. Its only coverage is indirect: `test_m2_parser.py::test_candidate_generation_stays_within_citing_paper_and_keeps_features` calls into the M3 candidate generator to check that candidates never leak across papers. That is a single assertion on the M2 side, not an M3 test.

When you start M3 for real, the first file you should add is `src/tests/test_m3_candidates.py` — at minimum, one test per branch of the `0.35` similarity gate in `generate_candidates`.

### 1.5 What the suite does *not* cover

| Not tested | Why it matters |
|---|---|
| `m1_download.py` retry/backoff paths | `MIN_DELAY_SECONDS = 3.0`, `MAX_RETRIES = 4`, `BACKOFF_SECONDS = 30` are never exercised against a failing server |
| The circuit breaker in `m1_references.py` | `BREAKER_THRESHOLD = 3`, `COOLDOWN_SECONDS = 300` — the split-and-retry path is tested, the cooldown is not |
| `run_m2.py` orchestration | `process_paper` is tested in isolation; the *order* of stages and the fact that `m2_hierarchy` is never called are not |
| `m2_content_dedup.py` | Only reachable through `run_m2`, which does not call it |
| `benchmark.py` | `PerfRecorder` and `ResourceMonitor` have no tests at all |
| `m1_references.build_papers_table` schema | `test_papers_table` checks row counts, not that the output validates against `PAPERS_SCHEMA` — which it does not (see [06_integration.md](./06_integration.md) §4.6) |

---

## 2. Performance Measurement

### 2.1 What the statement actually asks for

From the M1 statement, verbatim:

> The scraper's performance should also be illustrated through two main points, **running time** and **memory footprint**. Running time counts the total time to process one paper, the average time to process each of the required papers, and the total time for entry discovery, while memory footprint counts the **maximum RAM** used during the scraping, the **average RAM** consumption, the **maximum disk storage** required, and the **final output's storage size**. Measure both points in an auditable way, for example peak RAM through `/usr/bin/time -v` or `psutil`, average RAM through periodic sampling, and disk usage through `du`. These tools are examples, not requirements. If the system consists of several programs, the runtime is counted as the wall time taken to run the system in an end-to-end way, while the memory footprint is considered as the total memory consumed by the full system. The testbed for the final benchmarking is a **Google Colab instance running on CPU-only mode**.

Plus the M1 report also needs these corpus statistics:

> the number of papers scraped successfully, the overall success rate, the average paper size in bytes before and after removing figures, the average number of references per paper, and the average success rate for scraping reference metadata.

`src/utils/benchmark.py` exists to produce exactly these numbers. It is not decoration — every field below maps to a phrase in that paragraph.

### 2.2 Requirement → field mapping

| Statement phrase | Field in `benchmark_m1.json` | Produced by |
|---|---|---|
| total time to process one paper | `paper_times_s[arxiv_id]` | `PerfRecorder.paper()` / `add_paper_time()` |
| average time to process each paper | `avg_paper_s` | `PerfRecorder.summary()` |
| total time for entry discovery | `discovery_s`, also `stages_s.discovery` | `rec.stage("discovery")` in `run_m1.main` |
| total end-to-end wall time | `total_wall_s` | `PerfRecorder.stop()` |
| maximum RAM | `peak_ram_mb` | `ResourceMonitor.summary()` |
| average RAM | `avg_ram_mb` | `ResourceMonitor.summary()` |
| maximum disk storage | `peak_disk_mb` | `ResourceMonitor.summary()` |
| final output storage size | `final_output_mb`, `final_output_bytes` | `ResourceMonitor.summary()` |
| papers scraped successfully / success rate | `n_ok`, `n_failed`, `success_rate` | `record_result()` |
| avg paper size before/after figure removal | `avg_paper_size_before_bytes`, `avg_paper_size_after_bytes` | `record_paper_size()` |
| avg references per paper | `avg_refs_per_paper`, `avg_arxiv_refs_per_paper` | `record_references()` |
| reference crawl success rate | `ref_n_ok`, `ref_n_failed`, `ref_success_rate` | `record_references()` |
| version coverage | `coverage.version_coverage` | `run_m1.main` (added after `rec.save`) |

### 2.3 `ResourceMonitor` — how RAM and disk are measured

Two daemon threads, started by `ResourceMonitor.start()`:

**RAM thread** (`_ram_loop`, `interval=0.5`s) calls `_tree_rss()`, which sums `memory_info().rss` over `[self._proc] + self._proc.children(recursive=True)`. Summing the whole process tree is deliberate: the statement says memory is "the total memory consumed by the full system", and `run_m1` uses a `ThreadPoolExecutor`, so there are live child threads and possibly child processes. A single-process `psutil.Process().memory_info().rss` would under-report.

**Disk thread** (`_disk_loop`, `disk_interval=5.0`s) calls `dir_size()` over each watched directory via `os.walk`. `run_m1` passes `watch_dirs=[args.corpus_dir]`.

The disk thread has a Colab-specific escape hatch. `max_scan_s = 10.0`: if the *first* scan takes longer than that (Google Drive mounts are notorious for this), `ResourceMonitor.stop()` stops re-scanning and instead estimates:

```python
est = self.disk_baseline + self._tracked_added_bytes
```

and sets `disk_estimated = True` in the output. **If you see `"disk_estimated": true` in your benchmark JSON, your disk numbers are an estimate, not a measurement.** Say so in the report rather than presenting them as measured. To get real numbers, run on Colab's local `/content` filesystem instead of a mounted Drive.

`record_archive_cycle(temp_archive_bytes, kept_extracted_bytes)` is what makes `peak_disk_mb` meaningful: it adds the transient `.tar.gz` size plus the bytes actually kept, so the peak reflects the real high-water mark during a download, not just the settled state afterwards.

### 2.4 `PerfRecorder` — how time is attributed

`PerfRecorder` is used as a context manager in `run_m1.main`:

```python
with PerfRecorder("m1", watch_dirs=[args.corpus_dir]) as rec:
    with rec.stage("discovery"):
        info = discover(ids)
    with rec.stage("crawl"):
        for i in range(0, len(ids), args.batch_size):
            group = ids[i:i + args.batch_size]
            crawl_batch(group, info, rec, args.corpus_dir, session, coverage)
```

`__enter__` → `start()` → records `time.perf_counter()` and starts the monitor. `__exit__` → `stop()` → computes `wall_s` and stops the monitor.

The API you will actually call:

| Method | Use |
|---|---|
| `stage(name)` | `@contextmanager`; **accumulates** on repeat calls. Use for pipeline phases. |
| `paper(arxiv_id)` | `@contextmanager`; **accumulates** on repeat calls. Use for per-paper totals spanning versions. |
| `add_paper_time(arxiv_id, seconds)` | Adds time measured outside a `paper()` block — e.g. an even share of a batch API call. |
| `record_paper_size(...)` | One call per *version*. `before_bytes` = uncompressed size of everything in the archive (figures included); `after_bytes` = size of kept `.tex`/`.bib`; `archive_bytes` = the `.tar.gz` size. `is_pdf_only=True` records the version without touching the size sums. |
| `record_result(arxiv_id, ok)` | Overwrites — last write wins. |
| `record_references(arxiv_id, ok, total_refs, arxiv_refs)` | Per-paper reference crawl outcome. |
| `record_dedup(arxiv_id, kind, before, after)` | M2 only; accumulates per `(kind, arxiv_id)`. |
| `summary()` | Returns the dict without writing. |
| `save(path=None)` | Writes JSON and returns the same dict. Default path `data/benchmark_<milestone>.json`. |

The `milestone` argument matters: it only affects the default filename. `PerfRecorder("m2")` writes `data/benchmark_m2.json`, so M1 and M2 benchmarks never overwrite each other.

### 2.5 The output shape

`run_m1.main` calls `rec.save(args.bench_out)` (default `data/benchmark_m1.json`), then appends `coverage` and rewrites the file. A real run produces something like:

```json
{
  "milestone": "m1",
  "total_wall_s": 7412.31,
  "stages_s": { "discovery": 118.4, "crawl": 7293.91 },
  "n_papers_timed": 400,
  "avg_paper_s": 18.233,
  "max_paper_s": 141.87,
  "discovery_s": 118.4,
  "n_ok": 398,
  "n_failed": 2,
  "success_rate": 0.995,
  "ref_n_ok": 391,
  "ref_n_failed": 9,
  "ref_success_rate": 0.9775,
  "avg_refs_per_paper": 34.12,
  "avg_arxiv_refs_per_paper": 12.87,
  "n_papers_recorded": 400,
  "n_tex_papers": 396,
  "avg_paper_size_before_bytes": 2211337,
  "avg_paper_size_after_bytes": 184302,
  "n_tex_versions": 742,
  "n_pdf_only_versions": 31,
  "n_papers_with_pdf_only_version": 24,
  "avg_version_size_before_bytes": 1188204,
  "avg_version_size_after_bytes": 99312,
  "peak_ram_mb": 412.7,
  "avg_ram_mb": 188.3,
  "ram_samples": 14824,
  "disk_baseline_mb": 0.0,
  "peak_disk_mb": 2361.4,
  "final_output_mb": 1982.6,
  "final_output_bytes": 2078937088,
  "disk_estimated": false,
  "paper_times_s": { "2310.12345": 22.113, "...": 0 },
  "paper_sizes_bytes": { "2310.12345": { "before": 4102331, "after": 302144, "tex_versions": 3 } },
  "paper_results": { "2310.12345": true },
  "paper_references": { "2310.12345": { "ok": true, "total": 41, "with_arxiv": 15 } },
  "coverage": {
    "expected_versions": 773,
    "crawled_versions": 742,
    "failed_versions": 31,
    "not_found_papers": 2,
    "version_coverage": 0.9599
  }
}
```

*(Illustrative numbers — the repository ships no committed benchmark result. `data/` is in `.gitignore`.)*

`paper_*` keys are per-paper detail, included by default (`save(include_per_paper=True)`). `run_m1` prints the JSON with those keys filtered out, so the console shows only the summary. Pass `include_per_paper=False` if the per-paper block is bloating the file.

### 2.6 The `before` vs `after` size definition

This is the number the report is graded on and the easiest one to get wrong.

`inspect_archive_bytes(data, extracted_dir)` returns `(archive_bytes, before_bytes, after_bytes)`:

- `before_bytes` — `sum(m.size for m in tar.getmembers() if m.isfile())`. Uncompressed, **figures included**. This is the "before removing figures" number.
- `after_bytes` — sum of `.tex` and `.bib` file sizes in `extracted_dir`. This is the "after" number.
- `archive_bytes` — `len(data)`, the compressed `.tar.gz`.

It also handles the four shapes a `/src/` response can take:

| Prefix | Interpretation | Returned `before_bytes` |
|---|---|---|
| `%PDF` | PDF-only submission | `= archive_bytes` (and `after_bytes = 0`) |
| valid tar | normal `.tar.gz` source | sum of member sizes |
| `\x1f\x8b` | single gzipped `.tex` | `len(gzip.decompress(data))` |
| anything else | plain text | `max(archive_bytes, after_bytes)` |

So a PDF-only version is recorded as a version with `after_bytes == 0`, and `record_paper_size` classifies it via `pdf_flag` into `n_pdf_only_versions` / `papers_with_pdf_only_version` instead of polluting the size averages. The statement explicitly requires this: "for such versions you should keep an empty record in the corpus rather than dropping the publication."

### 2.7 Running a benchmark

Two ways.

**Built into `run_m1`** (the normal path — nothing extra to do):

```powershell
python src/run_m1.py --ids-file ids.txt --corpus-dir data/corpus --bench-out data/benchmark_m1.json
```

**End-to-end wrapper for anything** (`benchmark.py` CLI, which spawns the command and monitors *its* PID):

```powershell
python src/utils/benchmark.py --milestone m1 --watch data/corpus -- python src/run_m1.py --ids-file ids.txt
```

The wrapper path uses `run_command()`, which does `subprocess.Popen(cmd)` and then constructs a fresh `ResourceMonitor(..., pid=proc.pid)` so RAM is sampled from the child tree rather than the wrapper. It returns `(summary, recorder)` and adds `returncode` and `command` to the summary. Use it when you want to time a command you have not instrumented — including `run_m2.py`, which has no `PerfRecorder` calls at all.

> **Colab note.** The statement fixes the benchmark testbed at "a Google Colab instance running on CPU-only mode." Record the Colab tier and `nproc` alongside your numbers in the report; a run on a local 16-core machine is not comparable.

---

## 3. Known Gaps and What To Do

| # | Gap | Effect | Action |
|---|---|---|---|
| 1 | `run_m2.py` has no `PerfRecorder` | `data/benchmark_m2.json` is never produced; M2's statement-required dedup statistics have no machine-readable source | Either wrap with `benchmark.py` CLI, or add `PerfRecorder("m2")` + `record_dedup()` calls to `run_m2.main` |
| 2 | `benchmark.py` has no tests | A regression in `summary()` silently corrupts every reported number | Add `src/tests/test_benchmark.py`: one case for `inspect_archive_bytes` per archive shape, one for `record_paper_size` accumulation across versions, one for `ResourceMonitor.summary()` on an empty sample list |
| 3 | No committed benchmark result | Nobody can reproduce or compare numbers | Keep `data/benchmark_m1.json` out of git (correct — it is data), but paste the summary table into the Jupyter Book with the run date and Colab tier |
| 4 | `disk_estimated` can silently be `true` | Reported "max disk" may be an estimate | Check the flag; if true, rerun on local `/content` storage |
| 5 | M1 coverage counters live in `run_m1`, not `PerfRecorder` | `coverage` is appended *after* `save()`, so a crash before that line loses it | Acceptable; note it if you resume a partial run |
| 6 | `m1_download` retry paths untested | Backoff bugs surface only in production runs | Add a test with a fake session returning 503, asserting `MAX_RETRIES` attempts |

---

## 4. Reading Guide

- Want to know **what is tested** → §1.3.
- Want to know **what is not** → §1.5 and §3.
- Want to **produce the M1 report numbers** → §2.1, §2.2, §2.7.
- Want to know **why a size number looks wrong** → §2.6.
- Want to know **why disk numbers are estimates** → §2.3.

---

## 5. Team Review

- **Author:** P4 (owns `utils/benchmark.py` and the Parquet/export layer)
- **Reviewer:** P1 (M1 report lead) and the Jupyter Book owner
- **Design decisions:** Two-thread monitor (RAM and disk separated) so a slow Colab Drive scan never delays RAM sampling. Process-tree RSS rather than single-process, to match "total memory consumed by the full system". Per-version size recording with explicit PDF-only flagging, to satisfy "keep an empty record rather than dropping the publication".
- **Open questions:** Should M2 get first-class `PerfRecorder` support, or is the CLI wrapper enough? Who owns the benchmark numbers in the Jupyter Book — P1 (M1) or the report lead?