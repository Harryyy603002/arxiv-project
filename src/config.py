from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"          # nằm trên máy/Colab, bị .gitignore


def paper_key(arxiv_id: str) -> str:
    """'2310.12345' -> '2310-12345';  'hep-th/9901001' -> 'hep-th-9901001'."""
    return arxiv_id.replace(".", "-").replace("/", "-")


def paper_dir(arxiv_id: str) -> Path:
    return DATA_DIR / paper_key(arxiv_id)


def version_dir(arxiv_id: str, version: int) -> Path:
    return paper_dir(arxiv_id) / f"v{version}"

# Cách dùng (nhớ tạo thư mục khi cần):
#   version_dir("2310.12345", 1).mkdir(parents=True, exist_ok=True)
