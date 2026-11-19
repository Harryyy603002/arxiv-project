# PLAN M3 – Reference Matching Pipeline

Nhóm 4 người, **20.000 ID** (mỗi người một ID range ≈ 5.000 bài). Pipeline và mã nguồn dùng chung một repo; **riêng data submission thì mỗi người trích phần ID range của mình và host trên Hugging Face cá nhân** (thầy đã xác nhận).

Kế hoạch chia theo người, không chia theo ngày.

---

## 0. Những điểm đã chỉnh sau khi đọc code M1, M2

| # | Phát hiện trong code | Ảnh hưởng đến kế hoạch |
|---|---|---|
| 1 | `run_m2.py` (`process_paper`) đã gọi `matching.m3_candidates.generate_candidates` (cùng `normalize_text`, `normalize_authors`), chạy `validate_candidate_rows` (trong `schemas.py`) và ghi `candidates.json`, `candidate_entry_index.json`, `m2_stats.json` cho từng bài. | `m3_candidates.py` **đã có và đang chạy trong M2**. Quoc chỉ **kiểm tra recall và cải tiến**, không viết mới. Mọi thay đổi phải giữ nguyên tên hàm (vì `run_m2.py` import) và giữ khớp `CANDIDATE_FIELDS` / `validate_candidate_rows` (`candidate_id` duy nhất; `title_similarity`, `author_similarity` trong [0, 1]). |
| 2 | `schemas.py` đã có đủ 6 schema, gồm cả `PREDICTIONS_SCHEMA`. | Không cần "thêm schema". Quoc chỉ viết thêm hàm kiểm tra `predictions`. |
| 3 | Ứng viên đã có `title_similarity` (`difflib.SequenceMatcher`), `author_similarity`, `title_exact`, `reference_index`. Điều kiện duy nhất để thành ứng viên là **`title_similarity ≥ 0.35`**; entry hoặc reference **thiếu tiêu đề** bị loại (lý do `missing_title`), kể cả khi `eprint`/DOI trùng. | Feature chuỗi cơ bản đã có; Tri mở rộng thêm. **Rủi ro recall:** entry thiếu hoặc sai tiêu đề (hay gặp ở entry từ `\bibitem`) không có ứng viên dù khớp được bằng `eprint`/DOI. Quoc đo recall và cân nhắc **thêm ứng viên theo `eprint`/DOI** bất kể tiêu đề. |
| 4 | `normalize_text` / `normalize_authors` đã nằm trong `m3_candidates.py`. | Không thêm `m3_text_clean.py` rời rạc. Nếu muốn tách thì chuyển hàm sang file mới và **re-export** từ `m3_candidates.py`. |
| 5 | `references.parquet` chỉ có 6 cột của đề, không có DOI. `refs.json` và `references.csv` thì có `external_ids` (DOI, CorpusId...) và `ref_venue`. | Feature DOI/venue phải đọc `refs.json` (hoặc `references.csv`), không đọc `references.parquet`. |
| 6 | `bibtex_merged.json` giữ nguyên mọi trường gốc (`eprint`, `url`, `raw_text`, `source` bib/bibitem, `source_versions`...) dù Parquet chỉ lấy 9 cột. | Nhãn tự động và feature dùng được `eprint`/`url`/`raw_text`; bảng `bibtex` publish thì chỉ có 9 cột. |
| 7 | `elements.json` đã đổi `\cite` sang khóa chuẩn qua `cite_map`, nên `citation_key` trong `bibtex_merged.json` khớp trực tiếp với khóa trong câu. | Feature ngữ cảnh (câu chứa `\cite`, section, số lần cite) làm trực tiếp được, không cần ánh xạ thêm. |
| 8 | M1 chạy `python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 ...`. `ID.txt` mỗi dòng một khoảng, chấp nhận cả dạng chấm (`2602.04870-2602.09869`) lẫn dạng liền (`260204870-260209869`) qua `_parse_range`; khoảng qua nhiều tháng cần `month_last`. | Range của một người **có thể vắt qua nhiều tháng** (range 1: 2512 → 2601, `2512=25075`). Quoc **tái dùng `_parse_range`** (nên chuyển sang `utils/config.py`), không viết parser mới; `C.parse_id_range` hiện chỉ tách theo dấu `-` và **không** xử lý dạng liền. Lọc theo range so sánh khóa `(yymm, số)`, không so chuỗi thô. |
| 9 | **Quy mô 20k bài + nộp theo từng người** làm thay đổi cách chia nhãn và split (mục 2). | Bản kế hoạch trước (8 bài tự động, chia split toàn nhóm) **không còn đủ**. |
| 10 | Thầy đã trả lời 3 câu hỏi (mục 8): 10% tính trên **mọi bài**, Q **chỉ gồm entry có reference đúng**, mỗi repo HF **tự thỏa per-member**. | Cập nhật mục 2, 5, 6: đã bỏ phần xử lý "không có match" trong model và đổi cách chọn bài gán tay. |
| 11 | Đã đối chiếu với bài mẫu thật `2602.04912` và `ID.txt`. | Mục 4 viết lại theo dữ liệu thật. Kết luận **chắc** (4.0.A) được đưa vào thiết kế; con số chỉ có ở **một bài mẫu** (4.0.B) được ghi là giả thuyết, chờ kiểm tra. |

> **Đã đối chiếu với code:** `run_m1.py`, `run_m2.py` (các phần liên quan: đọc ID, `process_paper`), `schemas.py`, `config.py`, `m3_candidates.py`, `m1_references.py`, `m1_tables.py`, `m2_bib_dedup.py`, `m2_bibitem.py`, `m2_content_dedup.py`, `m2_pick_papers.py`, `m2_inspect.py`, và bài mẫu `2602.04912` (6 file JSON). **Chưa đọc:** `m2_cleanup.py`, `m2_gather.py`, `m2_hierarchy.py`, `benchmark.py`; các kết luận trên không phụ thuộc chúng (`tree_raw.json` không dùng ở M3).

