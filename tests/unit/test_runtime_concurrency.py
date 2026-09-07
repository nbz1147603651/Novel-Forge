"""Concurrency tests for RuntimeServices memory_contexts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.runtime import RuntimeServices


class _FakeMemoryContext:
    """Minimal fake for testing without real MemoryContext."""

    project_id: str

    @classmethod
    def create_from_settings(cls, **kwargs: object) -> "_FakeMemoryContext":
        instance = cls.__new__(cls)
        instance.project_id = str(kwargs.get("project_id", ""))
        return instance


@pytest.mark.asyncio
async def test_memory_contexts_concurrent_access(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    """20 tasks × 50 get_memory_context calls should not raise RuntimeError."""
    import novel_forge.memory as memory_module

    monkeypatch.setattr(memory_module, "MemoryContext", _FakeMemoryContext)

    router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")
    storage = FileSystemStorage(tmp_path)
    builder = PromptBuilder()
    runtime = RuntimeServices(
        settings=runtime_settings,
        router=router,
        builder=builder,
        storage=storage,
    )

    async def worker(task_id: int) -> None:
        for i in range(50):
            await runtime.get_memory_context(f"project_{task_id}_{i % 5}")

    await asyncio.gather(*[worker(i) for i in range(20)])
    assert len(runtime.memory_contexts) <= runtime_settings.runtime_memory_context_max_entries


@pytest.mark.asyncio
async def test_memory_contexts_lru_eviction(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    """With max_entries=4, 10 accesses should evict to 4."""
    runtime_settings.runtime_memory_context_max_entries = 4
    import novel_forge.memory as memory_module

    monkeypatch.setattr(memory_module, "MemoryContext", _FakeMemoryContext)

    router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")
    storage = FileSystemStorage(tmp_path)
    builder = PromptBuilder()
    runtime = RuntimeServices(
        settings=runtime_settings,
        router=router,
        builder=builder,
        storage=storage,
    )

    for i in range(10):
        await runtime.get_memory_context(f"project_{i}")

    assert len(runtime.memory_contexts) <= 4