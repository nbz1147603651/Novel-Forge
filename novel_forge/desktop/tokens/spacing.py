"""Spacing design tokens: a numeric scale for padding, margin, and gap values.

The scale is 4px-based with half-step intermediates to cover the values
actually used in ``theme/*.py`` QSS strings.

Usage::

    from novel_forge.desktop.tokens.spacing import SPACING

    SPACING["space-4"]    # 16
    SPACING.get("nope")   # None — never raises
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Dict


@dataclass(frozen=True)
class _SpacingTokens:
    """Canonical spacing-token definitions (frozen).

    Values are integers (pixels).  The base scale is 4px-based;
    half-step tokens (``space-X.5``) fill the gaps for values that
    appear in the existing QSS.
    """

    space_0: int = 0
    space_px: int = 1
    space_0_5: int = 2
    space_1: int = 4
    space_1_5: int = 6
    space_2: int = 8
    space_2_5: int = 10
    space_3: int = 12
    space_3_5: int = 14
    space_4: int = 16
    space_4_5: int = 18
    space_5: int = 20
    space_5_5: int = 22
    space_6: int = 24
    space_7: int = 28
    space_7_5: int = 30
    space_8: int = 32
    space_9: int = 40
    space_10: int = 48


# ── Build the dotted-name lookup dict ───────────────────────────────────────

_SPACING_TOKENS = _SpacingTokens()

_FIELD_TO_DOTTED: Dict[str, str] = {
    "space_0": "space-0",
    "space_px": "space-px",
    "space_0_5": "space-0.5",
    "space_1": "space-1",
    "space_1_5": "space-1.5",
    "space_2": "space-2",
    "space_2_5": "space-2.5",
    "space_3": "space-3",
    "space_3_5": "space-3.5",
    "space_4": "space-4",
    "space_4_5": "space-4.5",
    "space_5": "space-5",
    "space_5_5": "space-5.5",
    "space_6": "space-6",
    "space_7": "space-7",
    "space_7_5": "space-7.5",
    "space_8": "space-8",
    "space_9": "space-9",
    "space_10": "space-10",
}


def _build_spacing_dict() -> Dict[str, int]:
    """Build the dotted-name → pixel-value mapping from the dataclass."""
    result: Dict[str, int] = {}
    for f in fields(_SPACING_TOKENS):
        dotted = _FIELD_TO_DOTTED.get(f.name)
        if dotted is not None:
            result[dotted] = getattr(_SPACING_TOKENS, f.name)
    return result


SPACING: Dict[str, int] = _build_spacing_dict()
