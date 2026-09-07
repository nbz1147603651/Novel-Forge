"""Shared shutdown helpers for desktop UI modules."""

from __future__ import annotations

import warnings
from typing import Any, Callable


def safe_disconnect(signal: Any, slot: Callable[..., Any] | None = None) -> None:
    """Disconnect a Qt signal, ignoring if already disconnected."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*Failed to disconnect .* from signal .*",
            category=RuntimeWarning,
        )
        try:
            if slot is None:
                signal.disconnect()
            else:
                signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass


def safe_stop_timer(timer: Any) -> None:
    """Stop a QTimer, handling None and already-deleted timers."""
    if timer is None:
        return
    try:
        if timer.isActive():
            timer.stop()
    except RuntimeError:
        pass
