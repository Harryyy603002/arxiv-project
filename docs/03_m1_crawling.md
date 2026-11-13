# M1 — Data Crawling

> **Audience.** A student working on or reviewing Milestone 1. Read this with `src/run_m1.py` open in another window.
>
> **Official statement:** [Milestone 1 — Crawling](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling/)
>
> **Scope:** resolve the assigned arXiv IDs, crawl **every version** of each paper's LaTeX source, crawl reference metadata from Semantic Scholar, and measure the cost of doing so.

---

## 1. What M1 Has To Deliver

Verbatim from the statement, then the code that satisfies it:

| Requirement | Satisfied by |
|---|---|
| "resolve these assigned IDs and to locate every version of each paper" | `run_m1.discover()` — two arXiv API passes |
| "**all available versions must be crawled**" | `run_m1.crawl_batch()` — loops `v1..vN` per paper, no skipping |
| "retrieve full-text source files ... without modifying their contents" | `m1_download.fetch_source()` + `save_source_with_stats()` |
| "**REMOVE any figure** ... by extraction rather than by editing" | `_collect_tex_bib()` keeps only `.tex`/`.bib`; `\includegraphics` lines stay untouched inside the `.tex` |
| "reference metadata must additionally be crawled ... via the Semantic Scholar API" | `m1_references.fetch_batch_group()` |
| "keep **all** crawled references locally" | `refs_raw.json` + `refs.json`; only arXiv-carrying rows become table rows later |
| performance: wall time, RAM, peak disk, output size | `utils/benchmark.PerfRecorder` |
| two published tables: `papers`, `references` | `build_papers_table()` ok, `build_published_references()` CSV only |

Two things the statement stresses that are easy to miss:

- **Use `export.arxiv.org`, never `arxiv.org`.** The statement is explicit that the main site triggers robot detection and 403 blocks. `config.ARXIV_SRC_URL` hardcodes the mirror.
- **PDF-only versions must be kept as empty records, not dropped.** ~3% of submissions are PDF-only. `save_source_with_stats` returns `after_bytes == 0` and the version is recorded with `status: "empty"`.

---

## 2. Modules

| File | Responsibility | Owner |
|---|---|---|
| `src/run_m1.py` | Integrated entry point. Discovery, serial source download with a concurrent reference batch, checkpoint writes. Wraps everything in `PerfRecorder`. | P2 |
| `src/crawler/m1_metadata.py` | Standalone metadata crawler. Writes `meta.json` + `versions.json` per paper, `papers.csv`/`papers.parquet`, `failed_ids.txt`. | P3 |
| `src/crawler/m1_download.py` | Source download + extraction. Rate limiting, retries, atomic checkpoints, byte accounting. | P1 |
| `src/crawler/m1_references.py` | Semantic Scholar crawl, batching, circuit breaker, normalisation, published-reference export. | P2 |
| `src/utils/config.py` | Paths, ID normalisation, rate constants. Single source of truth. | P4 |
| `src/utils/benchmark.py` | `PerfRecorder` + `ResourceMonitor`. | P4 |

> **Two entry points exist.** `run_m1.py` is the integrated one and is what the report should describe. `m1_metadata.py` and `m1_download.py` are also runnable standalone for debugging. They are not two alternative pipelines — `run_m1` calls `m1_download` functions directly.

---

## 3. Workflow

```
ids.txt
   |
   v
discover(ids)                        -- arXiv API, 2 passes, 50 IDs/batch
   |   pass 1: id_list=[base ids]        -> title, authors, venue, n_versions, date of v1
   |   pass 2: id_list=[base v1..vN]     -> updated date for v2..vN
   v
for each batch of 25:
   |-- executor.submit(_fetch_reference_batch, group)   <- S2 runs concurrently
   |
   |-- for aid in group:                                <- serial, 3s apart
   |     data = m1_download.fetch_source(session, aid, v)
   |     status, reason, files, archive, before, after =
   |         m1_download.save_source_with_stats(data, vdir)
   |     write v{N}/metadata.json  (atomic)
   |
   |-- batch_data = reference_future.result()           <- join
         write refs_raw.json, refs.json
         write v{N}/references.json for every version
```

**Why the two streams overlap.** arXiv's `/src/` endpoint forces ~3 s between requests; Semantic Scholar has its own independent limit. Running them sequentially wastes half the available throughput. `crawl_batch` submits the reference batch to a `ThreadPoolExecutor(max_workers=1)` *before* the serial source loop and joins it afterwards, so S2 latency hides inside arXiv waiting time.

**Why only one worker.** More reference workers buy nothing — arXiv's 3 s floor is the bottleneck — and add failure modes. `test_crawl_batch_overlaps_reference_and_source_work` asserts the overlap actually occurs.

---

## 4. Function Reference