---

## 1. Mục đích của M3

Ghép **mỗi entry BibTeX** (bảng `bibtex`, M2) với **đúng một reference có arXiv ID** (bảng `references`, M1) **trong cùng một bài**, hoặc "không có" nếu không reference nào khớp. Đây là entity resolution.

- **Không gian ứng viên:** mỗi bài m entry × n reference có arXiv ID (ma trận nhị phân m×n; mỗi hàng đã gán nhãn có đúng một ô "yes" hoặc cả hàng "no").
- **Cách đặt bài toán:** ranking theo cặp (entry, reference): model chấm điểm từng cặp, sắp giảm dần, lấy top-5 làm `cand_1..cand_5`.
- **Metric:** MRR@5 trên tập test. Tập Q **chỉ gồm các entry có reference đúng**; entry không có reference đúng không phải sample hợp lệ nên không được tính (thầy xác nhận). Entry có reference đúng nhưng không nằm trong top-5 (kể cả không có ứng viên) vẫn tính 0.
- **Sản phẩm:** bảng `predictions`; dataset Hugging Face 6 bảng Parquet (mỗi người một repo); poster A2 dọc (.pptx); README (cài đặt, chạy, định dạng nhãn, giải thích feature).

---

## 2. Quy mô 20k ID và nộp theo từng người

### 2.1 Nguyên tắc thiết kế

Đề nói số lượng nhãn và split là "per-member" (k = 4). Thầy nói mỗi người nộp phần dữ liệu của ID range mình. Để **thỏa cả hai cách hiểu**, ta **chia nhãn và split theo từng ID range**, sao cho dataset của mỗi người tự đứng được:

- Nếu để split theo cả nhóm mà không phân bổ theo range, có người sẽ không có bài test/valid, và bảng `predictions` của người đó có thể rỗng hoặc thiếu `test`.
- Việc này không làm mất tính chung của pipeline: **model chỉ train một lần trên toàn nhóm**, mỗi người chỉ trích dự đoán của các bài thuộc range mình.

### 2.2 Số lượng cho **mỗi ID range** (≈ 5.000 bài)

| Hạng mục | Mỗi người | Toàn nhóm (k = 4) | Yêu cầu của đề |
|---|---|---|---|
| Bài gán nhãn **tay** | 5 bài | 20 | 5·k |
| Entry gán tay **có reference đúng** mỗi bài | ≥ 20 | ≥ 20 | ≥ 20 (chỉ áp cho bài tay) |
| Tổng entry gán tay (có reference đúng) | ≥ 100 | ≥ 400 | 100·k |
| Bài gán nhãn **tự động** | ≥ 10% × (số bài trong range − 5 bài tay) ≈ **500** | ≈ **2.000** | ≥ 10% và ≥ 2·k = 8 |
| Test | 1 tay + 1 tự động | 4 + 4 | k + k |
| Validation | 1 tay + 1 tự động | 4 + 4 | k + k |
| Train | 3 tay + phần tự động còn lại | 12 tay + phần còn lại | tất cả bài còn lại |

**Đã xác nhận với thầy:** 10% được tính trên **tổng số bài ngoài tập gán tay**, không chỉ trên bài đủ điều kiện (5.000 bài/người → 500 bài/người, 2.000 bài toàn nhóm). Hệ quả:

1. **Cần ≥ 500 bài thực sự có nhãn tự động trong mỗi range.** Con số đúng là `ceil(0.10 × (số bài của range − 5))`; số bài của range lấy từ số `paper.json` thực tế (có thể < 5.000 nếu crawl thiếu). Nên chọn dư một ít (khoảng +3–5%) phòng bài bị loại khi kiểm tra.
2. Bài chỉ tính là "có nhãn" khi có entry có reference đúng, nên bài phải có `canonical_entries > 0` và `references_with_arxiv_id > 0` (lấy từ `m2_stats.json`). **Quoc kiểm tra sớm** mỗi range có đủ ≥ 500 bài như vậy không; nếu thiếu thì báo cả nhóm để xử lý ngay (không để đến cuối).
3. `m3_auto_labeler.py` phải **nhanh, chạy song song theo bài** (như `ProcessPoolExecutor` của `run_m2.py`).
4. **Quy ước nhãn:** bộ gán nhãn tự động chỉ ghi nhãn cho entry nó tin chắc; entry không chắc hoặc không có reference đúng ghi `null` và **bị loại khỏi Q** (không vào đánh giá, không thành dòng `predictions`). Điều này tránh làm bẩn tập test/valid bằng nhãn sai.

**Ràng buộc per-member (đã xác nhận):** mỗi repo HF cá nhân phải **tự thỏa** toàn bộ yêu cầu trong bảng trên cho range của mình. Vì vậy phân bổ theo range ở mục 2.1 là bắt buộc, không phải lựa chọn.

### 2.3 Nội dung HF của mỗi người

| Bảng | Phạm vi |
|---|---|
| `papers`, `references`, `elements`, `hierarchy`, `bibtex` | **toàn bộ bài trong ID range của người đó** (`references` lọc theo `arxiv_id` = bài trích dẫn; `ref_arxiv_id` có thể nằm ngoài range, không sao) |
| `predictions` | chỉ các bài thuộc train/valid/test **nằm trong range của người đó**; mỗi dòng là một entry **có reference đúng** (entry không có reference đúng không được tính là sample nên không có dòng) |

Mỗi repo HF có README (dataset card) với `configs` riêng. Mỗi người upload bằng token HF của mình.

---

## 3. Cấu trúc `src/matching/`

Cách đặt hợp lý: mỗi file một bước của pipeline. Hai chỉnh sửa:

1. **`m2_pick_papers.py` (trong `parser/`) và `m3_pick_papers.py` trùng mục đích** → chỉ giữ một bản trong `matching/`; bản M2 xóa hoặc chỉ import lại.
2. **`m3_candidates.py` thực chất đã thuộc M2** (được `run_m2.py` gọi). Vẫn để ở `matching/`, nhưng phải giữ giao diện ổn định.

