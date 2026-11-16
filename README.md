# arXiv Project: Nhóm <tên nhóm>

Đồ án môn học: Xây dựng pipeline dữ liệu và ghép nối trích dẫn bài báo khoa học arXiv (**Milestone 1: Crawling → Milestone 2: Parsing → Milestone 3: Matching**).

- **Báo cáo (Jupyter Book):** `<link GitHub Pages>`
- **Dataset (Hugging Face):** `<link dataset>`
- **Tài liệu kỹ thuật chi tiết:** [docs/README.md](docs/README.md)
- **Nhật ký thay đổi M1/M2:** [docs/CHANGELOG_M1_M2.md](docs/CHANGELOG_M1_M2.md)

---

## 1. Giới thiệu tổng quan

Dự án xử lý dữ liệu quy mô lớn từ arXiv và Semantic Scholar, chia thành 3 giai đoạn chính:

1. **Milestone 1 — Data Crawling (`src/run_m1.py`):**
   - Thu thập metadata và toàn bộ mọi version (`v1`, `v2`, ...) của 20.000 bài báo arXiv được phân công qua arXiv API và `export.arxiv.org`.
   - Tải và giải nén source TeX từng version, **chỉ giữ lại file `.tex` và `.bib`** (loại bỏ toàn bộ hình ảnh và file phụ trợ). Version chỉ có PDF vẫn được ghi nhận một "bản ghi rỗng" (`vN/metadata.json`) để phục vụ thống kê.
   - Thu thập danh sách tài liệu tham khảo (references) qua Semantic Scholar API bằng batch requests (25 bài/nhịp), thực thi song song bất đồng bộ với luồng tải source TeX.
   - Dựng hai bảng Parquet đầu ra `papers.parquet` và `references.parquet` theo đúng schema quy định.
   - Đo lường và tổng hợp hiệu năng (thời gian, RAM đỉnh, dung lượng đĩa) theo từng phiên chạy trên Google Colab qua `src/crawler/m1_aggregate.py`.

2. **Milestone 2 — LaTeX Parsing & Normalization (`src/run_m2.py`):**
   - **Step `bib`:** Trích xuất mục trích dẫn từ file `.bib` và môi trường `\bibitem` trong `.tex`, chuẩn hóa tiêu đề/tác giả, hợp nhất chéo version thành `bibtex_merged.json` và sinh bản đồ ánh xạ `cite_map.json`. Sinh chỉ mục ứng viên và `candidates.json` cho M3.
   - **Step `tree`:** Xác định file gốc tài liệu, mở rộng đệ quy `\input` và `\include`, mở rộng macro người dùng (`\newcommand`, `\def`), trích xuất cấu trúc phân cấp tài liệu (section, paragraph, itemize, equation, figure) thành `tree_raw.json`.
   - **Step `content`:** Làm sạch văn bản LaTeX (loại bỏ comment, chuẩn hóa toán inline `MATH_` / block `EQ_`, bảo tồn lệnh `\cite{...}`), phân tách câu (`m2_sentences.py`), cập nhật các khóa trích dẫn theo khóa chuẩn hóa trong `cite_map.json`, và khử trùng lặp nội dung chéo version thành `elements.json` và `hierarchy.json`.
   - **Export Parquet:** Xuất 3 bảng Parquet chuẩn hóa `elements.parquet`, `hierarchy.parquet`, và `bibtex.parquet` vào thư mục `<corpus>/export/`.

3. **Milestone 3 — Citation Matching (`src/run_m3.py`):**
   - Ghép nối các mục BibTeX trong bài báo với các tài liệu tham khảo (references) thực tế từ Semantic Scholar / arXiv.
   - Trích xuất đặc trưng chuỗi (Levenshtein, Jaro-Winkler, token overlap, difflib ratio) và đặc trưng ngữ cảnh từ các câu trích dẫn trong `elements`.
   - Gán nhãn thủ công (`manual_labels.csv`) và tự động (`auto_labels.csv`), huấn luyện mô hình phân loại và đánh giá xếp hạng theo độ đo **MRR@5**.
   - Xuất bản bộ dữ liệu hoàn chỉnh lên Hugging Face Hub.

---

## 2. Cấu trúc Repository

