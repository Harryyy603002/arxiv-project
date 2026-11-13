# Development Log

> **Audience.** A student joining the project mid-way, or an AI picking up where the last session stopped. This file answers "why is the code like this?" and "what is still broken?".
>
> **Rule.** Every claim here is tied to a commit, a file, or a test. If you cannot point at evidence, it goes in §6 (open decisions), not §3 (design decisions).

---

## 1. Timeline

The project ran on an 18-day schedule, 19/10/2026 → 05/11/2026, with the poster due 06/11. `main` currently has **83 commits**. Milestone work landed in two dense bursts.

| Date | What landed | Representative commits |
|---|---|---|
| **19/10** | Repo skeleton: `README`, `.gitignore`, `requirements.txt`, `src/utils/config.py` with the path conventions | `d852db5`, `438a4c1`, `90cb9fa`, `eeb2c8d`, `90867e4` |
| **20–21/10** | M1/M2/M3 skeleton files, initial `run` docs, first repo restructure, branch-per-person workflow begins | `ad55dd2`, `7092f4b` (#1), `30d29aa`, `1e96419` (#2) |
| **22/10** | M1 metadata crawler (P3), M1 source downloader with checkpointing (P1) | `205f714` (#3), `fdf9c2d`, `5abebb8` (#4) |
| **23/10** | M1 package fixes, Semantic Scholar reference crawler (P2), `run_m1.py`, `pytest.ini`, first benchmark | `6e42c4f`, `f97998a`, `38aaeb2` (#5), `c4df4ef`, `0e7206c`, `e6cbf76` |
| **24–28/10** | `m2_gather` root detection + include collection, with tests (P1) | `af57163`, `36b184c`, `b639c6f`, `3c555fc` |
| **29/10–04/11** | M1 hardening: non-blocking Drive scan, 429 handling, batched reference crawl, thread overlap, downloader perf | `873d09c` (#9), `2d0a2ae`, `865abf9`, `cd07e42`, `3d84267` |
| **05/11** | The big M1+M2 completion push — 18 commits in one day: raw reference provenance, published reference export, bibtex merge, hierarchy tree, schema validation, checklist audit | `97c5ebc`, `5cac703`, `a4923d2`, `af8b9f4`, `80f86a4` (#13) |
| **08–09/11** | M2 Parquet export (P4), M2 hierarchy branch merge (P1), M2 text processing merge (P3), first docs commit | `fe3f9ac`, `2401680`, `fe8e351` (#16), `b2fdf9e` (#11), `a028b70` (#15), `cf5750d` |
| **10/11** | Revert of an accidental merge | `a64e709` |

**Why the dates matter.** The official submission deadline was 06/11, and M2 work continued to 10/11. If you are auditing, the commits after 05/11 are post-deadline M2 consolidation, not M1/M2 scope creep.

**Branch discipline.** Every member worked on `pN/m1-*` and `pN/m2-*` branches and merged via PR. `git branch -a` shows 20+ branches, including several stale ones (`p2/m1-completion`, `p2/m1-patch`, `p2/m2-completion`, `feature/m1-downloader-code-only`). Anything on a stale branch is **not** in `main` — check before assuming a feature exists.

---

## 2. Milestone Status

| Milestone | State | Evidence |
|---|---|---|
| **M1** | Functionally complete. Metadata, versioned source download, reference crawl, published-reference export all exist and are tested. | 18 tests across `test_m1_metadata.py`, `test_m1_references.py`, `test_run_m1_downloader.py` |
| **M2** | Complete for gather / hierarchy / text / bibtex / candidates / Parquet export. **Orchestration is incomplete** — see §4.3. | 132 tests across five `test_m2_*.py` files |
| **M3** | Only `m3_candidates.py` exists. Cleaning, labelling, features, modelling, evaluation are not started. | 0 tests; `m3_candidates` only exercised indirectly |

Test suite: `python -m pytest src/tests -q` → **150 passed**.

---

## 3. Design Decisions

Each entry: the decision, the reason, and where to look.

### 3.1 The filesystem is the integration layer

There is no database, no message queue, no shared in-memory object graph. Every stage writes JSON to `data/<paper_key>/...` and the next stage reads it. `src/utils/config.py` is the single source of truth for those paths.

**Why.** Four people working in parallel on one repo cannot coordinate a schema migration every day. A file either exists or it does not, which makes partial progress inspectable and resumable. It also means any stage can be re-run alone.

**Cost.** No transactional guarantees across stages; a half-written `refs.json` looks the same as a complete one. Mitigated by atomic writes (§3.2).

**Where.** `src/utils/config.py` (`meta_path`, `refs_path`, `elements_path`, `hierarchy_path`, `cite_map_path`, `parse_status_path`).

### 3.2 Atomic writes via temp-file rename

Writers do:

```python
temporary = output.with_suffix(output.suffix + ".tmp")
# ... write to temporary ...
temporary.replace(output)
```

**Why.** A crawl of 40k IDs runs for hours and *will* be interrupted — Colab disconnects, laptops sleep, quotas expire. A truncated JSON file is worse than no file, because the resume logic will read it and fail deep inside parsing instead of at the top.

**Where.** `m1_download.save_source_with_stats`, `m1_references` checkpoint writers, `m1_metadata`, `m1_references.build_published_references`.

### 3.3 Checkpoint-and-resume instead of "just run it again"

`run_m1` skips versions already on disk. `m1_references` keeps `refs_raw.json` as a provenance checkpoint and will refuse to resume from `refs.json` alone — `test_raw_checkpoint_is_required_for_resume` locks this in.

**Why.** The statement has no time limit but the crawl is dominated by a 3-second-per-request floor. Re-downloading 742 versions because the process died at version 700 is unacceptable.

**Where.** `run_m1.crawl_batch`, `m1_references._fetch_reference_batch`.

### 3.4 Rate limits are treated as physics, not obstacles

| Service | Constant | Value |
|---|---|---|
| arXiv `/src/` | `MIN_DELAY_SECONDS` | `3.0` |
| arXiv `/src/` | `MAX_RETRIES` / `BACKOFF_SECONDS` | `4` / `30` |
| Semantic Scholar | `S2_DELAY_SECONDS` | `1.1` with key, `3.2` without |
| Semantic Scholar batch | `DEFAULT_BATCH_SIZE` / `MAX_BATCH_SIZE` | `25` / `500` |
| Circuit breaker | `BREAKER_THRESHOLD` / `COOLDOWN_SECONDS` | `3` / `300` |

**Why.** The statement is explicit that `export.arxiv.org` must be used instead of `arxiv.org`, with a descriptive User-Agent and 3-second pacing, because aggressive crawling triggers robot detection and 403 blocks. Semantic Scholar is 1 req/s unauthenticated. Getting blocked mid-run costs far more than the delay.

**The 1.1 s with a key.** The key guarantees a steady 1 request/second rather than sharing the throttled unauthenticated pool — so the delay drops from 3.2 s to 1.1 s. That is roughly a **3× speedup on the reference crawl** for the cost of a free API key. If you are running a large crawl and have not set `S2_API_KEY`, that is the single highest-leverage change available.

**Where.** `src/crawler/m1_download.py`, `src/crawler/m1_references.py`, `src/utils/config.py`.

### 3.5 Bounded overlap: one reference worker alongside the serial source stream

The arXiv source endpoint forces serial access (3 s apart). The Semantic Scholar reference crawl is a *different* service with a *different* limit. `run_m1.crawl_batch` therefore runs one reference-batch future concurrently with the serial source loop, so S2 latency hides inside arXiv waiting time instead of adding to it.

**Why.** Two independent rate limits means two independent pipelines. Running them sequentially wastes half the available throughput.

**The bound is one worker, deliberately.** More workers buy nothing — the bottleneck is arXiv's 3 s floor, not S2 — and add failure modes. `test_crawl_batch_overlaps_reference_and_source_work` asserts the overlap actually happens.

**Where.** [run_m1.py](../src/run_m1.py) `crawl_batch`, `_process_reference_batch`.

### 3.6 Raw payloads are preserved; normalisation is a separate step

`refs_raw.json` holds the Semantic Scholar response as returned. `refs.json` holds normalised rows. `test_reference_ids_are_normalized_without_changing_raw_payload` locks the separation.

**Why.** If a normalisation bug is found later, you fix the code and re-derive `refs.json` from `refs_raw.json` — no re-crawl. Given the 3 s floor, a re-crawl of 40k IDs is ~20 hours. The raw checkpoint is the difference between a 1-minute fix and a 20-hour fix.

### 3.7 Deduplication is keyed on `(kind, content, occurrence_index_within_version)`

In `m2_content_dedup`, elements are deduplicated by content **within a version**, and the same sentence appearing twice in one version is kept as ambiguous rather than merged. `test_same_sentence_twice_in_one_version_stays_unambiguous` guards it.

**Why.** Merging two identical sentences that genuinely appear twice loses information; merging the same sentence across versions is the whole point. The occurrence index disambiguates the two cases.

### 3.8 The `0.35` similarity gate in M3 candidate generation

`m3_candidates.generate_candidates` keeps a candidate only if title similarity ≥ `0.35`.

**Why.** The matching pool is every arXiv-carrying reference in the corpus (thousands). Without a floor, every paper gets every reference as a candidate and the M3 ranker has nothing to rank. 0.35 is deliberately permissive — it is a recall gate, not a precision gate. Precision is the ranker's job.

**Cost.** If MRR@5 turns out to be dominated by recall failures (the true match never appears in the top 5 because it was filtered out), this constant is the first thing to lower. It is not yet tuned against a labelled set.

---

## 4. Known Defects

Ordered by how much damage they do. Each has a concrete fix in [06_integration.md](./06_integration.md) or the relevant milestone doc.

### 4.1 `PREDICTIONS_SCHEMA` does not match the statement

`src/utils/schemas.py` defines:

```python
pa.field("predicted_arxiv_ids", pa.list_(pa.string()), nullable=False),
```

with no `partition` column. The statement requires `arxiv_id, partition, citation_key, groundtruth_arxiv_id, cand_1 … cand_5` — one row per reference, five separate candidate columns, plus a `partition` column identifying train/valid/test.

**Impact.** Blocks M3 export entirely. The HF upload will be rejected on schema.

**Fix.** Rewrite `PREDICTIONS_SCHEMA`; update `validate_candidate_rows`; add a test asserting the schema has exactly those columns.

### 4.2 Folder-name mismatch breaks old-style arXiv IDs

Two functions build folder names differently:

| Function | Rule | `hep-th/9901001` becomes |
|---|---|---|
| `run_m1.folder_name` | `/`→`_`, `.`→`-` | `hep-th_9901001` |
| `config.paper_key` | `-` for both | `hep-th-9901001` |

New-style IDs (`2310.12345`) agree under both rules, so the bug is invisible until the assigned range includes pre-2007 papers. The statement explicitly warns that pre-2007 references use the old `archive/YYMMNNN` format and must be retained.

**Impact.** M2 silently skips those papers — no error, just missing data.

**Fix.** Make `run_m1.folder_name` delegate to `config.paper_key`. One line. Then add a test with `hep-th/9901001`.

### 4.3 `run_m2.py` never calls `m2_hierarchy` or `m2_content_dedup`

`run_m2.main` writes `bibtex_merged.json`, `candidates.json`, `m2_stats.json`. It does **not** call `m2_hierarchy` (so `elements.json` / `hierarchy.json` are never produced) and does not call `m2_content_dedup`.

**Impact.** A fresh student runs `run_m2.py`, gets two of four expected outputs, and `export_m2_parquet` writes empty `elements`/`hierarchy` tables. The 132 passing M2 tests do not catch this because they test the modules, not the orchestration.

**Fix.** Add the two calls to `run_m2.main` in the correct order: gather → hierarchy → text clean/split → content dedup → bibtex → candidates. Then add an integration test that runs `main()` on a two-paper fixture and asserts all four files exist.

### 4.4 `citation_key_replacements.json` vs `cite_map.json`

`run_m2.py` writes `citation_key_replacements.json`. `m2_content_dedup` reads `cite_map.json` (`config.cite_map_path()`). Different filenames.

**Impact.** The cite-map path is dead code. Content dedup, if it were called, would find no map.

**Fix.** Pick one name — `cite_map.json` matches the config helper, so change the writer.

### 4.5 `run_m2.py` mutates `.tex` files in place

`run_m2` rewrites `v{N}/*.tex` when replacing citation keys.

**Impact.** Original keys are unrecoverable after a run. Re-running produces different output than the first run. Any bug in key replacement permanently corrupts the corpus, and re-crawling costs hours.

**Fix.** Write cleaned copies to a `v{N}/clean/` subdirectory, or at minimum copy `v{N}/*.tex` → `v{N}/*.tex.orig` before rewriting.

### 4.6 `m2_bibitem.py` only parses `.bib` files

The statement's M2 scope is "convert `\bibitem` references into BibTeX". Inline `\bibitem{...}` inside a `.tex` `thebibliography` environment is **not** handled — only standalone `.bib` files are parsed.

**Impact.** Papers with inline bibliographies (common in older and single-file submissions) produce zero bibtex entries.

**Fix.** In `m2_gather`, capture the `thebibliography` environment before `m2_hierarchy` drops it (`SKIP_ENVS = {"thebibliography"}`), then add a parser branch for `\bibitem`.

### 4.7 `references.parquet` is never written; `papers.parquet` uses the wrong schema

`build_published_references()` produces correct rows and validates them, then writes CSV only. `m1_metadata.build_papers_table()` writes Parquet by calling `df.to_parquet(...)` directly, bypassing `PAPERS_SCHEMA` (missing `revised_dates` and `venue`; carrying eight extra columns).

**Impact.** Two of the six published tables are not in publishable form.

**Fix.** Both are one-line changes to route through `schemas.write_parquet`. Details in [06_integration.md](./06_integration.md) §4.6.

### 4.8 `run_m2._corpus_dir()` can diverge from where `refs.json` lives

`_corpus_dir()` prefers `data/corpus/` when it exists, but `refs.json` is written to `data/<key>/refs.json`. The two roots can diverge.

**Impact.** Masked in practice by a fallback to `v*/references.json`, which usually finds the data anyway. But if a run produces no bibtex, check this first.

**Fix.** Have `_corpus_dir()` derive from `config.DATA_DIR` and stop guessing.

### 4.9 `run_m2.run` silently skips papers with no `.bib` file

```python
for paper_dir in sorted(path for path in root.iterdir() if path.is_dir()):
    if any(paper_dir.glob("v*/*.bib")):
        results.append({"paper": paper_dir.name, **process_paper(paper_dir)})
```

A paper is only processed if at least one `v{N}/*.bib` exists.

**Impact.** Combined with §4.6 (inline `\bibitem` unsupported), every paper that keeps its bibliography inside the `.tex` file is **dropped from M2 entirely** — no `bibtex_merged.json`, no `candidates.json`, no `m2_stats.json` entry. The paper is not counted as a failure anywhere; it simply does not appear. See [06 Integration](./06_integration.md) §4.4.1.

**Fix.** Remove the `.bib` guard and let `process_paper` handle the zero-entry case (it already does). Then report papers with zero entries in `m2_stats.json` instead of hiding them.

---

## 5. Debugging Notes

Symptoms we already hit, so you do not rediscover them.

| Symptom | Cause | Where |
|---|---|---|
| Some papers have no `elements.json` after `run_m2` | `run_m2` does not call `m2_hierarchy` (§4.3) | `run_m2.py` |
| Pre-2007 papers silently missing from M2 | folder-name mismatch (§4.2) | `run_m1.folder_name` vs `config.paper_key` |
| `refs.json` present but crawl restarts | `refs_raw.json` deleted; resume requires the raw checkpoint by design | `m1_references` |
| `disk_estimated: true` in benchmark JSON | Colab Drive scan exceeded `max_scan_s = 10.0` | `benchmark.ResourceMonitor` |
| A batch of reference requests returns nothing | Circuit breaker tripped after `BREAKER_THRESHOLD = 3` rejections; waits `COOLDOWN_SECONDS = 300` | `m1_references` |
| 403 from arXiv | pointed at `arxiv.org` instead of `export.arxiv.org`, or `MIN_DELAY_SECONDS` lowered | `m1_download.BASE_URL` |
| 429 from Semantic Scholar | `S2_API_KEY` unset and running faster than the shared unauthenticated pool allows | `config.S2_DELAY_SECONDS` |
| Non-UTF-8 `.tex` crashes parse | handled — `test_non_utf8_file_does_not_crash` | `m2_gather` |
| Unbalanced `$` crashes parse | handled — `test_unbalanced_markers_do_not_crash` | `m2_cleanup` |
| `\input` cycle hangs the parser | handled — `test_collect_detects_cycles_without_infinite_loop` | `m2_gather.collect` |
| A "sentence" is half a sentence | `_BOUNDARY` regex; abbreviations/decimals/URLs are masked first, but a novel pattern can still break it | `m2_sentences` |

**Metadata transport failures are not recorded as nonexistent IDs.** If the arXiv API errors, the ID is skipped and retried on the next run; only a genuine "not found" goes to `failed_ids.txt`. Do not "clean up" `failed_ids.txt` — it is the record of IDs that truly do not exist.

**PDF-only versions are kept as empty records.** The statement requires this explicitly. `record_paper_size(is_pdf_only=True)` records the version without touching size averages.

---

## 6. Decisions Still Requiring Team Input

These are genuinely open. Do not guess.

1. **`paper.json` vs `meta.json`** — the original plan named `paper.json`; the code writes `meta.json`. Old docs and handoff notes still say `paper.json`. Pick one and purge the other from the docs.
2. **`tree_raw.json` vs `elements.json`/`hierarchy.json`** — `m2_hierarchy` writes `tree_raw.json`; the export reads `elements.json` and `hierarchy.json`. Clarify which stage is supposed to produce which.
3. **RapidFuzz vs `difflib.SequenceMatcher`** — `rapidfuzz` is in `src/requirements.txt`, but `m3_candidates` uses `difflib`. Either use RapidFuzz (much faster on thousands of pairs) or drop the dependency.
4. **M2 orchestration ownership** — who fixes §4.3? It is the single largest blocker for a fresh run.
5. **Schema ownership** — who owns `src/utils/schemas.py`? Both M2 (P4) and M3 need to edit `PREDICTIONS_SCHEMA`.
6. **Benchmark ownership** — P1 (M1) produces `benchmark_m1.json`; nobody produces `benchmark_m2.json`. See [07_testing_and_performance.md](./07_testing_and_performance.md) §3.
7. **Live results** — no benchmark output is committed (correctly, since `data/` is gitignored), but the Jupyter Book needs the actual numbers pasted in with run date and Colab tier.
8. **Root `requirements.txt`** — stale leftover (numpy/pandas/matplotlib/scipy/statsmodels/jupyter/seaborn/kaggle). The real one is `src/requirements.txt`. Delete the root file or replace it with a pointer.

---

## 7. Change Log Template

Append one row per substantive change. Keep "Evidence" to a commit hash or a test name.

| Date | Contributor | Change | Evidence | Review |
|---|---|---|---|---|
| 2026-10-19 | | Initial repo skeleton | `d852db5` | |
| 2026-10-22 | | M1 metadata crawler | `205f714` (#3) | |
| 2026-10-22 | | M1 source downloader + checkpoint | `fdf9c2d` (#4) | |
| 2026-10-23 | | Semantic Scholar reference crawler | `f97998a` (#5) | |
| 2026-10-29 | | M1 429 handling, batched refs, non-blocking disk scan | `873d09c` (#9) | |
| 2026-11-05 | | M1 raw provenance + published reference export | `8a19e46`, `5cac703` | |
| 2026-11-05 | | M2 hierarchy tree, bibtex merge, candidate stats | `6b5f530`, `97c5ebc`, `4e3fcfb` | |
| 2026-11-08 | | M2 Parquet export, schema alignment | `fe3f9ac`, `2401680` | |
| 2026-11-09 | | Technical documentation set | `cf5750d` | |
| | _your change_ | | | |

---

## 8. Review

- **Maintainer:** P4 (integration and schemas)
- **Last verified test run:** `python -m pytest src/tests -q` → **150 passed**
- **Last verified `main` commit:** `a64e709` (2026-11-10)
- **Known-red items:** none — the suite is green. The defects in §4 are all untested behaviour, not failing tests.