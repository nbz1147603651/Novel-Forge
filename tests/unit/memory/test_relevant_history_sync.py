from __future__ import annotations

from types import SimpleNamespace

from novel_forge.memory.integration import MemoryContext
from novel_forge.memory.relevant_history_sync import search_relevant_history_temporal_fallback


def test_search_relevant_history_sync_uses_async_semantic_path_without_running_loop() -> None:
    ctx = MemoryContext()
    ctx._episodic_memory = object()
    calls: list[dict[str, object]] = []

    async def _search(**kwargs: object) -> list[dict[str, object]]:
        calls.append(dict(kwargs))
        return [{"chapter_number": 3, "event_summary": "语义路径"}]

    ctx.search_relevant_history = _search

    result = ctx.search_relevant_history_sync(
        query="纸灰",
        current_chapter=5,
        lookback=3,
        top_k=2,
        min_relevance=0.4,
    )

    assert calls == [
        {
            "query": "纸灰",
            "current_chapter": 5,
            "lookback": 3,
            "top_k": 2,
            "min_relevance": 0.4,
        }
    ]
    assert result == [{"chapter_number": 3, "event_summary": "语义路径"}]


class _TemporalEpisodicMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, int]] = []

    def search_by_temporal(self, *, start_chapter: int, end_chapter: int):
        self.calls.append({"start_chapter": start_chapter, "end_chapter": end_chapter})
        return [
            SimpleNamespace(
                chapter_number=4,
                scene_index=2,
                event_summary="纸灰落进水里。",
                characters_involved=["林远", "周岚", "守夜人", "旁观者", "船夫", "更多"],
                timestamp_in_story="夜",
            ),
            SimpleNamespace(
                chapter_number=2,
                scene_index=1,
                event_summary="旧城早餐。",
                characters_involved=["周岚"],
                timestamp_in_story="晨",
            ),
        ]


async def test_search_relevant_history_sync_uses_temporal_bm25_inside_running_loop() -> None:
    episodic = _TemporalEpisodicMemory()
    ctx = MemoryContext()
    ctx._episodic_memory = episodic

    async def _semantic_path_must_not_run(**_: object) -> list[dict[str, object]]:
        raise AssertionError("semantic path should not run inside an active loop")

    ctx.search_relevant_history = _semantic_path_must_not_run

    result = ctx.search_relevant_history_sync(
        query="纸灰 水里",
        current_chapter=6,
        lookback=5,
        top_k=1,
        min_relevance=0.0,
    )

    assert episodic.calls == [{"start_chapter": 1, "end_chapter": 5}]
    assert result == [
        {
            "chapter_number": 4,
            "event_summary": "纸灰落进水里。",
            "scene_index": 2,
            "relevance_score": result[0]["relevance_score"],
            "characters_involved": ["林远", "周岚", "守夜人", "旁观者", "船夫"],
            "timestamp_in_story": "夜",
            "bm25_score": result[0]["bm25_score"],
        }
    ]
    assert result[0]["relevance_score"] > 0
    assert result[0]["bm25_score"] > 0

    assert search_relevant_history_temporal_fallback(
        episodic_memory=_TemporalEpisodicMemory(),
        query="纸灰 水里",
        current_chapter=6,
        lookback=5,
        top_k=1,
        min_relevance=0.0,
    ) == result
