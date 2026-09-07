from __future__ import annotations

import pytest
from pydantic import ValidationError

from novel_forge.editorial.cards import build_editorial_card
from novel_forge.editorial.metrics import (
    chapter_editorial_findings,
    detect_denouement_overrun,
    detect_paragraph_visual_fatigue,
    detect_title_repetition,
)
from novel_forge.editorial.revision_planner import build_structured_revision_plan
from novel_forge.editorial.schemas import (
    EditorialAuditReport,
    EditorialContract,
    EditorialRevisionAction,
)
from novel_forge.editorial.validators import validate_editorial_contract
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.pipeline.steps.book_audit_runner import BookAuditChapter


def _contract_payload() -> dict[str, object]:
    return {
        "project_title": "测试长篇",
        "character_voices": [
            {
                "character": "主角甲",
                "sentence_profile": "短句、准、会议纪要式。",
                "explanation_bias": "少解释，用事实和决策代替情绪。",
                "emotion_syntax": "情绪升高时更短。",
                "signature_moves": ["先看事实", "用结论截断"],
                "taboo_patterns": ["长篇命运独白"],
                "sample_lines": ["先看合同。"],
            }
        ],
        "climax_markers": [
            {
                "chapter_number": 40,
                "climax_type": "main",
                "description": "主线摊牌与核心选择完成。",
                "expected_aftermath_chapters": 6,
            }
        ],
        "denouement_budget": {
            "expected_chapters": 6,
            "max_confirmation_scenes": 2,
            "required_new_functions": ["余波后果", "关系制度化", "终场意象"],
            "forbidden_repeats": ["重复核心主题句"],
        },
        "theme_policies": ["主题通过选择和后果呈现，不直接解释。"],
        "symbol_policies": [
            {
                "symbol": "怀表",
                "narrative_function": "时间与承诺",
                "explanation_policy": "explain_once",
                "escalation_rule": "每次出现改变语境。",
                "max_explicit_explanations": 1,
            }
        ],
        "scene_resistance_rules": [
            {
                "scene_type": "对峙",
                "required_resistance": "必须有空间或流程阻力。",
                "examples": ["门被挡住", "签字窗口关闭"],
            }
        ],
        "expression_channel_budget": {
            "somatic_reaction": 1,
            "action_tag": 2,
            "dialogue_tag": 2,
            "sensory_anchor": 3,
        },
        "expression_channel_profiles": [
            {
                "channel_id": "heart_tightening",
                "channel": "somatic_reaction",
                "label": "心头/胸口惊动式反应",
                "surface_forms": ["心头一紧", "心头一跳"],
                "trigger_contexts": ["惊讶", "警觉"],
                "risk_reason": "不同情绪反复使用同一身体反应。",
                "replacement_axes": ["动作选择", "对白停顿"],
                "allowed_when": "重大身份线索或核心情感转折可少量保留。",
                "cooldown_chapters": 3,
                "actor_scope": "global",
                "confidence": 0.8,
                "evidence_quotes": ["怀表在掌心发烫，她心头一紧。"],
            }
        ],
        "body_signal_budget_per_high_emotion_scene": 1,
        "forbidden_confirmation_phrases": ["核心主题句"],
        "revision_priorities": ["先压结构"],
        "revelation_ladder": [
            {
                "thread": "信物线",
                "stage": "物证",
                "stage_order": 1,
                "target_chapter": 20,
                "trigger": "怀表刻字",
                "allowed_disclosure": "只确认怀表不是偶然出现。",
                "required_action_consequence": "主角主动查证来源。",
            }
        ],
        "editorial_element_directives": [
            {
                "element_id": "editorial_denouement_budget",
                "element_name": "高潮后余波预算",
                "directive_type": "压缩余波",
                "target_window": "主高潮后",
                "linked_characters": [],
                "requirement": "余波章节必须承担新功能。",
                "success_criteria": "读者能区分余波、制度化节点与终场。",
            }
        ],
        "time_bridge_policies": ["跨月转场必须明确标注。"],
        "title_policy": {
            "max_reuse": 2,
            "allowed_repeated_titles": ["核心回环标题"],
            "naming_strategy": "重复标题只保留核心回环。",
        },
    }


