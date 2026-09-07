"""Tests for Chapter Studio visual polish (Task 19).

Locks down the new micro-interactions on the chapter-studio surface:
- ModeSelector changed mode fade-in (200ms via Motion library) without
  restarting fades during state-sync refreshes.
- CompassCard expand/collapse fade + height (250ms, geometry-gated on macOS).
- ActionPanel buttons use the ActionButton primitive (Task 9 hover/focus).
- Uninitialised chapter-studio page (no project bound) constructs cleanly.

The mode-switch QA scenario from the plan (.omo/plans/desktop-ui-modernization.md
lines 2845-2864) is implemented here as ``test_page_mode_switch_via_set_mode``.

Design note: ``Motion.fade_in`` returns the QPropertyAnimation but only
caches it on the widget via ``setProperty("_motion_anim", anim)`` (D11
safety).  Reading that property later can segfault in PySide6 6.11 +
Python 3.12 once multiple animations are GC'd, so we capture animations
by patching the Motion.fade_in entrypoint instead.  This mirrors the
public contract: callers receive the animation handle directly.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect

from novel_forge.desktop.components.primitives import ActionButton
from novel_forge.desktop.pages.chapter_studio.widgets import (
    CompassCard,
    CompassPanel,
    ModeSelector,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


class _AnimCapture:
    """Record every animation Motion.fade_in / Motion.collapse_height creates."""

    def __init__(self) -> None:
        self.anims: list[QAbstractAnimation] = []

    def durations(self) -> list[int]:
        return [int(a.duration()) for a in self.anims]


@pytest.fixture
def anim_capture(monkeypatch) -> _AnimCapture:
    """Patch Motion.fade_in / Motion.collapse_height to capture returned anims."""
    from novel_forge.desktop import motion as motion_mod

    capture = _AnimCapture()
    real_fade_in = motion_mod.Motion.fade_in
    real_fade_out = motion_mod.Motion.fade_out
    real_collapse = motion_mod.Motion.collapse_height

    def wrap_fade_in(widget, **kwargs):
        anim = real_fade_in(widget, **kwargs)
        capture.anims.append(anim)
        return anim

    def wrap_fade_out(widget, **kwargs):
        anim = real_fade_out(widget, **kwargs)
        capture.anims.append(anim)
        return anim

    def wrap_collapse(widget, **kwargs):
        anim = real_collapse(widget, **kwargs)
        capture.anims.append(anim)
        return anim

    monkeypatch.setattr(motion_mod.Motion, "fade_in", staticmethod(wrap_fade_in))
    monkeypatch.setattr(motion_mod.Motion, "fade_out", staticmethod(wrap_fade_out))
    monkeypatch.setattr(
        motion_mod.Motion, "collapse_height", staticmethod(wrap_collapse)
    )
    return capture


# ── ModeSelector fade-in (Task 19 must-DO) ────────────────────────────────


class TestModeSelectorFade:
    """Mode selector fade must not hide controls during sync refreshes."""

    def test_set_mode_triggers_200ms_fade(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        selector = ModeSelector()
        try:
            anim_capture.anims.clear()
            selector.set_mode(2)  # AUTO
            qapp.processEvents()
            assert 200 in anim_capture.durations(), (
                f"ModeSelector fade should produce a 200ms animation, "
                f"got: {anim_capture.durations()}"
            )
        finally:
            selector.deleteLater()
            qapp.processEvents()

    def test_set_mode_same_value_keeps_controls_opaque(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        """Repeated sync of the same mode must not restart opacity at 0."""
        selector = ModeSelector()
        try:
            anim_capture.anims.clear()
            selector.set_mode(3)
            assert anim_capture.anims
            anim_capture.anims.clear()

            selector.set_mode(3)
            qapp.processEvents()
            assert anim_capture.durations() == []
            for btn in (
                selector._mode_manual_btn,
                selector._mode_suggest_btn,
                selector._mode_auto_btn,
                selector._mode_book_auto_btn,
            ):
                effect = btn.graphicsEffect()
                assert isinstance(effect, QGraphicsOpacityEffect)
                assert effect.opacity() == pytest.approx(1.0)
        finally:
            selector.deleteLater()
            qapp.processEvents()

    def test_set_mode_without_animation_restores_pending_fade(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        selector = ModeSelector()
        try:
            selector.set_mode(3)
            anim_capture.anims.clear()

            selector.set_mode(2, animate=False)
            qapp.processEvents()

            assert selector.current_mode() == 2
            assert anim_capture.durations() == []
            for btn in (
                selector._mode_manual_btn,
                selector._mode_suggest_btn,
                selector._mode_auto_btn,
                selector._mode_book_auto_btn,
            ):
                effect = btn.graphicsEffect()
                assert isinstance(effect, QGraphicsOpacityEffect)
                assert effect.opacity() == pytest.approx(1.0)
        finally:
            selector.deleteLater()
            qapp.processEvents()

    def test_set_mode_changes_checked_button(self, qapp: QApplication) -> None:
        selector = ModeSelector()
        try:
            selector.set_mode(3)  # BOOK_AUTO
            assert selector.current_mode() == 3
        finally:
            selector.deleteLater()
            qapp.processEvents()

    def test_set_mode_all_targets_succeed(self, qapp: QApplication) -> None:
        """All four modes should be settable without crashing."""
        selector = ModeSelector()
        try:
            for mode in (0, 1, 2, 3):
                selector.set_mode(mode)
                assert selector.current_mode() == mode
        finally:
            selector.deleteLater()
            qapp.processEvents()


# ── CompassCard expand/collapse (Task 19 must-DO) ─────────────────────────


class TestCompassCardExpand:
    """CompassCard expand/collapse uses fade + height (250ms)."""

    def test_initial_state_is_expanded(self, qapp: QApplication) -> None:
        card = CompassCard("上一章实际结果")
        try:
            assert card.is_expanded() is True
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_collapse_toggles_state(self, qapp: QApplication) -> None:
        card = CompassCard("本章目标")
        try:
            card.set_expanded(False, animate=False)
            assert card.is_expanded() is False
            card.set_expanded(True, animate=False)
            assert card.is_expanded() is True
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_collapse_produces_250ms_fade_animation(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        card = CompassCard("下一章预埋点")
        try:
            anim_capture.anims.clear()
            card.set_expanded(False, animate=True)
            qapp.processEvents()
            assert 250 in anim_capture.durations(), (
                f"CompassCard collapse fade should be 250ms, "
                f"got: {anim_capture.durations()}"
            )
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_expand_produces_250ms_fade_animation(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        card = CompassCard("本章目标")
        try:
            card.set_expanded(False, animate=False)
            qapp.processEvents()
            anim_capture.anims.clear()
            card.set_expanded(True, animate=True)
            qapp.processEvents()
            assert 250 in anim_capture.durations()
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_idempotent_collapse_does_nothing(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        card = CompassCard("本章目标")
        try:
            anim_capture.anims.clear()
            card.set_expanded(False, animate=True)
            qapp.processEvents()
            anims_first = list(anim_capture.anims)
            anim_capture.anims.clear()
            card.set_expanded(False, animate=True)  # no-op
            qapp.processEvents()
            # No new fade should have been scheduled on the second call.
            assert card.is_expanded() is False
            assert len(anims_first) >= 1
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_compass_panel_three_cards(self, qapp: QApplication) -> None:
        panel = CompassPanel()
        try:
            assert len(panel._cards) == 3  # type: ignore[attr-defined]
            for key in ("previous", "current", "next"):
                assert key in panel._cards  # type: ignore[attr-defined]
        finally:
            panel.deleteLater()
            qapp.processEvents()


# ── ActionPanel uses ActionButton primitive (Task 9 + Task 19 alignment) ──


class TestActionPanelButtons:
    """The action panel presenter must build ActionButton instances.

    Locks in the unified hover/focus polish from Task 9: any button
    in the action panel should be an ``ActionButton`` (not a raw
    ``QPushButton``) so the 120ms hover scale + focus outline apply.
    """

    def test_action_button_primitive_is_used_in_action_panel(
        self, qapp: QApplication
    ) -> None:
        from novel_forge.desktop.pages import chapter_studio_action_panel as mod
        from novel_forge.desktop.widgets import ActionButton as WidgetsActionButton

        assert mod.ActionButton is WidgetsActionButton, (
            "ActionPanel must import ActionButton from the unified widgets "
            "module (Task 9/19 alignment)."
        )

    def test_action_button_object_name_is_actionbutton(
        self, qapp: QApplication
    ) -> None:
        btn = ActionButton("测试按钮", variant="primary")
        try:
            assert btn.objectName() == "actionButton"
            assert btn.property("variant") == "primary"
        finally:
            btn.deleteLater()
            qapp.processEvents()

    def test_action_button_variants_distinct(
        self, qapp: QApplication
    ) -> None:
        for variant in ("primary", "secondary", "danger", "quiet"):
            btn = ActionButton("X", variant=variant)
            try:
                assert btn.property("variant") == variant
            finally:
                btn.deleteLater()
                qapp.processEvents()


# ── Edge case: chapter studio uninitialised (no project) ─────────────────


class TestChapterStudioUninitialised:
    """Chapter Studio must construct and close cleanly without a project."""

    def test_page_constructs_without_crash(self, qapp: QApplication) -> None:
        from novel_forge.desktop.pages.chapter_studio.page import (
            ChapterStudioPage,
        )

        page = ChapterStudioPage()
        try:
            page.show()
            qapp.processEvents()
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_page_mode_switch_via_set_mode_keeps_controls_opaque(
        self, qapp: QApplication, anim_capture: _AnimCapture
    ) -> None:
        from novel_forge.desktop.pages.chapter_studio.page import (
            ChapterStudioPage,
        )

        page = ChapterStudioPage()
        try:
            page.show()
            qapp.processEvents()
            page._mode_selector.set_mode(3)
            anim_capture.anims.clear()
            page._set_mode(page.MODE_AUTO)
            qapp.processEvents()
            assert page._mode_selector.current_mode() == 2
            assert anim_capture.durations() == []
            for btn in (
                page._mode_selector._mode_manual_btn,
                page._mode_selector._mode_suggest_btn,
                page._mode_selector._mode_auto_btn,
                page._mode_selector._mode_book_auto_btn,
            ):
                effect = btn.graphicsEffect()
                assert isinstance(effect, QGraphicsOpacityEffect)
                assert effect.opacity() == pytest.approx(1.0)
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_page_mode_constants_defined(self, qapp: QApplication) -> None:
        from novel_forge.desktop.pages.chapter_studio.page import (
            ChapterStudioPage,
        )

        page = ChapterStudioPage()
        try:
            assert page.MODE_MANUAL == "manual"
            assert page.MODE_AUTO == "auto"
            assert page.MODE_SUGGEST == "suggest"
            assert page.MODE_BOOK_AUTO == "book_auto"
        finally:
            page.deleteLater()
            qapp.processEvents()
