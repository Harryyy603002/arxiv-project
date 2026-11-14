# Đồ án arXiv: Milestone 1 (Data Crawling)

Mã nguồn crawl toàn bộ **mọi version** của các bài báo arXiv được phân công. Với mỗi bài, chương trình:

1. Tra metadata và số version qua **arXiv API** (thư viện `arxiv`).
2. Tải source từng version qua `https://export.arxiv.org/src/<id>v<N>`, **chỉ giữ `.tex` và `.bib`** (bỏ hình ảnh). Version chỉ có PDF vẫn được ghi một "bản ghi rỗng".
3. Lấy danh sách reference qua **Semantic Scholar API** (gọi theo batch, chạy song song với bước tải source).
4. Dựng hai bảng Parquet `papers` và `references` đúng schema đề, và đo hiệu năng (thời gian, RAM, đĩa) **theo từng phiên chạy**, kèm bản tổng hợp các phiên.

## 1. Cấu trúc repo

```text
src/
├── run_m1.py              # điểm chạy chính (discovery + crawl + bảng + benchmark)
├── requirements.txt
├── crawler/
│   ├── m1_download.py     # tải và trích .tex/.bib từ archive
│   ├── m1_references.py   # gọi Semantic Scholar, chuẩn hóa reference
│   ├── m1_tables.py       # dựng papers.parquet / references.parquet
│   └── m1_aggregate.py    # gộp log benchmark của nhiều phiên Colab
└── utils/
    ├── config.py          # đường dẫn, hằng số, biến môi trường
    ├── schemas.py         # schema pyarrow của các bảng
    └── benchmark.py       # PerfRecorder: đo thời gian, RAM, đĩa
```

Repo chỉ chứa mã nguồn. **Không commit thư mục dữ liệu** (corpus, parquet, benchmark).

## 2. Cài đặt

Yêu cầu: Python 3.10 trở lên.

```bash
git clone <repo-url> && cd <repo>
pip install -r src/requirements.txt
```

Biến môi trường (có thể đặt trong file `.env` ở thư mục gốc; `python-dotenv` sẽ tự đọc):

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `CONTACT_EMAIL` | Có | Email thật, đưa vào User-Agent để arXiv liên hệ khi cần. |
| `S2_API_KEY` | Nên có | Key miễn phí của Semantic Scholar. Không có key sẽ chậm và dễ bị lỗi 429. Trên Colab có thể lưu trong Secrets với tên `S2_API_KEY`. |
| `ARXIV_DATA_DIR` | Không | Thư mục dữ liệu mặc định (mặc định `<repo>/data`). Có thể ghi đè bằng `--corpus-dir`. |

```bash
export CONTACT_EMAIL="ten@domain.com"
export S2_API_KEY="xxxxxxxx"
```

> **Quy tắc arXiv đã được tuân thủ trong code:** chỉ dùng `export.arxiv.org`, một kết nối duy nhất, cách nhau ít nhất 3 giây giữa hai request, có User-Agent mô tả rõ. Đừng sửa các thông số này để chạy nhanh hơn, vì sẽ bị chặn 403 và vi phạm đề.

## 3. Chuẩn bị danh sách ID

`--ids-file` là file văn bản (ví dụ `ID.txt`), mỗi dòng là **một arXiv ID** hoặc **một khoảng ID** của giảng viên giao. Cả hai dạng viết đều được:

```text
260204870-260209869        # viết liền: YYMM + 5 chữ số, tức 2602.04870 đến 2602.09869 (5 000 ID)
2602.04870-2602.09869      # viết có dấu chấm
2310.12345                 # một ID đơn
hep-th/9901001             # ID kiểu cũ
```

Dòng bắt đầu bằng `#` bị bỏ qua. File có BOM hay xuống dòng kiểu Windows vẫn đọc được; dòng không hợp lệ bị bỏ qua và báo số dòng.

**Khoảng ID đi qua nhiều tháng** (ví dụ `251223231-260103155`, từ 2512.23231 sang 2601.03155) cần thêm ID cuối của tháng đầu bằng `--month-last`:

```bash
--month-last 2512=25075
```

Nếu thiếu, chương trình dừng và báo `Thiếu ID cuối của tháng 2512`. Số 25075 trên đây suy ra từ việc mỗi khoảng có đúng 5 000 ID (25075 − 23231 + 1 + 3155 = 5000); hãy kiểm tra bằng arXiv API (2512.25075 phải tồn tại, 2512.25076 thì không):

