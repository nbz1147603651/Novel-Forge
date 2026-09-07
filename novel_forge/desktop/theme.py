"""Unified desktop stylesheet — re-exports from theme package.

This file is kept for backward compatibility.  All logic lives in
``novel_forge/desktop/theme/`` submodules.
"""

from __future__ import annotations

from .theme import get_stylesheet, get_stylesheet_for_settings, resolve_qcolor
from .theme.core import (
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    BG_SURFACE,
    BG_WORKSPACE,
    COLOR_MAP,
    FADE_BG_COLOR,
    SHADOW_COLOR,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    get_color,
)
from .theme.palettes import (
    DEFAULT_DESKTOP_THEME_ID,
    DESKTOP_THEMES,
    DesktopTheme,
    desktop_theme_choices,
    get_desktop_theme,
    list_desktop_themes,
    normalize_theme_id,
)

__all__ = [
    "COLOR_MAP",
    "DEFAULT_DESKTOP_THEME_ID",
    "DESKTOP_THEMES",
    "DesktopTheme",
    "FADE_BG_COLOR",
    "SHADOW_COLOR",
    "ACCENT_PRIMARY",
    "ACCENT_PRIMARY_HOVER",
    "BG_WORKSPACE",
    "BG_SURFACE",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "desktop_theme_choices",
    "get_desktop_theme",
    "get_color",
    "get_stylesheet",
    "resolve_qcolor",
    "get_stylesheet_for_settings",
    "list_desktop_themes",
    "normalize_theme_id",
]