```text
arxiv-project/
├── data/                             # Dữ liệu cục bộ (được .gitignore, KHÔNG commit)
│   ├── papers.parquet, papers.csv
│   ├── references.parquet, references.csv
│   ├── export/                       # Đầu ra Parquet của M2
│   │   ├── elements.parquet
│   │   ├── hierarchy.parquet
│   │   └── bibtex.parquet
│   ├── benchmark/                    # Log hiệu năng từng phiên & tổng hợp
│   └── <paper_key>/                  # Dữ liệu chi tiết từng bài báo (2310-12345)
├── docs/                             # Tài liệu kỹ thuật chi tiết theo chuẩn đề
│   ├── 00_project_overview.md        # Tổng quan đồ án, yêu cầu đề, tiến độ
│   ├── 01_system_architecture.md     # Kiến trúc hệ thống, luồng dữ liệu Mermaid
│   ├── 02_data_contracts.md          # Đặc tả JSON trung gian và schema 6 bảng Parquet
│   ├── 03_m1_crawling.md             # Tài liệu kỹ thuật Milestone 1
│   ├── 04_m2_parsing.md              # Tài liệu kỹ thuật Milestone 2
│   ├── 05_m3_matching.md             # Tài liệu kỹ thuật Milestone 3
│   ├── 06_integration.md             # Hướng dẫn tích hợp liên milestone & run order
│   ├── 07_testing_and_performance.md # Kiểm thử và phân tích số liệu benchmark
│   ├── 08_development_log.md         # Nhật ký phát triển, commit history, lỗi đã sửa
│   ├── CHANGELOG_M1_M2.md            # Tổng kết cải tiến & bản vá PR #18/#19
│   └── README.md
├── src/                              # Toàn bộ mã nguồn thực thi
│   ├── run_m1.py                     # Điểm chạy chính M1 (crawl + tables + bench)
│   ├── run_m2.py                     # Điểm chạy chính M2 (bib + tree + content + export)
│   ├── run_m3.py                     # Điểm chạy chính M3 (matching + evaluation)
│   ├── crawler/                      # Pipeline Milestone 1
│   │   ├── m1_download.py            # Tải archive arXiv, trích .tex/.bib
│   │   ├── m1_references.py          # Batch API Semantic Scholar, chuẩn hóa ref
│   │   ├── m1_tables.py              # Dựng bảng papers.parquet & references.parquet
│   │   └── m1_aggregate.py           # Gộp log benchmark từ nhiều phiên Colab
│   ├── parser/                       # Pipeline Milestone 2
│   │   ├── m2_gather.py              # Thu thập file TeX, giải nén, scan cây
│   │   ├── m2_bibitem.py             # Trích xuất bibitem từ file .tex
│   │   ├── m2_bib_dedup.py           # Phân tích BibTeX & gộp chéo version
│   │   ├── m2_hierarchy.py           # Xây dựng cây phân cấp document (tree_raw.json)
│   │   ├── m2_cleanup.py             # Khử macro (Tầng 1), chuẩn hóa toán & làm sạch text (Tầng 2)
│   │   ├── m2_sentences.py           # Tách câu theo quy tắc/regex
│   │   ├── m2_content_dedup.py       # Khử trùng lặp nội dung & đổi khóa \cite
│   │   ├── m2_inspect.py             # Xem nhanh kết quả parse M2 từng bài
│   │   └── m2_pick_papers.py         # Lọc bài đủ điều kiện gán nhãn M3
│   ├── matching/                     # Pipeline Milestone 3
│   │   ├── m3_candidates.py          # Tạo cặp ứng viên BibTeX - Reference
│   │   ├── m3_pick_papers.py         # Lọc bài đủ điều kiện gán nhãn M3
│   │   ├── m3_string_features.py     # Đặc trưng so khớp chuỗi
│   │   ├── m3_context_features.py    # Đặc trưng ngữ cảnh từ câu trích dẫn
│   │   ├── m3_auto_labeler.py        # Tự động sinh nhãn
│   │   ├── m3_model.py               # Huấn luyện mô hình xếp hạng
│   │   ├── m3_predictions.py         # Xuất bảng predictions
│   │   ├── m3_evaluate.py            # Đánh giá độ đo MRR@5
│   │   └── m3_publish_hf.py          # Đóng gói và đẩy dataset lên Hugging Face
│   ├── utils/                        # Tiện ích chung & cấu hình
│   │   ├── config.py                 # Đường dẫn chuẩn, hằng số, biến môi trường
│   │   ├── logger.py                 # Chuẩn hóa định dạng log pipeline
│   │   ├── schemas.py                # Schema PyArrow 6 bảng & export Parquet
│   │   └── benchmark.py              # PerfRecorder đo RAM, đĩa, thời gian
│   └── tests/                        # Bộ kiểm thử tự động (165 tests)
├── pytest.ini
└── requirements.txt                  # Danh sách thư viện Python cần thiết
```

