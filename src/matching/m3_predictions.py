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

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402

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
    partitions: Optional[Dict[str, str]] = None,
    drop_no_answer: bool = False,
) -> List[dict]:
    """Sinh danh sách dòng dự đoán cho các BibTeX entry.

    Mỗi dòng có cả `predicted_arxiv_ids` (list, tương thích ngược với test cũ)
    lẫn `partition` và `cand_1..cand_5` (khớp PREDICTIONS_SCHEMA 9 cột).
    Khi `drop_no_answer=True` (mặc định khi ghi predictions.parquet theo §4.7 M3_PLAN.md),
    chỉ giữ các entry có `groundtruth_arxiv_id` khác None.
    """
    truth = truth or {}
    partitions = partitions or {}
    top = top_k_by_entry(scored, k)
    rows = []
    for arxiv_id, citation_key in sorted(set(entries)):
        key = (arxiv_id, citation_key)
        gt = truth.get(key)
        if drop_no_answer and not gt:
            continue
        preds = top.get(key, [])
        cands = {f"cand_{i}": (preds[i - 1] if i - 1 < len(preds) else None) for i in range(1, K + 1)}
        rows.append({
            "arxiv_id": arxiv_id,
            "partition": partitions.get(arxiv_id, "train"),
            "citation_key": citation_key,
            "groundtruth_arxiv_id": gt,
            "predicted_arxiv_ids": preds,
            **cands,
        })
    return rows


def write_predictions_parquet(rows: List[dict], out_path: Path) -> int:
    import pandas as pd
    from utils.schemas import PREDICTIONS_SCHEMA, write_parquet

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=PREDICTIONS_SCHEMA.names)
    write_parquet(df, "predictions", Path(out_path))
    return len(df)


def run_predictions_pipeline(
    data_dir: Path,
    scores_file: Optional[Path] = None,
    split_file: Optional[Path] = None,
    out_path: Optional[Path] = None,
) -> Dict[str, object]:
    """Đọc m3/scores.parquet + m3/split.json + m3/labels/*.json -> ghi m3/predictions.parquet (§4.7)."""
    import pyarrow.parquet as pq
    from matching.m3_model import load_paper_label_file, load_split_info

    data_dir = Path(data_dir)
    scores_file = Path(scores_file) if scores_file is not None else C.scores_path(data_dir)
    split_file = Path(split_file) if split_file is not None else C.split_path(data_dir)
    out_path = Path(out_path) if out_path is not None else C.predictions_path(data_dir)

    if not scores_file.exists():
        raise FileNotFoundError(f"Chưa có file điểm: {scores_file}")

    scored_rows = pq.read_table(scores_file).to_pandas().to_dict("records")
    if split_file.exists():
        partitions, _ = load_split_info(split_file)
    else:
        partitions = {str(r["arxiv_id"]): "train" for r in scored_rows}

    truth: Dict[Key, Optional[str]] = {}
    entries: List[Key] = []
    for arxiv_id in sorted(partitions.keys()):
        lf = C.labels_path(arxiv_id, data_dir)
        if not lf.exists():
            continue
        _src, labels = load_paper_label_file(lf)
        for ckey, gt_ref in labels.items():
            if gt_ref is not None:
                key = (arxiv_id, ckey)
                truth[key] = gt_ref
                entries.append(key)

    rows = build_predictions(
        scored=scored_rows,
        entries=entries,
        truth=truth,
        k=K,
        partitions=partitions,
        drop_no_answer=True,
    )
    n_written = write_predictions_parquet(rows, out_path)
    return {"out_path": out_path, "n_rows": n_written, "rows": rows}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tạo bảng m3/predictions.parquet từ m3/scores.parquet.")
    parser.add_argument("--data-dir", "--corpus-dir", dest="data_dir", type=Path, default=C.DATA_DIR)
    parser.add_argument("--scores", type=Path, default=None, help="Đường dẫn scores.parquet")
    parser.add_argument("--split-file", type=Path, default=None, help="Đường dẫn split.json")
    parser.add_argument("--out", type=Path, default=None, help="Đường dẫn predictions.parquet đầu ra")
    args = parser.parse_args(argv)

    res = run_predictions_pipeline(
        data_dir=args.data_dir,
        scores_file=args.scores,
        split_file=args.split_file,
        out_path=args.out,
    )
    print(f"[m3_predictions] Đã ghi {res['n_rows']} dòng vào {res['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())