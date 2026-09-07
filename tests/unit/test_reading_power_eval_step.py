"""Tests for ReadingPowerEvalStep — parse, fallback, and integration."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.reading_power import MicroPayoffType, ReadingPowerReport
from novel_forge.core.schemas.reading_power_repair import _build_reading_power_issues
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.reading_power_eval_step import (
    ReadingPowerEvalStep,
    ReadingPowerInput,
)
from tests.unit.conftest import _MockRouter

# ── Fixtures / fakes ─────────────────────────────────────────────────────


class _FakeBuilder:
    def build(
        self,
        task_type: TaskType,
        context: dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        top_p: float | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        prior_messages: list | None = None,
    ) -> ModelRequest:
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _make_step(response_content: str) -> ReadingPowerEvalStep:
    return ReadingPowerEvalStep(
        _MockRouter(response_content),
        _FakeBuilder(),
        settings=Settings(),
    )


def test_reading_power_context_preserves_complete_current_chapter_guidance() -> None:
    long_description = "长线悬念" * 100
    input_data = ReadingPowerInput(
        chapter_number=12,
        chapter_text="正文",
        expected_hook={"hook_description": long_description},
        expected_payoffs=[
            {"payoff_type": "clue", "description": f"兑现{i}" + long_description}
            for i in range(18)
        ],
        suspense_timeline_entries=[
            {"suspense_id": f"thread_{i}", "suspense_description": long_description}
            for i in range(25)
        ],
        preferred_payoff_types=[f"type_{i}" for i in range(12)],
        main_plot_points=[f"主线{i}" for i in range(16)],
    )

    context = ReadingPowerEvalStep._build_llm_context(input_data)

    assert context["expected_hook"]["hook_description"] == long_description
    assert len(context["expected_payoffs"]) == 18
    assert context["expected_payoffs"][-1]["description"].endswith(long_description)
    assert len(context["suspense_timeline_entries"]) == 25
    assert len(context["preferred_payoff_types"]) == 12
    assert len(context["main_plot_points"]) == 16


_VALID_RESPONSE = """{
    "hook_type": "crisis",
    "hook_strength": "strong",
    "hook_description": "主角被追杀",
    "prev_hook_fulfilled": true,
    "micro_payoffs": [
        {"type": "information", "description": "揭示了幕后黑手", "strength": "strong"},
        {"type": "ability", "description": "主角突破瓶颈", "strength": "medium"}
    ],
    "is_transition": false,
    "next_chapter_reason": "追杀下章如何逃脱",
    "information_pacing": "balanced",
    "main_plot_depth": "moderate",
    "tension_match": "matched",
    "character_drive": "moderate"
}"""

_MISSING_KEYS_RESPONSE = '{"hook_type": "mystery"}'  # missing required keys

_INVALID_JSON_RESPONSE = "这不是JSON内容，无法解析。"


# ── _parse_report unit tests ─────────────────────────────────────────────


def test_parse_report_valid_response() -> None:
    step = _make_step(_VALID_RESPONSE)
    import json

    data = json.loads(_VALID_RESPONSE)
    input_data = ReadingPowerInput(chapter_number=3, chapter_text="some text")
    report = step._parse_report(data, input_data)
    assert report.chapter == 3
    assert report.hook_type == "crisis"
    assert report.hook_strength == "strong"
    assert len(report.micro_payoffs) == 2
    assert report.micro_payoffs[0].payoff_type == MicroPayoffType.INFORMATION
    assert report.overall_score > 0
    assert report.evaluation_status == "ok"
    assert report.is_fallback is False
    assert report.review_mode == "full_review"
    assert report.source_text_hash


def test_parse_report_derives_missing_dimension_scores_from_labels() -> None:
    step = _make_step("")
    data = {
        "hook_type": "mystery",
        "hook_strength": "medium",
        "prev_hook_fulfilled": True,
        "micro_payoffs": [
            {"type": "emotion", "description": "情绪兑现", "strength": "medium"},
            {"type": "clue", "description": "线索推进", "strength": "medium"},
            {"type": "information", "description": "信息揭示", "strength": "medium"},
            {"type": "relationship", "description": "关系确认", "strength": "medium"},
            {"type": "information", "description": "反向强化谜团", "strength": "medium"},
        ],
        "information_pacing": "balanced",
        "main_plot_depth": "moderate",
        "tension_match": "matched",
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="正文")

    report = step._parse_report(data, input_data)

    assert report.information_pacing_score == 2.0
    assert report.tension_match_score == 2.0
    assert report.overall_score > 7.0


def test_parse_report_repairs_inconsistent_zero_scores_for_positive_labels() -> None:
    step = _make_step("")
    data = {
        "hook_type": "mystery",
        "hook_strength": "medium",
        "micro_payoffs": [],
        "information_pacing": "balanced",
        "information_pacing_score": 0.0,
        "tension_match": "matched",
        "tension_match_score": 0.0,
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="正文")

    report = step._parse_report(data, input_data)

    assert report.information_pacing_score == 2.0
    assert report.tension_match_score == 2.0


def test_parse_report_rescales_fractional_positive_dimension_scores() -> None:
    step = _make_step("")
    data = {
        "hook_type": "mystery",
        "hook_strength": "medium",
        "micro_payoffs": [],
        "information_pacing": "balanced",
        "information_pacing_score": 0.75,
        "tension_match": "matched",
        "tension_match_score": 0.7,
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="正文")

    report = step._parse_report(data, input_data)

    assert report.information_pacing_score == 1.5
    assert report.tension_match_score == 1.4
    assert report.score_breakdown["components"]["information_pacing"][
        "normalized_score"
    ] == 7.5
    assert report.score_breakdown["components"]["tension_match"]["normalized_score"] == 7.0


def test_parse_report_records_score_breakdown() -> None:
    step = _make_step("")
    data = {
        "hook_type": "mystery",
        "hook_strength": "strong",
        "hook_description": "门外响起熟悉的敲门暗号",
        "prev_hook_fulfilled": True,
        "micro_payoffs": [],
        "is_transition": False,
        "next_chapter_reason": "暗号来自谁",
        "information_pacing": "balanced",
        "main_plot_depth": "moderate",
        "tension_match": "matched",
        "character_drive": "moderate",
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="正文", min_payoffs=1)

    report = step._parse_report(data, input_data)

    assert report.score_breakdown["final_score"] == report.overall_score
    assert report.score_breakdown["components"]["payoff_density"]["normalized_score"] == 0.0
    assert any(adj["reason"] == "payoff_deficit" for adj in report.score_breakdown["adjustments"])


def test_parse_report_embeds_repair_contracts_for_recheck() -> None:
    step = _make_step("")
    data = {
        "hook_type": "none",
        "hook_strength": "weak",
        "prev_hook_fulfilled": False,
        "micro_payoffs": [],
    }
    input_data = ReadingPowerInput(
        chapter_number=5,
        chapter_text="正文",
        strict_review=True,
        min_payoffs=1,
    )

    report = step._parse_report(data, input_data)

    assert report.review_mode == "targeted_recheck"
    assert {finding.issue_type for finding in report.review_findings} >= {
        "hook_missing",
        "prev_hook_unfulfilled",
        "payoff_missing",
    }
    assert all(finding.review_round == 2 for finding in report.review_findings)
    assert report.repair_readiness["architecture"] == "diagnostic_to_code_action"
    assert report.repair_readiness["auto_repair_candidate_count"] == len(report.repair_tickets)
    assert report.repair_tickets
    assert report.repair_tickets[0].postconditions


def test_parse_report_invalid_hook_type_falls_to_none() -> None:
    step = _make_step("")
    data = {"hook_type": "INVALID_TYPE", "hook_strength": "strong", "micro_payoffs": []}
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert report.hook_type == "none"


def test_parse_report_normalises_suspense_alias() -> None:
    step = _make_step("")
    data = {"hook_type": "suspense", "hook_strength": "strong", "micro_payoffs": []}
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert report.hook_type == "mystery"


def test_parse_report_coerces_false_bool_strings() -> None:
    step = _make_step("")
    data = {
        "hook_type": "mystery",
        "hook_strength": "medium",
        "prev_hook_fulfilled": "false",
        "is_transition": "true",
        "micro_payoffs": [],
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert report.prev_hook_fulfilled is False
    assert report.is_transition is True


def test_parse_report_invalid_strength_falls_to_weak() -> None:
    step = _make_step("")
    data = {"hook_type": "crisis", "hook_strength": "UNKNOWN", "micro_payoffs": []}
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert report.hook_strength == "weak"


def test_parse_report_invalid_payoff_type_skipped() -> None:
    step = _make_step("")
    data = {
        "hook_type": "crisis",
        "hook_strength": "medium",
        "micro_payoffs": [
            {"type": "INVALID_PAYOFF", "description": "test"},
            {"type": "information", "description": "valid"},
        ],
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert len(report.micro_payoffs) == 1
    assert report.micro_payoffs[0].payoff_type == MicroPayoffType.INFORMATION


def test_parse_report_payoff_type_accepts_payoff_type_key() -> None:
    """Payoffs with 'payoff_type' key (instead of 'type') should also parse."""
    step = _make_step("")
    data = {
        "hook_type": "desire",
        "hook_strength": "medium",
        "micro_payoffs": [{"payoff_type": "relationship", "description": "关系进展"}],
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert len(report.micro_payoffs) == 1


def test_parse_report_collects_suspense_ids() -> None:
    step = _make_step("")
    data = {
        "hook_type": "mystery",
        "hook_strength": "medium",
        "micro_payoffs": [],
        "resolved_suspense_ids": ["s_1", "s_1", ""],
        "unresolved_suspense_ids": ["s_2"],
    }
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="text")
    report = step._parse_report(data, input_data)
    assert report.resolved_suspense_ids == ["s_1"]
    assert report.unresolved_suspense_ids == ["s_2"]


def test_parse_report_keeps_quality_issues_out_of_reading_power() -> None:
    step = _make_step("")
    input_data = ReadingPowerInput(chapter_number=1, chapter_text="excerpt")
    data = {
        "hook_type": "mystery",
        "hook_strength": "medium",
        "micro_payoffs": [{"type": "information", "description": "账簿线索"}],
    }

    report = step._parse_report(data, input_data)

    assert "quality_issues" not in report.model_dump()


def test_reading_power_llm_context_excludes_local_quality_inputs() -> None:
    context = ReadingPowerEvalStep._build_llm_context(
        ReadingPowerInput(
            chapter_number=2,
            chapter_text="章节摘录",
            genre="悬疑",
            expected_hook={
                "hook_type": "mystery",
                "hook_strength": "strong",
                "hook_description": "发现密信",
                "extra": "不应进入提示词",
            },
            expected_payoffs=[
                {"type": "information", "description": "揭示线索", "strength": "medium"}
            ],
            suspense_schedule=[
                {
                    "suspense_id": "s1",
                    "suspense_description": "密信来源",
                    "setup_chapter": 1,
                    "planned_resolution_chapter": 3,
                    "urgency_level": "high",
                    "global_notes": "不应进入提示词",
                }
            ],
            narrative_phase_context={
                "phase_name": "调查阶段",
                "description": "主动调查",
                "tension_level": "中",
                "global_notes": "不应进入提示词",
            },
            genre_weights={"hook": 0.9},
            main_plot_continuity={"global": "不应进入提示词"},
            pacing_dialogue_ratio="不应进入提示词",
        )
    )

    assert context["expected_hook"] == {
        "hook_type": "mystery",
        "hook_strength": "strong",
        "hook_description": "发现密信",
    }
    assert "genre_weights" not in context
    assert "main_plot_continuity" not in context
    assert "pacing_dialogue_ratio" not in context
    assert "不应进入提示词" not in str(context)


def test_reading_power_repair_issues_only_use_reading_power_fields() -> None:
    report = ReadingPowerReport(
        chapter=1,
        hook_type="none",
        hook_strength="none",
        prev_hook_fulfilled=False,
        micro_payoffs=[],
    )

    issues = _build_reading_power_issues(report, min_payoffs=1)

    assert issues
    assert {issue.issue_type for issue in issues} <= {
        "hook_missing",
        "prev_hook_unfulfilled",
        "payoff_missing",
    }


def test_reading_power_repair_issues_cover_new_diagnostic_dimensions() -> None:
    report = ReadingPowerReport(
        chapter=1,
        hook_type="mystery",
        hook_strength="strong",
        prev_hook_fulfilled=True,
        micro_payoffs=[],
        information_pacing="stagnant",
        main_plot_depth="stalled",
        tension_match="depressed",
        character_drive="weak",
        revelation_over_budget=True,
    )

    issues = _build_reading_power_issues(report, min_payoffs=1)
    issue_types = {issue.issue_type for issue in issues}

    assert {
        "information_pacing_stagnant",
        "main_plot_stalled",
        "tension_depressed",
        "character_drive_weak",
        "revelation_over_budget",
    } <= issue_types


def test_default_report_returns_valid_report() -> None:
    report = ReadingPowerEvalStep._default_report(5, reason="unit_test")
    assert report.chapter == 5
    assert report.hook_type == "none"
    assert report.overall_score == 0.0
    assert report.evaluation_status == "fallback"
    assert report.is_fallback is True
    assert report.fallback_reason == "unit_test"
    assert report.suggestions


# ── Async integration tests ──────────────────────────────────────────────


async def test_run_success_returns_parsed_report() -> None:
    step = _make_step(_VALID_RESPONSE)
    rp_input = ReadingPowerInput(
        chapter_number=3,
        chapter_text="章节文本",
        genre="fantasy",
    )
    report = await step.run(rp_input)
    assert isinstance(report, ReadingPowerReport)
    assert report.chapter == 3
    assert report.hook_type == "crisis"


async def test_run_fallback_on_invalid_json() -> None:
    """When LLM returns non-JSON, step should fall back to default report."""
    step = _make_step(_INVALID_JSON_RESPONSE)
    rp_input = ReadingPowerInput(
        chapter_number=2,
        chapter_text="some text",
    )
    report = await step.run(rp_input)
    # Should not raise; returns default
    assert isinstance(report, ReadingPowerReport)
    assert report.chapter == 2
    assert report.is_fallback is True
    assert report.overall_score == 0.0


async def test_run_with_min_payoffs_param() -> None:
    step = _make_step(_VALID_RESPONSE)
    rp_input = ReadingPowerInput(
        chapter_number=1,
        chapter_text="text",
        min_payoffs=3,
    )
    report = await step.run(rp_input)
    assert isinstance(report, ReadingPowerReport)
