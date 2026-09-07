"""Color constants and palette utilities for the Novel Forge desktop theme.

.. deprecated::
   The static RGBA tuples below (``ACCENT_PRIMARY``, ``BG_SURFACE``, etc.)
   are **default-theme** values kept for backward compatibility.  New code
   should call ``novel_forge.desktop.theme.resolve_qcolor(token, alpha)``
   to get a QColor that tracks the currently active theme.
"""

from __future__ import annotations

# Legacy static color constants (default-theme values).
# Prefer ``resolve_qcolor("token.name")`` for any code that runs at paint time.
FADE_BG_COLOR = (242, 232, 220)  # Fade overlay background  → token: fade.bg
SHADOW_COLOR = (69, 47, 29, 28)  # Surface shadow (RGBA)    → token: shadow
ACCENT_PRIMARY = (182, 86, 52)  # Primary accent #b65634    → token: accent.primary
ACCENT_PRIMARY_HOVER = (199, 100, 66)  # Hover accent #c76442 → token: accent.primary.hover
BG_WORKSPACE = (247, 240, 231)  # Workspace background #f7f0e7 → token: bg.workspace
BG_SURFACE = (255, 250, 243)  # Panel background #fffaf3    → token: bg.surface
TEXT_PRIMARY = (44, 36, 30)  # Primary text #2c241e          → token: text.primary
TEXT_SECONDARY = (102, 87, 75)  # Secondary text #66574b    → token: text.secondary

_COLOR_MAP = {
    "accent_primary": ACCENT_PRIMARY,
    "accent_primary_hover": ACCENT_PRIMARY_HOVER,
    "bg_workspace": BG_WORKSPACE,
    "bg_surface": BG_SURFACE,
    "text_primary": TEXT_PRIMARY,
    "text_secondary": TEXT_SECONDARY,
    "fade_bg": FADE_BG_COLOR,
    "shadow": SHADOW_COLOR,
}


def get_color(name: str) -> tuple[int, ...] | None:
    """Get a named RGB/RGBA color tuple."""
    return _COLOR_MAP.get(name)


# Public alias for backward compatibility
COLOR_MAP = _COLOR_MAP
