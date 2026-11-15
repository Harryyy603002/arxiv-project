# M1 — Data Crawling

> **Audience.** A student working on or reviewing Milestone 1. Read this with `src/run_m1.py` open in another window.
>
> **Official statement:** [Milestone 1 — Crawling](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling/)
>
> **Scope:** resolve the assigned arXiv IDs, crawl **every version** of each paper's LaTeX source, crawl reference metadata from Semantic Scholar, and measure the cost of doing so.

> **This document was re-verified against commit `01b5bb0`.** M1 was substantially
> rewritten by a collaborator: `m1_metadata.py` was deleted, discovery moved into
> `run_m1.py`, and two new modules (`m1_tables.py`, `m1_aggregate.py`) were added.
> If you are reading an older copy, several sections below will not match the code.

---

## 1. What M1 Has To Deliver

Verbatim from the statement, then the code that satisfies it:

| Requirement | Satisfied by |
|---|---|
| "resolve these assigned IDs and to locate every version of each paper" | `run_m1.discover()` — two arXiv API passes, cached |
| "**all available versions must be crawled**" | `run_m1.crawl_batch()` — loops `v1..vN` per paper |
| "retrieve full-text source files ... without modifying their contents" | `m1_download.fetch_source()` + `save_source_with_stats()` |
| "**REMOVE any figure** ... by extraction rather than by editing" | `_collect_tex_bib()` keeps only `.tex`/`.bib`; `\includegraphics` stays inside the `.tex` |
| "reference metadata must additionally be crawled ... via the Semantic Scholar API" | `m1_references.fetch_batch_group()` |
| "keep **all** crawled references locally" | `refs_raw.json` + `refs.json`; only arXiv-carrying rows become table rows later |
| performance: wall time, RAM, peak disk, output size | `utils/benchmark.PerfRecorder` + `m1_aggregate` |
| two published tables: `papers`, `references` | `m1_tables.build_all()` — **both now written as Parquet** |

Two things the statement stresses that are easy to miss:

- **Use `export.arxiv.org`, never `arxiv.org`.** `m1_download.BASE_URL` hardcodes the mirror because the main site returns HTTP 403.
- **PDF-only versions must be kept as empty records, not dropped.** ~3% of submissions are PDF-only. `save_source_with_stats` returns `after_bytes == 0` and the version is recorded with `status: "empty"`.

---

## 2. Modules

| File | Responsibility | Owner |
|---|---|---|
| `src/run_m1.py` | Integrated entry point. ID parsing, discovery, serial source download with a concurrent reference batch, multi-session checkpointing, table build. | P2 |
| `src/crawler/m1_download.py` | Source download + extraction. Rate limiting, retries, byte accounting. **Library only** — has no `__main__`. | P1 |
| `src/crawler/m1_references.py` | Semantic Scholar batch crawl, circuit breaker, normalisation, published-row collection. **Library only** — has no `__main__`. | P2 |
| `src/crawler/m1_tables.py` | Builds `papers.parquet`/`references.parquet` + CSV mirrors from the crawled tree. | P2 |
| `src/crawler/m1_aggregate.py` | Merges `benchmark/session_NNN.json` files into `benchmark_m1_total.json` + `sessions.csv`. | P4 |
| `src/utils/config.py` | Paths, ID normalisation, rate constants. Single source of truth. | P4 |
| `src/utils/benchmark.py` | `PerfRecorder` + `ResourceMonitor`. | P4 |

> **`m1_metadata.py` no longer exists.** It was deleted because it was not in the
> main flow and its schema diverged from the statement. Discovery now happens in
> `run_m1.discover()` and the per-paper metadata lands in `paper.json` (not
> `meta.json`/`versions.json`).

> **`m1_download.py` and `m1_references.py` are libraries.** The "Standalone
> source download" and "Standalone reference crawl" commands that older docs
> listed no longer work — neither module has an `if __name__ == "__main__"` block.
> Run `run_m1.py` instead, or import the functions.

---

## 3. Workflow