| File | Trạng thái | Ý nghĩa ngắn gọn |
|---|---|---|
| `m3_candidates.py` | **đã có** (M2 dùng) | Sinh cặp ứng viên (entry, reference) cho từng bài, kèm `title_similarity`, `author_similarity`, `title_exact`. Cũng chứa `normalize_text`, `normalize_authors`. |
| `m3_pick_papers.py` | cập nhật | Chọn bài **theo từng ID range**: pool tay (bài có ≥ 20 entry *có reference đúng*), pool tự động (≥ 500), sinh `split.json`. |
| `m3_data_inspector.py` | cập nhật | Xem entry cạnh các ứng viên để gán nhãn tay; kiểm tra file nhãn hợp lệ. |
| `m3_auto_labeler.py` | mới/hoàn thiện | Gán nhãn tự động bằng quy tắc (arXiv ID/`eprint`, DOI, tiêu đề, tác giả); chạy song song trên ~2.000 bài. |
| `m3_string_features.py` | mới/hoàn thiện | Feature chuỗi: tiêu đề (Jaccard, Levenshtein, TF-IDF), tác giả (trùng họ, tỉ lệ), năm, DOI, arXiv ID. |
| `m3_context_features.py` | mới/hoàn thiện | Feature `ctx_*` theo cặp/tương đối trong nhóm: chênh năm, độ giống venue, hạng ứng viên, hạng entry trong nhóm reference, độ trùng từ với câu trích dẫn (chi tiết ở 4.5). |
| `m3_model.py` | mới | Huấn luyện model xếp hạng cặp; lưu/nạp model; xuất điểm. |
| `m3_evaluate.py` | mới | Tính MRR@5, phân tích lỗi, ablation, so sánh baseline. |
| `m3_predictions.py` | mới | Dựng bảng `predictions` (train/valid/test) từ điểm model. |
| `m3_publish_hf.py` | mới | Cắt 6 bảng theo ID range của một người, shard Parquet, sinh dataset card, upload HF. |

`run_m3.py` chạy theo bước: `--stage pick | label | features | train | evaluate | predict | publish`, cùng `--member <i>` cho bước `publish`.

---

## 4. Hợp đồng dữ liệu giữa các file

Soạn dựa trên **bài mẫu thật** `2602.04912` (45 entry BibTeX, 28 reference, 14 reference có arXiv ID, 152 cặp ứng viên) và `ID.txt`. Sau khi cả nhóm duyệt, lưu thành `docs/M3_CONTRACT.md` và copy bài mẫu vào `tests/fixtures/`.

### 4.0 Phát hiện từ bài mẫu (chia theo mức độ chắc chắn)

**A. Kết luận chắc**: suy ra từ code hoặc phép tính, không phụ thuộc bài mẫu.

| # | Quan sát | Hệ quả |
|---|---|---|
| A1 | Số entry có nhãn không thể vượt số reference có arXiv ID (mỗi reference khớp tối đa một entry). Bài mẫu `2602.04912` có 45 entry, nhưng chỉ 14/28 reference có arXiv ID nên **tối đa 14 nhãn, không đủ điều kiện gán tay**. | Tiêu chí chọn bài gán tay phải là `references_with_arxiv_id` (cận trên số entry khớp), **không phải** `bibtex_entries` hay `entries_with_candidates`. `m2_pick_papers.py` hiện lọc theo `bibtex_entries ≥ 20` và xếp theo `entries_with_candidates` nên **sẽ chọn sai** (bài mẫu có `entries_with_candidates = 41` nhưng chỉ ≤ 14 nhãn). |
| A2 | `bibtex_merged.json` **sắp theo `citation_key`** (do `deduplicate_keys` sắp `canonical_entries`), không giữ thứ tự bibliography. | Feature "thứ tự entry so với `reference_index`" **không khả thi**; bỏ khỏi danh sách feature. |
| A3 | Feature "mức entry" (số lần cite, section chứa `\cite`, số version) **bằng nhau cho mọi ứng viên của cùng một entry**. | Với model xếp hạng **trong từng entry**, chúng không giúp phân biệt ứng viên. `ctx_*` phải chủ yếu là feature **theo cặp** hoặc **tương đối trong nhóm** (xem 4.5). |
| A4 | `candidates.json` lưu `*_authors_normalized` cho **mỗi cặp**: 165 KB cho 152 cặp ở bài mẫu (bài có 100+ tác giả càng nặng). | Với 20.000 bài, quyết định **trước khi chạy lại M2** có bỏ chuỗi tác giả chuẩn hóa khỏi file ứng viên hay không (tính lại khi cần, hoặc chỉ lưu độ giống). Ứng viên sinh trong bước `bib` của `run_m2.py`; chạy lại bằng `--steps bib` và **không** dùng `--skip-existing` (sẽ bỏ qua bài đã có `m2_stats.json`). Bài mẫu mất ~1 giây/bài, nên 20.000 bài ≈ 5,5 giờ một tiến trình (có `--workers`). Quoc và Tri quyết định; nếu đổi cột phải sửa `CANDIDATE_FIELDS` trong `schemas.py`. |
| A5 | `run_m2.process_paper` lọc `references = [r for r in references_all if r.get("ref_arxiv_id")]` rồi gọi `generate_candidates(rows, references)`, nên ứng viên chỉ lấy reference **có arXiv ID** và `reference_index` là chỉ số trong danh sách **đã lọc** (bài mẫu: 45×14 − 152 = 478 khớp `candidate_generation_skipped`). | Không dùng `reference_index` làm khóa nối vào `refs.json`; khóa nối là `ref_arxiv_id` (hoặc `ref_s2_id`). |

**B. Giả thuyết từ một bài mẫu**: chưa có bằng chứng ở quy mô corpus. **Không dùng các con số này làm cơ sở thiết kế** cho đến khi kiểm tra xong (xem việc kiểm tra bên dưới).

