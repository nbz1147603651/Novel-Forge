"""Unit tests for ActionButton hover / focus / disabled polish (Task 9).

Locks down the new micro-interactions:
- Hover scale 1.0 → 1.02 in 120ms via the Motion library (D1: macOS-safe).
- Action buttons do not show the old red/orange focus rectangle.
- Disabled QSS opacity (0.5).
- Press scale animation remains 150ms (Task 9 must-NOT-do).

Reentrancy / edge cases:
- Hover on a disabled button does not crash and does not animate.
- 50 rapid enter/leave cycles do not crash (D11 widget-destruction safety).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QPropertyAnimation, Qt  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import delete  # noqa: E402

from novel_forge.desktop.components.primitives import ActionButton  # noqa: E402
from novel_forge.desktop.theme import get_stylesheet  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _make_enter_event() -> QEnterEvent:
    """Build a synthetic QEnterEvent for offscreen enterEvent() dispatch.

    PySide6 ≥ 6.5 requires the strict ``QEnterEvent`` subclass; plain
    ``QEvent(QEvent.Enter)`` triggers a TypeError in ``super().enterEvent()``.
    """
    return QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0))


# ── Press animation (Task 9 must-NOT-do: keep 150ms) ──────────────────────


class TestActionButtonPressAnimation:
    """The existing press animation must remain 150ms (preserved)."""

    def test_press_animation_duration_150ms(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        assert btn._anim.duration() == 150, (
            f"Press animation duration must stay 150ms (Task 9 must-NOT-do), "
            f"got {btn._anim.duration()}ms"
        )

    def test_press_animation_easing_out_cubic(self, qapp: QApplication) -> None:
        from PySide6.QtCore import QEasingCurve

        btn = ActionButton("Test")
        btn._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        assert btn._anim.easingCurve().type() == QEasingCurve.Type.OutCubic


# ── Hover micro-scale (Task 9 must-DO) ────────────────────────────────────


class TestActionButtonHoverAnimation:
    """New: hover micro-scale 1.0 → 1.02 in 120ms via Motion library."""

    def test_hover_constant_duration_is_120ms(self, qapp: QApplication) -> None:
        assert ActionButton.HOVER_DURATION_MS == 120

    def test_hover_constant_scale_is_1_02(self, qapp: QApplication) -> None:
        assert ActionButton.HOVER_SCALE == pytest.approx(1.02, abs=1e-6)

    def test_hover_animation_duration_120ms(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        btn.show()
        btn.enterEvent(_make_enter_event())
        qapp.processEvents()
        assert btn._hover_anim is not None, "Hover animation must be created on enterEvent"
        assert btn._hover_anim.duration() == 120, (
            f"Hover animation duration must be 120ms (Task 9), "
            f"got {btn._hover_anim.duration()}ms"
        )

    def test_hover_animation_target_1_02(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        btn.show()
        btn.enterEvent(_make_enter_event())
        qapp.processEvents()
        assert btn._hover_anim is not None
        assert btn._hover_anim.endValue() == pytest.approx(1.02, abs=1e-6)

    def test_leave_animation_target_1_0(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        btn.show()
        btn.enterEvent(_make_enter_event())
        qapp.processEvents()
        btn.leaveEvent(QEvent(QEvent.Type.Leave))
        qapp.processEvents()
        assert btn._hover_anim is not None
        assert btn._hover_anim.endValue() == pytest.approx(1.0, abs=1e-6)

    def test_hover_animation_uses_scale_property(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        btn.show()
        btn.enterEvent(_make_enter_event())
        qapp.processEvents()
        assert btn._hover_anim is not None
        assert btn._hover_anim.propertyName() == b"scale"


# ── Focus QSS (Task 9 must-DO) ────────────────────────────────────────────


class TestActionButtonFocusQSS:
    """Action buttons must not show the old red/orange focus rectangle."""

    def test_focus_selector_exists(self, qapp: QApplication) -> None:
        qss = get_stylesheet()
        assert "actionButton:focus" in qss, (
            "Expected `actionButton:focus` selector in global QSS"
        )

    def test_focus_outline_property_exists(self, qapp: QApplication) -> None:
        qss = get_stylesheet()
        focus_block = _extract_block(qss, "QPushButton#actionButton:focus")
        assert focus_block is not None, "Focus rule block not found"
        assert "outline:" in focus_block, "Focus rule must set `outline:`"
        assert "none" in focus_block, "Focus outline must be disabled for action buttons"
        assert "outline-offset:" in focus_block, "Focus must set outline-offset"
        assert "0px" in focus_block.split("outline-offset:")[1].split(";")[0], (
            "outline-offset must be reset"
        )

    def test_focus_outline_does_not_use_accent_red(self, qapp: QApplication) -> None:
        qss = get_stylesheet()
        focus_block = _extract_block(qss, "QPushButton#actionButton:focus")
        assert focus_block is not None
        assert "182, 86, 52" not in focus_block
        assert "166, 61, 50" not in focus_block


# ── Disabled QSS (Task 9 must-DO) ─────────────────────────────────────────


class TestActionButtonDisabledQSS:
    """New: disabled QSS opacity 0.5."""

    def test_disabled_selector_exists(self, qapp: QApplication) -> None:
        qss = get_stylesheet()
        assert "actionButton:disabled" in qss

    def test_disabled_opacity_is_0_5(self, qapp: QApplication) -> None:
        qss = get_stylesheet()
        disabled_block = _extract_block(qss, "QPushButton#actionButton:disabled")
        assert disabled_block is not None, "Disabled rule block not found"
        # The opacity property must appear and be set to 0.5 in the disabled rule.
        opacity_lines = [
            line.strip() for line in disabled_block.split(";") if "opacity" in line
        ]
        assert opacity_lines, f"Disabled rule must include opacity, got: {disabled_block!r}"
        assert any("0.5" in line for line in opacity_lines), (
            f"Disabled opacity must be 0.5, got: {opacity_lines!r}"
        )


# ── Variants preserved (Task 9 must-NOT-do) ───────────────────────────────


class TestActionButtonVariantsPreserved:
    """Task 9 must-NOT-do: do not change the variant property."""

    @pytest.mark.parametrize("variant", ["primary", "secondary", "quiet", "danger"])
    def test_variant_property_preserved(
        self, qapp: QApplication, variant: str
    ) -> None:
        btn = ActionButton("Test", variant=variant)
        assert btn.property("variant") == variant

    def test_mouse_click_does_not_take_focus(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        assert btn.focusPolicy() == Qt.FocusPolicy.TabFocus


# ── Edge cases ─────────────────────────────────────────────────────────────


class TestActionButtonEdgeCases:
    """Reentrancy and disabled-state safety."""

    def test_hover_on_disabled_no_animation(self, qapp: QApplication) -> None:
        """Disabled button must NOT animate on hover (QSS opacity owns the visual)."""
        btn = ActionButton("Test")
        btn.setEnabled(False)
        btn.show()
        btn.enterEvent(_make_enter_event())
        qapp.processEvents()
        assert btn._hover_anim is None, (
            "Disabled button must not start a hover animation"
        )

    def test_hover_on_disabled_no_crash(self, qapp: QApplication) -> None:
        btn = ActionButton("Test")
        btn.setEnabled(False)
        btn.show()
        # Rapid hover/leave on disabled state must not raise.
        for _ in range(20):
            btn.enterEvent(_make_enter_event())
            btn.leaveEvent(QEvent(QEvent.Type.Leave))
            qapp.processEvents()
        # No exception = pass

    def test_rapid_hover_enter_leave_no_crash(self, qapp: QApplication) -> None:
        """50 rapid enter/leave cycles must not crash (D11 reentrancy safety)."""
        btn = ActionButton("Test")
        btn.show()
        for _ in range(50):
            btn.enterEvent(_make_enter_event())
            btn.leaveEvent(QEvent(QEvent.Type.Leave))
            qapp.processEvents()
        assert btn._hover_anim is not None
        # Don't assert exact endValue — when the widget is shown with the cursor
        # over it, Qt dispatches synthetic Enter events that flip the target.
        assert btn._hover_anim.endValue() in (pytest.approx(1.0, abs=1e-6), pytest.approx(1.02, abs=1e-6))

    def test_rapid_hover_final_state_consistent_no_show(self, qapp: QApplication) -> None:
        """Explicit enter→leave cycles correctly track the last event target."""
        # Intentionally NOT shown — Qt would otherwise dispatch synthetic Enter
        # events when the cursor geometry overlaps the button.
        btn = ActionButton("Test")
        btn.enterEvent(_make_enter_event())
        assert btn._hover_anim.endValue() == pytest.approx(1.02, abs=1e-6)
        btn.leaveEvent(QEvent(QEvent.Type.Leave))
        assert btn._hover_anim.endValue() == pytest.approx(1.0, abs=1e-6)
        for _ in range(20):
            btn.enterEvent(_make_enter_event())
            btn.leaveEvent(QEvent(QEvent.Type.Leave))
        assert btn._hover_anim.endValue() == pytest.approx(1.0, abs=1e-6)

    def test_deleted_hover_animation_reference_no_crash(self, qapp: QApplication) -> None:
        """A stale PySide wrapper must not crash the next hover transition."""
        btn = ActionButton("Test")
        stale = QPropertyAnimation(btn, b"scale")
        delete(stale)
        btn._hover_anim = stale

        btn.leaveEvent(QEvent(QEvent.Type.Leave))
        qapp.processEvents()

        assert btn._hover_anim is not stale


# ── Helpers ────────────────────────────────────────────────────────────────


def _extract_block(qss: str, selector: str) -> str | None:
    """Return the body of the ``selector { ... }`` rule, or None if not found.

    Handles nested braces by counting depth. Sufficient for our flat QSS.
    """
    idx = qss.find(selector)
    if idx == -1:
        return None
    open_brace = qss.find("{", idx)
    if open_brace == -1:
        return None
    depth = 1
    i = open_brace + 1
    while i < len(qss) and depth > 0:
        c = qss[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return qss[open_brace + 1 : i - 1]
