"""Test offline cho M1 references (không gọi mạng): pytest src/tests/test_m1_references.py

Luồng hiện tại: crawler/m1_references.fetch_batch_group() gọi Semantic Scholar theo batch,
run_m1.crawl_batch() điều phối (source arXiv tuần tự + reference song song) và ghi checkpoint.
"""
import json
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import m1_references as R  # noqa: E402
from utils import config as C  # noqa: E402
from utils.schemas import validate_reference_rows  # noqa: E402
import run_m1  # noqa: E402

class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.headers = {}

    def json(self):
        return self.payload

class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)

class FakePerf:
    def __init__(self):
        self.paper_times = {}
        self.ref_stats = {}
        self.results = {}

    def paper(self, _arxiv_id):
        return nullcontext()

    def add_paper_time(self, arxiv_id, seconds):
        self.paper_times[arxiv_id] = self.paper_times.get(arxiv_id, 0) + seconds

    def record_references(self, arxiv_id, ok, total_refs=0, arxiv_refs=0):
        self.ref_stats[arxiv_id] = (ok, total_refs, arxiv_refs)

    def record_paper_size(self, *_a, **_k):
        pass

    def record_result(self, arxiv_id, ok):
        self.results[arxiv_id] = ok

def _info(ids, n_versions=1):
    return {aid: {"title": "T", "authors": [], "venue": None, "n_versions": n_versions,
                  "version_dates": {1: "2020-01-01"}} for aid in ids}

def _coverage():
    return {"expected_versions": 0, "crawled_versions": 0, "failed_versions": 0,
            "not_found_papers": 0, "cached_versions": 0, "cached_refs_papers": 0}

def _fake_source(monkeypatch):
    monkeypatch.setattr(run_m1.m1_download, "fetch_source", lambda *_a: b"source")
    monkeypatch.setattr(
        run_m1.m1_download, "save_source_with_stats",
        lambda _data, out: (out.mkdir(parents=True, exist_ok=True),
                            (out / "main.tex").write_bytes(b"s"),
                            ("ok", "", ["main.tex"], 1, 1, 1))[-1],
    )

@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(R, "polite_wait", lambda: None)
    monkeypatch.setattr(R, "_circuit_breaker", lambda: None)

