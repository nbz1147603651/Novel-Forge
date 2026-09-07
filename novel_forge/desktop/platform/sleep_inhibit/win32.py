"""Windows sleep inhibit via SetThreadExecutionState."""

from __future__ import annotations

import ctypes

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class SleepInhibitBackend:
    def __init__(self):
        self._prev_state = 0

    def acquire(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            self._prev_state = kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            )
            return True
        except Exception:
            return False

    def release(self) -> None:
        if not self._prev_state:
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(self._prev_state)
            self._prev_state = 0
        except Exception:
            pass
