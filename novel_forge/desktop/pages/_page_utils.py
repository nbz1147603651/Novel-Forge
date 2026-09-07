"""Shared utilities for desktop page modules.

Extracts common patterns used across multiple page files:
- Safe signal disconnection
- Thread pool waiting
- Shutdown sequencing
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Optional

from PySide6.QtCore import QTimer

from novel_forge.desktop.thread_pools import desktop_thread_pools


def safe_disconnect(signal: Any, slot: Optional[Callable[..., Any]] = None) -> None:
    """Safely disconnect a Qt signal, ignoring errors if already disconnected.

    Wraps the common try/except pattern used across page shutdown methods:

        try:
            self.some_signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass

    Args:
        signal: The Qt signal to disconnect from.
        slot: Optional slot to disconnect. If None, disconnects all connections.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*Failed to disconnect .* from signal .*",
            category=RuntimeWarning,
        )
        try:
            if slot is not None:
                signal.disconnect(slot)
            else:
                signal.disconnect()
        except (RuntimeError, TypeError):
            pass


def wait_for_thread_pool(timeout_ms: int = 3000) -> None:
    """Wait briefly for auxiliary desktop page workers.

    Args:
        timeout_ms: Maximum time to wait in milliseconds (default: 3000).
    """
    desktop_thread_pools().aux_pool.waitForDone(timeout_ms)


def stop_timers(*timers: QTimer) -> None:
    """Stop one or more QTimer instances safely.

    Args:
        *timers: QTimer instances to stop. None values are ignored.
    """
    for timer in timers:
        if timer is not None:
            timer.stop()
