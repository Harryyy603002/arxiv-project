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
import math
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


def split_paper_ids(member_info: Dict[str, Any]) -> List[str]:
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
# Kiểm tra tuân thủ đề cho phần của MỘT thành viên (mỗi repo HF cá nhân phải tự đạt)
# ---------------------------------------------------------------------------

MANUAL_PAPERS_PER_MEMBER = 5
MIN_AUTO_PAPERS_PER_MEMBER = 2          # 2*k với k = 1
MIN_MANUAL_POSITIVES_TOTAL = 100        # 100*k với k = 1
MIN_POSITIVES_PER_MANUAL_PAPER = 20
AUTO_FRACTION = 0.10


def _papers_in_range(data_dir: Path, member: str, member_info: Dict[str, Any]) -> Tuple[int, str]:
    """Số bài của range: đếm paper.json thực tế; không có thì dùng số lúc pick đã ghi trong split."""
    try:
        from matching.m3_pick_papers import paper_json_ids
        found = paper_json_ids(data_dir, member)
        if found:
            return len(found), "paper.json"
    except Exception:  # pragma: no cover - không để lỗi phụ làm hỏng việc kiểm tra
        pass
    total = (member_info.get("selection_metadata") or {}).get("total_papers_in_range")
    return (int(total), "split.json (không thấy paper.json)") if total else (0, "không xác định")


