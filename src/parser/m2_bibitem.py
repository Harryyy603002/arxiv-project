"""Parse BibTeX entries from source archives into stable dictionaries."""

from __future__ import annotations

import re
from typing import Iterable

_ENTRY_START = re.compile(r"@(?P<entry_type>[A-Za-z]+)\s*[{(]\s*(?P<key>[^,\s]+)\s*,")
_FIELD_START = re.compile(r"(?P<name>[A-Za-z][A-Za-z0-9_-]*)\s*=")


def _balanced_entries(text: str) -> Iterable[str]:
    for match in re.finditer(r"@[A-Za-z]+\s*[{(]", text):
        opening = text[match.end() - 1]
        closing = "}" if opening == "{" else ")"
        depth = 0
        quoted = False
        escaped = False
        for index in range(match.end() - 1, len(text)):
            char = text[index]
            if quoted and char == '"' and not escaped:
                quoted = False
            elif not quoted and char == '"' and depth > 0:
                quoted = True
            elif not quoted and char == opening:
                depth += 1
            elif not quoted and char == closing:
                depth -= 1
                if depth == 0:
                    yield text[match.start():index + 1]
                    break
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False


def _value_end(text: str, start: int) -> int:
    if start >= len(text):
        return start
    if text[start] == "{":
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return len(text)
    if text[start] == '"':
        index = start + 1
        while index < len(text):
            if text[index] == '"' and text[index - 1] != "\\":
                return index + 1
            index += 1
        return len(text)
    return re.search(r",\s*[A-Za-z][A-Za-z0-9_-]*\s*=", text[start:]).start() + start if re.search(
        r",\s*[A-Za-z][A-Za-z0-9_-]*\s*=", text[start:]
    ) else len(text)


def _clean_value(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    if len(value) >= 2 and (
        (value[0] == "{" and value[-1] == "}")
        or (value[0] == '"' and value[-1] == '"')
    ):
        value = value[1:-1]
    return re.sub(r"\s+", " ", value).strip()


def parse_bibtex_entries(text: str, arxiv_id: str | None = None,
                         version: int | None = None) -> list[dict]:
    """Parse entries while preserving source and version provenance."""
    entries = []
    for raw in _balanced_entries(text):
        header = _ENTRY_START.search(raw)
        if not header:
            continue
        fields = {}
        body = raw[:-1]
        body_start = header.end()
        for field in _FIELD_START.finditer(body, body_start):
            value_start = field.end()
            value_end = _value_end(body, value_start)
            fields[field.group("name").lower()] = _clean_value(body[value_start:value_end])
        entry = {
            "citation_key": header.group("key").strip(),
            "entry_type": header.group("entry_type").lower(),
            **fields,
        }
        if arxiv_id is not None:
            entry["arxiv_id"] = arxiv_id
        if version is not None:
            entry["version"] = version
        entries.append(entry)
    return entries


def parse_bib_files(files: Iterable[tuple[str, str, int | None]]) -> list[dict]:
    """Parse ``(arxiv_id, text, version)`` tuples in deterministic order."""
    entries = []
    for arxiv_id, text, version in files:
        entries.extend(parse_bibtex_entries(text, arxiv_id, version))
    return entries
