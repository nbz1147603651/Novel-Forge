"""Tests for theme modularization in the desktop application.

Verifies:
- stylesheet_output non-empty
- color_constants valid RGBA tuples
- surface_tones render in stylesheet
- button_variants render in stylesheet
- badge_tones render in stylesheet
- theme_modularity imports work
"""

from __future__ import annotations

# ── Stylesheet Output ─────────────────────────────────────────────────


def _get_combined_stylesheet() -> str:
    from novel_forge.desktop.theme import _globals, components, dialogs, forms, navigation, toast

    return (
        _globals.CONTENT
        + navigation.CONTENT
        + components.CONTENT
        + forms.CONTENT
        + dialogs.CONTENT
        + toast.CONTENT
    )


def test_stylesheet_output_non_empty() -> None:
    css = _get_combined_stylesheet()
    assert isinstance(css, str)
    assert len(css) > 100
    assert "{" in css and "}" in css


def test_stylesheet_contains_global_rules() -> None:
    css = _get_combined_stylesheet()
    assert "QWidget" in css
    assert "QScrollBar" in css


def test_stylesheet_resource_placeholders_in_content() -> None:
    css = _get_combined_stylesheet()
    assert "__ARROW_DOWN__" in css or "__ARROW_UP__" in css or "arrow" in css.lower()


def test_combo_popups_expand_as_lists() -> None:
    css = _get_combined_stylesheet()
    assert "combobox-popup: 0;" in css
    assert "QComboBox QAbstractItemView" in css
    assert "QComboBox QAbstractItemView::item" in css
    assert "min-height: 30px;" in css


# ── Color Constants ───────────────────────────────────────────────────


def test_color_constants_are_tuples() -> None:
    """Color constants are valid RGBA/RGB tuples."""
    from novel_forge.desktop.theme import (
        ACCENT_PRIMARY,
        ACCENT_PRIMARY_HOVER,
        BG_SURFACE,
        BG_WORKSPACE,
        FADE_BG_COLOR,
        SHADOW_COLOR,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
    )

    for color in [
        ACCENT_PRIMARY,
        ACCENT_PRIMARY_HOVER,
        BG_SURFACE,
        BG_WORKSPACE,
        TEXT_PRIMARY,
        TEXT_SECONDARY,
        FADE_BG_COLOR,
        SHADOW_COLOR,
    ]:
        assert isinstance(color, tuple)
        assert len(color) in (3, 4)  # RGB or RGBA
        for channel in color:
            assert isinstance(channel, int)
            assert 0 <= channel <= 255


def test_color_map_contains_all_colors() -> None:
    """COLOR_MAP contains all expected color keys."""
    from novel_forge.desktop.theme import COLOR_MAP

    expected_keys = {
        "accent_primary",
        "accent_primary_hover",
        "bg_workspace",
        "bg_surface",
        "text_primary",
        "text_secondary",
        "fade_bg",
        "shadow",
    }
    assert expected_keys.issubset(set(COLOR_MAP.keys()))


def test_get_color_returns_valid_tuple() -> None:
    """get_color() returns valid color tuples for known names."""
    from novel_forge.desktop.theme import get_color

    for name in [
        "accent_primary",
        "bg_workspace",
        "text_primary",
        "shadow",
    ]:
        color = get_color(name)
        assert color is not None
        assert isinstance(color, tuple)
        assert all(0 <= c <= 255 for c in color)


def test_get_color_returns_none_for_unknown() -> None:
    """get_color() returns None for unknown color names."""
    from novel_forge.desktop.theme import get_color

    assert get_color("nonexistent_color") is None


# ── Surface Tones ─────────────────────────────────────────────────────


def test_surface_tones_in_stylesheet() -> None:
    css = _get_combined_stylesheet()
    assert 'QFrame#surface[tone="hero"]' in css
    assert 'QFrame#surface[tone="panel"]' in css
    assert 'QFrame#surface[tone="card"]' in css
    assert 'QFrame#surface[tone="inset"]' in css


def test_surface_tone_count() -> None:
    css = _get_combined_stylesheet()
    tones = ["hero", "panel", "card", "inset"]
    for tone in tones:
        assert f'tone="{tone}"' in css, f"Missing surface tone: {tone}"


# ── Button Variants ───────────────────────────────────────────────────


def test_button_variants_in_stylesheet() -> None:
    css = _get_combined_stylesheet()
    assert 'QPushButton#actionButton[variant="primary"]' in css
    assert 'QPushButton#actionButton[variant="secondary"]' in css
    assert 'QPushButton#actionButton[variant="quiet"]' in css
    assert 'QPushButton#actionButton[variant="danger"]' in css


