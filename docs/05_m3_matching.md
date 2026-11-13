# M3 — Reference Matching Pipeline

## 1. Overview

Milestone 3 matches M2's BibTeX entries against M1's crawled reference metadata. For each BibTeX entry, you must find the arXiv ID of the reference it cites. The output is a ranked top-5 list per entry, evaluated with **MRR@5**.

**Current status: mostly skeleton.** Only candidate generation is implemented. Everything from labeling onward still needs to be written.

| Module | Status | Owner |
|--------|--------|-------|
| `m3_candidates.py` | ✅ Implemented | P2 |
| `m3_auto_labeler.py` | ⚠️ Skeleton | P2 |
| `m3_string_features.py` | ⚠️ Skeleton | P4 |
| `m3_context_features.py` | ⚠️ Skeleton | P4 |
| `m3_model.py` | ⚠️ Skeleton | P4 |
| `m3_evaluate.py` | ⚠️ Skeleton | P4 |
| `m3_predictions.py` | ⚠️ Skeleton | P4 |
| `m3_publish_hf.py` | ⚠️ Skeleton | P4 |
| `run_m3.py` | ⚠️ Skeleton | P4 |

---

## 2. The Problem, Concretely

Paper `2310.12345` cites 40 works. M2 extracted 40 BibTeX entries from its `.tex`/`.bib` files. M1 crawled 60 references for the same paper from Semantic Scholar, of which 35 have arXiv IDs.

You must produce, for each of the 40 BibTeX entries, a ranked list of 5 candidate arXiv IDs from those 35 — with the correct one ranked as high as possible.

It is a **binary matrix problem** per paper:

```
              ref_1   ref_2   ref_3  ...  ref_35
entry_1  [    0       1       0    ...    0    ]
entry_2  [    0       0       0    ...    0    ]   ← no true match exists
entry_3  [    1       0       0    ...    0    ]
   ...
```

Each labeled row has exactly one `1` (or all zeros if the entry cites something not in the crawled set).

---

## 3. Implemented: `m3_candidates.py`

**Purpose:** Generate (BibTeX entry, reference) pairs with comparable string-similarity features. This is the input to every downstream M3 step.

### Key functions

```python
from matching.m3_candidates import (
    normalize_text, normalize_authors, similarity, generate_candidates,
)
```

#### `normalize_text(value) -> str`

```python
normalize_text(r"\textbf{Attention} Is All You Need")
# → 'attention is all you need'
```

Steps: strip LaTeX commands, remove braces, NFKD Unicode normalize, drop combining marks (so `ö` → `o`), collapse whitespace, casefold.

#### `normalize_authors(value) -> str`

Accepts a list of dicts (`[{"name": "..."}]`), a list of strings, or a `"A and B"` string. Splits on ` and `, converts `"Vaswani, Ashish"` → `"ashish vaswani"`, joins with spaces.

#### `similarity(left, right) -> float`

`SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()` — returns `0.0` when either side is empty.

#### `generate_candidates(bib_entries, references, min_title_similarity=0.35, diagnostics=None) -> list[dict]`

For every (bib entry, reference) pair:

1. Compute `title_score = similarity(bib_title, ref_title)`.
2. **Skip the pair** if `title_score < 0.35`. Increment `diagnostics["missing_title"]` or `diagnostics["title_below_threshold"]`.
3. Compute `author_similarity`.
4. Emit a candidate row.

**Candidate row shape:**

```json
{
  "candidate_id": "a3f8e2b1c4d5e6f7",
  "arxiv_id": "2310.12345",
  "citation_key": "vaswani2017",
  "ref_s2_id": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
  "ref_arxiv_id": "1706.03762",
  "reference_index": 0,
  "title_similarity": 1.0,
  "author_similarity": 0.95,
  "bib_title": "Attention Is All You Need",
  "ref_title": "Attention Is All You Need",
  "title_exact": true,
  "bib_title_normalized": "attention is all you need",
  "ref_title_normalized": "attention is all you need",
  "bib_authors_normalized": "ashish vaswani noam shazeer",
  "ref_authors_normalized": "a vaswani n shazeer"
}
```

`candidate_id` is a 16-hex-character SHA-256 prefix of `{arxiv_id, citation_key, ref_s2_id, ref_arxiv_id, reference_index}` — stable across runs.

**Run:** `generate_candidates` is called from `run_m2.process_paper`; you do not invoke it directly.

---

## 4. What Still Needs Building

