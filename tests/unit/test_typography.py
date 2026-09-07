"""Unit tests for desktop typography design tokens."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from novel_forge.desktop.tokens.typography import (
    FONT_SCALE_CHOICES,
    FONT_WEIGHTS,
    FONTS,
    READING_FONT_CHOICES,
    TYPE_RAMP,
    UI_FONT_CHOICES,
    FontFamily,
    apply_desktop_typography,
    apply_to_app,
    desktop_font,
    get_font,
    visualization_font,
)

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


# ── Import & Structure Tests ─────────────────────────────────────


class TestImports:
    """Verify all expected names are importable."""

    def test_import_all(self) -> None:
        from novel_forge.desktop.tokens.typography import (  # noqa: F811
            FONT_WEIGHTS,
            FONTS,
            TYPE_RAMP,
        )
        assert FONTS is not None
        assert FONT_WEIGHTS is not None
        assert TYPE_RAMP is not None


class TestFONTS:
    """Verify the font-family dictionary structure."""

    def test_five_families(self) -> None:
        assert len(FONTS) == 5

    @pytest.mark.parametrize("key", ["body", "serif", "calligraphy", "code", "model"])
    def test_family_has_three_platforms(self, key: str) -> None:
        fam = FONTS[key]
        assert isinstance(fam, FontFamily)
        assert fam.macos, f"{key}.macos is empty"
        assert fam.windows, f"{key}.windows is empty"
        assert fam.linux, f"{key}.linux is empty"

    @pytest.mark.parametrize("key", ["body", "serif", "calligraphy", "code", "model"])
    def test_family_for_platform_returns_nonempty(self, key: str) -> None:
        fam = FONTS[key]
        platform_str = fam.for_platform()
        assert isinstance(platform_str, str)
        assert len(platform_str) > 0


class TestTYPE_RAMP:
    """Verify the type ramp size definitions."""

    def test_twelve_steps(self) -> None:
        assert len(TYPE_RAMP) == 12

    def test_contains_critical_sizes(self) -> None:
        sizes = sorted(TYPE_RAMP.values())
        assert 10 in sizes
        assert 13 in sizes
        assert 32 in sizes

    def test_hero_is_largest(self) -> None:
        assert max(TYPE_RAMP.values()) == 32

    def test_text_xs_is_smallest(self) -> None:
        assert min(TYPE_RAMP.values()) == 10


class TestDesktopAppearanceTypography:
    """The two desktop UI stacks expose one stable typography contract."""

    def test_preference_choices_keep_shared_engine_ids(self) -> None:
        assert [value for value, _label in UI_FONT_CHOICES] == [
            "source_sans",
            "system_sans",
            "literary_serif",
        ]
        assert [value for value, _label in READING_FONT_CHOICES] == [
            "source_serif",
            "ui_sans",
            "calligraphy",
        ]
        assert [value for value, _label in FONT_SCALE_CHOICES] == ["0.9", "1.0", "1.1", "1.2"]

    def test_default_ui_font_prefers_songti(self) -> None:
        font = desktop_font(13)
        assert font.families()[0] in {"Songti SC", "SimSun", "Noto Serif CJK SC"}

    def test_visualization_font_uses_active_scale(self, qapp: QApplication) -> None:
        apply_desktop_typography(qapp, scale=1.2)
        assert visualization_font(10).pointSize() == 12

    def test_typography_application_scales_themed_qss(self, qapp: QApplication) -> None:
        qapp.setProperty("_novel_forge_theme_stylesheet", "QLabel { font-size: 10pt; }")
        apply_desktop_typography(qapp, reading_profile="source_serif", scale=1.1)
        assert "font-size: 11pt" in qapp.styleSheet()


class TestFONT_WEIGHTS:
    """Verify the font-weight constants."""

    def test_regular_is_400(self) -> None:
        assert FONT_WEIGHTS.regular == 400

    def test_bold_is_700(self) -> None:
        assert FONT_WEIGHTS.bold == 700

    def test_all_weights_present(self) -> None:
        assert FONT_WEIGHTS.regular == 400
        assert FONT_WEIGHTS.medium == 500
        assert FONT_WEIGHTS.semibold == 600
        assert FONT_WEIGHTS.bold == 700


class TestGetFont:
    """Verify the get_font factory function."""

    @pytest.mark.parametrize(
        "role, size",
        [
            ("body", "text-base"),
            ("serif", "text-lg"),
            ("calligraphy", "hero"),
            ("code", "text-xs"),
            ("model", "text-md"),
        ],
    )
    def test_valid_args_return_qfont(self, role: str, size: str) -> None:
        font = get_font(role, size)
        assert isinstance(font, QFont)
        assert font.family() != "", f"family() empty for role={role}, size={size}"
        assert font.pointSize() > 0, f"pointSize() <=0 for role={role}, size={size}"

    def test_invalid_role_falls_back_gracefully(self) -> None:
        """Unknown role must return a valid QFont (body fallback)."""
        font = get_font("nonexistent_role", "text-base")
        assert isinstance(font, QFont)
        assert font.family() != ""

    def test_invalid_size_falls_back_gracefully(self) -> None:
        """Unknown size must return a valid QFont (text-base fallback)."""
        font = get_font("body", "nonexistent_size")
        assert isinstance(font, QFont)
        # text-base = 13px
        assert font.pointSize() == 13

    def test_both_invalid_does_not_raise(self) -> None:
        """Neither valid role nor valid size → no crash, valid QFont."""
        font = get_font("bad_role", "bad_size")
        assert isinstance(font, QFont)
        assert font.family() != ""
        assert font.pointSize() > 0


class TestApplyToApp:
    """Verify apply_to_app works without exceptions."""

    def test_apply_does_not_raise(self, qapp: QApplication) -> None:
        apply_to_app(qapp)
        # After apply, the app's default font should be set
        default = qapp.font()
        assert default.family() != ""
        assert default.pointSize() > 0
