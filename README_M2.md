# Milestone 2: Parse LaTeX, chuẩn hóa và khử trùng

Đầu vào là corpus của M1 (mã nguồn `.tex`/`.bib` từng version). Mỗi bài, M2:

1. **Gom file** theo đường biên dịch: tìm file gốc có `\documentclass`, đi theo `\input`/`\include`, bỏ phần bị comment và các file thừa.
2. **[TẦNG 1] Bung macro và môi trường** tự định nghĩa (macro do tác giả định nghĩa trong preamble).
3. **Dựng cây phân cấp** cho từng version: Document → Section → … → lá (câu, công thức khối, hình/bảng). `itemize` rẽ nhánh theo từng mục. Bỏ References, giữ Acknowledgements và Appendix.
4. **[TẦNG 2] Làm sạch và chuẩn hóa:** bảo vệ nội dung quan trọng, phân loại lệnh theo bảng, bóc vỏ dự phòng cho lệnh lạ, đưa toán inline về `$...$` và toán khối về `\begin{equation}...\end{equation}`.
5. **Khử trùng nội dung xuyên version:** nội dung giống hệt (sau khi làm sạch) dùng chung **một** `element_id`.
6. **Trích BibTeX:** đọc `.bib` và chuyển `\bibitem` sang BibTeX, khử trùng giữa các version (hợp nhất trường, chọn một khóa chung, đổi `\cite`), rồi sinh cặp ứng viên (BibTeX, reference) cho M3.

Đầu ra cuối là ba bảng Parquet `elements`, `hierarchy`, `bibtex` đúng schema của đề. M2 không gọi mạng; toàn bộ chạy trên CPU.

## 🆕 Kiến trúc Hai Tầng Xử Lý LaTeX

M2 đã được nâng cấp với kiến trúc hai tầng để xử lý tốt hơn các lệnh LaTeX lạ:

### **Tầng 1: Bung Macro** (`m2_macro_expansion.py`)
Chạy **sau khi gom file**, **trước khi dựng cây**, riêng cho từng version.

**Chức năng:**
- Thu thập và bung macro: `\newcommand`, `\renewcommand`, `\def`, `\DeclareMathOperator`
- Bung môi trường: `\newenvironment`, `\newtheorem`
- Xử lý điều kiện: bỏ `\iffalse...\fi` và môi trường `comment`
- Viết tắt phổ biến: `\ie` → `i.e.`, `\eg` → `e.g.`, `\etal` → `et al.`
- Bỏ qua macro phức tạp (`\csname`, `\expandafter`, `\catcode`)

### **Tầng 2: Bảo vệ, Phân loại, Bóc vỏ** (`m2_cleanup.py`)
Chạy trong `clean_text()` (bước content).

**Quy trình:**
1. **Bảo vệ:** toán inline, `\cite{}`, `\ref{}`, URL, verbatim
2. **Phân loại theo bảng:** DROP (có/không đối số), UNWRAP, SPACE, KÝ HIỆU
3. **Bóc vỏ dự phòng:** lệnh chưa biết → xét đối số:
   - Kích thước (`3pt`, `\textwidth`) → xóa
   - Chữ thuần → unwrap (giữ chữ)
   - Phức tạp → xóa
   - Không đối số → xóa
4. **Ghi lại:** bộ đếm `unknown_commands` → file `benchmark/unknown_commands.json`

**Lợi ích:**
- ✅ Giảm lệnh LaTeX sót
- ✅ Dễ mở rộng (thêm vào bảng)
- ✅ Bảo vệ `\cite`, `\ref`, toán
- ✅ Thống kê lệnh lạ cho vòng lặp cải tiến

📖 **Chi tiết:** Xem [docs/CHANGELOG_M2_IMPROVEMENT.md](docs/CHANGELOG_M2_IMPROVEMENT.md)

---

## 1. Các file của M2

