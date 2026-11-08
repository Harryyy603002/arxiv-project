# M2 — Parsing and standardization

## 1. Overview and requirements

The official M2 requirement is to process every M1 version, follow the active LaTeX compilation path, build a hierarchy with sentence/formula/figure leaves, clean and normalize content, extract BibTeX, deduplicate references and full text across versions, and export `elements`, `hierarchy`, and `bibtex`.

On `main`, the balanced BibTeX parser and deduplicator are executable library logic. Gather, hierarchy, cleanup, sentence splitting, content deduplication, export, schemas, and `run_m2.py` are skeletons.

The team plan schedules M2 completion by 30/10/2026, with parser integration,
candidate creation, full-dataset processing, deduplication statistics, and
Parquet validation occurring during Days 7–11. The schedule is historical
planning evidence; the source status above is the current branch audit.

## 2. Repository modules

| Module | Responsibility | Status |
|---|---|---|
| `m2_gather.py` | root and transitive `input/include` traversal | Skeleton |
| `m2_hierarchy.py` | document/section/leaf tree and references exclusion | Skeleton |
| `m2_cleanup.py` | presentation-command and math cleanup | Skeleton |
| `m2_sentences.py` | safe sentence segmentation | Skeleton |
| `m2_content_dedup.py` | cross-version content identity and IDs | Skeleton |
| `m2_bibitem.py` | balanced BibTeX parsing and provenance | Implemented library |
| `m2_bib_dedup.py` | key identity, field merging, citation rewriting | Implemented library |
| `m2_export_parquet.py` | M2 table export | Skeleton |

## 3. LaTeX processing

The intended steps are: find the `.tex` containing `\documentclass`; follow active `\input`/`\include` transitively; ignore commented or unreachable files; parse sections, paragraphs, lists, equations, figures, and tables; exclude References; retain acknowledgements and appendices. No implementation proves these steps on `main`.

Expected synthetic behavior:

```tex
% \input{unused}
\input{intro}
\section*{Acknowledgements}
```

The commented include should not be followed, `intro.tex` should be included, and the unnumbered acknowledgements section should remain. This is `[TO BE VERIFIED]` until the parser exists.

## 4. Hierarchy and intermediate representations

The planned tree is `document → section/subsection → paragraph/list → sentence, block formula, figure/table`. README proposes node fields such as `type`, optional `level`/`title`, `text`, and `children`. The planned relational outputs are `element_id, arxiv_id, kind, content` and `arxiv_id, version, element_id, parent_id`. No serializer or ID algorithm exists on `main`.

## 5. Function documentation

### `parse_bibtex_entries(text, arxiv_id=None, version=None)`

- **Location:** `src/parser/m2_bibitem.py`.
- **Purpose:** parse balanced `@type{...}` and `@type(...)` entries.
- **Workflow:** scan entry boundaries while tracking braces/parentheses and quoted strings; parse field names; find braced, quoted, or unquoted field ends; strip outer braces/quotes and collapse whitespace; append optional provenance.
- **Returns:** list of dictionaries containing `citation_key`, lowercase `entry_type`, parsed fields, and optional `arxiv_id`/`version`.
- **Limitations:** lightweight grammar; no full BibTeX macro evaluation or LaTeX decoding.

### `parse_bib_files(files)`

- **Inputs:** deterministic tuples `(arxiv_id, text, version)`.
- **Returns:** concatenated parsed entries in tuple order.
- **Side effects:** none.

### `deduplicate_keys(entries)`

- **Purpose:** unify entries by normalized title, author, and year identity.
- **Workflow:** group by lowercased whitespace-normalized identity; sort each group by citation key/version; keep the first key; merge non-empty fields; record replacements and conflicts.
- **Returns:** `(canonical_entries, replacements, conflicts)`.
- **Compatibility:** `arxiv_id` and `version` are treated as provenance and not replaced.

### `merge_entries(entries)`

- **Purpose:** merge repeated exact citation keys.
- **Returns:** merged rows, replacement map, conflicts.
- **Known behavior:** exact-key merges currently return an empty replacement map.

### `rewrite_citations(text, replacements)`

- **Purpose:** rewrite active `\cite...{}` keys after deduplication.
- **Workflow:** process each line; preserve lines whose first non-whitespace character is `%`; replace keys inside simple citation braces.
- **Limitations:** no nested-brace, multiline-command, inline-comment, or escaped-percent handling.

## 6. Standardization

The official cleanup targets include `\centering`, placement options, `\midrule`, unified inline/block math, and `\bibitem` conversion. The current cleanup and sentence modules are stubs; the BibTeX parser only normalizes field names/whitespace and must not be described as a complete LaTeX standardizer.

## 7. Deduplication

Citation-key rewriting must precede content identity if citations participate in text identity. Full-text cross-version deduplication is required by the statement but is not implemented. Empty PDF-only versions should remain represented with no parsed elements; this is also not enforced by an M2 runner.

## 8. Testing and validation

`test_m2_parser.py` covers balanced parsing/provenance, duplicate field merging, active/commented citation rewriting, and candidate behavior. The full baseline is 14 passing tests. There are no tests for gather, hierarchy, cleanup, sentence splitting, content deduplication, Parquet export, or final schemas.

## 9. Team review

- Author: `[TEAM INPUT REQUIRED]`
- Reviewer: `[TEAM INPUT REQUIRED]`
- Design decisions to confirm: tree node schema, element ID stability, `bibitem` parser scope, and cleanup policy.
- Additional notes: `[TEAM INPUT REQUIRED]`

## 10. Ownership

P1 owns root discovery, hierarchy construction, `\bibitem` conversion,
citation-key deduplication, and citation rewriting. P2 owns BibTeX/reference
cleaning and candidate generation. P3 owns LaTeX cleanup, math
normalization, sentence splitting, content deduplication, and `element_id`.
P4 owns Parquet schemas/export, M2 reporting, and before/after statistics.


---
