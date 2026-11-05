"""Deterministic BibTeX deduplication and citation-key rewriting."""

from __future__ import annotations

import re
from collections import defaultdict


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
            " ".join(str(entry.get("title", "")).lower().split()),
            " ".join(str(entry.get("author", "")).lower().split()),
            str(entry.get("year", "")),
        )
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


_CITE_RE = re.compile(r"\\cite[a-zA-Z*]*\s*\{([^{}]*)\}")


def rewrite_citations(text: str, replacements: dict[str, str]) -> str:
    """Rewrite citation keys without changing commented-out lines."""
    lines = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("%"):
            lines.append(line)
            continue

        def replace(match: re.Match[str]) -> str:
            keys = [key.strip() for key in match.group(1).split(",")]
            rewritten = [replacements.get(key, key) for key in keys]
            return match.group(0).replace(match.group(1), ",".join(rewritten))

        lines.append(_CITE_RE.sub(replace, line))
    return "".join(lines)
