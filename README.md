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

Dữ liệu dùng chung được chia sẻ qua: <link Google Drive>. Mỗi người upload thư mục `corpus/` của khoảng ID mình phụ trách, giữ nguyên tên `<paper_key>`.

## 4. Thỏa thuận giao diện dữ liệu (không commit)

`<paper_key>` = arXiv ID với `.` và `/` đổi thành `-` (ví dụ `2310.12345` → `2310-12345`). Chỉ dùng `<paper_key>` để đặt tên thư mục. Trong mọi file dữ liệu (`arxiv_id`, `groundtruth_arxiv_id`...) luôn ghi arXiv ID gốc, kể cả ID cũ như `hep-th/9901001`.

Luôn dùng các hàm trong `src/utils/config.py` để tạo đường dẫn.

### Cấu trúc thư mục (Output M1 nằm trong `corpus`)

```text
data/
├── papers.csv, papers.parquet          # bảng papers gộp
├── references.csv, references.parquet  # bảng references gộp (chỉ giữ ref có arXiv ID)
├── failed_ids.txt                      # ID không tồn tại / lỗi (không phải bug)
├── benchmark_m1.json                   # kết quả đo hiệu năng M1 (không commit)
├── labels/
│   ├── manual_labels.csv
│   └── auto_labels.csv
└── corpus/
    └── <paper_key>/
        ├── paper.json                  # metadata của toàn bài báo
        ├── bibtex_merged.json          # BibTeX đã gộp xuyên version (Phục vụ M2/M3)
        ├── elements.json               # các phần tử đã làm sạch, loại trùng (Phục vụ M2/M3)
        ├── hierarchy.json              # cạnh nút–cha của từng version (Phục vụ M2/M3)
        └── vN/
            ├── *.tex, *.bib            # không có .tex = bản rỗng (PDF-only)
            ├── metadata.json           # metadata của version, trạng thái, dung lượng M1
            ├── references.json         # TOÀN BỘ reference crawl từ Semantic Scholar
            └── tree_raw.json           # cây thô, text chưa làm sạch (Phục vụ M2/M3)
```

Version chỉ có PDF vẫn phải có thư mục `vN/` (rỗng). M2 coi đó là bản rỗng và tính vào thống kê, không được bỏ qua.

### Các file trao đổi giữa các thành viên

| # | Từ → Đến | File | Ghi chú |
| --- | --- | --- | --- |
| 1 | M1 Crawler → Cả nhóm | `<paper_key>/paper.json` | Chứa metadata cấp bài báo (`title`, `authors`, `n_versions`, `submission_date`...) |
| 2 | M1 Crawler → Cả nhóm | `<paper_key>/vN/metadata.json` | Chứa metadata chi tiết cho từng version và thông tin đo lường dung lượng (`bytes_before`, `bytes_after`) |
| 3 | M1 Crawler → Cả nhóm | `<paper_key>/vN/references.json` | Mọi reference crawl được từ Semantic Scholar theo dạng list. |
| 4 | Quoc → Nguyen | `<paper_key>/vN/tree_raw.json` | Cây thô, text chưa làm sạch (cấu trúc bên dưới) |
| 5 | Tri → Khai | `<paper_key>/bibtex_merged.json` | Đúng cột bảng `bibtex`: `arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url` |
| 6 | Nguyen → Khai | `<paper_key>/elements.json`, `hierarchy.json` | Đúng cột bảng `elements` (`element_id, arxiv_id, kind, content`) và `hierarchy` (`arxiv_id, version, element_id, parent_id`) |
| 7 | Cả nhóm | `labels/manual_labels.csv`, `auto_labels.csv` | Cột: `arxiv_id,citation_key,groundtruth_arxiv_id` (để trống nếu không có đáp án) |

Hai lưu ý chéo giữa các mảng:

- Nội dung câu trong `elements` phải **giữ nguyên lệnh `\cite{...}`**, vì Quoc dùng chúng để tính feature ngữ cảnh ở M3.
- Việc đổi tên `\cite` theo khóa đã gộp phải xong **trước** khi loại trùng nội dung, vì khác khóa thì hai câu không còn giống nhau.

### Ví dụ `paper.json` (Đầu ra M1 cấp bài báo)

```json
{
  "arxiv_id": "2310.12345",
  "title": "Example Paper Title",
  "authors": ["Author A", "Author B"],
  "submission_date": "2023-10-18T10:00:00+00:00",
  "revised_dates": ["2024-01-05T08:30:00+00:00"],
  "venue": "Conference Journal",
  "n_versions": 2
}
```

