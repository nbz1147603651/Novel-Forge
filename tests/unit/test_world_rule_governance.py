"""Regression tests for the initialized world-rule ledger and stage projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.continuity import ChapterPlan, SceneIntent
from novel_forge.core.schemas.world_rules import (
    WorldRuleApplication,
    WorldRuleBook,
    WorldRuleComplianceReport,
    WorldRuleIssue,
)
from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
    build_world_rule_card,
    check_world_rule_compliance,
    coerce_world_rule_book,
    project_world_rule_card_for_stage,
    validate_world_rule_book,
    world_rule_report_to_findings,
)
from novel_forge.pipeline.long.services.constraints.world_rule_plan_contract import (
    validate_plan_world_rule_coverage,
)
from novel_forge.pipeline.long.services.context.source_artifacts import (
    _require_current_world_rule_book,
)
from novel_forge.pipeline.long.services.init.init_split_artifacts import _merge_story_fragment
from novel_forge.pipeline.long.stages.world_rule_repair import (
    patch_blocking_world_rule_conflicts,
)
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.schemas import StoryKernel, WorldRule


def _rule_book() -> WorldRuleBook:
    categories = [
        "time_space",
        "social_language",
        "information",
        "resource_material",
        "general",
        "time_space",
        "social_language",
        "information",
        "resource_material",
        "general",
    ]
    rules: list[dict[str, object]] = []
    for index, category in enumerate(categories, start=1):
        hard = index <= 4
        rule: dict[str, object] = {
            "rule_id": f"wr_{index:02d}",
            "content": f"世界规则{index}必须留下可观察证据。",
            "category": category,
            "severity": "hard" if hard else "soft",
            "applicability_tags": ["账房"] if index in {4, 5} else ["夜禁"],
        }
        if index <= 3:
            rule["always_on"] = True
        if hard:
            rule["forbidden_behavior"] = [f"违反规则{index}的便利行为"]
            rule["cost_or_consequence"] = [f"规则{index}会带来代价"]
        rules.append(rule)
    rules[-1]["content"] = "不应进入章节规则卡的海港税则。"
    rules[-1]["applicability_tags"] = ["海港"]
    return WorldRuleBook.model_validate({"version": "1", "rules": rules})


def _source_context(card: dict[str, object]) -> dict[str, object]:
    return {
        "stage_cards": {"source": {"world_rule_card": card, "chapter_contract": {}}},
        "chapter_number": 1,
        "chapter_text": "账房门外传来更鼓，玄昱按规矩核验封签。",
        "draft_text": "账房门外传来更鼓，玄昱按规矩核验封签。",
        "chapter_outline": {"title": "账房风声"},
        "target_word_count": 1200,
    }


def test_rule_book_requires_count_categories_and_ability_rule_when_needed() -> None:
    book = _rule_book()
    assert validate_world_rule_book(book) == []

    missing_ability = validate_world_rule_book(book, magic_or_tech="存在魔法体系")
    assert any("ability_tech" in issue for issue in missing_ability)

    incomplete = WorldRuleBook.model_validate({"rules": [{"content": "只剩一条"}]})
    issues = validate_world_rule_book(incomplete)
    assert any("10–14" in issue for issue in issues)
    assert any("始终生效" in issue for issue in issues)


def test_legacy_story_foundation_is_refused_until_full_reinitialization() -> None:
    with pytest.raises(ValueError, match="不支持自动迁移"):
        _require_current_world_rule_book({"rules": ["旧式字符串规则"]})


def test_stage_projection_keeps_always_on_rules_and_trims_conditional_rules() -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )

    draft = project_world_rule_card_for_stage(card, stage="draft")
    wave = project_world_rule_card_for_stage(card, stage="wave")
    plan = project_world_rule_card_for_stage(card, stage="plan")

    assert len(draft["always_on"]) == 3
    assert draft["relevant_rules"] == []
    assert len(wave["relevant_rules"]) <= 4
    assert len(plan["relevant_rules"]) <= 8
    assert "omitted_rule_ids" in plan
    assert "omitted_rule_ids" not in wave or wave["omitted_rule_ids"] == []


def test_plan_must_bind_each_selected_hard_rule_or_explain_inapplicability() -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )
    hard_rule_ids = [
        entry.rule.rule_id for entry in card.all_entries if entry.rule.severity == "hard"
    ]
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="s1",
                summary="核验封签",
                world_rule_ids=hard_rule_ids,
                world_rule_usage="角色按规则行动并承担后果。",
                world_rule_evidence_expectations=["正文出现核验动作和代价。"],
                world_rule_forbidden_boundaries=["不得跳过核验。"],
            )
        ]
    )
    assert validate_plan_world_rule_coverage(plan, card)

    applications = []
    for entry in card.all_entries:
        if entry.rule.severity != "hard":
            continue
        applications.append(
            WorldRuleApplication(
                rule_id=entry.rule.rule_id,
                scene_id="s1",
                usage="角色按规则行动并承担后果。",
                expected_evidence="正文出现核验动作和代价。",
                forbidden_boundary="不得跳过核验。",
            )
        )
    complete = plan.model_copy(update={"world_rule_applications": applications})
    assert validate_plan_world_rule_coverage(complete, card) == []
    projected = project_world_rule_card_for_stage(card, stage="plan")
    assert projected["view_stage"] == "plan"
    assert validate_plan_world_rule_coverage(complete, projected) == []


def test_plan_world_rule_application_must_reach_bound_scene_fields() -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )
    rule_id = next(
        entry.rule.rule_id for entry in card.all_entries if entry.rule.severity == "hard"
    )
    plan = ChapterPlan(
        scene_intents=[SceneIntent(scene_id="s1", summary="核验封签")],
        world_rule_applications=[
            WorldRuleApplication(
                rule_id=rule_id,
                scene_id="s1",
                usage="角色按规则行动。",
                expected_evidence="正文出现核验动作。",
                forbidden_boundary="不得跳过核验。",
            )
        ],
    )

    issues = validate_plan_world_rule_coverage(plan, card)

    assert any("未完整下沉到场景 s1" in issue for issue in issues)


def test_chapter_plan_preserves_legacy_world_rule_reason_alias() -> None:
    plan = ChapterPlan.model_validate(
        {
            "scene_intents": [{"scene_id": "s1", "summary": "核验封签"}],
            "world_rule_applications": [
                {
                    "rule_id": "wr_01",
                    "applicability": "not_applicable",
                    "reason": "本章没有进入夜禁区域。",
                }
            ],
        }
    )

    application = plan.world_rule_applications[0]
    assert application.not_applicable_reason == "本章没有进入夜禁区域。"
    assert "reason" not in application.model_dump(mode="json")


@pytest.mark.asyncio
async def test_world_rule_review_checks_only_plan_bound_rules_and_ignores_unknowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )
    bound_rule_id = card.relevant_rules[0].rule.rule_id
    always_on_ids = {entry.rule.rule_id for entry in card.always_on}
    seen_constraints: list[str] = []

    async def _fake_check(*args: Any, constraints: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        seen_constraints.extend(constraints or [])
        return {
            "compliance_results": [
                {"status": "unknown", "evidence": "无法确认"}
                for _ in list(constraints or [])
            ]
        }

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.check_guard_constraint_compliance",
        _fake_check,
    )
    report = await check_world_rule_compliance(
        runner=object(),
        chapter_text="玄昱进入账房。",
        chapter_number=1,
        world_rule_card=card,
        applications=[
            WorldRuleApplication(
                rule_id=bound_rule_id,
                scene_id="s1",
                usage="核验封签",
                expected_evidence="先验封签再行动",
            ),
            WorldRuleApplication(
                rule_id=card.relevant_rules[-1].rule.rule_id,
                applicability="not_applicable",
                not_applicable_reason="本章未进入对应场景。",
            ),
        ],
    )

    assert len(seen_constraints) == 1
    assert "已批准计划中的遵守方式：核验封签" in seen_constraints[0]
    assert "应视为合规的正文证据：先验封签再行动" in seen_constraints[0]
    assert set(report.checked_rule_ids) == {bound_rule_id}
    assert always_on_ids.issubset(set(report.skipped_rule_ids))
    assert card.relevant_rules[-1].rule.rule_id in report.skipped_rule_ids
    assert report.issues
    assert world_rule_report_to_findings(report) == []


@pytest.mark.asyncio
async def test_world_rule_review_respects_not_applicable_for_always_on_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )
    always_on_rule = card.always_on[0].rule
    seen_constraints: list[str] = []

    async def _fake_check(*args: Any, constraints: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        seen_constraints.extend(constraints or [])
        return {"compliance_results": []}

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.check_guard_constraint_compliance",
        _fake_check,
    )

    report = await check_world_rule_compliance(
        runner=object(),
        chapter_text="玄昱进入账房。",
        chapter_number=1,
        world_rule_card=card,
        applications=[
            WorldRuleApplication(
                rule_id=always_on_rule.rule_id,
                applicability="not_applicable",
                not_applicable_reason="本章未触发该规则的激活条件。",
            )
        ],
    )

    assert all(always_on_rule.content not in constraint for constraint in seen_constraints)
    assert always_on_rule.rule_id not in report.checked_rule_ids
    assert always_on_rule.rule_id in report.skipped_rule_ids


@pytest.mark.asyncio
async def test_blocking_world_rule_uses_evidence_anchored_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )
    rule = card.always_on[0].rule
    text = "玄昱绕过封签，直接推开了账房门。"
    captured: dict[str, Any] = {}

    async def _fake_patch_run(_self: Any, input_data: Any) -> Any:
        captured["issues"] = input_data.issues
        captured["must_fix"] = input_data.must_fix_summaries
        return type(
            "Result",
            (),
            {
                "revised_text": "玄昱先核验封签，再推开账房门。",
                "patches_attempted": 1,
                "patches_applied": 1,
                "fallback": False,
            },
        )()

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.world_rule_repair.ChapterPatchStep.run",
        _fake_patch_run,
    )
    result = await patch_blocking_world_rule_conflicts(
        router=object(),
        builder=object(),
        settings=object(),
        trace=None,
        chapter_number=1,
        current_text=text,
        card=card,
        applications=[
            WorldRuleApplication(
                rule_id=rule.rule_id,
                scene_id="s1",
                usage="先核验封签",
                expected_evidence="正文出现核验动作",
            )
        ],
        report=WorldRuleComplianceReport(
            chapter_number=1,
            issues=[
                WorldRuleIssue(
                    rule_id=rule.rule_id,
                    verdict="conflict",
                    severity="critical",
                    summary="未核验封签便进入账房。",
                    evidence="玄昱绕过封签，直接推开了账房门。",
                    root_cause="text",
                )
            ],
        ),
    )

    assert result.changed is True
    assert result.text == "玄昱先核验封签，再推开账房门。"
    assert len(captured["issues"]) == 1
    assert captured["issues"][0].evidence_quote == text
    assert any(
        "正文出现核验动作" in item
        for item in captured["issues"][0].repair_directive["required_context"]
    )


def test_extracted_world_fact_stays_pending_instead_of_mutating_world_rule() -> None:
    kernel = StoryKernel(
        project_id="p1",
        world_rules=[
            WorldRule(
                rule_id="wr_source",
                content="源头规则不可被章节提取覆盖。",
                category="general",
                severity="hard",
            )
        ],
    )
    result = StoryKernelMerger().merge_outcome(
        kernel,
        ChapterOutcome(source_chapter=1, new_world_facts={"new_fact": "账房有密道。"}),
    )

    assert [rule.rule_id for rule in result.world_rules] == ["wr_source"]
    assert result.pending_world_facts == {"chapter_1_new_fact": "账房有密道。"}


def test_rendered_stage_prompts_receive_projected_rule_card_not_raw_rule_book() -> None:
    card = build_world_rule_card(
        rule_book=_rule_book(),
        chapter_number=1,
        chapter_contract={"required_events": ["进入账房"]},
        chapter_outline={"setting": "夜禁后的账房"},
        relevant_entities=[],
    )
    builder = PromptBuilder()
    for task_type, stage in (
        (TaskType.BRIDGE_CHAPTER, "bridge"),
        (TaskType.PLAN_CHAPTER, "plan"),
        (TaskType.DRAFT_CHAPTER, "draft"),
        (TaskType.WAVE_CHAPTER, "wave"),
        (TaskType.POLISH_CHAPTER, "polish"),
    ):
        rendered = builder.render(
            task_type,
            _source_context(project_world_rule_card_for_stage(card, stage=stage)),
        )
        assert "世界规则1必须留下可观察证据。" in rendered
        assert "不应进入章节规则卡的海港税则。" not in rendered
        if task_type == TaskType.PLAN_CHAPTER:
            assert "not_applicable_reason" in rendered
            assert "不是 `reason`" in rendered

    continuity_context = _source_context(project_world_rule_card_for_stage(card, stage="review"))
    continuity_context.update(
        {
            "recheck_mode": False,
            "chapter_plan": {"scene_intents": []},
            "chapter_state_packet": {"chapter_number": 1},
            "chapter_bridge": {"bridge_summary": "承接上一章"},
            "previous_chapter_text": "上一章正文",
            "current_chapter_text": "本章正文",
        }
    )
    rendered = builder.render(TaskType.CHECK_CONTINUITY, continuity_context)
    assert "世界规则1必须留下可观察证据。" in rendered


# ---------------------------------------------------------------------------
# Regression: 青瓦梦匙 初始化崩溃 (2026-07-12)
#
# commit ec88814c 升级 init_story_world_rules.j2 要求生成结构化
# world_rule_book.rules（含 severity 字段），但 prompt 未声明 severity 的
# 合法枚举值（hard/soft）。LLM（MiniMax-M2.7）合理推断出三级体系 hard/medium/
# soft，导致 StoryBible.model_validate 在 world_rule_book.rules[N].severity 上
# 抛 literal_error，整个 init_long 失败。
# 以下测试固化期望：未知 severity 必须被兜底归一化，而不是让整份 StoryBible
# 解析崩溃。
# ---------------------------------------------------------------------------


def _llm_style_rule_book_with_medium_severity() -> dict[str, object]:
    """Mirror the actual MiniMax-M2.7 output that crashed 青瓦梦匙 initialization.

    The LLM emitted a mix of hard/medium/soft severity values because the prompt
    never stated that only ``hard``/``soft`` are legal. It also emitted a
    ``description`` summary field on ``world_rule_book`` that the strict schema
    rejected as an extra input.
    """
    rules: list[dict[str, object]] = []
    severities = [
        "hard",
        "hard",
        "hard",
        "hard",
        "hard",
        "medium",
        "hard",
        "medium",
        "medium",
        "medium",
        "soft",
        "hard",
    ]
    categories = [
        "ability_tech",
        "time_space",
        "ability_tech",
        "resource_material",
        "time_space",
        "social_language",
        "ability_tech",
        "ability_tech",
        "information",
        "time_space",
        "ability_tech",
        "information",
    ]
    always_on_flags = [
        True,
        True,
        True,
        True,
        True,
        False,
        True,
        False,
        False,
        False,
        False,
        True,
    ]
    for index, (severity, category, always_on) in enumerate(
        zip(severities, categories, always_on_flags, strict=False), start=1
    ):
        rule: dict[str, object] = {
            "rule_id": f"wr_{index:02d}",
            "content": f"世界规则{index}的规范化陈述。",
            "category": category,
            "severity": severity,
            "always_on": always_on,
            "applicability_tags": ["夜禁"] if not always_on else [],
            "trigger_conditions": [f"触发条件{index}"],
            "allowed_behavior": [f"允许行为{index}"],
            "forbidden_behavior": [f"禁止行为{index}"],
            "cost_or_consequence": [f"代价{index}"],
            "exceptions": [],
        }
        rules.append(rule)
    return {
        "version": "1",
        "description": "《青瓦梦匙》世界规则体系涵盖梦境神经机制与偏门职业伦理。",
        "rules": rules,
    }


def test_world_rule_spec_coerces_unauthorized_severity_medium_to_soft() -> None:
    """LLM-emitted ``severity='medium'`` must not crash rule parsing.

    The prompt historically did not publish the legal severity enum, so models
    invent a three-level scale. The schema must normalize unknown severity
    values to ``soft`` (the safer, non-blocking default) rather than reject
    the entire rule book.
    """
    raw = _llm_style_rule_book_with_medium_severity()
    book = WorldRuleBook.model_validate(raw)

    assert len(book.rules) == 12
    # medium entries must be coerced to soft, never crash.
    for rule in book.rules:
        assert rule.severity in {"hard", "soft"}, (
            f"rule {rule.rule_id} kept illegal severity={rule.severity!r}"
        )


def test_story_bible_validates_when_world_rules_fragment_uses_medium_severity() -> None:
    """End-to-end regression: the 青瓦梦匙 StoryBible merge must not crash.

    Reproduces the exact failure path: ``init_split_artifacts`` merges the
    world_rules fragment into a StoryBible payload and calls
    ``StoryBible.model_validate``. With unauthorized severity values this
    raised ``ValidationError: world_rule_book.rules.5.severity Input should
    be 'hard' or 'soft'``.
    """
    from novel_forge.core.schemas.bible import StoryBible

    fragment = _llm_style_rule_book_with_medium_severity()
    story_payload: dict[str, object] = {
        "title": "青瓦梦匙",
        "premise": "梦境神经校准密钥争夺战。",
        "era": "现代南方临海老城。",
        "geography": "骑楼街区与地下神经数据库。",
        "culture": "洗字人、阴邮差、梦侦探的偏门职业江湖。",
        "magic_or_tech": "神经同步技术与三层梦境结构。",
        "world_rule_book": fragment,
    }

    bible = StoryBible.model_validate(story_payload)

    assert bible.world_rule_book.rules
    for rule in bible.world_rule_book.rules:
        assert rule.severity in {"hard", "soft"}


def test_init_story_world_rules_prompt_declares_legal_severity_enum() -> None:
    """The prompt must tell the LLM which severity values are legal.

    Without this guidance, MiniMax-M2.7 (and other models) infer a
    ``hard/medium/soft`` scale and emit ``medium``, which the schema rejects.
    """
    from pathlib import Path

    from novel_forge.prompts.packs import get_prompt_pack

    pack = get_prompt_pack("zh")
    template_path = pack.templates_dir / "initialization" / "init_story_world_rules.j2"
    body = Path(template_path).read_text(encoding="utf-8")

    # The prompt must explicitly constrain severity to hard/soft.
    assert "severity" in body
    # There must be at least one line that lists hard AND soft as the legal
    # severity values (the enum declaration).
    severity_lines = [
        line
        for line in body.splitlines()
        if "severity" in line.lower() and "hard" in line.lower() and "soft" in line.lower()
    ]
    assert severity_lines, "prompt must contain a line declaring hard/soft as legal severity values"
    # If 'medium' appears in such a line, it must be in a forbidding context,
    # not presented as a legal option.
    forbid_markers = ("禁止", "不得", "不能", "forbidden", "must not", "do not")
    for line in severity_lines:
        if "medium" in line.lower():
            assert any(marker in line.lower() for marker in forbid_markers) or any(
                marker in line for marker in forbid_markers
            ), (
                f"prompt must forbid 'medium' as a severity option, not list it as "
                f"legal; got: {line!r}"
            )


def test_init_story_world_rules_prompt_declares_always_on_hard_rule_cap() -> None:
    """The prompt must state the always_on hard-rule cap to avoid init blocking.

    ``validate_world_rule_book`` rejects rule books with more than 4 always_on
    hard rules, and ``init_upstream_health`` marks this as a ``critical``
    issue which blocks initialization. The 青瓦梦匙 LLM output had 7 always_on
    hard rules because the prompt never stated the cap.
    """
    from pathlib import Path

    from novel_forge.prompts.packs import get_prompt_pack

    pack = get_prompt_pack("zh")
    template_path = pack.templates_dir / "initialization" / "init_story_world_rules.j2"
    body = Path(template_path).read_text(encoding="utf-8")

    # The prompt must mention the always_on hard-rule cap (4).
    assert "always_on" in body
    # Must contain a numeric cap guidance for always_on hard rules.
    cap_lines = [
        line
        for line in body.splitlines()
        if "always_on" in line.lower() and ("4" in line or "不超过" in line or "至多" in line)
    ]
    assert cap_lines, (
        "prompt must state the always_on hard-rule cap (4) to prevent governance rejection"
    )


def test_coerce_world_rule_book_downgrades_excess_always_on_hard_rules() -> None:
    """Excess always_on hard rules must be auto-downgraded, not block init.

    The 青瓦梦匙 LLM emitted 7 always_on hard rules, but the governance cap is
    4. Rather than blocking initialization with a critical issue, the coercion
    layer should keep all rules but flip the excess ones to ``always_on=false``
    (adding an ``applicability_tags`` entry so they remain selectable). This
    preserves the rule content while respecting the chapter-model attention
    budget.
    """
    from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
        coerce_world_rule_book,
        validate_world_rule_book,
    )

    rules: list[dict[str, object]] = []
    for index in range(1, 13):
        rules.append(
            {
                "rule_id": f"wr_{index:02d}",
                "content": f"规则{index}的陈述。",
                "category": [
                    "ability_tech",
                    "time_space",
                    "information",
                    "resource_material",
                    "social_language",
                    "general",
                ][index % 6],
                "severity": "hard" if index <= 8 else "soft",
                "always_on": index <= 7,  # 7 always_on hard rules -- exceeds cap of 4
                "applicability_tags": [f"场景{index}"] if index > 7 else [],
                "forbidden_behavior": [f"禁止{index}"],
                "cost_or_consequence": [f"代价{index}"],
            }
        )
    raw_book = {"version": "1", "rules": rules}

    book = coerce_world_rule_book(raw_book)
    always_on_hard = [r for r in book.rules if r.always_on and r.severity == "hard"]
    assert len(always_on_hard) <= 4, (
        f"coerce should cap always_on hard rules at 4, got {len(always_on_hard)}"
    )
    # All 8 hard rules must still be present (none dropped).
    assert len(book.hard_rules) == 8
    # Downgraded rules must have an applicability_tags entry so they stay selectable.
    downgraded = [r for r in book.rules if r.severity == "hard" and not r.always_on]
    for rule in downgraded:
        assert rule.applicability_tags, (
            f"downgraded rule {rule.rule_id} must retain applicability_tags"
        )
    # The governance validation must no longer flag the always_on cap.
    issues = validate_world_rule_book(book)
    assert not any("始终生效" in i and "超过" in i for i in issues), (
        f"coerced book must not trigger the always_on cap issue: {issues}"
    )


# ---------------------------------------------------------------------------
# Parameterized governance: settings-driven thresholds + non-blocking behavior
# ---------------------------------------------------------------------------


class _FakeSettings:
    """Minimal settings stub for governance parameterization tests."""

    def __init__(self, **kwargs: object) -> None:
        defaults = {
            "world_rule_count_min": 10,
            "world_rule_count_max": 14,
            "world_rule_hard_min": 3,
            "world_rule_always_on_hard_cap": 4,
            "world_rule_category_min": 4,
            "world_rule_ability_required": True,
            "world_rule_block_on_violation": False,
        }
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


def test_validate_world_rule_book_uses_custom_settings_thresholds() -> None:
    """Governance thresholds must come from settings, not hardcoded constants."""
    # Custom: require 12-16 rules, 5 hard min, cap 3 always_on, 5 categories
    settings = _FakeSettings(
        world_rule_count_min=12,
        world_rule_count_max=16,
        world_rule_hard_min=5,
        world_rule_always_on_hard_cap=3,
        world_rule_category_min=5,
    )
    # 10 rules -- below custom min of 12 but above default min of 10
    rules = [
        {
            "rule_id": f"wr_{i:02d}",
            "content": f"规则{i}",
            "category": ["time_space", "social_language", "information", "resource_material"][
                i % 4
            ],
            "severity": "hard",
            "always_on": i < 3,
            "applicability_tags": [],
            "forbidden_behavior": [f"禁止{i}"],
            "cost_or_consequence": [f"代价{i}"],
        }
        for i in range(10)
    ]
    book = WorldRuleBook.model_validate({"rules": rules})
    issues = validate_world_rule_book(book, settings=settings)
    # With custom min=12, 10 rules should trigger the count issue
    assert any("12" in i and "16" in i for i in issues), f"custom count range not applied: {issues}"
    # With custom hard_min=5, only 10 hard rules is fine (>=5)
    assert not any("至少需要 5" in i for i in issues)
    # With custom cap=3, 3 always_on is OK (== cap, not > cap)
    assert not any("不得超过 3" in i for i in issues)
    # With custom category_min=5, only 4 categories should trigger
    assert any("至少需要覆盖 5" in i for i in issues), f"custom category_min not applied: {issues}"


def test_coerce_uses_custom_always_on_cap_from_settings() -> None:
    """coerce_world_rule_book must respect the settings always_on cap."""
    settings = _FakeSettings(world_rule_always_on_hard_cap=2)
    rules = [
        {
            "rule_id": f"wr_{i:02d}",
            "content": f"规则{i}",
            "category": "ability_tech",
            "severity": "hard",
            "always_on": True,
            "forbidden_behavior": [f"禁止{i}"],
        }
        for i in range(6)
    ]
    book = coerce_world_rule_book({"rules": rules}, settings=settings)
    always_on = sum(1 for r in book.rules if r.always_on and r.severity == "hard")
    assert always_on == 2, f"custom cap=2 not applied: {always_on}"


def test_governance_issues_default_to_non_blocking_medium_severity() -> None:
    """World rule governance violations must be 'medium' (warning), not 'critical' (blocking).

    The 青瓦梦匙 init crashed because governance issues were marked critical,
    which blocked initialization. The default behavior is now 'medium' (warning),
    only escalating to 'critical' when world_rule_block_on_violation=True.
    The init_upstream_health blocking threshold is rank >= 3 (high/critical);
    'medium' (rank 2) does not block.
    """
    from novel_forge.pipeline.long.services.init.init_upstream_health import _severity_rank

    # Default: governance issues are 'medium' (non-blocking)
    settings = _FakeSettings(world_rule_block_on_violation=False)
    block_on_violation = settings.world_rule_block_on_violation
    severity = "critical" if block_on_violation else "medium"
    assert _severity_rank(severity) < 3, (
        f"default governance severity '{severity}' must be non-blocking (rank < 3), "
        f"got rank {_severity_rank(severity)}"
    )

    # When block_on_violation is True, severity escalates to critical (blocking)
    settings_block = _FakeSettings(world_rule_block_on_violation=True)
    severity_block = "critical" if settings_block.world_rule_block_on_violation else "medium"
    assert _severity_rank(severity_block) >= 3, (
        "block_on_violation=True must escalate to blocking severity (rank >= 3)"
    )


def test_governance_config_from_settings_handles_none() -> None:
    """from_settings(None) must return defaults (backward compat)."""
    from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
        WorldRuleGovernanceConfig,
    )

    cfg = WorldRuleGovernanceConfig.from_settings(None)
    assert cfg.rule_count_min == 10
    assert cfg.rule_count_max == 14
    assert cfg.hard_rule_min == 3
    assert cfg.always_on_hard_cap == 4
    assert cfg.category_min == 4
    assert cfg.ability_required is True


def test_prompt_template_uses_dynamic_governance_values() -> None:
    """The init prompt must reflect the configured governance thresholds."""
    from pathlib import Path

    from novel_forge.prompts.builder import PromptBuilder
    from novel_forge.prompts.packs import get_prompt_pack

    # Verify template has dynamic placeholders (not hardcoded)
    pack = get_prompt_pack("zh")
    body = Path(pack.templates_dir / "initialization" / "init_story_world_rules.j2").read_text(
        encoding="utf-8"
    )
    assert "wrg" in body, "template must use the wrg (world_rule_governance) variable"

    # Verify rendering with custom values
    builder = PromptBuilder()
    rendered = builder.render(
        TaskType.INIT_STORY_WORLD_RULES,
        {
            "premise": "测试。",
            "genre": "mystery",
            "tone": "dark",
            "world_hint": "wh",
            "conflict_hint": "ch",
            "story_core": {"title": "t", "premise": "p", "tone": "tn"},
            "research_context": {},
            "shared_evidence_anchor": {},
            "world_rule_governance": {
                "rule_count_min": 8,
                "rule_count_max": 12,
                "always_on_hard_cap": 2,
                "category_min": 3,
            },
        },
    )
    assert "8-12" in rendered, "custom rule count range not rendered"
    assert "不超过 **2**" in rendered or "不超过 2" in rendered, "custom cap not rendered"
    assert "至少覆盖 **3**" in rendered or "至少覆盖 3" in rendered, "custom category not rendered"

    # Verify rendering without governance context (defaults)
    rendered_default = builder.render(
        TaskType.INIT_STORY_WORLD_RULES,
        {
            "premise": "测试。",
            "genre": "mystery",
            "tone": "dark",
            "world_hint": "wh",
            "conflict_hint": "ch",
            "story_core": {"title": "t", "premise": "p", "tone": "tn"},
            "research_context": {},
            "shared_evidence_anchor": {},
        },
    )
    assert "10-14" in rendered_default, "default rule count range not rendered"
    assert "不超过 **4**" in rendered_default or "不超过 4" in rendered_default, (
        "default cap not rendered"
    )


# ---------------------------------------------------------------------------
# Regression: LLM-emitted flattened world_rule_book fields must not crash
# StoryBible validation. Mirrors the 青瓦梦匙 project failure where rule fields
# were distributed across three consecutive nesting layers.
# ---------------------------------------------------------------------------


def _three_layer_malformed_world_rule_book() -> dict[str, Any]:
    """Reproduce the 青瓦梦匙 checkpoint structure.

    - WB001: most complete fields, nested inside ``world_rule_book.world_rule_book``
    - WB002: flattened onto the ``world_rule_book`` container top level
    - WB003: flattened onto the outer ``world_rules`` fragment (caller injects it)
    - ``rules``: legacy string array whose first entry duplicates WB002 content
    """
    return {
        "version": "1",
        "rules": [
            "WB002 完整内容",  # duplicates the flattened WB002 below
            "其他独立规则摘要",
        ],
        "world_rule_book": {
            "rule_id": "WB001",
            "content": "WB001 完整内容",
            "category": "ability_tech",
            "severity": "hard",
            "always_on": True,
            "applicability_tags": [],
            "trigger_conditions": ["任何涉及多层梦境同步的操作"],
            "allowed_behavior": ["使用怀表逐层校准"],
            "forbidden_behavior": ["擅自跳层"],
            "cost_or_consequence": ["意识撕裂"],
            "exceptions": [],
        },
        "rule_id": "WB002",
        "content": "WB002 完整内容",
        "category": "social_language",
        "severity": "hard",
        "always_on": False,
        "applicability_tags": ["夜禁"],
    }


def test_world_rule_book_extracts_rules_from_three_nested_layers() -> None:
    """WB001 (inner), WB002 (flattened on book), and string rules must all survive."""
    malformed = _three_layer_malformed_world_rule_book()
    book = WorldRuleBook.model_validate(malformed)

    contents = {rule.content for rule in book.rules}
    assert "WB001 完整内容" in contents, "inner-layer WB001 rule was lost"
    assert "WB002 完整内容" in contents, "flattened WB002 rule was lost"
    assert "其他独立规则摘要" in contents, "legacy string rule was lost"

    # WB001 should retain its structured detail from the nested layer.
    wb001 = next(rule for rule in book.rules if rule.content == "WB001 完整内容")
    assert wb001.rule_id == "WB001"
    assert wb001.category == "ability_tech"
    assert wb001.severity == "hard"
    assert wb001.always_on is True
    assert wb001.forbidden_behavior == ["擅自跳层"]


def test_world_rule_book_deduplicates_by_content_preferring_full_objects() -> None:
    """A legacy string summary and a recovered object with the same content must
    collapse to one rule, keeping the structured object (more spec fields)."""
    malformed = _three_layer_malformed_world_rule_book()
    book = WorldRuleBook.model_validate(malformed)

    wb002_rules = [rule for rule in book.rules if rule.content == "WB002 完整内容"]
    assert len(wb002_rules) == 1, "duplicate content was not deduplicated"
    # The recovered object carries category/severity/applicability_tags; a bare
    # string summary would have defaulted those. Verify the object survived.
    wb002 = wb002_rules[0]
    assert wb002.category == "social_language"
    assert wb002.severity == "hard"
    assert wb002.applicability_tags == ["夜禁"]


def test_story_bible_tolerates_malformed_world_rules_fragment() -> None:
    """End-to-end: the real崩溃 path is build_story_bible_split -> _merge_story_fragment
    -> StoryBible.model_validate. The outer ``world_rules`` fragment may also
    carry a flattened rule (WB003), which StoryBible must absorb without crashing."""
    # Simulate the raw LLM fragment: WB003 flattened on the outer world_rules dict.
    fragment = {
        "era": "架空近代都市",
        "geography": "雨城旧区",
        "culture": "记忆交易行会",
        "magic_or_tech": "神经同步梦境技术",
        "rules": [],
        "world_rule_book": _three_layer_malformed_world_rule_book(),
        # WB003 flattened on the outer fragment level
        "rule_id": "WB003",
        "content": "WB003 外层平铺规则",
        "category": "information",
        "severity": "soft",
    }

    payload: dict[str, Any] = {}
    _merge_story_fragment(
        payload,
        fragment,
        fragment_name="world",
        allowed_keys={
            "era",
            "geography",
            "culture",
            "magic_or_tech",
            "rules",
            "world_rule_book",
        },
    )
    # StoryBible requires ``premise``; in the real flow the core fragment
    # supplies it. Inject a minimal premise so we isolate the world_rule_book
    # tolerance check from the unrelated required-field check.
    payload.setdefault("premise", "测试前提。")

    # This call used to raise 12 extra_forbidden errors.
    bible = StoryBible.model_validate(payload)
    assert bible.world_rule_book.rules, "no rules recovered from malformed fragment"

    contents = {rule.content for rule in bible.world_rule_book.rules}
    assert "WB001 完整内容" in contents
    assert "WB002 完整内容" in contents
    # WB003 was flattened on the world_rules fragment, not inside world_rule_book,
    # so it lands in StoryBible.notes via _preserve_unknown_fields_in_notes rather
    # than the rule book. The key assertion is that validation did not crash.


def test_world_rule_book_prompt_constraints_present_in_zh_and_en() -> None:
    """Both stable prompt packs must state the anti-flattening constraints."""
    zh_path = (
        Path(__file__).resolve().parents[2]
        / "novel_forge"
        / "prompts"
        / "packs"
        / "zh"
        / "templates"
        / "initialization"
        / "init_story_world_rules.j2"
    )
    en_path = (
        Path(__file__).resolve().parents[2]
        / "novel_forge"
        / "prompts"
        / "packs"
        / "en"
        / "templates"
        / "initialization"
        / "init_story_world_rules.j2"
    )

    zh_text = zh_path.read_text(encoding="utf-8")
    assert "对象数组，不是字符串数组" in zh_text
    assert "不得平铺在 `world_rule_book` 或 `world_rules` 顶层" in zh_text

    en_text = en_path.read_text(encoding="utf-8")
    assert "array of objects, not an array of strings" in en_text
    assert "do not flatten them onto the `world_rule_book` or `world_rules` top level" in en_text
