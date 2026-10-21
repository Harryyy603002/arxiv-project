r"""m1_metadata.py

Milestone: M1
Người phụ trách: P3
Mục đích: Gọi arXiv API lấy metadata và danh sách version; tạo bảng papers

Đầu vào: Danh sách arXiv ID được giao
Đầu ra:  data/<paper_key>/versions.json và meta.json

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P3): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
