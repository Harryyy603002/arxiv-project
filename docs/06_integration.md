# Integration — How the Milestones Connect

## 1. The Big Picture

The three milestones are not independent programs. They pass data through the filesystem, in a fixed order:

```
IDs.txt
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ M1                                                      │
│  m1_metadata.py  → meta.json, versions.json             │
│  m1_download.py  → v{N}/*.tex, v{N}/*.bib, metadata.json│
│  m1_references.py→ refs.json, refs_raw.json             │
└─────────────────────────────────────────────────────────┘
  │                           │
  ▼                           ▼
┌──────────────────────┐  ┌───────────────────────────────┐
│ M2 (LaTeX branch)    │  │ M2 (BibTeX branch)            │
│  m2_gather           │  │  m2_bibitem                   │
│  m2_hierarchy        │  │  m2_bib_dedup                 │
│  m2_content_dedup    │  │  m3_candidates                │
│  → elements.json     │  │  → bibtex_merged.json         │
│  → hierarchy.json    │  │  → candidates.json            │
└──────────────────────┘  └───────────────────────────────┘
                              │              ▲
                              │              │ refs.json (from M1)
                              ▼              │
                        ┌─────────────────────────────────┐
                        │ M3                              │
                        │  m3_auto_labeler (skeleton)     │
                        │  m3_*_features   (skeleton)     │
                        │  m3_model        (skeleton)     │
                        │  m3_evaluate     (skeleton)     │
                        │  → predictions.parquet          │
                        └─────────────────────────────────┘
```

---

## 2. Handoff Contracts

These are the only data dependencies between milestones. If you change one, you break the next.

| From | File | To | What it must contain |
|------|------|----|--------------------|
| M1 metadata | `data/<key>/versions.json` | M1 download, M2 dedup | `arxiv_id`, `latest_version`, `versions[]` with `version` and `date` |
| M1 download | `data/<key>/v{N}/*.tex` | M2 gather | `.tex` files with original include paths intact |
| M1 download | `data/<key>/v{N}/*.bib` | M2 bibitem | `.bib` files, unmodified |
| M1 download | `data/<key>/v{N}/metadata.json` | M1 resume, M2 status | `status`, `reason`, `bytes_before`, `bytes_after` |
| M1 references | `data/<key>/refs.json` | M2 candidates, M3 | `{"arxiv_id", "references": [...]}` with `ref_title`, `ref_authors`, `ref_arxiv_id`, `ref_s2_id` |
| M2 hierarchy | `data/<key>/v{N}/tree_raw.json` | M2 content dedup | `{"root": {"type", "children"}}` |
| M2 dedup | `data/<key>/elements.json` | M2 export, M3 context features | `element_id`, `arxiv_id`, `kind`, `content` |
| M2 dedup | `data/<key>/hierarchy.json` | M2 export, M3 context features | `arxiv_id`, `version`, `element_id`, `parent_id` |
| M2 bibtex | `data/<key>/bibtex_merged.json` | M2 export, M3 model | `citation_key`, `entry_type`, `title`, `author`, `year`, … |
| M2 candidates | `data/<key>/candidates.json` | M3 labeling, features, model | `candidate_id`, `citation_key`, `ref_arxiv_id`, `title_similarity`, `author_similarity`, … |

---

## 3. Running Order (End to End)

```bash
# ── M1 ────────────────────────────────────────────────────────
# 1a. Discover metadata and versions
python src/crawler/m1_metadata.py --ids-file ids.txt

# 1b. Download source (uses versions.json from 1a)
python src/crawler/m1_download.py --ids-file ids.txt

# 1c. Crawl references from Semantic Scholar
#     (m1_references has its own __main__ for standalone reference crawl)
python src/crawler/m1_references.py --ids-file ids.txt

# OR: do all three in one integrated run
python src/run_m1.py --ids-file ids.txt --corpus-dir data/corpus

# ── M2 ────────────────────────────────────────────────────────
# 2a. Build the LaTeX tree for each paper
python src/parser/m2_hierarchy.py --range <start>-<end>

# 2b. Clean, split sentences, dedup across versions
python src/parser/m2_content_dedup.py

# 2c. BibTeX dedup + citation rewriting + candidate generation
python -m run_m2

# 2d. Export Parquet tables
python src/parser/m2_export_parquet.py --out-dir data/export

# ── M3 ────────────────────────────────────────────────────────
python -m run_m3        # skeleton — raises NotImplementedError
```

---

## 4. Known Integration Problems

### 4.1 Two different folder-naming schemes

