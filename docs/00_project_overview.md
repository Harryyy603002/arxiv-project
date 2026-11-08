# Project overview

This document records the project objective, official milestone requirements, repository status, and team ownership. It describes the checked-out `main` branch, not assumptions from other branches.

## Project objective

Build a citation-matching dataset from assigned arXiv publications. M1 discovers papers, versions, LaTeX source, metadata, and references. M2 structures and standardizes the source, extracts and deduplicates BibTeX, and prepares hierarchy/content tables. M3 matches BibTeX entries to Semantic Scholar references, evaluates ranked predictions with MRR@5, and publishes the final tables.

## Evidence status

- **Implemented:** executable behavior exists and repository tests support it.
- **Partial:** library logic or a contract exists, but the end-to-end stage is incomplete.
- **Skeleton:** the entry point raises `NotImplementedError`.
- **Unverified:** the repository does not prove the claim.

The source documents are now available in this documentation directory:

- `Tổng-quan-Đồ-án-arXiv.pdf`
- `Ke_hoach_phan_chia_cong_viec_do_an_arXiv.pdf`

The overview describes an 18-day, three-milestone project running from
19/10/2026 through 05/11/2026, with the formal submission expected on
06/11/2026. Work is intentionally overlapped: M2 can begin while M1 crawls,
and M3 labeling can begin when M2 produces candidate files.

The project deliverables are the source repository, Jupyter Book/GitHub Pages
reports, a cleaned Hugging Face dataset and model publication, and an A2
poster. M3 is planned as a classical machine-learning pipeline using string
similarity, manually designed context features, and scikit-learn models; the
team plan explicitly does not rely on LLMs or pretrained models.

The team plan specifies buddy review pairs P1 ↔ P3 and P2 ↔ P4. The
implementation status below is still based on the checked-out `main` branch,
not on the schedule's intended completion claims.

## Team review

- Author: `[TEAM INPUT REQUIRED]`
- Reviewer: `[TEAM INPUT REQUIRED]`
- Design rationale to confirm: `[TEAM INPUT REQUIRED]`
- Additional notes: `[TEAM INPUT REQUIRED]`

## Reference documents

The project overview and work-allocation evidence used for this document are
the two PDFs listed above. Official milestone requirements remain linked from
[`docs/README.md`](README.md).


---