```
ID.txt  (one ID or one ID range per line)
   |
   v
read_ids(path, month_last)              -- BOM/CRLF/URL/vN tolerant, expands ranges
   |
   v
select_ids(ids, limit, sample)          -- --limit N or --sample N (evenly spaced)
   |
   v
discover(ids, cache_path=discovery_cache.json)
   |   pass 1: id_list=[base ids]     -> title, authors, venue, n_versions, v1 date
   |   pass 2: id_list=[v2..vN only]  -> updated date for v2..vN
   |   cache saved after every batch  -> reruns resume
   v
for each batch of --batch-size (default 25):
   |-- executor.submit(_fetch_reference_batch, need)   <- S2 runs concurrently
   |
   |-- for aid in group:                                <- serial, 3s apart
   |     write paper.json
   |     for v in 1..n:
   |        if v{N}/metadata.json exists: skip (resume)
   |        data = m1_download.fetch_source(session, aid, v)
   |        status, reason, files, archive, before, after =
   |            m1_download.save_source_with_stats(data, vdir)
   |        write v{N}/metadata.json (atomic)
   |
   |-- batch_data = reference_future.result()           <- join
   |     write refs_raw.json, refs.json
   |     write v{N}/references.json for every version
   |
   |-- checkpoint()  (session_NNN.json, max once/minute)
   v
m1_tables.build_all()                    -- papers.parquet + references.parquet
   |
   v
m1_aggregate.aggregate(corpus)           -- benchmark_m1_total.json + sessions.csv
```

**Why the two streams overlap.** arXiv's `/src/` endpoint forces ~3 s between requests; Semantic Scholar has its own independent limit. Running them sequentially wastes half the available throughput. `crawl_batch` submits the reference batch to a `ThreadPoolExecutor(max_workers=1)` *before* the serial source loop and joins it afterwards, so S2 latency hides inside arXiv waiting time.

**Why only one worker.** More reference workers buy nothing — arXiv's 3 s floor is the bottleneck — and add failure modes. `test_crawl_batch_overlaps_reference_and_source_work` asserts the overlap actually occurs. **That test currently fails** — see §7.8.

---

## 4. Function Reference

### 4.1 ID input: `read_ids` / `_parse_range` / `select_ids`

`read_ids(path, month_last=None) -> List[str]` accepts, per line:

| Form | Example |
|---|---|
| plain new ID | `2602.04870` |
| plain old ID | `hep-th/9901001` |
| dotted range | `2602.04870-2602.09869` |
| compact range | `260204870-260209869` |
| `arXiv:` prefix, `vN` suffix, full URL | `arXiv:2602.04870v2`, `https://arxiv.org/abs/2602.04870` |

Parsing tolerates a UTF-8 BOM (`encoding="utf-8-sig"`), CRLF, blank lines, and `#`
comments. Invalid lines are logged and skipped rather than aborting the run.

A range that crosses a month boundary needs `--month-last`:

```bash
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075
```

`2512=25075` means "December 2025 ends at 25075", which lets
`C.iter_id_range()` compute the intermediate last-ID for each month.

`select_ids(ids, limit, sample)`:

- `--limit N` → `ids[:N]`
- `--sample N` → N IDs evenly spaced across the whole list (`step = len(ids)/N`). Prefer this over `--limit` for representative dry runs — the first N IDs of a range are all from one month.

### 4.2 `run_m1.discover(ids, batch=50, cache_path=None) -> (info, n_cached)`

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

**Pass 2** builds `[f"{b}v{v}" for b in info for v in 2..n]` — **only v2 onward**, since v1's date already came from `published`. This halves the request count versus the old implementation. It fills `version_dates[v] = r.updated.isoformat()`.

`_query()` wraps `arxiv.Client.results()` and retries up to 5 times, with `30 * attempt` backoff on HTTP 429 and `10 * attempt` otherwise; HTTP 400 is never retried because it means the request itself is bad.

`_query_isolating()` is the important addition: when arXiv returns HTTP 400 for a batch (one poisoned ID in the group), it bisects the list and recurses, so a single bad ID is logged and skipped instead of losing the other 49 results.

