from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    CharacterState,
    CharacterStateDelta,
    PlotThreadDelta,
    PlotThreadState,
    RelationshipState,
    RelationshipStateDelta,
)
from novel_forge.pipeline.long.stages.finalize import _normalize_outcome_chapter_number
from novel_forge.story_kernel.schemas import TimelineAnchor


def test_normalize_outcome_chapter_number_returns_copy_without_mutating_input() -> None:
    outcome = ChapterOutcome(
        source_chapter=99,
        chapter_exit_state=ChapterExitState(chapter_number=99),
        new_events=[
            TimelineAnchor(anchor_id="event-1", chapter=99, event="错章事件"),
        ],
        character_state_deltas=[
            CharacterStateDelta(
                name="阿宁",
                to_state=CharacterState(name="阿宁", last_seen_chapter=99),
            )
        ],
        relationship_deltas=[
            RelationshipStateDelta(
                pair_id="阿宁__阿远",
                relationship=RelationshipState(
                    pair_id="阿宁__阿远",
                    characters=["阿宁", "阿远"],
                    last_updated_chapter=99,
                ),
            )
        ],
        plot_thread_deltas=[
            PlotThreadDelta(
                thread_id="thread-1",
                thread=PlotThreadState(
                    thread_id="thread-1",
                    title="旧约",
                    last_touched_chapter=99,
                ),
            )
        ],
    )
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(_on_step=lambda step, payload: events.append((step, payload)))

    normalized = _normalize_outcome_chapter_number(runner, outcome, 3)

    assert normalized is not outcome
    assert normalized.source_chapter == 3
    assert normalized.chapter_exit_state.chapter_number == 3
    assert normalized.new_events[0].chapter == 3
    assert normalized.character_state_deltas[0].to_state.last_seen_chapter == 3
    assert normalized.relationship_deltas[0].relationship.last_updated_chapter == 3
    assert normalized.plot_thread_deltas[0].thread.last_touched_chapter == 3

    assert outcome.source_chapter == 99
    assert outcome.chapter_exit_state.chapter_number == 99
    assert outcome.new_events[0].chapter == 99
    assert outcome.character_state_deltas[0].to_state.last_seen_chapter == 99
    assert outcome.relationship_deltas[0].relationship.last_updated_chapter == 99
    assert outcome.plot_thread_deltas[0].thread.last_touched_chapter == 99
    assert events[0][0] == "extract_canon_normalized"