```python
import arxiv
client = arxiv.Client()
for i in ["2512.25075", "2512.25076"]:
    print(i, bool(list(client.results(arxiv.Search(id_list=[i])))))   # mong đợi: True, False
```

Muốn dùng riêng một bộ ID thử nhanh, thêm `--limit N` (N ID đầu) hoặc `--sample N` (N ID cách đều trên toàn bộ danh sách, đại diện hơn).

## 4. Chạy

Luôn chạy từ **thư mục gốc repo**.

**Bước 1: chạy thử 3 bài** (nên làm trước khi chạy thật):

```bash
printf "1706.03762\n1810.04805\n1512.03385\n" > ids_test.txt
python src/run_m1.py --ids-file ids_test.txt --corpus-dir ./data_test
```

Hoặc thử 50 ID cách đều trong khoảng của nhóm:

```bash
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 --sample 50 --corpus-dir ./data_sample
```

Kiểm tra `data_test/` có source, `paper.json`, `refs.json`, hai file `.parquet` và thư mục `benchmark/` (xem mục 5).

**Bước 2: chạy toàn bộ:**

```bash
mkdir -p data/logs
nohup python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 --corpus-dir ./data \
    > data/logs/session_001.log 2>&1 &
tail -f data/logs/session_001.log
```

Các tùy chọn:

| Tùy chọn | Tác dụng |
|---|---|
| `--month-last YYMM=N` | ID cuối của tháng, cho khoảng ID đi qua nhiều tháng (vd. `2512=25075`). |
| `--limit N` / `--sample N` | Chỉ lấy N ID đầu / N ID cách đều (dùng để chạy thử). |
| `--batch-size 25` | Số bài mỗi batch gọi Semantic Scholar. |
| `--backup-dir <dir>` | Sao lưu từng batch và log các phiên vào thư mục này (ví dụ Google Drive). |
| `--bench-dir <dir>` | Thư mục log từng phiên (mặc định `<corpus-dir>/benchmark`). |
| `--no-discovery-cache` | Không đọc/ghi `discovery_cache.json` (mất khả năng resume discovery). |
| `--force-refs` | Gọi lại Semantic Scholar kể cả bài đã có refs. |
| `--skip-tables` | Không dựng `papers/references.parquet`. |
| `--user-agent "..."` | Ghi đè User-Agent (mặc định lấy từ `CONTACT_EMAIL`). |

### Chạy lại / tiếp tục sau khi bị ngắt

Chỉ cần chạy lại **đúng lệnh cũ**. Chương trình bỏ qua phần đã xong:

- version đã có `vN/metadata.json` (không tải lại source);
- bài đã có cả `refs.json` và `refs_raw.json` (không gọi lại Semantic Scholar);
- ID đã có trong `discovery_cache.json` (không hỏi lại arXiv API).

Version tải lỗi (mạng, 5xx) không được ghi checkpoint nên lần chạy sau sẽ tự thử lại.

### Chạy nhiều phiên Google Colab

Thầy đã xác nhận: số liệu hiệu năng **đo trên Google Colab**, được **chia thành nhiều phiên** nếu vượt giới hạn 12 giờ, với điều kiện **giữ log thời gian từng phiên** và **ghi rõ trong Jupyter Book** cách tổng hợp. Code hỗ trợ việc này như sau:

- Mỗi lần chạy `run_m1.py` là một phiên, ghi log riêng `benchmark/session_NNN.json` (số thứ tự tự tăng).
- Log được cập nhật sau mỗi batch (tối đa mỗi phút một lần) và khi gặp lỗi/Ctrl+C, nên phiên bị ngắt vẫn để lại số liệu gần nhất (`status` là `running` hoặc `interrupted`). Chỉ phần thời gian từ lần ghi cuối đến lúc bị ngắt đột ngột (tối đa khoảng một batch) là không được tính.
- Cuối mỗi phiên, `crawler/m1_aggregate.py` tự gộp mọi phiên vào `benchmark/benchmark_m1_total.json` và `benchmark/sessions.csv`. Có thể chạy tay: `python src/crawler/m1_aggregate.py --data-dir ./data`.
- **Không xóa các file `session_*.json`.** Dùng `--backup-dir` để chúng được sao lưu cùng dữ liệu.

