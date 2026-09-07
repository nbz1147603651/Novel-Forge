"""Detect macOS (and cross-platform) sleep/wake transitions via heartbeat timer.

When the system sleeps, Qt timers are suspended.  On wake, the heartbeat timer
fires and detects a large gap between the expected and actual elapsed time.
This triggers a ``system_woke`` signal that the main window uses to perform
a graceful recovery (suppress FS watcher storms, stagger timer restarts, etc.).

Usage::

    monitor = SleepWakeMonitor(parent=self)
    monitor.system_woke.connect(self._on_system_woke)
    monitor.start()
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

_logger = logging.getLogger(__name__)

# Heartbeat interval: fires every 2 seconds under normal operation.
_HEARTBEAT_INTERVAL_MS = 2_000

# If the gap between two consecutive heartbeats exceeds this threshold,
# we conclude the system was sleeping.  8 seconds gives ample margin over
# the 2-second interval to avoid false positives from brief event-loop stalls.
_SLEEP_GAP_THRESHOLD_S = 8.0

# After detecting wake, the monitor enters a "recovery window" during which
# downstream consumers can suppress noisy events (FS watcher, timer bursts).
_RECOVERY_WINDOW_S = 6.0


class SleepWakeMonitor(QObject):
    """Heartbeat-based sleep/wake detector for Qt applications.

    Signals:
        system_woke: Emitted once when the system resumes from sleep.
            The argument is the approximate sleep duration in seconds.
    """

    system_woke = Signal(float)  # sleep_duration_seconds

    def __init__(self, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(_HEARTBEAT_INTERVAL_MS)
        self._timer.timeout.connect(self._on_heartbeat)
        self._last_beat: float = 0.0
        self._wake_time: float = 0.0
        self._running = False

    def start(self) -> None:
        """Begin monitoring for sleep/wake transitions."""
        if self._running:
            return
        self._running = True
        self._last_beat = time.monotonic()
        self._timer.start()

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        self._timer.stop()

    @property
    def in_recovery_window(self) -> bool:
        """True if the system recently woke and is still in the recovery window.

        Downstream consumers (FS watcher, workspace refresh, autorun ticker)
        should suppress or defer non-critical work during this window.
        """
        if self._wake_time <= 0:
            return False
        return (time.monotonic() - self._wake_time) < _RECOVERY_WINDOW_S

    @property
    def seconds_since_wake(self) -> float:
        """Seconds elapsed since the last wake event, or inf if never woke."""
        if self._wake_time <= 0:
            return float("inf")
        return time.monotonic() - self._wake_time

    def _on_heartbeat(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_beat
        self._last_beat = now

        if elapsed < _SLEEP_GAP_THRESHOLD_S:
            return

        # Large gap detected — system was sleeping.
        sleep_duration = elapsed - (_HEARTBEAT_INTERVAL_MS / 1000.0)
        self._wake_time = now
        _logger.info(
            "System wake detected | sleep_duration=%.1fs | recovery_window=%.1fs",
            sleep_duration,
            _RECOVERY_WINDOW_S,
        )
        self.system_woke.emit(sleep_duration)
