import json
import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_m1
from crawler import m1_download


class FakePerf:
    def __init__(self):
        self.sizes = []
        self.results = {}
        self.references = {}
        self.paper_times = {}

    def add_paper_time(self, arxiv_id, seconds):
        self.paper_times[arxiv_id] = seconds

    def paper(self, _arxiv_id):
        from contextlib import nullcontext

        return nullcontext()

    def record_paper_size(self, arxiv_id, before, after, archive_bytes, is_pdf_only):
        self.sizes.append((arxiv_id, before, after, archive_bytes, is_pdf_only))

    def record_references(self, arxiv_id, ok, total_refs=0, arxiv_refs=0):
        self.references[arxiv_id] = (ok, total_refs, arxiv_refs)

    def record_result(self, arxiv_id, ok):
        self.results[arxiv_id] = ok


def test_run_m1_uses_canonical_downloader_and_checkpoints_versions(tmp_path, monkeypatch):
    calls = []

    def fetch_source(_session, arxiv_id, version):
        calls.append((arxiv_id, version))
        return b"source"

    def save_source_with_stats(data, out_dir):
        assert data == b"source"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "main.tex").write_bytes(data)
        return "ok", "", ["main.tex"], 10, 20, 8

    monkeypatch.setattr(run_m1.m1_download, "fetch_source", fetch_source)
    monkeypatch.setattr(run_m1.m1_download, "save_source_with_stats", save_source_with_stats)
    monkeypatch.setattr(
        run_m1.m1_references,
        "fetch_batch_group",
        lambda _session, group: [{"references": []} for _ in group],
    )

    info = {
        "1234.56789": {
            "title": "Test",
            "authors": [],
            "venue": None,
            "n_versions": 2,
            "version_dates": {1: "2020-01-01", 2: "2021-01-01"},
        }
    }
    coverage = {
        "expected_versions": 0,
        "crawled_versions": 0,
        "failed_versions": 0,
        "not_found_papers": 0,
    }
    perf = FakePerf()

    run_m1.crawl_batch(
        ["1234.56789"], info, perf, tmp_path, object(), coverage
    )
    run_m1.crawl_batch(
        ["1234.56789"], info, perf, tmp_path, object(), coverage
    )

    assert calls == [("1234.56789", 1), ("1234.56789", 2)]
    assert coverage["crawled_versions"] == 4
    assert coverage["failed_versions"] == 0
    assert perf.results["1234.56789"] is True

    checkpoint = tmp_path / "1234-56789" / "v1" / "metadata.json"
    record = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert record["status"] == "ok"
    assert record["files"] == ["main.tex"]


def test_save_source_with_stats_extracts_and_accounts_in_one_pass(tmp_path):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, content in (
            ("main.tex", b"\\documentclass{article}"),
            ("refs.bib", b"@article{x}"),
            ("figure.png", b"image"),
        ):
            payload = io.BytesIO(content)
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, payload)

    data = archive.getvalue()
    status, reason, files, archive_bytes, before, after = (
        m1_download.save_source_with_stats(data, tmp_path)
    )

    assert (status, reason) == ("ok", "")
    assert files == ["main.tex", "refs.bib"]
    assert archive_bytes == len(data)
    assert before == len(b"\\documentclass{article}") + len(b"@article{x}") + len(b"image")
    assert after == len(b"\\documentclass{article}") + len(b"@article{x}")


def test_standalone_process_writes_atomic_stats_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(m1_download, "version_dir", lambda _aid, _version: tmp_path / "v1")
    monkeypatch.setattr(
        m1_download,
        "fetch_source",
        lambda _session, _aid, _version: b"\\documentclass{article}",
    )

    assert m1_download.process_version(object(), "1234.56789", 1) == "ok"

    record = json.loads(
        (tmp_path / "v1" / "source_status.json").read_text(encoding="utf-8")
    )
    assert record["raw_bytes"] == len(b"\\documentclass{article}")
    assert record["uncompressed_bytes"] == len(b"\\documentclass{article}")
    assert record["extracted_bytes"] == len(b"\\documentclass{article}")
    assert not (tmp_path / "v1" / "source_status.json.tmp").exists()
