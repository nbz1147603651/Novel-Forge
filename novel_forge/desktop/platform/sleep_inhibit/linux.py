"""Linux sleep inhibit via systemd-inhibit --what=idle."""

from __future__ import annotations

import subprocess


class SleepInhibitBackend:
    def __init__(self):
        self._proc: subprocess.Popen | None = None

    def acquire(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                ["systemd-inhibit", "--what=idle", "--why=Novel Forge agent", "sleep", "infinity"],
                stdin=subprocess.PIPE,
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