def test_fetch_batch_group_uses_one_request_for_whole_group(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    papers = [{"references": [{"title": "Reference A"}]}, {"references": []}]
    session = FakeSession([FakeResponse(200, papers)])

    assert R.fetch_batch_group(session, ["2606.20734", "2606.20735"], attempts=1) == papers
    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == R.BATCH_URL
    assert request["json"]["ids"] == ["ARXIV:2606.20734", "ARXIV:2606.20735"]
    assert request["params"]["fields"] == C.S2_REF_FIELDS

def test_fetch_batch_group_splits_rejected_group():
    session = FakeSession([
        FakeResponse(413, {}),
        FakeResponse(200, [{"references": []}]),
        FakeResponse(200, [{"references": []}]),
    ])

    assert R.fetch_batch_group(session, ["2606.20734", "2606.20735"], attempts=1) == [
        {"references": []}, {"references": []},
    ]
    assert len(session.calls) == 3

def test_fetch_batch_group_returns_none_for_single_rejected_id():
    session = FakeSession([FakeResponse(400, {})])

    assert R.fetch_batch_group(session, ["2606.20734"], attempts=1) == [None]

def test_fetch_batch_group_raises_on_server_error():
    session = FakeSession([FakeResponse(500, {})])

    with pytest.raises(R.ReferenceCrawlError, match="HTTP 500"):
        R.fetch_batch_group(session, ["2606.20734"], attempts=1)

def test_crawl_batch_writes_refs_and_reuses_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    _fake_source(monkeypatch)
    ids = ["2606.20734", "2606.20735"]
    papers = [{"references": [{"externalIds": {"ArXiv": "1234.5678"}}]}, {"references": []}]
    calls = []

    def fetch_batch(_session, batch_ids):
        calls.append(list(batch_ids))
        return papers[:len(batch_ids)]

    monkeypatch.setattr(R, "fetch_batch_group", fetch_batch)
    perf, coverage = FakePerf(), _coverage()

    run_m1.crawl_batch(ids, _info(ids), perf, tmp_path, object(), coverage)

    assert calls == [ids]
    assert json.loads(C.refs_path(ids[0]).read_text())["references"][0]["ref_arxiv_id"] == "1234.5678"
    assert json.loads(C.raw_refs_path(ids[0]).read_text())["response"] == papers[0]
    assert perf.ref_stats == {ids[0]: (True, 1, 1), ids[1]: (True, 0, 0)}
    assert coverage["cached_refs_papers"] == 0

    # Lượt hai: đã có refs.json + raw_refs.json -> không gọi lại Semantic Scholar
    calls.clear()
    run_m1.crawl_batch(ids, _info(ids), perf, tmp_path, object(), coverage)

    assert calls == []
    assert coverage["cached_refs_papers"] == 2

def test_crawl_batch_records_structured_failure_when_batch_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    _fake_source(monkeypatch)

    def fetch_batch(_session, _batch_ids):
        raise R.ReferenceCrawlError("HTTP 500")

    monkeypatch.setattr(R, "fetch_batch_group", fetch_batch)
    perf = FakePerf()

    run_m1.crawl_batch(["2606.20734"], _info(["2606.20734"]), perf, tmp_path, object(), _coverage())

    record = json.loads(C.refs_failures_path().read_text(encoding="utf-8").splitlines()[0])
    assert record["arxiv_id"] == "2606.20734"
    assert record["category"] == "request_failure"
    assert "x-api-key" not in record["error"].lower()
    assert not C.refs_path("2606.20734").exists()
    assert perf.ref_stats == {"2606.20734": (False, 0, 0)}

def test_crawl_batch_skips_papers_not_in_arxiv_api(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    perf = FakePerf()

    run_m1.crawl_batch(["2606.20734"], {}, perf, tmp_path, object(), _coverage())

    assert perf.results == {"2606.20734": False}

def test_published_references_export_filters_arxiv_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    C.ensure_dir(C.paper_dir("2606.20734"))
    C.refs_path("2606.20734").write_text(json.dumps({
        "arxiv_id": "2606.20734",
        "references": [
            {"paperId": "s2-a", "title": "Linked", "externalIds": {"ArXiv": "1234.56789"}},
            {"paperId": "s2-b", "title": "Unlinked", "externalIds": {}},
        ],
    }), encoding="utf-8")

    rows, stats = R.collect_published_rows()

    assert stats == {
        "total_local_references": 2,
        "references_with_arxiv_id": 1,
        "references_without_arxiv_id": 1,
        "published_rows": 1,
        "duplicate_rows": 0,
    }
    assert [row["ref_arxiv_id"] for row in rows] == ["1234.56789"]

    R.write_published_csv(rows, stats)
    lines = C.references_csv_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "1234.56789" in lines[1]

def test_published_export_deduplicates_reference_identity_and_reports_count(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    C.ensure_dir(C.paper_dir("2606.20734"))
    duplicate = {"paperId": "s2-a", "title": "Linked", "externalIds": {"ArXiv": "1234.56789"}}
    C.refs_path("2606.20734").write_text(json.dumps({
        "arxiv_id": "2606.20734",
        "references": [duplicate, duplicate],
    }), encoding="utf-8")

    _, stats = R.collect_published_rows()

    assert stats["total_local_references"] == 2
    assert stats["duplicate_rows"] == 1
    assert stats["published_rows"] == 1

def test_reference_ids_are_normalized_without_changing_raw_payload():
    row = R.normalize_reference("https://arxiv.org/abs/2606.20734v2", {
        "externalIds": {"ArXiv": "arXiv:hep-th/9901001v3"},
        "title": "Reference",
    })

    assert row["arxiv_id"] == "2606.20734"
    assert row["ref_arxiv_id"] == "hep-th/9901001"

def test_published_reference_schema_rejects_empty_identifiers():
    with pytest.raises(ValueError, match="empty identifier"):
        validate_reference_rows([{
            "arxiv_id": "",
            "ref_arxiv_id": "",
            "ref_title": None,
            "ref_authors": [],
            "ref_submission_date": None,
            "ref_venue": None,
            "ref_s2_id": None,
            "external_ids": {},
        }])
