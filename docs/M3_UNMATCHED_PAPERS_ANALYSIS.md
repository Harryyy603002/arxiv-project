# Báo cáo Phân tích: Các bài báo không thể gán nhãn tự động trong Milestone 3

- **Thành viên phụ trách**: Tri (P2 - Auto Labeler & String Features)
- **Dự án**: arXiv Paper Matching Pipeline
- **Ngày thực hiện**: 28/11/2026

---

## 1. Tóm tắt vấn đề (Executive Summary)

Trong quá trình thực hiện Milestone 3 (M3) cho tập phân chia của thành viên **Tri** (dải 5,000 bài báo: `2606.20735` – `2606.25734`), bước gán nhãn tự động (`run_m3.py --stage label --member Tri --split-only`) ghi nhận:
- **522 / 525 bài** trong auto pool được gán nhãn thành công (vượt quota tối thiểu 500 bài theo yêu cầu đề bài).
- **3 bài báo** không thể tìm thấy bất kỳ nhãn dương (match) nào:
  1. `2606.22559`
  2. `2606.22111`
  3. `2606.21176`

Tài liệu này trình bày bằng chứng thực tế từ dữ liệu, phân tích nguyên nhân kỹ thuật, và giải thích các cập nhật mã nguồn để xử lý trường hợp biên (edge case) này mà vẫn bảo đảm tính toàn vẹn và tuân thủ tuyệt đối đặc tả đồ án.

---

## 2. Bằng chứng thực nghiệm từ dữ liệu thật (Empirical Data Evidence)

Phân tích trực tiếp các file dữ liệu thật trên đĩa (`bibtex_merged.json` và `refs.json`) của 3 bài báo:

| Bài báo (arXiv ID) | Số BibTeX Entries | Số References Crawl | Ref có Tiêu đề (`title`) | Ref có `doi` | BibTeX có `eprint` | Tỷ lệ khớp khả thi |
|---|---|---|---|---|---|---|
| **`2606.22559`** | 100 entries | 93 refs | **0 / 93 (0%)** | **0 / 93 (0%)** | **0 / 100 (0%)** | **0%** |
| **`2606.22111`** | 61 entries | 46 refs | **0 / 46 (0%)** | **0 / 46 (0%)** | **0 / 61 (0%)** | **0%** |
| **`2606.21176`** | 28 entries | 27 refs | **0 / 27 (0%)** | **0 / 27 (0%)** | **0 / 28 (0%)** | **0%** |

### Mẫu dữ liệu thực tế trích xuất từ `data/2606-22559`:

- **BibTeX entry** (`bibtex_merged.json`):
  ```json
  {
    "citation_key": "Abazajian:2004wh",
    "title": "The Second Data Release of the Sloan Digital Sky Survey",
    "author": "Abazajian, Kevork and others",
    "year": "2004",
    "doi": "10.1086/421365"
  }
  ```
  *(Tác giả bài báo chỉ khai báo DOI, hoàn toàn không có trường `eprint` hoặc link arXiv)*

- **Reference entry** (`refs.json`):
  ```json
  {
    "arxiv_id": "2605.03577",
    "title": "",
    "raw_text": ""
  }
  ```
  *(Crawler từ M1 chỉ thu thập được link/ID arXiv nhưng `title` và `raw_text` đều là chuỗi rỗng)*

---

## 3. Phân tích nguyên nhân kỹ thuật (Root Cause Analysis)

Module `src/matching/m3_auto_labeler.py` áp dụng 4 cấp độ luật xác định có bằng chứng nghiêm ngặt:

1. **Luật 1 (`eprint`)**: So khớp `eprint` trong BibTeX với `arxiv_id` của reference.
   - *Kết quả*: **Thất bại** vì BibTeX của tác giả các bài này không khai báo `eprint`.
2. **Luật 2 (`doi`)**: So khớp `doi` giữa BibTeX và reference.
   - *Kết quả*: **Thất bại** vì các reference được crawl về không có trường `doi`.
3. **Luật 3 & 4 (`title_exact` / `title_fuzzy`)**: So khớp chuỗi tiêu đề chuẩn hóa.
   - *Kết quả*: **Thất bại** vì 100% reference của 3 bài này đều có `title: ""` (rỗng). Độ tương đồng tiêu đề bằng `0.0`.

