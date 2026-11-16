# M2 — Hierarchical Parsing and Standardization

> **Re-verified against commit `01b5bb0`.** M2 was largely rewritten: `run_m2.py`
> now orchestrates all three steps itself, `m2_export_parquet.py` was deleted,
> `m2_cleanup.py` grew a macro-expansion layer and an unknown-command counter,
> `m2_bibitem.py` learned inline `\bibitem`, and two inspection helpers appeared.
> **`m2_hierarchy.py` is currently unimportable** — see §7.1 before you run anything.

## 1. Overview

Milestone 2 turns the raw LaTeX collected in M1 into structured, deduplicated data. It produces three of the six final tables — `elements`, `hierarchy`, `bibtex` — and also generates the candidate pairs M3 needs.

`run_m2.py` drives everything through three ordered steps:

| Step | Flag value | Modules used | Output |
|--------|-------------|--------------|--------|
| `bib` | `--steps bib` | `m2_bibitem`, `m2_bib_dedup`, `m3_candidates` | `bibtex_merged.json`, `cite_map.json`, `candidates.json` |
| `tree` | `--steps tree` | `m2_gather`, `m2_hierarchy` | `v{N}/tree_raw.json` |
| `content` | `--steps content` | `m2_cleanup`, `m2_sentences`, `m2_content_dedup` | `elements.json`, `hierarchy.json`, `parse_status.json` |

Plus `--export`, which writes `elements`/`hierarchy`/`bibtex` Parquet via
`utils.schemas.export_m2_parquet`.

```bash
python src/run_m2.py --corpus-dir data                 # bib, tree, content
python src/run_m2.py --corpus-dir data --export        # + Parquet into data/export/
python src/run_m2.py --corpus-dir data --export-only   # Parquet only, no re-parse
python src/run_m2.py --corpus-dir data --steps bib     # one step
```

> `run_m2.py` **does** now call the hierarchy parser. That closes the defect the
> previous revision of this doc documented. It only works once §7.1 is fixed.

Standalone helpers that are not part of the pipeline:

| Script | What it does |
|--------|-------------|
| `src/parser/m2_gather.py` | Inspect root-file detection for a range of papers |
| `src/parser/m2_hierarchy.py` | Build `tree_raw.json` and print an outline |
| `src/parser/m2_content_dedup.py` | Build `elements.json` / `hierarchy.json` for existing trees |
| `src/parser/m2_inspect.py` | **New.** Human-readable dump of one paper's M2 output |
| `src/parser/m2_pick_papers.py` | **New.** List papers suitable for M3 manual labeling, from `m2_stats.json` |

---

## 2. Requirements → Implementation Map

| Official requirement | Implemented in |
|---|---|
| Multi-file gathering (follow `\input`/`\include`) | `m2_gather.py` |
| Hierarchy: Document → Section → … → Sentence / Formula / Figure | `m2_hierarchy.py` |
| Itemize points branch into deeper levels | `m2_hierarchy.py` + `m2_content_dedup.py` |
| Exclude References, keep Acknowledgements / Appendix | `m2_hierarchy.py` |
| LaTeX cleanup (`\centering`, `[htbp]`, `\midrule`) | `m2_cleanup.py` |
| Macro expansion (`\newcommand`, `\def`, custom environments) | `m2_cleanup.collect_macros` + `expand_user_macros` (**not wired — see §7.1**) |
| Math normalization (inline → `$…$`, block → `equation`) | `m2_cleanup.py` |
| Sentence splitting without breaking on `i.e.` / `Fig. 1` / decimals | `m2_sentences.py` |
| `\bibitem` / `.bib` → BibTeX entries | `m2_bibitem.parse_bibitems` + `parse_bibtex_entries` |
| Dedup reference entries, union fields, rename `\cite` | `m2_bib_dedup.py` |
| Full-text dedup across versions | `m2_content_dedup.py` |
| Parquet export with schema enforcement | `run_m2 --export` → `utils/schemas.export_m2_parquet` |

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
    ├─► m2_hierarchy.expand_includes(vdir, root)   → full_text
    │       ⚠ ME.preprocess_version_text(full_text)  ← MISSING MODULE, §7.1
    │
    ├─► m2_hierarchy.parse_version(vdir, arxiv_id)
    │       → data/<key>/v{N}/tree_raw.json
    │
    └─► m2_content_dedup.process_paper(arxiv_id)
            ├─ flatten_tree(tree_raw, cite_map)   # tree → flat node list
            ├─ clean_text / clean_figure / split_sentences
            ├─ dedup by (kind, content, occurrence)
            └─► data/<key>/elements.json
                data/<key>/hierarchy.json
                data/<key>/parse_status.json

