"""Canonical field extractor — replaces 14+ private ``_field`` copies.

Usage::

    from novel_forge.core.utils.field_extractor import field

    field({"a": {"b": 2}}, "a.b")          # → 2
    field(obj, "name", default="unknown")   # → getattr(obj, "name", "unknown")
    field({"n": "42"}, "n", type_cast=int)  # → 42
"""

from __future__ import annotations

from typing import Any

_SENTINEL = object()


def field(
    data: Any,
    path: str,
    default: Any = None,
    *,
    type_cast: type | None = None,
) -> Any:
    """Extract a value from *data* by dot-separated *path*.

    Supports both dict key access and object attribute access, and can
    traverse mixed dict ↔ object boundaries in a single path.

    Parameters
    ----------
    data:
        A dict, object, or nested combination thereof.
    path:
        Dot-separated key/attribute path (e.g. ``"a.b.c"``).
    default:
        Value returned when the path cannot be resolved.
    type_cast:
        Optional type to cast the resolved value to.  If casting fails
        (``TypeError`` / ``ValueError``), *default* is returned instead.
    """
    value = _resolve(data, path, default)

    if type_cast is not None and value is not default:
        try:
            value = type_cast(value)
        except (TypeError, ValueError):
            return default

    return value


def _resolve(data: Any, path: str, default: Any) -> Any:
    """Walk *path* segments through *data*, returning *default* on miss."""
    current: Any = data
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current.get(segment, _SENTINEL)
        else:
            current = getattr(current, segment, _SENTINEL)

        if current is _SENTINEL:
            return default
    return current
