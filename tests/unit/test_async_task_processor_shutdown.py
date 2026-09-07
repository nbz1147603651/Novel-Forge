"""Shutdown behavior tests for AsyncTaskProcessor."""

from __future__ import annotations

import asyncio

from novel_forge.core.infra.async_task_processor import AsyncTaskProcessor


class _DummyThreadPool:
    def __init__(self) -> None:
        self.wait_args: list[bool] = []

    def shutdown(self, wait: bool = True) -> None:
        self.wait_args.append(wait)


def test_async_task_processor_stop_uses_non_blocking_thread_pool_shutdown() -> None:
    async def _run() -> None:
        processor = AsyncTaskProcessor(max_workers=1)
        dummy_pool = _DummyThreadPool()
        processor._thread_pool = dummy_pool  # type: ignore[assignment]

        await processor.start()
        await processor.stop()

        assert dummy_pool.wait_args == [False]

    asyncio.run(_run())
