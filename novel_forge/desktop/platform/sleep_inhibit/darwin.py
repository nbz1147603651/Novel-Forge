"""macOS sleep inhibit via caffeinate -i -w <pid>."""

from __future__ import annotations

import os
import subprocess
from typing import Optional


class SleepInhibitBackend:
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None

    def acquire(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def release(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
