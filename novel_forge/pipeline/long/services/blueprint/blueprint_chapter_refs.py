"""Utilities for explicit chapter references inside narrative blueprints."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_EXPLICIT_CHAPTER_REF_RE = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百千两〇零]+)\s*章")
_FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}


def extract_explicit_chapter_refs(value: Any) -> list[int]:
    """Return all explicit ``第N章`` references in nested blueprint text."""
    refs: set[int] = set()
    _collect_chapter_refs(value, refs)
    return sorted(refs)


def repair_explicit_chapter_refs(
    value: Any,
    *,
    start: int,
    end: int,
    total_chapters: int,
    replacement: str | Callable[[int, int, int], str],
) -> tuple[Any, bool]:
    """Replace explicit chapter refs that fall outside a bounded chapter range."""
    if value is None:
        return value, False

    bounded_start = max(1, int(start or 1))
    bounded_end = max(bounded_start, int(end or bounded_start))
    whole_total = max(0, int(total_chapters or 0))

    if isinstance(value, str):
        changed = False

        def replace_match(match: re.Match[str]) -> str:
            nonlocal changed
            chapter = _parse_chapter_ref(match.group(1))
            if chapter is None:
                return match.group(0)
            outside_total = bool(whole_total and (chapter < 1 or chapter > whole_total))
            outside_bound = chapter < bounded_start or chapter > bounded_end
            if not outside_total and not outside_bound:
                return match.group(0)
            changed = True
            if callable(replacement):
                return replacement(chapter, bounded_start, bounded_end)
            return replacement

        repaired = _EXPLICIT_CHAPTER_REF_RE.sub(replace_match, value)
        return (repaired, True) if changed else (value, False)

    if isinstance(value, list):
        changed = False
        repaired_items: list[Any] = []
        for item in value:
            repaired_item, item_changed = repair_explicit_chapter_refs(
                item,
                start=bounded_start,
                end=bounded_end,
                total_chapters=whole_total,
                replacement=replacement,
            )
            repaired_items.append(repaired_item)
            changed = changed or item_changed
        return (repaired_items, True) if changed else (value, False)

    if isinstance(value, tuple):
        repaired_items, changed = repair_explicit_chapter_refs(
            list(value),
            start=bounded_start,
            end=bounded_end,
            total_chapters=whole_total,
            replacement=replacement,
        )
        return (tuple(repaired_items), True) if changed else (value, False)

    if isinstance(value, dict):
        changed = False
        repaired_dict: dict[Any, Any] = {}
        for key, item in value.items():
            repaired_item, item_changed = repair_explicit_chapter_refs(
                item,
                start=bounded_start,
                end=bounded_end,
                total_chapters=whole_total,
                replacement=replacement,
            )
            repaired_dict[key] = repaired_item
            changed = changed or item_changed
        return (repaired_dict, True) if changed else (value, False)

    return value, False


def _collect_chapter_refs(value: Any, refs: set[int]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        for match in _EXPLICIT_CHAPTER_REF_RE.finditer(value):
            chapter = _parse_chapter_ref(match.group(1))
            if chapter is not None:
                refs.add(chapter)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_chapter_refs(item, refs)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_chapter_refs(item, refs)
        return
    if hasattr(value, "model_dump"):
        _collect_chapter_refs(value.model_dump(mode="json"), refs)


def _parse_chapter_ref(raw: str) -> int | None:
    normalized = raw.strip().translate(_FULLWIDTH_DIGIT_TRANS)
    if normalized.isdigit():
        return int(normalized)
    return _parse_chinese_number(normalized)


def _parse_chinese_number(value: str) -> int | None:
    if not value:
        return None
    if all(char in _CHINESE_DIGITS for char in value):
        number = int("".join(str(_CHINESE_DIGITS[char]) for char in value))
        return number if number > 0 else None
    total = 0
    current_digit = 0
    saw_token = False
    for char in value:
        if char in _CHINESE_DIGITS:
            current_digit = _CHINESE_DIGITS[char]
            saw_token = True
            continue
        unit = _CHINESE_UNITS.get(char)
        if unit is None:
            return None
        if current_digit == 0:
            current_digit = 1
        total += current_digit * unit
        current_digit = 0
        saw_token = True
    total += current_digit
    if not saw_token or total <= 0:
        return None
    return total


__all__ = ["extract_explicit_chapter_refs", "repair_explicit_chapter_refs"]
