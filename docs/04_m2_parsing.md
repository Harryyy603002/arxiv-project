# M2 — Hierarchical Parsing and Standardization

## 1. Overview

Milestone 2 turns the raw LaTeX collected in M1 into structured, deduplicated data. It produces three of the six final tables — `elements`, `hierarchy`, `bibtex` — and also generates the candidate pairs M3 needs.

There are two things you can run today:

| Script | What it does |
|--------|-------------|
| `src/parser/m2_hierarchy.py` | Parse one paper's `.tex` into `tree_raw.json` |
| `src/parser/m2_content_dedup.py` | Clean + dedup across versions → `elements.json`, `hierarchy.json` |
| `src/run_m2.py` | **BibTeX dedup + citation rewriting + candidate generation** |
| `src/parser/m2_export_parquet.py` | Export `elements`, `hierarchy`, `bibtex` to Parquet |

Note that `run_m2.py` does **not** call the hierarchy parser. You run the LaTeX tree steps separately.

---

## 2. Requirements → Implementation Map

| Official requirement | Implemented in |
|---|---|
| Multi-file gathering (follow `\input`/`\include`) | `m2_gather.py` |
| Hierarchy: Document → Section → … → Sentence / Formula / Figure | `m2_hierarchy.py` |
| Itemize points branch into deeper levels | `m2_hierarchy.py` + `m2_content_dedup.py` |
| Exclude References, keep Acknowledgements / Appendix | `m2_hierarchy.py` |
| LaTeX cleanup (`\centering`, `[htbp]`, `\midrule`) | `m2_cleanup.py` |
| Math normalization (inline → `$…$`, block → `equation`) | `m2_cleanup.py` |
| Sentence splitting without breaking on `i.e.` / `Fig. 1` / decimals | `m2_sentences.py` |
| `\bibitem` / `.bib` → BibTeX entries | `m2_bibitem.py` |
| Dedup reference entries, union fields, rename `\cite` | `m2_bib_dedup.py` |
| Full-text dedup across versions | `m2_content_dedup.py` |
| Parquet export with schema enforcement | `m2_export_parquet.py` + `utils/schemas.py` |

---

## 3. Full M2 Data Flow

```
data/<key>/v{N}/*.tex
    │
    ├─► m2_gather.find_root_file(vdir)
    │       → RootResult(root, candidates, reason, n_tex_files)
    │
    ├─► m2_gather.collect_compilation_files(vdir, root)
    │       → ordered list of .tex files actually compiled
    │
    ├─► m2_hierarchy.build_tree(files)
    │       → data/<key>/v{N}/tree_raw.json
    │
    └─► m2_content_dedup.process_paper(arxiv_id)
            ├─ flatten_tree(tree_raw)      # tree → flat node list
            ├─ clean_text / split_sentences
            ├─ dedup by (kind, content)
            └─► data/<key>/elements.json
                data/<key>/hierarchy.json
                data/<key>/parse_status.json

data/<key>/v{N}/*.bib
    │
    ├─► m2_bibitem.parse_bibtex_entries(text, arxiv_id, version)
    │       → list of entry dicts
    │
    ├─► m2_bib_dedup.deduplicate_keys(entries)
    │       → (canonical_entries, replacements, conflicts)
    │
    └─► m2_bib_dedup.rewrite_citations_with_count(tex_text, replacements)
            → rewrites \cite{} in the .tex files in place

data/<key>/bibtex_merged.json + data/<key>/refs.json
    │
    └─► m3_candidates.generate_candidates(merged, references)
            → data/<key>/candidates.json   (M3 input)
```

---

## 4. Module Reference

### 4.1 `m2_gather.py` — Multi-file gathering

**Purpose:** Find the root `.tex` (the one with `\documentclass`) and every file reachable from it via `\input` / `\include`.

```python
from utils.config import version_dir
from parser.m2_gather import find_root_file, collect_compilation_files

vdir = version_dir("2512.23231", 1)
result = find_root_file(vdir)
print(result.root, result.reason)       # 'main.tex', 'ok'

files = collect_compilation_files(vdir, result.root)
```

**Root selection priority** (from the module docstring):

