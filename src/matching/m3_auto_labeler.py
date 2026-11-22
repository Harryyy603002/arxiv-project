r"""m3_auto_labeler.py

Milestone: M3
Người phụ trách: P2
Mục đích: Matcher tự động (regex / string similarity) gán nhãn cho ít nhất 10% số bài ngoài
          nhóm gán tay và tối thiểu 2 bài.

Thuật toán (không dùng ML, chỉ dùng heuristic xác định):
  Với mỗi BibTeX entry, duyệt tất cả candidate pairs của bài đó rồi áp dụng các luật theo thứ tự:
    1. title_exact == True                                     -> gán
    2. title_sim >= 0.85  AND  author_sim >= 0.60              -> gán
    3. title_sim >= 0.70  AND  year_exact == 1.0               -> gán (year_exact tính qua compute_string_features)
  Chỉ lấy candidate đầu tiên khớp; nếu không có luật nào khớp -> groundtruth_arxiv_id = ''

Đầu vào:
  - <corpus_dir>/<paper_key>/candidates.json   (bắt buộc; cặp BibTeX-reference kèm features)
  - data/labels/manual_labels.csv              (tùy chọn; để loại bài đã gán tay)

Đầu ra:
  - data/labels/auto_labels.csv
      cột: arxiv_id, citation_key, groundtruth_arxiv_id
      (groundtruth_arxiv_id để trống nếu không tìm thấy reference tương ứng)

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.

Chạy:
    python src/matching/m3_auto_labeler.py --corpus-dir data
    python src/matching/m3_auto_labeler.py --corpus-dir data \
        --manual-labels data/labels/manual_labels.csv \
        --output data/labels/auto_labels.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_candidates import generate_candidates
from matching.m3_string_features import compute_string_features
from utils.config import DATA_DIR, paper_dir  # noqa: F401

# ── matching thresholds ────────────────────────────────────────────────────────
_EXACT_TITLE = True       # rule 1: normalized title identical
_HIGH_TITLE = 0.85        # rule 2: strong title match
_HIGH_AUTHOR = 0.60       # rule 2: author complement
_MED_TITLE = 0.70         # rule 3: moderate title match
# rule 3 also requires year_exact from compute_string_features


def _match_entry(entry_candidates: list[dict]) -> str:
    """Apply deterministic rules to one BibTeX entry's candidate list.

    Returns ref_arxiv_id of the best match, or '' when no rule fires.
    Candidates must all belong to the same (arxiv_id, citation_key) pair.
    """
    for cand in entry_candidates:
        feats = compute_string_features(cand)
        title_sim = feats["title_sim"]
        author_sim = feats["author_sim"]
        title_exact = feats["title_exact"]
        year_exact = feats["year_exact"]

        if title_exact:                                               # rule 1
            return cand.get("ref_arxiv_id") or ""
        if title_sim >= _HIGH_TITLE and author_sim >= _HIGH_AUTHOR:  # rule 2
            return cand.get("ref_arxiv_id") or ""
        if title_sim >= _MED_TITLE and year_exact:                   # rule 3
            return cand.get("ref_arxiv_id") or ""
    return ""


def _load_manual_pool(manual_labels_path: Path | None) -> set[str]:
    """Return set of arxiv_ids already manually labeled."""
    if not manual_labels_path or not manual_labels_path.exists():
        return set()
    with manual_labels_path.open(encoding="utf-8", newline="") as fh:
        return {row["arxiv_id"] for row in csv.DictReader(fh)}


def _candidates_for_paper(pdir: Path) -> list[dict]:
    """Load pre-computed candidates.json, or generate from bibtex/refs on the fly."""
    cand_file = pdir / "candidates.json"
    if cand_file.exists():
        return json.loads(cand_file.read_text(encoding="utf-8"))

    # Fallback: generate from raw files if candidates.json is absent
    bib_file = pdir / "bibtex_merged.json"
    if not bib_file.exists():
        bib_file = pdir / "bibtex.json"
    refs_file = pdir / "refs.json"
    if not bib_file.exists() or not refs_file.exists():
        return []

    bibtex = json.loads(bib_file.read_text(encoding="utf-8"))
    refs = json.loads(refs_file.read_text(encoding="utf-8"))
    # Keep only refs that have an arXiv ID (required for a useful label)
    refs_with_arxiv = [r for r in refs if r.get("ref_arxiv_id") or (r.get("externalIds") or {}).get("ArXiv")]
    return generate_candidates(bibtex, refs_with_arxiv)


def label_paper(pdir: Path) -> list[dict]:
    """Return auto-label rows for all BibTeX entries of one paper."""
    candidates = _candidates_for_paper(pdir)
    if not candidates:
        return []

    # Group candidates by citation_key
    groups: dict[str, list[dict]] = {}
    for cand in candidates:
        key = cand.get("citation_key", "")
        groups.setdefault(key, []).append(cand)

    rows = []
    arxiv_id = candidates[0].get("arxiv_id", pdir.name.replace("-", "."))
    for citation_key, cands in groups.items():
        # Sort by title_similarity desc so highest-scoring candidate is tried first
        cands_sorted = sorted(cands, key=lambda c: c.get("title_similarity", 0.0), reverse=True)
        groundtruth = _match_entry(cands_sorted)
        rows.append({
            "arxiv_id": arxiv_id,
            "citation_key": citation_key,
            "groundtruth_arxiv_id": groundtruth,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Tự động gán nhãn BibTeX↔reference (M3).")
    ap.add_argument("--corpus-dir", type=Path, default=DATA_DIR, help="Thư mục corpus (data/)")
    ap.add_argument("--manual-labels", type=Path, default=None,
                    help="CSV nhãn thủ công; các bài trong đây sẽ bị bỏ qua")
    ap.add_argument("--output", type=Path, default=None,
                    help="File đầu ra CSV (mặc định: <corpus_dir>/labels/auto_labels.csv)")
    ap.add_argument("--min-title-sim", type=float, default=0.35,
                    help="Ngưỡng title similarity để sinh candidate (truyền qua generate_candidates)")
    args = ap.parse_args()

    corpus_dir: Path = args.corpus_dir
    output_path: Path = args.output or (corpus_dir / "labels" / "auto_labels.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manual_pool = _load_manual_pool(args.manual_labels)

    # Collect all paper directories outside the manual pool
    paper_dirs = sorted(
        d for d in corpus_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
        and d.name.replace("-", ".") not in manual_pool
        and d.name not in manual_pool
    )

    total_outside = len(paper_dirs)
    min_required = max(2, int(total_outside * 0.10))

    print(f"Corpus: {corpus_dir}")
    print(f"Bài ngoài manual pool: {total_outside}  |  Cần gán nhãn tự động: >= {min_required}")

    all_rows: list[dict] = []
    labeled_papers = 0

    for pdir in paper_dirs:
        rows = label_paper(pdir)
        if rows:
            all_rows.extend(rows)
            labeled_papers += 1

    if labeled_papers < min_required:
        print(
            f"CẢNH BÁO: Chỉ gán nhãn được {labeled_papers} bài "
            f"(cần >= {min_required}). Kiểm tra candidates.json.",
            file=sys.stderr,
        )

    fieldnames = ["arxiv_id", "citation_key", "groundtruth_arxiv_id"]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    matched = sum(1 for r in all_rows if r["groundtruth_arxiv_id"])
    print(
        f"Đã gán nhãn {labeled_papers} bài, "
        f"{len(all_rows)} entries, "
        f"{matched} có match ({matched/len(all_rows)*100:.1f}%)."
        if all_rows else "Không có entry nào được gán nhãn."
    )
    print(f"Output -> {output_path}")


if __name__ == "__main__":
    main()
