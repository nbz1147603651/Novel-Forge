"""Regression tests for chapter-flow final-pass orchestration."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_forge.core.exceptions import ConsistencyViolationError, ModelGatewayError, RecoveryTarget
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.world_rules import WorldRuleComplianceReport, WorldRuleIssue
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.pipeline.long import chapter_flow, chapter_flow_review
from novel_forge.pipeline.long.execution_models import (
    ChapterReviewArtifacts,
    PreparedChapterArtifacts,
)
from novel_forge.pipeline.long.stages.finalize_report import _bounded_long_eval_context


def test_review_resume_plan_quality_done_continues_downstream_repairs() -> None:
    plan = chapter_flow._review_resume_plan("quality_done")  # noqa: SLF001

    assert plan.skip_draft is True
    assert plan.skip_quality is True
    assert plan.skip_repair is False
    assert plan.skip_extract is False
    assert plan.run_text_refinement is True


def test_review_resume_plan_repair_done_still_runs_text_refinement() -> None:
    plan = chapter_flow._review_resume_plan("repair_done")  # noqa: SLF001

    assert plan.skip_draft is True
    assert plan.skip_quality is True
    assert plan.skip_repair is True
    assert plan.skip_extract is False
    assert plan.run_text_refinement is True


def test_review_resume_plan_canon_done_skips_finished_review_body() -> None:
    plan = chapter_flow._review_resume_plan("canon_done")  # noqa: SLF001

    assert plan.skip_draft is True
    assert plan.skip_quality is True
    assert plan.skip_repair is True
    assert plan.skip_extract is True
    assert plan.run_text_refinement is False


@pytest.mark.parametrize("stage", ["refinement_done", "final_verify_done"])
def test_review_resume_plan_finalization_stages_skip_semantic_review(stage: str) -> None:
    plan = chapter_flow._review_resume_plan(stage)  # noqa: SLF001

    assert plan.skip_draft is True
    assert plan.skip_quality is True
    assert plan.skip_repair is True
    assert plan.skip_extract is True
    assert plan.run_text_refinement is False


@pytest.mark.asyncio
async def test_generate_and_wave_promotes_wave_warnings_to_review_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_generate_chapter_prose(*_args, **_kwargs):
        return chapter_flow.GenerateArtifacts(
            current_text="woven text",
            performed_edits=1,
            wave_meta={"warnings": ["wave_post_cond_cross_ref: hit 0/2"], "cross_ref_hits": []},
        )

    async def _fake_opening_guard_patch(*_args, **_kwargs):
        return "woven text"

    monkeypatch.setattr(chapter_flow, "generate_chapter_prose", _fake_generate_chapter_prose)
    monkeypatch.setattr(chapter_flow, "run_opening_guard_patch", _fake_opening_guard_patch)
    monkeypatch.setattr(
        "novel_forge.core.schemas.review_state.save_review_progress", lambda **_kwargs: None
    )

    prepared = SimpleNamespace(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(chapter_number=1),
            layout=SimpleNamespace(),
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        memory_hints={},
        window_manager=None,
        reading_power_hint=None,
    )

    state = await chapter_flow._generate_and_wave(  # noqa: SLF001
        runner=SimpleNamespace(),
        context=SimpleNamespace(storage=object()),
        prepared=prepared,
        trace=SimpleNamespace(),
        chapter_number=1,
        resume_progress=None,
        skip_draft=False,
        resume_stage=None,
        on_step=lambda *_args, **_kwargs: None,
    )

    assert "wave_post_cond_cross_ref: hit 0/2" in state.review_warnings


class _ContextStub:
    def __init__(self) -> None:
        self.storage = object()
        self.router = object()
        self.builder = object()
        self.merger = object()
        self.rules = object()
        self.config = SimpleNamespace(alignment_threshold=7.0, causal_threshold=5.0)
        self.settings = SimpleNamespace(
            long_causal_repair_enabled=True,
            max_revelations_per_chapter=2,
        )
        self.select_character_profiles = lambda *args, **kwargs: []
        self.compact_previous_creative_report = lambda report: report
        self.remove_opening_echo_from_previous = lambda text, prev: (text, None)
        self.apply_chapter_compaction = lambda **kwargs: (None, None)
        self.finalize_volume_if_needed = lambda **kwargs: None
        self.is_outline_option_enabled_for_task = lambda *args, **kwargs: False

        async def _compress(*args, **kwargs):  # pragma: no cover - defensive default
            return {}

        self.compress_prompt_context = _compress
        self.events: list[tuple[str, object]] = []

    def on_step(self, step: str, data: object) -> None:
        self.events.append((step, data))

    def has_audit_coordinator(self) -> bool:
        return False


class _TicketStub:
    ticket_id = "ticket_1"
    finding_ids: list[str] = []
    chapter_number = 1
    dimension = "chapter_quality"
    issue_type = "wave_integrity"
    severity = "high"
    target_summary = "需要修复章节质量问题"
    repair_goal = "保持文本稳定"
    repair_mode = "local"
    acceptance_criteria: list[str] = []
    postconditions: list[str] = []
    must_preserve: list[str] = []
    forbidden_changes: list[str] = []
    target_paragraph_start = 0
    target_paragraph_end = 0
    metadata: dict[str, object] = {}

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "ticket_id": self.ticket_id,
            "finding_ids": self.finding_ids,
            "chapter_number": self.chapter_number,
            "dimension": self.dimension,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "target_summary": self.target_summary,
            "repair_goal": self.repair_goal,
            "repair_mode": self.repair_mode,
            "acceptance_criteria": self.acceptance_criteria,
            "postconditions": self.postconditions,
            "must_preserve": self.must_preserve,
            "forbidden_changes": self.forbidden_changes,
            "target_paragraph_start": self.target_paragraph_start,
            "target_paragraph_end": self.target_paragraph_end,
            "metadata": self.metadata,
        }


@pytest.mark.asyncio
async def test_mechanical_cleanup_stage_merges_parallel_results_and_emits_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    self_repetition_inputs: list[str] = []
    pronoun_inputs: list[str] = []
    events: list[tuple[str, object]] = []

    class _FlowFacade:
        def run_final_dedup(self, _runner: object, text: str, previous: str) -> str:
            assert previous == "上一章"
            return text.replace("初稿", "去重稿")

        def run_self_repetition_check(self, _runner: object, text: str) -> tuple[str, object]:
            self_repetition_inputs.append(text)
            if text == "去重稿":
                return "自重清理稿", {"round": 1}
            if text == "代词修订稿":
                return "代词后再清理稿", {"round": 2}
            return text, {"round": 0}

        async def run_pronoun_check(
            self,
            _runner: object,
            _bundle: object,
            _packet: object,
            current_text: str,
            _chapter_number: int,
            _trace: object,
            *,
            chapter_plan: object | None = None,
        ) -> tuple[str, object]:
            assert chapter_plan is not None
            pronoun_inputs.append(current_text)
            return "代词修订稿", {"requires_rewrite": True}

    def _fake_pronoun_mechanical(
        text: str,
        char_map: dict[str, dict[str, object]],
        pov: str,
    ) -> tuple[str, int, int]:
        assert char_map == {"小芳": {"gender": "女"}}
        assert pov == "小芳"
        return f"{text} 她", 1, 0

    def _fake_non_cjk(text: str) -> tuple[str, list[str]]:
        assert text.endswith("她")
        return text.replace("她", ""), ["ASCII"]

    import novel_forge.core.domain.guardrails as _guardrails

    facade = _FlowFacade()
    monkeypatch.setattr(chapter_flow_review, "run_final_dedup", facade.run_final_dedup)
    monkeypatch.setattr(
        chapter_flow_review,
        "run_self_repetition_check",
        facade.run_self_repetition_check,
    )
    monkeypatch.setattr(chapter_flow_review, "run_pronoun_check", facade.run_pronoun_check)
    monkeypatch.setattr(_guardrails, "fix_pronouns_mechanical", _fake_pronoun_mechanical)
    monkeypatch.setattr(_guardrails, "fix_non_cjk_leakage", _fake_non_cjk)

    state = chapter_flow_review._ReviewPhaseState(  # noqa: SLF001
        current_text="初稿",
        baseline_text="初稿",
        wave_meta={},
        alignment_report=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        chapter_repair_report=None,
    )
    prepared = SimpleNamespace(
        packet=SimpleNamespace(
            previous_chapter_ending="上一章",
            canon_context={"characters": {"小芳": {"gender": "女"}}},
        ),
        plan=SimpleNamespace(marker=True),
    )
    bundle = SimpleNamespace(chapter_outline=SimpleNamespace(pov_character="小芳"))
    runner = SimpleNamespace(_settings=SimpleNamespace(pronoun_autofix_mode="pov_or_many"))

    result = await chapter_flow_review._run_mechanical_cleanup_stage(  # noqa: SLF001
        runner=runner,
        bundle=bundle,
        prepared=prepared,
        state=state,
        chapter_number=1,
        trace=SimpleNamespace(),
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert pronoun_inputs == ["去重稿"]
    assert self_repetition_inputs == ["去重稿", "代词修订稿"]
    assert result.current_text == "代词后再清理稿 "
    assert result.repetition_report == {"round": 2}
    assert result.pronoun_report == {"requires_rewrite": True}
    assert [entry["stage"] for entry in state.text_change_history] == [
        "final_dedup",
        "self_repetition_dedup",
        "pronoun_check_rewrite",
        "mechanical_pronoun_fix",
        "non_cjk_cleanup",
    ]
    assert [step for step, _payload in events] == [
        "mechanical_pronoun_fix",
        "non_cjk_cleanup",
    ]


@pytest.mark.parametrize(
    ("revised_text", "expected_quality_calls", "expected_skip_reason"),
    [
        ("原正文", 0, "no_text_change"),
        ("修后正文", 1, None),
    ],
)
@pytest.mark.asyncio
async def test_chapter_quality_repair_skips_quality_recheck_only_when_text_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    revised_text: str,
    expected_quality_calls: int,
    expected_skip_reason: str | None,
) -> None:
    quality_calls: list[str] = []

    class _FakeEditStep:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, input_data: object) -> object:
            assert input_data.draft_text == "原正文"
            return SimpleNamespace(revised_text=revised_text, edit_notes=["checked"])

    def _wave_result() -> object:
        return SimpleNamespace(
            blocking=False,
            archive_blocking=False,
            policy="repair",
            metrics={},
            issues=[],
            model_dump=lambda: {
                "blocking": False,
                "archive_blocking": False,
                "issues": [],
            },
        )

    async def _fake_run_quality_checks(
        *args: object, **_kwargs: object
    ) -> tuple[object, object, object]:
        quality_calls.append(str(args[5]))
        return (
            SimpleNamespace(alignment_score=9.0, repair_actions=[]),
            SimpleNamespace(continuity_score=9.0, issues=[]),
            SimpleNamespace(repair_tickets=[], review_findings=[]),
        )

    monkeypatch.setattr(chapter_flow_review, "EditStep", _FakeEditStep)
    monkeypatch.setattr(
        chapter_flow_review, "assess_wave_integrity", lambda *_a, **_kw: _wave_result()
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "assess_wave_integrity_against_plan",
        lambda *_a, **_kw: _wave_result(),
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "merge_opening_guard_pending_issues",
        lambda _runner, report, **_kwargs: report,
    )
    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_run_quality_checks)

    state = chapter_flow_review._ReviewPhaseState(  # noqa: SLF001
        current_text="原正文",
        baseline_text="原正文",
        wave_meta={"warnings": []},
        alignment_report=SimpleNamespace(alignment_score=9.0, repair_actions=[]),
        continuity_report=SimpleNamespace(continuity_score=9.0, issues=[]),
        chapter_repair_report=SimpleNamespace(
            repair_tickets=[_TicketStub()],
            review_findings=[],
        ),
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(
            long_wave_post_condition_policy="repair",
            long_wave_word_count_policy="inherit",
            long_eval_repair_enabled=False,
            long_word_count_archive_gate_enabled=False,
        ),
        config=SimpleNamespace(),
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=context.settings,
    )
    prepared = SimpleNamespace(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(expected_word_count=1000),
            chapter_source_slice=None,
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        window_manager=None,
        window_config=None,
        memory_hints={},
        reading_power_hint=None,
    )

    result = await chapter_flow_review._run_chapter_quality_repair_lane(  # noqa: SLF001
        runner=runner,
        context=context,
        prepared=prepared,
        trace=SimpleNamespace(),
        chapter_number=1,
        state=state,
        skip_quality=False,
        on_step=lambda *_args: None,
    )

    assert len(quality_calls) == expected_quality_calls
    assert result.current_text == revised_text
    assert result.chapter_quality_repair.get("quality_recheck_skipped_reason") == (
        expected_skip_reason
    )


@pytest.mark.parametrize(
    (
        "patch_changed",
        "unanchored_conflict",
        "recheck_failure",
        "expected_text",
        "expected_quality_calls",
        "expected_rollback",
    ),
    [
        (True, False, False, "世界规则定向修复正文", 2, False),
        (False, False, False, "原正文", 1, True),
        (True, True, False, "原正文", 1, True),
        (True, False, True, "原正文", 1, True),
    ],
)
@pytest.mark.asyncio
async def test_chapter_quality_repair_repairs_or_rolls_back_world_rule_regression(
    monkeypatch: pytest.MonkeyPatch,
    patch_changed: bool,
    unanchored_conflict: bool,
    recheck_failure: bool,
    expected_text: str,
    expected_quality_calls: int,
    expected_rollback: bool,
) -> None:
    quality_calls: list[str] = []
    events: list[str] = []

    class _FakeEditStep:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, _input_data: object) -> object:
            return SimpleNamespace(revised_text="通用质量修复正文", edit_notes=["checked"])

    def _wave_result() -> object:
        return SimpleNamespace(
            blocking=False,
            archive_blocking=False,
            policy="repair",
            metrics={},
            issues=[],
            model_dump=lambda: {
                "blocking": False,
                "archive_blocking": False,
                "issues": [],
            },
        )

    async def _fake_quality_checks(
        *args: object, **_kwargs: object
    ) -> tuple[object, object, object]:
        quality_calls.append(str(args[5]))
        return (
            SimpleNamespace(alignment_score=9.0, repair_actions=[]),
            SimpleNamespace(continuity_score=9.0, issues=[]),
            SimpleNamespace(repair_tickets=[], review_findings=[]),
        )

    async def _fake_world_rule_check(
        *, chapter_text: str, chapter_number: int, **kwargs: object
    ) -> WorldRuleComplianceReport:
        if chapter_text == "原正文":
            return WorldRuleComplianceReport(chapter_number=chapter_number)
        if chapter_text == "世界规则定向修复正文":
            if recheck_failure:
                raise RuntimeError("world-rule recheck unavailable")
            if not unanchored_conflict:
                return WorldRuleComplianceReport(chapter_number=chapter_number)
            assert kwargs.get("rule_ids") == {"wr_01", "wr_02"}
            return WorldRuleComplianceReport(
                chapter_number=chapter_number,
                issues=[
                    WorldRuleIssue(
                        rule_id="wr_02",
                        verdict="conflict",
                        severity="high",
                        summary="无精确锚点的阻断规则仍未解决。",
                        evidence="必须保留复检的规则证据",
                    )
                ],
            )
        issues = [
            WorldRuleIssue(
                rule_id="wr_01",
                verdict="conflict",
                severity="critical",
                summary="通用质量修复改坏了世界规则。",
                evidence="通用质量修复正文",
            )
        ]
        if unanchored_conflict:
            issues.append(
                WorldRuleIssue(
                    rule_id="wr_02",
                    verdict="conflict",
                    severity="high",
                    summary="还有一条规则没有精确文本锚点。",
                    evidence="不在正文中的证据",
                )
            )
        return WorldRuleComplianceReport(
            chapter_number=chapter_number,
            issues=issues,
        )

    async def _fake_world_rule_patch(**_kwargs: object) -> object:
        return SimpleNamespace(
            text="世界规则定向修复正文",
            attempted_rule_ids=("wr_01",),
            patches_attempted=1,
            patches_applied=1 if patch_changed else 0,
            fallback=False,
            skip_reason="" if patch_changed else "no_safe_patch",
            changed=patch_changed,
        )

    monkeypatch.setattr(chapter_flow_review, "EditStep", _FakeEditStep)
    monkeypatch.setattr(
        chapter_flow_review, "assess_wave_integrity", lambda *_a, **_kw: _wave_result()
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "assess_wave_integrity_against_plan",
        lambda *_a, **_kw: _wave_result(),
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "merge_opening_guard_pending_issues",
        lambda _runner, report, **_kwargs: report,
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "project_stage_source_cards",
        lambda *_args, **_kwargs: {"world_rule_card": {"marker": True}},
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "_build_chapter_quality_repair_stage_cards",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(chapter_flow_review, "coerce_world_rule_card", lambda card: card)
    monkeypatch.setattr(chapter_flow_review, "check_world_rule_compliance", _fake_world_rule_check)
    monkeypatch.setattr(
        chapter_flow_review,
        "patch_blocking_world_rule_conflicts",
        _fake_world_rule_patch,
    )
    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_quality_checks)

    original_alignment = SimpleNamespace(alignment_score=8.0, repair_actions=[])
    original_continuity = SimpleNamespace(continuity_score=8.0, issues=[])
    original_quality = SimpleNamespace(
        repair_tickets=[_TicketStub()],
        review_findings=[],
    )
    state = chapter_flow_review._ReviewPhaseState(  # noqa: SLF001
        current_text="原正文",
        baseline_text="原正文",
        wave_meta={"warnings": []},
        alignment_report=original_alignment,
        continuity_report=original_continuity,
        chapter_repair_report=original_quality,
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(
            long_wave_post_condition_policy="repair",
            long_wave_word_count_policy="inherit",
            long_eval_repair_enabled=False,
            long_word_count_archive_gate_enabled=False,
        ),
        config=SimpleNamespace(),
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=context.settings,
    )
    prepared = SimpleNamespace(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(expected_word_count=1000),
            chapter_source_slice=SimpleNamespace(),
            style_profile=None,
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(world_rule_applications=[]),
        window_manager=None,
        window_config=None,
        memory_hints={},
        reading_power_hint=None,
    )

    result = await chapter_flow_review._run_chapter_quality_repair_lane(  # noqa: SLF001
        runner=runner,
        context=context,
        prepared=prepared,
        trace=SimpleNamespace(),
        chapter_number=1,
        state=state,
        skip_quality=False,
        on_step=lambda step, _payload: events.append(step),
    )

    assert result.current_text == expected_text
    assert len(quality_calls) == expected_quality_calls
    assert bool(result.chapter_quality_repair.get("rolled_back")) is expected_rollback
    if expected_rollback:
        assert result.cumulative_change_ratio == 0.0
        assert result.alignment_report is original_alignment
        assert result.continuity_report is original_continuity
        assert result.chapter_repair_report is original_quality
        assert "world_rule_post_quality_rollback" in events
    else:
        assert result.cumulative_change_ratio > 0.0
        assert "world_rule_post_quality_patch_recheck" in events


@pytest.mark.asyncio
async def test_world_rule_patch_internal_failure_degrades_to_quality_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    seen_tickets: list[dict[str, object]] = []

    class _FakeEditStep:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, input_data: object) -> object:
            seen_tickets.extend(input_data.context["chapter_quality_repair_tickets"])
            return SimpleNamespace(revised_text="通用质量修复正文", edit_notes=["fixed world rule"])

    def _wave_result() -> object:
        return SimpleNamespace(
            blocking=False,
            archive_blocking=False,
            policy="repair",
            metrics={},
            issues=[],
            model_dump=lambda: {
                "blocking": False,
                "archive_blocking": False,
                "issues": [],
            },
        )

    async def _fake_world_rule_check(
        *, chapter_text: str, chapter_number: int, **_kwargs: object
    ) -> WorldRuleComplianceReport:
        if chapter_text == "通用质量修复正文":
            return WorldRuleComplianceReport(chapter_number=chapter_number)
        return WorldRuleComplianceReport(
            chapter_number=chapter_number,
            issues=[
                WorldRuleIssue(
                    rule_id="WR001",
                    verdict="conflict",
                    severity="critical",
                    summary="打开怀表后盖违反计划禁止边界。",
                    evidence="旋开表后盖",
                    repair_goal="保持怀表佩戴且不得开盖。",
                )
            ],
        )

    async def _failing_patch(**_kwargs: object) -> object:
        raise RuntimeError("missing target_window")

    async def _fake_quality_checks(
        *_args: object, **_kwargs: object
    ) -> tuple[object, object, object]:
        return (
            SimpleNamespace(alignment_score=9.0, repair_actions=[]),
            SimpleNamespace(continuity_score=9.0, issues=[]),
            SimpleNamespace(repair_tickets=[], review_findings=[]),
        )

    monkeypatch.setattr(chapter_flow_review, "EditStep", _FakeEditStep)
    monkeypatch.setattr(
        chapter_flow_review, "assess_wave_integrity", lambda *_a, **_kw: _wave_result()
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "assess_wave_integrity_against_plan",
        lambda *_a, **_kw: _wave_result(),
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "merge_opening_guard_pending_issues",
        lambda _runner, report, **_kwargs: report,
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "project_stage_source_cards",
        lambda *_args, **_kwargs: {"world_rule_card": {"marker": True}},
    )
    monkeypatch.setattr(
        chapter_flow_review,
        "_build_chapter_quality_repair_stage_cards",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(chapter_flow_review, "coerce_world_rule_card", lambda card: card)
    monkeypatch.setattr(chapter_flow_review, "check_world_rule_compliance", _fake_world_rule_check)
    monkeypatch.setattr(
        chapter_flow_review,
        "patch_blocking_world_rule_conflicts",
        _failing_patch,
    )
    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_quality_checks)

    state = chapter_flow_review._ReviewPhaseState(  # noqa: SLF001
        current_text="原正文",
        baseline_text="原正文",
        wave_meta={"warnings": []},
        alignment_report=SimpleNamespace(alignment_score=8.0, repair_actions=[]),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        chapter_repair_report=SimpleNamespace(repair_tickets=[], review_findings=[]),
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(
            long_wave_post_condition_policy="repair",
            long_wave_word_count_policy="inherit",
            long_eval_repair_enabled=False,
            long_word_count_archive_gate_enabled=False,
        ),
        config=SimpleNamespace(),
    )
    runner = SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=context.settings,
    )
    prepared = SimpleNamespace(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(expected_word_count=1000),
            chapter_source_slice=SimpleNamespace(),
            style_profile=None,
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(world_rule_applications=[]),
        window_manager=None,
        window_config=None,
        memory_hints={},
        reading_power_hint=None,
    )

    result = await chapter_flow_review._run_chapter_quality_repair_lane(  # noqa: SLF001
        runner=runner,
        context=context,
        prepared=prepared,
        trace=SimpleNamespace(),
        chapter_number=6,
        state=state,
        skip_quality=False,
        on_step=lambda step, payload: events.append((step, payload)),
    )

    event_names = [name for name, _payload in events]
    assert result.current_text == "通用质量修复正文"
    assert result.chapter_quality_repair["world_rule_patch_degraded"] is True
    assert result.chapter_quality_repair["world_rule_diagnostic_count"] == 1
    assert any(ticket["dimension"] == "world_rule" for ticket in seen_tickets)
    assert "world_rule_repair_internal_error" in event_names
    assert "world_rule_patch_degraded" in event_names
    assert "world_rule_repair_recheck" in event_names


def test_bounded_long_eval_context_projects_only_chapter_local_fields() -> None:
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            chapter_number=1,
            summary="本章只写沈既白取得魂玉残片。",
            goal="建立魂玉代价与第一处冲突。",
            involved_characters=["沈既白", "陆青岚"],
            time_marker="子夜",
        ),
        story_bible=SimpleNamespace(genre="玄幻", theme="代价"),
        chapter_source_slice={
            "story_foundation": {
                "premise": "本章局部前提",
                "future_blueprint": "第九章提前揭示幕后真凶",
            },
            "creative_direction": {"chapter_intent": "本章建立魂玉异常"},
            "style_voice": {"summary": "冷峻、克制、以动作承载情绪"},
        },
    )
    plan = SimpleNamespace(
        opening_contract="从魂玉碎裂后的异响开场。",
        closing_contract="以残片发热留下下一章钩子。",
        emotional_arc="疑惧到被迫行动",
        scene_intents=[
            {
                "summary": "沈既白在祠堂发现魂玉残片",
                "purpose": "引出主线物件",
                "required_outcome": "确认残片会回应血脉",
                "exit_target_state": "沈既白带走残片",
                "location": "沈家祠堂",
            }
        ],
        cross_scene_intent={
            "cross_scene_references": [{"description": "残片发热"}],
            "pacing_curve": [2],
            "future_chapter_payoff": "第九章真凶身份",
        },
    )

    context = _bounded_long_eval_context(bundle=bundle, plan=plan, chapter_number=1)
    rendered = json.dumps(context, ensure_ascii=False)

    assert context["genre"] == "玄幻"
    assert context["theme"] == "代价"
    assert context["blueprint"]["synopsis"] == "本章只写沈既白取得魂玉残片。"
    assert context["beats"][0]["summary"] == "沈既白在祠堂发现魂玉残片"
    assert context["execution_plan"]["cross_scene_intent"] == {
        "cross_scene_references": [{"description": "残片发热"}],
        "pacing_curve": [2],
    }
    assert "第九章" not in rendered
    assert "幕后真凶" not in rendered


def test_quality_gate_treats_stale_eval_as_diagnostic_only() -> None:
    current_text = "当前正文已经变化。" * 20
    stale_eval = EvalReport(
        overall_score=1.0,
        passed=False,
        source_text_hash=source_text_hash("旧正文"),
    )
    prepared = PreparedChapterArtifacts(
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(chapter_number=1),
            layout=SimpleNamespace(),
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
    )
    review = ChapterReviewArtifacts(
        prepared=prepared,
        current_text=current_text,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(
            alignment_score=9.0, missing_main_points=[], repair_actions=[]
        ),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(continuity_score=9.0, issues=[]),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(causal_score=9.0, issues=[]),
        eval_report=stale_eval,
    )

    gate = chapter_flow._build_quality_gate(  # noqa: SLF001
        review,
        settings=SimpleNamespace(
            max_revelations_per_chapter=99,
            long_plot_progression_quality_floor=0.0,
        ),
        target_word_count=0,
    )

    checks = gate.report().checks
    assert any(check.dimension == "eval_score_stale" for check in checks)
    assert not any(check.dimension == "eval_score" for check in checks)


def test_literary_contract_review_gate_warns_without_blocking_by_default() -> None:
    review = ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=1),
                layout=SimpleNamespace(),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
        ),
        current_text="当前正文已经变化。" * 20,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(
            alignment_score=9.0, missing_main_points=[], repair_actions=[]
        ),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(continuity_score=9.0, issues=[]),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(causal_score=9.0, issues=[]),
        warnings=["theme_arc_alignment: 本章是否执行主题职责？"],
    )

    gate = chapter_flow._build_quality_gate(  # noqa: SLF001
        review,
        settings=SimpleNamespace(
            max_revelations_per_chapter=99,
            long_plot_progression_quality_floor=0.0,
            long_literary_contract_review_gate_mode="warn",
        ),
        target_word_count=0,
    )

    literary_check = next(
        check for check in gate.report().checks if check.dimension == "literary_contract"
    )
    assert literary_check.passed is True
    assert literary_check.details["mode"] == "warn"


def test_literary_contract_review_gate_can_block_in_strict_mode() -> None:
    review = ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=1),
                layout=SimpleNamespace(),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
        ),
        current_text="当前正文已经变化。" * 20,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(
            alignment_score=9.0, missing_main_points=[], repair_actions=[]
        ),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(continuity_score=9.0, issues=[]),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(causal_score=9.0, issues=[]),
        warnings=["literary_contract: 主题职责未落入行动后果"],
    )

    gate = chapter_flow._build_quality_gate(  # noqa: SLF001
        review,
        settings=SimpleNamespace(
            max_revelations_per_chapter=99,
            long_plot_progression_quality_floor=0.0,
            long_literary_contract_review_gate_mode="strict",
        ),
        target_word_count=0,
    )

    literary_check = next(
        check for check in gate.report().checks if check.dimension == "literary_contract"
    )
    assert literary_check.passed is False
    assert "六要素文学合同履约存在" in literary_check.message


def test_literary_contract_strict_mode_keeps_theme_question_diagnostic() -> None:
    review = ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=1),
                layout=SimpleNamespace(),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
        ),
        current_text="当前正文已经变化。" * 20,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(
            alignment_score=9.0, missing_main_points=[], repair_actions=[]
        ),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(continuity_score=9.0, issues=[]),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(causal_score=9.0, issues=[]),
        warnings=["theme_arc_alignment: 本章是否执行主题职责？"],
    )

    gate = chapter_flow._build_quality_gate(  # noqa: SLF001
        review,
        settings=SimpleNamespace(
            max_revelations_per_chapter=99,
            long_plot_progression_quality_floor=0.0,
            long_literary_contract_review_gate_mode="strict",
        ),
        target_word_count=0,
    )

    literary_check = next(
        check for check in gate.report().checks if check.dimension == "literary_contract"
    )
    assert literary_check.passed is True
    assert literary_check.details["blocking_warning_count"] == 0
    assert literary_check.details["diagnostic_warning_count"] == 1


def test_literary_contract_diagnostic_warning_count_mixed_warnings() -> None:
    """Verify diagnostic_warning_count counts only theme_arc_alignment: prefix."""
    review = ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=1),
                layout=SimpleNamespace(),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
        ),
        current_text="当前正文已经变化。" * 20,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(
            alignment_score=9.0, missing_main_points=[], repair_actions=[]
        ),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(continuity_score=9.0, issues=[]),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(causal_score=9.0, issues=[]),
        warnings=[
            "theme_arc_alignment: 本章是否执行主题职责？",
            "theme_arc_alignment: 另一主题检查",
            "literary_contract: 主题职责未落入行动后果",
        ],
    )

    gate = chapter_flow._build_quality_gate(  # noqa: SLF001
        review,
        settings=SimpleNamespace(
            max_revelations_per_chapter=99,
            long_plot_progression_quality_floor=0.0,
            long_literary_contract_review_gate_mode="strict",
        ),
        target_word_count=0,
    )

    literary_check = next(
        check for check in gate.report().checks if check.dimension == "literary_contract"
    )
    assert literary_check.details["warning_count"] == 3
    assert literary_check.details["blocking_warning_count"] == 1
    assert literary_check.details["diagnostic_warning_count"] == 2
    assert literary_check.details["warnings"] == ["literary_contract: 主题职责未落入行动后果"]
    assert literary_check.details["diagnostic_literary_warnings"] == [
        "theme_arc_alignment: 本章是否执行主题职责？",
        "theme_arc_alignment: 另一主题检查",
    ]
    assert "1 个待核查问题" in literary_check.message
    assert literary_check.passed is False


def test_extract_dimension_scores_shared_function() -> None:
    """Verify extract_dimension_scores returns normalized scores from reports."""
    from novel_forge.pipeline.long.stages.finalize_common import extract_dimension_scores

    # All None reports
    result = extract_dimension_scores(eval_report=None, continuity_report=None, causal_report=None)
    assert result == {"eval": None, "continuity": None, "causal": None}

    # Valid reports
    result = extract_dimension_scores(
        eval_report=SimpleNamespace(overall_score=7.5),
        continuity_report=SimpleNamespace(continuity_score=8.0),
        causal_report=SimpleNamespace(causal_score=9.0),
    )
    assert result == {"eval": 7.5, "continuity": 8.0, "causal": 9.0}

    # Invalid/missing score fields fall back to defaults
    result = extract_dimension_scores(
        eval_report=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        causal_report=None,
    )
    assert result["eval"] == 0.0  # default for eval
    assert result["continuity"] == 10.0  # default for continuity
    assert result["causal"] is None


def test_quality_gate_uses_shared_dimension_score_normalization() -> None:
    """The QualityGate uses the same score defaults as the archive hard gate."""
    review = ChapterReviewArtifacts(
        prepared=PreparedChapterArtifacts(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=1),
                layout=SimpleNamespace(),
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(),
        ),
        current_text="当前正文已经变化。" * 20,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(
            alignment_score=9.0, missing_main_points=[], repair_actions=[]
        ),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(continuity_score="invalid", issues=[]),
        repair_plan=SimpleNamespace(),
        causal_report=SimpleNamespace(causal_score="invalid", issues=[]),
        eval_report=SimpleNamespace(overall_score="invalid"),
    )

    gate = chapter_flow._build_quality_gate(  # noqa: SLF001
        review,
        settings=SimpleNamespace(
            max_revelations_per_chapter=99,
            long_plot_progression_quality_floor=0.0,
        ),
        target_word_count=0,
    )
    checks = {check.dimension: check for check in gate.report().checks}

    assert checks["eval_score"].score == 0.0
    assert checks["continuity"].score == 10.0
    assert checks["causal"].score == 10.0
    assert "dimension_scores" not in checks


@pytest.mark.asyncio
async def test_review_chapter_uses_pronoun_fixed_text_and_extracts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ContextStub()
    _mock_layout = SimpleNamespace(
        chapter_review_progress_path=lambda ch: f"/tmp/test_review_progress_{ch}.json",
        blueprint_path="/tmp/test_blueprint.json",
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            chapter_number=3,
            expected_word_count=1800,
            title="回声",
            goal="推进主线",
            pov_character="沈既白",
            setting="废弃庭院",
            involved_characters=["沈既白"],
        ),
        story_bible=SimpleNamespace(genre="mystery"),
        chapter_source_slice={
            "artifact_type": "chapter_source_slice",
            "project_id": "test",
            "scope": {"kind": "chapter", "ids": ["3"]},
            "artifact_id": "chapter_source_slice:003",
            "payload": {
                "runtime": {
                    "schema": "runtime_capsule_v1",
                    "chapter_contract": {
                        "schema": "RuntimeChapterContract",
                        "chapter_number": 3,
                        "literary_contract": {
                            "schema": "literary_contract_v1",
                            "theme": {
                                "primary_theme": "代价",
                                "themes": ["代价"],
                                "arc_milestones": [
                                    {
                                        "character": "沈既白",
                                        "milestone_description": "决定承担残片代价",
                                    }
                                ],
                            },
                        },
                    },
                }
            },
        },
        layout=_mock_layout,
    )
    packet = SimpleNamespace(
        previous_chapter_ending="上一章收束",
        canon_context={"characters": {"沈既白": {}}},
        known_characters=["沈既白"],
        must_carry_forward=[],
        chapter_plan={},
    )
    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=packet,
        bridge=SimpleNamespace(
            bridge_summary="承接上章收束",
            opening_location="废弃庭院",
            action_handoff="沈既白继续追查",
            pending_questions=[],
        ),
        plan=SimpleNamespace(
            scene_intents=[
                {
                    "scene_id": "scene_01",
                    "required_characters": ["沈既白"],
                    "location": "废弃庭院",
                }
            ],
            opening_contract="承接上章动作",
            closing_contract="留下新线索",
            required_state_transitions=["沈既白获得线索"],
        ),
    )

    extract_inputs: list[str] = []
    pronoun_inputs: list[str] = []

    def _model_dump(**_kw):
        return {}

    async def _fake_generate_chapter_prose(*args, **kwargs):
        from novel_forge.pipeline.long.chapter_flow import GenerateArtifacts

        return GenerateArtifacts(
            current_text="draft_text",
            performed_edits=1,
            wave_meta={"warnings": [], "cross_ref_hits": []},
        )

    async def _fake_run_quality_checks(*args, **kwargs):
        return (
            SimpleNamespace(alignment_score=8.8, repair_actions=[], model_dump=_model_dump),
            SimpleNamespace(model_dump=_model_dump),
            None,
        )

    def _fake_run_self_repetition_check(*args, **kwargs):
        return "dedup_text", []

    async def _fake_run_pronoun_check(
        runner,
        bundle,
        packet,
        current_text,
        chapter_number,
        trace,
        *,
        chapter_plan=None,
    ):
        pronoun_inputs.append(current_text)
        return "pronoun_fixed_text", {"issues": ["x"], "requires_rewrite": True}

    async def _fake_extract_and_validate(
        runner,
        bundle,
        packet,
        bridge,
        plan,
        current_text,
        chapter_number,
        trace,
        continuity_report,
        repair_exhausted=False,
    ):
        extract_inputs.append(current_text)
        return SimpleNamespace(
            canon_delta=SimpleNamespace(),
            creative_report=SimpleNamespace(),
            chapter_exit_state=None,
        )

    async def _fake_continuity_repair_v2(*args, **kwargs):
        from novel_forge.core.schemas.continuity import RepairPlan
        from novel_forge.pipeline.long.stages.continuity_repair import ContinuityRepairLoopResult

        return ContinuityRepairLoopResult(
            current_text=kwargs["current_text"],
            continuity_repair=SimpleNamespace(
                repair_plan=RepairPlan(no_op=True), applied=False, model_dump=_model_dump
            ),
            alignment_report=kwargs["alignment_report"],
            continuity_report=kwargs["continuity_report"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            repair_exhausted=False,
        )

    async def _fake_causal_repair_v2(*args, **kwargs):
        from novel_forge.pipeline.long.stages.causal_repair import CausalRepairLoopResult

        return CausalRepairLoopResult(
            current_text=kwargs["current_text"],
            causal_report=SimpleNamespace(
                issues=[], causal_score=9.5, validation_status="ok", model_dump=_model_dump
            ),
            alignment_report=kwargs["alignment_report"],
            continuity_report=kwargs["continuity_report"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            causal_warnings=[],
        )

    monkeypatch.setattr(chapter_flow, "generate_chapter_prose", _fake_generate_chapter_prose)
    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_run_quality_checks)
    monkeypatch.setattr(
        chapter_flow_review,
        "run_continuity_repair_v2",
        _fake_continuity_repair_v2,
    )
    monkeypatch.setattr(chapter_flow, "_enforce_alignment_threshold", lambda **_: None)
    monkeypatch.setattr(
        chapter_flow_review, "run_final_dedup", lambda *args, **kwargs: "draft_text"
    )
    monkeypatch.setattr(
        chapter_flow_review, "run_self_repetition_check", _fake_run_self_repetition_check
    )
    monkeypatch.setattr(chapter_flow_review, "run_pronoun_check", _fake_run_pronoun_check)
    monkeypatch.setattr(chapter_flow_review, "run_causal_repair_v2", _fake_causal_repair_v2)
    monkeypatch.setattr(chapter_flow_review, "extract_and_validate", _fake_extract_and_validate)
    monkeypatch.setattr(
        chapter_flow_review, "build_word_count_warning", lambda *args, **kwargs: None
    )

    # Mechanical guardrails should be no-ops in this test
    import novel_forge.core.domain.guardrails as _guardrails

    monkeypatch.setattr(_guardrails, "fix_pronouns_mechanical", lambda text, *a, **kw: (text, 0, 0))
    monkeypatch.setattr(_guardrails, "fix_non_cjk_leakage", lambda text, **kw: (text, []))

    # Stub review-progress persistence (test storage is a mock object)

    monkeypatch.setattr(
        "novel_forge.core.schemas.review_state.save_review_progress", lambda **kw: None
    )
    monkeypatch.setattr(
        "novel_forge.core.schemas.review_state.clear_review_progress", lambda *a, **kw: None
    )

    review = await chapter_flow.review_chapter_draft(
        context,
        prepared=prepared,
        trace=SimpleNamespace(),
        include_evaluation=False,
    )

    # In the parallel dedup‖pronoun flow, pronoun_check receives the
    # run_final_dedup output directly (not the self_repetition_check output).
    assert pronoun_inputs == ["draft_text"]
    # After the merge, the stub self_repetition_check always returns "dedup_text",
    # so extract and the final text reflect that.
    assert extract_inputs == ["dedup_text"]
    assert review.current_text == "dedup_text"
    health_events = [
        data
        for step, data in context.events
        if step == "input_integrity_check" and isinstance(data, dict)
    ]
    stages = {str(item.get("stage", "")) for item in health_events}
    assert {"review_input", "extract_input"}.issubset(stages)
    theme_events = [
        data
        for step, data in context.events
        if step == "theme_arc_alignment_check" and isinstance(data, dict)
    ]
    assert theme_events
    assert theme_events[0]["source"] == "literary_contract_v1"
    assert "代价" in theme_events[0]["question"]


@pytest.mark.asyncio
async def test_parallel_eval_has_no_side_effects_before_extract_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    class _Storage:
        def __init__(self) -> None:
            self.saved: list[tuple[object, object]] = []

        def save_json(self, path: object, payload: object) -> None:
            self.saved.append((path, payload))

    context = _ContextStub()
    context.storage = _Storage()
    context.settings.long_polish_auto_trigger_threshold = 0.0
    layout = SimpleNamespace(
        chapter_review_progress_path=lambda ch: f"/tmp/test_review_progress_{ch}.json",
        blueprint_path="/tmp/test_blueprint.json",
        eval_report_path=lambda ch: f"/tmp/eval_report_{ch}.json",
    )
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            chapter_number=4,
            expected_word_count=0,
            title="失败章",
            goal="测试失败路径",
            pov_character="沈既白",
            setting="废弃庭院",
            involved_characters=["沈既白"],
        ),
        story_bible=SimpleNamespace(genre="mystery"),
        chapter_source_slice=None,
        layout=layout,
    )
    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=SimpleNamespace(
            previous_chapter_ending="",
            canon_context={"characters": {"沈既白": {}}},
            known_characters=["沈既白"],
            must_carry_forward=[],
            chapter_plan={},
        ),
        bridge=SimpleNamespace(
            bridge_summary="承接前文",
            opening_location="废弃庭院",
            action_handoff="沈既白继续行动",
            pending_questions=[],
        ),
        plan=SimpleNamespace(
            scene_intents=[
                {
                    "scene_id": "scene_01",
                    "required_characters": ["沈既白"],
                    "location": "废弃庭院",
                }
            ],
            opening_contract="承接前文",
            closing_contract="留下钩子",
            required_state_transitions=["沈既白完成行动"],
        ),
    )

    def _model_dump(**_kw):
        return {}

    async def _fake_generate_chapter_prose(*_args, **_kwargs):
        return chapter_flow.GenerateArtifacts(
            current_text="draft_text",
            performed_edits=1,
            wave_meta={"warnings": [], "cross_ref_hits": []},
        )

    async def _fake_run_quality_checks(*_args, **_kwargs):
        return (
            SimpleNamespace(alignment_score=9.0, repair_actions=[], model_dump=_model_dump),
            SimpleNamespace(model_dump=_model_dump),
            None,
        )

    async def _fake_continuity_repair_v2(*_args, **kwargs):
        from novel_forge.core.schemas.continuity import RepairPlan
        from novel_forge.pipeline.long.stages.continuity_repair import ContinuityRepairLoopResult

        return ContinuityRepairLoopResult(
            current_text=kwargs["current_text"],
            continuity_repair=SimpleNamespace(
                repair_plan=RepairPlan(no_op=True), applied=False, model_dump=_model_dump
            ),
            alignment_report=kwargs["alignment_report"],
            continuity_report=kwargs["continuity_report"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            repair_exhausted=False,
        )

    async def _fake_causal_repair_v2(*_args, **kwargs):
        from novel_forge.pipeline.long.stages.causal_repair import CausalRepairLoopResult

        return CausalRepairLoopResult(
            current_text=kwargs["current_text"],
            causal_report=SimpleNamespace(
                issues=[], causal_score=9.5, validation_status="ok", model_dump=_model_dump
            ),
            alignment_report=kwargs["alignment_report"],
            continuity_report=kwargs["continuity_report"],
            chapter_repair_report=kwargs["chapter_repair_report"],
            causal_warnings=[],
        )

    async def _fake_evaluate_chapter_text(eval_context, **kwargs):
        report = EvalReport(
            overall_score=8.0,
            passed=True,
            source_text_hash=source_text_hash(kwargs["current_text"]),
        )
        if kwargs.get("persist"):
            eval_context.storage.save_json(
                bundle.layout.eval_report_path(kwargs["chapter_number"]),
                report.model_dump(mode="json"),
            )
        if kwargs.get("emit_step"):
            eval_context.on_step("evaluate", report.model_dump(mode="json"))
        return report

    async def _failing_extract_and_validate(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        raise RuntimeError("extract failed")

    async def _fake_opening_guard_patch(*_args, **_kwargs):
        return "draft_text"

    async def _fake_run_pronoun_check(*_args, **_kwargs):
        return "draft_text", {}

    monkeypatch.setattr(chapter_flow, "generate_chapter_prose", _fake_generate_chapter_prose)
    monkeypatch.setattr(chapter_flow, "run_opening_guard_patch", _fake_opening_guard_patch)
    monkeypatch.setattr(chapter_flow_review, "run_quality_checks", _fake_run_quality_checks)
    monkeypatch.setattr(chapter_flow_review, "run_continuity_repair_v2", _fake_continuity_repair_v2)
    monkeypatch.setattr(chapter_flow_review, "run_causal_repair_v2", _fake_causal_repair_v2)
    monkeypatch.setattr(chapter_flow_review, "run_final_dedup", lambda *a, **kw: "draft_text")
    monkeypatch.setattr(
        chapter_flow_review, "run_self_repetition_check", lambda *a, **kw: ("draft_text", [])
    )
    monkeypatch.setattr(chapter_flow_review, "run_pronoun_check", _fake_run_pronoun_check)
    monkeypatch.setattr(chapter_flow_review, "evaluate_chapter_text", _fake_evaluate_chapter_text)
    monkeypatch.setattr(chapter_flow_review, "extract_and_validate", _failing_extract_and_validate)
    monkeypatch.setattr(chapter_flow_review, "build_word_count_warning", lambda *a, **kw: None)
    monkeypatch.setattr(
        chapter_flow, "_should_include_pre_final_evaluation", lambda _settings: False
    )

    import novel_forge.core.domain.guardrails as _guardrails

    monkeypatch.setattr(_guardrails, "fix_pronouns_mechanical", lambda text, *a, **kw: (text, 0, 0))
    monkeypatch.setattr(_guardrails, "fix_non_cjk_leakage", lambda text, **kw: (text, []))
    monkeypatch.setattr(
        "novel_forge.core.schemas.review_state.save_review_progress", lambda **kw: None
    )
    monkeypatch.setattr(
        "novel_forge.core.schemas.review_state.clear_review_progress", lambda *a, **kw: None
    )

    with pytest.raises(RuntimeError, match="extract failed"):
        await chapter_flow.review_chapter_draft(
            context,
            prepared=prepared,
            trace=SimpleNamespace(),
            include_evaluation=True,
            emit_evaluation_step=True,
            persist_evaluation=True,
        )

    assert context.storage.saved == []
    assert [step for step, _payload in context.events if step == "evaluate"] == []


@pytest.mark.asyncio
async def test_apply_terminal_humanize_returns_changed_text_without_refreshing_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ContextStub()
    context.storage = SimpleNamespace()
    context.settings = SimpleNamespace(humanize_enabled=True)
    context.on_step = lambda *_args, **_kwargs: None

    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(chapter_number=5, title="终章", goal="收束"),
        layout=SimpleNamespace(),
    )
    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
    )
    review = ChapterReviewArtifacts(
        prepared=prepared,
        current_text="拟人化前正文",
        performed_edits=1,
        outcome=SimpleNamespace(name="old_outcome"),
        alignment_report=SimpleNamespace(name="old_alignment"),
        chapter_repair_report=SimpleNamespace(name="old_repair"),
        continuity_report=SimpleNamespace(name="old_continuity"),
        causal_report=SimpleNamespace(name="old_causal"),
        repair_plan=SimpleNamespace(),
        eval_report=SimpleNamespace(name="old_eval"),
    )

    async def _fake_humanize(*_args, **_kwargs):
        return SimpleNamespace(
            current_text="拟人化后正文",
            report=None,
            comparison=None,
            patches_applied=1,
            paragraph_rewrites_applied=0,
            skipped_reason="",
        )

    async def _unexpected_refresh_reports(**_kwargs):
        raise AssertionError("terminal humanize must not refresh quality reports")

    async def _unexpected_extract_and_validate(*_args, **_kwargs):
        raise AssertionError("terminal humanize must not re-extract canon")

    async def _unexpected_evaluate_chapter_text(*_args, **_kwargs):
        raise AssertionError("terminal humanize must not re-evaluate chapter text")

    async def _unexpected_guard_refresh(**_kwargs):
        raise AssertionError("terminal humanize must not re-run guard compliance")

    monkeypatch.setattr(chapter_flow, "run_humanize_layer", _fake_humanize)
    monkeypatch.setattr(
        chapter_flow,
        "refresh_quality_reports_after_semantic_text_change",
        _unexpected_refresh_reports,
    )
    monkeypatch.setattr(
        chapter_flow_review, "extract_and_validate", _unexpected_extract_and_validate
    )
    monkeypatch.setattr(
        chapter_flow_review, "evaluate_chapter_text", _unexpected_evaluate_chapter_text
    )
    monkeypatch.setattr(
        chapter_flow, "run_guard_compliance_for_final_text", _unexpected_guard_refresh
    )

    result = await chapter_flow._apply_terminal_humanize(context, review, SimpleNamespace())  # noqa: SLF001

    assert result.current_text == "拟人化后正文"
    assert result.outcome.name == "old_outcome"
    assert result.eval_report.name == "old_eval"
    assert result.continuity_report.name == "old_continuity"
    assert result.performed_edits == 2


@pytest.mark.asyncio
async def test_alignment_repair_gateway_error_does_not_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=7.0),
        _on_step=lambda step, data: events.append((step, data)),
    )
    alignment_report = SimpleNamespace(
        alignment_score=3.0,
        repair_actions=["补齐主线动作"],
        missing_main_points=[],
    )

    async def _raise_gateway(*args, **kwargs):
        raise ModelGatewayError("rate limited", is_transient=True)

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _raise_gateway)

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=SimpleNamespace(),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text="原始正文",
        chapter_number=2,
        alignment_report=alignment_report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=8,
    )

    assert result.current_text == "原始正文"
    assert result.alignment_report is alignment_report
    assert result.repair_exhausted is True
    assert result.rounds_used == 0
    assert "对齐修复模型调用失败" in result.warning
    event_names = [step for step, _ in events]
    assert "alignment_repair_gateway_error" in event_names
    assert "alignment_repair_repair_gateway_error" in event_names


@pytest.mark.asyncio
async def test_alignment_repair_counts_one_round_after_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=7.0),
        _on_step=lambda step, data: events.append((step, data)),
    )
    before_report = SimpleNamespace(
        alignment_score=3.0,
        repair_actions=["补齐主线动作"],
        missing_main_points=[],
    )
    after_report = SimpleNamespace(
        alignment_score=8.2,
        repair_actions=[],
        missing_main_points=[],
    )

    async def _repair(*args, **kwargs):
        return "修复后正文内容显著变化"

    async def _recheck(*args, **kwargs):
        return after_report

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    monkeypatch.setattr(chapter_flow, "recheck_alignment", _recheck)
    monkeypatch.setattr(
        chapter_flow, "_should_skip_alignment_recheck", lambda *a, **kw: (False, "")
    )

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=SimpleNamespace(),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text="原始正文",
        chapter_number=2,
        alignment_report=before_report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=8,
    )

    assert result.current_text == "修复后正文内容显著变化"
    assert result.alignment_report is after_report
    assert result.rounds_used == 1


@pytest.mark.asyncio
async def test_alignment_repair_runs_followup_until_literal_contract_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=8.0),
        _settings=SimpleNamespace(guard_ticket_alignment_followup_max_attempts=2),
        _on_step=lambda step, data: events.append((step, data)),
    )
    plan = SimpleNamespace(
        required_literals=[
            {
                "contract_id": "secret-boundary",
                "literal": "有些事情不是你想知道就能知道的",
                "scene_id": "scene_02",
                "reason": "后文逐字回指",
                "placement_hint": "林小满拒绝回答时",
            }
        ],
        scene_intents=[
            {
                "scene_id": "scene_02",
                "required_outcome": "林小满说「有些事情不是你想知道就能知道的」",
            }
        ],
    )
    before_report = SimpleNamespace(
        alignment_score=5.0,
        repair_actions=["补回指定台词"],
        missing_main_points=["指定台词缺失"],
        weak_subplot_points=[],
    )
    reports = [
        SimpleNamespace(
            alignment_score=5.5,
            repair_actions=["继续补回指定台词"],
            missing_main_points=["指定台词仍缺失"],
            weak_subplot_points=[],
        ),
        SimpleNamespace(
            alignment_score=8.4,
            repair_actions=[],
            missing_main_points=[],
            weak_subplot_points=[],
        ),
    ]
    repair_rounds: list[int] = []

    async def _repair(*args, **kwargs):
        repair_rounds.append(kwargs["repair_round"])
        if kwargs["repair_round"] == 1:
            return kwargs["current_text"] + "\n第一轮只补了场景。"
        return kwargs["current_text"] + "\n林小满说：「有些事情不是你想知道就能知道的」"

    async def _recheck(*args, **kwargs):
        return reports.pop(0)

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    monkeypatch.setattr(chapter_flow, "recheck_alignment", _recheck)
    monkeypatch.setattr(
        chapter_flow, "_should_skip_alignment_recheck", lambda *a, **kw: (False, "")
    )

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=SimpleNamespace(),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=plan,
        current_text="原始正文",
        chapter_number=9,
        alignment_report=before_report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=5,
    )

    assert repair_rounds == [1, 2]
    assert result.rounds_used == 2
    assert "有些事情不是你想知道就能知道的" in result.current_text
    assert result.alignment_report.alignment_score == pytest.approx(8.4)


@pytest.mark.asyncio
async def test_yaozheng_alignment_rank_keeps_semantic_repair_before_literal_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay the real 2.6 -> 9.6 rollback shape from the 「药证」 run."""

    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=8.0),
        _settings=SimpleNamespace(
            guard_ticket_alignment_followup_max_attempts=2,
            long_alignment_critical_max_attempts=2,
        ),
        _on_step=lambda step, data: events.append((step, data)),
    )
    plan = SimpleNamespace(
        required_literals=[
            {
                "contract_id": "suspension-order",
                "literal": "停职五日",
                "scene_id": "scene_01",
                "reason": "后文回指",
                "placement_hint": "开场",
            }
        ],
        scene_intents=[],
    )
    before_report = SimpleNamespace(
        alignment_score=2.6,
        repair_actions=["补齐五项已核验主线缺口"],
        missing_main_points=[f"缺口{i}" for i in range(5)],
        weak_subplot_points=["表达偏弱"],
    )
    reports = [
        SimpleNamespace(
            alignment_score=9.6,
            repair_actions=[],
            missing_main_points=[],
            weak_subplot_points=[],
        ),
        SimpleNamespace(
            alignment_score=9.2,
            repair_actions=[],
            missing_main_points=[],
            weak_subplot_points=[],
        ),
    ]
    repair_inputs: list[str] = []

    async def _repair(*args, **kwargs):
        repair_inputs.append(kwargs["current_text"])
        if kwargs["repair_round"] == 1:
            return "证据链、支持态度与归档路径已补齐。"
        return kwargs["current_text"] + "\n停职五日。"

    async def _recheck(*args, **kwargs):
        return reports.pop(0)

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    monkeypatch.setattr(chapter_flow, "recheck_alignment", _recheck)
    monkeypatch.setattr(
        chapter_flow, "_should_skip_alignment_recheck", lambda *a, **kw: (False, "")
    )

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=SimpleNamespace(chapter_outline=SimpleNamespace()),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=plan,
        current_text="停职五日。旧文未完成五项主线义务。",
        chapter_number=2,
        alignment_report=before_report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=5,
    )

    assert repair_inputs[1].startswith("证据链")
    assert result.current_text.endswith("停职五日。")
    assert result.alignment_report.alignment_score == pytest.approx(9.2)
    accepted = [payload for step, payload in events if step == "alignment_repair_candidate_accepted"]
    assert len(accepted) == 2
    assert accepted[0]["rank"][:3] == [1, 1, 0]
    assert accepted[1]["rank"][:3] == [0, 0, 0]


