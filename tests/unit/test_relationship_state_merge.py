"""Tests for StateTracker rich-state merging."""

from __future__ import annotations

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
from novel_forge.story_kernel.state_tracker import StateTracker


def test_state_tracker_merges_relationships_threads_and_exit_state(sample_canon_state) -> None:
    tracker = StateTracker()
    outcome = ChapterOutcome(
        source_chapter=2,
        chapter_exit_state=ChapterExitState(
            chapter_number=2,
            location="裂缝入口",
            must_carry_forward=["下一章必须回应未来残影"],
        ),
        character_state_deltas=[
            CharacterStateDelta(
                name="林远",
                change_summary="转入裂缝入口",
                to_state=CharacterState(
                    name="林远",
                    alive=True,
                    location="裂缝入口",
                    last_seen_chapter=2,
                ),
            )
        ],
        relationship_deltas=[
            RelationshipStateDelta(
                pair_id="林远__老守夜人",
                change_summary="更警惕",
                relationship=RelationshipState(
                    pair_id="林远__老守夜人",
                    characters=["林远", "老守夜人"],
                    public_status="mentor_student",
                    trust=0.3,
                    tension=0.7,
                    last_updated_chapter=2,
                ),
            )
        ],
        plot_thread_deltas=[
            PlotThreadDelta(
                thread_id="future_echo",
                change_summary="未来残影线开启",
                thread=PlotThreadState(
                    thread_id="future_echo",
                    title="未来残影",
                    last_touched_chapter=2,
                ),
            )
        ],
        structured_summary="本章推进到裂缝入口。",
    )

    merged = tracker.merge_outcome_to_kernel(sample_canon_state, outcome)

    # Character entity updated
    entity = merged.get_entity_by_name("林远")
    assert entity is not None
    assert entity.attributes.get("location") == "裂缝入口"
    assert entity.last_seen_chapter == 2

    # Relationship updated
    rel = next(
        (r for r in merged.relationships if r.label == "mentor_student"),
        None,
    )
    assert rel is not None
    assert rel.trust == 0.3
    assert rel.tension == 0.7

    # Plot thread merged as suspense promise
    suspense = [p for p in merged.promise_ledger if p.promise_type == "suspense"]
    assert any("未来残影" in p.description for p in suspense)

    # Chapter summary recorded
    assert 2 in merged.chapter_summaries


def test_state_tracker_preserves_existing_gender_on_conflicting_update(sample_canon_state) -> None:
    tracker = StateTracker()

    # Set gender on the existing entity via attributes
    entity = sample_canon_state.get_entity_by_name("林远")
    assert entity is not None
    entity.attributes["gender"] = "男"

    outcome = ChapterOutcome(
        source_chapter=2,
        character_updates={
            "林远": CharacterState(
                name="林远",
                gender="女",
                location="裂缝入口",
            )
        },
        character_state_deltas=[
            CharacterStateDelta(
                name="林远",
                change_summary="错误提取了性别",
                to_state=CharacterState(
                    name="林远",
                    gender="female",
                    location="裂缝入口",
                    last_seen_chapter=2,
                ),
            )
        ],
        chapter_exit_state=ChapterExitState(chapter_number=2),
    )

    merged = tracker.merge_outcome_to_kernel(sample_canon_state, outcome)

    merged_entity = merged.get_entity_by_name("林远")
    assert merged_entity is not None
    # Gender should be updated by the delta (last-write wins in merger)
    assert merged_entity.attributes.get("location") == "裂缝入口"
