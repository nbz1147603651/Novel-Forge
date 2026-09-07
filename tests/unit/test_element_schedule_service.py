from __future__ import annotations

from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.element_progress import suggest_dynamic_focus_ids
from novel_forge.pipeline.long.services.element_schedule import (
    build_element_schedule_hint,
    load_element_schedule,
    update_element_schedule_from_progress,
)


def test_build_element_schedule_hint_mandates_due_progressive_element(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_schedule_due"))
    layout.ensure_dirs()

    hint = build_element_schedule_hint(
        tmp_storage,
        layout,
        chapter_number=3,
        extension_ids=["mystery_reveal_order"],
        cards_by_id={
            "mystery_reveal_order": {
                "element_id": "mystery_reveal_order",
                "category": "悬疑推理",
            }
        },
    )

    assert hint is not None
    assert hint["mandated_focus_ids"] == ["mystery_reveal_order"]
    tracker = load_element_schedule(tmp_storage, layout)
    assert tracker.mandated_next_chapters["mystery_reveal_order"] == [3]


def test_update_element_schedule_records_capped_violation(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_schedule_capped"))
    layout.ensure_dirs()
    cards = {
        "comedy_setup_payoff": {
            "element_id": "comedy_setup_payoff",
            "category": "喜剧轻松",
        }
    }

    for chapter_number in (1, 2, 3):
        update_element_schedule_from_progress(
            tmp_storage,
            layout,
            chapter_number=chapter_number,
            progress_entry={
                "scheduled_element_ids": ["comedy_setup_payoff"],
                "results": [
                    {
                        "element_id": "comedy_setup_payoff",
                        "status": "hit",
                    }
                ],
            },
            element_cards_by_id=cards,
        )

    tracker = load_element_schedule(tmp_storage, layout)
    violations = [item.model_dump(mode="json") for item in tracker.schedule_violations]

    assert violations
    assert violations[-1]["element_id"] == "comedy_setup_payoff"
    assert violations[-1]["violation_type"] == "capped_overrun"


def test_update_element_schedule_keeps_missed_mandate_unseen(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_schedule_missed"))
    layout.ensure_dirs()
    cards = {
        "mystery_reveal_order": {
            "element_id": "mystery_reveal_order",
            "category": "悬疑推理",
        }
    }

    build_element_schedule_hint(
        tmp_storage,
        layout,
        chapter_number=3,
        extension_ids=["mystery_reveal_order"],
        cards_by_id=cards,
    )
    entry = update_element_schedule_from_progress(
        tmp_storage,
        layout,
        chapter_number=3,
        progress_entry={
            "scheduled_element_ids": ["mystery_reveal_order"],
            "results": [
                {
                    "element_id": "mystery_reveal_order",
                    "status": "miss",
                }
            ],
        },
        element_cards_by_id=cards,
    )

    tracker = load_element_schedule(tmp_storage, layout)
    assert entry is not None
    assert entry["recorded_element_ids"] == []
    assert entry["violations"][-1]["violation_type"] == "mandated_missing"
    assert tracker.element_curves["mystery_reveal_order"].last_seen_chapter == 0


def test_dynamic_focus_prefers_mandated_before_recommended() -> None:
    class _Outline:
        element_focus: list[str] = []

    dynamic_focus = suggest_dynamic_focus_ids(
        chapter_outline=_Outline(),
        extension_ids=["mystery_reveal_order", "romance_sweet_bitter_ratio"],
        planning_hint={
            "mandated_focus_ids": ["mystery_reveal_order"],
            "recommended_focus_ids": ["romance_sweet_bitter_ratio"],
        },
    )

    assert dynamic_focus == ["mystery_reveal_order"]
