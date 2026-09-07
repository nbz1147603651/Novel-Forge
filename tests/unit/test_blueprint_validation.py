"""Tests for narrative blueprint validation and subplot execution matrix."""

from __future__ import annotations

from novel_forge.core.schemas.outline import NarrativeBlueprint, PayoffPlan
from novel_forge.pipeline.long.context import compute_narrative_context
from novel_forge.pipeline.long.services.blueprint.blueprint_validation import validate_blueprint
from novel_forge.pipeline.long.services.weave_validation import (
    build_subplot_execution_matrix,
    validate_subplot_weave,
)


def _valid_blueprint() -> NarrativeBlueprint:
    return NarrativeBlueprint.model_validate(
        {
            "synopsis": "主角追查旧案，在关系与权力夹击中完成真相揭露。",
            "volume_mode": False,
            "volumes": [],
            "narrative_phases": [
                {
                    "phase_name": "引入",
                    "chapter_start": 1,
                    "chapter_end": 6,
                    "description": "旧案重启，主角建立调查目标。",
                },
                {
                    "phase_name": "反击",
                    "chapter_start": 7,
                    "chapter_end": 12,
                    "description": "支线反哺主线，终局揭露真相。",
                },
            ],
            "key_turning_points": [
                {
                    "chapter_number": 3,
                    "description": "发现密信",
                    "location": "书房",
                    "characters_involved": ["沈砚"],
                },
                {
                    "chapter_number": 8,
                    "description": "证人倒戈",
                    "location": "公堂",
                    "characters_involved": ["沈砚", "陆微"],
                },
                {
                    "chapter_number": 12,
                    "description": "真相公开",
                    "location": "朝会",
                    "characters_involved": ["沈砚", "陆微"],
                },
            ],
            "subplot_plan": [
                {
                    "name": "密信线",
                    "description": "追查密信来源。",
                    "involved_chapters": [2, 5, 8],
                    "chapter_events": [
                        {
                            "chapter_number": 2,
                            "event": "得到密信",
                            "weave_notes": "",
                            "depends_on": [],
                        },
                        {
                            "chapter_number": 5,
                            "event": "核验笔迹",
                            "weave_notes": "",
                            "depends_on": [],
                        },
                        {
                            "chapter_number": 8,
                            "event": "证人倒戈",
                            "weave_notes": "",
                            "depends_on": [],
                        },
                    ],
                    "weave_links": [
                        {
                            "source_type": "main_plot",
                            "source_ref": "旧案重启",
                            "target_subplot": "密信线",
                            "trigger_chapter": 2,
                            "link_type": "trigger_start",
                            "description": "主线调查触发密信追踪。",
                        },
                        {
                            "source_type": "subplot",
                            "source_ref": "密信线",
                            "target_subplot": "主线",
                            "trigger_chapter": 8,
                            "link_type": "reveal_key",
                            "description": "密信证据改变主线认知。",
                        },
                    ],
                    "priority": "normal",
                    "resolution_chapter": 8,
                    "resolution_target": "main_turning_point:2",
                    "resolution_type": "reveal",
                },
                {
                    "name": "关系线",
                    "description": "主角与盟友从互疑到共担代价。",
                    "involved_chapters": [3, 7, 11],
                    "chapter_events": [
                        {
                            "chapter_number": 3,
                            "event": "互相试探",
                            "weave_notes": "",
                            "depends_on": [],
                        },
                        {
                            "chapter_number": 7,
                            "event": "交换把柄",
                            "weave_notes": "",
                            "depends_on": [],
                        },
                        {
                            "chapter_number": 11,
                            "event": "共同承担后果",
                            "weave_notes": "",
                            "depends_on": [],
                        },
                    ],
                    "weave_links": [
                        {
                            "source_type": "main_plot",
                            "source_ref": "证据被夺",
                            "target_subplot": "关系线",
                            "trigger_chapter": 3,
                            "link_type": "trigger_start",
                            "description": "主线危机迫使双方合作。",
                        },
                        {
                            "source_type": "subplot",
                            "source_ref": "关系线",
                            "target_subplot": "主线",
                            "trigger_chapter": 11,
                            "link_type": "feed_main",
                            "description": "盟友选择反哺终局行动。",
                        },
                    ],
                    "priority": "normal",
                    "resolution_chapter": 11,
                    "resolution_target": "character_fate:陆微",
                    "resolution_type": "merge",
                },
            ],
            "suspense_schedule": [
                {
                    "suspense_id": "s_001",
                    "suspense_type": "mystery",
                    "introduce_chapter": 2,
                    "resolve_chapter": 8,
                    "description": "密信来自谁",
                    "urgency_level": "normal",
                    "related_subplot": "密信线",
                    "strand_affinity": {"quest": 0.4, "fire": 0.1, "constellation": 0.5},
                }
            ],
            "ending_strategy": "公开真相并让关系线并入终局选择。",
        }
    )


