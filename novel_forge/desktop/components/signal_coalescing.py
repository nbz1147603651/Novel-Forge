"""Small, explicit Qt signal coalescing primitives.

``TrailingDebounce`` waits for input to become quiet.  ``LatestValueCoalescer``
opens a fixed coalescing window and flushes only the most recent payload.
They intentionally remain separate because their timing semantics differ.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar, cast

from PySide6.QtCore import QObject, QTimer

PayloadT = TypeVar("PayloadT")
_MISSING = object()


class TrailingDebounce:
    """Invoke a callback once after input has been quiet for an interval."""

    def __init__(self, owner: QObject, interval_ms: int, callback: Callable[[], None]) -> None:
        self.timer = QTimer(owner)
        self.timer.setInterval(interval_ms)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(callback)

    def trigger(self) -> None:
        """Restart the quiet-period countdown."""
        self.timer.start()

    def cancel(self) -> None:
        """Discard a pending callback."""
        self.timer.stop()

    def is_active(self) -> bool:
        """Return whether a callback is currently pending."""
        return self.timer.isActive()


class LatestValueCoalescer(Generic[PayloadT]):
    """Flush the latest submitted value at most once per fixed time window."""

    def __init__(
        self,
        owner: QObject,
        interval_ms: int,
        callback: Callable[[PayloadT], None],
    ) -> None:
        self._callback = callback
        self._pending: PayloadT | object = _MISSING
        self.timer = QTimer(owner)
        self.timer.setInterval(interval_ms)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._flush)

    def submit(self, value: PayloadT) -> None:
        """Retain *value* and open a window only when one is not active."""
        self._pending = value
        if not self.timer.isActive():
            self.timer.start()

    def clear(self) -> None:
        """Discard a pending value and stop the active window."""
        self.timer.stop()
        self._pending = _MISSING

    def is_active(self) -> bool:
        """Return whether a value is waiting to be flushed."""
        return self.timer.isActive()

    def _flush(self) -> None:
        value = self._pending
        self._pending = _MISSING
        if value is not _MISSING:
            self._callback(cast(PayloadT, value))
