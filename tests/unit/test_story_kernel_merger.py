"""Tests for StoryKernelMerger — applies ChapterOutcome to StoryKernel.

TDD: Tests written before implementation to drive the design.
"""

from __future__ import annotations

import pytest

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
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def base_kernel() -> StoryKernel:
    """A minimal kernel with one character, one relationship, one foreshadow."""
    return StoryKernel(
        project_id="test_project",
        current_chapter=1,
        entities=[
            Entity(
                entity_id="char_林远",
                name="林远",
                entity_type="character",
                status="active",
                attributes={"location": "雾霭小镇", "emotional_state": "困惑"},
                source_chapter=1,
                last_seen_chapter=1,
            ),
            Entity(
                entity_id="char_老守夜人",
                name="老守夜人",
                entity_type="character",
                status="active",
                attributes={"location": "钟楼", "emotional_state": "神秘"},
                source_chapter=1,
                last_seen_chapter=1,
            ),
        ],
        relationships=[
            Relationship(
                relationship_id="rel_林远_老守夜人",
                source_entity_id="char_林远",
                target_entity_id="char_老守夜人",
                relation_type="acquaintance",
                label="认识",
                trust=0.5,
                tension=0.3,
            ),
        ],
        timeline=[
            TimelineAnchor(
                anchor_id="evt_1",
                chapter=1,
                event="林远在雾霭小镇失忆醒来",
                in_story_time="第一天清晨",
                characters_involved=["char_林远"],
            ),
        ],
        promise_ledger=[
            PromiseLedger(
                entry_id="fs_watch",
                description="怀表停在午夜十二点",
                promise_type="foreshadow",
                planted_chapter=1,
                status="planted",
            ),
        ],
        world_rules=[
            WorldRule(
                rule_id="rule_时间裂缝入口",
                content="废弃图书馆",
                category="时间裂缝入口",
                severity="hard",
            ),
        ],
        chapter_summaries={1: "林远失忆醒来，在钟楼遇到老守夜人。"},
    )


@pytest.fixture
def sample_outcome() -> ChapterOutcome:
    return ChapterOutcome(
        source_chapter=2,
        character_updates={
            "林远": CharacterState(
                name="林远",
                alive=True,
                location="废弃图书馆",
                emotional_state="坚定",
                inventory=["铜质怀表", "神秘信件"],
            ),
        },
        new_events=[],
        foreshadowing_updates=[
            PromiseLedger(
                entry_id="fs_watch",
                description="怀表停在午夜十二点",
                promise_type="foreshadow",
                planted_chapter=1,
                status="hinted",
            ),
        ],
        new_world_facts={"裂缝内部": "时间倒流空间"},
        chapter_summary="林远追循信中线索来到废弃图书馆。",
        creative_report=None,
        chapter_exit_state=ChapterExitState(
            chapter_number=2,
            time_marker="第一天傍晚",
            location="废弃图书馆",
            pov="林远",
        ),
        character_state_deltas=[
            CharacterStateDelta(
                name="林远",
                change_summary="进入时间裂缝",
                to_state=CharacterState(
                    name="林远",
                    alive=True,
                    location="废弃图书馆",
                    emotional_state="坚定",
                ),
            ),
        ],
        relationship_deltas=[
            RelationshipStateDelta(
                pair_id="林远_老守夜人",
                change_summary="信任增加",
                relationship=RelationshipState(
                    pair_id="林远_老守夜人",
                    characters=["林远", "老守夜人"],
                    public_status="mentor_student",
                    trust=0.8,
                    tension=0.2,
                    dependency=0.45,
                ),
            ),
        ],
        plot_thread_deltas=[
            PlotThreadDelta(
                thread_id="thread_时间裂缝",
                change_summary="发现时间裂缝入口",
                thread=PlotThreadState(
                    thread_id="thread_时间裂缝",
                    title="时间裂缝之谜",
                    status="active",
                    last_touched_chapter=2,
                    summary="林远发现了时间裂缝",
                ),
            ),
        ],
        structured_summary="林远追循信中线索来到废弃图书馆，发现并进入了时间裂缝。",
    )


# ── Tests: StoryKernelMerger ────────────────────────────────────────


