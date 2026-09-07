"""Helpers for cross-chapter boundary context windows."""

from __future__ import annotations

from novel_forge.core.utils.patch_utils import split_paragraphs

DEFAULT_PREVIOUS_TAIL_PARAGRAPHS = 5
DEFAULT_OPENING_PARAGRAPHS = 3


def coerce_paragraph_count(
    value: object,
    *,
    default: int,
    minimum: int = 1,
    maximum: int = 12,
) -> int:
    """Coerce a user-facing paragraph count into a bounded integer."""
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        count = int(default)
    return max(minimum, min(maximum, count))


def story_paragraphs(text: str) -> list[str]:
    """Return non-empty story paragraphs using the patch engine splitter."""
    return [paragraph for paragraph in split_paragraphs(str(text or "")) if paragraph.strip()]


def take_tail_paragraphs(
    text: str,
    count: object,
    *,
    default: int = DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
    max_chars: int | None = None,
) -> str:
    """Take the final N paragraphs, then optionally cap by trailing characters."""
    paragraphs = story_paragraphs(text)
    if not paragraphs:
        return ""
    tail_count = coerce_paragraph_count(count, default=default)
    result = "\n\n".join(paragraphs[-tail_count:]).strip()
    if max_chars is not None and max_chars > 0 and len(result) > max_chars:
        result = result[-max_chars:]
        first_break = result.find("\n\n")
        if first_break != -1:
            result = result[first_break + 2 :].strip()
    return result


def take_head_paragraphs(
    text: str,
    count: object,
    *,
    default: int = DEFAULT_OPENING_PARAGRAPHS,
    max_chars: int | None = None,
) -> str:
    """Take the first N paragraphs, then optionally cap by leading characters."""
    paragraphs = story_paragraphs(text)
    if not paragraphs:
        return ""
    head_count = coerce_paragraph_count(count, default=default, maximum=8)
    result = "\n\n".join(paragraphs[:head_count]).strip()
    if max_chars is not None and max_chars > 0 and len(result) > max_chars:
        return result[:max_chars].rstrip()
    return result


def paragraph_range_label(start: int, end: int, *, prefix: str = "第") -> str:
    """Format a 1-based paragraph range for prompt and UI diagnostics."""
    start = max(1, int(start or 1))
    end = max(start, int(end or start))
    return f"{prefix}{start}段" if start == end else f"{prefix}{start}-{end}段"
