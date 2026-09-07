"""Regression tests for visibility-managed desktop timers."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from novel_forge.desktop.components.page_visibility import PageVisibilityMixin
from novel_forge.desktop.pages.voice_studio.script_render_mixin import ScriptRenderMixin
from novel_forge.tts.schemas import EmotionTag


class _VisibilityPage(PageVisibilityMixin, QWidget):
    pass


def test_periodic_timer_started_while_hidden_resumes_on_show(qtbot: QtBot) -> None:
    page = _VisibilityPage()
    qtbot.addWidget(page)
    timer = page._register_periodic_timer(QTimer(page))
    timer.setInterval(10_000)

    page._start_visibility_periodic_timer(timer)
    assert not timer.isActive()

    page.show()
    qtbot.waitUntil(timer.isActive)

    page.hide()
    qtbot.waitUntil(lambda: not timer.isActive())
    page._stop_visibility_periodic_timer(timer)

    page.show()
    qtbot.wait(1)
    assert not timer.isActive()


def test_throttle_timer_never_restarts_after_hidden_update(qtbot: QtBot) -> None:
    page = _VisibilityPage()
    qtbot.addWidget(page)
    timer = page._register_throttle_timer(QTimer(page))
    timer.setSingleShot(True)
    timer.setInterval(10_000)

    page._start_visibility_throttle_timer(timer)
    assert not timer.isActive()

    page.show()
    qtbot.waitUntil(page.isVisible)
    page._start_visibility_throttle_timer(timer)
    assert timer.isActive()

    page.hide()
    qtbot.waitUntil(lambda: not timer.isActive())
    page._start_visibility_throttle_timer(timer)
    assert not timer.isActive()

    page.show()
    qtbot.wait(1)
    assert not timer.isActive()


def test_registering_active_timer_on_hidden_page_defers_it(qtbot: QtBot) -> None:
    page = _VisibilityPage()
    qtbot.addWidget(page)
    timer = QTimer(page)
    timer.setInterval(10_000)
    timer.start()

    page._register_periodic_timer(timer)
    assert not timer.isActive()

    page.show()
    qtbot.waitUntil(timer.isActive)


def test_script_meta_preserves_unicode_emotion_transition() -> None:
    segment = SimpleNamespace(
        emotion=EmotionTag.NEUTRAL,
        sub_emotion=EmotionTag.SAD,
        tone_hint="",
    )

    rendered = ScriptRenderMixin()._render_segment_meta(segment)

    assert f"→ {EmotionTag.SAD.value}" in rendered
