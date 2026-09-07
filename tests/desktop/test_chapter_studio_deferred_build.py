"""Regression tests for staged Chapter Studio construction."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage


def _page(qtbot: QtBot) -> ChapterStudioPage:
    page = ChapterStudioPage(defer_sections=True)
    qtbot.addWidget(page)
    return page


def test_hidden_chapter_studio_starts_with_lightweight_shell(qtbot: QtBot) -> None:
    page = _page(qtbot)

    assert not page.is_ui_ready()
    assert page._deferred_placeholder is not None
    assert not hasattr(page, "_project_combo")


def test_focus_and_clear_binding_replay_after_staged_build(qtbot: QtBot) -> None:
    page = _page(qtbot)

    page.focus_project("demo", 7)
    page.bind_studio(None)

    assert page._pending_focus_request == ("demo", 7)
    assert page._pending_studio_binding == (None,)
    assert page.current_project_id() == "demo"
    assert page.current_chapter_number() == 7

    page.show()
    qtbot.waitUntil(page.is_ui_ready, timeout=3000)

    assert page.current_project_id() == "demo"
    assert page.current_chapter_number() == 7
    assert page._pending_focus_request is None
    assert page._pending_studio_binding is None


def test_partial_chapter_studio_build_shutdown_is_safe(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page._run_deferred_build_step()

    page.shutdown()

    assert page._shutdown_done is True
    assert not page._deferred_build_timer.isActive()
