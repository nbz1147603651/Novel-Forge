"""Text utilities shared by Repair Orchestration v2 core and handlers."""

from __future__ import annotations

from novel_forge.core.utils.text_validation import text_change_ratio as _canonical_text_change_ratio


def text_change_ratio(before: str, after: str) -> float:
    """Return a coarse bounded change ratio for repair budget checks.

    Delegates to the canonical implementation in ``core.utils.text_validation``.
    """
    return _canonical_text_change_ratio(before, after)
