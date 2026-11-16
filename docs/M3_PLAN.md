# 🎯 M3 PLAN - Reference Matching Pipeline (Nhóm 4 Người × 5 Ngày)

## 📋 Tổng Quan

**Mục tiêu:** Xây dựng pipeline ML để match BibTeX entries (M2) với references có arXiv ID (M1)  
**Nhóm:** 4 người (k=4)  
**Timeline:** 5 ngày  
**Metric:** MRR@5 (Mean Reciprocal Rank @5)  
**Strategy:** Option 1 - Develop với 40-50 papers đã crawl, scale up sau  

### Yêu Cầu Theo Đề (k=4)
| Requirement | Value | Note |
|-------------|-------|------|
| Manual papers | 20 (5×4) | ≥20 entries/paper |
| Total manual entries | ≥400 (100×4) | |
| Auto papers | ≥8 (2×4) | + 10% remaining |
| Test set | 8 papers | 4 manual + 4 auto |
| Valid set | 8 papers | 4 manual + 4 auto |
| Train set | Remaining | ~20 papers |
| **MINIMUM TOTAL** | **36 papers** | Để dev: 40-50 papers |

---

## 🔄 Pipeline Overview

```
M1 (references) + M2 (bibtex, elements, hierarchy)
    ↓
[0] Data Inspection (P1) → m3_data_inspector.py
    ↓
[1] Data Cleaning (P1) → m3_cleaning.py
    ↓
[2] Paper Selection (P1) → m3_pick_papers.py
    ↓
[3] Labeling
    ├─ Manual (P3) → notebooks/manual_labeling.ipynb → manual_labels.csv
    └─ Auto (P2) → m3_auto_labeler.py → auto_labels.csv
    ↓
[4] Feature Engineering
    ├─ String (P1) → m3_string_features.py
    └─ Context (P2) → m3_context_features.py
    ↓
[5] Data Split (P3) → m3_data_split.py
    ↓
[6] Model Training (P3) → m3_model.py
    ↓
[7] Evaluation (P4) → m3_evaluate.py → MRR@5
    ↓
[8] Predictions (P4) → m3_predictions.py → predictions.parquet
    ↓
[9] Export HF (P4) → m3_publish_hf.py → 6 Parquet tables
    ↓
[10] Poster (ALL) → poster.pptx (Portrait A2)
```

---

## 👥 PHÂN CÔNG 4 NGƯỜI × 5 NGÀY

### **P1: Data & String Features Lead** 🔵

#### **Files cần làm:**
- ✅ `src/matching/m3_data_inspector.py` (DONE)
- ✅ `src/matching/m3_pick_papers.py` (DONE)
- ⏭️ `src/matching/m3_cleaning.py` (NEW)
- ⏭️ `src/matching/m3_string_features.py` (COMPLETE)
- ⏭️ `notebooks/feature_analysis.ipynb` (NEW)

#### **Timeline:**
**Day 1 (Thứ 2):**
- [ ] 9h-11h: Chạy M2 cho 40-50 papers đã crawl từ M1
- [ ] 11h-13h: Run `m3_data_inspector.py`, verify data quality
- [ ] 14h-17h: Run `m3_pick_papers.py`, select 20+8 papers
- [ ] 17h-18h: Start `m3_cleaning.py` (text preprocessing)

**Day 2 (Thứ 3):**
- [ ] 9h-12h: Complete `m3_cleaning.py` (normalize text, authors, titles)
- [ ] 13h-15h: Implement `m3_string_features.py` (title/author similarity)
- [ ] 15h-18h: Test features trên sample data

**Day 3 (Thứ 4):**
- [ ] 9h-12h: Feature analysis notebook (correlation, importance)
- [ ] 13h-15h: Feature engineering round 2 (nếu cần improve MRR)
- [ ] 15h-18h: Document features với justification

**Day 4-5:**
- [ ] Support P3 với feature input
- [ ] Poster: Features section + statistics charts

**Deliverables:**
- `data/m3_inspection.json`
- `data/selected_papers.csv` 
- `m3_cleaning.py` (working)
- `m3_string_features.py` (complete)
- `notebooks/feature_analysis.ipynb`

---

### **P2: Auto Labeler & Context Features** 🟢

#### **Files cần làm:**
- ⏭️ `src/matching/m3_auto_labeler.py` (NEW)
- ⏭️ `src/matching/m3_context_features.py` (COMPLETE)
- ⏭️ `notebooks/auto_labeler_validation.ipynb` (NEW)

