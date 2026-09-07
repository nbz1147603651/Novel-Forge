"""Tests for continuity evaluation heuristics and scoring."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.steps.continuity_eval.context import build_llm_context
from novel_forge.pipeline.steps.continuity_eval_step import ContinuityEvalInput, ContinuityEvalStep


def _build_input() -> ContinuityEvalInput:
    packet = ChapterStatePacket(
        chapter_number=3,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="账簿",
            goal="推进账簿造假线",
            pov_character="周明",
            setting="西官仓耳房",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=2,
            time_marker="未时初",
            location="西官仓耳房",
            pov="周明",
            must_carry_forward=["周明与赵成安的合作关系", "苏婉的平粜基金计划"],
        ),
        previous_chapter_ending="周明把残页压在青砖下，仍留在耳房等更夫报时。",
        must_carry_forward=["周明与赵成安的合作关系", "苏婉的平粜基金计划"],
    )
    bridge = ChapterBridge(
        from_chapter=2,
        to_chapter=3,
        opening_time="未时初",
        opening_location="西官仓耳房",
        opening_pov="周明",
        transition_mode="direct_continue",
        action_handoff="周明守着账簿，等下一步消息。",
    )
    plan = ChapterPlan(
        opening_contract="承接耳房里的等待状态。",
        closing_contract="留下下一章可直接承接的证据交接。",
    )
    return ContinuityEvalInput(
        chapter_number=3,
        chapter_text="周明守着账簿，仍在耳房等下一步消息，想着赵成安会不会按约来人。",
        chapter_state_packet=packet,
        chapter_bridge=bridge,
        chapter_plan=plan,
    )


def _make_first_chapter_input() -> ContinuityEvalInput:
    input_data = _build_input()
    input_data.chapter_number = 1
    input_data.chapter_state_packet.chapter_number = 1
    input_data.chapter_state_packet.previous_exit_state = None
    input_data.chapter_state_packet.previous_chapter_ending = ""
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_bridge.from_chapter = 0
    input_data.chapter_bridge.to_chapter = 1
    input_data.chapter_bridge.action_handoff = ""
    input_data.chapter_bridge.opening_time = ""
    input_data.chapter_bridge.opening_location = "西官仓耳房"
    input_data.chapter_plan.opening_contract = "首章建立章内场景与视角。"
    input_data.chapter_text = "周明守着账簿。\n\n镜头突然切到苏婉在城门外听见密令。"
    return input_data


def test_continuity_context_receives_upstream_budgeted_evidence_without_scores() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.retrieval_evidence_pack = {
        "evidence_cards": [
            {
                "source_ref": "narrative_state/state_ledger/state_1",
                "excerpt": "已裁定状态：赵成安尚未拿到账簿。",
                "chapter_number": 2,
                "authority": "accepted",
                "score": 0.99,
            }
        ],
    }

    context, _ = build_llm_context(
        input_data,
        number_paragraphs_fn=lambda text: text,
    )

    evidence = context["chapter_state_packet"]["retrieval_evidence"]
    assert evidence["supporting_evidence"][0]["authority"] == "accepted"
    assert "score" not in evidence["supporting_evidence"][0]


def test_first_chapter_continuity_context_skips_previous_handoff_only() -> None:
    input_data = _make_first_chapter_input()

    context, _temperature = build_llm_context(
        input_data,
        number_paragraphs_fn=lambda text: f"[第1段] {text}",
        local_issues=[],
    )

    assert context["chapter_number"] == 1
    assert context["has_previous_chapter"] is False
    assert context["is_first_chapter"] is True
    assert context["boundary_window_policy"]["skip_previous_chapter_handoff"] is True
    assert context["chapter_plan"]["opening_contract"] == "首章建立章内场景与视角。"
    assert "numbered_chapter_text" in context


@pytest.mark.asyncio
async def test_first_chapter_continuity_runs_llm_instead_of_auto_full_score(
    monkeypatch: pytest.MonkeyPatch,
    router,
    builder,
) -> None:
    input_data = _make_first_chapter_input()
    captured_context: dict[str, object] = {}

    async def _fake_call_with_retry(self, task_type, context, **kwargs):
        captured_context.update(context)
        return {
            "continuity_score": 6.8,
            "summary": "首章章内信息承接突兀。",
            "issues": [
                {
                    "issue_type": "information_consistency",
                    "severity": "medium",
                    "location": "第2段",
                    "summary": "第2段突然出现密令，前文没有信息来源。",
                    "evidence": "苏婉在城门外听见密令",
                    "fix_actions": ["补充密令来源或前置线索。"],
                }
            ],
        }

    monkeypatch.setattr(ContinuityEvalStep, "_call_with_retry", _fake_call_with_retry)
    step = ContinuityEvalStep(router, builder, settings=Settings(_env_file=None))

    report = await step.run(input_data)

    assert captured_context["has_previous_chapter"] is False
    assert report.continuity_score == pytest.approx(6.8)
    assert [issue.issue_type for issue in report.issues] == ["information_consistency"]


def test_continuity_score_stays_above_four_for_minor_gaps() -> None:
    input_data = _build_input()
    payload = {
        "issues": [
            {
                "issue_type": "opening_gap",
                "severity": "medium",
                "summary": "开场感官描写还可加强。",
            },
            {
                "issue_type": "information_consistency",
                "severity": "low",
                "summary": "赵成安的反应还可多一层铺垫。",
            },
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(payload, input_data)

    assert normalized["continuity_score"] > 7.0
    assert len(normalized["issues"]) == 2
    # carry_forward_missing has requires_llm_judgment=True → forwarded to LLM as hint only,
    # not independently injected into the final issues list to avoid false positives.


def test_continuity_detects_custody_break_as_hard_issue() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.previous_chapter_ending = "周明被押回耳房，门外两名衙役看守。"
    input_data.chapter_bridge.opening_location = "坊市"
    input_data.chapter_bridge.action_handoff = "周明前往坊市调查粮价。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issue_types = {issue["issue_type"]: issue for issue in normalized["issues"]}
    assert "custody_break" in issue_types
    assert issue_types["custody_break"]["severity"] == "high"
    assert normalized["continuity_score"] <= 7.5


def test_continuity_does_not_claim_reading_power_quality_issues() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_plan.forbidden_elements = ["金丝微颤"]
    input_data.chapter_plan.forbidden_elements_soft = ["冷白灯光"]
    repeated = "周明守着账簿，等下一步消息。"
    input_data.chapter_text = (
        f"{repeated}延续上章末尾的视角与情绪。\n"
        f"亥初六刻，金丝微颤一下，冷白灯光在墙面滑过去。\n"
        f"{repeated}{repeated}"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issue_types = {issue["issue_type"] for issue in normalized["issues"]}
    assert "prompt_leak" not in issue_types
    assert "time_marker_invalid" not in issue_types
    assert "text_repetition" not in issue_types
    assert "forbidden_element_violation" not in issue_types
    assert "forbidden_element_usage" not in issue_types


def test_carry_forward_soft_matching_accepts_semantic_cues() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = [
        "周明确认警告录音提到‘镜渊’连接异常端口并标记自己"
    ]
    input_data.chapter_text = "那段警告录音里提到“镜渊”正在连接异常端口，而名单上也出现了周明。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    assert not any(issue["issue_type"] == "carry_forward_missing" for issue in normalized["issues"])


def test_continuity_ignores_hard_and_soft_forbidden_elements() -> None:
    input_data = _build_input()
    input_data.chapter_plan.forbidden_elements = ["金丝微颤"]
    input_data.chapter_plan.forbidden_elements_soft = ["冷白灯光"]
    input_data.chapter_text = "金丝微颤一下，冷白灯光在墙面滑过去。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issues = {issue["issue_type"]: issue for issue in normalized["issues"]}

    assert "forbidden_element_violation" not in issues
    assert "forbidden_element_usage" not in issues


def test_continuity_llm_context_uses_scoped_contract_fields() -> None:
    input_data = _build_input()
    input_data.chapter_plan.forbidden_elements = ["不应进入连续性提示词"]
    input_data.chapter_state_packet.canon_context = {"plot_threads": ["不应进入连续性提示词"]}

    context, _temperature = build_llm_context(
        input_data,
        number_paragraphs_fn=lambda text: f"[第1段] {text}",
        local_issues=[],
    )

    assert set(context["chapter_bridge"]) == {
        "from_chapter",
        "to_chapter",
        "opening_time",
        "opening_location",
        "opening_pov",
        "transition_mode",
        "emotional_carryover",
        "action_handoff",
        "causal_link",
        "bridge_summary",
        "opening_acceptance_criteria",
    }
    assert context["chapter_bridge"]["from_chapter"] == 2
    assert context["chapter_bridge"]["to_chapter"] == 3
    assert "forbidden_elements" not in context["chapter_plan"]
    assert "canon_context" not in context["chapter_state_packet"]
    assert "不应进入连续性提示词" not in str(context)


def test_soft_forbidden_usage_does_not_affect_continuity_score() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_plan.forbidden_elements_soft = ["冷白灯光"]
    input_data.chapter_text = "周明守着账簿，等下一步消息。冷白灯光在墙面滑过去。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issues = {issue["issue_type"]: issue for issue in normalized["issues"]}

    assert "forbidden_element_usage" not in issues
    assert normalized["continuity_score"] >= 9.5


def test_issue_type_aliases_are_normalized() -> None:
    input_data = _build_input()
    payload = {
        "issues": [
            {"issue_type": "bridge_contract_violation", "severity": "high", "summary": "桥接冲突"},
            {"issue_type": "forbidden_element_reuse", "severity": "medium", "summary": "复用问题"},
            {
                "issue_type": "fact_error",
                "severity": "low",
                "summary": "上一章已建立的事实承接误差",
            },
            {
                "issue_type": "pov_violation",
                "severity": "medium",
                "summary": "开场 POV 与 opening_pov 冲突",
            },
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(payload, input_data)
    issue_types = {item["issue_type"] for item in normalized["issues"]}
    assert "bridge_contract_not_followed" in issue_types
    assert "forbidden_element_usage" not in issue_types
    assert "factual_error" in issue_types
    assert "pov_jump" in issue_types


def test_resolved_recheck_notes_do_not_penalize_continuity_score() -> None:
    input_data = _build_input()
    payload = {
        "issues": [
            {
                "issue_type": "opening_gap",
                "severity": "medium",
                "summary": "经验证不成立，无需修复。",
                "fix_mode": "none",
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    assert normalized["continuity_score"] == 10.0
    assert normalized["issues"] == []


def test_blocking_summary_without_issues_is_synthesized_as_repairable_issue() -> None:
    input_data = _build_input()
    payload = {
        "continuity_score": 10.0,
        "summary": "时间线存在严重混乱：第12段与第18段重复描写同一事件，造成尾部倒流。",
        "issues": [],
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    assert normalized["continuity_score"] < 8.5
    assert len(normalized["issues"]) == 1
    issue = normalized["issues"][0]
    assert issue["issue_type"] == "continuity_gap"
    assert issue["severity"] == "high"
    assert issue["status"] == "open"
    assert "LLM summary claimed" in issue["diagnostic_note"]


def test_contextual_forbidden_with_note_suffix_is_not_a_hard_violation() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_plan.closing_contract = "章末必须回到外滩钟楼前，留下下一章交接点。"
    input_data.chapter_plan.forbidden_elements = ["外滩钟楼（场景锚点）——仅在本章老照片中出现"]
    input_data.chapter_text = "周明在外滩钟楼前停下，把账簿交给等候的人。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)
    issue_types = {item["issue_type"] for item in normalized["issues"]}

    assert "forbidden_element_violation" not in issue_types


def test_continuity_normalization_preserves_precise_paragraph_contract() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_text = (
        "第一段仍在耳房等消息。\n\n"
        "第二段里，周明忽然坐到坊市茶摊旁，正文没有交代离开耳房的过程。\n\n"
        "第三段收束账簿线索。"
    )
    payload = {
        "issues": [
            {
                "issue_type": "bridge_contract_not_followed",
                "severity": "high",
                "location": "第2段（桥接动作缺失处）",
                "paragraph_start": 2,
                "paragraph_end": 2,
                "location_confidence": 0.91,
                "anchor_type": "explicit_para",
                "summary": "正文从耳房直接跳到坊市茶摊，缺少桥接动作。",
                "evidence": "第二段里，周明忽然坐到坊市茶摊旁，正文没有交代离开耳房的过程。",
                "evidence_quote": "周明忽然坐到坊市茶摊旁",
                "fix_mode": "insert",
                "rewrite_scope": "paragraph",
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    issue = normalized["issues"][0]
    assert issue["location"] == "第2段（桥接动作缺失处）"
    assert issue["paragraph_start"] == 2
    assert issue["paragraph_end"] == 2
    assert issue["location_confidence"] == 0.91
    assert issue["anchor_type"] == "explicit_para"
    assert issue["evidence_quote"] == "周明忽然坐到坊市茶摊旁"
    assert issue["fix_mode"] == "insert"


def test_continuity_detects_bridge_handoff_missing_in_opening() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_bridge.action_handoff = "赵成安以铜钥解开囚锁并押周明穿过北廊。"
    input_data.chapter_text = "周明坐在坊市茶摊旁，听雨滴敲打伞骨，盯着街口的车辙。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issues = {item["issue_type"]: item for item in normalized["issues"]}
    assert "bridge_contract_not_followed" in issues
    assert issues["bridge_contract_not_followed"]["severity"] == "high"


def test_continuity_bridge_handoff_paraphrase_does_not_trigger_local_fallback() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_bridge.action_handoff = "沈鹿溪拎着摄影包，踩着民宿外搭的木梯爬上楼顶。"
    input_data.chapter_text = (
        "清晨，沈鹿溪背着摄影包，民宿木梯在脚下发出轻响，"
        "楼顶的风先抵住镜头。"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issues = {item["issue_type"]: item for item in normalized["issues"]}
    assert "bridge_contract_not_followed" not in issues


def test_continuity_does_not_flag_location_jump_for_declared_pov_switch() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.previous_exit_state.location = "沈念卿公寓"
    input_data.chapter_state_packet.previous_exit_state.pov = "沈念卿"
    input_data.chapter_bridge.opening_location = "智云科技办公室"
    input_data.chapter_bridge.opening_pov = "陆云峥"
    input_data.chapter_bridge.transition_mode = "pov_switch"
    input_data.chapter_bridge.action_handoff = (
        "浦东另一端的陆云峥同样握着怀表望向窗外夜色，各自迎接黎明。"
    )
    input_data.chapter_text = "他不知何时从窗前睡去。醒来时，晨光已穿透百叶窗。"
    input_data.pov_switch = True

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issues = {item["issue_type"]: item for item in normalized["issues"]}
    assert "location_jump" not in issues


def test_continuity_ignores_invalid_time_marker_expression() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    input_data.chapter_text = "亥初六刻，周明在耳房听见铜铃一响。"

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issues = {item["issue_type"]: item for item in normalized["issues"]}
    assert "time_marker_invalid" not in issues


def test_continuity_ignores_exact_sentence_repetition() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    repeated = "周明抬眼看向门缝，听见风里夹着铁器摩擦声。"
    input_data.chapter_text = (
        f"{repeated}\n他把账页压在掌心，指节因用力发白。\n{repeated}\n{repeated}"
    )

    normalized = ContinuityEvalStep._normalize_report({}, input_data)

    issues = {item["issue_type"]: item for item in normalized["issues"]}
    assert "text_repetition" not in issues


def test_continuity_normalization_coerces_structured_fix_actions_to_strings() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.must_carry_forward = []
    payload = {
        "issues": [
            {
                "issue_type": "bridge_contract_not_followed",
                "severity": "high",
                "summary": "桥接动作未落地。",
                "location": "第2段",
                "paragraph_start": 2,
                "paragraph_end": 2,
                "fix_actions": [
                    {
                        "paragraph": 2,
                        "original": "“王尚书。”",
                        "suggestion": "“王员外郎。”",
                    },
                    {
                        "target_text": "多出来的一百里，够装三千石粮草。",
                        "replacement_text": "多出来的一百二十里，够装四千石粮草。",
                        "rationale": "统一数字链路。",
                    },
                ],
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(payload, input_data)
    issue = normalized["issues"][0]

    assert issue["fix_actions"] == [
        "第2段；将““王尚书。””改为““王员外郎。””",
        "将“多出来的一百里，够装三千石粮草。”替换为“多出来的一百二十里，够装四千石粮草。”；理由：统一数字链路。",
    ]

    report = ContinuityReport.model_validate(normalized)
    assert report.issues[0].fix_actions == issue["fix_actions"]


def test_continuity_issue_semantics_adds_repair_contract() -> None:
    input_data = _build_input()
    input_data.chapter_text = "第一段。\n\n第二段。\n\n第三段。\n\n第四段。"
    input_data.chapter_state_packet.must_carry_forward = [
        "上一章留下的账簿残页必须继续影响周明判断"
    ]
    input_data.chapter_bridge.action_handoff = "周明将账簿残页压进袖中，继续等赵成安传信。"
    payload = {
        "issues": [
            {
                "issue_type": "opening_gap",
                "severity": "high",
                "summary": "开头没有回扣上一章残页与等待状态。",
                "location": "第1段",
                "paragraph_start": 1,
                "paragraph_end": 1,
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    issue = normalized["issues"][0]
    assert issue["issue_id"].startswith("ch003-cont-")
    assert issue["repair_surface"] == "chapter_text"
    assert issue["status"] == "open"
    assert issue["blocking"] is True
    assert issue["fix_mode"] == "window"
    assert issue["paragraph_start"] == 1
    assert issue["paragraph_end"] == 3
    assert any(
        anchor["source"] == "chapter_bridge.action_handoff" for anchor in issue["missing_anchors"]
    )
    assert issue["postconditions"][0]["validator_id"] == "opening_transition_validator"
    directive = issue["repair_directive"]
    assert directive["target_window"] == "本章开头第 1-3 段"
    assert directive["repair_order"] == "late_if_conflict"
    assert any("上一章结尾 5 段" in item for item in directive["required_context"])
    assert any("动作接力" in item for item in directive["required_anchors"])


def test_continuity_opening_directive_uses_configured_boundary_window() -> None:
    input_data = _build_input().model_copy(
        update={
            "chapter_text": "第一段硬切到新调查。\n\n第二段继续新任务。\n\n第三段才回忆上章。",
            "boundary_prev_tail_paragraphs": 4,
            "boundary_opening_paragraphs": 2,
        }
    )
    payload = {
        "issues": [
            {
                "issue_type": "opening_gap",
                "severity": "high",
                "summary": "开头没有承接上一章动作。",
                "paragraph_start": 1,
                "paragraph_end": 1,
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    issue = normalized["issues"][0]
    assert issue["paragraph_end"] == 2
    directive = issue["repair_directive"]
    assert directive["target_window"] == "本章开头第 1-2 段"
    assert any("上一章结尾 4 段" in item for item in directive["required_context"])
    assert any("本章开头 2 段" in item for item in directive["required_context"])


def test_continuity_llm_context_exposes_state_projection_and_plan_contracts() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.narrative_state_projection = {
        "characters": {"周明": {"knowledge": "已知账簿残页来自官仓"}},
    }
    input_data.chapter_state_packet.chapter_contract = {
        "must_preserve": ["周明仍在等待赵成安回信"],
    }
    input_data.chapter_plan.required_state_transitions = ["周明从等待转为主动查证"]
    input_data.chapter_plan.key_revelations = ["账簿残页指向平粜基金"]
    input_data.chapter_plan.foreshadowing_plan = ["赵成安迟迟未到"]

    context, _ = build_llm_context(
        input_data,
        number_paragraphs_fn=ContinuityEvalStep._number_paragraphs,
        use_local_as_prescreen=False,
    )

    assert context["chapter_bridge"]["from_chapter"] == 2
    assert context["chapter_bridge"]["to_chapter"] == 3
    assert (
        context["chapter_state_packet"]["narrative_state_projection"]["characters"]["周明"][
            "knowledge"
        ]
        == "已知账簿残页来自官仓"
    )
    assert context["chapter_state_packet"]["chapter_contract"]["must_preserve"] == [
        "周明仍在等待赵成安回信"
    ]
    assert context["chapter_plan"]["required_state_transitions"] == ["周明从等待转为主动查证"]
    assert context["chapter_plan"]["key_revelations"] == ["账簿残页指向平粜基金"]
    assert context["chapter_plan"]["foreshadowing_plan"] == ["赵成安迟迟未到"]
    assert context["boundary_window_policy"]["previous_tail_paragraphs"] == 5
    assert context["boundary_window_policy"]["opening_paragraphs"] == 3


def test_location_jump_becomes_local_rule_when_declared_pov_switch() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.previous_exit_state.location = "沈念卿公寓"
    input_data.chapter_state_packet.previous_exit_state.pov = "沈念卿"
    input_data.chapter_bridge.opening_location = "智云科技办公室"
    input_data.chapter_bridge.opening_pov = "陆云峥"
    input_data.chapter_bridge.transition_mode = "pov_switch"
    input_data.chapter_bridge.action_handoff = "镜头切到浦东另一端的办公室，陆云峥接住上一章余波。"
    payload = {
        "issues": [
            {
                "issue_type": "location_jump",
                "severity": "high",
                "summary": "地点从公寓跳到办公室。",
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    issue = normalized["issues"][0]
    assert issue["repair_surface"] == "local_rule"
    assert issue["status"] == "suppressed"
    assert normalized["continuity_score"] == 10.0


def test_high_confidence_location_jump_survives_declared_pov_switch() -> None:
    input_data = _build_input()
    input_data.chapter_state_packet.previous_exit_state.location = "沈念卿公寓"
    input_data.chapter_state_packet.previous_exit_state.pov = "沈念卿"
    input_data.chapter_bridge.opening_location = "智云科技办公室"
    input_data.chapter_bridge.opening_pov = "陆云峥"
    input_data.chapter_bridge.transition_mode = "pov_switch"
    input_data.chapter_bridge.action_handoff = "镜头切到浦东另一端的办公室，陆云峥接住上一章余波。"
    payload = {
        "issues": [
            {
                "issue_type": "location_jump",
                "severity": "high",
                "summary": "正文第1段仍写沈念卿醒在公寓，与陆云峥办公室开场冲突。",
                "location": "第1段",
                "location_confidence": 0.92,
                "paragraph_start": 1,
                "paragraph_end": 1,
            }
        ]
    }

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=[],
        use_local_as_prescreen=False,
    )

    issue = normalized["issues"][0]
    assert issue["repair_surface"] == "chapter_text"
    assert issue["status"] == "open"
    assert normalized["continuity_score"] <= 5.5


def test_local_prescreen_not_merged_when_llm_clears_continuity() -> None:
    input_data = _build_input()
    payload = {
        "continuity_score": 9.0,
        "summary": "跨章承接完整，未发现需要修复的连贯性问题。",
        "issues": [],
    }
    local_issues = [
        {
            "issue_type": "custody_break",
            "severity": "high",
            "confidence": 0.95,
            "summary": "本地预筛误报的受控状态断裂。",
        }
    ]

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=local_issues,
    )

    assert normalized["issues"] == []
    assert normalized["continuity_score"] == 9.0


def test_local_prescreen_only_merges_when_llm_agrees_on_issue_type() -> None:
    input_data = _build_input()
    payload = {
        "continuity_score": 8.2,
        "summary": "开场承接略弱。",
        "issues": [
            {
                "issue_type": "opening_gap",
                "severity": "medium",
                "summary": "开场需要补足上一章动作余波。",
            }
        ],
    }
    local_issues = [
        {
            "issue_type": "custody_break",
            "severity": "high",
            "confidence": 0.95,
            "summary": "本地预筛不应单独升级为最终问题。",
        },
        {
            "issue_type": "opening_gap",
            "severity": "low",
            "confidence": 0.95,
            "summary": "本地预筛也观察到开场承接偏弱。",
        },
    ]

    normalized = ContinuityEvalStep._normalize_report(
        payload,
        input_data,
        local_issues=local_issues,
    )

    issue_types = [issue["issue_type"] for issue in normalized["issues"]]
    assert "opening_gap" in issue_types
    assert "custody_break" not in issue_types
    assert normalized["continuity_score"] == 8.2