### 4.1 `run_m1.discover(ids, batch=50) -> Dict[str, dict]`

Two API passes because the arXiv API only exposes the v1 date and the latest-version date in one query — the intermediate versions need a separate lookup by versioned ID.

**Pass 1** queries the base IDs. For each result, `split_version(r.get_short_id())` yields `(base, n)` and stores:

```python
info[base] = {
    "title": " ".join(r.title.split()),
    "authors": [a.name for a in r.authors],
    "venue": r.journal_ref,
    "n_versions": n or 1,
    "version_dates": {1: r.published.isoformat()},
}
```

**Pass 2** builds `[f"{b}v{v}" for b in info for v in 1..n]` and queries those, filling `version_dates[v] = r.updated.isoformat()` for `v > 1`.

`_query()` wraps `arxiv.Client.results()` and retries up to 5 times on HTTP 429 with `30 * attempt` backoff. The client is constructed as `arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)`.

> **Known gap.** `version_dates` can be incomplete — the code logs `WARNING: thiếu ngày của N version` in `crawl_batch` rather than failing. The statement wants submission and revision dates per version, so check this warning count before publishing `papers.revised_dates`.

### 4.2 `run_m1.crawl_batch(group, info, rec, corpus, session, coverage, reference_executor=None)`

Processes 25 papers per call (the default `--batch-size`). Per paper:

1. `pdir = corpus / folder_name(aid)` — see §7.1.
2. Writes `pdir/paper.json` with `arxiv_id, title, authors, submission_date, revised_dates, venue, n_versions`.
3. For each `v` in `1..n`:
   - If `v{N}/metadata.json` exists, read `archive_bytes`/`bytes_before`/`bytes_after` from it, feed them to `rec.record_paper_size(...)`, and **skip the download**. This is the resume path.
   - Otherwise `fetch_source` -> `save_source_with_stats` -> `rec.record_paper_size(...)` -> write `metadata.json` atomically (`.json.tmp` then `.replace`).
4. After the loop, `reference_future.result()` joins, then per paper writes `refs_raw.json`, `refs.json`, and `v{N}/references.json` for every existing version directory.

`coverage` is mutated in place: `expected_versions`, `crawled_versions`, `failed_versions`, `not_found_papers`. `main()` appends `version_coverage` and rewrites the benchmark JSON after `rec.save()`.

**The `v{N}/references.json` write is duplicated data** — the same reference list written once per version. It exists because `run_m2._load_references` falls back to `v*/references.json` when `refs.json` is not found in the corpus root. See [06 Integration](./06_integration.md) §4.4.

### 4.3 `m1_download.fetch_source(session, arxiv_id, version) -> bytes`

```python
url = C.ARXIV_SRC_URL.format(id_with_version=f"{arxiv_id}v{version}")
```

Constants (module level, not in `config`):

| Constant | Value | Meaning |
|---|---|---|
| `MIN_DELAY_SECONDS` | `3.0` | enforced by `polite_wait()` before every request |
| `MAX_RETRIES` | `4` | attempts per version |
| `BACKOFF_SECONDS` | `30` | attempt *k* sleeps `30 * k` seconds |
| `MAX_FILE_BYTES` | `50 * 1024 * 1024` | individual members larger than this are skipped |
| `KEEP_SUFFIXES` | `{".tex", ".bib"}` | the only extensions extracted |

`polite_wait()` uses a module-level `_last_request` timestamp; `_touch()` updates it. This is process-global state, so it serialises correctly as long as only one thread calls it — which is why source downloads stay serial.

Raises `DownloadError` after `MAX_RETRIES`.

### 4.4 `m1_download.save_source_with_stats(data, out_dir)`

Returns `(status, reason, files, archive_bytes, before_bytes, after_bytes)`.

This is the function that implements "remove figures by extraction". It sniffs the payload four ways:

| Prefix | Handling | `status` |
|---|---|---|
| `%PDF` | not extractable | `"empty"`, `reason="pdf_only"` |
| tar (`r:*`) | `_collect_tex_bib` walks members, keeps `.tex`/`.bib` under `MAX_FILE_BYTES` | `"ok"` / `"empty"` |
| `\x1f\x8b` | single gzipped file; `_looks_like_tex` decides | `"ok"` / `"empty"` |
| else | written as `main.tex` if it looks like TeX | `"ok"` / `"empty"` |

`_safe_relative_path(name)` rejects absolute paths, `..` traversal, and anything that escapes `out_dir` before writing. **This is the security boundary for untrusted archive contents — do not weaken it.**

`before_bytes` is the *uncompressed* size of every file in the archive (figures included); `after_bytes` is the sum of kept `.tex`/`.bib` sizes. That is exactly the "before vs after removing figures" pair the report needs.

`save_source(data, out_dir)` is the older 3-tuple version without stats; `save_source_with_stats` is what `run_m1` uses.

