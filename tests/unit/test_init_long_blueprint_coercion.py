import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    apply_local_blueprint_structural_fallback,
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.blueprint.blueprint_validation import validate_blueprint
from novel_forge.pipeline.long.services.init_repair import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssueKind,
    get_init_repair_policy,
)
from novel_forge.pipeline.long.services.init_repair.policies import BlueprintRepairPolicy


def test_percentage_position_blueprint_payload_requires_current_schema() -> None:
    payload = {
        "synopsis": "s",
        "narrative_phases": [
            {
                "phase_name": "引入",
                "position_start": 0,
                "position_end": 25,
                "description": "d",
                "tension_level": 3,
                "time_setting": "t",
                "location": "loc",
                "characters_present": ["a"],
                "key_event": "e",
            }
        ],
        "turning_points": [{"position_percent": 50, "description": "tp", "impact": "i"}],
        "character_arcs": [{"character": "a", "arc_summary": "arc", "key_moment": "m"}],
        "ending_strategy": "end",
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=20)

    with pytest.raises(ValidationError):
        NarrativeBlueprint.model_validate(normalized)


# ---------------------------------------------------------------------------
# pre_normalize_blueprint_payload tests
# ---------------------------------------------------------------------------


def test_pre_normalize_volumes_with_volume_name_field() -> None:
    """Reproduces the runtime error: LLM returns volume_name instead of title."""
    payload = {
        "synopsis": "s",
        "volume_mode": True,
        "volumes": [
            {"volume_name": "卷一……认知与初步同盟"},
            {"volume_name": "卷二……裂变与代价"},
        ],
        "narrative_phases": [
            {"phase_name": "引入", "chapter_start": 1, "chapter_end": 5, "description": "d"}
        ],
    }
    normalized = pre_normalize_blueprint_payload(payload, total_chapters=10)
    assert normalized is not None
    blueprint = NarrativeBlueprint.model_validate(normalized)
    assert len(blueprint.volumes) == 2
    assert blueprint.volumes[0].title == "卷一……认知与初步同盟"
    assert blueprint.volumes[0].volume_number == 1
    assert blueprint.volumes[0].start_chapter == 1
    assert blueprint.volumes[0].end_chapter == 5
    assert blueprint.volumes[1].title == "卷二……裂变与代价"
    assert blueprint.volumes[1].volume_number == 2


def test_pre_normalize_volumes_preserves_existing_correct_volumes() -> None:
    """Volumes that are already correct must pass through unchanged."""
    payload = {
        "synopsis": "s",
        "volume_mode": True,
        "volumes": [
            {
                "volume_number": 1,
                "title": "卷一",
                "start_chapter": 1,
                "end_chapter": 6,
                "arc_goal": "goal",
            }
        ],
    }
    normalized = pre_normalize_blueprint_payload(payload, total_chapters=10)
    assert normalized is not None
    blueprint = NarrativeBlueprint.model_validate(normalized)
    assert blueprint.volumes[0].volume_number == 1
    assert blueprint.volumes[0].title == "卷一"
    assert blueprint.volumes[0].end_chapter == 6


def test_pre_normalize_volumes_noop_when_no_volumes() -> None:
    """Payload with empty volumes is returned as-is."""
    payload = {"synopsis": "s", "volumes": [], "volume_mode": False}
    result = pre_normalize_blueprint_payload(payload, total_chapters=10)
    assert result is payload


def test_pre_normalize_blueprint_coerces_subplot_dependency_string() -> None:
    payload = {
        "synopsis": "s",
        "volumes": [],
        "volume_mode": False,
        "subplot_plan": [
            {
                "name": "林绾绾的感情线",
                "description": "关系线",
                "involved_chapters": [33],
                "chapter_events": [
                    {
                        "chapter_number": 33,
                        "event": "林绾绾作出选择",
                        "depends_on": "林绾绾的感情线:33",
                    }
                ],
            }
        ],
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=82)
    assert normalized is not payload
    blueprint = NarrativeBlueprint.model_validate(normalized)
    assert blueprint.subplot_plan[0].chapter_events[0].depends_on == ["林绾绾的感情线:33"]


