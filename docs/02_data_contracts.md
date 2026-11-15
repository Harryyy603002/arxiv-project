# Data Contracts

Every file that passes between modules, with the **actual** producer, the
**actual** consumer, and the **actual** shape taken from source.

> **Re-verified against `01b5bb0`** (the collaborator's M1/M2 rework).
> Two files this document used to describe — `data/<paper_key>/meta.json` and
> `data/<paper_key>/versions.json` — **are no longer written by anything**.
> `m1_metadata.py` was deleted; its job is now inside `run_m1.crawl_batch`.
> `utils/config.py` still has `meta_path()` and `versions_path()`, but only
> legacy tests and one compatibility fallback read those paths.
> See §7 "What changed" for the substitution table.

**Rule of thumb when reading this doc:** a contract is only real if you can
name the line that writes it. Every block below does.

---

## 1. The corpus layout on disk

One tree. `--corpus-dir` on both `run_m1` and `run_m2` points at the same root.

```
<corpus>/
├── discovery_cache.json            ← run_m1, arXiv API discovery cache
├── papers.parquet / papers.csv     ← m1_tables.build_papers
├── references.parquet / .csv       ← m1_tables.build_references
├── references_stats.json           ← m1_references.write_published_csv
├── refs_failures.jsonl             ← m1_references.record_failure (append-only)
├── m2_stats.json                   ← run_m2.run (array, one entry per paper)
├── 2602-04870/                     ← folder_name(arxiv_id) / paper_key(arxiv_id)
│   ├── paper.json                  ← run_m1.crawl_batch
│   ├── refs.json                   ← m1_references.write_refs
│   ├── refs_raw.json               ← m1_references.write_raw_refs
│   ├── bibtex_entries_raw.json     ← run_m2.process_paper
│   ├── cite_map.json               ← run_m2.process_paper
│   ├── bibtex_conflicts.json       ← run_m2.process_paper
│   ├── bibtex_merged.json          ← run_m2.process_paper
│   ├── candidates.json             ← run_m2.process_paper
│   ├── candidate_entry_index.json  ← run_m2.process_paper
│   ├── m2_stats.json               ← run_m2.process_paper
│   ├── elements.json               ← m2_content_dedup.process_paper
│   ├── hierarchy.json              ← m2_content_dedup.process_paper
│   ├── parse_status.json           ← m2_content_dedup.process_paper
│   ├── v1/
│   │   ├── metadata.json           ← run_m1.crawl_batch (per-version checkpoint)
│   │   ├── references.json         ← run_m1.crawl_batch (copy of refs.json)
│   │   ├── tree_raw.json           ← m2_hierarchy.process_paper
│   │   └── *.tex, *.bib            ← m1_download.save_source_with_stats
│   └── v2/ …
├── benchmark/
│   ├── session_001.json …          ← run_m1 (one per Colab session)
│   ├── benchmark_m1_total.json     ← m1_aggregate.aggregate
│   ├── sessions.csv                ← m1_aggregate.aggregate
│   ├── m2_benchmark.json           ← run_m2.run (PerfRecorder.save)
│   └── unknown_commands.json       ← run_m2.run (cleanup stats dump)
└── export/
    ├── elements.parquet            ← utils.schemas.export_m2_parquet
    ├── hierarchy.parquet
    └── bibtex.parquet
```

**Naming gotcha.** `paper_key("hep-th/9901001")` → `hep-th-9901001`;
`folder_name` in `run_m1` now delegates to `C.paper_key`, so the two agree.
`re.sub(r"[^0-9A-Za-z._-]+", "-", ...)` is the shared rule. The original ID
(including `/` and `.`) is preserved **inside** the JSON, so nothing is lost.

---

## 2. M1 — per-paper payloads

### 2.1 `<corpus>/<paper_key>/paper.json`

**Producer:** `run_m1.crawl_batch`, line 337. Written **before** the version
loop, so it exists even if every download fails.
**Consumers:** `m2_hierarchy.read_arxiv_id`, `m2_content_dedup.list_versions`,
`run_m2.paper_arxiv_id`, `m1_tables.build_papers`, `m2_gather.list_paper_dirs`.

```json
{
  "arxiv_id": "2310.12345",
  "title": "Attention Is All You Need",
  "authors": ["Ashish Vaswani", "Noam Shazeer"],
  "submission_date": "2017-06-12T17:00:00+00:00",
  "revised_dates": ["2023-08-02T17:00:00+00:00"],
  "venue": "Advances in Neural Information Processing Systems 30",
  "n_versions": 2
}
```

| Field | Source | Notes |
|---|---|---|
| `arxiv_id` | the requested ID | base ID, no `vN` |
| `title` | arXiv API `r.title`, whitespace-collapsed | |
| `authors` | `[a.name for a in r.authors]` | list, may be empty |
| `submission_date` | `version_dates[1]` = API `published` | ISO 8601, `null` if unknown |
| `revised_dates` | `[version_dates[v] for v in 2..n]` | **excludes v1**; may be shorter than `n_versions - 1` |
| `venue` | API `journal_ref` | `null` when the paper has no journal reference |
| `n_versions` | API `n` (defaults to 1) | `int` |

> **Why `n_versions` matters:** `m2_content_dedup.list_versions` reads it to
> include versions M1 *knows about* but could not download, so a PDF-only v3
> still shows up in `parse_status.json` as `"missing"` instead of vanishing.

> **Defect (recorded in doc 03 §7.4):** `papers.parquet` has **no**
> `n_versions` column (`PAPERS_SCHEMA` is 6 fields), so the Parquet copy loses
> this field. Only the JSON keeps it.

### 2.2 `<corpus>/<paper_key>/v{N}/metadata.json`

**Producer:** `run_m1.crawl_batch` lines 371–383.
**Consumer:** `run_m1.crawl_batch` itself (checkpoint), and
`m2_hierarchy.m1_source_reason` (reads only `reason`).

**This is the per-version checkpoint.** If it exists and parses, the version is
skipped entirely — no network call at all.

```json
{
  "arxiv_id": "2310.12345v1",
  "version": 1,
  "date": "2017-06-12T17:00:00+00:00",
  "title": "Attention Is All You Need",
  "authors": ["Ashish Vaswani", "Noam Shazeer"],
  "venue": null,
  "status": "ok",
  "reason": "",
  "files": ["main.tex", "sections/intro.tex", "refs.bib"],
  "archive_bytes": 1048576,
  "bytes_before": 2097152,
  "bytes_after": 32768
}
```

| Field | Meaning |
|---|---|
| `arxiv_id` | **with** the `vN` suffix — different from `paper.json` |
| `date` | `paper_info["version_dates"].get(v)`; `null` for versions the API did not date |
| `status` | `"ok"` (`.tex` present) or `"empty"` (archive had no TeX) |
| `reason` | why empty: `pdf_only`, `no_tex_in_archive`, `corrupt_archive`, `single_file_not_tex`, `unknown_format` |
| `files` | relative POSIX paths actually written |
| `archive_bytes` | size of the downloaded tarball |
| `bytes_before` / `bytes_after` | raw vs. kept bytes after `KEEP_SUFFIXES` filtering; `bytes_after == 0` means PDF-only |

**Write protocol:** `checkpoint.with_suffix(".json.tmp")` then
`Path.replace(checkpoint)` — atomic, so a Colab kill never leaves a truncated
file. If the cached read raises `KeyError`/`ValueError`/`JSONDecodeError`, the
`pass`-then-`else` structure means the code **falls through to re-download**.
That is deliberate.

### 2.3 `<corpus>/<paper_key>/refs.json`

**Producer:** `m1_references.write_refs(arxiv_id, refs)`.
**Consumers:** `run_m1._load_cached_refs` (skip check),
`run_m2._load_references`, `m1_references.collect_published_rows`.

```json
{
  "arxiv_id": "2310.12345",
  "references": [
    {
      "ref_arxiv_id": "1706.03762",
      "ref_title": "Attention Is All You Need",
      "ref_authors": ["Ashish Vaswani", "Noam Shazeer"],
      "ref_submission_date": "2017-06-12",
      "ref_venue": "NeurIPS",
      "ref_s2_id": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
      "external_ids": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222.3295349"}
    }
  ]
}
```

Exactly the 8 `PUBLISHED_REFERENCE_FIELDS`, **minus** `arxiv_id` — that key is
hoisted to the envelope and stripped per row by
`run_m1.normalize_references` (line 241–243 filters `key != "arxiv_id"`).

`m1_references.normalize_reference(citing_id, ref)` is the single normalizer;
it accepts **either** Semantic Scholar camelCase (`paperId`, `externalIds`,
`publicationDate`) **or** the already-normalized snake_case names, so it is
idempotent:

```python
"ref_arxiv_id":         ref.get("ref_arxiv_id") or external_ids.get("ArXiv")
"ref_title":            ref.get("ref_title") or ref.get("title")
"ref_submission_date":  ref.get("ref_submission_date") or ref.get("publicationDate")
                        or str(ref["year"])
"ref_s2_id":            ref.get("ref_s2_id") or ref.get("paperId")
"ref_venue":            ref.get("ref_venue") or ref.get("venue")
```

> **Note:** `refs.json` keeps **all** references, including those with
> `ref_arxiv_id: null`. Only the ones with an arXiv ID reach
> `references.parquet` **and** only those become M3 candidates
> (`run_m2.process_paper` line 176). The filter happens in two places on
> purpose: publishing completeness vs. candidate matrix size.

### 2.4 `<corpus>/<paper_key>/v{N}/references.json`

**Producer:** `run_m1.crawl_batch` lines 434–439.
**Consumer:** `run_m2._load_references` — the **fallback** when `refs.json` is
absent.

It is a bare JSON **list**, not an envelope:

```json
[{"ref_arxiv_id": "1706.03762", "ref_title": "...", "...": "..."}]
```

**It carries no `arxiv_id` per row.** `run_m2._load_references` handles both
shapes:

```python
payload = json.loads(path.read_text())
return payload.get("references", []) if isinstance(payload, dict) else payload
```

> **Redundancy:** the same list is written into **every** `v{N}/` directory.
> A paper with 8 versions stores 8 identical copies. Harmless but wasteful —
> see §7.3.

### 2.5 `<corpus>/<paper_key>/refs_raw.json`

**Producer:** `m1_references.write_raw_refs(arxiv_id, payload)`.
**Consumers:** humans, and `run_m1._refs_cached` as part of the "already done" test.

```json
{
  "arxiv_id": "2310.12345",
  "retrieved_at": "2026-10-20T08:01:00+00:00",
  "endpoint": "https://api.semanticscholar.org/graph/v1/paper/arXiv:2310.12345",
  "response": { "...raw S2 paper object with its references[]..." }
}
```

The `_refs_cached` predicate is **both** files existing:

```python
def _refs_cached(aid: str) -> bool:
    return C.refs_path(aid).exists() and C.raw_refs_path(aid).exists()
```

So deleting `refs_raw.json` forces a re-fetch — that is the supported way to
retry one paper's references without `--force-refs`.

> **Size warning (doc 03 §7.6):** `refs_raw.json` holds the full S2 response
> including every reference's `authors` array. It is routinely the largest file
> in a paper directory.

### 2.6 `<corpus>/<paper_key>/v{N}/*.tex`, `*.bib`

**Producer:** `m1_download.save_source_with_stats(data, vdir)`.
Only `KEEP_SUFFIXES = {".tex", ".bib"}` survive, each capped at
`MAX_FILE_BYTES` (50 MB). `_safe_relative_path` refuses `..` traversal.
`_write_main_tex` synthesises a root `.tex` when the archive is a single
non-`.tex` file.

### 2.7 `<corpus>/discovery_cache.json`

**Producer:** `run_m1._save_cache(path, info, not_found)` after **every** batch.
**Consumer:** `run_m1._load_cache`.

```json
{
  "info": {
    "2310.12345": {
      "title": "Attention Is All You Need",
      "authors": ["Ashish Vaswani"],
      "venue": null,
      "n_versions": 2,
      "version_dates": {"1": "2017-06-12T17:00:00+00:00", "2": "2023-08-02T17:00:00+00:00"}
    }
  },
  "not_found": ["9999.99999"]
}
```

> **Type conversion trap:** JSON object keys are strings, so
> `_load_cache` converts back with
> `{int(k): v for k, v in d["version_dates"].items()}`. Anything that reads the
> cache without this conversion will silently miss v1.

`--no-discovery-cache` sets `cache_path = None`; `_save_cache` returns early
and `_load_cache` yields `({}, set())`, so the whole discovery pass is redone
every run.

### 2.8 `<corpus>/refs_failures.jsonl`

**Producer:** `m1_references.record_failure(...)` — **append-only**, one JSON
object per line.

```json
{"arxiv_id": "2310.12345", "recorded_at": "2026-10-20T08:02:00+00:00",
 "attempts": 5, "category": "request_failure", "error": "HTTP 429"}
```

`category` is `"request_failure"` (transport/batch error) or
`"invalid_response"` (paper found but `references` was not a list).
The file is **never truncated or deduplicated** — repeated runs accumulate
duplicate lines for the same paper. Read it as "what failed at least once",
not "what is still failing".

### 2.9 Published M1 tables

`m1_tables.build_all()` reads the corpus and writes four files:

| File | Producer call | Columns |
|---|---|---|
| `papers.parquet` | `write_parquet(df, "papers", ...)` | the 6 `PAPERS_SCHEMA` names |
| `papers.csv` | `_csv_copy(df, ("authors", "revised_dates"), ...)` | same, list columns JSON-encoded |
| `references.parquet` | `m1_tables.build_references` | the 6 `REFERENCES_SCHEMA` names |
| `references.csv` | `m1_references.write_published_csv` | all 8 `PUBLISHED_REFERENCE_FIELDS` |
| `references_stats.json` | same function | counts, see below |

**Deliberate asymmetry:** the CSV has 8 columns, the Parquet has 6
(`ref_venue` and `external_ids` are dropped by `REFERENCES_SCHEMA`). The
comment in `m1_tables.build_references` says so explicitly:
`# Parquet đúng 6 cột của đề`. If you need venue or DOI, read the CSV.

`references_stats.json`:

```json
{"papers_scanned": 4200, "references_total": 183000,
 "references_with_arxiv_id": 61000, "duplicates_removed": 900}
```

`collect_published_rows()` is the single reader of every `refs.json`; it
deduplicates with `deduplicate_references(rows)`, whose identity is:

```python
identity = row["ref_s2_id"] or row["ref_arxiv_id"] or (
    row["ref_title"].strip().casefold(),
    tuple(author.casefold() for author in row["ref_authors"]),
    row["ref_submission_date"],
)
```

So a reference is a duplicate of another if either S2 ID matches, or arXiv ID
matches, or the full (title, authors, date) triple matches.

---

## 3. M2 — per-paper payloads

### 3.1 `<corpus>/<paper_key>/v{N}/tree_raw.json`

**Producer:** `m2_hierarchy.process_paper` → `parse_version` → `target.write_text(...)`.

**Consumer:** `m2_content_dedup.process_paper` via
`_read_json(C.tree_raw_path(...))`.

```json
{
  "arxiv_id": "2310.12345",
  "version": "v1",
  "status": "ok",
  "root_file": "main.tex",
  "reason": null,
  "source_reason": null,
  "n_unused": 2,
  "n_unresolved": 1,
  "root": {
    "type": "document",
    "children": [
      {"type": "section", "title": "Introduction",
       "children": [
         {"type": "paragraph", "text": "We study citation matching ..."},
         {"type": "equation", "text": "\\begin{equation} E = mc^2 \\end{equation}"}
       ]}
    ]
  }
}
```

| Field | Meaning |
|---|---|
| `status` | `ok` \| `empty` \| `no_root` \| `error` |
| `reason` | why `find_root_file` failed: `no_tex_files`, `ambiguous_root`, … |
| `source_reason` | **only when `status == "empty"`** — the `reason` copied from `vN/metadata.json`, e.g. `pdf_only` |
| `error` | **only when `status == "error"`** — `"TypeName: message"` |
| `n_unused` | `.tex` files in the version that are not reachable from the root |
| `n_unresolved` | `\input`/`\include` targets that do not exist on disk |

> **`version` is the label string `"v1"`, not an integer.** Every other file in
> the pipeline uses `int`. `m2_content_dedup` gets the integer from the
> directory name instead, so the two never have to agree.

> **`source_reason` is what makes a PDF-only version legible.** Without it a
> version with no `.tex` looks like an unexplained empty tree.

**Not written by this module:** sentence splitting. `tree_raw.json` stops at
`paragraph`; `flatten_tree` produces the sentences.

### 3.2 `<corpus>/<paper_key>/elements.json`

**Producer:** `m2_content_dedup.process_paper` line 226.
**Consumer:** `utils.schemas.export_m2_parquet`.

```json
[
  {"element_id": "2310-12345#root1", "arxiv_id": "2310.12345", "kind": "document", "content": "Version 1 root"},
  {"element_id": "2310-12345#h1",    "arxiv_id": "2310.12345", "kind": "heading",  "content": "Introduction"},
  {"element_id": "2310-12345#s1",    "arxiv_id": "2310.12345", "kind": "sentence", "content": "We study citation matching."},
  {"element_id": "2310-12345#s2",    "arxiv_id": "2310.12345", "kind": "sentence", "content": "We study citation matching."},
  {"element_id": "2310-12345#ul2_1", "arxiv_id": "2310.12345", "kind": "itemize",  "content": ""},
  {"element_id": "2310-12345#li1_3", "arxiv_id": "2310.12345", "kind": "item",     "content": "first bullet"}
]
```

`#s1` and `#s2` above are **not** a mistake — see the occurrence-index rule.

**ID grammar (verified, lines 204–216):**

| kind | Pattern | Allocated |
|---|---|---|
| `document` | `<key>#root{N}` | one per version, always new |
| `heading` | `<key>#h{n}` | only when the dedup key is new |
| `sentence` | `<key>#s{n}` | same |
| `formula` | `<key>#e{n}` | same |
| `figure` | `<key>#g{n}` | same |
| `itemize` | `<key>#ul{N}_{n}` | one per version, `n` restarts at 1 |
| `item` | `<key>#li{N}_{n}` | same |

**The occurrence-index rule.** The dedup key is

```python
k = (kind, content, seen[(kind, content)])
seen[(kind, content)] += 1
```

`MERGEABLE = {"heading", "sentence", "formula", "figure"}`. So the *first*
"We study citation matching." in v1 and the *first* one in v2 share `#s1`; a
*second* identical sentence in v1 gets its own `#s2`. This keeps each version's
tree unambiguous while still collapsing genuine duplicates.

`document`, `itemize`, `item` are **not** mergeable — every version gets fresh
IDs for them, which is why their patterns embed `{N}`.

**Content is post-`cite_map`.** `rename_cites(content, cite_map)` runs inside
`flatten_tree`, so two versions that differ only by a citation key still hash
to the same element.

### 3.3 `<corpus>/<paper_key>/hierarchy.json`

**Producer:** `m2_content_dedup.process_paper` line 227.
**Consumer:** `utils.schemas.export_m2_parquet`.

```json
[
  {"arxiv_id": "2310.12345", "version": 1, "element_id": "2310-12345#h1", "parent_id": "2310-12345#root1"},
  {"arxiv_id": "2310.12345", "version": 1, "element_id": "2310-12345#s1", "parent_id": "2310-12345#h1"},
  {"arxiv_id": "2310.12345", "version": 2, "element_id": "2310-12345#s1", "parent_id": "2310-12345#h2"}
]
```

One row per **non-root** node per version. `version` here **is** an `int`
(unlike `tree_raw.json`). A root node produces no row — `nd["parent"]` is
`None` for the document, and the `if` on line 220 skips it.

The second and third rows are the interesting case: the same `element_id`
appears at two different parents in two versions. That is correct — elements
are shared, hierarchy is per-version.

### 3.4 `<corpus>/<paper_key>/parse_status.json`

**Producer:** `m2_content_dedup.process_paper` line 236.

```json
{
  "arxiv_id": "2310.12345",
  "versions": {
    "v1": {"status": "ok", "n_nodes": 1420},
    "v2": {"status": "ok", "n_nodes": 1455},
    "v3": {"status": "missing", "n_nodes": 0, "reason": "not_downloaded"}
  },
  "n_versions": 3,
  "n_versions_ok": 2,
  "n_nodes_before_dedup": 2875,
  "n_elements_after_dedup": 2410
}
```

**Per-version `status` values** (four, not two):

| status | When | `reason` |
|---|---|---|
| `ok` | `tree_raw.json` parsed and had nodes | — |
| `empty` | `tree_raw.json` exists but `flatten_tree` returned `[]` | `tree["source_reason"]` or `tree["error"]` or `tree["status"]` |
| `empty` | `tree_raw.json` **absent** but `vN/` exists | `no_tree_raw` |
| `missing` | `tree_raw.json` absent **and** `vN/` absent | `not_downloaded` |

`empty` vs `missing` is the distinction the statement wants: `empty` means
"this version was downloaded but has no TeX", `missing` means "M1 knows this
version exists but we never got it".

`n_nodes_before_dedup - n_elements_after_dedup` is the deduplication figure for
the report.

### 3.5 `<corpus>/<paper_key>/bibtex_entries_raw.json`

**Producer:** `run_m2.process_paper` line 190.
**Consumer:** none automatically — it exists for audit.

A JSON **list** of every entry **before** dedup, straight from
`parse_bibtex_entries` and `parse_bibitems`. Each entry keeps its `version`
field, which `to_bibtex_row` later strips.

### 3.6 `<corpus>/<paper_key>/bibtex_merged.json`

**Producer:** `run_m2.process_paper` line 193, from
`[to_bibtex_row(e) for e in merged]`.
**Consumers:** `utils.schemas.export_m2_parquet`, `generate_candidates` (as
`rows`), `m2_stats`.

```json
[
  {
    "citation_key": "vaswani2017attention",
    "entry_type": "inproceedings",
    "arxiv_id": "2310.12345",
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani", "Noam Shazeer"],
    "author": "Vaswani, Ashish and Shazeer, Noam",
    "year": 2017,
    "journal": "NeurIPS",
    "doi": null,
    "url": "https://arxiv.org/abs/1706.03762"
  }
]
```

**`to_bibtex_row` keeps every original field** except `version`, then overwrites
the schema columns:

```python
row = {k: v for k, v in entry.items() if k != "version"}
row.update({
    "arxiv_id":    entry.get("arxiv_id"),
    "citation_key": entry["citation_key"],
    "entry_type":  (entry.get("entry_type") or "misc").lower(),
    "title":       _clean_title(entry.get("title")),
    "authors":     split_authors(_author_field(entry)) or None,
    "year":        _year(entry.get("year")),          # int or None
    "doi":         _norm_doi(entry.get("doi")) or None,
    "journal":     entry.get("journal") or entry.get("booktitle") or None,
    "url":         entry.get("url") or f"https://arxiv.org/abs/{eprint}",
})
```

| Noteworthy behaviour | Why it matters |
|---|---|
| `authors` is a **list**, `author` is the raw BibTeX **string** | M3 reads `author` via `normalize_authors`, Parquet writes `authors` |
| `year` is **int**, and `_year()` returns `None` on failure | `BIBTEX_SCHEMA.year` is `pa.int32()`, so a string would fail the cast |
| `journal` falls back to `booktitle` | `@inproceedings` entries have no `journal` |
| `url` is synthesised from `eprint` | many `.bib` files omit `url` entirely |

> **Defect:** because every original field is preserved, `bibtex_merged.json`
> rows contain keys outside `BIBTEX_SCHEMA`. That is harmless for Parquet
> (`df.reindex(columns=schema.names)` drops extras) but it means the JSON and
> the Parquet table are **not** the same shape. Document it; do not assume
> round-trip equality.

### 3.7 `<corpus>/<paper_key>/cite_map.json`

**Producer:** `run_m2.process_paper` line 191, from `deduplicate_keys().replacements`.
**Consumer:** `m2_content_dedup.process_paper` line 170, via
`_read_json(C.cite_map_path(arxiv_id))`.

```json
{"vit_transformer": "vaswani2017", "attention_is_all_you_need": "vaswani2017"}
```

`{old_key: canonical_key}`. Consumed by `rename_cites` inside `flatten_tree`,
**before** content hashing — so citation-key drift between versions does not
prevent element dedup.

> **Ordering constraint:** the `bib` step must run **before** the `content`
> step for this file to exist. `run_m2`'s `ALL_STEPS = ("bib", "tree",
> "content")` enforces that order. Running `--steps content` alone silently
> dedups without renaming.

### 3.8 `<corpus>/<paper_key>/bibtex_conflicts.json`

**Producer:** `run_m2.process_paper` line 192.

```json
[{"citation_key": "vaswani2017", "field": "year", "kept": "2017", "discarded": "2018"}]
```

Produced by both dedup levels. `kept` is the value from the entry whose
`(citation_key, version)` sorts first.

### 3.9 `<corpus>/<paper_key>/candidates.json`

**Producer:** `run_m2.process_paper` line 194, after
`generate_candidates(...)` and `validate_candidate_rows(...)`.
**Consumer:** `utils.schemas.export_m2_parquet` does **not** read it; M3 will.

```json
[
  {
    "candidate_id": "a3f8e2b1c4d5e6f7",
    "arxiv_id": "2310.12345",
    "citation_key": "vaswani2017attention",
    "ref_s2_id": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "ref_arxiv_id": "1706.03762",
    "reference_index": 0,
    "title_similarity": 1.0,
    "author_similarity": 0.95,
    "bib_title_normalized": "attention is all you need",
    "ref_title_normalized": "attention is all you need",
    "bib_authors_normalized": "ashish vaswani noam shazeer",
    "ref_authors_normalized": "ashish vaswani noam shazeer",
    "title_exact": true,
    "bib_title": "Attention Is All You Need",
    "ref_title": "Attention Is All You Need"
  }
]
```

**`candidate_id` is a content hash**, not a sequence number:

```python
hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
# identity = {arxiv_id, citation_key, ref_s2_id, ref_arxiv_id, reference_index}
```

So the same pair always gets the same ID across runs, which makes the file
diff-able.

`CANDIDATE_FIELDS` (enforced by `validate_candidate_rows`) is **10** fields:
`candidate_id`, `arxiv_id`, `citation_key`, `ref_s2_id`, `ref_arxiv_id`,
`reference_index`, `title_similarity`, `author_similarity`, `bib_title`,
`ref_title`. The `*_normalized` and `title_exact` keys are **extra** — allowed
by the validator, not required by it.

`validate_candidate_rows` also checks `0 <= title_similarity <= 1` and
`0 <= author_similarity <= 1`, raising `ValueError` otherwise.

**`reference_index` is positional into the arXiv-ID-filtered list**, not the
full `refs.json`. Reconstructing which reference it points at therefore
requires applying the same filter first:

```python
references = [r for r in refs_all if r.get("ref_arxiv_id")]   # must match run_m2
chosen = references[candidate["reference_index"]]
```

> **Sharp edge for M3:** if you index into the raw `refs.json` list you will
> silently point at the wrong reference whenever the paper has any reference
> without an arXiv ID. `ref_s2_id` and `ref_arxiv_id` are carried on the
> candidate for exactly this reason — prefer them.

### 3.10 `<corpus>/<paper_key>/candidate_entry_index.json`

**Producer:** `run_m2.process_paper` lines 195–196.

```json
{
  "vaswani2017attention": {"candidate_count": 2, "candidate_ids": ["a3f8e2b1c4d5e6f7", "b7c1..."]},
  "smith2020graph": {"candidate_count": 0, "candidate_ids": []}
}
```

A per-entry reverse index built from `candidates.json`. Lets M3 pull one
citation key's candidates without scanning the whole array. Every merged entry
appears, including the ones with zero candidates.

### 3.11 `<corpus>/<paper_key>/m2_stats.json`

**Producer:** `run_m2.process_paper` line 235.
**Consumers:** `run_m2.run` (folds into the aggregate), `--skip-existing`
(existence test), humans.

Fields are listed in doc 04 §4.8. Two structural notes:

- `citation_key_replacements` duplicates the whole `cite_map.json` inside the
  stats file.
- `candidates_per_entry` is a full `{key: [candidate_id, ...]}` dict.

Together these make `m2_stats.json` the largest per-paper file after
`refs_raw.json`.

### 3.12 Aggregate M2 outputs

**`<corpus>/m2_stats.json`** — an **array**, one element per processed paper:

```json
[
  {"paper": "2602-04870", "arxiv_id": "2602.04870",
   "tree_statuses": {"v1": "ok", "v2": "ok"},
   "parse": {"n_versions": 2, "n_versions_ok": 2,
             "n_nodes_before_dedup": 2875, "n_elements_after_dedup": 2410},
   "version_status": {"v1": "ok", "v2": "ok"}},
  {"paper": "2602-04871", "arxiv_id": "2602.04871",
   "error": "ModuleNotFoundError: No module named 'm2_macro_expansion'"}
]
```

The `error` form is what a failed paper looks like: `ok` is `False`, the
exception text is stored, and the run continues.

**`<corpus>/benchmark/m2_benchmark.json`** — `PerfRecorder` output plus:

```json
{"n_papers": 4200, "steps": ["bib", "tree", "content"],
 "export": {"elements": 9000000},
 "parse_success_rate": 0.97,
 "m2_json_bytes": 1200000000, "export_parquet_bytes": 400000000,
 "m2_output_bytes": 1600000000}
