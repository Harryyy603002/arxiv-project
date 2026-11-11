"""m1_tables.py

Milestone: M1 - Crawling
Mục đích: dựng hai bảng M1 (papers, references) từ corpus đã crawl, đúng schema
          trong utils/schemas.py, và ghi ra Parquet (kèm CSV để xem nhanh).

Đầu vào:  <DATA_DIR>/<paper_key>/paper.json   (do run_m1.py ghi)
          <DATA_DIR>/<paper_key>/refs.json    (do run_m1.py / m1_references.py ghi)
Đầu ra:   <DATA_DIR>/papers.parquet, papers.csv
          <DATA_DIR>/references.parquet, references.csv, references_stats.json

Chạy riêng (từ thư mục gốc repo):
    python src/crawler/m1_tables.py --data-dir /content/corpus
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import m1_references  # noqa: E402
from utils import config as C  # noqa: E402
from utils.schemas import PAPERS_SCHEMA, REFERENCES_SCHEMA, write_parquet  # noqa: E402


def _csv_copy(df: pd.DataFrame, list_columns: tuple[str, ...], path: Path) -> None:
    """CSV không có kiểu list nên lưu các cột list dưới dạng chuỗi JSON."""
    out = df.copy()
    for col in list_columns:
        out[col] = out[col].map(lambda v: json.dumps(v, ensure_ascii=False))
    out.to_csv(path, index=False, encoding="utf-8")


def build_papers() -> int:
    """Gộp paper.json của mọi bài thành bảng papers."""
    rows = []
    for path in sorted(C.DATA_DIR.glob("*/paper.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not data.get("arxiv_id"):
            continue
        row = {name: data.get(name) for name in PAPERS_SCHEMA.names}
        row["authors"] = row["authors"] or []
        row["revised_dates"] = row["revised_dates"] or []
        rows.append(row)

    df = pd.DataFrame(rows, columns=PAPERS_SCHEMA.names)
    write_parquet(df, "papers", C.papers_parquet_path())
    _csv_copy(df, ("authors", "revised_dates"), C.papers_csv_path())
    return len(df)


def build_references() -> dict[str, int]:
    """Bảng references: chỉ các reference có arXiv ID (theo đề M1)."""
    rows, stats = m1_references.collect_published_rows()
    m1_references.write_published_csv(rows, stats)  # CSV đầy đủ cột (có ref_venue, external_ids)
    df = pd.DataFrame(rows, columns=REFERENCES_SCHEMA.names)  # Parquet đúng 6 cột của đề
    write_parquet(df, "references", C.DATA_DIR / "references.parquet")
    return stats


def build_all() -> dict:
    """Dựng cả hai bảng, trả về số liệu để ghi vào benchmark/báo cáo."""
    C.ensure_dir(C.DATA_DIR)
    n_papers = build_papers()
    ref_stats = build_references()
    return {"papers_rows": n_papers, **ref_stats}


def main() -> None:
    ap = argparse.ArgumentParser(description="Dựng bảng papers/references (M1).")
    ap.add_argument("--data-dir", type=Path, default=C.DATA_DIR)
    args = ap.parse_args()
    C.DATA_DIR = args.data_dir.expanduser().resolve()
    print(json.dumps(build_all(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