def test_pre_normalize_blueprint_parses_stringified_weave_links() -> None:
    payload = {
        "synopsis": "s",
        "subplot_plan": [
            {
                "name": "身世谜团线",
                "involved_chapters": [3],
                "chapter_events": [{"chapter_number": 3, "event": "发现密信"}],
                "weave_links": [
                    '{"source_type":"main_plot","source_ref":"密信","target_subplot":"身世谜团线","trigger_chapter":3,"link_type":"trigger_start","description":"主线事件触发身世调查"}'
                ],
            }
        ],
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=10)
    blueprint = NarrativeBlueprint.model_validate(normalized)
    assert blueprint.subplot_plan[0].weave_links[0].source_type == "main_plot"


def test_pre_normalize_blueprint_migrates_weave_fields_from_chapter_events() -> None:
    payload = {
        "synopsis": "s",
        "subplot_plan": [
            {
                "name": "陈默的守护与银杏道见证",
                "involved_chapters": [32],
                "chapter_events": [
                    {
                        "chapter_number": 32,
                        "source_type": "main_plot",
                        "source_ref": "第32章沈知微发现对话日志异常",
                        "target_subplot": "陈默的守护与银杏道见证",
                        "trigger_chapter": 32,
                        "link_type": "create_tension",
                        "description": "陈默隐约察觉沈知微的异常，却选择先保护她。",
                    }
                ],
            }
        ],
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=61)
    blueprint = NarrativeBlueprint.model_validate(normalized)
    subplot = blueprint.subplot_plan[0]

    assert subplot.chapter_events[0].chapter_number == 32
    assert subplot.chapter_events[0].event == "陈默隐约察觉沈知微的异常，却选择先保护她。"
    assert len(subplot.weave_links) == 1
    assert subplot.weave_links[0].source_type == "main_plot"
    assert subplot.weave_links[0].target_subplot == "陈默的守护与银杏道见证"
    assert subplot.weave_links[0].link_type == "create_tension"


def test_pre_normalize_blueprint_removes_extra_character_arc_milestone_fields() -> None:
    payload = {
        "synopsis": "s",
        "character_arcs": [
            {
                "character": "陆砚深",
                "arc_summary": "从理性算计到坦诚心动。",
                "milestones": [
                    {
                        "chapter_start": 56,
                        "chapter_end": 81,
                        "description": "理性与感性的弥合共生。",
                        "情感变化曲线": "克制→坦白→释然",
                    }
                ],
            },
            {
                "character": "陈默",
                "arc_summary": "从保护性助攻到有分寸旁观。",
                "milestones": [
                    {
                        "chapter_start": 36,
                        "chapter_end": 81,
                        "description": "退回专业同门边界。",
                        "角色定位": "情感推手与边界守护者",
                    }
                ],
            },
        ],
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=81)
    blueprint = NarrativeBlueprint.model_validate(normalized)
    descriptions = [
        milestone.description
        for arc in blueprint.character_arcs
        for milestone in arc.milestones
    ]

    assert any("情感变化曲线" in item for item in descriptions)
    assert any("角色定位" in item for item in descriptions)


def test_pre_normalize_blueprint_recovers_leaked_arc_milestone_chapter_end() -> None:
    payload = {
        "synopsis": "主角在权力与情感夹击中完成信任选择。",
        "character_arcs": [
            {
                "character": "沈知微",
                "arc_summary": "从设防到主动信任。",
                "milestones": [
                    {
                        "chapter_start": 1,
                        "chapter_end": 18,
                        "description": "初遇与设防。",
                    },
                    {
                        "chapter_start": 19,
                        "chapter_end": 1,
                        "description": "主动靠近并进入信任博弈。；.chapter_end: 36",
                    },
                    {
                        "chapter_start": 37,
                        "description": "真相揭开，完成核心选择。；.chapter_end: 54",
                    },
                ],
            },
            {
                "character": "陆砚",
                "arc_summary": "从旁观到守护。",
                "milestones": [
                    {
                        "chapter_start": 1,
                        "chapter_end": 18,
                        "description": "旁观旧案。",
                    },
                    {
                        "chapter_start": 19,
                        "chapter_end": 1,
                        "description": "短促清醒提供线索。；.chapter_end: 18",
                    },
                    {
                        "chapter_start": 37,
                        "chapter_end": 1,
                        "description": "尾声兑现守护。；.chapter_end: 36",
                    },
                ],
            },
        ],
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=70)
    blueprint = NarrativeBlueprint.model_validate(normalized)

    assert [
        (milestone.chapter_start, milestone.chapter_end, milestone.description)
        for milestone in blueprint.character_arcs[0].milestones
    ] == [
        (1, 18, "初遇与设防。"),
        (19, 36, "主动靠近并进入信任博弈。"),
        (37, 54, "真相揭开，完成核心选择。"),
    ]
    assert [
        (milestone.chapter_start, milestone.chapter_end)
        for milestone in blueprint.character_arcs[1].milestones
    ] == [(1, 18), (19, 36), (37, 70)]
    validation = validate_blueprint(
        blueprint,
        total_chapters=70,
        narrative_complexity="standard",
    )
    assert not any("章节区间倒置" in error for error in validation.errors)


