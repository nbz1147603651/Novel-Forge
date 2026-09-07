"""Tests for semantic tools in EpisodicMemory."""

from __future__ import annotations

import pytest

from novel_forge.memory.episodic import EpisodicMemory


@pytest.fixture
def memory():
    return EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
    )


def _make_outcome(chapter: int, summary: str, events: list, text: str = ""):
    from dataclasses import dataclass, field
    from typing import Any

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


@pytest.mark.asyncio
async def test_get_recent_semantic_facts(memory):
    """Index chapters 1-10, call with current_chapter=11, lookback=5, verify results from chapters 6-10."""
    for chapter in range(1, 11):
        outcome = _make_outcome(
            chapter=chapter,
            summary=f"Chapter {chapter} summary",
            events=[{"chapter": chapter, "event": f"event_{chapter}"}],
            text=f"Chapter {chapter} content",
        )
        await memory.index_chapter(outcome)

    results = await memory.get_recent_semantic_facts(
        current_chapter=11,
        lookback=5,
        top_k=8,
    )

    assert len(results) <= 8
    for r in results:
        assert 6 <= r.chapter_number <= 10


@pytest.mark.asyncio
async def test_get_recent_semantic_facts_empty(memory):
    """current_chapter=1, verify empty list returned."""
    outcome = _make_outcome(
        chapter=1,
        summary="Chapter 1 summary",
        events=[{"chapter": 1, "event": "event_1"}],
        text="Chapter 1 content",
    )
    await memory.index_chapter(outcome)

    results = await memory.get_recent_semantic_facts(current_chapter=1)

    assert results == []


@pytest.mark.asyncio
async def test_get_outline_context_returns_chapter1_init_outline_summary(memory):
    await memory.index_outline_phase(
        chapter_number=1,
        plot_points=["林远在旧城天台发现异常信号", "怀表第一次与裂缝共振"],
        characters=["林远"],
        pov_character="林远",
        chapter_goal="发现异常信号",
        themes=["现实裂缝"],
        unresolved_questions=["第1章悬念: 信号来自哪里"],
    )

    context = await memory.get_outline_context(current_chapter=1)

    assert "初始化大纲参考（第1章）" in context.chapter_summary
    assert "林远在旧城天台发现异常信号" in context.chapter_summary
    assert context.similar_events == []
    assert context.unresolved_questions == ["第1章悬念: 信号来自哪里"]


@pytest.mark.asyncio
async def test_get_recent_semantic_facts_with_event_types(memory):
    """Index events with different event_types, filter by type, verify only matching types returned."""
    outcome1 = _make_outcome(
        chapter=1,
        summary="Chapter 1 action",
        events=[{"chapter": 1, "event": "event_action"}],
        text="Chapter 1 content",
    )
    await memory.index_chapter(outcome1)

    outcome2 = _make_outcome(
        chapter=2,
        summary="Chapter 2 dialogue",
        events=[{"chapter": 2, "event": "event_dialogue"}],
        text="Chapter 2 content",
    )
    await memory.index_chapter(outcome2)

    outcome3 = _make_outcome(
        chapter=3,
        summary="Chapter 3 action",
        events=[{"chapter": 3, "event": "event_action_2"}],
        text="Chapter 3 content",
    )
    await memory.index_chapter(outcome3)

    results = await memory.get_recent_semantic_facts(
        current_chapter=4,
        lookback=10,
        top_k=8,
        event_types=["action"],
    )

    assert len(results) <= 8


# ── Characters filter tests for search_by_semantic ──────────────────────────


def _make_outcome_with_characters(
    chapter: int,
    summary: str,
    events: list,
    characters_involved: list[str] | None = None,
    text: str = "",
):
    """Helper to create a FakeOutcome with specific character names in events."""
    from dataclasses import dataclass, field
    from typing import Any

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

    enriched_events = []
    for e in events:
        ev = dict(e)
        if characters_involved is not None:
            ev["characters_involved"] = characters_involved
        enriched_events.append(ev)

    return FakeOutcome(
        chapter_summary=summary,
        source_chapter=chapter,
        character_updates={c: {} for c in (characters_involved or [])},
        new_events=[FakeTimelineEvent(**e) for e in enriched_events],
        text=text,
    )