#### **Timeline:**
**Day 1 (Thứ 2):**
- [ ] 9h-12h: Design auto labeling logic (string similarity)
- [ ] 13h-15h: Implement `m3_auto_labeler.py` v1 (title only)
- [ ] 15h-18h: Test trên 5 papers, tune threshold

**Day 2 (Thứ 3):**
- [ ] 9h-11h: Add author similarity to auto labeler
- [ ] 11h-13h: Run auto labeler on ≥8 papers
- [ ] 13h-15h: Validation notebook, check label quality
- [ ] 15h-18h: Save `auto_labels.csv`, stats

**Day 3 (Thứ 4):**
- [ ] 9h-12h: Start `m3_context_features.py` (citation context)
- [ ] 13h-15h: Extract context từ hierarchy (section type, position)
- [ ] 15h-18h: Test context features

**Day 4:**
- [ ] Error analysis trên auto labels
- [ ] Support P3 với context features

**Day 5:**
- [ ] Poster: Auto labeling methodology section

**Deliverables:**
- `data/labels/auto_labels.csv` (≥8 papers)
- `m3_auto_labeler.py` (working)
- `m3_context_features.py` (working)
- `notebooks/auto_labeler_validation.ipynb`

---

### **P3: Manual Labeling & Model Training** 🟡

#### **Files cần làm:**
- ⏭️ `notebooks/manual_labeling.ipynb` (NEW)
- ⏭️ `src/matching/m3_data_split.py` (NEW)
- ⏭️ `src/matching/m3_model.py` (COMPLETE)
- ⏭️ `src/matching/m3_baseline.py` (NEW - threshold model)

#### **Timeline:**
**Day 1 (Thứ 2):**
- [ ] 9h-11h: Setup manual labeling notebook
- [ ] 11h-18h: **Manual label 10 papers** (50% done)
  - Display: BibTeX entry + candidate references
  - Input: Match index hoặc -1
  - Save incrementally

**Day 2 (Thứ 3):**
- [ ] 9h-15h: **Manual label 10 papers** (100% done = 20 papers)
- [ ] 15h-17h: Quality check, verify ≥20 entries/paper
- [ ] 17h-18h: Save `manual_labels.csv`

**Day 3 (Thứ 4):**
- [ ] 9h-11h: Implement `m3_data_split.py` (train/valid/test)
- [ ] 11h-13h: Train baseline model (threshold-based)
- [ ] 13h-15h: Load features từ P1, implement `m3_model.py`
- [ ] 15h-18h: Train LightGBM ranker v1

**Day 4 (Thứ 5):**
- [ ] 9h-12h: Hyperparameter tuning (valid set)
- [ ] 12h-14h: Train final model
- [ ] 14h-16h: Evaluate trên test set (với P4)
- [ ] 16h-18h: Document model architecture

**Day 5 (Thứ 6):**
- [ ] Poster: Model architecture + training section

**Deliverables:**
- `data/labels/manual_labels.csv` (20 papers, ≥400 entries)
- `data/labels/labels_metadata.json` (split info)
- `m3_data_split.py` (working)
- `m3_baseline.py` (baseline model)
- `m3_model.py` (complete với LightGBM)
- `data/models/model_final.pkl`

---

### **P4: Evaluation, Export & Poster Lead** 🔴

#### **Files cần làm:**
- ⏭️ `src/matching/m3_evaluate.py` (COMPLETE)
- ⏭️ `src/matching/m3_predictions.py` (COMPLETE)
- ⏭️ `src/matching/m3_publish_hf.py` (COMPLETE)
- ⏭️ `notebooks/error_analysis.ipynb` (NEW)
- ⏭️ `poster.pptx` (NEW - Portrait A2)

#### **Timeline:**
**Day 1 (Thứ 2):**
- [ ] 9h-12h: Implement `m3_evaluate.py` (MRR@5 calculation)
- [ ] 13h-15h: Unit test với dummy predictions
- [ ] 15h-18h: Setup evaluation pipeline

**Day 2 (Thứ 3):**
- [ ] 9h-12h: Start `m3_predictions.py` (top-5 generation logic)
- [ ] 13h-15h: Test prediction format
- [ ] 15h-18h: Start `m3_publish_hf.py` (schema definition)

