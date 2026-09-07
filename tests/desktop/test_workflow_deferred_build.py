"""Regression tests for staged Workflow page construction."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from novel_forge.desktop.pages.workflow.page import WorkflowPage


def _page(qtbot: QtBot) -> WorkflowPage:
    page = WorkflowPage(defer_sections=True)
    qtbot.addWidget(page)
    return page


def test_deferred_workflow_starts_with_lightweight_shell(qtbot: QtBot) -> None:
    page = _page(qtbot)

    assert not page.is_ui_ready()
    assert page._deferred_placeholder is not None
    assert not hasattr(page, "_short_form")


def test_hidden_workflow_pauses_and_resumes_build(qtbot: QtBot) -> None:
    page = _page(qtbot)

    qtbot.wait(40)
    assert page._deferred_build_index == 0
    assert not page._deferred_build_timer.isActive()

    page.show()
    qtbot.waitUntil(page.is_ui_ready, timeout=3000)

    assert hasattr(page, "_short_form")
    assert hasattr(page, "_long_panel")
    assert hasattr(page, "_jobs_panel")


def test_focus_request_is_replayed_after_staged_build(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page.focus_long_chapter()

    assert page._pending_focus_request == ("long_chapter", "", None)

    page.show()
    qtbot.waitUntil(page.is_ui_ready, timeout=3000)

    assert page.current_mode() == "long"
    # Chapter continuation now lives in 章台; the legacy long sub-mode
    # intentionally remains on init after the queued request is replayed.
    assert page.current_long_mode() == "init"
    assert page._pending_focus_request is None


def test_partial_build_shutdown_is_safe(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page._build_hero_section()

    page.shutdown()

    assert page._shutdown_done is True
    assert not page._deferred_build_timer.isActive()
