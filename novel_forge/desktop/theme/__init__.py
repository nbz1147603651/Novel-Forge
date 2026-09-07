"""Novel Forge desktop theme package.

Splits the monolithic theme.py into logical sections while preserving
the ``get_stylesheet()`` API.  Hex values in CONTENT strings are replaced
with ``{{token.name}}`` placeholders; ``get_stylesheet()`` resolves them
via the design-token system (colors + spacing).  Alpha colors can use
``rgba({{token.name}}, 0.7)`` and are expanded to Qt-compatible RGBA.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QColor as _QColor
from PySide6.QtWidgets import QApplication as _QApplication

from . import _globals, components, dialogs, forms, memory, navigation, palettes, toast
from .core import (
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
from .palettes import (
    DEFAULT_DESKTOP_THEME_ID,
    DESKTOP_THEMES,
    DesktopTheme,
    desktop_theme_choices,
    desktop_theme_token_overrides,
    get_desktop_theme,
    list_desktop_themes,
    normalize_theme_id,
)

_RES = Path(__file__).parent.parent / "resources"

# ── Dynamic QColor resolution ────────────────────────────────────────────────


def resolve_qcolor(token_name: str, alpha: int = 255) -> _QColor:
    """Resolve a design-token name to a ``QColor`` for the *current* theme.

    The resolution is **call-time**, not import-time, so ``paintEvent``
    callers automatically pick up theme switches.

    Parameters
    ----------
    token_name:
        Dotted token name, e.g. ``"bg.surface"``, ``"accent.primary"``.
    alpha:
        Opacity 0–255 (default fully opaque).

    Returns
    -------
    QColor
        A new ``QColor`` instance.  Falls back to magenta ``(255, 0, 255)``
        when the token is unknown so misconfigured tokens are immediately
        visible during development.
    """
    rgb = _theme_token_rgb(active_theme_id(), token_name)
    resolved_alpha = max(0, min(255, int(alpha)))
    if rgb is None:
        return _QColor(255, 0, 255, resolved_alpha)

    r, g, b = rgb
    return _QColor(r, g, b, resolved_alpha)

# ── Token substitution ──────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"\{\{([^}]+)\}\}")
_RGBA_TOKEN_RE = re.compile(r"rgba\(\s*\{\{([^}]+)\}\}\s*,\s*([0-9]*\.?[0-9]+)\s*\)")
_HEX6_RE = re.compile(r"#[0-9a-fA-F]{6}\b")


def active_theme_id() -> str:
    """Return the currently applied desktop theme identifier.

    All runtime color and template resolution goes through this function so a
    theme switch has one authoritative source of truth.
    """
    app = _QApplication.instance()
    raw = app.property("_novel_forge_desktop_theme") if isinstance(app, _QApplication) else None
    return normalize_theme_id(str(raw) if raw else None)


def _build_base_tokens() -> dict[str, str]:
    """Build the base token → value mapping from colors and spacing."""
    from novel_forge.desktop.tokens.colors import COLORS
    from novel_forge.desktop.tokens.spacing import SPACING

    tokens: dict[str, str] = {}
    # Color tokens: dotted_name → hex value
    for dotted_name, (hex_val, _desc) in COLORS.items():
        tokens[dotted_name] = hex_val
    # Spacing tokens: dotted_name → pixel value (as string)
    for dotted_name, px_val in SPACING.items():
        tokens[dotted_name] = str(px_val)
    return tokens


def _build_theme_tokens(theme_id: str | None) -> dict[str, str]:
    """Build the resolved token map for a registered desktop theme."""

    tokens = _build_base_tokens()
    tokens.update(desktop_theme_token_overrides(theme_id))
    return tokens


@lru_cache(maxsize=512)
def _theme_token_rgb(theme_id: str, token_name: str) -> tuple[int, int, int] | None:
    """Return cached RGB components for a resolved theme token.

    ``resolve_qcolor`` is called from paint paths, including animations.  Keep
    token-map construction and hex parsing out of those hot paths while still
    returning a new mutable ``QColor`` for every caller.
    """
    hex_val = _build_theme_tokens(theme_id).get(token_name)
    return _hex_to_rgb(hex_val) if hex_val is not None else None


def _hex_to_rgb(hex_value: str) -> tuple[int, int, int] | None:
    value = hex_value.strip()
    if _HEX6_RE.fullmatch(value) is None:
        return None
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
    )


def qcolor_hex(token_name: str) -> str:
    """Resolve a design-token name to a hex color string (e.g. ``"#b65634"``).

    Suitable for embedding in QSS stylesheets and HTML inline styles.
    The resolution is **call-time**, picking up the current theme automatically.

    Falls back to ``"#ff00ff"`` (magenta) for unknown tokens so misconfigured
    tokens are immediately visible during development.
    """
    rgb = _theme_token_rgb(active_theme_id(), token_name)
    if rgb is None:
        return "#ff00ff"
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def qcolor_rgba(token_name: str, alpha: float) -> str:
    """Resolve a design-token name to a CSS ``rgba(r, g, b, alpha)`` string.

    *alpha* should be a float in the range ``[0.0, 1.0]``.

    Suitable for embedding in QSS stylesheets and HTML inline styles.
    The resolution is **call-time**, picking up the current theme automatically.

    Falls back to ``rgba(255, 0, 255, alpha)`` (magenta) for unknown tokens.
    """
    rgb = _theme_token_rgb(active_theme_id(), token_name)
    if rgb is None:
        return f"rgba(255, 0, 255, {alpha})"
    r, g, b = rgb
    return f"rgba({r}, {g}, {b}, {alpha})"


def __apply_token_substitution(qss: str, tokens: dict[str, str]) -> str:
    """Replace ``{{token.name}}`` placeholders in *qss* with values from *tokens*.

    Unknown token keys are left as-is (no crash).  Uses ``str.replace()``
    semantics via regex for single-pass substitution.
    """

    def _rgba_replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        alpha = match.group(2)
        value = tokens.get(key)
        if value is None:
            return match.group(0)
        rgb = _hex_to_rgb(value)
        if rgb is None:
            return match.group(0)
        r, g, b = rgb
        return f"rgba({r}, {g}, {b}, {alpha})"

    def _replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        return tokens.get(key, match.group(0))  # Leave unknown keys intact

    qss = _RGBA_TOKEN_RE.sub(_rgba_replacer, qss)
    return _TOKEN_RE.sub(_replacer, qss)


def resolve_theme_tokens(template: str) -> str:
    """Resolve ``{{semantic.token}}`` placeholders using the active theme.

    This is the shared rendering primitive for application-owned QSS and HTML
    CSS.  Templates must name semantic roles; they must not contain palette
    hex values or page-specific compatibility mappings.
    """
    return __apply_token_substitution(template, _build_theme_tokens(active_theme_id()))


# ── Stylesheet assembly ─────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _raw_combined_qss() -> str:
    """Return the concatenated CONTENT strings (with placeholders, no substitution)."""
    return (
        _globals.CONTENT
        + navigation.CONTENT
        + components.CONTENT
        + forms.CONTENT
        + dialogs.CONTENT
        + toast.CONTENT
        + memory.CONTENT
    )


def _resolve_stylesheet(raw: str, tokens: dict[str, str]) -> str:
    resolved = __apply_token_substitution(raw, tokens)
    arrow_down = (_RES / "arrow_down.svg").as_posix()
    arrow_up = (_RES / "arrow_up.svg").as_posix()
    return resolved.replace("__ARROW_DOWN__", arrow_down).replace("__ARROW_UP__", arrow_up)


@lru_cache(maxsize=16)
def _stylesheet_for_theme(theme_id: str) -> str:
    """Return the fully resolved stylesheet for a registered theme."""

    return _resolve_stylesheet(_raw_combined_qss(), _build_theme_tokens(theme_id))


@lru_cache(maxsize=1)
def _default_stylesheet() -> str:
    """Return the default theme stylesheet for backward-compatible cache control."""

    return _resolve_stylesheet(
        _raw_combined_qss(),
        _build_theme_tokens(DEFAULT_DESKTOP_THEME_ID),
    )


def get_stylesheet(
    tokens: dict[str, str] | None = None,
    *,
    theme_id: str | None = None,
) -> str:
    """Return the application stylesheet with resource paths and tokens resolved.

    Parameters
    ----------
    tokens:
        Optional mapping of ``token_name → value`` overrides.  Missing keys
        fall back to the design-token defaults.  Pass ``{}`` for defaults.
    theme_id:
        Optional registered desktop theme id.  Unknown values fall back to the
        default theme.

    Returns
    -------
    str
        Fully resolved QSS string ready for ``app.setStyleSheet()``.
    """
    resolved_theme_id = normalize_theme_id(theme_id)
    if tokens is None or tokens == {}:
        if resolved_theme_id == DEFAULT_DESKTOP_THEME_ID:
            return _default_stylesheet()
        return _stylesheet_for_theme(resolved_theme_id)

    # Merge: defaults + theme + user overrides
    merged = _build_theme_tokens(resolved_theme_id)
    merged.update(tokens)
    return _resolve_stylesheet(_raw_combined_qss(), merged)


def get_stylesheet_for_settings(settings: object | None = None) -> str:
    """Return the stylesheet selected by ``Settings.desktop_theme``."""

    if settings is None:
        from novel_forge.core.config import get_settings

        settings = get_settings()
    return get_stylesheet(theme_id=getattr(settings, "desktop_theme", DEFAULT_DESKTOP_THEME_ID))


__all__ = [
    "ACCENT_PRIMARY",
    "ACCENT_PRIMARY_HOVER",
    "BG_SURFACE",
    "BG_WORKSPACE",
    "COLOR_MAP",
    "DEFAULT_DESKTOP_THEME_ID",
    "DESKTOP_THEMES",
    "DesktopTheme",
    "FADE_BG_COLOR",
    "SHADOW_COLOR",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "_globals",
    "components",
    "dialogs",
    "forms",
    "memory",
    "navigation",
    "palettes",
    "toast",
    "desktop_theme_choices",
    "get_desktop_theme",
    "get_color",
    "get_stylesheet",
    "get_stylesheet_for_settings",
    "list_desktop_themes",
    "normalize_theme_id",
    "active_theme_id",
    "qcolor_hex",
    "qcolor_rgba",
    "resolve_qcolor",
    "resolve_theme_tokens",
    "_default_stylesheet",
    "_raw_combined_qss",
]
