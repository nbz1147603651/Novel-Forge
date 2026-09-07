"""Tests for Surface shadow strategy — verifies which tones use QGraphicsDropShadowEffect.

This test suite locks down the shadow behavior per Surface tone after the
Task 8 D9 refactor: card/panel now rely on QSS border + background
(qlineargradient), and only hero/elevated/inset retain a real
QGraphicsDropShadowEffect for visual elevation impact.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect  # noqa: E402

from novel_forge.desktop.components.primitives import Surface  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


# ── No-shadow tones (QSS border + background only) ─────────────


def test_surface_flat_no_shadow(qapp: QApplication) -> None:
    """flat tone must NOT have any graphics effect (pure flat container)."""
    s = Surface(tone="flat")
    assert s.graphicsEffect() is None


def test_surface_rail_no_shadow(qapp: QApplication) -> None:
    """rail tone must NOT have any graphics effect."""
    s = Surface(tone="rail")
    assert s.graphicsEffect() is None


def test_surface_card_no_shadow(qapp: QApplication) -> None:
    """card tone now relies on QSS border (Task 8 D9) — no QGraphicsDropShadowEffect."""
    s = Surface(tone="card")
    assert s.graphicsEffect() is None, (
        "card tone should NOT have a QGraphicsDropShadowEffect; QSS border "
        "in QFrame#surface[tone=card] provides the visual separation."
    )


def test_surface_panel_no_shadow(qapp: QApplication) -> None:
    """panel tone now relies on QSS border (Task 8 D9) — no QGraphicsDropShadowEffect."""
    s = Surface(tone="panel")
    assert s.graphicsEffect() is None, (
        "panel tone should NOT have a QGraphicsDropShadowEffect; QSS border "
        "in QFrame#surface[tone=panel] provides the visual separation."
    )


# ── Shadow tones (kept for visual elevation impact) ────────────


def test_surface_hero_has_strong_shadow(qapp: QApplication) -> None:
    """hero tone preserves QGraphicsDropShadowEffect (blur=8, offset_y=4)."""
    s = Surface(tone="hero")
    effect = s.graphicsEffect()
    assert effect is not None
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.blurRadius() == 8
    assert effect.offset().y() == 4


def test_surface_elevated_has_shadow(qapp: QApplication) -> None:
    """elevated tone (NEW) keeps QGraphicsDropShadowEffect (blur=4, offset_y=2)."""
    s = Surface(tone="elevated")
    effect = s.graphicsEffect()
    assert effect is not None, "elevated tone should have a graphics effect"
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.blurRadius() == 4
    assert effect.offset().y() == 2


def test_surface_inset_has_shadow(qapp: QApplication) -> None:
    """inset tone keeps QGraphicsDropShadowEffect (blur=4, offset_y=2)."""
    s = Surface(tone="inset")
    effect = s.graphicsEffect()
    assert effect is not None, "inset tone should have a graphics effect"
    assert isinstance(effect, QGraphicsDropShadowEffect)
    assert effect.blurRadius() == 4
    assert effect.offset().y() == 2


# ── Surface subclass inheritance ──────────────────────────────


def test_short_form_clears_shadow_explicitly(qapp: QApplication) -> None:
    """ShortForm extends Surface with tone='panel' and explicitly clears any effect."""
    from novel_forge.desktop.pages.workflow.forms import ShortForm

    form = ShortForm()
    assert form.graphicsEffect() is None


def test_long_init_form_clears_shadow_explicitly(qapp: QApplication) -> None:
    """LongInitForm extends Surface with tone='panel' and explicitly clears any effect."""
    from novel_forge.desktop.pages.workflow.forms import LongInitForm

    form = LongInitForm()
    assert form.graphicsEffect() is None


def test_blueprint_element_preference_panel_has_shadow(qapp: QApplication) -> None:
    """BlueprintElementPreferencePanel is a Surface subclass with tone='inset' → has shadow."""
    from novel_forge.desktop.pages.workflow.forms import BlueprintElementPreferencePanel

    panel = BlueprintElementPreferencePanel("long")
    effect = panel.graphicsEffect()
    assert effect is not None, "inset tone should have a shadow"
    assert isinstance(effect, QGraphicsDropShadowEffect)
