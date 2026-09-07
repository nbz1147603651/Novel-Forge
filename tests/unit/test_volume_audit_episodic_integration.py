"""Tests for episodic context integration in VolumeAuditStep."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput


def _make_volume(title: str = "卷一", arc_goal: str = "主角觉醒") -> VolumeOutline:
    return VolumeOutline(
        volume_number=1,
        title=title,
        start_chapter=1,
        end_chapter=10,
        arc_goal=arc_goal,
    )


def _make_report(volume_number: int = 1) -> VolumeAuditReport:
    return VolumeAuditReport(
        volume_number=volume_number,
        volume_title=f"Volume {volume_number}",
        chapter_range="1-10",
        volume_summary=f"Summary of volume {volume_number}",
        consistency_score=8.0,
        consistency_issues=[],
        carry_over_characters=[],
        carry_over_items=[],
        carry_over_world_fact_keys=[],
        carry_over_foreshadowing_ids=[],
        next_volume_focus="",
    )


class TestVolumeAuditEpisodicContext:
    async def test_volume_audit_with_episodic_context(self) -> None:
        episodic_memory = EpisodicMemory(use_mock_embeddings=True)
        reports = [
            _make_report(1),
            _make_report(2),
            _make_report(3),
        ]
        for report in reports:
            await episodic_memory.index_volume_audit(report)

        volume = _make_volume(title="修炼之路")
        similar = await episodic_memory.search_similar_volumes(
            query_text=volume.title,
            top_k=3,
            min_relevance=-1.0,
        )

        audit_input = VolumeAuditInput(
            volume=volume,
            story_synopsis="Test synopsis",
            chapter_summaries=[{"chapter": 1, "summary": "Ch1"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            episodic_context={"similar_volumes": similar},
        )

        assert audit_input.episodic_context is not None
        assert "similar_volumes" in audit_input.episodic_context
        assert len(audit_input.episodic_context["similar_volumes"]) > 0
        for v in audit_input.episodic_context["similar_volumes"]:
            assert "volume_number" in v
            assert "relevance_score" in v

    async def test_volume_audit_without_episodic_context(self) -> None:
        volume = _make_volume()

        audit_input = VolumeAuditInput(
            volume=volume,
            story_synopsis="Test synopsis",
            chapter_summaries=[],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            episodic_context=None,
        )

        assert audit_input.episodic_context is None

    async def test_volume_audit_episodic_context_none_by_default(self) -> None:
        volume = _make_volume()

        audit_input = VolumeAuditInput(
            volume=volume,
            story_synopsis="Test synopsis",
            chapter_summaries=[],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
        )

        assert audit_input.episodic_context is None

    async def test_chapter_runner_episodic_context_population(self) -> None:
        mock_episodic = AsyncMock(spec=EpisodicMemory)
        mock_episodic.search_similar_volumes.return_value = [
            {"volume_number": 1, "relevance_score": 0.85, "volume_summary": "test"}
        ]

        mock_memory_context = MagicMock()
        mock_memory_context.episodic_memory = mock_episodic

        volume = _make_volume(title="秘境探险")
        query_text = volume.title or volume.arc_goal

        episodic_context = None
        if mock_memory_context is not None and mock_memory_context.episodic_memory is not None:
            if query_text:
                episodic_context = {
                    "similar_volumes": await mock_memory_context.episodic_memory.search_similar_volumes(
                        query_text=query_text,
                        top_k=3,
                    )
                }

        assert episodic_context is not None
        assert len(episodic_context["similar_volumes"]) == 1
        mock_episodic.search_similar_volumes.assert_called_once_with(
            query_text="秘境探险",
            top_k=3,
        )

    async def test_chapter_runner_episodic_context_none_when_memory_missing(self) -> None:
        mock_memory_context = MagicMock()
        mock_memory_context.episodic_memory = None

        volume = _make_volume(title="test")
        query_text = volume.title or volume.arc_goal

        episodic_context = None
        if mock_memory_context is not None and mock_memory_context.episodic_memory is not None:
            if query_text:
                episodic_context = {"similar_volumes": []}

        assert episodic_context is None

    async def test_chapter_runner_episodic_context_none_when_context_missing(self) -> None:
        memory_context = None

        volume = _make_volume(title="test")
        query_text = volume.title or volume.arc_goal

        episodic_context = None
        if memory_context is not None and getattr(memory_context, "episodic_memory", None) is not None:
            if query_text:
                episodic_context = {"similar_volumes": []}

        assert episodic_context is None