**Day 3 (Thứ 4):**
- [ ] 9h-12h: Complete `m3_predictions.py`
- [ ] 13h-15h: Evaluate baseline model (với P3)
- [ ] 15h-18h: Error analysis notebook

**Day 4 (Thứ 5):**
- [ ] 9h-11h: Generate final predictions (trained model)
- [ ] 11h-13h: Export 6 Parquet tables
- [ ] 13h-15h: Upload to Hugging Face
- [ ] 15h-17h: Verify dataset, test streaming
- [ ] 17h-18h: Start poster template

**Day 5 (Thứ 6):**
- [ ] 9h-12h: **Coordinate poster creation with ALL**
- [ ] 12h-15h: Poster content filling
- [ ] 15h-17h: Poster polish, final review
- [ ] 17h-18h: README final update

**Deliverables:**
- `m3_evaluate.py` (working, MRR@5)
- `m3_predictions.py` (working)
- `m3_publish_hf.py` (working)
- `data/predictions/predictions.parquet`
- All 6 Parquet tables on HF
- `notebooks/error_analysis.ipynb`
- **`poster.pptx` (Portrait A2)** ← FINAL OUTPUT
- Updated `README.md`

---

## 📅 DAILY TIMELINE

### **Day 1: Setup & Data Prep (Thứ 2)**
```
Morning:
- P1: M2 run (40-50 papers) → data inspection
- P2: Design auto labeler logic
- P3: Setup manual labeling notebook
- P4: Implement MRR@5 code

Afternoon:
- P1: Pick papers, start cleaning
- P2: Implement auto labeler v1
- P3: Start manual labeling (5 papers)
- P4: Test evaluation pipeline

EOD Deliverables:
✅ M2 done cho 40-50 papers
✅ Selected papers list ready
✅ Auto labeler logic ready
✅ Manual labeling: 5/20 papers done
✅ MRR@5 code working
```

### **Day 2: Labeling Focus (Thứ 3)**
```
Morning:
- P1: Complete cleaning, start string features
- P2: Run auto labeler, validate
- P3: Continue manual labeling (10 more papers)
- P4: Predictions script

Afternoon:
- P1: Test string features
- P2: Save auto_labels.csv
- P3: Complete manual labeling (20 total)
- P4: HF export schema

EOD Deliverables:
✅ ALL LABELING DONE (20 manual + 8 auto)
✅ String features ready
✅ Cleaning done
✅ Labels saved
```

### **Day 3: Features & Modeling (Thứ 4)**
```
Morning:
- P1: Feature analysis
- P2: Context features implementation
- P3: Data split + baseline model
- P4: Complete predictions.py

Afternoon:
- P1: Feature documentation
- P2: Test context features
- P3: Train LightGBM v1
- P4: Error analysis

EOD Deliverables:
✅ Features documented
✅ Baseline model trained
✅ LightGBM v1 trained
✅ MRR@5 measured (initial)
```

### **Day 4: Optimization & Export (Thứ 5)**
```
Morning:
- P1: Feature round 2 (if needed)
- P2: Error analysis support
- P3: Hyperparameter tuning
- P4: Generate final predictions

Afternoon:
- P1: Support modeling
- P2: Support modeling
- P3: Train final model
- P4: Export to HF + verify

EOD Deliverables:
✅ Final model trained
✅ Dataset on HF (6 tables)
✅ MRR@5 final score
✅ All code complete
```

### **Day 5: Poster & Polish (Thứ 6)**
```
ALL DAY: POSTER FOCUS
Morning (9h-12h):
- P1: Features section + stats charts
- P2: Auto labeling methodology
- P3: Model architecture diagram
- P4: Overall workflow + results

Afternoon (12h-17h):
- ALL: Integrate sections
- ALL: Design polish
- ALL: Final review

Evening (17h-18h):
- README update
- Final Git commit
- Submission prep

EOD Deliverables:
✅ poster.pptx (Portrait A2) DONE
✅ README complete
✅ Code tested on Colab
🎉 READY TO SUBMIT
```

---

## 📂 File Structure với Owner

