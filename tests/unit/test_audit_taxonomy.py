from __future__ import annotations

from novel_forge.core.review.audit_taxonomy import (
    DIMENSION_CHAPTER_QUALITY,
    DIMENSION_CONTINUITY,
    DIMENSION_GUARD,
    DIMENSION_READING_POWER,
    classify_review_issue,
)


def test_character_inconsistency_without_boundary_markers_is_chapter_quality() -> None:
    classification = classify_review_issue(
        issue_type="character_inconsistency",
        source_module="critic_agent",
        summary="正文直接揭示人物身份，造成章内表达突兀。",
        evidence="照片里的人影与她几乎重合。",
    )

    assert classification.primary_dimension == DIMENSION_CHAPTER_QUALITY


def test_character_inconsistency_with_boundary_markers_is_continuity() -> None:
    classification = classify_review_issue(
        issue_type="character_inconsistency",
        source_module="critic_agent",
        summary="上一章结尾的伤势状态没有在开场承接。",
    )

    assert classification.primary_dimension == DIMENSION_CONTINUITY


def test_guard_issue_source_owns_guard_dimension() -> None:
    classification = classify_review_issue(
        issue_type="guard_constraint_missing",
        source_module="guard_constraint_compliance",
        summary="AI护栏约束未兑现。",
    )

    assert classification.primary_dimension == DIMENSION_GUARD


def test_forbidden_element_issue_stays_out_of_continuity() -> None:
    classification = classify_review_issue(
        issue_type="forbidden_element_reuse",
        dimension="continuity",
        summary="复用了本章禁用的微节拍。",
    )

    assert classification.primary_dimension == DIMENSION_CHAPTER_QUALITY


def test_reading_power_issue_owns_reading_power_dimension() -> None:
    classification = classify_review_issue(
        issue_type="hook_missing",
        source_module="reading_power_eval",
        summary="章尾缺少明确钩子。",
    )

    assert classification.primary_dimension == DIMENSION_READING_POWER


def test_check_chapter_source_owns_chapter_quality_dimension() -> None:
    classification = classify_review_issue(
        issue_type="prompt_leak",
        source_module="check_chapter",
        summary="正文混入规划层元语言。",
    )

    assert classification.primary_dimension == DIMENSION_CHAPTER_QUALITY