```text
src/
├── run_m2.py                           # điều phối: chạy 3 bước cho từng bài, đo hiệu năng, xuất Parquet
├── parser/
│   ├── m2_gather.py                    # tìm file gốc, theo \input/\include, danh sách file thừa
│   ├── m2_macro_expansion.py           # **[TẦNG 1]** bung macro và môi trường tự định nghĩa
│   ├── m2_hierarchy.py                 # dựng cây thô -> vN/tree_raw.json
│   ├── m2_cleanup.py                   # **[TẦNG 2]** làm sạch LaTeX, chuẩn hóa toán, bóc vỏ dự phòng
│   ├── m2_sentences.py                 # tách câu (hàm dùng chung)
│   ├── m2_content_dedup.py             # cây thô -> elements.json, hierarchy.json, parse_status.json
│   ├── m2_bibitem.py                   # đọc .bib và chuyển \bibitem thành BibTeX
│   ├── m2_bib_dedup.py                 # khử trùng BibTeX, chọn khóa, đổi \cite, dòng bảng bibtex
│   └── export_unknown_commands.py      # phân tích bộ đếm lệnh lạ
├── matching/m3_candidates.py           # sinh cặp ứng viên (run_m2.py gọi)
├── utils/{config,schemas,benchmark}.py
└── tests/{test_m2_pipeline,test_m2_bibliography}.py
```

Thứ tự trong `run_m2.py` cho mỗi bài là **bib → tree → content**. Bước `bib` phải chạy trước vì nó tạo `cite_map.json`, mà bước `content` dùng để đổi `\cite` trước khi khử trùng.

## 2. Cài đặt

Yêu cầu Python **3.10 trở lên** (thư mục `parser/` trùng tên với module `parser` cũ của Python < 3.10).

```bash
pip install -r src/requirements.txt pytest
```

M2 cần `pandas`, `pyarrow`, `psutil` (đã có trong `requirements.txt`). Trên Windows đặt `PYTHONUTF8=1` (PowerShell: `$env:PYTHONUTF8="1"`) để in tiếng Việt không lỗi.

## 3. Chạy

Luôn chạy từ **thư mục gốc repo**. `--corpus-dir` là thư mục dữ liệu M1 (chứa các thư mục `<paper_key>/`).

```bash
# Thử vài bài trước (đề xuất)
python src/run_m2.py --corpus-dir data_m2 --export

# Toàn bộ corpus
python src/run_m2.py --corpus-dir /content/corpus --export
```

| Tùy chọn | Tác dụng |
|---|---|
| `--steps bib,tree,content` | Chọn bước (mặc định cả ba, luôn chạy theo thứ tự bib, tree, content). |
| `--ids 2602.04870 hep-th/9901001` | Chỉ xử lý các arXiv ID này. |
| `--limit N` | Chỉ xử lý N bài đầu. |
| `--skip-existing` | Bỏ qua bài đã có `m2_stats.json` và `elements.json` (chạy tiếp sau khi bị ngắt). |
| `--export` | Ghi `elements/hierarchy/bibtex.parquet` vào `<corpus>/export/`. |

Một bài lỗi không làm dừng cả lượt: lỗi được ghi vào kết quả của bài đó (`error`) và tính là thất bại trong tỉ lệ thành công.

### Xem cây của một bài

Hai lệnh này đọc thư mục dữ liệu qua biến môi trường `ARXIV_DATA_DIR`:

```bash
export ARXIV_DATA_DIR=data_m2          # PowerShell: $env:ARXIV_DATA_DIR = "data_m2"
python src/parser/m2_hierarchy.py --ids 2602.04870 --outline    # in cây từng version
python src/parser/m2_gather.py --ids 2602.04870 --gather --verbose   # file gốc và các file trên đường biên dịch
```

### Phân tích lệnh lạ (Unknown Commands)

Sau khi chạy M2, xem thống kê lệnh lạ để cải tiến:

```bash
python src/parser/export_unknown_commands.py --corpus-dir data_m2 --top 50
```

File `benchmark/unknown_commands.json` chứa bộ đếm các lệnh LaTeX chưa được xử lý. Nếu file rỗng `{}`, nghĩa là **tất cả lệnh đã được xử lý** (kết quả tốt!).

**Vòng lặp cải tiến:**
1. Xem top 30-50 lệnh lạ
2. Thêm vào bảng `UNWRAP_COMMANDS`, `DROP_NO_ARGS`, `DROP_WITH_ARGS` trong `m2_cleanup.py`
3. Chạy lại M2
4. So sánh kết quả

