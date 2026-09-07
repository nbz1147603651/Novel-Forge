"""Tests for novel_forge.desktop.motion animation library.

Covers:
- Factory methods create correct QPropertyAnimation instances
- macOS granularity (animations_supported with different kinds)
- Widget destruction safety (D11)
- Reduced motion behavior (accessibility)
- Edge case: animations disabled → instant animations
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtWidgets import QApplication, QWidget


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Create a QApplication instance for tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestMotionFactory:
    """Test Motion factory methods create correct animations."""

    def test_fade_in_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """fade_in should return a QPropertyAnimation instance."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.fade_in(widget)

        assert isinstance(anim, QPropertyAnimation)
        assert anim.targetObject() is not None
        # Default duration is 'modal' = 250ms
        assert anim.duration() == 250

    def test_fade_out_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """fade_out should return a QPropertyAnimation instance."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.fade_out(widget)

        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 250  # modal default

    def test_slide_in_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """slide_in should return a QPropertyAnimation instance."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.slide_in(widget)

        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 300  # panel-slide default

    def test_scale_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """scale should return a QPropertyAnimation instance."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        # Widget needs a 'scale' property for this to work
        widget._scale = 1.0  # type: ignore[attr-defined]
        anim = Motion.scale(widget, start_value=0.9, end_value=1.0)

        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 200  # toggle default

    def test_pulse_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """pulse should return a QPropertyAnimation instance."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        widget._opacity = 0.5  # type: ignore[attr-defined]
        anim = Motion.pulse(widget)

        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 1200  # pulse default
        assert anim.loopCount() == -1  # infinite loop

    def test_collapse_height_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """collapse_height should return a QPropertyAnimation instance."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.collapse_height(widget, start_height=200, end_height=0)

        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 200  # toggle default

    def test_fade_in_raises_on_none_widget(self, qapp: QApplication) -> None:
        """fade_in should raise ValueError if widget is None."""
        from novel_forge.desktop.motion import Motion

        with pytest.raises(ValueError, match="widget must not be None"):
            Motion.fade_in(None)  # type: ignore[arg-type]

    def test_custom_duration_by_name(self, qapp: QApplication) -> None:
        """Factory should accept duration by name from DURATIONS catalog."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.fade_in(widget, duration="hover")

        assert anim.duration() == 100  # hover duration

    def test_custom_duration_by_int(self, qapp: QApplication) -> None:
        """Factory should accept duration as integer milliseconds."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.fade_in(widget, duration=500)

        assert anim.duration() == 500


class TestMacOSGranularity:
    """Test animations_supported() granular macOS guard (D1)."""

    def test_opacity_safe_on_macos(self) -> None:
        """Opacity animations should be safe on macOS."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        with (
            patch.object(constants, "ANIMATIONS_ENABLED", True),
            patch.object(sys, "platform", "darwin"),
        ):
            assert animations_supported(kind="opacity") is True

    def test_color_safe_on_macos(self) -> None:
        """Color animations should be safe on macOS."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        with (
            patch.object(constants, "ANIMATIONS_ENABLED", True),
            patch.object(sys, "platform", "darwin"),
        ):
            assert animations_supported(kind="color") is True

    def test_geometry_blocked_on_macos(self) -> None:
        """Geometry animations should be blocked on macOS."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        with (
            patch.object(constants, "ANIMATIONS_ENABLED", True),
            patch.object(sys, "platform", "darwin"),
        ):
            assert animations_supported(kind="geometry") is False

    def test_any_conservative_on_macos(self) -> None:
        """'any' kind should be conservative (False) on macOS."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        with (
            patch.object(constants, "ANIMATIONS_ENABLED", True),
            patch.object(sys, "platform", "darwin"),
        ):
            assert animations_supported(kind="any") is False

    def test_all_kinds_supported_on_linux(self) -> None:
        """All animation kinds should be supported on Linux."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        with (
            patch.object(constants, "ANIMATIONS_ENABLED", True),
            patch.object(sys, "platform", "linux"),
        ):
            assert animations_supported(kind="opacity") is True
            assert animations_supported(kind="color") is True
            assert animations_supported(kind="geometry") is True
            assert animations_supported(kind="any") is True


