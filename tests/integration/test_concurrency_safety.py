"""Integration tests for concurrency safety in RuntimeServices.get_memory_context.

Tests verify:
- get_memory_context returns MemoryContext instance, not coroutine
- Concurrent get_memory_context calls for same project work correctly
- Concurrent get_memory_context calls for different projects work correctly
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.integration import MemoryContext
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.runtime import RuntimeServices


@pytest.fixture
def mock_router() -> ModelRouter:
    return ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
    )


@pytest.fixture
def prompt_builder() -> PromptBuilder:
    return PromptBuilder()


@pytest.fixture
def tmp_storage(tmp_path: Path) -> FileSystemStorage:
    return FileSystemStorage(tmp_path)


@pytest.fixture
def runtime_settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def runtime(
    mock_router: ModelRouter,
    prompt_builder: PromptBuilder,
    tmp_storage: FileSystemStorage,
    runtime_settings: Settings,
) -> RuntimeServices:
    return RuntimeServices(
        settings=runtime_settings,
        router=mock_router,
        builder=prompt_builder,
        storage=tmp_storage,
    )


class TestGetMemoryContextConcurrency:
    """Concurrency safety tests for RuntimeServices.get_memory_context."""

    @pytest.mark.asyncio
    async def test_get_memory_context_returns_instance_not_coroutine(
        self,
        runtime: RuntimeServices,
    ) -> None:
        result = runtime.get_memory_context(project_id="test_project_basic")

        assert asyncio.iscoroutine(result), (
            "get_memory_context must return a coroutine - did you forget to await?"
        )

        memory_ctx = await result

        assert memory_ctx is not None
        assert isinstance(memory_ctx, MemoryContext)
        assert memory_ctx.project_id == "test_project_basic"
        assert not asyncio.iscoroutine(memory_ctx)

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_get_memory_context_async_loads_summary_service(
        self,
        runtime: RuntimeServices,
        tmp_storage: FileSystemStorage,
    ) -> None:
        project_id = "test_project_async_summary_reload"
        memory_dir = tmp_storage.root / project_id / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        tmp_storage.save_json(
            memory_dir / "project_memory.json",
            {
                "last_indexed_chapter": 2,
                "summary_cache": {},
                "chapter_content_hash": {},
            },
        )
        tmp_storage.save_json(
            memory_dir / "summaries.json",
            {
                "scene_summaries": {},
                "chapter_summaries": {"1": "第一章概要：主角觉醒。"},
                "volume_summaries": {},
                "arc_summaries": {},
            },
        )

        memory_ctx = await runtime.get_memory_context(project_id)

        assert isinstance(memory_ctx, MemoryContext)
        assert memory_ctx.summary_service is not None
        assert memory_ctx.get_cached_summary(1) == "第一章概要：主角觉醒。"

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_get_memory_context_same_project(
        self,
        runtime: RuntimeServices,
    ) -> None:
        project_id = "test_project_concurrent_same"

        results = await asyncio.gather(
            runtime.get_memory_context(project_id),
            runtime.get_memory_context(project_id),
            runtime.get_memory_context(project_id),
        )

        for result in results:
            assert isinstance(result, MemoryContext), (
                f"Expected MemoryContext, got {type(result)}"
            )
            assert not asyncio.iscoroutine(result)

        assert results[0] is results[1] is results[2], (
            "Concurrent calls for same project should return same cached context"
        )
        assert results[0].project_id == project_id

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_get_memory_context_different_projects(
        self,
        runtime: RuntimeServices,
    ) -> None:
        project_a = "test_project_concurrent_a"
        project_b = "test_project_concurrent_b"
        project_c = "test_project_concurrent_c"

        results = await asyncio.gather(
            runtime.get_memory_context(project_a),
            runtime.get_memory_context(project_b),
            runtime.get_memory_context(project_c),
        )

        context_a, context_b, context_c = results

        for ctx in [context_a, context_b, context_c]:
            assert isinstance(ctx, MemoryContext)
            assert not asyncio.iscoroutine(ctx)

        assert context_a is not context_b
        assert context_b is not context_c
        assert context_a is not context_c

        assert context_a.project_id == project_a
        assert context_b.project_id == project_b
        assert context_c.project_id == project_c

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_mixed_same_and_different_projects(
        self,
        runtime: RuntimeServices,
    ) -> None:
        project_x = "test_project_mixed_x"
        project_y = "test_project_mixed_y"

        results = await asyncio.gather(
            runtime.get_memory_context(project_x),
            runtime.get_memory_context(project_y),
            runtime.get_memory_context(project_x),
        )

        context_x1, context_y, context_x2 = results

        assert context_x1 is context_x2
        assert context_x1.project_id == project_x

        assert context_y is not context_x1
        assert context_y.project_id == project_y

        await runtime.shutdown()