### Kiểm thử

```bash
python -m pytest src/tests -v
```

Test tự dựng corpus giả đúng định dạng M1 và kiểm tra: tìm file gốc và bỏ phần bị comment, cấu trúc cây, dedup xuyên version, ID kiểu cũ, version PDF-only, hợp nhất BibTeX theo ví dụ của đề, `\bibitem`, schema Parquet, và việc `run_m2.py` **không sửa** file nguồn của M1.

### Chạy trên Google Colab (để đo benchmark)

Đề yêu cầu đo hiệu năng M2 trên Colab CPU. Khôi phục corpus từ Drive rồi chạy:

```python
!mkdir -p /content/corpus && cp -r /content/drive/MyDrive/arxiv_project/backup/. /content/corpus/
!python src/run_m2.py --corpus-dir /content/corpus --export 2>&1 | tee m2_run.log
```

## 4. Dữ liệu vào và ra

**Vào** (do M1 tạo): `<paper_key>/paper.json` (cho arXiv ID gốc và số version), `<paper_key>/vN/*.tex|*.bib`, `<paper_key>/vN/metadata.json`, `<paper_key>/refs.json`.

**Ra**, trong mỗi thư mục bài:

```text
<paper_key>/
├── vN/tree_raw.json              # cây thô của version N (bước tree)
├── elements.json                 # bảng elements của bài (một kho chung mọi version)
├── hierarchy.json                # các cạnh nút-cha của từng version
├── parse_status.json             # trạng thái từng version + số lượng trước/sau dedup
├── bibtex_entries_raw.json       # entry BibTeX thô của mọi version (trước dedup)
├── cite_map.json                 # {khóa_bị_bỏ: khóa_được_chọn}
├── bibtex_merged.json            # dòng bảng bibtex sau khử trùng (+ trường gốc cho M3)
├── bibtex_conflicts.json         # trường mâu thuẫn khi hợp nhất (để kiểm tra)
├── candidates.json               # cặp ứng viên (BibTeX, reference có arXiv ID)
├── candidate_entry_index.json    # số ứng viên và danh sách candidate_id của từng entry
└── m2_stats.json                 # số liệu BibTeX/ứng viên/thời gian của bài
```

Ở thư mục gốc corpus: `m2_stats.json` (danh sách kết quả mọi bài), `benchmark/m2_benchmark.json`, `benchmark/unknown_commands.json` **(mới!)**, và `export/*.parquet` khi dùng `--export`.

### Trạng thái từng version (`parse_status.json`)

```json
{
  "arxiv_id": "2602.04870",
  "versions": {"v1": {"status": "ok", "n_nodes": 4}, "v2": {"status": "ok", "n_nodes": 4}},
  "n_versions": 2, "n_versions_ok": 2,
  "n_nodes_before_dedup": 8, "n_elements_after_dedup": 6
}
```

| `status` | Ý nghĩa |
|---|---|
| `ok` | Parse được, có nội dung. |
| `empty` | Không có TeX (bản ghi rỗng của M1, ví dụ `reason: pdf_only`) hoặc không có nội dung. Vẫn được ghi nhận, không bị bỏ qua. |
| `missing` | M1 biết version này tồn tại (`n_versions` trong `paper.json`) nhưng không tải được (`reason: not_downloaded`). |

Ở `tree_raw.json`, `status` còn có `no_root` (có TeX nhưng không tìm được file gốc) và `error` (lỗi khi parse). Hai trạng thái này tính là bài **parse thất bại** trong `parse_success_rate`.

### Ba bảng đầu ra

| Bảng | Cột |
|---|---|
| `elements` | `element_id`, `arxiv_id`, `kind`, `content` |
| `hierarchy` | `arxiv_id`, `version` (int), `element_id`, `parent_id` |
| `bibtex` | `arxiv_id`, `citation_key`, `entry_type`, `title`, `authors` (list), `year` (int), `doi`, `journal`, `url` |

`arxiv_id` luôn là ID **gốc** đọc từ `paper.json` (ví dụ `hep-th/9901001`), không suy ra từ tên thư mục. `kind` thuộc `document`, `heading`, `sentence`, `formula`, `figure`, `itemize`, `item`.