---

## 3. Chuẩn bị môi trường & Cài đặt

Yêu cầu: **Python 3.10 trở lên**.

### 3.1 Cài đặt trên máy cá nhân

Tạo môi trường ảo và cài đặt dependencies:

```bash
git clone https://github.com/Harryyy603002/arxiv-project.git
cd arxiv-project
python -m venv .venv

# Kích hoạt môi trường ảo:
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# Linux / macOS:        source .venv/bin/activate

pip install -r requirements.txt
```

### 3.2 Cấu hình biến môi trường

Tạo file `.env` ở thư mục gốc (file này đã có trong `.gitignore`, **tuyệt đối không commit API key**):

```bash
# Bắt buộc: Email thật để đưa vào User-Agent liên hệ khi tải từ arXiv
CONTACT_EMAIL=ten_cua_ban@domain.com

# Khuyến nghị: Key miễn phí của Semantic Scholar (tránh lỗi HTTP 429 và tăng tốc rate limit)
S2_API_KEY=s2_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Tùy chọn: Thư mục lưu trữ dữ liệu (mặc định là ./data)
ARXIV_DATA_DIR=./data
```

> **Tuân thủ quy định crawler của arXiv:** Hệ thống tuân thủ nghiêm ngặt chính sách của arXiv: chỉ truy vấn `export.arxiv.org`, duy trì duy nhất 1 kết nối tuần tự, giãn cách tối thiểu 3 giây giữa hai lượt tải source, và gắn User-Agent định danh rõ ràng kèm email. Không được phép chỉnh sửa hạ thấp độ trễ 3s để tránh bị cấm IP (HTTP 403).

### 3.3 Chạy trên Google Colab

Mở notebook mới và thực hiện theo từng cell:

```python
# 1. Mount Google Drive để lưu trữ và backup dữ liệu bền vững
from google.colab import drive, userdata
import os
drive.mount("/content/drive")

# 2. Đặt cấu hình môi trường
os.environ["CONTACT_EMAIL"] = "ten_cua_ban@domain.com"
os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")  # Lưu trong mục Secrets của Colab

# 3. Clone repo và cài dependencies
!git clone https://github.com/Harryyy603002/arxiv-project.git
%cd arxiv-project
!pip install -q -r requirements.txt
```

---

## 4. Quy định dữ liệu & Thỏa thuận giao diện (Data Contracts)

### 4.1 Quy tắc chung về dữ liệu

- Thư mục `data/` chứa toàn bộ dữ liệu thô, corpus và Parquet. **Nghiêm cấm commit bất kỳ dữ liệu nào vào Git** (kể cả CSV nhãn hoặc log tạm).
- Tên thư mục bài báo tuân theo quy ước `<paper_key>`: thay thế tất cả dấu `.` và `/` trong arXiv ID bằng dấu `-` (ví dụ `2310.12345` → `2310-12345`, `hep-th/9901001` → `hep-th-9901001`).
- Trong nội dung các file JSON và Parquet, trường `arxiv_id` **luôn luôn giữ nguyên định dạng arXiv ID gốc**.
- Luôn sử dụng các hàm dựng đường dẫn tập trung trong `src/utils/config.py` (`paper_dir`, `version_dir`, `papers_parquet_path`, `cite_map_path`, v.v.).

### 4.2 Cấu trúc thư mục dữ liệu hoàn chỉnh

