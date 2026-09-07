from __future__ import annotations

import inspect

from novel_forge.core.constants import PipelineConstants
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.pipeline.long.services.blueprint.outline_helpers import is_outline_complete
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    _apply_progressive_commitment_boundary,
)
from novel_forge.workspace.planning_horizon import next_planning_horizon_target


def test_new_projects_default_to_full_planning_at_every_entrypoint():
    from novel_forge.api.routes.engine import _LongInitWorkflowPayload
    from novel_forge.app_service.workflow_requests import build_init_long_request
    from novel_forge.pipeline.chapter_runner import ChapterRunner
    from novel_forge.pipeline.long.services.init.init_cache import _build_long_init_request_payload
    from novel_forge.pipeline.long.services.init.init_orchestrator import init_long_project
    from novel_forge.workspace.contracts import InitLongRequest

    assert InitLongRequest(premise="test").planning_commitment == "full"
    assert _LongInitWorkflowPayload(premise="test").planning_commitment == "full"
    for entry in (
        build_init_long_request,
        ChapterRunner.init_long,
        _build_long_init_request_payload,
        init_long_project,
    ):
        assert inspect.signature(entry).parameters["planning_commitment"].default == "full"
    assert (
        InitLongRequest(premise="test", planning_commitment="progressive").planning_commitment
        == "progressive"
    )


def _chapter(number: int, *, placeholder: bool = False) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=number,
        title=f"第{number}节",
        goal=f"推进第{number}章",
        beats_summary=[] if placeholder else [f"第{number}章节拍"],
        notes=PipelineConstants.PLACEHOLDER_NOTE if placeholder else "",
        expected_word_count=3000,
    )


def test_legacy_outline_loads_as_fully_hard() -> None:
    outline = StoryOutline(total_chapters=50, chapters=[_chapter(1)])

    assert outline.hard_through_chapter == 50
    assert outline.planned_through_chapter == 50


def test_progressive_outline_preserves_five_hard_and_ten_planned() -> None:
    outline = StoryOutline(
        total_chapters=50,
        chapters=[_chapter(number) for number in range(1, 11)],
        hard_through_chapter=5,
        planned_through_chapter=10,
    )

    assert outline.hard_through_chapter == 5
    assert outline.planned_through_chapter == 10
    assert is_outline_complete(outline)


def test_future_placeholders_do_not_invalidate_current_planned_window() -> None:
    outline = StoryOutline(
        total_chapters=50,
        chapters=[*[_chapter(number) for number in range(1, 11)], _chapter(11, placeholder=True)],
        hard_through_chapter=5,
        planned_through_chapter=10,
    )

    assert is_outline_complete(outline)


def test_watermarks_are_clamped_and_ordered() -> None:
    outline = StoryOutline(
        total_chapters=10,
        chapters=[_chapter(1)],
        hard_through_chapter=11,
        planned_through_chapter=6,
    )

    assert outline.hard_through_chapter == 10
    assert outline.planned_through_chapter == 10


def test_preview_chapters_do_not_lock_pov_or_required_cast() -> None:
    preview = _chapter(6).model_copy(
        update={
            "pov_character": "甲",
            "pov_character_id": "char:a",
            "pov_character_name": "甲",
            "required_character_ids": ["char:a"],
            "support_character_ids": ["char:b"],
        }
    )

    bounded = _apply_progressive_commitment_boundary([_chapter(5), preview], hard_through_chapter=5)

    assert bounded[0].chapter_number == 5
    assert bounded[1].pov_character_id == ""
    assert bounded[1].required_character_ids == []
    assert bounded[1].cast_plan.required_character_ids == []


def test_horizon_advances_only_when_two_hard_chapters_remain() -> None:
    assert (
        next_planning_horizon_target(total_chapters=50, hard_through_chapter=5, current_chapter=1)
        is None
    )
    assert (
        next_planning_horizon_target(total_chapters=50, hard_through_chapter=5, current_chapter=3)
        == 10
    )
    assert (
        next_planning_horizon_target(total_chapters=50, hard_through_chapter=5, current_chapter=5)
        == 10
    )
    assert (
        next_planning_horizon_target(total_chapters=50, hard_through_chapter=10, current_chapter=6)
        is None
    )
    assert (
        next_planning_horizon_target(total_chapters=50, hard_through_chapter=10, current_chapter=8)
        == 15
    )
    assert (
        next_planning_horizon_target(total_chapters=11, hard_through_chapter=10, current_chapter=10)
        == 11
    )
