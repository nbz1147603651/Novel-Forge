from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.guidance import project_guidance_requirements
from novel_forge.pipeline.long.services.guidance_contract_audit import (
    audit_plan_contract,
    audit_report_consistency,
    compile_guidance_repair_tickets,
)
from novel_forge.pipeline.long.services.plan_obligations import assess_plan_literal_coverage


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_number_fact_is_not_promoted_to_a_literal_obligation():
    requirements = project_guidance_requirements(
        {"required_events": ["能力不足迫使他改变策略"], "symbols": ["钟声"]},
        world_rules=["能力最多持续七秒"],
        chapter_number=2,
    )
    assert [(r["text"], r["satisfaction"]) for r in requirements] == [
        ("能力最多持续七秒", "invariant"),
        ("能力不足迫使他改变策略", "narrative"),
        ("钟声", "optional"),
    ]
    report = assess_plan_literal_coverage(
        _ns(guidance_requirements=requirements, required_literals=[]),
        "他没有发动能力，转身寻求同伴帮助。",
    )
    assert report["missing_required_literal_count"] == 0


def test_literal_fulfillment_is_recomputed_for_each_text_version():
    plan = _ns(
        required_literals=[
            {
                "contract_id": "password",
                "literal": "0707",
                "scene_id": "scene_02",
                "reason": "密码线索原文",
                "placement_hint": "输入密码",
            }
        ]
    )
    first = assess_plan_literal_coverage(plan, "她输入0707。")
    revised = assess_plan_literal_coverage(plan, "她离开了房间。")
    assert first["fulfillment"][0]["status"] == "fulfilled"
    assert revised["fulfillment"][0]["status"] == "pending"
    assert first["source_text_hash"] != revised["source_text_hash"]


def _complete_scene(**overrides):
    data = {
        "scene_id": "scene_01",
        "summary": "开场",
        "purpose": "承接危机",
        "conflict": "窗口只剩一次",
        "required_characters": ["沈鹿溪"],
        "character_motivations": [_ns(character="沈鹿溪", motivation="完成剪辑", stake="错过窗口")],
        "entry_state_refs": ["上一章已确认倒计时"],
        "required_outcome": "沈鹿溪完成剪辑框架。",
        "exit_target_state": "剪辑框架完成，准备导出。",
        "owned_state_changes": [],
        "location": "民宿楼顶",
        "time_marker": "清晨",
        "target_words": 2000,
    }
    data.update(overrides)
    return _ns(**data)


def test_plan_contract_audit_blocks_incomplete_scene() -> None:
    plan = _ns(
        scene_intents=[
            _ns(
                scene_id="scene_01",
                summary="开场",
                purpose="承接危机",
                conflict="",
                required_characters=[],
                character_motivations=[],
                required_outcome="",
                exit_target_state="",
                location="",
                time_marker="",
                target_words=0,
            )
        ]
    )

    findings = audit_plan_contract(
        plan=plan,
        bridge=_ns(action_handoff="动作接力"),
        chapter_number=2,
        target_word_count=4000,
    )

    issue_types = {finding.issue_type for finding in findings}
    assert "plan_scene_incomplete" in issue_types
    assert "plan_word_budget_mismatch" in issue_types
    assert all(finding.blocks_finalize for finding in findings)


def test_plan_contract_audit_accepts_structurally_complete_plan() -> None:
    plan = _ns(
        scene_intents=[
            _ns(
                scene_id="scene_01",
                summary="开场",
                purpose="承接危机",
                conflict="被搜查",
                required_characters=["令昭"],
                character_motivations=[_ns(character="令昭", motivation="藏起残页", stake="被抓")],
                entry_state_refs=["上章门外脚步"],
                required_outcome="藏起残页",
                exit_target_state="暂时脱身",
                location="巷口",
                time_marker="当夜",
                target_words=2000,
            ),
            _ns(
                scene_id="scene_02",
                summary="转移",
                purpose="推进主线",
                conflict="追兵逼近",
                required_characters=["令昭"],
                character_motivations=[
                    _ns(character="令昭", motivation="送出残页", stake="线索丢失")
                ],
                entry_state_refs=["暂时脱身"],
                required_outcome="把残页交给同伴",
                exit_target_state="线索转移成功",
                location="药铺后巷",
                time_marker="当夜稍后",
                target_words=2000,
            ),
        ]
    )

    findings = audit_plan_contract(
        plan=plan,
        bridge=_ns(action_handoff="动作接力"),
        chapter_number=2,
        target_word_count=4000,
    )

    assert findings == []


