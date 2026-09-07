from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, SceneIntent
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.desktop.workflow_requests import build_prepare_chapter_request
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.generation.scene_writing import (
    assess_draft_plan_coverage,
    build_scene_draft_context,
    plan_structure_audit_path,
    scene_execution_groups,
    stitch_scene_texts,
    validate_scene_draft_locally,
    validate_scene_plan_locally,
)
from novel_forge.pipeline.long.stages import planning as planning_stage
from novel_forge.pipeline.steps import bridge_step, plan_step
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.workspace.contracts import RunChapterRequest
from novel_forge.workspace.sessions.chapter_session_handlers import _guard_protected_plan_texts


def _scene(scene_id: str, order: int, *, deps: list[str] | None = None) -> SceneIntent:
    return SceneIntent(
        scene_id=scene_id,
        summary=f"{scene_id} summary",
        scene_goal=f"{scene_id} goal",
        required_outcome=f"{scene_id} event",
        exit_target_state=f"{scene_id} exit",
        owned_events=[f"{scene_id} event"],
        owned_state_changes=[f"{scene_id} exit"],
        dependency_scene_ids=deps or [],
        draft_order=order,
        target_words=500,
    )


def test_chapter_writing_mode_defaults_to_whole_chapter() -> None:
    request = RunChapterRequest(project_id="p", chapter_number=1)

    assert request.writing_mode == "whole_chapter"


def test_desktop_prepare_request_passes_writing_mode() -> None:
    request = build_prepare_chapter_request(
        project_id="p",
        chapter_number=2,
        force=False,
        notes="",
        writing_mode="scene_level",
    )

    assert request.writing_mode == "scene_level"


def test_local_scene_plan_validation_catches_overlap_and_bad_dependency() -> None:
    plan = ChapterPlan(
        scene_intents=[
            _scene("scene_01", 1),
            SceneIntent(
                scene_id="scene_02",
                summary="scene_02 summary",
                scene_goal="scene_02 goal",
                required_outcome="scene_01 event",
                exit_target_state="scene_02 exit",
                owned_events=["scene_01 event"],
                owned_state_changes=["scene_02 exit"],
                dependency_scene_ids=["missing_scene"],
                draft_order=2,
                target_words=500,
            ),
        ],
        opening_contract="open",
        closing_contract="close",
    )
    report = validate_scene_plan_locally(
        plan=plan,
        bridge=ChapterBridge(to_chapter=1, action_handoff="scene_01 summary"),
        target_word_count=1000,
    )

    codes = {issue["code"] for issue in report["issues"]}
    assert "duplicate_owned_events" in codes
    assert "invalid_dependency" in codes
    assert report["valid"] is False


def test_local_scene_plan_validation_requires_atomic_owned_events() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="令昭进入账房",
                required_outcome="发现残页；避开守卫",
                owned_events=["发现残页；避开守卫"],
                target_words=500,
            )
        ]
    )

    report = validate_scene_plan_locally(
        plan=plan,
        bridge=ChapterBridge(to_chapter=1),
        target_word_count=500,
    )

    codes = {issue["code"] for issue in report["issues"]}
    assert "required_outcome_not_atomically_owned" in codes
    assert "non_atomic_owned_event" in codes
    assert report["valid"] is False


def test_plan_prompt_exposes_local_structure_repair_without_changing_schema() -> None:
    prompt = PromptBuilder().render(
        TaskType.PLAN_CHAPTER,
        {
            "stage_cards": {
                "chapter": {"chapter_number": 2, "goal": "取回账簿"},
                "contract": {},
                "bridge": {},
            },
            "scene_plan_validation_issues": [
                {"code": "last_scene_exit_missing", "message": "最后一场缺少出口状态。"}
            ],
        },
    )

    assert "本地结构审计修复指令（P0）" in prompt
    assert "最后一场缺少出口状态。" in prompt
    assert "只输出以下 16 个英文" in prompt


