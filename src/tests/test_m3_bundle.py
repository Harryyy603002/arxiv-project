"""Kiểm thử m3_bundle: đóng gói theo thành viên và ghép nhiều máy (chạy: pytest tests/test_m3_bundle.py)."""
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from matching import m3_bundle as B  # noqa: E402
from utils import config as C  # noqa: E402

START = {"Nguyen": ("2602", 4870), "Khai": ("2603", 580)}


def _make_paper(root: Path, aid: str, source: str, columns=("candidate_id", "str_title_sim", "str_eprint_match")):
    pk = C.paper_key(aid)
    pdir = root / pk
    pdir.mkdir(parents=True)
    n = 22
    bib = [{"citation_key": f"k{i}", "arxiv_id": aid, "title": f"title {i}"} for i in range(n)]
    refs = [{"ref_arxiv_id": f"1901.{i:05d}", "ref_title": f"title {i}"} for i in range(n)]
    cands = [{"candidate_id": f"{aid}-{i}", "arxiv_id": aid, "citation_key": f"k{i}", "ref_arxiv_id": f"1901.{i:05d}"} for i in range(n)]
    (pdir / "bibtex_merged.json").write_text(json.dumps(bib))
    (pdir / "refs.json").write_text(json.dumps({"arxiv_id": aid, "references": refs}))
    (pdir / "candidates.json").write_text(json.dumps(cands))
    labels = {f"k{i}": f"1901.{i:05d}" for i in range(n)}
    payload = {"arxiv_id": aid, "source": source, "labeler": "t", "labels": labels}
    if source == "auto":
        payload["evidence"] = {k: "title_exact" for k in labels}
    lp = C.labels_path(aid, root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(json.dumps(payload))
    fp = C.features_path(aid, root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({c: [0.0] for c in columns}).to_parquet(fp)


def _make_member(root: Path, member: str, columns=("candidate_id", "str_title_sim", "str_eprint_match")) -> Path:
    prefix, start = START[member]
    ids = [f"{prefix}.{start + i:05d}" for i in range(6)]
    for i, aid in enumerate(ids):
        _make_paper(root, aid, "manual" if i < 5 else "auto", columns)
    info = {"range": "x", "manual": ids[:5], "auto": ids[5:], "manual_backup_candidates": [],
            "test": {"manual": [ids[0]], "auto": [ids[5]]}, "valid": {"manual": [ids[1]], "auto": [ids[5]]},
            "selection_metadata": {}}
    sp = C.split_path(root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps({"seed": 42, "members": {member: info}}))
    return root


@pytest.fixture
def two_bundles(tmp_path):
    out = {}
    for member in ("Nguyen", "Khai"):
        root = _make_member(tmp_path / member, member)
        out[member] = tmp_path / f"bundle_{member}.zip"
        report = B.export_bundle(root, member, out[member], strict=True)
        assert report["errors"] == []
    return out


def test_export_contains_only_needed_files(two_bundles):
    names = zipfile.ZipFile(two_bundles["Nguyen"]).namelist()
    assert "manifest.json" in names and "split.json" in names
    assert sum(n.endswith("candidates.json") for n in names) == 6
    assert sum("m3/features/" in n for n in names) == 6
    assert not any("elements" in n or "hierarchy" in n for n in names)


def test_merge_combines_members_into_one_split(two_bundles, tmp_path):
    report = B.merge_bundles([two_bundles["Nguyen"], two_bundles["Khai"]], tmp_path / "merged", allow_partial=True)
    split = json.loads(C.split_path(tmp_path / "merged").read_text())
    assert list(split["members"]) == ["Nguyen", "Khai"] and split["seed"] == 42
    assert report["members"]["Nguyen"]["positives_manual"] == 5 * 22
    assert C.labels_path("2603.00580", tmp_path / "merged").exists()
    assert C.features_path("2602.04870", tmp_path / "merged").exists()


def test_merge_requires_all_members_unless_allowed(two_bundles, tmp_path):
    with pytest.raises(B.BundleError, match="Thiếu gói"):
        B.merge_bundles([two_bundles["Nguyen"]], tmp_path / "m")


def test_strict_export_rejects_missing_label(tmp_path):
    root = _make_member(tmp_path / "n", "Nguyen")
    C.labels_path("2602.04875", root).unlink()
    with pytest.raises(B.BundleError, match="thiếu file nhãn"):
        B.export_bundle(root, "Nguyen", tmp_path / "x.zip", strict=True)


def test_export_rejects_too_few_manual_positives(tmp_path):
    root = _make_member(tmp_path / "n", "Nguyen")
    lp = C.labels_path("2602.04870", root)
    data = json.loads(lp.read_text())
    data["labels"] = dict(list(data["labels"].items())[:3])
    lp.write_text(json.dumps(data))
    report = B.export_bundle(root, "Nguyen", tmp_path / "x.zip")
    assert any("cần >= 20" in e for e in report["errors"])


def test_export_flags_papers_built_with_old_feature_code(tmp_path):
    root = _make_member(tmp_path / "n", "Nguyen")
    pd.DataFrame({"candidate_id": ["a"], "str_title_sim": [0.0]}).to_parquet(C.features_path("2602.04872", root))
    report = B.export_bundle(root, "Nguyen", tmp_path / "x.zip")
    assert any("bộ cột feature khác" in e for e in report["errors"])


def test_merge_rejects_different_feature_columns_between_members(tmp_path):
    a = _make_member(tmp_path / "a", "Nguyen")
    b = _make_member(tmp_path / "b", "Khai", columns=("candidate_id", "str_title_sim"))  # máy dùng code cũ
    za, zb = tmp_path / "a.zip", tmp_path / "b.zip"
    B.export_bundle(a, "Nguyen", za, strict=True)
    B.export_bundle(b, "Khai", zb, strict=True)
    with pytest.raises(B.BundleError, match="Bộ cột feature"):
        B.merge_bundles([za, zb], tmp_path / "m")


def test_merge_rejects_paper_outside_member_range(tmp_path):
    root = _make_member(tmp_path / "n", "Nguyen")
    bundle = tmp_path / "n.zip"
    B.export_bundle(root, "Nguyen", bundle, strict=True)
    tampered = tmp_path / "t.zip"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "manifest.json":
                m = json.loads(data)
                m["member"] = "Khai"  # gói của Nguyên nhưng khai là Khải
                data = json.dumps(m).encode()
            elif name == "split.json":
                sp = json.loads(data)
                sp["members"] = {"Khai": sp["members"]["Nguyen"]}
                data = json.dumps(sp).encode()
            dst.writestr(name, data)
    with pytest.raises(B.BundleError, match="ngoài range"):
        B.merge_bundles([tampered], tmp_path / "m", allow_partial=True)


def test_merge_blocks_path_traversal_and_refuses_existing_output(two_bundles, tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(two_bundles["Nguyen"]) as src, zipfile.ZipFile(evil, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("data/../../escaped.txt", "x")
    with pytest.raises(B.BundleError, match="không hợp lệ"):
        B.merge_bundles([evil], tmp_path / "m", allow_partial=True)
    assert not (tmp_path / "escaped.txt").exists()
    B.merge_bundles([two_bundles["Nguyen"]], tmp_path / "ok", allow_partial=True)
    with pytest.raises(B.BundleError, match="đã tồn tại"):
        B.merge_bundles([two_bundles["Nguyen"]], tmp_path / "ok", allow_partial=True)