### 4.5 `m1_references.fetch_batch_group(session, ids, attempts=MAX_RETRIES) -> list`

POSTs to the Semantic Scholar batch endpoint. Key behaviour:

- **HTTP 413 -> recursive split.** If S2 rejects the group as too large, the batch is halved and each half retried. `test_batch_crawl_splits_rejected_group` covers this.
- **Circuit breaker.** `BREAKER_THRESHOLD = 3` consecutive rate-limit responses opens the breaker; `COOLDOWN_SECONDS = 300` before it closes. `_on_rate_limited()` / `_on_success()` maintain the counter.
- **`Retry-After` respected.** `_retry_after_seconds(response)` parses the header and sleeps that long rather than guessing.
- **`MAX_EXTRA_DELAY = 30.0`** caps the adaptive extra delay added on top of `S2_DELAY_SECONDS`.

`DEFAULT_BATCH_SIZE = 25`, `MAX_BATCH_SIZE = 500`.

### 4.6 `m1_references.build_published_references() -> dict[str, int]`

Walks `data/*/refs.json`, normalises each reference with `normalize_reference()`, dedupes with `deduplicate_references()` on `(ref_title, ref_authors, ref_submission_date)`, and keeps only rows where `ref_arxiv_id` is truthy — matching the statement's rule that only arXiv-carrying references become `references` table rows.

Writes `data/references.csv` and `data/references_stats.json`:

```json
{
  "total_local_references": 13842,
  "references_with_arxiv_id": 5210,
  "references_without_arxiv_id": 8632,
  "published_rows": 5187,
  "duplicate_rows": 23
}
```

> **It writes CSV, not Parquet.** `REFERENCES_SCHEMA` exists in `utils/schemas.py` and is never used here. The fix is one line — see [06 Integration](./06_integration.md) §4.6.

### 4.7 `run_m1.build_references_table() -> int`

```python
def build_references_table() -> int:
    """Compatibility hook for callers that build the references table separately."""
    return 0
```

A **no-op stub**. It is called at the very end of the legacy `run_m1.crawl_references()`, and `test_m1_references.py` monkeypatches it for that reason. It does not build the references table. The real work is in `m1_references.build_published_references()`. Do not call this expecting output.

---

## 5. Configuration

All in `src/utils/config.py`:

| Name | Value | Note |
|---|---|---|
| `ARXIV_SRC_URL` | `https://export.arxiv.org/src/{id_with_version}` | mirror only |
| `ARXIV_DELAY_SECONDS` | `3.0` | |
| `ARXIV_API_BATCH` | `100` | IDs per `id_list` call |
| `CONTACT_EMAIL` | env, default `your-email@example.com` | **set this** — it goes in the User-Agent |
| `USER_AGENT` | `arxiv-course-project/1.0 (student crawler; contact: ...)` | descriptive, as the statement requires |
| `KEEP_EXTENSIONS` | `(".tex", ".bib")` | |
| `S2_API_URL` | `https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}` | |
| `S2_API_KEY` | env or Colab `userdata` | see below |
| `S2_DELAY_SECONDS` | `1.1` with key, `3.2` without | |

### The `S2_API_KEY` speedup

Without a key you share one throttled unauthenticated pool at ~100 requests / 5 minutes. With a free key you get a guaranteed 1 request/second. `S2_DELAY_SECONDS` reflects this: **3.2 s -> 1.1 s is roughly a 3x speedup on the reference crawl** for zero cost.

```ini
# .env at repo root
S2_API_KEY=your_key_here
```

Or in Colab: **Secrets** -> add `S2_API_KEY`. The config tries `os.environ` first, then `google.colab.userdata`.

`_warn_if_no_api_key()` in `m1_references` logs a warning at crawl start if the key is absent.

---

## 6. Running It

### Integrated (the one that matters)

```powershell
python src/run_m1.py --ids-file ids.txt --corpus-dir data/corpus --bench-out data/benchmark_m1.json --user-agent "my-team/1.0" --batch-size 25
```

`ids.txt` is one base ID per line. Output lands in `data/corpus/<paper_key>/`.

### Standalone metadata discovery

```powershell
python src/crawler/m1_metadata.py --ids 1706.03762 1810.04805
python src/crawler/m1_metadata.py --range 2602.04870-2602.09869
python src/crawler/m1_metadata.py --table-only      # rebuild papers.csv/parquet from existing meta.json
```

### Standalone source download

```powershell
python src/crawler/m1_download.py --ids-file ids.txt
```

`m1_download.read_versions()` reads `versions.json` if present, else falls back to `--fallback-n`. This is why `m1_metadata` must run first when using the standalone path.

---

## 7. Known Issues

### 7.1 Folder-name mismatch breaks old-style arXiv IDs