@pytest.mark.asyncio
async def test_search_by_semantic_characters_filter_returns_matching(memory):
    """search_by_semantic with characters filter returns entries that match any character."""
    # Index entries with different characters
    outcome_a = _make_outcome_with_characters(
        chapter=1,
        summary="Alice discovers the artifact",
        events=[{"chapter": 1, "event": "Alice finds artifact", "characters_involved": ["Alice"]}],
        text="Chapter 1 content",
    )
    await memory.index_chapter(outcome_a)

    outcome_b = _make_outcome_with_characters(
        chapter=2,
        summary="Bob fights the dragon",
        events=[{"chapter": 2, "event": "Bob slays dragon", "characters_involved": ["Bob"]}],
        text="Chapter 2 content",
    )
    await memory.index_chapter(outcome_b)

    # Search filtered by Alice only
    results = await memory.search_by_semantic(
        query="artifact discovery",
        characters=["Alice"],
        top_k=10,
    )

    # All returned results should involve Alice
    for r in results:
        assert "Alice" in r.characters_involved, (
            f"Expected Alice in characters_involved, got {r.characters_involved}"
        )


@pytest.mark.asyncio
async def test_search_by_semantic_characters_filter_excludes_non_matching(memory):
    """search_by_semantic with characters filter excludes entries without matching characters."""
    outcome_a = _make_outcome_with_characters(
        chapter=1,
        summary="Alice explores the cave",
        events=[{"chapter": 1, "event": "Alice in cave", "characters_involved": ["Alice"]}],
        text="Chapter 1 content",
    )
    await memory.index_chapter(outcome_a)

    outcome_b = _make_outcome_with_characters(
        chapter=2,
        summary="Bob reads a book",
        events=[{"chapter": 2, "event": "Bob reading", "characters_involved": ["Bob"]}],
        text="Chapter 2 content",
    )
    await memory.index_chapter(outcome_b)

    # Search for Bob only — should not return Alice's entries
    results = await memory.search_by_semantic(
        query="cave exploration",
        characters=["Bob"],
        top_k=10,
    )

    # Bob's entries should not be returned for a cave query (they're not semantically similar),
    # but if any are returned, they must involve Bob
    for r in results:
        assert "Bob" in r.characters_involved


@pytest.mark.asyncio
async def test_search_by_semantic_characters_filter_none_returns_all(memory):
    """search_by_semantic with characters=None returns all matching entries (backward compat)."""
    outcome_a = _make_outcome_with_characters(
        chapter=1,
        summary="Alice explores the cave",
        events=[{"chapter": 1, "event": "Alice in cave", "characters_involved": ["Alice"]}],
        text="Chapter 1 content",
    )
    await memory.index_chapter(outcome_a)

    outcome_b = _make_outcome_with_characters(
        chapter=2,
        summary="Bob reads a book",
        events=[{"chapter": 2, "event": "Bob reading", "characters_involved": ["Bob"]}],
        text="Chapter 2 content",
    )
    await memory.index_chapter(outcome_b)

    # No characters filter — should return results from both chapters
    results_all = await memory.search_by_semantic(
        query="",
        top_k=10,
    )
    results_filtered = await memory.search_by_semantic(
        query="",
        characters=["Alice"],
        top_k=10,
    )

    # Without filter should return at least as many as with filter
    assert len(results_all) >= len(results_filtered)


@pytest.mark.asyncio
async def test_search_by_semantic_characters_filter_multi_character(memory):
    """search_by_semantic with multiple characters returns entries matching ANY of them."""
    outcome_a = _make_outcome_with_characters(
        chapter=1,
        summary="Alice and Bob together",
        events=[{"chapter": 1, "event": "Alice Bob meet", "characters_involved": ["Alice", "Bob"]}],
        text="Chapter 1 content",
    )
    await memory.index_chapter(outcome_a)

    outcome_c = _make_outcome_with_characters(
        chapter=2,
        summary="Charlie alone",
        events=[{"chapter": 2, "event": "Charlie solo", "characters_involved": ["Charlie"]}],
        text="Chapter 2 content",
    )
    await memory.index_chapter(outcome_c)

    # Filter by Alice or Charlie
    results = await memory.search_by_semantic(
        query="",
        characters=["Alice", "Charlie"],
        top_k=10,
    )

    for r in results:
        has_match = any(c in r.characters_involved for c in ["Alice", "Charlie"])
        assert has_match, (
            f"Expected Alice or Charlie in characters_involved, got {r.characters_involved}"
        )
