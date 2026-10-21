r"""m2_content_dedup.py

Milestone: M2
Người phụ trách: P3
Mục đích: Khử trùng lặp nội dung xuyên version (so khớp sau làm sạch), sinh element_id dạng <paper_key>#<discriminant>

Đầu vào: Cây đã làm sạch của mọi version
Đầu ra:  Dữ liệu trung gian của bảng elements và hierarchy

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P3): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
