r"""m1_references.py

Milestone: M1
Người phụ trách: P2
Mục đích: Crawl reference từ Semantic Scholar, giữ reference có externalIds.ArXiv; tạo bảng references

Đầu vào: arXiv ID của các bài
Đầu ra:  data/<paper_key>/refs.json

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P2): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