def test_scene_plan_prompt_receives_world_rules_and_replan_issues() -> None:
    prompt = PromptBuilder().render(
        TaskType.PLAN_CHAPTER_SCENES,
        {
            "stage_cards": {
                "chapter": {
                    "chapter_number": 6,
                    "title": "逆时怀表",
                    "goal": "进入深层梦境",
                    "pov_character": "沈岸",
                    "setting": "醒梦事务所",
                    "target_word_count": 3000,
                },
                "source": {
                    "chapter_contract": {},
                    "world_rule_card": {
                        "rule_book_version": "1",
                        "source_hash": "rules-v1",
                        "always_on": [
                            {
                                "rule": {
                                    "rule_id": "WR001",
                                    "content": "怀表倒转三圈后校准失效。",
                                    "severity": "hard",
                                    "always_on": True,
                                    "forbidden_behavior": ["禁止拆解核心机芯"],
                                    "cost_or_consequence": ["校准功能永久失效"],
                                },
                                "selection_reason": "始终生效",
                            }
                        ],
                        "relevant_rules": [],
                    },
                },
            },
            "scene_plan_validation_issues": [],
            "world_rule_validation_issues": ["硬规则 WR001 未绑定场景，也未声明不适用。"],
        },
    )

    assert "怀表倒转三圈后校准失效" in prompt
    assert "禁止拆解核心机芯" in prompt
    assert "校准功能永久失效" in prompt
    assert "世界规则覆盖重规划（P0）" in prompt
    assert "硬规则 WR001 未绑定场景" in prompt
    assert "scene_plan.world_rule_applications" in prompt
    assert "world_rule_ids" in prompt
    assert "scene_plan.cross_scene_intent" in prompt


def test_draft_scene_prompt_consumes_scene_owned_world_rule_fields() -> None:
    prompt = PromptBuilder().render(
        TaskType.DRAFT_SCENE,
        {
            "chapter_number": 6,
            "target_word_count": 800,
            "stage_cards": {
                "chapter": {
                    "chapter_number": 6,
                    "title": "逆时怀表",
                    "goal": "进入深层梦境",
                    "pov_character": "沈岸",
                    "target_word_count": 3000,
                },
                "source": {"chapter_contract": {}},
            },
            "current_scene": {
                "scene_id": "scene_03",
                "scene_goal": "安全启动同步设备",
                "summary": "沈岸核对怀表后启动设备。",
                "required_outcome": "设备启动",
                "target_words": 800,
                "world_rule_ids": ["WR001"],
                "world_rule_usage": "启动前核验怀表未被倒转三圈。",
                "world_rule_evidence_expectations": ["正文出现指针与校准读数核验"],
                "world_rule_forbidden_boundaries": ["不得拆解怀表核心机芯"],
            },
        },
    )

    assert "本场世界规则 ID：WR001" in prompt
    assert "启动前核验怀表未被倒转三圈" in prompt
    assert "正文出现指针与校准读数核验" in prompt
    assert "不得拆解怀表核心机芯" in prompt


def test_scene_execution_groups_respect_serial_edges() -> None:
    plan = ChapterPlan(
        scene_intents=[
            _scene("scene_01", 1),
            _scene("scene_02", 2),
            _scene("scene_03", 3),
        ]
    )
    report = {
        "parallel_groups": [["scene_02", "scene_03"]],
        "serial_edges": [{"before": "scene_02", "after": "scene_03"}],
    }

    groups = scene_execution_groups(plan, report)

    positions = {scene.scene_id: index for index, group in enumerate(groups) for scene in group}
    assert positions["scene_02"] < positions["scene_03"]


def test_scene_execution_groups_default_to_serial_without_explicit_parallel_opt_in() -> None:
    plan = ChapterPlan(scene_intents=[_scene("scene_01", 1), _scene("scene_02", 2)])

    groups = scene_execution_groups(
        plan,
        {"parallel_groups": [["scene_01", "scene_02"]], "serial_edges": []},
    )

    assert [[scene.scene_id for scene in group] for group in groups] == [
        ["scene_01"],
        ["scene_02"],
    ]


