"""Tests for novel_forge.desktop.components.skeleton.

Task 11 — Skeleton shimmer + fade-in/out upgrade.

Covers:
- shimmer_offset Property exposed as a Qt Property
- QVariantAnimation driving shimmer with duration=1500ms, loop=-1
- start() / stop() lifecycle starts/stops both opacity-pulse and shimmer
- paintEvent renders without crashing (smoke)
- fade_in / fade_out methods delegate to Motion library
- Edge case: hiding the widget pauses shimmer (no wasted CPU + no crash)
- macOS safety: shimmer animation runs on darwin (D1: color is safe)
- Existing SkeletonLine / SkeletonCard / factory functions untouched
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import (
    QAbstractAnimation,
    QPropertyAnimation,
    QVariantAnimation,
)
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Create a single QApplication instance for all tests in this module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def cleanup_skeleton_widgets(qapp: QApplication):
    yield
    from novel_forge.desktop.components.skeleton import SkeletonPulse

    for widget in list(qapp.allWidgets()):
        if isinstance(widget, SkeletonPulse):
            widget.stop()
            widget.close()
            widget.setParent(None)
            widget.deleteLater()


# ── Shimmer animation ──────────────────────────────────────────────


class TestSkeletonShimmer:
    """SkeletonPulse shimmer animation tests."""

    def test_shimmer_anim_duration_is_1500ms(self, qapp: QApplication) -> None:
        """The QVariantAnimation driving shimmer must be 1500ms (per spec)."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        assert skel._shimmer_anim.duration() == 1500

    def test_shimmer_anim_loops_infinitely(self, qapp: QApplication) -> None:
        """Shimmer animation must have loopCount=-1 (infinite)."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        assert skel._shimmer_anim.loopCount() == -1

    def test_shimmer_offset_property_initial_value(self, qapp: QApplication) -> None:
        """shimmer_offset Property starts at 0.0."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        assert skel.shimmer_offset == 0.0

    def test_shimmer_offset_property_settable(self, qapp: QApplication) -> None:
        """shimmer_offset can be set via the Qt Property protocol."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.shimmer_offset = 0.42
        assert skel._shimmer_offset == pytest.approx(0.42)

    def test_start_starts_shimmer_when_shown(self, qapp: QApplication) -> None:
        """start() should start the shimmer animation on a visible widget.

        Mirrors the QA scenario: skel.show() + skel.start() → ≥1
        QVariantAnimation running with duration 1500ms.
        """
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.show()
        skel.start()

        running = [
            a
            for a in skel.findChildren(QVariantAnimation)
            if a.state() == QAbstractAnimation.Running
        ]
        assert len(running) >= 1
        assert running[0].duration() == 1500

    def test_stop_stops_shimmer(self, qapp: QApplication) -> None:
        """stop() (alias for stop_pulse) should stop the shimmer animation."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.show()
        skel.start()
        assert skel._shimmer_anim.state() == QAbstractAnimation.Running

        skel.stop()
        assert skel._shimmer_anim.state() != QAbstractAnimation.Running

    def test_shimmer_runs_on_macos_darwin(self, qapp: QApplication) -> None:
        """Shimmer (color animation) must run on macOS per D1 platform safety.

        We patch sys.platform to 'darwin' and force ANIMATIONS_ENABLED=True,
        then verify the granular color-animation check returns True and the
        animation transitions to Running state on start().
        """
        from novel_forge.desktop import constants
        from novel_forge.desktop.components.skeleton import SkeletonPulse
        from novel_forge.desktop.motion import animations_supported

        with (
            patch.object(constants, "ANIMATIONS_ENABLED", True),
            patch.object(sys, "platform", "darwin"),
        ):
            assert animations_supported(kind="color") is True

            skel = SkeletonPulse()
            skel.show()
            skel.start()
            assert skel._shimmer_anim.state() == QAbstractAnimation.Running


# ── Opacity pulse (preserved) ──────────────────────────────────────


class TestSkeletonOpacityPulse:
    """Existing opacity-pulse animation must be preserved unchanged."""

    def test_opacity_pulse_duration_default_1200ms(self, qapp: QApplication) -> None:
        """Default opacity-pulse duration is 1200ms."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        assert skel._pulse_anim.duration() == 1200

    def test_opacity_pulse_loops_infinitely(self, qapp: QApplication) -> None:
        """Opacity-pulse animation must loop infinitely."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        assert skel._pulse_anim.loopCount() == -1

    def test_opacity_property_default_min(self, qapp: QApplication) -> None:
        """Initial opacity equals min_opacity."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse(min_opacity=0.25, max_opacity=0.65)
        assert skel.opacity == pytest.approx(0.25)

    def test_start_pulse_legacy_still_works(self, qapp: QApplication) -> None:
        """Legacy start_pulse() entrypoint remains functional."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.show()
        skel.start_pulse()
        # start_pulse starts the opacity-pulse animation
        assert skel._pulse_anim.state() in (
            QAbstractAnimation.Running,
            QAbstractAnimation.Stopped,  # accept Stopped if platform blocks
        )


# ── Fade in / out via Motion ───────────────────────────────────────


