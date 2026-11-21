"""Small terminal tool for manual M3 labels and label-file validation.

The tool does not preload or reveal automatic labels. For each BibTeX
citation_key it shows candidate pairs ranked by title similarity. Annotators
may select a candidate, inspect the complete arXiv-reference list (to catch
candidate-generation misses), explicitly mark ``null``, or skip an entry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from m3_candidates import normalize_arxiv_id
except ImportError:  # pragma: no cover
    from .m3_candidates import normalize_arxiv_id


def paper_key(arxiv_id: str) -> str:
    return str(arxiv_id).strip().replace(".", "-").replace("/", "-")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được {path}: {exc}") from exc




def _read_json_unique_keys(path: Path) -> Any:
    """Read JSON while rejecting duplicate object keys (including labels)."""
    if not path.exists():
        return None
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON có key bị lặp: {key!r}")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"JSON không hợp lệ {path}: {exc}") from exc


def _refs_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        data = data.get("references", [])
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _ref_id(ref: dict[str, Any]) -> str | None:
    value = ref.get("ref_arxiv_id") or ref.get("arxiv_id")
    if not value:
        ext = ref.get("external_ids") or ref.get("externalIds") or {}
        if isinstance(ext, dict):
            value = ext.get("ArXiv") or ext.get("arxiv")
    return normalize_arxiv_id(value) if value else None


def _ref_title(ref: dict[str, Any]) -> str:
    return str(ref.get("ref_title") or ref.get("title") or "")


def _ref_authors(ref: dict[str, Any]) -> str:
    value = ref.get("ref_authors") or ref.get("authors") or ""
    return ", ".join(map(str, value)) if isinstance(value, list) else str(value)


def validate_label_data(
    label_data: dict[str, Any],
    bib_entries: list[dict[str, Any]],
    references: list[dict[str, Any]],
    min_positive: int = 0,
) -> list[str]:
    """Return validation errors; empty list means valid."""
    errors: list[str] = []
    if label_data.get("source") not in {"manual", "auto"}:
        errors.append("source phải là 'manual' hoặc 'auto'")
    if not str(label_data.get("arxiv_id") or "").strip():
        errors.append("Thiếu arxiv_id trong file nhãn")
    if not str(label_data.get("labeler") or "").strip():
        errors.append("Thiếu labeler")
    labels = label_data.get("labels")
    if not isinstance(labels, dict):
        errors.append("labels phải là object/dict")
        return errors
    bib_keys = [str(row.get("citation_key")) for row in bib_entries if row.get("citation_key")]
    valid_keys = set(bib_keys)
    duplicate_bib_keys = sorted({key for key in bib_keys if bib_keys.count(key) > 1})
    if duplicate_bib_keys:
        errors.append(f"bibtex_merged.json có citation_key trùng: {duplicate_bib_keys[:10]}")
    unknown_keys = sorted(set(labels) - valid_keys)
    if unknown_keys:
        errors.append(f"Labels chứa citation_key không có trong bibtex_merged.json: {unknown_keys[:10]}")

    valid_ref_ids = {_ref_id(ref) for ref in references}
    valid_ref_ids.discard(None)
    positive = {key: value for key, value in labels.items() if value is not None}
    if label_data.get("source") == "auto":
        evidence = label_data.get("evidence")
        if not isinstance(evidence, dict):
            errors.append("Nhãn auto bắt buộc có object evidence")
        else:
            for key in positive:
                if evidence.get(key) not in {"eprint", "doi", "title_exact", "title_fuzzy"}:
                    errors.append(f"{key}: auto evidence thiếu hoặc không hợp lệ")
    for key, value in positive.items():
        normalized = normalize_arxiv_id(value)
        if not normalized:
            errors.append(f"{key}: ref_arxiv_id không hợp lệ: {value!r}")
        elif normalized not in valid_ref_ids:
            errors.append(f"{key}: ref_arxiv_id {normalized} không có trong refs.json của paper")
    normalized_values = [normalize_arxiv_id(value) for value in positive.values() if value is not None]
    duplicates = sorted({value for value in normalized_values if normalized_values.count(value) > 1})
    if duplicates:
        errors.append(f"Một ref_arxiv_id bị gán cho nhiều citation_key: {duplicates[:10]}")
    if len(positive) < min_positive:
        errors.append(f"Chỉ có {len(positive)} positive labels; yêu cầu >= {min_positive}")
    return errors


def validate_label_file(
    corpus_dir: Path,
    arxiv_id: str,
    min_positive: int = 0,
) -> tuple[bool, list[str], int]:
    pdir = corpus_dir / paper_key(arxiv_id)
    label_path = corpus_dir / "m3" / "labels" / f"{pdir.name}.json"
    label_data = _read_json_unique_keys(label_path)
    if not isinstance(label_data, dict):
        return False, [f"Không tìm thấy label JSON: {label_path}"], 0
    bib = _read_json(pdir / "bibtex_merged.json", [])
    refs = _refs_list(_read_json(pdir / "refs.json", []))
    if not isinstance(bib, list):
        bib = []
    errors = validate_label_data(label_data, bib, refs, min_positive=min_positive)
    if str(label_data.get("arxiv_id")) != str(arxiv_id):
        errors.append(f"arxiv_id trong file ({label_data.get('arxiv_id')}) không khớp paper {arxiv_id}")
    positives = sum(value is not None for value in label_data.get("labels", {}).values())
    return not errors, errors, positives


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def annotate_paper(corpus_dir: Path, arxiv_id: str, labeler: str, overwrite: bool = False) -> Path:
    pdir = corpus_dir / paper_key(arxiv_id)
    bib_path = pdir / "bibtex_merged.json"
    refs_path = pdir / "refs.json"
    candidates_path = pdir / "candidates.json"
    if not bib_path.exists() or not refs_path.exists():
        raise FileNotFoundError(f"Paper {arxiv_id} thiếu bibtex_merged.json hoặc refs.json")
    bib = _read_json(bib_path, [])
    refs = _refs_list(_read_json(refs_path, []))
    candidates = _read_json(candidates_path, [])
    if not isinstance(bib, list) or not isinstance(candidates, list):
        raise ValueError("bibtex_merged.json và candidates.json phải là list")

    output_path = corpus_dir / "m3" / "labels" / f"{pdir.name}.json"
    existing_labels: dict[str, str | None] = {}
    if output_path.exists() and not overwrite:
        previous = _read_json_unique_keys(output_path)
        if not isinstance(previous, dict) or previous.get("source") != "manual":
            raise ValueError(f"File đã tồn tại nhưng không phải manual-label JSON hợp lệ: {output_path}")
        if str(previous.get("arxiv_id")) != str(arxiv_id):
            raise ValueError(f"arxiv_id trong file cũ không khớp {arxiv_id}")
        existing_labels = dict(previous.get("labels") or {})
        previous_errors = validate_label_data(previous, bib, refs)
        if previous_errors:
            raise ValueError("File nhãn partial cũ không hợp lệ:\n- " + "\n- ".join(previous_errors[:20]))

    refs_by_id: dict[str, dict[str, Any]] = {}
    for ref in refs:
        rid = _ref_id(ref)
        if rid:
            refs_by_id[rid] = ref
    candidate_by_key: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        key = str(cand.get("citation_key") or "")
        if key:
            candidate_by_key.setdefault(key, []).append(cand)
    for values in candidate_by_key.values():
        values.sort(key=lambda row: (
            float(row.get("title_similarity") or 0),
            float(row.get("author_similarity") or 0),
        ), reverse=True)

    labels: dict[str, str | None] = existing_labels
    used_refs: dict[str, str] = {}
    for citation_key, ref_id in labels.items():
        if ref_id is not None:
            normalized = normalize_arxiv_id(ref_id) or str(ref_id)
            if normalized in used_refs and used_refs[normalized] != citation_key:
                raise ValueError(f"File nhãn cũ gán trùng reference {normalized}")
            used_refs[normalized] = citation_key
    print(f"\nManual labeling: {arxiv_id} | {len(bib)} BibTeX entries | {len(refs_by_id)} references có arXiv ID")
    print(f"Đã có nhãn từ lần trước: {len(labels)} entries; sẽ tiếp tục các entry còn thiếu.")
    print("Lệnh: số = chọn candidate | n = null | a = xem toàn bộ refs | s = bỏ qua | q = lưu và thoát")
    for index, entry in enumerate(bib, 1):
        key = str(entry.get("citation_key") or "").strip()
        if not key:
            print(f"[{index}/{len(bib)}] Bỏ qua entry không có citation_key")
            continue
        if key in labels:
            continue
        title = str(entry.get("title") or "(không có title)")
        author = str(entry.get("author") or entry.get("authors") or "")
        eprint = str(entry.get("eprint") or "")
        doi = str(entry.get("doi") or "")
        options = candidate_by_key.get(key, [])
        print("\n" + "-" * 88)
        print(f"[{index}/{len(bib)}] {key}\nTITLE: {title}\nAUTHOR: {author[:220]}\nEPRINT: {eprint or '(none)'}\nDOI: {doi or '(none)'}")
        if options:
            print("Candidates theo title_similarity:")
            for n, cand in enumerate(options, 1):
                rid = normalize_arxiv_id(cand.get("ref_arxiv_id")) or str(cand.get("ref_arxiv_id") or "")
                print(f"  {n:>2}. {rid:<18} title={float(cand.get('title_similarity') or 0):.3f} "
                      f"author={float(cand.get('author_similarity') or 0):.3f} | {str(cand.get('ref_title') or '')[:120]}")
        else:
            print("Không có candidate (đây có thể là candidate-generation miss).")

        while True:
            choice = input("Chọn candidate / n / a / s / q: ").strip().lower()
            if choice == "q":
                payload = {"arxiv_id": arxiv_id, "source": "manual", "labeler": labeler, "labels": labels}
                errors = validate_label_data(payload, bib, refs)
                if errors:
                    print("Cảnh báo khi lưu partial:")
                    for err in errors:
                        print(" -", err)
                _atomic_write(output_path, payload)
                print(f"Đã lưu nhãn một phần: {output_path}")
                return output_path
            if choice == "s":
                break
            if choice == "n":
                labels[key] = None
                break
            if choice == "a":
                all_refs = sorted(refs_by_id.items(), key=lambda item: item[0])
                print("Tất cả reference có arXiv ID (không chỉ candidates):")
                for n, (rid, ref) in enumerate(all_refs, 1):
                    print(f"  {n:>3}. {rid:<18} | {_ref_title(ref)[:105]} | {_ref_authors(ref)[:85]} | year={ref.get('ref_year', ref.get('year', ''))}")
                raw = input("Nhập số reference trong danh sách (Enter để quay lại): ").strip()
                if not raw:
                    continue
                try:
                    selected = all_refs[int(raw) - 1]
                except (ValueError, IndexError):
                    print("Số không hợp lệ")
                    continue
                rid = selected[0]
                if rid in used_refs and used_refs[rid] != key:
                    print(f"Reference {rid} đã gán cho {used_refs[rid]}; mỗi ref chỉ gán một lần trong một paper.")
                    continue
                labels[key] = rid
                used_refs[rid] = key
                break
            try:
                number = int(choice)
                cand = options[number - 1]
            except (ValueError, IndexError):
                print("Lựa chọn không hợp lệ")
                continue
            rid = normalize_arxiv_id(cand.get("ref_arxiv_id")) or str(cand.get("ref_arxiv_id") or "")
            if not rid or rid not in refs_by_id:
                print(f"Candidate không ánh xạ được về refs.json: {rid!r}; chọn 'a' để kiểm tra reference")
                continue
            if rid in used_refs and used_refs[rid] != key:
                print(f"Reference {rid} đã gán cho {used_refs[rid]}; không được gán trùng.")
                continue
            labels[key] = rid
            used_refs[rid] = key
            break

    payload = {"arxiv_id": arxiv_id, "source": "manual", "labeler": labeler, "labels": labels}
    errors = validate_label_data(payload, bib, refs)
    if errors:
        print("CẢNH BÁO kiểm tra file nhãn:")
        for err in errors:
            print(" -", err)
    _atomic_write(output_path, payload)
    positives = sum(value is not None for value in labels.values())
    print(f"Đã lưu: {output_path} | positive labels={positives}/{len(bib)}")
    if positives < 20:
        print("CHƯA ĐẠT: bài manual cần ít nhất 20 entry có reference đúng.")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Gán nhãn tay và kiểm tra file labels M3")
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--labeler", default="Quoc")
    parser.add_argument("--overwrite", action="store_true", help="Xóa nhãn cũ và bắt đầu gán lại từ đầu; mặc định tiếp tục file partial")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--min-positive", type=int, default=20)
    args = parser.parse_args()
    if args.validate_only:
        valid, errors, positives = validate_label_file(args.corpus_dir, args.paper_id, args.min_positive)
        print(f"{args.paper_id}: {'VALID' if valid else 'INVALID'} | positive={positives}")
        for error in errors:
            print(" -", error)
        raise SystemExit(0 if valid else 1)
    annotate_paper(args.corpus_dir, args.paper_id, args.labeler, args.overwrite)


if __name__ == "__main__":
    main()