## 5. Cách hoạt động

### 5.1 Gom nhiều file (`m2_gather.py`)

- Chỉ xét file `.tex`, bỏ phần comment (sau `%` chưa escape).
- File gốc là file có `\documentclass`. Nếu có nhiều ứng viên thì ưu tiên: có `\begin{document}`, không phải lớp `standalone`/`subfiles`, không bị file khác `\input`/`\include`, tên như `main`, `paper`...
- Từ file gốc đi theo `\input`/`\include` đệ quy; lệnh bị comment coi là chữ thường. File không nằm trên đường này là **file thừa** và không được parse.
- `reason` mô tả độ tin cậy: `ok`, `ambiguous`, `weak_candidate`, `root_without_begin_document`, `fallback_begin_document`, `no_documentclass`, `no_tex_files`.

### 5.2 Tầng 1: Bung macro (`m2_macro_expansion.py`) **[MỚI]**

Chạy sau gom file, trước dựng cây. Xử lý riêng từng version.

1. Thu thập định nghĩa: `\newcommand`, `\def`, `\DeclareMathOperator`, `\newenvironment`, `\newtheorem`
2. Xóa các dòng định nghĩa
3. Bung lặp (tối đa 10 vòng, có giới hạn kích thước)
4. Không bung trong `\cite{}`, `\ref{}`, `\label{}`
5. Bỏ qua định nghĩa phức tạp (`\csname`, `\expandafter`)
6. Xử lý điều kiện: bỏ `\iffalse...\fi` và môi trường `comment`
7. Bung viết tắt phổ biến: `\ie`, `\eg`, `\etal`

**Ví dụ:**
```latex
% Input:
\newcommand{\method}[1]{\texttt{#1}}
The \method{FooNet} achieves high accuracy.

% Output sau Tầng 1:
The \texttt{FooNet} achieves high accuracy.
```

### 5.3 Dựng cây (`m2_hierarchy.py`)

1. Mở rộng `\input`/`\include` tại chỗ thành một chuỗi LaTeX duy nhất, đúng thứ tự đọc.
2. **[TẦNG 1]** Bung macro và môi trường tự định nghĩa.
3. Lấy phần giữa `\begin{document}` và `\end{document}`.
4. Quét thành danh sách khối: heading, đoạn văn, công thức, hình/bảng, danh sách, khung `abstract`. Các lệnh đầu bài `\title`, `\author`, `\date`, `\thanks`, `\affiliation`... bị bỏ (không phải nội dung bài).
5. Lồng các heading thành cây. **Cấp heading tự nhận theo các lệnh phân mục bài thực sự dùng**: bài có `\chapter` thì chapter là cấp 1; bài chỉ có `\section`/`\subsection`/`\paragraph` thì section là cấp 1. `\section*` và phụ lục được giữ; mục References và `thebibliography` bị bỏ.

### 5.4 Tầng 2: Làm sạch và chuẩn hóa (`m2_cleanup.py`) **[CẢI TIẾN]**

**Quy trình:**

1. **Bảo vệ (mask):** thay bằng token, trả lại sau cùng
   - Toán inline (`$...$`)
   - `\cite{}`, `\ref{}`, `\eqref{}`, `\label{}`
   - URL và `\url{}`
   - Verbatim (`\verb|...|`)

2. **Phân loại theo bảng:**
   - `DROP_WITH_ARGS`: xóa cả lệnh và đối số (`\vspace`, `\bibliographystyle`, `\hypersetup`...)
   - `DROP_NO_ARGS`: xóa lệnh (`\centering`, `\noindent`, `\small`, `\midrule`...)
   - `UNWRAP_COMMANDS`: giữ đối số cuối (`\textbf`, `\emph`, `\mbox`...)
   - `UNWRAP_SECOND_ARG`: giữ đối số thứ 2 (`\textcolor{c}{x}`, `\href{u}{x}`...)
   - `SPACE_COMMANDS`: đổi thành khoảng trắng (`\quad`, `\qquad`...)
   - `SYMBOL_MAP`: đổi sang ký tự (`\&`, `\ldots`, `\'e`...)

