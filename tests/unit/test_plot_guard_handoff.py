"""Tests for the accepted Plot Guard → next-chapter handoff."""

from __future__ import annotations

from pathlib import Path

from novel_forge.common.plot_guard import (
    _apply_plot_guard_to_creative_report,
    record_plot_guard_handoff,
)
from novel_forge.core.schemas.chapter import PlotGuardDecision
from novel_forge.desktop.pages.chapter_studio.memory import load_guardrail_data
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout


def test_record_plot_guard_handoff_requires_explicit_acceptance(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "handoff")
    layout.ensure_dirs()
    storage = FileSystemStorage(tmp_path)
    decision = PlotGuardDecision(
        decision="continue_with_constraints",
        next_chapter_constraints=["保留铜铃异响", "不得提前揭示井底真相"],
    )
    storage.save_json(
        layout.guard_report_path(2),
        {"chapter_number": 2, "decision": decision.model_dump(mode="json")},
    )

    record_plot_guard_handoff(
        storage=storage,
        layout=layout,
        chapter_number=2,
        decision=decision,
        accepted=True,
        recorded_by="test",
    )

    payload = storage.load_json(layout.guard_report_path(2))
    assert payload["next_chapter_handoff"] == {
        "status": "accepted",
        "target_chapter": 3,
        "constraints": ["保留铜铃异响", "不得提前揭示井底真相"],
        "recorded_by": "test",
        "recorded_at": payload["next_chapter_handoff"]["recorded_at"],
    }


def test_accepted_constraints_do_not_depend_on_entity_action_setting() -> None:
    decision = PlotGuardDecision(
        next_chapter_constraints=["不得提前揭露守夜人的真实身份"],
    )
    report = {
        "new_characters": [{"name": "新角色"}],
        "suggestions_for_next_chapter": "保持图书馆的压迫感。",
    }

    stats = _apply_plot_guard_to_creative_report(
        report,
        decision,
        apply_entity_actions=False,
    )

    assert report["new_characters"] == [{"name": "新角色"}]
    assert "AI护栏约束：" in report["suggestions_for_next_chapter"]
    assert "不得提前揭露守夜人的真实身份" in report["suggestions_for_next_chapter"]
    assert stats["constraints_appended"] == 1


def test_guardrail_panel_distinguishes_pending_from_accepted_handoff(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "guardrail_panel")
    layout.ensure_dirs()
    storage = FileSystemStorage(tmp_path)
    previous = PlotGuardDecision(next_chapter_constraints=["承接铜铃异响"])
    current = PlotGuardDecision(next_chapter_constraints=["不得揭露井底真相"])

    storage.save_json(
        layout.guard_report_path(1),
        {
            "chapter_number": 1,
            "decision": previous.model_dump(mode="json"),
            "next_chapter_handoff": {
                "status": "accepted",
                "target_chapter": 2,
                "constraints": ["承接铜铃异响"],
            },
        },
    )
    storage.save_json(
        layout.guard_report_path(2),
        {"chapter_number": 2, "decision": current.model_dump(mode="json")},
    )

    pending = load_guardrail_data(layout.root, 2)
    assert pending["prev_constraints"] == ["承接铜铃异响"]
    assert pending["next_constraints"] == ["不得揭露井底真相"]
    assert pending["next_handoff_status"] == "pending"

    record_plot_guard_handoff(
        storage=storage,
        layout=layout,
        chapter_number=2,
        decision=current,
        accepted=True,
        recorded_by="test",
    )
    accepted = load_guardrail_data(layout.root, 2)
    assert accepted["next_constraints"] == ["不得揭露井底真相"]
    assert accepted["next_handoff_status"] == "accepted"


def test_guardrail_panel_keeps_legacy_text_handoff_visible(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "legacy_guardrail_panel")
    layout.ensure_dirs()
    storage = FileSystemStorage(tmp_path)
    storage.save_json(
        layout.creative_report_path(1),
        {
            "suggestions_for_next_chapter": (
                "先承接旧书店的线索。\n\n"
                "AI护栏约束：\n"
                "- 不得提前揭露井底真相"
            )
        },
    )

    data = load_guardrail_data(layout.root, 2)

    assert data["prev_constraints"] == ["不得提前揭露井底真相"]