| # | Quan sát trên bài `2602.04912` | Cần kiểm tra |
|---|---|---|
| B1 | Phần lớn entry có `eprint`, nhưng chỉ một phần trùng `ref_arxiv_id` (bài mẫu: 11/14 nhãn thật khớp bằng `eprint`, 3 bài `inproceedings` không có `eprint`); các entry `eprint` còn lại là bài arXiv không nằm trong reference của S2, nhãn `null`. | Tỉ lệ nhãn thật khớp được bằng `eprint` trên nhiều bài, và tỉ lệ khác nhau giữa bài `.bib` với bài `\bibitem` (bài mẫu toàn `.bib`). Quyết định luật nhãn tự động mạnh đến đâu. |
| B2 | Mọi cặp khớp thật đều nằm trong ứng viên và đứng top-1 theo `title_similarity` (bài mẫu: 11/11, cả 14 nhãn thật có `title_similarity ≥ 0.98`). Hard negative: entry `Guo_2025` (bài Nature, không có reference arXiv) vẫn có ứng viên "GAP…" với `title_similarity = 0.58`. | Recall ứng viên trên các bài đã gán tay, đặc biệt bài có entry thiếu tiêu đề hoặc tiêu đề parse từ `\bibitem`; mức độ nhiều hard negative ở bài khác. |
| B3 | Thứ tự reference có arXiv ID trong `refs.json` giảm dần theo ngày (2025-10 → 2018-09). | Chỉ để tham khảo: kết luận bỏ feature thứ tự đã đứng vững nhờ A2. |

**Việc kiểm tra B (Quoc, làm sớm):** chạy thống kê trên `m2_stats.json` bản gộp toàn corpus của từng range và đọc thêm 2–3 bài đa dạng (một bài đủ điều kiện gán tay; một bài có entry `source = "bibitem"`; một bài nhiều entry thiếu `eprint` hoặc tiêu đề lạ). Sau đó **cập nhật bảng B** bằng số liệu thật (tỉ lệ, trung bình, phân bố) và đưa kết quả vào buổi duyệt hợp đồng. Cũng từ thống kê này kiểm tra mỗi range có đủ bài cho nhãn tay (≥ 5 bài có `references_with_arxiv_id ≥ 22`) và nhãn tự động (≈ 500 bài).

### 4.1 ID range của từng người (`ID.txt`)

Thứ tự dòng trong `ID.txt` ↔ người: **Quoc, Nguyen, Khai, Tri**.

| Range | Dòng trong `ID.txt` | Dạng chuẩn | Số bài | Người (repo HF) |
|---|---|---|---|---|
| 1 | `251223231-260103155` | `2512.23231 – 2601.03155` (vắt qua 2 tháng; `--month-last 2512=25075`) | 1.845 + 3.155 = 5.000 | **Quoc** |
| 2 | `260204870-260209869` | `2602.04870 – 2602.09869` (bài mẫu `2602.04912` thuộc range này) | 5.000 | **Nguyen** |
| 3 | `260300580-260305579` | `2603.00580 – 2603.05579` | 5.000 | **Khai** |
| 4 | `260620734-260625733` | `2606.20734 – 2606.25733` | 5.000 | **Tri** |

`ID.txt` dùng dạng liền (YYMM + 5 chữ số). M1 đã có `_parse_range` đọc được cả dạng liền lẫn dạng chấm, và `run_m1.py` chạy với `--month-last 2512=25075`; số 25075 khớp phép tính của range 1 (23231..25075 là 1.845 ID, cộng 3.155 ID của tháng 2601 thành 5.000).

Quy ước kỹ thuật (Quoc cài trong `config.py`, **tái dùng `_parse_range`** thay vì viết lại parser):

```python
MEMBERS = ["Quoc", "Nguyen", "Khai", "Tri"]          # thứ tự = thứ tự dòng trong ID.txt
MEMBER_RANGES = {"Quoc": ("2512.23231", "2601.03155"), "Nguyen": ("2602.04870", "2602.09869"),
                 "Khai": ("2603.00580", "2603.05579"), "Tri": ("2606.20734", "2606.25733")}
def id_key(arxiv_id): yymm, num = C.split_version(arxiv_id)[0].split("."); return (int(yymm), int(num))
def in_range(arxiv_id, member): lo, hi = MEMBER_RANGES[member]; return id_key(lo) <= id_key(arxiv_id) <= id_key(hi)
def member_of(arxiv_id): ...   # None nếu không thuộc range nào
```

So sánh bằng khóa `(yymm, số)`, **không so chuỗi**. Range 1 gồm mọi `2512.n` với n ≥ 23231 và mọi `2601.n` với n ≤ 3155.

### 4.2 Đầu vào có sẵn từ M1/M2 (đã kiểm tra trên bài mẫu)

Mỗi bài: thư mục `<DATA_DIR>/<paper_key>/`, với `paper_key = 2602-04912`.