def test_editorial_contract_roundtrip_and_main_climax_required() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    dumped = contract.model_dump(mode="json")

    assert EditorialContract.model_validate(dumped).main_climax().chapter_number == 40

    invalid = dict(_contract_payload())
    invalid["climax_markers"] = [
        {
            "chapter_number": 22,
            "climax_type": "subplot",
            "description": "支线高潮",
            "expected_aftermath_chapters": 2,
        }
    ]
    with pytest.raises(ValidationError):
        EditorialContract.model_validate(invalid)


def test_editorial_contract_strips_revelation_ladder_explanatory_drift() -> None:
    payload = _contract_payload()
    payload["revelation_ladder"] = [
        {
            "thread": "信物线",
            "stage": "物证",
            "stage_order": 1,
            "target_chapter": 20,
            "trigger": "怀表刻字",
            "allowed_disclosure": "只确认怀表不是偶然出现。",
            "required_action_consequence": "主角主动查证来源。",
            "rationale": "模型解释字段不能进入严格 schema。",
            "evidence_based": "证据说明应留在生成过程，不进入契约。",
        }
    ]

    contract = EditorialContract.model_validate(payload)
    step = contract.revelation_ladder[0].model_dump(
        mode="json",
        exclude={"schema_version", "created_at"},
    )

    assert step == {
        "thread": "信物线",
        "stage": "物证",
        "stage_order": 1,
        "target_chapter": 20,
        "trigger": "怀表刻字",
        "allowed_disclosure": "只确认怀表不是偶然出现。",
        "required_action_consequence": "主角主动查证来源。",
    }


def test_scene_resistance_examples_accept_string_and_default_when_missing() -> None:
    payload = _contract_payload()
    payload["scene_resistance_rules"] = [
        {
            "scene_type": "对峙",
            "required_resistance": "必须有空间或流程阻力。",
            "examples": "门被挡住",
        },
        {
            "scene_type": "等待",
            "required_resistance": "必须有时间窗口。",
        },
    ]

    contract = EditorialContract.model_validate(payload)

    assert contract.scene_resistance_rules[0].examples == ["门被挡住"]
    assert contract.scene_resistance_rules[1].examples == ["等待需要通过环境、流程或物件制造阻力"]


def test_editorial_contract_normalizes_common_llm_schema_drift() -> None:
    payload = _contract_payload()
    payload["climax_markers"] = [
        {
            "chapter_number": 40,
            "climax_type": "main",
            "description": "主线摊牌与核心选择完成。",
            "expected_aftermath_chapters": 6,
        },
        {
            "chapter_number": 28,
            "climax_type": "secondary",
            "description": "支线债务完成第一次清算。",
            "expected_aftermath_chapters": 2,
        },
        {
            "chapter_number": 35,
            "climax_type": "political",
            "description": "朝堂权谋线完成清算。",
            "expected_aftermath_chapters": 2,
        },
        {
            "chapter_number": 51,
            "climax_type": "resolution",
            "description": "情感后果落地。",
            "expected_aftermath_chapters": 1,
        },
        {
            "chapter_number": 56,
            "climax_type": "denouement",
            "description": "尾声意象收束。",
            "expected_aftermath_chapters": 0,
        },
    ]
    payload["symbol_policies"] = [
        {
            "symbol": "怀表",
            "narrative_function": "时间与承诺",
            "explanation_policy": "never",
            "forbidden_explanation": "禁止叙述者直接解释象征含义。",
            "max_explicit_explanations": 3,
        }
    ]
    payload["scene_resistance_rules"] = [
        {
            "scene_type": "对峙",
            "required_resistance": "必须有空间或流程阻力。",
            "scene_resistance_chapter_windows": "第20章、第40章",
            "examples": ["门被挡住", "签字窗口关闭"],
        }
    ]
    payload["expression_channel_budget"] = {
        "per_chapter_limit": "同一章内身体反应不超过一处、对白标签不超过两次、感官锚点不超过三个",
        "per_high_emotion_scene_limit": "高情绪场景身体信号不超过一处",
    }
    payload["body_signal_budget_per_high_emotion_scene"] = "高情绪场景身体信号不超过一处"

    contract = EditorialContract.model_validate(payload)

    assert [item.climax_type for item in contract.climax_markers] == [
        "main",
        "subplot",
        "subplot",
        "emotional",
        "emotional",
    ]
    policy = contract.symbol_policies[0]
    assert policy.explanation_policy == "never_explain"
    assert policy.max_explicit_explanations == 0
    assert "禁止叙述者直接解释象征含义" in policy.escalation_rule
    assert "第20章、第40章" in contract.scene_resistance_rules[0].required_resistance
    assert contract.expression_channel_budget == {
        "somatic_reaction": 1,
        "action_tag": 2,
        "dialogue_tag": 2,
        "sensory_anchor": 3,
    }
    assert contract.body_signal_budget_per_high_emotion_scene == 1


