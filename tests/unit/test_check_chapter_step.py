"""Tests for deterministic chapter-level leak detection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.pipeline.steps.check_chapter_step import ChapterRepairInput, ChapterRepairStep


def test_chapter_repair_merges_local_prompt_leaks() -> None:
    report = ChapterRepairStep._normalize_payload(
        {"risk_level": "low", "summary": "", "prompt_leaks": []},
        local_prompt_leaks=["【交接】", "预期扰动路径"],
    )

    assert report.risk_level == "medium"
    assert report.prompt_leaks == ["【交接】", "预期扰动路径"]


def test_chapter_repair_report_embeds_review_contracts() -> None:
    report = ChapterRepairStep._normalize_payload(
        {
            "risk_level": "high",
            "summary": "存在提示泄露。",
            "prompt_leaks": ["【交接】"],
            "repair_actions": ["删除规划层标记"],
        },
        chapter_text="她推开门，看见【交接】两个字刻在纸边。",
        chapter_number=4,
        check_mode="delta",
    )

    assert report.review_mode == "targeted_recheck"
    assert report.source_text_hash
    assert len(report.review_findings) == 1
    assert report.review_findings[0].review_mode == "targeted_recheck"
    assert report.review_findings[0].chapter_number == 4
    assert report.repair_tickets
    assert report.repair_readiness["status"] == "ready"
    assert report.repair_readiness["auto_repair_candidate_count"] == 1
    assert report.repair_tickets[0].source_text_hash == report.source_text_hash


def test_chapter_repair_payload_schema_contract_consumer_chain() -> None:
    payload = {
        "risk_level": "high",
        "summary": "存在提示泄露和表达问题。",
        "prompt_leaks": ["【交接】"],
        "factual_errors": [],
        "continuity_errors": [],
        "expression_errors": ["重复解释同一动作。"],
        "repair_actions": ["删除提示层标记。"],
        "forbidden_element_findings": [],
    }

    validate_response_schema(payload, TaskType.CHECK_CHAPTER)
    validate_json_output_contract(TaskType.CHECK_CHAPTER, payload)
    report = ChapterRepairStep._normalize_payload(
        payload,
        chapter_text="她推开门，看见【交接】两个字刻在纸边。",
        chapter_number=4,
    )

    assert report.risk_level == "high"
    assert report.prompt_leaks == ["【交接】"]
    assert [issue.summary for issue in report.expression_errors] == ["重复解释同一动作。"]
    assert report.repair_tickets


def test_chapter_repair_keeps_one_structured_llm_finding_as_one_ticket() -> None:
    report = ChapterRepairStep._normalize_payload(
        {
            "risk_level": "low",
            "factual_errors": [
                {
                    "type": "rule_violation",
                    "rule_id": "WR003",
                    "description": "主角未提前一日告知便接受术式。",
                    "evidence": "她当夜便按下了阵眼。",
                    "severity": "low",
                    "suggestion": "补入提前告知的可见动作。",
                }
            ],
        },
        chapter_text="她当夜便按下了阵眼。",
        chapter_number=3,
    )

    assert len(report.factual_errors) == 1
    issue = report.factual_errors[0]
    assert issue.issue_type == "rule_violation"
    assert issue.severity == "low"
    assert issue.evidence == "她当夜便按下了阵眼。"
    factual_findings = [
        finding
        for finding in report.review_findings
        if finding.metadata.get("chapter_quality_category") == "factual_error"
    ]
    assert len(factual_findings) == 1
    assert factual_findings[0].severity == "low"


def test_chapter_repair_contract_rejects_missing_required_field() -> None:
    payload = {
        "risk_level": "low",
        "summary": "未发现问题。",
        "prompt_leaks": [],
        "factual_errors": [],
        "continuity_errors": [],
        "expression_errors": [],
        "repair_actions": [],
    }

    with pytest.raises(KeyError, match="forbidden_element_findings"):
        validate_json_output_contract(TaskType.CHECK_CHAPTER, payload)


def test_build_delta_recheck_payload_uses_delta_for_local_change() -> None:
    previous = (
        "第一段，主角进入档案室，意识到门锁被人动过。\n\n"
        "第二段，她在书架后找到一张被折起的纸条。\n\n"
        "第三段，她听见走廊尽头传来脚步声。"
    )
    revised = (
        "第一段，主角进入档案室，意识到门锁被人动过。\n\n"
        "第二段，她在书架后找到一张被折起的纸条，纸角还沾着潮湿的灰。\n\n"
        "第三段，她听见走廊尽头传来脚步声。"
    )

    mode, sections, ratio = ChapterRepairStep.build_delta_recheck_payload(previous, revised)

    assert mode == "delta"
    assert ratio > 0.9
    assert len(sections) == 1
    assert sections[0]["change_type"] == "replace"
    assert "潮湿的灰" in sections[0]["after"]


def test_build_delta_recheck_payload_falls_back_to_full_for_broad_rewrite() -> None:
    previous = (
        "第一段，主角在港口等待天亮。\n\n"
        "第二段，她回想昨夜的争执。\n\n"
        "第三段，船笛声逼近，决定已经没有退路。"
    )
    revised = (
        "暴雨直接在城市上空炸开，新的旁观视角先交代了港口以外的军方调动。\n\n"
        "随后章节改写为多人群像，回溯三天前的秘密会面与另一条支线任务。\n\n"
        "结尾不再停在港口，而是切到山区据点，揭示新的敌对阵营。"
    )

    mode, sections, ratio = ChapterRepairStep.build_delta_recheck_payload(previous, revised)

    assert mode == "full"
    assert sections == []
    assert ratio < 0.72


def test_chapter_repair_llm_context_is_scoped_to_check_fields() -> None:
    context = ChapterRepairStep._build_llm_context(
        ChapterRepairInput(
            chapter_number=2,
            chapter_text="正文",
            canon_context=SimpleNamespace(
                characters={
                    "林晚": SimpleNamespace(
                        alive=True,
                        location="机库",
                        emotional_state="警惕",
                        secret_global_notes="不应进入提示词",
                    )
                },
                plot_threads=["不应进入提示词"],
            ),
            character_profiles=[
                {
                    "name": "林晚",
                    "role": "工程师",
                    "personality": "冷静",
                    "private_notes": "不应进入提示词",
                }
            ],
            previous_chapter_ending="不应转发的上一章结尾",
            time_convention="不应转发的时间规则",
            address_rules="称谓规则",
            world_context_rules="世界观硬规则",
        ),
        check_mode="full",
    )

    assert set(context["canon_context"]) == {"characters"}
    assert context["canon_context"]["characters"]["林晚"] == {
        "alive": True,
        "location": "机库",
        "emotional_state": "警惕",
    }
    assert context["character_profiles"] == [
        {"name": "林晚", "role": "工程师", "personality": "冷静", "backstory": ""}
    ]
    assert "previous_chapter_ending" not in context
    assert "time_convention" not in context
    assert "plot_threads" not in context["canon_context"]


def test_chapter_repair_llm_context_includes_scoped_creative_contract() -> None:
    context = ChapterRepairStep._build_llm_context(
        ChapterRepairInput(
            chapter_number=2,
            chapter_text="正文",
            canon_context={},
            scene_intents=[
                {
                    "scene_id": "scene_01",
                    "summary": "林晚在机库门口压住真实来意",
                    "emotional_beat": "警惕转为试探",
                    "sensory_focus": "听觉+触觉",
                    "dialogue_subtext": "表面询问维修记录，实际确认谁动过引擎。",
                    "required_outcome": "不应进入创意合同",
                    "future_payoff": "不应进入创意合同",
                },
                {
                    "scene_id": "scene_02",
                    "summary": "没有创意字段的普通场景",
                    "required_outcome": "只推进主线",
                },
            ],
        ),
        check_mode="full",
    )

    assert context["creative_contract"] == [
        {
            "scene_id": "scene_01",
            "summary": "林晚在机库门口压住真实来意",
            "emotional_beat": "警惕转为试探",
            "sensory_focus": "听觉+触觉",
            "dialogue_subtext": "表面询问维修记录，实际确认谁动过引擎。",
        }
    ]
    assert "required_outcome" not in context["creative_contract"][0]
    assert "future_payoff" not in context["creative_contract"][0]


def test_chapter_repair_delta_context_does_not_recheck_full_creative_contract() -> None:
    context = ChapterRepairStep._build_llm_context(
        ChapterRepairInput(
            chapter_number=2,
            chapter_text="正文",
            canon_context={},
            scene_intents=[
                {
                    "scene_id": "scene_01",
                    "summary": "林晚在机库门口压住真实来意",
                    "emotional_beat": "警惕转为试探",
                    "sensory_focus": "听觉+触觉",
                    "dialogue_subtext": "表面询问维修记录，实际确认谁动过引擎。",
                }
            ],
        ),
        check_mode="delta",
    )

    assert "creative_contract" not in context


def test_chapter_repair_local_quality_signals_feed_report() -> None:
    repeated = "周明抬眼看向门缝，听见风里夹着铁器摩擦声。"
    input_data = ChapterRepairInput(
        chapter_number=2,
        chapter_text=(
            f"{repeated}\n亥初六刻，金丝微颤一下，冷白灯光在墙面滑过去。\n{repeated}\n{repeated}"
        ),
        canon_context={},
        forbidden_elements=["金丝微颤"],
        forbidden_elements_soft=["冷白灯光"],
    )
    local_quality = ChapterRepairStep._build_local_quality_payload(input_data)

    report = ChapterRepairStep._normalize_payload(
        {"risk_level": "low", "summary": "", "repair_actions": []},
        local_factual_errors=local_quality["factual_errors"],
        local_expression_errors=local_quality["expression_errors"],
        local_repair_actions=local_quality["repair_actions"],
        local_forbidden_candidates=local_quality["forbidden_element_candidates"],
        chapter_text=input_data.chapter_text,
    )

    assert report.risk_level == "medium"
    assert any("非法时辰" in issue.summary for issue in report.factual_errors)
    assert {(item["level"], item["forbidden"]) for item in report.forbidden_element_candidates} == {
        ("hard", "金丝微颤"),
        ("soft", "冷白灯光"),
    }
    assert not any("硬禁元素" in issue.summary for issue in report.expression_errors)
    assert not any("软禁元素" in issue.summary for issue in report.expression_errors)
    assert any("整句级重复" in issue.summary for issue in report.expression_errors)
    assert report.repair_actions


def test_chapter_repair_forbidden_findings_require_llm_semantics() -> None:
    report = ChapterRepairStep._normalize_payload(
        {
            "risk_level": "low",
            "summary": "",
            "forbidden_element_findings": [
                {
                    "forbidden": "金丝微颤",
                    "matched": "金丝微颤",
                    "verdict": "mechanical_reuse",
                    "severity": "medium",
                    "blocking": False,
                    "reason": "仅复刻近章意象，没有新的叙事功能。",
                    "replacement_advice": "改为听觉或触觉通道承载紧张感。",
                },
                {
                    "forbidden": "账簿残页",
                    "matched": "账簿残页",
                    "verdict": "story_anchor",
                    "severity": "none",
                    "blocking": False,
                    "reason": "这是本章承接所需道具。",
                },
            ],
        },
        chapter_text="金丝微颤，账簿残页仍压在袖中。",
    )

    assert any("禁用元素机械复用" in issue.summary for issue in report.expression_errors)
    assert not any("账簿残页" in issue.summary for issue in report.expression_errors)
    assert "改为听觉或触觉通道承载紧张感。" in report.repair_actions


def test_chapter_repair_unverified_forbidden_findings_do_not_penalize() -> None:
    report = ChapterRepairStep._normalize_payload(
        {
            "risk_level": "low",
            "summary": "",
            "forbidden_element_findings": [
                {
                    "forbidden": "这是一个未决的线索",
                    "matched": "这是一个未决的线索",
                    "verdict": "violation",
                    "severity": "high",
                    "blocking": True,
                    "confidence": 0.92,
                    "reason": "疑似模板化线索说明。",
                    "replacement_advice": "改为正文内可感知的具体行动。",
                }
            ],
        },
        chapter_text="她把文件夹合上，只听见会议室外有人停步。",
    )

    finding = report.forbidden_element_findings[0]
    assert finding["evidence_verified"] is False
    assert finding["verdict"] == "uncertain"
    assert finding["blocking"] is False
    assert report.expression_errors == []
    assert report.risk_level == "low"


def test_to_string_list_recovers_issue_from_python_repr_dict() -> None:
    """repr(dict) string items should yield the actual issue text, not the repr.

    Some LLMs return a list of Python repr-of-dict strings instead of a list
    of dicts. Without recovery, downstream substring filters operate on the
    whole repr blob and may false-positive or false-negative on filter tokens.
    The recovery helper picks the most descriptive field (issue/text/etc).
    """
    repr_item = (
        "{'text': '那是蜀国影卫的密语', "
        "'location': '沈清漪听到笛声后', "
        "'issue': '叙述者直接解释密语含义（pov_knowledge_breach）', "
        "'severity': 'high'}"
    )
    out = ChapterRepairStep._to_string_list([repr_item])
    assert out == ["叙述者直接解释密语含义（pov_knowledge_breach）"]


def test_to_string_list_handles_mixed_list_of_dicts_and_repr_strings() -> None:
    """Both dict and repr-dict items in the same list should be normalized."""
    items = [
        {"text": "原稿片段", "issue": "措辞问题"},
        "{'issue': 'pov 越权', 'severity': 'high'}",
        "普通字符串条目",
    ]
    out = ChapterRepairStep._to_string_list(items)
    assert "issue: 措辞问题" in out  # dict item, "key: value" form
    assert "pov 越权" in out  # repr-dict item, recovered verbatim
    assert "普通字符串条目" in out  # plain string, unchanged


def test_to_string_list_keeps_non_repr_strings_unchanged() -> None:
    """Strings that are not repr-of-dict should pass through verbatim."""
    out = ChapterRepairStep._to_string_list(["事实错误：称谓与身份规则冲突", "时间系统使用不准确"])
    assert out == ["事实错误：称谓与身份规则冲突", "时间系统使用不准确"]


def test_coerce_repr_dict_recovers_action_field() -> None:
    """repair_actions repr-dicts use 'action'/'target' keys, not 'issue'/'text'.

    Without 'action' in the priority list, _coerce_repr_dict_to_string would
    fall through to the first string value (insertion-order), which is typically
    'target' like 'factual_errors[0]' — useless for downstream substring matching.
    """
    repr_action = (
        "{'target': 'factual_errors[0]', "
        "'action': '将「奏折」改为「奏牍」以符合古代官文称谓', "
        "'priority': 'high'}"
    )
    out = ChapterRepairStep._to_string_list([repr_action])
    assert out == ["将「奏折」改为「奏牍」以符合古代官文称谓"]


def test_coerce_repr_dict_to_string_extracts_action_field() -> None:
    """repair_actions repr-dicts use target/action/priority keys, not issue/text.

    The 'action' key should be picked after issue/text/description/summary but
    before falling back to the first string value (which would be 'target').
    """
    repr_item = (
        "{'target': 'factual_errors[0]', "
        "'action': '将「奏折」改为「奏牍」以符合古代官场用语', "
        "'priority': 'high'}"
    )
    out = ChapterRepairStep._coerce_repr_dict_to_string(repr_item)
    assert out == "将「奏折」改为「奏牍」以符合古代官场用语"


def test_coerce_repr_dict_to_string_issue_beats_action() -> None:
    """When both 'issue' and 'action' exist, 'issue' wins (higher priority)."""
    repr_item = "{'issue': 'pov_knowledge_breach', 'action': '改写为感官描写'}"
    out = ChapterRepairStep._coerce_repr_dict_to_string(repr_item)
    assert out == "pov_knowledge_breach"


def test_coerce_json_dict_to_string_uses_json_path() -> None:
    json_item = '{"target": "factual_errors[0]", "action": "改写官文称谓"}'
    out = ChapterRepairStep._coerce_repr_dict_to_string(json_item)
    assert out == "改写官文称谓"


def test_coerce_oversized_repr_dict_is_ignored() -> None:
    oversized = "{'action': '" + ("x" * 4097) + "'}"
    out = ChapterRepairStep._coerce_repr_dict_to_string(oversized)
    assert out is None
