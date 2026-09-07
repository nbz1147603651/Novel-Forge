"""Tests for Memory Panel visual polish (Task 23).

Locks down the new micro-interactions on the memory surface:
- MemoryMotifCard / MemorySuggestionCard hover: QSS-only border/background
  state, with graphics effects disabled so child labels remain visible in
  macOS/Qt scroll-area compositing.
- UnifiedMemoryPanel tab switch: 200ms opacity fade via Motion.fade_in
  on the newly-active tab page (D1-safe).
- MemoryBadge status transition: 200ms windowOpacity fade (D1-safe,
  composes with QSS border without QGraphicsOpacityEffect).
- Edge case: empty memory (no motifs, no suggestions) constructs the
  panel cleanly without crashing.

The tests run under ``QT_QPA_PLATFORM=offscreen`` so no real display
window is opened.
"""

from __future__ import annotations

import os
from contextlib import suppress

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPropertyAnimation  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from novel_forge.desktop.components.memory_components import (  # noqa: E402
    MemoryBadge,
    MemoryMotifCard,
    MemorySuggestionCard,
    UnifiedMemoryPanel,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def cleanup_memory_test_widgets(qapp: QApplication) -> object:
    existing_widget_ids = {id(widget) for widget in qapp.topLevelWidgets()}
    yield

    for widget in list(qapp.topLevelWidgets()):
        if id(widget) in existing_widget_ids:
            continue
        if not isinstance(
            widget,
            (MemoryBadge, MemoryMotifCard, MemorySuggestionCard, UnifiedMemoryPanel),
        ):
            continue
        # Restrict cleanup to this module's widget types. Timers from earlier
        # desktop tests may create unrelated top-level windows after the
        # snapshot above; deleting those through a global DeferredDelete flush
        # can tear down live Qt objects owned by another fixture.
        with suppress(RuntimeError, AttributeError):
            widget.close()
        with suppress(RuntimeError):
            widget.deleteLater()
            QCoreApplication.sendPostedEvents(widget, QEvent.DeferredDelete)


# ── MemoryMotifCard hover stability ───────────────────────────────


class TestMemoryMotifCardHover:
    """MemoryMotifCard hover is QSS-only and scroll-area stable."""

    def test_card_opts_into_qss_hover_without_graphics_effect(
        self, qapp: QApplication
    ) -> None:
        card = MemoryMotifCard(
            motif_id="m1",
            category="象征",
            description="旧钢琴",
            occurrence_count=3,
            last_chapter=2,
            status="active",
            name="遗物",
        )

        assert card.property("memoryHover") is True
        assert card.property("memoryPanelSurface") is True
        assert card.graphicsEffect() is None

    def test_card_has_no_hover_animation_state(self, qapp: QApplication) -> None:
        card = MemoryMotifCard(
            motif_id="m1",
            category="象征",
            description="旧钢琴",
            occurrence_count=3,
            last_chapter=2,
            status="active",
            name="遗物",
        )

        assert not hasattr(card, "_hover_anim")
        assert not hasattr(card, "_scale")

    def test_rapid_hover_no_crash(self, qapp: QApplication) -> None:
        card = MemoryMotifCard(
            motif_id="m1",
            category="象征",
            description="desc",
            occurrence_count=1,
        )
        card.show()
        for _ in range(20):
            qapp.sendEvent(card, QEvent(QEvent.Type.Enter))
            qapp.sendEvent(card, QEvent(QEvent.Type.Leave))
        assert card.graphicsEffect() is None


# ── MemorySuggestionCard hover stability ─────────────────────────


class TestMemorySuggestionCardHover:
    """MemorySuggestionCard hover is QSS-only and scroll-area stable."""

    def test_card_opts_into_qss_hover_without_graphics_effect(
        self, qapp: QApplication
    ) -> None:
        card = MemorySuggestionCard(
            motif_id="m2",
            suggestion="建议在下一章强化母题",
            priority="high",
            category="象征",
            motif_name="遗物",
        )

        assert card.property("memoryHover") is True
        assert card.property("memoryPanelSurface") is True
        assert card.graphicsEffect() is None

    def test_card_has_no_hover_animation_state(self, qapp: QApplication) -> None:
        card = MemorySuggestionCard(
            motif_id="m2",
            suggestion="建议",
            priority="medium",
        )

        assert not hasattr(card, "_hover_anim")
        assert not hasattr(card, "_scale")


# ── Tab switch fade animation ────────────────────────────────────


class TestTabSwitchFade:
    """UnifiedMemoryPanel tab switch: 200ms opacity fade on new page."""

    def test_tab_switch_fade_duration_constant(self, qapp: QApplication) -> None:
        assert UnifiedMemoryPanel.TAB_SWITCH_FADE_DURATION_MS == 200

    def test_panel_construction_creates_tab_widget(self, qapp: QApplication) -> None:
        panel = UnifiedMemoryPanel()
        try:
            assert panel._tab_widget is not None
            assert panel._tab_widget.count() == 7, (
                "UnifiedMemoryPanel should expose 7 tabs (概览/母题/关系/问题/追读力/护栏/控制)"
            )
        finally:
            panel.close()
            panel.deleteLater()
            qapp.processEvents()

    def test_changing_tab_triggers_200ms_fade(self, qapp: QApplication) -> None:
        panel = UnifiedMemoryPanel()
        try:
            panel.show()
            # Switch from index 0 (概览) to index 1 (母题).
            panel._tab_widget.setCurrentIndex(1)
            assert panel._tab_fade_anim is not None, (
                "Tab switch must create a fade animation on the new page"
            )
            assert panel._tab_fade_anim.duration() == 200, (
                f"Tab switch fade must be 200ms, got {panel._tab_fade_anim.duration()}ms"
            )
        finally:
            panel.close()
            panel.deleteLater()
            qapp.processEvents()

    def test_rapid_tab_switches_only_keep_latest(self, qapp: QApplication) -> None:
        panel = UnifiedMemoryPanel()
        try:
            panel.show()
            for idx in (1, 2, 3, 4, 5, 6):
                panel._tab_widget.setCurrentIndex(idx)
            # After the rapid switches, the latest anim is the one
            # cached on the panel (older ones are stopped by Motion).
            assert panel._tab_fade_anim is not None
            assert panel._tab_fade_anim.duration() == 200
            assert panel._tab_widget.currentIndex() == 6
        finally:
            panel.close()
            panel.deleteLater()
            qapp.processEvents()


# ── MemoryBadge status transition fade ───────────────────────────


class TestMemoryBadgeStatusFade:
    """MemoryBadge status change: 200ms windowOpacity fade."""

    def test_status_fade_duration_constant(self, qapp: QApplication) -> None:
        assert MemoryBadge.STATUS_FADE_DURATION_MS == 200

    def test_first_set_memory_status_does_not_fade(self, qapp: QApplication) -> None:
        """The initial status in __init__ is the baseline — no fade."""
        badge = MemoryBadge("test", memory_status="active")
        assert badge._current_status == "active"
        assert badge._status_fade_anim is None, (
            "Initial status assignment must not trigger a fade (no transition)"
        )

    def test_status_change_triggers_200ms_fade(self, qapp: QApplication) -> None:
        badge = MemoryBadge("test", memory_status="active")
        badge.show()
        badge.set_memory_status("warning")
        assert badge._current_status == "warning"
        assert badge._status_fade_anim is not None, (
            "Status change must create a fade animation"
        )
        assert badge._status_fade_anim.duration() == 200, (
            f"Status fade must be 200ms, got {badge._status_fade_anim.duration()}ms"
        )

    def test_repeat_status_no_fade(self, qapp: QApplication) -> None:
        """Re-setting the same status must not re-trigger the fade."""
        badge = MemoryBadge("test", memory_status="active")
        badge.show()
        badge.set_memory_status("active")  # same as current
        assert badge._status_fade_anim is None, (
            "Setting the same status must not trigger a fade"
        )

    def test_status_fade_animates_window_opacity(self, qapp: QApplication) -> None:
        badge = MemoryBadge("test", memory_status="active")
        badge.show()
        badge.set_memory_status("success")
        anim = badge._status_fade_anim
        assert anim is not None
        assert anim.propertyName() == b"windowOpacity"
        assert anim.startValue() == pytest.approx(0.6, abs=1e-6)
        assert anim.endValue() == pytest.approx(1.0, abs=1e-6)

    def test_status_fade_ignores_deleted_qt_animation_refs(
        self, qapp: QApplication
    ) -> None:
        """A stale PySide animation wrapper must not crash badge refreshes."""

        class DeletedQtAnimation:
            finished = None

            def stop(self) -> None:
                raise RuntimeError(
                    "libshiboken: Internal C++ object "
                    "(PySide6.QtCore.QPropertyAnimation) already deleted."
                )

        badge = MemoryBadge("test", memory_status="active")
        badge.show()
        badge._status_fade_anim = DeletedQtAnimation()

        badge.set_memory_status("warning")

        assert badge._current_status == "warning"
        assert isinstance(badge._status_fade_anim, QPropertyAnimation)

    def test_status_fade_ref_clears_after_finish(self, qapp: QApplication) -> None:
        badge = MemoryBadge("test", memory_status="active")
        badge.show()
        badge.set_memory_status("warning")
        anim = badge._status_fade_anim
        assert anim is not None

        anim.finished.emit()

        assert badge._status_fade_anim is None


# ── Edge case: empty memory constructs cleanly ───────────────────


class TestEmptyMemoryNoCrash:
    """UnifiedMemoryPanel with no data constructs without error."""

    def test_empty_panel_construction(self, qapp: QApplication) -> None:
        panel = UnifiedMemoryPanel()
        try:
            panel.show()
            qapp.processEvents()
            assert panel._tab_widget.count() == 7
        finally:
            panel.close()
            panel.deleteLater()
            qapp.processEvents()

    def test_empty_panel_tab_switch_safe(self, qapp: QApplication) -> None:
        panel = UnifiedMemoryPanel()
        try:
            panel.show()
            # Cycle through every tab on an empty panel.
            for idx in range(panel._tab_widget.count()):
                panel._tab_widget.setCurrentIndex(idx)
                qapp.processEvents()
            assert panel._tab_widget.currentIndex() == panel._tab_widget.count() - 1
        finally:
            panel.close()
            panel.deleteLater()
            qapp.processEvents()

    def test_empty_motifs_renders_hint(self, qapp: QApplication) -> None:
        """update_motifs with no data must not crash and must not break tab switching."""
        panel = UnifiedMemoryPanel()
        try:
            panel.show()
            panel.update_motifs(motifs=[], suggestions=[], warnings=[])
            qapp.processEvents()
            # Tab switching should still work after an empty update.
            panel._tab_widget.setCurrentIndex(1)
            qapp.processEvents()
            assert panel._tab_widget.currentIndex() == 1
        finally:
            panel.close()
            panel.deleteLater()
            qapp.processEvents()
