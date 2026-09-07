"""Prevent the OS from sleeping while long-running tasks are active.

Platform support:
- macOS: IOKit power assertions via ctypes
- Windows: SetThreadExecutionState via ctypes
- Linux: org.freedesktop.login1 Inhibit via D-Bus (best-effort)

Usage:
    inhibitor = SleepInhibitor()
    inhibitor.acquire()   # prevent sleep
    inhibitor.release()   # allow sleep again
"""

from __future__ import annotations

import atexit
import logging
import os
import platform
import subprocess
import weakref

logger = logging.getLogger(__name__)

_REASON = "NIMO — AI 创作任务运行中"


def _atexit_release(ref: weakref.ref) -> None:
    """atexit handler that weakly references a SleepInhibitor.

    Uses weakref to avoid resurrection (strong reference via bound method
    would prevent GC of the SleepInhibitor instance).
    """
    obj = ref()
    if obj is not None:
        try:
            obj.release()
        except Exception:
            logger.debug("atexit release for sleep inhibitor failed", exc_info=True)


class SleepInhibitor:
    """Cross-platform sleep inhibitor with acquire/release semantics."""

    def __init__(self) -> None:
        self._held = False
        self._atexit_registered = False
        self._backend = _create_backend()

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self) -> None:
        if self._held:
            return
        try:
            self._backend.acquire()
            self._held = True
            logger.info("Sleep inhibitor acquired")
        except Exception:
            logger.warning("Failed to acquire sleep inhibitor", exc_info=True)
            return

        # Register atexit fallback (only once, after successful acquire)
        if not self._atexit_registered:
            atexit.register(_atexit_release, weakref.ref(self))
            self._atexit_registered = True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._backend.release()
            logger.info("Sleep inhibitor released")
        except Exception:
            logger.warning("Failed to release sleep inhibitor", exc_info=True)
        finally:
            self._held = False


# ---------------------------------------------------------------------------
# Platform backends
# ---------------------------------------------------------------------------

class _NullBackend:
    """Fallback when no OS-level mechanism is available."""

    def acquire(self) -> None:
        pass

    def release(self) -> None:
        pass


class _MacOSBackend:
    """macOS: spawn ``caffeinate`` scoped to the current process lifetime."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None

    def acquire(self) -> None:
        import subprocess

        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def release(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None


class _WindowsBackend:
    """Windows SetThreadExecutionState."""

    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    _ES_FLAGS = 0x80000001

    def acquire(self) -> None:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(self._ES_FLAGS)  # type: ignore[attr-defined]

    def release(self) -> None:
        import ctypes
        # ES_CONTINUOUS alone resets to normal
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # type: ignore[attr-defined]


class _LinuxBackend:
    """Linux systemd-logind Inhibit via D-Bus (best-effort)."""

    def __init__(self) -> None:
        self._fd: int | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    def acquire(self) -> None:
        import subprocess

        proc = subprocess.Popen(
            [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=NIMO",
                f"--reason={_REASON}",
                "--mode=block",
                "cat",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Keep the process alive; closing stdin will end "cat" → release inhibit
        self._fd = proc.stdin.fileno() if proc.stdin else None
        self._proc = proc

    def release(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass
        self._proc = None
        self._fd = None


def _create_backend() -> _NullBackend | _MacOSBackend | _WindowsBackend | _LinuxBackend:
    system = platform.system()
    if system == "Darwin":
        return _MacOSBackend()
    if system == "Windows":
        return _WindowsBackend()
    if system == "Linux":
        try:
            import subprocess
            subprocess.run(
                ["systemd-inhibit", "--version"],
                capture_output=True,
                timeout=3,
            )
            return _LinuxBackend()
        except Exception:
            pass
    logger.debug("No sleep inhibitor backend for %s — using null backend", system)
    return _NullBackend()