3. **Bóc vỏ dự phòng** (lệnh chưa biết):
   - Đối số giống kích thước → xóa
   - Đối số toàn chữ → unwrap (giữ chữ)
   - Đối số phức tạp → xóa
   - Không đối số → xóa
   - **Ghi vào `unknown_commands`**

4. **Trả lại token đã bảo vệ**

**Ví dụ:**
```latex
% Input:
The \method{FooNet} with \vspace{2pt} and \cite{ref}.

% Output sau Tầng 2:
The FooNet with and \cite{ref}.
```

- `\textbf` → bỏ (UNWRAP)
- `\method{FooNet}` → `FooNet` (unwrap text, ghi vào bộ đếm)
- `\vspace{2pt}` → xóa (DROP_WITH_ARGS)
- `\cite{ref}` → **giữ nguyên** (bảo vệ)

### 5.5 Toán: Chuẩn hóa

- **Toán inline** về dạng `$...$` (từ `\( \)` và `\begin{math}`).
- **Toán khối** (`$$..$$`, `\[..\]`, `equation*`, `align`, `gather`, `eqnarray`...) về `\begin{equation} ... \end{equation}` trên một dòng; các môi trường nhiều dòng được bọc thêm `aligned`/`gathered`.

### 5.6 Tách câu (`m2_sentences.py`)

Tách ở `.`, `!`, `?` khi câu sau bắt đầu bằng chữ hoa, ngoặc mở, dấu trích, lệnh LaTeX (`\cite...`) hoặc toán. **Không tách** trong: toán inline, viết tắt (`i.e.`, `e.g.`, `et al.`, `Fig. 1`, `Eq. (2)`, `No. 5`...), URL, số thập phân.

### 5.7 `element_id` và khử trùng nội dung (`m2_content_dedup.py`)

- Mỗi version có nút gốc `<paper_key>#root<N>` (kind `document`, nội dung `Version N root`). Nút gốc không có hàng trong `hierarchy`.
- Heading, câu, công thức, hình là các nút **dùng chung**: id dạng `#h<k>`, `#s<k>`, `#e<k>`, `#g<k>`. Hai nút có cùng (kind, nội dung đã làm sạch) ở các version khác nhau chỉ lưu **một** `element_id`. Trong **một** version, lần xuất hiện thứ k của cùng nội dung là một element riêng để cây không bị nhập nhằng.
- `itemize` và `item` có id riêng cho từng version (`#ul<N>_<k>`, `#li<N>_<k>`), không gộp.
- `hierarchy` chỉ chứa cạnh nút-cha của từng version. Nút dùng chung có thể gắn vào cha khác nhau ở các version khác nhau.
- `\cite` được đổi khóa theo `cite_map.json` **trước** khi so khớp nội dung. Vì vậy một câu giống nhau giữa các version nhưng khác khóa cite (ví dụ `vaswani2017` và `vit_transformer`) vẫn dùng chung một element.

### 5.8 BibTeX (`m2_bibitem.py`, `m2_bib_dedup.py`, `run_m2.py`)

- **Nguồn entry** của mỗi version (chỉ xét file trên đường biên dịch):
  - file `.bib` được `\bibliography{...}` hoặc `\addbibresource{...}` nhắc tới (không nhắc tới thì bỏ qua; nếu không khớp được tên nào thì lấy mọi `.bib` của version và tính vào `bib_fallback_versions`);
  - các `\bibitem` trong `thebibliography`, chuyển sang BibTeX bằng quy tắc (regex): rút tác giả, tiêu đề, năm, DOI, URL, nơi xuất bản; giữ `raw_text` gốc.
