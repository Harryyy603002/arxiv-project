r"""m3_predictions.py

Milestone: M3
Người phụ trách: P4
Mục đích: Tạo bảng predictions (partition, groundtruth_arxiv_id, cand_1..cand_5)

Đầu vào: Kết quả mô hình
Đầu ra:  Bảng predictions (Parquet)

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

K = 5
Key = Tuple[str, str]  # (arxiv_id, citation_key)


def load_labels(*paths: Path) -> Dict[Key, Optional[str]]:
    """Đọc manual_labels.csv / auto_labels.csv. groundtruth rỗng -> None (không có đáp án)."""
    truth: Dict[Key, Optional[str]] = {}
    for path in paths:
        with Path(path).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row["arxiv_id"].strip(), row["citation_key"].strip())
                truth[key] = (row.get("groundtruth_arxiv_id") or "").strip() or None
    return truth


def top_k_by_entry(scored: Iterable[dict], k: int = K) -> Dict[Key, List[str]]:
    """Gom theo (arxiv_id, citation_key), xếp theo score giảm dần, khử trùng ref, lấy k đầu.

    Bỏ candidate không có ref_arxiv_id (tránh lỗi chuỗi rỗng giống nhau).
    Hòa điểm: xếp theo ref_arxiv_id để kết quả ổn định giữa các lần chạy.
    """
    groups: Dict[Key, List[dict]] = defaultdict(list)
    for row in scored:
        if not row.get("ref_arxiv_id"):
            continue
        groups[(row["arxiv_id"], row["citation_key"])].append(row)

    result: Dict[Key, List[str]] = {}
    for key, rows in groups.items():
        rows.sort(key=lambda r: (-float(r["score"]), str(r["ref_arxiv_id"])))
        picked: List[str] = []
        for row in rows:
            if row["ref_arxiv_id"] not in picked:
                picked.append(row["ref_arxiv_id"])
            if len(picked) == k:
                break
        result[key] = picked
    return result


def build_predictions(
    scored: Iterable[dict],
    entries: Iterable[Key],
    truth: Optional[Dict[Key, Optional[str]]] = None,
    k: int = K,
) -> List[dict]:
    """Một dòng cho mỗi BibTeX entry trong `entries`, kể cả entry không có candidate (list rỗng)."""
    truth = truth or {}
    top = top_k_by_entry(scored, k)
    rows = []
    for arxiv_id, citation_key in sorted(set(entries)):
        key = (arxiv_id, citation_key)
        rows.append({
            "arxiv_id": arxiv_id,
            "citation_key": citation_key,
            "groundtruth_arxiv_id": truth.get(key),
            "predicted_arxiv_ids": top.get(key, []),
        })
    return rows


def write_predictions_parquet(rows: List[dict], out_path: Path) -> int:
    import pandas as pd
    from utils.schemas import write_parquet

    write_parquet(pd.DataFrame(rows), "predictions", Path(out_path))
    return len(rows)