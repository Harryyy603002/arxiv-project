r"""m3_pick_papers.py

Milestone: M3 (chuẩn bị gán nhãn)
Mục đích: liệt kê các bài phù hợp để gán nhãn thủ công ở M3, dựa trên <corpus>/m2_stats.json
          (do run_m2.py ghi). Một bài gán nhãn tốt cần nhiều entry BibTeX (đề M3: ít nhất 20 entry
          được gán nhãn mỗi bài) và nhiều reference có arXiv ID để có đáp án đúng để ghép.

Chạy (từ thư mục gốc repo):
    python src/parser/m3_pick_papers.py --corpus-dir data_m2
    python src/parser/m3_pick_papers.py --corpus-dir /content/corpus --min-entries 25 --top 40 --csv picks.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="Chọn bài phù hợp để gán nhãn thủ công (M3).")
    ap.add_argument("--corpus-dir", type=Path, required=True)
    ap.add_argument("--min-entries", type=int, default=20, help="số entry BibTeX tối thiểu (mặc định 20)")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--csv", type=Path, help="ghi kết quả ra file CSV")
    args = ap.parse_args()

    stats = json.loads((args.corpus_dir / "m2_stats.json").read_text(encoding="utf-8"))
    rows = []
    for s in stats:
        if "canonical_entries" not in s:
            continue
        rows.append({
            "arxiv_id": s["arxiv_id"],
            "bibtex_entries": s["canonical_entries"],
            "refs_with_arxiv_id": s["references_with_arxiv_id"],
            "entries_with_candidates": s["entries_with_candidates"],
            "candidate_pairs": s["candidate_pairs"],
        })
    good = [r for r in rows if r["bibtex_entries"] >= args.min_entries and r["entries_with_candidates"] > 0]
    good.sort(key=lambda r: (-r["entries_with_candidates"], -r["refs_with_arxiv_id"]))
    print(f"{len(rows)} bài có BibTeX | {len(good)} bài đạt (>= {args.min_entries} entry và có ứng viên)")
    print(f"{'arxiv_id':18}{'entry':>7}{'ref có arXiv':>14}{'entry có ứng viên':>19}{'cặp':>7}")
    for r in good[:args.top]:
        print(f"{r['arxiv_id']:18}{r['bibtex_entries']:>7}{r['refs_with_arxiv_id']:>14}"
              f"{r['entries_with_candidates']:>19}{r['candidate_pairs']:>7}")
    if args.csv:
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["arxiv_id"])
            writer.writeheader()
            writer.writerows(good)
        print(f"Đã ghi {len(good)} dòng vào {args.csv}")


if __name__ == "__main__":
    main()