1. Only `.tex` files; comments are stripped first, so `% \documentclass{article}` never counts.
2. Candidate must contain `\documentclass` (or legacy `\documentstyle`).
3. If several candidates, rank by:
   - has `\begin{document}` (a real root has a body)
   - is **not** class `standalone` / `subfiles`
   - is **not** `\input` by another file
   - has the most `\input` / `\include` commands
   - has a familiar name (`main`, `ms`, `paper`, `article`, `manuscript`, `root`, `arxiv`)
   - is shallowest, then largest, then alphabetical

**`reason` codes** (`REASONS` dict):

| reason | Meaning | What to do |
|--------|---------|-----------|
| `ok` | One clear root | Nothing |
| `ambiguous` | Several equal candidates | Spot-check a sample |
| `weak_candidate` | Only `standalone`/`subfiles` or already-included files | Inspect manually |
| `root_without_begin_document` | Has `\documentclass` but no `\begin{document}` | Likely the `\input{preamble}` pattern |
| `fallback_begin_document` | No `\documentclass`, but a file has `\begin{document}` | Inspect manually |
| `no_documentclass` | `.tex` exists but none is a root | Inspect manually |
| `no_tex_files` | Empty version (PDF-only from M1) | Expected; leave as empty |

**Run standalone:**
```bash
python src/parser/m2_gather.py --ids 2512.23231 --outline
python src/parser/m2_gather.py --range 2512.23231-2601.03155 --month-last 2512=25075 --limit 30
```

**Known limitations** (module docstring):
- A root hidden behind `\input{preamble}` where `\documentclass` lives in `preamble.tex` is chosen but flagged `root_without_begin_document`.
- Does not understand `\import{dir/}{file}`, `\includeonly`, or `\iffalse…\fi`.
- `\input` of `.tikz` / `.pgf` / `.txt` files appears in `unresolved` — this follows from M1 keeping only `.tex`/`.bib`, so it is not a parser bug.

---

### 4.2 `m2_hierarchy.py` — Tree construction

**Purpose:** Expand includes in place, take the body, scan into flat blocks, then nest headings into a tree.

```bash
python src/parser/m2_hierarchy.py --ids 2512.23231 --outline
python src/parser/m2_hierarchy.py --range 2512.23231-2601.03155 --limit 30
```

**Four internal steps, one function each:**

| Step | Function | What it does |
|------|----------|-------------|
| 1 | `expand_includes` | Inlines `\input`/`\include` in place → one LaTeX string in reading order |
| 2 | `extract_body` | Keeps text between `\begin{document}` and `\end{document}` |
| 3 | `scan_blocks` | Splits into flat blocks: `heading`, `paragraph`, `equation`, `figure`, `itemize`, `container` |
| 4 | `build_tree` | Nests headings; level is inferred from which commands the paper actually uses |

**Constants that control behaviour:**

```python
SECTION_CMDS = ("part", "chapter", "section", "subsection",
                "subsubsection", "paragraph", "subparagraph")
MATH_ENVS    = {"equation", "align", "alignat", "flalign", "gather",
                "multline", "eqnarray", "displaymath", "dmath"}
FIGURE_ENVS  = {"figure", "table", "wrapfigure", "wraptable", "tabular",
                "tabularx", "longtable", "tikzpicture", "algorithm", "verbatim", ...}
LIST_ENVS    = {"itemize", "enumerate", "description"}
SKIP_ENVS    = {"thebibliography"}
```

`REFERENCES_TITLE_RE` matches `References`, `Bibliography`, `Literature Cited`, `Works Cited`, etc. A section whose cleaned title matches is dropped from the tree, but `\section*{Acknowledgements}` and appendices are kept.

**Output `tree_raw.json` shape:**

