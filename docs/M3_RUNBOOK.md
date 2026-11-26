# M3 – quy trình chạy của nhóm (mỗi người một máy, Khải ghép và train)

Lệnh dưới đây viết cho PowerShell, chạy ở thư mục gốc dự án với `.venv` đã bật.
Thay `<Tên>` bằng một trong `Quoc`, `Nguyen`, `Khai`, `Tri`.

## 0. Trước khi bắt đầu (cả nhóm)
- Mọi người dùng **cùng một commit** (đã gồm các file sửa và `src/matching/m3_bundle.py`).
  Nếu hai người dùng hai phiên bản code, bộ cột feature sẽ khác nhau và `merge` sẽ từ chối.
- Khải cài thêm gói train nếu chưa có: `pip install lightgbm scikit-learn pyarrow pandas`.
- Chạy `pytest tests/` — phải qua hết trước khi làm tiếp.

## 1. Mỗi người, trên máy mình
```powershell
# 1. Chọn bài (tạo m3/split.json cho range của bạn)
python src/run_m3.py --stage pick --member <Tên>

# 2. Gán tay 5 bài trong split (mỗi bài >= 20 entry có reference đúng). NHỚ truyền --labeler <Tên>
python src/matching/m3_manual_labeler.py --corpus-dir data --paper-id <arxiv_id> --labeler <Tên>
python src/matching/m3_manual_labeler.py --corpus-dir data --paper-id <arxiv_id> --validate-only --min-positive 20

# 3. Gán nhãn tự động + tính feature, CHỈ cho các bài trong split (khoảng 500 bài auto + 5 bài tay)
python src/run_m3.py --stage label --member <Tên> --split-only
python src/run_m3.py --stage features --member <Tên> --split-only --rebuild-features
```
Đề chỉ yêu cầu gán tự động **ít nhất 10% số bài ngoài nhóm gán tay** (khoảng 500 bài mỗi người), nên không cần chạy
trên toàn bộ data. Không có `--split-only` thì lệnh chạy trên MỌI bài trong `data/` (khoảng 5.000 bài), tốn thời gian
mà phần lớn kết quả không dùng tới.

**Đọc kỹ kết quả bước `label`.** Nó in dòng kiểu
`Nguyen: bài auto có nhãn dương = 5xx/5xx trong pool; cần >= 5xx theo đề -> ĐẠT`.
Nếu là `CHƯA ĐẠT` (hoặc có cảnh báo "bài trong split chưa có nhãn dương"), nghĩa là một số bài trong pool auto
không gán được nhãn nào. Sửa như sau (5 bài gán tay **không bị đổi**, đã kiểm tra):
```powershell
python src/run_m3.py --stage label --member <Tên>                                   # không --split-only: gán thử trên toàn bộ data
python src/run_m3.py --stage pick --member <Tên> --rebuild-split --require-auto-labels   # chọn lại pool, chỉ lấy bài đã có nhãn dương
python src/run_m3.py --stage label --member <Tên> --split-only                      # xác nhận lại: phải ra ĐẠT
python src/run_m3.py --stage features --member <Tên> --split-only --rebuild-features
```

Soát nhanh trước khi gửi: mở vài file `data/m3/labels/*.json` của bài auto, xem khoảng 20 cặp
(mỗi loại bằng chứng eprint / doi / title_exact / title_fuzzy vài cặp); nếu thấy nhãn sai thì báo lại.

## 2. Đóng gói và gửi cho Khải
```powershell
python src/matching/m3_bundle.py export --member <Tên> --out bundle_<Tên>.zip --strict
```
`--strict` sẽ **không tạo gói** nếu còn lỗi: thiếu nhãn, bài gán tay chưa đủ 20 nhãn dương, bài tính feature bằng
code cũ, bài ngoài range. Sửa theo thông báo rồi chạy lại. Gói chỉ chứa nhãn, feature, bibtex_merged, refs, candidates
của các bài trong split (chưa đo kích thước trên dữ liệu thật; có thể vài chục đến vài trăm MB).

## 3. Khải: ghép và train
```powershell
python src/matching/m3_bundle.py merge bundle_Quoc.zip bundle_Nguyen.zip bundle_Khai.zip bundle_Tri.zip --out-dir data_merged
python src/run_m3.py --data-dir data_merged --stage train,predict,evaluate
```
`merge` kiểm tra: đủ 4 thành viên, mỗi người một lần, bài đúng range, không trùng bài, cùng bộ cột feature, file zip không hỏng.
Nó in bảng tóm tắt từng người (số bài tay/auto, số nhãn dương, test/valid) để đối chiếu với đề.
(Dùng `--allow-partial` nếu muốn chạy thử khi chưa đủ người.)

Khải gửi lại cho mọi người: `data_merged/m3/predictions.parquet` (và `evaluation_report.json`, `model_metrics.json` để đưa vào báo cáo).

## 4. Mỗi người: publish phần của mình
Chép `predictions.parquet` của Khải vào `data/m3/` trên máy mình, rồi:
```powershell
python src/run_m3.py --stage publish --member <Tên> --require-all-partitions --verify-streaming
```
Bảng `predictions` xuất ra chỉ chứa bài thuộc range của bạn (đã kiểm tra: không lẫn bài của người khác).
Kiểm tra gói trước, rồi mới thêm tham số upload Hugging Face.

## Về nhãn auto trong tập test (nhóm đã chấp nhận)
Nhãn auto được sinh bằng luật dựa trên eprint/DOI/title, mà model cũng dùng feature eprint/title, nên MRR trên bài auto
sẽ **cao hơn thực tế**. Khi viết báo cáo:
- Báo **cả hai** số, lấy từ `evaluation_report.json` → `test_by_source` (`manual` và `auto`) và số test gộp.
- Nói rõ trong báo cáo vì sao hai số khác nhau; số trên bài gán tay là ước lượng đáng tin hơn về chất lượng thật.