| File | Cấu trúc | Ghi chú |
|---|---|---|
| `refs.json` | `{"arxiv_id", "references": [...]}`; mỗi reference: `ref_arxiv_id` (null nếu không có), `ref_title`, `ref_authors` (list tên), `ref_submission_date` (`YYYY-MM-DD`, `YYYY` hoặc null), `ref_venue`, `ref_s2_id` (có thể null), `external_ids` (dict: `ArXiv`, `DOI`, `DBLP`, `ACL`, `MAG`, `CorpusId`) | Chứa **mọi** reference (28), gồm cả reference không có arXiv ID. DOI có ở `external_ids`. |
| `bibtex_merged.json` | list entry. Luôn có: `citation_key`, `entry_type`, `title`, `author` (chuỗi "A and B"), `authors` (list), `year` (int), `arxiv_id` (bài chứa entry), `source_versions` (list). Thường null/thiếu: `doi`, `journal`, `url`, `eprint`, `archiveprefix`, `primaryclass`, `booktitle`, `pages`... Entry từ `\bibitem` có thêm `raw_text`, `source = "bibitem"`. | **Sắp theo `citation_key`**, không theo thứ tự trong bibliography. `eprint` có ở 38/45 entry bài mẫu. |
| `candidates.json` | list cặp; mỗi cặp: `candidate_id` (hash 16 ký tự), `arxiv_id`, `citation_key`, `ref_s2_id`, `ref_arxiv_id`, `reference_index`, `title_similarity`, `author_similarity`, `title_exact`, `bib_title`, `ref_title`, `*_normalized` | Chỉ cặp với reference **có arXiv ID** và `title_similarity ≥ 0.35`. `candidate_id` là **khóa chính** của mọi bảng M3. |
| `elements.json` | list `{element_id, arxiv_id, kind, content}`; `kind ∈ {document, heading, sentence, formula, figure, itemize, item}` | Câu chứa `\cite{khóa}` dùng **khóa chuẩn** (sau `cite_map`), khớp trực tiếp với `citation_key` trong `bibtex_merged.json`. |
| `hierarchy.json` | list `{arxiv_id, version, element_id, parent_id}` | Không có dòng cho gốc `document`. Element dùng chung giữa các version (`version` khác nhau, cùng `element_id`). Muốn biết section chứa câu: đi ngược `parent_id` đến `heading` gần nhất, trong version mới nhất. |
| `m2_stats.json` (trong thư mục bài) | dict; các khóa dùng cho chọn bài: `canonical_entries`, `references_ingested`, `references_with_arxiv_id`, `candidate_pairs`, `entries_with_candidates`, `zero_candidate_entries`, `exact_title_candidates` | Bản gộp toàn corpus `<DATA_DIR>/m2_stats.json` là **list** các dict này (`m2_pick_papers.py` đọc bản này). |

### 4.3 Nhãn: `m3/labels/<paper_key>.json`

Một định dạng cho nhãn tay và nhãn tự động. Đáp án luôn là **`ref_arxiv_id`** (không phải `ref_s2_id`, không kèm hậu tố version).

```json
{
  "arxiv_id": "2602.04912",
  "source": "manual",
  "labeler": "Quoc",
  "labels": {
    "wu2025gapgraphbasedagentplanning": "2510.25320",
    "min-etal-2023-factscore": "2305.14251",
    "Guo_2025": null
  },
  "evidence": {
    "wu2025gapgraphbasedagentplanning": "eprint",
    "min-etal-2023-factscore": "title_exact"
  }
}
```

- `source`: `"manual"` hoặc `"auto"`. `labeler`: tên người, hoặc `"auto_v1"` (ghi phiên bản luật).
- `labels[khóa] = "<ref_arxiv_id>"`: entry **có** reference đúng. Giá trị phải là một `ref_arxiv_id` có trong `refs.json` của bài.
- `labels[khóa] = null`: **đã xem, không có** reference đúng. Entry này **không** tính vào Q, vào số lượng ≥ 20/≥ 100, và không có dòng trong `predictions`.
- Khóa **không có mặt** trong `labels`: chưa gán (với nhãn tự động: bộ gán nhãn không tin chắc; coi như không có nhãn, không dùng cho đánh giá).
- `evidence` (tùy chọn, bắt buộc với nhãn tự động): `eprint | doi | title_exact | title_fuzzy`; để Tri đo precision theo từng luật.
- Gán tay **không điền sẵn đáp án** từ nhãn tự động (tránh người gán tay xác nhận theo thói quen và làm test tay thiên lệch). Công cụ hiển thị ứng viên theo `title_similarity` giảm dần, người gán chọn.
- Một `ref_arxiv_id` chỉ được gán cho **một** entry trong cùng một bài; nếu hai entry cùng trỏ về một reference thì ghi chú, Quoc kiểm tra file nhãn sẽ cảnh báo.

### 4.4 Split: `m3/split.json`

```json
{
  "seed": 42,
  "members": {
    "<tên>": {
      "range": "2602.04870-2602.09869",
      "manual": ["2602.0xxxx", "... 5 bài ..."],
      "auto":   ["... ≥ 10% × (số bài − 5) ≈ 500 bài ..."],
      "test":  {"manual": ["id"], "auto": ["id"]},
      "valid": {"manual": ["id"], "auto": ["id"]}
    }
  }
}
```

- `train` **không lưu**: là mọi bài có nhãn (`manual` ∪ `auto`) trừ test và valid. Hàm `partition_of(arxiv_id)` trong `m3_pick_papers.py` trả về `train | valid | test | None`.
- Mọi ID trong `manual`/`auto` của một người phải thuộc range của người đó (`in_range`).
- Bài chưa nằm trong `split.json` thì không có dòng `predictions`.

### 4.5 Feature: `m3/features/<paper_key>.parquet`

- **Khóa:** `candidate_id` (khóa chính) kèm `arxiv_id`, `citation_key`, `ref_arxiv_id` để đọc cho dễ. **Đúng một dòng cho mỗi dòng trong `candidates.json`**; không thêm bớt dòng.
- Không chứa nhãn. Nối nhãn lúc train bằng `labels` (`ref_arxiv_id == labels[citation_key]`).
- **Cột chỉ là số** (`float32`, `int8`, `bool`); thiếu dữ liệu để **null**, không điền 0/−1. Khai quyết định cách xử lý null khi train.
- **Tiền tố cột:** `str_*` (Tri: tiêu đề, tác giả, DOI, arXiv ID, năm), `ctx_*` (Nguyen). Không trùng tên giữa hai nhóm.
- Tri ghi `features/<paper_key>.str.parquet`; Nguyen đọc, thêm `ctx_*` và ghi bản ghép `features/<paper_key>.parquet`.
- **`ctx_*` nên là feature theo cặp hoặc tương đối trong nhóm** (xem 4.0.A3), ví dụ: chênh lệch năm giữa `year` của entry và năm của `ref_submission_date`; độ giống giữa `journal`/`booktitle` và `ref_venue`; hạng của ứng viên trong nhóm entry (theo `str_*`), **hạng của entry trong nhóm reference** (mỗi reference nên khớp một entry; "mutual best"); chênh điểm với ứng viên thứ hai; số ứng viên của entry; độ trùng từ giữa câu trích dẫn (các câu `\cite{khóa}`) và tiêu đề reference. Feature chỉ phụ thuộc entry (số lần cite, section, số version) có thể giữ nhưng chỉ dùng làm biến phụ.

