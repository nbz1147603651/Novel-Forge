from __future__ import annotations

import asyncio
from typing import Any

import pytest

from novel_forge.core.infra import async_runner


def test_registered_runner_timeout_does_not_retry_coroutine_on_calling_thread() -> None:
    executed: list[str] = []

    async def operation() -> None:
        executed.append("ran")

    class TimeoutRunner:
        def run_coro(self, coro: Any, *, timeout: float = 30.0) -> None:
            _ = coro, timeout
            raise TimeoutError("slow cleanup")

    coro = operation()
    async_runner.register_async_runner(TimeoutRunner())
    try:
        with pytest.raises(TimeoutError, match="slow cleanup"):
            async_runner.run_async_coro(coro, timeout=0.01)
    finally:
        coro.close()
        async_runner.register_async_runner(None)

    assert executed == []


def test_async_runner_falls_back_only_when_no_runner_is_registered() -> None:
    async_runner.register_async_runner(None)

    async def operation() -> int:
        await asyncio.sleep(0)
        return 7

    assert async_runner.run_async_coro(operation()) == 7


def test_desktop_workspace_shutdown_uses_short_close_event_budget() -> None:
    from novel_forge.desktop.workspace import DesktopWorkspaceService

    timeouts: list[float] = []

    class Runtime:
        async def shutdown(self) -> None:
            return None

    class RecordingRunner:
        def run_coro(self, coro: Any, *, timeout: float = 30.0) -> None:
            timeouts.append(timeout)
            coro.close()
            raise TimeoutError

    service = DesktopWorkspaceService.__new__(DesktopWorkspaceService)
    service.runtime = Runtime()
    async_runner.register_async_runner(RecordingRunner())
    try:
        service.shutdown()
    finally:
        async_runner.register_async_runner(None)

    assert timeouts == [0.2]
