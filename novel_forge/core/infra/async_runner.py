"""Process-wide async coroutine runner.

Centralises the decision of which asyncio event loop to use when sync code
(Qt slots, QThreadPool workers, CLI entry points) needs to run an async
coroutine.  In the desktop app, the long-lived ``_AsyncServiceLoop`` thread
is the canonical owner of aiosqlite / SQLAlchemy async engines, so running
coroutines there keeps connections bound to a loop that outlives the call.
When the desktop loop is not registered (CLI, unit tests, background
threads started before the desktop loop is up), it falls back to a
private ``asyncio.run()`` loop.

This avoids the ``RuntimeError: Event loop is closed`` failure mode where
aiosqlite's connection worker thread tries to deliver a result to a
short-lived ``asyncio.run()`` loop that has already been closed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Protocol, TypeVar, cast

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class _RunnerLike(Protocol):
    def run_coro(self, coro: Coroutine[Any, Any, Any], *, timeout: float = 30.0) -> Any: ...


_active_runner: _RunnerLike | None = None


def register_async_runner(runner: _RunnerLike | None) -> None:
    """Install the process-wide async runner.

    Pass ``None`` to clear the registration.  Called once during desktop
    startup and again during shutdown so other modules can locate the
    long-lived event loop.
    """
    global _active_runner
    _active_runner = runner


def get_async_runner() -> _RunnerLike | None:
    """Return the currently-registered async runner, or None if not set."""
    return _active_runner


def run_async_coro(
    coro: Coroutine[Any, Any, _T],
    *,
    timeout: float = 30.0,
) -> _T:
    """Run an async coroutine on the registered async runner.

    Falls back to ``asyncio.run(coro)`` when no runner is registered.  Use
    this instead of calling ``asyncio.run()`` directly from sync code so
    aiosqlite / SQLAlchemy async engines remain bound to a long-lived
    event loop.
    """
    runner = _active_runner
    if runner is not None:
        # Once handed to the long-lived runner, the coroutine may already be
        # scheduled on its event loop. Retrying it with asyncio.run() after a
        # timeout or provider exception can execute cleanup twice and block the
        # Qt main thread during application exit. Let the caller handle the
        # original error; fallback is only safe when no runner was registered.
        return cast(_T, runner.run_coro(coro, timeout=timeout))
    return asyncio.run(coro)


__all__ = [
    "register_async_runner",
    "get_async_runner",
    "run_async_coro",
]