### 4.6 Điểm: `m3/scores.parquet` (Khai ghi, Quoc đọc)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `candidate_id` | string | khóa nối với `candidates.json` |
| `arxiv_id`, `citation_key`, `ref_arxiv_id` | string | |
| `score` | float | **càng cao càng khớp**, so sánh được trong cùng `(arxiv_id, citation_key)` |
| `model_version` | string | ví dụ `lgbm_v3` |

- Chứa mọi ứng viên của mọi bài trong `split.json`, mọi partition.
- Entry **có nhãn** nhưng **không có ứng viên đúng** trong `scores` thì `evaluate` tự tính 0 (vẫn nằm trong Q); không cần thêm dòng giả.

### 4.7 Predictions: `m3/predictions.parquet`

Đúng `PREDICTIONS_SCHEMA` trong `schemas.py` (`arxiv_id, partition, citation_key, groundtruth_arxiv_id, cand_1 … cand_5`).

- Một dòng mỗi entry **có nhãn khác `null`** của các bài trong `split.json`. Không có dòng cho entry nhãn `null` hay chưa gán.
- `partition ∈ {train, valid, test}`; `groundtruth_arxiv_id` là `ref_arxiv_id` đúng.
- `cand_k` là `ref_arxiv_id` xếp hạng k theo `score`; **không trùng nhau** trong một dòng; thiếu ứng viên thì để null.
- Cần `validate_predictions()` kiểm tra: đủ cột, đúng kiểu, `partition` hợp lệ, `cand_k` không trùng, mọi `arxiv_id` thuộc `split.json`.

### 4.8 Đường dẫn và `poster_inputs/`

`config.py` thêm (Quoc): `M3_DIR = DATA_DIR / "m3"`, `labels_path(id)`, `features_path(id)`, `split_path()`, `scores_path()`, `predictions_path()`, `poster_inputs_dir()`.

`m3/poster_inputs/<người>_<chủ_đề>.{png,csv,md}`: mỗi người bỏ hình/bảng đã làm xong cho phần của mình; Nguyen chỉ lấy từ đây, không đi lấy số liệu ở nơi khác.

### 4.9 Ai đề xuất, ai duyệt, đóng băng

| Định dạng | Đề xuất | Duyệt |
|---|---|---|
| `MEMBER_RANGES`, `split.json`, `labels/*.json`, đường dẫn | Quoc | Tri, Khai |
| `candidates.json` (cột, ngưỡng, có lưu chuỗi tác giả không) | Quoc | Tri, Nguyen |
| `features/*.parquet` (khóa, tiền tố, null) | Nguyen | Tri, Khai |
| `scores.parquet` | Khai | Quoc |
| `predictions.parquet` | Quoc | Khai |
| `poster_inputs/` | Nguyen | cả nhóm |

Khai quyết định khi hai bên không đồng ý. Sau buổi duyệt (1–2 giờ), nội dung này đóng băng; đổi định dạng phải báo người dùng dữ liệu đó và sửa `docs/M3_CONTRACT.md` trong cùng một commit.

---

## 5. Phân công theo người

Bảng người ↔ vai (theo vai trò ở M1/M2):

| Người | Vai trò ở M1/M2 | Vai trò ở M3 | ID range (repo HF) |
|---|---|---|---|
| **Quoc** | Source & Structure | Chọn bài, split, công cụ gán nhãn, **evaluate, predictions** | Range 1 (`2512.23231–2601.03155`) |
| **Tri** | References & BibTeX | Nhãn tự động, feature chuỗi | Range 4 (`2606.20734–2606.25733`) |
| **Nguyen** | Text & Normalization | Feature ngữ cảnh, ghép feature, **poster** | Range 2 (`2602.04870–2602.09869`) |
| **Khai** | Data, Model & Delivery | Model, publish HF, `run_m3`, README | Range 3 (`2603.00580–2603.05579`) |

Mỗi người phụ trách ID range của mình và **gán tay 5 bài trong range đó** (mỗi bài ≥ 20 entry có reference đúng, tổng ≥ 100 entry). Mỗi người cũng **tự chạy `publish` cho range của mình** (với token HF của mình) và kiểm tra repo HF của mình.

### Quoc – Chọn bài, split, đánh giá, predictions

**File:** `m3_pick_papers.py`, `m3_candidates.py` (chỉ cải tiến), `m3_data_inspector.py`, `m3_evaluate.py`, `m3_predictions.py`, `config.py` (`MEMBER_RANGES`), `schemas.py` (thêm `validate_predictions`).