@pytest.mark.asyncio
async def test_alignment_repair_routes_conflicting_outline_plan_evidence_to_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair_called = False

    async def _repair(*args, **kwargs):
        nonlocal repair_called
        repair_called = True
        return kwargs["current_text"]

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    with pytest.raises(ConsistencyViolationError) as exc_info:
        await chapter_flow._run_alignment_repair_stage(
            runner=SimpleNamespace(
                _config=SimpleNamespace(alignment_threshold=8.0),
                _settings=SimpleNamespace(),
                _on_step=lambda *_args: None,
            ),
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(goal="在停职五日期间完成受审")
            ),
            packet=SimpleNamespace(),
            bridge=SimpleNamespace(),
            plan=SimpleNamespace(
                scene_intents=[SimpleNamespace(required_outcome="三日停职结束前复验")]
            ),
            current_text="沈昭开始复验。",
            chapter_number=2,
            alignment_report=SimpleNamespace(
                alignment_score=6.0,
                repair_actions=["改期限"],
                missing_main_points=["期限不一致"],
                weak_subplot_points=[],
            ),
            trace=SimpleNamespace(),
            on_step=lambda *_args: None,
            total_rounds_used=0,
            total_rounds_cap=5,
        )

    assert exc_info.value.replan_target is RecoveryTarget.PLAN
    assert repair_called is False


