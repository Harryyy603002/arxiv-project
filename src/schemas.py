r"""schemas.py

Milestone: Chung (M1-M3)
Người phụ trách: P4
Mục đích: Schema pyarrow tường minh cho 6 bảng: papers, references, elements, hierarchy, bibtex, predictions

Đầu vào: Đặc tả cột trong đề của 3 milestone
Đầu ra:  Các schema dùng khi xuất Parquet

Ghi chú: dùng src/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P4): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