def test_editorial_contract_downgrades_unknown_llm_enums_without_validation_crash() -> None:
    payload = _contract_payload()
    payload["climax_markers"] = [
        {
            "chapter_number": 40,
            "climax_type": "main",
            "description": "主线摊牌与核心选择完成。",
            "expected_aftermath_chapters": 6,
        },
        {
            "chapter_number": 44,
            "climax_type": "ideological",
            "description": "理念冲突达到阶段顶点。",
            "expected_aftermath_chapters": 1,
        },
    ]
    payload["symbol_policies"] = [
        {
            "symbol": "棋盘",
            "narrative_function": "权力秩序",
            "explanation_policy": "symbolic_guidance",
            "escalation_rule": "每次出现改变阵营语境。",
            "max_explicit_explanations": 2,
        }
    ]

    contract = EditorialContract.model_validate(payload)

    assert [item.climax_type for item in contract.climax_markers] == ["main", "subplot"]
    assert contract.symbol_policies[0].explanation_policy == "explain_once"


def test_editorial_contract_clamps_symbol_policy_explicit_explanation_budget() -> None:
    payload = _contract_payload()
    payload["symbol_policies"] = [
        {
            "symbol": "残缺意象",
            "narrative_function": "身份错位与自我接纳。",
            "explanation_policy": "free",
            "escalation_rule": "终局只允许行动承接，不允许反复直白解释。",
            "max_explicit_explanations": 99,
        }
    ]

    contract = EditorialContract.model_validate(payload)

    assert contract.symbol_policies[0].max_explicit_explanations == 10


def test_editorial_audit_and_revision_enums_are_lenient_for_llm_labels() -> None:
    report = EditorialAuditReport.model_validate(
        {
            "summary": "存在结构风险。",
            "findings": [
                {
                    "issue_type": "structure",
                    "severity": "warning",
                    "chapter_number": 1,
                    "summary": "余波过长。",
                },
                {
                    "issue_type": "voice",
                    "severity": "fatal",
                    "chapter_number": 2,
                    "summary": "声纹严重漂移。",
                },
            ],
        }
    )
    action = EditorialRevisionAction.model_validate(
        {
            "action_type": "rewrite",
            "priority": "informational",
            "rationale": "提示级修订。",
            "instruction": "减少解释。",
        }
    )

    assert [item.severity for item in report.findings] == ["medium", "critical"]
    assert action.priority == "low"


def test_editorial_contract_clamps_body_signal_budget_drift() -> None:
    payload = _contract_payload()
    payload["body_signal_budget_per_high_emotion_scene"] = 6

    contract = EditorialContract.model_validate(payload)

    assert contract.body_signal_budget_per_high_emotion_scene == 5


def test_editorial_contract_normalizes_expression_channel_profiles() -> None:
    payload = _contract_payload()
    payload["expression_channel_profiles"] = [
        {
            "channel_id": "Heart Tightening!",
            "channel": "body_signal",
            "label": "心头反应",
            "surface_forms": ["心头一紧", "心头一跳", r"心头.*一沉", "心头一沉"] * 3,
            "trigger_contexts": ["惊讶", "警觉", "情动", "危险", "多余"],
            "risk_reason": "不同情绪反复使用同一身体反应。" * 8,
            "replacement_axes": ["动作选择", "对白停顿", "物件操作", "场景阻力", "多余"],
            "allowed_when": "重大身份线索或核心情感转折可少量保留。",
            "cooldown_chapters": 20,
            "confidence": "86%",
        },
        {
            "channel_id": "low_confidence",
            "channel": "sentence_pattern",
            "label": "低置信",
            "surface_forms": ["只是泛泛建议"],
            "risk_reason": "置信不足。",
            "replacement_axes": ["动作选择"],
            "allowed_when": "很少。",
            "confidence": 0.2,
        },
    ]

    contract = EditorialContract.model_validate(payload)
    profile = contract.expression_channel_profiles[0]

    assert len(contract.expression_channel_profiles) == 1
    assert profile.channel_id == "heart_tightening"
    assert profile.channel == "somatic_reaction"
    assert r"心头.*一沉" not in profile.surface_forms
    assert len(profile.surface_forms) <= 8
    assert len(profile.trigger_contexts) == 4
    assert len(profile.replacement_axes) == 4
    assert profile.cooldown_chapters == 12
    assert profile.confidence == 0.86


