"""Tests for finalize_volume_memory wiring of index_volume_audit, save_summaries, and evict_volume_summaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.memory.integration import MemoryContext

if TYPE_CHECKING:
    from novel_forge.core.schemas.volume import VolumeAuditReport


@pytest.fixture
def context() -> MemoryContext:
    return MemoryContext()


@pytest.fixture
def audit_report() -> "VolumeAuditReport":
    from novel_forge.core.schemas.volume import VolumeAuditReport

    return VolumeAuditReport(
        volume_number=2,
        volume_title="第二卷",
        chapter_range="11-20",
        volume_summary="这是一个测试摘要",
        consistency_score=8.5,
        consistency_issues=[],
        carry_over_characters=["角色A"],
        carry_over_items=["物品A"],
        carry_over_world_fact_keys=["世界观A"],
        carry_over_foreshadowing_ids=["伏笔A"],
        next_volume_focus="下一卷重点",
    )


class TestIndexVolumeAuditWiring:
    async def test_index_volume_audit_called(
        self,
        context: MemoryContext,
        audit_report: "VolumeAuditReport",
    ) -> None:
        context._episodic_memory = MagicMock()
        context._episodic_memory.index_volume_audit = AsyncMock(return_value="sig123")
        context._summary_service = MagicMock()

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=audit_report,
        )

        context._episodic_memory.index_volume_audit.assert_called_once_with(audit_report)
        assert stats.get("volume_audit_indexed") is True

    async def test_index_volume_audit_skipped_when_no_episodic_memory(
        self,
        context: MemoryContext,
        audit_report: "VolumeAuditReport",
    ) -> None:
        context._episodic_memory = None
        context._summary_service = MagicMock()

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=audit_report,
        )

        assert "volume_audit_indexed" not in stats

    async def test_index_volume_audit_skipped_when_report_none(
        self,
        context: MemoryContext,
    ) -> None:
        context._episodic_memory = MagicMock()
        context._episodic_memory.index_volume_audit = AsyncMock()

        await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=None,
        )

        context._episodic_memory.index_volume_audit.assert_not_called()


class TestSaveSummariesWiring:
    async def test_volume_summary_uses_current_signature_and_records_strategy(
        self,
        context: MemoryContext,
        audit_report: "VolumeAuditReport",
    ) -> None:
        context._summary_service = MagicMock()
        context._summary_service.get_summary = MagicMock(
            side_effect=lambda granularity, chapter: (
                f"第{chapter}章摘要" if granularity == "chapter" else None
            )
        )
        context._summary_service.generate_volume_summary = AsyncMock(
            return_value=SimpleNamespace(
                metadata={
                    "summary_strategy": "hierarchical_map_reduce",
                    "summary_chunk_count": 3,
                }
            )
        )
        context._summary_service.save_summaries = AsyncMock(return_value=True)

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=12,
            audit_report=audit_report,
        )

        call = context._summary_service.generate_volume_summary.await_args.kwargs
        assert call["chapter_summaries"] == {11: "第11章摘要", 12: "第12章摘要"}
        assert call["canon_state"] is None
        assert call["audit_report"] is audit_report
        assert "story_outline" not in call
        assert stats["volume_summary"] is True
        assert stats["volume_summary_strategy"] == "hierarchical_map_reduce"
        assert stats["volume_summary_chunk_count"] == 3

    async def test_save_summaries_called(
        self,
        context: MemoryContext,
    ) -> None:
        context._summary_service = MagicMock()
        context._summary_service.save_summaries = AsyncMock(return_value=True)

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=None,
        )

        context._summary_service.save_summaries.assert_called_once()
        assert stats.get("summaries_saved") is True

    async def test_save_summaries_skipped_when_no_service(
        self,
        context: MemoryContext,
    ) -> None:
        context._summary_service = None

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=None,
        )

        assert "summaries_saved" not in stats


class TestEvictVolumeSummariesWiring:
    async def test_evict_volume_summaries_called(
        self,
        context: MemoryContext,
    ) -> None:
        context._summary_service = MagicMock()
        context._summary_service.evict_volume_summaries = MagicMock(return_value=5)
        context._summary_service.save_summaries = AsyncMock()

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=None,
        )

        context._summary_service.evict_volume_summaries.assert_called_once_with(
            volume_number=2,
            keep_recent_volumes=2,
            max_chapter_to_keep=None,
        )
        assert stats.get("summaries_evicted") == 5

    async def test_evict_volume_summaries_uses_computed_threshold(
        self,
        context: MemoryContext,
    ) -> None:
        context._summary_service = MagicMock()
        context._summary_service.evict_volume_summaries = MagicMock(return_value=10)
        context._summary_service.save_summaries = AsyncMock()

        stats = await context.finalize_volume_memory(
            volume_number=3,
            start_chapter=21,
            end_chapter=30,
            audit_report=None,
        )

        context._summary_service.evict_volume_summaries.assert_called_once_with(
            volume_number=3,
            keep_recent_volumes=2,
            max_chapter_to_keep=11,
        )
        assert stats.get("eviction_threshold") == 11
        assert stats.get("summaries_evicted") == 10

    async def test_evict_skips_when_no_service(
        self,
        context: MemoryContext,
    ) -> None:
        context._summary_service = None

        stats = await context.finalize_volume_memory(
            volume_number=2,
            start_chapter=11,
            end_chapter=20,
            audit_report=None,
        )

        assert "summaries_evicted" not in stats


class TestVolumeMemoryPruningWiring:
    async def test_prune_by_volume_called_with_threshold(
        self,
        context: MemoryContext,
    ) -> None:
        context._episodic_memory = MagicMock()
        context._episodic_memory.on_volume_end = MagicMock(return_value={"critique_pruning": {}})
        context._episodic_memory.prune_by_volume = MagicMock(
            return_value={
                "removed_entries": 2,
                "removed_outlines": 0,
                "removed_critiques": 0,
                "remaining_entries": 5,
                "remaining_outlines": 0,
                "remaining_critiques": 1,
            }
        )

        stats = await context.finalize_volume_memory(
            volume_number=3,
            start_chapter=21,
            end_chapter=30,
            audit_report=None,
        )

        context._episodic_memory.prune_by_volume.assert_called_once_with(
            volume_number=3,
            keep_recent_volumes=2,
            max_chapter_to_keep=11,
            prune_critiques=False,
        )
        assert stats.get("memory_pruning", {}).get("removed_entries") == 2