The client is `arxiv.Client(page_size=100, delay_seconds=3.0, num_retries=3)`.

**Caching.** `discovery_cache.json` stores `{"info": {...}, "not_found": [...]}`. It is rewritten after every batch, so an interrupted run resumes rather than restarting discovery. `--no-discovery-cache` disables it.

> **Known gap.** `version_dates` can be incomplete — `crawl_batch` logs `WARNING: thiếu ngày của N version` rather than failing. The statement wants submission and revision dates per version, so check this warning count before publishing `papers.revised_dates`.

### 4.3 `run_m1.crawl_batch(group, info, rec, corpus, session, coverage, force_refs=False)`

Processes `--batch-size` papers per call (default 25). Per paper:

1. `pdir = corpus / folder_name(aid)`, and `folder_name()` is now literally `C.paper_key(arxiv_id)` — see §7.1.
2. Writes `pdir/paper.json` with `arxiv_id, title, authors, submission_date, revised_dates, venue, n_versions`.
3. For each `v` in `1..n`:
   - If `v{N}/metadata.json` exists, read `archive_bytes`/`bytes_before`/`bytes_after` from it, feed them to `rec.record_paper_size(...)`, and **skip the download**. This is the resume path.
   - Otherwise `fetch_source` → `save_source_with_stats` → `rec.record_paper_size(...)` → write `metadata.json` atomically (`.json.tmp` then `.replace`).
4. After both streams, per paper: write `refs_raw.json`, `refs.json`, and `v{N}/references.json` for every existing version directory.

`coverage` is mutated in place: `expected_versions`, `crawled_versions`, `failed_versions`, `not_found_papers`, `cached_versions`, `cached_refs_papers`, `discovery_cached_ids`.

**The `v{N}/references.json` write is duplicated data** — the same reference list once per version. It exists because `run_m2._load_references` falls back to `v*/references.json` when `refs.json` is missing at the paper root.

**Reference checkpointing.** `_refs_cached(aid)` gates the S2 call: a paper that already has `refs.json` is not re-fetched unless `--force-refs`. Failures go to `refs_failures.jsonl` via `m1_references.record_failure(...)` with `attempts`, `error`, and `kind`.

### 4.4 `m1_download.fetch_source(session, arxiv_id, version) -> bytes`

```python
url = C.ARXIV_SRC_URL.format(id_with_version=f"{arxiv_id}v{version}")
```

Constants (module level, not in `config`):

| Constant | Value | Meaning |
|---|---|---|
| `BASE_URL` | `https://export.arxiv.org/src` | never the main arxiv.org (403) |
| `MIN_DELAY_SECONDS` | `3.0` | enforced by `polite_wait()` before every request |
| `MAX_RETRIES` | `4` | attempts per version |
| `BACKOFF_SECONDS` | `30` | attempt *k* sleeps `30 * k` seconds |
| `RETRY_STATUS` | `{429, 500, 502, 503, 504}` | retryable HTTP codes |
| `MAX_FILE_BYTES` | `50 * 1024 * 1024` | individual members larger than this are skipped |
| `KEEP_SUFFIXES` | `{".tex", ".bib"}` | the only extensions extracted |

`polite_wait()` uses a module-level `_last_request_time` timestamp that is measured
**from the start of the previous request**, not from its end. This is
process-global state, so it serialises correctly as long as only one thread calls
it — which is why source downloads stay serial.

Raises `DownloadError` after `MAX_RETRIES`.

### 4.5 `m1_download.save_source_with_stats(data, out_dir)`

Returns `(status, reason, files, archive_bytes, before_bytes, after_bytes)`.

This is the function that implements "remove figures by extraction". It sniffs the payload four ways:

| Prefix | Handling | `status` |
|---|---|---|
| `%PDF` | not extractable | `"empty"`, `reason="pdf_only"` |
| tar (`r:*`) | `_collect_tex_bib` walks members, keeps `.tex`/`.bib` under `MAX_FILE_BYTES` | `"ok"` / `"empty"` |
| `\x1f\x8b` | single gzipped file; `_looks_like_tex` decides | `"ok"` / `"empty"` |
| else | written as `main.tex` if it looks like TeX | `"ok"` / `"empty"` |

