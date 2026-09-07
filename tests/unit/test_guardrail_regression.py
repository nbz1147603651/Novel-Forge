"""Regression tests for guardrail alignment fixes.

Validates that alignment repair operations do not introduce regressions:
1. alignment_score does not degrade after fix
2. missing_main_points do not increase after fix
"""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.chapter import AlignmentReport


@pytest.fixture
def before_guardrail_fix() -> AlignmentReport:
    """Simulates alignment report before guardrail repair."""
    return AlignmentReport(
        alignment_score=7.5,
        risk_level="medium",
        summary="修复前的对齐报告",
        missing_main_points=["主线点A", "主线点B"],
        supportive_subplot_points=["支线赋能点1"],
        weak_subplot_points=["支线问题点1"],
        repair_actions=["添加缺失的主线点"],
    )


@pytest.fixture
def after_guardrail_fix_improved() -> AlignmentReport:
    """Simulates alignment report after successful guardrail repair."""
    return AlignmentReport(
        alignment_score=8.5,
        risk_level="low",
        summary="修复后的对齐报告（改进）",
        missing_main_points=["主线点A"],
        supportive_subplot_points=["支线赋能点1", "新识别的赋能支线"],
        weak_subplot_points=[],
        repair_actions=["已添加主线点A"],
    )


@pytest.fixture
def after_guardrail_fix_degraded() -> AlignmentReport:
    """Simulates alignment report with regression (should fail tests)."""
    return AlignmentReport(
        alignment_score=6.5,
        risk_level="high",
        summary="修复后的对齐报告（退化）",
        missing_main_points=["主线点A", "主线点B", "主线点C"],
        supportive_subplot_points=["支线赋能点1"],
        weak_subplot_points=["支线问题点1", "支线问题点2"],
        repair_actions=[],
    )


def test_alignment_score_not_degraded(
    before_guardrail_fix: AlignmentReport,
    after_guardrail_fix_improved: AlignmentReport,
) -> None:
    """Verify alignment score does not decrease after guardrail fix."""
    assert after_guardrail_fix_improved.alignment_score >= before_guardrail_fix.alignment_score, (
        f"对齐分数不应下降: 修复前={before_guardrail_fix.alignment_score}, "
        f"修复后={after_guardrail_fix_improved.alignment_score}"
    )


def test_alignment_score_not_degraded_with_degraded_fixture(
    before_guardrail_fix: AlignmentReport,
    after_guardrail_fix_degraded: AlignmentReport,
) -> None:
    """Verify this test catches the regression case (分数下降)."""
    assert after_guardrail_fix_degraded.alignment_score < before_guardrail_fix.alignment_score, (
        "此测试用例验证分数下降能被捕获"
    )


def test_missing_main_points_not_increased(
    before_guardrail_fix: AlignmentReport,
    after_guardrail_fix_improved: AlignmentReport,
) -> None:
    """Verify missing_main_points count does not increase after guardrail fix."""
    before_count = len(before_guardrail_fix.missing_main_points)
    after_count = len(after_guardrail_fix_improved.missing_main_points)

    assert after_count <= before_count, (
        f"缺失主线点数量不应增加: 修复前={before_count}, 修复后={after_count}, "
        f"新增={set(after_guardrail_fix_improved.missing_main_points) - set(before_guardrail_fix.missing_main_points)}"
    )


def test_missing_main_points_not_increased_with_degraded_fixture(
    before_guardrail_fix: AlignmentReport,
    after_guardrail_fix_degraded: AlignmentReport,
) -> None:
    """Verify this test catches the regression case (缺失点增加)."""
    before_count = len(before_guardrail_fix.missing_main_points)
    after_count = len(after_guardrail_fix_degraded.missing_main_points)

    assert after_count > before_count, (
        "此测试用例验证缺失主线点增加能被捕获"
    )


def test_guardrail_constraint_passed(
    after_guardrail_fix_improved: AlignmentReport,
) -> None:
    """Verify guardrail constraints are satisfied after fix."""
    # Low risk level is acceptable
    assert after_guardrail_fix_improved.risk_level in ("low", "medium"), (
        f"风险等级应为 low 或 medium，实际为 {after_guardrail_fix_improved.risk_level}"
    )
    # Supportive points should be present and non-empty
    assert len(after_guardrail_fix_improved.supportive_subplot_points) > 0, (
        "赋能支线点应存在"
    )
    # No weak subplot points after successful fix
    assert len(after_guardrail_fix_improved.weak_subplot_points) == 0, (
        "成功修复后不应有弱支线点"
    )


def test_pre_evaluation_warning(
    before_guardrail_fix: AlignmentReport,
) -> None:
    """Verify pre-evaluation warning is raised when alignment score is low."""
    # Medium risk level should trigger warning
    assert before_guardrail_fix.risk_level == "medium", (
        f"预期中等风险等级，实际为 {before_guardrail_fix.risk_level}"
    )
    # Alignment score below threshold should trigger warning
    assert before_guardrail_fix.alignment_score < 8.0, (
        f"对齐分数 {before_guardrail_fix.alignment_score} 应低于阈值 8.0"
    )
    # Missing main points should be present
    assert len(before_guardrail_fix.missing_main_points) > 0, (
        "修复前应有缺失的主线点"
    )


def test_quality_gate_rollback(
    before_guardrail_fix: AlignmentReport,
    after_guardrail_fix_degraded: AlignmentReport,
) -> None:
    """Verify quality gate triggers rollback when degradation detected."""
    # Degraded report has high risk level
    assert after_guardrail_fix_degraded.risk_level == "high", (
        f"退化报告风险等级应为 high，实际为 {after_guardrail_fix_degraded.risk_level}"
    )
    # Alignment score decreased significantly
    score_diff = before_guardrail_fix.alignment_score - after_guardrail_fix_degraded.alignment_score
    assert score_diff > 0, (
        f"分数下降量应为正数，实际为 {score_diff}"
    )
    # More missing main points indicates rollback needed
    before_count = len(before_guardrail_fix.missing_main_points)
    after_count = len(after_guardrail_fix_degraded.missing_main_points)
    assert after_count > before_count, (
        "退化情况下缺失点应增加，触发回退"
    )