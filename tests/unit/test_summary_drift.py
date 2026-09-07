"""Tests for deterministic summary drift checks."""

from __future__ import annotations

from novel_forge.pipeline.long.services.summary_drift import check_summary_drift
from novel_forge.story_kernel.schemas import Entity, StoryKernel


def _kernel_with_dead_character() -> StoryKernel:
    return StoryKernel(
        project_id="drift-demo",
        entities=[
            Entity(
                entity_id="char_lin",
                name="林照",
                entity_type="character",
                attributes={"status": "dead"},
            )
        ],
    )


def test_summary_drift_flags_missing_dead_character_name() -> None:
    result = check_summary_drift(
        _kernel_with_dead_character(),
        volume_summary="本卷收束了码头追逃，众人暂时脱险。",
        chapter_summaries={1: "林照在码头牺牲。"},
        volume_start=1,
        volume_end=3,
    )

    assert result.has_critical is True
    assert result.facts_missing == 1
    assert result.issues[0].summary == "角色死亡未提及: 林照"


def test_summary_drift_accepts_dead_character_when_name_is_in_summary() -> None:
    result = check_summary_drift(
        _kernel_with_dead_character(),
        volume_summary="林照在码头牺牲后，众人带着他的线索继续追查。",
        chapter_summaries={1: "林照在码头牺牲。"},
        volume_start=1,
        volume_end=3,
    )

    assert result.has_critical is False
    assert result.facts_missing == 0
    assert result.issues == []