def test_validate_blueprint_accepts_closed_loop_structure() -> None:
    result = validate_blueprint(
        _valid_blueprint(), total_chapters=12, narrative_complexity="standard"
    )

    assert result.errors == []
    assert result.structure_check_passed is True
    assert result.quantity_check_passed is True


def test_validate_blueprint_catches_incomplete_phase_and_weak_subplot_loop() -> None:
    blueprint = _valid_blueprint().model_copy(deep=True)
    blueprint.narrative_phases[1].chapter_end = 10
    blueprint.subplot_plan[0].weave_links = [
        blueprint.subplot_plan[0]
        .weave_links[1]
        .model_copy(update={"link_type": "theme_echo", "target_subplot": "主线"})
    ]

    result = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="standard")

    assert any("必须覆盖到总章节数 12" in item for item in result.errors)
    assert any("缺少主线触发启动" in item for item in result.errors)
    assert any("缺少强反哺" in item for item in result.errors)


def test_validate_blueprint_rejects_textual_chapter_refs_outside_container_ranges() -> None:
    payload = _valid_blueprint().model_dump(mode="json")
    payload.update(
        {
            "volume_mode": True,
            "volumes": [
                {
                    "volume_number": 1,
                    "title": "开局卷",
                    "start_chapter": 1,
                    "end_chapter": 6,
                    "arc_goal": "第3章完成旧案重启。",
                    "milestone_targets": ["第5章完成密信核验"],
                    "main_conflicts": ["证据来源不明"],
                    "climax_hint": "第6章证据被夺。",
                    "resolution_hint": "留下第二卷入口。",
                },
                {
                    "volume_number": 2,
                    "title": "反击卷",
                    "start_chapter": 7,
                    "end_chapter": 12,
                    "arc_goal": "完成终局反击。",
                    "milestone_targets": ["第5章旧线索被重新确认"],
                    "main_conflicts": ["盟友倒戈"],
                    "climax_hint": "第12章公开真相。",
                    "resolution_hint": "主线收束。",
                },
            ],
            "character_arcs": [
                {
                    "character": "沈砚",
                    "arc_summary": "从独行到信任盟友。",
                    "milestones": [
                        {
                            "chapter_start": 7,
                            "chapter_end": 9,
                            "description": "第十二章才真正交付信任。",
                        }
                    ],
                }
            ],
        }
    )
    payload["narrative_phases"][1]["description"] = "第3章的证词在本阶段被重新解释。"
    blueprint = NarrativeBlueprint.model_validate(payload)

    result = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="standard")

    assert any("分卷「反击卷」文本引用第 5 章" in item for item in result.errors)
    assert any("叙事阶段「反击」文本引用第 3 章" in item for item in result.errors)
    assert any("角色弧光「沈砚」里程碑文本引用第 12 章" in item for item in result.errors)
    assert result.structure_check_passed is False


def test_validate_blueprint_rejects_character_arc_milestone_range_errors() -> None:
    payload = _valid_blueprint().model_dump(mode="json")
    payload["character_arcs"] = [
        {
            "character": "沈砚",
            "arc_summary": "从孤证到共证。",
            "milestones": [
                {
                    "chapter_start": 9,
                    "chapter_end": 7,
                    "description": "章节区间倒置。",
                },
                {
                    "chapter_start": 11,
                    "chapter_end": 14,
                    "description": "越过全书边界。",
                },
            ],
        }
    ]
    blueprint = NarrativeBlueprint.model_validate(payload)

    result = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="standard")

    assert any("章节区间倒置：9-7" in item for item in result.errors)
    assert any("结束章节 14 超出总章节数 12" in item for item in result.errors)
    assert result.structure_check_passed is False


