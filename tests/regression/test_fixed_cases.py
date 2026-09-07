"""Regression tests — ensure known-good inputs always produce consistent results."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.schemas.story_state import CharacterState
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.rules import StoryKernelConsistencyRules
from novel_forge.story_kernel.schemas import StoryKernel, TimelineAnchor

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class TestFixedCases:
    """Deterministic regression checks against fixture data."""

    def test_spec_from_fixture(self) -> None:
        data = json.loads((FIXTURE_DIR / "sample_spec.json").read_text(encoding="utf-8"))
        spec = StorySpec.model_validate(data)
        assert spec.title == "时间裂缝"
        assert spec.genre == "fantasy"
        assert spec.language == "zh"
        assert spec.length_target == 3000

    def test_canon_state_from_fixture(self) -> None:
        data = json.loads(
            (FIXTURE_DIR / "expected_canon.json").read_text(encoding="utf-8")
        )
        state = StoryKernel.model_validate(data)
        assert state.project_id == "regression_test"
        assert state.current_chapter == 1
        entity_names = {e.name for e in state.entities}
        assert "林远" in entity_names
        lin = state.get_entity_by_name("林远")
        assert lin is not None
        assert lin.status == "active"
        assert len(state.timeline) == 2
        assert len(state.promise_ledger) == 1

    def test_canon_merge_deterministic(self) -> None:
        state_data = json.loads(
            (FIXTURE_DIR / "expected_canon.json").read_text(encoding="utf-8")
        )
        state = StoryKernel.model_validate(state_data)

        delta = ChapterOutcome(
            source_chapter=2,
            character_updates={
                "林远": CharacterState(
                    name="林远",
                    alive=True,
                    location="裂缝内部",
                    emotional_state="惊恐",
                ),
            },
            new_events=[],
            foreshadowing_updates=[],
            new_world_facts={"裂缝内部": "时间倒流空间"},
            chapter_summary="林远进入裂缝，体验了时间倒流。",
        )

        merger = StoryKernelMerger()
        new_state = merger.merge_outcome(state, delta)

        assert new_state.current_chapter == 2
        new_lin = new_state.get_entity_by_name("林远")
        assert new_lin is not None
        assert new_lin.attributes.get("location") == "裂缝内部"
        entity_names = {e.name for e in new_state.entities}
        assert "老守夜人" in entity_names
        assert 2 in new_state.chapter_summaries

    def test_consistency_rules_pass_on_valid_delta(self) -> None:
        state_data = json.loads(
            (FIXTURE_DIR / "expected_canon.json").read_text(encoding="utf-8")
        )
        state = StoryKernel.model_validate(state_data)

        delta = ChapterOutcome(
            source_chapter=2,
            character_updates={
                "林远": CharacterState(
                    name="林远",
                    alive=True,
                    location="裂缝内部",
                ),
            },
            new_events=[
                TimelineAnchor(
                    anchor_id="evt_2",
                    chapter=2,
                    event="进入裂缝",
                    characters_involved=["char_林远"],
                    in_story_time="第一天傍晚",
                )
            ],
            foreshadowing_updates=[],
            new_world_facts={},
            chapter_summary="林远进入时间裂缝。",
        )

        rules = StoryKernelConsistencyRules()
        result = rules.validate(state, delta)
        assert result.violations == []