def test_scene_execution_groups_honors_validator_parallelism_after_plan_opt_in() -> None:
    plan = ChapterPlan(
        scene_intents=[
            _scene("scene_01", 1).model_copy(update={"parallel_group": "intercut_a"}),
            _scene("scene_02", 2).model_copy(update={"parallel_group": "intercut_a"}),
        ]
    )

    groups = scene_execution_groups(
        plan,
        {"parallel_groups": [["scene_01", "scene_02"]], "serial_edges": []},
    )

    assert [[scene.scene_id for scene in group] for group in groups] == [
        ["scene_01", "scene_02"],
    ]


def test_scene_execution_groups_never_leapfrog_an_earlier_ready_scene() -> None:
    plan = ChapterPlan(
        scene_intents=[
            _scene("scene_01", 1).model_copy(update={"parallel_group": "intercut_a"}),
            _scene("scene_02", 2),
            _scene("scene_03", 3).model_copy(update={"parallel_group": "intercut_a"}),
        ]
    )

    groups = scene_execution_groups(
        plan,
        {"parallel_groups": [["scene_01", "scene_03"]], "serial_edges": []},
    )

    assert [[scene.scene_id for scene in group] for group in groups] == [
        ["scene_01"],
        ["scene_02"],
        ["scene_03"],
    ]


def test_draft_plan_coverage_reports_weak_anchors_without_blocking() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="林远在钟楼找钥匙",
                owned_events=["找到钥匙"],
                owned_revelations=["钥匙藏在钟楼夹层"],
                owned_state_changes=["林远握住钥匙"],
                exit_target_state="带着钥匙离开钟楼",
                draft_order=1,
            )
        ],
        opening_bridge={"action_handoff": "林远冒雨赶到钟楼"},
    )

    report = assess_draft_plan_coverage(
        plan=plan,
        text="林远摸黑找遍钟楼，终于在断裂的木阶下找到钥匙。",
        target_word_count=500,
    )

    assert report["source"] == "local"
    assert report["weak_anchor_count"] >= 2
    assert {item["kind"] for item in report["weak_anchors"]} >= {
        "owned_revelations",
        "owned_state_changes",
    }
    assert report["word_count_status"] == "warning"


def test_draft_plan_coverage_tracks_planning_declared_literals() -> None:
    literal = "有些事情不是你想知道就能知道的"
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_02",
                summary="众人在糖水铺聚餐",
                required_outcome=f"林小满说明自己暂时不能透露真相，并说「{literal}」",
                owned_events=["林小满递出薄荷糖"],
                draft_order=2,
            )
        ],
        required_literals=[
            {
                "contract_id": "chapter-009-secret-boundary",
                "literal": literal,
                "scene_id": "scene_02",
                "reason": "这句话是后续解谜时回指的原始口令",
                "placement_hint": "糖水铺聚餐中林小满拒绝透露真相之后",
            }
        ],
    )

    missing = assess_draft_plan_coverage(
        plan=plan,
        text="众人只在门外提到了糖水铺。",
        target_word_count=0,
    )
    covered = assess_draft_plan_coverage(
        plan=plan,
        text="林小满说：「有些事情不是你想知道就能知道的」",
        target_word_count=0,
    )

    assert missing["missing_required_literal_count"] == 1
    assert missing["missing_required_literals"][0]["scene_id"] == "scene_02"
    assert missing["missing_required_literals"][0]["contract_id"] == (
        "chapter-009-secret-boundary"
    )
    assert covered["missing_required_literal_count"] == 0


def test_draft_plan_coverage_does_not_promote_quotes_locally() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_02",
                summary="众人在糖水铺谈到所谓的「秘密」",
                required_outcome="林小满说「有些事情不是你想知道就能知道的」",
            )
        ]
    )

    report = assess_draft_plan_coverage(
        plan=plan,
        text="众人在门外谈到秘密，林小满拒绝继续解释。",
        target_word_count=0,
    )

    assert report["required_literal_count"] == 0
    assert report["missing_required_literal_count"] == 0


