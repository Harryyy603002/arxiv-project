import json
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crawler import m1_references as R  # noqa: E402
from utils import config as C  # noqa: E402
from run_m1 import crawl_references  # noqa: E402


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

    @contextmanager
    def paper(self, arxiv_id):
        yield

    def add_paper_time(self, arxiv_id, seconds):
        self.paper_times[arxiv_id] = self.paper_times.get(arxiv_id, 0) + seconds

    def record_references(self, arxiv_id, ok, total_refs=0, arxiv_refs=0):
        self.ref_stats[arxiv_id] = (ok, total_refs, arxiv_refs)


def test_batch_crawl_writes_and_skips_cached_results(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    papers = [{"references": [{"title": "Reference A"}]}, {"references": []}]
    session = FakeSession([FakeResponse(200, papers)])

    assert R.crawl(
        ["2606.20734", "2606.20735"], attempts=1, session=session, batch_size=25
    ) == (2, 0)
    assert len(session.calls) == 1
    url, request = session.calls[0]
    assert url == R.BATCH_URL
    assert request["json"]["ids"] == ["ARXIV:2606.20734", "ARXIV:2606.20735"]
    assert request["params"]["fields"] == C.S2_REF_FIELDS
    assert json.loads(C.refs_path("2606.20734").read_text()) == {
        "arxiv_id": "2606.20734", "references": papers[0]["references"]
    }

    assert R.crawl(
        ["2606.20734", "2606.20735"], attempts=1, session=session, batch_size=25
    ) == (0, 0)
    assert len(session.calls) == 1


def test_batch_crawl_splits_rejected_group(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(R, "polite_wait", lambda: None)
    monkeypatch.setattr(R, "_touch", lambda: None)
    session = FakeSession([
        FakeResponse(413, {}),
        FakeResponse(200, [{"references": []}]),
        FakeResponse(200, [{"references": []}]),
    ])

    assert R.crawl(
        ["2606.20734", "2606.20735"], attempts=1, session=session, batch_size=2
    ) == (2, 0)
    assert len(session.calls) == 3
    assert all(C.refs_path(arxiv_id).exists() for arxiv_id in ["2606.20734", "2606.20735"])


def test_run_m1_fetches_references_in_batches(tmp_path, monkeypatch):
    import run_m1

    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    ids = ["2606.20734", "2606.20735"]
    for arxiv_id in ids:
        C.ensure_dir(C.paper_dir(arxiv_id))
        C.meta_path(arxiv_id).write_text("{}", encoding="utf-8")

    calls = []

    def fetch_batch(_session, batch_ids):
        calls.append(batch_ids)
        return [{"references": [{"externalIds": {"ArXiv": "1234.5678"}}]} for _ in batch_ids]

    monkeypatch.setattr(R, "fetch_batch_group", fetch_batch)
    monkeypatch.setattr(run_m1, "build_references_table", lambda: 0)
    perf = FakePerf()

    crawl_references(ids, perf, batch_size=25)

    assert calls == [ids]
    assert perf.ref_stats == {arxiv_id: (True, 1, 1) for arxiv_id in ids}
    assert all(perf.paper_times[arxiv_id] >= 0 for arxiv_id in ids)
