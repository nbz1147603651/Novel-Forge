"""End-to-end integration tests for memory system modules.

Validates cross-module interactions across compression, volume audit,
and summary services:

1. Full pipeline compress with episodic enrichment
2. Volume audit with similarity search
3. Summary persistence survives restart
4. Volume eviction after finalize
5. Graceful degradation with no real embeddings

All tests use mock embeddings (use_mock_embeddings=True) — no external API calls.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.memory.compression import AdaptiveCompressionService, CompressionConfig
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.summary import MultiGranularitySummaryService, SummaryConfig
from novel_forge.persistence.filesystem import FileSystemStorage

# ── Shared Helpers ─────────────────────────────────────────────────


def _make_mock_builder() -> MagicMock:
    """Create a mock PromptBuilder that returns a valid ModelRequest."""
    builder = MagicMock()
    builder.build = MagicMock(
        return_value=ModelRequest(
            task_type=TaskType.CONTEXT_COMPRESS,
            messages=[{"role": "user", "content": "test"}],
        )
    )
    return builder


def _make_mock_router(responses: list[str]) -> MagicMock:
    """Create a mock ModelRouter with canned responses."""
    router = MagicMock()
    router.route = AsyncMock(
        side_effect=[ModelResponse(content=r) for r in responses]
    )
    return router


def _make_mock_router_for_compression() -> MagicMock:
    """Router configured for compress + verify responses."""
    return _make_mock_router([
        '{"items": [{"compressed": "这是一段压缩后的文本，保留了关键信息。"}]}',
        '{"quality_score": 0.85}',
    ])


def _make_temp_storage() -> FileSystemStorage:
    """Create a FileSystemStorage backed by a temp directory."""
    tmp = tempfile.mkdtemp()
    return FileSystemStorage(root=Path(tmp))


def _generate_chapter_text(chapter: int, length: int = 2000) -> str:
    """Generate synthetic chapter text for testing."""
    base = f"第{chapter}章：故事继续发展。"
    filler = "林远走在街道上，思考着未来的方向。" * 50
    return (base + filler)[:length]


# ── Test 1: Full Pipeline Compress with Episodic ───────────────────


class TestFullPipelineCompressWithEpisodic:
    """Verify compression service uses episodic memory for fact enrichment."""

    @pytest.mark.asyncio
    async def test_compress_with_episodic_enrichment(self) -> None:
        """Index 5 chapters, compress chapter 6 context, verify quality >= 0.7."""
        episodic = EpisodicMemory(use_mock_embeddings=True)

        for ch in range(1, 6):
            await episodic.index_chapter_outcome(
                chapter_number=ch,
                event_summary=f"第{ch}章：林远发现了新的线索，剧情推进。",
                full_text=_generate_chapter_text(ch),
            )

        original_search = episodic.search_by_semantic
        call_tracker: list[bool] = []

        async def tracked_search(*args, **kwargs):
            call_tracker.append(True)
            return await original_search(*args, **kwargs)

        episodic.search_by_semantic = tracked_search

        router = _make_mock_router_for_compression()
        builder = _make_mock_builder()
        compression = AdaptiveCompressionService(
            router=router,
            builder=builder,
            config=CompressionConfig(min_quality_score=0.7),
            episodic_memory=episodic,
        )

        original_text = _generate_chapter_text(6)
        result = await compression.compress_with_quality_check(
            original_text=original_text,
            target_chars=500,
            task_type=TaskType.CONTEXT_COMPRESS,
            context_facts=["林远是主角"],
            current_chapter=6,
        )

        assert result is not None
        assert result.quality_score >= 0.7
        assert result.compressed_text
        assert len(result.compressed_text) <= 500
        assert len(call_tracker) >= 1


# ── Test 2: Volume Audit with Similarity Search ────────────────────


class TestVolumeAuditWithSimilaritySearch:
    """Verify volume audit indexing and similarity search work together."""

    @pytest.mark.asyncio
    async def test_search_similar_volumes_returns_historical_refs(self) -> None:
        """Index 2 volume audit reports, search, verify results returned."""
        episodic = EpisodicMemory(use_mock_embeddings=True)

        report1 = VolumeAuditReport(
            volume_number=1,
            volume_title="第一卷：觉醒",
            chapter_range="1-10",
            volume_summary="林远觉醒了超能力，开始探索未知世界。",
            consistency_score=8.5,
            consistency_issues=[],
            carry_over_characters=["林远", "苏晴"],
            carry_over_items=["神秘信件"],
            carry_over_world_fact_keys=["超能力体系"],
            carry_over_foreshadowing_ids=["fs_001"],
            next_volume_focus="深入调查超能力来源",
        )

        report2 = VolumeAuditReport(
            volume_number=2,
            volume_title="第二卷：迷雾",
            chapter_range="11-20",
            volume_summary="林远和苏晴一起调查超能力组织的秘密。",
            consistency_score=9.0,
            consistency_issues=[],
            carry_over_characters=["林远", "苏晴", "神秘人"],
            carry_over_items=["神秘信件", "时间裂缝"],
            carry_over_world_fact_keys=["超能力体系", "组织背景"],
            carry_over_foreshadowing_ids=["fs_001", "fs_002"],
            next_volume_focus="揭开组织真相",
        )

        await episodic.index_volume_audit(report1)
        await episodic.index_volume_audit(report2)

        results = await episodic.search_similar_volumes(
            query_text="林远调查超能力组织",
            top_k=3,
            min_relevance=-1.0,
        )

        assert len(results) >= 1
        first_result = results[0]
        assert "volume_number" in first_result
        assert "similarity" in first_result
        assert "volume_summary" in first_result
        assert "relevance_score" in first_result


# ── Test 3: Summary Persistence Survives Restart ───────────────────


class TestSummaryPersistenceSurvivesRestart:
    """Verify summaries persist across MemoryContext restarts."""

    @pytest.mark.asyncio
    async def test_summaries_survive_context_restart(self) -> None:
        """Generate summaries, save, reload, verify they match."""
        storage = _make_temp_storage()
        project_id = "test_persistence_project"

        router = _make_mock_router([])
        builder = _make_mock_builder()
        episodic = EpisodicMemory(use_mock_embeddings=True)

        ctx1 = MemoryContext()
        ctx1._episodic_memory = episodic
        ctx1._summary_service = MultiGranularitySummaryService(
            router=router,
            builder=builder,
            config=SummaryConfig(),
        )
        ctx1._project_id = project_id
        ctx1._storage = storage

        ctx1._summary_cache[1] = {
            "text": "第一章摘要：林远觉醒超能力。",
            "source_hash": "hash_ch1",
            "version": 1,
            "updated_at": "2026-01-01",
        }
        ctx1._summary_cache[2] = {
            "text": "第二章摘要：林远遇到苏晴。",
            "source_hash": "hash_ch2",
            "version": 1,
            "updated_at": "2026-01-02",
        }
        ctx1._last_indexed_chapter = 2

        saved = ctx1.save_to_disk()
        assert saved is True

        ctx2 = MemoryContext()
        ctx2._episodic_memory = EpisodicMemory(use_mock_embeddings=True)
        ctx2._summary_service = MultiGranularitySummaryService(
            router=router,
            builder=builder,
            config=SummaryConfig(),
        )
        ctx2._project_id = project_id
        ctx2._storage = storage

        loaded = ctx2.load_from_disk()
        assert loaded is True

        assert ctx2._last_indexed_chapter == 2
        summary1 = ctx2.get_cached_summary(1)
        summary2 = ctx2.get_cached_summary(2)

        assert summary1 == "第一章摘要：林远觉醒超能力。"
        assert summary2 == "第二章摘要：林远遇到苏晴。"


# ── Test 4: Volume Eviction After Finalize ─────────────────────────


class TestVolumeEvictionAfterFinalize:
    """Verify old volume summaries are evicted after finalize_volume_memory."""

    @pytest.mark.asyncio
    async def test_old_summaries_evicted_on_finalize(self) -> None:
        """Create summaries for multiple volumes, finalize, verify eviction."""
        router = _make_mock_router([])
        builder = _make_mock_builder()
        episodic = EpisodicMemory(use_mock_embeddings=True)

        summary_service = MultiGranularitySummaryService(
            router=router,
            builder=builder,
            config=SummaryConfig(),
        )

        ctx = MemoryContext()
        ctx._episodic_memory = episodic
        ctx._summary_service = summary_service

        for ch in range(1, 11):
            ctx._summary_cache[ch] = {
                "text": f"第{ch}章摘要",
                "source_hash": f"hash_{ch}",
                "version": 1,
                "updated_at": "2026-01-01",
            }

        for ch in range(11, 21):
            ctx._summary_cache[ch] = {
                "text": f"第{ch}章摘要",
                "source_hash": f"hash_{ch}",
                "version": 1,
                "updated_at": "2026-01-02",
            }

        for ch in range(21, 31):
            ctx._summary_cache[ch] = {
                "text": f"第{ch}章摘要",
                "source_hash": f"hash_{ch}",
                "version": 1,
                "updated_at": "2026-01-03",
            }

        assert len(ctx._summary_cache) == 30

        await ctx.finalize_volume_memory(
            volume_number=3,
            start_chapter=21,
            end_chapter=30,
        )

        purge_stats = ctx.purge_volume_caches(
            volume_number=3,
            volume_chapter_range=(21, 30),
        )

        assert purge_stats["cleared_summaries"] == 10
        assert purge_stats["cleared_motifs"] == 0

        remaining = len(ctx._summary_cache)
        assert remaining == 20


# ── Test 5: Graceful Degradation No Embedding ──────────────────────


class TestGracefulDegradationNoEmbedding:
    """Verify full pipeline works gracefully without real embeddings."""

    @pytest.mark.asyncio
    async def test_full_pipeline_no_real_embeddings(self) -> None:
        """Run index → compress → volume audit → summary persistence, no exceptions."""
        storage = _make_temp_storage()
        project_id = "test_graceful_project"

        episodic = EpisodicMemory(use_mock_embeddings=True)

        router = _make_mock_router_for_compression()
        builder = _make_mock_builder()

        compression = AdaptiveCompressionService(
            router=router,
            builder=builder,
            config=CompressionConfig(min_quality_score=0.7),
            episodic_memory=episodic,
        )

        summary_service = MultiGranularitySummaryService(
            router=router,
            builder=builder,
            config=SummaryConfig(),
        )

        ctx = MemoryContext()
        ctx._episodic_memory = episodic
        ctx._compression_service = compression
        ctx._summary_service = summary_service
        ctx._project_id = project_id
        ctx._storage = storage

        errors: list[str] = []

        try:
            for ch in range(1, 4):
                await episodic.index_chapter_outcome(
                    chapter_number=ch,
                    event_summary=f"第{ch}章：故事发展。",
                    full_text=_generate_chapter_text(ch),
                )
        except Exception as exc:
            errors.append(f"index_chapter failed: {exc}")

        try:
            result = await compression.compress_with_quality_check(
                original_text=_generate_chapter_text(4),
                target_chars=500,
                task_type=TaskType.CONTEXT_COMPRESS,
                current_chapter=4,
            )
            assert result is not None
            assert result.quality_score >= 0.7
        except Exception as exc:
            errors.append(f"compress failed: {exc}")

        try:
            audit_report = VolumeAuditReport(
                volume_number=1,
                volume_title="测试卷",
                chapter_range="1-3",
                volume_summary="测试卷摘要：故事开始。",
                consistency_score=8.0,
                consistency_issues=[],
                carry_over_characters=["主角"],
                carry_over_items=["道具"],
                carry_over_world_fact_keys=["设定"],
                carry_over_foreshadowing_ids=[],
                next_volume_focus="继续发展",
            )
            await episodic.index_volume_audit(audit_report)

            search_results = await episodic.search_similar_volumes(
                query_text="故事开始",
                top_k=2,
                min_relevance=-1.0,
            )
            assert isinstance(search_results, list)
        except Exception as exc:
            errors.append(f"volume_audit failed: {exc}")

        try:
            summary_service.cache_summary("chapter", 1, "第一章摘要")
            summary_service.cache_summary("chapter", 2, "第二章摘要")
            summary_service.cache_summary("chapter", 3, "第三章摘要")

            saved = ctx.save_to_disk()
            assert saved is True

            ctx2 = MemoryContext()
            ctx2._episodic_memory = EpisodicMemory(use_mock_embeddings=True)
            ctx2._summary_service = MultiGranularitySummaryService(
                router=router,
                builder=builder,
                config=SummaryConfig(),
            )
            ctx2._project_id = project_id
            ctx2._storage = storage

            loaded = ctx2.load_from_disk()
            assert loaded is True
            assert ctx2._last_indexed_chapter == 0
        except Exception as exc:
            errors.append(f"persistence failed: {exc}")

        assert errors == [], f"Pipeline errors: {errors}"
