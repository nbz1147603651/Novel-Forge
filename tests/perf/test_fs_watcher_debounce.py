"""Test: fs watcher fileChanged events are debounced (10 rapid events → 1 refresh).

Verifies that the QFileSystemWatcher debounce timer coalesces rapid file-change
signals into a single refresh_workspace(force=True) call within the debounce window.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402


class _FakeFsWatcher(QObject):
    """Minimal stand-in for QFileSystemWatcher with emit-able signals."""

    fileChanged = Signal(str)
    directoryChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._paths: list[str] = []

    def files(self) -> list[str]:
        return list(self._paths)

    def directories(self) -> list[str]:
        return []

    def addPath(self, path: str) -> None:
        self._paths.append(path)

    def removePath(self, path: str) -> None:
        if path in self._paths:
            self._paths.remove(path)

    def blockSignals(self, block: bool) -> bool:
        return super().blockSignals(block)

    def signalsBlocked(self) -> bool:
        return super().signalsBlocked()


def _build_debounce_harness() -> tuple[_FakeFsWatcher, QTimer, MagicMock]:
    """Create a fake watcher + debounce timer + mock refresh, wired like window.py."""
    from novel_forge.desktop.constants import FS_WATCHER_DEBOUNCE_MS

    watcher = _FakeFsWatcher()
    mock_refresh = MagicMock()

    debounce_timer = QTimer()
    debounce_timer.setSingleShot(True)
    debounce_timer.setInterval(FS_WATCHER_DEBOUNCE_MS)
    debounce_timer.timeout.connect(lambda: mock_refresh(force=True))

    # Wire watcher → debounce timer (same pattern as window.py)
    watcher.fileChanged.connect(lambda _path="": debounce_timer.start())
    watcher.directoryChanged.connect(lambda _path="": debounce_timer.start())

    return watcher, debounce_timer, mock_refresh


class TestFsWatcherDebounce:
    """10 rapid fileChanged events should trigger exactly 1 refresh after debounce."""

    def test_rapid_events_coalesced_to_single_refresh(
        self, qapp: object, qtbot: Any
    ) -> None:
        watcher, timer, mock_refresh = _build_debounce_harness()

        # Emit 10 rapid fileChanged signals
        for i in range(10):
            watcher.fileChanged.emit(f"/fake/project/spec_{i}.json")

        # Before timer fires, no refresh should have happened
        assert mock_refresh.call_count == 0

        # Wait for the debounce timer to fire
        with qtbot.waitSignal(timer.timeout, timeout=10_000):
            pass

        # Exactly 1 refresh should have been triggered
        assert mock_refresh.call_count == 1
        mock_refresh.assert_called_with(force=True)

    def test_separated_events_trigger_multiple_refreshes(
        self, qapp: object, qtbot: Any
    ) -> None:
        """Events separated by more than the debounce interval each trigger a refresh."""
        from novel_forge.desktop.constants import FS_WATCHER_DEBOUNCE_MS

        watcher, timer, mock_refresh = _build_debounce_harness()

        # First batch
        watcher.fileChanged.emit("/fake/project/spec.json")
        with qtbot.waitSignal(timer.timeout, timeout=10_000):
            pass
        assert mock_refresh.call_count == 1

        # Wait for full debounce interval to ensure timer is idle
        qtbot.wait(FS_WATCHER_DEBOUNCE_MS + 100)

        # Second batch
        watcher.fileChanged.emit("/fake/project/outline.json")
        with qtbot.waitSignal(timer.timeout, timeout=10_000):
            pass
        assert mock_refresh.call_count == 2
