"""Utilities for parsing model responses that may contain Markdown fencing.

This module provides backward-compatible access to JSON parsing utilities.
Core implementations have been moved to novel_forge.core.utils.json.

Text-content helpers (e.g. extract_text_content) live in
novel_forge.core.text_utils — import from there directly.
"""

from __future__ import annotations

import logging
from typing import Any

# Import implementations from new location for backward compatibility
from novel_forge.core.utils.json import (
    JSONRepairMode,
    strip_markdown_fences,
    try_repair_json,
)
from novel_forge.core.utils.json import (
    _escape_unescaped_quotes as _escape_impl,
)
from novel_forge.core.utils.json import (
    _extract_balanced_json as _extract_impl,
)
from novel_forge.core.utils.json import (
    safe_parse_json as _safe_parse_impl,
)

logger = logging.getLogger(__name__)

__all__ = [
    "JSONRepairMode",
    "safe_parse_json",
    "strip_markdown_fences",
    "try_repair_json",
    "_try_repair_json",
    "_escape_unescaped_quotes",
    "_extract_balanced_json",
]


def _try_repair_json(text: str) -> str:
    """Best-effort repair of truncated JSON (legacy wrapper).
    
    This function is kept for backward compatibility.
    New code should use try_repair_json with JSONRepairMode directly.
    """
    result = try_repair_json(text, mode=JSONRepairMode.LENIENT)
    return result.json_text if result.success and result.json_text is not None else text


def _escape_unescaped_quotes(text: str) -> str:
    """Attempt to escape unescaped quotes in JSON string values (legacy import).
    
    This function is now implemented in novel_forge.core.utils.json.
    """
    return _escape_impl(text)


def _extract_balanced_json(text: str) -> str | None:
    """Extract the longest balanced JSON object/array (legacy import).
    
    This function is now implemented in novel_forge.core.utils.json.
    """
    return _extract_impl(text)


def safe_parse_json(text: str) -> Any:
    """Parse a JSON string that may be wrapped in Markdown fences (legacy import).
    
    This function is now implemented in novel_forge.core.utils.json.
    New code should import directly from novel_forge.core.utils.json.
    """
    return _safe_parse_impl(text)
