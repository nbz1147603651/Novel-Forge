"""Tests for API lifespan startup/shutdown resource handling."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novel_forge.api.app import create_app, lifespan
from novel_forge.core.infra.async_task_processor import async_task_processor_initialized


def test_api_lifespan_does_not_create_cache_files_when_unused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    app = create_app()

    async def _run() -> None:
        async with lifespan(app):
            pass

    asyncio.run(_run())

    assert (tmp_path / "data" / "cache").exists() is False


def test_api_lifespan_starts_and_stops_async_task_processor() -> None:
    app = create_app()

    async def _run() -> tuple[bool, bool]:
        before = async_task_processor_initialized()
        async with lifespan(app):
            inside = async_task_processor_initialized()
        after = async_task_processor_initialized()
        return before, inside and (after is False)

    before, inside_and_stopped = asyncio.run(_run())
    assert before is False
    assert inside_and_stopped is True