def test_pre_normalize_blueprint_accepts_chinese_enum_equivalents() -> None:
    payload = {
        "synopsis": "主角追查旧案，在关系与权力夹击中完成真相揭露。",
        "volume_mode": False,
        "volumes": [],
        "narrative_phases": [
            {
                "phase_name": "开局",
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
        ],
        "subplot_plan": [
            {
                "name": "密信线",
                "description": "追查密信来源。",
                "involved_chapters": ["第2章", "第5章", "第8章"],
                "chapter_events": [
                    {"chapter_number": "第2章", "event": "得到密信"},
                    {"chapter_number": "第5章", "event": "核验笔迹"},
                    {"chapter_number": "第8章", "event": "证人倒戈"},
                ],
                "weave_links": [
                    {
                        "source_type": "主线",
                        "source_ref": "旧案重启",
                        "target_subplot": "密信线",
                        "trigger_chapter": "第2章",
                        "link_type": "触发启动",
                        "description": "主线调查触发密信追踪。",
                    },
                    {
                        "source_type": "支线",
                        "source_ref": "密信线",
                        "target_subplot": "主线剧情",
                        "trigger_chapter": "第8章",
                        "link_type": "反哺主线",
                        "description": "密信证据改变主线认知。",
                    },
                ],
                "priority": "常规",
                "resolution_chapter": "第8章",
                "resolution_target": "主线转折2",
                "resolution_type": "揭示",
            }
        ],
        "suspense_schedule": [
            {
                "suspense_id": "s_001",
                "suspense_type": "谜团",
                "introduce_chapter": "第2章",
                "resolve_chapter": "第8章",
                "description": "密信来自谁",
                "urgency_level": "普通",
                "related_subplot": "密信线",
                "strand_affinity": {"调查": 0.4, "冲突": 0.1, "关系": 0.5},
            }
        ],
        "ending_strategy": "公开真相并让关系线并入终局选择。",
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=12)
    blueprint = NarrativeBlueprint.model_validate(normalized)
    subplot = blueprint.subplot_plan[0]

    assert subplot.involved_chapters == [2, 5, 8]
    assert subplot.weave_links[0].source_type == "main_plot"
    assert subplot.weave_links[0].link_type == "trigger_start"
    assert subplot.weave_links[1].source_type == "subplot"
    assert subplot.weave_links[1].target_subplot == "主线"
    assert subplot.weave_links[1].link_type == "feed_main"
    assert subplot.priority == "normal"
    assert subplot.resolution_target == "main_turning_point:2"
    assert subplot.resolution_type == "reveal"
    assert blueprint.suspense_schedule[0].suspense_type == "mystery"
    assert blueprint.suspense_schedule[0].urgency_level == "normal"
    assert blueprint.suspense_schedule[0].strand_affinity == {
        "quest": 0.4,
        "fire": 0.1,
        "constellation": 0.5,
    }
    assert validate_blueprint(
        blueprint, total_chapters=12, narrative_complexity="simple"
    ).errors == []


def test_pre_normalize_blueprint_closes_suspense_subplot_references() -> None:
    payload = {
        "synopsis": "主角追查旧案并完成关系选择。",
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 12,
                "description": "旧案重启并完成抉择。",
                "key_characters": ["沈砚"],
            }
        ],
        "key_turning_points": [{"chapter_number": 6, "description": "证据反转。"}],
        "character_arcs": [
            {
                "character": "沈砚",
                "arc_summary": "从独断到共担。",
                "milestones": [
                    {
                        "chapter_start": 1,
                        "chapter_end": 12,
                        "description": "沈砚学会共同承担。",
                    }
                ],
            }
        ],
        "subplot_plan": [
            {
                "name": "密信线",
                "description": "追索密信来源。",
                "involved_chapters": [2, 6, 12],
                "chapter_events": [
                    {"chapter_number": 2, "event": "密信出现。"},
                    {"chapter_number": 6, "event": "证据反转。"},
                    {"chapter_number": 12, "event": "密信兑现。"},
                ],
                "weave_links": [
                    {
                        "source_type": "main_plot",
                        "source_ref": "旧案重启",
                        "target_subplot": "密信线",
                        "trigger_chapter": 2,
                        "link_type": "trigger_start",
                        "description": "主线触发密信线。",
                    },
                    {
                        "source_type": "subplot",
                        "source_ref": "密信线",
                        "target_subplot": "主线",
                        "trigger_chapter": 12,
                        "link_type": "feed_main",
                        "description": "密信反哺主线。",
                    },
                ],
                "priority": "normal",
                "resolution_chapter": 12,
                "resolution_target": "main_turning_point:1",
                "resolution_type": "reveal",
            }
        ],
        "suspense_schedule": [
            {
                "suspense_id": "s_unknown",
                "suspense_type": "choice",
                "introduce_chapter": 4,
                "resolve_chapter": 8,
                "description": "角色会如何选择。",
                "urgency_level": "high",
                "related_subplot": "临时抉择线",
                "strand_affinity": {"quest": 0.4, "fire": 0.4, "constellation": 0.2},
            },
            {
                "suspense_id": "s_known",
                "suspense_type": "mystery",
                "introduce_chapter": 2,
                "resolve_chapter": 12,
                "description": "密信来自哪里。",
                "urgency_level": "normal",
                "related_subplot": "密信线",
                "strand_affinity": {"quest": 0.5, "fire": 0.1, "constellation": 0.4},
            },
        ],
        "ending_strategy": "公开密信并完成主线选择。",
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=12)
    blueprint = NarrativeBlueprint.model_validate(normalized)
    result = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="simple")

    assert blueprint.suspense_schedule[0].related_subplot == "主线"
    assert blueprint.suspense_schedule[1].related_subplot == "密信线"
    assert not any("关联了不存在的支线" in warning for warning in result.warnings)