| Writer | Rule | Example for `hep-th/9901001` |
|--------|------|------------------------------|
| `config.paper_key()` | `/` → `-`, `.` → `-` | `hep-th-9901001` |
| `run_m1.folder_name()` | `/` → `_`, `.` → `-` | `hep-th_9901001` |

For new-style IDs (`2310.12345`) both produce `2310-12345`, so the problem only affects **old-style IDs** with a slash. If you run `run_m1.py` and then `m2_hierarchy.py`, M2 looks for `hep-th-9901001` and will not find the folder created as `hep-th_9901001`.

**Impact:** old-style papers silently get skipped by M2.
**Fix:** make `run_m1.folder_name` call `C.paper_key`:

```python
def folder_name(arxiv_id: str) -> str:
    return C.paper_key(arxiv_id)
```

---

### 4.2 `run_m2.py` mutates source files

`run_m2.process_paper` rewrites `\cite{}` keys **in place** inside `v{N}/*.tex`. This is fine the first time. It means:

- You cannot re-run `m2_hierarchy` afterwards and get the original citation keys back.
- If you run `run_m2` before `m2_hierarchy`, the tree contains rewritten keys (usually what you want).
- If you run `m2_hierarchy` twice around a `run_m2`, the two trees differ.

**Recommended order:** `m2_hierarchy` → `m2_content_dedup` → `run_m2`. This keeps the original keys in the tree and lets `cite_map.json` drive the renaming inside `m2_content_dedup` instead.

> **However:** nothing currently writes `cite_map.json`. `run_m2` writes `citation_key_replacements.json`, a different filename. `m2_content_dedup.cite_map_path()` looks for `cite_map.json`. **These do not match.**
>
> **Fix:** either rename the output in `run_m2.py`:
> ```python
> _atomic_json(paper_dir / "cite_map.json", replacements)
> ```
> or change `config.cite_map_path` to point at `citation_key_replacements.json`. The second is safer — it doesn't break the existing test that checks for `citation_key_replacements.json`.

---

### 4.3 `run_m2.py` does not run `m2_hierarchy`

There is no single M2 command. A student starting fresh will run `run_m2`, get `bibtex_merged.json` and `candidates.json`, and assume M2 is done — but `elements.json` and `hierarchy.json` will not exist, and the export step will produce empty tables.

**Fix (if you want one command):** add the missing calls at the top of `run_m2.run()`:

```python
from parser import m2_hierarchy, m2_content_dedup
for paper_dir in papers:
    m2_hierarchy.process_paper(paper_dir.name)      # writes tree_raw.json
    m2_content_dedup.process_paper(paper_dir.name)  # writes elements/hierarchy
```

Verify the function names before wiring this — `m2_hierarchy` exposes `process_paper(arxiv_id)` per its tests, and `m2_content_dedup` exposes `process_paper(arxiv_id)`.

---

### 4.4 `refs.json` location vs `run_m2` lookup

`run_m2._load_references(paper_dir)` reads `paper_dir / "refs.json"`. With the integrated `run_m1.py`, `m1_references.write_refs` writes to `C.refs_path(arxiv_id)` = `C.paper_dir(arxiv_id) / "refs.json"` = `data/<key>/refs.json`.

If you used `--corpus-dir data/corpus` on `run_m1`, then `_corpus_dir()` in `run_m2` will pick `data/corpus/` — and `.tex`/`.bib` live there, but `refs.json` lives in `data/<key>/`. **The two roots differ.**

**Check before running:** confirm where `refs.json` actually landed, and pass the right corpus dir. `_load_references` has a fallback to `v*/references.json`, which `run_m1.crawl_batch` does write, so this often works by accident.

---

### 4.4.1 `run_m2.run` silently skips papers with no `.bib`

The loop is:

```python
for paper_dir in sorted(path for path in root.iterdir() if path.is_dir()):
    if any(paper_dir.glob("v*/*.bib")):
        results.append({"paper": paper_dir.name, **process_paper(paper_dir)})
```

A paper only enters M2 if at least one `v{N}/*.bib` exists.

Because `m2_bibitem.parse_bibtex_entries` handles only standalone `.bib` files and not inline `\bibitem` inside a `.tex` `thebibliography` environment, any paper whose bibliography lives in the `.tex` file is **dropped entirely** — not counted as a failure, just absent. Older and single-file submissions are the ones most likely to be affected.

**Fix:** delete the `if any(...)` guard. `process_paper` already handles an empty entry list and will write a `m2_stats.json` row with zero counts, which is auditable. Fixing inline `\bibitem` parsing is a separate, larger change.

---

### 4.5 `PREDICTIONS_SCHEMA` does not match the statement