def test_plan_contract_audit_requires_substantive_transition_coverage() -> None:
    transition = "风季从倒数第一天推进到倒数第一天，信号窗口收窄至每日仅余一次"
    plan = _ns(
        required_state_transitions=[transition],
        scene_intents=[
            _complete_scene(
                required_outcome="沈鹿溪完成一次剪辑框架。",
                exit_target_state="剪辑框架完成，准备导出。",
            )
        ],
    )

    findings = audit_plan_contract(plan=plan, chapter_number=23, target_word_count=2000)

    assert [finding.issue_type for finding in findings] == ["plan_transition_uncovered"]


def test_plan_contract_audit_accepts_transition_claimed_by_owned_state_changes() -> None:
    transition = "风季从倒数第一天推进到倒数第一天，信号窗口收窄至每日仅余一次"
    plan = _ns(
        required_state_transitions=[transition],
        scene_intents=[
            _complete_scene(
                owned_state_changes=[transition],
            )
        ],
    )

    findings = audit_plan_contract(plan=plan, chapter_number=23, target_word_count=2000)

    assert findings == []


def test_report_consistency_creates_blocking_finding_for_empty_issues_with_critical_summary() -> (
    None
):
    report = _ns(summary="存在 critical bridge 问题，需要阻断归档", issues=[])

    findings = audit_report_consistency(
        report,
        source_module="continuity_report",
        chapter_number=2,
    )
    tickets = compile_guidance_repair_tickets(findings)

    assert len(findings) == 1
    assert findings[0].issue_type == "report_issue_summary_mismatch"
    assert tickets[0].blocking is True


def test_report_consistency_ignores_high_risk_level_when_summary_has_no_blocker() -> None:
    report = _ns(
        summary="正文基本覆盖章节目标，但部分大纲节拍与计划必须达成点未显化，需补强。",
        risk_level="high",
        issues=[],
    )

    findings = audit_report_consistency(
        report,
        source_module="alignment_report",
        chapter_number=53,
    )

    assert findings == []


def test_report_consistency_treats_alignment_missing_points_as_issues() -> None:
    report = _ns(
        summary="存在 high alignment 问题，需要补齐主线点。",
        issues=[],
        missing_main_points=["缺少怀表与金镯呼应"],
    )

    findings = audit_report_consistency(
        report,
        source_module="alignment_report",
        chapter_number=53,
    )

    assert findings == []


def test_report_consistency_treats_causal_issues_as_structured_issues() -> None:
    report = _ns(
        summary="存在 high 因果问题，需要修复。",
        issues=[_ns(issue_type="opening_causal_gap", severity="high")],
        validation_status="ok",
    )

    findings = audit_report_consistency(
        report,
        source_module="causal_report",
        chapter_number=53,
    )

    assert findings == []


def test_report_consistency_treats_continuity_issues_as_structured_issues() -> None:
    report = _ns(
        summary="存在 high 连贯性问题，需要修复。",
        issues=[_ns(issue_type="bridge_contract_not_followed", severity="high")],
        pipeline_stage="targeted_recheck",
    )

    findings = audit_report_consistency(
        report,
        source_module="continuity_report",
        chapter_number=53,
    )

    assert findings == []


def test_report_consistency_ignores_negated_blocking_summary_with_empty_issues() -> None:
    report = _ns(summary="本章连贯性点验通过。无critical/high级回归问题。", issues=[])

    findings = audit_report_consistency(
        report,
        source_module="continuity_report",
        chapter_number=3,
    )

    assert findings == []


def test_report_consistency_ignores_english_negated_blocking_summary() -> None:
    report = _ns(summary="Continuity passed with no critical/high regressions.", issues=[])

    findings = audit_report_consistency(
        report,
        source_module="continuity_report",
        chapter_number=3,
    )

    assert findings == []
