"""Sleep inhibit backend dispatcher.

Returns a SleepInhibitBackend instance appropriate for the current
platform. Each platform module wraps the appropriate syscall:
- macOS: caffeinate -i -w <pid>
- Linux: systemd-inhibit --what=idle
- Windows: SetThreadExecutionState

The backend exposes .acquire() / .release(); SleepInhibitor (at
desktop/sleep_inhibitor.py) wraps the dispatch.
"""

import sys
from typing import Any


def create_backend() -> Any:
    if sys.platform == "darwin":
        from .darwin import SleepInhibitBackend
        return SleepInhibitBackend()
    if sys.platform == "win32":
        from .win32 import SleepInhibitBackend
        return SleepInhibitBackend()
    if sys.platform.startswith("linux"):
        from .linux import SleepInhibitBackend
        return SleepInhibitBackend()
    from .null_backend import NullBackend
    return NullBackend()


__all__ = ["create_backend"]