```text
data/                                      # Thư mục gốc dữ liệu (DATA_DIR)
├── papers.parquet, papers.csv             # Bảng papers tổng hợp M1
├── references.parquet, references.csv     # Bảng references tổng hợp M1 (chỉ giữ ref có arXiv ID)
├── references_stats.json                  # Thống kê số lượng reference tổng / có arXiv ID / trùng
├── refs_failures.jsonl                    # Log các bài lỗi Semantic Scholar cần thử lại
├── discovery_cache.json                   # Cache metadata từ arXiv API phục vụ resume
├── export/                                # Đầu ra 3 bảng Parquet chuẩn hóa của M2
│   ├── elements.parquet                   # Bảng các phần tử văn bản đã làm sạch, khử trùng lặp
│   ├── hierarchy.parquet                  # Bảng quan hệ cây phân cấp nút - cha
│   └── bibtex.parquet                     # Bảng mục trích dẫn BibTeX đã chuẩn hóa & gộp version
├── labels/                                # Nhãn phục vụ huấn luyện và đánh giá M3
│   ├── manual_labels.csv                  # Nhãn gán thủ công (tối thiểu 100 cặp)
│   └── auto_labels.csv                    # Nhãn sinh tự động (≥ 10% số bài ngoài tập tay)
├── benchmark/                             # Nhật ký đo lường hiệu năng
│   ├── session_001.json                   # Số liệu chi tiết từng phiên chạy Colab
│   ├── session_002.json
│   ├── sessions.csv                       # Tổng kết các phiên (thời gian, RAM, CPU, đĩa)
│   └── benchmark_m1_total.json            # Bản tổng hợp hiệu năng M1 cuối cùng đưa vào báo cáo
└── <paper_key>/                           # Thư mục cho từng bài báo (ví dụ: 2310-12345/)
    ├── paper.json                         # Metadata cấp bài báo (từ arXiv API)
    ├── refs.json                          # Toàn bộ reference đã chuẩn hóa (cả có và không có arXiv ID)
    ├── refs_raw.json                      # Phản hồi gốc từ Semantic Scholar Graph API
    ├── cite_map.json                      # Bản đồ ánh xạ citation key cũ -> citation key chuẩn hóa
    ├── bibtex_entries_raw.json            # Toàn bộ mục BibTeX thô trích xuất từ .bib và \bibitem
    ├── bibtex_merged.json                 # Các mục BibTeX đã hợp nhất xuyên suốt các version
    ├── candidate_entry_index.json         # Danh sách chỉ mục phục vụ sinh ứng viên M3
    ├── candidates.json                    # Cặp ứng viên (BibTeX entry - Reference) kèm similarity
    ├── elements.json                      # Các phần tử văn bản (paragraph, item, math, ...) sau khử trùng
    ├── hierarchy.json                     # Danh sách cạnh nút - cha cho từng version
    ├── m2_stats.json                      # Thống kê chi tiết quá trình parse M2 của bài
    └── vN/                                # Thư mục version (v1, v2, ...; bản rỗng nếu chỉ có PDF)
        ├── *.tex, *.bib                   # Mã nguồn TeX và file thư viện đã lọc sạch ảnh
        ├── metadata.json                  # Metadata version, trạng thái tải, dung lượng byte trước/sau
        ├── references.json                # Bản sao danh sách references của version
        └── tree_raw.json                  # Cây cấu trúc tài liệu thô trước khi làm sạch text
```

### 4.3 Thỏa thuận giao diện trao đổi giữa các thành viên

| # | Từ → Đến | File dữ liệu | Quy cách & Nội dung chính |
| --- | --- | --- | --- |
| 1 | M1 → Cả nhóm | `<paper_key>/paper.json` | `arxiv_id, title, authors, submission_date, revised_dates, venue, n_versions` |
| 2 | M1 → Cả nhóm | `<paper_key>/vN/metadata.json` | `arxiv_id, version, date, title, authors, venue, source_kind, archive_bytes, bytes_before, bytes_after` |
| 3 | M1 → Cả nhóm | `<paper_key>/refs.json` | Danh sách toàn bộ reference crawl từ Semantic Scholar |
| 4 | M1 → Báo cáo | `data/papers.parquet`, `references.parquet` | Bảng papers và bảng references tổng hợp toàn bộ tập ID phân công |
| 5 | P1 → P3 | `<paper_key>/vN/tree_raw.json` | Cây phân cấp thô chứa các khối block `document`, `section`, `paragraph`, `equation`, `figure` |
| 6 | P2 → P3, P4 | `<paper_key>/cite_map.json`, `bibtex_merged.json` | Ánh xạ đổi tên khóa trích dẫn và bảng BibTeX đã gộp chéo version |
| 7 | P3 → P4 | `<paper_key>/elements.json`, `hierarchy.json` | Văn bản đã tách câu, giữ nguyên `\cite{...}`, đổi khóa chuẩn, và bảng cạnh cây phân cấp |
| 8 | P4 → Báo cáo | `data/export/*.parquet` | 3 bảng Parquet M2: `elements.parquet`, `hierarchy.parquet`, `bibtex.parquet` |
| 9 | Cả nhóm | `labels/manual_labels.csv`, `auto_labels.csv` | Cột: `arxiv_id, citation_key, groundtruth_arxiv_id` |