```

`m2_output_bytes(root)` sums only files whose **name** is in
`M2_OUTPUT_FILES` — the eleven per-paper JSON names. The M1 `.tex`/`.bib`
sitting in the same directories are deliberately excluded.

**`<corpus>/benchmark/unknown_commands.json`**

```json
{"foo(dimension)": 812, "bar(text)": 44, "baz(complex)": 9, "qux(no_arg)": 1301}
```

Written by `m2_cleanup.save_unknown_commands_stats`. The parenthesised suffix
is the classifier from `unwrap_unknown_command`. This file is the feedback
loop: read the top entries, add them to a table in `m2_cleanup.py`, re-run.

---

## 4. M3 — the prediction contract

`run_m3.py` is still a `NotImplementedError` stub, so this is the **intended**
contract, taken from `PREDICTIONS_SCHEMA`.

### 4.1 Labels CSV (input to M3)

Agreed shape, from the work-allocation document: three columns.

```csv
arxiv_id,citation_key,groundtruth_arxiv_id
2310.12345,vaswani2017attention,1706.03762
2310.12345,smith2020graph,
```

An empty `groundtruth_arxiv_id` means "this citation genuinely has no arXiv
counterpart" — it is a label, not missing data. M3 must not drop those rows;
they are the negative class.

### 4.2 `predictions`

```json
[{"arxiv_id": "2310.12345", "citation_key": "vaswani2017attention",
  "groundtruth_arxiv_id": "1706.03762",
  "predicted_arxiv_ids": ["1706.03762", "1810.04805"]}]
