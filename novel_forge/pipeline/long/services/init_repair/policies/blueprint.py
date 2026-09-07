"""Narrative blueprint repair policy."""

from __future__ import annotations

import json
import logging
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    apply_local_blueprint_structural_fallback,
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.blueprint.blueprint_validation import validate_blueprint
from novel_forge.pipeline.long.services.init_repair.models import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairReport,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

_log = logging.getLogger(__name__)


class BlueprintRepairPolicy:
    """Repair strategy for NarrativeBlueprint artifacts.

    Blueprint is a structured planning artifact, so its policy is allowed to
    synthesize minimal executable subplots and suspense schedules when LLM
    repair omits them. Higher-semantics artifacts should use stricter policies.
    """

    artifact = InitArtifact.BLUEPRINT

    def normalize(self, payload: Any, ctx: InitRepairContext) -> NarrativeBlueprint:
        return _model_from_payload(payload, total_chapters=ctx.total_chapters)

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        blueprint = self.normalize(payload, ctx)
        result = validate_blueprint(
            blueprint,
            total_chapters=ctx.total_chapters,
            narrative_complexity=ctx.narrative_complexity,
        )
        return InitRepairReport(
            errors=list(result.errors),
            warnings=list(result.warnings),
            suggestions=list(result.suggestions),
            issues=_classify_blueprint_issues(
                errors=list(result.errors),
                warnings=list(result.warnings),
                suggestions=list(result.suggestions),
            ),
            raw=result,
        )

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> NarrativeBlueprint:
        blueprint_data = _dump_blueprint_payload(payload)
        repaired_data = await _call_blueprint_llm_repair(
            blueprint_data=blueprint_data,
            validation_errors=report.errors,
            ctx=ctx,
        )
        for soft_key in {"suspense_schedule"}:
            if soft_key in _repair_fields(report) and soft_key not in repaired_data:
                repaired_data[soft_key] = []

        merged = _merge_repaired_blueprint(blueprint_data, repaired_data)
        normalized = pre_normalize_blueprint_payload(merged, total_chapters=ctx.total_chapters)
        alignment_errors = _validate_chapter_alignment(normalized)
        if alignment_errors:
            _log.warning("blueprint_repair_alignment_warnings | errors=%s", alignment_errors)
        return _model_from_payload(normalized, total_chapters=ctx.total_chapters)

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> NarrativeBlueprint | None:
        local_payload = apply_local_blueprint_structural_fallback(
            _dump_blueprint_payload(payload),
            total_chapters=ctx.total_chapters,
            narrative_complexity=ctx.narrative_complexity,
            validation_errors=report.errors,
        )
        if local_payload is None:
            return None
        return _model_from_payload(local_payload, total_chapters=ctx.total_chapters)


def _model_from_payload(payload: Any, *, total_chapters: int) -> NarrativeBlueprint:
    if isinstance(payload, NarrativeBlueprint):
        payload = payload.model_dump(mode="json")
    normalized = pre_normalize_blueprint_payload(payload, total_chapters=total_chapters)
    return NarrativeBlueprint.model_validate(normalized)


def _dump_blueprint_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, NarrativeBlueprint):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return dict(payload)
    return NarrativeBlueprint.model_validate(payload).model_dump(mode="json")


