r"""schemas.py

Milestone: Chung (M1-M3)
Người phụ trách: P4
Mục đích:
  1. Schema pyarrow tường minh cho 6 bảng: papers, references, elements, hierarchy, bibtex, predictions
  2. Kiểm tra toàn vẹn dữ liệu M2 và xuất Parquet
  3. Kiểm tra các dòng dữ liệu dùng chung của M1 references và M2 candidates (validate_*_rows)

Đầu vào: Đặc tả cột trong đề của 3 milestone
Đầu ra:  Các schema dùng khi xuất Parquet

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: E402, F401

# 1. ĐỊNH NGHĨA PYARROW SCHEMA CHO 6 BẢNG
# Kiểu cột theo bảng "Dataset tables" trong đề M1/M2; "dictionary" = mã hóa từ điển của Parquet.
DICT_STRING = pa.dictionary(pa.int32(), pa.string())

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
    pa.field("kind", DICT_STRING, nullable=False),
    pa.field("content", pa.string(), nullable=True),
])

HIERARCHY_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("version", pa.int32(), nullable=False),
    pa.field("element_id", pa.string(), nullable=False),
    pa.field("parent_id", pa.string(), nullable=False),
])

BIBTEX_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("citation_key", pa.string(), nullable=False),
    pa.field("entry_type", DICT_STRING, nullable=True),
    pa.field("title", pa.string(), nullable=True),
    pa.field("authors", pa.list_(pa.string()), nullable=True),
    pa.field("year", pa.int32(), nullable=True),
    pa.field("doi", pa.string(), nullable=True),
    pa.field("journal", pa.string(), nullable=True),
    pa.field("url", pa.string(), nullable=True),
])

# Milestone 3
SCORES_SCHEMA = pa.schema([
    pa.field("candidate_id", pa.string(), nullable=False),
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("citation_key", pa.string(), nullable=False),
    pa.field("ref_arxiv_id", pa.string(), nullable=False),
    pa.field("score", pa.float64(), nullable=False),
    pa.field("model_version", pa.string(), nullable=False),
])

PREDICTIONS_SCHEMA = pa.schema([
    pa.field("arxiv_id", pa.string(), nullable=False),
    pa.field("partition", DICT_STRING, nullable=False),
    pa.field("citation_key", pa.string(), nullable=False),
    pa.field("groundtruth_arxiv_id", pa.string(), nullable=True),
    pa.field("cand_1", pa.string(), nullable=True),
    pa.field("cand_2", pa.string(), nullable=True),
    pa.field("cand_3", pa.string(), nullable=True),
    pa.field("cand_4", pa.string(), nullable=True),
    pa.field("cand_5", pa.string(), nullable=True),
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

def _parse_version(value):
    """'v2' -> 2, 2 -> 2; giá trị không đọc được -> None."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _parse_year(value):
    """'2020' -> 2020, '2020a' -> 2020, 2020 -> 2020; giá trị không đọc được -> None."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value))
    return int(m.group(1)) if m else None


def to_arrow_table(df: pd.DataFrame, table_name: str) -> pa.Table:
    """Ép kiểu DataFrame theo đúng PyArrow Schema của bảng.

    Cột int (hierarchy.version, bibtex.year) được đọc từ chuỗi như 'v1' hoặc '2020'.
    """
    schema = ALL_SCHEMAS[table_name]
    aligned = df.reindex(columns=schema.names)
    if table_name == "hierarchy":
        aligned["version"] = aligned["version"].map(_parse_version).astype("Int32")
    elif table_name == "bibtex":
        aligned["year"] = aligned["year"].map(_parse_year).astype("Int32")
    # Cột bị thiếu được reindex thành NaN (float64), mà pyarrow không đổi được float64 sang dictionary
    # hay list; ép các cột đó về object (giá trị hoặc None) trước khi chuyển.
    for field in schema:
        col = aligned[field.name]
        is_numeric_field = pa.types.is_integer(field.type) or pa.types.is_floating(field.type)
        if pa.types.is_dictionary(field.type) or (col.dtype.kind == "f" and not is_numeric_field):
            aligned[field.name] = col.astype(object).where(col.notna(), None)
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
    # Đề: parent_id non-null, nút gốc không có cạnh trong bảng hierarchy -> bỏ cạnh có parent_id rỗng.
    n_root_edges = int(hier_df["parent_id"].isna().sum())
    if n_root_edges:
        hier_df = hier_df.dropna(subset=["parent_id"])
        print(f"[INFO] Bỏ {n_root_edges} cạnh nút gốc (parent_id rỗng) khỏi bảng hierarchy.")
    bib_df = pd.DataFrame(bib_rows, columns=BIBTEX_SCHEMA.names)

    errors = validate_m2_integrity(el_df, hier_df, bib_df)
    for err in errors:
        print(f"[INTEGRITY WARNING] {err}")

    write_parquet(el_df, "elements", out_dir / "elements.parquet")
    write_parquet(hier_df, "hierarchy", out_dir / "hierarchy.parquet")
    write_parquet(bib_df, "bibtex", out_dir / "bibtex.parquet")

    return {"elements": len(el_df), "hierarchy": len(hier_df), "bibtex": len(bib_df)}


# 3. KIỂM TRA CÁC DÒNG DỮ LIỆU DÙNG CHUNG (M1 references, M2 candidates)

REFERENCE_FIELDS = (
    "arxiv_id",
    "ref_arxiv_id",
    "ref_title",
    "ref_authors",
    "ref_submission_date",
    "ref_venue",
    "ref_s2_id",
    "external_ids",
)
CANDIDATE_FIELDS = (
    "candidate_id",
    "arxiv_id",
    "citation_key",
    "ref_s2_id",
    "ref_arxiv_id",
    "reference_index",
    "title_similarity",
    "author_similarity",
    "bib_title",
    "ref_title",
)


def validate_rows(rows: list[dict], required_fields: tuple[str, ...]) -> None:
    """Raise a descriptive error when exported rows violate a shared schema."""
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"row {index} is not an object")
        missing = [field for field in required_fields if field not in row]
        if missing:
            raise ValueError(f"row {index} is missing fields: {', '.join(missing)}")


def validate_reference_rows(rows: list[dict]) -> None:
    validate_rows(rows, REFERENCE_FIELDS)
    for index, row in enumerate(rows):
        if not row["arxiv_id"] or not row["ref_arxiv_id"]:
            raise ValueError(f"reference row {index} has an empty identifier")


def validate_candidate_rows(rows: list[dict]) -> None:
    validate_rows(rows, CANDIDATE_FIELDS)
    candidate_ids = [row["candidate_id"] for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be unique")
    for index, row in enumerate(rows):
        if not row["candidate_id"]:
            raise ValueError(f"candidate row {index} has an empty candidate_id")
        for field in ("title_similarity", "author_similarity"):
            if not 0 <= float(row[field]) <= 1:
                raise ValueError(f"candidate row {index} has invalid {field}")



VALID_PARTITIONS = {"train", "valid", "test"}
CAND_COLS = ("cand_1", "cand_2", "cand_3", "cand_4", "cand_5")


def _is_null_value(value: object) -> bool:
    """Check None, NaN and pandas nullable values safely."""
    if value is None:
        return True

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def validate_predictions(
    predictions_df: pd.DataFrame | pa.Table | Iterable[dict],
    split_ids: Optional[Iterable[str]] = None,
    *,
    allowed_arxiv_ids: Optional[Iterable[str]] = None,
    partitions_by_id: Optional[Dict[str, str]] = None,
    raise_on_error: Optional[bool] = None,
) -> List[str]:
    """Validate M3 predictions against PREDICTIONS_SCHEMA and split.json.

    Supports:
      - pandas DataFrame
      - PyArrow Table
      - Iterable[dict]

    Checks:
      1. Exactly 9 columns according to PREDICTIONS_SCHEMA.
      2. Required fields are not null or empty.
      3. partition belongs to train/valid/test.
      4. No duplicate candidate IDs within one row.
      5. No duplicate (arxiv_id, citation_key).
      6. Optional validation against split IDs.
      7. Optional validation of partition assignments.

    Compatibility:
      - split_ids: legacy main API, returns a list of errors.
      - allowed_arxiv_ids / partitions_by_id: PR API, raises ValueError
        when validation fails by default.
      - raise_on_error explicitly overrides this behavior.
    """

    errors: List[str] = []

    # 1. Convert input into records.
    if isinstance(predictions_df, pa.Table):
        records = predictions_df.to_pylist()
        columns = predictions_df.column_names

        # Parquet may return partition as string instead of dictionary string.
        for field in PREDICTIONS_SCHEMA:
            if field.name not in columns:
                continue

            actual_type = predictions_df.schema.field(field.name).type

            if field.name == "partition":
                compatible = (
                    pa.types.is_string(actual_type)
                    or (
                        pa.types.is_dictionary(actual_type)
                        and pa.types.is_string(actual_type.value_type)
                    )
                )
            else:
                compatible = actual_type == field.type

            if not compatible:
                errors.append(
                    f"Column '{field.name}' sai kiểu dữ liệu: "
                    f"expected {field.type}, got {actual_type}"
                )

    elif isinstance(predictions_df, pd.DataFrame):
        records = predictions_df.to_dict("records")
        columns = list(predictions_df.columns)

    else:
        records = list(predictions_df)
        columns = (
            list(records[0].keys())
            if records
            else PREDICTIONS_SCHEMA.names
        )

    # 2. Validate schema columns.
    expected_columns = PREDICTIONS_SCHEMA.names

    missing_cols = [
        col for col in expected_columns if col not in columns
    ]
    extra_cols = [
        col for col in columns if col not in expected_columns
    ]

    if missing_cols:
        errors.append(f"Thiếu cột: {missing_cols}")

    if extra_cols:
        errors.append(f"Cột ngoài PREDICTIONS_SCHEMA: {extra_cols}")

    # 3. Normalize allowed paper IDs.
    split_allowed = (
        {str(x).strip() for x in split_ids}
        if split_ids is not None
        else None
    )

    explicit_allowed = (
        {str(x).strip() for x in allowed_arxiv_ids}
        if allowed_arxiv_ids is not None
        else None
    )

    if (
        split_allowed is not None
        and explicit_allowed is not None
        and split_allowed != explicit_allowed
    ):
        errors.append(
            "split_ids và allowed_arxiv_ids có nội dung khác nhau."
        )

    allowed = (
        explicit_allowed
        if explicit_allowed is not None
        else split_allowed
    )

    partition_lookup = (
        {
            str(key).strip(): str(value).strip()
            for key, value in partitions_by_id.items()
        }
        if partitions_by_id is not None
        else None
    )

    # 4. Validate individual rows.
    seen_entries = set()

    for index, row in enumerate(records):

        if not isinstance(row, dict):
            errors.append(f"Dòng {index} không phải dict.")
            continue

        def clean_value(value):
            if _is_null_value(value):
                return ""
            return str(value).strip()

        aid = clean_value(row.get("arxiv_id"))
        partition = clean_value(row.get("partition"))
        citation_key = clean_value(row.get("citation_key"))
        groundtruth = clean_value(
            row.get("groundtruth_arxiv_id")
        )

        # Required fields.
        required = {
            "arxiv_id": aid,
            "partition": partition,
            "citation_key": citation_key,
            "groundtruth_arxiv_id": groundtruth,
        }

        for field, value in required.items():
            if not value:
                errors.append(
                    f"Dòng {index}: '{field}' không được NULL/rỗng."
                )

        # Partition validation.
        if partition and partition not in VALID_PARTITIONS:
            errors.append(
                f"Dòng {index}: partition không hợp lệ: {partition!r}"
            )

        # Split membership.
        if allowed is not None and aid and aid not in allowed:
            errors.append(
                f"Dòng {index}: {aid} không nằm trong split.json."
            )

        # Correct partition assignment.
        if partition_lookup is not None and aid:
            expected_partition = partition_lookup.get(aid)

            if expected_partition is None:
                errors.append(
                    f"Dòng {index}: không tìm thấy {aid} "
                    "trong partition mapping."
                )
            elif partition != expected_partition:
                errors.append(
                    f"Dòng {index}: partition '{partition}' "
                    f"không khớp '{expected_partition}' của {aid}."
                )

        # Duplicate entry.
        if aid and citation_key:
            key = (aid, citation_key)

            if key in seen_entries:
                errors.append(
                    f"Dòng {index}: entry bị trùng {key}."
                )

            seen_entries.add(key)

        # Duplicate candidates.
        candidates = [
            clean_value(row.get(col))
            for col in CAND_COLS
        ]

        candidates = [value for value in candidates if value]

        if len(candidates) != len(set(candidates)):
            errors.append(
                f"Dòng {index}: cand_1..cand_5 bị trùng ứng viên."
            )

    # 5. Maintain compatibility with both callers.
    strict_mode = (
        raise_on_error
        if raise_on_error is not None
        else (
            allowed_arxiv_ids is not None
            or partitions_by_id is not None
        )
    )

    if errors and strict_mode:
        raise ValueError(
            "Predictions không hợp lệ:\n- "
            + "\n- ".join(errors[:50])
        )

    return errors



def main() -> None:
    """Chạy tự kiểm tra (self-test): xuất thử định dạng Parquet và kiểm tra toàn vẹn dữ liệu."""
    import tempfile
    sample_elements = pd.DataFrame([
        {"element_id": "el_1", "arxiv_id": "1706.03762", "kind": "document", "content": None},
        {"element_id": "el_2", "arxiv_id": "1706.03762", "kind": "section", "content": "Introduction"},
        {"element_id": "el_3", "arxiv_id": "1706.03762", "kind": "paragraph", "content": "We follow \\cite{vaswani2017}."},
    ])
    sample_hierarchy = pd.DataFrame([
        {"arxiv_id": "1706.03762", "version": "v1", "element_id": "el_2", "parent_id": "el_1"},
        {"arxiv_id": "1706.03762", "version": 1, "element_id": "el_3", "parent_id": "el_2"},
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
    sample_predictions = pd.DataFrame([
        {
            "arxiv_id": "1706.03762",
            "partition": "test",
            "citation_key": "bahdanau2014",
            "groundtruth_arxiv_id": "1409.0473",
            "cand_1": "1409.0473",
            "cand_2": "1508.04025",
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
    ])
    validate_predictions(sample_predictions)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for name, df in [
            ("elements", sample_elements),
            ("hierarchy", sample_hierarchy),
            ("bibtex", sample_bibtex),
            ("predictions", sample_predictions),
        ]:
            out_file = tmp_dir / f"{name}.parquet"
            write_parquet(df, name, out_file)
            loaded = pq.read_table(out_file)
            assert loaded.schema.equals(ALL_SCHEMAS[name]), f"Sai lệch schema ở bảng {name}"
            print(f"[OK] {name}.parquet -> {loaded.num_rows} dòng, schema khớp hoàn toàn.")
    print("Hoàn tất kiểm tra 6 PyArrow schemas & xuất thử Parquet M2 thành công!")

    sample_ref = {
        "arxiv_id": "1706.03762", "ref_arxiv_id": "1409.0473", "ref_title": "NMT by jointly learning to align and translate",
        "ref_authors": ["D. Bahdanau"], "ref_submission_date": "2014-09-01", "ref_venue": None,
        "ref_s2_id": "abc", "external_ids": {"ArXiv": "1409.0473"},
    }
    validate_reference_rows([sample_ref])
    try:
        validate_reference_rows([{**sample_ref, "ref_arxiv_id": None}])
        raise AssertionError("validate_reference_rows phải báo lỗi khi ref_arxiv_id rỗng")
    except ValueError:
        pass
    sample_cand = {f: "x" for f in CANDIDATE_FIELDS}
    sample_cand.update(candidate_id="c1", title_similarity=0.9, author_similarity=0.5)
    validate_candidate_rows([sample_cand])
    print("[OK] validate_reference_rows / validate_candidate_rows hoạt động đúng.")


if __name__ == "__main__":
    main()