def test_materialize_scene_ownership_unpacks_list_literal_strings() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="团队会合",
                required_outcome="['沈岸宣布协作形式', '林小满递出薄荷糖']",
                owned_events=["['沈岸宣布协作形式', '林小满递出薄荷糖']"],
            )
        ]
    )

    normalized = planning_stage.materialize_scene_event_ownership(plan)
    scene = normalized.scene_intents[0]

    assert scene.required_outcome == "沈岸宣布协作形式；林小满递出薄荷糖"
    assert scene.owned_events == ["沈岸宣布协作形式", "林小满递出薄荷糖"]


def test_scene_coverage_checks_outline_backfilled_fifth_anchor() -> None:
    """Backfilled beats append to owned_events and must reach draft diagnostics."""
    fifth_anchor = "第五个补回的关键事件"
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="场景推进",
                owned_events=["事件一", "事件二", "事件三", "事件四", fifth_anchor],
                draft_order=1,
                target_words=0,
            )
        ]
    )

    coverage = assess_draft_plan_coverage(
        plan=plan,
        text="正文没有写入那项关键事件。",
        target_word_count=0,
    )
    assert any(item["anchor"] == fifth_anchor for item in coverage["weak_anchors"])

    local = validate_scene_draft_locally(
        plan.scene_intents[0],
        "正文没有写入那项关键事件。",
        {"target_word_count": 0},
    )
    assert any(item.get("anchor") == fifth_anchor for item in local["soft_warnings"])


def test_guard_protects_scene_owned_anchors_during_forbidden_normalization() -> None:
    owned_event = "梦境三层的时间规则让他陷入危险"
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="角色在梦境中察觉时间异样",
                owned_events=[owned_event],
                owned_revelations=["表针与现实不同步"],
                owned_state_changes=["角色意识到时间风险"],
                draft_order=1,
            )
        ]
    )

    protected = _guard_protected_plan_texts(plan, ())

    assert owned_event in protected
    assert "表针与现实不同步" in protected
    assert "角色意识到时间风险" in protected


def test_stitch_scene_texts_blocks_missing_scene_and_orders_by_draft_order() -> None:
    plan = ChapterPlan(
        scene_intents=[
            _scene("scene_02", 2, deps=["scene_01"]),
            _scene("scene_01", 1),
        ]
    )

    missing_text, missing_report = stitch_scene_texts(
        plan=plan,
        scene_texts={"scene_01": "第一场正文"},
    )
    assert missing_text == ""
    assert missing_report["missing_scenes"] == ["scene_02"]

    stitched, report = stitch_scene_texts(
        plan=plan,
        scene_texts={"scene_01": "# scene_01\n第一场正文", "scene_02": "第二场正文"},
    )
    assert stitched.startswith("第一场正文")
    assert "第二场正文" in stitched
    assert report["missing_scenes"] == []


def test_scene_draft_local_validation_separates_hard_and_soft_diagnostics() -> None:
    scene = SceneIntent(
        scene_id="scene_01",
        summary="林远进入钟楼密室",
        scene_goal="让林远找到钥匙",
        owned_events=["打开密室"],
        owned_revelations=["发现钥匙"],
        owned_state_changes=["林远掌握钥匙"],
        forbidden_overlap=["提前相认"],
        handoff_to_next="钟声响起",
        target_words=0,
    )

    hard_report = validate_scene_draft_locally(
        scene,
        "# scene_01\nscene_id: scene_01\nowned_events: 打开密室",
        {"target_word_count": 0},
    )
    hard_codes = {item["code"] for item in hard_report["hard_failures"]}
    assert {"scene_title_leak", "scene_id_leak", "planning_token_leak"} <= hard_codes
    assert hard_report["valid"] is False

    soft_report = validate_scene_draft_locally(
        scene,
        "林远推开石门，潮湿的钟楼里提前相认四个字像被人刻在墙上。"
        "他停住脚步，听见楼上传来脚步声，却还没看见钥匙。",
        {"target_word_count": 0},
    )
    soft_codes = {item["code"] for item in soft_report["soft_warnings"]}
    assert "forbidden_overlap_possible_hit" in soft_codes
    assert "handoff_anchor_weak" in soft_codes
    assert "owned_state_changes_anchor_weak" in soft_codes
    assert soft_report["valid"] is True