### 4.1 Data Cleaning (`m3_cleanup` — not yet a file)

The statement requires additional preprocessing before matching. `normalize_text` and `normalize_authors` already cover most of it (lowercasing, LaTeX stripping, Unicode folding, author reordering). Remaining useful additions:

- **Stop-word removal** on titles before similarity, so "a study of the" does not inflate scores.
- **Token-level similarity** (Jaccard, token-sort ratio) alongside `SequenceMatcher`, because word order differs between BibTeX and Semantic Scholar titles far more often than characters do.
- **Year proximity** as a feature — a candidate whose `ref_submission_date` is far from the BibTeX `year` is unlikely.

`rapidfuzz` is already in `src/requirements.txt` — use it for `token_sort_ratio` / `partial_ratio`, which are far faster than `SequenceMatcher` at corpus scale.

### 4.2 Data Labeling (`m3_auto_labeler.py`)

**Requirements (per the statement):**

| Requirement | Value |
|-------------|-------|
| Manually labeled publications | ≥ 5 per member (≥ 20 for k=4) |
| Labels per manually labeled publication | ≥ 20 BibTeX entries |
| Total manual labels | ≥ 100 per member (≥ 400 for k=4) |
| Automatically matched publications | ≥ 10 % of the non-manual pool, and ≥ 2 × k (≥ 8) |

**Suggested output format** — an explicit CSV so it is auditable and documentable in the repo:

```
data/labels/manual_labels.csv
arxiv_id,citation_key,groundtruth_ref_arxiv_id,labeler
2310.12345,vaswani2017,1706.03762,P1
2310.12345,some_book,,P1
```

An empty `groundtruth_ref_arxiv_id` means "no crawled reference matches this entry" — an all-negative row, which the statement explicitly allows.

**Auto-labeler approach** (regex / string similarity only — no LLMs, per the team plan):

```python
def auto_label(candidates, threshold=0.90):
    # One accepted match per citation_key: highest title_similarity
    best = {}
    for cand in candidates:
        key = cand["citation_key"]
        if cand["title_similarity"] >= threshold and cand["ref_arxiv_id"]:
            if key not in best or cand["title_similarity"] > best[key]["title_similarity"]:
                best[key] = cand
    return best
```

Then verify the automatic labels against a held-out slice of your own manual labels to report precision in the poster.

### 4.3 Feature Engineering (`m3_string_features.py`, `m3_context_features.py`)

**String features** (per candidate row):

| Feature | Source |
|---------|--------|
| `title_similarity` | already in `candidates.json` |
| `author_similarity` | already in `candidates.json` |
| `title_exact` | already in `candidates.json` |
| `title_token_jaccard` | new — token set overlap |
| `author_overlap_count` | new — number of shared author surnames |
| `year_match` | new — `bibtex.year == ref_submission_date[:4]` |
| `year_distance` | new — absolute difference |

**Context features** — the statement explicitly allows using the M2 hierarchy:

| Feature | Idea |
|---------|------|
| `section_of_citation` | A citation in "Related Work" behaves differently from one in "Experiments" |
| `n_citations_in_sentence` | How many `\cite{}` share the sentence |
| `citation_position` | First / middle / last in the paper |
| `is_first_citation` | First-ever citation of a key |

To compute these you need the paragraph/sentence that contained the `\cite{key}`. That information is **not** currently preserved by `m2_content_dedup` — the element content keeps `\cite{key}` inline, so you can recover it by scanning elements for `\cite{<key>}` and walking `hierarchy.json` upward to the nearest heading. This is a real piece of work, budget a day for it.

### 4.4 Modeling (`m3_model.py`)

**Task framing:** rank candidate references per BibTeX entry. Practical choice for scikit-learn:

- **Binary classifier + sort** — train `GradientBoostingClassifier` or `RandomForestClassifier` on `(entry, candidate)` rows with `label ∈ {0,1}`, then sort candidates per entry by predicted probability and take the top 5. Simple, well understood, good enough for MRR@5.
- **Alternative:** `LGBMRanker` if you add LightGBM — but that adds a dependency; stick to scikit-learn per the team plan.

**Split (per the statement, scaled to k=4 members):**

| Set | Composition |
|-----|-------------|
| Test | 4 manually labeled + 4 automatically matched publications |
| Validation | 4 manually labeled + 4 automatically matched publications |
| Train | everything else |

Split **by publication, never by row** — splitting by row leaks references across the train/test boundary.

