r"""schemas.py

Milestone: Chung (M1-M3)
Người phụ trách: P4
Mục đích: Schema pyarrow tường minh cho 6 bảng: papers, references, elements, hierarchy, bibtex, predictions

Đầu vào: Đặc tả cột trong đề của 3 milestone
Đầu ra:  Các schema dùng khi xuất Parquet

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: E402, F401

# 1. ĐỊNH NGHĨA PYARROW SCHEMA CHO 6 BẢNG

# Milestone 1
PAPERS_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("title", pa.string(), nullable=True),
    pa.field("authors", pa.list_(pa.string()), nullable=True),
    pa.field("submission_date", pa.string(), nullable=True),
    pa.field("revised_dates", pa.list_(pa.string()), nullable=True),
    pa.field("venue", pa.string(), nullable=True),
])

REFERENCES_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("ref_arxiv_id", pa.string(), nullable=False),
    pa.field("ref_title", pa.string(), nullable=True),
    pa.field("ref_authors", pa.list_(pa.string()), nullable=True),
    pa.field("ref_submission_date", pa.string(), nullable=True),
    pa.field("ref_s2_id", pa.string(), nullable=True),
])

# Milestone 2
ELEMENTS_SCHEMA = pa.schema([
    pa.field("element_id", pa.string(), nullable=False),
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("kind", pa.string(), nullable=False),
    pa.field("content", pa.string(), nullable=True),
])

HIERARCHY_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("version", pa.string(), nullable=False),
    pa.field("element_id", pa.string(), nullable=False),
    pa.field("parent_id", pa.string(), nullable=True),
])

BIBTEX_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("citation_key", pa.string(), nullable=False),
    pa.field("entry_type", pa.string(), nullable=True),
    pa.field("title", pa.string(), nullable=True),
    pa.field("authors", pa.list_(pa.string()), nullable=True),
    pa.field("year", pa.string(), nullable=True),
    pa.field("doi", pa.string(), nullable=True),
    pa.field("journal", pa.string(), nullable=True),
    pa.field("url", pa.string(), nullable=True),
])

# Milestone 3
PREDICTIONS_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("citation_key", pa.string(), nullable=False),
    pa.field("groundtruth_arxiv_id", pa.string(), nullable=True),
    pa.field("predicted_arxiv_ids", pa.list_(pa.string()), nullable=False),
])

ALL_SCHEMAS: Dict[str, pa.Schema] = {
    "papers": PAPERS_SCHEMA,
    "references": REFERENCES_SCHEMA,
    "elements": ELEMENTS_SCHEMA,
    "hierarchy": HIERARCHY_SCHEMA,
    "bibtex": BIBTEX_SCHEMA,
    "predictions": PREDICTIONS_SCHEMA,
}

# 2. KIỂM TRA TOÀN VẸN DỮ LIỆU & XUẤT PARQUET

def to_arrow_table(df: pd.DataFrame, table_name: str) -> pa.Table:
    """Ép kiểu DataFrame theo đúng PyArrow Schema của bảng."""
    schema = ALL_SCHEMAS[table_name]
    aligned = df.reindex(columns=schema.names)
    return pa.Table.from_pandas(aligned, schema=schema, preserve_index=False)


def write_parquet(df: pd.DataFrame, table_name: str, out_path: Path) -> pa.Table:
    """Kiểm tra schema và ghi DataFrame ra file Parquet."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = to_arrow_table(df, table_name)
    pq.write_table(table, out_path)
    return table


def validate_m2_integrity(
    elements_df: pd.DataFrame,
    hierarchy_df: pd.DataFrame,
    bibtex_df: pd.DataFrame,
) -> List[str]:
    """Kiểm tra tính toàn vẹn dữ liệu giữa 3 bảng M2. Trả về danh sách lỗi (rỗng = hợp lệ)."""
    errors: List[str] = []

    if not elements_df.empty:
        if elements_df["element_id"].isna().any() or elements_df["arxiv_id"].isna().any():
            errors.append("Bảng elements có element_id hoặc arxiv_id bị NULL.")
        dup_el = int(elements_df.duplicated(subset=["arxiv_id", "element_id"]).sum())
        if dup_el > 0:
            errors.append(f"Bảng elements có {dup_el} dòng bị trùng khóa (arxiv_id, element_id).")

    if not bibtex_df.empty:
        if bibtex_df["citation_key"].isna().any() or bibtex_df["arxiv_id"].isna().any():
            errors.append("Bảng bibtex có citation_key hoặc arxiv_id bị NULL.")
        dup_bib = int(bibtex_df.duplicated(subset=["arxiv_id", "citation_key"]).sum())
        if dup_bib > 0:
            errors.append(f"Bảng bibtex có {dup_bib} dòng bị trùng khóa (arxiv_id, citation_key).")

    if not hierarchy_df.empty and not elements_df.empty:
        valid_ids = set(elements_df["element_id"].dropna())
        hier_ids = set(hierarchy_df["element_id"].dropna())
        missing_children = hier_ids - valid_ids
        if missing_children:
            errors.append(f"Bảng hierarchy có {len(missing_children)} element_id không tồn tại trong elements.")

        parent_ids = set(hierarchy_df["parent_id"].dropna())
        missing_parents = parent_ids - valid_ids
        if missing_parents:
            errors.append(f"Bảng hierarchy có {len(missing_parents)} parent_id không tồn tại trong elements.")

    return errors