def test_pre_normalize_blueprint_folds_extra_character_arc_milestone_keys() -> None:
    payload = {
        "synopsis": "主角在理性与情感之间完成选择。",
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 12,
                "description": "建立关系张力",
            }
        ],
        "character_arcs": [
            {
                "character": "沈知微",
                "arc_summary": "从自我保护到主动信任。",
                "milestones": [
                    {
                        "chapter_start": 1,
                        "chapter_end": 3,
                        "description": "用理性克制情感反应。",
                        "情感变化曲线": "表面理性克制到内心坦然",
                        "角色定位": "情感推手与边界守护者",
                    }
                ],
            }
        ],
    }

    normalized = pre_normalize_blueprint_payload(payload, total_chapters=12)
    blueprint = NarrativeBlueprint.model_validate(normalized)
    milestone = blueprint.character_arcs[0].milestones[0]

    assert "情感变化曲线" in milestone.description
    assert "角色定位" in milestone.description
    assert milestone.chapter_start == 1
    assert milestone.chapter_end == 3


def test_local_blueprint_structural_fallback_fills_missing_subplots_and_suspense() -> None:
    payload = {
        "synopsis": "这是一个仍未补齐支线与悬念的蓝图。",
        "volume_mode": False,
        "volumes": [],
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 20,
                "description": "建立主线冲突",
                "key_events": ["引入核心谜团"],
                "tension_level": "渐升",
            }
        ],
        "key_turning_points": [],
        "character_arcs": [],
        "subplot_plan": [],
        "suspense_schedule": [],
        "ending_strategy": "暂未完善",
    }

    before = validate_blueprint(
        NarrativeBlueprint.model_validate(payload),
        total_chapters=20,
        narrative_complexity="standard",
    )
    assert any("支线数量不足" in error for error in before.errors)
    assert any("悬念时间表为空" in error for error in before.errors)

    repaired = apply_local_blueprint_structural_fallback(
        payload,
        total_chapters=20,
        narrative_complexity="standard",
        validation_errors=before.errors,
    )
    assert repaired is not None
    blueprint = NarrativeBlueprint.model_validate(repaired)
    after = validate_blueprint(blueprint, total_chapters=20, narrative_complexity="standard")

    assert after.errors == []
    assert len(blueprint.subplot_plan) == 2
    assert blueprint.suspense_schedule
    for subplot in blueprint.subplot_plan:
        assert len(subplot.chapter_events) >= 3
        assert any(
            link.source_type == "main_plot" and link.link_type == "trigger_start"
            for link in subplot.weave_links
        )
        assert any(
            link.link_type in {"feed_main", "reveal_key"} for link in subplot.weave_links
        )


