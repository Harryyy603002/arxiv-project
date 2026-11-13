"""Deterministic BibTeX deduplication and citation-key rewriting."""

from __future__ import annotations

import re
from collections import defaultdict

from matching.m3_candidates import normalize_authors, normalize_text


def _merge_value(current: str | None, incoming: str | None) -> tuple[str | None, bool]:
    if current:
        return current, bool(incoming and incoming != current)
    return incoming or None, False


def merge_entries(entries: list[dict]) -> tuple[list[dict], dict[str, str], list[dict]]:
    """Merge duplicate keys, returning entries, replacements, and conflicts."""
    merged = {}
    replacements = {}
    conflicts = []
    for entry in entries:
        key = entry["citation_key"]
        if key not in merged:
            merged[key] = dict(entry)
            continue
        target = merged[key]
        for field, value in entry.items():
            if field in {"citation_key", "arxiv_id", "version"} or not value:
                continue
            chosen, conflict = _merge_value(target.get(field), value)
            target[field] = chosen
            if conflict:
                conflicts.append({"citation_key": key, "field": field,
                                  "kept": target[field], "discarded": value})
        source = entry.get("arxiv_id", "")
        if source and target.get("arxiv_id") and source != target["arxiv_id"]:
            conflicts.append({"citation_key": key, "field": "arxiv_id",
                              "kept": target["arxiv_id"], "discarded": source})
    return list(merged.values()), replacements, conflicts


def deduplicate_keys(entries: list[dict]) -> tuple[list[dict], dict[str, str], list[dict]]:
    """Deduplicate by normalized title/author identity and assign stable keys."""
    groups = defaultdict(list)
    for entry in entries:
        identity = (
            normalize_text(entry.get("title", "")),
            normalize_authors(entry.get("author", entry.get("authors", ""))),
            str(entry.get("year", "")),
        )
        if not any(identity):
            identity = ("key", entry["citation_key"], "")
        groups[identity].append(entry)

    canonical_entries = []
    replacements = {}
    conflicts = []
    for group in groups.values():
        group = sorted(group, key=lambda item: (item["citation_key"], item.get("version", 0) or 0))
        canonical = group[0]["citation_key"]
        merged = dict(group[0])
        for duplicate in group[1:]:
            replacements[duplicate["citation_key"]] = canonical
            for field, value in duplicate.items():
                if field in {"citation_key", "arxiv_id", "version"} or not value:
                    continue
                chosen, conflict = _merge_value(merged.get(field), value)
                merged[field] = chosen
                if conflict:
                    conflicts.append({"citation_key": canonical, "field": field,
                                      "kept": chosen, "discarded": value})
        canonical_entries.append(merged)
    return canonical_entries, replacements, conflicts


def rewrite_citations_with_count(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    """Rewrite active citation keys and return the rewritten-key occurrence count.
    Solves limitations: nested-braces, multiline-commands, inline-comments, escaped-percents.
    """
    result = []
    rewritten_count = 0
    i = 0
    n = len(text)
    
    while i < n:
        if text[i] == '\\':
            # 1. Xử lý dấu phần trăm bị escape: \% (không phải là comment)
            if i + 1 < n and text[i+1] == '%':
                result.append('\\%')
                i += 2
                continue
            
            # 2. Xử lý lệnh \cite (bao gồm cả \citep, \citet... và các ngoặc vuông tùy chọn lùi dòng)
            cite_match = re.match(r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*)*\{", text[i:])
            if cite_match:
                prefix = cite_match.group(0)
                result.append(prefix)
                i += len(prefix)
                
                # Quét để tìm dấu ngoặc đóng '}', hỗ trợ ngoặc lồng nhau và xuống dòng
                brace_level = 1
                start_keys = i
                while i < n and brace_level > 0:
                    if text[i] == '\\':
                        # Bỏ qua các ký tự bị escape bên trong (ví dụ \{ hoặc \})
                        i += 2
                        continue
                    elif text[i] == '{':
                        brace_level += 1
                    elif text[i] == '}':
                        brace_level -= 1
                    
                    if brace_level > 0:
                        i += 1
                        
                keys_str = text[start_keys:i]
                
                # Tách các mã trích dẫn, đối chiếu với map và tăng biến đếm
                rewritten_keys = []
                for k in keys_str.split(','):
                    original_k = k.strip()
                    if original_k in replacements:
                        rewritten_count += 1
                        # Thay thế nhưng vẫn giữ nguyên khoảng trắng/xuống dòng gốc của tác giả
                        rewritten_keys.append(k.replace(original_k, replacements[original_k]))
                    else:
                        rewritten_keys.append(k)
                        
                result.append(",".join(rewritten_keys))
                
                if brace_level == 0:
                    result.append('}')
                    i += 1
                continue
                
            # Nếu không phải \% hoặc \cite, chép lại dấu \ và đi tiếp
            result.append('\\')
            i += 1
            
        elif text[i] == '%':
            # 3. Xử lý comment: phát hiện dấu % ở bất kỳ đâu (kể cả giữa dòng),
            # bỏ qua tất cả cho tới khi hết dòng (gặp \n).
            start = i
            while i < n and text[i] != '\n':
                i += 1
            result.append(text[start:i])
            # Không tăng i qua ký tự \n, để vòng lặp sau tự chép ký tự \n vào kết quả
            
        else:
            result.append(text[i])
            i += 1
            
    return "".join(result), rewritten_count


def rewrite_citations(text: str, replacements: dict[str, str]) -> str:
    """Rewrite citation keys without changing commented-out lines."""
    return rewrite_citations_with_count(text, replacements)[0]