```python
# run_m1.py:36
def folder_name(arxiv_id: str) -> str:
    return arxiv_id.replace("/", "_").replace(".", "-")

# config.py:92
def paper_key(arxiv_id: str) -> str:
    return split_version(arxiv_id)[0].replace("/", "-").replace(".", "-")
```

| ID | `run_m1.folder_name` | `config.paper_key` |
|---|---|---|
| `2310.12345` | `2310-12345` | `2310-12345` |
| `hep-th/9901001` | `hep-th_9901001` | `hep-th-9901001` |

The statement explicitly says pre-2007 references use the old `archive/YYMMNNN` format and must be retained. When the assigned range includes such IDs, M2 will look for `hep-th-9901001` and find `hep-th_9901001`, then silently skip the paper.

**Fix:** make `folder_name` delegate to `C.paper_key`. One line. Add a test with `hep-th/9901001`.

### 7.2 `build_references_table()` is a no-op

See §4.7. Kept only for test compatibility. Use `m1_references.build_published_references()`.

### 7.3 `references.parquet` is never written

See §4.6 and [06 Integration](./06_integration.md) §4.6.

### 7.4 `papers.parquet` bypasses `PAPERS_SCHEMA`

`m1_metadata.build_papers_table()` calls `df.to_parquet(...)` directly on a DataFrame built from `META_FIELDS`, so `revised_dates` and `venue` are missing and eight extra columns are present. See [06 Integration](./06_integration.md) §4.6.

### 7.5 `ResourceMonitor` disk scan can silently estimate

If the first disk scan exceeds `max_scan_s = 10.0` (common on Colab Drive mounts), the final disk figure is estimated and `disk_estimated` is set. Check the flag before reporting. See [07 Testing and performance](./07_testing_and_performance.md) §2.3.

### 7.6 Standalone and integrated paths write different files

| Path | Writes |
|---|---|
| `m1_metadata.py` | `meta.json`, `versions.json`, `papers.csv`, `papers.parquet`, `failed_ids.txt` |
| `run_m1.py` | `paper.json`, `v{N}/metadata.json`, `refs.json`, `refs_raw.json`, `v{N}/references.json` |

They are **not** interchangeable. `run_m1` does not write `meta.json`, so the legacy helper `run_m1.crawl_references()` — which skips any ID without `meta.json` — would process nothing if only `run_m1.main` had been run. Conversely `m1_metadata` does not write `paper.json`. See [02 Data contracts](./02_data_contracts.md) for both shapes.

`run_m1.crawl_references()` is legacy and only used by `test_m1_references.py`. The live path is `crawl_batch`. Its `meta_path` guard is the one trap worth knowing:

```python
if aid in seen or not C.meta_path(aid).exists():
    continue
```

`m1_references.crawl()` (the standalone module entry point) does **not** have this guard — it filters on `refs_path` / `raw_refs_path` existence instead.

---

## 8. Error Handling

| Situation | Behaviour |
|---|---|
| arXiv HTTP 429 during discovery | retry up to 5x, `30 * attempt` backoff |
| arXiv HTTP 4xx/5xx during source fetch | retry up to `MAX_RETRIES = 4`, `30 * k` backoff, then `DownloadError` -> `coverage["failed_versions"] += 1` |
| PDF-only submission | `status: "empty"`, `reason: "pdf_only"`, version kept, `after_bytes = 0` |
| Corrupt tar | `status: "empty"`, `reason: "corrupt_archive"` |
| Archive member > 50 MB | skipped silently |
| Archive member escapes `out_dir` | `_safe_relative_path` returns `None`, member skipped |
| S2 HTTP 413 (batch too large) | recursively halve and retry |
| S2 HTTP 429 | `Retry-After` respected, adaptive delay, circuit breaker after 3 |
| S2 returns nothing for a paper | `refs.json` written with `[]`, `rec.record_references(aid, False)` |
| Paper not in arXiv API | `coverage["not_found_papers"] += 1`, no directory created |
| Interrupted mid-version | `v{N}/metadata.json` absent -> retried on next run |
| Interrupted mid-JSON-write | `.tmp` file left behind; the real file is untouched |

---

## 9. Team Review

- **Author:** P1, P2, P3
- **Reviewer:** P4
- **Design decisions:**
  - Extract only `.tex`/`.bib` rather than downloading then deleting figures — the statement requires removal "by extraction rather than by editing", and it also cuts transfer volume.
  - Overlap the S2 reference batch with the serial arXiv stream, bounded at one worker, because the two services have independent rate limits.
  - Keep `refs_raw.json` alongside `refs.json` so normalisation bugs are fixable without a re-crawl. At a 3 s floor, a 40k-ID re-crawl is ~20 hours.
- **Open questions:** Who fixes §7.1 (folder naming) and §7.3 (`references.parquet`)? Both are one-line changes but neither is owned.
