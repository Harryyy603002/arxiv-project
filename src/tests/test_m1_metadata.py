"""Test offline cho m1_metadata (không gọi mạng): pytest src/tests/test_m1_metadata.py"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "crawler"))
from utils import config as C  # noqa: E402
import m1_metadata as M  # noqa: E402


def fake(entry_id, title="A  title\nwrapped"):
    return SimpleNamespace(
        entry_id=entry_id, title=title, summary="An abstract.\n",
        authors=[SimpleNamespace(name="Ann"), SimpleNamespace(name="Bob")],
        published=datetime(2026, 2, 5, 10, tzinfo=timezone.utc),
        updated=datetime(2026, 3, 1, 8, tzinfo=timezone.utc),
        primary_category="cs.LG", categories=["cs.LG", "stat.ML"],
        journal_ref=None, doi=None, comment="10 pages",
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    return tmp_path


def test_parse_entry_id():
    assert M.parse_entry_id("http://arxiv.org/abs/2310.12345v4") == ("2310.12345", 4)
    assert M.parse_entry_id("http://arxiv.org/abs/hep-th/9901001v2") == ("hep-th/9901001", 2)
    assert M.parse_entry_id("http://arxiv.org/api/errors#bad") is None


def test_build_versions():
    v = M.build_versions("2310.12345", 4, "P", "U")
    assert [x["version"] for x in v["versions"]] == ["v1", "v2", "v3", "v4"]
    assert [x["date"] for x in v["versions"]] == ["P", None, None, "U"]
    assert M.build_versions("x", 1, "P", "U")["versions"][0]["date"] == "P"


def test_store_results_and_missing(data_dir):
    res = [fake("http://arxiv.org/abs/2602.04870v2"), fake("http://arxiv.org/abs/hep-th/9901001v1"),
           fake("http://arxiv.org/api/errors#x")]
    ok, missing = M.store_results(["2602.04870", "hep-th/9901001", "2602.04871"], res)
    assert ok == 2 and missing == ["2602.04871"]
    meta = json.loads((data_dir / "2602-04870" / "meta.json").read_text())
    assert meta["title"] == "A title wrapped" and meta["latest_version"] == 2
    assert (data_dir / "hep-th-9901001" / "versions.json").exists()
    assert json.loads((data_dir / "2602-04870" / "versions.json").read_text())["versions"][1]["version"] == "v2"


def test_papers_table(data_dir):
    pytest.importorskip("pandas")
    M.store_results(["2602.04870"], [fake("http://arxiv.org/abs/2602.04870v1")])
    assert M.build_papers_table() == 1
    assert C.papers_csv_path().exists()