def test_build_scene_draft_context_scopes_stage_cards_and_adds_boundaries() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="甲发现门缝里的线索",
                scene_goal="发现线索",
                required_characters=["甲"],
                owned_events=["发现纸条"],
                handoff_to_next="甲把纸条藏进袖口",
                draft_order=1,
            ),
            SceneIntent(
                scene_id="scene_02",
                summary="乙追问甲",
                scene_goal="制造压力",
                required_characters=["乙"],
                entry_state="甲藏起纸条",
                draft_order=2,
            ),
        ]
    )
    base_context = {
        "chapter_number": 1,
        "stage_cards": {
            "plan": {
                "opening_bridge": {"action_handoff": "甲刚听见门外脚步"},
                "scene_intents": [{"scene_id": "scene_01"}, {"scene_id": "scene_02"}],
                "cross_scene_intent": {"pacing_curve": [2, 4]},
                "expression_channel_records": [{"channel": str(i)} for i in range(8)],
            },
            "bridge": {"action_handoff": "旧路径不应进入场景卡"},
            "contract": {
                "forbidden_progressions": ["不得坐实小书童无人见过"],
                "future_leak_risks": ["提前确认小书童无人见过"],
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_ladder_15",
                        "cognitive_object": "小书童无人见过",
                        "cognitive_level": "suspicion",
                    }
                ],
            },
            "characters": [{"name": "甲"}, {"name": "乙"}],
            "knowledge": {
                "character_cards": [{"character": "甲"}, {"character": "乙"}],
                "chapter_ops": list(range(10)),
                "global_ops": list(range(10)),
            },
            "editorial": {
                "character_voices": [{"character": "甲"}, {"character": "乙"}],
            },
        },
    }

    context = build_scene_draft_context(
        base_context=base_context,
        plan=plan,
        scene=plan.scene_intents[0],
        target_word_count=500,
        completed_scene_handoffs=[],
    )

    cards = context["stage_cards"]
    assert "bridge" not in cards
    assert "scene_intents" not in cards["plan"]
    assert "cross_scene_intent" not in cards["plan"]
    assert cards["plan"]["opening_bridge"]["action_handoff"] == "甲刚听见门外脚步"
    assert "scene_plan_summary" not in context
    assert cards["contract"]["forbidden_progressions"] == ["不得坐实小书童无人见过"]
    assert cards["contract"]["future_leak_risks"] == ["提前确认小书童无人见过"]
    assert cards["contract"]["cognitive_constraints"][0]["claim_id"] == "claim_ladder_15"
    assert cards["characters"] == [{"name": "甲"}]
    assert cards["knowledge"]["character_cards"] == [{"character": "甲"}]
    assert cards["editorial"]["character_voices"] == [{"character": "甲"}]
    assert context["current_scene"]["owned_events"] == ["发现纸条"]
    assert context["scene_intent"]["topic"] == "甲发现门缝里的线索"
    assert context["scene_intent"]["characters_involved"] == ["甲"]
    assert context["adjacent_scene_handoffs"][0]["scene_id"] == "scene_02"

    retry_context = build_scene_draft_context(
        base_context=base_context,
        plan=plan,
        scene=plan.scene_intents[0],
        target_word_count=500,
        completed_scene_handoffs=[],
        retry_feedback=[{"code": "severe_word_shortfall", "message": "场景正文过短。"}],
    )
    assert retry_context["scene_retry_feedback"] == [
        {"code": "severe_word_shortfall", "message": "场景正文过短。"}
    ]

    retry_prompt = PromptBuilder().render(TaskType.DRAFT_SCENE, retry_context)
    assert "上一稿局部验收失败" in retry_prompt
    assert "场景正文过短。" in retry_prompt


