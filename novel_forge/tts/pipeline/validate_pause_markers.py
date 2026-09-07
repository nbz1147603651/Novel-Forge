"""Pause-marker validation and auto-fix for MiniMax ``<#X#>`` syntax.

Ported from ``Reference/audiobook/scripts/validate_pause_markers.py``
(Phase 2 Sub-step 1.5), which was battle-tested against real LLM output.
Auto-fixes the common mistakes:

- ``<#0.3-0.5#>`` / ``<#0.5~0.8#>`` -> ``<#0.4#>`` (range averaged)
- ``<#0.6s#>`` -> ``<#0.6#>`` (spurious ``s`` suffix — the ``s`` suffix
  makes the TTS read the marker as literal text, producing audio ~3x
  longer than expected)
- ``<#0.6>`` -> ``<#0.6#>`` (missing closing ``#``)
- ``<# 0.6 #>`` -> ``<#0.6#>`` (whitespace around the number)

Unfixable residual markers are reported so callers can decide whether to
block or degrade.  The fix order matters: ranges are handled first, then
the ``s`` suffix, then missing closing ``#``, then whitespace.

Author: novel-forge
"""
from __future__ import annotations

import re

from novel_forge.obs.logger import get_logger

_log = get_logger(__name__)

# ── Fix patterns (order matters — mirrors the reference implementation) ─────

# 1. Range pattern: <#0.3-0.5#> or <#0.5~0.8#> -> average
RANGE_PATTERN = re.compile(r"<#\s*(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*#?>")

# 2. Spurious 's' suffix: <#0.6s#> -> <#0.6#>
S_SUFFIX_PATTERN = re.compile(r"<#\s*(\d+(?:\.\d+)?)\s*s\s*#>")

# 3. Missing closing #: <#0.6> -> <#0.6#>
MISSING_HASH_PATTERN = re.compile(r"<#\s*(\d+(?:\.\d+)?)\s*>")

# 4. Whitespace around number: <# 0.6 #> -> <#0.6#>
WHITESPACE_PATTERN = re.compile(r"<#\s+(\d+(?:\.\d+)?)\s+#>")

# ── Validation patterns ──────────────────────────────────────────────────────
VALID_MARKER = re.compile(r"<#\d+(?:\.\d+)?#>")
MAYBE_MARKER = re.compile(r"<#[^>]*>")


def _fix_range(match: re.Match[str]) -> str:
    low, high = float(match.group(1)), float(match.group(2))
    average = round((low + high) / 2, 2)
    return f"<#{int(average) if average == int(average) else average}#>"


def _fix_s_suffix(match: re.Match[str]) -> str:
    return f"<#{match.group(1)}#>"


def _fix_missing_hash(match: re.Match[str]) -> str:
    return f"<#{match.group(1)}#>"


def _fix_whitespace(match: re.Match[str]) -> str:
    return f"<#{match.group(1)}#>"


def auto_fix_pause_markers(text: str) -> tuple[str, int]:
    """Auto-fix common pause-marker mistakes.

    Args:
        text: Text possibly containing malformed pause markers.

    Returns:
        ``(fixed_text, fix_count)``.  When no fixes were applied the input
        string is returned unchanged (same object identity).
    """
    original = text
    text, count = RANGE_PATTERN.subn(_fix_range, text)
    if count:
        _log.debug("Pause markers: %d range(s) averaged", count)
    text, s_count = S_SUFFIX_PATTERN.subn(_fix_s_suffix, text)
    if s_count:
        _log.debug("Pause markers: %d spurious 's' suffix(es) removed", s_count)
    text, hash_count = MISSING_HASH_PATTERN.subn(_fix_missing_hash, text)
    if hash_count:
        _log.debug("Pause markers: %d missing closing '#' fixed", hash_count)
    text, ws_count = WHITESPACE_PATTERN.subn(_fix_whitespace, text)
    if ws_count:
        _log.debug("Pause markers: %d whitespace issue(s) cleaned", ws_count)
    total = count + s_count + hash_count + ws_count
    return (text, total) if total else (original, 0)


def find_invalid_pause_markers(text: str) -> list[str]:
    """Return unfixable malformed pause markers in *text*.

    A marker is invalid when it looks like a marker (``<#...>``) but does
    not match the strict ``<#\\d+(?:\\.\\d+)?#>`` shape.
    """
    invalid: list[str] = []
    for match in MAYBE_MARKER.finditer(text):
        if not VALID_MARKER.match(match.group()):
            invalid.append(match.group())
    return invalid


def validate_pause_markers(text: str) -> tuple[bool, str]:
    """Validate and auto-fix pause markers in one call.

    Returns ``(valid, fixed_text_or_reason)``.  When valid, the second item
    is the fixed text; when invalid, it is a human-readable failure reason.
    """
    fixed, _count = auto_fix_pause_markers(text)
    invalid = find_invalid_pause_markers(fixed)
    if invalid:
        return False, f"unfixable_pause_markers={','.join(sorted(set(invalid)))}"
    return True, fixed