### Kết luận về mặt dữ liệu:
Hai tập dữ liệu (BibTeX trích xuất từ LaTeX và References crawl từ web) **hoàn toàn không có giao thoa về trường thông tin định danh**. Việc auto-labeler từ chối gán nhãn là **hoàn toàn chính xác theo nguyên tắc khoa học**: không gán nhãn bừa khi thiếu bằng chứng xác thực.

---

## 4. Các cập nhật mã nguồn (Codebase Updates)

Trước khi sửa, hệ thống gặp mâu thuẫn kiến trúc:
- Đề bài yêu cầu: Gán tự động **≥ 10% số bài ngoài nhóm gán tay** (tức ≥ 500 bài trên 5,000 bài). Không yêu cầu 100% bài phải match được.
- Code `m3_auto_labeler.py` cũ: Nếu một bài không có nhãn nào thì xóa / không tạo file JSON.
- Code `m3_bundle.py` cũ: Quét mọi bài trong split, nếu phát hiện file nhãn thiếu hoặc có 0 nhãn dương thì **báo lỗi chặn đóng gói (`strict error`)**, bất kể quota 500 bài đã đạt hay chưa.

### Các thay đổi đã thực hiện:

1. **`src/matching/m3_auto_labeler.py`**:
   - Khi gặp bài không có candidate hoặc không có nhãn nào khớp (như 3 bài trên), hệ thống **vẫn tạo file JSON nhãn hợp lệ** với `labels: {}` và `evidence: {}`.
   - Giữ nguyên các file nhãn rỗng này qua các lần chạy kế tiếp thay vì tự động xóa, đảm bảo mọi bài trong split đều có tệp metadata theo dõi.

2. **`src/matching/m3_bundle.py`**:
   - Trong `check_member_compliance`: Chỉ ghi nhận lỗi nếu tổng số bài auto có nhãn dương **nhỏ hơn quota yêu cầu** (`auto_ok < need_auto`).
   - Trong `export_bundle`: Phân biệt rõ giữa bài gán tay (bắt buộc phải có ≥ 20 nhãn dương) và bài tự động (nếu 0 nhãn dương chỉ ghi nhận cảnh báo `warning`, không chặn xuất file nộp).

3. **`src/tests/test_m3_p2.py`**:
   - Thêm test case `test_label_paper_entries_no_candidates` để kiểm thử tự động trường hợp bài không có candidate. Đảm bảo toàn bộ 18/18 test case của P2 vượt qua (`PASSED`).

---

## 5. Bảng chứng nhận tuân thủ (Compliance Certification)

Kết quả kiểm tra từ công cụ kiểm chuẩn chính thức của dự án:
```powershell
python src/matching/m3_bundle.py check --data-dir data --member Tri
```

```text
Kiểm tra tuân thủ đề cho Tri:
  [ĐẠT] Cấu trúc split (không trùng bài, valid/test thuộc manual/auto)      | cần hợp lệ | hiện hợp lệ
  [ĐẠT] Mọi bài thuộc đúng range của thành viên                             | cần 0 bài ngoài range | hiện 0 bài ngoài range
  [ĐẠT] Số bài gán tay (khác nhau)                                          | cần >= 5 | hiện 5
  [ĐẠT] Bài gán tay đạt (source=manual, >= 20 nhãn dương, hợp lệ)           | cần 5/5 | hiện 5/5
  [ĐẠT] Tổng nhãn dương gán tay                                             | cần >= 100 | hiện 167
  [ĐẠT] Bài auto có nhãn dương: 10% x (5000 - 5), range đếm theo paper.json | cần >= 500 | hiện 522
  [ĐẠT] test: bài gán tay                                                   | cần 1 | hiện 1
  [ĐẠT] test: bài tự động                                                   | cần 1 | hiện 1
  [ĐẠT] valid: bài gán tay                                                  | cần 1 | hiện 1
  [ĐẠT] valid: bài tự động                                                  | cần 1 | hiện 1
  [ĐẠT] train: bài gán tay (tất cả bài tay còn lại)                         | cần 3 | hiện 3
  [ĐẠT] train: bài tự động (tất cả bài auto còn lại)                        | cần >= 1 | hiện 523
=> ĐẠT TOÀN BỘ
```

- **File đóng gói đã tạo**: `bundle_Tri.zip` (202.81 MB)
- **Tình trạng**: Đạt toàn bộ tiêu chí định lượng và định tính của Milestone 3.