@pytest.mark.asyncio
async def test_alignment_repair_rechecks_literal_fix_against_exact_changed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    persisted_reports: list[dict[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=8.0),
        _settings=SimpleNamespace(guard_ticket_alignment_followup_max_attempts=2),
        _storage=SimpleNamespace(
            save_json=lambda _path, payload: persisted_reports.append(payload)
        ),
        _on_step=lambda step, data: events.append((step, data)),
    )
    bundle = SimpleNamespace(
        layout=SimpleNamespace(alignment_report_path=lambda chapter: f"alignment-{chapter}.json")
    )
    plan = SimpleNamespace(
        required_literals=[
            {
                "contract_id": "secret-boundary",
                "literal": "有些事情不是你想知道就能知道的",
                "scene_id": "scene_04",
                "reason": "后文逐字回指",
                "placement_hint": "聚餐对话中",
            }
        ],
        scene_intents=[
            {
                "scene_id": "scene_04",
                "summary": "林小满说「有些事情不是你想知道就能知道的」",
            }
        ],
    )
    report = SimpleNamespace(
        alignment_score=9.6,
        repair_actions=[],
        missing_main_points=[],
        weak_subplot_points=[],
    )

    async def _repair(*args, **kwargs):
        return kwargs["current_text"] + "\n林小满说：「有些事情不是你想知道就能知道的」"

    recheck_calls = 0

    async def _recheck(*args, **kwargs):
        nonlocal recheck_calls
        recheck_calls += 1
        return report

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    monkeypatch.setattr(chapter_flow, "recheck_alignment", _recheck)
    monkeypatch.setattr(
        chapter_flow,
        "_should_skip_alignment_recheck",
        lambda *a, **kw: (True, "high similarity"),
    )

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=bundle,
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=plan,
        current_text="原始正文",
        chapter_number=9,
        alignment_report=report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=5,
    )

    assert "有些事情不是你想知道就能知道的" in result.current_text
    assert result.rounds_used == 1
    assert recheck_calls == 1
    assert persisted_reports[-1]["source_text_hash"] == source_text_hash(result.current_text)
    assert persisted_reports[-1]["report_context_hash"]
    accepted = [
        payload for step, payload in events if step == "alignment_repair_candidate_accepted"
    ]
    assert accepted
    assert any(step == "alignment_recheck_cache_bypassed" for step, _ in events)