def test_editorial_contract_normalizes_policy_object_lists() -> None:
    payload = _contract_payload()
    payload["theme_policies"] = [{"policy": "主题必须通过选择与后果呈现。"}]
    payload["time_bridge_policies"] = [
        {"policy": "现代线跨月转场必须标注月份。"},
        {"rule": "民国闪回使用器物触发，不按连续日历写。"},
    ]
    payload["revision_priorities"] = [{"requirement": "先压结构，再修声纹。"}]

    contract = EditorialContract.model_validate(payload)

    assert contract.theme_policies == ["主题必须通过选择与后果呈现。"]
    assert contract.time_bridge_policies == [
        "现代线跨月转场必须标注月份。",
        "民国闪回使用器物触发，不按连续日历写。",
    ]
    assert contract.revision_priorities == ["先压结构，再修声纹。"]


def test_editorial_contract_normalizes_denouement_budget_text_lists() -> None:
    payload = _contract_payload()
    payload["denouement_budget"] = {
        "expected_chapters": 6,
        "max_confirmation_scenes": 2,
        "required_new_functions": "残玉合器时那声轻归一的制度落定",
        "forbidden_repeats": {"rule": "禁止在终局中安排环新婚之夜誓约"},
    }

    contract = EditorialContract.model_validate(payload)

    assert contract.denouement_budget.required_new_functions == ["残玉合器时那声轻归一的制度落定"]
    assert contract.denouement_budget.forbidden_repeats == ["禁止在终局中安排环新婚之夜誓约"]


def test_editorial_contract_migrates_denouement_description_alias() -> None:
    payload = _contract_payload()
    payload["denouement_budget"] = {
        "expected_chapters": 1,
        "max_confirmation_scenes": 1,
        "required_new_functions": ["余波后果"],
        "forbidden_repeats": ["重复圆满确认"],
        "forbidden_repeats_description": "结尾一章只能完成一项收束，不得新开反派动作。",
    }

    contract = EditorialContract.model_validate(payload)

    assert contract.denouement_budget.forbidden_repeats == [
        "重复圆满确认",
        "结尾一章只能完成一项收束，不得新开反派动作。",
    ]
    assert "forbidden_repeats_description" not in contract.denouement_budget.model_dump(mode="json")


def test_editorial_contract_normalizes_title_policy_no_reuse_drift() -> None:
    payload = _contract_payload()
    payload["title_policy"] = {
        "max_reuse": 0,
        "allowed_titles": [{"title": "残玉归心"}, "月下残局"],
        "naming_strategy": "全书标题尽量唯一，只保留首尾回环。",
    }

    contract = EditorialContract.model_validate(payload)

    assert contract.title_policy.max_reuse == 1
    assert contract.title_policy.allowed_repeated_titles == ["残玉归心", "月下残局"]
    assert contract.title_policy.naming_strategy == "全书标题尽量唯一，只保留首尾回环。"


def test_editorial_contract_normalizes_title_policy_aliases_and_no_reuse_text() -> None:
    payload = _contract_payload()
    payload["title_policy"] = {
        "reuse_limit": "禁止复用",
        "allow_repeated_titles": ["终章回环"],
        "naming_strategy": "",
    }

    contract = EditorialContract.model_validate(payload)

    assert contract.title_policy.max_reuse == 1
    assert contract.title_policy.allowed_repeated_titles == ["终章回环"]
    assert (
        contract.title_policy.naming_strategy == "章节标题应提示节点功能；只保留少量有意回环标题。"
    )


