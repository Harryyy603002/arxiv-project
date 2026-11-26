# M3 – các bản sửa (đã chạy thử với `utils/` thật của nhóm)

## Cách áp dụng
| File gửi kèm | Chép vào |
|---|---|
| `m3_candidates.py`, `m3_string_features.py`, `m3_auto_labeler.py`, `m3_evaluate.py`, `m3_predictions.py` | `src/matching/` |
| `run_m3.py` | `src/` |
| `tests/test_m3_labeler_features.py` | `tests/` |

Không sửa `utils/` (config, schemas, benchmark, logger đều tương thích, không cần đổi gì).

Sau khi chép: **chạy lại** `python src/run_m3.py --stage label,features,train,predict,evaluate --rebuild-features`
(feature str_* tăng từ 9 lên 11 cột nên phải tính lại cho MỌI bài và train lại model; nhãn auto cũ sẽ tự bị ghi đè).

## Đã sửa
1. **Nhãn auto thiếu luật eprint/DOI** – thêm: eprint > doi > title_exact > title_fuzzy.
   Có veto khi BibTeX khai eprint khác reference; bỏ qua entry mơ hồ (nhiều reference cùng title);
   một reference chỉ gán cho entry có bằng chứng mạnh nhất.
2. **Luật năm (luật 5) vốn không chạy** – nay dùng năm thật của BibTeX và reference (cả hai phải có).
3. **Logic nhãn nằm 2 chỗ lệch nhau** – nay chỉ còn `m3_auto_labeler.label_corpus()`; `run_m3` và CLI cùng gọi.
   CLI cũng hết đếm nhầm các thư mục `m3/`, `labels/`, `export/`, `benchmark/` và bỏ cờ chết `--manual-labels`.
4. **Feature chuỗi**
   - `year_*` không còn mượn năm BibTeX khi reference thiếu ngày.
   - Giá trị thiếu là NaN (null trong Parquet) thay vì 0.0, đúng hợp đồng M3_PLAN §4.5.
   - `first_author_match` so **họ** tác giả đầu (trước đây so tên riêng).
   - Thêm `str_eprint_match`, `str_doi_match` (1 = trùng, 0 = có ID nhưng khác, NaN = BibTeX không có ID).
5. **Chạy lại không cập nhật** – nhãn auto được ghi đè; `.str.parquet` tự tính lại khi cũ hơn
   `candidates.json`/`bibtex_merged.json`/`refs.json`/`m3_string_features.py` (hoặc dùng `--rebuild-features`).
6. **`--rebuild-split --member X`** giữ nguyên các thành viên khác thay vì xóa.
7. **Hiệu năng evaluate/predictions** – xếp hạng một lần cho cả bảng: 60.000 entry / 360.000 dòng scores
   từ ~11 phút mỗi lượt xuống 1,3 giây, kết quả giống hệt (0 lệch trên mẫu kiểm tra).
8. **Số báo cáo** – `evaluate` in MRR@5 của test / valid / train riêng; số "gộp" có ghi chú không dùng để báo cáo.
9. **Cảnh báo sau `label`**: liệt kê bài trong split chưa có nhãn dương nào (vì `pick` chạy trước `label`).

## Kết quả trên corpus giả (80 bài, 1.266 entry)
| | trước | sau |
|---|---|---|
| precision nhãn auto | 97,9% (11 sai) | 100% |
| recall nhãn auto | 72,6% | 90,6% |
| entry không title (bibitem) được gán | 0 | có (qua eprint) |
Phần recall còn thiếu là entry title bị cắt ngắn và không có eprint: labeler chủ động bỏ qua.
`pytest tests/test_m3_labeler_features.py`: 21 test đều qua.

## Chưa sửa / cần bạn lưu ý
- **Đánh giá bài auto bị "vòng tròn"**: nhãn auto dựa trên eprint/title, mà model cũng có feature eprint/title,
  nên MRR trên bài auto sẽ cao giả. Dùng MRR của bài **gán tay** (`test_by_source.manual`) làm số chính.
