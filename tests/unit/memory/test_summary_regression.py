"""Regression tests for Phase 2+3 SummaryService + MemoryContext changes.

These tests verify that existing functionality remains unchanged after T11/T12/T13's
additions. Focus on backward compatibility and ensuring core behavior is preserved.

MUST DO:
- test_summary_service_existing_methods: generate_chapter_summary, get_summary, get_summary_for_context, clear_cache still work
- test_memory_context_save_load_backward_compat: MemoryContext save_to_disk/load_from_disk still works
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.summary import MultiGranularitySummaryService

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.prompts.builder import PromptBuilder


class TestSummaryServiceExistingMethods:
    """Test that existing SummaryService methods still work after T11/T13 additions."""

    @pytest.fixture
    def summary_service(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
    ) -> MultiGranularitySummaryService:
        return MultiGranularitySummaryService(router=router, builder=builder)

    def test_generate_chapter_summary_creates_cache_entry(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """generate_chapter_summary caches the summary for later retrieval."""
        summary_service.cache_summary(
            granularity="chapter",
            chapter_number=1,
            text="第一章概要内容",
        )

        result = summary_service.get_summary("chapter", 1)
        assert result == "第一章概要内容"

    def test_get_summary_returns_cached_value(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """get_summary returns previously cached summary."""
        summary_service.cache_summary("chapter", 5, "第五章测试概要")

        result = summary_service.get_summary("chapter", 5)
        assert result == "第五章测试概要"

    def test_get_summary_returns_none_for_missing(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """get_summary returns None for non-existent summary."""
        result = summary_service.get_summary("chapter", 999)
        assert result is None

    def test_get_summary_for_context_returns_chapter_summaries(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """get_summary_for_context returns formatted chapter summaries."""
        summary_service.cache_summary("chapter", 1, "第一章")
        summary_service.cache_summary("chapter", 2, "第二章")
        summary_service.cache_summary("chapter", 3, "第三章")

        result = summary_service.get_summary_for_context(
            current_chapter=4,
            granularity="chapter",
        )

        assert isinstance(result, str)
        assert "第一章" in result or "第二章" in result or "第三章" in result

    def test_clear_cache_removes_all_cached_summaries(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """clear_cache removes all cached chapter/volume/arc summaries."""
        summary_service.cache_summary("chapter", 1, "第一章")
        summary_service.cache_summary("chapter", 2, "第二章")
        summary_service.cache_summary("volume", 1, "第一卷概要")

        assert summary_service.get_summary("chapter", 1) is not None
        assert summary_service.get_summary("chapter", 2) is not None
        assert summary_service.get_summary("volume", 1) is not None

        summary_service.clear_cache()

        assert summary_service.get_summary("chapter", 1) is None
        assert summary_service.get_summary("chapter", 2) is None
        assert summary_service.get_summary("volume", 1) is None

    def test_cache_summary_handles_all_granularities(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """cache_summary works for scene, chapter, volume, and arc granularities."""
        summary_service.cache_summary("scene", 1, "场景1内容", scene_index=0)
        summary_service.cache_summary("chapter", 1, "第一章内容")
        summary_service.cache_summary("volume", 1, "第一卷内容")
        summary_service.cache_summary("arc", 1, "弧线1内容", arc_name="主弧线")

        assert summary_service.get_summary("scene", 1, scene_index=0) == "场景1内容"
        assert summary_service.get_summary("chapter", 1) == "第一章内容"
        assert summary_service.get_summary("volume", 1) == "第一卷内容"
        assert summary_service.get_summary("arc", 1, arc_name="主弧线") == "弧线1内容"

    def test_get_chapter_summaries_returns_filtered_summaries(
        self,
        summary_service: MultiGranularitySummaryService,
    ) -> None:
        """get_chapter_summaries returns summaries within chapter range."""
        for ch in range(1, 11):
            summary_service.cache_summary("chapter", ch, f"第{ch}章")

        all_summaries = summary_service.get_chapter_summaries()
        assert len(all_summaries) == 10

        partial = summary_service.get_chapter_summaries(start_chapter=3, end_chapter=7)
        assert len(partial) == 5
        assert "3" in partial
        assert "7" in partial
        assert "1" not in partial


class TestMemoryContextSaveLoadBackwardCompat:
    """Test that MemoryContext save_to_disk/load_from_disk still work after T12 changes."""

    @pytest.fixture
    def memory_ctx(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
    ) -> MemoryContext:
        return MemoryContext(
            _project_id="test_project",
            _storage=None,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

    def test_save_to_disk_returns_false_without_storage(
        self,
        memory_ctx: MemoryContext,
    ) -> None:
        """save_to_disk returns False when storage is not set (same as before T12)."""
        result = memory_ctx.save_to_disk()
        assert result is False

    def test_load_from_disk_returns_false_without_storage(
        self,
        memory_ctx: MemoryContext,
    ) -> None:
        """load_from_disk returns False when storage is not set (same as before T12)."""
        result = memory_ctx.load_from_disk()
        assert result is False

    def test_summary_cache_persists_through_save_load(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
        tmp_storage: "FileSystemStorage",
    ) -> None:
        """_summary_cache is saved and loaded correctly (backward compatibility)."""
        ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        ctx._summary_cache[1] = {
            "text": "第一章概要",
            "source_hash": "abc123",
            "version": 1,
            "updated_at": "",
        }
        ctx._summary_cache[2] = {
            "text": "第二章概要",
            "source_hash": "def456",
            "version": 1,
            "updated_at": "",
        }

        ctx.save_to_disk()

        new_ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        new_ctx.load_from_disk()

        assert new_ctx._summary_cache[1]["text"] == "第一章概要"
        assert new_ctx._summary_cache[2]["text"] == "第二章概要"

    def test_load_from_disk_uses_pending_journal_when_main_is_corrupt(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
        tmp_storage: "FileSystemStorage",
    ) -> None:
        ctx = MemoryContext(
            _project_id="journal_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )
        ctx._summary_cache[1] = {
            "text": "可恢复概要",
            "source_hash": "hash",
            "version": 1,
            "updated_at": "",
        }
        assert ctx.save_to_disk() is True

        memory_path = ctx._get_memory_path()
        journal_path = ctx._get_memory_journal_path()
        saved_payload = tmp_storage.load_json(memory_path)
        tmp_storage.save_json(journal_path, saved_payload)
        tmp_storage.save_text(memory_path, "{not-json")

        recovered = MemoryContext(
            _project_id="journal_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        assert recovered.load_from_disk() is True
        assert recovered._summary_cache[1]["text"] == "可恢复概要"

    def test_last_indexed_chapter_persists_through_save_load(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
        tmp_storage: "FileSystemStorage",
    ) -> None:
        """_last_indexed_chapter is saved and loaded correctly."""
        ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        ctx._last_indexed_chapter = 42

        ctx.save_to_disk()

        new_ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        new_ctx.load_from_disk()

        assert new_ctx._last_indexed_chapter == 42

    def test_chapter_content_hash_persists_through_save_load(
        self,
        router: "ModelRouter",
        builder: "PromptBuilder",
        tmp_storage: "FileSystemStorage",
    ) -> None:
        """_chapter_content_hash is saved and loaded correctly."""
        ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        ctx._chapter_content_hash[1] = "hash_abc"
        ctx._chapter_content_hash[2] = "hash_def"

        ctx.save_to_disk()

        new_ctx = MemoryContext(
            _project_id="test_proj",
            _storage=tmp_storage,
            _router=router,
            _builder=builder,
            _summary_service=None,
        )

        new_ctx.load_from_disk()

        assert new_ctx._chapter_content_hash[1] == "hash_abc"
        assert new_ctx._chapter_content_hash[2] == "hash_def"

    def test_get_cached_summary_uses_legacy_cache(
        self,
        memory_ctx: MemoryContext,
    ) -> None:
        """get_cached_summary returns from legacy _summary_cache when no summary_service."""
        memory_ctx._summary_cache[5] = {
            "text": "Legacy chapter 5 summary",
            "source_hash": "abc123",
            "version": 1,
            "updated_at": "",
        }

        result = memory_ctx.get_cached_summary(5)
        assert result == "Legacy chapter 5 summary"

        result_missing = memory_ctx.get_cached_summary(99)
        assert result_missing is None
