"""Tests for the early Bridge/Plan compass gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.pipeline.long.services.upstream_compass import (
    audit_outline_source_consistency,
    audit_upstream_compass,
    blocking_messages,
    build_location_transition_semantic_check,
    find_outline_plan_duration_conflicts,
)


def _scene(**kwargs: object) -> SimpleNamespace:
    base = {
        "summary": "沈知微接住上一章遗留线索",
        "required_outcome": "确认密函来自旧档案室",
        "exit_target_state": "带着密函前往码头",
        "target_words": 1200,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _packet(
    *,
    goal: str = "调查蓝色密函的来源",
    must_carry_forward: list[str] | None = None,
    open_questions: list[str] | None = None,
    location: str = "旧档案室",
    previous_exit: bool = True,
    previous_pov: str = "沈知微",
) -> SimpleNamespace:
    return SimpleNamespace(
        must_carry_forward=must_carry_forward or [],
        previous_exit_state=(
            SimpleNamespace(location=location, pov=previous_pov, open_questions=open_questions or [])
            if previous_exit
            else None
        ),
        chapter_outline=SimpleNamespace(goal=goal),
    )


def _bridge(**kwargs: object) -> SimpleNamespace:
    base = {
        "to_chapter": 3,
        "opening_location": "旧档案室",
        "opening_pov": "沈知微",
        "transition_mode": "action_handoff",
        "action_handoff": "沈知微在旧档案室接过蓝色密函，继续调查蓝色密函的来源。",
        "bridge_summary": "沈知微承接蓝色密函，调查其来源。",
        "pending_questions": [],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _plan(**kwargs: object) -> SimpleNamespace:
    base = {
        "scene_intents": [
            _scene(
                summary="沈知微检查蓝色密函并调查蓝色密函的来源",
                required_outcome="确认蓝色密函的来源线索",
                exit_target_state="沈知微带着密函前往码头",
                target_words=1200,
            )
        ],
        "opening_contract": "沈知微在旧档案室接过蓝色密函，继续调查蓝色密函的来源。",
        "closing_contract": "确认蓝色密函的来源线索，并带出码头行动钩子。",
        "required_state_transitions": ["蓝色密函交到沈知微手里"],
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _issue_types(report: object) -> set[str]:
    return {finding.issue_type for finding in getattr(report, "findings", [])}


def test_upstream_compass_blocks_dropped_carry_forward() -> None:
    packet = SimpleNamespace(
        must_carry_forward=["蓝色密函必须交到沈知微手里"],
        previous_exit_state=SimpleNamespace(location="旧档案室", open_questions=["密函是谁放下的"]),
        chapter_outline=SimpleNamespace(goal="调查蓝色密函的来源"),
    )
    bridge = SimpleNamespace(
        to_chapter=3,
        opening_location="码头",
        action_handoff="沈知微直接来到码头。",
        bridge_summary="码头开场。",
        pending_questions=[],
    )
    plan = SimpleNamespace(
        scene_intents=[_scene(summary="码头冲突")],
        opening_contract="码头开场",
        closing_contract="以追逐结束",
        required_state_transitions=[],
    )

    report = audit_upstream_compass(
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=3,
        target_word_count=1200,
    )

    assert report.blocking is True
    assert "必须承接项" in "；".join(blocking_messages(report))
    assert any(f.issue_type == "bridge_dropped_open_question" for f in report.findings)


@pytest.mark.parametrize(
    ("bridge_override", "issue_type"),
    [
        ({"to_chapter": 99}, "bridge_target_chapter_mismatch"),
        ({"action_handoff": ""}, "bridge_missing_action_handoff"),
        ({"bridge_summary": ""}, "bridge_missing_summary"),
    ],
)
def test_upstream_compass_blocks_core_bridge_shape_errors(
    bridge_override: dict[str, object],
    issue_type: str,
) -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(**bridge_override),
        plan=_plan(),
        chapter_number=3,
        target_word_count=1200,
    )

    assert report.blocking is True
    assert issue_type in _issue_types(report)


def test_upstream_compass_blocks_unexplained_location_jump() -> None:
    report = audit_upstream_compass(
        packet=_packet(location="旧档案室"),
        bridge=_bridge(opening_location="码头", action_handoff="沈知微看见人群。"),
        plan=_plan(),
        chapter_number=3,
        target_word_count=1200,
        location_transition_judgment={
            "issue_resolved": False,
            "confidence": 0.91,
            "reasoning": "Bridge 只写到码头开场，没有解释从旧档案室如何转到码头。",
        },
    )

    assert "bridge_unexplained_location_jump" in _issue_types(report)
    assert report.blocking is True


def test_upstream_compass_allows_location_jump_when_llm_judges_it_explained() -> None:
    report = audit_upstream_compass(
        packet=_packet(location="镇口老街/民宿"),
        bridge=_bridge(
            opening_location="三山风眼小镇民宿楼顶",
            action_handoff="沈鹿溪拎着摄影包，踩着民宿外搭的木梯爬上楼顶。",
            bridge_summary="风季倒数第一天清晨，沈鹿溪从民宿房间到楼顶完成最后收音。",
        ),
        plan=_plan(
            opening_contract="开头明确沈鹿溪从民宿一楼房间出来，踩着外搭木梯爬上楼顶。",
        ),
        chapter_number=3,
        target_word_count=1200,
        location_transition_judgment={
            "issue_resolved": True,
            "confidence": 0.93,
            "reasoning": "Bridge 和 Plan 已说明从民宿内部移动到楼顶，属于同一建筑内的子地点转换。",
        },
    )

    assert "bridge_unexplained_location_jump" not in _issue_types(report)
    assert report.blocking is False


def test_location_transition_semantic_check_assembles_bridge_and_plan_evidence() -> None:
    packet = _packet(location="镇口老街/民宿")
    bridge = _bridge(
        opening_location="三山风眼小镇民宿楼顶",
        action_handoff="沈鹿溪拎着摄影包，踩着民宿外搭的木梯爬上楼顶。",
        bridge_summary="沈鹿溪从民宿房间登上楼顶。",
    )
    plan = _plan(
        scene_intents=[
            _scene(
                location="三山风眼小镇·民宿楼顶",
                summary="沈鹿溪从民宿房间上到楼顶架好麦克风。",
            )
        ],
        opening_contract="从民宿房间到楼顶的移动必须在开头交代。",
    )

    context = build_location_transition_semantic_check(
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=3,
    )

    assert context is not None
    assert "Bridge/Plan 是否已经在语义上解释" in context["issue_description"]
    assert "previous_location=镇口老街/民宿" in context["issue_evidence"]
    assert "踩着民宿外搭的木梯爬上楼顶" in context["repaired_text"]
    assert "opening_contract" in context["repaired_text"]
    assert "first_scene.summary" in context["repaired_text"]


def test_upstream_compass_allows_pov_switch_location_jump_when_focus_shift_is_explicit() -> None:
    report = audit_upstream_compass(
        packet=_packet(location="国际会议中心休息室", previous_pov="沈念卿"),
        bridge=_bridge(
            opening_location="陆家嘴智云科技办公室",
            opening_pov="陆云峥",
            transition_mode="pov_switch",
            action_handoff="陆云峥在办公室翻开会议资料，触到内袋里的怀表。",
            bridge_summary="叙事焦点从沈念卿离场后的疑问转向陆云峥处理供应链危机。",
        ),
        plan=_plan(
            opening_contract="陆云峥在办公室翻开会议资料，触到内袋里的怀表。",
        ),
        chapter_number=3,
        target_word_count=1200,
    )

    assert "bridge_unexplained_location_jump" not in _issue_types(report)


def test_upstream_compass_blocks_dropped_open_question() -> None:
    report = audit_upstream_compass(
        packet=_packet(open_questions=["银铃为何在雨夜断裂"]),
        bridge=_bridge(pending_questions=[]),
        plan=_plan(),
        chapter_number=3,
        target_word_count=1200,
    )

    assert "bridge_dropped_open_question" in _issue_types(report)
    assert "未决问题" in "；".join(blocking_messages(report))


@pytest.mark.parametrize(
    ("plan_override", "issue_type"),
    [
        ({"scene_intents": []}, "plan_missing_scene_intents"),
        ({"opening_contract": ""}, "plan_missing_opening_contract"),
        ({"closing_contract": ""}, "plan_missing_closing_contract"),
    ],
)
def test_upstream_compass_blocks_core_plan_shape_errors(
    plan_override: dict[str, object],
    issue_type: str,
) -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(),
        plan=_plan(**plan_override),
        chapter_number=3,
        target_word_count=1200,
    )

    assert issue_type in _issue_types(report)
    assert report.blocking is True


def test_upstream_compass_blocks_goal_drift() -> None:
    report = audit_upstream_compass(
        packet=_packet(goal="破解雪鸢令牌的真正用途"),
        bridge=_bridge(),
        plan=_plan(
            scene_intents=[_scene(summary="沈知微整理药柜")],
            opening_contract="沈知微整理药柜。",
            closing_contract="整理结束。",
            required_state_transitions=["药柜状态更新"],
        ),
        chapter_number=3,
        target_word_count=1200,
    )

    assert "plan_goal_drift" in _issue_types(report)
    assert report.blocking is True


def test_upstream_compass_blocks_yaozheng_five_day_three_day_plan_conflict() -> None:
    packet = _packet(goal="沈昭在停职五日受审仪轨下退出晨钟验方一线")
    packet.chapter_outline.main_plot_points = ["停职五日受审仪轨正式执行"]
    plan = _plan(
        scene_intents=[
            _scene(
                summary="停职受审第三日暮色将至",
                required_outcome="沈昭决定在三日停职结束前复验甘松",
            )
        ],
    )

    conflicts = find_outline_plan_duration_conflicts(
        outline=packet.chapter_outline,
        plan=plan,
    )
    report = audit_upstream_compass(
        packet=packet,
        bridge=_bridge(),
        plan=plan,
        chapter_number=3,
        target_word_count=1200,
    )

    assert len(conflicts) == 1
    assert conflicts[0]["subject"] == "停职"
    assert conflicts[0]["outline_days"] == [5]
    assert conflicts[0]["plan_days"] == [3]
    assert conflicts[0]["conflict_scope"] == "outline_plan"
    assert conflicts[0]["recovery_target"] == "plan"
    assert conflicts[0]["plan_evidence"]["3"] == ["三日停职结束"]
    assert conflicts[0]["plan_locators"]["3"][0]["json_pointer"]
    assert conflicts[0]["plan_locators"]["3"][0]["char_end"] > 0
    assert "plan_outline_duration_conflict" in _issue_types(report)
    assert report.blocking is True
    finding = next(
        item for item in report.findings if item.issue_type == "plan_outline_duration_conflict"
    )
    assert finding.diagnostic["architecture"] == "diagnose_locate_patch_verify"
    assert finding.diagnostic["status"] == "ready"
    assert finding.diagnostic["authority"] == "automatic_candidate"
    assert finding.diagnostic["targets"][0]["path"]


def test_upstream_compass_stops_when_approved_outline_is_internally_inconsistent() -> None:
    packet = _packet(goal="沈昭在停职五日受审仪轨下退出晨钟验方一线")
    packet.chapter_outline.main_plot_points = [
        "停职五日受审仪轨正式执行",
        "她必须在三日停职结束前决定复验顺序",
    ]
    plan = _plan(
        required_state_transitions=[
            "停职五日受审进行中",
            "三日停职结束前做出决定",
        ]
    )

    conflicts = find_outline_plan_duration_conflicts(
        outline=packet.chapter_outline,
        plan=plan,
    )
    report = audit_upstream_compass(
        packet=packet,
        bridge=_bridge(),
        plan=plan,
        chapter_number=3,
        target_word_count=1200,
    )

    assert conflicts[0]["conflict_scope"] == "outline_and_plan_internal"
    assert conflicts[0]["recovery_target"] == "manual"
    assert conflicts[0]["outline_days"] == [3, 5]
    assert "outline_internal_duration_conflict" in _issue_types(report)
    finding = next(
        item for item in report.findings if item.issue_type == "outline_internal_duration_conflict"
    )
    assert finding.metadata["recovery_target"] == "manual"
    assert finding.diagnostic["status"] == "proposal_required"
    assert finding.diagnostic["authority"] == "proposal_required"
    assert finding.diagnostic["targets"][0]["path"]


def test_outline_source_preflight_reports_conflict_without_plan_artifact() -> None:
    outline = {
        "goal": "停职五日受审仪轨正式执行",
        "time_span": "停职首日至第三日",
        "expected_hook": {"hook_description": "三日停职结束前决定复验顺序"},
    }

    report = audit_outline_source_consistency(outline=outline, chapter_number=2)

    assert report.blocking is True
    finding = report.findings[0]
    assert finding.issue_type == "outline_internal_duration_conflict"
    assert finding.metadata["outline_days"] == [3, 5]
    assert finding.metadata["plan_days"] == []
    assert finding.diagnostic["authority"] == "proposal_required"


def test_outline_source_preflight_treats_ordinal_day_as_progress_not_total() -> None:
    outline = {
        "goal": "停职五日受审仪轨正式执行",
        "time_span": "停职首日至第三日",
        "beats_summary": ["停职第三日完成复验"],
        "expected_hook": {"hook_description": "停职第三日结束前决定复验顺序"},
    }

    report = audit_outline_source_consistency(outline=outline, chapter_number=2)

    assert report.blocking is False
    assert report.findings == []


def test_duration_locator_prefers_longer_evidence_at_same_text_anchor() -> None:
    conflicts = find_outline_plan_duration_conflicts(
        outline={
            "goal": "停职五日受审",
            "aftertaste": "三日停职让她看清程序",
            "hook": "她须在三日停职结束前决定复验顺序",
        },
        plan={"goal": "停职五日受审", "hook": "三日停职结束前决定"},
    )

    three_day_targets = conflicts[0]["outline_locators"]["3"]
    assert len(three_day_targets) == 2
    assert {target["quote"] for target in three_day_targets} == {
        "三日停职",
        "三日停职结束",
    }


def test_plan_internal_duration_diagnostic_only_targets_non_authoritative_days() -> None:
    packet = _packet(goal="停职五日受审")
    plan = _plan(
        required_state_transitions=["停职五日受审进行中", "三日停职结束前决定复验顺序"]
    )

    report = audit_upstream_compass(
        packet=packet,
        bridge=_bridge(),
        plan=plan,
        chapter_number=3,
        target_word_count=1200,
    )

    finding = next(
        item for item in report.findings if item.issue_type == "plan_outline_duration_conflict"
    )
    assert {target["current_value"] for target in finding.diagnostic["targets"]} == {
        "三日停职结束"
    }
    references = finding.diagnostic["issue"]["reference_targets"]
    assert {target["quote"] for target in references} == {"停职五日"}


def test_upstream_compass_blocks_missing_state_work() -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(),
        plan=_plan(
            scene_intents=[
                _scene(required_outcome="", exit_target_state="", owned_state_changes=[])
            ],
            required_state_transitions=[],
        ),
        chapter_number=3,
        target_word_count=1200,
    )

    assert "plan_missing_state_work" in _issue_types(report)
    assert report.blocking is True


def test_upstream_compass_blocks_word_budget_drift() -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(),
        plan=_plan(scene_intents=[_scene(target_words=300)]),
        chapter_number=3,
        target_word_count=3000,
        word_budget_tolerance=0.25,
    )

    assert "plan_word_budget_drift" in _issue_types(report)
    assert report.blocking is True


def test_upstream_compass_skips_first_chapter_previous_exit_checks() -> None:
    report = audit_upstream_compass(
        packet=_packet(open_questions=["第一章不应检查上一章悬念"]),
        bridge=_bridge(to_chapter=1, action_handoff="", bridge_summary=""),
        plan=_plan(),
        chapter_number=1,
        target_word_count=1200,
    )

    assert "bridge_dropped_open_question" not in _issue_types(report)
    assert "bridge_missing_action_handoff" not in _issue_types(report)
    assert "bridge_missing_summary" not in _issue_types(report)


def test_upstream_compass_serializes_stage_and_blocking_findings() -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(to_chapter=99),
        plan=_plan(),
        chapter_number=3,
    )

    payload = report.model_dump()

    assert payload["stage"] == "upstream_compass"
    assert payload["finding_count"] == len(report.findings)
    assert payload["blocking_findings"]
    assert blocking_messages(report)


def test_upstream_compass_word_budget_zero_bypasses_budget_check() -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(),
        plan=_plan(scene_intents=[_scene(target_words=50)]),
        chapter_number=3,
        target_word_count=0,
    )

    assert "plan_word_budget_drift" not in _issue_types(report)


def test_upstream_compass_handles_sparse_inputs_without_crashing() -> None:
    report = audit_upstream_compass(
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(to_chapter=1),
        plan=SimpleNamespace(),
        chapter_number=1,
        target_word_count=0,
    )

    assert report.blocking is True
    assert "plan_missing_scene_intents" in _issue_types(report)


def test_upstream_compass_can_report_without_blocking() -> None:
    report = audit_upstream_compass(
        packet=_packet(),
        bridge=_bridge(to_chapter=99),
        plan=_plan(),
        chapter_number=3,
        blocking_enabled=False,
    )

    assert report.findings
    assert report.blocking is False
    assert report.passed is True


def test_upstream_compass_passes_when_bridge_and_plan_preserve_direction() -> None:
    packet = SimpleNamespace(
        must_carry_forward=["蓝色密函必须交到沈知微手里"],
        previous_exit_state=SimpleNamespace(location="旧档案室", open_questions=["密函是谁放下的"]),
        chapter_outline=SimpleNamespace(goal="调查蓝色密函的来源"),
    )
    bridge = SimpleNamespace(
        to_chapter=3,
        opening_location="旧档案室",
        action_handoff="沈知微在旧档案室接过蓝色密函，继续追查密函是谁放下的。",
        bridge_summary="承接蓝色密函，调查其来源。",
        pending_questions=["密函是谁放下的"],
    )
    plan = SimpleNamespace(
        scene_intents=[
            _scene(
                summary="沈知微检查蓝色密函",
                required_outcome="确认蓝色密函的来源线索",
                exit_target_state="沈知微带着密函前往码头",
            )
        ],
        opening_contract="沈知微在旧档案室接住蓝色密函，追问密函是谁放下的。",
        closing_contract="确认蓝色密函的来源线索，并带出码头行动钩子。",
        required_state_transitions=["蓝色密函交到沈知微手里"],
    )

    report = audit_upstream_compass(
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=3,
        target_word_count=1200,
    )

    assert report.blocking is False
    assert report.passed is True
