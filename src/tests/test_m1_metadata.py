"""Test offline cho phần đọc/chuẩn hóa ID của M1 (thay cho m1_metadata.py đã gộp vào run_m1):
pytest src/tests/test_m1_metadata.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_m1 import _parse_range, folder_name, parse_ids_list, read_ids, split_version  # noqa: E402

def test_split_version():
    assert split_version("2310.12345v4") == ("2310.12345", 4)
    assert split_version("hep-th/9901001v2") == ("hep-th/9901001", 2)
    assert split_version("2310.12345") == ("2310.12345", None)

def test_folder_name_matches_config_paper_key():
    from utils import config as C
    for arxiv_id in ("2310.12345", "hep-th/9901001"):
        assert folder_name(arxiv_id) == C.paper_key(arxiv_id)

def test_parse_range():
    assert _parse_range("2602.04870-2602.09869") == ("2602.04870", "2602.09869")
    assert _parse_range("260204870-260209869") == ("2602.04870", "2602.09869")
    assert _parse_range("2310.12345") is None

def test_read_ids_accepts_prefixes_suffixes_and_ranges(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text(
        "\ufeffarXiv:2310.12345v2\r\n"
        "https://arxiv.org/abs/2602.04870\n"
        "\n"
        "hep-th/9901001\n"
        "2603.00001-2603.00003\n"
        "not-an-id\n",
        encoding="utf-8",
    )

    assert read_ids(path) == [
        "2310.12345", "2602.04870", "hep-th/9901001", "2603.00001", "2603.00002", "2603.00003",
    ]


def test_parse_ids_list_from_cli():
    # ponytail: parse_ids_list takes an iterable of strings (single IDs or ranges).
    # Upgrade path: add support for arbitrary custom wildcards if needed.
    raw_args = ["2603.00001-2603.00003", "2310.12345"]
    assert parse_ids_list(raw_args) == [
        "2603.00001", "2603.00002", "2603.00003", "2310.12345"
    ]

