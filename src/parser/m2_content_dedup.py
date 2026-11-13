"""M2 - P3: từ cây thô (tree_raw.json) -> elements + hierarchy đã làm sạch, loại trùng xuyên version.

Đọc : data/<paper_key>/paper.json              (M1: arXiv ID gốc, n_versions)
      data/<paper_key>/vN/tree_raw.json        (m2_hierarchy.py)
      data/<paper_key>/cite_map.json           (bước gộp BibTeX, tùy chọn: {khóa_cũ: khóa_đã_chọn})
Ghi : data/<paper_key>/elements.json           cột: element_id, arxiv_id, kind, content
      data/<paper_key>/hierarchy.json          cột: arxiv_id, version, element_id, parent_id
      data/<paper_key>/parse_status.json       trạng thái từng version (ok | empty | missing) + số lượng trước/sau dedup

Chạy từ gốc repo:
    python src/parser/m2_content_dedup.py                      # mọi bài có paper.json
    python src/parser/m2_content_dedup.py --ids 2602.04870

Quy tắc dedup: một element (heading/sentence/formula/figure) có cùng (kind, nội dung đã clean)
ở nhiều version dùng chung một element_id. Trong MỘT version, lần xuất hiện thứ k của cùng
nội dung là một element riêng (để cây của version đó không bị nhập nhằng).
document / itemize / item không gộp (id riêng cho từng version).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import config as C  # noqa: E402
from m2_cleanup import (  # noqa: E402
    clean_figure, clean_heading, clean_text, normalize_block_math, split_segments, strip_comments,
)
from m2_sentences import split_sentences  # noqa: E402

log = logging.getLogger("m2_content_dedup")

REFERENCE_TITLES = {"references", "bibliography", "references and notes", "reference list"}
MERGEABLE = {"heading", "sentence", "formula", "figure"}
ID_PREFIX = {"heading": "h", "sentence": "s", "formula": "e", "figure": "g"}

_INCLUDEGRAPHICS_RE = re.compile(r"(\\includegraphics\s*(?:\[[^\]]*\])?\s*\{[^{}]*\})")
_CITE_RE = re.compile(r"(\\(?:no)?cite[A-Za-z]*\*?(?:\s*\[[^\]]*\])*\s*)\{([^{}]*)\}")


# ----------------------------------------------------------------------------- cite
def rename_cites(text: str, cite_map: Dict[str, str]) -> str:
    """Đổi khóa trong \\cite{a,b} theo cite_map, bỏ khóa trùng sau khi đổi."""
    if not cite_map:
        return text

    def repl(m):
        keys: List[str] = []
        for k in (x.strip() for x in m.group(2).split(",")):
            k = cite_map.get(k, k)
            if k and k not in keys:
                keys.append(k)
        return m.group(1) + "{" + ",".join(keys) + "}"

    return _CITE_RE.sub(repl, text)


# ----------------------------------------------------------------------------- cây thô -> danh sách nút
def flatten_tree(tree: dict, cite_map: Optional[Dict[str, str]] = None) -> List[dict]:
    """Trả về danh sách nút theo thứ tự tài liệu: {'kind','content','parent'(chỉ số hoặc None)}.

    nodes[0] luôn là document. Danh sách rỗng nếu version không có nội dung.
    """
    cite_map = cite_map or {}
    root = tree.get("root", tree)
    nodes: List[dict] = [{"kind": "document", "content": "", "parent": None}]

    def add(kind: str, content: str, parent: int) -> int:
        nodes.append({"kind": kind, "content": content, "parent": parent})
        return len(nodes) - 1

    def add_text(text: str, parent: int) -> None:
        for seg_kind, seg in split_segments(strip_comments(text or "")):
            if seg_kind == "text":
                # \includegraphics nằm ngoài môi trường figure (vd. trong \twocolumn[...]) vẫn là một hình
                pieces = _INCLUDEGRAPHICS_RE.split(seg)
                for number, piece in enumerate(pieces):
                    if number % 2:
                        fig = clean_figure(piece)
                        if fig:
                            add("figure", rename_cites(fig, cite_map), parent)
                        continue
                    for sent in split_sentences(clean_text(piece)):
                        add("sentence", rename_cites(sent, cite_map), parent)
            elif seg_kind == "formula":
                f = normalize_block_math(seg)
                if f:
                    add("formula", f, parent)
            else:
                add("figure", rename_cites(clean_figure(seg), cite_map), parent)

    def walk(node: dict, parent: int) -> None:
        t = node.get("type")
        children = node.get("children") or []
        if t == "section":
            title = clean_heading(node.get("title", ""))
            if title.lower().strip(" .:") in REFERENCE_TITLES:
                return
            idx = add("heading", rename_cites(title, cite_map), parent)
            for c in children:
                walk(c, idx)
        elif t in ("itemize", "enumerate", "description"):
            idx = add("itemize", "", parent)
            for c in children:
                walk(c, idx)
        elif t == "item":
            idx = add("item", "", parent)
            if node.get("text"):
                add_text(node["text"], idx)
            for c in children:
                walk(c, idx)
        elif t in ("equation", "formula"):
            f = normalize_block_math(node.get("text", ""))
            if f:
                add("formula", f, parent)
        elif t == "figure":
            f = clean_figure(node.get("text", ""))
            if f:
                add("figure", rename_cites(f, cite_map), parent)
        else:  # document, paragraph, text, loại lạ
            if node.get("text"):
                add_text(node["text"], parent)
            for c in children:
                walk(c, parent)

    walk(root, 0)
    return nodes if len(nodes) > 1 else []


# ----------------------------------------------------------------------------- đọc/ghi
def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path: Path, obj) -> None:
    C.ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


_VERSION_DIR_RE = re.compile(r"^v(\d+)$")


def list_versions(arxiv_id: str) -> List[Tuple[str, int]]:
    """[(label, số)] của mọi version: các thư mục vN đã có, cộng các version M1 biết là có
    (paper.json: n_versions) nhưng không tải được (không có thư mục) để thống kê coverage."""
    numbers = set()
    pdir = C.paper_dir(arxiv_id)
    if pdir.is_dir():
        for d in pdir.iterdir():
            m = _VERSION_DIR_RE.match(d.name)
            if d.is_dir() and m:
                numbers.add(int(m.group(1)))
    n_versions = (_read_json(pdir / "paper.json") or {}).get("n_versions")
    if isinstance(n_versions, int) and n_versions > 0:
        numbers.update(range(1, n_versions + 1))
    return [(f"v{n}", n) for n in sorted(numbers)]


# ----------------------------------------------------------------------------- xử lý một bài
def process_paper(arxiv_id: str) -> dict:
    key = C.paper_key(arxiv_id)
    cite_map = _read_json(C.cite_map_path(arxiv_id)) or {}

    element_ids: Dict[tuple, str] = {}   # (kind, content, lần xuất hiện) -> element_id
    counters: Counter = Counter()
    elements: List[dict] = []
    edges: List[dict] = []
    status: Dict[str, dict] = {}
    n_before = 0

    versions = list_versions(arxiv_id)
    if not versions:
        log.warning("%s: không có thư mục vN / paper.json, bỏ qua", arxiv_id)
        return {"arxiv_id": arxiv_id, "versions": {}, "n_versions": 0, "n_versions_ok": 0,
                "n_nodes_before_dedup": 0, "n_elements_after_dedup": 0}

    for label, n in versions:
        tree = _read_json(C.tree_raw_path(arxiv_id, label))
        if tree is None:  # M1 biết version này nhưng không tải được (hoặc chưa chạy m2_hierarchy)
            has_dir = C.version_dir(arxiv_id, label).is_dir()
            status[label] = {"status": "empty" if has_dir else "missing", "n_nodes": 0,
                             "reason": "no_tree_raw" if has_dir else "not_downloaded"}
            continue
        nodes = flatten_tree(tree, cite_map)
        if not nodes:  # version rỗng: ghi nhận, không bỏ qua
            status[label] = {"status": "empty", "n_nodes": 0,
                             "reason": tree.get("source_reason") or tree.get("error") or tree.get("status")}
            continue

        seen: Counter = Counter()
        local: Counter = Counter()
        ids: List[str] = []
        for nd in nodes:
            kind, content = nd["kind"], nd["content"]
            new = False
            if kind == "document":
                eid, content, new = f"{key}#root{n}", f"Version {n} root", True
            elif kind not in MERGEABLE:  # itemize / item
                local[kind] += 1
                eid, new = f"{key}#{'ul' if kind == 'itemize' else 'li'}{n}_{local[kind]}", True
            else:
                k = (kind, content, seen[(kind, content)])
                seen[(kind, content)] += 1
                if k not in element_ids:
                    counters[kind] += 1
                    element_ids[k] = f"{key}#{ID_PREFIX[kind]}{counters[kind]}"
                    new = True
                eid = element_ids[k]
            if new:
                elements.append({"element_id": eid, "arxiv_id": arxiv_id, "kind": kind, "content": content})
            ids.append(eid)
            if nd["parent"] is not None:
                edges.append({"arxiv_id": arxiv_id, "version": n, "element_id": eid,
                              "parent_id": ids[nd["parent"]]})
        n_before += len(nodes)
        status[label] = {"status": "ok", "n_nodes": len(nodes)}

    _write_json(C.elements_path(arxiv_id), elements)
    _write_json(C.hierarchy_path(arxiv_id), edges)
    summary = {
        "arxiv_id": arxiv_id,
        "versions": status,
        "n_versions": len(status),
        "n_versions_ok": sum(v["status"] == "ok" for v in status.values()),
        "n_nodes_before_dedup": n_before,
        "n_elements_after_dedup": len(elements),
    }
    _write_json(C.parse_status_path(arxiv_id), summary)
    return summary


def discover_ids() -> List[str]:
    """arXiv ID gốc của mọi bài có paper.json (do run_m1.py ghi) trong DATA_DIR."""
    ids = []
    for p in sorted(C.DATA_DIR.glob("*/paper.json")):
        d = _read_json(p)
        if d and d.get("arxiv_id"):
            ids.append(d["arxiv_id"])
    return ids


def process_all(ids: Optional[List[str]] = None) -> None:
    ids = ids or discover_ids()
    t0, tot_before, tot_after, ok = time.time(), 0, 0, 0
    for i, aid in enumerate(ids, 1):
        try:
            s = process_paper(aid)
        except Exception:  # noqa: BLE001 - một bài lỗi không làm dừng cả lô
            log.exception("Lỗi khi xử lý %s", aid)
            continue
        ok += 1
        tot_before += s["n_nodes_before_dedup"]
        tot_after += s["n_elements_after_dedup"]
        if i % 100 == 0 or i == len(ids):
            log.info("[%d/%d] %.0fs", i, len(ids), time.time() - t0)
    log.info("XONG %d/%d bài. Nút trước dedup=%d, element sau dedup=%d", ok, len(ids), tot_before, tot_after)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", nargs="*", help="chỉ xử lý các ID này (mặc định: mọi bài có paper.json)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    process_all(args.ids)


if __name__ == "__main__":
    main()