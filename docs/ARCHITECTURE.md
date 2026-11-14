# High-Level Architecture - arXiv Research Paper Processing Pipeline

## 📊 Tổng Quan Kiến Trúc

Đồ án xây dựng pipeline xử lý và phân tích bài báo khoa học từ arXiv, bao gồm 3 milestone chính với luồng dữ liệu liên tiếp:

```mermaid
flowchart LR
    %% External Sources
    ArxivAPI["arXiv API<br/>(metadata + source)"]
    S2API["Semantic Scholar API<br/>(references)"]
    
    %% Milestone 1
    M1["<b>MILESTONE 1</b><br/>Data Crawling"]
    M1_Input["<b>INPUT</b><br/>ID.txt<br/>(arXiv IDs)"]
    M1_Output["<b>OUTPUT</b><br/>• papers.parquet<br/>• references.parquet<br/>• LaTeX sources (.tex/.bib)<br/>• refs.json"]
    
    %% Milestone 2
    M2["<b>MILESTONE 2</b><br/>LaTeX Parsing<br/>& Deduplication"]
    M2_Output["<b>OUTPUT</b><br/>• elements.parquet<br/>• hierarchy.parquet<br/>• bibtex.parquet<br/>• candidates.json"]
    
    %% Milestone 3
    M3["<b>MILESTONE 3</b><br/>Reference Matching<br/>(ML Model)"]
    M3_Training["<b>TRAINING DATA</b><br/>• Manual labels (≥5k papers)<br/>• Auto labels (≥10%)"]
    M3_Output["<b>OUTPUT</b><br/>• predictions.parquet<br/>• MRR@5 metrics<br/>• Poster (A2)"]
    
    %% Final Output
    HF["Hugging Face<br/>Dataset<br/>(6 Parquet tables)"]
    
    %% Flow connections
    M1_Input --> M1
    ArxivAPI -.->|rate limit<br/>3s/request| M1
    S2API -.->|batch API| M1
    M1 --> M1_Output
    
    M1_Output --> M2
    M2 --> M2_Output
    
    M1_Output --> M3
    M2_Output --> M3
    M3_Training --> M3
    M3 --> M3_Output
    
    M1_Output --> HF
    M2_Output --> HF
    M3_Output --> HF
    
    %% Styling
    classDef milestone fill:#4A90E2,stroke:#2E5C8A,stroke-width:3px,color:#fff
    classDef input fill:#E8F4F8,stroke:#4A90E2,stroke-width:2px
    classDef output fill:#D4EDDA,stroke:#28A745,stroke-width:2px
    classDef api fill:#FFF3CD,stroke:#FFC107,stroke-width:2px
    classDef final fill:#9C27B0,stroke:#6A1B9A,stroke-width:3px,color:#fff
    
    class M1,M2,M3 milestone
    class M1_Input,M3_Training input
    class M1_Output,M2_Output,M3_Output output
    class ArxivAPI,S2API api
    class HF final
```

---

## 🔄 Chi Tiết Từng Milestone

### Milestone 1: Data Crawling & Collection

**Mục tiêu:** Thu thập dữ liệu thô từ arXiv và Semantic Scholar