```
arxiv-project/
├── data/
│   ├── labels/
│   │   ├── manual_labels.csv        (P3)
│   │   ├── auto_labels.csv          (P2)
│   │   └── labels_metadata.json     (P3)
│   ├── features/
│   │   ├── string_features.json     (P1)
│   │   └── context_features.json    (P2 - optional)
│   ├── models/
│   │   ├── baseline.pkl             (P3)
│   │   ├── model_final.pkl          (P3)
│   │   └── model_config.json        (P3)
│   ├── predictions/
│   │   └── predictions.parquet      (P4)
│   ├── m3_inspection.json           (P1)
│   └── selected_papers.csv          (P1)
│
├── src/matching/
│   ├── m3_data_inspector.py         ✅ DONE (P1)
│   ├── m3_pick_papers.py            ✅ DONE (P1)
│   ├── m3_cleaning.py               ⏭️ P1
│   ├── m3_auto_labeler.py           ⏭️ P2
│   ├── m3_candidates.py             ✅ EXISTS
│   ├── m3_string_features.py        ⏭️ P1 (complete)
│   ├── m3_context_features.py       ⏭️ P2 (complete)
│   ├── m3_data_split.py             ⏭️ P3
│   ├── m3_baseline.py               ⏭️ P3
│   ├── m3_model.py                  ⏭️ P3 (complete)
│   ├── m3_evaluate.py               ⏭️ P4 (complete)
│   ├── m3_predictions.py            ⏭️ P4 (complete)
│   └── m3_publish_hf.py             ⏭️ P4 (complete)
│
├── notebooks/
│   ├── manual_labeling.ipynb        ⏭️ P3
│   ├── auto_labeler_validation.ipynb ⏭️ P2
│   ├── feature_analysis.ipynb       ⏭️ P1
│   └── error_analysis.ipynb         ⏭️ P4
│
├── poster.pptx                      ⏭️ ALL (P4 lead)
├── README.md                        ⏭️ Update by ALL
├── M3_PLAN.md                       ✅ THIS FILE
└── QUICKSTART_M3.md                 ✅ DONE
```

---

## 📊 Evaluation: MRR@5

```python
MRR@5 = (1/|Q|) × Σ(1/rank_i)

where:
- |Q| = số labeled BibTeX entries trong test set
- rank_i = vị trí của correct reference trong top-5 (1-5)
- Nếu correct reference không có trong top-5: rank_i = 0

Example:
Entry 1: correct at rank 1 → 1/1 = 1.0
Entry 2: correct at rank 3 → 1/3 = 0.333
Entry 3: correct not in top-5 → 0
MRR@5 = (1.0 + 0.333 + 0) / 3 = 0.444
```

**Target MRR@5:**
- Baseline (threshold): 0.3-0.4
- LightGBM: 0.4-0.6
- Good: ≥0.5
- Excellent: ≥0.6

---

## 🚨 Risk Management

| Risk | Impact | Owner | Mitigation |
|------|--------|-------|------------|
| M1 chưa crawl đủ 40 papers | 🔴 HIGH | P1 | Minimum 30 papers, adjust k=3 |
| Manual labeling quá chậm | 🔴 HIGH | P3 | P1,P2 hỗ trợ label 5 papers mỗi người |
| Auto labeler low quality | 🟡 MEDIUM | P2 | Tune threshold, manual validation |
| MRR@5 < 0.3 | 🟡 MEDIUM | P3,P1 | Feature round 2, ensemble |
| HF upload fail | 🟢 LOW | P4 | Test sớm Day 3, backup Google Drive |
| Poster delay | 🟡 MEDIUM | P4 | Template sớm, parallel work |

**Daily Blocker Check:**
- 15 phút standup mỗi sáng 9h
- Ai block → toàn nhóm hỗ trợ ngay

---

## ✅ Definition of Done

### **Technical Deliverables:**
- [ ] Dataset on Hugging Face (6 Parquet tables, schema valid)
- [ ] MRR@5 calculated on test set (any score ≥0.3)
- [ ] All code runs on Google Colab (tested)
- [ ] Git repo clean, organized, documented

### **Documentation:**
- [ ] `README.md` updated:
  - [ ] Feature documentation với justification
  - [ ] Label format specification
  - [ ] How to run each script
  - [ ] Environment setup
- [ ] Code comments đầy đủ
- [ ] Jupyter notebooks có markdown explanation

### **Poster (Portrait A2, pptx):**
- [ ] Section 1: Introduction & Workflow
- [ ] Section 2: Features (P1) - with justification
- [ ] Section 3: Labeling (P2, P3) - manual + auto
- [ ] Section 4: Model (P3) - architecture diagram
- [ ] Section 5: Results (P4) - MRR@5, error analysis
- [ ] Section 6: Statistics - notable insights
- [ ] Design: Clean, professional, readable from 2m distance

