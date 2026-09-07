from __future__ import annotations

from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore, RepairSuggestion
from novel_forge.pipeline.short.quality_gate import (
    ShortQualityGateThresholds,
    evaluate_short_quality_gate,
)
from novel_forge.pipeline.short.stages.edit import collect_short_quality_repair_issues


def test_collect_short_quality_repair_issues_picks_targeted_closure_and_style_signals() -> None:
    report = EvalReport(
        scores=[
            EvalScore(dimension="continuity", score=6.6, comment="结尾收束过快，余韵不足"),
            EvalScore(dimension="style", score=6.8, comment="风格尚稳，但文学氛围还不够集中"),
            EvalScore(dimension="character", score=7.8, comment="人物动机基本成立"),
        ],
        overall_score=7.1,
        passed=True,
        threshold=6.0,
        summary="主题回应偏弱，最后一段没有形成足够的情绪落点。",
        repair_suggestions=[
            RepairSuggestion(
                issue="结尾回响不足",
                location="最后一段",
                suggestion="补一个回应主题的动作或意象，让选择真正落地",
                priority="high",
                dimension="continuity",
            )
        ],
    )

    issues = collect_short_quality_repair_issues(report)

    assert len(issues) >= 2
    assert any("主题回应偏弱" in item for item in issues)
    assert any("最后一段" in item for item in issues)


def test_collect_short_quality_repair_issues_ignores_healthy_eval_reports() -> None:
    report = EvalReport(
        scores=[
            EvalScore(dimension="continuity", score=8.2, comment="衔接自然"),
            EvalScore(dimension="style", score=8.1, comment="风格稳定"),
            EvalScore(dimension="engagement", score=8.0, comment="悬念与余韵兼具"),
        ],
        overall_score=8.1,
        passed=True,
        threshold=6.0,
        summary="整体完成度高，结构与风格都比较稳定。",
        repair_suggestions=[],
    )

    assert collect_short_quality_repair_issues(report) == []


def test_evaluate_short_quality_gate_uses_continuity_style_engagement_floors() -> None:
    report = EvalReport(
        scores=[
            EvalScore(dimension="continuity", score=6.9, comment="尾段衔接略急"),
            EvalScore(dimension="style", score=7.6, comment="风格稳定"),
            EvalScore(dimension="engagement", score=7.2, comment="吸引力尚可"),
        ],
        overall_score=7.8,
        passed=True,
        threshold=6.0,
        summary="整体尚可，但结尾衔接略急。",
        repair_suggestions=[],
    )

    gate_report = evaluate_short_quality_gate(
        report,
        ShortQualityGateThresholds(
            min_overall_score=7.4,
            continuity_floor=7.0,
            style_floor=7.0,
            engagement_floor=7.0,
        ),
    )

    assert gate_report.verdict.value == "warn"
    assert any(item.dimension == "eval_continuity" for item in gate_report.failed_checks)


def test_short_quality_gate_serialization_marks_warn_as_not_passed() -> None:
    report = EvalReport(
        scores=[
            EvalScore(dimension="continuity", score=6.8, comment="结尾衔接略急"),
            EvalScore(dimension="style", score=7.5, comment="风格稳定"),
            EvalScore(dimension="engagement", score=7.4, comment="吸引力尚可"),
        ],
        overall_score=7.7,
        passed=True,
        threshold=6.0,
        summary="整体尚可，但尾段衔接略急。",
        repair_suggestions=[],
    )
    thresholds = ShortQualityGateThresholds()
    gate_report = evaluate_short_quality_gate(report, thresholds)

    from novel_forge.pipeline.short.quality_gate import serialize_short_quality_gate_report

    payload = serialize_short_quality_gate_report(
        gate_report,
        thresholds,
        attempted_repair=False,
        best_effort_accepted=False,
    )

    assert payload["verdict"] == "warn"
    assert payload["passed"] is False