Quy trình mỗi phiên trên Colab (phiên đầu bỏ bước khôi phục):

```python
from google.colab import drive, userdata
import os
drive.mount('/content/drive')
os.environ["CONTACT_EMAIL"] = "ten@domain.com"
os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")

BACKUP = "/content/drive/MyDrive/arxiv_backup"
!git clone <repo-url> && pip install -q -r <repo>/src/requirements.txt
# Phiên 2 trở đi: khôi phục dữ liệu và log các phiên trước
!mkdir -p /content/corpus && [ -d {BACKUP} ] && cp -r {BACKUP}/. /content/corpus/ || true
!mkdir -p {BACKUP}/logs
# Số phiên (NNN) khớp với số thứ tự tự tăng của benchmark/session_NNN.json
!cd <repo> && python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 --corpus-dir /content/corpus \
    --backup-dir {BACKUP} 2>&1 | tee {BACKUP}/logs/session_002.log
```

Cuối cùng, `/content/corpus/benchmark/benchmark_m1_total.json` là số liệu tổng hợp để đưa vào báo cáo; `sessions.csv` là bảng từng phiên (giờ bắt đầu, thời gian chạy, số version mới tải, RAM đỉnh, cấu hình máy, `is_colab`).

#### Quy tắc gộp số liệu giữa các phiên

| Số liệu | Cách gộp |
|---|---|
| Tổng thời gian chạy, thời gian discovery, thời gian từng giai đoạn | **Cộng** qua các phiên (không tính khoảng nghỉ giữa các phiên). |
| Thời gian xử lý trung bình/lớn nhất một bài | Cộng thời gian của bài qua các phiên, rồi lấy trung bình/cực đại. |
| RAM đỉnh, đĩa đỉnh | **Cực đại** qua các phiên. |
| RAM trung bình | Trung bình có trọng số theo số mẫu của từng phiên. |
| Dung lượng đầu ra cuối | Tính lại từ thư mục dữ liệu. |
| Tỉ lệ thành công, kích thước trước/sau bỏ hình, số reference trung bình | Lấy từ **phiên hoàn tất cuối cùng**, vì phiên đó duyệt lại toàn bộ danh sách ID. |

## 5. Cấu trúc thư mục dữ liệu sau khi crawl

Ví dụ **minh họa** (định dạng file là thật; nội dung lấy từ lần chạy thử với dữ liệu giả). Bài `2301.00001` có 2 version, bài `2301.00002` chỉ có PDF. ID cũ như `hep-th/9901001` được lưu ở thư mục `hep-th-9901001/`.

```text
data/                                  # = --corpus-dir = ARXIV_DATA_DIR
├── 2301-00001/                        # một thư mục mỗi bài: <paper_key>
│   ├── paper.json                     # metadata cấp bài
│   ├── refs.json                      # toàn bộ reference đã chuẩn hóa (cả loại không có arXiv ID)
│   ├── refs_raw.json                  # phản hồi gốc của Semantic Scholar
│   ├── v1/
│   │   ├── main.tex                   # chỉ .tex và .bib, giữ nguyên đường dẫn con (sections/intro.tex...)
│   │   ├── refs.bib
│   │   ├── metadata.json              # trạng thái tải + số byte (đồng thời là checkpoint của version)
│   │   └── references.json            # bản sao references cho version này
│   └── v2/ ...
├── 2301-00002/                        # bài chỉ có PDF
│   └── v1/metadata.json               # bản ghi rỗng, không có .tex
├── hep-th-9901001/ ...                # ID cũ giữ nguyên trong file, "/" đổi thành "-" ở tên thư mục
├── discovery_cache.json               # kết quả tra arXiv API (phục vụ resume)
├── papers.parquet, papers.csv         # bảng papers (schema đề)
├── references.parquet, references.csv # bảng references (chỉ reference có arXiv ID)
├── references_stats.json              # số reference tổng / có arXiv ID / trùng
├── refs_failures.jsonl                # (nếu có) bài Semantic Scholar trả lỗi, sẽ gọi lại ở lần sau
├── benchmark/
│   ├── session_001.json               # log của từng phiên Colab (GIỮ LẠI để đối chiếu)
│   ├── session_002.json
│   ├── benchmark_m1_total.json        # số liệu tổng hợp mọi phiên (dùng cho báo cáo)
│   └── sessions.csv                   # một dòng mỗi phiên
└── logs/                              # (tùy chọn) log console của từng phiên: session_NNN.log
```