```

| Column | Type | Notes |
|---|---|---|
| `arxiv_id` | string, non-null | citing paper |
| `citation_key` | string, non-null | |
| `groundtruth_arxiv_id` | string, **nullable** | `null` when no match exists |
| `predicted_arxiv_ids` | **list** of strings, non-null | ranked, best first |

> **Changed from the old doc.** `predictions` used to be documented as
> `partition` + `cand_1`…`cand_5`. `PREDICTIONS_SCHEMA` has neither. It has a
> **list** column and no `partition` column. If partitioning is required it is
> a train-time concern, not a column.

---

## 5. Parquet schemas — the authoritative list

From `src/utils/schemas.py`. `DICT_STRING = pa.dictionary(pa.int32(), pa.string())`.

**`PAPERS_SCHEMA`** — 6 columns

| name | type | nullable |
|---|---|---|
| `arxiv_id` | string | **False** |
| `title` | string | True |
| `authors` | list\<string\> | True |
| `submission_date` | string | True |
| `revised_dates` | list\<string\> | True |
| `venue` | string | True |

**`REFERENCES_SCHEMA`** — 6 columns

| name | type | nullable |
|---|---|---|
| `arxiv_id` | string | **False** |
| `ref_arxiv_id` | string | **False** |
| `ref_title` | string | True |
| `ref_authors` | list\<string\> | True |
| `ref_submission_date` | string | True |
| `ref_s2_id` | string | True |

**`ELEMENTS_SCHEMA`** — `element_id`(F), `arxiv_id`(F), `kind`(F, dict), `content`(T)

**`HIERARCHY_SCHEMA`** — `arxiv_id`(F), `version`(int32, F), `element_id`(F), `parent_id`(F)

**`BIBTEX_SCHEMA`** — `arxiv_id`(F), `citation_key`(F), `entry_type`(T, dict),
`title`(T), `authors`(T, list), `year`(int32, T), `doi`(T), `journal`(T), `url`(T)

**`PREDICTIONS_SCHEMA`** — `arxiv_id`(F), `citation_key`(F),
`groundtruth_arxiv_id`(T), `predicted_arxiv_ids`(list, F)

> **`REFERENCES_SCHEMA.ref_arxiv_id` is `nullable=False`.** That is what forces
> `collect_published_rows` to filter on `ref_arxiv_id` — an unfiltered row would
> fail the Arrow cast, not merely be semantically wrong.

`write_parquet(df, table_name, out_path)` does
`df.reindex(columns=schema.names)` before writing, so **extra** columns are
silently dropped and **missing** columns become all-`null` — which then fails
on a non-nullable field.

---

## 6. Validation helpers

```python
from utils.schemas import (
    validate_reference_rows, validate_candidate_rows,
    validate_m2_integrity, validate_rows,
)
```

| Helper | Called from | Behaviour on violation |
|---|---|---|
| `validate_rows(rows, fields)` | internal | raises `TypeError` / `ValueError` with the row index |
| `validate_reference_rows(rows)` | **not called in the pipeline** | raises |
| `validate_candidate_rows(rows)` | `run_m2.process_paper` line 180 | **raises** — aborts that paper, caught by the per-paper `except` |
| `validate_m2_integrity(el, hi, bi)` | `export_m2_parquet` line 216 | **returns** a list of strings, printed as `[INTEGRITY WARNING]`; export continues |

That asymmetry is intentional: a bad candidate row is a logic bug (fail the
paper); a bad element/hierarchy cross-reference is a data-quality signal (warn
and still ship the table).

`validate_m2_integrity` checks four things:

1. `elements`: no NULL `element_id` / `arxiv_id`.
2. `elements`: no duplicate `(arxiv_id, element_id)`.
3. `bibtex`: no NULL `citation_key` / `arxiv_id`; no duplicate `(arxiv_id, citation_key)`.
4. `hierarchy`: every `element_id` **and** every `parent_id` exists in `elements`.

Root edges are dropped **before** check 4:
`hier_df.dropna(subset=["parent_id"])`, with a printed count.

---

## 7. What changed, and what is still wrong

### 7.1 Substitutions since the last version of this doc

| Old contract | Status | Replacement |
|---|---|---|
| `data/<key>/meta.json` | **deleted** | `paper.json` (§2.1) |
| `data/<key>/versions.json` | **deleted** | `paper.json.n_versions` + `revised_dates` (§2.1) |
| `m1_metadata.store_results` | **deleted module** | `run_m1.crawl_batch` inline (§2.1, §2.2) |
| `m1_download.process_version` | **does not exist** | `m1_download.save_source_with_stats` |
| `m1_download.save_source` | **does not exist** | same |
| `m2_export_parquet.py` | **deleted** | `run_m2 --export` → `utils.schemas.export_m2_parquet` |
| `rewrite_citations` | **renamed** | `rewrite_citations_with_count` |
| `predictions.partition`, `cand_1..5` | **never existed** | `predicted_arxiv_ids` list (§4.2) |
| "`utils.schemas.py` is still a stub" | **false** | fully implemented (§5) |

`utils/config.py` still defines `meta_path()` and `versions_path()`. Only two
things touch them:

- `m2_hierarchy.read_arxiv_id` (line 473) tries `paper.json`, **then**
  `versions.json`, then `meta.json` — a compatibility fallback for corpora
  crawled before the rework.
- `test_m1_metadata.py`, `test_m1_references.py`, `test_m2_text.py` write them.

Since `m1_metadata.py` is gone, nothing creates them any more. **New corpora
never have these files.**

### 7.2 🔴 `refs.json` and `v{N}/references.json` disagree on shape

`refs.json` is `{"arxiv_id": ..., "references": [...]}`.
`v{N}/references.json` is a bare `[...]` with **no** per-row `arxiv_id`.

`run_m2._load_references` handles both, so nothing breaks today. But the two
files are written from the same `refs` list at different indentation levels
(`indent=2` vs. none), and any new consumer must know which one it reads. The
envelope `arxiv_id` is also the only place the citing ID appears in `refs.json`,
because `normalize_references` strips the per-row `arxiv_id`.

**Fix:** make `v{N}/references.json` use the same envelope, or drop it (§7.3).

### 7.3 🟠 `v{N}/references.json` is written N times per paper

`run_m1.crawl_batch` lines 434–439 loop every version and write the identical
list into each. For a paper with 15 versions that is 15 copies of a file that
can be hundreds of KB.

Nothing reads it except `run_m2._load_references`, which only falls back to it
when `refs.json` is missing — and `refs.json` is written by the same run.

**Fix:** write it once (or not at all) and keep the fallback for old corpora.

### 7.4 🟠 `refs_failures.jsonl` grows without bound

Append-only, never compacted, one line per failure. On a 40k-ID crawl with
retries this file reaches tens of thousands of duplicate lines for the same
paper. `record_failure` always passes `MAX_RETRIES` as `attempts`, so you
cannot distinguish "failed once" from "failed five times".

**Fix:** key by `arxiv_id` and rewrite, or at least log the attempt index.

### 7.5 🟠 `bibtex_merged.json` has more keys than `BIBTEX_SCHEMA`

`to_bibtex_row` preserves every original entry field, so JSON rows carry keys
the Parquet table will never contain. The reindex in `write_parquet` hides this
at export time, but a test comparing JSON to Parquet will fail.

**Fix (documentation-level):** state that `bibtex.parquet` is a *projection* of
`bibtex_merged.json`, not a serialization of it.

### 7.6 🟡 `m2_stats.json` embeds two large structures

`citation_key_replacements` is `cite_map.json` inlined;
`candidates_per_entry` is `{key: [candidate_id, ...]}`. At 40k papers this
aggregate file becomes multi-GB and dominates any `json.load` on the corpus
root.

**Fix:** drop `candidates_per_entry` from the aggregate, or rely on
`candidate_entry_index.json`, which already exists per paper.

### 7.7 🟡 `discovery_cache.json` int-key round-trip

`version_dates` is keyed by `int` in Python and by `str` after JSON. Every
reader must repeat the `int(k)` conversion that `_load_cache` performs. No
public helper exposes this — `_load_cache` is private.

**Fix:** expose `load_discovery_cache(path) -> (info, not_found)`.

### 7.8 🟢 `paper_key` / `folder_name` divergence is fixed

`run_m1.folder_name` now returns `C.paper_key(arxiv_id)`, so the two naming
rules cannot drift. Old corpora built under the `_` rule
(`hep-th_9901001`) are **not** renamed; `run_m2.paper_arxiv_id` reads
`paper.json` and therefore still works, but `list_paper_dirs` relies on
`paper.json` existing rather than on the folder name matching.

---

## 8. Boundary summary

Producer → consumer, one row per contract. "Validated?" means an explicit
check exists in code.

| # | Payload | Producer | Consumer | Validated? |
|---|---|---|---|---|
| 1 | `paper.json` | `run_m1.crawl_batch` | M2, `m1_tables` | `arxiv_id` truthiness only |
| 2 | `v{N}/metadata.json` | `run_m1.crawl_batch` | `run_m1` (checkpoint), `m2_hierarchy` | self-healing on parse failure |
| 3 | `refs.json` | `m1_references.write_refs` | `run_m2`, `m1_tables`, `run_m1` | `validate_reference_rows` exists but is never called |
| 4 | `v{N}/references.json` | `run_m1.crawl_batch` | `run_m2._load_references` | no |
| 5 | `refs_raw.json` | `m1_references.write_raw_refs` | humans, `_refs_cached` | no |
| 6 | `discovery_cache.json` | `run_m1._save_cache` | `run_m1._load_cache` | tolerant load |
| 7 | `refs_failures.jsonl` | `m1_references.record_failure` | humans | no |
| 8 | `papers.parquet` | `m1_tables.build_papers` | final dataset | `write_parquet` schema |
| 9 | `references.parquet` | `m1_tables.build_references` | final dataset | `write_parquet` schema |
| 10 | `v{N}/tree_raw.json` | `m2_hierarchy.process_paper` | `m2_content_dedup` | `status` field |
| 11 | `elements.json` | `m2_content_dedup` | `export_m2_parquet` | `validate_m2_integrity` (warn) |
| 12 | `hierarchy.json` | `m2_content_dedup` | `export_m2_parquet` | same |
| 13 | `parse_status.json` | `m2_content_dedup` | humans, `run_m2` | no |
| 14 | `bibtex_entries_raw.json` | `run_m2` | audit only | no |
| 15 | `bibtex_merged.json` | `run_m2` | `export_m2_parquet`, M3 | `validate_m2_integrity` (warn) |
| 16 | `cite_map.json` | `run_m2` | `m2_content_dedup` | no |
| 17 | `bibtex_conflicts.json` | `run_m2` | humans | no |
| 18 | `candidates.json` | `run_m2` | M3 | `validate_candidate_rows` (**raises**) |
| 19 | `candidate_entry_index.json` | `run_m2` | M3 | no |
| 20 | `m2_stats.json` (per paper) | `run_m2.process_paper` | `run_m2.run` | no |
| 21 | `m2_stats.json` (corpus) | `run_m2.run` | humans, report | no |
| 22 | `m2_benchmark.json` | `PerfRecorder.save` | report | no |
| 23 | `unknown_commands.json` | `m2_cleanup.save_unknown_commands_stats` | developers | no |
| 24 | `elements/hierarchy/bibtex.parquet` | `export_m2_parquet` | final dataset | `validate_m2_integrity` (warn) |
| 25 | labels CSV | M3 team | M3 | not implemented |
| 26 | `predictions` | M3 | final dataset | not implemented |

**Cross-milestone handoffs that actually exist:**

```
M1 ──paper.json, refs.json, vN/*.tex|.bib ──► M2 (bib, tree, content)
                                               │
        ┌──────────────────────────────────────┘
        ▼
M2 ──candidates.json, bibtex_merged.json, elements.json, hierarchy.json ──► M3
        │
        └──cite_map.json (internal M2↔M2)
        └──export/*.parquet ──► final dataset
```

---

## 9. Team-agreed contracts vs. what the code does

The work-allocation document records five interfaces. Two are now stale:

| Agreed contract | Reality |
|---|---|
| `data/<paper_key>/v1/` contains source plus `meta.json` and `refs.json` | `paper.json` at paper level; `v{N}/metadata.json` per version; `refs.json` at paper level. No `meta.json`. |
| P3 → P1: `versions.json` lists versions to download | No `versions.json`. `paper.json.n_versions` plus `discovery_cache.json.version_dates` carry this. |
| P1 → P3: raw hierarchy JSON has uncleaned node text | Still true — `tree_raw.json`; cleaning happens in `flatten_tree`. |
| P2 → P4: merged BibTeX follows the assignment's `bibtex` columns | True **as a projection**; the JSON has extra keys (§7.5). |
| Labels use `arxiv_id,citation_key,groundtruth_arxiv_id` | Not implemented yet; recorded as the intended shape (§4.1). |

Treat these as checkpoints, not as proof that the branch satisfies every
contract.

---

## 10. Verification

```bash
# Self-test every PyArrow schema
python src/utils/schemas.py

# Rebuild the M1 tables from an existing corpus
python src/crawler/m1_tables.py --data-dir ./data

# Export M2 tables and print integrity warnings
python -m run_m2 --export-only --corpus-dir ./data
```

`python src/utils/schemas.py` builds a sample row for every schema, writes it,
and re-reads it — the fastest way to confirm a schema change did not break the
cast.