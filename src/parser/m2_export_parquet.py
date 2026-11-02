r"""m2_export_parquet.py

Milestone: M2
Người phụ trách: P4
Mục đích: Xuất 3 bảng elements, hierarchy, bibtex ra Parquet theo schema

Đầu vào: Dữ liệu trung gian của M2
Đầu ra:  Các file .parquet trong data/export/

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: F401
from utils.schemas import export_m2_parquet


def main():
    parser = argparse.ArgumentParser(description="Xuất các bảng dữ liệu M2 ra định dạng Parquet theo schema.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=DATA_DIR,
        help="Thư mục chứa corpus dữ liệu đã parse (mặc định: data/)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DATA_DIR / "export",
        help="Thư mục lưu các file parquet đầu ra (mặc định: data/export/)",
    )
    args = parser.parse_args()

    print(f"Bắt đầu xuất dữ liệu M2 từ '{args.corpus_dir}' ra Parquet tại '{args.out_dir}'...")

    # Gọi hàm export đã được chuẩn hóa schema và kiểm tra toàn vẹn trong utils.schemas
    counts = export_m2_parquet(corpus_dir=args.corpus_dir, out_dir=args.out_dir)

    print("Xuất Parquet hoàn tất!")
    for table_name, row_count in counts.items():
        print(f" - {table_name}.parquet: {row_count} dòng")

if __name__ == "__main__":
    main()