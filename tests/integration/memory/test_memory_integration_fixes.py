"""Integration tests verifying all 4 memory fixes work together.

Validates end-to-end flows across the memory system:

1. Episodic context flows from chapter_runner → VolumeAuditInput → VolumeAuditStep payload
2. finalize_volume_memory calls index_volume_audit when audit_report provided
3. finalize_volume_memory calls save_summaries and evict_volume_summaries
4. compress_prompt_context performs quality verification with retry

All tests use mock embeddings (use_mock_embeddings=True) — no external API calls.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.summary import MultiGranularitySummaryService, SummaryConfig
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput, VolumeAuditStep

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


# ── Test 1: Episodic Context Reaches LLM ───────────────────────────


class TestVolumeAuditEpisodicContextReachesLLM:
    """Verify episodic_context flows from chapter_runner → VolumeAuditInput → VolumeAuditStep payload."""

    @pytest.mark.asyncio
    async def test_volume_audit_episodic_context_reaches_llm(self) -> None:
        """Build VolumeAuditInput with episodic_context, run step, verify payload includes it."""
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
        await episodic.index_volume_audit(report1)

        similar = await episodic.search_similar_volumes(
            query_text="林远觉醒超能力",
            top_k=2,
            min_relevance=-1.0,
        )
        assert len(similar) >= 1

        episodic_context = {"similar_volumes": similar}

        volume = VolumeOutline(
            volume_number=2,
            title="第二卷：迷雾",
            start_chapter=11,
            end_chapter=20,
            arc_goal="揭开超能力组织的秘密",
            milestone_targets=[],
            main_conflicts=[],
            climax_hint="",
            resolution_hint="",
            notes="",
        )

        audit_input = VolumeAuditInput(
            volume=volume,
            story_synopsis="林远的超能力冒险故事。",
            chapter_summaries=[{"chapter": 11, "summary": "第十一章摘要"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=["超能力体系"],
            episodic_context=episodic_context,
        )

        assert audit_input.episodic_context is not None
        assert "similar_volumes" in audit_input.episodic_context
        assert len(audit_input.episodic_context["similar_volumes"]) >= 1

        first_vol = audit_input.episodic_context["similar_volumes"][0]
        assert "volume_number" in first_vol
        assert "similarity" in first_vol

        step = VolumeAuditStep(
            router=_make_mock_router([]),
            builder=_make_mock_builder(),
            settings=Settings(),
        )
        captured_payload: dict = {}

        async def capture_payload(*args, **kwargs):
            captured_payload["task_type"] = args[0] if args else kwargs.get("task_type")
            captured_payload["payload"] = args[1] if len(args) > 1 else kwargs.get("payload", {})
            return {
                "volume_number": 2,
                "volume_title": "第二卷：迷雾",
                "chapter_range": "11-20",
                "volume_summary": "测试摘要",
                "consistency_score": 8.0,
                "consistency_issues": [],
                "carry_over_characters": ["林远"],
                "carry_over_items": [],
                "carry_over_world_fact_keys": [],
                "carry_over_foreshadowing_ids": [],
                "next_volume_focus": "继续",
            }

        step._call_with_retry = capture_payload

        result = await step._execute(audit_input)

        assert isinstance(result, VolumeAuditReport)
        assert "episodic_context" in captured_payload["payload"]
        payload_ec = captured_payload["payload"]["episodic_context"]
        assert "similar_volumes" in payload_ec
        assert len(payload_ec["similar_volumes"]) >= 1


# ── Test 2: Volume Audit Indexed on Finalize ───────────────────────


class TestVolumeAuditIndexedOnFinalize:
    """Verify finalize_volume_memory calls index_volume_audit when audit_report provided."""

    @pytest.mark.asyncio
    async def test_volume_audit_indexed_on_finalize(self) -> None:
        """Create MemoryContext with episodic memory, finalize with audit_report, verify indexing."""
        episodic = EpisodicMemory(use_mock_embeddings=True)
        router = _make_mock_router([])
        builder = _make_mock_builder()

        ctx = MemoryContext()
        ctx._episodic_memory = episodic
        ctx._summary_service = MultiGranularitySummaryService(
            router=router,
            builder=builder,
            config=SummaryConfig(),
        )

        audit_report = VolumeAuditReport(
            volume_number=1,
            volume_title="第一卷：觉醒",
            chapter_range="1-10",
            volume_summary="林远觉醒了超能力。",
            consistency_score=9.0,
            consistency_issues=[],
            carry_over_characters=["林远"],
            carry_over_items=["神秘信件"],
            carry_over_world_fact_keys=["超能力体系"],
            carry_over_foreshadowing_ids=["fs_001"],
            next_volume_focus="继续探索",
        )

        stats = await ctx.finalize_volume_memory(
            volume_number=1,
            start_chapter=1,
            end_chapter=10,
            audit_report=audit_report,
        )

        assert stats.get("volume_audit_indexed") is True

        results = await episodic.search_similar_volumes(
            query_text="林远觉醒",
            top_k=1,
            min_relevance=-1.0,
        )
        assert len(results) >= 1
        assert results[0]["volume_number"] == 1


# ── Test 3: Summaries Persisted on Finalize ────────────────────────


class TestSummariesPersistedOnFinalize:
    """Verify finalize_volume_memory calls save_summaries."""

    @pytest.mark.asyncio
    async def test_summaries_persisted_on_finalize(self) -> None:
        """Create MemoryContext with summary service, finalize, verify summaries_saved flag."""
        storage = _make_temp_storage()
        project_id = "test_persist_project"

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
        ctx._project_id = project_id
        ctx._storage = storage

        summary_service.cache_summary("chapter", 1, "第一章摘要：故事开始。")
        summary_service.cache_summary("chapter", 2, "第二章摘要：林远觉醒。")

        stats = await ctx.finalize_volume_memory(
            volume_number=1,
            start_chapter=1,
            end_chapter=2,
        )

        assert stats.get("summaries_saved") is True


# ── Test 4: Old Summaries Evicted on Finalize ──────────────────────


class TestOldSummariesEvictedOnFinalize:
    """Verify finalize_volume_memory calls evict_volume_summaries."""

    @pytest.mark.asyncio
    async def test_old_summaries_evicted_on_finalize(self) -> None:
        """Create summaries for multiple volumes, finalize volume 3, verify eviction."""
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

        stats = await ctx.finalize_volume_memory(
            volume_number=3,
            start_chapter=21,
            end_chapter=30,
        )

        assert stats.get("summaries_evicted") is not None
        assert len(stats.get("errors", [])) == 0

        purge_stats = ctx.purge_volume_caches(
            volume_number=3,
            volume_chapter_range=(21, 30),
        )
        assert purge_stats["cleared_summaries"] == 10
        remaining = len(ctx._summary_cache)
        assert remaining == 20


# ── Test 5: Compress Quality Verified ──────────────────────────────


class TestCompressQualityVerified:
    """Verify compress_prompt_context performs quality verification."""

    @pytest.mark.asyncio
    async def test_compress_quality_verified(self) -> None:
        """Run compress_prompt_context with quality_check=True, verify retry on low score."""
        from novel_forge.pipeline.long.services.context.context_helpers import (
            _verify_compression_quality,
            compress_prompt_context,
        )

        original_text = (
            "林远因为发现了神秘信件，所以决定调查真相。但是敌人很强大，如果他不能找到盟友，就会失败。"
            "最终他决定去寻找传说中的超能力者。一路上他遇到了各种困难和挑战，但他从未放弃过。"
            "因为他知道，只有找到超能力者，才能对抗强大的敌人。所以他继续前行，不管前方有多少危险在等着他。"
            "如果他失败了，整个世界都会陷入黑暗之中。但是他相信，只要坚持下去，就一定能找到希望的曙光。"
            "所以他决定，无论付出什么代价，都要完成这个使命。他踏上了漫长的旅途，穿越了无数的城市和乡村。"
            "在这个过程中，他结识了许多志同道合的伙伴，也遭遇了无数强大的敌人。但他始终没有放弃。"
        )

        call_count = 0

        async def mock_call_with_retry(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "items": [
                        {
                            "id": "ctx_1",
                            "compressed": "林远调查。",
                        }
                    ]
                }
            return {
                "items": [
                    {
                        "id": "ctx_1",
                        "compressed": "林远因发现信件决定调查，需找盟友对抗强敌。",
                    }
                ]
            }

        step_calls: list[tuple[str, dict]] = []

        def mock_on_step(step_name: str, data: dict) -> None:
            step_calls.append((step_name, data))

        creative_report: dict = {
            "plot_deviations": [
                {
                    "outline_plan": original_text,
                    "actual_plot": original_text,
                    "reason": "测试",
                    "impact_on_future": "后续发展",
                }
            ],
            "suggestions_for_next_chapter": "继续推进剧情。",
            "new_characters": [],
        }

        character_profiles: list[dict] = []

        result = await compress_prompt_context(
            previous_creative_report=creative_report,
            character_profiles=character_profiles,
            packet=None,
            report_text_chars=200,
            profile_field_chars=100,
            bridge_brief_chars=200,
            planning_brief_chars=200,
            continuity_brief_chars=200,
            compress_enabled=True,
            compress_min_chars=10,
            compress_max_tokens=512,
            temperature=0.3,
            call_with_retry=mock_call_with_retry,
            on_step=mock_on_step,
            budget_pressure=0.5,
            quality_check=True,
            adaptive_skip_enabled=False,
        )

        assert result["candidates"] >= 1
        assert call_count >= 2, "Expected at least 2 calls: initial + quality retry"
        assert result["quality_retries"] >= 1, "Expected at least 1 quality retry"

        score_low = _verify_compression_quality(original_text, "林远调查。")
        assert score_low < 0.5, "Overly compressed text should score below 0.5"

        score_good = _verify_compression_quality(
            original_text,
            "林远因发现信件决定调查，需找盟友对抗强敌。",
        )
        assert score_good >= score_low, "Better compression should score higher"
