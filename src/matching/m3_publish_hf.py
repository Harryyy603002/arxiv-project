r"""m3_publish_hf.py

Milestone: M3
Người phụ trách: Khai (P4)
Mục đích:
  - Lọc 6 bảng Parquet (papers, references, elements, hierarchy, bibtex, predictions)
    theo ID range của thành viên (`--member 1..4` hoặc tên `Quoc|Nguyen|Khai|Tri`).
  - Kiểm tra schema PyArrow (`ALL_SCHEMAS`), tính toàn vẹn khóa ngoại và quy tắc bảng `predictions`.
  - Chia nhỏ (shard) từng bảng Parquet theo dung lượng (`--max-shard-mb`) hoặc số dòng (`--max-shard-rows`)
    vào `<out_dir>/data/<table_name>/part-00000-of-0000N.parquet`.
  - Sinh Dataset Card `<out_dir>/README.md` có YAML `configs:` cho cả 6 bảng.
  - Upload lên Hugging Face Hub và kiểm tra đọc lại ở chế độ streaming.
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import config as C  # noqa: E402
from utils.schemas import (  # noqa: E402
    ALL_SCHEMAS,
    to_arrow_table,
    validate_predictions,
)

TABLE_ORDER: Tuple[str, ...] = (
    "papers",
    "references",
    "elements",
    "hierarchy",
    "bibtex",
    "predictions",
)


def resolve_member(member_spec: Optional[str]) -> Optional[str]:
    """Chuẩn hóa tham số --member ('1'..'4' hoặc 'Quoc'|'Nguyen'|'Khai'|'Tri' hoặc None/'all')."""
    if member_spec is None:
        return None
    raw = str(member_spec).strip()
    if not raw or raw.lower() == "all":
        return None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(C.MEMBERS):
            return C.MEMBERS[idx - 1]
        raise ValueError(f"Chỉ số thành viên phải từ 1 đến {len(C.MEMBERS)}, nhận được: {member_spec!r}")
    for name in C.MEMBERS:
        if name.lower() == raw.lower():
            return name
    raise ValueError(f"Tên thành viên không hợp lệ: {member_spec!r}. Chọn trong {C.MEMBERS} hoặc 1..4.")


def table_paths(data_dir: Path) -> Dict[str, Path]:
    """Đường dẫn 6 bảng Parquet nguồn trong thư mục dữ liệu."""
    data_dir = Path(data_dir)
    # Ưu tiên m3/predictions.parquet theo §4.7 M3_PLAN.md; fallback về predictions/ hoặc export/
    pred_candidates = [
        C.predictions_path(data_dir),
        data_dir / "predictions" / "predictions.parquet",
        data_dir / "export" / "predictions.parquet",
    ]
    pred_path = next((p for p in pred_candidates if p.exists()), pred_candidates[0])
    return {
        "papers": data_dir / "papers.parquet",
        "references": data_dir / "references.parquet",
        "elements": data_dir / "export" / "elements.parquet",
        "hierarchy": data_dir / "export" / "hierarchy.parquet",
        "bibtex": data_dir / "export" / "bibtex.parquet",
        "predictions": pred_path,
    }


def filter_dataframe_for_member(
    df: pd.DataFrame,
    table_name: str,
    member: Optional[str],
) -> pd.DataFrame:
    """Lọc bảng theo ID range của `member` (dựa trên cột `arxiv_id` của bài trích dẫn).

    Với bảng `predictions`, loại bỏ mọi dòng có `groundtruth_arxiv_id` rỗng/null
    (đúng §2.3 & §9: mỗi dòng là một entry có reference đúng).
    """
    out = df.copy()
    if table_name == "predictions" and "groundtruth_arxiv_id" in out.columns:
        valid_gt = out["groundtruth_arxiv_id"].notna() & (
            out["groundtruth_arxiv_id"].astype(str).str.strip() != ""
        )
        out = out[valid_gt].copy()

    if member is not None and "arxiv_id" in out.columns and not out.empty:
        mask = out["arxiv_id"].astype(str).map(lambda aid: C.in_range(aid, member))
        out = out[mask].copy()

    return out.reset_index(drop=True)


def validate_tables(
    data_dir: Path,
    member: Optional[str] = None,
    require_all_partitions: bool = False,
) -> List[str]:
    """Trả về danh sách lỗi của 6 bảng (rỗng = hợp lệ, sẵn sàng publish)."""
    member_name = resolve_member(member)
    errors: List[str] = []
    tables: Dict[str, pd.DataFrame] = {}

    for name, path in table_paths(data_dir).items():
        if not path.exists():
            errors.append(f"[{name}] thiếu file {path}")
            continue
        table = pq.read_table(path)
        if not table.schema.equals(ALL_SCHEMAS[name]):
            errors.append(
                f"[{name}] schema lệch:\n  có:  {table.schema.names}\n  cần: {ALL_SCHEMAS[name].names}"
            )
        df = table.to_pandas()
        tables[name] = filter_dataframe_for_member(df, name, member_name)

    def keys(df: pd.DataFrame, cols: List[str]):
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
        missing = keys(tables["predictions"], ["arxiv_id", "citation_key"]) - keys(
            tables["bibtex"], ["arxiv_id", "citation_key"]
        )
        if missing:
            errors.append(f"[predictions] {len(missing)} (arxiv_id, citation_key) không có trong bibtex")

    if "predictions" in tables:
        pred_df = tables["predictions"]
        errors.extend(validate_predictions(pred_df))
        if require_all_partitions:
            if pred_df.empty:
                errors.append("[predictions] bảng rỗng sau khi lọc theo range.")
            else:
                present_parts = set(pred_df["partition"].dropna().astype(str).unique())
                missing_parts = {"train", "valid", "test"} - present_parts
                if missing_parts:
                    errors.append(
                        f"[predictions] thiếu partition bắt buộc trong range: {sorted(missing_parts)}"
                    )

    return errors


def shard_table(
    df: pd.DataFrame,
    table_name: str,
    table_out_dir: Path,
    max_shard_mb: float = 100.0,
    max_shard_rows: Optional[int] = None,
) -> List[Path]:
    """Chia một bảng thành các shard Parquet chuẩn schema trong `table_out_dir`."""
    table_out_dir = Path(table_out_dir)
    if table_out_dir.exists():
        shutil.rmtree(table_out_dir)
    table_out_dir.mkdir(parents=True, exist_ok=True)

    full_arrow = to_arrow_table(df, table_name)
    total_rows = full_arrow.num_rows

    if total_rows == 0:
        n_shards = 1
        rows_per_shard = 0
    elif max_shard_rows is not None and max_shard_rows > 0:
        rows_per_shard = int(max_shard_rows)
        n_shards = max(1, math.ceil(total_rows / rows_per_shard))
    else:
        max_bytes = max(1.0, float(max_shard_mb)) * 1024.0 * 1024.0
        total_bytes = max(1, full_arrow.nbytes)
        n_shards = max(1, math.ceil(total_bytes / max_bytes))
        rows_per_shard = math.ceil(total_rows / n_shards)

    shard_paths: List[Path] = []
    for idx in range(n_shards):
        if total_rows == 0:
            sub_df = df
        else:
            start = idx * rows_per_shard
            end = min(total_rows, (idx + 1) * rows_per_shard)
            sub_df = df.iloc[start:end]
        shard_arrow = to_arrow_table(sub_df, table_name)
        shard_file = table_out_dir / f"part-{idx:05d}-of-{n_shards:05d}.parquet"
        tmp_file = shard_file.with_suffix(".parquet.tmp")
        pq.write_table(shard_arrow, tmp_file)
        tmp_file.replace(shard_file)
        shard_paths.append(shard_file)

    return shard_paths


def generate_dataset_card(
    member: Optional[str],
    stats: Dict[str, Dict[str, Any]],
    hf_repo: Optional[str] = None,
) -> str:
    """Sinh nội dung README.md (Hugging Face Dataset Card) có YAML front-matter `configs:`."""
    member_name = resolve_member(member)
    if member_name:
        lo, hi = C.MEMBER_RANGES[member_name]
        range_label = f"{lo} – {hi} (Member: {member_name})"
    else:
        range_label = "All member ranges (Full Corpus)"

    repo_label = hf_repo or f"<hf-user>/arxiv-citation-matching-{(member_name or 'full').lower()}"

    yaml_lines = [
        "---",
        "license: mit",
        "task_categories:",
        "  - text-retrieval",
        "  - feature-extraction",
        "language:",
        "  - en",
        "configs:",
    ]
    for name in TABLE_ORDER:
        yaml_lines.extend([
            f"  - config_name: {name}",
            "    data_files:",
            "      - split: train",
            f"        path: data/{name}/*.parquet",
        ])
    yaml_lines.append("---")

    table_rows_md = []
    for name in TABLE_ORDER:
        info = stats.get(name, {})
        n_rows = info.get("n_rows", 0)
        n_shards = info.get("n_shards", 1)
        cols = ", ".join(f"`{c}`" for c in ALL_SCHEMAS[name].names)
        table_rows_md.append(f"| `{name}` | {n_rows:,} | {n_shards} | {cols} |")

    body = f"""
