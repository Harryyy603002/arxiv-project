# arXiv Project: Nhóm <tên nhóm>

Pipeline: M1 Crawling → M2 Parsing → M3 Matching.

- Báo cáo (Jupyter Book): <link GitHub Pages>
- Dataset (Hugging Face): <link dataset>

## 1. Chuẩn bị môi trường

Yêu cầu: Python 3.10 trở lên.

### Google Colab

Tạo notebook mới. Chạy các cell theo thứ tự:

1. Clone repo và cài dependencies:

  ```python
  !git clone https://github.com/Harryyy603002/arxiv-project.git
  %cd arxiv-project
  !pip install -r src/requirements.txt
  ```

1. Thêm API key vào Colab Secrets: mở bảng **Secrets** (biểu tượng chìa khóa), tạo secret tên `S2_API_KEY`, rồi bật quyền truy cập cho notebook. Trước khi chạy M1, chạy cell:

  ```python
  import os
  from google.colab import userdata

  os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")
  ```

  Key này dùng cho Semantic Scholar references; không cần cho bước metadata hoặc tải source.

1. (Tùy chọn) Gắn Google Drive nếu muốn lưu dữ liệu ở đó:

  ```python
  import os
  from google.colab import drive

  drive.mount("/content/drive")
  os.environ["ARXIV_DATA_DIR"] = "/content/drive/MyDrive/arxiv-data"
  ```

  Đặt `ARXIV_DATA_DIR` trước khi chạy pipeline.

### Máy cá nhân

Tạo môi trường ảo, sau đó cài dependencies:

```bash
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Linux / macOS:        source .venv/bin/activate
pip install -r src/requirements.txt
```

Đặt `S2_API_KEY` (khuyến nghị cho Semantic Scholar) và `CONTACT_EMAIL` (User-Agent tải source) qua biến môi trường hoặc file `.env` đã bị `.gitignore`. Tuyệt đối không commit API key. Tên biến được đọc trong `src/utils/config.py`.

## 3. Dữ liệu KHÔNG nằm trong repo

Thư mục `data/` chỉ tồn tại trên máy hoặc Colab của mỗi người (đã bị `.gitignore`). Đề cấm commit dữ liệu, kể cả CSV nhãn.

Dữ liệu dùng chung được chia sẻ qua: <link Google Drive>. Mỗi người upload thư mục `data/` của khoảng ID mình phụ trách, giữ nguyên tên `<paper_key>`.

## 4. Thỏa thuận giao diện dữ liệu (không commit)

`<paper_key>` = arXiv ID với `.` và `/` đổi thành `-` (ví dụ `2310.12345` → `2310-12345`). Chỉ dùng `<paper_key>` để đặt tên thư mục. Trong mọi file dữ liệu (`arxiv_id`, `groundtruth_arxiv_id`...) luôn ghi arXiv ID gốc, kể cả ID cũ như `hep-th/9901001`.

Luôn dùng các hàm trong `src/utils/config.py` để tạo đường dẫn.

### Cấu trúc thư mục

```text
data/
├── papers.csv, papers.parquet          # bảng papers gộp
├── references.csv, references.parquet  # bảng references gộp (chỉ giữ ref có arXiv ID)
├── failed_ids.txt                      # ID không tồn tại / lỗi (không phải bug)
├── benchmark_m1.json                   # kết quả đo hiệu năng M1 (không commit)
├── labels/
│   ├── manual_labels.csv
│   └── auto_labels.csv
└── <paper_key>/
    ├── versions.json                   # danh sách version
    ├── meta.json                       # metadata bài báo
    ├── refs.json                       # reference từ Semantic Scholar (cấp bài)
    ├── bibtex_merged.json              # BibTeX đã gộp xuyên version
    ├── elements.json                   # các phần tử đã làm sạch, loại trùng
    ├── hierarchy.json                  # cạnh nút–cha của từng version
    └── vN/
        ├── *.tex, *.bib                # không có .tex = bản rỗng (PDF-only)
        ├── source_status.json          # trạng thái tải + dung lượng trước/sau lọc hình
        └── tree_raw.json               # cây thô, text chưa làm sạch
```

`refs.json` đặt ở cấp bài thay vì trong `vN/` vì Semantic Scholar trả reference theo bài, không phân biệt version, nên để trong từng `vN/` chỉ là chép lặp.

Version chỉ có PDF vẫn phải có thư mục `vN/` (rỗng). M2 coi đó là bản rỗng và tính vào thống kê, không được bỏ qua.

### Các file trao đổi giữa các thành viên

