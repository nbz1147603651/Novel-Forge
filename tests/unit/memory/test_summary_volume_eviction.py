"""Tests for volume-scoped summary cache eviction."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from novel_forge.memory.summary import MultiGranularitySummaryService

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.prompts.builder import PromptBuilder


@pytest.fixture
def service(router: "ModelRouter", builder: "PromptBuilder") -> MultiGranularitySummaryService:
    return MultiGranularitySummaryService(router=router, builder=builder)


class TestEvictVolumeSummaries:
    async def test_evict_volume_summaries(self, service: MultiGranularitySummaryService) -> None:
        for ch in range(1, 51):
            service.cache_summary("chapter", ch, f"Chapter {ch} summary")

        evicted = service.evict_volume_summaries(
            volume_number=3,
            keep_recent_volumes=2,
            max_chapter_to_keep=21,
        )

        assert evicted == 20
        for ch in range(1, 21):
            assert service.get_summary("chapter", ch) is None
        for ch in range(21, 51):
            assert service.get_summary("chapter", ch) is not None


class TestEvictKeepsRecentVolumes:
    async def test_evict_keeps_recent_volumes(self, service: MultiGranularitySummaryService) -> None:
        for ch in range(1, 51):
            service.cache_summary("chapter", ch, f"Chapter {ch} summary")

        service.evict_volume_summaries(
            volume_number=3,
            keep_recent_volumes=2,
            max_chapter_to_keep=21,
        )

        assert service.get_summary("chapter", 21) == "Chapter 21 summary"
        assert service.get_summary("chapter", 35) == "Chapter 35 summary"
        assert service.get_summary("chapter", 50) == "Chapter 50 summary"

        remaining = service.get_chapter_summaries()
        assert len(remaining) == 30


class TestEvictEmptyCache:
    async def test_evict_empty_cache(self, service: MultiGranularitySummaryService) -> None:
        evicted = service.evict_volume_summaries(
            volume_number=1,
            keep_recent_volumes=2,
            max_chapter_to_keep=10,
        )
        assert evicted == 0

    async def test_evict_no_threshold_returns_zero(self, service: MultiGranularitySummaryService) -> None:
        for ch in range(1, 11):
            service.cache_summary("chapter", ch, f"Chapter {ch} summary")

        evicted = service.evict_volume_summaries(volume_number=1)
        assert evicted == 0
        assert len(service.get_chapter_summaries()) == 10
