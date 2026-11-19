"""M3 - P4: đánh giá xếp hạng bằng MRR@5."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402

K = 5


def extract_predicted_ids(row: dict, k: int = K) -> List[str]:
    """Lấy danh sách top-k arXiv ID dự đoán từ `predicted_arxiv_ids` hoặc `cand_1..cand_5`."""
    if "predicted_arxiv_ids" in row and row["predicted_arxiv_ids"] is not None:
        return [str(x) for x in row["predicted_arxiv_ids"] if x is not None and str(x).strip() != ""][:k]
    preds: List[str] = []
    for idx in range(1, k + 1):
        val = row.get(f"cand_{idx}")
        if val is not None and str(val).strip() != "" and str(val).lower() != "nan":
            preds.append(str(val).strip())
    return preds


def reciprocal_rank(predicted: list[str], truth: Optional[str], k: int = K) -> float:
    """1/rank nếu đáp án nằm trong top-k, ngược lại 0."""
    if not truth:
        return 0.0
    for rank, ref_id in enumerate(predicted[:k], start=1):
        if ref_id == truth:
            return 1.0 / rank
    return 0.0


def mrr_at_k(
    rows: Iterable[dict],
    k: int = K,
    skip_no_answer: bool = True,
) -> dict:
    """rows: mỗi dòng có groundtruth_arxiv_id và predicted_arxiv_ids (hoặc cand_1..cand_5).

    skip_no_answer=True: bỏ entry không có đáp án khỏi mẫu số (|Q| chỉ gồm entry có nhãn).
    """
    scores, skipped = [], 0
    for row in rows:
        raw_truth = row.get("groundtruth_arxiv_id")
        truth = str(raw_truth).strip() if (raw_truth is not None and str(raw_truth).strip() != "" and str(raw_truth).lower() != "nan") else None
        if truth is None and skip_no_answer:
            skipped += 1
            continue
        scores.append(reciprocal_rank(extract_predicted_ids(row, k=k), truth, k))
    n = len(scores)
    return {
        f"mrr@{k}": sum(scores) / n if n else 0.0,
        "n_evaluated": n,
        "n_skipped_no_answer": skipped,
        "hit_rate": sum(s > 0 for s in scores) / n if n else 0.0,
    }


def load_split(path: Path) -> dict:
    """Đọc split.json (chuẩn §4.4 theo từng member hoặc dạng phẳng) -> {arxiv_id: tên tập}."""
    from matching.m3_model import parse_split_data

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    partitions, _ = parse_split_data(data)
    return partitions


def mrr_by_group(rows, group_of, k: int = K) -> dict:
    groups = defaultdict(list)
    for row in rows:
        g = group_of(row)
        if g is not None:
            groups[str(g)].append(row)
    return {name: mrr_at_k(items, k) for name, items in sorted(groups.items())}


def run_evaluation_pipeline(
    data_dir: Path,
    predictions_file: Optional[Path] = None,
    split_file: Optional[Path] = None,
    keep_no_answer: bool = False,
) -> Dict[str, Any]:
    """Đánh giá MRR@5 tổng thể, theo partition (train/valid/test), theo nguồn (manual/auto) và theo range thành viên."""
    import pyarrow.parquet as pq
    from matching.m3_model import parse_split_data

    data_dir = Path(data_dir)
    predictions_file = Path(predictions_file) if predictions_file is not None else C.predictions_path(data_dir)
    split_file = Path(split_file) if split_file is not None else C.split_path(data_dir)

    if not predictions_file.exists():
        raise FileNotFoundError(f"Chưa có file predictions: {predictions_file}")

    rows = pq.read_table(predictions_file).to_pandas().to_dict("records")
    for row in rows:
        row["predicted_arxiv_ids"] = extract_predicted_ids(row)
        raw_gt = row.get("groundtruth_arxiv_id")
        row["groundtruth_arxiv_id"] = (
            str(raw_gt).strip()
            if (raw_gt is not None and str(raw_gt).strip() != "" and str(raw_gt).lower() != "nan")
            else None
        )

    split_parts: Dict[str, str] = {}
    split_sources: Dict[str, str] = {}
    if split_file.exists():
        split_parts, split_sources = parse_split_data(
            json.loads(split_file.read_text(encoding="utf-8"))
        )

    overall = mrr_at_k(rows, skip_no_answer=not keep_no_answer)
    by_partition = mrr_by_group(
        rows,
        lambda r: r.get("partition") or split_parts.get(str(r.get("arxiv_id"))),
    )
    test_rows = [
        r for r in rows
        if (r.get("partition") or split_parts.get(str(r.get("arxiv_id")))) == "test"
    ]
    by_test_source = mrr_by_group(
        test_rows,
        lambda r: split_sources.get(str(r.get("arxiv_id")), "unknown"),
    )
    by_member = mrr_by_group(
        rows,
        lambda r: C.member_of(str(r.get("arxiv_id"))) or "other",
    )

    report = {
        "overall": overall,
        "by_partition": by_partition,
        "test_by_source": by_test_source,
        "by_member": by_member,
    }
    out_report = C.m3_dir(data_dir) / "evaluation_report.json"
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tính MRR@5 từ predictions.parquet")
    parser.add_argument("--data-dir", "--corpus-dir", dest="data_dir", type=Path, default=C.DATA_DIR)
    parser.add_argument("--predictions", type=Path, default=None,
                        help="Đường dẫn predictions.parquet (mặc định: <data-dir>/m3/predictions.parquet)")
    parser.add_argument("--split-file", type=Path, default=None, help="JSON chia train/valid/test")
    parser.add_argument("--keep-no-answer", action="store_true",
                        help="tính cả entry không có đáp án (điểm 0)")
    args = parser.parse_args(argv)

    report = run_evaluation_pipeline(
        data_dir=args.data_dir,
        predictions_file=args.predictions,
        split_file=args.split_file,
        keep_no_answer=args.keep_no_answer,
    )
    print("TỔNG:", report["overall"])
    for name, result in report["by_partition"].items():
        print(f"{name:>8}:", result)
    if report["test_by_source"]:
        print("TEST THEO NGUỒN NHÃN:", report["test_by_source"])
    if report["by_member"]:
        print("THEO THÀNH VIÊN:", {k: v["mrr@5"] for k, v in report["by_member"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())