- Các cột trùng nhau (`title_similarity` và `str_title_sim`, v.v.) vẫn cùng vào model, làm ablation theo nhóm feature chưa sạch.
- `pick` vẫn chạy trước `label`; nếu `label` cảnh báo thiếu nhãn dương, chạy lại `pick --rebuild-split` (nhớ cố định `--seed`).
- Mới kiểm tra trên 1 bài thật (2602.08695). Tên khóa của `refs.json`, `bibtex_merged.json`, `candidates.json`, `m2_stats.json`
  đã được xác nhận khớp code; nên thử thêm vài bài khác (nhất là bài có nhiều `\bibitem`) trước khi chạy cả corpus.
- `pick` (`_identity_coverage`) còn một bản regex arXiv ID cũ; nó chỉ dùng để xếp hạng bài auto và chỉ tính khi ID giả
  trùng đúng ID một reference của bài, nên tác động rất nhỏ. Chưa sửa.

## Cập nhật sau khi chạy bài thật 2602.08695
Chạy trên bài thật phát hiện regex arXiv ID cũ (không có ranh giới số) bắt nhầm số trong DOI/URL thành arXiv ID:
`https://doi.org/10.1145/792538.792543` -> `2538.79254`, `ieeexplore.../document/5128907` -> `document/5128907`.
Hậu quả: veto "eprint mâu thuẫn" chặn nhầm cặp title trùng 100% (mất 1 nhãn), và feature `str_eprint_match` ra 0.0 thay vì NaN.
Đã sửa trong các hàm helper mới của `m3_candidates.py` (`generate_candidates` của M2 giữ nguyên):
- bỏ DOI trước khi tìm ID (DOI arXiv `10.48550/arXiv.X` vẫn được nhận);
- ID mới không được dính chữ số/dấu chấm phía trước và chữ số phía sau;
- ID cũ chỉ nhận archive thật của arXiv (cs, math, hep-th, ...);
- so sánh bỏ subject class (`math.GT/0309136` = `math/0309136`).
Kết quả bài thật: gán được 35/35 reference có arXiv ID (24 title_exact, 8 eprint, 3 doi), không nhãn nào sai khi soát tay.

## Cập nhật sau khi chạy bài 2602.08163 (toàn `\bibitem`, 223 entry không có eprint/doi, 72 reference có arXiv ID)
Phát hiện 3 vấn đề, đã sửa trong `m3_string_features.py` và `m3_auto_labeler.py`:
1. **`first_author_match` sai với "C. X. Wang et al"**: token cuối là "al" nên feature gần như luôn 0. Nay bỏ đuôi
   "et al"/"and others" trước khi lấy họ. Sau sửa: cặp đúng 1.00, cặp sai 0.09.
2. **Luật năm (luật 5) quá lỏng trên dữ liệu thật**: hai entry không có reference đúng suýt bị gán nhầm
   (title_sim 0.71 do cùng lĩnh vực AFDM, cùng năm). Nay luật 5 cần thêm họ tác giả đầu trùng;
   luật 4 chấp nhận "họ tác giả đầu trùng" thay cho author_sim vì bibitem IEEE chỉ có "A. Smith et al".
3. **Title khác nhau nhưng thực chất giống hệt** bị bỏ sót: ghi chú cuối "[Special Issue on ...]" do Semantic Scholar thêm,
   và dấu gạch "–" so với "-". Nay `title_exact` được tính sau khi thống nhất dấu gạch/nháy và bỏ ghi chú cuối.
Kết quả: bài 2602.08163 gán được 72/72 reference có arXiv ID (54 title_exact, 18 eprint lấy từ url/raw_text);
bài 2602.08695 giữ nguyên 35/35. Corpus giả không đổi (precision 100%, recall 90,6%).