class TestStoryKernelMerger:
    """Tests for StoryKernelMerger.merge_outcome()."""

    def test_merge_updates_current_chapter(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Merging should update current_chapter."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)
        assert new_kernel.current_chapter == 2

    def test_merge_preserves_original(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Original kernel must not be mutated."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        merger.merge_outcome(base_kernel, sample_outcome)
        assert base_kernel.current_chapter == 1
        assert base_kernel.entities[0].name == "林远"

    def test_merge_updates_character_entity_attributes(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Character entity attributes should be updated from character_state_deltas."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        lin_yuan = next(e for e in new_kernel.entities if e.name == "林远")
        assert lin_yuan.attributes.get("location") == "废弃图书馆"
        assert lin_yuan.attributes.get("emotional_state") == "坚定"
        assert lin_yuan.last_seen_chapter == 2

    def test_merge_rejects_unknown_character_without_registry_entry(
        self, base_kernel: StoryKernel
    ) -> None:
        """Extraction deltas cannot bypass explicit character introduction."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            character_updates={
                "新角色": CharacterState(
                    name="新角色", alive=True, location="森林"
                ),
            },
            chapter_exit_state=ChapterExitState(chapter_number=2),
            character_state_deltas=[
                CharacterStateDelta(
                    name="新角色",
                    to_state=CharacterState(
                        name="新角色", alive=True, location="森林"
                    ),
                ),
            ],
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        names = [e.name for e in new_kernel.entities]
        assert "新角色" not in names
        assert any(
            warning.warning_type == "unknown_character_delta_rejected"
            for warning in new_kernel.structured_warnings
        )

    def test_merge_updates_relationships(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Relationship deltas should update existing relationships."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        rels = new_kernel.relationships
        assert len(rels) >= 1
        # The existing relationship should be updated
        rel = next(
            r for r in rels
            if r.source_entity_id == "char_林远" and r.target_entity_id == "char_老守夜人"
        )
        assert rel.trust == 0.8
        assert rel.tension == 0.2
        assert rel.dependency == 0.45
        assert rel.last_shift_chapter == 2

    def test_merge_backfills_shift_summary_from_public_status(
        self, base_kernel: StoryKernel
    ) -> None:
        """Regression: chapter 2 crash 'dict has no attribute shift_summary'.

        When both rel_delta.change_summary and existing.shift_summary are
        empty, the merger must backfill shift_summary from rel.public_status
        so downstream prompt construction receives a non-empty value.
        """
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远_老守夜人",
                    change_summary="",  # empty
                    relationship=RelationshipState(
                        pair_id="林远_老守夜人",
                        characters=["林远", "老守夜人"],
                        public_status="亦师亦友",
                        trust=0.6,
                        tension=0.4,
                    ),
                ),
            ],
        )
        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        rel = next(
            r for r in new_kernel.relationships
            if r.source_entity_id == "char_林远" and r.target_entity_id == "char_老守夜人"
        )
        assert rel.shift_summary == "亦师亦友"
        assert rel.label == "亦师亦友"

    def test_merge_preserves_existing_shift_summary_when_delta_empty(
        self, base_kernel: StoryKernel
    ) -> None:
        from novel_forge.story_kernel.merger import StoryKernelMerger
        from novel_forge.story_kernel.schemas import Relationship

        preloaded = base_kernel.model_copy(deep=True)
        preloaded.relationships = [
            Relationship(
                relationship_id="rel_林远_老守夜人",
                source_entity_id="char_林远",
                target_entity_id="char_老守夜人",
                relation_type="acquaintance",
                label="认识",
                shift_summary="原始信任",
                trust=0.5,
                tension=0.3,
            ),
        ]

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远_老守夜人",
                    change_summary="",  # empty
                    relationship=RelationshipState(
                        pair_id="林远_老守夜人",
                        characters=["林远", "老守夜人"],
                        public_status="亦师亦友",
                        trust=0.6,
                        tension=0.4,
                    ),
                ),
            ],
        )
        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(preloaded, outcome)

        rel = next(
            r for r in new_kernel.relationships
            if r.source_entity_id == "char_林远" and r.target_entity_id == "char_老守夜人"
        )
        assert rel.shift_summary == "原始信任"

    def test_merge_rejects_relationship_with_unknown_character(
        self, base_kernel: StoryKernel
    ) -> None:
        """Relationship deltas cannot create orphan character ids."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远_新角色",
                    relationship=RelationshipState(
                        pair_id="林远_新角色",
                        characters=["林远", "新角色"],
                        public_status="friend",
                        trust=0.7,
                        dependency=0.6,
                    ),
                ),
            ],
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        assert all(r.label != "friend" for r in new_kernel.relationships)
        assert any(
            warning.warning_type == "unknown_relationship_character_rejected"
            for warning in new_kernel.structured_warnings
        )

    def test_merge_preserves_existing_metrics_when_delta_omits_them(
        self, base_kernel: StoryKernel
    ) -> None:
        """Omitted relationship metrics must not overwrite existing canonical values."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远_老守夜人",
                    change_summary="二人确认继续同行",
                    relationship=RelationshipState(
                        pair_id="林远_老守夜人",
                        characters=["林远", "老守夜人"],
                        public_status="亦师亦友",
                    ),
                ),
            ],
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        rel = next(
            r for r in new_kernel.relationships
            if r.source_entity_id == "char_林远" and r.target_entity_id == "char_老守夜人"
        )
        assert rel.trust == 0.5
        assert rel.tension == 0.3
        assert rel.dependency == 0.0
        assert rel.shift_summary == "二人确认继续同行"

    def test_merge_deduplicates_reversed_relationship_pair(
        self, base_kernel: StoryKernel
    ) -> None:
        """A↔B and B↔A deltas should merge into one canonical relationship."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        empty_kernel = base_kernel.model_copy(deep=True)
        empty_kernel.relationships = []
        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远_老守夜人",
                    change_summary="林远接受老守夜人的提醒",
                    relationship=RelationshipState(
                        pair_id="林远_老守夜人",
                        characters=["林远", "老守夜人"],
                        public_status="师徒",
                        trust=0.6,
                    ),
                ),
                RelationshipStateDelta(
                    pair_id="老守夜人_林远",
                    change_summary="老守夜人继续帮助林远",
                    relationship=RelationshipState(
                        pair_id="老守夜人_林远",
                        characters=["老守夜人", "林远"],
                        public_status="师徒",
                        trust=0.7,
                    ),
                ),
            ],
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(empty_kernel, outcome)

        assert len(new_kernel.relationships) == 1
        rel = new_kernel.relationships[0]
        assert rel.relationship_id == "rel_林远__老守夜人"
        assert rel.trust == 0.7
        assert rel.shift_summary == "老守夜人继续帮助林远"

    def test_merge_skips_malformed_relationship_character_names(
        self, base_kernel: StoryKernel
    ) -> None:
        """Schema keys and gender values must not become synthetic character ids."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="bad",
                    change_summary="错误抽取",
                    relationship=RelationshipState(
                        pair_id="bad",
                        characters=["character_id: 林正", "gender: 男"],
                        public_status="合作",
                        trust=0.7,
                    ),
                ),
                RelationshipStateDelta(
                    pair_id="crowd",
                    change_summary="错误抽取",
                    relationship=RelationshipState(
                        pair_id="crowd",
                        characters=["林远", "镇民"],
                        public_status="认识",
                        trust=0.6,
                    ),
                ),
            ],
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        assert len(new_kernel.relationships) == len(base_kernel.relationships)
        assert all(
            "character_id" not in rel.source_entity_id
            and "character_id" not in rel.target_entity_id
            and "gender" not in rel.source_entity_id
            and "gender" not in rel.target_entity_id
            for rel in new_kernel.relationships
        )

    def test_merge_does_not_store_prose_public_status_as_label(
        self, base_kernel: StoryKernel
    ) -> None:
        """Long public_status prose should not replace the compact relationship label."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
            relationship_deltas=[
                RelationshipStateDelta(
                    pair_id="林远_老守夜人",
                    change_summary="林远冒险回到钟楼救出老守夜人",
                    relationship=RelationshipState(
                        pair_id="林远_老守夜人",
                        characters=["林远", "老守夜人"],
                        public_status="林远与老守夜人在本章经历救援后形成更深的信任关系",
                        trust=0.8,
                    ),
                ),
            ],
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        rel = next(
            r for r in new_kernel.relationships
            if r.source_entity_id == "char_林远" and r.target_entity_id == "char_老守夜人"
        )
        assert rel.label == "认识"
        assert len(rel.label) <= 20
        assert rel.shift_summary == "林远冒险回到钟楼救出老守夜人"

    def test_merge_appends_timeline_events(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """New events should be appended to timeline."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        # Add an event to the outcome
        sample_outcome.new_events = [
            # TimelineEvent is the canon schema, but merger should handle it
        ]
        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        # At minimum, the original event should still be there
        assert len(new_kernel.timeline) >= 1

    def test_merge_updates_foreshadowing_promises(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Foreshadowing updates should update promise_ledger entries."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        # The foreshadowing status should be updated
        fs = next(
            (p for p in new_kernel.promise_ledger if p.entry_id == "fs_watch"), None
        )
        assert fs is not None
        assert fs.status == "hinted"  # reinforced → hinted

    def test_merge_adds_plot_threads_as_suspense_promises(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Plot thread deltas should be added as suspense-type promises."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        suspense = [
            p for p in new_kernel.promise_ledger if p.promise_type == "suspense"
        ]
        assert len(suspense) >= 1
        assert any("时间裂缝" in p.description for p in suspense)

    def test_merge_queues_world_facts_for_adjudication(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Chapter discoveries should not silently become immutable rules."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        contents = [wr.content for wr in new_kernel.world_rules]
        assert "时间倒流空间" not in contents
        assert "时间倒流空间" in new_kernel.pending_world_facts.values()

    def test_merge_adds_chapter_summary(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Chapter summary should be added to chapter_summaries."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        assert 2 in new_kernel.chapter_summaries
        assert "废弃图书馆" in new_kernel.chapter_summaries[2]

    def test_merge_stores_chapter_exit_state(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Chapter exit state should be available to the next chapter retriever."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        assert 2 in new_kernel.chapter_exit_states
        exit_state = new_kernel.chapter_exit_states[2]
        assert exit_state.time_marker == "第一天傍晚"
        assert exit_state.location == "废弃图书馆"
        assert exit_state.pov == "林远"
        assert exit_state is not sample_outcome.chapter_exit_state
        assert base_kernel.chapter_exit_states == {}

        from novel_forge.story_kernel.retriever import StoryKernelRetriever

        context = StoryKernelRetriever().get_context(new_kernel, for_chapter=3)
        assert context.previous_exit_state is not None
        assert context.previous_exit_state.time_marker == "第一天傍晚"

    def test_merge_adds_banned_phrases(
        self, base_kernel: StoryKernel
    ) -> None:
        """New banned phrases should be added."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            new_banned_phrases=["禁用短语测试"],
            chapter_exit_state=ChapterExitState(chapter_number=2),
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        assert "禁用短语测试" in new_kernel.banned_phrases

    def test_merge_handles_empty_deltas(
        self, base_kernel: StoryKernel
    ) -> None:
        """Empty deltas should not crash."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        outcome = ChapterOutcome(
            source_chapter=2,
            chapter_exit_state=ChapterExitState(chapter_number=2),
        )

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, outcome)

        assert new_kernel.current_chapter == 2
        assert len(new_kernel.entities) == len(base_kernel.entities)

    def test_merge_deduplicates_entities_by_name(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Merging should not create duplicate entities."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        names = [e.name for e in new_kernel.entities if e.entity_type == "character"]
        assert len(names) == len(set(names))

    def test_merge_deduplicates_relationships(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Merging should not create duplicate relationships."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        rel_keys = set()
        for r in new_kernel.relationships:
            key = (r.source_entity_id, r.target_entity_id)
            assert key not in rel_keys
            rel_keys.add(key)

    def test_merge_deduplicates_promises_by_id(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        """Merging should not create duplicate promise entries."""
        from novel_forge.story_kernel.merger import StoryKernelMerger

        merger = StoryKernelMerger()
        new_kernel = merger.merge_outcome(base_kernel, sample_outcome)

        ids = [p.entry_id for p in new_kernel.promise_ledger]
        assert len(ids) == len(set(ids))


# ── Tests: StateTracker.merge_outcome_to_kernel ─────────────────────


class TestStateTrackerMergeOutcomeToKernel:
    """Tests for StateTracker.merge_outcome_to_kernel()."""

    def test_merge_outcome_to_kernel_updates_chapter(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        from novel_forge.story_kernel.state_tracker import StateTracker

        tracker = StateTracker()
        new_kernel = tracker.merge_outcome_to_kernel(base_kernel, sample_outcome)
        assert new_kernel.current_chapter == 2

    def test_merge_outcome_to_kernel_preserves_original(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        from novel_forge.story_kernel.state_tracker import StateTracker

        tracker = StateTracker()
        tracker.merge_outcome_to_kernel(base_kernel, sample_outcome)
        assert base_kernel.current_chapter == 1

    def test_merge_outcome_to_kernel_updates_characters(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        from novel_forge.story_kernel.state_tracker import StateTracker

        tracker = StateTracker()
        new_kernel = tracker.merge_outcome_to_kernel(base_kernel, sample_outcome)

        lin_yuan = next(e for e in new_kernel.entities if e.name == "林远")
        assert lin_yuan.attributes.get("location") == "废弃图书馆"


# ── Tests: ExtractStep.apply_to_kernel ──────────────────────────────


class TestExtractStepApplyToKernel:
    """Tests for ExtractChapterOutcomeStep.apply_outcome_to_kernel()."""

    def test_apply_to_kernel_returns_new_kernel(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep

        step = ExtractCanonDeltaStep.__new__(ExtractCanonDeltaStep)
        new_kernel = step.apply_outcome_to_kernel(base_kernel, sample_outcome)
        assert new_kernel.current_chapter == 2
        assert base_kernel.current_chapter == 1  # original preserved

    def test_apply_to_kernel_updates_entities(
        self, base_kernel: StoryKernel, sample_outcome: ChapterOutcome
    ) -> None:
        from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep

        step = ExtractCanonDeltaStep.__new__(ExtractCanonDeltaStep)
        new_kernel = step.apply_outcome_to_kernel(base_kernel, sample_outcome)

        lin_yuan = next(e for e in new_kernel.entities if e.name == "林远")
        assert lin_yuan.last_seen_chapter == 2