@pytest.mark.asyncio
async def test_alignment_repair_bypasses_similarity_cache_for_semantic_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=8.0),
        _settings=SimpleNamespace(guard_ticket_alignment_followup_max_attempts=2),
        _on_step=lambda step, data: events.append((step, data)),
    )
    before_report = SimpleNamespace(
        alignment_score=9.2,
        repair_actions=["补回主角拿到档案的现场动作"],
        missing_main_points=["主角尚未拿到档案"],
        weak_subplot_points=[],
    )
    after_report = SimpleNamespace(
        alignment_score=9.3,
        repair_actions=[],
        missing_main_points=[],
        weak_subplot_points=[],
    )
    recheck_calls = 0

    async def _repair(*args, **kwargs):
        return kwargs["current_text"] + "\n他伸手接过档案。"

    async def _recheck(*args, **kwargs):
        nonlocal recheck_calls
        recheck_calls += 1
        return after_report

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    monkeypatch.setattr(chapter_flow, "recheck_alignment", _recheck)
    monkeypatch.setattr(
        chapter_flow,
        "_should_skip_alignment_recheck",
        lambda *a, **kw: (True, "high similarity"),
    )

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=SimpleNamespace(),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(scene_intents=[]),
        current_text="原始正文。" * 200,
        chapter_number=2,
        alignment_report=before_report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=5,
    )

    assert recheck_calls == 1
    assert result.alignment_report is after_report
    assert any(step == "alignment_recheck_cache_bypassed" for step, _ in events)


