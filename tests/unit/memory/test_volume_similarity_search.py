"""Tests for search_similar_volumes in EpisodicMemory."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.memory.episodic import EpisodicMemory


@pytest.fixture
def episodic_memory() -> EpisodicMemory:
    return EpisodicMemory(use_mock_embeddings=True)


def _make_report(
    volume_number: int,
    volume_summary: str,
    chapter_range: str = "1-10",
    consistency_score: float = 8.0,
    carry_over_characters: list[str] | None = None,
    carry_over_items: list[str] | None = None,
) -> VolumeAuditReport:
    return VolumeAuditReport(
        volume_number=volume_number,
        volume_title=f"Volume {volume_number}",
        chapter_range=chapter_range,
        volume_summary=volume_summary,
        consistency_score=consistency_score,
        consistency_issues=[],
        carry_over_characters=carry_over_characters or [],
        carry_over_items=carry_over_items or [],
        carry_over_world_fact_keys=[],
        carry_over_foreshadowing_ids=[],
        next_volume_focus="",
    )


class TestSearchSimilarVolumes:
    async def test_search_similar_volumes(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        reports = [
            _make_report(1, "主角觉醒并踏上修炼之路", "1-20", 8.5),
            _make_report(2, "宗门试炼与初次历练", "21-40", 7.8),
            _make_report(3, "秘境探险与宝物争夺", "41-60", 9.0),
        ]
        for report in reports:
            await episodic_memory.index_volume_audit(report)

        results = await episodic_memory.search_similar_volumes(
            query_text="修炼与试炼",
            top_k=3,
            min_relevance=-1.0,
        )

        assert len(results) <= 3
        assert len(results) > 0
        for i in range(len(results) - 1):
            assert results[i]["relevance_score"] >= results[i + 1]["relevance_score"]
        for r in results:
            assert "volume_number" in r
            assert "similarity" in r
            assert "volume_summary" in r
            assert "consistency_score" in r
            assert "chapter_range" in r
            assert "carry_over_characters" in r
            assert "carry_over_items" in r

    async def test_search_similar_volumes_min_relevance(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        reports = [
            _make_report(1, "主角觉醒并踏上修炼之路", "1-20", 8.5),
            _make_report(2, "宗门试炼与初次历练", "21-40", 7.8),
            _make_report(3, "秘境探险与宝物争夺", "41-60", 9.0),
        ]
        for report in reports:
            await episodic_memory.index_volume_audit(report)

        low_results = await episodic_memory.search_similar_volumes(
            query_text="修炼",
            top_k=5,
            min_relevance=-1.0,
        )
        high_results = await episodic_memory.search_similar_volumes(
            query_text="修炼",
            top_k=5,
            min_relevance=0.99,
        )

        assert len(high_results) <= len(low_results)

    async def test_search_similar_volumes_top_k(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        reports = [
            _make_report(1, "主角觉醒并踏上修炼之路", "1-20", 8.5),
            _make_report(2, "宗门试炼与初次历练", "21-40", 7.8),
            _make_report(3, "秘境探险与宝物争夺", "41-60", 9.0),
        ]
        for report in reports:
            await episodic_memory.index_volume_audit(report)

        results = await episodic_memory.search_similar_volumes(
            query_text="修炼",
            top_k=2,
            min_relevance=-1.0,
        )

        assert len(results) <= 2

    async def test_search_similar_volumes_no_results(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        reports = [
            _make_report(1, "主角觉醒并踏上修炼之路", "1-20", 8.5),
        ]
        for report in reports:
            await episodic_memory.index_volume_audit(report)

        results = await episodic_memory.search_similar_volumes(
            query_text="完全无关的量子物理与相对论",
            top_k=3,
            min_relevance=0.95,
        )

        assert results == []