---

## 5. Phân công & Khoảng ID phụ trách

### 5.1 Phân công vai trò

| Mã | Thành viên | Mảng phụ trách | Nhiệm vụ trọng tâm |
| --- | --- | --- | --- |
| **P1** | Quoc | Source & Structure | Tải source TeX, bóc tách cấu trúc tài liệu, dựng cây `tree_raw.json` |
| **P2** | Tri | References & BibTeX | Thu thập reference Semantic Scholar, bóc tách `\bibitem` / `.bib`, hợp nhất BibTeX |
| **P3** | Nguyen | Text & Normalization | Làm sạch văn bản TeX, tách câu, đổi khóa `\cite`, khử trùng lặp nội dung `elements` |
| **P4** | Khai | Data, Model & Delivery | Đóng gói Parquet, sinh feature so khớp, gán nhãn, huấn luyện mô hình M3, xuất bản HF |

### 5.2 Khoảng ID được phân công thực tế

Mỗi thành viên phụ trách một dải 5.000 ID độc lập:

| Thành viên | Khoảng ID thực tế | Số lượng | Ghi chú & Tham số đặc biệt |
| --- | --- | --- | --- |
| **Quoc** | `2512.23231` → `2601.03155` | 5.000 | Khoảng đi qua 2 tháng. Cần thêm `--month-last 2512=25075` |
| **Nguyen** | `2602.04870` → `2602.09869` | 5.000 | Trong cùng tháng `2602` |
| **Khai** | `2603.00580` → `2603.05579` | 5.000 | Trong cùng tháng `2603` |
| **Tri** | `2606.20734` → `2606.25733` | 5.000 | Trong cùng tháng `2606` |

**Quy cách ID đầu vào (`--ids-file` hoặc `--ids`):**
Hỗ trợ truyền qua file (`--ids-file`) hoặc trực tiếp từ dòng lệnh (`--ids`). Mỗi mục có thể là:

- Một khoảng ID có dấu chấm: `2606.20734-2606.25733`
- Một khoảng ID viết liền: `260620734-260625733`
- Một ID đơn: `2310.12345` hoặc ID cũ `hep-th/9901001`
- Trong file, dòng bắt đầu bằng `#` được xem là comment và tự động bỏ qua.

---

## 6. Hướng dẫn chạy Pipeline

### 6.1 Milestone 1: Thu thập dữ liệu (`src/run_m1.py`)

#### Bước 1: Chạy thử nghiệm nhanh (3 bài hoặc 50 bài lấy mẫu)

```bash
# Thử nghiệm 3 ID tiêu biểu
printf "1706.03762\n1810.04805\n1512.03385\n" > test_ids.txt
python src/run_m1.py --ids-file test_ids.txt --corpus-dir ./data_test

# Hoặc lấy mẫu 50 ID cách đều trong khoảng ID phân công
python src/run_m1.py --ids-file ID.txt --sample 50 --corpus-dir ./data_sample
```

#### Bước 2: Chạy toàn bộ dữ liệu trên máy cá nhân

```bash
# Tạo thư mục log
mkdir -p data/logs

# Chạy ngầm bằng nohup
nohup python src/run_m1.py --ids-file ID.txt --corpus-dir ./data \
    > data/logs/session_001.log 2>&1 &

# Theo dõi tiến trình
tail -f data/logs/session_001.log
```

*(Nếu là dải ID của Quoc, hãy thêm cờ `--month-last 2512=25075`).*

#### Bước 3: Chạy nhiều phiên trên Google Colab & Tự động gộp Benchmark

Google Colab giới hạn thời gian chạy liên tục (12 giờ). Nhóm áp dụng quy trình chia phiên và tự động sao lưu vào Google Drive:

```python
# Script chạy trên Colab
from google.colab import drive, userdata
import os
drive.mount('/content/drive')
os.environ["CONTACT_EMAIL"] = "ten@domain.com"
os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")

BACKUP = "/content/drive/MyDrive/arxiv_backup"
!mkdir -p /content/corpus && [ -d {BACKUP} ] && cp -r {BACKUP}/. /content/corpus/ || true
!mkdir -p {BACKUP}/logs

# Chạy crawler kèm sao lưu tự động sau từng batch
!python src/run_m1.py --ids-file ID.txt --corpus-dir /content/corpus \
    --backup-dir {BACKUP} 2>&1 | tee {BACKUP}/logs/session_001.log
```

