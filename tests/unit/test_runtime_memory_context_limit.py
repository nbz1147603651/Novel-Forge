"""Tests for RuntimeServices memory context retention limits."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.runtime import RuntimeServices


@pytest.mark.asyncio
async def test_runtime_memory_contexts_are_pruned_lru(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    runtime_settings.runtime_memory_context_max_entries = 2
    runtime = RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )

    class _FakeMemoryContext:
        @classmethod
        def create_from_settings(cls, **kwargs: object) -> object:
            return SimpleNamespace(project_id=kwargs["project_id"])

    import novel_forge.memory as memory_module

    monkeypatch.setattr(memory_module, "MemoryContext", _FakeMemoryContext)

    await runtime.get_memory_context("p1")
    await runtime.get_memory_context("p2")
    await runtime.get_memory_context("p3")
    assert list(runtime.memory_contexts.keys()) == ["p2", "p3"]

    # Access p2 to make it most recently used, then add p4 and evict p3.
    await runtime.get_memory_context("p2")
    await runtime.get_memory_context("p4")
    assert list(runtime.memory_contexts.keys()) == ["p2", "p4"]


@pytest.mark.asyncio
async def test_acquired_memory_context_is_not_evicted_until_lease_released(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    runtime_settings.runtime_memory_context_max_entries = 1
    closed: list[str] = []

    class _FakeMemoryContext:
        def __init__(self, project_id: str) -> None:
            self.project_id = project_id

        @classmethod
        def create_from_settings(cls, **kwargs: object) -> object:
            return cls(str(kwargs["project_id"]))

        async def shutdown(self) -> None:
            closed.append(self.project_id)

    import novel_forge.memory as memory_module

    monkeypatch.setattr(memory_module, "MemoryContext", _FakeMemoryContext)

    runtime = RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )

    p1 = await runtime.acquire_memory_context("p1")
    p2 = await runtime.get_memory_context("p2")

    assert p1 is not None and p1.project_id == "p1"
    assert p2 is not None and p2.project_id == "p2"
    assert "p1" in runtime.memory_contexts
    assert "p2" in runtime.memory_contexts
    assert closed == []

    runtime.release_memory_context_lease("p1")
    await asyncio.sleep(0)

    assert "p1" not in runtime.memory_contexts
    assert list(runtime.memory_contexts.keys()) == ["p2"]
    assert closed == ["p1"]


@pytest.mark.asyncio
async def test_runtime_shutdown_closes_memory_contexts_and_router_adapters(
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    class _ClosableMockAdapter(MockAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        async def shutdown(self) -> None:  # type: ignore[override]
            self.closed = True

    class _ClosableMemoryContext:
        def __init__(self) -> None:
            self.closed = False

        async def shutdown(self) -> None:
            self.closed = True

    adapter = _ClosableMockAdapter()
    router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
    runtime = RuntimeServices(
        settings=runtime_settings,
        router=router,
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )
    context = _ClosableMemoryContext()
    runtime.memory_contexts["demo"] = context  # type: ignore[assignment]

    await runtime.shutdown()

    assert context.closed is True
    assert adapter.closed is True
    assert runtime.memory_contexts == {}


@pytest.mark.asyncio
async def test_release_memory_context_closes_detached_context(
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    class _ClosableMemoryContext:
        def __init__(self) -> None:
            self.closed = False

        async def shutdown(self) -> None:
            self.closed = True

    runtime = RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )
    context = _ClosableMemoryContext()
    runtime.memory_contexts["demo"] = context  # type: ignore[assignment]

    runtime.release_memory_context("demo")
    await asyncio.sleep(0)

    assert context.closed is True
    assert "demo" not in runtime.memory_contexts


async def test_runtime_shutdown_drains_detached_memory_cleanup(
    runtime_settings: object, tmp_path: Path
) -> None:
    closed: list[bool] = []

    async def close() -> None:
        await asyncio.sleep(0)
        closed.append(True)

    runtime = RuntimeServices(
        settings=runtime_settings,
        router=ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock"),
        builder=PromptBuilder(),
        storage=FileSystemStorage(tmp_path),
    )
    runtime.memory_contexts["demo"] = SimpleNamespace(shutdown=close)
    runtime.release_memory_context("demo")
    assert not closed
    await runtime.shutdown()
    assert closed == [True]
    assert not runtime._eviction_tasks


@pytest.mark.asyncio
async def test_memory_context_shutdown_persists_before_close(
    monkeypatch: pytest.MonkeyPatch,
    runtime_settings: object,
    tmp_path: Path,
) -> None:
    from novel_forge.memory.integration import MemoryContext

    context = MemoryContext(
        settings=runtime_settings,
        _project_id="demo",
        _storage=FileSystemStorage(tmp_path),
    )
    saved = False

    def _save_to_disk() -> bool:
        nonlocal saved
        saved = True
        return True

    monkeypatch.setattr(context, "save_to_disk", _save_to_disk)

    await context.shutdown()

    assert saved is True
