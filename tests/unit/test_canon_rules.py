"""Tests for StoryKernelConsistencyRules."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.story_state import CharacterState
from novel_forge.story_kernel.rules import StoryKernelConsistencyRules
from novel_forge.story_kernel.schemas import PromiseLedger, StoryKernel, TimelineAnchor


class TestConsistencyRules:
    def setup_method(self) -> None:
        self.rules = StoryKernelConsistencyRules()

    def test_valid_delta_passes(
        self,
        sample_canon_state: StoryKernel,
        sample_canon_delta: ChapterOutcome,
    ) -> None:
        result = self.rules.validate(sample_canon_state, sample_canon_delta)
        assert result.violations == []
        assert result.warnings == []

    def test_dead_character_resurrection(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        delta = ChapterOutcome(
            source_chapter=2,
            character_updates={
                "已故角色": CharacterState(
                    name="已故角色",
                    alive=True,
                    location="somewhere",
                ),
            },
        )
        result = self.rules.validate(sample_canon_state, delta)
        assert any("resurrected" in v for v in result.violations)

    def test_dead_character_mention_allowed(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        delta = ChapterOutcome(
            source_chapter=2,
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_2a",
                    chapter=2,
                    event="主角发现了已故角色的遗物和日记",
                    characters_involved=["已故角色", "林远"],
                ),
                TimelineAnchor(
                    anchor_id="evt_2b",
                    chapter=2,
                    event="已故角色的魂魄出现警告主角",
                    characters_involved=["已故角色"],
                ),
            ],
        )
        result = self.rules.validate(sample_canon_state, delta)
        assert not any("已故角色" in v for v in result.violations)

    def test_timeline_monotonicity(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        delta = ChapterOutcome(
            source_chapter=2,
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_bad",
                    chapter=1,
                    event="回到过去的事件",
                    characters_involved=["林远"],
                ),
            ],
        )
        result = self.rules.validate(sample_canon_state, delta)
        assert any("chapter 1" in v for v in result.violations)

    def test_foreshadowing_invalid_transition(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        sample_canon_state.promise_ledger.append(
            PromiseLedger(
                entry_id="fs_resolved",
                description="已揭示的伏笔",
                planted_chapter=1,
                status="paid",
            )
        )
        delta = ChapterOutcome(
            source_chapter=2,
            foreshadowing_updates=[
                PromiseLedger(
                    entry_id="fs_resolved",
                    description="已揭示的伏笔",
                    planted_chapter=1,
                    status="planted",
                ),
            ],
        )
        result = self.rules.validate(sample_canon_state, delta)
        assert any("invalid transition" in v for v in result.violations)

    def test_chapter_sequence(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        delta = ChapterOutcome(source_chapter=5)
        result = self.rules.validate(sample_canon_state, delta)
        assert any("Expected chapter 2" in v for v in result.violations)

    def test_valid_foreshadowing_transition(
        self,
        sample_canon_state: StoryKernel,
    ) -> None:
        delta = ChapterOutcome(
            source_chapter=2,
            foreshadowing_updates=[
                PromiseLedger(
                    entry_id="fs_watch",
                    description="怀表停在午夜十二点",
                    planted_chapter=1,
                    status="paid",
                    payoff_chapter=2,
                ),
            ],
        )
        result = self.rules.validate(sample_canon_state, delta)
        assert result.violations == []