Cuối mỗi phiên, `src/crawler/m1_aggregate.py` tự động tổng hợp toàn bộ các file `session_*.json` thành `benchmark_m1_total.json` và `sessions.csv`. Có thể chạy tổng hợp thủ công bất kỳ lúc nào:

```bash
python src/crawler/m1_aggregate.py --data-dir ./data
```

#### Bảng tham số CLI của `src/run_m1.py`

| Tham số | Ý nghĩa & Hướng dẫn sử dụng |
| --- | --- |
| `--ids-file PATH` | Đường dẫn file chứa danh sách arXiv ID hoặc khoảng ID (truyền `--ids-file` hoặc `--ids`). |
| `--ids [ID ...]` | Danh sách arXiv ID hoặc khoảng ID trực tiếp từ dòng lệnh (vd: `2606.20734-2606.25733`). |
| `--month-last YYMM=N` | Khai báo ID cuối của tháng đối với dải ID vắt qua 2 tháng (vd: `2512=25075`). |
| `--limit N` | Chỉ crawl N bài đầu tiên trong danh sách (dùng khi kiểm tra nhanh). |
| `--sample N` | Lấy N bài cách đều trên toàn dải ID (cho mẫu kiểm tra có tính đại diện cao). |
| `--corpus-dir PATH` | Thư mục lưu trữ dữ liệu (mặc định: `./data`). |
| `--batch-size 25` | Số bài trong một đợt gửi API Semantic Scholar (mặc định: 25). |
| `--backup-dir PATH` | Thư mục sao lưu (Google Drive); tự động copy dữ liệu và log sau mỗi batch. |
| `--bench-dir PATH` | Thư mục lưu trữ log phiên benchmark (mặc định: `<corpus-dir>/benchmark`). |
| `--no-discovery-cache` | Tắt tính năng lưu/đọc `discovery_cache.json` (không khuyến nghị). |
| `--force-refs` | Ép buộc gọi lại Semantic Scholar ngay cả khi bài đã có `refs.json`. |
| `--skip-tables` | Bỏ qua bước xuất `papers.parquet` và `references.parquet`. |

#### Cơ chế khôi phục (Resume)

Chỉ cần chạy lại đúng câu lệnh cũ. Hệ thống tự động bỏ qua các phần việc đã hoàn thành:

- Version đã có `vN/metadata.json` sẽ không tải lại source.
- Bài báo đã có đủ `refs.json` và `refs_raw.json` sẽ không gọi lại Semantic Scholar API.
- ID đã có trong `discovery_cache.json` sẽ không tra cứu lại arXiv API.

---

### 6.2 Milestone 2: Phân tích & Chuẩn hóa LaTeX (`src/run_m2.py`)

Sau khi M1 thu thập xong source TeX và references, M2 tiến hành trích xuất cấu trúc và nội dung:

```bash
# Chạy đầy đủ cả 3 bước (bib -> tree -> content) trên toàn bộ corpus:
python src/run_m2.py --corpus-dir ./data

# Chạy M2 và tự động xuất các bảng Parquet vào data/export/:
python src/run_m2.py --corpus-dir ./data --export

# Chỉ chạy xuất Parquet từ kết quả đã parse xong trước đó (không parse lại):
python src/run_m2.py --corpus-dir ./data --export-only
```

#### Các tùy chọn linh hoạt cho M2

```bash
# Chỉ chạy riêng lẻ bước xử lý BibTeX (bước 1):
python src/run_m2.py --steps bib

# Chỉ chạy bước dựng cây cấu trúc (bước 2):
python src/run_m2.py --steps tree

# Chỉ chạy bước làm sạch và tách câu văn bản (bước 3):
python src/run_m2.py --steps content

# Chỉ chạy thử trên một vài ID bài báo cụ thể:
python src/run_m2.py --ids 2310.12345 2606.20734

# Bỏ qua bài đã xong cả 3 bước (chạy tiếp tục khi bị dừng). Bài thiếu bất kỳ đầu ra nào
# (m2_stats.json / tree_raw.json của mọi version / elements.json) sẽ được xử lý lại:
python src/run_m2.py --skip-existing

# Xử lý song song bằng đa tiến trình (mặc định: số CPU - 2, tăng tốc 6x-8x):
python src/run_m2.py --workers 8 --skip-existing --export
```

