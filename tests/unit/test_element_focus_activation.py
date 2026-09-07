"""Tests for chapter-level active narrative element focus resolution."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.pipeline.long.services.element_progress import record_schedule
from novel_forge.pipeline.long.stages.draft import (
    _resolve_active_element_focus_ids,
)


def test_outline_focus_wins_over_scheduled_dynamic_focus(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_focus_outline_wins"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=3,
        scheduled_ids=["dynamic_a", "dynamic_b"],
        focus_source="dynamic",
    )
    outline = SimpleNamespace(
        element_focus=["outline_a", "outline_a", "outline_b", "outline_c", "outline_d"]
    )

    focus_ids, source = _resolve_active_element_focus_ids(
        outline,
        tmp_storage,
        layout,
        chapter_number=3,
    )

    assert focus_ids == ["outline_a", "outline_b", "outline_c"]
    assert source == "outline"


def test_dynamic_schedule_reaches_draft_when_outline_focus_empty(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_focus_dynamic_schedule"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=4,
        scheduled_ids=["dynamic_a", "dynamic_b", "dynamic_c", "dynamic_d"],
        focus_source="dynamic",
    )
    outline = SimpleNamespace(element_focus=[])

    focus_ids, source = _resolve_active_element_focus_ids(
        outline,
        tmp_storage,
        layout,
        chapter_number=4,
    )

    assert focus_ids == ["dynamic_a", "dynamic_b", "dynamic_c"]
    assert source == "dynamic"


def test_stage_cards_include_only_active_focus_elements() -> None:
    element_selection = {
        "extension_elements": [
            {
                "element_id": "dynamic_a",
                "name": "动态聚焦要素",
                "prompt_hint": "用场景动作自然体现",
            },
            {
                "element_id": "inactive_b",
                "name": "未激活扩展要素",
                "prompt_hint": "不应生成要求",
            },
        ]
    }

    cards = build_stage_cards(
        stage="draft",
        element_selection=element_selection,
        element_focus=["dynamic_a"],
    )

    focused = cards["element"]["focused_extension_elements"]
    assert len(focused) == 1
    assert focused[0]["name"] == "动态聚焦要素"
    assert focused[0]["prompt_hint"] == "用场景动作自然体现"
    assert all(item["name"] != "未激活扩展要素" for item in focused)
