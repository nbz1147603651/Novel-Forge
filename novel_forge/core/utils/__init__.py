"""Core utilities package - organized tooling for common operations.

This package provides structured access to commonly-used utility functions:
- String manipulation (trim_text, normalize_input_list, etc.)
- JSON parsing and repair (safe_parse_json, try_repair_json, etc.)
- Validation utilities (is_valid_json, validate_keys, etc.)
- Collection utilities (group_by, deduplicate_list, etc.)
- Pipeline helpers (save_and_emit, validate_threshold, etc.)
"""

from __future__ import annotations

# Coerce utilities
from .coerce import (
    coerce_alive,
    coerce_float,
    coerce_int,
    coerce_optional_int,
)

# Collection utilities
from .collections import (
    chunk_list,
    deduplicate_list,
    flatten_list,
    group_by,
    merge_dicts,
    partition_list,
    unique_by,
)

# JSON utilities
from .json import (
    JSONRepairMode,
    JSONRepairResult,
    safe_parse_json,
    strip_markdown_fences,
    try_repair_json,
)

# Pipeline helpers
from .pipeline_helpers import (
    format_repair_actions,
    has_prompt_leaks,
    join_repair_actions,
    normalize_threshold,
    safe_bool,
    safe_get,
    save_and_emit,
    validate_threshold,
)

# String utilities
from .string import (
    clean_str,
    estimate_tokens,
    extract_and_trim,
    extract_text_content,
    normalize_input_list,
    normalize_string_list,
    trim_text,
)

# Validation utilities
from .validation import (
    ensure_dict,
    ensure_list,
    is_empty,
    is_non_empty,
    is_valid_json,
    safe_coerce_type,
    validate_keys,
)

__all__ = [
    # Coerce utilities
    "coerce_int",
    "coerce_optional_int",
    "coerce_float",
    "coerce_alive",
    # String utilities
    "trim_text",
    "extract_and_trim",
    "clean_str",
    "normalize_input_list",
    "normalize_string_list",
    "estimate_tokens",
    "extract_text_content",
    # JSON utilities
    "JSONRepairMode",
    "JSONRepairResult",
    "strip_markdown_fences",
    "try_repair_json",
    "safe_parse_json",
    # Validation utilities
    "is_valid_json",
    "validate_keys",
    "safe_coerce_type",
    "is_non_empty",
    "is_empty",
    "ensure_list",
    "ensure_dict",
    # Collection utilities
    "deduplicate_list",
    "group_by",
    "merge_dicts",
    "flatten_list",
    "chunk_list",
    "partition_list",
    "unique_by",
    # Pipeline helpers
    "save_and_emit",
    "validate_threshold",
    "normalize_threshold",
    "has_prompt_leaks",
    "format_repair_actions",
    "join_repair_actions",
    "safe_get",
    "safe_bool",
]