#### Ba bước xử lý của M2 (`--steps bib,tree,content`)

1. **`bib` (P2):** Quét toàn bộ file `.bib` và khối `\begin{thebibliography}` / `\bibitem` trong tất cả file `.tex`. Chuẩn hóa tiêu đề, tác giả, năm; gộp các mục trùng giữa các version; sinh `cite_map.json` ánh xạ các citation key cũ về canonical key; sinh `candidates.json` cho M3.
2. **`tree` (P1):** Tìm file TeX chính (`\documentclass`), phân giải đệ quy `\input` và `\include`, mở rộng macro người dùng (`m2_cleanup.expand_user_macros`), xây dựng cây cấu trúc phân cấp tài liệu lưu vào `tree_raw.json`.
3. **`content` (P3):** Duyệt cây cấu trúc, làm sạch văn bản (loại bỏ comment TeX, chuẩn hóa toán inline `$...$` và toán khối `\begin{equation}...\end{equation}`), tách câu (`m2_sentences.py`), đổi tên các lệnh `\cite{...}` theo `cite_map.json`, khử trùng lặp nội dung chéo version và ghi ra `elements.json` cùng `hierarchy.json`.
4. **`export` (P4):** Kiểm tra tính toàn vẹn dữ liệu (`validate_m2_integrity`) và xuất 3 bảng Parquet vào `<corpus>/export/`:
   - `elements.parquet`: `element_id, arxiv_id, kind, content`
   - `hierarchy.parquet`: `arxiv_id, version, element_id, parent_id` (nút gốc có `parent_id` rỗng được lược bỏ theo đúng đặc tả)
   - `bibtex.parquet`: `arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url`

---

### 6.3 Milestone 3: Ghép nối trích dẫn (`src/run_m3.py`)

- **Đầu vào:** `data/export/bibtex.parquet`, `data/references.parquet`, `data/export/elements.parquet`, và file nhãn trong `data/labels/`.
- **Dữ liệu nhãn:**
  - `data/labels/manual_labels.csv` và `data/labels/auto_labels.csv`.
  - Cột: `arxiv_id, citation_key, groundtruth_arxiv_id` (để trống nếu không tìm thấy arXiv ID tương ứng).
  - Tối thiểu 100 cặp nhãn gán tay từ ít nhất 5 bài báo (mỗi bài ≥ 20 mục BibTeX).
  - Nhãn tự động được sinh cho ≥ 10% số bài báo còn lại dựa trên đối sánh chính xác tiêu đề và tác giả.
- **Quy trình M3:**
  1. Sinh danh sách ứng viên (candidate pairs) giữa BibTeX entries và Semantic Scholar references (`src/matching/m3_candidates.py`).
  2. Tính toán đặc trưng chuỗi (`src/matching/m3_string_features.py`) và đặc trưng ngữ cảnh từ các câu chứa `\cite` trong `elements` (`src/matching/m3_context_features.py`).
  3. Huấn luyện bộ phân lớp xếp hạng (`src/matching/m3_model.py`) và đánh giá chất lượng bằng chỉ số **MRR@5** (`src/matching/m3_evaluate.py`).
  4. Đóng gói và upload toàn bộ 6 bảng Parquet cùng predictions lên Hugging Face Hub (`src/matching/m3_publish_hf.py`).

---

## 7. Kiểm thử & Đảm bảo chất lượng (QA)

Dự án sở hữu bộ kiểm thử tự động toàn diện với **164 tests** (10 file) được tổ chức khoa học trong `src/tests/`.

```bash
# Chạy toàn bộ test suite từ thư mục gốc:
pytest src/tests -q

# Chạy kiểm thử riêng cho M1:
pytest src/tests/test_run_m1_downloader.py src/tests/test_m1_references.py src/tests/test_m1_metadata.py -v

# Chạy kiểm thử riêng cho M2:
pytest src/tests/test_m2_pipeline.py src/tests/test_m2_hierarchy.py src/tests/test_m2_text.py src/tests/test_m2_bibliography.py src/tests/test_m2_export_parquet.py -v
```

Mọi bài test đều tự động cô lập thư mục dữ liệu qua `tmp_path`, đảm bảo không làm bẩn hoặc thay đổi dữ liệu thật trong `data/`.

