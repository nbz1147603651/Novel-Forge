"""Presence contracts for LLM-authored narrative fields.

JSON Schema validates transport shape.  These checks cover the narrower
boundary that a structurally valid response must still contain the narrative
decisions requested from the model.  They deliberately do not score or infer
those decisions locally.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import FormatSchemaIssue
from novel_forge.pipeline.long.services.claim_field_policy import (
    CHARACTER_KNOWLEDGE_COVERAGE_VALUES,
    CLAIM_ENUM_VALUES,
)

_OUTLINE_TASKS = frozenset({TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE})
_EDITORIAL_STRUCTURE_TASKS = frozenset(
    {TaskType.DERIVE_EDITORIAL_CONTRACT, TaskType.DERIVE_EDITORIAL_STRUCTURE}
)
_INIT_CLAIM_TASKS = frozenset(
    {TaskType.EXTRACT_INIT_COHERENCE_CLAIMS, TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS}
)
_BOOK_CONSISTENCY_TASKS = frozenset(
    {
        TaskType.BOOK_CONSISTENCY,
        TaskType.BOOK_CONSISTENCY_NAMING,
        TaskType.BOOK_CONSISTENCY_TIMELINE,
        TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
    }
)


class TaskSemanticContractError(ValueError):
    """Raised when required LLM-authored meaning is absent from valid JSON."""

    def __init__(self, issues: Sequence[FormatSchemaIssue]) -> None:
        self.issues = tuple(issues)
        detail = "; ".join(f"{issue.path}: {issue.message}" for issue in self.issues)
        super().__init__(f"Task semantic contract failed: {detail}")


def validate_task_semantic_contract(data: dict[str, Any], task_type: TaskType) -> None:
    """Reject empty narrative decisions without inventing local replacements."""

    issues: list[FormatSchemaIssue] = []
    if task_type in _OUTLINE_TASKS:
        _collect_outline_issues(data, issues)
    if task_type in _EDITORIAL_STRUCTURE_TASKS:
        _collect_editorial_structure_issues(data, issues)
    if task_type in _INIT_CLAIM_TASKS:
        _collect_init_claim_issues(data, issues)
    if task_type == TaskType.EXTRACT_CANDIDATE_STATE_DELTAS:
        _collect_candidate_state_delta_issues(data, issues)
    if task_type == TaskType.GUARD_CONSTRAINT_CHECK:
        _collect_guard_constraint_issues(data, issues)
    if task_type == TaskType.MACRO_GUARD_AUDIT:
        _collect_macro_guard_issues(data, issues)
    if task_type in _BOOK_CONSISTENCY_TASKS:
        _collect_book_consistency_issues(
            data,
            issues,
            include_report=task_type == TaskType.BOOK_CONSISTENCY,
        )
    if task_type == TaskType.BOOK_CONSISTENCY_VERIFY:
        _collect_book_consistency_verify_issues(data, issues)
    if issues:
        raise TaskSemanticContractError(issues)


def _collect_outline_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        return
    for index, raw_chapter in enumerate(chapters):
        if not isinstance(raw_chapter, Mapping):
            continue
        base = f"$.chapters[{index}]"
        for key in ("goal", "setting", "pov_character_id"):
            _require_text(raw_chapter.get(key), f"{base}.{key}", issues)
        if not _has_text(raw_chapter.get("pov_character_name")) and not _has_text(
            raw_chapter.get("pov_character")
        ):
            _add_issue(
                issues,
                path=f"{base}.pov_character_name",
                expected="non-empty POV display name authored by the model",
                actual="empty",
                message="POV 展示名缺失；不得由本地猜测角色身份。",
            )
        _require_text_list(raw_chapter.get("beats_summary"), f"{base}.beats_summary", issues)
        _require_text_list(
            raw_chapter.get("main_plot_points"),
            f"{base}.main_plot_points",
            issues,
        )
        _require_text_list(
            raw_chapter.get("scene_design_goals"),
            f"{base}.scene_design_goals",
            issues,
            minimum=2,
        )

        emotional_plan = raw_chapter.get("emotional_plan")
        if isinstance(emotional_plan, Mapping):
            for key in (
                "subject_entity_id",
                "entry_state",
                "pressure_source",
                "relationship_choice",
                "turning_emotion",
                "exit_aftertaste",
            ):
                _require_text(emotional_plan.get(key), f"{base}.emotional_plan.{key}", issues)
            _require_text_list(
                emotional_plan.get("expression_channels"),
                f"{base}.emotional_plan.expression_channels",
                issues,
            )

        hook = raw_chapter.get("expected_hook")
        if isinstance(hook, Mapping):
            for key in ("hook_type", "hook_strength", "hook_description"):
                _require_text(hook.get(key), f"{base}.expected_hook.{key}", issues)

        payoffs = raw_chapter.get("expected_payoffs")
        if isinstance(payoffs, list):
            if not payoffs:
                _add_issue(
                    issues,
                    path=f"{base}.expected_payoffs",
                    expected="at least one LLM-authored micro-payoff",
                    actual="empty array",
                    message="每章至少需要一个由模型设计的微兑现。",
                )
            for payoff_index, payoff in enumerate(payoffs):
                if not isinstance(payoff, Mapping):
                    continue
                payoff_base = f"{base}.expected_payoffs[{payoff_index}]"
                _require_text(payoff.get("payoff_type"), f"{payoff_base}.payoff_type", issues)
                _require_text(payoff.get("description"), f"{payoff_base}.description", issues)


def _collect_editorial_structure_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    title_policy = data.get("title_policy")
    if isinstance(title_policy, Mapping):
        _require_text(
            title_policy.get("naming_strategy"),
            "$.title_policy.naming_strategy",
            issues,
        )


def _collect_init_claim_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    claims = data.get("claims")
    if not isinstance(claims, list):
        return
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            continue
        base = f"$.claims[{index}]"
        for key, valid_values in CLAIM_ENUM_VALUES.items():
            value = claim.get(key)
            if isinstance(value, str) and value.strip().lower() in valid_values:
                continue
            _add_issue(
                issues,
                path=f"{base}.{key}",
                expected=f"one of {sorted(valid_values)}",
                actual=repr(value),
                message="模型必须明确给出合法枚举，不能由本地降级为默认语义。",
            )
        coverage = claim.get("character_knowledge_coverage")
        if isinstance(coverage, Mapping):
            for character, value in coverage.items():
                if (
                    isinstance(value, str)
                    and value.strip().lower() in CHARACTER_KNOWLEDGE_COVERAGE_VALUES
                ):
                    continue
                _add_issue(
                    issues,
                    path=f"{base}.character_knowledge_coverage.{character}",
                    expected="one of ['unknown', 'partial', 'full']",
                    actual=repr(value),
                    message="角色知情程度必须由模型重新裁定。",
                )
        else:
            _add_issue(
                issues,
                path=f"{base}.character_knowledge_coverage",
                expected="object with explicit awareness values",
                actual=type(coverage).__name__,
                message="角色知情映射缺失或类型错误。",
            )
        confidence = claim.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not 0.0 <= float(confidence) <= 1.0
        ):
            _add_issue(
                issues,
                path=f"{base}.confidence",
                expected="number between 0 and 1",
                actual=repr(confidence),
                message="置信度必须由模型明确输出，不能在本地重置为 0.5。",
            )
        for key, expected_type in (
            ("irreversible", bool),
            ("cognitive_subjects", list),
            ("foreshadow_chapters", list),
        ):
            if isinstance(claim.get(key), expected_type):
                continue
            _add_issue(
                issues,
                path=f"{base}.{key}",
                expected=expected_type.__name__,
                actual=type(claim.get(key)).__name__,
                message="模型输出缺少必需的声明字段或类型不正确。",
            )
        for key in ("cognitive_object", "cognitive_chapter", "public_reveal_chapter"):
            if key in claim:
                continue
            _add_issue(
                issues,
                path=f"{base}.{key}",
                expected="explicit field (nullable where allowed)",
                actual="missing",
                message="必需字段必须由模型显式返回。",
            )


def _collect_candidate_state_delta_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        base = f"$.candidates[{index}]"
        _require_text(candidate.get("summary"), f"{base}.summary", issues)
        proposed_delta = candidate.get("proposed_delta")
        if not isinstance(proposed_delta, Mapping) or not proposed_delta:
            _add_issue(
                issues,
                path=f"{base}.proposed_delta",
                expected="non-empty model-authored state delta",
                actual="empty or invalid",
                message="候选状态变化必须包含模型提取的具体变更。",
            )
        evidence = candidate.get("evidence")
        if not isinstance(evidence, list) or not any(
            isinstance(item, Mapping) and _has_text(item.get("quote")) for item in evidence
        ):
            _add_issue(
                issues,
                path=f"{base}.evidence",
                expected="at least one evidence quote",
                actual="empty or invalid",
                message="候选状态变化必须带有正文证据。",
            )


def _collect_guard_constraint_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    _require_text(data.get("status"), "$.status", issues)
    _require_text(data.get("evidence"), "$.evidence", issues)
    _require_unit_interval(data.get("confidence"), "$.confidence", issues)


def _collect_macro_guard_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    _require_text(data.get("recommended_action"), "$.recommended_action", issues)
    _require_text(data.get("reasoning"), "$.reasoning", issues)
    _require_unit_interval(data.get("drift_score"), "$.drift_score", issues)
    _require_unit_interval(data.get("confidence"), "$.confidence", issues)
    dimensions = data.get("dimensions")
    if isinstance(dimensions, Mapping):
        for key in (
            "outline_alignment",
            "character_arc_consistency",
            "pacing_curve",
            "foreshadowing_recovery",
            "thematic_cohesion",
        ):
            _require_unit_interval(dimensions.get(key), f"$.dimensions.{key}", issues)
    findings = data.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, Mapping):
                continue
            base = f"$.findings[{index}]"
            for key in ("severity", "type", "description"):
                _require_text(finding.get(key), f"{base}.{key}", issues)
            _require_text_list(finding.get("evidence"), f"{base}.evidence", issues)


def _require_unit_interval(
    value: Any,
    path: str,
    issues: list[FormatSchemaIssue],
) -> None:
    if (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and 0.0 <= float(value) <= 1.0
    ):
        return
    _add_issue(
        issues,
        path=path,
        expected="number between 0 and 1",
        actual=repr(value),
        message="质量裁判分值必须由模型明确输出，不能使用本地健康默认值。",
    )


def _collect_book_consistency_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
    *,
    include_report: bool,
) -> None:
    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list):
        return
    for index, issue in enumerate(raw_issues):
        if not isinstance(issue, Mapping):
            continue
        base = f"$.issues[{index}]"
        for key in (
            "issue_id",
            "category",
            "severity",
            "issue_type",
            "location",
            "evidence",
            "description",
            "suggestion",
            "fix_mode",
            "fix_action",
        ):
            _require_text(issue.get(key), f"{base}.{key}", issues)
        _require_unit_interval(issue.get("confidence"), f"{base}.confidence", issues)
        chapters = issue.get("chapters_involved")
        if not isinstance(chapters, list) or not any(
            isinstance(chapter, int) and not isinstance(chapter, bool) and chapter > 0
            for chapter in chapters
        ):
            _add_issue(
                issues,
                path=f"{base}.chapters_involved",
                expected="at least one positive chapter number",
                actual=repr(chapters),
                message="一致性问题必须定位到具体章节。",
            )
        primary_chapter = issue.get("primary_chapter")
        if (
            isinstance(primary_chapter, bool)
            or not isinstance(primary_chapter, int)
            or primary_chapter <= 0
        ):
            _add_issue(
                issues,
                path=f"{base}.primary_chapter",
                expected="positive chapter number",
                actual=repr(primary_chapter),
                message="一致性问题必须明确主修复章节。",
            )
        evidence_pairs = issue.get("evidence_pairs")
        if isinstance(evidence_pairs, list):
            for pair_index, pair in enumerate(evidence_pairs):
                if not isinstance(pair, Mapping):
                    continue
                pair_base = f"{base}.evidence_pairs[{pair_index}]"
                _require_text(pair.get("evidence"), f"{pair_base}.evidence", issues)
                _require_text(pair.get("claim"), f"{pair_base}.claim", issues)

    if not include_report:
        return
    _require_text(data.get("summary"), "$.summary", issues)
    _require_number_range(data.get("consistency_score"), "$.consistency_score", issues, 0, 10)
    repair_plan = data.get("repair_plan")
    if isinstance(repair_plan, list):
        for index, plan in enumerate(repair_plan):
            if not isinstance(plan, Mapping):
                continue
            base = f"$.repair_plan[{index}]"
            _require_text(plan.get("priority"), f"{base}.priority", issues)
            _require_text(plan.get("strategy"), f"{base}.strategy", issues)


def _collect_book_consistency_verify_issues(
    data: Mapping[str, Any],
    issues: list[FormatSchemaIssue],
) -> None:
    verified = data.get("verified_issues")
    if not isinstance(verified, list):
        return
    for index, issue in enumerate(verified):
        if not isinstance(issue, Mapping):
            continue
        base = f"$.verified_issues[{index}]"
        for key in (
            "issue_id",
            "status",
            "description",
            "severity",
            "evidence",
            "location",
            "anchor_type",
            "fix_mode",
            "fix_action",
        ):
            _require_text(issue.get(key), f"{base}.{key}", issues)
        _require_unit_interval(issue.get("confidence"), f"{base}.confidence", issues)
        _require_unit_interval(
            issue.get("location_confidence"),
            f"{base}.location_confidence",
            issues,
        )


def _require_number_range(
    value: Any,
    path: str,
    issues: list[FormatSchemaIssue],
    minimum: float,
    maximum: float,
) -> None:
    if (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and minimum <= float(value) <= maximum
    ):
        return
    _add_issue(
        issues,
        path=path,
        expected=f"number between {minimum} and {maximum}",
        actual=repr(value),
        message="审计评分必须由模型明确给出，不能按问题数量本地推算。",
    )


def _require_text(value: Any, path: str, issues: list[FormatSchemaIssue]) -> None:
    if _has_text(value):
        return
    _add_issue(
        issues,
        path=path,
        expected="non-empty LLM-authored text",
        actual="empty",
        message="缺少模型应明确给出的叙事语义。",
    )


def _require_text_list(
    value: Any,
    path: str,
    issues: list[FormatSchemaIssue],
    *,
    minimum: int = 1,
) -> None:
    if isinstance(value, list) and sum(1 for item in value if _has_text(item)) >= minimum:
        return
    _add_issue(
        issues,
        path=path,
        expected=f"at least {minimum} non-empty LLM-authored item(s)",
        actual="empty or insufficient",
        message="模型未提供足量的可执行叙事信息。",
    )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _add_issue(
    issues: list[FormatSchemaIssue],
    *,
    path: str,
    expected: str,
    actual: str,
    message: str,
) -> None:
    issues.append(
        FormatSchemaIssue(
            path=path,
            issue_type="missing_semantics",
            expected=expected,
            actual=actual,
            message=message,
        )
    )


__all__ = ["TaskSemanticContractError", "validate_task_semantic_contract"]
