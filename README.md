# arXiv Project: Nhóm <tên nhóm>

Pipeline: M1 Crawling → M2 Parsing → M3 Matching.

## Chạy code
Mở Google Colab, tạo notebook mới và chạy:

```python
!git clone https://github.com/<username>/arxiv-project.git
%cd arxiv-project
!pip install -r src/requirements.txt
```

## Dữ liệu KHÔNG nằm trong repo
Thư mục `data/` chỉ tồn tại trên máy hoặc Colab của mỗi người (đã bị .gitignore).
Dữ liệu dùng chung được chia sẻ qua: <link Google Drive>.
API key Semantic Scholar để riêng (Colab Secrets hoặc file `.env`), tuyệt đối không commit.

## Thỏa thuận giao diện dữ liệu

`<paper_key>` = arXiv ID với `.` và `/` đổi thành `-` (ví dụ `2310-12345`).
Luôn dùng các hàm trong `src/config.py` để tạo đường dẫn.

| # | Từ → Đến | File | Ghi chú |
|---|---|---|---|
| 1 | Cả nhóm | `data/<paper_key>/vN/` chứa `*.tex`, `*.bib`, `meta.json`, `refs.json` | Quy ước thư mục |
| 2 | Nguyen → Quoc | `data/<paper_key>/versions.json` | Danh sách version của bài |
| 3 | Quoc → Nguyen | `data/<paper_key>/vN/tree_raw.json` | Cây thô, text chưa làm sạch |
| 4 | Tri → Khai | `data/<paper_key>/bibtex_merged.json` | Đúng cột của bảng `bibtex` (M2) |
| 5 | Cả nhóm | `data/labels/manual_labels.csv`, `auto_labels.csv` | Cột: `arxiv_id,citation_key,groundtruth_arxiv_id` (để trống nếu không có đáp án) |

Ví dụ `versions.json`:
```json
{"arxiv_id": "2310.12345", "num_versions": 4, "versions": [1, 2, 3, 4]}
```
Muốn đổi định dạng của dòng nào: báo cả nhóm và cập nhật bảng này TRƯỚC khi sửa code.

## Quy tắc Git
- Không commit trực tiếp lên `main`. Mỗi việc một branch: `p1/m1-download`, `p2/m2-bibitem`...
- Commit message có tiền tố giai đoạn: `m1: ...`, `m2: ...`, `m3: ...`
- Mọi branch merge vào `main` bằng Pull Request, có buddy review (P1↔P3, P2↔P4).
- Không commit dữ liệu và không commit API key.

## Phân công
| Người | Mảng |
|---|---|
| Quoc | Source & Structure |
| Tri | References |
| Nguyen | Text & Model |
| Khai | Data, Report & Delivery |


## Chạy pipeline

```bash
python src/run_m1.py
python src/run_m2.py
python src/run_m3.py
```

## Tài liệu nhãn và feature (M3)

### Định dạng nhãn
<P4 điền: file nhãn nằm ở đâu, cột nào, cách gán tay, cách gán tự động>

### Danh sách feature
| Feature | Ý tưởng | Người phụ trách |
|---|---|---|
| <tên feature> | <vì sao feature này giúp ghép đúng> | <P1/P2/P3> |