def test_validate_blueprint_warns_when_character_arc_milestone_lacks_phase_anchor() -> None:
    payload = _valid_blueprint().model_dump(mode="json")
    payload["narrative_phases"][0]["key_characters"] = ["陆微"]
    payload["character_arcs"] = [
        {
            "character": "沈砚",
            "arc_summary": "从孤证到共证。",
            "milestones": [
                {
                    "chapter_start": 2,
                    "chapter_end": 4,
                    "description": "试图独自验证密信。",
                }
            ],
        }
    ]
    blueprint = NarrativeBlueprint.model_validate(payload)

    result = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="standard")

    assert result.errors == []
    assert any("未列入阶段核心人物" in item for item in result.suggestions)


def test_validate_blueprint_rejects_single_late_character_arc_milestone_in_long_blueprint() -> None:
    payload = _valid_blueprint().model_dump(mode="json")
    payload["narrative_phases"] = [
        {
            "phase_name": "开端",
            "chapter_start": 1,
            "chapter_end": 20,
            "description": "开端。",
            "key_characters": ["沈砚、陆微"],
        },
        {
            "phase_name": "中段",
            "chapter_start": 21,
            "chapter_end": 40,
            "description": "中段。",
            "key_characters": ["沈砚、陆微"],
        },
        {
            "phase_name": "终局",
            "chapter_start": 41,
            "chapter_end": 70,
            "description": "终局。",
            "key_characters": ["沈砚、陆微"],
        },
    ]
    payload["character_arcs"] = [
        {
            "character": "沈砚",
            "arc_summary": "从孤证到共证。",
            "milestones": [
                {
                    "chapter_start": 41,
                    "chapter_end": 70,
                    "description": "终局才完成变化。",
                }
            ],
        }
    ]
    blueprint = NarrativeBlueprint.model_validate(payload)

    result = validate_blueprint(blueprint, total_chapters=70, narrative_complexity="standard")

    assert any("milestones 数量不足" in item for item in result.errors)
    assert any("缺少前中段角色变化锚点" in item for item in result.errors)
    assert not any("未列入阶段核心人物" in item for item in result.suggestions)


def test_subplot_execution_matrix_reports_closed_loop_status() -> None:
    matrix = build_subplot_execution_matrix(_valid_blueprint(), current_chapter=4)
    rows = {row.name: row for row in matrix.rows}

    assert matrix.closed_loop_ratio == 1.0
    assert rows["密信线"].status == "dormant"
    assert rows["密信线"].next_planned_chapter == 5
    assert rows["关系线"].closed_loop is True


def test_weave_validation_requires_strong_feedback_not_theme_echo_only() -> None:
    blueprint = _valid_blueprint().model_copy(deep=True)
    blueprint.subplot_plan[0].weave_links[1] = (
        blueprint.subplot_plan[0].weave_links[1].model_copy(update={"link_type": "theme_echo"})
    )

    result = validate_subplot_weave(blueprint)

    assert result.feedback_check_passed is False
    assert any("缺少「回馈主线」" in item for item in result.suggestions)


def test_narrative_context_includes_subplot_convergence_hints() -> None:
    blueprint = _valid_blueprint()

    context = compute_narrative_context(blueprint.model_dump(mode="json"), 8)

    assert any("支线「密信线」在本章收束" in item for item in context.active_subplot_weave_hints)
    assert any("该支线将推动主线转折" in item for item in context.active_subplot_weave_hints)


def test_payoff_plan_accepts_full_micro_payoff_taxonomy() -> None:
    assert PayoffPlan(payoff_type="资源").payoff_type == "resource"
    assert PayoffPlan(payoff_type="recognition").payoff_type == "recognition"
    assert PayoffPlan(payoff_type="emotion").payoff_type == "emotion"
    assert PayoffPlan(payoff_type="unknown").payoff_type == "information"
