"""Structured replan context for plan-stage failure feedback.

When a chapter is replanned, the plan LLM receives a
:class:`ReplanContext` summarising why the previous attempt failed and
what to try differently.  This replaces the old approach of injecting
unstructured diagnostic strings into chapter_outline.notes, which the
planner could not act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.exceptions import ConsistencyViolationError


@dataclass(frozen=True)
class ReplanContext:
    """Structured replan guidance passed to the plan step.

    Attributes:
        attempt_number: Which replan attempt this is (1-based).
        previous_plan_summary: Compressed summary of the previous plan's
            scenes, used to avoid regenerating identical content.
        failed_stage: Name of the pipeline stage that failed
            (e.g. ``"wave"``, ``"draft"``, ``"check"``).
        failure_kind: The ``violation_kind`` from the
            :class:`ConsistencyViolationError` (e.g.
            ``"wave_post_condition"``, ``"contract_violation"``).
        failure_details: Specific items that failed (e.g. which scene
            anchors were missing, which contract clauses were breached).
        attempted_fixes: Descriptions of fixes already tried across
            prior replan rounds, so the planner does not repeat them.
        actionable_guidance: Concrete suggestions for the planner, derived
            from the failure pattern.
    """

    attempt_number: int = 0
    previous_plan_summary: str = ""
    failed_stage: str = ""
    failure_kind: str = ""
    failure_details: list[str] = field(default_factory=list)
    attempted_fixes: list[str] = field(default_factory=list)
    actionable_guidance: list[str] = field(default_factory=list)


def _guidance_for_failure(
    *,
    failure_kind: str,
    failure_details: list[str],
) -> list[str]:
    """Generate actionable guidance strings keyed off the failure pattern."""
    if failure_kind == "wave_post_condition":
        return [
            "WAVE 编织阶段锚点缺失 — 在每个 scene_intent 的 summary 中使用具体名词短语（人名/地名/物品/动作），"
            "避免抽象描述，让 WAVE 能从 summary 中锚定到正文。",
            "减少 scene 数量或合并相邻 scene，确保每个 scene 都有独特的实体锚点。",
            "在 opening_bridge 或 handoff_to_next 中加入跨场景的具体实体名。",
        ]
    if failure_kind == "contract_violation":
        details = "；".join(failure_details[:3]) if failure_details else "未指定"
        return [
            f"契约违反：{details} — 调整对应 scene 的 owned_events / owned_revelations"
            " / owned_state_changes 避免越界。",
            "检查 forbidden_changes 边界，确保新方案不引入被禁止的人物/事件/状态变化。",
        ]
    if failure_kind == "alignment_failure":
        details = "；".join(failure_details[:3]) if failure_details else "未指定"
        return [
            f"对齐失败：{details} — 补全覆盖缺失的 main_plot_points，"
            "确保每个 plan 元素都有对应的 scene_intent。",
        ]
    if failure_kind == "upstream_plan_conflict":
        details = "；".join(failure_details[:3]) if failure_details else "未指定"
        return [
            f"上游证据冲突：{details}",
            "只调整精确靶点所指向的 Plan 字段；大纲是只读参考，不得让正文迁就错误期限。",
            "生成候选后重新运行 upstream_compass.duration_consistency；未通过时换策略，"
            "不得重复相同修复。",
        ]
    return [
        "参考上次失败的具体细节调整方案；不要简单重做。",
    ]


def build_replan_context(
    *,
    exc: ConsistencyViolationError,
    previous_plan: Any | None,
    replan_history: list[Any] | None,
) -> ReplanContext:
    """Assemble a :class:`ReplanContext` from the exception and history.

    Args:
        exc: The :class:`ConsistencyViolationError` that triggered the replan.
        previous_plan: The previous ``ChapterPlan`` object (or ``None``).
        replan_history: Cross-run persistent replan history.
    """
    history = list(replan_history or [])
    attempt_number = len(history) + 1

    # Summarize the previous plan's scenes for the planner.
    plan_summary = ""
    if previous_plan is not None:
        scenes = getattr(previous_plan, "scene_intents", None) or []
        if scenes:
            lines: list[str] = []
            for scene in list(scenes)[:10]:
                sid = getattr(scene, "scene_id", "?")
                summary = getattr(scene, "summary", "")
                pov = getattr(scene, "pov_character", "")
                lines.append(f"  {sid} (POV: {pov}): {summary}")
            plan_summary = "\n".join(lines)

    violations = list(getattr(exc, "violations", []) or [])
    failed_stage = str(getattr(exc, "failed_stage", "") or "")
    failure_kind = str(getattr(exc, "violation_kind", "") or "")

    attempted_fixes = []
    for entry in history:
        entry_attempt = int(getattr(entry, "attempt_number", 0) or 0)
        entry_stage = str(getattr(entry, "failed_stage", "") or "未指定阶段")
        entry_kind = str(getattr(entry, "failure_kind", "") or "未指定类型")
        attempted_fixes.append(f"第 {entry_attempt or '?'} 次：{entry_stage} / {entry_kind}")

    guidance = _guidance_for_failure(
        failure_kind=failure_kind,
        failure_details=violations,
    )

    return ReplanContext(
        attempt_number=attempt_number,
        previous_plan_summary=plan_summary,
        failed_stage=failed_stage,
        failure_kind=failure_kind,
        failure_details=violations,
        attempted_fixes=attempted_fixes,
        actionable_guidance=guidance,
    )
