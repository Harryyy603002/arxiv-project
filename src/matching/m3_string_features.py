r"""m3_string_features.py

Milestone: M3
Người phụ trách: P2
Mục đích: Tính vector feature string-similarity cho từng cặp ứng viên (BibTeX entry, reference).

Đầu vào:
  - candidates.json  mỗi thư mục bài báo (do m3_candidates.generate_candidates sinh ra)
  - data/labels/auto_labels.csv  (tùy chọn, để gắn nhãn vào feature rows)

Đầu ra:
  - data/labels/string_features.jsonl  — mỗi dòng là một JSON object:
      candidate_id, arxiv_id, citation_key, ref_arxiv_id, <feature keys...>, [label]

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.

Chạy:
    python src/matching/m3_string_features.py --corpus-dir data
    python src/matching/m3_string_features.py --corpus-dir data --labels data/labels/auto_labels.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_candidates import normalize_text, normalize_authors, similarity
from utils.config import DATA_DIR, paper_dir  # noqa: F401


def _year(value: object) -> int | None:
    """Extract a 4-digit year from a string or int; return None if absent."""
    text = str(value or "").strip()
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize_text(text)))


def compute_string_features(candidate: dict) -> dict:
    """Return a flat dict of float features in [0, 1] for one candidate pair.

    Expected candidate keys (from m3_candidates.generate_candidates):
        bib_title, ref_title, bib_authors_normalized, ref_authors_normalized,
        title_similarity, author_similarity, bib_title_normalized,
        ref_title_normalized, title_exact.
    Additional BibTeX fields used if present: year, venue/journal.
    """
    bib_title_norm = candidate.get("bib_title_normalized") or normalize_text(
        candidate.get("bib_title", "")
    )
    ref_title_norm = candidate.get("ref_title_normalized") or normalize_text(
        candidate.get("ref_title", "")
    )
    bib_authors = candidate.get("bib_authors_normalized") or normalize_authors(
        candidate.get("author", candidate.get("authors", ""))
    )
    ref_authors = candidate.get("ref_authors_normalized") or normalize_authors(
        candidate.get("ref_authors", "")
    )

    title_sim: float = candidate.get("title_similarity") or similarity(
        bib_title_norm, ref_title_norm
    )
    author_sim: float = candidate.get("author_similarity") or similarity(
        bib_authors, ref_authors
    )
    title_exact: float = float(
        candidate.get("title_exact") or (bib_title_norm == ref_title_norm and bool(bib_title_norm))
    )

    # Token-level Jaccard on title words (robust to word-order permutations)
    bib_tokens = _token_set(bib_title_norm)
    ref_tokens = _token_set(ref_title_norm)
    if bib_tokens or ref_tokens:
        title_jaccard = len(bib_tokens & ref_tokens) / len(bib_tokens | ref_tokens)
    else:
        title_jaccard = 0.0

    # Title length ratio: shorter / longer (penalises very different-length titles)
    bib_len = len(bib_title_norm)
    ref_len = len(ref_title_norm)
    title_len_ratio = (min(bib_len, ref_len) / max(bib_len, ref_len)) if max(bib_len, ref_len) > 0 else 0.0

    # Year features
    bib_year = _year(candidate.get("year"))
    ref_year = _year(candidate.get("ref_year") or candidate.get("publicationDate") or candidate.get("year"))
    if bib_year and ref_year:
        year_exact = float(bib_year == ref_year)
        year_close = float(abs(bib_year - ref_year) <= 1)
    else:
        year_exact = 0.0
        year_close = 0.0

    # First-author last-name overlap: check whether the leading last name from
    # bib appears anywhere in the ref author string (handles "Smith et al." style).
    first_author_match = 0.0
    if bib_authors:
        first_name = bib_authors.split()[0] if bib_authors.split() else ""
        if first_name and len(first_name) > 2 and first_name in ref_authors:
            first_author_match = 1.0

    # Venue / journal similarity (present only in some entries)
    bib_venue = normalize_text(candidate.get("journal") or candidate.get("booktitle") or "")
    ref_venue = normalize_text(candidate.get("ref_venue") or candidate.get("venue") or "")
    venue_sim = similarity(bib_venue, ref_venue) if bib_venue and ref_venue else 0.0

    return {
        "title_sim": round(title_sim, 6),
        "author_sim": round(author_sim, 6),
        "title_exact": title_exact,
        "title_jaccard": round(title_jaccard, 6),
        "title_len_ratio": round(title_len_ratio, 6),
        "year_exact": year_exact,
        "year_close": year_close,
        "first_author_match": first_author_match,
        "venue_sim": round(venue_sim, 6),
    }


def _load_labels(labels_path: Path) -> dict[tuple[str, str], str]:
    """Return {(arxiv_id, citation_key): groundtruth_arxiv_id}."""
    if not labels_path.exists():
        return {}
    out: dict[tuple[str, str], str] = {}
    with labels_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[(row["arxiv_id"], row["citation_key"])] = row.get("groundtruth_arxiv_id", "")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Tính string-similarity features cho candidate pairs.")
    ap.add_argument("--corpus-dir", type=Path, default=DATA_DIR, help="Thư mục corpus (data/)")
    ap.add_argument("--labels", type=Path, help="CSV nhãn (auto hoặc manual) để gắn vào output")
    ap.add_argument("--output", type=Path, default=None, help="Đường dẫn output JSONL")
    args = ap.parse_args()

    corpus_dir: Path = args.corpus_dir
    output_path: Path = args.output or (corpus_dir / "labels" / "string_features.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    labels = _load_labels(args.labels) if args.labels else {}

    paper_dirs = [d for d in corpus_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    total_rows = 0
    total_papers = 0

    with output_path.open("w", encoding="utf-8") as out_fh:
        for pdir in sorted(paper_dirs):
            cand_file = pdir / "candidates.json"
            if not cand_file.exists():
                continue
            candidates = json.loads(cand_file.read_text(encoding="utf-8"))
            if not candidates:
                continue
            total_papers += 1
            for cand in candidates:
                feats = compute_string_features(cand)
                row = {
                    "candidate_id": cand.get("candidate_id"),
                    "arxiv_id": cand.get("arxiv_id"),
                    "citation_key": cand.get("citation_key"),
                    "ref_arxiv_id": cand.get("ref_arxiv_id"),
                    **feats,
                }
                key = (cand.get("arxiv_id", ""), cand.get("citation_key", ""))
                if key in labels:
                    row["label"] = 1 if cand.get("ref_arxiv_id") == labels[key] else 0
                out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                total_rows += 1

    print(f"Đã xử lý {total_papers} bài, {total_rows} dòng feature -> {output_path}")


if __name__ == "__main__":
    main()
