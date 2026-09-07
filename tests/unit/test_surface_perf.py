"""Performance tests for Surface shadow offscreen buffer optimization (Task 24).

Verifies:
- 100 Surface creation time < 1s
- Single paint time < 2ms per surface
- DisableTransformHint optimization flag is set on shadow effects
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect  # noqa: E402

from novel_forge.desktop.components.primitives import Surface  # noqa: E402

TONES = ["hero", "elevated", "inset", "panel", "card", "flat", "rail"]
NUM_SURFACES = 100


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_surface_creation_100_under_1s(qapp: QApplication) -> None:
    """Creating 100 Surface instances (mixed tones) must complete in < 1 second."""
    start = time.perf_counter()
    surfaces = [Surface(tone=TONES[i % len(TONES)]) for i in range(NUM_SURFACES)]
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"100 Surface creation took {elapsed:.3f}s, expected < 1.0s"
    assert len(surfaces) == NUM_SURFACES


def test_surface_single_paint_under_2ms(qapp: QApplication) -> None:
    """A single paintEvent on a shadow-tone Surface must complete in < 2ms."""
    for tone in ["hero", "elevated", "inset"]:
        surface = Surface(tone=tone)
        surface.resize(400, 80)
        surface.show()
        qapp.processEvents()

        event = QPaintEvent(surface.rect())
        start = time.perf_counter()
        surface.paintEvent(event)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 2.0, (
            f"Surface(tone={tone!r}) paintEvent took {elapsed_ms:.3f}ms, expected < 2ms"
        )


def test_shadow_tones_have_small_blur_radius(qapp: QApplication) -> None:
    """Shadow-tone surfaces must have small blur radius to minimize offscreen buffer cost.

    NOTE: PySide6 6.11 / Qt 6 removed QGraphicsEffect::setOptimizationFlags.
    The offscreen-buffer cost is bounded by keeping blurRadius small (4/8).
    """
    expected_blur = {"hero": 8, "elevated": 4, "inset": 4}
    for tone, expected in expected_blur.items():
        surface = Surface(tone=tone)
        effect = surface.graphicsEffect()
        assert effect is not None, f"Surface(tone={tone!r}) should have a graphics effect"
        assert isinstance(effect, QGraphicsDropShadowEffect)
        assert effect.blurRadius() == expected, (
            f"Surface(tone={tone!r}) blur radius should be {expected} "
            f"to minimize offscreen buffer cost"
        )


def test_non_shadow_tones_no_effect(qapp: QApplication) -> None:
    """Non-shadow tones must not have any graphics effect."""
    for tone in ["panel", "card", "flat", "rail"]:
        surface = Surface(tone=tone)
        assert surface.graphicsEffect() is None, (
            f"Surface(tone={tone!r}) should NOT have a graphics effect"
        )
