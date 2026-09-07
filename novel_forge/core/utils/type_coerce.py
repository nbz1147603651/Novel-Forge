"""Type coercion utilities for LLM text field normalization.

Provides coercion utilities for model-emitted text snippets:

- ``stringify_text_value`` flattens nested snippets into a single string.
- ``coerce_text_list`` keeps list-typed text fields as ``list[str]`` while
  still normalizing nested item values.

Standalone by design: no dependencies on ``bible.py`` or any schema module.
"""

from __future__ import annotations

from typing import Any


def stringify_text_value(value: Any) -> str:
    """Flatten model-emitted structured snippets into a single profile text field.

    Handles:
    - ``None`` / ``""`` / ``[]`` / ``{}`` → ``""``
    - ``str`` → stripped
    - ``dict`` → recursive ``"key: value"`` joined with ``；`` (Chinese semicolon),
      skipping ``None``/empty items
    - ``list`` → recursive ``stringify_text_value(item)`` for each item,
      joined with ``；``, skipping ``None``/empty items
    - ``int`` / ``float`` / ``bool`` → ``str(value).strip()``

    Examples
    --------
    >>> stringify_text_value(None)
    ''
    >>> stringify_text_value('hello')
    'hello'
    >>> stringify_text_value(42)
    '42'
    >>> stringify_text_value({'k1': 'v1', 'k2': None})
    'k1: v1'
    >>> stringify_text_value(['a', None, 'b'])
    'a；b'
    >>> stringify_text_value({'nested': {'deep': 'val'}})
    'nested: deep: val'
    >>> stringify_text_value([{'a': '1'}, {'b': '2'}])
    'a: 1；b: 2'
    """
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            rendered = stringify_text_value(item)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "；".join(parts)
    if isinstance(value, list):
        parts = [
            stringify_text_value(item)
            for item in value
            if item not in (None, "", [], {})
        ]
        return "；".join(part for part in parts if part)
    return str(value).strip()


def _preferred_mapping_text(
    value: dict[Any, Any],
    *,
    preferred_keys: tuple[str, ...],
) -> str:
    """Return the first non-empty preferred mapping value as flattened text."""

    if not preferred_keys:
        return ""
    for key in preferred_keys:
        if key not in value:
            continue
        text = stringify_text_value(value.get(key))
        if text:
            return text
    return ""


def coerce_text_list(
    value: Any,
    *,
    preferred_keys: tuple[str, ...] = (),
) -> list[str]:
    """Normalize model-emitted snippets into a ``list[str]``.

    This is the list-field counterpart to ``stringify_text_value``.  Existing
    lists remain lists, with each item flattened independently.  A scalar or
    object becomes a single-item list when it contains usable text.

    Examples
    --------
    >>> coerce_text_list(None)
    []
    >>> coerce_text_list('hello')
    ['hello']
    >>> coerce_text_list(['a', {'b': 'c'}])
    ['a', 'b: c']
    >>> coerce_text_list({'k': 'v'})
    ['k: v']
    """
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        normalized = [
            _preferred_mapping_text(item, preferred_keys=preferred_keys)
            if isinstance(item, dict)
            else stringify_text_value(item)
            for item in value
        ]
        normalized = [
            item if item else stringify_text_value(raw_item)
            for item, raw_item in zip(normalized, value, strict=False)
        ]
        return [item for item in normalized if item]
    if isinstance(value, dict):
        preferred_text = _preferred_mapping_text(value, preferred_keys=preferred_keys)
        if preferred_text:
            return [preferred_text]
    text = stringify_text_value(value)
    return [text] if text else []
