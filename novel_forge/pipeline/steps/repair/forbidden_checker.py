"""Forbidden element detection utilities for repair steps.

Provides unified forbidden element detection logic extracted from continuity_eval_step.
"""

from __future__ import annotations

# Re-export for direct usage (e.g., when tuple results are needed)
from novel_forge.pipeline.steps.continuity_eval_step import (  # noqa: E402
    _detect_forbidden_elements,
)


def check_forbidden(text: str, forbidden: list[str]) -> list[str]:
    """Convenience function to detect forbidden elements and return matched element names.

    Args:
        text: The text to check for forbidden elements.
        forbidden: List of forbidden element strings to check against.

    Returns:
        List of matched forbidden elements (deduplicated, order of first appearance).
    """
    # Lazy import to avoid potential circular dependency
    from novel_forge.pipeline.steps.continuity_eval_step import (
        _detect_forbidden_elements,
    )

    results = _detect_forbidden_elements(text, forbidden)
    seen: set[str] = set()
    matched: list[str] = []
    for element, _ in results:
        if element not in seen:
            seen.add(element)
            matched.append(element)
    return matched


__all__ = ["check_forbidden", "_detect_forbidden_elements"]
