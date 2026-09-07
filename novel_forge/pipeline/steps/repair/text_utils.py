"""Shared text utilities for repair steps.

Provides common text processing functions used across:
- ContinuityRepairStep
- CausalRepairStep
- ReadingPowerRepairStep
"""

from __future__ import annotations

import json

from novel_forge.core.parsing.parse_utils import safe_parse_json, strip_markdown_fences
from novel_forge.obs.logger import get_logger

_logger = get_logger("pipeline.repair.text_utils")


def extract_revised_text(content: str) -> tuple[str, bool]:
    """Extract repaired prose from model output.

    Handles wrappers like:
    - ``</think>\\n\\n{...}``
    - ``{"revised_text": "..."} ``
    - plain text

    Returns:
        Tuple of (extracted_text, was_truncated).
        was_truncated is True when JSON parsing failed (likely truncated response).
    """
    raw = str(content or "")
    cleaned = strip_markdown_fences(raw).strip()

    if cleaned.startswith("<think>") and "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[1].strip()
    if cleaned.startswith("</think>"):
        cleaned = cleaned[len("</think>") :].strip()
    if cleaned.startswith("{"):
        try:
            data = safe_parse_json(cleaned)
        except (json.JSONDecodeError, ValueError):
            _logger.warning(
                "repair: JSON parse failed (truncated response?), "
                "discarding repair result and keeping original text"
            )
            return "", True
        if isinstance(data, dict):
            revised = data.get("revised_text") or data.get("content")
            if isinstance(revised, str) and revised.strip():
                return revised.strip(), False
    return cleaned, False


def non_space_len(text: str) -> int:
    """Count non-whitespace characters as a proxy for Chinese word count."""
    return sum(1 for c in text if not c.isspace())


def paragraph_count(text: str) -> int:
    """Count non-empty paragraphs separated by double newlines."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return max(1, len(paragraphs))