@pytest.mark.asyncio
async def test_alignment_repair_rolls_back_regressive_candidate_before_followup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    persisted_reports: list[dict[str, object]] = []
    runner = SimpleNamespace(
        _config=SimpleNamespace(alignment_threshold=8.0),
        _settings=SimpleNamespace(guard_ticket_alignment_followup_max_attempts=2),
        _storage=SimpleNamespace(
            save_json=lambda _path, payload: persisted_reports.append(payload)
        ),
        _on_step=lambda step, data: events.append((step, data)),
    )
    bundle = SimpleNamespace(
        layout=SimpleNamespace(alignment_report_path=lambda chapter: f"alignment-{chapter}.json")
    )
    before_report = SimpleNamespace(
        alignment_score=4.0,
        repair_actions=["补足主线"],
        missing_main_points=["主线缺失"],
        weak_subplot_points=[],
    )
    reports = [
        SimpleNamespace(
            alignment_score=2.0,
            repair_actions=["恢复主线"],
            missing_main_points=["主线缺失", "结尾被破坏"],
            weak_subplot_points=[],
        ),
        SimpleNamespace(
            alignment_score=8.5,
            repair_actions=[],
            missing_main_points=[],
            weak_subplot_points=[],
        ),
    ]
    bases: list[str] = []

    async def _repair(*args, **kwargs):
        bases.append(kwargs["current_text"])
        return kwargs["current_text"] + f"\n候选{kwargs['repair_round']}"

    async def _recheck(*args, **kwargs):
        return reports.pop(0)

    monkeypatch.setattr(chapter_flow, "alignment_repair_edit", _repair)
    monkeypatch.setattr(chapter_flow, "recheck_alignment", _recheck)
    monkeypatch.setattr(
        chapter_flow, "_should_skip_alignment_recheck", lambda *a, **kw: (False, "")
    )

    result = await chapter_flow._run_alignment_repair_stage(
        runner=runner,
        bundle=bundle,
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(scene_intents=[]),
        current_text="原始正文",
        chapter_number=9,
        alignment_report=before_report,
        trace=SimpleNamespace(),
        on_step=lambda step, data: events.append((step, data)),
        total_rounds_used=0,
        total_rounds_cap=5,
    )

    assert bases == ["原始正文", "原始正文"]
    assert result.current_text == "原始正文\n候选2"
    assert persisted_reports[0]["source_text_hash"] == source_text_hash("原始正文")
    assert any(step == "alignment_repair_candidate_rolled_back" for step, _ in events)
