# arXiv Data Crawling & Processing Pipeline

Pipeline thu thập và xử lý bài báo arXiv (LaTeX source + references) cho đồ án Nhập môn Khoa học Dữ liệu.

## 📋 Tổng quan

**Milestone 1 (M1)**: Crawl arXiv (mọi version) + references (Semantic Scholar) + benchmark

Với mỗi bài báo, chương trình:
1. Tra metadata và số version qua **arXiv API**
2. Tải source từng version, **chỉ giữ `.tex` và `.bib`** (bỏ hình ảnh)
3. Lấy references qua **Semantic Scholar API** (batch, song song với tải source)
4. Dựng bảng Parquet `papers` và `references` (đúng schema đề)
5. Đo hiệu năng (thời gian, RAM, đĩa) theo từng phiên và tổng hợp

**Đặc điểm:**
- ✅ Một cây thư mục duy nhất (corpus)
- ✅ Resume an toàn: checkpoint theo version/bài/ID
- ✅ Nhiều phiên Colab: log riêng mỗi phiên + tổng hợp tự động
- ✅ Backup thông minh: tarball (nhanh 10-20x so với rsync)
- ✅ Cache discovery để không crawl lại từ đầu

---

## 🚀 Quick Start

### Google Colab (Khuyến nghị)

**Lý do**: Miễn phí, RAM cao, kết nối nhanh, backup an toàn lên Drive, restore nhanh.

**3 bước cơ bản:**

1. **Setup**: Upload `src.zip` + `ID.txt` lên Colab, cài dependencies
2. **Restore**: Giải nén backup từ Drive (2-5 phút cho 5K papers)
3. **Run M1**: Chạy với backup tự động mỗi batch