def test_stitch_scene_texts_includes_scene_diagnostics() -> None:
    plan = ChapterPlan(scene_intents=[_scene("scene_01", 1)])
    diagnostics = {
        "scene_01": {
            "scene_id": "scene_01",
            "valid": True,
            "hard_failures": [],
            "soft_warnings": [{"code": "handoff_anchor_weak"}],
        }
    }

    _text, report = stitch_scene_texts(
        plan=plan,
        scene_texts={"scene_01": "第一场正文"},
        scene_diagnostics=diagnostics,
        hard_failures_resolved=[{"scene_id": "scene_01", "code": "scene_title_leak"}],
    )

    assert report["scene_diagnostics"] == diagnostics
    assert report["soft_warning_count"] == 1
    assert report["hard_failures_resolved"][0]["code"] == "scene_title_leak"


def test_wave_prompt_renders_scene_stitch_report_guidance() -> None:
    prompt = PromptBuilder().render(
        TaskType.WAVE_CHAPTER,
        {
            "chapter_number": 1,
            "target_word_count": 1000,
            "draft_text": "第一场正文\n\n第二场正文",
            "scene_stitch_report": {
                "scene_count": 2,
                "missing_scenes": [],
                "possible_transition_gaps": ["scene_01 -> scene_02 交接可能偏弱"],
                "soft_warning_count": 1,
                "scene_diagnostics": {
                    "scene_01": {
                        "soft_warnings": [
                            {
                                "code": "handoff_anchor_weak",
                                "message": "交接锚点弱",
                            }
                        ]
                    }
                },
            },
            "draft_plan_coverage": {
                "word_count": 970,
                "target_word_count": 1000,
                "word_count_status": "pass",
                "weak_anchors": [
                    {
                        "scene_id": "scene_02",
                        "kind": "owned_events",
                        "message": "scene_02 的独占事件在草稿中覆盖较弱",
                    }
                ],
                "missing_required_literals": [
                    {
                        "scene_id": "scene_02",
                        "literal": "有些事情不是你想知道就能知道的",
                        "source_field": "owned_events",
                    }
                ],
            },
            "stage_cards": {
                "chapter": {"chapter_number": 1, "target_word_count": 1000},
                "plan": {
                    "scene_intents": [
                        {"scene_id": "scene_01", "summary": "第一场", "target_words": 500},
                        {"scene_id": "scene_02", "summary": "第二场", "target_words": 500},
                    ],
                    "cross_scene_intent": {"cross_scene_references": [], "pacing_curve": [2, 4]},
                },
            },
        },
    )

    assert "场景拼接诊断" in prompt
    assert "scene_01 -> scene_02 交接可能偏弱" in prompt
    assert "草稿计划覆盖诊断" in prompt
    assert "scene_02 的独占事件在草稿中覆盖较弱" in prompt
    assert "有些事情不是你想知道就能知道的" in prompt
    assert "恢复 Plan 中已有而 DRAFT 遗漏的 scene" in prompt
    assert "不得为了处理软诊断而新增未规划事件" in prompt