def test_local_blueprint_structural_fallback_repairs_out_of_range_textual_refs() -> None:
    payload = {
        "synopsis": "杏林旧案推动主角完成信任与权力边界的抉择。",
        "volume_mode": True,
        "volumes": [
            {
                "volume_number": 1,
                "title": "初诊",
                "start_chapter": 1,
                "end_chapter": 6,
                "arc_goal": "建立旧案入口与人物关系。",
            },
            {
                "volume_number": 2,
                "title": "问心",
                "start_chapter": 7,
                "end_chapter": 12,
                "arc_goal": "第3章旧案线索在权力压力中反转。",
                "milestone_targets": ["第3章完成关键证据回收。"],
            },
        ],
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 6,
                "description": "建立主线冲突。",
                "key_events": ["引入核心谜团"],
                "tension_level": "渐升",
            },
            {
                "phase_name": "倒置前铺垫",
                "chapter_start": 7,
                "chapter_end": 12,
                "description": "第3章的证据压力在本阶段升级。",
                "key_events": ["第3章日记曝光前的关系试探"],
                "tension_level": "高压",
            },
        ],
        "key_turning_points": [
            {
                "chapter_number": 2,
                "description": "旧案重启。",
                "location": "诊所",
                "characters_involved": ["林晚"],
            },
            {
                "chapter_number": 7,
                "description": "证据被重新解释。",
                "location": "医院",
                "characters_involved": ["林晚", "沈砚"],
            },
            {
                "chapter_number": 12,
                "description": "权力倒置完成。",
                "location": "听证会",
                "characters_involved": ["林晚", "沈砚"],
            },
        ],
        "character_arcs": [
            {
                "character": "林晚",
                "arc_summary": "从自证清白到主动掌握话语权。",
                "milestones": [
                    {
                        "chapter_start": 7,
                        "chapter_end": 12,
                        "description": "第3章的退让在本阶段被改写为主动反击。",
                    }
                ],
            }
        ],
        "subplot_plan": [
            {
                "name": "真相线",
                "description": "围绕旧案证据的追索与公开。",
                "involved_chapters": [2, 7, 12],
                "chapter_events": [
                    {"chapter_number": 2, "event": "旧案线索出现。"},
                    {"chapter_number": 7, "event": "证据链被重新组织。"},
                    {"chapter_number": 12, "event": "关键真相公开。"},
                ],
                "weave_links": [
                    {
                        "source_type": "main_plot",
                        "source_ref": "旧案重启",
                        "target_subplot": "真相线",
                        "trigger_chapter": 2,
                        "link_type": "trigger_start",
                        "description": "主线调查触发真相线。",
                    },
                    {
                        "source_type": "subplot",
                        "source_ref": "真相线",
                        "target_subplot": "主线",
                        "trigger_chapter": 12,
                        "link_type": "reveal_key",
                        "description": "真相线反哺主线裁决。",
                    },
                ],
                "priority": "normal",
                "resolution_chapter": 12,
                "resolution_target": "main_turning_point:3",
                "resolution_type": "reveal",
            }
        ],
        "suspense_schedule": [
            {
                "suspense_id": "s_truth",
                "suspense_type": "mystery",
                "introduce_chapter": 2,
                "resolve_chapter": 12,
                "description": "旧案证据为何被掩盖。",
                "urgency_level": "high",
                "related_subplot": "真相线",
                "strand_affinity": {"quest": 0.5, "fire": 0.2, "constellation": 0.3},
            }
        ],
        "ending_strategy": "公开证据并完成关系与权力秩序的重新定位。",
    }

    before = validate_blueprint(
        NarrativeBlueprint.model_validate(payload),
        total_chapters=12,
        narrative_complexity="simple",
    )
    assert any("文本引用第 3 章" in error for error in before.errors)

    repaired = apply_local_blueprint_structural_fallback(
        payload,
        total_chapters=12,
        narrative_complexity="simple",
        validation_errors=before.errors,
    )

    assert repaired is not None
    assert "第3章" not in str(repaired)
    blueprint = NarrativeBlueprint.model_validate(repaired)
    after = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="simple")
    assert after.errors == []
    assert "本卷前段" in blueprint.volumes[1].arc_goal
    assert "本阶段前段" in blueprint.narrative_phases[1].description
    assert "该里程碑阶段" in blueprint.character_arcs[0].milestones[0].description


