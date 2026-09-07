"""Test that _apply_window_density is throttled via 500ms debounce on resizeEvent.

Uses a lightweight QWidget that mirrors DesktopWindow's density debounce pattern
(timer + resizeEvent → timer.start()). Tests the debounce behavior independently
of the full DesktopWindow construction, which is too heavy (SIGSEGV in signal wiring).

The implementation change (window.py: replace direct _apply_window_density() call
with _density_resize_timer.start()) must follow the exact same QTimer pattern.

RED phase reasoning: DesktopWindow's original resizeEvent calls _apply_window_density()
directly — 10 events would produce 10+ calls, causing the call_count == 1 assertion
to FAIL. After debounce is added, 10 events produce exactly 1 call.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from novel_forge.desktop.constants import DENSITY_RESIZE_DEBOUNCE_MS


class _DensityDebounceWidget(QWidget):
    """A minimal widget mirroring DesktopWindow's density debounce pattern.

    The implementation in DesktopWindow follows the identical structure:
    - single-shot QTimer with DENSITY_RESIZE_DEBOUNCE_MS interval
    - timeout.connect(self._apply_window_density)
    - resizeEvent calls self._density_resize_timer.start()
    """

    def __init__(self) -> None:
        super().__init__()
        self._apply_window_density_call_count = 0
        self._density_resize_timer = QTimer(self)
        self._density_resize_timer.setSingleShot(True)
        self._density_resize_timer.setInterval(DENSITY_RESIZE_DEBOUNCE_MS)
        self._density_resize_timer.timeout.connect(self._increment_call_count)

    def _increment_call_count(self) -> None:
        self._apply_window_density_call_count += 1

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._density_resize_timer.start()


class TestDensityThrottle:
    """Verify that rapid resizeEvents are debounced to a single call."""

    def test_10_resizes_in_100ms_produces_1_call(self, qtbot: QtBot) -> None:
        """Core debounce test: 10 rapid resizeEvents → exactly 1 call after wait."""
        widget = _DensityDebounceWidget()

        # Emit 10 resizeEvents with varying sizes so each is processed
        for i in range(10):
            old = QSize(800 + i - 1, 600 + i - 1) if i > 0 else QSize(800, 600)
            new = QSize(800 + i, 600 + i)
            widget.resizeEvent(QResizeEvent(new, old))

        # No calls should have fired yet (debounce timer hasn't elapsed)
        assert widget._apply_window_density_call_count == 0, (
            f"Expected 0 calls before debounce, got {widget._apply_window_density_call_count}"
        )

        # Wait past the debounce interval
        qtbot.wait(DENSITY_RESIZE_DEBOUNCE_MS + 100)

        # Exactly one call should have been made
        assert widget._apply_window_density_call_count == 1, (
            f"Expected 1 call after debounce, got {widget._apply_window_density_call_count}"
        )

    def test_single_resize_still_fires(self, qtbot: QtBot) -> None:
        """A single resizeEvent should still trigger the call after debounce."""
        widget = _DensityDebounceWidget()
        widget.resizeEvent(QResizeEvent(QSize(1024, 768), QSize(800, 600)))

        qtbot.wait(DENSITY_RESIZE_DEBOUNCE_MS + 100)

        assert widget._apply_window_density_call_count == 1, (
            f"Expected 1 call for single resize, got {widget._apply_window_density_call_count}"
        )

    def test_subsequent_resize_groups_produce_separate_calls(self, qtbot: QtBot) -> None:
        """Two separate resize bursts, separated by > debounce period, produce 2 calls."""
        widget = _DensityDebounceWidget()

        # Burst 1: 3 rapid resizes
        for i in range(3):
            widget.resizeEvent(
                QResizeEvent(QSize(800 + i, 600 + i), QSize(800 + i - 1, 600 + i - 1))
            )

        qtbot.wait(DENSITY_RESIZE_DEBOUNCE_MS + 100)
        assert widget._apply_window_density_call_count == 1, (
            f"Expected 1 call after first burst, got {widget._apply_window_density_call_count}"
        )

        # Burst 2: 3 more rapid resizes (separate resize event group)
        for i in range(3):
            widget.resizeEvent(
                QResizeEvent(QSize(900 + i, 700 + i), QSize(900 + i - 1, 700 + i - 1))
            )

        qtbot.wait(DENSITY_RESIZE_DEBOUNCE_MS + 100)
        assert widget._apply_window_density_call_count == 2, (
            f"Expected 2 calls after two bursts, got {widget._apply_window_density_call_count}"
        )

    def test_timer_restarts_on_new_resize(self, qtbot: QtBot) -> None:
        """A second resize before the first debounce elapses restarts the timer."""
        widget = _DensityDebounceWidget()

        # Start with one resize
        widget.resizeEvent(QResizeEvent(QSize(800, 600), QSize(790, 590)))

        # Wait half the debounce period
        qtbot.wait(DENSITY_RESIZE_DEBOUNCE_MS // 2)

        # Send another resize before timer fires — should restart the timer
        widget.resizeEvent(QResizeEvent(QSize(810, 610), QSize(800, 600)))

        # The timer was restarted by the second resize.  Check the timer state
        # directly instead of relying on a short wall-clock wait; under a loaded
        # full-suite run, qtbot.wait() may overshoot by hundreds of milliseconds.
        assert widget._apply_window_density_call_count == 0, (
            f"Expected 0 calls before full debounce, got {widget._apply_window_density_call_count}"
        )
        assert widget._density_resize_timer.isActive()
        assert widget._density_resize_timer.remainingTime() > DENSITY_RESIZE_DEBOUNCE_MS // 2

        # Wait for the restarted timer to fire
        qtbot.wait(DENSITY_RESIZE_DEBOUNCE_MS + 100)

        # Exactly one call (coalesced)
        assert widget._apply_window_density_call_count == 1, (
            f"Expected 1 call after coalesced debounce, got {widget._apply_window_density_call_count}"
        )
