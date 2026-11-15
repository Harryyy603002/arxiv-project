# Integration — How the Milestones Connect

> **Re-verified against `01b5bb0`.** Three of the six "known integration
> problems" this document used to list are now **fixed**, two are still open,
> and two new ones appeared. The fixed list is preserved in §5 so you can see
> what the rework actually bought.

The milestones are not independent programs. They pass data through the
filesystem in a fixed order, and the only interface is a set of files.

---

## 1. The Big Picture

```
ids.txt (or a range)
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ M1  —  python src/run_m1.py --ids-file ids.txt --corpus-dir data  │
│                                                                   │
│   discover()          → discovery_cache.json                     │
│   crawl_batch()       → paper.json                               │
│                         v{N}/metadata.json   (checkpoint)        │
│                         v{N}/*.tex, *.bib    (via m1_download)   │
│   m1_references       → refs.json, refs_raw.json                 │
│                         v{N}/references.json                     │
│   m1_tables.build_all → papers.parquet, references.parquet       │
│   m1_aggregate        → benchmark/benchmark_m1_total.json        │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ M2  —  python -m run_m2 [--steps bib,tree,content] [--export]    │
│                                                                   │
│   step "bib"      run_m2.process_paper                            │
│                     → bibtex_merged.json, candidates.json,       │
│                       cite_map.json, m2_stats.json, …            │
│   step "tree"     m2_hierarchy.process_paper   ⚠ see §4.1         │
│                     → v{N}/tree_raw.json                          │
│   step "content"  m2_content_dedup.process_paper                  │
│                     → elements.json, hierarchy.json,              │
│                       parse_status.json                           │
│   --export        utils.schemas.export_m2_parquet                 │
│                     → export/{elements,hierarchy,bibtex}.parquet  │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│ M3  —  python -m run_m3          (still NotImplementedError)      │
│                                                                   │
│   m3_candidates.generate_candidates   ✔ implemented, used by M2   │
│   m3_auto_labeler / m3_*_features / m3_model / m3_evaluate        │
│                                        ✗ skeletons                │
│                     → predictions.parquet                         │
└──────────────────────────────────────────────────────────────────┘
```

**Key structural change:** M2 is now **one command with three ordered steps**
instead of four separate scripts. `ALL_STEPS = ("bib", "tree", "content")`.

---

## 2. Handoff Contracts

One row per real dependency. Full shapes are in
[02_data_contracts.md](./02_data_contracts.md).

| # | From | File | To | Status |
|---|------|------|----|--------|
| 1 | M1 discover | `discovery_cache.json` | M1 resume | ✅ |
| 2 | M1 crawl | `paper.json` | M2, `m1_tables` | ✅ |
| 3 | M1 crawl | `v{N}/metadata.json` | M1 resume, `m2_hierarchy.m1_source_reason` | ✅ |
| 4 | M1 crawl | `v{N}/*.tex`, `*.bib` | M2 `bib` + `tree` | ✅ |
| 5 | M1 refs | `refs.json` | M2 `bib`, `m1_tables` | ✅ |
| 6 | M1 refs | `v{N}/references.json` | `run_m2._load_references` fallback | ⚠ duplicate of #5 (§4.3) |
| 7 | M1 tables | `papers.parquet`, `references.parquet` | final dataset | ✅ |
| 8 | M2 `bib` | `cite_map.json` | M2 `content` | ✅ but ordering-sensitive (§4.4) |
| 9 | M2 `bib` | `bibtex_merged.json` | M2 export, M3 | ✅ |
| 10 | M2 `bib` | `candidates.json` | M3 | ✅ |
| 11 | M2 `tree` | `v{N}/tree_raw.json` | M2 `content` | 🔴 blocked (§4.1) |
| 12 | M2 `content` | `elements.json`, `hierarchy.json` | M2 export, M3 features | 🔴 blocked by #11 |
| 13 | M2 export | `export/*.parquet` | final dataset | 🔴 blocked by #11 |
| 14 | M3 | labels CSV | M3 | ❌ not built |
| 15 | M3 | `predictions` | final dataset | ❌ not built |

---

## 3. Running Order (End to End)

```bash
# ── M1 ───────────────────────────────────────────────────────────────
# The integrated run does discovery + download + references + tables.
python src/run_m1.py --ids-file ids.txt --corpus-dir data

# Useful variations
python src/run_m1.py --ids-file ids.txt --corpus-dir data --sample 50   # smoke test
python src/run_m1.py --ids-file ids.txt --corpus-dir data --force-refs  # re-fetch S2
python src/run_m1.py --ids-file ids.txt --corpus-dir data --skip-tables # skip Parquet

# Standalone: rebuild the M1 tables from an existing corpus
python src/crawler/m1_tables.py --data-dir ./data

# Standalone: merge multi-session Colab logs
python src/crawler/m1_aggregate.py --data-dir ./data

# ── M2 ───────────────────────────────────────────────────────────────
python -m run_m2 --corpus-dir data                 # all three steps
python -m run_m2 --corpus-dir data --steps bib     # works today
python -m run_m2 --corpus-dir data --steps content # needs "tree" output
python -m run_m2 --corpus-dir data --export        # + Parquet tables

# ── M3 ───────────────────────────────────────────────────────────────
python -m run_m3        # skeleton — raises NotImplementedError
```