def test_button_hover_states() -> None:
    css = _get_combined_stylesheet()
    assert "QPushButton#actionButton[variant=\"primary\"]:hover" in css
    assert "QPushButton#actionButton[variant=\"danger\"]:hover" in css


def test_button_disabled_state() -> None:
    css = _get_combined_stylesheet()
    assert "QPushButton#actionButton:disabled" in css


def test_memory_action_buttons_share_variant_styles() -> None:
    from novel_forge.desktop.theme import get_stylesheet

    css = get_stylesheet()
    assert "QPushButton#memorySmallButton[variant=\"primary\"]" in css
    assert "QPushButton#memorySmallButton[variant=\"secondary\"]:hover" in css
    assert "QPushButton#memorySmallButton:disabled" in css
    assert "QPushButton#memoryWarningClearBtn:hover" in css


def test_filter_chip_styles() -> None:
    css = _get_combined_stylesheet()
    assert "QPushButton#filterChip" in css
    assert "QPushButton#filterChip:checked" in css


# ── Badge Tones ───────────────────────────────────────────────────────


def test_badge_tones_in_stylesheet() -> None:
    css = _get_combined_stylesheet()
    assert "QLabel#badge" in css
    assert 'QLabel#badge[tone="default"]' in css
    assert 'QLabel#badge[tone="warning"]' in css
    assert 'QLabel#badge[tone="success"]' in css
    assert 'QLabel#badge[tone="danger"]' in css


def test_badge_tone_count() -> None:
    css = _get_combined_stylesheet()
    tones = ["default", "warning", "success", "danger", "muted", "quiet", "primary", "info"]
    for tone in tones:
        assert f'tone="{tone}"' in css, f"Missing badge tone: {tone}"


# ── Theme Modularity Imports ──────────────────────────────────────────


def test_theme_core_imports() -> None:
    """Theme core module exports are accessible."""
    from novel_forge.desktop.theme.core import (
        ACCENT_PRIMARY,
        COLOR_MAP,
        get_color,
    )

    assert ACCENT_PRIMARY is not None
    assert COLOR_MAP is not None
    assert callable(get_color)


def test_theme_globals_import() -> None:
    """Theme globals module is importable."""
    from novel_forge.desktop.theme._globals import CONTENT

    assert isinstance(CONTENT, str)
    assert len(CONTENT) > 50


def test_theme_components_import() -> None:
    """Theme components module is importable."""
    from novel_forge.desktop.theme.components import CONTENT

    assert isinstance(CONTENT, str)
    assert "QPushButton#actionButton" in CONTENT


def test_theme_dialogs_import() -> None:
    """Theme dialogs module is importable."""
    from novel_forge.desktop.theme.dialogs import CONTENT

    assert isinstance(CONTENT, str)
    assert len(CONTENT) > 10


def test_theme_forms_import() -> None:
    """Theme forms module is importable."""
    from novel_forge.desktop.theme.forms import CONTENT

    assert isinstance(CONTENT, str)
    assert len(CONTENT) > 10


def test_theme_navigation_import() -> None:
    """Theme navigation module is importable."""
    from novel_forge.desktop.theme.navigation import CONTENT

    assert isinstance(CONTENT, str)
    assert len(CONTENT) > 10


def test_theme_toast_import() -> None:
    from novel_forge.desktop.theme.toast import CONTENT

    assert isinstance(CONTENT, str)


def test_backward_compat_re_exports() -> None:
    from novel_forge.desktop import theme

    expected_exports = [
        "COLOR_MAP", "FADE_BG_COLOR", "SHADOW_COLOR",
        "ACCENT_PRIMARY", "ACCENT_PRIMARY_HOVER",
        "BG_WORKSPACE", "BG_SURFACE",
        "TEXT_PRIMARY", "TEXT_SECONDARY",
        "get_color",
    ]
    for name in expected_exports:
        assert hasattr(theme, name), f"Missing export: {name}"


def test_theme_modules_are_independent() -> None:
    """Each theme submodule can be imported independently."""
    # Import each module directly without going through the facade
    from novel_forge.desktop.theme import (
        _globals,
        components,
        core,
        dialogs,
        forms,
        navigation,
        toast,
    )

    # Each should have a CONTENT or color definitions
    assert hasattr(core, "COLOR_MAP")
    for mod in [_globals, components, dialogs, forms, navigation, toast]:
        assert hasattr(mod, "CONTENT")
