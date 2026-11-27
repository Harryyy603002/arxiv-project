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

# 3. Gán nhãn tự động, CHỈ cho các bài trong split (khoảng 500 bài auto + 5 bài tay)
python src/run_m3.py --stage label --member <Tên> --split-only

# 4. Kiểm tra phần của bạn có tự thỏa yêu cầu của đề không (phải ra "ĐẠT TOÀN BỘ")
python src/matching/m3_bundle.py check --data-dir data --member <Tên>

# 5. Tính feature (chỉ chạy khi bước 4 đã đạt)
python src/run_m3.py --stage features --member <Tên> --split-only --rebuild-features
```
Đề chỉ yêu cầu gán tự động **ít nhất 10% số bài ngoài nhóm gán tay** (khoảng 500 bài mỗi người), nên luôn dùng `--split-only`.
Không có nó thì lệnh chạy trên MỌI bài trong `data/` (khoảng 5.000 bài), rất chậm mà phần lớn kết quả không dùng tới.

Bước `check` đọc trực tiếp `split.json` và `m3/labels/*.json`, kiểm tra: bài tay (>= 5 bài, mỗi bài >= 20 nhãn dương,
tổng >= 100), quota bài auto có nhãn dương (>= 10% × (số bài trong range − 5)), test/valid mỗi phần 1 tay + 1 auto,
và mọi bài trong split đều có file nhãn. `export --strict` ở mục 2 chạy lại đúng kiểm tra này.

### Nếu bước 3 cảnh báo "bài trong split chưa có nhãn dương" (hoặc bước 4 báo lỗi)
**Cách A (nên chọn): gỡ các bài đó khỏi split, mất vài phút.** Chỉ dùng được khi số bài auto còn lại vẫn >= quota.
Script sao lưu `split.json`, gỡ bài lỗi, và thay bài auto của test/valid nếu bài lỗi nằm ở đó:

```python
import json, shutil
from pathlib import Path

p = Path("data/m3/split.json")
shutil.copy(p, p.with_suffix(".json.bak"))
s = json.loads(p.read_text(encoding="utf-8"))
m = s["members"]["<Tên>"]
bad = {"xxxx.xxxxx", "xxxx.xxxxx"}  # ID các bài chưa có nhãn dương (lấy từ cảnh báo)

used = {a for part in ("test", "valid") for v in m[part].values() for a in v}
good = [a for a in m["auto"] if a not in bad and a not in used]
for part in ("test", "valid"):
    m[part]["auto"] = [a if a not in bad else good.pop(0) for a in m[part].get("auto", [])]
m["auto"] = [a for a in m["auto"] if a not in bad]

meta = m.setdefault("selection_metadata", {})
meta["auto_selected_with_buffer"] = len(m["auto"])
meta["dropped_auto_without_positive_labels"] = sorted(bad)

p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(m["auto"]), "bài auto; quota tối thiểu:", meta.get("auto_minimum_by_plan"))
```
Rồi chạy lại bước 4 (`check`) và bước 5 (features). Không cần chạy lại `label`. Đừng chạy `pick --rebuild-split` sau khi đã chỉnh tay, vì nó ghi đè.

**Cách B (chậm, vài giờ): chọn lại pool.** Chỉ dùng khi sau khi gỡ thì số bài auto thấp hơn quota.
5 bài gán tay không bị đổi, nhưng test/valid auto có thể bị xáo lại.
```powershell
python src/run_m3.py --stage label --member <Tên>                                     # không --split-only: gán thử trên toàn bộ data
python src/run_m3.py --stage pick --member <Tên> --rebuild-split --require-auto-labels # chọn lại pool, chỉ lấy bài đã có nhãn dương
python src/run_m3.py --stage label --member <Tên> --split-only
python src/matching/m3_bundle.py check --data-dir data --member <Tên>
python src/run_m3.py --stage features --member <Tên> --split-only --rebuild-features
```
(`--allow-insufficient-auto` chỉ tạo split thử nghiệm khi pool thiếu, không phải xác nhận đạt; phải dựa vào `check`.)

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