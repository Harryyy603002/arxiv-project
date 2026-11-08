# M3 — Reference matching

## 1. Overview and requirements

M3 matches unified M2 BibTeX entries to arXiv-keyed M1 references within the same publication. The official pipeline requires cleaning, manual and automatic labeling, feature engineering, publication-level train/validation/test splits, ranking, top-five predictions, MRR@5 evaluation, and a final predictions table.

On `main`, only lightweight candidate generation and normalization are executable. Labeling, context features, model training, evaluation, predictions, publishing, and `run_m3.py` are skeletons.

The team plan requires a classical baseline: string similarity, hand-designed
context features, and scikit-learn modeling. It explicitly treats LLM or
pretrained-model use as outside the planned approach. The intended M3 window
is 01/11/2026–05/11/2026, with manual labeling starting immediately after M2
candidate files are available.

## 2. Repository modules

| Module | Responsibility | Status |
|---|---|---|
| `m3_candidates.py` | candidate pairs and title/author similarity | Implemented library |
| `m3_string_features.py` | feature vector generation | Skeleton |
| `m3_context_features.py` | hierarchy/citation context | Skeleton |
| `m3_auto_labeler.py` | automatic labels | Skeleton |
| `m3_model.py` | split and model | Skeleton |
| `m3_evaluate.py` | MRR@5 | Skeleton |
| `m3_predictions.py` | predictions table | Skeleton |
| `m3_publish_hf.py` | Hugging Face publishing | Skeleton |

## 3. Function documentation

### `normalize_text(value)`

- **Purpose:** produce comparison text without mutating source fields.
- **Workflow:** Unicode NFKC normalization; replace braces/backslashes with spaces; collapse whitespace; strip; case-fold.
- **Returns:** string.

### `normalize_authors(value)`

- **Inputs:** list of strings/dictionaries or any other value.
- **Workflow:** extract dictionary `name` values where applicable, normalize each, join with spaces.
- **Returns:** comparison string.

### `similarity(left, right)`

- **Implementation:** `difflib.SequenceMatcher` ratio over normalized strings.
- **Returns:** float in the comparator's ratio range.
- **Design note:** `rapidfuzz` is declared but not used; replacing it changes scores and thresholds.

### `generate_candidates(bib_entries, references, min_title_similarity=0.35)`

- **Purpose:** produce publication-local BibTeX/reference candidate rows.
- **Workflow:** compare every BibTeX/reference pair; normalize title/authors; calculate title and author similarity; discard title scores below threshold; preserve source titles and identifiers.
- **Returns:** list of dictionaries containing source/reference identifiers, similarity features, and titles.
- **Limitations:** does not explicitly filter missing `ref_arxiv_id`; identical empty strings can receive a misleading perfect similarity.

## 4. Labeling

The official minimum is five manually labeled publications per member, at least 20 labeled entries per manual publication, at least 100 labeled entries per member, and automatic labels for at least 10% of publications outside the manual pool. README proposes `manual_labels.csv` and `auto_labels.csv` with `arxiv_id,citation_key,groundtruth_arxiv_id`. No label files or labeler implementation exists. Counts and empty-ground-truth policy are `[TEAM INPUT REQUIRED]`.

## 5. Feature engineering

Implemented features are normalized title, normalized authors, title similarity, and author similarity. Context features from hierarchy/citation location and learned features are planned but absent. The official candidate space is an `m × n` publication-local binary matrix; no matrix builder exists.

## 6. Models and evaluation

The official split reserves one manual and one automatic publication for validation and the same for test, with remaining publications for training. Predictions rank five candidates. MRR@5 gives reciprocal rank when the correct reference is in the top five and zero otherwise. No model, split, predictions writer, or evaluator is implemented; no MRR value should be reported.

## 7. Testing and validation

Candidate behavior is covered indirectly by `test_m2_parser.py`. No tests cover labels, features, splits, modeling, predictions, MRR@5, or publishing. No M3 runtime or quality metric is recorded.

## 8. Team review

- Author: `[TEAM INPUT REQUIRED]`
- Reviewer: `[TEAM INPUT REQUIRED]`
- Design decisions to confirm: matching model, feature list, label policy, publication split, and RapidFuzz decision.
- Additional notes: `[TEAM INPUT REQUIRED]`

## 9. Ownership

P1 owns citation-position and section-context features. P2 owns string
similarity, automatic matching, and automatic labels. P3 owns publication
splits, model training, validation feature selection, and MRR@5. P4 owns the
predictions table, dataset card, Hugging Face upload, README, and poster
delivery.


---