### 4.5 Evaluation (`m3_evaluate.py`)

```python
def mrr_at_5(ranked_candidates, groundtruth):
    """ranked_candidates: list of top-5 arXiv IDs.
       groundtruth: the correct arXiv ID, or None.
       Returns reciprocal rank (0 if not found in top 5)."""
    if groundtruth is None:
        return 0.0
    if groundtruth in ranked_candidates:
        return 1.0 / (ranked_candidates.index(groundtruth) + 1)
    return 0.0
```

Final MRR@5 = mean over all labeled test entries. The statement defines `|Q|` as *all* labeled BibTeX entries in the Test set, including entries with no true reference (which contribute 0).

> **Check with your instructor:** whether entries with `groundtruth = None` should be counted in `|Q|`. The formula counts them as scoring 0; that is the literal reading, but it depresses your score for reasons unrelated to your model. Document whichever choice you make.

### 4.6 Predictions table (`m3_predictions.py`)

**Columns the final table must have** (note the statement lists `cand_1`…`cand_5` as separate columns):

| Column | Type |
|--------|------|
| `arxiv_id` | string, non-null |
| `partition` | dictionary: `train` / `valid` / `test` |
| `citation_key` | string, non-null |
| `groundtruth_arxiv_id` | string, nullable |
| `cand_1` … `cand_5` | string, nullable |

> **Schema mismatch to fix:** `src/utils/schemas.py` currently defines `PREDICTIONS_SCHEMA` with a single `predicted_arxiv_ids` list column and no `partition` column. The statement requires five separate `cand_N` columns plus `partition`. Update `PREDICTIONS_SCHEMA` before final export:
>
> ```python
> PREDICTIONS_SCHEMA = pa.schema([
>     pa.field("arxiv_id", pa.string(), nullable=False),
>     pa.field("partition", DICT_STRING, nullable=True),
>     pa.field("citation_key", pa.string(), nullable=False),
>     pa.field("groundtruth_arxiv_id", pa.string(), nullable=True),
>     pa.field("cand_1", pa.string(), nullable=True),
>     pa.field("cand_2", pa.string(), nullable=True),
>     pa.field("cand_3", pa.string(), nullable=True),
>     pa.field("cand_4", pa.string(), nullable=True),
>     pa.field("cand_5", pa.string(), nullable=True),
> ])
> ```

Only publications in train/valid/test get rows — publications outside the three sets must be excluded, per the statement.

### 4.7 Hugging Face publishing (`m3_publish_hf.py`)

Six sharded Parquet tables, each in its own subfolder, with a dataset card:

```
<dataset-repo>/
├── README.md          # YAML front-matter mapping configs
└── data/
    ├── papers/
    ├── references/
    ├── elements/
    ├── hierarchy/
    ├── bibtex/
    └── predictions/
```

`huggingface_hub` is already in `src/requirements.txt`. Minimal upload:

```python
from huggingface_hub import HfApi
api = HfApi()
api.upload_folder(folder_path="data/hf", repo_id="<user>/<dataset>", repo_type="dataset")
```

---

## 5. Minimum Viable Path (if time is short)

1. Auto-labeler with `title_similarity >= 0.90` → gives you the 10 % automatic pool.
2. Manual labels for 5 papers × 20 entries = 100 entries (minimum for one person; multiply by group size).
3. Features: use only `title_similarity`, `author_similarity`, `title_exact` — already in `candidates.json`, zero extra work.
4. Model: `LogisticRegression` on those three features. Sort by `predict_proba`, take top 5.
5. `mrr_at_5` evaluation.
6. Emit the predictions table.

That satisfies every grading criterion at a basic level. Context features and year features are the upgrade path to a better poster.

---

## 6. Team Review

- **Author:** P2 (candidates, auto-labeler), P4 (features, model, evaluation, publishing)
- **Reviewer:** P2 ↔ P4
- **Design decision:** candidate generation uses title similarity as a *gate* (0.35) rather than generating all m×n pairs, to keep the matrix tractable. `candidate_generation_skipped_by_reason` in `m2_stats.json` records how many pairs this removed — report that number, since the grader will ask.
- **Open question:** threshold of 0.35 is untuned. Once you have manual labels, measure recall of the correct reference within the generated candidate set. If it is below ~95 %, lower the threshold.
- **Constraint to respect:** the team plan commits to classical ML. Do not introduce LLMs or pretrained embeddings — the statement says such work is judged separately.