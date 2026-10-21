r"""m2_gather.py

Milestone: M2
Người phụ trách: P1
Mục đích: Tìm file gốc (chứa documentclass), đi theo lệnh input/include đệ quy, bỏ lệnh bị comment

Đầu vào: data/<paper_key>/vN/*.tex
Đầu ra:  Danh sách file .tex nằm trên đường biên dịch

Ghi chú: dùng src/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P1): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
