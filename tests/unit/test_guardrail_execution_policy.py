from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
    ChapterRepairReport,
    PlotGuardDecision,
)
from novel_forge.core.schemas.continuity import (
    CausalLink,
    ChapterBridge,
    ChapterPlan,
    ContinuityReport,
    SceneIntent,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.stages.planning import (
    _apply_macro_guard_guidance,
    absorb_bridge_into_plan,
)
from novel_forge.workspace.sessions.chapter_session_state import build_guard_checkpoint


def _build_checkpoint(tmp_path, *, guard_decision=None, warnings=(), chapter_repair_report=None):
    layout = ProjectLayout(tmp_path / "guardrail_policy")
    layout.ensure_dirs()
    bundle = SimpleNamespace(layout=layout)
    return build_guard_checkpoint(
        bundle,
        2,
        current_text="测试正文",
        alignment_report=AlignmentReport(
            alignment_score=8.6,
            risk_level="low",
            conflict_level="low",
            summary="对齐稳定",
        ),
        continuity_report=ContinuityReport(
            continuity_score=8.2,
            summary="连贯稳定",
            issues=[],
        ),
        eval_report=EvalReport(
            overall_score=8.4,
            passed=True,
            threshold=6.0,
            summary="质量稳定",
        ),
        causal_report=CausalValidationReport(
            causal_score=8.3,
            summary="因果稳定",
            issues=[],
            causal_link_verified=True,
        ),
        guard_decision=guard_decision,
        chapter_repair_report=chapter_repair_report,
        warnings=warnings,
    )


def test_build_guard_checkpoint_blocks_accept_for_high_risk_ai_decision(tmp_path) -> None:
    checkpoint = _build_checkpoint(
        tmp_path,
        guard_decision=PlotGuardDecision(
            decision="pause_for_human",
            risk_level="high",
            outline_action="none",
            reasoning_brief="需要人工复核",
        ),
    )

    option_ids = {option.option_id for option in checkpoint.options}
    assert "accept_and_finalize" not in option_ids
    assert "pause_for_human" in option_ids
    assert [option.option_id for option in checkpoint.options if option.is_recommended] == [
        "pause_for_human"
    ]
    assert "不可直接归档" in checkpoint.summary


def test_build_guard_checkpoint_recommends_repairs_for_guard_compliance_warning(tmp_path) -> None:
    checkpoint = _build_checkpoint(
        tmp_path,
        warnings=("AI护栏约束合规率过低: 主线承诺未兑现",),
    )

    recommended = [option.option_id for option in checkpoint.options if option.is_recommended]
    assert recommended == ["apply_repairs_and_finalize"]
    repair_option = next(option for option in checkpoint.options if option.option_id == "apply_repairs_and_finalize")
    assert "优先回收 AI 护栏未兑现的关键约束" in repair_option.description


def test_build_guard_checkpoint_does_not_recommend_repairs_for_guard_check_failure(
    tmp_path,
) -> None:
    checkpoint = _build_checkpoint(
        tmp_path,
        warnings=("AI护栏约束检查未完成: 2 条检查失败；不会自动触发文本修复。",),
    )

    recommended = [option.option_id for option in checkpoint.options if option.is_recommended]
    assert recommended != ["apply_repairs_and_finalize"]
    assert "accept_and_finalize" in recommended


def test_build_guard_checkpoint_requires_repair_for_confirmed_prompt_leak(tmp_path) -> None:
    checkpoint = _build_checkpoint(
        tmp_path,
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【紧急通知】"]),
    )

    option_ids = {option.option_id for option in checkpoint.options}
    assert "accept_and_finalize" in option_ids

    checkpoint = build_guard_checkpoint(
        SimpleNamespace(layout=ProjectLayout(tmp_path / "guardrail_policy_confirmed")),
        2,
        current_text="她刚踏进门，正文里却混进【交接】这样的规划标记。",
        alignment_report=AlignmentReport(alignment_score=8.6, risk_level="low"),
        continuity_report=ContinuityReport(continuity_score=8.2, summary="连贯稳定", issues=[]),
        eval_report=EvalReport(overall_score=8.4, passed=True, threshold=6.0),
        causal_report=CausalValidationReport(
            causal_score=8.3,
            summary="因果稳定",
            issues=[],
            causal_link_verified=True,
        ),
        guard_decision=None,
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【交接】"]),
    )
    option_ids = {option.option_id for option in checkpoint.options}
    assert "accept_and_finalize" not in option_ids
    assert [option.option_id for option in checkpoint.options if option.is_recommended] == [
        "apply_repairs_and_finalize"
    ]


def test_build_guard_checkpoint_allows_in_world_bracketed_title(tmp_path) -> None:
    checkpoint = build_guard_checkpoint(
        SimpleNamespace(layout=ProjectLayout(tmp_path / "guardrail_policy_in_world")),
        2,
        current_text="邮件标题写着：【紧急通知】关于订单交付计划调整。",
        alignment_report=AlignmentReport(alignment_score=8.6, risk_level="low"),
        continuity_report=ContinuityReport(continuity_score=8.2, summary="连贯稳定", issues=[]),
        eval_report=EvalReport(overall_score=8.4, passed=True, threshold=6.0),
        causal_report=CausalValidationReport(
            causal_score=8.3,
            summary="因果稳定",
            issues=[],
            causal_link_verified=True,
        ),
        guard_decision=None,
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【紧急通知】"]),
    )

    option_ids = {option.option_id for option in checkpoint.options}
    assert "accept_and_finalize" in option_ids
    assert [option.option_id for option in checkpoint.options if option.is_recommended] == [
        "accept_and_finalize"
    ]


def test_macro_guard_alert_is_promoted_to_guard_constraints() -> None:
    packet = SimpleNamespace(guard_constraints=["保留既有案件线索"])
    reading_power_hint = {"prev_chapter_suggestions": ["保留上一章钩子"]}

    merged_hint = _apply_macro_guard_guidance(
        packet=packet,
        reading_power_hint=reading_power_hint,
        hint_payload={"reasoning": "下一章开场更快进入追查"},
        alert_payload={
            "action": "critical_rollback",
            "reasoning": "主线追查被支线情绪戏挤占，必须立即收束。",
            "adjustment_plan": {
                "must_preserve": "保住案情推进",
                "must_reduce": "压缩支线抒情",
                "next_arc_target": "把调查重新拉回失踪案",
            },
        },
        auto_apply_hint=True,
    )

    assert merged_hint is not None
    assert "[宏观护栏·轨迹修正] 下一章开场更快进入追查" in merged_hint["prev_chapter_suggestions"]
    assert any("宏观护栏紧急纠偏" in item for item in packet.guard_constraints)
    assert any("must_preserve" in item for item in packet.guard_constraints)
    assert any("next_arc_target" in item for item in packet.guard_constraints)


def test_absorb_bridge_into_plan_makes_plan_opening_source_of_truth() -> None:
    bridge = ChapterBridge(
        to_chapter=3,
        opening_time="子夜",
        opening_location="钟楼",
        opening_pov="林远",
        transition_mode="direct_continue",
        emotional_carryover="上一章余悸未散",
        action_handoff="林远抵住钟楼侧门",
        bridge_summary="承接上一章追击",
        pending_questions=["追击者是谁"],
        sensory_anchors=["铜铃震颤"],
        opening_acceptance_criteria=["前三段必须落地侧门动作"],
        causal_link=CausalLink(
            previous_event="上一章追击逼近",
            causal_mechanism="追击迫使林远躲进钟楼",
            unresolved_question="追击者是谁",
            open_threads=["钟楼密钥"],
        ),
    )
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="林远进入钟楼",
                required_outcome="找到密钥；避开追击者",
            )
        ],
        opening_contract="",
        closing_contract="林远取得密钥",
    )

    absorbed = absorb_bridge_into_plan(plan, bridge)

    assert absorbed.opening_bridge["action_handoff"] == "林远抵住钟楼侧门"
    assert absorbed.opening_bridge["causal_link"]["previous_event"] == "上一章追击逼近"
    assert "林远抵住钟楼侧门" in absorbed.opening_contract
    first_scene = absorbed.scene_intents[0]
    assert first_scene.location == "钟楼"
    assert first_scene.time_marker == "子夜"
    assert first_scene.pov_character == "林远"
    assert "林远抵住钟楼侧门" in first_scene.entry_state_refs
    assert "追击者是谁" in first_scene.entry_state_refs
    assert first_scene.owned_events == ["找到密钥", "避开追击者"]
