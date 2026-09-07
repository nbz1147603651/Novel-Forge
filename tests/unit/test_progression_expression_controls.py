from __future__ import annotations

from novel_forge.editorial.signals import (
    build_expression_channel_records,
    classify_expression_channel,
    detect_expression_channel_hits,
)
from novel_forge.narrative_state.schemas import (
    PlotMilestoneIndex,
    exceeds_action_level,
    exceeds_cognitive_level,
)
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.pipeline.long.services.plot_milestones import select_milestone_window
from novel_forge.pipeline.steps.check_chapter_step import ChapterRepairInput, ChapterRepairStep
from novel_forge.pipeline.steps.contract_execution_audit_step import (
    ContractExecutionAuditInput,
    build_local_contract_execution_report,
    merge_contract_execution_adjudication,
)


def test_milestone_window_withholds_future_details_from_draft() -> None:
    index = PlotMilestoneIndex.model_validate(
        {
            "project_id": "demo",
            "total_chapters": 61,
            "milestones": [
                {
                    "milestone_id": "pm10",
                    "chapter_number": 10,
                    "title": "试探",
                    "summary": "沈念卿只发现怀表纹样异常，不能触发共振。",
                    "kind": "knowledge",
                    "status": "planned",
                },
                {
                    "milestone_id": "pm20",
                    "chapter_number": 20,
                    "title": "信物共振",
                    "summary": "陆云峥在古董店将怀表与沈念卿腕间金镯并置，两人触发强烈前世记忆。",
                    "kind": "item",
                    "status": "planned",
                    "payoff_only": True,
                },
            ],
        }
    )
    window = select_milestone_window(index, chapter_number=10, future=10).model_dump(mode="json")

    bridge_cards = build_stage_cards(stage="bridge", milestone_window=window)
    draft_cards = build_stage_cards(stage="draft", milestone_window=window)

    assert bridge_cards["contract"]["milestone_window"]["future_guardrails"]
    assert draft_cards["contract"]["milestone_window"].get("future_guardrails", []) == []
    assert draft_cards["stage_visibility"]["visible_future_guardrails"] == 0
    assert draft_cards["stage_visibility"]["withheld_future_count"] >= 1


def test_cognitive_and_action_level_ordering_helpers() -> None:
    assert exceeds_cognitive_level("confirmed", "suspicion") is True
    assert exceeds_cognitive_level("suspicion", "confirmed") is False
    assert exceeds_action_level("revealed", "internal") is True
    assert exceeds_action_level("hinted", "acted") is False


def test_contract_execution_audit_lets_llm_own_future_payoff_judgment() -> None:
    input_data = ContractExecutionAuditInput(
        chapter_number=10,
        chapter_text="陆云峥把怀表与沈念卿腕间金镯并置，强烈前世记忆同时涌入两人脑海。",
        chapter_contract={
            "chapter_number": 10,
            "required_progressions": ["发现怀表纹样异常"],
            "allowed_progressions": ["怀表可以被提及但不触发共振"],
            "forbidden_progressions": ["怀表与金镯并置触发强烈前世记忆"],
            "future_leak_risks": ["信物共振高潮"],
        },
        milestone_window={
            "future_guardrails": [
                {
                    "chapter_number": 20,
                    "title": "信物共振高潮",
                    "summary": "未来节点功能边界：怀表与金镯并置触发强烈前世记忆。",
                    "payoff_only": True,
                }
            ]
        },
        strictness="block",
    )

    local = build_local_contract_execution_report(input_data)

    assert local.should_block_archive is False
    assert local.local_candidate_hits
    assert local.forbidden_progression_hits == []
    assert local.future_leak_hits == []

    report = merge_contract_execution_adjudication(
        local,
        {
            "verdict": "needs_repair",
            "severity": "critical",
            "rationale": "正文语义上已经完成第20章信物共振 payoff。",
            "forbidden_progression_hits": ["怀表与金镯并置触发强烈前世记忆"],
            "future_leak_hits": ["信物共振高潮"],
            "contract_completion_score": 3.0,
            "repair_or_replan_decision": "repair_or_replan",
        },
        strictness="block",
    )

    assert report.should_block_archive is True
    assert report.forbidden_progression_hits
    assert report.future_leak_hits