```mermaid
flowchart TB
    subgraph M1["MILESTONE 1: Data Crawling"]
        direction TB
        
        Input1["📄 INPUT<br/>━━━━━━━━━━━━<br/>ID.txt<br/>(danh sách arXiv IDs)"]
        
        Discovery["🔍 DISCOVERY<br/>Query arXiv API<br/>• Metadata<br/>• Số versions"]
        
        Parallel["⚡ PARALLEL PROCESSING"]
        
        Download["📥 DOWNLOAD<br/>Tải source code<br/>• .tex files<br/>• .bib files<br/>(bỏ hình ảnh)"]
        
        References["🔗 REFERENCES<br/>Semantic Scholar API<br/>• Batch requests<br/>• Reference metadata"]
        
        Checkpoint["💾 CHECKPOINT<br/>• discovery_cache.json<br/>• Backup mỗi batch<br/>• Resume support"]
        
        Tables["📊 BUILD TABLES<br/>• papers.parquet<br/>• references.parquet<br/>(schema validation)"]
        
        Output1["📦 OUTPUT<br/>━━━━━━━━━━━━<br/>✓ papers.parquet<br/>✓ references.parquet<br/>✓ LaTeX sources/version<br/>✓ refs.json/paper<br/>✓ Benchmark metrics"]
        
        Input1 --> Discovery
        Discovery --> Parallel
        Parallel --> Download
        Parallel --> References
        Download --> Checkpoint
        References --> Checkpoint
        Checkpoint --> Tables
        Tables --> Output1
    end
    
    ArxivAPI["arXiv API<br/>⚠️ Rate limit:<br/>1 req/3s"]
    S2API["Semantic Scholar<br/>Batch API"]
    
    ArxivAPI -.->|metadata<br/>+ source| Discovery
    ArxivAPI -.->|tar.gz| Download
    S2API -.->|ref data| References
    
    classDef input fill:#E8F4F8,stroke:#4A90E2,stroke-width:2px
    classDef process fill:#4A90E2,stroke:#2E5C8A,stroke-width:2px,color:#fff
    classDef output fill:#D4EDDA,stroke:#28A745,stroke-width:2px
    classDef external fill:#FFF3CD,stroke:#FFC107,stroke-width:2px
    
    class Input1 input
    class Discovery,Parallel,Download,References,Checkpoint,Tables process
    class Output1 output
    class ArxivAPI,S2API external
```

**Đặc điểm kỹ thuật:**
- **Thời gian ước tính:** 20,000 IDs ≈ 28-45 giờ (3-4 phiên Colab)
- **Resume:** Discovery cache + checkpoint theo version
- **Backup:** Tarball nhanh 10-20x so với rsync (2-5 phút restore)
- **Tuân thủ:** arXiv ToS (1 connection, 3s delay, proper User-Agent)

---

### Milestone 2: LaTeX Parsing & Deduplication

**Mục tiêu:** Parse LaTeX thành cấu trúc và khử trùng nội dung

```mermaid
flowchart TB
    subgraph M2["MILESTONE 2: LaTeX Parsing"]
        direction TB
        
        Input2["📂 INPUT<br/>━━━━━━━━━━━━<br/>M1 Corpus:<br/>• paper.json<br/>• vN/*.tex<br/>• vN/*.bib<br/>• refs.json"]
        
        Gather["📑 GATHER FILES<br/>• Tìm root file<br/>• Theo \\input/\\include<br/>• Bỏ comments<br/>• List file thừa"]
        
        Layer1["🔧 LAYER 1: Macro Expansion<br/>• Bung \\newcommand<br/>• Bung \\def<br/>• Bỏ \\iffalse<br/>• Xử lý abbreviations"]
        
        Tree["🌲 BUILD HIERARCHY<br/>• Document → Sections<br/>• Extract content blocks<br/>• Nested structure<br/>• Bỏ References"]
        
        Layer2["🧹 LAYER 2: Cleanup<br/>• Bảo vệ \\cite, \\ref<br/>• Phân loại commands<br/>• Unwrap/drop/space<br/>• Normalize math"]
        
        Dedup["♻️ DEDUPLICATION<br/>• Element_id sharing<br/>• Version dedup<br/>• Update \\cite keys"]
        
        BibTeX["📚 BIBTEX PROCESSING<br/>• Parse .bib files<br/>• Convert \\bibitem<br/>• Merge conflicts<br/>• cite_map.json"]
        
        Candidates["🎯 CANDIDATE GENERATION<br/>Match BibTeX ↔ References<br/>for M3 training"]
        
        Output2["📦 OUTPUT<br/>━━━━━━━━━━━━<br/>✓ elements.parquet<br/>✓ hierarchy.parquet<br/>✓ bibtex.parquet<br/>✓ candidates.json<br/>✓ parse_status.json<br/>✓ unknown_commands.json"]
        
        Input2 --> Gather
        Gather --> Layer1
        Layer1 --> Tree
        Tree --> Layer2
        Layer2 --> Dedup
        Gather --> BibTeX
        Dedup --> Output2
        BibTeX --> Dedup
        BibTeX --> Candidates
        Candidates --> Output2
    end
    
    classDef input fill:#E8F4F8,stroke:#4A90E2,stroke-width:2px
    classDef process fill:#4A90E2,stroke:#2E5C8A,stroke-width:2px,color:#fff
    classDef output fill:#D4EDDA,stroke:#28A745,stroke-width:2px
    
    class Input2 input
    class Gather,Layer1,Tree,Layer2,Dedup,BibTeX,Candidates process
    class Output2 output
```