### Các trường của Dữ liệu M1 và quy ước bảng

- **`paper.json` (cấp bài):** `arxiv_id, title, authors, submission_date, revised_dates, venue, n_versions`.
- **`metadata.json` (trong từng `vN/`):** `arxiv_id, version, date, title, authors, venue, source_kind, archive_bytes, bytes_before, bytes_after`. Dùng để phục vụ thống kê báo cáo M1.
- **`references.json` (trong từng `vN/`):** `ref_s2_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_venue, external_ids`.
- **Bảng `papers` (xuất Parquet/CSV theo chuẩn đề):** Tập hợp từ các file `paper.json`.
- **Bảng `references` (xuất Parquet/CSV theo chuẩn đề):** Tập hợp từ các file `references.json`, nhưng CHỈ giữ lại các ref có `ref_arxiv_id`.

### Cấu trúc `tree_raw.json` (đề xuất, chốt với cả nhóm phục vụ M2)

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

Chạy lệnh từ thư mục repo. Cần chuẩn bị trước một file văn bản (VD: `ids.txt`) chứa danh sách các arXiv ID (mỗi dòng 1 ID).

1. Chạy thử M1 (bằng cách để vài ID vào file `test_ids.txt`):

  ```bash
  python src/run_m1.py --ids-file test_ids.txt --corpus-dir data/corpus
  ```

1. Chạy toàn bộ kho dữ liệu thực tế (bằng file danh sách thực sự của bạn):

  ```bash
  python src/run_m1.py --ids-file my_full_ids.txt --corpus-dir data/corpus
  ```

`run_m1.py` xử lý đồng bộ việc lấy metadata, tải source và lấy references theo từng cụm (batch). Việc gọi API lấy reference Semantic Scholar sử dụng endpoint batch (mặc định 25 bài/nhịp) để tối ưu hóa và chống Rate Limit. Bạn có thể điều chỉnh số lượng này thông qua cờ `--batch-size`. Đặt `S2_API_KEY` trước khi chạy để tránh giới hạn lượng request của các IP chưa xác thực.

1. Sau khi nhóm hoàn tất và gộp dữ liệu M1, chạy các milestone tiếp theo:

  ```bash
  python src/run_m2.py
  python src/run_m3.py
  ```

### Khoảng ID được phân công (Dữ liệu chạy thực tế)

Mỗi thành viên chạy khoảng 5.000 ID riêng; sau đó dữ liệu (nằm trong thư mục `corpus/`) được gộp qua Drive chung. Bạn cần tạo một script nhỏ hoặc dùng tools để sinh ra file danh sách text `my_ids.txt` cho dải ID được giao trước khi đưa vào lệnh chạy.

| Người | Khoảng ID | Lệnh M1 đầy đủ (Giả định file danh sách là `my_ids.txt`) |
| --- | --- | --- |
| Quoc | 2512.23231 → 2601.03155 | `python src/run_m1.py --ids-file quoc_ids.txt --corpus-dir data/corpus` |
| Nguyen | 2602.04870 → 2602.09869 | `python src/run_m1.py --ids-file nguyen_ids.txt --corpus-dir data/corpus` |
| Khai | 2603.00580 → 2603.05579 | `python src/run_m1.py --ids-file khai_ids.txt --corpus-dir data/corpus` |
| Tri | 2606.20734 → 2606.25733 | `python src/run_m1.py --ids-file tri_ids.txt --corpus-dir data/corpus` |

Một số ID có thể không tồn tại (bị rút/gộp); crawler ghi nhận chúng là ID thất bại và tiếp tục (không phải lỗi).

Kiểm thử: `pytest src/tests` (đã có `pytest.ini` đặt `pythonpath = src`). `test_m1_references.py` kiểm tra batch request, cache khi chạy lại và chia nhóm khi API từ chối request quá lớn.

### Đo hiệu năng

Kết quả đo hiệu năng nằm ở `data/benchmark_m1.json` sau khi chạy kịch bản `run_m1.py`. Chỉ số bao gồm: thời gian từng giai đoạn, RAM tối đa/trung bình, đỉnh đĩa tối đa, dung lượng đầu ra thực tế. 
Có thể đo thêm end-to-end bên ngoài bằng `benchmark.py` (tùy chọn).

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