def test_contract_execution_audit_matches_knowledge_op_evidence_phrase() -> None:
    input_data = ContractExecutionAuditInput(
        chapter_number=4,
        chapter_text="林远终于确认，裂缝不是自然现象。",
        chapter_contract={
            "chapter_number": 4,
            "knowledge_ops": [
                {
                    "type": "known",
                    "character": "林远",
                    "description": "裂缝不是自然现象",
                    "evidence": "裂缝不是自然现象",
                }
            ],
        },
        strictness="strict",
    )

    local = build_local_contract_execution_report(input_data)

    assert local.missing_knowledge_ops == []
    assert not any(hit["category"] == "missing_knowledge_op" for hit in local.local_candidate_hits)


def test_contract_execution_audit_prescreens_future_cognitive_constraint() -> None:
    input_data = ContractExecutionAuditInput(
        chapter_number=2,
        chapter_text="沈清漪终于确认，东宫上下无人见过小书童。",
        chapter_contract={
            "chapter_number": 2,
            "cognitive_constraints": [
                {
                    "claim_id": "claim_ladder_15",
                    "claim_text": "第15章才能确认小书童无人见过",
                    "cognitive_subjects": ["沈清漪"],
                    "cognitive_object": "小书童无人见过",
                    "cognitive_level": "suspicion",
                    "action_level": "hinted",
                    "cognitive_chapter": 15,
                    "public_reveal_chapter": 45,
                }
            ],
        },
        strictness="block",
    )

    local = build_local_contract_execution_report(input_data)

    assert local.should_block_archive is False
    assert any(hit["category"] == "cognitive_constraint" for hit in local.local_candidate_hits)
    assert local.cognitive_constraint_hits == []

    report = merge_contract_execution_adjudication(
        local,
        {
            "verdict": "needs_repair",
            "severity": "high",
            "rationale": "正文把第15章才允许确认的认知提前写成坐实。",
            "cognitive_constraint_hits": ["小书童无人见过"],
            "contract_completion_score": 4.0,
            "repair_or_replan_decision": "repair",
        },
        strictness="block",
    )

    assert report.should_block_archive is True
    assert report.cognitive_constraint_hits == ["小书童无人见过"]
    assert report.future_leak_hits == ["小书童无人见过"]


def test_expression_variants_share_heart_stutter_channel() -> None:
    profiles = [
        {
            "channel_id": "heart_stutter",
            "channel": "somatic_reaction",
            "label": "心跳/胸口惊动式反应",
            "surface_forms": ["心跳漏了一拍", "胸口空了一瞬", "心脏像被轻轻撞了一下"],
            "trigger_contexts": ["惊讶", "警觉"],
            "risk_reason": "不同情绪反复使用同一身体反应。",
            "replacement_axes": ["动作选择", "对白延迟"],
            "allowed_when": "重大转折可少量保留。",
            "cooldown_chapters": 3,
            "confidence": 0.9,
        }
    ]
    first = classify_expression_channel("她的心跳漏了一拍", profiles=profiles)
    second = classify_expression_channel("他胸口空了一瞬", profiles=profiles)

    assert first["channel_id"] == "heart_stutter"
    assert second["channel_id"] == "heart_stutter"

    records = build_expression_channel_records(
        ["心跳漏了一拍"],
        source="motif",
        cooldown_chapters=3,
        profiles=profiles,
    )
    hits = detect_expression_channel_hits("她胸口空了一瞬。心脏像被轻轻撞了一下。", records)

    assert hits
    assert hits[0]["channel_id"] == "heart_stutter"
    assert "不要同义替换" in hits[0]["replacement_advice"]


def test_pov_marker_requirement_is_reported_locally() -> None:
    payload = ChapterRepairInput(
        chapter_number=8,
        chapter_text="陆云峥站在窗前。\n\n她深吸一口气，发动了车子。",
        canon_context={},
        pov_character="陆云峥",
        scene_intents=[
            {
                "scene_id": "s1",
                "pov_character": "陆云峥",
                "pov_switch_allowed": True,
                "pov_switch_marker_required": True,
            },
            {
                "scene_id": "s2",
                "pov_character": "沈念卿",
                "pov_switch_allowed": True,
                "pov_switch_marker_required": True,
            },
        ],
    )

    local = ChapterRepairStep._build_local_quality_payload(payload)
    issue_types = {
        issue.get("issue_type")
        for issue in local.get("advisory_issues", [])
        if isinstance(issue, dict)
    }

    assert "pov_switch_marker_missing" in issue_types
