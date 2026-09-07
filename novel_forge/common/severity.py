"""Canonical severity ranking and normalization utilities.

This module provides the single source of truth for severity levels across
the Novel Forge codebase. It consolidates multiple incompatible severity
systems into one canonical 8-level hierarchy.

Severity Levels (highest to lowest):
    critical (5) > high (4) > major (3) = warning (3) > medium (2) > low (1) = minor (1) > info (0)

Note: major/warning and low/minor share ranks intentionally. This supports
reverse-rank patterns that will be handled explicitly during migration.
"""

from __future__ import annotations

from typing import Any

# Canonical severity ranking: 8-level superset covering all existing systems.
# Higher value = more severe. Used for threshold comparisons.
SEVERITY_RANK: dict[str, int] = {
    "critical": 5,
    "high": 4,
    "major": 3,
    "warning": 3,
    "medium": 2,
    "low": 1,
    "minor": 1,
    "info": 0,
}

# Common aliases mapped to canonical labels.
_SEVERITY_ALIASES: dict[str, str] = {
    # Common abbreviations and variants
    "crit": "critical",
    "fatal": "critical",
    "error": "high",
    "err": "high",
    "warn": "warning",
    "med": "medium",
    "normal": "medium",
    "default": "medium",
    "debug": "info",
    "trace": "info",
    "notice": "low",
    "trivial": "minor",
    "negligible": "minor",
}

# Default severity for unknown/unrecognized values.
_DEFAULT_SEVERITY = "medium"


def normalize_severity(value: Any, *, default: str = _DEFAULT_SEVERITY) -> str:
    """Normalize a severity value to a canonical label.

    Accepts canonical labels, common aliases, and handles case/whitespace.
    Unknown values default to "medium" to preserve existing behavior.

    Args:
        value: Severity value to normalize (string, None, or other).
        default: Canonical label to return for missing or unrecognized values.

    Returns:
        Canonical severity label (one of the 8 keys in SEVERITY_RANK).

    Examples:
        >>> normalize_severity("CRITICAL")
        'critical'
        >>> normalize_severity("warn")
        'warning'
        >>> normalize_severity(None)
        'medium'
    """
    fallback = str(default or _DEFAULT_SEVERITY).strip().lower()
    if fallback not in SEVERITY_RANK:
        fallback = _DEFAULT_SEVERITY

    if value is None:
        return fallback

    # Convert to string, strip whitespace, lowercase
    severity = str(value).strip().lower()

    if not severity:
        return fallback

    # Check if it's already a canonical label
    if severity in SEVERITY_RANK:
        return severity

    # Check aliases
    normalized = _SEVERITY_ALIASES.get(severity)
    if normalized is not None:
        return normalized

    # Unknown value: return default
    return fallback


def severity_at_least(actual: Any, threshold: Any) -> bool:
    """Check if actual severity meets or exceeds the threshold.

    Uses SEVERITY_RANK for numeric comparison. Both values are normalized
    before comparison.

    Args:
        actual: The actual severity value to check.
        threshold: The minimum severity threshold.

    Returns:
        True if actual >= threshold in severity ranking.

    Examples:
        >>> severity_at_least("high", "medium")
        True
        >>> severity_at_least("low", "high")
        False
        >>> severity_at_least("major", "warning")  # Equal rank
        True
    """
    actual_normalized = normalize_severity(actual)
    threshold_normalized = normalize_severity(threshold)

    actual_rank = SEVERITY_RANK.get(actual_normalized, 0)
    threshold_rank = SEVERITY_RANK.get(threshold_normalized, 0)

    return actual_rank >= threshold_rank


__all__ = [
    "SEVERITY_RANK",
    "normalize_severity",
    "severity_at_least",
]