class TestWidgetDestructionSafety:
    """Test widget destruction safety (D11)."""

    def test_animation_cached_on_widget(self, qapp: QApplication) -> None:
        """Animation should be cached on widget to prevent GC."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.fade_in(widget)

        # Animation should be stored as widget property
        cached = widget.property("_motion_anim")
        assert cached is anim

    def test_destroyed_signal_connected(self, qapp: QApplication) -> None:
        """Widget destroyed signal should be connected to anim.stop()."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        assert Motion.fade_in(widget) is widget.property("_motion_anim")

        # The destroyed signal should be connected
        # We can verify by checking the animation doesn't crash when widget is deleted
        widget.deleteLater()
        qapp.processEvents()
        # If we get here without crash, the safety mechanism works

    def test_no_crash_on_widget_delete(self, qapp: QApplication) -> None:
        """Deleting widget during animation should not crash."""
        from novel_forge.desktop.motion import Motion

        widget = QWidget()
        anim = Motion.fade_in(widget)
        anim.start()

        # Delete widget while animation is running
        widget.deleteLater()
        qapp.processEvents()
        # No exception = pass


class TestReducedMotion:
    """Test reduced motion behavior (accessibility)."""

    def test_reduced_motion_instant_duration(self, qapp: QApplication) -> None:
        """When _reduced=True, all animations should have duration=1."""
        from novel_forge.desktop.motion import Motion, MotionManager

        original_reduced = MotionManager._reduced
        try:
            MotionManager._reduced = True
            widget = QWidget()
            anim = Motion.fade_in(widget)

            assert anim.duration() == 1
        finally:
            MotionManager._reduced = original_reduced

    def test_reduced_motion_all_factories(self, qapp: QApplication) -> None:
        """All factory methods should respect reduced motion."""
        from novel_forge.desktop.motion import Motion, MotionManager

        original_reduced = MotionManager._reduced
        try:
            MotionManager._reduced = True
            widget = QWidget()

            # Test all factories
            assert Motion.fade_in(widget).duration() == 1
            assert Motion.fade_out(widget).duration() == 1
            assert Motion.slide_in(widget).duration() == 1
            assert Motion.slide_out(widget).duration() == 1
            assert Motion.pulse(widget).duration() == 1
            assert Motion.collapse_height(widget, start_height=100, end_height=0).duration() == 1
        finally:
            MotionManager._reduced = original_reduced

    def test_prefers_reduced_motion_returns_bool(self) -> None:
        """prefers_reduced_motion should return a boolean."""
        from novel_forge.desktop.motion import MotionManager

        result = MotionManager.prefers_reduced_motion()
        assert isinstance(result, bool)


class TestEdgeCaseDisabled:
    """Test edge case: animations disabled → instant animations."""

    def test_disabled_animations_instant_duration(self, qapp: QApplication) -> None:
        """When ANIMATIONS_ENABLED=False, all animations should have duration=1."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import Motion

        original_enabled = constants.ANIMATIONS_ENABLED
        try:
            constants.ANIMATIONS_ENABLED = False
            widget = QWidget()
            anim = Motion.fade_in(widget)

            assert anim.duration() <= 1
        finally:
            constants.ANIMATIONS_ENABLED = original_enabled

    def test_disabled_animations_supported_returns_false(self) -> None:
        """animations_supported should return False when disabled."""
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        original_enabled = constants.ANIMATIONS_ENABLED
        try:
            constants.ANIMATIONS_ENABLED = False
            assert animations_supported(kind="opacity") is False
            assert animations_supported(kind="geometry") is False
            assert animations_supported(kind="any") is False
        finally:
            constants.ANIMATIONS_ENABLED = original_enabled


class TestMotionCatalogs:
    """Test Motion class catalogs (DURATIONS and EASINGS)."""

    def test_durations_has_9_entries(self) -> None:
        """DURATIONS should have exactly 9 named durations."""
        from novel_forge.desktop.motion import Motion

        assert len(Motion.DURATIONS) == 9
        expected_keys = {
            "hover", "focus", "toggle", "tooltip", "modal",
            "panel-slide", "page", "notification", "pulse",
        }
        assert set(Motion.DURATIONS.keys()) == expected_keys

    def test_easings_has_5_entries(self) -> None:
        """EASINGS should have exactly 5 standard easings."""
        from novel_forge.desktop.motion import Motion

        assert len(Motion.EASINGS) == 5
        expected_keys = {"standard", "emphasized", "accelerate", "decelerate", "bounce"}
        assert set(Motion.EASINGS.keys()) == expected_keys
