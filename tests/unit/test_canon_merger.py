"""Tests for StoryKernelMerger."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import (
    ChapterExitState,
    CharacterState,
    RelationshipState,
    RelationshipStateDelta,
)
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.schemas import Entity, StoryKernel, TimelineAnchor


class TestCanonMerger:
    def test_merge_updates_chapter(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(sample_canon_state, sample_canon_delta)
        assert new_state.current_chapter == 2

    def test_merge_updates_characters(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(sample_canon_state, sample_canon_delta)
        entity = new_state.get_entity_by_name("林远")
        assert entity is not None
        assert entity.attributes.get("location") == "废弃图书馆"

    def test_merge_appends_events(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(sample_canon_state, sample_canon_delta)
        assert len(new_state.timeline) == 2

    def test_merge_updates_foreshadowing(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(sample_canon_state, sample_canon_delta)
        fs = {f.entry_id: f for f in new_state.promise_ledger}
        assert fs["fs_watch"].status == "hinted"

    def test_merge_adds_chapter_summary(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(sample_canon_state, sample_canon_delta)
        assert 2 in new_state.chapter_summaries

    def test_merge_preserves_original(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        merger = StoryKernelMerger()
        merger.merge_outcome(sample_canon_state, sample_canon_delta)
        assert sample_canon_state.current_chapter == 1

    def test_merge_reactivates_archived_entity(self, sample_canon_state: StoryKernel) -> None:
        merger = StoryKernelMerger()
        sample_canon_state.archived_entities.append(
            Entity(entity_id="char_old", name="老守夜人", entity_type="character", status="active")
        )
        sample_canon_state.entities = [
            e for e in sample_canon_state.entities if e.name != "老守夜人"
        ]

        delta = ChapterOutcome(
            source_chapter=2,
            character_updates={"老守夜人": CharacterState(name="老守夜人", alive=True, location="河堤")},
        )
        merged = merger.merge_outcome(sample_canon_state, delta)
        entity_names = [e.name for e in merged.entities]
        assert "老守夜人" in entity_names

    def test_merge_rejects_drifted_character_and_relationship_names(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        merger = StoryKernelMerger()
        known_name = next(
            entity.name
            for entity in sample_canon_state.entities
            if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
        )
        delta = ChapterOutcome(
            source_chapter=2,
            character_updates={"林远远": CharacterState(name="林远远", location="错误地点")},
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远远::林晚",
                    change_summary="错名关系不应落入内核。",
                    relationship=RelationshipState(
                        pair_id="林远远::林晚",
                        characters=["林远远", known_name],
                    ),
                )
            ],
        )

        merged = merger.merge_outcome(sample_canon_state, delta)

        assert merged.get_entity_by_name("林远远") is None
        warning_types = {warning.warning_type for warning in merged.structured_warnings}
        assert "unknown_character_delta_rejected" in warning_types
        assert "unknown_relationship_character_rejected" in warning_types

    def test_merge_sanitizes_drifted_names_from_timeline_and_exit_state(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        merger = StoryKernelMerger()
        known_entity = next(
            entity
            for entity in sample_canon_state.entities
            if str(getattr(entity.entity_type, "value", entity.entity_type)) == "character"
        )
        delta = ChapterOutcome(
            source_chapter=2,
            new_events=[
                TimelineAnchor(
                    anchor_id="raw_event",
                    chapter=2,
                    event="抵达码头。",
                    characters_involved=[known_entity.entity_id, "林远远"],
                )
            ],
            chapter_exit_state=ChapterExitState(
                chapter_number=2,
                pov="林远远",
                character_end_states={
                    known_entity.name: CharacterState(name=known_entity.name),
                    "林远远": CharacterState(name="林远远"),
                },
            ),
        )

        merged = merger.merge_outcome(sample_canon_state, delta)

        assert merged.timeline[-1].characters_involved == [known_entity.entity_id]
        exit_state = merged.chapter_exit_states[2]
        assert exit_state.pov == ""
        assert set(exit_state.character_end_states) == {known_entity.name}
        warning_types = {warning.warning_type for warning in merged.structured_warnings}
        assert "unknown_timeline_character_rejected" in warning_types
        assert "unknown_exit_state_character_rejected" in warning_types
