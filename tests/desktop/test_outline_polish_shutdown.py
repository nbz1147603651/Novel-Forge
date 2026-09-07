"""Shutdown behavior for the legacy outline-polish panel."""

from __future__ import annotations

from unittest.mock import MagicMock

from novel_forge.desktop.pages.workflow.components import OutlinePolishPanel


def test_outline_polish_shutdown_cancels_own_worker_without_waiting_shared_pool(
    qtbot,
) -> None:
    panel = OutlinePolishPanel()
    qtbot.addWidget(panel)
    worker = MagicMock()
    panel._polish_worker = worker
    panel._threadpool = MagicMock()

    panel.shutdown()

    worker.request_cancel.assert_called_once_with()
    worker.signals.suggestions_ready.disconnect.assert_called_once_with(
        panel._show_suggestions
    )
    worker.signals.polish_done.disconnect.assert_called_once_with(panel._on_polish_done)
    worker.signals.error.disconnect.assert_called_once_with(panel._on_worker_error)
    panel._threadpool.waitForDone.assert_not_called()
    assert panel._polish_worker is None