def check_member_compliance(data_dir: Path, member: str, split: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Kiểm tra phần của `member` có tự thỏa bảng yêu cầu của đề (mục 2.2) hay không.

    Đọc trực tiếp split.json và m3/labels/*.json, không dựa vào selection_metadata.
    Trả về {"member", "ok", "rows": [(hạng mục, yêu cầu, thực tế, đạt)], "errors", "warnings"}.
    """
    from matching.m3_pick_papers import partition_map

    data_dir = Path(data_dir)
    errors: List[str] = []
    warnings: List[str] = []
    rows: List[Tuple[str, str, str, bool]] = []

    def add(name: str, required: str, actual: Any, ok: bool, error: Optional[str] = None) -> None:
        rows.append((name, required, str(actual), bool(ok)))
        if not ok:
            errors.append(error or f"{name}: cần {required}, hiện {actual}")

    if member not in C.MEMBERS:
        raise BundleError(f"Thành viên không hợp lệ: {member!r}. Chọn trong {C.MEMBERS}")
    if split is None:
        split_file = C.split_path(data_dir)
        if not split_file.exists():
            raise BundleError(f"Chưa có {split_file}. Chạy `run_m3.py --stage pick --member {member}` trước.")
        split = _read_json(split_file)
    info = (split.get("members") or {}).get(member)
    if not info:
        raise BundleError(f"split.json không có thành viên {member}.")

    manual = [str(a) for a in info.get("manual") or []]
    auto = [str(a) for a in info.get("auto") or []]

    # --- cấu trúc split ---------------------------------------------------------------
    try:
        partitions = partition_map(split, member)
        structure_ok, structure_msg = True, "hợp lệ"
    except (ValueError, KeyError) as exc:
        partitions, structure_ok, structure_msg = {}, False, str(exc)
    add("Cấu trúc split (không trùng bài, valid/test thuộc manual/auto)", "hợp lệ", structure_msg, structure_ok,
        f"split.json không hợp lệ: {structure_msg}")
    out_of_range = [a for a in manual + auto if not C.in_range(a, member)]
    add("Mọi bài thuộc đúng range của thành viên", "0 bài ngoài range", f"{len(out_of_range)} bài ngoài range",
        not out_of_range, f"Bài ngoài range {C.MEMBER_RANGES[member]}: {out_of_range[:5]}")

    # --- bài gán tay ------------------------------------------------------------------
    add("Số bài gán tay (khác nhau)", f">= {MANUAL_PAPERS_PER_MEMBER}", len(set(manual)),
        len(set(manual)) >= MANUAL_PAPERS_PER_MEMBER)
    manual_ok, manual_positive_total = 0, 0
    for aid in manual:
        label_path = C.labels_path(aid, data_dir)
        source = _read_json(label_path).get("source") if label_path.exists() else None
        valid, label_errors, n_pos = validate_label_file(data_dir, aid, MIN_POSITIVES_PER_MANUAL_PAPER)
        if source != "manual":
            errors.append(f"{aid}: bài gán tay nhưng file nhãn có source={source!r}")
        elif not valid:
            errors.append(f"{aid}: bài gán tay không đạt (>= {MIN_POSITIVES_PER_MANUAL_PAPER} nhãn dương): {label_errors[:2]}")
        else:
            manual_ok += 1
            manual_positive_total += n_pos
    rows.append((f"Bài gán tay đạt (source=manual, >= {MIN_POSITIVES_PER_MANUAL_PAPER} nhãn dương, hợp lệ)",
                 f"{len(manual)}/{len(manual)}", f"{manual_ok}/{len(manual)}", manual_ok == len(manual) and len(manual) > 0))
    add("Tổng nhãn dương gán tay", f">= {MIN_MANUAL_POSITIVES_TOTAL}", manual_positive_total,
        manual_positive_total >= MIN_MANUAL_POSITIVES_TOTAL)

    # --- bài tự động ------------------------------------------------------------------
    n_range, n_source = _papers_in_range(data_dir, member, info)
    need_auto = max(MIN_AUTO_PAPERS_PER_MEMBER, math.ceil(AUTO_FRACTION * max(n_range - MANUAL_PAPERS_PER_MEMBER, 0)))
    auto_ok, auto_bad = 0, []
    for aid in auto:
        label_path = C.labels_path(aid, data_dir)
        if not label_path.exists():
            auto_bad.append(f"{aid} (thiếu file nhãn)")
            continue
        source = _read_json(label_path).get("source")
        valid, label_errors, n_pos = validate_label_file(data_dir, aid, 1)
        if source != "auto":
            auto_bad.append(f"{aid} (source={source!r})")
        elif not valid or n_pos < 1:
            auto_bad.append(f"{aid} (không có nhãn dương hợp lệ)")
        else:
            auto_ok += 1
    if auto_bad:
        errors.append(f"{len(auto_bad)} bài auto trong split không có nhãn dương hợp lệ: {auto_bad[:5]}"
                      + (" ..." if len(auto_bad) > 5 else ""))
    add(f"Bài auto có nhãn dương: 10% x ({n_range} - 5), range đếm theo {n_source}", f">= {need_auto}", auto_ok,
        auto_ok >= need_auto, f"Bài auto có nhãn dương: {auto_ok}, cần >= {need_auto} (range {n_range} bài, theo {n_source})")

    # --- test / valid / train ---------------------------------------------------------
    for part in ("test", "valid"):
        got = info.get(part) or {}
        n_manual_part = len(got.get("manual") or [])
        n_auto_part = len(got.get("auto") or [])
        add(f"{part}: bài gán tay", "1", n_manual_part, n_manual_part == 1)
        add(f"{part}: bài tự động", "1", n_auto_part, n_auto_part == 1)
    train_manual = [a for a in manual if partitions.get(a) == "train"]
    train_auto = [a for a in auto if partitions.get(a) == "train"]
    want_train_manual = max(len(manual) - 2, 0)
    add("train: bài gán tay (tất cả bài tay còn lại)", str(want_train_manual), len(train_manual),
        structure_ok and len(train_manual) == want_train_manual)
    add("train: bài tự động (tất cả bài auto còn lại)", ">= 1", len(train_auto), structure_ok and len(train_auto) >= 1)

    return {"member": member, "ok": not errors, "rows": rows, "errors": errors, "warnings": warnings}


def print_compliance(report: Dict[str, Any]) -> None:
    print(f"\nKiểm tra tuân thủ đề cho {report['member']}:")
    width = max(len(r[0]) for r in report["rows"])
    for name, required, actual, ok in report["rows"]:
        print(f"  [{'ĐẠT' if ok else 'LỖI'}] {name:<{width}} | cần {required} | hiện {actual}")
    for err in report["errors"]:
        print(f"  - LỖI: {err}")
    for w in report["warnings"]:
        print(f"  CẢNH BÁO: {w}")
    print(f"=> {'ĐẠT TOÀN BỘ' if report['ok'] else 'CHƯA ĐẠT: ' + str(len(report['errors'])) + ' lỗi'}")


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

    ids = split_paper_ids(member_info)
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

    # Yêu cầu của đề cho riêng phần của thành viên này (bài tay, quota auto, test/valid/train).
    compliance = check_member_compliance(data_dir, member, split)
    errors.extend(compliance["errors"])
    warnings.extend(compliance["warnings"])

    report = {"member": member, "n_papers": len(ids), "errors": errors, "warnings": warnings,
              "positives": positives, "n_files": len(files), "compliance": compliance}
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
        ids = split_paper_ids(info)
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
    if report.get("compliance"):
        print_compliance(report["compliance"])
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

    ck = sub.add_parser("check", help="Chỉ kiểm tra phần của một thành viên có đạt yêu cầu của đề không")
    ck.add_argument("--data-dir", type=Path, default=C.DATA_DIR)
    ck.add_argument("--member", required=True, choices=list(C.MEMBERS))

    mg = sub.add_parser("merge", help="Ghép các gói thành một thư mục data chung")
    mg.add_argument("bundles", nargs="+", type=Path)
    mg.add_argument("--out-dir", type=Path, required=True)
    mg.add_argument("--force", action="store_true", help="Cho phép ghi vào thư mục đã có split.json")
    mg.add_argument("--allow-partial", action="store_true", help="Cho phép thiếu gói của một số thành viên")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            report = check_member_compliance(args.data_dir, args.member)
            print_compliance(report)
            return 0 if report["ok"] else 1
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