| # | Từ → Đến | File | Ghi chú |
| --- | --- | --- | --- |
| 1 | Nguyen → Quoc | `<paper_key>/versions.json` | Quoc lặp qua `versions[i]["version"]` để tạo `vN/` và tải source |
| 2 | Nguyen → cả nhóm | `<paper_key>/meta.json`, `papers.csv`, `papers.parquet` | Metadata từng bài và bảng `papers` gộp |
| 3 | Tri → cả nhóm | `<paper_key>/refs.json` | Mọi reference crawl được, kể cả không có arXiv ID. Chỉ cái có `externalIds.ArXiv` vào bảng `references` |
| 4 | Quoc → Nguyen | `<paper_key>/vN/tree_raw.json` | Cây thô, text chưa làm sạch (cấu trúc bên dưới) |
| 5 | Tri → Khai | `<paper_key>/bibtex_merged.json` | Đúng cột bảng `bibtex`: `arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url` |
| 6 | Nguyen → Khai | `<paper_key>/elements.json`, `hierarchy.json` | Đúng cột bảng `elements` (`element_id, arxiv_id, kind, content`) và `hierarchy` (`arxiv_id, version, element_id, parent_id`) |
| 7 | Cả nhóm | `labels/manual_labels.csv`, `auto_labels.csv` | Cột: `arxiv_id,citation_key,groundtruth_arxiv_id` (để trống nếu không có đáp án) |
| 8 | P1 → cả nhóm | `data/<paper_key>/vN/source_status.json` | Trạng thái tải của từng version: `ok` hoặc `empty` kèm lý do |

Hai lưu ý chéo giữa các mảng:

- Nội dung câu trong `elements` phải **giữ nguyên lệnh `\cite{...}`**, vì Quoc dùng chúng để tính feature ngữ cảnh ở M3.
- Việc đổi tên `\cite` theo khóa đã gộp phải xong **trước** khi loại trùng nội dung, vì khác khóa thì hai câu không còn giống nhau.

### Ví dụ `versions.json`

```json
{
  "arxiv_id": "2310.12345",
  "latest_version": 4,
  "versions": [
    {"version": "v1", "id_with_version": "2310.12345v1", "date": "2023-10-18T10:00:00+00:00"},
    {"version": "v2", "id_with_version": "2310.12345v2", "date": null},
    {"version": "v3", "id_with_version": "2310.12345v3", "date": null},
    {"version": "v4", "id_with_version": "2310.12345v4", "date": "2024-01-05T08:30:00+00:00"}
  ]
}
```

`date`: arXiv API chỉ cung cấp ngày của v1 và của version mới nhất, các version ở giữa để `null`.

### Các trường của `meta.json`, `refs.json` và quy ước bảng M1

- **`meta.json` (lưu cục bộ):** `arxiv_id, title, authors, abstract, submission_date, revised_dates, latest_update, latest_version, primary_category, categories, journal_ref, doi, comment`
- **Bảng `papers` (xuất Parquet/CSV theo đúng chuẩn đề):** `arxiv_id, title, authors, submission_date, revised_dates, venue` (lấy từ `journal_ref`).
- **`source_status.json` (trong từng `vN/`):** lưu `status` (`ok` / `empty`), `reason`, `raw_bytes` (dung lượng `.tar.gz` gốc trước khi bỏ hình) và `extracted_bytes` (dung lượng `.tex` + `.bib` sau khi lọc) để phục vụ thống kê báo cáo M1.
- **Bảng `references` (lọc từ `refs.json` các mục có `externalIds.ArXiv`):** `arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id`.

### Cấu trúc `tree_raw.json` (đề xuất, chốt với cả nhóm)

```json
{
  "arxiv_id": "2310.12345",
  "version": "v1",
  "root": {
    "type": "document",
    "children": [
      {
        "type": "section", "level": 1, "title": "Introduction",
        "children": [
          {"type": "paragraph", "text": "We follow \\cite{vaswani2017}. Next ..."},
          {"type": "equation", "text": "E = mc^2"},
          {"type": "figure", "text": "\\begin{figure} ... \\end{figure}"}
        ]
      }
    ]
  }
}
```

`type` nhận: `document`, `section` (kèm `level`), `paragraph`, `itemize`, `item`, `equation`, `figure`. Quoc dựng cấu trúc đến mức paragraph. Nguyen tách paragraph thành câu và làm sạch.

Muốn đổi định dạng của dòng nào: báo cả nhóm và cập nhật mục này TRƯỚC khi sửa code.

## 5. Quy tắc Git