@pytest.mark.asyncio
async def test_scene_plan_validation_replan_preserves_outline_and_kernel_context(
    tmp_path, monkeypatch
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("scene_replan"))
    chapter_outline = ChapterOutline(
        chapter_number=1,
        goal="让主角发现第一条异常线索",
        expected_word_count=1000,
    )
    bridge = ChapterBridge(to_chapter=1, action_handoff="主角抵达钟楼")
    first_plan = ChapterPlan(
        scene_intents=[_scene("scene_01", 1)],
        opening_contract="开场",
        closing_contract="收束",
    )
    second_plan = ChapterPlan(
        scene_intents=[_scene("scene_01", 1), _scene("scene_02", 2, deps=["scene_01"])],
        opening_contract="修正后开场",
        closing_contract="修正后收束",
    )
    plan_inputs: list[Any] = []
    validation_calls = 0
    events: list[str] = []

    class FakePacket:
        bridge: ChapterBridge | None = None
        guard_constraints: list[str] = []

        def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
            return {"bridge": self.bridge.model_dump(mode=mode) if self.bridge else None}

    class FakeBridgeStep:
        coherence_issues: list[str] = []

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def run(self, _input_data: Any) -> ChapterBridge:
            return bridge

    class FakePlanStep:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def run(self, input_data: Any) -> ChapterPlan:
            plan_inputs.append(input_data)
            return first_plan if len(plan_inputs) == 1 else second_plan

    class FakeComposer:
        def compose_bridge_input(self, _chapter_number: int) -> dict[str, Any]:
            return {}

        def compose_plan_input(self, _chapter_number: int) -> dict[str, Any]:
            return {"relationships": [{"relationship_id": "rel_1", "summary": "师徒关系"}]}

    async def fake_load_story_kernel_composer(*_args: Any, **_kwargs: Any) -> FakeComposer:
        return FakeComposer()

    async def fake_validate_scene_plan_with_llm(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            return {
                "valid": False,
                "issues": [
                    {
                        "code": "missing_dependency",
                        "message": "缺少依赖",
                        "severity": "high",
                    }
                ],
            }
        return {"valid": True, "issues": []}

    async def fake_collect_memory_hints(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(bridge_step, "BridgeStep", FakeBridgeStep)
    monkeypatch.setattr(plan_step, "PlanChapterStep", FakePlanStep)
    monkeypatch.setattr(
        planning_stage, "load_story_kernel_composer", fake_load_story_kernel_composer
    )
    monkeypatch.setattr(
        planning_stage, "_collect_generation_memory_hints", fake_collect_memory_hints
    )
    monkeypatch.setattr(planning_stage, "dump_story_bible_for_prompt", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(planning_stage, "audit_plan_contract", lambda **_kwargs: [])
    monkeypatch.setattr(planning_stage, "build_planning_hint", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(planning_stage, "suggest_dynamic_focus_ids", lambda **_kwargs: [])
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.generation.scene_writing.validate_scene_plan_with_llm",
        fake_validate_scene_plan_with_llm,
    )

    runner = SimpleNamespace(
        _storage=storage,
        _router=None,
        _builder=None,
        _settings=SimpleNamespace(long_upstream_compass_enabled=False),
        _config=SimpleNamespace(writing_mode="scene_level"),
        _on_step=lambda step, _payload: events.append(step),
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=chapter_outline,
        story_bible=SimpleNamespace(genre="", tone="", rules=[]),
        blueprint=None,
        outline=SimpleNamespace(chapters=[]),
        style_profile=None,
        narrative_contract=None,
        editorial_contract=None,
    )

    _bridge, plan, _memory_hints, report = await planning_stage.generate_bridge_and_plan(
        runner,
        bundle,
        FakePacket(),
        1,
        SimpleNamespace(),
    )

    assert plan is not second_plan
    assert len(plan.scene_intents) == 2
    assert plan.opening_bridge["action_handoff"] == "主角抵达钟楼"
    assert report == {"valid": True, "issues": []}
    assert len(plan_inputs) == 2
    assert plan_inputs[1].chapter_outline is chapter_outline
    assert plan_inputs[1].kernel_context == {
        "relationships": [{"relationship_id": "rel_1", "summary": "师徒关系"}]
    }
    assert plan_inputs[1].scene_plan_validation_issues
    assert events.count("scene_plan_validation") == 2
    assert "retrieval_eval_planning" not in events


@pytest.mark.asyncio
async def test_planning_retrieval_eval_hook_writes_report_and_event(tmp_path, monkeypatch) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("retrieval_eval"))
    chapter_outline = ChapterOutline(
        chapter_number=1,
        goal="让主角发现秘密线索",
        pov_character="玄昱",
        involved_characters=["沈清漪"],
        expected_word_count=1000,
    )
    bridge = ChapterBridge(to_chapter=1, action_handoff="玄昱抵达钟楼")
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="玄昱在棋局旁发现秘密线索",
                pov_character="玄昱",
                required_characters=["玄昱", "沈清漪"],
                owned_events=["棋局留下线索"],
            )
        ],
        key_revelations=["秘密线索指向影卫"],
    )
    events: list[tuple[str, Any]] = []

    class FakePacket:
        bridge: ChapterBridge | None = None
        guard_constraints: list[str] = []
        known_characters: list[str] = []
        active_relationships: list[Any] = []

        def model_dump(self, *, mode: str = "json") -> dict[str, Any]:
            return {"bridge": self.bridge.model_dump(mode=mode) if self.bridge else None}

    class FakeBridgeStep:
        coherence_issues: list[str] = []

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def run(self, _input_data: Any) -> ChapterBridge:
            return bridge

    class FakePlanStep:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def run(self, _input_data: Any) -> ChapterPlan:
            return plan

    class FakeComposer:
        def compose_bridge_input(self, _chapter_number: int) -> dict[str, Any]:
            return {}

        def compose_plan_input(self, _chapter_number: int) -> dict[str, Any]:
            return {
                "entities": [
                    {
                        "entity_id": "char_1",
                        "name": "玄昱",
                        "entity_type": "character",
                        "source_chapter": 0,
                        "last_seen_chapter": 1,
                    },
                    {
                        "entity_id": "item_1",
                        "name": "黑灰沾领",
                        "entity_type": "item",
                        "source_chapter": 0,
                        "last_seen_chapter": 1,
                    },
                ],
                "timeline": [{"event": "玄昱发现秘密线索", "chapter": 1}],
            }

    async def fake_load_story_kernel_composer(*_args: Any, **_kwargs: Any) -> FakeComposer:
        return FakeComposer()

    async def fake_collect_memory_hints(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"relevant_history": [{"event_summary": "棋局留下线索", "chapter_number": 1}]}

    monkeypatch.setattr(bridge_step, "BridgeStep", FakeBridgeStep)
    monkeypatch.setattr(plan_step, "PlanChapterStep", FakePlanStep)
    monkeypatch.setattr(
        planning_stage, "load_story_kernel_composer", fake_load_story_kernel_composer
    )
    monkeypatch.setattr(
        planning_stage, "_collect_generation_memory_hints", fake_collect_memory_hints
    )
    monkeypatch.setattr(planning_stage, "dump_story_bible_for_prompt", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(planning_stage, "audit_plan_contract", lambda **_kwargs: [])
    monkeypatch.setattr(planning_stage, "build_planning_hint", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(planning_stage, "suggest_dynamic_focus_ids", lambda **_kwargs: [])

    runner = SimpleNamespace(
        _storage=storage,
        _router=None,
        _builder=None,
        _settings=SimpleNamespace(
            long_upstream_compass_enabled=False,
            long_retrieval_eval_point_a_enabled=True,
            long_retrieval_eval_max_scenes=None,
            long_retrieval_eval_persist_report=True,
            long_plan_max_foreshadowing=2,
        ),
        _config=SimpleNamespace(writing_mode="whole_chapter"),
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=chapter_outline,
        story_bible=SimpleNamespace(genre="", tone="", rules=[]),
        blueprint=None,
        outline=SimpleNamespace(chapters=[]),
        style_profile=None,
        narrative_contract=None,
        editorial_contract=None,
    )

    await planning_stage.generate_bridge_and_plan(
        runner,
        bundle,
        FakePacket(),
        1,
        SimpleNamespace(),
    )

    report_path = layout.retrieval_eval_report_path(1)
    assert storage.exists(report_path)
    payload = storage.load_json(report_path)
    assert payload["report_type"] == "retrieval_eval_point_a"
    assert payload["retrieved"]["noisy_entity_names"] == ["黑灰沾领"]
    eval_events = [payload for step, payload in events if step == "retrieval_eval_planning"]
    assert len(eval_events) == 1
    assert eval_events[0]["aggregate"]["character_recall"] == 0.5
    structure_audit = storage.load_json(plan_structure_audit_path(layout, 1))
    assert structure_audit["mode"] == "whole_chapter"
    assert structure_audit["replanned"] is True
    assert any(step == "plan_structure_audit" for step, _payload in events)