**Kiến trúc hai tầng xử lý LaTeX:**
1. **Tầng 1 (Pre-tree):** Bung macro do tác giả định nghĩa
2. **Tầng 2 (Post-tree):** Bảo vệ, phân loại, bóc vỏ dự phòng

**Đầu ra chính:**
- `elements`: Nội dung đã dedup (sentence, formula, heading...)
- `hierarchy`: Cấu trúc cây từng version
- `bibtex`: BibTeX entries sau merge và dedup

---

### Milestone 3: Reference Matching with ML

**Mục tiêu:** Match BibTeX entries với references bằng ML model

```mermaid
flowchart TB
    subgraph M3["MILESTONE 3: Reference Matching"]
        direction TB
        
        Input3A["📂 INPUT từ M1<br/>━━━━━━━━━━━━<br/>• references.parquet<br/>• refs.json"]
        Input3B["📂 INPUT từ M2<br/>━━━━━━━━━━━━<br/>• bibtex.parquet<br/>• elements.parquet<br/>• hierarchy.parquet<br/>• candidates.json"]
        
        Inspect["🔍 DATA INSPECTION<br/>• Check quality<br/>• Count entries<br/>• Identify best papers"]
        
        Manual["✍️ MANUAL LABELING<br/>• ≥5k papers<br/>• ≥20 entries/paper<br/>• ≥100k total labels<br/>→ manual_labels.csv"]
        
        Auto["🤖 AUTO LABELING<br/>• String similarity<br/>• ≥10% remaining papers<br/>• ≥2k papers<br/>→ auto_labels.csv"]
        
        Features["⚙️ FEATURE ENGINEERING<br/><b>String Features:</b><br/>• Title similarity<br/>• Author similarity<br/>• Year match<br/><br/><b>Context Features:</b><br/>• Citation context<br/>• Document position<br/>• Section type"]
        
        Split["📊 DATA SPLIT<br/>• Test: k manual + k auto<br/>• Valid: k manual + k auto<br/>• Train: remaining<br/>(k = team members)"]
        
        Train["🎓 MODEL TRAINING<br/>Approaches:<br/>• LightGBM Ranker<br/>• XGBoost<br/>• Neural Network<br/>• Threshold-based baseline"]
        
        Evaluate["📈 EVALUATION<br/><b>Metric: MRR@5</b><br/>Mean Reciprocal Rank<br/>Top-5 predictions"]
        
        Predict["🎯 GENERATE PREDICTIONS<br/>For ALL entries:<br/>• Top-5 candidates<br/>• cand_1 to cand_5"]
        
        Output3["📦 OUTPUT<br/>━━━━━━━━━━━━<br/>✓ predictions.parquet<br/>✓ MRR@5 metrics<br/>✓ Feature importance<br/>✓ Error analysis<br/>✓ Poster (A2 Portrait)"]
        
        Input3A --> Inspect
        Input3B --> Inspect
        Inspect --> Manual
        Inspect --> Auto
        Manual --> Features
        Auto --> Features
        Features --> Split
        Split --> Train
        Train --> Evaluate
        Evaluate -.->|iterate| Features
        Evaluate --> Predict
        Predict --> Output3
    end
    
    classDef input fill:#E8F4F8,stroke:#4A90E2,stroke-width:2px
    classDef process fill:#4A90E2,stroke:#2E5C8A,stroke-width:2px,color:#fff
    classDef output fill:#D4EDDA,stroke:#28A745,stroke-width:2px
    
    class Input3A,Input3B input
    class Inspect,Manual,Auto,Features,Split,Train,Evaluate,Predict process
    class Output3 output
```

**Công thức MRR@5:**
```
MRR@5 = (1/|Q|) × Σ(1/rank_i)

Trong đó:
- |Q| = số labeled entries trong test set
- rank_i = vị trí correct reference trong top-5 (1-5)
- rank_i = 0 nếu không có trong top-5
```

