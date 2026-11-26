r"""m3_bundle.py

Milestone: M3
Mục đích: Chuyển dữ liệu M3 giữa các máy khi mỗi thành viên chạy trên máy riêng và một người
          (Khải) ghép lại để train một lần cho cả nhóm.

Luồng làm việc:
  1. Mỗi người, trên máy mình, sau khi xong pick -> gán tay -> label -> features:
         python src/matching/m3_bundle.py export --member Nguyen --out bundle_Nguyen.zip
     Lệnh này kiểm tra dữ liệu rồi đóng gói ĐÚNG những file mà train/predict/evaluate cần
     cho các bài trong split (nhãn, feature, bibtex_merged/refs/candidates), kèm phần split của người đó.
  2. Khải gom 4 file zip rồi ghép:
         python src/matching/m3_bundle.py merge bundle_Quoc.zip bundle_Nguyen.zip bundle_Khai.zip bundle_Tri.zip \
             --out-dir data_merged
     Lệnh này kiểm tra (mỗi thành viên một lần, ID đúng range, không trùng bài, cùng bộ cột feature),
     giải nén và ghi m3/split.json chung. Sau đó:
         python src/run_m3.py --data-dir data_merged --stage train,predict,evaluate
  3. Khải gửi lại m3/predictions.parquet (và evaluation_report.json) cho mọi người. Mỗi người chép
     predictions.parquet vào <data>/m3/ của mình rồi chạy:
         python src/run_m3.py --stage publish --member <Tên> --require-all-partitions --verify-streaming

Gói không chứa elements/hierarchy/tree: mấy bảng đó mỗi người tự publish từ máy của mình.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching.m3_manual_labeler import validate_label_file  # noqa: E402
from utils import config as C  # noqa: E402

BUNDLE_FORMAT = 1
PER_PAPER_REQUIRED = ("bibtex_merged.json", "refs.json", "candidates.json")
PER_PAPER_OPTIONAL = ("paper.json",)
MIN_MANUAL_POSITIVES = 20
MIN_MANUAL_PAPERS = 5


class BundleError(RuntimeError):
    """Lỗi chặn: gói/ghép không hợp lệ."""


def _split_paper_ids(member_info: Dict[str, Any]) -> List[str]:
    """Mọi bài của một thành viên trong split (manual + auto; valid/test chỉ là tập con)."""
    seen: Dict[str, None] = {}
    for aid in list(member_info.get("manual") or []) + list(member_info.get("auto") or []):
        seen.setdefault(str(aid), None)
    for part in ("valid", "test"):
        for ids in (member_info.get(part) or {}).values():
            for aid in (ids if isinstance(ids, list) else [ids]):
                seen.setdefault(str(aid), None)
    return list(seen)


def _feature_columns(path: Path) -> List[str]:
    return list(pq.read_schema(path).names)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export_bundle(data_dir: Path, member: str, out_path: Path, strict: bool = False) -> Dict[str, Any]:
    """Đóng gói dữ liệu M3 của một thành viên. Trả về báo cáo {errors, warnings, ...}."""
    data_dir = Path(data_dir)
    if member not in C.MEMBERS:
        raise BundleError(f"Thành viên không hợp lệ: {member!r}. Chọn trong {C.MEMBERS}")
    split_file = C.split_path(data_dir)
    if not split_file.exists():
        raise BundleError(f"Chưa có {split_file}. Chạy `run_m3.py --stage pick --member {member}` trước.")
    split = _read_json(split_file)
    member_info = (split.get("members") or {}).get(member)
    if not member_info:
        raise BundleError(f"split.json không có thành viên {member}.")

    ids = _split_paper_ids(member_info)
    errors: List[str] = []
    warnings: List[str] = []
    files: List[Tuple[Path, str]] = []  # (đường dẫn thật, đường dẫn trong zip)
    columns_by_paper: Dict[str, List[str]] = {}
    positives: Dict[str, int] = {}
    sources: Dict[str, str] = {}

    for aid in ids:
        if not C.in_range(aid, member):
            errors.append(f"{aid}: không thuộc range của {member} {C.MEMBER_RANGES[member]}")
            continue
        pk = C.paper_key(aid)
        pdir = data_dir / pk

        label_path = C.labels_path(aid, data_dir)
        if not label_path.exists():
            errors.append(f"{aid}: thiếu file nhãn {label_path.name} (chưa gán tay / `label` chưa gán được nhãn nào)")
        else:
            valid, label_errors, n_pos = validate_label_file(data_dir, aid)
            positives[aid] = n_pos
            sources[aid] = str(_read_json(label_path).get("source"))
            if not valid:
                errors.append(f"{aid}: file nhãn không hợp lệ: {label_errors[:3]}")
            elif n_pos == 0:
                errors.append(f"{aid}: không có nhãn dương nào")
            files.append((label_path, f"data/m3/labels/{pk}.json"))

        feat_path = C.features_path(aid, data_dir)
        if not feat_path.exists():
            errors.append(f"{aid}: thiếu {feat_path.name} (chạy `--stage features`)")
        else:
            columns_by_paper[aid] = _feature_columns(feat_path)
            files.append((feat_path, f"data/m3/features/{pk}.parquet"))

        for name in PER_PAPER_REQUIRED:
            if not (pdir / name).exists():
                errors.append(f"{aid}: thiếu {name}")
            else:
                files.append((pdir / name, f"data/{pk}/{name}"))
        for name in PER_PAPER_OPTIONAL:
            if (pdir / name).exists():
                files.append((pdir / name, f"data/{pk}/{name}"))

    # Mọi bài phải cùng bộ cột feature (nếu không là có bài tính bằng code cũ).
    distinct = {tuple(cols) for cols in columns_by_paper.values()}
    if len(distinct) > 1:
        reference = max(distinct, key=lambda cols: sum(1 for c in columns_by_paper.values() if tuple(c) == cols))
        odd = [aid for aid, cols in columns_by_paper.items() if tuple(cols) != reference]
        errors.append(f"{len(odd)} bài có bộ cột feature khác số đông (tính bằng code cũ?): {odd[:5]}. "
                      f"Chạy lại `--stage features --rebuild-features`.")
    feature_columns = list(next(iter(distinct))) if distinct else []

    # Yêu cầu của đề cho nhóm gán tay.
    manual_ids = [str(a) for a in member_info.get("manual") or []]
    if len(manual_ids) < MIN_MANUAL_PAPERS:
        errors.append(f"Chỉ có {len(manual_ids)} bài gán tay trong split (cần >= {MIN_MANUAL_PAPERS}).")
    for aid in manual_ids:
        if sources.get(aid) not in (None, "manual"):
            errors.append(f"{aid}: nằm trong nhóm gán tay nhưng file nhãn có source={sources.get(aid)!r}")
        if positives.get(aid, 0) < MIN_MANUAL_POSITIVES:
            errors.append(f"{aid}: bài gán tay mới có {positives.get(aid, 0)} nhãn dương (cần >= {MIN_MANUAL_POSITIVES}).")
    for part in ("valid", "test"):
        got = member_info.get(part) or {}
        for kind in ("manual", "auto"):
            if not got.get(kind):
                warnings.append(f"split.{part} thiếu bài {kind}.")

    report = {"member": member, "n_papers": len(ids), "errors": errors, "warnings": warnings,
              "positives": positives, "n_files": len(files)}
    if errors and strict:
        raise BundleError("Không đóng gói vì có lỗi:\n- " + "\n- ".join(errors))

    manifest = {
        "format": BUNDLE_FORMAT,
        "member": member,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": split.get("seed"),
        "n_papers": len(ids),
        "feature_columns": feature_columns,
        "papers": {aid: {"source": sources.get(aid), "positives": positives.get(aid, 0)} for aid in ids},
        "errors_at_export": errors,
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("split.json", json.dumps({"seed": split.get("seed"), "members": {member: member_info}},
                                             ensure_ascii=False, indent=2))
        for real, arc in files:
            zf.write(real, arc)
    tmp.replace(out_path)
    report["out"] = str(out_path)
    report["size_mb"] = round(out_path.stat().st_size / 1e6, 2)
    return report


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def _read_bundle(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any], zipfile.ZipFile]:
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BundleError(f"{path}: không mở được file zip ({exc})") from exc
    bad = zf.testzip()
    if bad:
        raise BundleError(f"{path}: file bị hỏng trong gói: {bad} (gửi lại?)")
    try:
        manifest = json.loads(zf.read("manifest.json"))
        split = json.loads(zf.read("split.json"))
    except KeyError as exc:
        raise BundleError(f"{path}: không phải gói M3 (thiếu {exc})") from exc
    if manifest.get("format") != BUNDLE_FORMAT:
        raise BundleError(f"{path}: format {manifest.get('format')} không được hỗ trợ (cần {BUNDLE_FORMAT})")
    return manifest, split, zf


def merge_bundles(bundles: Sequence[Path], out_dir: Path, force: bool = False,
                  allow_partial: bool = False) -> Dict[str, Any]:
    """Ghép các gói của từng thành viên vào một thư mục data chung."""
    out_dir = Path(out_dir)
    if (C.split_path(out_dir)).exists() and not force:
        raise BundleError(f"{C.split_path(out_dir)} đã tồn tại. Dùng --force hoặc chọn --out-dir mới.")

    opened = [_read_bundle(Path(b)) for b in bundles]
    errors: List[str] = []
    warnings: List[str] = []

    members_seen: Dict[str, Path] = {}
    owner_of: Dict[str, str] = {}
    columns: Dict[str, List[str]] = {}
    seeds: Dict[str, Any] = {}
    merged_members: Dict[str, Any] = {}

    for (manifest, split, _zf), path in zip(opened, bundles):
        member = manifest.get("member")
        if member not in C.MEMBERS:
            errors.append(f"{path}: thành viên không hợp lệ {member!r}")
            continue
        if member in members_seen:
            errors.append(f"{member} xuất hiện 2 lần ({members_seen[member]} và {path})")
            continue
        members_seen[member] = Path(path)
        if manifest.get("errors_at_export"):
            errors.append(f"{path}: gói được tạo khi còn lỗi: {manifest['errors_at_export'][:3]}")
        info = (split.get("members") or {}).get(member)
        if not info:
            errors.append(f"{path}: split.json không có {member}")
            continue
        merged_members[member] = info
        seeds[member] = split.get("seed")
        columns[member] = list(manifest.get("feature_columns") or [])
        for aid in manifest.get("papers", {}):
            if not C.in_range(aid, member):
                errors.append(f"{member}: bài {aid} nằm ngoài range {C.MEMBER_RANGES[member]}")
            if aid in owner_of:
                errors.append(f"Bài {aid} có trong cả {owner_of[aid]} và {member}")
            owner_of[aid] = member

    if len({tuple(cols) for cols in columns.values()}) > 1:
        ref_member = next(iter(columns))
        for member, cols in columns.items():
            if cols != columns[ref_member]:
                only_a = sorted(set(columns[ref_member]) - set(cols))[:5]
                only_b = sorted(set(cols) - set(columns[ref_member]))[:5]
                errors.append(f"Bộ cột feature của {member} khác {ref_member}: thiếu {only_a}, thừa {only_b}. "
                              f"Hai người đang dùng hai phiên bản code khác nhau.")
    if len({str(s) for s in seeds.values()}) > 1:
        warnings.append(f"Seed khác nhau giữa các thành viên: {seeds} (không ảnh hưởng train, chỉ ảnh hưởng pick).")
    missing = [m for m in C.MEMBERS if m not in members_seen]
    if missing:
        (warnings if allow_partial else errors).append(f"Thiếu gói của: {missing}")

    if errors:
        for _, _, zf in opened:
            zf.close()
        raise BundleError("Không ghép được:\n- " + "\n- ".join(errors))

    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    n_files = 0
    for (_manifest, _split, zf) in opened:
        for name in zf.namelist():
            if not name.startswith("data/") or name.endswith("/"):
                continue
            target = (out_dir / name[len("data/"):]).resolve()
            if root not in target.parents:  # chặn đường dẫn thoát khỏi out_dir
                raise BundleError(f"Đường dẫn không hợp lệ trong gói: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
            n_files += 1
        zf.close()

    seed = next((s for s in seeds.values() if s is not None), None)
    ordered = {m: merged_members[m] for m in C.MEMBERS if m in merged_members}
    split_out = C.split_path(out_dir)
    split_out.parent.mkdir(parents=True, exist_ok=True)
    split_out.write_text(json.dumps({"seed": seed, "members": ordered}, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {}
    for member, info in ordered.items():
        ids = _split_paper_ids(info)
        manifest = next(m for (m, _s, _z), p in zip(opened, bundles) if m.get("member") == member)
        pos = {a: v["positives"] for a, v in manifest["papers"].items()}
        summary[member] = {
            "papers": len(ids), "manual": len(info.get("manual") or []), "auto": len(info.get("auto") or []),
            "positives_manual": sum(pos.get(a, 0) for a in info.get("manual") or []),
            "positives_auto": sum(pos.get(a, 0) for a in info.get("auto") or []),
            "test": {k: len(v) for k, v in (info.get("test") or {}).items()},
            "valid": {k: len(v) for k, v in (info.get("valid") or {}).items()},
        }
    report = {"out_dir": str(out_dir), "n_files": n_files, "members": summary, "warnings": warnings}
    (C.m3_dir(out_dir) / "merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_export(report: Dict[str, Any]) -> None:
    print(f"Thành viên {report['member']}: {report['n_papers']} bài, {report['n_files']} file.")
    for w in report["warnings"]:
        print(f"  CẢNH BÁO: {w}")
    if report["errors"]:
        print(f"  CÓ {len(report['errors'])} LỖI (gói vẫn được tạo nhưng merge sẽ từ chối):")
        for e in report["errors"]:
            print(f"   - {e}")
    else:
        print("  Không có lỗi.")
    print(f"Đã ghi {report['out']} ({report['size_mb']} MB). Gửi file này cho người ghép.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Đóng gói và ghép dữ liệu M3 giữa các máy.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export", help="Đóng gói dữ liệu của một thành viên")
    ex.add_argument("--data-dir", type=Path, default=C.DATA_DIR)
    ex.add_argument("--member", required=True, choices=list(C.MEMBERS))
    ex.add_argument("--out", type=Path, default=None, help="Mặc định: bundle_<member>.zip")
    ex.add_argument("--strict", action="store_true", help="Không tạo gói nếu còn lỗi")

    mg = sub.add_parser("merge", help="Ghép các gói thành một thư mục data chung")
    mg.add_argument("bundles", nargs="+", type=Path)
    mg.add_argument("--out-dir", type=Path, required=True)
    mg.add_argument("--force", action="store_true", help="Cho phép ghi vào thư mục đã có split.json")
    mg.add_argument("--allow-partial", action="store_true", help="Cho phép thiếu gói của một số thành viên")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "export":
            report = export_bundle(args.data_dir, args.member, args.out or Path(f"bundle_{args.member}.zip"), args.strict)
            _print_export(report)
            return 1 if report["errors"] else 0
        report = merge_bundles(args.bundles, args.out_dir, args.force, args.allow_partial)
    except BundleError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2

    print(f"Đã ghép {len(report['members'])} thành viên, {report['n_files']} file vào {report['out_dir']}")
    for member, s in report["members"].items():
        print(f"  {member:<7} bài={s['papers']:<4} tay={s['manual']} (nhãn dương {s['positives_manual']}) "
              f"auto={s['auto']} (nhãn dương {s['positives_auto']}) test={s['test']} valid={s['valid']}")
    for w in report["warnings"]:
        print(f"  CẢNH BÁO: {w}")
    print(f"\nBước tiếp theo:\n  python src/run_m3.py --data-dir {report['out_dir']} --stage train,predict,evaluate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())