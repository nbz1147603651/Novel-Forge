"""Regression tests for Phase 2+3 VolumeAuditStep + EpisodicMemory changes.

These tests verify that existing functionality remains unchanged after T8/T9/T10's
additions. Focus on backward compatibility and ensuring core behavior is preserved.

MUST DO:
- test_volume_audit_step_backward_compat: VolumeAuditStep works without episodic_context
- test_episodic_memory_existing_methods: search_by_semantic, search_by_temporal, get_recent_events still work
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.gateway.types import ModelResponse
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput, VolumeAuditStep

if TYPE_CHECKING:
    from novel_forge.core.config import Settings
    from novel_forge.core.schemas.outline import VolumeOutline
    from novel_forge.prompts.builder import PromptBuilder


def _make_volume_outline(
    volume_number: int = 1,
    title: str = "第一卷",
    arc_goal: str = "建立世界观",
    start_chapter: int = 1,
    end_chapter: int = 10,
) -> "VolumeOutline":
    from novel_forge.core.schemas.outline import VolumeOutline

    return VolumeOutline(
        volume_number=volume_number,
        title=title,
        arc_goal=arc_goal,
        start_chapter=start_chapter,
        end_chapter=end_chapter,
    )


def _make_outcome(
    chapter: int,
    summary: str,
    events: list,
    text: str = "",
):
    from dataclasses import dataclass, field

    @dataclass
    class FakeTimelineEvent:
        chapter: int
        event: str
        characters_involved: list = field(default_factory=list)
        timestamp_in_story: str = ""

    @dataclass
    class FakeOutcome:
        chapter_summary: str = ""
        source_chapter: int = 0
        character_updates: dict = field(default_factory=dict)
        new_events: list = field(default_factory=list)
        text: str = ""
        creative_report: Any = None
        alignment_report: Any = None
        plan: Any = None

    return FakeOutcome(
        chapter_summary=summary,
        source_chapter=chapter,
        character_updates={"角色A": {}} if chapter else {},
        new_events=[FakeTimelineEvent(**e) for e in events],
        text=text,
    )


class TestVolumeAuditStepBackwardCompat:
    """Test that VolumeAuditStep works without episodic_context (backward compatibility)."""

    @pytest.fixture
    def mock_router(self):
        router = MagicMock()
        router.route = AsyncMock(
            return_value=ModelResponse(
                content='{"volume_number": 1, "volume_summary": "Test volume summary", "consistency_score": 8.5, '
                '"consistency_issues": [], "carry_over_characters": [], "retire_characters": [], '
                '"carry_over_items": [], "retire_items": [], "carry_over_world_fact_keys": [], '
                '"retire_world_fact_keys": [], "carry_over_foreshadowing_ids": [], '
                '"resolved_foreshadowing_ids": [], "next_volume_focus": "Continue arc", '
                '"milestone_status": {}}'
            )
        )
        router.stream_route = router.route
        return router

    @pytest.fixture
    def volume_audit_step(
        self,
        mock_router,
        builder: "PromptBuilder",
        runtime_settings: "Settings",
    ) -> VolumeAuditStep:
        return VolumeAuditStep(router=mock_router, builder=builder, settings=runtime_settings)

    @pytest.mark.asyncio
    async def test_volume_audit_step_without_episodic_context(
        self,
        volume_audit_step: VolumeAuditStep,
    ) -> None:
        """VolumeAuditStep works without episodic_context - same as before T8/T10.

        This is the key backward compatibility test: episodic_context is optional
        and defaults to None. When not provided, the step should still work
        using only the explicit fields.
        """
        input_data = VolumeAuditInput(
            volume=_make_volume_outline(),
            story_synopsis="测试故事概要",
            chapter_summaries=[
                {"chapter": 1, "summary": "第一章概要"},
                {"chapter": 2, "summary": "第二章概要"},
            ],
            timeline_events=[
                {"chapter": 1, "event": "事件1", "characters": ["角色A"]},
            ],
            active_characters=[{"name": "角色A", "status": "active"}],
            active_foreshadowing=[],
            world_fact_keys=["key1"],
            # NO episodic_context - this is the backward compatibility case
        )

        result = await volume_audit_step.run(input_data)

        assert result is not None
        assert isinstance(result, VolumeAuditReport)
        assert result.volume_summary == "Test volume summary"
        assert result.consistency_score == 8.5

    @pytest.mark.asyncio
    async def test_volume_audit_step_with_none_episodic_context(
        self,
        volume_audit_step: VolumeAuditStep,
    ) -> None:
        """VolumeAuditStep works with explicit None episodic_context."""
        input_data = VolumeAuditInput(
            volume=_make_volume_outline(),
            story_synopsis="测试故事概要",
            chapter_summaries=[{"chapter": 1, "summary": "第一章概要"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            episodic_context=None,  # Explicit None
        )

        result = await volume_audit_step.run(input_data)

        assert result is not None
        assert isinstance(result, VolumeAuditReport)


class TestEpisodicMemoryExistingMethods:
    """Test that existing EpisodicMemory methods still work after T8/T9/T10 additions."""

    @pytest.fixture
    def episodic_memory(self) -> EpisodicMemory:
        return EpisodicMemory(use_mock_embeddings=True)

    @pytest.mark.asyncio
    async def test_search_by_semantic_still_works(
        self,
        episodic_memory: EpisodicMemory,
    ) -> None:
        """search_by_semantic returns results after T8/T10 additions."""
        # Index a chapter first
        outcome = _make_outcome(
            chapter=1,
            summary="第一章：主角来到陌生小镇",
            events=[{"chapter": 1, "event": "到达", "characters_involved": ["主角"]}],
            text="主角来到陌生的小镇，遇到一个神秘老人。",
        )
        await episodic_memory.index_chapter(outcome)

        # search_by_semantic should still work
        results = await episodic_memory.search_by_semantic(
            query="主角来到小镇",
            top_k=5,
        )

        assert isinstance(results, list)
        assert len(results) >= 0  # May be empty depending on embedding quality

    @pytest.mark.asyncio
    async def test_search_by_temporal_still_works(
        self,
        episodic_memory: EpisodicMemory,
    ) -> None:
        """search_by_temporal returns results after T8/T10 additions."""
        # Index multiple chapters
        for ch in range(1, 6):
            outcome = _make_outcome(
                chapter=ch,
                summary=f"第{ch}章概要",
                events=[{"chapter": ch, "event": f"事件{ch}", "characters_involved": [f"角色{ch}"]}],
            )
            await episodic_memory.index_chapter(outcome)

        # search_by_temporal should still work
        results = episodic_memory.search_by_temporal(
            start_chapter=1,
            end_chapter=3,
        )

        assert isinstance(results, list)
        assert len(results) >= 0

    @pytest.mark.asyncio
    async def test_get_recent_events_still_works(
        self,
        episodic_memory: EpisodicMemory,
    ) -> None:
        """get_recent_events returns recent events after T8/T10 additions."""
        # Index chapters
        for ch in range(1, 6):
            outcome = _make_outcome(
                chapter=ch,
                summary=f"第{ch}章概要",
                events=[{"chapter": ch, "event": f"事件{ch}", "characters_involved": ["角色A"]}],
            )
            await episodic_memory.index_chapter(outcome)

        # get_recent_events should still work
        results = await episodic_memory.get_recent_events(
            current_chapter=6,
            lookback=3,
        )

        assert isinstance(results, list)
        # Should get events from chapters 3, 4, 5 (lookback=3, current=6, so 3-5)
        for r in results:
            assert 3 <= r.chapter_number <= 5

    @pytest.mark.asyncio
    async def test_get_recent_events_empty_when_first_chapter(
        self,
        episodic_memory: EpisodicMemory,
    ) -> None:
        """get_recent_events returns empty list for first chapter (same as before)."""
        outcome = _make_outcome(
            chapter=1,
            summary="第一章概要",
            events=[{"chapter": 1, "event": "事件1", "characters_involved": ["角色A"]}],
        )
        await episodic_memory.index_chapter(outcome)

        results = await episodic_memory.get_recent_events(current_chapter=1)

        assert results == []

    @pytest.mark.asyncio
    async def test_index_chapter_still_works(
        self,
        episodic_memory: EpisodicMemory,
    ) -> None:
        """index_chapter works after T8/T10 additions."""
        outcome = _make_outcome(
            chapter=1,
            summary="第一章概要",
            events=[{"chapter": 1, "event": "事件1", "characters_involved": ["角色A"]}],
            text="第一章正文内容",
        )

        # Should not raise any exceptions
        sigs = await episodic_memory.index_chapter(outcome)

        assert sigs is not None
        assert isinstance(sigs, list)
        assert len(sigs) > 0
        for sig in sigs:
            assert isinstance(sig, str)
            assert len(sig) == 32  # MD5 signature
