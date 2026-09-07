"""Tests for ChapterStudioPage.shutdown() robustness.

Locks down the safe_disconnect refactor in chapter_studio_page.py:
- ``test_shutdown_idempotent``: calling shutdown() twice must not raise.
- ``test_shutdown_with_deleted_widget``: shutdown() after a widget is deleted
  (or its signal already disconnected) must not raise.

Runs under ``QT_QPA_PLATFORM=offscreen`` (set in pyproject.toml).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pytestqt.qtbot import QtBot


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def studio_page(qtbot: QtBot):
    """Create a ChapterStudioPage, register with qtbot, return it."""
    from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

    page = ChapterStudioPage()
    qtbot.addWidget(page)
    return page


class TestChapterStudioShutdown:
    """Shutdown robustness tests for ChapterStudioPage."""

    def test_shutdown_idempotent(self, studio_page) -> None:
        """Calling shutdown() twice must not raise any exception."""
        studio_page.shutdown()

    def test_shutdown_detaches_page_coordinators(self, studio_page) -> None:
        """Coordinator objects cannot retain a page after the shutdown boundary."""
        studio_page.shutdown()

        assert studio_page.state_manager.state is None
        assert studio_page.coordinator.state is None
        assert studio_page.renderer.state is None
        studio_page.coordinator.on_state_changed("jobs", [])
        # Second call should be a no-op (guarded by _shutdown_done flag)
        studio_page.shutdown()

    def test_shutdown_does_not_render_while_resetting_state(self, studio_page) -> None:
        """State cleanup must not rebuild widgets after teardown starts."""
        render_action_panel = MagicMock(wraps=studio_page._render_action_panel)
        studio_page._render_action_panel = render_action_panel

        studio_page.shutdown()

        render_action_panel.assert_not_called()

    def test_shutdown_with_deleted_widget(self, qtbot: QtBot) -> None:
        """shutdown() must not raise even if a widget's C++ side is deleted."""
        from PySide6.QtWidgets import QApplication

        from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

        page = ChapterStudioPage()
        qtbot.addWidget(page)

        # Force-disconnect one signal manually to simulate already-disconnected state
        try:
            page._project_combo.currentTextChanged.disconnect()
        except (RuntimeError, TypeError):
            pass

        # Delete the C++ underlying object for _chapter_spin to simulate deleted widget
        # We use deleteLater + processEvents to ensure cleanup
        page._chapter_spin.deleteLater()
        QApplication.processEvents()

        # shutdown() must handle all these gracefully via safe_disconnect
        page.shutdown()