**Feature Ideas:**
- String similarity: Levenshtein, Jaccard, SequenceMatcher
- TF-IDF cosine similarity
- Author name overlap (normalized)
- Year difference
- Exact title match (binary)
- Citation context từ hierarchy

---

## 📊 Pipeline Metrics Tổng Hợp

### M1 Benchmark
| Metric | Giá trị |
|--------|---------|
| Thời gian | 28-45h (20k IDs) |
| RAM đỉnh | ~12 GB (Colab) |
| Disk đỉnh | ~50 GB |
| Success rate | >99% |
| Backup speed | 2-5 phút restore |

### M2 Benchmark
| Metric | Giá trị |
|--------|---------|
| Parse success | >95% |
| Dedup reduction | ~30-40% |
| Unknown commands | →0 (goal) |
| Processing | CPU only |
| Version coverage | Multi-version aware |

### M3 Target
| Metric | Target |
|--------|--------|
| MRR@5 | Maximize |
| Manual labels | ≥100k entries |
| Auto labels | ≥10% papers |
| Test fairness | k+k papers |

---

## 🗂️ Final Dataset Structure

```
Hugging Face Dataset
├── 📊 M1 Tables
│   ├── papers.parquet
│   │   └── Columns: arxiv_id, title, authors, submission_date, revised_dates, venue
│   └── references.parquet
│       └── Columns: arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id
│
├── 📊 M2 Tables  
│   ├── elements.parquet
│   │   └── Columns: element_id, arxiv_id, kind, content
│   ├── hierarchy.parquet
│   │   └── Columns: arxiv_id, version, element_id, parent_id
│   └── bibtex.parquet
│       └── Columns: arxiv_id, citation_key, entry_type, title, authors, year, doi, journal, url
│
└── 📊 M3 Table
    └── predictions.parquet
        └── Columns: arxiv_id, partition, citation_key, groundtruth_arxiv_id, 
                     cand_1, cand_2, cand_3, cand_4, cand_5
```

---

## 🎯 Key Design Decisions

### M1: Reliability & Resume
- **Why Tarball?** 10-20x faster restore than rsync
- **Why Discovery Cache?** Avoid re-querying arXiv API (1000s of requests)
- **Why Batch Backup?** Colab 12h limit requires frequent saves

### M2: Two-Layer Processing
- **Layer 1 (Pre-tree):** Handle author macros early, before structure parsing
- **Layer 2 (Post-tree):** Fallback for unknown commands, preserves important content
- **Why Dedup?** Save space, enable version comparison, identical content = same ID

### M3: ML Ranking
- **Why Ranking?** Better than binary classification (accounts for uncertainty)
- **Why MRR@5?** Standard IR metric, focuses on top results
- **Why Manual+Auto Labels?** Quality (manual) + Quantity (auto)

---

## 🔧 Technology Stack

| Component | Tools |
|-----------|-------|
| **Language** | Python 3.10+ |
| **Data** | Pandas, PyArrow (Parquet) |
| **ML** | LightGBM, XGBoost, scikit-learn |
| **APIs** | arXiv, Semantic Scholar |
| **Storage** | Google Drive (Colab) |
| **Versioning** | Git |
| **Publishing** | Hugging Face Datasets |

---

## 📈 Execution Timeline

```
Week 1-2:  M1 Data Crawling (3-4 Colab sessions)
Week 3:    M2 LaTeX Parsing
Week 4-5:  M3 Labeling (manual + auto)
Week 6:    M3 Feature Engineering
Week 7:    M3 Model Training & Tuning
Week 8:    Final Testing, Export, Poster
```

---

## ✅ Success Criteria

- [x] M1: All IDs crawled, <1% missing versions
- [x] M2: >95% parse success, unknown commands →0
- [ ] M3: MRR@5 competitive on test set
- [ ] All 6 Parquet tables validated
- [ ] Dataset published to Hugging Face
- [ ] Poster completed (A2 Portrait)

---

**Created:** 2024  
**Status:** M1 & M2 Complete, M3 In Progress  
**Team:** [k members]

