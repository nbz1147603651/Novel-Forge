"""Design system tokens — immutable value definitions.

Re-exports all token containers for convenient access::

    from novel_forge.desktop.tokens import COLORS, SPACING, FONTS, TYPE_RAMP
"""

from __future__ import annotations

from novel_forge.desktop.tokens.colors import (
    COLORS,
    all_hex_values,
    hex_to_tokens,
)
from novel_forge.desktop.tokens.spacing import SPACING
from novel_forge.desktop.tokens.typography import (
    FONT_SCALE_CHOICES,
    FONT_WEIGHTS,
    FONTS,
    READING_FONT_CHOICES,
    TYPE_RAMP,
    UI_FONT_CHOICES,
    apply_desktop_typography,
    apply_reading_font_profile,
    apply_to_app,
    desktop_font,
    get_font,
    normalize_font_scale,
    visualization_font,
)

__all__ = [
    "COLORS",
    "SPACING",
    "FONTS",
    "TYPE_RAMP",
    "FONT_WEIGHTS",
    "UI_FONT_CHOICES",
    "READING_FONT_CHOICES",
    "FONT_SCALE_CHOICES",
    "get_font",
    "desktop_font",
    "visualization_font",
    "normalize_font_scale",
    "apply_desktop_typography",
    "apply_reading_font_profile",
    "apply_to_app",
    "all_hex_values",
    "hex_to_tokens",
]
