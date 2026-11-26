import json, sys
path = sys.argv[1] if len(sys.argv) > 1 else "data/m2_stats.json"
rows = json.load(open(path, encoding="utf-8"))

cand = [r for r in rows
        if r.get("bibtex_from_bibitem", 0) >= 10          # nhiều entry từ \bibitem
        and r.get("references_with_arxiv_id", 0) >= 10    # có đủ reference arXiv để có nhãn
        and r.get("candidate_pairs", 0) > 0]              # M2 đã sinh candidate

cand.sort(key=lambda r: r["bibtex_from_bibitem"], reverse=True)
print(f"{len(cand)} bài thỏa điều kiện\n")
print(f"{'arxiv_id':<12}{'bibitem':>8}{'.bib':>6}{'refs_arxiv':>11}{'cands':>7}")
for r in cand[:10]:
    print(f"{r['arxiv_id']:<12}{r['bibtex_from_bibitem']:>8}{r['bibtex_from_bib_files']:>6}"
          f"{r['references_with_arxiv_id']:>11}{r['candidate_pairs']:>7}")