**`paper.json`**

```json
{
  "arxiv_id": "2301.00001",
  "title": "...",
  "authors": ["A B"],
  "submission_date": "2023-01-01T00:00:00+00:00",
  "revised_dates": ["2023-01-02T00:00:00+00:00"],
  "venue": null,
  "n_versions": 2
}
```

**`v2/metadata.json`** (version có TeX; `status="ok"`)

```json
{
  "arxiv_id": "2301.00001v2", "version": 2, "date": "2023-01-02T00:00:00+00:00",
  "status": "ok", "reason": "", "files": ["main.tex", "refs.bib"],
  "archive_bytes": 244, "bytes_before": 20076, "bytes_after": 76
}
```

`bytes_before` là tổng kích thước mọi file trong archive, còn `bytes_after` là tổng kích thước `.tex` và `.bib` còn giữ lại. Hai số này dùng để tính kích thước trung bình trước/sau khi bỏ hình.

**Bản ghi rỗng** (version chỉ có PDF, ví dụ `2301-00002/v1/metadata.json`)

```json
{
  "arxiv_id": "2301.00002v1", "version": 1, "status": "empty", "reason": "pdf_only",
  "files": [], "archive_bytes": 13, "bytes_before": 13, "bytes_after": 0
}
```

Các giá trị `reason` khác: `no_tex_in_archive`, `single_file_not_tex`, `corrupt_archive`, `unknown_format`.

**Một phần tử trong `refs.json`** (giữ mọi reference, kể cả không có arXiv ID)

```json
{
  "ref_arxiv_id": "1706.03762", "ref_title": "...", "ref_authors": ["X"],
  "ref_submission_date": "2020-05-01", "ref_venue": null, "ref_s2_id": "p1",
  "external_ids": {"ArXiv": "1706.03762"}
}
```

`ref_submission_date` lấy từ trường `publicationDate` của Semantic Scholar (theo hướng dẫn của thầy: tên `submission_date` chỉ để thống nhất với bảng `papers`). Nếu reference không có `publicationDate`, code dùng năm xuất bản (`YYYY`) làm giá trị dự phòng, nên cột này có thể chứa cả `YYYY-MM-DD` lẫn `YYYY`.

### Hai bảng đầu ra

| Bảng | Cột |
|---|---|
| `papers.parquet` | `arxiv_id`, `title`, `authors` (list), `submission_date`, `revised_dates` (list), `venue` |
| `references.parquet` | `arxiv_id` (bài trích dẫn), `ref_arxiv_id`, `ref_title`, `ref_authors` (list), `ref_submission_date`, `ref_s2_id` |

Bảng `references` chỉ chứa reference **có arXiv ID** (theo đề). `references.csv` có thêm `ref_venue` và `external_ids`. Có thể dựng lại hai bảng bất cứ lúc nào: `python src/crawler/m1_tables.py --data-dir ./data`.

### Log từng phiên `benchmark/session_NNN.json`

Cùng các khóa chỉ số như `benchmark_m1_total.json` (bảng dưới) cộng thêm thông tin phiên:

| Khóa | Ý nghĩa |
|---|---|
| `session_index`, `status` | Số thứ tự phiên; `completed`, `interrupted` hoặc `running` (bị kill đột ngột). |
| `started_at_utc`, `updated_at_utc`, `ended_at_utc` | Mốc thời gian của phiên. |
| `environment` | Hệ điều hành, Python, số CPU, RAM, cờ `is_colab` (chứng minh đo trên Colab). |
| `resumed`, `new_versions_this_session` | Phiên này có dùng dữ liệu từ phiên trước không; số version tải mới. |
| `coverage` | Số version dự kiến/đã crawl/lỗi, số bài không tồn tại, số mục lấy từ cache. |
| `paper_times_s`, `paper_sizes_bytes`, `paper_results`, `paper_references` | Số liệu từng bài. |

### `benchmark_m1_total.json`