`_safe_relative_path(name)` rejects absolute paths, `..` traversal, and anything
that escapes `out_dir` before writing. **This is the security boundary for
untrusted archive contents — do not weaken it.**

`before_bytes` is the *uncompressed* size of every file in the archive (figures
included); `after_bytes` is the sum of kept `.tex`/`.bib` sizes. That is exactly
the "before vs after removing figures" pair the report needs.

> **`save_source()` no longer exists.** The older 3-tuple variant was removed
> together with the standalone `__main__` block. `save_source_with_stats` is the
> only entry point.

### 4.6 `m1_references.fetch_batch_group(session, ids, attempts=MAX_RETRIES) -> list`

POSTs to `m1_references.BATCH_URL`. Key behaviour:

- **HTTP 413 → recursive split.** If S2 rejects the group as too large, the batch is halved and each half retried.
- **Circuit breaker.** `BREAKER_THRESHOLD = 3` consecutive rate-limit responses opens the breaker; `COOLDOWN_SECONDS = 300` before it closes. `_on_rate_limited()` / `_on_success()` maintain the counter.
- **`Retry-After` respected.** `_retry_after_seconds(response)` parses the header and sleeps that long rather than guessing.
- **`MAX_EXTRA_DELAY = 30.0`** caps the adaptive extra delay added on top of `S2_DELAY_SECONDS`.

Retry constants changed: `MAX_RETRIES = 5`, `BACKOFF_SECONDS = 15`,
`MAX_BACKOFF_SECONDS = 120`, `RETRY_STATUS = {429, 500, 502, 503, 504}`,
`REQUEST_TIMEOUT = 60`.

`DEFAULT_BATCH_SIZE` and `MAX_BATCH_SIZE` were removed. Batch size is now the
`--batch-size` argument on `run_m1.py` (default 25).

### 4.7 `m1_references.collect_published_rows() -> (rows, stats)`

Walks `data/*/refs.json`, normalises each reference with `normalize_reference()`,
and dedupes with `deduplicate_references()`.

The dedup identity is a three-level fallback, not a tuple of three fields:

```python
identity = (
    row.get("ref_s2_id")
    or row.get("ref_arxiv_id")
    or (
        str(row.get("ref_title") or "").strip().casefold(),
        tuple(str(a).strip().casefold() for a in row.get("ref_authors") or []),
        row.get("ref_submission_date"),
    )
)
```

Cheapest identifier first: S2 paper ID, then arXiv ID, then the
(title, authors, date) triple. Only rows with a truthy `ref_arxiv_id` are kept,
matching the statement's rule that only arXiv-carrying references become
`references` table rows.

Returns `(rows, stats)` where stats is:

```json
{
  "total_local_references": 13842,
  "references_with_arxiv_id": 4137,
  "references_without_arxiv_id": 9705,
  "published_rows": 4137,
  "duplicate_rows": 212
}
```

`collect_published_rows()` writes nothing. That separation is what lets
`m1_tables` export the same rows to both CSV (8 columns) and Parquet (6 columns).

### 4.8 `m1_references.write_published_csv(rows, stats)`

Writes `references.csv` (all 8 `PUBLISHED_REFERENCE_FIELDS`, with `ref_authors`
and `external_ids` JSON-encoded) plus `references_stats.json`. CSV is for eyeballing;
Parquet is the deliverable.

### 4.9 `m1_tables.build_all() -> dict`

New module. Builds both M1 tables:

```python
def build_all() -> dict:
    C.ensure_dir(C.DATA_DIR)
    n_papers = build_papers()
    ref_stats = build_references()
    return {"papers_rows": n_papers, **ref_stats}
```

`build_papers()` globs `*/paper.json`, projects each onto `PAPERS_SCHEMA.names`,
fills `authors`/`revised_dates` with `[]` when `None`, then `write_parquet(df,
"papers", C.papers_parquet_path())`. It also emits `papers.csv` with list columns
JSON-encoded (`_csv_copy`) because CSV has no list type.

