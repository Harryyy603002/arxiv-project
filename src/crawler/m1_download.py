r"""m1_download.py

Milestone: M1
Người phụ trách: P1
Mục đích: Tải source từng version, chỉ giữ .tex và .bib; version không có TeX thì ghi bản rỗng

Đầu vào: data/<paper_key>/versions.json
Đầu ra:  data/<paper_key>/vN/*.tex và *.bib

Ghi chú: dùng src/config.py để tạo đường dẫn (paper_dir, version_dir).
Nếu đổi định dạng đầu vào/đầu ra, cập nhật bảng "Thỏa thuận giao diện dữ liệu" trong README.
"""

from config import DATA_DIR, paper_dir, version_dir  # noqa: F401


def main():
    # TODO(P1): viết code ở đây
    raise NotImplementedError


if __name__ == "__main__":
    main()