```json
{
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

> **Important:** `m2_hierarchy.py` stops at `paragraph`. Sentence splitting happens later, in `m2_content_dedup.flatten_tree`.

---

### 4.3 `m2_cleanup.py` — LaTeX cleanup and math normalization

**Functions:**

| Function | Purpose |
|----------|---------|
| `strip_comments(text)` | Remove `% …` but not `\%` |
| `clean_text(text, figure=False)` | Main cleaner |
| `clean_figure(text)` | `clean_text(figure=True)` — preserves `\\` and `~` in tables |
| `clean_heading(text)` | Cleaner applied to a heading |
| `normalize_inline_math(text)` | `\(…\)`, `\begin{math}` → `$…$` |
| `normalize_block_math(raw)` | `$$…$$`, `\[…\]`, `align`, `gather` → `\begin{equation}…\end{equation}` |
| `split_segments(text)` | → `[("text"|"formula"|"figure", chunk)]` in order |

**Commands dropped** (`DROP_NOARG`):

```
\centering, \raggedright, \raggedleft, \noindent, \indent, \clearpage,
\newpage, \midrule, \toprule, \bottomrule, \hline, \small, \large,
\bfseries, \itshape, \medskip, \smallskip, \bigskip, ...
```

**Commands unwrapped** (`UNWRAP` — command removed, content preserved):

```
\textbf, \textit, \emph, \textsc, \textsl, \underline
```

**Math protection mechanism:** inline math is swapped for placeholder characters `\ue000`/`\ue001` before text cleaning, then restored. This guarantees no cleanup rule can corrupt a formula.

**Multi-line math mapping** (`_MULTI_ENV`):

| Source env | Becomes |
|-----------|---------|
| `align`, `alignat`, `flalign`, `eqnarray` | `\begin{equation} \begin{aligned} …` |
| `gather`, `multline` | `\begin{equation} \begin{gathered} …` |

> **Never simplified away:** `\cite{}` and `\ref{}` are always preserved. M3 depends on citation keys surviving cleanup.

---

### 4.4 `m2_sentences.py` — Sentence splitting

**Purpose:** Split a cleaned paragraph into sentences without breaking on dots that do not end a sentence.

```python
from parser.m2_sentences import split_sentences
split_sentences("We study this. See Fig. 1 for details. Results improve.")
# → ['We study this.', 'See Fig. 1 for details.', 'Results improve.']
```

**Protected before splitting** (dots masked with private-use characters `\ue002`–`\ue004`):

| Pattern | Regex constant | Example |
|---------|---------------|---------|
| Abbreviations | `_ABBR_CI` | `e.g.`, `i.e.`, `et al.`, `Fig.`, `Eq.`, `Sec.`, `vs.` |
| Numbered abbreviations | `_ABBR_NUM` | `No. 5`, `p. 3`, `pp. 1-5` |
| Acronyms | `_ACRONYM` | `U.S.`, `i.i.d.` |
| Decimals | `_DECIMAL` | `3.14` |
| URLs | `_URL` | `https://…`, `\url{…}` |
| Inline math | `find_inline_math` | `$x = 1.5$` |

**Boundary regex:**

```python
_BOUNDARY = re.compile(r"([.!?]+[\"')\]}\u201d\u2019]*)\s+(?=[A-Z(\[\u201c\"'`\\$])")
```

Split happens only when `.`/`!`/`?` is followed by whitespace and then an uppercase letter, an opening bracket, a quote, a LaTeX command, or `$`.

---

### 4.5 `m2_bibitem.py` — BibTeX parsing

**Purpose:** Parse `.bib` file text into dictionaries, handling nested braces and quoted values.

```python
from parser.m2_bibitem import parse_bibtex_entries