async def _call_blueprint_llm_repair(
    *,
    blueprint_data: dict[str, Any],
    validation_errors: list[str],
    ctx: InitRepairContext,
) -> dict[str, Any]:
    service_ctx = ctx.service_ctx
    settings = service_ctx.settings
    blueprint_max_tokens = calculate_route_aware_max_tokens(
        service_ctx.router,
        TaskType.PLAN_OUTLINE,
        max(2400, ctx.total_chapters * 120),
        prompt_overhead=5000,
        min_tokens=2048,
    )

    error_summary = "\n".join(f"- {error}" for error in validation_errors)
    repair_fields = _repair_fields(validation_errors) or ["synopsis"]
    guidance_text = _repair_guidance(validation_errors, total_chapters=ctx.total_chapters)

    repair_ctx = {
        **ctx.outline_ctx,
        "current_blueprint": blueprint_data,
        "validation_errors": error_summary,
        "blueprint_generation_mode": "quality_first_expansion",
        "blueprint_fragment_request": {
            "block_key": "repair",
            "title": "蓝图修复片段",
            "required_keys": repair_fields,
            "instructions": [
                "只输出本轮修复字段；未列入字段不得输出。",
                f"新增支线的 involved_chapters 必须与现有叙事阶段章节范围对齐（1-{ctx.total_chapters}）。",
                "支线交织必须包含 trigger_start（主线到支线）和 feed_main/reveal_key（支线反哺主线）。",
                "悬念必须包含 strand_affinity 字段，且包含 quest、fire、constellation 三个 key。",
            ],
        },
    }
    prior_messages = [
        {
            "role": "assistant",
            "content": json.dumps(blueprint_data, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                f"蓝图验证发现以下问题，请按本轮统一格式契约输出修复片段。\n\n"
                f"## 验证错误\n{error_summary}\n\n"
                f"## 修复指引\n{guidance_text or '按验证错误修复对应字段。'}\n\n"
                "未列入本轮片段契约的字段不要输出。"
            ),
        },
    ]

    _log.info(
        "blueprint_repair_attempt | errors=%s | fields=%s",
        validation_errors,
        repair_fields,
    )
    repaired = await service_ctx.call_with_retry(
        TaskType.PLAN_OUTLINE,
        repair_ctx,
        max_tokens=blueprint_max_tokens,
        temperature=min(settings.temp_plan_outline, 0.4),
        required_keys=tuple(repair_fields),
        thinking=ctx.outline_thinking,
        prior_messages=prior_messages,
        include_contract_required_keys=False,
    )
    if not isinstance(repaired, dict):
        raise TypeError(
            "blueprint LLM repair returned non-object payload: "
            f"{type(repaired).__name__}"
        )
    return repaired


def _repair_guidance(validation_errors: list[str], *, total_chapters: int) -> str:
    guidance = {
        "支线数量不足": "添加新的支线规划条目，每条支线需包含：name, description, involved_chapters（章节列表，必须在 1-{total_chapters} 范围内）, chapter_events（≥3个事件）, weave_links（≥2条，必须包含 trigger_start 和 feed_main/reveal_key）, priority, resolution_chapter, resolution_target, resolution_type",
        "悬念时间表为空": '添加悬念时间表条目，每条悬念需包含：suspense_id, suspense_type（mystery/crisis/emotion/choice/desire）, introduce_chapter, resolve_chapter, description, urgency_level（critical/high/normal/low）, related_subplot, strand_affinity（{{"quest": 0.3, "fire": 0.1, "constellation": 0.6}}）',
        "synopsis 为空": "用2-4句话概括故事主线、核心冲突与结局方向",
        "narrative_phases 为空": "添加3-6个叙事阶段，每个阶段需包含：phase_name, chapter_start, chapter_end, description, key_events, tension_level",
        "叙事阶段存在间隙": "调整 chapter_start/chapter_end 确保连续覆盖所有章节，无间隙无重叠",
        "分卷": "调整 volumes，使其连续覆盖 1-{total_chapters} 章；每卷包含 volume_number, title, start_chapter, end_chapter, arc_goal",
        "文本引用": "分卷、叙事阶段、角色里程碑文本中的“第N章”必须落在自身章节区间内；无法确定时改写为“本卷前段/本阶段后段/该里程碑阶段”等相对位置，不要输出越界章节号",
        "关键转折": "调整 key_turning_points，使 chapter_number 落在 1-{total_chapters} 章内，并保留 description, location, characters_involved",
    }
    return "\n".join(
        f"【{key}】{value.format(total_chapters=total_chapters)}"
        for key, value in guidance.items()
        if any(key in error for error in validation_errors)
    )