> **`m1_download.py` and `m1_references.py` no longer have a `__main__`.**
> They are libraries called by `run_m1.py`. Running them directly does nothing.
> This is a change from the previous version of this document, which told you to
> invoke them as scripts.

> **`m1_metadata.py` and `m2_export_parquet.py` were deleted.** Do not look for
> them. Their jobs are now inside `run_m1.crawl_batch` and
> `run_m2 --export` respectively.

---

## 4. Known Integration Problems

### 4.1 🔴 The `tree` step cannot import

`m2_hierarchy.py` line 45:

```python
import m2_macro_expansion as ME
```

That module has **never existed in any branch**. Consequences ripple through
the whole pipeline:

| Command | Result |
|---|---|
| `python -m run_m2` (default) | `ModuleNotFoundError` before paper 1 |
| `python -m run_m2 --steps tree` | same |
| `python -m run_m2 --steps content` | runs, but every version reports `no_tree_raw` |
| `python -m run_m2 --steps bib` | ✅ works |
| `python -m run_m2 --export` | writes empty Parquet tables |
| `python src/parser/m2_hierarchy.py` | same `ModuleNotFoundError` |

**The workaround that keeps M2 usable:** run `--steps bib` now, and accept that
`elements.json` / `hierarchy.json` / the Parquet export are unavailable until
this is fixed.

**The fix:** `m2_hierarchy.py` line 46 already imports the real implementation:

```python
from m2_cleanup import expand_user_macros
```

So either delete line 45 and line 446 (`ME.preprocess_version_text`), or add a
small `m2_macro_expansion.py` that delegates to
`m2_cleanup.collect_macros` + `expand_user_macros`. The shim is the lower-risk
option because it preserves the intended two-layer design.

> `docs/CHANGELOG_M1_M2.md` describes `m2_macro_expansion.py` as committed. It
> is not. That changelog is the reason this defect went unnoticed for a while.

### 4.2 🔴 Seven test files fail at collection

`pytest src/tests` collects only 81 tests instead of ~150. The integration
consequence is that **CI cannot tell you whether a handoff broke**. See
[07_testing_and_performance.md](./07_testing_and_performance.md) §1.3.

### 4.3 🟠 `v{N}/references.json` is a redundant, differently-shaped copy

`run_m1.crawl_batch` writes the **same** reference list into every `v{N}/`
directory (lines 434–439). Two problems:

1. **Wasted space** — a 15-version paper stores 15 identical copies.
2. **Shape mismatch** — `refs.json` is `{"arxiv_id": ..., "references": [...]}`,
   but `v{N}/references.json` is a bare `[...]` with no per-row `arxiv_id`.

`run_m2._load_references` tolerates both, so nothing breaks today, but any new
consumer must know which file it is reading.

### 4.4 🟠 `--steps content` silently degrades without `--steps bib`

`m2_content_dedup.process_paper` reads `cite_map.json` to rename citations
**before** hashing content. If `bib` did not run, `cite_map.json` is missing,
`_read_json` returns `None`, `cite_map` becomes `{}`, and dedup still works —
but two versions that differ only by citation key will **not** merge, and
nothing warns you.

`run_m2`'s `ALL_STEPS` order protects the default run. The danger is a manual
`--steps content`.

**Fix:** log a warning in `m2_content_dedup` when `cite_map.json` is absent but
`bibtex_entries_raw.json` is present.

### 4.5 🟡 `run_m2` and `run_m1` disagree on the default corpus

| Script | Default |
|---|---|
| `run_m1.py` | `--corpus-dir` is **required** |
| `run_m2.run()` | `data/corpus/` if it exists, else `data/` |

`run_m2` has no `--corpus-dir` requirement, so a bare `python -m run_m2` picks
`data/` even if your M1 run used `data/corpus/`. Passing `--corpus-dir` to both
is the only safe convention.

> `run_m2.run()` does reassign `C.DATA_DIR` to the resolved root, so **within**
> one process all paths agree. The divergence is only at the CLI boundary.

### 4.6 🟡 `run_m2` catches every exception per paper

```python
except Exception as error:  # noqa: BLE001 - một bài lỗi không làm dừng cả lượt
    ok = False
    entry["error"] = f"{type(error).__name__}: {error}"
```

Good for a 40k-paper sweep — one bad paper cannot kill the run. Bad for
debugging: a systematic bug produces 40,000 identical `entry["error"]` strings
and the run still "succeeds".

**Watch:** `parse_success_rate` in `benchmark/m2_benchmark.json`. If it is
below ~0.95, read the `error` fields before trusting any table.

