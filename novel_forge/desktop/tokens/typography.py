"""Typography design tokens: font families, type ramp, font weights, and factory functions.

This module defines the typography system for the Novel Forge desktop app.
All token containers are frozen dataclasses for immutability.

Usage:
    from novel_forge.desktop.tokens.typography import (
        get_font, apply_to_app, FONTS, TYPE_RAMP, FONT_WEIGHTS,
    )
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Dict

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# ── Font Family Definitions ───────────────────────────────────────


@dataclass(frozen=True)
class FontFamily:
    """A font family with per-platform fallback stacks.

    Each platform entry is a CSS ``font-family`` string (comma-separated
    quoted family names).  Import from ``FONTS`` dict, do not instantiate
    directly.
    """

    macos: str
    windows: str
    linux: str

    def for_platform(self) -> str:
        """Return the best font-family CSS string for the current OS."""
        if sys.platform == "darwin":
            return self.macos
        if sys.platform == "win32":
            return self.windows
        return self.linux


# ── Five Font Families (each with 3-platform fallback) ────────────

FONTS: Dict[str, FontFamily] = {
    # Theme UI and reading — Songti stays consistent across compact controls
    # and long-form text, with conventional Western fallbacks.
    "body": FontFamily(
        macos='"Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"',
        windows='"SimSun", "STSong", "Times New Roman", "Arial"',
        linux='"Noto Serif CJK SC", "Noto Serif", "Times New Roman", "Liberation Serif", "Arial"',
    ),
    # Serif / literary — used for prose, long-form reading, chapter displays
    "serif": FontFamily(
        macos='"Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"',
        windows='"SimSun", "STSong", "Times New Roman", "Arial"',
        linux='"Noto Serif CJK SC", "Noto Serif", "Times New Roman", "Liberation Serif", "Arial"',
    ),
    # Calligraphy / Kai — used for decorative headings, brand elements
    "calligraphy": FontFamily(
        macos='"STKaiti", "Kaiti SC", "KaiTi"',
        windows='"KaiTi", "STKaiti", "SimKai"',
        linux='"Noto Serif CJK SC", "AR PL KaitiM GB", "KaiTi"',
    ),
    # Code / monospace — used for log viewers, artifact content, JSON
    "code": FontFamily(
        macos='"Menlo", "Monaco", "Courier New"',
        windows='"Consolas", "Courier New", "Lucida Console"',
        linux='"Noto Mono", "DejaVu Sans Mono", "Droid Sans Mono"',
    ),
    # Model / label sans — used for model names, status indicators
    "model": FontFamily(
        macos='"Times New Roman", "Arial", "Songti SC", "STSong"',
        windows='"Times New Roman", "Arial", "SimSun", "STSong"',
        linux='"Times New Roman", "Liberation Serif", "Arial", "Noto Serif CJK SC"',
    ),
}


# ── Appearance preference contract ───────────────────────────────

# These identifiers are deliberately shared with the React Engine contract
# and the persisted ``NOVEL_FORGE_DESKTOP_*`` settings.  The actual platform
# fallback stacks remain native to this module so PySide pages never need to
# hard-code a font family or a size scale locally.
UI_FONT_CHOICES: tuple[tuple[str, str], ...] = (
    ("source_sans", "主题宋体（默认）"),
    ("system_sans", "Arial / 系统无衬线"),
    ("literary_serif", "经典宋体"),
)
READING_FONT_CHOICES: tuple[tuple[str, str], ...] = (
    ("source_serif", "主题宋体（默认）"),
    ("ui_sans", "Arial / 系统无衬线"),
    ("calligraphy", "楷体"),
)
FONT_SCALE_CHOICES: tuple[tuple[str, str], ...] = (
    ("0.9", "90%"),
    ("1.0", "100%（复刻基线）"),
    ("1.1", "110%"),
    ("1.2", "120%"),
)


def normalize_font_scale(value: object) -> float:
    """Clamp one user-controlled scale value to the supported type range."""
    try:
        return min(1.2, max(0.9, float(value)))
    except (TypeError, ValueError):
        return 1.0


def _platform_families(profile: str, *, reading: bool = False) -> list[str]:
    """Return the platform stack for one persisted UI preference identifier."""
    if profile in {"source_sans", "source_serif", "literary_serif"}:
        if sys.platform == "darwin":
            return ["Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"]
        if sys.platform == "win32":
            return ["SimSun", "STSong", "Times New Roman", "Arial"]
        return ["Noto Serif CJK SC", "Noto Serif", "Times New Roman", "Arial"]
    if profile in {"system_sans", "ui_sans"}:
        if sys.platform == "darwin":
            return ["Arial", ".AppleSystemUIFont", "PingFang SC"]
        if sys.platform == "win32":
            return ["Arial", "Segoe UI", "Microsoft YaHei"]
        return ["Arial", "Noto Sans CJK SC", "Noto Sans", "DejaVu Sans"]
    if profile == "calligraphy" and reading:
        if sys.platform == "darwin":
            return ["STKaiti", "Kaiti SC", "Songti SC", "Times New Roman"]
        if sys.platform == "win32":
            return ["KaiTi", "STKaiti", "SimSun", "Times New Roman"]
        return ["Noto Serif CJK SC", "AR PL KaitiM GB", "Times New Roman"]
    return _platform_families("source_serif", reading=reading)


def _qfont_from_families(font_cls: Any, families: list[str], point_size: int) -> Any:
    """Construct a QFont without looking up the platform font database."""
    font = font_cls()
    if hasattr(font, "setFamilies"):
        font.setFamilies(families)
    else:  # pragma: no cover - defensive for unusual Qt bindings
        font = font_cls(families[0])
    font.setPointSize(max(1, point_size))
    return font


def desktop_font(
    size: int,
    *,
    family_profile: str = "source_sans",
    scale: object = 1.0,
    font_cls: Any = QFont,
) -> Any:
    """Build a UI font from the shared appearance preference contract."""
    return _qfont_from_families(
        font_cls,
        _platform_families(family_profile),
        round(size * normalize_font_scale(scale)),
    )


def visualization_font(size: int, *, reading: bool = False) -> QFont:
    """Return a scaled graph/canvas font using the active desktop preference.

    QPainter-based visualizations do not inherit a widget stylesheet, so they
    must use this helper rather than instantiate a platform font by name.
    """
    app = QApplication.instance()
    profile = "source_serif" if reading else "source_sans"
    scale: object = 1.0
    if isinstance(app, QApplication):
        profile = str(
            app.property(
                "_novel_forge_desktop_reading_font"
                if reading
                else "_novel_forge_desktop_ui_font"
            )
            or profile
        )
        scale = app.property("_novel_forge_desktop_font_scale") or scale
    return desktop_font(size, family_profile=profile, scale=scale)


def _qss_stack(profile: str, *, reading: bool) -> str:
    return ", ".join(f'"{family}"' for family in _platform_families(profile, reading=reading))


def _scale_qss_font_sizes(stylesheet: str, scale: float) -> str:
    """Scale semantic QSS point sizes from the unmodified theme stylesheet."""
    if scale == 1.0:
        return stylesheet

    def replace(match: re.Match[str]) -> str:
        scaled = max(1.0, float(match.group(2)) * scale)
        rendered = f"{scaled:.1f}".rstrip("0").rstrip(".")
        return f"{match.group(1)}{rendered}pt"

    return re.sub(r"(font-size\s*:\s*)(\d+(?:\.\d+)?)pt", replace, stylesheet)


def apply_reading_font_profile(
    app: QApplication,
    profile: str,
    *,
    scale: object = 1.0,
) -> None:
    """Apply the reading family to QSS from the last theme baseline.

    The baseline is saved by ``apply_desktop_theme``.  Replacing from that
    immutable string makes changes reversible: choosing Arial or Kai and then
    returning to Songti cannot leave stale, rewritten selectors behind.
    """
    base_stylesheet = app.property("_novel_forge_theme_stylesheet") or app.styleSheet()
    stylesheet = _scale_qss_font_sizes(
        str(base_stylesheet or ""), normalize_font_scale(scale)
    )
    if not stylesheet:
        return
    if profile == "source_serif":
        app.setStyleSheet(stylesheet)
        return

    stack = _qss_stack(profile, reading=True)
    pattern = re.compile(r"font-family\s*:\s*([^;]+);")

    def replace(match: re.Match[str]) -> str:
        current = match.group(1).lower()
        if "kaiti" in current or "stkaiti" in current:
            return match.group(0)
        if any(marker in current for marker in ("songti", "stsong", "simsun", "georgia")):
            return f"font-family: {stack};"
        return match.group(0)

    app.setStyleSheet(pattern.sub(replace, stylesheet))


def apply_desktop_typography(
    app: QApplication,
    *,
    ui_profile: str = "source_sans",
    reading_profile: str = "source_serif",
    scale: object = 1.0,
) -> None:
    """Apply the complete desktop typography preference immediately.

    It changes the application default, reading QSS stacks and the properties
    exposed to theme-aware widgets.  This is the single runtime entry point
    used by startup and the PySide ``火候`` preview controls.
    """
    normalized_scale = normalize_font_scale(scale)
    app.setFont(desktop_font(13, family_profile=ui_profile, scale=normalized_scale))
    apply_reading_font_profile(app, reading_profile, scale=normalized_scale)
    app.setProperty("_novel_forge_desktop_ui_font", ui_profile)
    app.setProperty("_novel_forge_desktop_reading_font", reading_profile)
    app.setProperty("_novel_forge_desktop_font_scale", normalized_scale)


# ── Type Ramp ─────────────────────────────────────────────────────

TYPE_RAMP: Dict[str, int] = {
    "text-xs": 10,
    "text-sm": 12,
    "text-base": 13,
    "text-md": 14,
    "text-lg": 15,
    "text-xl": 17,
    "text-2xl": 19,
    "text-3xl": 22,
    "text-4xl": 24,
    "display": 26,
    "display-lg": 28,
    "hero": 32,
}


# ── Font Weights ──────────────────────────────────────────────────


@dataclass(frozen=True)
class FontWeights:
    """Semantic-to-numeric font weight mapping."""

    regular: int = 400
    medium: int = 500
    semibold: int = 600
    bold: int = 700


FONT_WEIGHTS = FontWeights()


# ── Public Factory Helpers ────────────────────────────────────────


def get_font(role: str, size: str) -> QFont:
    """Build a ``QFont`` for the given *role* and type-ramp *size*.

    Graceful fallback
    -----------------
    - Unknown *role* → ``"body"`` family.
    - Unknown *size* → ``"text-base"`` (13pt).

    Parameters
    ----------
    role : str
        One of ``FONTS`` keys (``"body"``, ``"serif"``, ``"calligraphy"``,
        ``"code"``, ``"model"``).
    size : str
        One of ``TYPE_RAMP`` keys (``"text-xs"`` … ``"hero"``).

    Returns
    -------
    QFont
        A configured ``QFont``.  Never raises on invalid input.
    """
    family_str = FONTS.get(role, FONTS["body"]).for_platform()
    point_size = TYPE_RAMP.get(size, TYPE_RAMP["text-base"])
    families = [part.strip().strip('"') for part in family_str.split(",") if part.strip()]
    return _qfont_from_families(QFont, families, point_size)


def apply_to_app(app: QApplication) -> None:
    """Inject the default body font into a ``QApplication``.

    All widgets that do **not** override ``font-family`` via QSS will
    inherit ``body`` at ``text-base`` (13pt).
    """
    apply_desktop_typography(app)


# ── QSS Helper ────────────────────────────────────────────────────


def to_qss_font_size(token_name: str) -> str:
    """Map a type-ramp token to a QSS ``font-size`` value in points.

    Args:
        token_name: Key from TYPE_RAMP (e.g. "text-base", "text-sm", "text-xs").

    Returns:
        String like ``"13pt"``. If token is not in TYPE_RAMP, returns the
        token itself as a fallback (no transformation).
    """
    if token_name in TYPE_RAMP:
        return f"{TYPE_RAMP[token_name]}pt"
    return token_name
