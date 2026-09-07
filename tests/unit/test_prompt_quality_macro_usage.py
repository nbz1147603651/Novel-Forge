"""Guardrails for quality-macro usage in prompt templates."""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_ROOT = Path("novel_forge/prompts/prompts")


def test_quality_macros_are_not_rewritten_with_replace_chains() -> None:
    """Shared quality macros must be parameterized instead of string-rewritten."""
    pattern = re.compile(
        r"\{\{\s*(forbidden_system_markers|anti_exposition_rules|sensory_diversity)\([^}]*\)\s*\|\s*replace\("  # noqa: E501
    )

    offenders: list[str] = []
    for path in _PROMPTS_ROOT.rglob("*.j2"):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(str(path))

    assert not offenders, f"Macro replace-chains found in: {offenders}"