entries = parse_bibtex_entries(
    open("data/2310-12345/v1/refs.bib").read(),
    arxiv_id="2310.12345",
    version=1,
)
# [{"citation_key": "vaswani2017", "entry_type": "inproceedings",
#   "title": "Attention Is All You Need",
#   "author": "Vaswani, Ashish and Shazeer, Noam",
#   "year": "2017", "arxiv_id": "2310.12345", "version": 1}, ...]
```

**How it works:**

| Helper | Job |
|--------|-----|
| `_balanced_entries(text)` | Yields each `@type{...}` / `@type(...)` block, tracking brace depth and quoted strings |
| `_value_end(text, start)` | Finds the end of a braced, quoted, or unquoted field value |
| `_clean_value(value)` | Strips outer braces/quotes, collapses whitespace |

> **Gap to close:** `m2_bibitem.py` parses only `.bib` files. The statement also mentions `\bibitem` entries written directly inside `.tex`. Check whether your assigned corpus contains those — if so, extend `parse_bibtex_entries` (or add a `\bibitem` extractor) before relying on the `bibtex` table being complete.

**Limitations:** lightweight grammar; no BibTeX macro evaluation (`@string`) and no LaTeX accent decoding (`{\"o}` stays as-is).

---

### 4.6 `m2_bib_dedup.py` — Dedup and `\cite` rewriting

**Two dedup levels:**

**Level 1 — same key, different versions** — `merge_entries(entries)`:
Same `citation_key` appearing in v1 and v2 with different fields: union the fields. Disagreements go into `conflicts` as `{"citation_key", "field", "kept", "discarded"}`. Replacement map is empty at this level.

**Level 2 — different keys, same reference** — `deduplicate_keys(entries)`:
Identity = `(normalize_text(title), normalize_authors(author), str(year))`. If all three are empty, identity falls back to `("key", citation_key, "")` so unrelated entries are never merged.

Within a group, entries are sorted by `(citation_key, version)`. The first key becomes canonical. Every other key maps to it in `replacements`. Fields union across the group, conflicts recorded.

**Return signature:**

```python
canonical_entries, replacements, conflicts = deduplicate_keys(entries)
# replacements: {"vit_transformer": "vaswani2017", ...}
```

**Citation rewriting:**

```python
from parser.m2_bib_dedup import rewrite_citations_with_count
new_text, rewritten_count = rewrite_citations_with_count(tex_text, replacements)
```

- Matches `\cite`, `\citep`, `\citet`, `\nocite`, starred variants, with optional `[...]` arguments.
- Splits comma-separated keys inside the braces, maps each, removes duplicates.
- **Skips lines whose first non-whitespace character is `%`** — commented citations are never rewritten.

**Known limitation:** `_CITE_RE` uses `\{([^{}]*)\}` and therefore does not handle nested braces inside a citation argument.

---

### 4.7 `m2_content_dedup.py` — Cross-version element dedup

**Purpose:** Flatten each version's `tree_raw.json`, clean the content, and give identical content across versions a shared `element_id`.

```bash
python src/parser/m2_content_dedup.py --ids 2602.04870
python src/parser/m2_content_dedup.py          # every paper with versions.json
```

**`flatten_tree(tree, cite_map)`** returns a document-order list:

```python
[{"kind": "document", "content": "", "parent": None},
 {"kind": "heading",  "content": "Introduction", "parent": 0},
 {"kind": "sentence", "content": "We study citation matching.", "parent": 1},
 ...]
```

`nodes[0]` is always the document root. An empty list means the version had no content.

**Which kinds merge across versions** (`MERGEABLE`): `heading`, `sentence`, `formula`, `figure`.

**Which do not:** `document`, `itemize`, `item` — each version gets its own ID.

**ID prefixes** (`ID_PREFIX`):

| kind | ID pattern | example |
|------|-----------|---------|
| document | `<key>#root{N}` | `2310-12345#root1` |
| heading | `<key>#h{n}` | `2310-12345#h1` |
| sentence | `<key>#s{n}` | `2310-12345#s1` |
| formula | `<key>#e{n}` | `2310-12345#e1` |
| figure | `<key>#g{n}` | `2310-12345#g1` |
| itemize | `<key>#ul{N}_{n}` | `2310-12345#ul2_1` |
| item | `<key>#li{N}_{n}` | `2310-12345#li1_3` |

**Subtle but important rule:** dedup key is `(kind, content, occurrence_index_within_version)`. So the *first* "We study X." in v1 and the *first* "We study X." in v2 share an ID, but the *second* occurrence in v1 gets a different ID. This keeps each version's tree unambiguous while still collapsing true duplicates.

**Citation renaming before hashing:** if `data/<key>/cite_map.json` exists (`{old_key: chosen_key}`), every `\cite{}` inside element content is rewritten by `rename_cites` **before** the content hash. This means two versions that differ only by citation key still dedupe to one element.

**Outputs:**

```json
// elements.json  — one row per distinct element
[{"element_id": "2310-12345#s1", "arxiv_id": "2310.12345",
  "kind": "sentence", "content": "We study citation matching."}]

// hierarchy.json — one row per node-parent edge per version
[{"arxiv_id": "2310.12345", "version": 1,
  "element_id": "2310-12345#s1", "parent_id": "2310-12345#h1"}]

// parse_status.json — per-paper statistics
{"arxiv_id": "2310.12345",
 "versions": {"v1": {"status": "ok", "n_nodes": 1420},
              "v2": {"status": "ok", "n_nodes": 1455}},
 "n_versions": 2, "n_versions_ok": 2,
 "n_nodes_before_dedup": 2875, "n_elements_after_dedup": 2410}
```

`n_nodes_before_dedup - n_elements_after_dedup` is your report's deduplication figure. A version with no `tree_raw.json` gets `{"status": "empty", "n_nodes": 0}` — it is recorded, not skipped, which is what the statement requires for PDF-only versions.

---

### 4.8 `run_m2.py` — BibTeX + candidate pipeline

**This is the script that produces M3's input.** It does not call the hierarchy/crawl steps.

```bash
python -m run_m2
```

**`process_paper(paper_dir)` step by step:**

1. Glob `v*/*.bib`, parse each with `parse_bibtex_entries(text, paper_dir.name, version)`.
2. `deduplicate_keys(entries)` → `merged`, `replacements`, `conflicts`.
3. Write `bibtex_entries_raw.json` (raw, pre-dedup).
4. Glob `v*/*.tex`, call `rewrite_citations_with_count` and **overwrite the `.tex` files in place**.
5. Write `citation_key_replacements.json`.
6. `_load_references(paper_dir)` — reads `refs.json`; falls back to the first `v*/references.json`.
7. `generate_candidates(merged, references)`.
8. `validate_candidate_rows(candidates)` — raises `ValueError` on schema violation.
9. Sanity check: every replacement target must exist in `merged`, else `ValueError`.
10. Write `candidate_entry_index.json`, `bibtex_merged.json`, `candidates.json`, `m2_stats.json`.

> **Warning about in-place mutation:** step 4 rewrites your `.tex` files. If you re-run M2 or re-parse the hierarchy, the citations are already renamed — the second run finds nothing to rewrite. This is idempotent in effect but means you cannot recover the original keys from the `.tex` files afterwards. Keep `data/` read-only backups if you need to re-parse from scratch.

**`_corpus_dir()` fallback:** uses `data/corpus/` if it exists, otherwise `data/`. This matches the `run_m1.py --corpus-dir` default.

**`run_m2.run()`** iterates every directory under the corpus root that has at least one `v*/*.bib`, calls `process_paper`, and writes an aggregate `data/m2_stats.json` array.

**`m2_stats.json` fields for your report:**

| Field | Use in report |
|-------|--------------|
| `bibtex_entries_ingested` | Total entries before dedup |
| `canonical_entries` | Entries after dedup |
| `duplicate_bibtex_entries` | `ingested - canonical` |
| `merged_keys` | Number of old keys remapped |
| `citation_keys_rewritten` | Occurrences of `\cite{}` rewritten in `.tex` |
| `bibtex_conflicts` | Fields where versions disagreed |
| `references_ingested` | References available for matching |
| `candidate_pairs` | Total (entry, reference) pairs generated |
| `zero_candidate_entries` | Entries with no candidate above threshold |
| `zero_candidate_percentage` | Same, as % |
| `candidate_count_{min,max,average,median}` | Candidate distribution |
| `exact_title_candidates` / `fuzzy_title_candidates` | Title match quality |
| `candidate_generation_skipped_by_reason` | Why pairs were dropped: `missing_title`, `title_below_threshold` |
| `deduplication_seconds`, `candidate_generation_seconds`, `total_seconds` | Performance numbers |

---

### 4.9 `m2_export_parquet.py` — Parquet export

```bash
python src/parser/m2_export_parquet.py --corpus-dir data --out-dir data/export
```

Calls `utils.schemas.export_m2_parquet`, which:

1. Globs `elements.json`, `hierarchy.json`, `bibtex_merged.json` from every paper folder under `--corpus-dir`.
2. Concatenates them **horizontally** — i.e. all papers' elements into one DataFrame, etc.
3. Runs `validate_m2_integrity(elements_df, hierarchy_df, bibtex_df)` and prints each problem as `[INTEGRITY WARNING]`. Missing `element_id`s referenced by `hierarchy`, duplicate `(arxiv_id, element_id)` keys, and NULL required columns all show up here.
4. Drops `hierarchy` rows with NULL `parent_id` (root nodes), printing how many were dropped.
5. Writes `elements.parquet`, `hierarchy.parquet`, `bibtex.parquet` with explicit PyArrow schemas.

**Integrity checks performed** (`validate_m2_integrity`):

- `elements`: no NULL `element_id`/`arxiv_id`; no duplicate `(arxiv_id, element_id)`.
- `bibtex`: no NULL `citation_key`/`arxiv_id`; no duplicate `(arxiv_id, citation_key)`.
- `hierarchy`: every `element_id` and `parent_id` must exist in `elements`.

---

## 5. Configuration

Paths come from `utils/config.py`:

```python
tree_raw_path(arxiv_id, version)   # data/<key>/v{N}/tree_raw.json
cite_map_path(arxiv_id)            # data/<key>/cite_map.json
elements_path(arxiv_id)            # data/<key>/elements.json
hierarchy_path(arxiv_id)           # data/<key>/hierarchy.json
parse_status_path(arxiv_id)        # data/<key>/parse_status.json
```

---

## 6. Practical Run Order

```bash
# Step 1 — parse LaTeX trees (one paper at a time during development)
python src/parser/m2_gather.py --ids 2310.12345 --outline
python src/parser/m2_hierarchy.py --ids 2310.12345

# Step 2 — clean, split sentences, dedup across versions
python src/parser/m2_content_dedup.py --ids 2310.12345

# Step 3 — BibTeX dedup + citation rewrite + candidate generation
python -m run_m2

# Step 4 — export Parquet tables
python src/parser/m2_export_parquet.py --out-dir data/export
```

Steps 1–2 have to run per paper; steps 3–4 sweep the whole corpus.

---

## 7. Known Issues and Improvements

| Issue | Where | Impact | Suggested fix |
|-------|-------|--------|--------------|
| `\bibitem` in `.tex` not parsed | `m2_bibitem.py` | BibTeX table may miss entries for papers that use inline `\bibitem` instead of `.bib` | Add a `\bibitem` extractor, or confirm the corpus only uses `.bib` |
| `@string` macros not expanded | `m2_bibitem.py` | `journal = jmlr` stays literal | Add a pre-pass collecting `@string{...}` definitions |
| LaTeX accents not decoded | `m2_bibitem.py` | `G{\"o}del` compares poorly against Semantic Scholar's `Gödel` | Decode in `normalize_text` (`m3_candidates.py`) — partially handled there |
| Nested braces in `\cite` | `m2_bib_dedup.py` | `\cite{a{b}}` not rewritten | Rare; accept or extend regex |
| `.tex` files mutated in place | `run_m2.py` | Cannot re-derive original citation keys | Write to a copy, or always run `m2_hierarchy` before `run_m2` |
| `\import{dir/}{file}` unsupported | `m2_gather.py` | Includes in that form are not followed | Add to `INPUT_RE` handling |
| No `.tex` for a version | `m2_content_dedup.py` | Records `status: "empty"` — correct behaviour | None |
| `m2_hierarchy` output overwritten silently | `m2_hierarchy.py` | Re-running discards the previous tree | Acceptable; it is derived data |

---

## 8. Testing

Current coverage (`pytest src/tests`):

| Test file | Covers |
|-----------|--------|
| `test_m2_parser.py` | `parse_bibtex_entries`, `deduplicate_keys`, `rewrite_citations`, `generate_candidates` |
| `test_m2_gather.py` | Root detection and include traversal |
| `test_m2_hierarchy.py` | Tree building, References exclusion |
| `test_m2_text.py` | `clean_text`, `normalize_block_math`, `split_sentences` |
| `test_m2_export_parquet.py` | Schema writing and integrity validation |

Not covered: `m2_content_dedup` cross-version ID stability, `cite_map` renaming, `run_m2` end-to-end.

---

## 9. Team Review

- **Author:** P1 (gather, hierarchy), P3 (cleanup, sentences, content dedup), P4 (BibTeX dedup, export, schemas)
- **Reviewer:** P1 ↔ P3, P2 ↔ P4
- **Design decision:** Dedup identity for references is `(title, authors, year)` rather than a hash of the whole entry, because versions legitimately drop and add fields (the statement's own `vaswani2017` → `vit_transformer` example).
- **Design decision:** Elements are deduplicated **after** cleanup so that spacing and formatting differences do not prevent a match, as the statement requires.
- **Open question:** Should `run_m2.py` also invoke `m2_hierarchy` and `m2_content_dedup` so there is a single M2 command? Currently they are separate.