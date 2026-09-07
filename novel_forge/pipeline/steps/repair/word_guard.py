"""Word count guard for repair steps.

Provides a shared guard that checks whether a revised text's word count
ratio stays within acceptable bounds relative to the original.
"""

from __future__ import annotations

from novel_forge.pipeline.steps.repair.text_utils import non_space_len


def apply_word_guard(
    original: str,
    revised: str,
    guard_lo: float,
    guard_hi: float,
) -> tuple[str, bool]:
    """Check if revised text's word count ratio is within [guard_lo, guard_hi].

    Args:
        original: The original text before repair.
        revised: The repaired/revised text to validate.
        guard_lo: Lower bound ratio (inclusive). e.g. 0.60 means revised must be
            at least 60%% of original's length.
        guard_hi: Upper bound ratio (inclusive). e.g. 1.55 means revised must be
            at most 155%% of original's length.

    Returns:
        A tuple of (text, guard_triggered):
        - If guard_triggered is False: revised text is within bounds, returned as-is.
        - If guard_triggered is True: text was out of bounds, original text returned
          to signal guard was triggered.

    Examples:
        >>> apply_word_guard("hello world", "hello", 0.5, 2.0)
        ('hello', False)  # ratio 5/11 ≈ 0.45 < 0.5, guard triggered
        >>> apply_word_guard("hello world", "hello there world", 0.5, 2.0)
        ('hello there world', False)  # ratio 16/11 ≈ 1.45, within bounds
    """
    orig_len = non_space_len(original)
    if orig_len == 0:
        return revised, False

    revised_len = non_space_len(revised)
    ratio = revised_len / orig_len

    if ratio < guard_lo or ratio > guard_hi:
        return original, True

    return revised, False