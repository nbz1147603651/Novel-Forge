from __future__ import annotations

from novel_forge.core.schemas.continuity import ChapterPlan
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.world_rules import WorldRuleCard, WorldRuleCardEntry, WorldRuleSpec
from novel_forge.pipeline.long.services.constraints.world_rule_plan_contract import (
    materialize_plan_world_rule_bindings,
    validate_plan_world_rule_coverage,
)
from novel_forge.pipeline.steps.planning.hints import (
    _normalize_scene_intents,
    enforce_scene_switch_limit,
)
from novel_forge.pipeline.steps.planning.scene_contract import (
    canonicalize_cross_scene_intent,
    canonicalize_scene_intent_inputs,
    remap_plan_scene_references,
    restore_scene_execution_fields,
    scene_limit_id_map,
)


def _card() -> WorldRuleCard:
    entries = [
        WorldRuleCardEntry(
            rule=WorldRuleSpec(
                rule_id=f"WR00{index}",
                category="physics",
                content=f"规则 {index}",
                severity="hard",
                always_on=True,
                forbidden_behavior=[f"不得违反规则 {index}"],
            ),
            selection_reason="始终生效",
        )
        for index in range(1, 4)
    ]
    return WorldRuleCard(chapter_number=6, always_on=entries)


def test_scene_aliases_survive_legacy_normalizer() -> None:
    raw = [
        {
            "scene_id": "s6_02",
            "overview": "阿荞执行洗字流程。",
            "characters": {"pov": "沈岸", "active": ["沈岸", "阿荞"]},
            "motivations": {"阿荞": "完成清理"},
            "entry": "沈岸坐到工作台前。",
            "exit": "清理完成。",
            "time": "上午",
            "word_count_budget": 1200,
            "sensory_anchors": ["靛蓝溶液的气味"],
            "sensory_focus": "嗅觉",
            "dialogue_subtext": "阿荞隐瞒碎片。",
            "body_language_budget": ["指尖轻颤", "屏住呼吸"],
            "pov_boundary": "沈岸不知道阿荞私留碎片。",
            "world_rule_ids": ["WR003"],
            "world_rule_usage": "安全封存记忆。",
            "world_rule_evidence_expectations": "玻璃罐被妥善放置。",
            "world_rule_forbidden_boundaries": "不得故意损毁玻璃罐。",
        }
    ]
    canonical = canonicalize_scene_intent_inputs(raw)
    scenes = _normalize_scene_intents(
        canonical,
        outline=ChapterOutline(
            chapter_number=6,
            title="洗字留痕",
            goal="完成洗字并准备入梦",
            pov_character="沈岸",
            setting="靛蓝洗字巷",
        ),
    )
    scene = restore_scene_execution_fields(scenes, canonical)[0]

    assert scene["summary"] == "阿荞执行洗字流程。"
    assert scene["required_characters"] == ["沈岸", "阿荞"]
    assert scene["character_motivations"][0]["character"] == "阿荞"
    assert scene["target_words"] == 1200
    assert scene["sensory_focus"] == "嗅觉"
    assert scene["dialogue_subtext"] == "阿荞隐瞒碎片。"
    assert scene["pov_knowledge_constraints"]["forbidden_knowledge"]
    assert scene["world_rule_ids"] == ["WR003"]


def test_scene_limit_remaps_all_reference_namespaces() -> None:
    payload = {
        "scene_intents": [
            {
                "scene_id": f"s6_{index:02d}",
                "summary": f"场景 {index}",
                "dependency_scene_ids": [f"s6_{index - 1:02d}"] if index > 1 else [],
                "target_words": 500,
            }
            for index in range(1, 5)
        ],
        "world_rule_applications": [{"rule_id": "WR001", "scene_id": "s6_03"}],
        "required_literals": [{"literal": "停手", "scene_id": "s6_04"}],
        "cross_scene_intent": {
            "cross_scene_references": [{"source_scene_id": "s6_03", "target_scene_id": "s6_04"}]
        },
    }
    before = [scene["scene_id"] for scene in payload["scene_intents"]]

    assert enforce_scene_switch_limit(payload, max_scenes=3)
    remap_plan_scene_references(payload, scene_limit_id_map(before, max_scenes=3))

    assert payload["world_rule_applications"][0]["scene_id"] == "scene_02"
    assert payload["required_literals"][0]["scene_id"] == "scene_03"
    assert payload["cross_scene_intent"]["cross_scene_references"][0] == {
        "source_scene_id": "scene_02",
        "target_scene_id": "scene_03",
    }


def test_cross_scene_aliases_are_canonicalized_for_wave() -> None:
    assert canonicalize_cross_scene_intent(
        {
            "cross_scene_references": [
                {
                    "source_scene_id": "scene_01",
                    "target_scene_id": "scene_02",
                    "type": "state_transfer",
                    "description": "状态接力",
                }
            ],
            "pacing_curve": [2, 4],
        }
    ) == {
        "cross_scene_references": [
            {
                "from_scene": "scene_01",
                "to_scene": "scene_02",
                "ref_type": "callback",
                "description": "状态接力",
            }
        ],
        "pacing_curve": [2, 4],
    }


def test_world_rule_ledger_is_deterministically_projected() -> None:
    applications = [
        {
            "rule_id": entry.rule.rule_id,
            "scene_id": "s6_02",
            "usage": f"执行 {entry.rule.rule_id}",
            "expected_evidence": f"出现 {entry.rule.rule_id} 证据",
            "forbidden_boundary": f"不得违反 {entry.rule.rule_id}",
        }
        for entry in _card().all_entries
    ]
    plan = ChapterPlan.model_validate(
        materialize_plan_world_rule_bindings(
            {
                "scene_intents": [
                    {"scene_id": "scene_01", "summary": "进入洗字巷", "draft_order": 1},
                    {"scene_id": "scene_02", "summary": "执行洗字", "draft_order": 2},
                ],
                "world_rule_applications": applications,
            }
        )
    )

    assert {item.scene_id for item in plan.world_rule_applications} == {"scene_02"}
    assert set(plan.scene_intents[1].world_rule_ids) == {"WR001", "WR002", "WR003"}
    assert validate_plan_world_rule_coverage(plan, _card()) == []