`build_references()` calls `collect_published_rows()`, writes the CSV, then
projects onto `REFERENCES_SCHEMA.names` (6 columns) for Parquet. The extra
`ref_venue` and `external_ids` columns survive only in the CSV.

Run it standalone:

```bash
python src/crawler/m1_tables.py --data-dir data
```

Skip it during a crawl with `--skip-tables`.

### 4.10 `m1_aggregate.aggregate(data_dir=None) -> dict`

New module. Merges every `benchmark/session_NNN.json` into:

| Output | Contents |
|---|---|
| `benchmark/benchmark_m1_total.json` | summed wall time, peak RAM, peak disk, per-session list, distinct environments |
| `benchmark/sessions.csv` | one row per session, for the report table |

`run_m1.main()` calls it on both the success path and the `BaseException` path,
so an interrupted Colab session still produces a total.

The reason this exists: a full 40k-ID crawl needs 3–4 Colab sessions (see
[07 Testing and Performance](07_testing_and_performance.md)). Each session writes
its own `session_NNN.json`; **never delete those files**, because the aggregate is
rebuilt from them.

### 4.11 Multi-session checkpointing in `main()`

`_next_session(bench_dir)` picks the next free `session_NNN.json` index. Then:

```python
def checkpoint(force=False):
    if force or time.monotonic() - last_checkpoint >= 60:
        rec.checkpoint(session_path, extra=session_extra("running"))
        if args.backup_dir:
            backup_benchmark(corpus, args.backup_dir)
        last_checkpoint = time.monotonic()
```

`rec.checkpoint()` is a new `PerfRecorder` method that writes the current numbers
mid-run without stopping the recorder. `status` is one of:

| `status` | Meaning |
|---|---|
| `running` | written by an in-flight checkpoint, or left behind by a killed process |
| `interrupted` | written by the `except BaseException` handler (Ctrl+C, OOM) |
| `completed` | written by `rec.save()` at the end |

`--backup-dir` copies each finished batch plus the benchmark logs to, e.g., Google
Drive, so a Colab disconnect does not lose the session.

---

## 5. Configuration

Everything lives in `src/utils/config.py`:

| Name | Value | Note |
|---|---|---|
| `ARXIV_SRC_URL` | `https://export.arxiv.org/src/{id_with_version}` | export mirror only |
| `ARXIV_DELAY_SECONDS` | `3.0` | arXiv minimum |
| `ARXIV_API_BATCH` | `100` | page size for `id_list` queries |
| `CONTACT_EMAIL` | `$CONTACT_EMAIL` or `your-email@example.com` | `run_m1` warns if still default |
| `USER_AGENT` | `arxiv-course-project/1.0 (student crawler; contact: {CONTACT_EMAIL})` | |
| `S2_API_URL` | `https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}` | single-paper endpoint |
| `S2_DELAY_SECONDS` | `1.1` with key, `3.2` without | |
| `DATA_DIR` | `$ARXIV_DATA_DIR` or `<repo>/data` | overridden by `--corpus-dir` |

`S2_API_KEY` is read from the environment, then from Colab `userdata`. Without it
you get roughly 100 requests per 5 minutes and frequent 429s.

### The `S2_API_KEY` speedup

```bash
# .env at repo root, or export in the shell
export CONTACT_EMAIL=you@student.hcmus.edu.vn
export S2_API_KEY=xxxxxxxxxxxxxxxx
```

With a key, `S2_DELAY_SECONDS` drops from 3.2 to 1.1 — roughly a 3× speedup on the
reference half of the pipeline. On a 40k-ID crawl that is the difference between
one and three extra sessions.

---

## 6. Running It

### Integrated (the one that matters)

```bash
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 \
    --corpus-dir /content/corpus --backup-dir /content/drive/MyDrive/arxiv_backup
```

Dry run on 50 evenly-spaced IDs:

```bash
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 \
    --sample 50 --corpus-dir /content/data_sample
```

Force a re-crawl of references only:

```bash
python src/run_m1.py --ids-file ID.txt --force-refs --corpus-dir /content/corpus
```

