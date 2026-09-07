"""Unit tests for page transition animation (task 16 of desktop UI plan).

Covers:
- Fade-in animation: 300ms duration with OutCubic easing
- Hidden pages never receive an opacity effect during a switch
- macOS granularity (D1): slide geometry animation is skipped
- Non-macOS: optional slide-from-right runs alongside fade
- Rapid page switching: no crash, no stale animation leaks
- Public ``switch_page`` API signature is unchanged
"""

from __future__ import annotations

import inspect
import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QLabel,
    QStackedWidget,
    QWidget,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Module-scoped QApplication for Qt widget tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeWindowHandle:
    def __init__(self, visibility: QWindow.Visibility) -> None:
        self._visibility = visibility

    def visibility(self) -> QWindow.Visibility:
        return self._visibility


class TransitionHarness(QWidget):
    """QWidget-based harness that binds the real window transition methods.

    Using a QWidget (not a MagicMock) as ``self`` so that ``QPropertyAnimation.setParent(self)``
    inside the implementation works (reparenting requires a QObject parent).
    """

    _animate_current_page = None  # bound below after class definition
    _animate_top_bar_title = None
    _clear_page_animation = None
    _apply_page_slide = None
    _skip_page_motion_for_window_state = None
    _skip_page_motion_for_graphics_effect_tree = None
    _widget_tree_has_graphics_effect = None

    def __init__(self) -> None:
        super().__init__()
        self._stack = QStackedWidget(self)
        self._old = QWidget()
        self._new = QWidget()
        self._stack.addWidget(self._old)
        self._stack.addWidget(self._new)
        self.resize(500, 400)
        self._stack.setGeometry(0, 0, 500, 400)
        self._old.setGeometry(0, 0, 500, 400)
        self._new.setGeometry(0, 0, 500, 400)
        self._stack.setCurrentWidget(self._old)
        self._previous_widget: QWidget | None = None
        self._active_page_id = "workflow"
        self._previous_page_id: str | None = "dashboard"
        self._page_animation: QPropertyAnimation | None = None
        self._page_animation_widget: QWidget | None = None
        self._force_instant_page_transition_once = False
        self._top_eyebrow = QLabel("eyebrow", self)
        self._top_title = QLabel("title", self)
        self._top_subtitle = QLabel("subtitle", self)
        self._top_bar_animations: list[QPropertyAnimation] = []
        self._fullscreen = False
        self._window_state = Qt.WindowState.WindowNoState
        self._window_handle_override: _FakeWindowHandle | None = None

    def isFullScreen(self) -> bool:  # noqa: N802
        return self._fullscreen

    def windowState(self) -> Qt.WindowState:  # noqa: N802
        return self._window_state

    def windowHandle(self):  # noqa: N802, ANN201
        if self._window_handle_override is not None:
            return self._window_handle_override
        return super().windowHandle()

    def _page_transition_profile(self, page_id: str | None) -> str:
        if page_id == "settings":
            return "instant"
        return "standard"


# Bind real methods AFTER the class is defined so __get__ works on instances.
from novel_forge.desktop.window import NovelForgeDesktopWindow  # noqa: E402

TransitionHarness._animate_current_page = NovelForgeDesktopWindow._animate_current_page
TransitionHarness._animate_top_bar_title = NovelForgeDesktopWindow._animate_top_bar_title
TransitionHarness._clear_page_animation = NovelForgeDesktopWindow._clear_page_animation
TransitionHarness._apply_page_slide = NovelForgeDesktopWindow._apply_page_slide
TransitionHarness._skip_page_motion_for_window_state = (
    NovelForgeDesktopWindow._skip_page_motion_for_window_state
)
TransitionHarness._skip_page_motion_for_graphics_effect_tree = (
    NovelForgeDesktopWindow._skip_page_motion_for_graphics_effect_tree
)
TransitionHarness._widget_tree_has_graphics_effect = (
    NovelForgeDesktopWindow._widget_tree_has_graphics_effect
)


def _switch(harness: TransitionHarness) -> None:
    """Simulate the slice of ``switch_page`` that feeds the animator."""
    harness._previous_widget = harness._stack.currentWidget()
    harness._previous_page_id = "dashboard"
    harness._active_page_id = "workflow"
    harness._stack.setCurrentWidget(harness._new)


def _running_anims(harness: TransitionHarness) -> list[QAbstractAnimation]:
    """All running animations parented to the harness."""
    return [
        a
        for a in harness.findChildren(QAbstractAnimation)
        if a.state() == QAbstractAnimation.Running
    ]


# ---------------------------------------------------------------------------
# Fade durations
# ---------------------------------------------------------------------------