- Lấy số liệu từ `m2_stats.json`; với mỗi range chọn 5 bài tay + bài tự động (≥ 10% × (số bài − 5) ≈ 500, chọn dư vài %) + 1 tay/1 tự động cho test, 1 tay/1 tự động cho valid. Ghi `split.json`.
- **Bài gán tay phải có ≥ 20 entry có reference đúng** (entry không match không được tính). Cận trên của số entry khớp là `references_with_arxiv_id` (mỗi reference khớp tối đa một entry), nên **tiêu chí chọn là `references_with_arxiv_id ≥ 22–25`** (chừa vài entry không khớp được). **Không** dùng `bibtex_entries` hay `entries_with_candidates` (bài mẫu có 45 entry, 41 entry có ứng viên nhưng chỉ 14 reference có arXiv ID nên chỉ tối đa 14 nhãn, **không đủ**). Sửa `m2_pick_papers.py`/`m3_pick_papers.py` theo tiêu chí này, rồi chạy nhanh nhãn tự động của Tri để ước lượng số entry khớp thật; chọn dư vài bài dự phòng.
- **Kiểm tra sớm nguồn cung:** mỗi range có đủ ≥ 500 bài đủ điều kiện cho nhãn tự động và ≥ 5 bài cho nhãn tay không (mục 2.2).
- **Thống kê kiểm tra giả thuyết (mục 4.0.B):** chạy trên `m2_stats.json` toàn corpus và 2–3 bài đa dạng, cập nhật bảng B bằng số liệu thật trước buổi duyệt hợp đồng.
- Đo **recall của bước sinh ứng viên** trên các bài đã gán tay (trong các entry có reference đúng, bao nhiêu % có reference đó nằm trong ứng viên). Đây là **cận trên của MRR@5**; nếu thấp thì nới điều kiện trong `generate_candidates` (ví dụ thêm ứng viên theo `eprint`/DOI khi thiếu tiêu đề, xem mục 0 dòng 3).
- Công cụ hỗ trợ gán tay và hàm kiểm tra file nhãn (mỗi `citation_key` đúng một giá trị, ref nằm trong danh sách reference của bài).
- **`m3_evaluate.py`:** MRR@5 đúng đề (Q = entry có reference đúng; rank ngoài top-5 hoặc không có ứng viên tính 0), test bằng ví dụ nhỏ tính tay. Báo cáo theo tập (tay/tự động) **và theo từng range**, kèm ablation theo nhóm feature và phân tích lỗi.
- **`m3_predictions.py`:** đọc `scores.parquet`, lấy top-5 không trùng, ghi một dòng mỗi entry **có reference đúng** của các bài thuộc train/valid/test; `partition` đúng `train/valid/test`; không tạo dòng cho entry không có reference đúng.
- Viết mục "Định dạng nhãn" trong README.

**Giao cho:** Tri, Nguyen, Khai (split, ranges, định dạng nhãn); Nguyen (số liệu recall, MRR cho poster).

### Tri – Gán nhãn tự động và feature chuỗi

**File:** `m3_auto_labeler.py`, `m3_string_features.py` (và tách cleaning nếu cần, nhớ re-export).

- Luật `eprint == ref_arxiv_id` cho nhãn chính xác nhưng **không phủ hết**: entry `inproceedings` thường không có `eprint` và chỉ khớp bằng tiêu đề (xem giả thuyết B1, tỉ lệ cụ thể chờ thống kê trên nhiều bài), nên các luật tiêu đề/tác giả sau luật định danh là bắt buộc. Chuẩn hóa ID bằng `C.normalize_arxiv_id` (bỏ `arXiv:`, URL, hậu tố version) trước khi so sánh.
- Bộ gán nhãn tự động theo thứ tự ưu tiên: khớp `eprint`/arXiv ID trong `raw_text`/`url` → khớp DOI với `external_ids` của reference → tiêu đề chuẩn hóa trùng → tiêu đề gần giống + tác giả khớp → `null` nếu dưới ngưỡng (entry đó bị loại khỏi Q). Chạy song song trên ≥ 500 bài mỗi range; ngưỡng đặt thiên về **precision** để nhãn tự động trong test/valid sạch.
- Bài tự động dùng cho test/valid: chọn trong số bài có tỉ lệ entry khớp bằng định danh chính xác (arXiv ID/DOI) cao, và kiểm tra tay một mẫu nhỏ.
- Đo precision/recall của bộ gán nhãn tự động trên các bài đã gán tay (số liệu cho poster).
- Feature chuỗi và phân tích minh họa từng feature (phân bố cặp đúng/sai).
- **Rủi ro:** feature trùng với luật của bộ gán nhãn → model chỉ học lại luật. Vì vậy báo cáo MRR tách test tay và test tự động, và dùng thêm feature ngữ cảnh của Nguyen.

**Giao cho:** Khai (nhãn tự động, feature chuỗi); Nguyen (feature chuỗi để ghép; số liệu cho poster).

### Nguyen – Feature ngữ cảnh, ghép feature, poster

**File:** `m3_context_features.py`, bước ghép feature trong `run_m3.py` (hàm gọi từ `--stage features`), poster (`poster_m3.pptx`, thư mục `poster/`).

- Feature `ctx_*` (xem 4.5): do entry-level feature (số lần cite, section, số version) bằng nhau cho mọi ứng viên của một entry nên không giúp xếp hạng, hãy ưu tiên feature **theo cặp hoặc tương đối trong nhóm**: chênh lệch năm, độ giống venue (`journal`/`booktitle` với `ref_venue`), hạng ứng viên trong entry và **hạng entry trong nhóm reference** (mutual best), chênh điểm với ứng viên thứ hai, độ trùng từ giữa câu trích dẫn và tiêu đề reference. **Không dùng thứ tự bibliography** (bài mẫu: `bibtex_merged.json` sắp theo khóa, `refs.json` sắp theo ngày; kiểm lại trên vài bài khác). Kiểm chứng từng feature bằng phân tích dữ liệu.
- Tính feature song song theo bài, lưu theo bài để chạy lại được; ghép `str_*` của Tri với `ctx_*` thành `features/<paper_key>.parquet`.
- **Poster A2 dọc (.pptx):** dựng khung ngay từ đầu, rồi điền dần từ `poster_inputs/` của mọi người (pipeline, định dạng nhãn, độ phủ ứng viên, chất lượng nhãn tự động, bảng feature, MRR@5, phân tích lỗi). Khung poster phải xong **trước** khi có kết quả cuối để không dồn vào phút chót.

**Giao cho:** Khai (feature đã ghép).

### Khai – Model, Hugging Face, chạy tổng, README

**File:** `m3_model.py`, `m3_publish_hf.py`, `run_m3.py`, `tests/`, `README.md`, `requirements.txt`.

