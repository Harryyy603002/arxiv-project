r"""m3_data_inspector.py

Milestone: M3 (Data Inspection)
Mục đích: Kiểm tra và thống kê dữ liệu từ M1 và M2 để chuẩn bị cho M3

Đầu vào: Corpus từ M1 và M2 (data/)
Đầu ra:  Thống kê về:
         - Số bài có đầy đủ BibTeX entries
         - Số bài có references với arXiv ID
         - Số bài phù hợp để labeling (manual và auto)
         - Thống kê về candidate pairs

Chạy:
    python src/matching/m3_data_inspector.py --corpus-dir data
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List
from collections import Counter

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.config import (
    paper_dir, elements_path, hierarchy_path, refs_path,
    parse_status_path, DATA_DIR
)


def load_paper_data(arxiv_id: str, corpus_dir: Path) -> Dict:
    """Load tất cả dữ liệu cần thiết của một paper."""
    pdir = corpus_dir / arxiv_id.replace(".", "-").replace("/", "-")
    
    data = {
        "arxiv_id": arxiv_id,
        "exists": pdir.exists(),
        "has_elements": False,
        "has_hierarchy": False,
        "has_refs": False,
        "has_bibtex": False,
        "bibtex_entries": [],
        "references": [],
        "refs_with_arxiv": [],
        "elements_count": 0,
        "versions": [],
    }
    
    if not pdir.exists():
        return data
    
    # Load elements
    elem_file = pdir / "elements.json"
    if elem_file.exists():
        data["has_elements"] = True
        try:
            elements = json.loads(elem_file.read_text(encoding="utf-8"))
            data["elements_count"] = len(elements)
        except Exception as e:
            print(f"  ⚠️  Lỗi đọc elements {arxiv_id}: {e}")
    
    # Load hierarchy
    hier_file = pdir / "hierarchy.json"
    if hier_file.exists():
        data["has_hierarchy"] = True
        try:
            hierarchy = json.loads(hier_file.read_text(encoding="utf-8"))
            data["versions"] = sorted(set(h["version"] for h in hierarchy))
        except Exception as e:
            print(f"  ⚠️  Lỗi đọc hierarchy {arxiv_id}: {e}")
    
    # Load references (M1)
    refs_file = pdir / "refs.json"
    if refs_file.exists():
        data["has_refs"] = True
        try:
            refs = json.loads(refs_file.read_text(encoding="utf-8"))
            data["references"] = refs
            data["refs_with_arxiv"] = [
                r for r in refs 
                if r.get("ref_arxiv_id") or (r.get("externalIds") or {}).get("ArXiv")
            ]
        except Exception as e:
            print(f"  ⚠️  Lỗi đọc refs {arxiv_id}: {e}")
    
    # Load BibTeX (M2)
    bibtex_file = pdir / "bibtex.json"
    if bibtex_file.exists():
        data["has_bibtex"] = True
        try:
            bibtex = json.loads(bibtex_file.read_text(encoding="utf-8"))
            data["bibtex_entries"] = bibtex
        except Exception as e:
            print(f"  ⚠️  Lỗi đọc bibtex {arxiv_id}: {e}")
    
    # Load candidates (nếu có)
    cand_file = pdir / "candidates.json"
    if cand_file.exists():
        try:
            candidates = json.loads(cand_file.read_text(encoding="utf-8"))
            data["candidate_pairs"] = len(candidates)
            
            # Count unique entries with candidates
            entries_with_cands = set(c["citation_key"] for c in candidates)
            data["entries_with_candidates"] = len(entries_with_cands)
        except Exception as e:
            print(f"  ⚠️  Lỗi đọc candidates {arxiv_id}: {e}")
            data["candidate_pairs"] = 0
            data["entries_with_candidates"] = 0
    else:
        data["candidate_pairs"] = 0
        data["entries_with_candidates"] = 0
    
    return data


def inspect_corpus(corpus_dir: Path, limit: int = None) -> Dict:
    """Thống kê toàn bộ corpus."""
    print(f"\n{'='*80}")
    print(f"📊 KIỂM TRA DỮ LIỆU M1 & M2 - Corpus: {corpus_dir}")
    print(f"{'='*80}\n")
    
    # Tìm tất cả papers
    paper_dirs = [d for d in corpus_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    
    if limit:
        paper_dirs = paper_dirs[:limit]
    
    print(f"🔍 Tìm thấy {len(paper_dirs)} papers trong corpus\n")
    
    stats = {
        "total_papers": len(paper_dirs),
        "has_elements": 0,
        "has_hierarchy": 0,
        "has_refs": 0,
        "has_bibtex": 0,
        "has_candidates": 0,
        "papers_ready_for_labeling": [],  # Papers có đủ điều kiện cho manual labeling
        "papers_for_auto_labeling": [],   # Papers có đủ điều kiện cho auto labeling
        "bibtex_entry_counts": [],
        "refs_arxiv_counts": [],
        "candidate_pair_counts": [],
    }
    
    all_papers = []
    
    for i, pdir in enumerate(paper_dirs, 1):
        arxiv_id = pdir.name.replace("-", ".")
        if "/" in arxiv_id or "hep-th" in pdir.name:
            arxiv_id = pdir.name  # Giữ nguyên format cũ
        
        data = load_paper_data(arxiv_id, corpus_dir)
        all_papers.append(data)
        
        if data["has_elements"]:
            stats["has_elements"] += 1
        if data["has_hierarchy"]:
            stats["has_hierarchy"] += 1
        if data["has_refs"]:
            stats["has_refs"] += 1
        if data["has_bibtex"]:
            stats["has_bibtex"] += 1
        
        bib_count = len(data["bibtex_entries"])
        ref_arxiv_count = len(data["refs_with_arxiv"])
        cand_count = data.get("candidate_pairs", 0)
        entries_with_cands = data.get("entries_with_candidates", 0)
        
        if bib_count > 0:
            stats["bibtex_entry_counts"].append(bib_count)
        if ref_arxiv_count > 0:
            stats["refs_arxiv_counts"].append(ref_arxiv_count)
        if cand_count > 0:
            stats["candidate_pair_counts"].append(cand_count)
            stats["has_candidates"] += 1
        
        # Tiêu chí cho manual labeling: >= 20 bibtex entries và có candidates
        if bib_count >= 20 and entries_with_cands > 0:
            stats["papers_ready_for_labeling"].append({
                "arxiv_id": arxiv_id,
                "bibtex_entries": bib_count,
                "refs_with_arxiv": ref_arxiv_count,
                "entries_with_candidates": entries_with_cands,
                "candidate_pairs": cand_count,
            })
        
        # Tiêu chí cho auto labeling: có bibtex và có candidates (ít hơn manual)
        if bib_count >= 10 and entries_with_cands > 0:
            stats["papers_for_auto_labeling"].append({
                "arxiv_id": arxiv_id,
                "bibtex_entries": bib_count,
                "refs_with_arxiv": ref_arxiv_count,
                "entries_with_candidates": entries_with_cands,
                "candidate_pairs": cand_count,
            })
        
        if i % 10 == 0:
            print(f"  Đã xử lý {i}/{len(paper_dirs)} papers...", end="\r")
    
    print(f"  Đã xử lý {len(paper_dirs)}/{len(paper_dirs)} papers ✓       \n")
    
    # Sort papers by quality
    stats["papers_ready_for_labeling"].sort(
        key=lambda x: (-x["entries_with_candidates"], -x["refs_with_arxiv"])
    )
    stats["papers_for_auto_labeling"].sort(
        key=lambda x: (-x["entries_with_candidates"], -x["refs_with_arxiv"])
    )
    
    return stats, all_papers


def print_statistics(stats: Dict) -> None:
    """In thống kê đẹp."""
    print(f"\n{'='*80}")
    print(f"📈 THỐNG KÊ TỔNG QUAN")
    print(f"{'='*80}\n")
    
    print(f"📄 Tổng số papers:          {stats['total_papers']}")
    print(f"   ├─ Có elements (M2):     {stats['has_elements']:>4} ({stats['has_elements']/stats['total_papers']*100:>5.1f}%)")
    print(f"   ├─ Có hierarchy (M2):    {stats['has_hierarchy']:>4} ({stats['has_hierarchy']/stats['total_papers']*100:>5.1f}%)")
    print(f"   ├─ Có references (M1):   {stats['has_refs']:>4} ({stats['has_refs']/stats['total_papers']*100:>5.1f}%)")
    print(f"   ├─ Có BibTeX (M2):       {stats['has_bibtex']:>4} ({stats['has_bibtex']/stats['total_papers']*100:>5.1f}%)")
    print(f"   └─ Có candidates (M2):   {stats['has_candidates']:>4} ({stats['has_candidates']/stats['total_papers']*100:>5.1f}%)")
    
    if stats['bibtex_entry_counts']:
        import statistics
        print(f"\n📚 BibTeX Entries:")
        print(f"   ├─ Trung bình: {statistics.mean(stats['bibtex_entry_counts']):.1f}")
        print(f"   ├─ Median:     {statistics.median(stats['bibtex_entry_counts']):.0f}")
        print(f"   ├─ Min:        {min(stats['bibtex_entry_counts'])}")
        print(f"   └─ Max:        {max(stats['bibtex_entry_counts'])}")
    
    if stats['refs_arxiv_counts']:
        import statistics
        print(f"\n🔗 References với arXiv ID:")
        print(f"   ├─ Trung bình: {statistics.mean(stats['refs_arxiv_counts']):.1f}")
        print(f"   ├─ Median:     {statistics.median(stats['refs_arxiv_counts']):.0f}")
        print(f"   ├─ Min:        {min(stats['refs_arxiv_counts'])}")
        print(f"   └─ Max:        {max(stats['refs_arxiv_counts'])}")
    
    if stats['candidate_pair_counts']:
        import statistics
        print(f"\n🎯 Candidate Pairs:")
        print(f"   ├─ Trung bình: {statistics.mean(stats['candidate_pair_counts']):.1f}")
        print(f"   ├─ Median:     {statistics.median(stats['candidate_pair_counts']):.0f}")
        print(f"   ├─ Min:        {min(stats['candidate_pair_counts'])}")
        print(f"   └─ Max:        {max(stats['candidate_pair_counts'])}")
    
    print(f"\n{'='*80}")
    print(f"🏷️  PAPERS PHÙ HỢP CHO LABELING")
    print(f"{'='*80}\n")
    
    ready = stats["papers_ready_for_labeling"]
    auto_ready = stats["papers_for_auto_labeling"]
    
    print(f"✅ Papers phù hợp cho MANUAL labeling (>= 20 BibTeX entries, có candidates):")
    print(f"   Số lượng: {len(ready)}")
    
    if len(ready) > 0:
        print(f"\n   Top 10 papers tốt nhất:")
        print(f"   {'arxiv_id':<20} {'BibTeX':<8} {'Refs w/ arXiv':<15} {'Entries w/ Cands':<18} {'Cand Pairs':<12}")
        print(f"   {'-'*73}")
        for p in ready[:10]:
            print(f"   {p['arxiv_id']:<20} {p['bibtex_entries']:<8} {p['refs_with_arxiv']:<15} "
                  f"{p['entries_with_candidates']:<18} {p['candidate_pairs']:<12}")
    
    print(f"\n✅ Papers phù hợp cho AUTO labeling (>= 10 BibTeX entries, có candidates):")
    print(f"   Số lượng: {len(auto_ready)}")
    
    # Kiểm tra đủ điều kiện theo đề
    print(f"\n{'='*80}")
    print(f"📋 ĐÁNH GIÁ THEO YÊU CẦU ĐỀ BÀI (giả sử k=1 member)")
    print(f"{'='*80}\n")
    
    k = 1  # số thành viên, có thể thay đổi
    
    manual_needed = 5 * k
    manual_entries_needed = 100 * k
    auto_needed_pct = 10  # 10% papers ngoài manual pool
    auto_min_papers = 2 * k
    
    print(f"📌 Manual labeling:")
    print(f"   Cần: {manual_needed} papers × >= 20 entries/paper, tổng >= {manual_entries_needed} entries")
    print(f"   Có:  {len(ready)} papers phù hợp")
    
    if len(ready) >= manual_needed:
        total_entries = sum(p["bibtex_entries"] for p in ready[:manual_needed])
        print(f"   ✅ ĐỦ điều kiện! Top {manual_needed} papers có {total_entries} entries")
    else:
        print(f"   ❌ THIẾU {manual_needed - len(ready)} papers!")
    
    remaining_papers = len(auto_ready) - len(ready)
    auto_pool_size = max(stats["total_papers"] - manual_needed, 1)
    auto_needed = max(int(auto_pool_size * auto_needed_pct / 100), auto_min_papers)
    
    print(f"\n📌 Auto labeling:")
    print(f"   Cần: >= 10% papers ngoài manual pool = >= {auto_needed} papers (tối thiểu {auto_min_papers})")
    print(f"   Có:  {remaining_papers} papers phù hợp (ngoài manual pool)")
    
    if remaining_papers >= auto_needed:
        print(f"   ✅ ĐỦ điều kiện!")
    else:
        print(f"   ❌ THIẾU {auto_needed - remaining_papers} papers!")
    
    print()


def save_report(stats: Dict, output_file: Path) -> None:
    """Lưu report ra JSON."""
    report = {
        "summary": {
            "total_papers": stats["total_papers"],
            "has_elements": stats["has_elements"],
            "has_hierarchy": stats["has_hierarchy"],
            "has_refs": stats["has_refs"],
            "has_bibtex": stats["has_bibtex"],
            "has_candidates": stats["has_candidates"],
        },
        "papers_for_manual_labeling": stats["papers_ready_for_labeling"],
        "papers_for_auto_labeling": stats["papers_for_auto_labeling"],
    }
    
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"💾 Đã lưu report vào {output_file}")


def main():
    ap = argparse.ArgumentParser(description="Kiểm tra dữ liệu M1 & M2 cho M3")
    ap.add_argument("--corpus-dir", type=Path, required=True, help="Thư mục corpus (data/)")
    ap.add_argument("--limit", type=int, help="Giới hạn số papers để test nhanh")
    ap.add_argument("--output", type=Path, help="File output JSON (mặc định: corpus/m3_inspection.json)")
    args = ap.parse_args()
    
    if not args.corpus_dir.exists():
        print(f"❌ Không tìm thấy corpus dir: {args.corpus_dir}")
        return
    
    stats, all_papers = inspect_corpus(args.corpus_dir, args.limit)
    print_statistics(stats)
    
    output_file = args.output or (args.corpus_dir / "m3_inspection.json")
    save_report(stats, output_file)
    
    print(f"\n{'='*80}")
    print(f"✅ Hoàn thành! Xem chi tiết tại {output_file}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