Tables only, no crawling (already have the tree):

```bash
python src/run_m1.py --ids-file ID.txt --skip-tables   # skip table build
python src/crawler/m1_tables.py --data-dir data        # build them separately
```

Full argument list:

| Flag | Purpose |
|---|---|
| `--ids-file` | **required**; one ID or one range per line |
| `--month-last YYMM=N` | last ID of each month, for ranges crossing months |
| `--limit N` | first N IDs |
| `--sample N` | N evenly-spaced IDs |
| `--corpus-dir` | becomes `DATA_DIR` |
| `--bench-dir` | default `<corpus-dir>/benchmark` |
| `--user-agent` | override |
| `--batch-size` | papers per batch (default 25) |
| `--no-discovery-cache` | disable `discovery_cache.json` |
| `--force-refs` | re-query S2 even when `refs.json` exists |
| `--backup-dir` | copy batches + logs to Drive |
| `--skip-tables` | do not build `papers`/`references` Parquet |

### Merging sessions afterwards

```bash
python src/crawler/m1_aggregate.py --data-dir data
```

---

## 7. Known Issues

### 7.1 ~~Folder-name mismatch breaks old-style arXiv IDs~~ — fixed

`run_m1.folder_name()` is now `return C.paper_key(arxiv_id)`. The two divergent
implementations (`/` → `_` in `run_m1` vs `/` → `-` in `config`) are gone, so
`hep-th/9901001` resolves to `hep-th-9901001` in every module. **This section is
kept only so older reports can be corrected.**

### 7.2 ~~`build_references_table()` is a no-op~~ — removed

`run_m1.build_references_table()` was deleted along with the legacy
`crawl_references()` path. `m1_tables.build_references()` is the real
implementation and it writes Parquet.

### 7.3 ~~`references.parquet` is never written~~ — fixed

`m1_tables.build_references()` writes `C.DATA_DIR / "references.parquet"` via
`write_parquet(df, "references", ...)` with exactly the 6 columns of
`REFERENCES_SCHEMA`. `papers.parquet` likewise now goes through
`PAPERS_SCHEMA` instead of whatever columns the metadata crawl happened to
produce.

### 7.4 `papers.parquet` has no `n_versions` column

`paper.json` carries seven keys including `n_versions`, but `PAPERS_SCHEMA` has
six fields and does not include it:

```python
PAPERS_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("title", pa.string(), nullable=True),
    pa.field("authors", pa.list_(pa.string()), nullable=True),
    pa.field("submission_date", pa.string(), nullable=True),
    pa.field("revised_dates", pa.list_(pa.string()), nullable=True),
    pa.field("venue", pa.string(), nullable=True),
])
```

`build_papers()` builds `row = {name: data.get(name) for name in
PAPERS_SCHEMA.names}`, so `n_versions` is silently dropped. If the report needs
"papers with more than one version", compute it from `revised_dates` or keep
reading `paper.json`. **Do not widen the schema** — it is fixed by the statement.

### 7.5 `ResourceMonitor` disk scan can silently estimate

In `utils/benchmark.py`, if the baseline directory scan takes longer than
`max_scan_s=10.0`, the monitor stops rescanning and sets `disk_estimated: true`
in the summary. Check that flag before quoting peak-disk numbers for a corpus
with many small files.

### 7.6 `refs_raw.json` is much larger than `refs.json`

`write_raw_refs` stores the entire Semantic Scholar response envelope including
`retrieved_at` and `endpoint`. On 40k papers this dominates disk usage. The
statement asks you to keep all crawled references, so it is intentional — but
budget for it when planning Colab disk, and mention it in the size table.

### 7.7 `--force-refs` costs real quota

`--force-refs` bypasses the `_refs_cached` gate and re-queries every paper. On a
full corpus without an API key this is hours of waiting and a near-guaranteed
breaker trip. Use `--ids`-style targeting instead where possible.

### 7.8 Four M1/M2 tests now fail (**new**)

`test_run_m1_downloader.py` (4 tests) fails against the rewritten `run_m1.py`:

| Test | Failure |
|---|---|
| `test_run_m1_uses_canonical_downloader_and_checkpoints_versions` | asserts a call signature `crawl_batch` no longer takes |
| `test_crawl_batch_overlaps_reference_and_source_work` | uses a `threading.Event` handshake the new batch loop does not tick |
| `test_standalone_process_writes_atomic_stats_checkpoint` | `monkeypatch.setattr(m1_download, "version_dir", ...)` — `version_dir` no longer exists in that module |

Plus `test_m2_text.py::test_clean_figure_keeps_structure_drops_placement`, which
expects `\caption{A plot.}` and a preserved `\includegraphics` option, but the new
`clean_figure` strips `\label` and mangles `width=\linewidth` into `width= `.

Details and the full broken-test inventory are in
[07 Testing and Performance](07_testing_and_performance.md) §1.4.

### 7.9 The collaborator changelog describes files that do not exist

`docs/CHANGELOG_M1_M2.md` §2.2 documents a two-layer LaTeX architecture whose
Tầng 1 module is `src/parser/m2_macro_expansion.py`, and §2.3 documents
`src/parser/export_unknown_commands.py`. **Neither file exists in any branch** —
`git log --all` has no record of them.

`m2_hierarchy.py` line 446 calls `ME.preprocess_version_text(full_text)`, where
`ME` is that missing module, so **`m2_hierarchy` cannot be imported at all**:

```
ModuleNotFoundError: No module named 'm2_macro_expansion'
```

The macro-expansion logic itself does exist — `m2_cleanup.collect_macros()` and
`m2_cleanup.expand_user_macros()` — so the fix is to repoint the import rather
than write a new module. See [04 M2 Parsing](04_m2_parsing.md) §7.1.

### 7.10 The `unknown_commands` counter never resets between runs

`m2_cleanup.unknown_commands` is a module-level `Counter()`. `run_m2` writes it to
`benchmark/unknown_commands.json` at the end of each run but never calls
`reset_unknown_commands()`, so a second run in the same process accumulates.
Not a correctness bug for a fresh `python src/run_m2.py`, but a trap for anyone
importing `run_m2.run()` twice from a script.

---

## 8. Error Handling

| Failure | Behaviour |
|---|---|
| arXiv HTTP 400 on a batch | `_query_isolating` bisects the group; the offending ID is logged and skipped |
| arXiv HTTP 429 | `_query` retries 5× with `30 * attempt` backoff |
| arXiv HTTP 5xx | `_query` retries 5× with `10 * attempt` backoff |
| Source download fails 4× | `DownloadError`; version counted in `failed_versions`; other versions of the same paper still proceed |
| PDF-only version | recorded with `status="empty"`, `reason="pdf_only"`, `bytes_after=0`; not an error |
| S2 group too large (413) | batch halved recursively |
| S2 429 × 3 consecutive | circuit breaker opens for 300 s |
| S2 paper missing / no references | `record_failure(...)` to `refs_failures.jsonl`; retried next run |
| Interrupted run (Ctrl+C / OOM) | `except BaseException` writes `status="interrupted"`, runs `m1_aggregate`, re-raises |
| Corrupt `discovery_cache.json` | `_load_cache` swallows and starts fresh |
| Invalid line in `ID.txt` | logged, skipped, run continues |
| Invalid ID range | `SystemExit` with the line number and the raw value |

---

## 9. Team Review

- **Author:** P2 (M1 references + integrated runner)
- **Reviewers:** P1 (downloader), P4 (benchmark)
- **Design decisions:**
  1. Discovery caching is per-batch, not per-run, because Colab sessions die without warning.
  2. `_query_isolating` exists because one malformed ID otherwise destroys a 50-ID batch.
  3. Reference crawl runs concurrently with the source loop but on one thread — arXiv's 3 s floor dominates.
  4. Parquet building is a separate module so it can be re-run without re-crawling.
- **Open questions:** Should `refs_raw.json` be pruned after `refs.json` is validated (see §7.6)? The statement says keep everything; the disk budget says otherwise.