The statement requires:

```
arxiv_id, partition, citation_key, groundtruth_arxiv_id, cand_1..cand_5
```

`utils/schemas.py` currently defines:

```python
PREDICTIONS_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("citation_key", pa.string(), nullable=False),
    pa.field("groundtruth_arxiv_id", pa.string(), nullable=True),
    pa.field("predicted_arxiv_ids", pa.list_(pa.string()), nullable=False),
])
```

Missing `partition`; `predicted_arxiv_ids` must become five separate columns. Fix this before M3 export or the Hugging Face dataset will be rejected on schema.

---

### 4.6 `references.parquet` has no writer, and `papers.parquet` uses the wrong schema

Two separate problems.

**`papers.parquet` exists but is out of spec.** `m1_metadata.build_papers_table()` writes both `data/papers.csv` and `data/papers.parquet` by calling `df.to_parquet(...)` directly — it never goes through `to_arrow_table`, so `PAPERS_SCHEMA` is not applied. The DataFrame is built with `.reindex(columns=META_FIELDS)`, which does not line up with the schema:

| | `META_FIELDS` (what is written) | `PAPERS_SCHEMA` (what the statement wants) |
|---|---|---|
| extra columns | `abstract`, `latest_update`, `latest_version`, `primary_category`, `categories`, `journal_ref`, `doi`, `comment` | — |
| missing columns | — | `revised_dates`, `venue` |

The file loads, so nothing crashes; it just won't validate against `PAPERS_SCHEMA` and the HF upload will reject it. Fix: build the row with `revised_dates` (a list, from `versions.json`) and `venue` (from `journal_ref`), then project through the schema instead of writing raw:

```python
from utils.schemas import write_parquet
write_parquet(df, "papers", C.papers_parquet_path())
```

`to_arrow_table` reindexes to the schema's column names and fills absent ones with null, so the extra `META_FIELDS` columns drop out cleanly. `authors` and `revised_dates` are list columns, so they need the `object`-dtype path that `to_arrow_table` already handles.

**`references.parquet` is not written at all.** `m1_references.build_published_references()` produces exactly the right rows — it walks `data/*/refs.json`, normalises each reference via `normalize_reference()`, dedupes with `deduplicate_references()`, keeps only rows where `ref_arxiv_id` is truthy, and validates them with `validate_reference_rows()`. But it then writes **CSV** to `C.references_csv_path()`, plus `data/references_stats.json`. There is no `pq.write_table` call anywhere in that path, and `REFERENCES_SCHEMA` in `utils/schemas.py` is never consumed.

Fix is two lines inside `build_published_references()`, after `validate_reference_rows(rows)`. The module already imports from `utils.schemas`, so only the names change:

```python
import pandas as pd
from utils.schemas import write_parquet
write_parquet(pd.DataFrame(rows), "references", C.DATA_DIR / "references.parquet")
```

`write_parquet` → `to_arrow_table` reindexes to `REFERENCES_SCHEMA.names`, so the two extra keys that `PUBLISHED_REFERENCE_FIELDS` carries but the schema does not — `ref_venue` and `external_ids` — are silently dropped. That is the intended behaviour for the published table (the schema is the contract), but it does mean `ref_venue` survives only in the CSV. Keep the CSV for that reason; it is also what `references_stats.json` describes and what is easiest to eyeball.

`export_m2_parquet` is not the place to fix either of these; it only ever handles the three M2 tables.

---

## 5. Shared Conventions

**IDs.** Always store the raw base ID (`2310.12345`) inside JSON. Only use `paper_key` for folder names.

**Version label.** Folder is `v1`, `v2`, … (string). `hierarchy.json` stores `version` as an integer. `versions.json` stores `"v1"` strings. `schemas.to_arrow_table` handles both via `_parse_version`.

**Atomic writes.** Every writer uses `.tmp` → `.replace()`. Do not `open(path, "w")` directly for anything a later stage reads.

**Determinism.** `deduplicate_keys` sorts each identity group by `(citation_key, version)` before choosing a canonical key. `find_root_file` has a fully ordered tie-break chain. Re-running produces identical output.

---

## 6. Team Review

- **Author:** P4 (integration), reviewed by all
- **Design decision:** filesystem as the only interface — no database, no in-memory handoff. This lets each person work on their module without running the others.
- **Decision to confirm:** should `run_m2.py` become the single M2 command that also invokes `m2_hierarchy` and `m2_content_dedup`? Currently no.
- **Blocking issue for M3:** `PREDICTIONS_SCHEMA` must be updated before M3 predictions can be exported.