"""Tests for the unified chapter review orchestration layer."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.review.review_orchestration import (
    OPTION_ACCEPT,
    OPTION_APPLY_REPAIRS,
    OPTION_PAUSE,
    build_chapter_review_matrix,
    format_review_score_line,
)
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalIssue,
    CausalValidationReport,
    ChapterRepairReport,
)
from novel_forge.core.schemas.continuity import ContinuityIssue, ContinuityReport
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.reading_power import ReadingPowerReport


def _matrix(**overrides):
    payload = {
        "alignment_report": AlignmentReport(alignment_score=8.4, risk_level="low"),
        "continuity_report": ContinuityReport(continuity_score=8.8, issues=[]),
        "eval_report": EvalReport(overall_score=8.7, passed=True, threshold=6.0),
        "causal_report": CausalValidationReport(causal_score=8.9, issues=[]),
        "warnings": (),
        "repair_tickets": (),
    }
    payload.update(overrides)
    return build_chapter_review_matrix(**payload)


def test_review_matrix_uses_canonical_score_order() -> None:
    assert (
        format_review_score_line(
            alignment_score=4.0,
            continuity_score=9.2,
            overall_score=9.67,
            causal_score=9.0,
        )
        == "对齐 4.0 / 连贯 9.2 / 质量 9.7 / 因果 9.0"
    )


def test_review_matrix_routes_alignment_gap_to_text_repair() -> None:
    matrix = _matrix(
        alignment_report=AlignmentReport(
            alignment_score=5.8,
            risk_level="high",
            missing_main_points=["核心节拍缺失"],
            repair_actions=["补齐核心节拍"],
        )
    )

    assert matrix.decision.recommended_option == OPTION_APPLY_REPAIRS
    assert "补齐大纲对齐缺口" in matrix.decision.repair_option_description
    assert matrix.axis("alignment").repairable_issue_count == 1


def test_review_matrix_does_not_repair_incomplete_guard_warning() -> None:
    matrix = _matrix(
        warnings=("AI护栏约束检查未完成: 2 条检查失败；不会自动触发文本修复。",)
    )

    assert matrix.decision.recommended_option == OPTION_ACCEPT


def test_review_matrix_routes_confirmed_prompt_leak_to_repair() -> None:
    matrix = _matrix(
        current_text="她刚踏进门，正文里却混进【交接】这样的规划标记。",
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【交接】"]),
    )

    assert matrix.decision.recommended_option == OPTION_APPLY_REPAIRS
    assert OPTION_ACCEPT not in matrix.decision.allowed_option_ids
    assert "清理 1 处提示词/规划语句泄露" in matrix.decision.repair_option_description


def test_review_matrix_does_not_repair_in_world_bracketed_title() -> None:
    matrix = _matrix(
        current_text="邮件标题写着：【紧急通知】关于订单交付计划调整。",
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【紧急通知】"]),
    )

    assert matrix.decision.recommended_option == OPTION_ACCEPT
    assert OPTION_ACCEPT in matrix.decision.allowed_option_ids


def test_review_matrix_pauses_for_ambiguous_prompt_leak() -> None:
    matrix = _matrix(
        current_text="她在纸页边缘看见【未校验】三个字。",
        chapter_repair_report=ChapterRepairReport(prompt_leaks=["【未校验】"]),
    )

    assert matrix.decision.recommended_option == OPTION_PAUSE
    assert OPTION_ACCEPT not in matrix.decision.allowed_option_ids
    assert "无法确认" in matrix.decision.decision_line


def test_review_matrix_blocks_accept_when_quality_failed() -> None:
    matrix = _matrix(eval_report=EvalReport(overall_score=5.2, passed=False, threshold=6.0))

    assert matrix.decision.recommended_option == OPTION_PAUSE
    assert OPTION_ACCEPT not in matrix.decision.allowed_option_ids
    assert "质量评估未通过" in matrix.decision.decision_line


def test_review_matrix_counts_all_repairable_dimensions() -> None:
    matrix = _matrix(
        continuity_report=ContinuityReport(
            continuity_score=7.0,
            issues=[
                ContinuityIssue(
                    issue_type="state_conflict",
                    severity="critical",
                    summary="角色状态冲突",
                )
            ],
        ),
        causal_report=CausalValidationReport(
            causal_score=7.2,
            issues=[
                CausalIssue(
                    issue_type="event_without_cause",
                    severity="high",
                    summary="关键转折缺因",
                )
            ],
        ),
        repair_tickets=(
            SimpleNamespace(
                issue_type="guard_constraint_missing",
                metadata={"status": "non_compliant", "repairable": True},
            ),
        ),
    )

    assert matrix.decision.recommended_option == OPTION_APPLY_REPAIRS
    assert "连贯性问题" in matrix.decision.repair_option_description
    assert "高风险因果问题" in matrix.decision.repair_option_description
    assert "AI 护栏修复票" in matrix.decision.repair_option_description


def test_review_matrix_includes_reading_power_axis_and_score() -> None:
    matrix = _matrix(
        reading_power_report=ReadingPowerReport(
            chapter=3,
            hook_type="none",
            hook_strength="weak",
            micro_payoffs=[],
        ),
        reading_power_threshold=6.0,
    )

    assert "追读" in matrix.score_line
    assert matrix.axis("reading_power").status == "fail"
    assert matrix.axis("reading_power").repairable_issue_count >= 1
    assert matrix.decision.recommended_option == OPTION_APPLY_REPAIRS
    assert "追读力问题" in matrix.decision.repair_option_description