data/<key>/v{N}/*.bib  +  \bibitem inside .tex
    │
    ├─► m2_bibitem.parse_bibtex_entries(text, arxiv_id, version)
    ├─► m2_bibitem.parse_bibitems(text, arxiv_id, version)
    │       → list of entry dicts
    │
    ├─► m2_bib_dedup.deduplicate_keys(entries)
    │       → (canonical_entries, replacements, conflicts)
    │
    ├─► m2_bib_dedup.to_bibtex_row(entry)   → BIBTEX_SCHEMA-shaped dict
    │
    └─► m2_bib_dedup.rewrite_citations_with_count(tex_text, replacements)
            → counts \\cite rewrites (does NOT write .tex — see §7.3)

data/<key>/bibtex_merged.json + data/<key>/refs.json
    │
    └─► m3_candidates.generate_candidates(merged, references_with_arxiv_id)
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
| 1b | `ME.preprocess_version_text` | **BROKEN — module does not exist, see §7.1** |
| 2 | `extract_body` | Keeps text between `\begin{document}` and `\end{document}` |
| 3 | `scan_blocks` | Splits into flat blocks: `heading`, `paragraph`, `equation`, `figure`, `itemize`, `container` |
| 4 | `build_tree` | Nests headings; level is inferred from which commands the paper actually uses |

The public entry point is `parse_version(vdir, arxiv_id=None) -> dict`, which
returns the tree dict and writes nothing. `process_paper(arxiv_id,
skip_existing=False) -> list[tuple[str, str]]` loops every version directory and
writes `v{N}/tree_raw.json`.

`process_paper` returns `[(vdir.name, tree["status"]), ...]` where status is:

| status | Meaning |
|---|---|
| `ok` | root found and parsed |
| `empty` | no `.tex` files (PDF-only version from M1) |
| `no_root` | `.tex` files exist but no `\documentclass` candidate |
| `error` | exception during parse; `tree["error"]` holds the message |

`run_m2` treats `no_root` and `error` as a paper-level failure:

```python
ok = not any(s in {"no_root", "error"} for _, s in statuses)
```

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

`is_references_title(title)` matches `References`, `Bibliography`, `Literature
Cited`, `Works Cited`, etc. A section whose cleaned title matches is dropped from
the tree, but `\section*{Acknowledgements}` and appendices are kept.

`m1_source_reason(vdir)` maps M1's `metadata.json` status onto a
human-readable skip reason, so a PDF-only version reports why the tree is empty
instead of silently producing nothing.

**Output `tree_raw.json` shape:**

```json
{
  "arxiv_id": "2512.23231",
  "version": "v1",
  "status": "ok",
  "root_file": "main.tex",
  "reason": null,
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
| `protect_citations_and_refs(text)` | **New.** Masks `\cite*`/`\ref`/`\eqref`/`\label` |
| `protect_urls(text)` | **New.** Masks `\url{}`/`\href{}`/bare URLs |
| `protect_verbatim(text)` | **New.** Masks `\verb` |
| `restore_protected(...)` | **New.** Puts the three stores back |
| `normalize_units(text)` | **New.** `\SI`, `\qty`, `\num`, `\ang`, `\si` → plain text |
| `strip_layout_commands(text)` | **New.** Removes `\footnotetext`, `\definecolor`, … plus arguments |
| `normalize_accents(text)` | **New.** `\'e` → `é`, combining accents → Unicode |
| `strip_group_braces(text)` | **New.** Drops redundant `{…}` grouping |
| `collect_macros(text)` | **New.** Harvests `\newcommand`/`\def`/`\newenvironment`, returns `(macro_map, text_without_defs)` |
| `expand_user_macros(text, max_rounds=4)` | **New.** Expands user macros with a recursion cap |
| `process_unknown_commands(text)` | **New.** Fallback shell-stripper with a counter |
| `get_unknown_commands_stats()` / `reset_unknown_commands()` / `save_unknown_commands_stats(path)` / `load_unknown_commands_stats(path)` | **New.** Counter plumbing |

**Commands dropped** (`DROP_NO_ARGS`, ~50 entries):

```
\centering, \raggedright, \raggedleft, \noindent, \indent, \clearpage,
\newpage, \midrule, \toprule, \bottomrule, \hline, \small, \large,
\bfseries, \itshape, \medskip, \smallskip, \bigskip, ...
```

`DROP_WITH_ARGS` (~35 entries) removes the command **and** its braces.

**Commands unwrapped** (`UNWRAP_COMMANDS` — command removed, content preserved):

```
\textbf, \textit, \emph, \textsc, \textsl, \underline
```

Plus `UNWRAP_SECOND_ARG` (4 commands, keeps the second argument),
`SPACE_COMMANDS` (6 → single space), and `SYMBOL_MAP` (~20 → Unicode).

**Math protection mechanism:** inline math is swapped for placeholder characters `\ue000`/`\ue001` before text cleaning, then restored. This guarantees no cleanup rule can corrupt a formula.

**Multi-line math mapping** (`_MULTI_ENV`):

| Source env | Becomes |
|-----------|---------|
| `align`, `alignat`, `flalign`, `eqnarray` | `\begin{equation} \begin{aligned} …` |
| `gather`, `multline` | `\begin{equation} \begin{gathered} …` |

**Unknown-command fallback (`process_unknown_commands`).** Anything still
standing after the tables is classified by argument shape and recorded in a
module-level `Counter`:

| Classifier | Test | Action | Counter key |
|---|---|---|---|
| dimension | `is_dimension_like(arg)` | drop arg | `name(dimension)` |
| text-only | `is_text_only(arg)` | unwrap | `name(text)` |
| complex | neither | drop everything | `name(complex)` |
| no argument | `{` absent | drop command | `name(no_arg)` |

`run_m2` dumps that counter to `benchmark/unknown_commands.json` at the end of
every run, which is the "improvement loop" the changelog describes: run → read
top unknown commands → add them to a table → rerun.

> **Never simplified away:** `\cite{}` and `\ref{}` are always preserved. M3 depends on citation keys surviving cleanup.

> **§7.10:** the counter is never reset between runs in the same process.

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

### 4.5 `m2_bibitem.py` — BibTeX parsing (two sources)

**Purpose:** Produce BibTeX entry dicts from **both** a `.bib` file and an inline
`\begin{thebibliography}` list.

```python
from parser.m2_bibitem import parse_bibtex_entries, parse_bibitems

# from a .bib file
entries = parse_bibtex_entries(
    open("data/2310-12345/v1/refs.bib").read(),
    arxiv_id="2310.12345",
    version=1,
)
# [{"citation_key": "vaswani2017", "entry_type": "inproceedings",
#   "title": "Attention Is All You Need",
#   "author": "Vaswani, Ashish and Shazeer, Noam",
#   "year": "2017", "arxiv_id": "2310.12345", "version": 1}, ...]

# from inline \bibitem
items = parse_bibitems(full_tex_text, arxiv_id="2310.12345", version=1)
```

`parse_bibitems` is the fix for the previous "papers using `thebibliography` are
silently skipped" defect. It handles three common styles:

| Style | Marker |
|---|---|
| `\newblock` (ACM / ACM-Reference-Format) | `\bibitem{key}` … `\newblock` segments |
| ACL (`\bibitem[key]{key}` + `\begin{thebibliography}`) | optional argument form |
| IEEE | bare `\bibitem{key}` followed by a single dense paragraph |

Extracted per item: authors (`split_authors`), title (quoted-span heuristics),
year, DOI (`_DOI_RE`), URL (`_URL_RE`), arXiv ID (`_ARXIV_RE`), venue
(`_guess_type` decides `article` vs `inproceedings` vs `misc`), plus `raw_text`.
LaTeX accents are decoded by `_ACCENT_RE` → `_strip_latex`.

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

### 4.8 `run_m2.py` — the integrated M2 runner

**This is now the one command that runs all of M2.** It was extended upstream; it
now calls `m2_hierarchy` and `m2_content_dedup` itself instead of leaving them as
separate scripts.

```bash
python src/run_m2.py                                   # all three steps, every paper
python src/run_m2.py --steps bib                       # BibTeX + candidates only
python src/run_m2.py --steps tree,content              # LaTeX parsing only
python src/run_m2.py --ids 2602.04870 --limit 5
python src/run_m2.py --workers 8                       # override multiprocessing concurrency
python src/run_m2.py --export                          # ...then write the Parquet tables
python src/run_m2.py --export-only                     # skip parsing, only export
```

**Three ordered steps** (`ALL_STEPS = ("bib", "tree", "content")`):

| Step | Function called | Writes |
|------|----------------|--------|
| `bib` | `process_paper(paper_dir)` | `bibtex_entries_raw.json`, `cite_map.json`, `bibtex_conflicts.json`, `bibtex_merged.json`, `candidates.json`, `candidate_entry_index.json`, `m2_stats.json` |
| `tree` | `H.process_paper(arxiv_id)` (`m2_hierarchy`) | `v{N}/tree_raw.json` |
| `content` | `D.process_paper(arxiv_id)` (`m2_content_dedup`) | `elements.json`, `hierarchy.json`, `parse_status.json` |

When `--export`/`--export-only` is set, `export_m2_parquet(corpus_dir=root,
out_dir=root/"export")` runs inside a `PerfRecorder` stage and produces
`elements.parquet`, `hierarchy.parquet`, `bibtex.parquet`. All three steps
run cleanly end-to-end (`m2_hierarchy` imports `expand_user_macros` from
`m2_cleanup`).

**`process_paper(paper_dir)` — the `bib` step, line by line:**

1. `paper_arxiv_id(paper_dir)` — reads `paper.json`; falls back to the folder
   name. The folder name is **not** a valid arXiv ID for old-style IDs
   (`hep-th-9901001` cannot be un-mangled), which is why `paper.json` is
   preferred.
2. `collect_entries(paper_dir, arxiv_id)` — for every `v{N}` that contains at
   least one `.tex` file:
   - `_compiled_text(vdir)` → `G.find_root_file` then `H.expand_includes`, i.e.
     the include-resolved source. Falls back to concatenating every `.tex`.
   - `_bib_files(vdir, text)` → only the `.bib` names referenced by
     `\bibliography{}`/`\addbibresource{}`; if none match, all `.bib` files in
     the version (the `bib_fallback_versions` counter records this).
   - `parse_bibtex_entries(...)` per `.bib` file.
   - `parse_bibitems(text, arxiv_id, version)` on the `.tex` text.
3. `deduplicate_keys(entries)` → `merged`, `replacements`, `conflicts`.
4. `to_bibtex_row(entry)` per merged entry → the exact Parquet row shape.
5. **Guard:** every replacement target must exist in `merged`, else
   `ValueError("citation replacement points to a missing canonical key")`.
6. `rewrite_citations_with_count(text, replacements)[1]` summed over every
   version → `citation_keys_rewritten`. This **counts only**; the `.tex` files
   are *not* rewritten any more.
7. `_load_references(paper_dir)` → `refs.json` (`{"references": [...]}`) or the
   first `v*/references.json` (a bare list).
8. `references = [r for r in references_all if r.get("ref_arxiv_id")]` — **only
   references with an arXiv ID become candidates**, implementing the statement's
   "m × n with n = references having an arXiv ID".
9. `generate_candidates(rows, references, diagnostics=...)` then
   `validate_candidate_rows(candidates)` (raises on schema violation).
10. Writes the JSON files listed in the table above, atomically
    (`_atomic_json` writes `.tmp` then `Path.replace`).

**`run(...)` orchestration:**

```python
run(corpus_dir=None, steps=ALL_STEPS, ids=None, limit=None,
    skip_existing=False, export=False, bench_dir=None) -> list[dict]