def test_editorial_contract_normalizes_aftermath_ranges_to_counts() -> None:
    payload = _contract_payload()
    payload["climax_markers"] = [
        {
            "chapter_number": 60,
            "climax_type": "main",
            "description": "终局选择完成。",
            "expected_aftermath_chapters": [65, 70],
        },
        {
            "chapter_number": 50,
            "climax_type": "subplot",
            "description": "支线清算完成。",
            "expected_aftermath_chapters": {"start": 55, "end": 60},
        },
        {
            "chapter_number": 30,
            "climax_type": "emotional",
            "description": "关系后果落地。",
            "expected_aftermath_chapters": "35-40",
        },
        {
            "chapter_number": 5,
            "climax_type": "mystery",
            "description": "早期悬念转折。",
            "expected_aftermath_chapters": [1, 3],
        },
    ]

    contract = EditorialContract.model_validate(payload)

    assert [marker.expected_aftermath_chapters for marker in contract.climax_markers] == [
        10,
        10,
        10,
        3,
    ]


def test_editorial_contract_still_rejects_unknown_extra_fields() -> None:
    payload = _contract_payload()
    payload["symbol_policies"] = [
        {
            "symbol": "怀表",
            "narrative_function": "时间与承诺",
            "explanation_policy": "explain_once",
            "escalation_rule": "每次出现改变语境。",
            "max_explicit_explanations": 1,
            "unknown_policy_note": "这类未知字段仍应暴露为契约错误。",
        }
    ]

    with pytest.raises(ValidationError):
        EditorialContract.model_validate(payload)


def test_editorial_card_marks_denouement_over_budget() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    card = build_editorial_card(contract, chapter_number=50, stage="draft")

    assert card["over_denouement_budget"] is True
    assert card["climax_distance"] == 10
    assert card["expression_channel_profiles"][0]["channel_id"] == "heart_tightening"
    assert "surface_forms" not in card["expression_channel_profiles"][0]
    assert "evidence_quotes" not in card["expression_channel_profiles"][0]
    assert contract.expression_channel_profiles[0].evidence_quotes


def test_stage_cards_include_editorial_contract() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    readiness = {
        "status": "warning",
        "summary": "编辑契约校验通过但有风险。",
        "findings": [
            {
                "issue_type": "editorial_contract_confirmation_budget_high",
                "severity": "medium",
                "summary": "确认性场景预算偏高，可能形成多次“再结尾”。",
                "recommendation": "将确认性场景预算控制在 1-2 次。",
            }
        ],
        "revision_plan": ["将确认性场景预算控制在 1-2 次。"],
    }
    cards = build_stage_cards(
        stage="draft",
        chapter_outline={"chapter_number": 50, "involved_characters": ["主角甲"]},
        editorial_contract=contract.model_dump(mode="json"),
        editorial_readiness=readiness,
    )
    repair_cards = build_stage_cards(
        stage="reading_power_repair",
        chapter_outline={"chapter_number": 50},
        editorial_contract=contract,
        editorial_readiness=readiness,
    )

    # DRAFT must NOT receive character_voices (6-phase long-form architecture:
    # voice is a WAVE/PLAN responsibility).  Repair stages keep them.
    assert cards["editorial"].get("character_voices", []) == []
    assert repair_cards["editorial"]["character_voices"][0]["character"] == "主角甲"
    assert repair_cards["editorial"]["over_denouement_budget"] is True
    assert cards["editorial"]["editorial_element_directives"][0]["element_id"] == (
        "editorial_denouement_budget"
    )
    assert cards["editorial"]["risk_guidance"]["items"][0]["issue_type"] == (
        "editorial_contract_confirmation_budget_high"
    )
    assert cards["editorial"]["risk_guidance"]["denouement"]["requires_new_function"] is True
    assert repair_cards["editorial"]["risk_guidance"]["items"] == []


def test_editorial_metrics_detect_repetition_and_denouement() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    text = (
        "她心跳漏了一拍，胸口空了一瞬。"
        "怀表意味着时间，怀表象征着承诺，怀表代表着旧梦。"
        "终于核心主题句，终于不会再错过。"
    )
    findings = chapter_editorial_findings(
        chapter_number=50,
        chapter_text=text,
        contract=contract,
    )
    overrun = detect_denouement_overrun(completed_chapters=[1, 50], contract=contract)

    assert {item.issue_type for item in findings} >= {
        "symbol_over_explanation",
        "confirmation_scene_repetition",
    }
    assert overrun and overrun[0].issue_type == "denouement_overrun"


def test_editorial_metrics_use_contract_expression_profiles() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    findings = chapter_editorial_findings(
        chapter_number=3,
        chapter_text="她心头一紧。片刻后，她心头一跳。夜色压近时，她心头一紧。",
        contract=contract,
    )

    expression_findings = [
        item for item in findings if item.issue_type == "expression_channel_overuse"
    ]
    assert expression_findings
    assert expression_findings[0].metadata["channel_id"] == "heart_tightening"