### **Validation:**
- [ ] Chạy trên Colab không lỗi
- [ ] Schema Parquet match đề bài 100%
- [ ] HF dataset streamable
- [ ] Manual labels quality checked (spot check 20 entries)

---

## 💡 Tips & Best Practices

### **Code:**
- Commit sau mỗi file hoàn thành
- Tên commit rõ ràng: `[P1] Add m3_cleaning.py`
- Test với small subset trước khi chạy full

### **Labeling:**
- P3: Sort candidates by title_similarity trước khi manual label
- Top 1 candidate thường đúng → nhanh hơn
- Dùng keyboard shortcuts (1-5 cho rank, 0 cho no match)

### **Features:**
- Start simple: title + author similarity đã đủ baseline 0.3-0.4
- Add complexity nếu cần improve MRR
- Document WHY mỗi feature (cho poster)

### **Model:**
- LightGBM với default params đã ok
- Hyperparameter tuning: `n_estimators`, `learning_rate`, `max_depth`
- Early stopping với validation set

### **Poster:**
- Dùng charts từ notebooks (export PNG)
- Less text, more visuals
- Font size ≥24pt (readable from 2m)

---

## 🔄 Communication Protocol

### **Daily Standup (15 phút @ 9h):**
1. Yesterday: Làm gì xong?
2. Today: Làm gì?
3. Blockers: Bị block gì?

### **Slack/Discord Channels:**
- `#m3-general`: Thảo luận chung
- `#m3-blockers`: Report blockers ngay
- `#m3-results`: Share progress, screenshots

### **File Sharing:**
- Code: Git commit thường xuyên
- Data: Google Drive shared folder
- Poster: Google Slides (collaborate) → export pptx

### **End of Day Sync (5 phút @ 18h):**
- Check timeline: On track?
- Adjust tomorrow's plan if needed

---

## 🎯 Success Criteria

### **Minimum (Pass):**
- ✅ 6 Parquet tables on HF (schema correct)
- ✅ MRR@5 ≥ 0.3
- ✅ Poster done (Portrait A2)
- ✅ Code runs without error

### **Good:**
- ✅ MRR@5 ≥ 0.4
- ✅ Features well-documented
- ✅ Error analysis insightful
- ✅ Poster professional

### **Excellent:**
- ✅ MRR@5 ≥ 0.5
- ✅ Novel features (context, embeddings)
- ✅ Multiple models compared
- ✅ Poster outstanding

---

## 📞 Emergency Contacts

**If M1 crawling too slow:**
- Reduce to 30 papers minimum
- Adjust requirements: k=3 instead of k=4

**If manual labeling behind schedule:**
- P1, P2 each label 3 papers (Day 1 evening)
- Reduce entries/paper to 15 (still valid if total ≥400)

**If MRR too low (<0.3):**
- Check label quality first
- Add year_match, exact_title features
- Ensemble: avg(baseline, LightGBM)

**If HF upload fails:**
- Backup: Upload to Google Drive
- Submit Drive link + mention HF issue

---

## 🚀 Quick Start Commands

```bash
# Day 1 Morning - P1
python src/run_m2.py --corpus-dir data --limit 50 --export
python src/matching/m3_data_inspector.py --corpus-dir data
python src/matching/m3_pick_papers.py --corpus-dir data --top 30 --csv data/selected_papers.csv

# Day 1 Afternoon - P2
python src/matching/m3_auto_labeler.py --corpus-dir data --test --papers 5

# Day 2 - P3
jupyter notebook notebooks/manual_labeling.ipynb

# Day 3 - P3
python src/matching/m3_data_split.py --labels-dir data/labels
python src/matching/m3_baseline.py --data-dir data
python src/matching/m3_model.py --train

# Day 4 - P4
python src/matching/m3_evaluate.py --model data/models/model_final.pkl
python src/matching/m3_predictions.py --model data/models/model_final.pkl --output data/predictions/
python src/matching/m3_publish_hf.py --corpus-dir data --hf-repo <user>/<dataset>

# Day 5 - Test on Colab
# Upload code to Colab, run end-to-end
```

---

**Version:** 2.0  
**Last Updated:** [Today]  
**Status:** Ready for 5-day sprint  
**Next Action:** P1 start Day 1 morning tasks