```

- `_corpus_dir()` → `data/corpus/` if it exists, else `data/`.
- It **reassigns `C.DATA_DIR` and `C.LOGS_DIR`** to the resolved corpus root so
  every parser module that resolves paths through `utils.config` follows along.
- Wraps the loop in `PerfRecorder("m2", watch_dirs=[root])`.
- Per paper: `rec.paper(arxiv_id)`, then each requested step inside its own
  `rec.stage_time(...)`, then `rec.record_result(arxiv_id, ok)`.
- **One paper's exception never stops the run** — it is caught, stored as
  `entry["error"] = "TypeName: message"`, and `ok` becomes `False`. The
  `tree` step additionally marks failure for `no_root`/`error` statuses.
- `--skip-existing` skips a paper only when **every requested step already has
  its output on disk** (`_completed_steps`): `bib` → `m2_stats.json`,
  `tree` → `tree_raw.json` for **all** versions, `content` → `elements.json`.
  A paper missing any of them is reprocessed. Skipped papers are counted and
  logged (`SKIP`), and their rows are preserved in the aggregate `m2_stats.json`
  instead of being dropped.
- `--limit` is applied **before** `--skip-existing`, so `--limit N` may process
  fewer than N new papers; the run warns about this combination.
- Logging: one line per paper
  (`<index>/<n> <id> bib=<ingested>→<canonical> refs=<with_arxiv>/<all> cand=<pairs> tree=<v:status,...> content=<ok>/<all> el=<before>→<after> t=<s> ok=<bool>`),
  a progress line every 100 processed papers with rate + ETA, and a summary
  (processed/skipped/failed, per-stage seconds, dedup before→after, output paths).
  The benchmark file still ends with `n_papers` / `steps` /
  `parse_success_rate` / `m2_output_bytes`.

**Two aggregate outputs at the corpus root:**

| File | Contents |
|------|----------|
| `m2_stats.json` | Array of every per-paper `stats` dict plus `tree_statuses` / `parse` / `version_status` / `error` |
| `benchmark/m2_benchmark.json` | `PerfRecorder` timings + `m2_output_bytes` (`m2_json_bytes`, `export_parquet_bytes`) |
| `benchmark/unknown_commands.json` | Top unknown LaTeX commands from `m2_cleanup` (§4.3) |

**`m2_stats.json` fields for your report:**

| Field | Use in report |
|-------|--------------|
| `bibtex_entries_ingested` | Total entries before dedup |
| `bibtex_from_bib_files` / `bibtex_from_bibitem` | Which bibliography source dominated |
| `bib_fallback_versions` | Versions where no `\bibliography{}` matched |
| `versions_with_bibliography` | Versions that yielded any entry at all |
| `canonical_entries` | Entries after dedup |
| `duplicate_bibtex_entries` | `ingested - canonical` |
| `merged_keys` | Number of old keys remapped |
| `citation_keys_rewritten` | `\cite{}` keys that *would* be renamed |
| `bibtex_conflicts` | Fields where versions disagreed |
| `references_ingested` / `references_with_arxiv_id` | Candidate pool size before/after the arXiv-ID filter |
| `candidate_pairs` | Total (entry, reference) pairs generated |
| `zero_candidate_entries` / `zero_candidate_percentage` | Entries with no candidate |
| `candidate_count_{min,max,average,median}` | Candidate distribution |
| `exact_title_candidates` / `fuzzy_title_candidates` | Title match quality |
| `candidate_generation_skipped_by_reason` | `missing_title`, `title_below_threshold` |
| `deduplication_seconds`, `candidate_generation_seconds`, `total_seconds` | Performance numbers |

> **Note:** `candidates_per_entry` is a full dict (`{citation_key: [candidate_id,
> ...]}`), so `m2_stats.json` is large. `benchmark/m2_benchmark.json`
> `m2_json_bytes` counts only the eleven `M2_OUTPUT_FILES` names, deliberately
> excluding the M1 `.tex`/`.bib` sources stored beside them.

---

### 4.9 Parquet export — `utils.schemas.export_m2_parquet`

The standalone `src/parser/m2_export_parquet.py` **no longer exists**; the export
is reached through `run_m2 --export`.

```bash
python src/run_m2.py --export-only            # corpus default
python src/run_m2.py --export --corpus-dir data/corpus
```

`export_m2_parquet(corpus_dir, out_dir)`:

1. Globs `elements.json`, `hierarchy.json`, `bibtex_merged.json` from every
   paper folder under `corpus_dir`.
2. Concatenates each type into one DataFrame.
3. Runs `validate_m2_integrity(elements_df, hierarchy_df, bibtex_df)` and prints
   each problem as `[INTEGRITY WARNING]`.
4. Drops `hierarchy` rows with NULL `parent_id` (root nodes), reporting how many.
5. Writes `elements.parquet`, `hierarchy.parquet`, `bibtex.parquet` into
   `<corpus>/export/` with explicit PyArrow schemas.

**Integrity checks performed:**

- `elements`: no NULL `element_id`/`arxiv_id`; no duplicate `(arxiv_id, element_id)`.
- `bibtex`: no NULL `citation_key`/`arxiv_id`; no duplicate `(arxiv_id, citation_key)`.
- `hierarchy`: every `element_id` and `parent_id` must exist in `elements`.

`validate_candidate_rows(rows)` is a separate, stricter check called from
`run_m2.process_paper`; it **raises** rather than warning.

---

### 4.10 Standalone helpers

These are inspect/debug tools, not part of the pipeline.

| Script | Purpose |
|--------|---------|
| `src/parser/m2_inspect.py` | Dump one paper's tree / elements / bibliography counts for a quick look |
| `src/parser/m2_pick_papers.py` | Pick a small set of papers (by size or failure status) into a scratch corpus for debugging |
| `src/matching/m3_pick_papers.py` | Same idea for M3 |

---

## 5. Configuration

Paths come from `utils/config.py`:

```python
paper_key(arxiv_id)                # data/<key>/        ← folder name
paper_dir(arxiv_id)                # data/<key>/
version_dir(arxiv_id, version)     # data/<key>/v{N}/
tree_raw_path(arxiv_id, version)   # data/<key>/v{N}/tree_raw.json
cite_map_path(arxiv_id)            # data/<key>/cite_map.json
elements_path(arxiv_id)            # data/<key>/elements.json
hierarchy_path(arxiv_id)           # data/<key>/hierarchy.json
parse_status_path(arxiv_id)        # data/<key>/parse_status.json
refs_path(arxiv_id)                # data/<key>/refs.json
```

`run_m2.run()` **overwrites `C.DATA_DIR`** with the resolved corpus root, so
every one of these resolves under the corpus you passed on the command line.

---

## 6. Practical Run Order

```bash
# Step 1 — BibTeX + citations + candidates
python src/run_m2.py --steps bib --ids 2602.04870