def _repair_fields(report_or_errors: InitRepairReport | list[str]) -> list[str]:
    if isinstance(report_or_errors, InitRepairReport):
        issues = report_or_errors.issues
        errors = report_or_errors.errors
    else:
        issues = []
        errors = report_or_errors

    fields: list[str] = []
    issue_fields = {issue.field for issue in issues if issue.field}
    if "subplot_plan" in issue_fields or any("支线" in error for error in errors):
        fields.append("subplot_plan")
    if "suspense_schedule" in issue_fields or any("悬念" in error for error in errors):
        fields.append("suspense_schedule")
    if "synopsis" in issue_fields or any("synopsis" in error for error in errors):
        fields.append("synopsis")
    if "narrative_phases" in issue_fields or any(
        "narrative_phases" in error
        or "间隙" in error
        or ("文本引用" in error and "叙事阶段" in error)
        for error in errors
    ):
        fields.append("narrative_phases")
    if "volumes" in issue_fields or any(
        "文本引用" in error and "分卷" in error for error in errors
    ):
        fields.append("volumes")
    if "character_arcs" in issue_fields or any(
        "文本引用" in error and "角色弧光" in error for error in errors
    ):
        fields.append("character_arcs")
    if "key_turning_points" in issue_fields:
        fields.append("key_turning_points")
    return list(dict.fromkeys(fields))


def _classify_blueprint_issues(
    *,
    errors: list[str],
    warnings: list[str],
    suggestions: list[str],
) -> list[InitRepairIssue]:
    issues = [
        _classify_blueprint_message(message, severity="error")
        for message in errors
    ]
    issues.extend(
        _classify_blueprint_message(message, severity="warning")
        for message in warnings
    )
    issues.extend(
        _classify_blueprint_message(message, severity="suggestion")
        for message in suggestions
    )
    return issues


def _classify_blueprint_message(message: str, *, severity: str) -> InitRepairIssue:
    field = _blueprint_issue_field(message)
    kind = _blueprint_issue_kind(message, field=field)
    return InitRepairIssue(kind=kind, message=message, field=field, severity=severity)


def _blueprint_issue_field(message: str) -> str:
    if "synopsis" in message:
        return "synopsis"
    if "narrative_phases" in message or "叙事阶段" in message:
        return "narrative_phases"
    if "支线" in message or "weave link" in message:
        return "subplot_plan"
    if "悬念" in message or "suspense" in message:
        return "suspense_schedule"
    if "分卷" in message or "volume" in message:
        return "volumes"
    if "角色弧光" in message or "character_arc" in message:
        return "character_arcs"
    if "关键转折" in message or "turning" in message:
        return "key_turning_points"
    return ""


def _blueprint_issue_kind(message: str, *, field: str) -> InitRepairIssueKind:
    if any(marker in message for marker in ("非法", "格式非法", "字段", "source_type", "link_type")):
        return InitRepairIssueKind.FORMAT_ALIAS
    if any(
        marker in message
        for marker in ("间隙", "覆盖", "越界", "倒置", "重叠", "不连续", "文本引用")
    ):
        return InitRepairIssueKind.RANGE_OR_COVERAGE
    if "指向不存在" in message or "关联了不存在" in message:
        return InitRepairIssueKind.CROSS_ARTIFACT_DRIFT
    if field in {"subplot_plan", "suspense_schedule"} and any(
        marker in message for marker in ("为空", "数量不足", "缺少", "少于")
    ):
        return InitRepairIssueKind.MISSING_EXECUTION_GRAPH
    if any(marker in message for marker in ("为空", "缺少")):
        return InitRepairIssueKind.SCHEMA_SHAPE
    return InitRepairIssueKind.SEMANTIC_CONFLICT


def _merge_repaired_blueprint(original: dict[str, Any], repaired: dict[str, Any]) -> dict[str, Any]:
    merged = {**original}
    for key in (
        "subplot_plan",
        "suspense_schedule",
        "character_arcs",
        "key_turning_points",
        "synopsis",
        "narrative_phases",
        "volumes",
    ):
        if key in repaired:
            merged[key] = repaired[key]
    return merged


def _validate_chapter_alignment(blueprint: Any) -> list[str]:
    if not isinstance(blueprint, dict):
        return []
    errors: list[str] = []
    phases = blueprint.get("narrative_phases", [])
    if not phases:
        return errors

    min_ch = min(phase.get("chapter_start", 1) for phase in phases)
    max_ch = max(phase.get("chapter_end", 1) for phase in phases)
    for subplot in blueprint.get("subplot_plan", []):
        name = subplot.get("name", "未知支线")
        for chapter in subplot.get("involved_chapters", []):
            if chapter < min_ch or chapter > max_ch:
                errors.append(f"支线「{name}」章节 {chapter} 超出叙事阶段范围 [{min_ch}-{max_ch}]")
    return errors
