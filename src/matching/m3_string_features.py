r"""m3_string_features.py

Milestone: M3
Người phụ trách: P2
Mục đích: Tính vector feature string-similarity + định danh (eprint/DOI) cho từng cặp ứng viên
          (BibTeX entry, reference).

Quy ước giá trị thiếu (M3_PLAN §4.5): feature nào KHÔNG đủ dữ liệu để tính thì trả NaN
(ghi thành null trong Parquet), không điền 0.0. Điền 0.0 sẽ lẫn "không biết" với "khác nhau".
LightGBM nhận NaN trực tiếp; LogisticRegression dùng SimpleImputer ở m3_model.

Đầu vào:
  - <paper_dir>/candidates.json, bibtex_merged.json, refs.json
  - data/labels/auto_labels.csv  (tùy chọn, để gắn nhãn vào feature rows)

Đầu ra:
  - data/labels/string_features.jsonl  — mỗi dòng: candidate_id, arxiv_id, citation_key,
    ref_arxiv_id, <feature keys...>, [label]

Hàm dùng chung (m3_auto_labeler và run_m3 đều gọi, để luật gán nhãn và feature không lệch nhau):
  enrich_candidate(cand, bib, ref)  - gắn năm, venue, tác giả thô, eprint/doi match vào candidate
  load_paper_context(pdir)          - đọc bibtex_merged.json + refs.json của một bài

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
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_candidates import (
    bib_arxiv_ids,
    bib_dois,
    canonical_arxiv_id,
    normalize_authors,
    normalize_text,
    reference_arxiv_ids,
    reference_dois,
    similarity,
)
from utils.config import DATA_DIR

NAN = float("nan")

STR_FEATURE_NAMES = (
    "title_sim",
    "author_sim",
    "title_exact",
    "title_jaccard",
    "title_len_ratio",
    "year_exact",
    "year_close",
    "first_author_match",
    "venue_sim",
    "eprint_match",
    "doi_match",
)

_RESERVED_DIRS = {"export", "m3", "labels", "benchmark", "logs", "predictions"}
_SKIP_NAMES = {"others", "other", "al", "et al", "et al."}


def _year(value: object) -> int | None:
    """Extract a 4-digit year from a string or int; return None if absent."""
    text = str(value or "").strip()
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group()) if m else None


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", normalize_text(text)))


def _tri(value: Any) -> float:
    """True/False/None -> 1.0/0.0/NaN."""
    if value is None:
        return NAN
    return 1.0 if bool(value) else 0.0


def _first_author_last(raw: Any) -> str:
    """Họ (token cuối của họ) của tác giả đầu tiên trong chuỗi BibTeX 'A and B' hoặc list.

    Xử lý "Last, First" lẫn "First Last"; bỏ 'others' / 'et al'. Trả '' nếu không xác định.
    """
    if raw is None:
        return ""
    if isinstance(raw, list):
        if not raw:
            return ""
        first = raw[0]
        first = first.get("name", "") if isinstance(first, dict) else str(first)
    else:
        parts = re.split(r"\s+\band\b\s+", str(raw), flags=re.IGNORECASE)
        first = parts[0] if parts else ""
    name = normalize_text(first)
    # BibTeX/IEEE hay viết "C. X. Wang et al" hoặc "A. Smith and others": bỏ đuôi đó trước khi lấy họ
    # (nếu không, token cuối là "al"/"others" và feature luôn sai).
    name = re.sub(r"[\s,]*\b(?:et\.?\s*al\.?|and\s+others|others)\b.*$", "", name).strip(" ,.")
    if not name or name in _SKIP_NAMES:
        return ""
    last = name.split(",", 1)[0].strip() if "," in name else (name.split()[-1] if name.split() else "")
    tokens = re.findall(r"\w+", last)
    last_token = tokens[-1] if tokens else ""
    return last_token if len(last_token) >= 2 else ""


def _ref_author_tokens(raw: Any) -> set[str]:
    """Mọi token tên của các tác giả reference (đã chuẩn hóa)."""
    if raw is None:
        return set()
    names = raw if isinstance(raw, list) else re.split(r"\s+\band\b\s+|;", str(raw))
    tokens: set[str] = set()
    for item in names:
        text = item.get("name", "") if isinstance(item, dict) else str(item)
        tokens |= _token_set(text)
    return tokens


def compute_string_features(candidate: dict) -> dict:
    """Return a flat dict of float features for one candidate pair (NaN = không đủ dữ liệu).

    Khóa candidate (từ m3_candidates.generate_candidates):
        bib_title, ref_title, bib_authors_normalized, ref_authors_normalized,
        title_similarity, author_similarity, bib_title_normalized, ref_title_normalized, title_exact.
    Khóa bổ sung do enrich_candidate gắn (thiếu thì feature tương ứng là NaN):
        year, ref_year, journal/booktitle, ref_venue, bib_author_raw, ref_authors_raw,
        eprint_match, doi_match.
    """
    bib_title_norm = candidate.get("bib_title_normalized") or normalize_text(candidate.get("bib_title", ""))
    ref_title_norm = candidate.get("ref_title_normalized") or normalize_text(candidate.get("ref_title", ""))
    bib_authors = candidate.get("bib_authors_normalized") or normalize_authors(
        candidate.get("author", candidate.get("authors", ""))
    )
    ref_authors = candidate.get("ref_authors_normalized") or normalize_authors(candidate.get("ref_authors", ""))

    title_sim: float = candidate.get("title_similarity") or similarity(bib_title_norm, ref_title_norm)
    author_sim: float = candidate.get("author_similarity") or similarity(bib_authors, ref_authors)
    title_exact: float = float(
        candidate.get("title_exact") or (bib_title_norm == ref_title_norm and bool(bib_title_norm))
    )

    # Token-level Jaccard on title words (robust to word-order permutations)
    bib_tokens = _token_set(bib_title_norm)
    ref_tokens = _token_set(ref_title_norm)
    title_jaccard = len(bib_tokens & ref_tokens) / len(bib_tokens | ref_tokens) if (bib_tokens or ref_tokens) else 0.0

    # Title length ratio: shorter / longer
    bib_len, ref_len = len(bib_title_norm), len(ref_title_norm)
    title_len_ratio = (min(bib_len, ref_len) / max(bib_len, ref_len)) if max(bib_len, ref_len) > 0 else 0.0

    # Năm: chỉ so khi CẢ HAI phía có năm. Tuyệt đối không mượn năm BibTeX làm năm reference.
    bib_year = _year(candidate.get("year"))
    ref_year = _year(candidate.get("ref_year") or candidate.get("publicationDate"))
    if bib_year and ref_year:
        year_exact = float(bib_year == ref_year)
        year_close = float(abs(bib_year - ref_year) <= 1)
    else:
        year_exact = NAN
        year_close = NAN

    # Họ của tác giả đầu (BibTeX) có xuất hiện trong danh sách tác giả reference không.
    bib_last = _first_author_last(candidate.get("bib_author_raw"))
    ref_name_tokens = _ref_author_tokens(candidate.get("ref_authors_raw"))
    first_author_match = float(bib_last in ref_name_tokens) if (bib_last and ref_name_tokens) else NAN

    # Venue / journal similarity
    bib_venue = normalize_text(candidate.get("journal") or candidate.get("booktitle") or "")
    ref_venue = normalize_text(candidate.get("ref_venue") or candidate.get("venue") or "")
    venue_sim = similarity(bib_venue, ref_venue) if bib_venue and ref_venue else NAN

    return {
        "title_sim": round(title_sim, 6),
        "author_sim": round(author_sim, 6),
        "title_exact": title_exact,
        "title_jaccard": round(title_jaccard, 6),
        "title_len_ratio": round(title_len_ratio, 6),
        "year_exact": year_exact,
        "year_close": year_close,
        "first_author_match": first_author_match,
        "venue_sim": round(venue_sim, 6) if venue_sim == venue_sim else NAN,
        # 1.0 = trùng ID; 0.0 = BibTeX có ID nhưng KHÁC; NaN = BibTeX không có ID để so
        "eprint_match": _tri(candidate.get("eprint_match")),
        "doi_match": _tri(candidate.get("doi_match")),
    }


# ---------------------------------------------------------------------------
# Làm giàu candidate bằng dữ liệu BibTeX/reference (dùng chung với labeler và run_m3)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def index_refs(refs: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """(theo ref_s2_id, theo arXiv ID đã chuẩn hóa). Tra theo ID, không theo reference_index."""
    by_s2: dict[str, dict] = {}
    by_arxiv: dict[str, dict] = {}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        s2 = ref.get("ref_s2_id") or ref.get("paperId")
        if s2:
            by_s2.setdefault(str(s2), ref)
        for rid in reference_arxiv_ids(ref):
            by_arxiv.setdefault(rid, ref)
    return by_s2, by_arxiv


def find_ref(candidate: dict, ref_index: tuple[dict[str, dict], dict[str, dict]]) -> dict:
    by_s2, by_arxiv = ref_index
    ref_id = canonical_arxiv_id(candidate.get("ref_arxiv_id"))
    if ref_id and ref_id in by_arxiv:
        return by_arxiv[ref_id]
    s2 = str(candidate.get("ref_s2_id") or "")
    return by_s2.get(s2, {}) if s2 else {}


def load_paper_context(pdir: Path) -> tuple[dict[str, dict], tuple[dict[str, dict], dict[str, dict]]]:
    """Đọc bibtex_merged.json (theo citation_key) và refs.json (đã index) của một bài."""
    bib_rows = _read_json(pdir / "bibtex_merged.json")
    bib_by_key = {
        str(r.get("citation_key")): r for r in (bib_rows if isinstance(bib_rows, list) else [])
        if isinstance(r, dict) and r.get("citation_key")
    }
    refs_payload = _read_json(pdir / "refs.json")
    refs = refs_payload.get("references", []) if isinstance(refs_payload, dict) else refs_payload
    return bib_by_key, index_refs(refs if isinstance(refs, list) else [])


def enrich_candidate(candidate: dict, bib: dict, ref: dict) -> dict:
    """Trả về bản sao candidate có thêm trường BibTeX/reference mà compute_string_features cần."""
    out = dict(candidate)
    if bib:
        out["year"] = bib.get("year")
        out["journal"] = bib.get("journal")
        out["booktitle"] = bib.get("booktitle")
        out["bib_author_raw"] = bib.get("author", bib.get("authors"))
    if ref:
        out["ref_year"] = ref.get("ref_submission_date") or ref.get("year") or ref.get("publicationDate")
        out["ref_venue"] = ref.get("ref_venue") or ref.get("venue")
        out["ref_authors_raw"] = ref.get("ref_authors", ref.get("authors"))
    if bib and ref:
        b_ids, r_ids = bib_arxiv_ids(bib), reference_arxiv_ids(ref)
        out["eprint_match"] = bool(b_ids & r_ids) if b_ids else None
        b_dois, r_dois = bib_dois(bib), reference_dois(ref)
        out["doi_match"] = bool(b_dois & r_dois) if (b_dois and r_dois) else None
    return out


def _load_labels(labels_path: Path) -> dict[tuple[str, str], str]:
    """Return {(arxiv_id, citation_key): groundtruth_arxiv_id}."""
    if not labels_path.exists():
        return {}
    out: dict[tuple[str, str], str] = {}
    with labels_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[(row["arxiv_id"], row["citation_key"])] = row.get("groundtruth_arxiv_id", "")
    return out


def _jsonable(value: float) -> Optional[float]:
    return None if value != value else value


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

    paper_dirs = [d for d in corpus_dir.iterdir()
                  if d.is_dir() and not d.name.startswith(".") and d.name not in _RESERVED_DIRS]
    total_rows = total_papers = 0

    with output_path.open("w", encoding="utf-8") as out_fh:
        for pdir in sorted(paper_dirs):
            cand_file = pdir / "candidates.json"
            if not cand_file.exists():
                continue
            candidates = _read_json(cand_file)
            if not isinstance(candidates, list) or not candidates:
                continue
            total_papers += 1
            bib_by_key, ref_index = load_paper_context(pdir)
            for cand in candidates:
                enriched = enrich_candidate(cand, bib_by_key.get(str(cand.get("citation_key")), {}),
                                            find_ref(cand, ref_index))
                feats = {k: _jsonable(v) for k, v in compute_string_features(enriched).items()}
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