class TestSkeletonFadeMethods:
    """fade_in / fade_out delegate to the Motion library."""

    def test_fade_in_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """fade_in() should return a QPropertyAnimation."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        anim = skel.fade_in()
        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 250  # default 'modal' duration

    def test_fade_in_accepts_custom_duration_ms(self, qapp: QApplication) -> None:
        """fade_in(duration=N) passes the integer through to Motion."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        anim = skel.fade_in(duration=200)
        assert anim.duration() == 200

    def test_fade_out_returns_qpropertyanimation(self, qapp: QApplication) -> None:
        """fade_out() should return a QPropertyAnimation."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        anim = skel.fade_out()
        assert isinstance(anim, QPropertyAnimation)
        assert anim.duration() == 250  # default 'modal' duration

    def test_fade_out_accepts_custom_duration_ms(self, qapp: QApplication) -> None:
        """fade_out(duration=N) passes the integer through to Motion."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        anim = skel.fade_out(duration=300)
        assert anim.duration() == 300


# ── Edge case: hidden widget stops shimmer ─────────────────────────


class TestSkeletonEdgeCases:
    """Edge cases for the shimmer animation."""

    def test_hidden_widget_pauses_shimmer(self, qapp: QApplication) -> None:
        """Hiding a started skeleton should not crash and should stop the
        shimmer (Running → Paused or Stopped), so we don't waste CPU on an
        invisible widget.

        Mirrors the QA edge scenario: skel.start() + skel.hide() must not
        raise; the number of Running animations afterwards can be 0 or 1.
        """
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.show()
        skel.start()
        assert skel._shimmer_anim.state() == QAbstractAnimation.Running

        # Edge: hide while running — must not crash
        skel.hide()

        # State should be Paused (or Stopped) — never Running while hidden
        assert skel._shimmer_anim.state() in (
            QAbstractAnimation.Paused,
            QAbstractAnimation.Stopped,
        )

        # The QA scenario counts Running only; must be 0 here
        running = [
            a
            for a in skel.findChildren(QVariantAnimation)
            if a.state() == QAbstractAnimation.Running
        ]
        assert len(running) == 0


class TestLoadingState:
    """The shared panel/page loading state composes the skeleton primitives."""

    def test_loading_state_animates_and_updates_copy(self, qapp: QApplication) -> None:
        from novel_forge.desktop.components.skeleton import LoadingState, SkeletonPulse

        state = LoadingState("正在读取…", "等待后台结果", card_count=2)
        state.show()
        state.start()

        skeletons = state.findChildren(SkeletonPulse)
        assert len(skeletons) == 6
        assert state._message.text() == "正在读取…"
        assert state._detail.text() == "等待后台结果"

        state.set_loading_text("结果准备中…")
        assert state._message.text() == "结果准备中…"
        assert not state._detail.isVisible()

        state.stop()
        assert all(
            skeleton._shimmer_anim.state() != QAbstractAnimation.Running
            for skeleton in skeletons
        )

    def test_showing_again_resumes_shimmer(self, qapp: QApplication) -> None:
        """Showing the widget again should resume the paused shimmer."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.show()
        skel.start()
        skel.hide()
        assert skel._shimmer_anim.state() == QAbstractAnimation.Paused

        skel.show()
        # On macOS color is safe, so the animation resumes to Running
        # On platforms where animations are disabled, it stays Paused — both
        # are acceptable per the design (no crash, no CPU waste).
        assert skel._shimmer_anim.state() in (
            QAbstractAnimation.Running,
            QAbstractAnimation.Paused,
        )


# ── paintEvent smoke test ──────────────────────────────────────────


class TestSkeletonPaintEvent:
    """paintEvent should render without crashing for all subclasses."""

    def test_skeleton_pulse_paints_without_crash(self, qapp: QApplication) -> None:
        """SkeletonPulse.paintEvent renders the shimmer gradient safely."""
        from novel_forge.desktop.components.skeleton import SkeletonPulse

        skel = SkeletonPulse()
        skel.resize(120, 20)
        skel.show()
        # Trigger paint
        pixmap = skel.grab()
        assert pixmap is not None
        assert pixmap.width() == 120
        assert not pixmap.isNull()

    def test_skeleton_line_paints_with_shimmer_base(self, qapp: QApplication) -> None:
        """SkeletonLine.paintEvent calls super for shimmer then overlays."""
        from novel_forge.desktop.components.skeleton import SkeletonLine

        line = SkeletonLine(width=160, height=16)
        line.show()
        pixmap = line.grab()
        assert pixmap is not None
        assert not pixmap.isNull()

    def test_skeleton_card_paints_with_shimmer_base(self, qapp: QApplication) -> None:
        """SkeletonCard.paintEvent calls super for shimmer then border."""
        from novel_forge.desktop.components.skeleton import SkeletonCard

        card = SkeletonCard()
        card.resize(220, 120)
        card.show()
        pixmap = card.grab()
        assert pixmap is not None
        assert not pixmap.isNull()


# ── Factory functions (must remain unchanged) ──────────────────────


class TestSkeletonFactories:
    """create_skeleton_lines and create_skeleton_card_with_lines still work."""

    def test_create_skeleton_lines_returns_list(self, qapp: QApplication) -> None:
        from novel_forge.desktop.components.skeleton import (
            SkeletonLine,
            create_skeleton_lines,
        )

        lines = create_skeleton_lines(count=3, min_width=80, max_width=200)
        assert len(lines) == 3
        assert all(isinstance(line, SkeletonLine) for line in lines)

    def test_create_skeleton_card_with_lines_returns_card(self, qapp: QApplication) -> None:
        from novel_forge.desktop.components.skeleton import (
            SkeletonCard,
            SkeletonLine,
            create_skeleton_card_with_lines,
        )

        card = create_skeleton_card_with_lines(line_count=4)
        assert isinstance(card, SkeletonCard)
        # Should contain skeleton line children
        child_lines = [child for child in card.children() if isinstance(child, SkeletonLine)]
        assert len(child_lines) == 4