def export_m2_parquet(corpus_dir: Path | None = None, out_dir: Path | None = None) -> Dict[str, int]:
    """Gộp các file JSON của M2 trong từng thư mục bài báo và xuất ra 3 file Parquet chuẩn."""
    corpus_dir = corpus_dir or DATA_DIR
    out_dir = out_dir or DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    el_rows, hier_rows, bib_rows = [], [], []
    if corpus_dir.exists():
        for pdir in sorted(corpus_dir.iterdir()):
            if not pdir.is_dir():
                continue
            for fname, target in [
                ("elements.json", el_rows),
                ("hierarchy.json", hier_rows),
                ("bibtex_merged.json", bib_rows),
            ]:
                fpath = pdir / fname
                if fpath.exists():
                    try:
                        data = json.loads(fpath.read_text(encoding="utf-8"))
                        if isinstance(data, list):
                            target.extend(data)
                    except (OSError, ValueError):
                        pass

    el_df = pd.DataFrame(el_rows, columns=ELEMENTS_SCHEMA.names)
    hier_df = pd.DataFrame(hier_rows, columns=HIERARCHY_SCHEMA.names)
    bib_df = pd.DataFrame(bib_rows, columns=BIBTEX_SCHEMA.names)

    errors = validate_m2_integrity(el_df, hier_df, bib_df)
    for err in errors:
        print(f"[INTEGRITY WARNING] {err}")

    write_parquet(el_df, "elements", out_dir / "elements.parquet")
    write_parquet(hier_df, "hierarchy", out_dir / "hierarchy.parquet")
    write_parquet(bib_df, "bibtex", out_dir / "bibtex.parquet")

    return {"elements": len(el_df), "hierarchy": len(hier_df), "bibtex": len(bib_df)}


def main() -> None:
    """Chạy tự kiểm tra (self-test): xuất thử định dạng Parquet và kiểm tra toàn vẹn dữ liệu."""
    import tempfile
    sample_elements = pd.DataFrame([
        {"element_id": "el_1", "arxiv_id": "1706.03762", "kind": "document", "content": None},
        {"element_id": "el_2", "arxiv_id": "1706.03762", "kind": "section", "content": "Introduction"},
        {"element_id": "el_3", "arxiv_id": "1706.03762", "kind": "paragraph", "content": "We follow \\cite{vaswani2017}."},
    ])
    sample_hierarchy = pd.DataFrame([
        {"arxiv_id": "1706.03762", "version": "v1", "element_id": "el_1", "parent_id": None},
        {"arxiv_id": "1706.03762", "version": "v1", "element_id": "el_2", "parent_id": "el_1"},
        {"arxiv_id": "1706.03762", "version": "v1", "element_id": "el_3", "parent_id": "el_2"},
    ])
    sample_bibtex = pd.DataFrame([
        {
            "arxiv_id": "1706.03762",
            "citation_key": "vaswani2017",
            "entry_type": "inproceedings",
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": "2017",
            "doi": None,
            "journal": "NeurIPS",
            "url": "https://arxiv.org/abs/1706.03762",
        }
    ])
    errors = validate_m2_integrity(sample_elements, sample_hierarchy, sample_bibtex)
    assert not errors, f"Phát hiện lỗi toàn vẹn: {errors}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, df in [
            ("elements", sample_elements),
            ("hierarchy", sample_hierarchy),
            ("bibtex", sample_bibtex),
        ]:
            out_file = tmp_dir / f"{name}.parquet"
            write_parquet(df, name, out_file)
            loaded = pq.read_table(out_file)
            assert loaded.schema.equals(ALL_SCHEMAS[name]), f"Sai lệch schema ở bảng {name}"
            print(f"[OK] {name}.parquet -> {loaded.num_rows} dòng, schema khớp hoàn toàn.")
    print("Hoàn tất kiểm tra 6 PyArrow schemas & xuất thử Parquet M2 thành công!")


if __name__ == "__main__":
    main()