"""No-op sleep inhibit for unsupported platforms (e.g., BSD)."""

from __future__ import annotations


class NullBackend:
    held: bool = False

    def acquire(self) -> bool:
        self.held = True
        return True

    def release(self) -> None:
        self.held = False
