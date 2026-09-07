"""Page shutdown helpers.

safe_shutdown_page() is the unified teardown for any page:
1. Cancel all workers passed in (via request_cancel()).
2. Disconnect signals (tolerate already-disconnected).
3. Stop and deleteLater timers.
4. NEVER call waitForDone on global thread pools — that's window's
   job in _pre_close_cleanup() via shutdown_desktop_thread_pools().

Why no waitForDone? Because page shutdown can be invoked mid-app
(e.g. user navigates away); waiting global UI I/O pool would block
on unrelated long tasks. Only the final app teardown should wait pools.
"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

from novel_forge.desktop.workers.base import BaseJobWorker


def disconnect_signals(
    pairs: Iterable[tuple[Signal, Callable]],
) -> None:
    """Disconnect signal/slot pairs, tolerating already-disconnected state.

    Many Qt signal/slot pairs are already disconnected by the time a
    page shutdown runs (e.g. owner deleted). Direct disconnect() would
    raise RuntimeError or TypeError. We swallow those — the contract
    is "best-effort teardown".
    """
    for signal, slot in pairs:
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass


def safe_shutdown_page(
    page: QWidget,
    *,
    workers: Iterable[BaseJobWorker] = (),
    timers: Iterable[QTimer] = (),
    signals_to_disconnect: Iterable[tuple[Signal, Callable]] = (),
) -> None:
    """Unified page teardown.

    Usage in page.shutdown() implementations:

        def shutdown(self):
            safe_shutdown_page(
                self,
                workers=[self._worker_a, self._worker_b],
                timers=[self._timer_a, self._timer_b],
                signals_to_disconnect=[(s, self._handler) for s, self._handler in ...],
            )

    This replaces ad-hoc try/except (RuntimeError, TypeError) blocks.
    """
    # 1. Cancel workers (safe to call repeatedly; uses threading.Event)
    for worker in workers:
        try:
            worker.request_cancel()
        except Exception:
            pass

    # 2. Disconnect signals
    disconnect_signals(signals_to_disconnect)

    # 3. Stop + deleteLater timers
    for timer in timers:
        try:
            if timer is not None and timer.isActive():
                timer.stop()
            timer.deleteLater()
        except (RuntimeError, TypeError):
            pass


__all__ = ["disconnect_signals", "safe_shutdown_page"]