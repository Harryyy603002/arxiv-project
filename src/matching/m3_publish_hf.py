r"""m3_publish_hf.py

Milestone: M3
Người phụ trách: P4
Mục đích: Upload 6 bảng Parquet và dataset card lên Hugging Face

Đầu vào: Các file Parquet
Đầu ra:  Dataset public trên Hugging Face

Ghi chú: dùng src/utils/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from utils.config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P4): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