- **Khử trùng** (trong từng version và giữa các version). Hai entry là cùng một tài liệu khi trùng khóa, hoặc trùng DOI, hoặc trùng tiêu đề chuẩn hóa mà tác giả không mâu thuẫn. Năm không dùng để loại trừ (một bài thường đổi năm giữa các version). Liên kết có tính bắc cầu.
- **Khóa được chọn** là khóa của entry ở version mới nhất, hòa thì theo thứ tự chữ cái. Các khóa còn lại vào `cite_map.json`.
- **Hợp nhất trường (union):** trường thiếu ở bản mới được lấy từ bản cũ; trường mâu thuẫn thì giữ bản mới nhất và ghi vào `bibtex_conflicts.json` (khác hoa/thường hoặc cách viết tên không tính là mâu thuẫn).
- **Dòng bảng `bibtex`:** `journal` lấy từ `journal`, không có thì `booktitle`; `url` lấy từ `url`, hoặc `https://arxiv.org/abs/<eprint>`; `year` là số nguyên; `authors` là list.
- **Ứng viên cho M3:** với mỗi entry sau khử trùng, ghép với các reference **có arXiv ID** của cùng bài (`refs.json`), giữ cặp có độ giống tiêu đề đủ lớn (xem `matching/m3_candidates.py`).
- File nguồn `.tex`/`.bib` **không bị sửa**. Việc đổi `\cite` thực hiện ở nội dung `elements`, không ghi đè lên nguồn.

## 6. Hiệu năng

`run_m2.py` ghi `benchmark/m2_benchmark.json` bằng `PerfRecorder`:

| Nhóm | Khóa chính |
|---|---|
| Thời gian | `total_wall_s`, `avg_paper_s`, `max_paper_s`, `stages_s` (`bib`, `tree`, `content`, `export`) |
| Bộ nhớ | `peak_ram_mb`, `avg_ram_mb` |
| Đầu ra | `m2_output_bytes` (riêng đầu ra M2), `m2_json_bytes`, `export_parquet_bytes`, `peak_disk_mb` |
| Thành công | `n_ok`, `n_failed`, `success_rate`, `parse_success_rate` |
| Khử trùng | `dedup.elements` và `dedup.bibtex`, mỗi mục có `before`, `after`, `reduction_pct` |
| Lệnh lạ **(mới)** | File `benchmark/unknown_commands.json` chứa bộ đếm |
| Khác | `n_papers`, `steps`, `export` (số dòng từng bảng) |

`final_output_bytes` đo cả thư mục corpus (gồm `.tex`/`.bib` của M1), nên dùng **`m2_output_bytes`** làm "dung lượng đầu ra" của M2.

## 7. Hạn chế đã biết

- Bài chỉ có `.bbl` (không có `.bib` và không có `\bibitem` trong `.tex`) không có reference, vì M1 chỉ giữ `.tex` và `.bib` theo đề.
- Tách tác giả và tiêu đề của `\bibitem` bằng quy tắc, nên có thể sai ở một số kiểu trích dẫn (ví dụ `Polosukhin, I. Attention is...`). `raw_text` luôn được giữ để đối chiếu.
- Macro định nghĩa trong `.sty`/`.cls` (M1 không giữ) không được Tầng 1 bung, chỉ xử lý được bằng Tầng 2.
- Macro quá phức tạp (`\csname`, `\expandafter`) bị bỏ qua ở Tầng 1, rơi xuống Tầng 2.
- Chú thích `\footnote{...}` chứa dấu chấm thường (không phải URL hay viết tắt) có thể bị tách thành nhiều câu.

## 8. Xử lý lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `ModuleNotFoundError: matching` hoặc `utils` | Chạy từ thư mục gốc repo bằng `python src/run_m2.py`. |
| Lỗi liên quan `parser` | Dùng Python 3.10 trở lên. |
| `parse_success_rate` thấp | Xem `m2_stats.json` ở gốc corpus, tìm bài có `tree_statuses` chứa `no_root` hoặc `error`, rồi chạy `m2_gather.py --ids <ID> --gather --verbose` để xem vì sao không tìm được file gốc. |
| `bibtex.parquet` ít dòng | Bài có `.bbl` thay vì `.bib` (xem mục 7), hoặc `bib_fallback_versions` cao nghĩa là `\bibliography` không khớp tên file. |
| Không có `elements.json` của một bài | Bài đó chưa có `paper.json` (M1 chưa xong) hoặc thư mục `vN` rỗng. |
| UnicodeEncodeError khi in tiếng Việt (Windows) | `$env:PYTHONUTF8="1"` trước khi chạy. |
| File `unknown_commands.json` rỗng | **Kết quả tốt!** Tầng 1 và Tầng 2 đã xử lý hết các lệnh. |