Xem chi tiết: **[Section 3 - Chạy trên Colab](#3-chạy-trên-google-colab)**

### Local

```bash
# Setup
git clone <repo> && cd <repo>
pip install -r src/requirements.txt
export CONTACT_EMAIL="your@email.com"
export S2_API_KEY="your_key"

# Chạy thử
printf "1706.03762\n1810.04805\n1512.03385\n" > ids_test.txt
python src/run_m1.py --ids-file ids_test.txt --corpus-dir ./data_test

# Chạy full
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 \
    --corpus-dir ./data --backup-dir ./backup
```

---

## 1. Cài đặt

### Yêu cầu
- Python 3.10+
- Dependencies: xem `src/requirements.txt`

### Biến môi trường

| Biến | Bắt buộc | Mô tả |
|------|----------|-------|
| `CONTACT_EMAIL` | ✅ Có | Email thật cho User-Agent (arXiv yêu cầu) |
| `S2_API_KEY` | ⚠️ Nên có | API key Semantic Scholar (miễn phí, tránh 429) |
| `ARXIV_DATA_DIR` | ❌ Không | Thư mục dữ liệu mặc định (có thể dùng `--corpus-dir`) |

**Cách đặt:**

```bash
# Linux/Mac
export CONTACT_EMAIL="your@email.com"
export S2_API_KEY="your_key_here"

# Hoặc tạo file .env ở thư mục gốc
echo 'CONTACT_EMAIL=your@email.com' > .env
echo 'S2_API_KEY=your_key_here' >> .env

# Colab: Dùng Secrets
from google.colab import userdata
os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")
```

### Cấu trúc code

```
src/
├── run_m1.py              # Entry point chính
├── crawler/
│   ├── m1_download.py     # Tải + trích .tex/.bib từ archive
│   ├── m1_references.py   # Gọi Semantic Scholar API
│   ├── m1_tables.py       # Dựng papers/references.parquet
│   └── m1_aggregate.py    # Gộp log nhiều phiên
├── utils/
│   ├── config.py          # Đường dẫn, constants
│   ├── schemas.py         # PyArrow schemas
│   └── benchmark.py       # Đo hiệu năng (thời gian, RAM, đĩa)
└── tests/                 # Test suite
```

---

## 2. Chuẩn bị danh sách ID

File `--ids-file` (ví dụ `ID.txt`): mỗi dòng là **một ID** hoặc **một khoảng ID**.

**Format hợp lệ:**

```text
# ID đơn
2310.12345
1706.03762
hep-th/9901001

# Khoảng ID (hai dạng viết)
260204870-260209869          # Viết liền: YYMM + 5 chữ số
2602.04870-2602.09869        # Viết có dấu chấm

# Comment
# Dòng bắt đầu bằng # bị bỏ qua
```

**Khoảng ID qua nhiều tháng** cần `--month-last`:

```bash
# Ví dụ: 2512.23231 → 2601.03155 (tổng 5000 IDs)
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075
```

**Tính ID cuối tháng:**
- Công thức: `ID_cuối_tháng = ID_đầu + (5000 - số_ID_tháng_sau - 1)`
- Ví dụ: 23231 + (5000 - 3155 - 1) = 25075
- Verify: `2512.25075` phải tồn tại, `2512.25076` không

**Chạy thử với sample:**

```bash
# 50 ID đầu
python src/run_m1.py --ids-file ID.txt --limit 50

# 50 ID cách đều (đại diện hơn)
python src/run_m1.py --ids-file ID.txt --sample 50
```

---

## 3. Chạy trên Google Colab

### 🎯 Lý do nên dùng Colab

| Ưu điểm | Chi tiết |
|---------|----------|
| **Miễn phí** | 12GB RAM, GPU/TPU, kết nối tốc độ cao |
| **Backup an toàn** | Tự động lên Google Drive, không mất dữ liệu |
| **Resume dễ dàng** | Checkpoint mỗi batch, tiếp tục sau 12h ngắt |
| **Restore nhanh** | Tarball: 2-5 phút thay vì 30-60 phút (rsync) |

### 📋 Workflow 3 ô

Copy code dưới đây vào 3 ô Colab riêng biệt:

#### **Ô 1: Setup môi trường**

```python
# Cài đặt dependencies
!unzip -q -o src.zip
!pip install -q arxiv requests psutil pandas pyarrow python-dotenv

# Mount Google Drive
from google.colab import drive, userdata
import os

drive.mount('/content/drive')

# Cấu hình (THAY ĐỔI email của bạn)
os.environ["CONTACT_EMAIL"] = "your@email.com"
os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")

# Đường dẫn
BACKUP = "/content/drive/MyDrive/arxiv_project/backup"
CORPUS = "/content/corpus"
LOGS = "/content/drive/MyDrive/arxiv_project/logs"

!mkdir -p {CORPUS} {LOGS} {BACKUP}

print("✓ Setup hoàn tất")
```

#### **Ô 2: Restore từ backup**

```python
import tarfile
from pathlib import Path
import time
import json

print("🔍 Checking for backups...\n")

# 1. Restore critical cache (discovery + benchmark logs) - Ưu tiên cao
critical_tar = Path(f"{BACKUP}/critical_cache.tar.gz")
if critical_tar.exists():
    print("📦 Restoring critical cache...")
    start = time.time()
    !tar -xzf {critical_tar} -C {CORPUS}/ 2>/dev/null
    print(f"✓ Done in {time.time()-start:.1f}s\n")
else:
    print("ℹ️  No critical cache (first run)\n")

# 2. Restore papers từ các batch
paper_tars = sorted(Path(BACKUP).glob("papers_batch_*.tar.gz"))
if paper_tars:
    print(f"📦 Restoring {len(paper_tars)} paper batches...")
    start = time.time()
    for tar_file in paper_tars:
        !tar -xzf {tar_file} -C {CORPUS}/ 2>/dev/null
    print(f"✓ Done in {time.time()-start:.1f}s\n")
else:
    print("ℹ️  No paper backups (first run)\n")

# 3. Restore benchmark logs
bench_tar = Path(f"{BACKUP}/benchmark_logs.tar.gz")
if bench_tar.exists():
    !tar -xzf {bench_tar} -C {CORPUS}/ 2>/dev/null
    print("✓ Benchmark logs restored\n")

# 4. Status check
print("="*50)
print("STATUS")
print("="*50)

cache_file = Path(f"{CORPUS}/discovery_cache.json")
if cache_file.exists():
    cache = json.loads(cache_file.read_text())
    n_cached = len(cache.get("info", {}))
    print(f"✅ Discovery cache: {n_cached} IDs")
else:
    print("⚠️  No discovery cache")

paper_dirs = [d for d in Path(CORPUS).iterdir() 
              if d.is_dir() and not d.name.startswith(".") and d.name != "benchmark"]
print(f"✅ Papers crawled: {len(paper_dirs)}")

bench_dir = Path(f"{CORPUS}/benchmark")
if bench_dir.exists():
    sessions = list(bench_dir.glob("session_*.json"))
    print(f"✅ Previous sessions: {len(sessions)}")
    if sessions:
        latest = max(sessions, key=lambda p: p.stat().st_mtime)
        data = json.loads(latest.read_text())
        cov = data.get('coverage', {})
        crawled = cov.get('crawled_versions', 0)
        expected = cov.get('expected_versions', 0)
        if expected:
            pct = crawled/expected*100
            print(f"   Progress: {crawled}/{expected} ({pct:.1f}%)")

print(f"\n{'='*50}")
print("🚀 Ready to run M1!")
```

**So sánh tốc độ restore:**

| Phương pháp | 5,000 papers | Cải thiện |
|-------------|--------------|-----------|
| rsync (cũ) | 30-60 phút | - |
| **tarball (mới)** | **2-5 phút** | **10-20x** |

#### **Ô 3: Chạy M1 với backup tự động**

```python
import datetime
import subprocess

# Tạo log file với timestamp
timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
log_file = f"{LOGS}/run_m1_{timestamp}.log"

print(f"🚀 Starting M1 at {timestamp}")
print(f"📝 Log: {log_file}")
print(f"💾 Backup: {BACKUP}\n")

# Command
cmd = [
    "python", "src/run_m1.py",
    "--ids-file", "ID.txt",
    "--month-last", "2512=25075",  # THAY ĐỔI theo dataset của bạn
    "--corpus-dir", CORPUS,
    "--backup-dir", BACKUP,
    "--batch-size", "20"  # Giảm xuống để backup thường xuyên hơn
]

# Chạy và ghi log
with open(log_file, 'w', encoding='utf-8') as f:
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    for line in process.stdout:
        print(line, end='')
        f.write(line)
        f.flush()
    
    process.wait()

print(f"\n{'='*60}")
print(f"✓ Completed with exit code: {process.returncode}")
```

**Backup tự động:**
- **Discovery cache**: Backup ngay sau discovery (~5 giây)
- **Papers**: Backup sau mỗi batch 20 papers (~30-60 giây/batch)
- **Benchmark logs**: Checkpoint mỗi phút

### 📊 Workflow nhiều phiên

```
Phiên 1: Discovery (500 IDs) → Backup cache → Crawl 100 papers → Ngắt (8h)
          ↓ save to Drive
Phiên 2: Restore (5s) → Skip 100 done → Crawl next 100 → Ngắt (9h)
          ↓ save to Drive
Phiên 3: Restore (5s) → Skip 200 done → Crawl final 50 → Complete (5h)

Kết quả: benchmark_m1_total.json (tổng hợp 3 phiên, 22h)
```

### 🔍 Ô phụ: Kiểm tra tiến độ

```python
# Chạy ô này ở tab khác để xem progress realtime (không làm gián đoạn crawl)
import json
from pathlib import Path

bench_dir = Path(f"{CORPUS}/benchmark")
if bench_dir.exists():
    sessions = sorted(bench_dir.glob("session_*.json"))
    if sessions:
        latest = sessions[-1]
        data = json.loads(latest.read_text())
        
        print(f"📊 Session {data['session_index']}: {data['status']}")
        
        cov = data.get('coverage', {})
        crawled = cov.get('crawled_versions', 0)
        expected = cov.get('expected_versions', 0)
        if expected:
            print(f"📈 Progress: {crawled}/{expected} ({crawled/expected*100:.1f}%)")
        
        stages = data.get('stages_s', {})
        if stages:
            print(f"\n⏱️  Time spent:")
            for stage, sec in stages.items():
                print(f"   {stage}: {sec/60:.1f} min")
        
        print(f"\n💾 Resources:")
        print(f"   Peak RAM: {data.get('peak_ram_mb', 0):.0f} MB")
        print(f"   Peak Disk: {data.get('peak_disk_mb', 0):.0f} MB")
```

### ✅ Verify trước khi nộp

```bash
python scripts/verify_m1_completion.py --ids-file ID.txt --corpus-dir /content/corpus
```

**Output:**
```
✅ Discovery: OK
✅ Papers: 19998/20000 (99.99% coverage)
✅ Benchmark: 3 sessions, completed
✅ Tables: papers.parquet (245 MB), references.parquet (129 MB)

🎉 SẴN SÀNG NỘP BÀI!
```

---

## 4. Chạy local

```bash
# Chạy thử 3 bài
printf "1706.03762\n1810.04805\n1512.03385\n" > ids_test.txt
python src/run_m1.py --ids-file ids_test.txt --corpus-dir ./data_test

# Chạy full với backup
python src/run_m1.py --ids-file ID.txt --month-last 2512=25075 \
    --corpus-dir ./data --backup-dir ./backup --batch-size 20
```

### Options

| Flag | Mô tả |
|------|-------|
| `--ids-file PATH` | File chứa danh sách ID (bắt buộc) |
| `--corpus-dir PATH` | Thư mục dữ liệu đầu ra |
| `--backup-dir PATH` | Thư mục backup (Google Drive) |
| `--month-last YYMM=N` | ID cuối của tháng (cho khoảng ID qua nhiều tháng) |
| `--batch-size N` | Số papers/batch (mặc định 25, giảm để backup thường xuyên) |
| `--limit N` | Chỉ lấy N ID đầu (test) |
| `--sample N` | Lấy N ID cách đều (test đại diện hơn) |
| `--no-discovery-cache` | Không dùng cache discovery (mất resume) |
| `--force-refs` | Gọi lại Semantic Scholar kể cả đã có refs |
| `--skip-tables` | Không dựng Parquet tables |

### Resume tự động

Chạy lại **đúng lệnh cũ**, chương trình tự động skip:
- Version có `vN/metadata.json` → không tải lại source
- Bài có `refs.json` + `refs_raw.json` → không gọi lại Semantic Scholar
- ID trong `discovery_cache.json` → không hỏi lại arXiv API

Version lỗi không có checkpoint → tự động retry.

---

## 5. Cấu trúc dữ liệu đầu ra

```
corpus/                              # = --corpus-dir
├── <paper_key>/                     # VD: 2310-12345, hep-th-9901001
│   ├── paper.json                   # Metadata bài
│   ├── refs.json                    # References đã chuẩn hóa
│   ├── refs_raw.json                # Raw response Semantic Scholar
│   ├── v1/
│   │   ├── *.tex, *.bib             # Chỉ giữ .tex và .bib
│   │   ├── metadata.json            # Checkpoint + stats
│   │   └── references.json          # Copy refs cho version
│   └── v2/ ...
├── discovery_cache.json             # Cache arXiv API (resume)
├── papers.parquet, papers.csv       # Bảng papers
├── references.parquet, references.csv # Bảng references (chỉ có arXiv ID)
├── refs_failures.jsonl              # Bài lỗi Semantic Scholar
└── benchmark/
    ├── session_001.json             # Log từng phiên (GIỮ LẠI)
    ├── session_002.json
    ├── benchmark_m1_total.json      # Tổng hợp (dùng cho báo cáo)
    └── sessions.csv                 # Bảng từng phiên
```

### Schema chính

**papers.parquet:**
```
arxiv_id, title, authors (list), submission_date, revised_dates (list), venue
```

**references.parquet:**
```
arxiv_id (bài trích dẫn), ref_arxiv_id, ref_title, ref_authors (list), 
ref_submission_date, ref_s2_id
```

Chỉ chứa references **có arXiv ID** (theo đề).

### Benchmark metrics

**`benchmark_m1_total.json`** (tổng hợp nhiều phiên):

| Nhóm | Metrics |
|------|---------|
| **Thời gian** | `total_wall_s`, `stages_s` (discovery/crawl/backup/tables), `avg_paper_s`, `max_paper_s` |
| **Thành công** | `success_rate`, `ref_success_rate`, `avg_refs_per_paper`, `avg_arxiv_refs_per_paper` |
| **Kích thước** | `avg_paper_size_before_bytes` (có hình), `avg_paper_size_after_bytes` (chỉ tex/bib) |
| **Tài nguyên** | `peak_ram_mb`, `avg_ram_mb`, `peak_disk_mb`, `final_output_mb` |
| **Phiên** | `n_sessions`, `sessions` (chi tiết từng phiên) |

**Quy tắc gộp nhiều phiên:**
- Thời gian: **Cộng** (không tính thời gian nghỉ giữa các phiên)
- RAM/Disk đỉnh: **Max** qua các phiên
- Tỉ lệ thành công, kích thước: **Lấy từ phiên hoàn tất cuối** (vì đã duyệt hết IDs)

---

## 6. Ước lượng thời gian

> Đây là ước tính, có thể khác thực tế. Chạy thử 3 bài để verify.

**Nút thắt**: arXiv rate limit (1 request/3 giây, 1 connection)

**Công thức**:
```
Tổng ≈ (số version) × max(3s, thời gian tải) + discovery
```

**Benchmark năm ngoái** (theo thầy):
- 5,000 IDs: ~7-8 giờ (trung vị các nhóm)
- 20,000 IDs: **28-45 giờ** (ước tính, ~3-4 phiên Colab)
- Discovery: ~1 giờ

**Giả định**: ~1.6 versions/paper → 20,000 IDs = ~32,000 versions

---

## 7. Tối ưu hóa

### ✅ Đã làm

| Kỹ thuật | Lợi ích |
|----------|---------|
| **Song song Semantic Scholar** | Gọi batch refs trong lúc chờ tải source |
| **Khoảng cách 3s tính từ bắt đầu request** | Giải nén/ghi file nằm trong thời gian chờ |
| **Discovery cache** | Không phải discovery lại khi resume |
| **Checkpoint theo version/bài** | Resume chính xác, không lặp công việc |
| **Tarball backup** | Restore nhanh 10-20x (2-5 phút vs 30-60 phút) |
| **Backup cache ngay sau discovery** | Không mất cache khi ngắt sớm |

### ❌ Không làm (vi phạm arXiv ToS hoặc lợi ích thấp)

- ❌ Giảm khoảng cách < 3 giây
- ❌ Tải song song nhiều version
- ❌ Đổi IP / chia ID cho nhiều máy
- ❌ Bất kỳ kỹ thuật nào bypass rate limit

**Kết luận**: Tốc độ đã gần tối ưu với giới hạn của arXiv.

---

## 8. Xử lý lỗi

| Lỗi | Nguyên nhân / Giải pháp |
|-----|------------------------|
| `ModuleNotFoundError: utils` | Chạy từ thư mục gốc: `python src/run_m1.py` |
| `HTTP 403 từ arXiv` | Sai host hoặc request quá dày, check `BASE_URL` và delay |
| `HTTP 429 từ Semantic Scholar` | Đặt `S2_API_KEY`, code tự retry |
| `version_coverage < 1` | Xem log lỗi, chạy lại để retry version lỗi |
| `not_found_papers > 0` | ID không tồn tại trên arXiv (bị xóa/chưa có), không phải lỗi mạng |
| Phiên mới không thấy dữ liệu cũ | Chưa restore từ `--backup-dir` (xem Ô 2) |

---

## 9. Files quan trọng nộp bài

1. `corpus/benchmark/benchmark_m1_total.json` - Số liệu tổng hợp
2. `corpus/benchmark/sessions.csv` - Chi tiết từng phiên
3. `corpus/papers.parquet` - Bảng papers
4. `corpus/references.parquet` - Bảng references
5. Logs từ Google Drive: `logs/run_m1_*.log`

---

## ⚖️ Quy tắc arXiv

**Đã tuân thủ trong code:**
- ✅ Chỉ dùng `export.arxiv.org`
- ✅ Một kết nối duy nhất
- ✅ Khoảng cách tối thiểu 3 giây giữa các request
- ✅ User-Agent có email liên hệ

**KHÔNG sửa các thông số này** để chạy nhanh hơn → Vi phạm ToS → Bị 403 → Fail project.
