"""Regression tests for memory API route edge cases."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.api.routes import memory
from novel_forge.memory.critic import CritiqueIssue, CritiqueReport
from novel_forge.memory.summary import MultiGranularitySummaryService


class _RuntimeStub:
    def __init__(self, memory_context: object) -> None:
        self._memory_context = memory_context

    async def get_memory_context(self, _project_id: str) -> object:
        return self._memory_context


@pytest.mark.asyncio
async def test_get_summaries_reads_cached_multi_granularity_data() -> None:
    summary_service = MultiGranularitySummaryService(router=None, builder=None)  # type: ignore[arg-type]
    summary_service.cache_summary("chapter", 1, "chapter-1")
    summary_service.cache_summary("chapter", 2, "chapter-2")
    summary_service.cache_summary("chapter", 10, "chapter-10")
    summary_service.cache_summary("chapter", 11, "chapter-11")
    summary_service.cache_summary("chapter", 12, "chapter-12")
    summary_service.cache_summary("volume", 1, "volume-1")
    summary_service.cache_summary("arc", 0, "arc-main", arc_name="main")

    memory_context = SimpleNamespace(
        summary_service=summary_service,
        get_cached_summary=lambda _chapter: "cached-context",
    )
    runtime = _RuntimeStub(memory_context)

    result = await memory.get_summaries(
        project_id="demo",
        current_chapter=12,
        runtime=runtime,
        lookback_volumes=1,
    )

    assert result.chapter_summaries == {
        "2": "chapter-2",
        "10": "chapter-10",
        "11": "chapter-11",
    }
    assert result.volume_summaries == {"1": "volume-1"}
    assert result.arc_summaries == {"main": "arc-main"}
    assert result.current_context_summary == "cached-context"


@pytest.mark.asyncio
async def test_critique_route_uses_issue_type_as_category() -> None:
    issue = CritiqueIssue(
        issue_type="causal_break",
        severity="high",
        summary="因果链断裂",
        evidence="线索缺失",
        affected_chapters=[3],
        suggested_fix="补一段动机过渡",
    )
    report = CritiqueReport(
        chapter_number=5,
        overall_score=8.0,
        issues=[issue],
        strengths=["节奏稳定"],
    )

    class _CriticAgentStub:
        async def critique_chapter(self, **_kwargs) -> CritiqueReport:
            return report

    memory_context = SimpleNamespace(critic_agent=_CriticAgentStub())
    runtime = _RuntimeStub(memory_context)

    result = await memory.critique_chapter(
        project_id="demo",
        body=memory.CritiqueRequest(chapter_number=5, chapter_text="text"),
        runtime=runtime,
    )

    assert result.issues[0]["category"] == "causal_break"
    assert result.issues[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_track_character_arc_uses_query_character_name() -> None:
    class _MemoryContextStub:
        def __init__(self) -> None:
            self._last_indexed_chapter = 8
            self.calls: list[tuple[str, int, int]] = []

        def get_character_history(
            self,
            character_name: str,
            current_chapter: int,
            lookback: int = 10,
        ) -> list[dict]:
            self.calls.append((character_name, current_chapter, lookback))
            return [
                {
                    "chapter": 7,
                    "location": "旧城门",
                    "emotional_state": "紧张",
                }
            ]

    memory_context = _MemoryContextStub()
    runtime = _RuntimeStub(memory_context)

    result = await memory.track_character_arc(
        project_id="demo",
        character_name="林青",
        runtime=runtime,
        lookback_chapters=6,
    )

    assert result.character_name == "林青"
    assert result.arc_milestones[0]["chapter"] == 7
    assert memory_context.calls == [("林青", 9, 6)]
