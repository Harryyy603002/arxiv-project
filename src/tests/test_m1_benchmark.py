import time
from utils.benchmark import PerfRecorder

def test_m1_recorder(tmp_path):
    with PerfRecorder("m1", watch_dirs=[tmp_path], interval=0.05, disk_interval=0.1) as p:
        with p.stage("discovery"):
            time.sleep(0.1)
        with p.paper("2310.12345"):
            (tmp_path / "a.tex").write_bytes(b"x" * 300)
            p.record_paper_size("2310.12345", before_bytes=5000, after_bytes=300,
                                archive_bytes=1000, is_pdf_only=False)
            p.record_result("2310.12345", True)
        with p.paper("2310.99999"):
            p.record_paper_size("2310.99999", 0, 0, is_pdf_only=True)
            p.record_result("2310.99999", True)
    s = p.save(tmp_path / "out.json")
    assert s["n_papers_timed"] == 2
    assert s["discovery_s"] >= 0.1
    assert s["n_tex_versions"] == 1 and s["n_pdf_only_versions"] == 1
    assert s["avg_paper_size_before_bytes"] == 5000
    assert s["success_rate"] == 1.0
    assert s["peak_ram_mb"] > 0