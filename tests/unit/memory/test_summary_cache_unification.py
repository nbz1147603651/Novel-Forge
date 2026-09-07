"""Tests for summary cache unification between MemoryContext and SummaryService."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.summary import MultiGranularitySummaryService

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.prompts.builder import PromptBuilder


@pytest.fixture
def summary_service(
    router: "ModelRouter",
    builder: "PromptBuilder",
) -> MultiGranularitySummaryService:
    return MultiGranularitySummaryService(router=router, builder=builder)


@pytest.fixture
def memory_ctx_with_service(
    router: "ModelRouter",
    builder: "PromptBuilder",
    summary_service: MultiGranularitySummaryService,
) -> MemoryContext:
    return MemoryContext(
        _project_id="test_project",
        _storage=None,
        _router=router,
        _builder=builder,
        _summary_service=summary_service,
    )


class TestSingleSourceOfTruth:
    async def test_single_source_of_truth(
        self,
        memory_ctx_with_service: MemoryContext,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        summary_service.cache_summary("chapter", 1, "Chapter 1: The beginning")
        result = memory_ctx_with_service.get_cached_summary(1)
        assert result == "Chapter 1: The beginning"


class TestSaveLoadUsesSummaryService:
    async def test_save_load_uses_summary_service(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
        tmp_storage: "FileSystemStorage",
    ) -> None:
        summary_service = MultiGranularitySummaryService(router=router, builder=builder)
        summary_service.set_storage(tmp_storage, "test_proj")
        summary_service.cache_summary("chapter", 1, "Ch1 summary")
        summary_service.cache_summary("chapter", 2, "Ch2 summary")

        ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=summary_service,
        )

        await summary_service.save_summaries()
        ctx.save_to_disk()

        new_service = MultiGranularitySummaryService(router=router, builder=builder)
        new_service.set_storage(tmp_storage, "test_proj")
        new_ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=new_service,
        )

        await new_service.load_summaries()
        new_ctx.load_from_disk()

        assert new_ctx.get_cached_summary(1) == "Ch1 summary"
        assert new_ctx.get_cached_summary(2) == "Ch2 summary"
        assert new_service.get_summary("chapter", 1) == "Ch1 summary"


class TestBackwardCompatWithLegacyCache:
    async def test_backward_compat_with_legacy_cache(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
    ) -> None:
        ctx = MemoryContext(
            _project_id="legacy_proj",
            _storage=None,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        ctx._summary_cache[5] = {
            "text": "Legacy chapter 5 summary",
            "source_hash": "abc123",
            "version": 1,
            "updated_at": "",
        }

        result = ctx.get_cached_summary(5)
        assert result == "Legacy chapter 5 summary"

        assert ctx.get_cached_summary(99) is None
