r"""m3_publish_hf.py

Milestone: M3
Người phụ trách: P4
Mục đích: Upload 6 bảng Parquet và dataset card lên Hugging Face

Đầu vào: Các file Parquet
Đầu ra:  Dataset public trên Hugging Face

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.schemas import ALL_SCHEMAS  # noqa: E402


def table_paths(data_dir: Path) -> dict:
    return {
        "papers": data_dir / "papers.parquet",
        "references": data_dir / "references.parquet",
        "elements": data_dir / "export" / "elements.parquet",
        "hierarchy": data_dir / "export" / "hierarchy.parquet",
        "bibtex": data_dir / "export" / "bibtex.parquet",
        "predictions": data_dir / "predictions" / "predictions.parquet",
    }


def validate_tables(data_dir: Path) -> List[str]:
    """Trả về danh sách lỗi (rỗng = sẵn sàng publish)."""
    errors: List[str] = []
    tables = {}
    for name, path in table_paths(data_dir).items():
        if not path.exists():
            errors.append(f"[{name}] thiếu file {path}")
            continue
        table = pq.read_table(path)
        if not table.schema.equals(ALL_SCHEMAS[name]):
            errors.append(f"[{name}] schema lệch:\n  có:  {table.schema.names}\n  cần: {ALL_SCHEMAS[name].names}")
        tables[name] = table.to_pandas()

    def keys(df, cols):
        return set(map(tuple, df[cols].itertuples(index=False, name=None)))

    if {"papers", "bibtex"} <= tables.keys():
        missing = set(tables["bibtex"]["arxiv_id"]) - set(tables["papers"]["arxiv_id"])
        if missing:
            errors.append(f"[bibtex] {len(missing)} arxiv_id không có trong papers")
    if {"papers", "references"} <= tables.keys():
        missing = set(tables["references"]["arxiv_id"]) - set(tables["papers"]["arxiv_id"])
        if missing:
            errors.append(f"[references] {len(missing)} arxiv_id không có trong papers")
    if {"bibtex", "predictions"} <= tables.keys():
        missing = keys(tables["predictions"], ["arxiv_id", "citation_key"]) - keys(tables["bibtex"], ["arxiv_id", "citation_key"])
        if missing:
            errors.append(f"[predictions] {len(missing)} (arxiv_id, citation_key) không có trong bibtex")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra 6 bảng rồi upload lên Hugging Face.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--hf-repo", help="user/dataset; bỏ trống = chỉ kiểm tra")
    args = parser.parse_args()

    errors = validate_tables(args.data_dir)
    for err in errors:
        print("[LỖI]", err)
    if errors:
        return 1
    print("Cả 6 bảng hợp lệ.")
    if args.hf_repo:
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(args.hf_repo, repo_type="dataset", exist_ok=True)
        for name, path in table_paths(args.data_dir).items():
            api.upload_file(path_or_fileobj=str(path), path_in_repo=f"{name}.parquet",
                            repo_id=args.hf_repo, repo_type="dataset")
        print("Đã upload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())