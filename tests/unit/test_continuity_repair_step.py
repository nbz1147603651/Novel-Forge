"""Tests for ContinuityRepairStep."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityAnchor,
    ContinuityIssue,
    ContinuityRepairDirective,
    ContinuityReport,
    RepairPostcondition,
    SceneIntent,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.steps.continuity_artifact_repair import BridgeArtifactRepairer
from novel_forge.pipeline.steps.continuity_repair_step import (
    ContinuityRepairInput,
    ContinuityRepairStep,
)


def _build_plan() -> ChapterPlan:
    return ChapterPlan(
        scene_intents=[
            SceneIntent(scene_id="scene_01", summary="开场承接上一章并进入图书馆"),
            SceneIntent(scene_id="scene_02", summary="深入裂缝并留下新交接点"),
        ],
        opening_contract="承接上一章的图书馆交接点。",
        closing_contract="章末留下可直接承接的裂缝后果。",
    )


def _build_packet() -> ChapterStatePacket:
    return ChapterStatePacket(
        chapter_number=2,
        chapter_outline=ChapterOutline(
            chapter_number=2,
            title="裂缝",
            goal="推进裂缝真相线",
            pov_character="林远",
            setting="废弃图书馆",
            expected_word_count=2500,
        ),
        canon_context={},
        must_carry_forward=["林远必须进入图书馆"],
    )


@pytest.mark.asyncio
async def test_continuity_repair_step_returns_noop_when_no_issues(router, builder) -> None:
    from novel_forge.core.config import Settings

    step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
    result = await step.run(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n第二段。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=_build_plan(),
            continuity_report=ContinuityReport(continuity_score=9.5, issues=[]),
        )
    )

    assert result.applied is False
    assert result.repair_plan.no_op is True
    assert result.revised_text == "第一段。\n\n第二段。"


@pytest.mark.asyncio
async def test_continuity_repair_step_builds_targeted_plan(router, builder) -> None:
    from novel_forge.core.config import Settings

    step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="location_jump",
                severity="medium",
                summary="地点突变缺少动作交接。",
                rewrite_scope="opening",
            )
        ],
    )
    result = await step.run(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n第二段。\n\n第三段。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=_build_plan(),
            continuity_report=report,
        )
    )

    assert result.repair_plan.no_op is False
    assert result.repair_plan.target_sections
    assert result.repair_plan.target_sections[0].section_type == "opening"


def test_continuity_repair_plan_uses_configured_boundary_window() -> None:
    report = ContinuityReport(
        continuity_score=5.0,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="high",
                summary="开头没有接住上一章动作。",
                paragraph_start=1,
                paragraph_end=1,
                rewrite_scope="opening",
            )
        ],
    )
    payload = ContinuityRepairInput(
        chapter_number=2,
        chapter_text="第一段。\n\n第二段。\n\n第三段。\n\n第四段。",
        chapter_state_packet=_build_packet(),
        chapter_bridge=ChapterBridge(to_chapter=2),
        chapter_plan=_build_plan(),
        continuity_report=report,
        boundary_prev_tail_paragraphs=4,
        boundary_opening_paragraphs=2,
    )

    plan = ContinuityRepairStep._build_repair_plan(payload)

    issue = plan.issues[0]
    assert issue.paragraph_start == 1
    assert issue.paragraph_end == 2
    assert issue.repair_directive is not None
    assert issue.repair_directive.target_window == "本章开头第1-2段"
    assert "上一章结尾 4 段（只读）" in issue.repair_directive.required_context
    assert "本章开头 2 段（目标窗口）" in issue.repair_directive.required_context


def test_patch_continuity_context_projects_carry_forward_models_to_text() -> None:
    payload = ContinuityRepairInput(
        chapter_number=2,
        chapter_text="第一段。",
        chapter_state_packet=_build_packet(),
        chapter_bridge=ChapterBridge(to_chapter=2),
        chapter_plan=_build_plan(),
        continuity_report=ContinuityReport(continuity_score=5.0, issues=[]),
    )

    context = ContinuityRepairStep._build_patch_continuity_context(payload)

    assert context["must_carry_forward"] == ["林远必须进入图书馆"]
    assert all(isinstance(item, str) for item in context["must_carry_forward"])


@pytest.mark.asyncio
async def test_continuity_repair_step_routes_boundary_issues_to_patch_with_boundary_context(
    router,
    builder,
    monkeypatch,
) -> None:
    from novel_forge.core.config import Settings

    step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
    captured: dict[str, object] = {}

    async def _fake_patch_run(_self, payload):
        from novel_forge.pipeline.steps.patch_step import PatchResult

        captured["payload"] = payload
        return PatchResult(
            revised_text=payload.chapter_text + "\n\n边界补丁已应用",
            patches_applied=2,
            patches_attempted=2,
            fallback=False,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.patch_step.ChapterPatchStep.run", _fake_patch_run
    )

    report = ContinuityReport(
        continuity_score=5.8,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="high",
                summary="开头承接上一章余波不够自然。",
                rewrite_scope="chapter",
            ),
            ContinuityIssue(
                issue_type="closing_gap",
                severity="high",
                summary="结尾缺少可交接到下一章的动作出口。",
                rewrite_scope="chapter",
            ),
        ],
    )
    plan = _build_plan()
    packet = _build_packet().model_copy(
        update={"previous_chapter_ending": "她推门离开前，火把已在回廊尽头晃动。"}
    )
    result = await step.run(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text=(
                "第一段。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。\n\n第六段。\n\n第七段。"
            ),
            chapter_state_packet=packet,
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=plan,
            continuity_report=report,
            must_fix_issues=(report.issues[0],),
        )
    )

    assert result.applied is True
    assert result.patch_only is True
    payload = captured["payload"]
    assert payload.context_size == 5
    assert payload.must_fix_summaries == ["开头承接上一章余波不够自然。"]
    assert payload.boundary_context["focus"] == "开头+结尾"
    assert (
        payload.boundary_context["previous_chapter_ending"]
        == "她推门离开前，火把已在回廊尽头晃动。"
    )
    assert payload.boundary_context["opening_contract"] == plan.opening_contract
    assert payload.boundary_context["closing_contract"] == plan.closing_contract
    locations = [iss.location for iss in payload.issues]
    assert "第1-3段" in locations
    assert "结尾1-3段" in locations


@pytest.mark.asyncio
async def test_continuity_repair_force_patch_only_skips_fulltext_escalation(
    router,
    builder,
    monkeypatch,
) -> None:
    from novel_forge.core.config import Settings

    step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))

    async def _failed_patch_run(_self, payload):
        from novel_forge.pipeline.steps.patch_step import PatchResult

        return PatchResult(
            revised_text=payload.chapter_text,
            patches_applied=0,
            patches_attempted=1,
            fallback=False,
        )

    async def _unexpected_fulltext(*_args, **_kwargs):
        raise AssertionError("force_patch_only should not escalate to fulltext repair")

    monkeypatch.setattr(
        "novel_forge.pipeline.steps.patch_step.ChapterPatchStep.run", _failed_patch_run
    )
    monkeypatch.setattr(step, "_run_fulltext_repair", _unexpected_fulltext)

    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="redundancy",
                severity="medium",
                summary="重复句需要删除。",
                evidence="重复句",
                rewrite_scope="paragraph",
            )
        ],
    )

    result = await step.run(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n重复句。\n\n重复句。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=_build_plan(),
            continuity_report=report,
            memory_context={"force_patch_only": True},
        )
    )

    assert result.applied is False
    assert result.patch_only is True
    assert result.failure_reason == "force_patch_only_after_regression"
    assert result.revised_text == "第一段。\n\n重复句。\n\n重复句。"


def test_continuity_repair_plan_sanitizes_meta_fix_actions() -> None:
    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="medium",
                summary="开篇承接仍显得太说明。",
                evidence="原文直接解释承接关系。",
                fix_actions=[
                    "在开篇首段加入明确承接句，例如：‘延续上章末尾的视角与情绪，林晚仍保持戒备。’"
                ],
            )
        ],
    )
    repair_plan = ContinuityRepairStep._build_repair_plan(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n第二段。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=_build_plan(),
            continuity_report=report,
        )
    )

    actions = repair_plan.issues[0].fix_actions
    # After sanitization: the original LLM action is kept (example clause uses
    # single quotes not matched by _EXAMPLE_CLAUSE_RE), and a generic
    # "修复方法参考" entry is appended.
    assert len(actions) == 2
    assert "在开篇首段加入明确承接句" in actions[0]
    assert "【修复方法参考】" in actions[1]
    assert "承接上一章的图书馆交接点。" not in repair_plan.must_keep
    assert any("说明腔" in item for item in repair_plan.must_keep)


def test_continuity_repair_plan_synthesizes_opening_directive_for_old_reports() -> None:
    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="opening_gap",
                severity="medium",
                summary="开头缺少动作接力。",
                paragraph_start=1,
                paragraph_end=1,
            )
        ],
    )
    repair_plan = ContinuityRepairStep._build_repair_plan(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n第二段。\n\n第三段。\n\n第四段。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(
                to_chapter=2,
                action_handoff="林远握紧钥匙走向图书馆。",
            ),
            chapter_plan=_build_plan(),
            continuity_report=report,
        )
    )

    directive = repair_plan.issues[0].repair_directive
    assert directive is not None
    assert directive.repair_order == "late_if_conflict"
    assert repair_plan.issues[0].fix_mode == "window"
    assert repair_plan.issues[0].paragraph_start == 1
    assert repair_plan.issues[0].paragraph_end == 3
    assert any("动作接力" in item for item in directive.required_anchors)


def test_continuity_repair_plan_keeps_precise_bridge_contract_patch() -> None:
    report = ContinuityReport(
        continuity_score=6.0,
        issues=[
            ContinuityIssue(
                issue_type="bridge_contract_not_followed",
                severity="high",
                summary="第2段缺少从耳房到茶摊的移动过程。",
                paragraph_start=2,
                paragraph_end=2,
                rewrite_scope="paragraph",
                fix_mode="insert",
            )
        ],
    )
    repair_plan = ContinuityRepairStep._build_repair_plan(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n第二段。\n\n第三段。\n\n第四段。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=_build_plan(),
            continuity_report=report,
        )
    )

    issue = repair_plan.issues[0]
    assert issue.repair_directive is None
    assert issue.fix_mode == "insert"
    assert issue.paragraph_start == 2
    assert issue.paragraph_end == 2


def test_continuity_repair_extract_revised_text_unwraps_think_json() -> None:
    raw = '</think>\n\n{"revised_text":"修复后正文A"}'
    revised, truncated = ContinuityRepairStep._extract_revised_text(raw)
    assert truncated is False
    assert revised == "修复后正文A"


@pytest.mark.asyncio
async def test_continuity_repair_defers_bridge_artifact_issues(router, builder) -> None:
    from novel_forge.core.config import Settings

    step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
    report = ContinuityReport(
        continuity_score=5.5,
        issues=[
            ContinuityIssue(
                issue_type="bridge_contract_not_followed",
                severity="high",
                summary="Bridge 缺少 opening_pov，无法验证视角一致性。",
                repair_surface="bridge_artifact",
            )
        ],
    )

    result = await step.run(
        ContinuityRepairInput(
            chapter_number=2,
            chapter_text="第一段。\n\n第二段。",
            chapter_state_packet=_build_packet(),
            chapter_bridge=ChapterBridge(to_chapter=2),
            chapter_plan=_build_plan(),
            continuity_report=report,
        )
    )

    assert result.applied is False
    assert result.repair_plan.no_op is True
    assert result.deferred_issue_count == 1
    assert result.deferred_surfaces == ("bridge_artifact",)
    assert result.failure_reason == "non_text_continuity_issues_pending"


def test_bridge_artifact_repairer_updates_pov_switch_from_outline() -> None:
    packet = _build_packet().model_copy(
        update={
            "previous_exit_state": ChapterExitState(
                chapter_number=1,
                time_marker="深夜",
                pov="沈念卿",
                active_goals=["把怀表交给陆云峥"],
                open_questions=["陆云峥是否已经收到讯号"],
            )
        }
    )
    outline = ChapterOutline(
        chapter_number=2,
        title="怀表",
        goal="切换到陆云峥行动线",
        pov_character="陆云峥",
        setting="办公室",
        expected_word_count=2500,
    )
    bridge = ChapterBridge(
        from_chapter=0,
        to_chapter=2,
        opening_pov="沈念卿",
        opening_location="沈念卿公寓",
        transition_mode="action_handoff",
        bridge_summary="怀表成为切换到陆云峥行动线的触发物。",
    )
    issue = ContinuityIssue(
        issue_type="bridge_contract_not_followed",
        severity="high",
        summary="bridge.opening_pov 与本章大纲 POV 不一致，bridge.opening_location 仍指向旧地点。",
        repair_surface="bridge_artifact",
    )

    result = BridgeArtifactRepairer.repair(
        bridge=bridge,
        chapter_state_packet=packet,
        chapter_outline=outline,
        issues=[issue],
    )

    assert result.applied is True
    assert result.revised_bridge.from_chapter == 1
    assert result.revised_bridge.opening_pov == "陆云峥"
    assert result.revised_bridge.opening_location == "办公室"
    assert result.revised_bridge.opening_time == "深夜"
    assert result.revised_bridge.action_handoff == "怀表成为切换到陆云峥行动线的触发物。"
    assert result.revised_bridge.transition_mode == "pov_switch"
    assert result.revised_bridge.causal_link is not None
    assert result.revised_bridge.causal_link.unresolved_question == "陆云峥是否已经收到讯号"


def test_continuity_patch_adapter_preserves_repair_contract_fields(router, builder) -> None:
    from novel_forge.core.config import Settings

    step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
    issue = ContinuityIssue(
        issue_id="ch003-cont-anchor",
        issue_type="opening_gap",
        severity="high",
        repair_surface="chapter_text",
        summary="开头缺少上章动作接力。",
        missing_anchors=[
            ContinuityAnchor(
                source="chapter_bridge.action_handoff",
                text="陆云峥接住怀表余波。",
                role="action_handoff",
            )
        ],
        postconditions=[
            RepairPostcondition(
                validator_id="opening_transition_validator",
                description="开头落地动作接力。",
            )
        ],
        repair_directive=ContinuityRepairDirective(
            target_window="本章开头第1-3段",
            repair_strategy="窗口级改写开头。",
            recommended_rewrite="先写怀表余温，再落到新章行动。",
            conflict_policy="开场衔接最后统一修。",
            repair_order="late_if_conflict",
        ),
    )

    compat = step.adapt_issue_for_patch(issue)

    assert compat.issue_id == "ch003-cont-anchor"
    assert compat.repair_surface == "chapter_text"
    assert compat.missing_anchors[0].text == "陆云峥接住怀表余波。"
    assert compat.postconditions[0].validator_id == "opening_transition_validator"
    assert compat.repair_directive["repair_order"] == "late_if_conflict"
    assert "窗口级改写开头" in compat.fix_suggestion