- Không commit trực tiếp lên `main`. Mỗi việc một branch: `p1/m1-download`, `p2/m2-bibitem`...
- Commit message có tiền tố giai đoạn: `m1: ...`, `m2: ...`, `m3: ...`
- Mọi branch merge vào `main` bằng Pull Request, có buddy review (P1↔P3, P2↔P4).
- Commit nhỏ và thường xuyên (lịch sử commit dùng để đánh giá đóng góp cá nhân).
- Không commit dữ liệu và không commit API key.

## 6. Phân công

| Mã | Người | Mảng |
| --- | --- | --- |
| P1 | Quoc | Source & Structure |
| P2 | Tri | References |
| P3 | Nguyen | Text & Model |
| P4 | Khai | Data, Report & Delivery |

## 7. Chạy pipeline

Chạy lệnh từ thư mục repo. Thay khoảng mẫu bằng khoảng ID được giao bên dưới.

1. Chạy thử 5 bài trước:

  ```bash
  python src/run_m1.py --range 2606.20734-2606.25733 --limit 5
  ```

1. Nếu các file trong `data/` được tạo đúng, chạy toàn bộ khoảng:

  ```bash
  python src/run_m1.py --range 2606.20734-2606.25733
  ```

`run_m1.py` chạy metadata, tải source và references. Reference requests dùng endpoint batch của Semantic Scholar, mặc định 25 bài/request. Có thể đổi bằng `--references-batch-size` (tối đa 500; response tối đa 10 MB). `--batch-size` điều chỉnh riêng batch metadata arXiv. Các file đã hoàn tất được cache trong `data/`, nên chạy lại sẽ tiếp tục phần còn thiếu. Đặt `S2_API_KEY` trước khi chạy để tránh giới hạn rate của request không xác thực.

1. Sau khi nhóm hoàn tất và gộp dữ liệu M1, chạy các milestone tiếp theo:

  ```bash
  python src/run_m2.py
  python src/run_m3.py
  ```

### Khoảng ID được phân công

Mỗi thành viên chạy khoảng 5.000 ID riêng; sau đó dữ liệu được gộp qua Drive chung.

| Người | Khoảng ID | Lệnh M1 đầy đủ |
| --- | --- | --- |
| Quoc | 2512.23231 → 2601.03155 | `python src/run_m1.py --range 2512.23231-2601.03155 --month-last 2512=<ID cuối tháng 12/2025>` |
| Nguyen | 2602.04870 → 2602.09869 | `python src/run_m1.py --range 2602.04870-2602.09869` |
| Khai | 2603.00580 → 2603.05579 | `python src/run_m1.py --range 2603.00580-2603.05579` |
| Tri | 2606.20734 → 2606.25733 | `python src/run_m1.py --range 2606.20734-2606.25733` |

Một số ID có thể không tồn tại (bị rút/gộp); crawler ghi nhận chúng là ID thất bại và tiếp tục.

Một số ID trong khoảng có thể không tồn tại (bị rút/gộp). Chúng được ghi vào `data/failed_ids.txt` và không phải lỗi.

Kiểm thử: `pytest src/tests` (đã có `pytest.ini` đặt `pythonpath = src`). `test_m1_references.py` kiểm tra batch request, cache khi chạy lại và chia nhóm khi API từ chối request quá lớn.

### Đo hiệu năng

Đo trên Colab (CPU), với thư mục `data/` trống, chạy bằng `python src/run_m1.py ...`.
Kết quả nằm ở `data/benchmark_m1.json`. Chỉ số: thời gian từng giai đoạn, RAM tối đa/trung bình,
đĩa tối đa, dung lượng đầu ra. Có thể đo end-to-end bên ngoài bằng
`python src/utils/benchmark.py --milestone m1 --watch data -- python src/run_m1.py ...`.

## 8. Tài liệu nhãn và feature (M3)

### Định dạng nhãn

<P4 điền: file nhãn nằm ở đâu, cột nào, cách gán tay, cách gán tự động>

Khung gợi ý:

- File: `data/labels/manual_labels.csv`, `data/labels/auto_labels.csv`
- Cột: `arxiv_id, citation_key, groundtruth_arxiv_id` (rỗng nếu không có reference khớp)
- Mỗi mục BibTeX có tối đa một reference đúng
- Gán tay: <số bài>, <số mục>, <cách kiểm tra chéo> (tối thiểu 5 bài, mỗi bài ≥ 20 mục, tổng ≥ 100)
- Gán tự động: <phương pháp, ngưỡng>, áp dụng cho ≥ 10% số bài ngoài tập gán tay

### Danh sách feature

| Feature | Ý tưởng | Người phụ trách |
| --- | --- | --- |
| <tên feature> | <vì sao feature này giúp ghép đúng> | <P1/P2/P3> |