class TestHiddenPreviousPage:
    """A page hidden by QStackedWidget must not leave an opacity cache behind."""

    def test_hidden_old_page_has_no_opacity_animation(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()
        harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        durations = sorted({a.duration() for a in opacity_anims})
        assert durations == [300]
        assert harness._old.graphicsEffect() is None


class TestFadeInDuration:
    """fade-in animation must use 300ms duration with OutCubic easing."""

    def test_fade_in_duration_300ms(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()
        harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        durations = sorted({a.duration() for a in running})
        assert 300 in durations, (
            f"Expected fade-in 300ms among running animations, got durations={durations}"
        )

    def test_fade_in_easing_out_cubic(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()
        harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        target = next((a for a in running if a.duration() == 300), None)
        assert target is not None, "Expected a 300ms animation"
        assert target.easingCurve().type() == QEasingCurve.Type.OutCubic


class TestInstantTransitionProfile:
    """Settings page switches must bypass page fade effects in both directions."""

    def test_force_instant_once_skips_page_fade(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._active_page_id = "workflow"
        harness._previous_page_id = "dashboard"
        harness._force_instant_page_transition_once = True
        harness._clear_page_animation()

        harness._animate_current_page()
        qapp.processEvents()

        assert _running_anims(harness) == []
        assert harness._force_instant_page_transition_once is False
        assert harness._previous_widget is None
        assert harness._old.graphicsEffect() is None
        assert harness._new.graphicsEffect() is None

    def test_entering_settings_skips_page_fade(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._active_page_id = "settings"
        harness._previous_page_id = "workflow"
        harness._clear_page_animation()

        harness._animate_current_page()
        qapp.processEvents()

        assert _running_anims(harness) == []
        assert harness._previous_widget is None
        assert harness._old.graphicsEffect() is None
        assert harness._new.graphicsEffect() is None

    def test_leaving_settings_skips_page_fade(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._active_page_id = "workflow"
        harness._previous_page_id = "settings"
        harness._clear_page_animation()

        harness._animate_current_page()
        qapp.processEvents()

        assert _running_anims(harness) == []
        assert harness._previous_widget is None
        assert harness._old.graphicsEffect() is None
        assert harness._new.graphicsEffect() is None


class TestGraphicsEffectTreeGuard:
    """Effectful page subtrees must not receive page-level opacity effects."""

    def test_new_page_descendant_effect_skips_page_fade(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        shadowed_child = QWidget(harness._new)
        shadowed_child.setGraphicsEffect(QGraphicsDropShadowEffect(shadowed_child))
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        harness._animate_current_page()
        qapp.processEvents()

        opacity_anims = [a for a in _running_anims(harness) if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._previous_widget is None
        assert harness._new.graphicsEffect() is None
        assert shadowed_child.graphicsEffect() is not None

    def test_old_page_descendant_effect_skips_page_fade(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        shadowed_child = QWidget(harness._old)
        shadowed_child.setGraphicsEffect(QGraphicsDropShadowEffect(shadowed_child))
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        harness._animate_current_page()
        qapp.processEvents()

        opacity_anims = [a for a in _running_anims(harness) if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._previous_widget is None
        assert harness._old.graphicsEffect() is None
        assert shadowed_child.graphicsEffect() is not None


# ---------------------------------------------------------------------------
# Platform granularity (D1)
# ---------------------------------------------------------------------------


class TestMacOSGuard:
    """On macOS (or patched to darwin), slide geometry animation must be skipped."""

    def test_no_geometry_animation_on_darwin(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        geom_anims = [
            a
            for a in running
            if a.propertyName() in (b"pos", b"geometry", b"minimumHeight", b"maximumHeight")
        ]
        assert geom_anims == [], (
            f"Expected zero geometry animations on macOS, got {[a.propertyName() for a in geom_anims]}"
        )

    def test_incoming_page_fade_still_runs_on_darwin(self, qapp: QApplication) -> None:
        """macOS keeps the incoming-page fade but never animates the hidden page."""
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        durations = sorted({a.duration() for a in opacity_anims})
        assert durations == [300]
        assert harness._old.graphicsEffect() is None

    def test_page_fade_skipped_on_darwin_fullscreen(self, qapp: QApplication) -> None:
        """macOS native fullscreen must not attach opacity effects during page switches."""
        harness = TransitionHarness()
        harness._fullscreen = True
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._previous_widget is None
        assert harness._new.graphicsEffect() is None

    def test_page_fade_skipped_on_darwin_fullscreen_window_state(self, qapp: QApplication) -> None:
        """macOS fullscreen can surface through windowState before isFullScreen."""
        harness = TransitionHarness()
        harness._window_state = Qt.WindowState.WindowFullScreen
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._previous_widget is None
        assert harness._new.graphicsEffect() is None

    def test_page_fade_skipped_on_darwin_fullscreen_window_visibility(
        self, qapp: QApplication
    ) -> None:
        """QWindow visibility catches native macOS Spaces fullscreen transitions."""
        harness = TransitionHarness()
        harness._window_handle_override = _FakeWindowHandle(QWindow.Visibility.FullScreen)
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._previous_widget is None
        assert harness._new.graphicsEffect() is None

    def test_top_bar_fade_skipped_on_darwin_fullscreen(self, qapp: QApplication) -> None:
        """Top-bar title fade is also skipped to keep macOS fullscreen stable."""
        harness = TransitionHarness()
        harness._fullscreen = True
        harness.show()
        qapp.processEvents()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_top_bar_title()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._top_bar_animations == []

    def test_top_bar_fade_skipped_on_darwin_fullscreen_window_state(
        self, qapp: QApplication
    ) -> None:
        """Top-bar title fade must also respect Qt's fullscreen state flag."""
        harness = TransitionHarness()
        harness._window_state = Qt.WindowState.WindowFullScreen
        harness.show()
        qapp.processEvents()

        with patch.object(sys, "platform", "darwin"):
            harness._animate_top_bar_title()
        qapp.processEvents()

        running = _running_anims(harness)
        opacity_anims = [a for a in running if a.propertyName() == b"opacity"]
        assert opacity_anims == []
        assert harness._top_bar_animations == []


class TestSlideOnNonMacOS:
    """On non-macOS, optional slide-from-right is enabled."""

    def test_slide_runs_on_linux(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        with patch.object(sys, "platform", "linux"):
            harness._animate_current_page()
        qapp.processEvents()

        running = _running_anims(harness)
        slide = [a for a in running if a.propertyName() == b"pos"]
        assert len(slide) >= 1, (
            f"Expected slide (pos) animation on Linux, got {[a.propertyName() for a in running]}"
        )


# ---------------------------------------------------------------------------
# switch_page public API contract
# ---------------------------------------------------------------------------


class TestSwitchPageAPIContract:
    """The public ``switch_page`` signature must stay backwards-compatible."""

    def test_switch_page_signature_unchanged(self) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        sig = inspect.signature(NovelForgeDesktopWindow.switch_page)
        params = list(sig.parameters.keys())
        # self + page_id
        assert params == ["self", "page_id"], (
            f"switch_page signature changed; expected ['self', 'page_id'], got {params}"
        )

    def test_switch_page_returns_none(self) -> None:
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        sig = inspect.signature(NovelForgeDesktopWindow.switch_page)
        # Under `from __future__ import annotations`, the annotation is the
        # string "None" rather than NoneType — compare by string.
        assert str(sig.return_annotation) == "None", (
            f"switch_page return annotation should be None, got {sig.return_annotation!r}"
        )


# ---------------------------------------------------------------------------
# Edge case: rapid page switching
# ---------------------------------------------------------------------------


class TestRapidPageSwitching:
    """Rapid sequential page switches must not crash or leak animations."""

    def test_many_rapid_switches_no_crash(self, qapp: QApplication) -> None:
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()

        for _ in range(20):
            _switch(harness)
            harness._clear_page_animation()
            harness._animate_current_page()
            qapp.processEvents()

        # No assertion beyond "didn't crash" — track the final state is sane.
        assert harness._stack.currentWidget() is harness._new
        # After all switches, _previous_widget should always be cleared.
        assert harness._previous_widget is None

    def test_animation_state_clean_after_rapid_switches(self, qapp: QApplication) -> None:
        """Each switch should not accumulate stale _page_animation references."""
        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()

        for _ in range(10):
            _switch(harness)
            harness._clear_page_animation()
            harness._animate_current_page()
            qapp.processEvents()

        # Final state: tracked animation matches the most recent new widget
        assert harness._page_animation_widget is harness._new


# ---------------------------------------------------------------------------
# Disabled animations: graceful no-op
# ---------------------------------------------------------------------------


class TestAnimationsDisabled:
    """When ANIMATIONS_ENABLED=False, _animate_current_page is a no-op."""

    def test_no_op_when_animations_disabled(self, qapp: QApplication) -> None:
        from novel_forge.desktop import constants

        harness = TransitionHarness()
        harness.show()
        qapp.processEvents()
        _switch(harness)
        harness._clear_page_animation()

        original = constants.ANIMATIONS_ENABLED
        try:
            constants.ANIMATIONS_ENABLED = False
            harness._animate_current_page()
            qapp.processEvents()

            running = _running_anims(harness)
            assert running == [], (
                f"Expected no animations when disabled, got {[a.propertyName() for a in running]}"
            )
        finally:
            constants.ANIMATIONS_ENABLED = original