def test_local_blueprint_structural_fallback_treats_total_chapters_as_contract() -> None:
    payload = {
        "synopsis": "第15章完成终局公开。",
        "volume_mode": True,
        "volumes": [
            {
                "volume_number": 1,
                "title": "初诊",
                "start_chapter": 1,
                "end_chapter": 6,
                "arc_goal": "建立旧案入口。",
            },
            {
                "volume_number": 2,
                "title": "问心",
                "start_chapter": 7,
                "end_chapter": 15,
                "arc_goal": "第15章完成权力倒置。",
            },
        ],
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 6,
                "description": "建立冲突。",
            },
            {
                "phase_name": "收束",
                "chapter_start": 7,
                "chapter_end": 15,
                "description": "第15章公开证据。",
            },
        ],
        "key_turning_points": [
            {"chapter_number": 2, "description": "线索出现。"},
            {"chapter_number": 7, "description": "证据反转。"},
            {"chapter_number": 15, "description": "真相公开。"},
        ],
        "subplot_plan": [
            {
                "name": "真相线",
                "description": "旧案证据追索。",
                "involved_chapters": [2, 7, 15],
                "chapter_events": [
                    {"chapter_number": 2, "event": "线索出现。"},
                    {"chapter_number": 7, "event": "证据反转。"},
                    {"chapter_number": 15, "event": "真相公开。"},
                ],
                "weave_links": [
                    {
                        "source_type": "main_plot",
                        "source_ref": "旧案重启",
                        "target_subplot": "真相线",
                        "trigger_chapter": 2,
                        "link_type": "trigger_start",
                        "description": "主线触发真相线。",
                    },
                    {
                        "source_type": "subplot",
                        "source_ref": "真相线",
                        "target_subplot": "主线",
                        "trigger_chapter": 15,
                        "link_type": "reveal_key",
                        "description": "真相线反哺主线。",
                    },
                ],
                "priority": "normal",
                "resolution_chapter": 15,
                "resolution_target": "main_turning_point:3",
                "resolution_type": "reveal",
            }
        ],
        "suspense_schedule": [
            {
                "suspense_id": "s_truth",
                "suspense_type": "mystery",
                "introduce_chapter": 15,
                "resolve_chapter": 15,
                "description": "证据何时公开。",
                "urgency_level": "high",
                "related_subplot": "真相线",
                "strand_affinity": {"quest": 0.5, "fire": 0.2, "constellation": 0.3},
            }
        ],
        "ending_strategy": "第15章完成关系收束。",
    }

    before = validate_blueprint(
        NarrativeBlueprint.model_validate(payload),
        total_chapters=12,
        narrative_complexity="simple",
    )
    assert any("超出总章节数" in error or "越界" in error for error in before.errors)

    repaired = apply_local_blueprint_structural_fallback(
        payload,
        total_chapters=12,
        narrative_complexity="simple",
        validation_errors=before.errors,
    )

    assert repaired is not None
    blueprint = NarrativeBlueprint.model_validate(repaired)
    after = validate_blueprint(blueprint, total_chapters=12, narrative_complexity="simple")
    assert after.errors == []
    assert max(phase.chapter_end for phase in blueprint.narrative_phases) == 12
    assert max(volume.end_chapter for volume in blueprint.volumes) == 12
    assert all(point.chapter_number <= 12 for point in blueprint.key_turning_points)
    assert all(chapter <= 12 for chapter in blueprint.subplot_plan[0].involved_chapters)
    assert blueprint.subplot_plan[0].resolution_chapter <= 12
    assert blueprint.suspense_schedule[0].resolve_chapter <= 12


def test_blueprint_repair_policy_classifies_missing_execution_graph() -> None:
    payload = {
        "synopsis": "这是一个仍未补齐支线与悬念的蓝图。",
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 20,
                "description": "建立主线冲突",
            }
        ],
        "subplot_plan": [],
        "suspense_schedule": [],
    }
    policy = get_init_repair_policy(InitArtifact.BLUEPRINT)
    assert isinstance(policy, BlueprintRepairPolicy)

    report = policy.validate(
        NarrativeBlueprint.model_validate(payload),
        InitRepairContext(service_ctx=None, outline_ctx={}, total_chapters=20),
    )

    issue_kinds = {issue.kind for issue in report.issues if issue.severity == "error"}
    issue_fields = {issue.field for issue in report.issues if issue.severity == "error"}
    assert InitRepairIssueKind.MISSING_EXECUTION_GRAPH in issue_kinds
    assert {"subplot_plan", "suspense_schedule"}.issubset(issue_fields)