def test_editorial_metrics_detect_paragraph_visual_fatigue() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    long_paragraph = (
        "她站在门边，听见雨声压下来。"
        "她没有立刻开口，只把那封信翻到背面。"
        "走廊尽头有人经过，脚步声很轻，却让整间屋子都停了一下。"
        "她意识到这一切并不只是误会，而是长久沉默之后终于露出的裂缝。"
        "她看着怀表，又看着窗外，像是在等待一个迟到很多年的回答。"
        "陆云峥没有解释，他只是把伞放在门槛外，仿佛在告诉她他不会再往前一步。"
        "她的手指碰到纸边，指尖发凉，却还是把信递了过去。"
        "那一刻屋里的灯光晃了一下，所有没有说出口的话都挤在同一段里。"
        "她明白自己必须做出选择。"
    )
    findings = detect_paragraph_visual_fatigue(chapter_number=56, text=long_paragraph)
    chapter_findings = chapter_editorial_findings(
        chapter_number=56,
        chapter_text=long_paragraph,
        contract=contract,
    )

    assert findings and findings[0].issue_type == "paragraph_visual_fatigue"
    assert findings[0].metadata["paragraph_index"] == 1
    assert any(item.issue_type == "paragraph_visual_fatigue" for item in chapter_findings)


def test_editorial_validator_checks_element_references_and_budgets() -> None:
    payload = _contract_payload()
    payload["editorial_element_directives"] = [
        {
            "element_id": "missing_editorial_element",
            "element_name": "缺失要素",
            "directive_type": "测试",
            "target_window": "全书",
            "linked_characters": [],
            "requirement": "必须引用要素库。",
            "success_criteria": "校验应阻断。",
        }
    ]
    contract = EditorialContract.model_validate(payload)

    report = validate_editorial_contract(
        contract,
        total_chapters=61,
        selected_element_ids={"editorial_denouement_budget"},
        outline_titles=["并肩之约"] * 3,
    )

    assert report.metrics["status"] == "blocked"
    assert any(item.issue_type == "editorial_element_unknown" for item in report.findings)


def test_editorial_title_repetition_uses_contract_policy() -> None:
    payload = _contract_payload()
    payload["title_policy"] = {
        "max_reuse": 2,
        "allowed_repeated_titles": ["残玉归心"],
        "naming_strategy": "普通标题唯一，回环标题最多两次。",
    }
    contract = EditorialContract.model_validate(payload)

    findings = detect_title_repetition(
        ["残玉归心", "残玉归心", "残玉归心", "月下残局", "月下残局"],
        contract=contract,
    )
    report = validate_editorial_contract(
        contract,
        total_chapters=61,
        selected_element_ids={"editorial_denouement_budget"},
        outline_titles=["残玉归心", "残玉归心", "残玉归心", "月下残局", "月下残局"],
    )

    assert {item.metadata["title"] for item in findings} == {"残玉归心", "月下残局"}
    title_finding = next(
        item for item in report.findings if item.issue_type == "outline_title_reuse_over_budget"
    )
    assert title_finding.metadata["titles"] == {"残玉归心": 3, "月下残局": 2}


def test_revision_planner_builds_structured_actions_from_contract_elements() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    findings = detect_denouement_overrun(completed_chapters=[1, 61], contract=contract)

    plan = build_structured_revision_plan(
        findings=findings,
        chapters=[BookAuditChapter(chapter_number=chapter) for chapter in range(1, 62)],
        contract=contract,
    )

    action_types = {str(item["action_type"]) for item in plan["actions"]}
    assert "merge_chapters" in action_types
    assert "apply_editorial_element:editorial_denouement_budget" in action_types


def test_revision_planner_builds_split_paragraph_actions() -> None:
    contract = EditorialContract.model_validate(_contract_payload())
    findings = detect_paragraph_visual_fatigue(
        chapter_number=56,
        text="。".join(["这是一句很长的段落内容"] * 12),
    )

    plan = build_structured_revision_plan(
        findings=findings,
        chapters=[BookAuditChapter(chapter_number=56)],
        contract=contract,
    )

    action_types = {str(item["action_type"]) for item in plan["actions"]}
    assert "split_paragraphs" in action_types
    assert plan["paragraph_actions"]
