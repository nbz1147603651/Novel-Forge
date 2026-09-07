"""Type coercion utilities for LLM output normalization.

Centralizes the coerce_int / coerce_float / coerce_optional_int helpers that
were previously duplicated across every Normalizer class in
``novel_forge.core.normalizers``.
"""

from __future__ import annotations

from typing import Any


def coerce_int(value: Any, default: int) -> int:
    """Safely cast *value* to ``int``, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_optional_int(value: Any) -> int | None:
    """Cast *value* to ``int`` or return ``None`` if empty / unparsable."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_float(
    value: Any,
    default: float = 0.0,
    *,
    clamp: tuple[float, float] | None = None,
) -> float:
    """Cast *value* to ``float``, with optional clamping.

    Parameters
    ----------
    value:
        Raw value from LLM output.
    default:
        Fallback when conversion fails (default ``0.0``).
    clamp:
        ``(low, high)`` bounds applied after conversion.
        ``None`` (the default) disables clamping.
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if clamp is not None:
        low, high = clamp
        return max(low, min(high, result))
    return result


def coerce_alive(value: Any) -> bool:
    """Interpret various LLM representations as a boolean *alive* flag."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"false", "0", "no", "dead", "deceased"}:
            return False
        if lowered in {"true", "1", "yes", "alive"}:
            return True
    return True
