r"""m3_context_features.py

Milestone: M3 - Reference Matching
Người phụ trách: Nguyen
Mục đích: tính feature ngữ cảnh `ctx_*` cho từng cặp ứng viên (entry BibTeX, reference) và
          ghép với feature chuỗi `str_*` của Tri thành một bảng feature cho mỗi bài.

Hợp đồng dữ liệu (PLAN_M3.md, mục 4.5):
  - Đầu vào (trong <DATA_DIR>/<paper_key>/): candidates.json, bibtex_merged.json, refs.json,
    elements.json, hierarchy.json. Tùy chọn: <DATA_DIR>/m3/features/<paper_key>.str.parquet (Tri).
  - Đầu ra: <DATA_DIR>/m3/features/<paper_key>.parquet
      * ĐÚNG một dòng cho mỗi dòng của candidates.json (khóa chính: candidate_id).
      * Cột khóa: candidate_id, arxiv_id, citation_key, ref_arxiv_id.
      * Sau đó là các cột str_* (nếu có file của Tri) rồi các cột ctx_*.
      * Mọi cột feature là số (float32 hoặc Int8/Int16 nullable); thiếu dữ liệu để NULL (không điền 0/-1).
      * Không chứa nhãn.

Vì sao feature là "theo cặp" hoặc "tương đối trong nhóm" (PLAN_M3.md, 4.0.A3):
  model xếp hạng các ứng viên TRONG MỘT entry, nên feature chỉ phụ thuộc entry (bằng nhau cho mọi
  ứng viên của entry đó) không giúp phân biệt. Các feature chính là:

  Theo cặp (khác nhau giữa các ứng viên của cùng một entry)
    ctx_year_diff            |năm của entry - năm của reference| (năm reference lấy từ ref_submission_date)
    ctx_year_signed          năm reference - năm entry (âm: entry ghi năm muộn hơn bản arXiv)
    ctx_venue_sim            độ giống giữa journal/booktitle của entry và ref_venue
    ctx_ref_venue_is_arxiv   ref_venue của reference chứa "arxiv" (1/0)
    ctx_cite_title_overlap   tỉ lệ từ khóa trong tiêu đề reference xuất hiện trong các câu trích dẫn entry
    ctx_cite_author_mentioned họ tác giả đầu của reference xuất hiện trong các câu trích dẫn entry (1/0)

  Tương đối trong nhóm (dựa trên title_similarity / author_similarity của candidates.json, nên
  không phụ thuộc feature của Tri)
    ctx_n_cand_entry         số ứng viên của entry
    ctx_rank_in_entry        hạng của ứng viên trong entry theo title_similarity (1 = tốt nhất)
    ctx_author_rank_in_entry hạng theo author_similarity trong entry
    ctx_title_margin         title_similarity trừ title_similarity cao nhất của ứng viên KHÁC trong entry
    ctx_n_cand_ref           số entry có reference này làm ứng viên
    ctx_rank_in_ref          hạng của entry trong nhóm entry của reference này (hạng ngược)
    ctx_ref_margin           title_similarity trừ điểm cao nhất của entry KHÁC cho cùng reference
    ctx_mutual_best          hạng 1 ở cả hai chiều (1/0)

  Mức entry (bằng nhau trong một entry, chỉ dùng làm biến phụ)
    ctx_n_cite_sentences     số câu có \cite{khóa}
    ctx_n_cite_sections      số section (heading) khác nhau chứa các câu đó
    ctx_n_source_versions    số version có entry này
    ctx_bib_is_bibitem       entry đến từ \bibitem (1) hay .bib (0)

Chạy (từ thư mục gốc repo):
    python src/matching/m3_context_features.py --corpus-dir /content/corpus
    python src/matching/m3_context_features.py --corpus-dir /content/corpus --ids 2602.04912
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402
from matching.m3_candidates import normalize_text  # noqa: E402

log = logging.getLogger("m3_context_features")

KEY_COLUMNS = ("candidate_id", "arxiv_id", "citation_key", "ref_arxiv_id")

# Kiểu của từng cột ctx_*: "f" = float32, "i8" = Int8 nullable, "i16" = Int16 nullable.
CTX_DTYPES: Dict[str, str] = {
    "ctx_year_diff": "f",
    "ctx_year_signed": "f",
    "ctx_venue_sim": "f",
    "ctx_ref_venue_is_arxiv": "i8",
    "ctx_cite_title_overlap": "f",
    "ctx_cite_author_mentioned": "i8",
    "ctx_n_cand_entry": "i16",
    "ctx_rank_in_entry": "i16",
    "ctx_author_rank_in_entry": "i16",
    "ctx_title_margin": "f",
    "ctx_n_cand_ref": "i16",
    "ctx_rank_in_ref": "i16",
    "ctx_ref_margin": "f",
    "ctx_mutual_best": "i8",
    "ctx_n_cite_sentences": "i16",
    "ctx_n_cite_sections": "i16",
    "ctx_n_source_versions": "i16",
    "ctx_bib_is_bibitem": "i8",
}
CTX_COLUMNS = tuple(CTX_DTYPES)
_PANDAS_DTYPE = {"f": "float32", "i8": "Int8", "i16": "Int16"}


# ----------------------------------------------------------------------------- đường dẫn
def features_dir() -> Path:
    """<DATA_DIR>/m3/features. (Khi Quoc thêm helper vào config.py thì đổi sang helper đó.)"""
    return C.DATA_DIR / "m3" / "features"


def features_path(arxiv_id: str, suffix: str = "") -> Path:
    """suffix="" -> <paper_key>.parquet (bản ghép); suffix=".str" -> bản của Tri."""
    return features_dir() / f"{C.paper_key(arxiv_id)}{suffix}.parquet"


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ----------------------------------------------------------------------------- văn bản
_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the this to with via using use "
    "based towards toward into over under between its their our can how what why when".split()
)
_WORD = re.compile(r"[a-z0-9]+")
_CITE_RE = re.compile(r"\\(?:no)?cite[A-Za-z]*\*?(?:\s*\[[^\]]*\])*\s*\{([^{}]*)\}")
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _words(text: object) -> set[str]:
    return set(_WORD.findall(normalize_text(text)))


def _content_words(text: object) -> set[str]:
    """Từ khóa: bỏ stop-word và từ quá ngắn (< 3 ký tự)."""
    return {w for w in _words(text) if len(w) >= 3 and w not in _STOPWORDS}


def cite_keys(text: str) -> List[str]:
    """Các khóa trong mọi lệnh \\cite* của một câu (giữ thứ tự, bỏ trùng)."""
    keys: List[str] = []
    for match in _CITE_RE.finditer(text or ""):
        for key in match.group(1).split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def strip_cites(text: str) -> str:
    return _CITE_RE.sub(" ", text or "")


def _year(value: object) -> Optional[int]:
    match = _YEAR_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _first_author_surname(authors: object) -> Optional[str]:
    if isinstance(authors, str):
        authors = [authors]
    if not authors:
        return None
    name = authors[0].get("name", "") if isinstance(authors[0], dict) else str(authors[0])
    base = name.split(",")[0]  # "Last, First" -> "Last"; "First Last" giữ nguyên
    tokens = _WORD.findall(normalize_text(base))
    return tokens[-1] if tokens else None


# ----------------------------------------------------------------------------- ngữ cảnh trích dẫn
def build_cite_contexts(elements: List[dict], edges: List[dict]) -> Dict[str, dict]:
    """{citation_key: {"texts": [câu đã bỏ \\cite], "sections": {id heading}}} từ elements/hierarchy.

    Khóa trong câu là khóa chuẩn (M2 đã đổi qua cite_map) nên khớp trực tiếp citation_key.
    Section = heading gần nhất khi đi ngược parent_id (version mới nhất ghi đè version cũ).
    """
    kind = {e["element_id"]: e.get("kind") for e in elements}
    parent: Dict[str, str] = {}
    for edge in sorted(edges, key=lambda e: e.get("version") or 0):
        parent[edge["element_id"]] = edge["parent_id"]

    def section_of(element_id: str) -> str:
        current, guard = parent.get(element_id), 0
        while current is not None and guard < 100:
            if kind.get(current) == "heading":
                return current
            current, guard = parent.get(current), guard + 1
        return "<root>"

    contexts: Dict[str, dict] = defaultdict(lambda: {"texts": [], "sections": set()})
    for element in elements:
        if element.get("kind") != "sentence":
            continue
        content = element.get("content") or ""
        keys = cite_keys(content)
        if not keys:
            continue
        section = section_of(element["element_id"])
        clean = strip_cites(content)
        for key in keys:
            contexts[key]["texts"].append(clean)
            contexts[key]["sections"].add(section)
    return contexts


# ----------------------------------------------------------------------------- feature nhóm
def _margin_to_best_other(frame: pd.DataFrame, group: str, value: str) -> pd.Series:
    """value - (giá trị lớn nhất của các dòng KHÁC trong cùng nhóm); NaN nếu nhóm chỉ có một dòng."""
    grouped = frame.groupby(group)[value]
    first = grouped.transform("max")
    second = grouped.transform(lambda x: x.nlargest(2).iloc[-1] if len(x) > 1 else np.nan)
    other_best = first.where(frame[value] < first, second)  # nếu mình là max thì "người khác" là hạng nhì
    return frame[value] - other_best


def _group_features(frame: pd.DataFrame) -> pd.DataFrame:
    by_entry = frame.groupby("citation_key")
    by_ref = frame.groupby("ref_arxiv_id")
    frame["ctx_n_cand_entry"] = by_entry["candidate_id"].transform("size")
    frame["ctx_rank_in_entry"] = by_entry["title_similarity"].rank(method="min", ascending=False)
    frame["ctx_author_rank_in_entry"] = by_entry["author_similarity"].rank(method="min", ascending=False)
    frame["ctx_title_margin"] = _margin_to_best_other(frame, "citation_key", "title_similarity")
    frame["ctx_n_cand_ref"] = by_ref["candidate_id"].transform("size")
    frame["ctx_rank_in_ref"] = by_ref["title_similarity"].rank(method="min", ascending=False)
    frame["ctx_ref_margin"] = _margin_to_best_other(frame, "ref_arxiv_id", "title_similarity")
    frame["ctx_mutual_best"] = ((frame["ctx_rank_in_entry"] == 1) & (frame["ctx_rank_in_ref"] == 1)).astype(int)
    return frame


# ----------------------------------------------------------------------------- tính feature một bài
def _empty_frame() -> pd.DataFrame:
    columns = {name: pd.Series(dtype="string") for name in KEY_COLUMNS}
    for name, code in CTX_DTYPES.items():
        columns[name] = pd.Series(dtype=_PANDAS_DTYPE[code])
    return pd.DataFrame(columns)


def compute_features(candidates: List[dict], bib_rows: List[dict], references: List[dict],
                     elements: List[dict], edges: List[dict]) -> pd.DataFrame:
    """Tính ctx_* cho mọi dòng của candidates; trả về DataFrame cùng thứ tự với candidates."""
    if not candidates:
        return _empty_frame()

    frame = pd.DataFrame({
        "candidate_id": [c["candidate_id"] for c in candidates],
        "arxiv_id": [c.get("arxiv_id") for c in candidates],
        "citation_key": [c.get("citation_key") for c in candidates],
        "ref_arxiv_id": [c.get("ref_arxiv_id") for c in candidates],
        "title_similarity": [float(c.get("title_similarity") or 0.0) for c in candidates],
        "author_similarity": [float(c.get("author_similarity") or 0.0) for c in candidates],
    })
    frame = _group_features(frame)

    ref_by_id = {C.normalize_arxiv_id(r.get("ref_arxiv_id")): r for r in references if r.get("ref_arxiv_id")}
    bib_by_key = {row["citation_key"]: row for row in bib_rows}
    contexts = build_cite_contexts(elements, edges)

    # Cache theo khóa để không tính lại cho mỗi cặp.
    context_words: Dict[str, tuple[set, set]] = {}
    for key, ctx in contexts.items():
        joined = " ".join(ctx["texts"])
        context_words[key] = (_words(joined), _content_words(joined))

    n_rows = len(candidates)
    year_diff = np.full(n_rows, np.nan)
    year_signed = np.full(n_rows, np.nan)
    venue_sim = np.full(n_rows, np.nan)
    ref_arxiv_venue = [pd.NA] * n_rows
    title_overlap = np.full(n_rows, np.nan)
    author_mentioned = [pd.NA] * n_rows

    for i, cand in enumerate(candidates):
        reference = ref_by_id.get(C.normalize_arxiv_id(cand.get("ref_arxiv_id")))
        bib = bib_by_key.get(cand.get("citation_key"), {})
        if reference is not None:
            ref_year = _year(reference.get("ref_submission_date"))
            bib_year = _year(bib.get("year"))
            if ref_year is not None and bib_year is not None:
                year_diff[i] = abs(bib_year - ref_year)
                year_signed[i] = ref_year - bib_year

            ref_venue = normalize_text(reference.get("ref_venue"))
            bib_venue = normalize_text(bib.get("journal") or bib.get("booktitle"))
            if ref_venue and bib_venue:
                venue_sim[i] = SequenceMatcher(None, bib_venue, ref_venue).ratio()
            if ref_venue:  # venue rỗng = không biết, để null thay vì 0
                ref_arxiv_venue[i] = int("arxiv" in ref_venue)

            ctx = context_words.get(cand.get("citation_key"))
            if ctx is not None:
                all_words, content = ctx
                title_words = _content_words(reference.get("ref_title"))
                if title_words:
                    title_overlap[i] = len(title_words & content) / len(title_words)
                surname = _first_author_surname(reference.get("ref_authors"))
                if surname and len(surname) >= 2:
                    author_mentioned[i] = int(surname in all_words)

    frame["ctx_year_diff"] = year_diff
    frame["ctx_year_signed"] = year_signed
    frame["ctx_venue_sim"] = venue_sim
    frame["ctx_ref_venue_is_arxiv"] = pd.Series(ref_arxiv_venue, dtype="object")
    frame["ctx_cite_title_overlap"] = title_overlap
    frame["ctx_cite_author_mentioned"] = pd.Series(author_mentioned, dtype="object")

    keys = frame["citation_key"]
    frame["ctx_n_cite_sentences"] = keys.map(lambda k: len(contexts[k]["texts"]) if k in contexts else 0)
    frame["ctx_n_cite_sections"] = keys.map(lambda k: len(contexts[k]["sections"]) if k in contexts else 0)
    frame["ctx_n_source_versions"] = keys.map(lambda k: len(bib_by_key.get(k, {}).get("source_versions") or []))
    frame["ctx_bib_is_bibitem"] = keys.map(lambda k: int(bib_by_key.get(k, {}).get("source") == "bibitem"))

    out = frame[list(KEY_COLUMNS) + list(CTX_COLUMNS)].copy()
    for name, code in CTX_DTYPES.items():
        out[name] = pd.to_numeric(out[name], errors="coerce").astype(_PANDAS_DTYPE[code])
    return out


# ----------------------------------------------------------------------------- kiểm tra hợp đồng
def validate_features(frame: pd.DataFrame, candidates: List[dict]) -> None:
    """Đúng một dòng cho mỗi candidate_id, đúng cột khóa, mọi cột còn lại là số."""
    ids = [c["candidate_id"] for c in candidates]
    if len(frame) != len(ids) or list(frame["candidate_id"]) != ids:
        raise ValueError("features phải có đúng một dòng cho mỗi dòng của candidates.json, cùng thứ tự")
    if frame["candidate_id"].duplicated().any():
        raise ValueError("candidate_id bị trùng trong features")
    missing = [c for c in KEY_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"thiếu cột khóa: {missing}")
    for name in frame.columns:
        if name in KEY_COLUMNS:
            continue
        if not (name.startswith("str_") or name.startswith("ctx_")):
            raise ValueError(f"cột {name!r} không có tiền tố str_/ctx_")
        if not pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name]):
            raise ValueError(f"cột {name!r} không phải kiểu số")


def merge_string_features(frame: pd.DataFrame, str_frame: pd.DataFrame) -> pd.DataFrame:
    """Ghép cột str_* của Tri vào frame theo candidate_id (phải trùng khớp hoàn toàn tập dòng)."""
    str_cols = [c for c in str_frame.columns if c.startswith("str_")]
    if "candidate_id" not in str_frame.columns:
        raise ValueError("file .str.parquet thiếu candidate_id")
    if str_frame["candidate_id"].duplicated().any():
        raise ValueError("file .str.parquet có candidate_id trùng")
    if set(str_frame["candidate_id"]) != set(frame["candidate_id"]):
        raise ValueError("file .str.parquet không cùng tập candidate_id với candidates.json")
    merged = frame[list(KEY_COLUMNS)].merge(
        str_frame[["candidate_id"] + str_cols], on="candidate_id", how="left", validate="one_to_one")
    merged = pd.concat([merged, frame[list(CTX_COLUMNS)]], axis=1)
    return merged


# ----------------------------------------------------------------------------- một bài
def process_paper(arxiv_id: str) -> dict:
    """Đọc dữ liệu M2 của một bài, tính ctx_*, ghép str_* nếu có, ghi <paper_key>.parquet."""
    started = time.perf_counter()
    pdir = C.paper_dir(arxiv_id)
    candidates = _read_json(pdir / "candidates.json", [])
    bib_rows = _read_json(pdir / "bibtex_merged.json", [])
    refs_payload = _read_json(pdir / "refs.json", {})
    references = refs_payload.get("references", []) if isinstance(refs_payload, dict) else []
    elements = _read_json(pdir / "elements.json", [])
    edges = _read_json(pdir / "hierarchy.json", [])

    frame = compute_features(candidates, bib_rows, references, elements, edges)

    has_str = False
    str_path = features_path(arxiv_id, ".str")
    if str_path.exists():
        frame = merge_string_features(frame, pd.read_parquet(str_path))
        has_str = True
    validate_features(frame, candidates)

    out = features_path(arxiv_id)
    C.ensure_dir(out.parent)
    tmp = out.with_suffix(out.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(out)
    return {"arxiv_id": arxiv_id, "rows": len(frame), "has_str": has_str,
            "seconds": round(time.perf_counter() - started, 4)}


# ----------------------------------------------------------------------------- nhiều bài
def discover_ids() -> List[str]:
    """arXiv ID của mọi bài đã có candidates.json (M2 đã chạy xong bước bib)."""
    ids = []
    for path in sorted(C.DATA_DIR.glob("*/candidates.json")):
        paper = _read_json(path.parent / "paper.json", {}) or {}
        arxiv_id = paper.get("arxiv_id")
        if not arxiv_id:
            rows = _read_json(path, []) or []
            arxiv_id = rows[0].get("arxiv_id") if rows else None
        if arxiv_id:
            ids.append(arxiv_id)
    return ids


def _worker_init(data_dir: str) -> None:
    C.DATA_DIR = Path(data_dir)  # tiến trình con (spawn) không thừa hưởng DATA_DIR đã ghi đè ở main


def _safe_process(arxiv_id: str) -> dict:
    try:
        return process_paper(arxiv_id)
    except Exception as error:  # noqa: BLE001 - một bài lỗi không làm dừng cả lô
        return {"arxiv_id": arxiv_id, "error": f"{type(error).__name__}: {error}"}


def build_all(ids: Optional[Iterable[str]] = None, workers: int = 1) -> dict:
    ids = list(ids) if ids else discover_ids()
    started = time.time()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init,
                                 initargs=(str(C.DATA_DIR),)) as pool:
            results = list(pool.map(_safe_process, ids, chunksize=16))
    else:
        results = [_safe_process(i) for i in ids]
    errors = [r for r in results if "error" in r]
    for item in errors[:20]:
        log.error("%s: %s", item["arxiv_id"], item["error"])
    summary = {
        "papers": len(ids), "ok": len(ids) - len(errors), "errors": len(errors),
        "rows": sum(r.get("rows", 0) for r in results),
        "with_str_features": sum(bool(r.get("has_str")) for r in results),
        "seconds": round(time.time() - started, 1),
    }
    log.info("XONG: %s", summary)
    return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Tính feature ngữ cảnh ctx_* và ghép str_* (M3).")
    ap.add_argument("--corpus-dir", type=Path, default=C.DATA_DIR)
    ap.add_argument("--ids", nargs="*", help="chỉ xử lý các arXiv ID này (mặc định: mọi bài có candidates.json)")
    ap.add_argument("--limit", type=int, help="chỉ xử lý N bài đầu (chạy thử)")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    C.DATA_DIR = args.corpus_dir.expanduser().resolve()
    ids = args.ids or discover_ids()
    if args.limit:
        ids = ids[:args.limit]
    print(json.dumps(build_all(ids, args.workers), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()