"""Async adapters for workspace orchestration helpers."""

from __future__ import annotations

import asyncio
from typing import Any


class SyncToAsyncContext:
    """Expose a synchronous context manager as an async context manager."""

    def __init__(self, sync_ctx: Any) -> None:
        self._sync_ctx = sync_ctx

    async def __aenter__(self) -> None:
        await asyncio.to_thread(self._sync_ctx.__enter__)

    async def __aexit__(self, *args: Any) -> None:
        await asyncio.to_thread(self._sync_ctx.__exit__, *args)


def sync_to_async_context(sync_ctx: Any) -> SyncToAsyncContext:
    return SyncToAsyncContext(sync_ctx)