# arXiv Citation Matching Dataset — {member_name or "Full Group"}

Bộ dữ liệu gồm **6 bảng Parquet chuẩn hóa** từ pipeline **Milestone 1 (Crawling) → Milestone 2 (LaTeX Parsing) → Milestone 3 (Citation Matching)**.

- **Phạm vi ID (`arxiv_id`):** `{range_label}`
- **Độ đo đánh giá M3:** `MRR@5` (tập truy vấn $Q$ chỉ gồm các mục BibTeX có tài liệu tham khảo arXiv đúng).

## Tóm tắt 6 bảng (`configs`)

| Config / Bảng | Số dòng | Số shard | Các cột theo PyArrow Schema |
|---|---:|---:|---|
{chr(10).join(table_rows_md)}

## Hướng dẫn tải bằng `datasets` (Streaming)

```python
from datasets import load_dataset

for cfg in ("papers", "references", "elements", "hierarchy", "bibtex", "predictions"):
    ds = load_dataset("{repo_label}", name=cfg, split="train", streaming=True)
    first_row = next(iter(ds), None)
    print(cfg, "->", first_row)
```
"""
    return "\n".join(yaml_lines) + "\n" + body.lstrip("\n")


def build_hf_package(
    data_dir: Path,
    out_dir: Optional[Path] = None,
    member: Optional[str] = None,
    max_shard_mb: float = 100.0,
    max_shard_rows: Optional[int] = None,
    hf_repo: Optional[str] = None,
    require_all_partitions: bool = False,
) -> Dict[str, Any]:
    """Lọc 6 bảng theo `--member`, kiểm tra hợp lệ, chia shard Parquet và sinh README.md."""
    data_dir = Path(data_dir)
    member_name = resolve_member(member)
    errors = validate_tables(
        data_dir=data_dir,
        member=member_name,
        require_all_partitions=require_all_partitions,
    )
    if errors:
        raise ValueError("Dữ liệu chưa hợp lệ để đóng gói HF:\n  - " + "\n  - ".join(errors))

    if out_dir is None:
        suffix = member_name.lower() if member_name else "all"
        out_dir = C.m3_dir(data_dir) / "hf_export" / suffix
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = table_paths(data_dir)
    stats: Dict[str, Dict[str, Any]] = {}
    for name in TABLE_ORDER:
        raw_df = pq.read_table(paths[name]).to_pandas()
        filtered_df = filter_dataframe_for_member(raw_df, name, member_name)
        shards = shard_table(
            df=filtered_df,
            table_name=name,
            table_out_dir=out_dir / "data" / name,
            max_shard_mb=max_shard_mb,
            max_shard_rows=max_shard_rows,
        )
        stats[name] = {
            "n_rows": int(len(filtered_df)),
            "n_shards": len(shards),
            "shard_files": [str(p.relative_to(out_dir)).replace("\\", "/") for p in shards],
        }

    card_text = generate_dataset_card(member_name, stats, hf_repo=hf_repo)
    readme_path = out_dir / "README.md"
    readme_path.write_text(card_text, encoding="utf-8")

    return {
        "out_dir": out_dir,
        "member": member_name,
        "range": C.MEMBER_RANGES[member_name] if member_name else None,
        "readme_path": readme_path,
        "tables": stats,
    }


def verify_streaming(
    package_or_repo: str | Path,
    use_hf_datasets: bool = False,
) -> Dict[str, int]:
    """Kiểm tra đọc streaming cả 6 bảng từ gói local hoặc repo Hugging Face.

    Trả về `{table_name: số_dòng_đếm_được_qua_stream}`.
    """
    counts: Dict[str, int] = {}
    local_dir = Path(package_or_repo)

    if not use_hf_datasets and local_dir.exists():
        for name in TABLE_ORDER:
            t_dir = local_dir / "data" / name
            if not t_dir.exists():
                raise FileNotFoundError(f"Thiếu thư mục shard: {t_dir}")
            dataset = ds.dataset(t_dir, format="parquet")
            if not dataset.schema.equals(ALL_SCHEMAS[name]):
                raise ValueError(
                    f"[{name}] schema shard không khớp ALL_SCHEMAS: {dataset.schema.names}"
                )
            total = 0
            for batch in dataset.to_batches():
                total += batch.num_rows
            counts[name] = total
        return counts

    # Dùng datasets.load_dataset(..., streaming=True) nếu đã cài thư viện datasets
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as exc:
        if local_dir.exists():
            return verify_streaming(local_dir, use_hf_datasets=False)
        raise RuntimeError(
            "Cần cài đặt thư viện `datasets` (`pip install datasets`) để kiểm tra streaming từ HF Hub."
        ) from exc

    target = str(local_dir) if local_dir.exists() else str(package_or_repo)
    for name in TABLE_ORDER:
        stream_ds = load_dataset(target, name=name, split="train", streaming=True)
        iterator = iter(stream_ds)
        first = next(iterator, None)
        counts[name] = 0 if first is None else 1 + sum(1 for _ in iterator)
    return counts


def upload_to_hf(
    package_dir: Path,
    hf_repo: str,
    token: Optional[str] = None,
    private: bool = False,
) -> None:
    """Tạo repo dataset (nếu chưa có) và upload toàn bộ thư mục đã shard lên Hugging Face."""
    from huggingface_hub import HfApi

    resolved_token = token or os.environ.get("HF_TOKEN") or None
    api = HfApi(token=resolved_token)
    api.create_repo(hf_repo, repo_type="dataset", private=private, exist_ok=True)
    api.upload_folder(
        folder_path=str(package_dir),
        repo_id=hf_repo,
        repo_type="dataset",
        commit_message="Upload 6 sharded Parquet tables and dataset card (M3)",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lọc 6 bảng theo --member, shard Parquet, sinh dataset card và upload Hugging Face."
    )
    parser.add_argument("--data-dir", "--corpus-dir", dest="data_dir", type=Path, default=C.DATA_DIR,
                        help="Thư mục dữ liệu gốc (mặc định: ./data)")
    parser.add_argument("--member", default=None,
                        help="Thành viên cần trích range: 1..4 hoặc Quoc|Nguyen|Khai|Tri (bỏ trống = toàn bộ)")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Thư mục xuất gói HF đã shard (mặc định: <data-dir>/m3/hf_export/<member>)")
    parser.add_argument("--max-shard-mb", type=float, default=100.0,
                        help="Kích thước tối đa mỗi shard Parquet tính theo MB (mặc định: 100.0)")
    parser.add_argument("--max-shard-rows", type=int, default=None,
                        help="Số dòng tối đa mỗi shard (tùy chọn, ghi đè --max-shard-mb khi cần test shard)")
    parser.add_argument("--require-all-partitions", action="store_true",
                        help="Bắt buộc bảng predictions sau khi lọc phải có đủ cả train, valid và test")
    parser.add_argument("--verify-streaming", action="store_true",
                        help="Kiểm tra đọc lại cả 6 bảng ở chế độ streaming sau khi đóng gói/upload")
    parser.add_argument("--hf-repo", default=None,
                        help="Repo Hugging Face đích (vd: username/arxiv-m3-khai); bỏ trống = chỉ đóng gói local")
    parser.add_argument("--token", default=None,
                        help="Hugging Face token (hoặc đặt biến môi trường HF_TOKEN)")
    parser.add_argument("--private", action="store_true",
                        help="Tạo repo Hugging Face ở chế độ private")
    args = parser.parse_args(argv)

    try:
        member_name = resolve_member(args.member)
    except ValueError as exc:
        print("[LỖI]", exc)
        return 1

    errors = validate_tables(
        data_dir=args.data_dir,
        member=member_name,
        require_all_partitions=args.require_all_partitions,
    )
    for err in errors:
        print("[LỖI]", err)
    if errors:
        return 1

    pkg = build_hf_package(
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        member=member_name,
        max_shard_mb=args.max_shard_mb,
        max_shard_rows=args.max_shard_rows,
        hf_repo=args.hf_repo,
        require_all_partitions=args.require_all_partitions,
    )
    label = f"member={pkg['member']} ({pkg['range'][0]}..{pkg['range'][1]})" if pkg["member"] else "toàn bộ corpus"
    print(f"[m3_publish_hf] Cả 6 bảng hợp lệ cho {label}.")
    print(f"[m3_publish_hf] Đã đóng gói vào {pkg['out_dir']}:")
    for name in TABLE_ORDER:
        t_info = pkg["tables"][name]
        print(f"  - {name:12s}: {t_info['n_rows']:8d} dòng ({t_info['n_shards']} shard)")

    if args.verify_streaming:
        stream_counts = verify_streaming(pkg["out_dir"])
        print(f"[m3_publish_hf] Kiểm tra streaming local thành công: {stream_counts}")

    if args.hf_repo:
        upload_to_hf(pkg["out_dir"], args.hf_repo, token=args.token, private=args.private)
        print(f"[m3_publish_hf] Đã upload lên Hugging Face: {args.hf_repo}")
        if args.verify_streaming:
            remote_counts = verify_streaming(args.hf_repo, use_hf_datasets=True)
            print(f"[m3_publish_hf] Kiểm tra streaming từ HF Hub thành công: {remote_counts}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