### 4.7 🟡 `predictions` schema cannot hold `partition` or `cand_1..5`

`PREDICTIONS_SCHEMA` has four columns and no `partition`. If the statement
requires a train/valid/test split, it must be stored elsewhere (or the schema
must change). See [02_data_contracts.md](./02_data_contracts.md) §4.2.

### 4.8 🟢 `run_m2` no longer mutates `.tex` files

Previously `process_paper` rewrote `\cite{}` keys in place. Now it only
**counts** them:

```python
citation_occurrences = sum(
    rewrite_citations_with_count(text, replacements)[1]
    for text in info["texts"].values())
```

`data/` stays pristine, `m2_hierarchy` can be re-run safely, and
`citation_keys_rewritten` in `m2_stats.json` is a *would-rename* figure.

### 4.9 🟢 `run_m2` now does call `m2_hierarchy` and `m2_content_dedup`

The old defect "`run_m2.py` never calls `m2_hierarchy`" is gone. `run()` has
explicit `if "tree" in steps` and `if "content" in steps` blocks. It would work
today were it not for §4.1.

### 4.10 🟢 `run_m2` no longer skips papers with no `.bib`

`collect_entries` falls back to every `.bib` in the version, and **also** parses
`\begin{thebibliography}` via `parse_bibitems`. A paper with neither produces
zero entries and still gets a `m2_stats.json` with zero counts — recorded, not
skipped.

---

## 5. What the rework fixed

Kept as a record so the diff is legible and nobody re-fixes these.

| Old defect | How it was fixed |
|---|---|
| Two folder-naming schemes (`_` vs `-`) | `run_m1.folder_name` now returns `C.paper_key(arxiv_id)` |
| `run_m2.py` mutates source files | replaced with `rewrite_citations_with_count(...)[1]` (count only) |
| `run_m2.py` never calls `m2_hierarchy` / `m2_content_dedup` | `ALL_STEPS` with explicit `tree` / `content` blocks |
| `run_m2.run` silently skips papers with no `.bib` | `.bib` fallback + `parse_bibitems` + zero-stats recording |
| `references.parquet` is never written | `m1_tables.build_references` writes it |
| `papers.parquet` uses the wrong schema | `PAPERS_SCHEMA` matches `paper.json` keys via `build_papers` |
| `bibitem` entries ignored | `m2_bibitem.parse_bibitems` handles ACM / ACL / IEEE |
| `m1_metadata` duplicated `crawl_batch` logic | module deleted, logic inlined |
| `m2_export_parquet.py` duplicated `utils.schemas` | module deleted, uses `export_m2_parquet` |
| No multi-session benchmark merge | `m1_aggregate.aggregate` |

---

## 6. Shared Conventions

Rules that every milestone must follow. Breaking one breaks a downstream stage.

1. **The filesystem is the interface.** No module imports another milestone's
   internals. M2 reads M1's JSON; it never imports `run_m1`.
2. **`utils/config.py` is the only path authority.** Except for the per-paper
   JSON names (`elements.json`, `cite_map.json`, …) which are literals inside
   `run_m2` / `m2_content_dedup`. If you add a file, add a path helper.
3. **`paper.json` is the source of truth for `arxiv_id`.** Never derive an ID
   from a folder name — `hep-th-9901001` cannot be un-mangled to
   `hep-th/9901001`.
4. **Atomic writes.** Every JSON writer does `write to .tmp` then
   `Path.replace`. A Colab kill must never leave a half file.
5. **A checkpoint is a file's existence.** `v{N}/metadata.json` means the
   version is done; `refs.json` **and** `refs_raw.json` both existing means the
   references are done; `m2_stats.json` **and** `elements.json` both existing
   means `--skip-existing` will skip the paper.
6. **One bad paper must not kill the run.** But it must be *recorded* — in
   `entry["error"]` or `refs_failures.jsonl`, never swallowed silently.

---

## 7. Team Review

- **Author:** P1 (M1 crawl, M2 gather/hierarchy), P2 (references, integration), P3 (M2 cleanup/content dedup), P4 (M2 BibTeX/export, M3)
- **Reviewer:** P1 ↔ P3, P2 ↔ P4
- **Design decision:** filesystem handoffs rather than a database. Cheap to
  resume, cheap to inspect, and a partial corpus is still usable. The cost is
  that shape mismatches (§4.3) are only caught at read time.
- **Design decision:** `run_m2` was collapsed from four scripts into one with
  `--steps`. Reproducibility improved; debuggability got worse while `tree` is
  broken — hence the escape hatch.
- **Open question:** should `--export` be the default? Today a plain
  `python -m run_m2` leaves no Parquet, so M3 has nothing to read even once
  `tree` works.
- **Open question:** should `run_m2` fail fast when `m2_hierarchy` cannot be
  imported, rather than logging 40,000 identical `error` entries? A cheap
  pre-flight `importlib.util.find_spec("parser.m2_macro_expansion")` would do
  it.