# Step 2 — LaTeX trees
python src/run_m2.py --steps tree --ids 2602.04870

# Step 3 — clean, split sentences, dedup across versions
python src/run_m2.py --steps content --ids 2602.04870

# Step 4 — everything + Parquet, whole corpus
python src/run_m2.py --export
```

`--steps bib` and `--steps tree` are independent; `content` reads `v{N}/tree_raw.json`
and `cite_map.json` from disk, so `bib` and `tree` should run before `content`.

---

## 7. Known Issues and Improvements

### 7.1 🟢 `m2_hierarchy` macro-expansion import (Resolved)

Earlier commits broke `m2_hierarchy.py:45` by importing a non-existent
`m2_macro_expansion` module. That import has been replaced with:

```python
from m2_cleanup import expand_user_macros
```

and `parse_version` now calls `full_text = expand_user_macros(full_text)`. Both
`run_m2.py --steps tree` and `m2_hierarchy.py --outline` run without import errors.

### 7.2 🟢 Test suite modernization (Resolved)

All 7 previously failing test imports (`m1_metadata`, `crawl_references`,
`rewrite_citations`, `m2_export_parquet`, `m2_macro_expansion`) and runtime
mismatches have been updated to match the refactored M1/M2 APIs. All 10 test
files (**164 tests**) now collect and pass (`python -m pytest src/tests -q`).

### 7.3 🟠 `.bib` fallback may over-read

`_bib_files` falls back to *every* `.bib` in the version when no
`\bibliography{}`/`\addbibresource{}` matches. For a paper that ships several
`.bib` files (one real bibliography, one for a supplementary appendix), this
inflates `bibtex_entries_ingested` and can create spurious `canonical_entries`.
`bib_fallback_versions` is the counter to watch. Consider requiring a non-empty
`\cite`-set intersection before falling back.

### 7.4 🟠 `@string` macros still not expanded

`@string{jmlr = "J

ournal of Machine Learning Research"}` — `journal = jmlr` stays literal in the output row. Add a pre-pass collecting `@string{...}` definitions before `_balanced_entries`.

### 7.5 🟠 `\bibitem` parsing is heuristic

`parse_bibitems` infers authors/title from layout, not from a grammar. Papers with unusual bibliography styles yield entries whose `title` is empty, which then shows up in `candidate_generation_skipped_by_reason["missing_title"]`. Acceptable, but report the number rather than hiding it.

### 7.6 🟡 `\import{dir/}{file}` includes not followed

`m2_gather.expand_includes` handles `\input`/`\include` but not `\import`/`\subimport`. A paper using them loses sections from the tree. Add to the input-command regex table.

### 7.7 🟡 Nested braces inside `\cite{}`

`m2_bib_dedup._CITE_RE` uses `\{([^{}]*)\}`, so `\cite{a{b}}` is not matched. Rare; documented rather than fixed.

### 7.8 🟡 `m2_hierarchy` overwrites `tree_raw.json` silently

Re-running the `tree` step discards the previous tree. It is derived data, so acceptable, but there is no `.bak`.

### 7.9 🟡 `m2_hierarchy` stops at `paragraph`

M3's feature extraction expects sentences. That conversion lives in `m2_content_dedup.flatten_tree`, so the `tree` and `content` steps are not optional companions — running `--steps tree` alone gives no `elements.json`.

### 7.10 🟡 `unknown_commands` counter is never reset in-process

`m2_cleanup` keeps its `Counter` at module level. `run()` calls
`cleanup.save_unknown_commands_stats(...)` at the end but never
`cleanup.reset_unknown_commands()`, so a second `run()` in the same process (which
is what the test suite and any notebook do) writes cumulative, not per-run,
numbers:

```python
num_cmds, num_total = cleanup.save_unknown_commands_stats(
    str(out_dir / "unknown_commands.json"))
if num_cmds > 0:
    log(f"[m2] Đã ghi {num_cmds} lệnh lạ ({num_total} lần xuất hiện) vào ...")
```

Fix: call `cleanup.reset_unknown_commands()` immediately after saving.

### 7.11 🟢 `.tex` files are no longer mutated

The old `run_m2` overwrote `.tex` files with renamed citations. It now only
**counts** rewrites (`rewrite_citations_with_count(...)[1]`), so `data/` stays
pristine. This means `citation_keys_rewritten` is a *would-rename* figure, and
`cite_map.json` is what `m2_content_dedup` uses to rename during hashing.

### 7.12 🟡 Four helpers are defined twice in `m2_cleanup.py`

`_balanced_end`, `_skip_ws`, `_skip_argument` and `_skip_optionals` each appear
twice in the same file — first at lines 191/209/215/228, then again at
516/534/540/553. The bodies are identical apart from the type annotation
(`Optional[int]` vs `int | None`). Python keeps the second definition, so the
code works, but the file reads as if there were two layers when there is only
one. Delete the first block.

---

## 8. Testing

Current state (`pytest src/tests`):

| Test file | Result | Notes |
|---|---|---|
| `test_m2_gather.py` | ✅ 55 passed | Root detection and include traversal |
| `test_m2_hierarchy.py` | ✅ 33 passed | Section nesting, math/figure/list leaves, include expansion |
| `test_m2_text.py` | ✅ 22 passed | `clean_text`, `clean_figure`, `normalize_block_math`, `split_sentences` |
| `test_m2_bibliography.py` | ✅ 4 passed | `\bibitem`, `deduplicate_keys`, `rewrite_citations_with_count`, `run_m2.run` |
| `test_m2_parser.py` | ✅ 5 passed | BibTeX provenance, `cite_map`, candidate generation (`m3_candidates`) |
| `test_m2_pipeline.py` | ✅ 7 passed | End-to-end M1-layout → M2 Parquet pipeline |
| `test_m2_export_parquet.py` | ✅ 18 passed | Schema roundtrips (including 9-column `PREDICTIONS_SCHEMA`), integrity, CLI |
| `test_m1_metadata.py` | ✅ 5 passed | `run_m1` ID/range parsing & `paper_key` |
| `test_m1_references.py` | ✅ 11 passed | S2 batch crawling, deduplication, `REFERENCES_SCHEMA` |
| `test_run_m1_downloader.py` | ✅ 4 passed | Archival extraction & checkpointing |

**Net: all 10 files collect cleanly, 164/164 tests pass.**

---

## 9. Team Review

- **Author:** P1 (gather, hierarchy), P3 (cleanup, sentences, content dedup), P4 (BibTeX dedup, export, schemas)
- **Reviewer:** P1 ↔ P3, P2 ↔ P4
- **Design decision:** Dedup identity for references uses title/DOI/key matching without excluding differing years, because versions legitimately update publication years (e.g., `vaswani2017` → `vit_transformer`).
- **Design decision:** Elements are deduplicated **after** cleanup so that spacing and formatting differences do not prevent a match, as the statement requires.
- **Design decision:** `run_m2` combines `bib`, `tree`, and `content` into one runner with `--steps` so stages can run together or independently for debugging.
- **Open question:** Should `--export` be the default? Today a plain `python src/run_m2.py` leaves no Parquet until `--export` or `--export-only` is passed.