| Nhóm | Khóa chính |
|---|---|
| Phiên | `n_sessions`, `n_completed_sessions`, `n_interrupted_sessions`, `sessions` (bảng từng phiên) |
| Thời gian | `total_wall_s`, `total_wall_h`, `stages_s` (discovery/crawl/backup/tables), `discovery_s`, `avg_paper_s`, `max_paper_s` |
| Thành công | `success_rate`, `ref_success_rate`, `avg_refs_per_paper`, `avg_arxiv_refs_per_paper` |
| Kích thước | `avg_paper_size_before_bytes`, `avg_paper_size_after_bytes`, `n_pdf_only_versions` |
| Tài nguyên | `peak_ram_mb`, `avg_ram_mb`, `peak_disk_mb`, `final_output_mb`, `environments` |
| Bao phủ | `coverage.version_coverage`, `coverage.failed_versions`, `coverage.not_found_papers` |

## 6. Ước lượng thời gian

> Đây là **ước lượng tính toán, chưa đo thực tế**. Hãy chạy 3 bài thật ở bước 1 rồi so lại.

Nút thắt là giới hạn của arXiv: **mỗi version là một request riêng, cách nhau tối thiểu 3 giây**, chỉ một kết nối. CPU, RAM và băng thông gần như không ảnh hưởng. Công thức: `tổng ≈ (số version) × max(3 giây, thời gian tải) + discovery`.

Thầy cho biết trung vị của các nhóm năm ngoái vào khoảng **7–8 giờ cho 5 000 ID**. Con số này khớp với tính toán của nhóm: với giả định ~1,6 version mỗi bài thì 5 000 ID cần ~8 000 lần tải ≈ 6,7–9 giờ. Với 20 000 ID, ước lượng khoảng **28–45 giờ** (tức 3–4 phiên Colab), trong đó discovery chiếm khoảng một giờ. Số version trung bình thực tế sẽ biết sau khi discovery xong (xem `discovery_cache.json`).

Dung lượng đầu ra ước tính từ vài GB đến hơn chục GB (chưa đo), đủ trong giới hạn đĩa Colab. RAM đỉnh bị chi phối bởi archive lớn nhất (mỗi archive được giữ trong bộ nhớ).

## 7. Tăng tốc

Theo thầy, **chính thức không có cách nào tăng tốc việc crawl**; hướng tối ưu duy nhất là xử lý dữ liệu đã lấy trong khi chờ dữ liệu mới. Code đã làm như sau:

- Gọi Semantic Scholar (theo batch) **song song** với việc tải source từ arXiv. Hai bước dùng hai máy chủ khác nhau nên không tranh giới hạn của nhau.
- Khoảng cách 3 giây được tính từ lúc **bắt đầu** request này đến lúc bắt đầu request kế tiếp, nên thời gian giải nén và ghi file của version trước nằm trong khoảng chờ, không cộng thêm.
- Cache discovery và checkpoint theo version/bài, nên chạy lại không lặp công việc.

Các hướng còn lại có lợi ích nhỏ: giải nén/ghi file bất đồng bộ (chỉ có ích khi lần tải kèm giải nén dài hơn 3 giây), hoặc gom thêm ID mỗi request discovery (tiết kiệm khoảng 15–20 phút). Mọi cách làm giảm khoảng cách 3 giây, tải song song nhiều version hay đổi IP/chia ID cho nhiều máy chạy cùng lúc đều vi phạm quy định và dễ bị arXiv chặn 403, nên **không dùng**.

## 8. Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `ModuleNotFoundError: utils` hoặc `crawler` | Chạy từ thư mục gốc repo bằng `python src/run_m1.py`. |
| `ModuleNotFoundError: pyarrow` | `pip install -r src/requirements.txt`. |
| HTTP 403 từ arXiv | Đang dùng sai host hoặc request quá dày; kiểm tra lại `BASE_URL` và `MIN_DELAY_SECONDS`. |
| HTTP 429 từ Semantic Scholar | Đặt `S2_API_KEY`; code tự giãn nhịp và thử lại. Bài lỗi sẽ được gọi lại ở lần chạy sau. |
| Cảnh báo `CONTACT_EMAIL` mặc định | `export CONTACT_EMAIL=...` trước khi chạy. |
| `version_coverage` < 1 | Xem các dòng lỗi tải source trong log, chạy lại cùng lệnh để thử lại version lỗi. |
| `not_found_papers` > 0 | ID không tồn tại trên arXiv API (bị xóa/chưa có); không phải lỗi mạng. |
| Phiên mới không thấy dữ liệu cũ | Chưa khôi phục từ `--backup-dir` (xem mục "Chạy nhiều phiên Google Colab"). |
