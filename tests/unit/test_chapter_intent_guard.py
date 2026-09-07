from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.pipeline.long.stages import report_refresh
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    guard_report_has_actionable_low_compliance,
)


def _intent_card() -> dict[str, Any]:
    return {
        "schema": "user_intent_card_v1",
        "explicit_intents": [
            {
                "intent_id": "user:ending_style",
                "field": "ending_style",
                "value": "HE，两人共同生活",
            }
        ],
        "immutable_intent_ids": ["user:ending_style"],
        "locked_blueprint_elements": [],
    }


@pytest.mark.parametrize(
    ("mode", "expected_actionable"),
    [("warn", 0), ("block", 1)],
)
async def test_long_intent_guard_warns_or_blocks_without_a_second_task_type(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_actionable: int,
) -> None:
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _settings=SimpleNamespace(chapter_intent_guard_mode=mode),
        _storage=None,
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(chapter_source_slice=object(), layout=SimpleNamespace())
    packet = SimpleNamespace(guard_constraints=[], chapter_contract={})
    seen_constraints: list[str] = []

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.context.source_artifacts."
        "project_stage_source_cards",
        lambda _slice, stage: {"user_intent": _intent_card()},
    )

    async def _check(**kwargs: Any) -> dict[str, Any]:
        constraints = list(kwargs["constraints"])
        seen_constraints.extend(constraints)
        return {
            "constraints": constraints,
            "compliance_results": [
                {
                    "constraint": constraint,
                    "status": "non_compliant",
                    "confidence": 0.99,
                    "evidence": "他们选择了悲剧结局。",
                    "notes": "结局被改写",
                    "check_error": False,
                    "repairable": True,
                }
                for constraint in constraints
            ],
        }

    monkeypatch.setattr(report_refresh, "check_guard_constraint_compliance", _check)
    monkeypatch.setattr(report_refresh, "attach_guard_repair_metadata", lambda *a, **k: ([], []))

    refreshed = await report_refresh.run_guard_compliance_for_final_text(
        runner=runner,
        bundle=bundle,
        packet=packet,
        current_text="他们选择了悲剧结局。",
        chapter_number=6,
    )

    assert seen_constraints and "user:ending_style" in seen_constraints[0]
    report = refreshed.guard_compliance_report
    assert report is not None
    assert report["intent_guard_mode"] == mode
    assert report["intent_guard"]["actionable_violation_count"] == 1
    assert report["actionable_violation_count"] == expected_actionable
    assert report["intent_conflicts"][0]["field"] == "immutable_user_intent"
    assert report["intent_conflicts"][0]["fields"] == ["ending_style"]
    assert guard_report_has_actionable_low_compliance(report) is (mode == "block")
    assert events[-1][1]["intent_conflicts"]


async def test_long_intent_guard_off_preserves_legacy_no_guard_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = SimpleNamespace(
        _settings=SimpleNamespace(chapter_intent_guard_mode="off"),
        _storage=None,
        _on_step=lambda _step, _payload: None,
    )
    bundle = SimpleNamespace(chapter_source_slice=object(), layout=SimpleNamespace())
    packet = SimpleNamespace(guard_constraints=[], chapter_contract={})

    async def _unexpected(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("off mode must not add a guard call")

    monkeypatch.setattr(report_refresh, "check_guard_constraint_compliance", _unexpected)

    refreshed = await report_refresh.run_guard_compliance_for_final_text(
        runner=runner,
        bundle=bundle,
        packet=packet,
        current_text="本章没有额外护栏。",
        chapter_number=2,
    )

    assert refreshed.guard_compliance_report is None
