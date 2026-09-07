"""Narrow, evidence-anchored repair for blocking world-rule conflicts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.schemas.world_rules import (
    WorldRuleApplication,
    WorldRuleCard,
    WorldRuleComplianceReport,
)
from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput
from novel_forge.pipeline.steps.repair.patch_bridge import PatchCompatIssue


@dataclass(frozen=True)
class WorldRulePatchResult:
    """Outcome of a constrained world-rule patch attempt."""

    text: str
    attempted_rule_ids: tuple[str, ...]
    patches_attempted: int = 0
    patches_applied: int = 0
    fallback: bool = False
    skip_reason: str = ""
    failure_kind: str = ""
    failure_reason: str = ""
    error_type: str = ""

    @property
    def changed(self) -> bool:
        return self.patches_applied > 0 and not self.fallback


def _patch_issue(
    *,
    issue: Any,
    rule: Any,
    application: WorldRuleApplication | None,
) -> PatchCompatIssue | None:
    evidence = str(getattr(issue, "evidence", "") or "").strip()
    if not evidence:
        return None
    required_context = [
        f"[{rule.rule_id}] {rule.content}",
        *(f"触发条件：{item}" for item in list(rule.trigger_conditions or [])),
        *(f"禁止行为：{item}" for item in list(rule.forbidden_behavior or [])),
        *(f"代价/后果：{item}" for item in list(rule.cost_or_consequence or [])),
    ]
    if application is not None:
        if application.usage:
            required_context.append(f"计划中的遵守方式：{application.usage}")
        if application.expected_evidence:
            required_context.append(f"正文必须可见的证据：{application.expected_evidence}")
        if application.forbidden_boundary:
            required_context.append(f"不可跨越的边界：{application.forbidden_boundary}")
    return PatchCompatIssue(
        issue_id=f"world_rule_{rule.rule_id}",
        severity=str(getattr(issue, "severity", "high") or "high"),
        summary=str(getattr(issue, "summary", "") or f"世界规则冲突：{rule.content}"),
        evidence=evidence,
        evidence_quote=evidence,
        issue_type="world_rule_conflict",
        repair_surface="chapter_text",
        fix_mode="replace",
        fix_suggestion=(
            "只改写定位原句或相邻句，使其满足以下世界规则和计划中的可验证证据；"
            "不要改变已发生事件、角色认知或章节目标。"
        ),
        repair_directive={
            "repair_strategy": "以最小文本改动消除世界规则冲突，并保留当前叙事结果。",
            "required_context": required_context,
        },
    )


async def patch_blocking_world_rule_conflicts(
    *,
    router: Any,
    builder: Any,
    settings: Any,
    trace: Any,
    chapter_number: int,
    current_text: str,
    card: WorldRuleCard,
    applications: list[WorldRuleApplication | dict[str, Any]],
    report: WorldRuleComplianceReport,
    style_profile: Any = None,
) -> WorldRulePatchResult:
    """Patch only hard conflicts that have exact in-text evidence.

    Refusing to infer a location is intentional: a world-rule repair without an
    exact anchor must be escalated as a review failure, never converted into a
    blind whole-chapter rewrite or a new plan.
    """

    rule_by_id = {entry.rule.rule_id: entry.rule for entry in card.all_entries}
    application_by_id: dict[str, WorldRuleApplication] = {}
    for value in applications:
        application = (
            value
            if isinstance(value, WorldRuleApplication)
            else WorldRuleApplication.model_validate(value)
        )
        application_by_id[application.rule_id] = application

    patch_issues: list[PatchCompatIssue] = []
    attempted_rule_ids: list[str] = []
    for issue in report.issues:
        if issue.verdict != "conflict" or issue.severity not in {"critical", "high"}:
            continue
        rule = rule_by_id.get(issue.rule_id)
        if rule is None:
            continue
        patch_issue = _patch_issue(
            issue=issue,
            rule=rule,
            application=application_by_id.get(issue.rule_id),
        )
        if patch_issue is None or patch_issue.evidence_quote not in current_text:
            continue
        patch_issues.append(patch_issue)
        attempted_rule_ids.append(issue.rule_id)

    if not patch_issues:
        return WorldRulePatchResult(
            text=current_text,
            attempted_rule_ids=tuple(),
            skip_reason="blocking_world_rule_conflict_has_no_exact_text_anchor",
        )

    result = await ChapterPatchStep(router, builder, settings=settings, trace=trace).run(
        PatchInput(
            chapter_number=chapter_number,
            chapter_text=current_text,
            issues=patch_issues,
            context_size=1,
            must_fix_summaries=[issue.summary for issue in patch_issues],
            style_profile=style_profile,
            kernel_context={
                "world_rules": [
                    rule_by_id[rule_id].model_dump(mode="json") for rule_id in attempted_rule_ids
                ]
            },
            propagate_failure=True,
        )
    )
    skip_reason = ""
    if result.fallback:
        skip_reason = "patch_execution_failed"
    elif result.patches_attempted == 0:
        skip_reason = "patch_model_returned_no_patches"
    elif result.patches_applied == 0:
        skip_reason = "patches_not_applied"
    return WorldRulePatchResult(
        text=result.revised_text,
        attempted_rule_ids=tuple(attempted_rule_ids),
        patches_attempted=result.patches_attempted,
        patches_applied=result.patches_applied,
        fallback=result.fallback,
        skip_reason=skip_reason,
        failure_kind=str(getattr(result, "failure_kind", "") or ""),
        failure_reason=str(getattr(result, "failure_reason", "") or ""),
        error_type=str(getattr(result, "error_type", "") or ""),
    )
