"""Shape-level repairs for parsed LLM JSON payloads.

These helpers run after JSON decoding but before strict schema validation.
They keep common model drift, such as a single string where an array is
expected, out of domain schemas and pipeline services.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_TEXT_LIST_SEPARATORS = ("\r\n", "\n", "；", ";", "，", ",", "、", "|")


def _dedupe_text(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def parse_json_object_string(value: Any) -> dict[str, Any] | None:
    """Parse a JSON object that arrived as a string, returning ``None`` on miss."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_json_array_string(value: str) -> list[Any] | None:
    text = value.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def coerce_llm_string_list(value: Any, *, split_commas: bool = True) -> list[str]:
    """Normalize common LLM list drift into a deduplicated ``list[str]``."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parsed_array = _parse_json_array_string(text)
        if parsed_array is not None:
            return coerce_llm_string_list(parsed_array, split_commas=split_commas)
        separators = _TEXT_LIST_SEPARATORS if split_commas else _TEXT_LIST_SEPARATORS[:3]
        for separator in separators:
            text = text.replace(separator, "\n")
        return _dedupe_text(text.splitlines())
    if isinstance(value, Mapping):
        items: list[str] = []
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            item_value = str(raw_value or "").strip()
            if key and item_value:
                items.append(f"{key}: {item_value}")
            elif key:
                items.append(key)
            elif item_value:
                items.append(item_value)
        return _dedupe_text(items)
    if isinstance(value, (list, tuple, set)):
        list_items: list[str] = []
        for item in value:
            if isinstance(item, (list, tuple, set, Mapping)):
                list_items.extend(coerce_llm_string_list(item, split_commas=split_commas))
            else:
                text = str(item or "").strip()
                if text:
                    list_items.append(text)
        return _dedupe_text(list_items)
    text = str(value or "").strip()
    return [text] if text else []


def coerce_dependency_ref_list(value: Any) -> list[str]:
    """Normalize subplot dependency references to ``['支线名:章节号']`` strings."""
    if isinstance(value, Mapping):
        name = (
            value.get("subplot")
            or value.get("subplot_name")
            or value.get("line")
            or value.get("name")
            or value.get("target_subplot")
            or value.get("source_ref")
        )
        chapter = (
            value.get("chapter")
            or value.get("chapter_number")
            or value.get("chapter_no")
            or value.get("trigger_chapter")
        )
        if str(name or "").strip() and str(chapter or "").strip():
            return [f"{str(name).strip()}:{str(chapter).strip()}"]
        for key in ("ref", "reference", "dependency", "depends_on", "value", "id"):
            if str(value.get(key) or "").strip():
                return coerce_dependency_ref_list(value.get(key))
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(coerce_dependency_ref_list(item))
        return _dedupe_text(items)
    return coerce_llm_string_list(value, split_commas=True)


__all__ = [
    "coerce_dependency_ref_list",
    "coerce_llm_string_list",
    "parse_json_object_string",
]