- **Model:** baseline (logistic regression) và model chính (ví dụ LightGBM ranker); chọn siêu tham số trên valid. Cho **nhãn tay trọng số cao hơn** nhãn tự động. Xử lý mất cân bằng (mỗi hàng 1 dương, n−1 âm). Vì entry không có reference đúng **không nằm trong Q**, model **không cần ngưỡng từ chối**, chỉ cần xếp hạng; có thể dùng entry `null` đã xác nhận làm mẫu âm nếu thấy có ích. Xuất `scores.parquet` cho mọi cặp ở mọi tập.
- **`m3_publish_hf.py --member i`:** lọc 6 bảng theo range của người i (papers, references theo `arxiv_id` trích dẫn; elements/hierarchy/bibtex/predictions theo `arxiv_id`), ép đúng schema (`write_parquet`), **shard** theo kích thước, sinh dataset card với `configs` cho từng bảng, upload và kiểm tra bằng `load_dataset(..., streaming=True)`. Dữ liệu M2 lấy từ `<corpus>/export/*.parquet` (gộp mọi bài, cần cắt theo range); `papers.parquet`, `references.parquet` ở gốc corpus. Mỗi người chạy lệnh này cho range của mình.
- **`run_m3.py`:** chạy end-to-end theo `--stage`, đo hiệu năng bằng `utils/benchmark.py`.
- Cập nhật `README.md` (cài đặt, chạy, mô tả feature) và `requirements.txt`.

**Giao cho:** Quoc (`scores.parquet`); Nguyen (số liệu model cho poster).

---

## 6. Quy ước gán nhãn tay

- Mỗi người 5 bài **trong range của mình**, mỗi bài có **≥ 20 entry có reference đúng**, tổng ≥ 100 entry có reference đúng.
- Với mỗi entry: ghi reference đúng (`ref_arxiv_id` trong danh sách reference có arXiv ID của bài), hoặc `null` nếu không có reference đúng. Entry `null` **không được tính** vào Q, vào số lượng ≥ 20/≥ 100 và vào bảng `predictions`; vẫn ghi `null` trong file nhãn để phân biệt "đã xem, không match" với "chưa gán".
- Ghi `labeler` trong file nhãn để truy vết.
- (Tùy chọn) 2 người cùng gán 2–3 bài để đo độ đồng thuận Cohen's kappa, làm số liệu cho poster.

---

## 7. Phụ thuộc giữa các người

Có thể **làm song song ngay** sau khi chốt mục 4 (hợp đồng dữ liệu), dùng dữ liệu mẫu nhỏ (`--ids`, `--limit`).

```
Quoc (ranges, split, định dạng nhãn) ──► Tri, Nguyen, Khai
Tri (nhãn tự động, str_*) ──┬──► Nguyen (ghép features) ──► Khai (model) ──► Quoc (evaluate, predictions)
                            └──► Khai (nhãn train)                              │
Nguyen (poster) ◄── poster_inputs/ của mọi người ◄───────────────────────────────┘
Khai (publish HF) ◄── predictions của Quoc
```

Việc có thể làm trước khi người khác xong:
- **Quoc:** `evaluate` và `predictions` viết trước với `scores.parquet` giả (điểm ngẫu nhiên hoặc `title_similarity` có sẵn).
- **Khai:** `publish` viết và thử trên một range nhỏ; baseline model dùng `title_similarity`/`author_similarity` trong `candidates.json`.
- **Nguyen:** feature ngữ cảnh và khung poster, không phụ thuộc ai.
- **Tri:** bản v1 của bộ gán nhãn nên ra sớm (dù còn thô) để Khai có dữ liệu train thử.

Điều kiện chung: `run_m2.py` đã chạy xong cho cả 4 range (có `m2_stats.json` đủ).

---

## 8. Các điểm đã được thầy/đề xác nhận

| Câu hỏi | Kết luận | Áp dụng ở |
|---|---|---|
| "10% publications" đếm trên gì? | Trên **tổng số bài ngoài tập gán tay** (5.000 bài/người → 500; toàn nhóm 2.000). | Mục 2.2, Quoc, Tri |
| Entry không có reference đúng có vào Q không? | **Không.** Không phải sample hợp lệ; Q chỉ gồm các nhãn có match. | Mục 1, 2.3, 6, Khai, Quoc |
| Mỗi repo HF có phải tự thỏa per-member? | **Có.** Mỗi repo cá nhân phải tự thỏa ràng buộc per-member của riêng mình. | Mục 2.1, 2.2, Khai |

Điểm nhỏ còn lại nhóm tự quyết (không cần hỏi): cách tính ngưỡng "tin chắc" của bộ gán nhãn tự động; tỉ lệ dư khi chọn bài tự động.

---

## 9. Checklist nộp bài

- [ ] Mỗi range: ≥ 5 bài tay (≥ 20 entry **có reference đúng**/bài, ≥ 100 entry tổng), ≥ 10% × (số bài − 5) ≈ 500 bài tự động
- [ ] Mỗi range: test 1 tay + 1 tự động, valid 1 tay + 1 tự động; chia theo bài
- [ ] Recall ứng viên đo trên nhãn tay và ghi trong báo cáo
- [ ] Feature nào cũng có lý do + phân tích dữ liệu (README, poster)
- [ ] MRR@5 trên test (Q chỉ gồm entry có reference đúng), báo cáo tách tay/tự động và theo từng range; ghi rõ nếu dùng LLM/pretrained (bị chấm riêng)
- [ ] 4 repo HF cá nhân: 6 bảng Parquet đúng schema, đã shard, dataset card có `configs`, tải được bằng streaming
- [ ] `predictions` mỗi repo không rỗng, có đủ train/valid/test, không có dòng cho entry không có reference đúng
- [ ] README: cài đặt, chạy, định dạng nhãn, mô tả feature
- [ ] Poster pptx A2 dọc
- [ ] Repo chỉ chứa mã nguồn (không dữ liệu), có `requirements.txt`
