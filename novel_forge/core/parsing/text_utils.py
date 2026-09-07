"""Backward-compatible string utility exports.

This module keeps legacy imports stable while the canonical implementations
live in ``novel_forge.core.utils.string``.
"""

from __future__ import annotations

from novel_forge.core.utils.string import (
    calculate_safe_max_tokens,
    clean_str,
    estimate_tokens,
    extract_and_trim,
    extract_text_content,
    normalize_input_list,
    normalize_string_list,
    trim_text,
)

__all__ = [
    "calculate_safe_max_tokens",
    "clean_str",
    "estimate_tokens",
    "extract_and_trim",
    "extract_text_content",
    "normalize_input_list",
    "normalize_string_list",
    "trim_text",
]