---

## 8. Quy tắc làm việc trên Git

1. **Không commit trực tiếp lên nhánh `main`:**
   - Mỗi tính năng/nhiệm vụ được thực hiện trên một nhánh riêng: `p1/m1-download`, `p2/m2-bibitem`, `p3/m2-cleanup`, `p4/m2-parquet`...
2. **Quy ước đặt thông điệp commit:**
   - Commit message bắt buộc có tiền tố phân đoạn:
     - `m1: ...` cho các thay đổi liên quan đến crawler, Semantic Scholar, bảng M1.
     - `m2: ...` cho parser, BibTeX, hierarchy, text cleaning, parquet M2.
     - `m3: ...` cho candidate generation, feature, model, evaluation.
     - `docs: ...` cho cập nhật tài liệu kỹ thuật.
     - `test: ...` hoặc `fix: ...` cho bổ sung kiểm thử và sửa lỗi.
3. **Quy trình Pull Request và Buddy Review:**
   - Mọi Pull Request trước khi merge vào `main` bắt buộc phải có ít nhất 1 buddy review:
     - **P1** review chéo với **P3** (Source & Structure ↔ Text & Model).
     - **P2** review chéo với **P4** (References & BibTeX ↔ Data & Delivery).
   - Đảm bảo 100% tests chạy thành công (`pytest src/tests`) trước khi merge.
4. **Tuyệt đối bảo mật:**
   - Không commit dữ liệu (`data/`, `corpus/`, `*.parquet`, `*.csv`).
   - Không commit thông tin nhạy cảm (`.env`, `S2_API_KEY`).

---

## 9. Xử lý sự cố thường gặp (Troubleshooting)

| Hiện tượng / Thông báo lỗi | Nguyên nhân gốc rễ | Cách xử lý dứt điểm |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'utils'` hoặc `'crawler'` | Chạy python từ thư mục con hoặc chưa nhận diện `src/`. | Luôn chạy lệnh từ **thư mục gốc repo** bằng `python src/run_m1.py` hoặc `python src/run_m2.py`. Đã có `pytest.ini` cấu hình sẵn `pythonpath = src`. |
| `HTTP 403 Forbidden` từ arXiv | Vi phạm chính sách crawler (quá nhiều kết nối song song, giãn cách < 3s, hoặc sai host). | Giữ nguyên mặc định kết nối tuần tự và `MIN_DELAY_SECONDS = 3.0`. Đảm bảo `BASE_URL` sử dụng `export.arxiv.org`. |
| `HTTP 429 Too Many Requests` từ Semantic Scholar | Gửi request quá nhanh khi không có API key. | Đăng ký API key miễn phí tại Semantic Scholar và cấu hình biến `S2_API_KEY`. Mã nguồn đã tích hợp sẵn cơ chế lũy thừa giãn nhịp (exponential backoff) và tự thử lại. |
| `Thiếu ID cuối của tháng 2512` | Khoảng ID đi qua 2 tháng nhưng chưa khai báo ID cuối tháng trước. | Bổ sung tham số `--month-last 2512=25075` vào lệnh chạy `run_m1.py`. |
| Cảnh báo `User-Agent mặc định` | Chưa cấu hình email liên hệ cho crawler. | Khai báo `export CONTACT_EMAIL="email_cua_ban@domain.com"` trong môi trường hoặc `.env`. |
| Bản ghi rỗng không có file `.tex` trong `vN/` | Bài báo chỉ xuất bản định dạng PDF trên arXiv. | Đây là đặc điểm bình thường của dữ liệu. M1 tạo thư mục `vN/` rỗng kèm `metadata.json`, M2 tự động ghi nhận là bản ghi rỗng theo đúng yêu cầu của đề. |
| Mất checkpoint khi mở phiên Colab mới | Chưa sao lưu dữ liệu phiên trước vào Google Drive. | Luôn truyền cờ `--backup-dir /content/drive/MyDrive/...` và thực hiện khôi phục dữ liệu ở đầu mỗi notebook Colab. |
| Cảnh báo tính toàn vẹn Parquet (`validate_m2_integrity`) | Có `element_id` trong hierarchy không tồn tại trong `elements.json`. | Chạy lại `python src/run_m2.py --steps tree,content --export` để đảm bảo cây phân cấp và danh sách phần tử văn bản được đồng bộ cùng nhịp. |
