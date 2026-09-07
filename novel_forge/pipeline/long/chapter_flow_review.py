"""Review-stage orchestration: quality checks, repair loops, polish, and ``review_chapter_draft``.

Extracted from ``chapter_flow.py`` during the Task-17 decomposition.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from novel_forge.common.constants import TaskType
from novel_forge.core.domain.world_context import dump_story_bible_for_prompt
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.core.review.review_contracts import (
    alignment_report_to_findings,
    causal_report_to_findings,
    chapter_repair_report_to_findings,
    compile_repair_tickets_from_findings,
    continuity_report_to_findings,
    eval_report_to_findings,
    reading_power_report_to_findings,
    repair_ticket_to_continuity_issue_payload,
    source_text_hash,
)
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
)
from novel_forge.core.schemas.continuity import (
    ContinuityIssue,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.text_validation import (
    check_revelation_density,
    count_chapter_words,
)
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.chapter_flow_finalize import (
    _dedupe_repair_tickets,
    _finalize_review_artifacts,
    _run_alignment_repair_stage,
    _should_include_pre_final_evaluation,
    normalize_continuity_report_repair_state,
)
from novel_forge.pipeline.long.chapter_flow_generate import (
    _build_repair_metrics_payload,
    _persist_repair_metrics_report,
)
from novel_forge.pipeline.long.chapter_flow_orchestrate import (
    _coerce_float,
    _coerce_reading_power_report,
    _emit_input_integrity_check,
    _generate_and_wave,
    _load_report_for_text_hash,
    _make_on_step_with_tokens,
    _persist_stage_artifact_safely,
    _record_text_change,
    _review_resume_plan,
    _ReviewPhaseState,
)
from novel_forge.pipeline.long.decisions import (
    CrossDimensionAction,
    CrossDimensionContext,
    build_repair_thresholds,
    decide_cross_dimension_action,
    resolve_total_repair_rounds_cap,
)
from novel_forge.pipeline.long.execution_models import (
    ChapterExecutionContext,
    ChapterReviewArtifacts,
    FlowContextAdapter,
    PreparedChapterArtifacts,
)
from novel_forge.pipeline.long.repair_dimensions import (
    CAUSAL_DIMENSION,
    CONTINUITY_DIMENSION,
    READING_POWER_DIMENSION,
    absorb_dimension_result,
    emit_dimension_cap_skip,
    gate_dimension_rounds,
    remaining_rounds,
)
from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairFailurePolicy,
    RepairRoundSnapshot,
)
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
    check_world_rule_compliance,
    coerce_world_rule_card,
    world_rule_report_to_findings,
)
from novel_forge.pipeline.long.services.context.source_artifacts import project_stage_source_cards
from novel_forge.pipeline.long.services.pov_drift_audit import run_pov_drift_audit
from novel_forge.pipeline.long.services.quality.style_metrics import (
    compute_style_metrics,
    style_metrics_to_quality_check,
)
from novel_forge.pipeline.long.services.quality.wave_integrity import (
    assess_wave_integrity,
    assess_wave_integrity_against_plan,
    wave_integrity_to_findings,
)
from novel_forge.pipeline.long.stages.causal_repair import CausalRepairLoopResult
from novel_forge.pipeline.long.stages.dedup_pronoun import (
    run_final_dedup,
    run_pronoun_check,
    run_self_repetition_check,
)
from novel_forge.pipeline.long.stages.draft import (
    _build_eval_repair_warnings,
    build_revelation_density_warning,
    build_word_count_warning,
    run_post_repair_polish_layer,
)
from novel_forge.pipeline.long.stages.finalize_common import extract_dimension_scores
from novel_forge.pipeline.long.stages.finalize_report import (
    evaluate_chapter_text,
    extract_and_validate,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    _artifact_report_summary,
    guard_report_has_actionable_low_compliance,
    merge_opening_guard_pending_issues,
)
from novel_forge.pipeline.long.stages.quality_checks_runner import run_quality_checks
from novel_forge.pipeline.long.stages.reading_power_repair import (
    ReadingPowerRepairLoopResult,
    _text_change_ratio,
)
from novel_forge.pipeline.long.stages.report_refresh import (
    refresh_quality_reports_after_semantic_text_change,
)
from novel_forge.pipeline.long.stages.word_count import (
    word_count_archive_gate_enabled,
)
from novel_forge.pipeline.long.stages.world_rule_repair import (
    WorldRulePatchResult,
    patch_blocking_world_rule_conflicts,
)
from novel_forge.pipeline.quality_gate import QualityCheckResult, QualityGate
from novel_forge.pipeline.repair_orchestration.domains.causal import (
    run_causal_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.continuity import (
    run_continuity_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.reading_power import (
    run_reading_power_repair_v2,
)
from novel_forge.pipeline.steps.edit_step import EditInput, EditStep

if TYPE_CHECKING:
    from novel_forge.core.schemas.review_state import ReviewProgressState

_logger = logging.getLogger(__name__)


async def _async_save_review_progress(**kwargs: Any) -> None:
    """Offload checkpoint persistence to a worker thread (non-blocking)."""
    from novel_forge.core.schemas.review_state import (
        save_review_progress as _sync_save,
    )

    await asyncio.to_thread(_sync_save, **kwargs)


_BACKSTORY_SEGMENT_RE = re.compile(r"[^。！？；\n]+[。！？；\n]*")
_PROTAGONIST_ROLE_MARKERS = {
    "protagonist",
    "main",
    "lead",
    "hero",
    "heroine",
    "主角",
    "主要角色",
    "男主",
    "女主",
}


def _dump_for_prompt(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return str(value)


def _causal_report_score(report: Any | None) -> float | None:
    if report is None:
        return None
    score = getattr(report, "causal_score", None)
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def _causal_report_high_priority_issue_count(report: Any | None) -> int:
    count = 0
    for issue in list(getattr(report, "issues", []) or []):
        severity = str(getattr(issue, "severity", "") or "").strip().lower()
        if severity in {"high", "critical"}:
            count += 1
    return count


def _causal_report_hash_conflicts(report: Any | None, current_text: str) -> bool:
    if report is None:
        return False
    stored_hash = str(getattr(report, "source_text_hash", "") or "").strip()
    return bool(stored_hash and stored_hash != source_text_hash(current_text))


def _causal_high_score_skip_result(
    *,
    settings: Any,
    initial_causal_report: Any | None,
    current_text: str,
    alignment_report: Any,
    continuity_report: Any,
    chapter_repair_report: Any | None,
    chapter_number: int,
    max_causal_rounds: int,
    on_step: Any,
) -> CausalRepairLoopResult | None:
    if not bool(getattr(settings, "long_causal_high_score_skip_enabled", True)):
        return None
    if initial_causal_report is None or max_causal_rounds <= 0:
        return None
    status = str(getattr(initial_causal_report, "validation_status", "ok") or "ok").lower()
    if status != "ok":
        return None
    score = _causal_report_score(initial_causal_report)
    try:
        threshold = float(getattr(settings, "long_causal_skip_threshold", 9.5) or 0.0)
    except (TypeError, ValueError):
        threshold = 9.5
    if threshold <= 0.0 or score is None or score < threshold:
        return None
    high_priority_count = _causal_report_high_priority_issue_count(initial_causal_report)
    if high_priority_count > 0:
        return None
    if _causal_report_hash_conflicts(initial_causal_report, current_text):
        return None
    source_hash = source_text_hash(current_text)
    on_step(
        "causal_repair_skipped_high_score",
        {
            "chapter": chapter_number,
            "score": round(score, 2),
            "threshold": threshold,
            "issue_count": len(getattr(initial_causal_report, "issues", []) or []),
            "source_text_hash": source_hash,
        },
    )
    return CausalRepairLoopResult(
        current_text=current_text,
        causal_report=initial_causal_report,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=chapter_repair_report,
        causal_warnings=[],
        rounds_used=0,
    )


def _drop_stale_report_for_hash(report: Any | None, expected_hash: str) -> Any | None:
    if report is None:
        return None
    stored_hash = str(getattr(report, "source_text_hash", "") or "").strip()
    if stored_hash and stored_hash != expected_hash:
        return None
    return report


async def _ensure_reports_current_after_text_change(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: PipelineTrace,
    alignment_report: Any,
    continuity_report: Any,
    causal_report: Any | None,
    chapter_repair_report: Any | None,
    reading_power_report: Any | None,
    window_manager: Any | None,
    window_config: Any | None,
    stale_reason: str,
) -> tuple[Any, Any, Any | None, Any | None, Any | None]:
    """Bind hard review reports to text changed inside Review before downstream use."""

    current_hash = source_text_hash(current_text)
    loaded_alignment = _load_report_for_text_hash(
        runner._storage,
        bundle.layout.alignment_report_path(chapter_number),
        AlignmentReport,
        current_hash,
    )
    loaded_continuity = _load_report_for_text_hash(
        runner._storage,
        bundle.layout.continuity_report_path(chapter_number),
        ContinuityReport,
        current_hash,
    )
    needs_causal = causal_report is not None
    loaded_causal = None
    if needs_causal:
        loaded_causal = _load_report_for_text_hash(
            runner._storage,
            bundle.layout.chapter_causal_report_path(chapter_number),
            CausalValidationReport,
            current_hash,
        )

    if (
        loaded_alignment is not None
        and loaded_continuity is not None
        and (not needs_causal or loaded_causal is not None)
    ):
        on_step = getattr(runner, "_on_step", None)
        if callable(on_step):
            on_step(
                "quality_reports_rebound_after_text_change",
                {
                    "chapter": chapter_number,
                    "reason": stale_reason,
                    "source": "persisted_hash_match",
                    "source_text_hash": current_hash,
                },
            )
        return (
            loaded_alignment,
            loaded_continuity,
            loaded_causal if needs_causal else causal_report,
            _drop_stale_report_for_hash(chapter_repair_report, current_hash),
            reading_power_report,
        )

    refreshed = await refresh_quality_reports_after_semantic_text_change(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        current_text=current_text,
        chapter_number=chapter_number,
        trace=trace,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
        chapter_repair_report=chapter_repair_report,
        reading_power_report=reading_power_report,
        window_manager=window_manager,
        window_config=window_config,
        stale_reason=stale_reason,
    )
    return (
        refreshed.alignment_report,
        refreshed.continuity_report,
        refreshed.causal_report,
        refreshed.chapter_repair_report,
        refreshed.reading_power_report,
    )


_TICKET_BRIEF_METADATA_KEYS = frozenset(
    {
        "evidence_quote",
        "location",
        "anchor_type",
        "wave_integrity",
        "from_scene",
        "to_scene",
        "scene_id",
        "scene_summary",
        "description",
    }
)


def _extract_ticket_brief_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract a bounded brief from a plain-dict ticket payload."""
    return {
        "ticket_id": payload.get("ticket_id", ""),
        "finding_ids": list(payload.get("finding_ids", []) or []),
        "dimension": payload.get("dimension", ""),
        "issue_type": payload.get("issue_type", ""),
        "severity": payload.get("severity", ""),
        "target_summary": payload.get("target_summary", ""),
        "repair_goal": payload.get("repair_goal", ""),
        "repair_mode": payload.get("repair_mode", ""),
        "acceptance_criteria": list(payload.get("acceptance_criteria", []) or []),
        "postconditions": list(payload.get("postconditions", []) or []),
        "must_preserve": list(payload.get("must_preserve", []) or []),
        "forbidden_changes": list(payload.get("forbidden_changes", []) or []),
        "target_paragraph_start": payload.get("target_paragraph_start", 0),
        "target_paragraph_end": payload.get("target_paragraph_end", 0),
        "metadata": {
            key: value
            for key, value in dict(payload.get("metadata", {}) or {}).items()
            if key in _TICKET_BRIEF_METADATA_KEYS
        },
    }


def _repair_ticket_briefs(tickets: list[Any]) -> list[dict[str, Any]]:
    """Build bounded brief dicts from RepairTicket objects.

    Uses direct attribute access for Pydantic models to avoid the overhead
    of a full ``model_dump(mode="json")`` when only ~15 fields are needed.
    """
    briefs: list[dict[str, Any]] = []
    for ticket in tickets:
        if isinstance(ticket, dict):
            briefs.append(_extract_ticket_brief_from_payload(dict(ticket)))
        elif hasattr(ticket, "ticket_id"):
            # Direct field access — avoid full model_dump serialization.
            # RepairTicket fields are all simple types (str, int, float,
            # list[str], list[dict], dict[str, Any]) after Pydantic validation.
            metadata = dict(getattr(ticket, "metadata", {}) or {})
            briefs.append(
                {
                    "ticket_id": getattr(ticket, "ticket_id", ""),
                    "finding_ids": list(getattr(ticket, "finding_ids", []) or []),
                    "dimension": getattr(ticket, "dimension", ""),
                    "issue_type": getattr(ticket, "issue_type", ""),
                    "severity": getattr(ticket, "severity", ""),
                    "target_summary": getattr(ticket, "target_summary", ""),
                    "repair_goal": getattr(ticket, "repair_goal", ""),
                    "repair_mode": getattr(ticket, "repair_mode", ""),
                    "acceptance_criteria": list(getattr(ticket, "acceptance_criteria", []) or []),
                    "postconditions": list(getattr(ticket, "postconditions", []) or []),
                    "must_preserve": list(getattr(ticket, "must_preserve", []) or []),
                    "forbidden_changes": list(getattr(ticket, "forbidden_changes", []) or []),
                    "target_paragraph_start": getattr(ticket, "target_paragraph_start", 0),
                    "target_paragraph_end": getattr(ticket, "target_paragraph_end", 0),
                    "metadata": {
                        k: v for k, v in metadata.items() if k in _TICKET_BRIEF_METADATA_KEYS
                    },
                }
            )
    return briefs


def _chapter_quality_ticket_constraints(
    ticket_briefs: list[dict[str, Any]],
) -> dict[str, list[Any]]:
    return {
        "preserve": [
            item for ticket in ticket_briefs for item in list(ticket.get("must_preserve", []) or [])
        ],
        "do_not_introduce": [
            item
            for ticket in ticket_briefs
            for item in list(ticket.get("forbidden_changes", []) or [])
        ],
    }


def _build_chapter_quality_repair_stage_cards(
    *,
    prepared: PreparedChapterArtifacts,
    ticket_briefs: list[dict[str, Any]],
    settings: Any,
) -> dict[str, Any]:
    """Build the edit-stage card bundle used by the bounded quality repair lane."""

    bundle = prepared.bundle
    packet = prepared.packet
    chapter_outline = getattr(bundle, "chapter_outline", None)
    blueprint = getattr(bundle, "blueprint", None)
    element_selection = getattr(blueprint, "element_selection", None)
    element_selection_payload = _dump_for_prompt(element_selection)
    stage_cards = build_stage_cards(
        stage="edit",
        packet=packet,
        chapter_outline=chapter_outline,
        bridge=prepared.bridge,
        plan=prepared.plan,
        canon_context=getattr(packet, "canon_context", {}),
        style_profile=getattr(bundle, "style_profile", None),
        editorial_contract=getattr(bundle, "editorial_contract", None),
        editorial_readiness=getattr(bundle, "editorial_readiness", None),
        narrative_contract=getattr(bundle, "narrative_contract", None),
        memory_hints=prepared.memory_hints,
        reading_power_hint=prepared.reading_power_hint,
        story_bible=dump_story_bible_for_prompt(getattr(bundle, "story_bible", None)),
        element_selection=element_selection_payload,
        element_focus=list(getattr(chapter_outline, "element_focus", []) or []),
        known_issues_to_avoid=ticket_briefs,
        weak_senses=list(getattr(bundle, "weak_senses", []) or []),
        pov_hint=getattr(chapter_outline, "pov_character", ""),
        chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        settings=settings,
    )

    repair_card = dict(stage_cards.get("repair") or {})
    ticket_constraints = _chapter_quality_ticket_constraints(ticket_briefs)
    for key, values in ticket_constraints.items():
        if not values:
            continue
        existing = list(repair_card.get(key, []) or [])
        combined: list[Any] = []
        for item in [*existing, *values]:
            if item not in combined:
                combined.append(item)
        repair_card[key] = combined
    if repair_card:
        stage_cards["repair"] = repair_card
    return stage_cards


def _persist_eval_report_after_extract(
    context: ChapterExecutionContext,
    *,
    bundle: Any,
    chapter_number: int,
    eval_report: EvalReport,
) -> None:
    payload = eval_report.model_dump(mode="json")
    payload["source_text_hash"] = getattr(eval_report, "source_text_hash", "")
    context.storage.save_json(bundle.layout.eval_report_path(chapter_number), payload)


def _eval_report_is_fallback(report: Any | None) -> bool:
    if report is None:
        return False
    return bool(
        getattr(report, "is_fallback", False)
        or str(getattr(report, "evaluation_status", "") or "").strip().lower()
        in {"fallback", "timeout", "degraded"}
        or str(getattr(report, "score_confidence", "") or "").strip().lower() == "fallback"
    )


async def _attempt_world_rule_patch_safely(
    *,
    runner: Any,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    chapter_number: int,
    current_text: str,
    world_rule_card: Any,
    report: Any,
    blocking_issues: list[Any],
    on_step: Any,
    repair_lane: str,
    failure_action: str,
) -> WorldRulePatchResult:
    """Run the surgical patch while preserving the orchestration failure contract."""

    try:
        return await patch_blocking_world_rule_conflicts(
            router=runner._router,
            builder=runner._builder,
            settings=runner._settings,
            trace=trace,
            chapter_number=chapter_number,
            current_text=current_text,
            card=coerce_world_rule_card(world_rule_card),
            applications=list(getattr(prepared.plan, "world_rule_applications", []) or []),
            report=report,
            style_profile=getattr(prepared.bundle, "style_profile", None),
        )
    except Exception as exc:  # noqa: BLE001 - shared safety policy owns degradation
        outcome = RepairFailurePolicy(on_step, logger=_logger).repair_failed(
            snapshot=RepairRoundSnapshot(
                stage=RepairDimension.WORLD_RULE,
                chapter_number=chapter_number,
                text=current_text,
                report=report,
                metadata={
                    "repair_lane": repair_lane,
                    "rule_ids": [issue.rule_id for issue in blocking_issues],
                },
            ),
            exc=exc,
            action=failure_action,
            warning="世界规则精确补丁执行失败，已保留当前正文并进入安全降级路径。",
            repair_exhausted=False,
        )
        return WorldRulePatchResult(
            text=outcome.current_text,
            attempted_rule_ids=tuple(issue.rule_id for issue in blocking_issues),
            fallback=True,
            skip_reason="patch_execution_failed",
            failure_kind=outcome.error_kind.value,
            failure_reason=outcome.failure_reason,
            error_type=type(exc).__name__,
        )


@dataclass(frozen=True)
class MechanicalCleanupResult:
    """Result from deterministic/near-deterministic review text cleanup."""

    current_text: str
    repetition_report: Any | None = None
    pronoun_report: Any | None = None


async def _run_chapter_quality_repair_lane(
    *,
    runner: Any,
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    chapter_number: int,
    state: _ReviewPhaseState,
    skip_quality: bool,
    on_step: Any,
) -> _ReviewPhaseState:
    """One bounded Review repair lane for WAVE / CHECK_CHAPTER / eval quality tickets."""

    policy = (
        str(getattr(context.settings, "long_wave_post_condition_policy", "repair") or "repair")
        .strip()
        .lower()
    )
    word_count_policy = (
        str(getattr(context.settings, "long_wave_word_count_policy", "inherit") or "inherit")
        .strip()
        .lower()
    )
    archive_gate_enabled = word_count_archive_gate_enabled(context.settings)
    wave_integrity = assess_wave_integrity(
        state.wave_meta,
        policy=policy,
        word_count_policy=word_count_policy,
        archive_gate_enabled=archive_gate_enabled,
    )
    state.wave_integrity = wave_integrity.model_dump()
    if wave_integrity.blocking:
        on_step(
            "wave_integrity_blocking",
            {
                "chapter": chapter_number,
                "policy": wave_integrity.policy,
                "archive_blocking": wave_integrity.archive_blocking,
                "diagnostic_blocking": wave_integrity.blocking,
                "metrics": dict(wave_integrity.metrics),
                "issues": [issue.summary for issue in wave_integrity.issues],
            },
        )
        if wave_integrity.archive_blocking:
            raise ConsistencyViolationError(
                [
                    "WAVE post-condition 阻断归档："
                    + f"{issue.issue_type}: {issue.summary} ({issue.evidence})"
                    for issue in wave_integrity.issues
                    if issue.blocking
                ],
                violation_kind="wave_post_condition",
                failed_stage="wave",
                replan_target=RecoveryTarget.MANUAL,
            )

    state.chapter_quality_repair = {
        "attempted": False,
        "rounds_used": 0,
        "ticket_count": 0,
        "text_changed": False,
        "wave_blocking_before": bool(wave_integrity.archive_blocking),
        "wave_blocking_after": bool(wave_integrity.archive_blocking),
        "wave_diagnostic_blocking_before": bool(wave_integrity.blocking),
        "wave_diagnostic_blocking_after": bool(wave_integrity.blocking),
    }
    if skip_quality:
        state.chapter_quality_repair["skip_reason"] = "quality_stage_skipped"
        return state
    if state.total_rounds_cap > 0 and state.total_repair_rounds_used >= state.total_rounds_cap:
        state.chapter_quality_repair["skip_reason"] = "total_rounds_cap_reached"
        return state

    current_hash = source_text_hash(state.current_text)
    findings: list[Any] = []
    world_rule_diagnostics: list[Any] = []
    world_rule_card: dict[str, Any] | None = None
    source_slice = getattr(prepared.bundle, "chapter_source_slice", None)
    if source_slice is not None:
        world_rule_card = project_stage_source_cards(source_slice, stage="review").get(
            "world_rule_card"
        )
        if world_rule_card:
            world_rule_report = await check_world_rule_compliance(
                runner=runner,
                chapter_text=state.current_text,
                chapter_number=chapter_number,
                world_rule_card=world_rule_card,
                applications=list(getattr(prepared.plan, "world_rule_applications", []) or []),
            )
            world_rule_diagnostics = world_rule_report_to_findings(world_rule_report)
            on_step(
                "world_rule_review",
                {
                    "chapter": chapter_number,
                    "rule_book_hash": world_rule_report.rule_book_hash,
                    "checked_rule_ids": world_rule_report.checked_rule_ids,
                    "skipped_rule_ids": world_rule_report.skipped_rule_ids,
                    "issues": [item.model_dump(mode="json") for item in world_rule_report.issues],
                },
            )
            blocking_world_issues = [
                issue
                for issue in world_rule_report.issues
                if issue.verdict == "conflict" and issue.severity in {"critical", "high"}
            ]
            if blocking_world_issues:
                world_patch = await _attempt_world_rule_patch_safely(
                    runner=runner,
                    prepared=prepared,
                    trace=trace,
                    chapter_number=chapter_number,
                    current_text=state.current_text,
                    world_rule_card=world_rule_card,
                    report=world_rule_report,
                    blocking_issues=blocking_world_issues,
                    on_step=on_step,
                    repair_lane="pre_quality_surgical_patch",
                    failure_action="degrade_to_chapter_quality_repair",
                )
                world_patch_meta = {
                    "rule_ids": list(world_patch.attempted_rule_ids),
                    "patches_attempted": world_patch.patches_attempted,
                    "patches_applied": world_patch.patches_applied,
                    "fallback": world_patch.fallback,
                    "skip_reason": world_patch.skip_reason,
                    "failure_kind": str(getattr(world_patch, "failure_kind", "") or ""),
                    "failure_reason": str(getattr(world_patch, "failure_reason", "") or ""),
                    "error_type": str(getattr(world_patch, "error_type", "") or ""),
                }
                on_step(
                    "world_rule_patch_repair",
                    {
                        "chapter": chapter_number,
                        **world_patch_meta,
                    },
                )
                if not world_patch.changed:
                    state.chapter_quality_repair.update(
                        {
                            "attempted": True,
                            "world_rule_patch": world_patch_meta,
                            "world_rule_patch_degraded": True,
                        }
                    )
                    state.review_warnings.append(
                        "世界规则精确补丁未生效，已降级到受限章节质量修复："
                        + (
                            str(world_patch_meta["failure_reason"])
                            or str(world_patch_meta["skip_reason"])
                            or "no_change"
                        )
                    )
                    on_step(
                        "world_rule_patch_degraded",
                        {
                            "chapter": chapter_number,
                            **world_patch_meta,
                            "action": "degrade_to_chapter_quality_repair",
                        },
                    )
                else:
                    pre_patch_text = state.current_text
                    state.current_text = world_patch.text
                    state.total_repair_rounds_used += 1
                    state.chapter_quality_repair.update(
                        {
                            "attempted": True,
                            "world_rule_patch": world_patch_meta,
                        }
                    )
                    _record_text_change(
                        state,
                        stage="world_rule_patch_repair",
                        before_text=pre_patch_text,
                        after_text=state.current_text,
                        applied=True,
                    )
                    patch_resolved = False
                    try:
                        post_patch_report = await check_world_rule_compliance(
                            runner=runner,
                            chapter_text=state.current_text,
                            chapter_number=chapter_number,
                            world_rule_card=world_rule_card,
                            applications=list(
                                getattr(prepared.plan, "world_rule_applications", []) or []
                            ),
                            rule_ids=set(world_patch.attempted_rule_ids),
                        )
                    except Exception as exc:  # noqa: BLE001 - rollback policy owns fallback
                        failed_text = state.current_text
                        outcome = RepairFailurePolicy(on_step, logger=_logger).recheck_failed(
                            snapshot=RepairRoundSnapshot(
                                stage=RepairDimension.WORLD_RULE,
                                chapter_number=chapter_number,
                                text=pre_patch_text,
                                report=world_rule_report,
                                metadata={
                                    "repair_lane": "pre_quality_surgical_patch",
                                    "rule_ids": list(world_patch.attempted_rule_ids),
                                },
                            ),
                            exc=exc,
                            action="rollback_and_degrade_to_chapter_quality_repair",
                            warning=("世界规则精确补丁复检失败，已回滚并降级到受限章节质量修复。"),
                            repair_exhausted=False,
                        )
                        state.current_text = outcome.current_text
                        state.review_warnings.append(outcome.warning)
                        state.chapter_quality_repair["world_rule_patch_degraded"] = True
                        _record_text_change(
                            state,
                            stage="world_rule_patch_recheck_rollback",
                            before_text=failed_text,
                            after_text=state.current_text,
                            applied=False,
                            reason="world_rule_recheck_failed",
                        )
                    else:
                        remaining_world_issues = [
                            issue
                            for issue in post_patch_report.issues
                            if issue.verdict == "conflict"
                            and issue.severity in {"critical", "high"}
                        ]
                        on_step(
                            "world_rule_patch_recheck",
                            {
                                "chapter": chapter_number,
                                "checked_rule_ids": post_patch_report.checked_rule_ids,
                                "resolved": not remaining_world_issues,
                                "remaining_rule_ids": [
                                    issue.rule_id for issue in remaining_world_issues
                                ],
                            },
                        )
                        patch_resolved = not remaining_world_issues
                        if remaining_world_issues:
                            failed_text = state.current_text
                            state.current_text = pre_patch_text
                            state.chapter_quality_repair["world_rule_patch_degraded"] = True
                            state.review_warnings.append(
                                "世界规则精确补丁复检后仍冲突，已回滚并降级到受限章节质量修复。"
                            )
                            _record_text_change(
                                state,
                                stage="world_rule_patch_recheck_rollback",
                                before_text=failed_text,
                                after_text=state.current_text,
                                applied=False,
                                reason="world_rule_conflict_remains",
                            )
                            on_step(
                                "world_rule_patch_recheck_rollback",
                                {
                                    "chapter": chapter_number,
                                    "action": "degrade_to_chapter_quality_repair",
                                    "remaining_rule_ids": [
                                        issue.rule_id for issue in remaining_world_issues
                                    ],
                                },
                            )
                    if patch_resolved:
                        (
                            state.alignment_report,
                            state.continuity_report,
                            state.chapter_repair_report,
                        ) = await run_quality_checks(
                            runner,
                            prepared.bundle,
                            prepared.packet,
                            prepared.bridge,
                            prepared.plan,
                            state.current_text,
                            chapter_number,
                            trace,
                            window_manager=prepared.window_manager,
                            window_config=prepared.window_config,
                            memory_hints=prepared.memory_hints,
                        )
                        state.continuity_report = merge_opening_guard_pending_issues(
                            runner,
                            state.continuity_report,
                            chapter_number=chapter_number,
                            on_step=on_step,
                        )
                        current_hash = source_text_hash(state.current_text)
                        world_rule_diagnostics = []
                        if (
                            state.total_rounds_cap > 0
                            and state.total_repair_rounds_used >= state.total_rounds_cap
                        ):
                            state.chapter_quality_repair["skip_reason"] = (
                                "total_rounds_cap_reached_after_world_rule_patch"
                            )
                            return state
    findings.extend(world_rule_diagnostics)
    existing_tickets = list(getattr(state.chapter_repair_report, "repair_tickets", []) or [])
    chapter_quality_findings = list(
        getattr(state.chapter_repair_report, "review_findings", []) or []
    )
    if not chapter_quality_findings:
        chapter_quality_findings = chapter_repair_report_to_findings(
            state.chapter_repair_report,
            chapter_number=chapter_number,
            current_text_hash=current_hash,
        )
    findings.extend(chapter_quality_findings)
    if wave_integrity.policy == "repair":
        findings.extend(
            wave_integrity_to_findings(
                wave_integrity,
                chapter_number=chapter_number,
                current_text_hash=current_hash,
            )
        )

    eval_findings: list[Any] = []
    if bool(getattr(context.settings, "long_eval_repair_enabled", True)):
        try:
            eval_report = await evaluate_chapter_text(
                context,
                bundle=prepared.bundle,
                chapter_number=chapter_number,
                current_text=state.current_text,
                trace=trace,
                emit_step=False,
                persist=False,
                plan=prepared.plan,
            )
            state.quality_lane_eval_report = eval_report
            state.quality_lane_eval_text_hash = current_hash
            if _eval_report_is_fallback(eval_report):
                state.review_warnings.append("章节质量评估暂不可用：未把兜底分数转换成修复任务。")
                on_step(
                    "chapter_quality_eval_fallback",
                    {
                        "chapter": chapter_number,
                        "fallback_reason": str(getattr(eval_report, "fallback_reason", "") or ""),
                        "action": "skip_untrusted_eval_findings",
                    },
                )
            else:
                eval_findings = eval_report_to_findings(
                    eval_report,
                    chapter_number=chapter_number,
                    current_text_hash=current_hash,
                )
                findings.extend(eval_findings)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "chapter_quality_eval_repair_scan_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
            state.review_warnings.append(f"评估建议修复扫描失败：{exc}")

    tickets = _dedupe_repair_tickets(
        [
            *existing_tickets,
            *compile_repair_tickets_from_findings(
                [
                    finding
                    for finding in findings
                    if str(getattr(finding, "dimension", "") or "").strip().lower()
                    in {"chapter_quality", "world_rule"}
                ]
            ),
        ]
    )
    tickets = [
        ticket
        for ticket in tickets
        if str(getattr(ticket, "dimension", "") or "").strip().lower()
        in {"chapter_quality", "world_rule"}
        and str(getattr(ticket, "severity", "") or "").strip().lower()
        in {"critical", "high", "medium"}
    ]
    state.chapter_quality_repair.update(
        {
            "ticket_count": len(tickets),
            "eval_ticket_count": len(eval_findings),
            "chapter_quality_finding_count": len(chapter_quality_findings),
            "world_rule_diagnostic_count": len(world_rule_diagnostics),
        }
    )
    if not tickets:
        state.chapter_quality_repair["skip_reason"] = "no_actionable_tickets"
        return state

    ticket_briefs = _repair_ticket_briefs(tickets)
    on_step(
        "chapter_quality_repair_started",
        {
            "chapter": chapter_number,
            "ticket_count": len(tickets),
            "wave_blocking": bool(wave_integrity.archive_blocking),
            "wave_diagnostic_blocking": bool(wave_integrity.blocking),
        },
    )
    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=trace,
        on_step=getattr(runner, "on_step", None),
    )
    chapter_card = {
        "chapter_number": chapter_number,
        "target_word_count": int(
            getattr(prepared.bundle.chapter_outline, "expected_word_count", 0) or 0
        ),
    }
    stage_cards = _build_chapter_quality_repair_stage_cards(
        prepared=prepared,
        ticket_briefs=ticket_briefs,
        settings=runner._settings,
    )
    stage_cards["chapter"] = {**dict(stage_cards.get("chapter") or {}), **chapter_card}
    edit_result = await edit_step.run(
        EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text=state.current_text,
            context={
                "chapter_number": chapter_number,
                "target_word_count": chapter_card["target_word_count"],
                "stage_cards": stage_cards,
                "chapter_repair_report": _dump_for_prompt(state.chapter_repair_report),
                "chapter_quality_repair_tickets": ticket_briefs,
                "chapter_quality_repair_focus": [
                    ticket.get("repair_goal") or ticket.get("target_summary")
                    for ticket in ticket_briefs
                    if ticket.get("repair_goal") or ticket.get("target_summary")
                ],
                "wave_integrity": state.wave_integrity,
                "alignment_report": _dump_for_prompt(state.alignment_report),
                "continuity_report": _dump_for_prompt(state.continuity_report),
            },
            max_rounds=1,
            iteration=1,
            chapter_source_slice=getattr(prepared.bundle, "chapter_source_slice", None),
        )
    )
    revised_text = edit_result.revised_text or state.current_text
    text_changed = revised_text != state.current_text
    _pre_chapter_quality_text = state.current_text
    _pre_chapter_quality_alignment = state.alignment_report
    _pre_chapter_quality_continuity = state.continuity_report
    _pre_chapter_quality_report = state.chapter_repair_report
    state.current_text = revised_text
    if text_changed:
        _record_text_change(
            state,
            stage="chapter_quality_repair",
            before_text=_pre_chapter_quality_text,
            after_text=state.current_text,
            applied=True,
        )
    state.total_repair_rounds_used += 1
    state.chapter_quality_repair.update(
        {
            "attempted": True,
            "rounds_used": 1,
            "text_changed": text_changed,
            "edit_notes": list(getattr(edit_result, "edit_notes", []) or [])[:6],
        }
    )
    state.cumulative_change_ratio = _text_change_ratio(state.baseline_text, state.current_text)

    if text_changed:
        (
            state.alignment_report,
            state.continuity_report,
            state.chapter_repair_report,
        ) = await run_quality_checks(
            runner,
            prepared.bundle,
            prepared.packet,
            prepared.bridge,
            prepared.plan,
            state.current_text,
            chapter_number,
            trace,
            window_manager=prepared.window_manager,
            window_config=prepared.window_config,
            memory_hints=prepared.memory_hints,
        )
        state.continuity_report = merge_opening_guard_pending_issues(
            runner,
            state.continuity_report,
            chapter_number=chapter_number,
            on_step=on_step,
        )
        if world_rule_card:
            post_repair_world_report = await check_world_rule_compliance(
                runner=runner,
                chapter_text=state.current_text,
                chapter_number=chapter_number,
                world_rule_card=world_rule_card,
                applications=list(getattr(prepared.plan, "world_rule_applications", []) or []),
            )
            blocking_world_issues = [
                issue
                for issue in post_repair_world_report.issues
                if issue.severity in {"critical", "high"} and issue.verdict == "conflict"
            ]
            on_step(
                "world_rule_repair_recheck",
                {
                    "chapter": chapter_number,
                    "rule_book_hash": post_repair_world_report.rule_book_hash,
                    "resolved": not blocking_world_issues,
                    "remaining_rule_ids": [issue.rule_id for issue in blocking_world_issues],
                },
            )
            if blocking_world_issues:
                # A general quality edit introduced or exposed a P0 world-rule
                # regression. Reuse the evidence-anchored repair lane before
                # restoring the last world-rule-validated rollback anchor.
                post_quality_world_patch = await _attempt_world_rule_patch_safely(
                    runner=runner,
                    prepared=prepared,
                    trace=trace,
                    chapter_number=chapter_number,
                    current_text=state.current_text,
                    world_rule_card=world_rule_card,
                    report=post_repair_world_report,
                    blocking_issues=blocking_world_issues,
                    on_step=on_step,
                    repair_lane="post_quality_surgical_patch",
                    failure_action="rollback_to_pre_quality_text",
                )
                on_step(
                    "world_rule_post_quality_patch",
                    {
                        "chapter": chapter_number,
                        "rule_ids": list(post_quality_world_patch.attempted_rule_ids),
                        "patches_attempted": post_quality_world_patch.patches_attempted,
                        "patches_applied": post_quality_world_patch.patches_applied,
                        "fallback": post_quality_world_patch.fallback,
                        "skip_reason": post_quality_world_patch.skip_reason,
                        "failure_kind": str(
                            getattr(post_quality_world_patch, "failure_kind", "") or ""
                        ),
                        "failure_reason": str(
                            getattr(post_quality_world_patch, "failure_reason", "") or ""
                        ),
                        "error_type": str(
                            getattr(post_quality_world_patch, "error_type", "") or ""
                        ),
                    },
                )
                remaining_world_issues = blocking_world_issues
                if post_quality_world_patch.changed:
                    pre_world_patch_text = state.current_text
                    state.current_text = post_quality_world_patch.text
                    state.total_repair_rounds_used += 1
                    _record_text_change(
                        state,
                        stage="world_rule_post_quality_patch",
                        before_text=pre_world_patch_text,
                        after_text=state.current_text,
                        applied=True,
                    )
                    try:
                        patched_world_report = await check_world_rule_compliance(
                            runner=runner,
                            chapter_text=state.current_text,
                            chapter_number=chapter_number,
                            world_rule_card=world_rule_card,
                            applications=list(
                                getattr(prepared.plan, "world_rule_applications", []) or []
                            ),
                            # Recheck every rule that blocked the quality edit, not
                            # only the subset with an exact patch anchor. Otherwise
                            # an unanchored conflict can disappear from the report
                            # and make a partial patch look fully resolved.
                            rule_ids={issue.rule_id for issue in blocking_world_issues},
                        )
                    except Exception as exc:  # noqa: BLE001 - rollback policy owns fallback
                        outcome = RepairFailurePolicy(on_step, logger=_logger).recheck_failed(
                            snapshot=RepairRoundSnapshot(
                                stage=RepairDimension.WORLD_RULE,
                                chapter_number=chapter_number,
                                text=_pre_chapter_quality_text,
                                report=post_repair_world_report,
                                metadata={
                                    "repair_lane": "post_quality_surgical_patch",
                                    "rule_ids": [issue.rule_id for issue in blocking_world_issues],
                                },
                            ),
                            exc=exc,
                            action="rollback_to_pre_quality_text",
                            warning=(
                                "质量修复后的世界规则补丁复检失败，已回滚到修复前的已验证正文。"
                            ),
                            repair_exhausted=False,
                        )
                        state.review_warnings.append(outcome.warning)
                    else:
                        remaining_world_issues = [
                            issue
                            for issue in patched_world_report.issues
                            if issue.severity in {"critical", "high"}
                            and issue.verdict == "conflict"
                        ]
                        on_step(
                            "world_rule_post_quality_patch_recheck",
                            {
                                "chapter": chapter_number,
                                "resolved": not remaining_world_issues,
                                "remaining_rule_ids": [
                                    issue.rule_id for issue in remaining_world_issues
                                ],
                            },
                        )

                if remaining_world_issues:
                    failed_text = state.current_text
                    state.current_text = _pre_chapter_quality_text
                    state.alignment_report = _pre_chapter_quality_alignment
                    state.continuity_report = _pre_chapter_quality_continuity
                    state.chapter_repair_report = _pre_chapter_quality_report
                    _record_text_change(
                        state,
                        stage="chapter_quality_repair_world_rule_rollback",
                        before_text=failed_text,
                        after_text=state.current_text,
                        applied=False,
                        reason="post_quality_world_rule_regression",
                    )
                    state.chapter_quality_repair.update(
                        {
                            "text_changed": False,
                            "rolled_back": True,
                            "rollback_reason": "post_quality_world_rule_regression",
                            "world_rule_patch": {
                                "rule_ids": list(post_quality_world_patch.attempted_rule_ids),
                                "patches_attempted": post_quality_world_patch.patches_attempted,
                                "patches_applied": post_quality_world_patch.patches_applied,
                            },
                        }
                    )
                    state.review_warnings.append(
                        "章节质量修复引入世界规则回归，已回滚到修复前的已验证正文："
                        + "；".join(
                            f"{issue.rule_id} {issue.summary}"
                            for issue in remaining_world_issues[:5]
                        )
                    )
                    on_step(
                        "world_rule_post_quality_rollback",
                        {
                            "chapter": chapter_number,
                            "action": "restore_last_world_rule_validated_text",
                            "rule_ids": [issue.rule_id for issue in remaining_world_issues],
                        },
                    )
                else:
                    state.chapter_quality_repair["world_rule_patch"] = {
                        "rule_ids": list(post_quality_world_patch.attempted_rule_ids),
                        "patches_attempted": post_quality_world_patch.patches_attempted,
                        "patches_applied": post_quality_world_patch.patches_applied,
                    }
                    (
                        state.alignment_report,
                        state.continuity_report,
                        state.chapter_repair_report,
                    ) = await run_quality_checks(
                        runner,
                        prepared.bundle,
                        prepared.packet,
                        prepared.bridge,
                        prepared.plan,
                        state.current_text,
                        chapter_number,
                        trace,
                        window_manager=prepared.window_manager,
                        window_config=prepared.window_config,
                        memory_hints=prepared.memory_hints,
                    )
                    state.continuity_report = merge_opening_guard_pending_issues(
                        runner,
                        state.continuity_report,
                        chapter_number=chapter_number,
                        on_step=on_step,
                    )
    else:
        state.chapter_quality_repair["quality_recheck_skipped_reason"] = "no_text_change"
        unresolved_world_findings = [
            finding
            for finding in world_rule_diagnostics
            if bool(getattr(finding, "blocks_finalize", False))
        ]
        if unresolved_world_findings:
            raise ConsistencyViolationError(
                [
                    "世界规则定向修复未产生可验证改动："
                    + "；".join(
                        str(getattr(finding, "summary", ""))
                        for finding in unresolved_world_findings[:5]
                    )
                ],
                violation_kind="world_rule_conflict",
                failed_stage="world_rule_repair",
                replan_target=RecoveryTarget.MANUAL,
            )
    state.cumulative_change_ratio = _text_change_ratio(state.baseline_text, state.current_text)
    _wave_warnings = list(state.wave_meta.get("warnings", []) or [])
    _wave_was_skipped = any(str(w).startswith("wave skipped:") for w in _wave_warnings)
    wave_after = assess_wave_integrity_against_plan(
        current_text=state.current_text,
        plan=prepared.plan,
        target_word_count=int(
            getattr(prepared.bundle.chapter_outline, "expected_word_count", 0) or 0
        ),
        policy=policy,
        word_count_policy=word_count_policy,
        archive_gate_enabled=archive_gate_enabled,
        wave_skipped=_wave_was_skipped,
    )
    state.wave_integrity = wave_after.model_dump()
    state.chapter_quality_repair["wave_blocking_after"] = bool(wave_after.archive_blocking)
    state.chapter_quality_repair["wave_diagnostic_blocking_after"] = bool(wave_after.blocking)
    if wave_after.archive_blocking:
        raise ConsistencyViolationError(
            [
                "WAVE post-condition 修复后仍阻断归档："
                + f"{issue.issue_type}: {issue.summary} ({issue.evidence})"
                for issue in wave_after.issues
                if issue.blocking
            ],
            violation_kind="wave_post_condition",
            failed_stage="wave",
            replan_target=RecoveryTarget.MANUAL,
        )
    if wave_after.blocking:
        residual_issues = [issue.summary for issue in wave_after.issues if issue.blocking]
        state.review_warnings.append(
            "WAVE post-condition 修复后仍有本地诊断候选；repair 模式下不由本地启发式阻断，"
            "归档阻断以 LLM 质量门和 Finalize gate 为准：" + "；".join(residual_issues[:6])
        )
        on_step(
            "wave_integrity_residual_after_repair",
            {
                "chapter": chapter_number,
                "policy": wave_after.policy,
                "archive_blocking": wave_after.archive_blocking,
                "metrics": dict(wave_after.metrics),
                "issues": residual_issues,
            },
        )
    on_step(
        "chapter_quality_repair_done",
        {
            "chapter": chapter_number,
            "ticket_count": len(tickets),
            "text_changed": text_changed,
            "wave_blocking_after": bool(wave_after.archive_blocking),
            "wave_diagnostic_blocking_after": bool(wave_after.blocking),
        },
    )
    return state


async def _run_pov_drift_audit_stage(
    *,
    bundle: Any,
    prepared: PreparedChapterArtifacts,
    current_text: str,
    chapter_number: int,
    state: _ReviewPhaseState,
    on_step: Any,
) -> _ReviewPhaseState:
    """Run local POV drift audit against the current Review text."""

    pov_character = str(getattr(bundle.chapter_outline, "pov_character", "") or "")
    if not pov_character:
        state.pov_drift_findings = []
        state.pov_drift_tickets = []
        return state

    pov_scope = str(
        getattr(prepared.plan, "pov_scope", "")
        or getattr(bundle.chapter_outline, "pov_scope", "limited")
        or "limited"
    )
    known_chars = list(
        getattr(getattr(bundle, "character_bible", None), "character_names", []) or []
    )
    pov_result = await run_pov_drift_audit(
        current_text=current_text,
        chapter_number=chapter_number,
        pov_character=pov_character,
        pov_scope=pov_scope,
        known_characters=known_chars,
    )
    state.pov_drift_findings = pov_result.findings
    state.pov_drift_tickets = pov_result.repair_tickets
    on_step(
        "pov_drift_audit",
        {
            "chapter": chapter_number,
            "verdict": pov_result.verdict,
            "candidate_count": len(pov_result.candidates),
            "finding_count": len(pov_result.findings),
            "details": pov_result.details,
            "source_text_hash": source_text_hash(current_text),
        },
    )
    return state


def _project_pov_ticket_to_continuity_issue(ticket: Any) -> ContinuityIssue | None:
    """Project a bounded POV drift ticket into continuity repair's issue shape."""

    dimension = str(getattr(ticket, "dimension", "") or "").strip().lower()
    source_module = str(getattr(ticket, "source_module", "") or "").strip().lower()
    if dimension != "pov" or source_module != "pov_drift_audit":
        return None

    original_severity = str(getattr(ticket, "severity", "") or "medium").strip().lower()
    if original_severity not in {"critical", "high"}:
        return None

    payload = repair_ticket_to_continuity_issue_payload(ticket)
    metadata = dict(getattr(ticket, "metadata", {}) or {})
    characters = [
        str(item or "").strip()
        for item in list(metadata.get("characters_with_interior") or [])
        if str(item or "").strip()
    ]
    pov_character = str(metadata.get("pov_character", "") or "").strip()
    raw_start = int(getattr(ticket, "target_paragraph_start", 0) or 0)
    raw_end = int(getattr(ticket, "target_paragraph_end", raw_start) or raw_start)
    paragraph_start = max(1, raw_start + 1)
    paragraph_end = max(paragraph_start, raw_end + 1)
    evidence = str(payload.get("evidence") or getattr(ticket, "target_summary", "") or "")
    ticket_id = str(getattr(ticket, "ticket_id", "") or "").strip()
    issue_id = f"pov_continuity_{ticket_id or paragraph_start}"

    fix_actions = [
        "把非 POV 角色的内心描写改写为可观察的动作、表情、语气或环境反应。",
        "不得为修复局部 POV 漂移而改动本章主线结果、角色状态或关键因果关系。",
        "除非章节计划明确允许，不能新增 '---' POV 切换标记来规避问题。",
        *[str(item) for item in list(payload.get("fix_actions") or []) if str(item or "").strip()],
    ]
    forbidden_changes = [
        *[
            str(item)
            for item in list(payload.get("forbidden_changes") or [])
            if str(item or "").strip()
        ],
        "不得扩大为全章视角重写。",
        "不得新增非计划内的 POV 段落或旁白视角。",
    ]
    must_preserve = [
        *[
            str(item)
            for item in list(payload.get("must_preserve") or [])
            if str(item or "").strip()
        ],
        "本章已通过的大纲目标、主线结果和关键因果链。",
    ]
    repair_scope = dict(payload.get("repair_scope") or {})
    repair_scope.update(
        {
            "rewrite_scope": "paragraph",
            "allowed_changes": [
                "仅改写目标段落及必要的相邻衔接句。",
                "优先把心理活动外化为动作/表情/语气，不改变事实结果。",
            ],
            "forbidden_changes": forbidden_changes,
            "source": "pov_drift_audit",
            "original_severity": original_severity,
        }
    )

    return ContinuityIssue(
        issue_id=issue_id,
        issue_type="pov_intrusion",
        severity="critical",
        confidence=0.7,
        source="local",
        repair_surface="chapter_text",
        status="open",
        blocking=True,
        summary=str(getattr(ticket, "target_summary", "") or payload.get("summary") or ""),
        evidence=evidence,
        location=f"第{paragraph_start}段",
        location_confidence=0.85,
        anchor_type="explicit_para",
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
        evidence_quote=evidence,
        fix_mode="window",
        affected_characters=characters,
        rewrite_scope="paragraph",
        fix_actions=fix_actions,
        repair_scope=repair_scope,
        must_preserve=must_preserve,
        forbidden_changes=forbidden_changes,
        postconditions=[
            {
                "validator_id": "pov_intrusion_validator",
                "description": (
                    "目标段落不再直接进入非 POV 角色内心；若保留其状态，"
                    "必须通过 POV 可观察证据呈现。"
                ),
                "evidence_hint": f"POV角色: {pov_character}"
                if pov_character
                else "target paragraph",
                "required": True,
            }
        ],
        validator_id="pov_intrusion_validator",
        diagnostic_note="projected_from_pov_drift_audit_for_continuity_repair",
    )


def _merge_pov_drift_into_continuity_report(
    continuity_report: Any,
    *,
    pov_tickets: list[Any],
    on_step: Any,
    chapter_number: int,
) -> Any:
    """Inject actionable POV drift issues into continuity repair input."""

    if continuity_report is None or not pov_tickets:
        return continuity_report

    additions: list[ContinuityIssue] = []
    existing_issues = list(getattr(continuity_report, "issues", []) or [])
    existing_ids = {str(getattr(issue, "issue_id", "") or "").strip() for issue in existing_issues}
    for ticket in pov_tickets:
        issue = _project_pov_ticket_to_continuity_issue(ticket)
        if issue is None or issue.issue_id in existing_ids:
            continue
        additions.append(issue)
        existing_ids.add(issue.issue_id)

    if not additions:
        return continuity_report

    current_score = _coerce_float(getattr(continuity_report, "continuity_score", 10.0), 10.0)
    adjusted_score = min(current_score, 8.0)
    summary = str(getattr(continuity_report, "summary", "") or "").strip()
    pov_summary = (
        f"POV drift audit projected {len(additions)} local issue(s) into continuity repair."
    )
    merged_summary = f"{summary}\n\n{pov_summary}" if summary else pov_summary
    merged_issues = [*existing_issues, *additions]
    merged_tickets = [*list(getattr(continuity_report, "repair_tickets", []) or []), *pov_tickets]

    if hasattr(continuity_report, "model_copy"):
        merged = continuity_report.model_copy(
            update={
                "issues": merged_issues,
                "continuity_score": adjusted_score,
                "summary": merged_summary,
                "repair_tickets": merged_tickets,
            }
        )
    else:
        continuity_report.issues = merged_issues
        continuity_report.continuity_score = adjusted_score
        continuity_report.summary = merged_summary
        continuity_report.repair_tickets = merged_tickets
        merged = continuity_report

    on_step(
        "pov_drift_continuity_injected",
        {
            "chapter": chapter_number,
            "ticket_count": len(pov_tickets),
            "injected_issue_count": len(additions),
            "continuity_score_before": current_score,
            "continuity_score_after": adjusted_score,
            "issue_ids": [issue.issue_id for issue in additions],
        },
    )
    return merged


async def _run_mechanical_cleanup_stage(
    *,
    runner: Any,
    bundle: Any,
    prepared: PreparedChapterArtifacts,
    state: _ReviewPhaseState,
    chapter_number: int,
    trace: PipelineTrace,
    on_step: Any,
) -> MechanicalCleanupResult:
    """Run review text cleanup steps while preserving legacy events/stage names."""

    runner = FlowContextAdapter(runner)
    repetition_report: Any | None = None
    pronoun_report: Any | None = None

    _pre_final_dedup_text = state.current_text
    state.current_text = run_final_dedup(
        runner, state.current_text, prepared.packet.previous_chapter_ending
    )
    if state.current_text != _pre_final_dedup_text:
        _record_text_change(
            state,
            stage="final_dedup",
            before_text=_pre_final_dedup_text,
            after_text=state.current_text,
            applied=True,
        )

    _dedup_task = asyncio.to_thread(
        run_self_repetition_check,
        runner,
        state.current_text,
    )
    _pronoun_task = run_pronoun_check(
        runner,
        bundle,
        prepared.packet,
        state.current_text,
        chapter_number,
        trace,
        chapter_plan=prepared.plan,
    )
    _dedup_result, _pronoun_result = await asyncio.gather(_dedup_task, _pronoun_task)
    _dedup_text, repetition_report = _dedup_result
    _pronoun_text, pronoun_report = _pronoun_result
    _pre_self_repetition_text = state.current_text
    state.current_text = _dedup_text
    if state.current_text != _pre_self_repetition_text:
        _record_text_change(
            state,
            stage="self_repetition_dedup",
            before_text=_pre_self_repetition_text,
            after_text=state.current_text,
            applied=True,
        )
    if _pronoun_text != state.current_text and _pronoun_text != _dedup_text:
        _pre_pronoun_check_text = state.current_text
        state.current_text, repetition_report = await asyncio.to_thread(
            run_self_repetition_check,
            runner,
            _pronoun_text,
        )
        if state.current_text != _pre_pronoun_check_text:
            _record_text_change(
                state,
                stage="pronoun_check_rewrite",
                before_text=_pre_pronoun_check_text,
                after_text=state.current_text,
                applied=True,
            )

    try:
        from novel_forge.core.domain.guardrails import fix_non_cjk_leakage, fix_pronouns_mechanical

        char_map: dict[str, dict[str, Any]] = {}
        canon_ctx = prepared.packet.canon_context
        raw_chars = (
            canon_ctx.get("characters", {})
            if isinstance(canon_ctx, dict)
            else {
                name: (
                    char_state_obj.model_dump(mode="json")
                    if hasattr(char_state_obj, "model_dump")
                    else char_state_obj
                )
                for name, char_state_obj in getattr(canon_ctx, "characters", {}).items()
            }
        )
        for name, char_state in raw_chars.items():
            if isinstance(char_state, dict) and char_state.get("gender"):
                char_map[name] = char_state

        pronoun_autofix_mode = str(
            getattr(runner._settings, "pronoun_autofix_mode", "off") or "off"
        )
        if pronoun_autofix_mode == "pov_or_many":
            pov = getattr(bundle.chapter_outline, "pov_character", "") or ""
            _pre_mechanical_pronoun_text = state.current_text
            state.current_text, pronoun_fixes, pronoun_skipped = fix_pronouns_mechanical(
                state.current_text,
                char_map,
                pov,
            )
            if state.current_text != _pre_mechanical_pronoun_text:
                _record_text_change(
                    state,
                    stage="mechanical_pronoun_fix",
                    before_text=_pre_mechanical_pronoun_text,
                    after_text=state.current_text,
                    applied=True,
                )
            if pronoun_fixes > 0 or pronoun_skipped > 0:
                on_step(
                    "mechanical_pronoun_fix",
                    {
                        "chapter": chapter_number,
                        "fixes": pronoun_fixes,
                        "skipped_ambiguous": pronoun_skipped,
                    },
                )
            if pronoun_skipped > 0:
                _logger.warning(
                    "章节 %d 代词机械修复跳过 %d 处歧义替换，建议人工核查。",
                    chapter_number,
                    pronoun_skipped,
                )

        _pre_non_cjk_text = state.current_text
        state.current_text, removed_words = fix_non_cjk_leakage(state.current_text)
        if state.current_text != _pre_non_cjk_text:
            _record_text_change(
                state,
                stage="non_cjk_cleanup",
                before_text=_pre_non_cjk_text,
                after_text=state.current_text,
                applied=True,
            )
        if removed_words:
            on_step(
                "non_cjk_cleanup",
                {"chapter": chapter_number, "removed": removed_words[:10]},
            )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("non_cjk_cleanup step notification failed (non-critical): %s", exc)

    return MechanicalCleanupResult(
        current_text=state.current_text,
        repetition_report=repetition_report,
        pronoun_report=pronoun_report,
    )


async def _run_quality_and_repairs(
    *,
    runner: Any,
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    chapter_number: int,
    state: _ReviewPhaseState,
    resume_progress: ReviewProgressState | None,
    skip_quality: bool,
    skip_causal_repair: bool,
    skip_repair: bool,
    resume_stage: str | None,
    on_step: Any,
) -> _ReviewPhaseState:
    """Phase 3 — Review: quality checks + all repair loops.

    Covers alignment, continuity, causal, reading-power, guard-constraint,
    dedup, and pronoun checks.  Handles its own checkpoint saves
    (``quality_done``, ``causal_repair_done``, ``repair_done``) and resume restore.
    """
    runner = FlowContextAdapter(runner)
    bundle = prepared.bundle

    from novel_forge.core.schemas.review_state import (
        ReviewProgressState as _ReviewProgressState,
    )

    _precomputed_causal: dict[str, Any] = {}
    _precomputed_reading_power: dict[str, Any] = {}
    _precomputed_causal_text = state.current_text

    # ── Resume restore of quality reports ──
    if skip_quality and resume_progress is not None:
        from novel_forge.core.schemas.chapter import (
            AlignmentReport as _AR,
        )
        from novel_forge.core.schemas.chapter import (
            CausalValidationReport as _CVR,
        )
        from novel_forge.core.schemas.chapter import (
            ChapterRepairReport as _CRR,
        )
        from novel_forge.core.schemas.continuity import ContinuityReport as _CR

        state.alignment_report = _AR.model_validate(resume_progress.alignment_report or {})
        state.continuity_report = _CR.model_validate(resume_progress.continuity_report or {})
        state.chapter_repair_report = (
            _CRR.model_validate(resume_progress.chapter_repair_report)
            if resume_progress.chapter_repair_report
            else None
        )
        state.current_text = resume_progress.current_text
        state.total_repair_rounds_used = getattr(resume_progress, "total_repair_rounds_used", 0)

        if state.chapter_repair_report is not None and state.current_text:
            _stored_hash = getattr(state.chapter_repair_report, "source_text_hash", None)
            _current_hash = source_text_hash(state.current_text)
            if _stored_hash and _stored_hash != _current_hash:
                on_step(
                    "chapter_repair_report_dropped_on_resume",
                    {
                        "chapter": chapter_number,
                        "stored_hash": _stored_hash,
                        "current_hash": _current_hash,
                        "reason": "source_text_hash_mismatch_on_resume",
                    },
                )
                state.chapter_repair_report = None
        state.review_warnings = list(resume_progress.warnings or [])
        on_step(
            "resume_from_progress",
            {
                "chapter": chapter_number,
                "completed_stage": resume_stage,
                "skipped": "quality_checks_and_continuity_repair",
            },
        )
    else:
        # ── Precomputed sinks for quality checks ──
        _precomputed_causal_text = state.current_text

        (
            state.alignment_report,
            state.continuity_report,
            state.chapter_repair_report,
        ) = await run_quality_checks(
            runner,
            bundle,
            prepared.packet,
            prepared.bridge,
            prepared.plan,
            state.current_text,
            chapter_number,
            trace,
            window_manager=prepared.window_manager,
            window_config=prepared.window_config,
            memory_hints=prepared.memory_hints,
            precomputed_causal_sink=_precomputed_causal,
            precomputed_reading_power_sink=_precomputed_reading_power,
        )
        state.continuity_report = merge_opening_guard_pending_issues(
            runner,
            state.continuity_report,
            chapter_number=chapter_number,
            on_step=on_step,
        )
        state.continuity_report = normalize_continuity_report_repair_state(state.continuity_report)

        # ── Theme + arc alignment soft-check (warning only) ──────────────
        try:
            from novel_forge.pipeline.long.services.context.source_artifacts import (
                project_stage_source_cards,
            )
            from novel_forge.pipeline.long.services.theme_arc_projection import (
                format_theme_arc_alignment_question,
            )

            _source_slice = getattr(bundle, "chapter_source_slice", None)
            _source_cards = (
                project_stage_source_cards(_source_slice, stage="review")
                if _source_slice is not None
                else {}
            )
            _literary = _source_cards.get("literary_contract", {})
            _theme = _literary.get("theme", {}) if isinstance(_literary, dict) else {}
            if isinstance(_theme, dict) and _theme:
                _ta_ctx = {
                    "theme_focus": {
                        "primary_theme": _theme.get("primary_theme", ""),
                        "theme_list": _theme.get("themes", []),
                        "phase_context": _theme.get("phase_context", {}),
                    },
                    "active_arc_milestones": list(_theme.get("arc_milestones") or []),
                    "source": "literary_contract_v1",
                }
                _alignment_q = format_theme_arc_alignment_question(_ta_ctx)
                if _alignment_q:
                    state.review_warnings.append(f"theme_arc_alignment: {_alignment_q}")
                    on_step(
                        "theme_arc_alignment_check",
                        {
                            "chapter": chapter_number,
                            "question": _alignment_q,
                            "mode": "warning_only",
                            "source": "literary_contract_v1",
                        },
                    )
        except Exception as _ta_exc:
            _logger.debug(
                "theme_arc_alignment_check_skipped | chapter=%d | error=%s",
                chapter_number,
                _ta_exc,
            )

    # ── Build repair thresholds ──
    _cont_max_rounds = max(0, getattr(context.settings, "long_continuity_max_repair_rounds", 1))
    _cont_must_fix_sev = (
        getattr(context.settings, "repair_must_fix_severity", "critical") or "critical"
    ).lower()
    _cont_threshold = getattr(context.settings, "long_continuity_repair_threshold", 8.5)
    _repair_thresholds = build_repair_thresholds(context.settings, context.config)

    # ── Global repair budget ──
    state.baseline_text = state.current_text
    _global_budget = getattr(context.settings, "long_global_repair_budget", 3)

    state.total_rounds_cap = resolve_total_repair_rounds_cap(context.settings)

    if skip_quality:
        _cont_max_rounds = 0

    state.repair_exhausted = False
    _causal_result_local: Any | None = None
    _rp_result_local: ReadingPowerRepairLoopResult | None = None

    if not skip_quality:
        state = await _run_chapter_quality_repair_lane(
            runner=runner,
            context=context,
            prepared=prepared,
            trace=trace,
            chapter_number=chapter_number,
            state=state,
            skip_quality=skip_quality,
            on_step=on_step,
        )
        if state.chapter_quality_repair.get("attempted") and state.chapter_quality_repair.get(
            "text_changed"
        ):
            _precomputed_causal.clear()
            _precomputed_reading_power.clear()
            _precomputed_causal_text = state.current_text

    state = await _run_pov_drift_audit_stage(
        bundle=bundle,
        prepared=prepared,
        current_text=state.current_text,
        chapter_number=chapter_number,
        state=state,
        on_step=on_step,
    )
    state.continuity_report = _merge_pov_drift_into_continuity_report(
        state.continuity_report,
        pov_tickets=state.pov_drift_tickets,
        on_step=on_step,
        chapter_number=chapter_number,
    )
    state.continuity_report = normalize_continuity_report_repair_state(state.continuity_report)

    # ── Total rounds cap: gate continuity dimension ──
    _cont_max_rounds = gate_dimension_rounds(
        policy=CONTINUITY_DIMENSION,
        max_rounds=_cont_max_rounds,
        total_rounds_used=state.total_repair_rounds_used,
        total_rounds_cap=state.total_rounds_cap,
        chapter_number=chapter_number,
        on_step=on_step,
    )

    # ── Continuity repair loop ──
    _cont_loop_result = await run_continuity_repair_v2(
        runner=runner,
        bundle=bundle,
        packet=prepared.packet,
        bridge=prepared.bridge,
        plan=prepared.plan,
        on_step=on_step,
        current_text=state.current_text,
        alignment_report=state.alignment_report,
        continuity_report=state.continuity_report,
        chapter_repair_report=state.chapter_repair_report,
        chapter_number=chapter_number,
        trace=trace,
        repair_thresholds=_repair_thresholds,
        cont_max_rounds=_cont_max_rounds,
        cont_must_fix_sev=_cont_must_fix_sev,
        cont_threshold=_cont_threshold,
        memory_hints=prepared.memory_hints,
    )
    absorb_dimension_result(policy=CONTINUITY_DIMENSION, state=state, result=_cont_loop_result)
    state.continuity_repair = _cont_loop_result.continuity_repair
    state.alignment_report = _cont_loop_result.alignment_report
    state.continuity_report = normalize_continuity_report_repair_state(
        _cont_loop_result.continuity_report
    )
    state.chapter_repair_report = _cont_loop_result.chapter_repair_report
    state.cont_loop_result = _cont_loop_result

    # ── Cross-dimension coordination: causal ──
    _causal_max_rounds = max(0, getattr(context.settings, "long_causal_max_repair_rounds", 2))
    if state.repair_exhausted and _global_budget > 0:
        _causal_max_rounds = min(_causal_max_rounds, max(0, _global_budget - 1))

    _cont_score_for_coord = getattr(state.continuity_report, "continuity_score", 10.0)
    _cont_hard_floor_for_coord = getattr(
        context.settings, "long_continuity_hard_block_threshold", 4.0
    )
    _cross_dim_action = decide_cross_dimension_action(
        CrossDimensionContext(
            upstream_repair_exhausted=_cont_loop_result.repair_exhausted,
            upstream_best_effort_accepted=_cont_loop_result.best_effort_accepted,
            upstream_score=_cont_score_for_coord,
            upstream_hard_floor=_cont_hard_floor_for_coord,
            downstream_dimension="causal",
        )
    )
    state.cross_dim_action = _cross_dim_action
    if _cross_dim_action == CrossDimensionAction.REDUCE_SCOPE:
        _causal_max_rounds = min(_causal_max_rounds, 1)
        on_step(
            "cross_dimension_causal_reduced",
            {
                "chapter": chapter_number,
                "reason": "continuity_best_effort_or_exhausted",
                "causal_max_rounds": _causal_max_rounds,
            },
        )
    elif _cross_dim_action == CrossDimensionAction.SKIP_NON_CRITICAL:
        _causal_max_rounds = 0
        on_step(
            "cross_dimension_causal_skipped",
            {
                "chapter": chapter_number,
                "reason": "continuity_below_hard_floor",
                "continuity_score": _cont_score_for_coord,
            },
        )

    # ── Total rounds cap: gate causal dimension (compresses to remaining) ──
    _causal_max_rounds = gate_dimension_rounds(
        policy=CAUSAL_DIMENSION,
        max_rounds=_causal_max_rounds,
        total_rounds_used=state.total_repair_rounds_used,
        total_rounds_cap=state.total_rounds_cap,
        chapter_number=chapter_number,
        on_step=on_step,
    )

    # ── Alignment repair + dedup + pronoun (only when quality stage ran) ──
    if not skip_quality:
        _alignment_stage = await _run_alignment_repair_stage(
            runner=runner,
            bundle=bundle,
            packet=prepared.packet,
            bridge=prepared.bridge,
            plan=prepared.plan,
            current_text=state.current_text,
            chapter_number=chapter_number,
            alignment_report=state.alignment_report,
            trace=trace,
            on_step=on_step,
            total_rounds_used=state.total_repair_rounds_used,
            total_rounds_cap=state.total_rounds_cap,
        )
        _pre_alignment_text = state.current_text
        state.current_text = _alignment_stage.current_text
        if state.current_text != _pre_alignment_text:
            _record_text_change(
                state,
                stage="alignment_repair",
                before_text=_pre_alignment_text,
                after_text=state.current_text,
                applied=True,
            )
        state.alignment_report = _alignment_stage.alignment_report
        state.total_repair_rounds_used += _alignment_stage.rounds_used
        state.cumulative_change_ratio = _text_change_ratio(state.baseline_text, state.current_text)
        if _alignment_stage.repair_exhausted:
            state.repair_exhausted = True
        if _alignment_stage.warning:
            state.review_warnings.append(_alignment_stage.warning)

        state.mechanical_cleanup_result = await _run_mechanical_cleanup_stage(
            runner=runner,
            bundle=bundle,
            prepared=prepared,
            state=state,
            chapter_number=chapter_number,
            trace=trace,
            on_step=on_step,
        )

        # ── Save progress: quality + continuity repair complete ──
        _rp_data = None
        _rp_path_fn = getattr(bundle.layout, "reading_power_report_path", None)
        if _rp_path_fn is not None:
            _rp_report_path = _rp_path_fn(chapter_number)
            _storage_exists = getattr(context.storage, "exists", None)
            if _storage_exists is not None and _storage_exists(_rp_report_path):
                _rp_data = context.storage.load_json(_rp_report_path)
        await _async_save_review_progress(
            storage=context.storage,
            layout=bundle.layout,
            chapter_number=chapter_number,
            progress=_ReviewProgressState(
                completed_stage="quality_done",
                current_text=state.current_text,
                performed_edits=state.performed_edits,
                total_repair_rounds_used=state.total_repair_rounds_used,
                alignment_report=state.alignment_report.model_dump(mode="json"),
                continuity_report=state.continuity_report.model_dump(mode="json"),
                chapter_repair_report=(
                    state.chapter_repair_report.model_dump(mode="json")
                    if state.chapter_repair_report is not None
                    else None
                ),
                reading_power_report=_rp_data,
                warnings=state.review_warnings,
            ),
        )
    # ── END if not skip_quality ──

    # ── Causal repair ──
    if skip_causal_repair and resume_progress is not None:
        from novel_forge.core.schemas.chapter import CausalValidationReport as _CVR

        state.causal_report = (
            _CVR.model_validate(resume_progress.causal_report)
            if resume_progress.causal_report
            else None
        )
        state.current_text = resume_progress.current_text
        state.total_repair_rounds_used = getattr(resume_progress, "total_repair_rounds_used", 0)
        on_step(
            "resume_from_progress",
            {
                "chapter": chapter_number,
                "completed_stage": resume_stage,
                "skipped": "causal_repair",
            },
        )
    else:
        _pre_causal_cont_score = getattr(state.continuity_report, "continuity_score", 10.0)
        _pre_causal_text = state.current_text
        _pre_causal_alignment = state.alignment_report
        _pre_causal_continuity = state.continuity_report
        _pre_causal_chapter_repair = state.chapter_repair_report
        _initial_causal_report = (
            (
                _precomputed_causal.get("causal_report")
                if not skip_quality and _precomputed_causal_text == state.current_text
                else None
            )
            if not skip_quality
            else None
        )

        _causal_result_local = _causal_high_score_skip_result(
            settings=context.settings,
            initial_causal_report=_initial_causal_report,
            current_text=state.current_text,
            alignment_report=state.alignment_report,
            continuity_report=state.continuity_report,
            chapter_repair_report=state.chapter_repair_report,
            chapter_number=chapter_number,
            max_causal_rounds=_causal_max_rounds,
            on_step=on_step,
        )
        if _causal_result_local is None:
            _causal_result_local = await run_causal_repair_v2(
                runner=runner,
                bundle=bundle,
                packet=prepared.packet,
                bridge=prepared.bridge,
                plan=prepared.plan,
                on_step=on_step,
                current_text=state.current_text,
                alignment_report=state.alignment_report,
                continuity_report=state.continuity_report,
                chapter_repair_report=state.chapter_repair_report,
                chapter_number=chapter_number,
                trace=trace,
                repair_thresholds=_repair_thresholds,
                prev_chapter_ending=getattr(prepared.packet, "previous_chapter_ending", "") or "",
                max_causal_rounds=_causal_max_rounds,
                initial_causal_report=_initial_causal_report,
            )
        state.review_warnings.extend(_causal_result_local.causal_warnings)
        absorb_dimension_result(policy=CAUSAL_DIMENSION, state=state, result=_causal_result_local)
        state.causal_report = _causal_result_local.causal_report
        state.alignment_report = _causal_result_local.alignment_report
        state.continuity_report = _causal_result_local.continuity_report
        state.chapter_repair_report = _causal_result_local.chapter_repair_report
        state.causal_result = _causal_result_local

        # ── Cross-dimension regression check ──
        _post_causal_cont_score = getattr(state.continuity_report, "continuity_score", 10.0)
        _cross_dim_drop = _pre_causal_cont_score - _post_causal_cont_score
        if _cross_dim_drop > 1.5:
            _logger.warning(
                "cross_dimension_regression | causal_repair_dropped_continuity | "
                "before=%.1f after=%.1f drop=%.1f | chapter=%d",
                _pre_causal_cont_score,
                _post_causal_cont_score,
                _cross_dim_drop,
                chapter_number,
            )
            on_step(
                "cross_dimension_regression",
                {
                    "chapter": chapter_number,
                    "triggered_by": "causal_repair",
                    "affected": "continuity",
                    "score_before": round(_pre_causal_cont_score, 1),
                    "score_after": round(_post_causal_cont_score, 1),
                    "drop": round(_cross_dim_drop, 1),
                    "action": "revert_to_pre_causal_text",
                },
            )
            state.current_text = _pre_causal_text
            _record_text_change(
                state,
                stage="causal_repair_rollback",
                before_text=_causal_result_local.current_text,
                after_text=state.current_text,
                applied=False,
                reason="continuity_regression",
            )
            state.alignment_report = _pre_causal_alignment
            state.continuity_report = _pre_causal_continuity
            state.chapter_repair_report = _pre_causal_chapter_repair
            if state.causal_result is not None:
                state.causal_result.applied = False
                state.causal_result.rolled_back = True
            state.repair_exhausted = True

    # ── END causal repair ──

    # ── Save progress: causal repair complete ──
    if not skip_causal_repair:
        _rp_payload_pre: dict[str, Any] | None = None
        _rp_path_fn_pre = getattr(bundle.layout, "reading_power_report_path", None)
        if _rp_path_fn_pre is not None:
            _rp_report_path_pre = _rp_path_fn_pre(chapter_number)
            _storage_exists_pre = getattr(context.storage, "exists", None)
            if _storage_exists_pre is not None and _storage_exists_pre(_rp_report_path_pre):
                _rp_payload_pre = context.storage.load_json(_rp_report_path_pre)
        await _async_save_review_progress(
            storage=context.storage,
            layout=bundle.layout,
            chapter_number=chapter_number,
            progress=_ReviewProgressState(
                completed_stage="causal_repair_done",
                current_text=state.current_text,
                performed_edits=state.performed_edits,
                total_repair_rounds_used=state.total_repair_rounds_used,
                alignment_report=state.alignment_report.model_dump(mode="json"),
                continuity_report=state.continuity_report.model_dump(mode="json"),
                chapter_repair_report=(
                    state.chapter_repair_report.model_dump(mode="json")
                    if state.chapter_repair_report is not None
                    else None
                ),
                causal_report=(
                    state.causal_report.model_dump(mode="json")
                    if state.causal_report is not None
                    else None
                ),
                repair_plan=(
                    state.continuity_repair.repair_plan.model_dump(mode="json")
                    if hasattr(state.continuity_repair, "repair_plan")
                    and state.continuity_repair.repair_plan is not None
                    else None
                ),
                reading_power_report=_rp_payload_pre,
                warnings=state.review_warnings,
            ),
        )

    # ── Reading power repair ──
    if resume_progress is not None and resume_progress.reading_power_report:
        state.final_rp_report = _coerce_reading_power_report(resume_progress.reading_power_report)
    if not skip_repair:
        _skip_rp_for_cap = (
            state.total_rounds_cap > 0 and state.total_repair_rounds_used >= state.total_rounds_cap
        )
        _rp_remaining_rounds = remaining_rounds(
            state.total_repair_rounds_used, state.total_rounds_cap
        )
        _skip_rp_for_upstream = _cross_dim_action == CrossDimensionAction.SKIP_NON_CRITICAL
        if _skip_rp_for_upstream:
            on_step(
                "cross_dimension_rp_skipped",
                {
                    "chapter": chapter_number,
                    "reason": "upstream_continuity_below_hard_floor",
                },
            )
        if _skip_rp_for_cap:
            emit_dimension_cap_skip(
                policy=READING_POWER_DIMENSION,
                total_rounds_used=state.total_repair_rounds_used,
                total_rounds_cap=state.total_rounds_cap,
                chapter_number=chapter_number,
                on_step=on_step,
            )
        try:
            _pre_rp_text = state.current_text
            _pre_rp_alignment_report = state.alignment_report
            _pre_rp_continuity_report = state.continuity_report
            _pre_rp_causal_report = state.causal_report
            _pre_rp_chapter_repair_report = state.chapter_repair_report
            _pre_rp_report = state.final_rp_report
            if _skip_rp_for_cap or _skip_rp_for_upstream:
                _rp_result_local = ReadingPowerRepairLoopResult(
                    current_text=state.current_text,
                    report=None,
                    text_hash=None,
                )
            else:
                _rp_result_local = await run_reading_power_repair_v2(
                    runner=runner,
                    bundle=bundle,
                    packet=prepared.packet,
                    bridge=prepared.bridge,
                    plan=prepared.plan,
                    current_text=state.current_text,
                    chapter_number=chapter_number,
                    trace=trace,
                    window_manager=prepared.window_manager,
                    window_config=prepared.window_config,
                    precomputed_reading_power_report=(
                        _precomputed_reading_power.get("reading_power_report")
                    ),
                    precomputed_reading_power_text_hash=(
                        _precomputed_reading_power.get("source_text_hash")
                    ),
                    max_reading_power_rounds=_rp_remaining_rounds,
                )
            state.current_text = _rp_result_local.current_text
            state.final_rp_report = _rp_result_local.report
            state.total_repair_rounds_used += _rp_result_local.rounds_used
            if _rp_result_local.best_effort_accepted:
                state.review_warnings.append(
                    f"追读力修复已尽力接受：{_rp_result_local.best_effort_reason}"
                )
            state.rp_result = _rp_result_local
            if state.current_text != _pre_rp_text:
                _record_text_change(
                    state,
                    stage="reading_power_repair",
                    before_text=_pre_rp_text,
                    after_text=state.current_text,
                    applied=True,
                )
                try:
                    (
                        state.alignment_report,
                        state.continuity_report,
                        state.causal_report,
                        state.chapter_repair_report,
                        state.final_rp_report,
                    ) = await _ensure_reports_current_after_text_change(
                        runner=runner,
                        bundle=bundle,
                        packet=prepared.packet,
                        bridge=prepared.bridge,
                        plan=prepared.plan,
                        current_text=state.current_text,
                        chapter_number=chapter_number,
                        trace=trace,
                        alignment_report=state.alignment_report,
                        continuity_report=state.continuity_report,
                        causal_report=state.causal_report,
                        chapter_repair_report=state.chapter_repair_report,
                        reading_power_report=state.final_rp_report,
                        window_manager=prepared.window_manager,
                        window_config=prepared.window_config,
                        stale_reason="reading_power_repair_text_changed",
                    )
                except Exception as _rp_refresh_exc:
                    _attempted_rp_text = state.current_text
                    _logger.warning(
                        "reading_power_repair_report_refresh_failed | chapter=%d | error=%s",
                        chapter_number,
                        _rp_refresh_exc,
                    )
                    state.current_text = _pre_rp_text
                    _record_text_change(
                        state,
                        stage="reading_power_repair_rollback",
                        before_text=_attempted_rp_text,
                        after_text=state.current_text,
                        applied=False,
                        reason="report_refresh_failed",
                    )
                    state.alignment_report = _pre_rp_alignment_report
                    state.continuity_report = _pre_rp_continuity_report
                    state.causal_report = _pre_rp_causal_report
                    state.chapter_repair_report = _pre_rp_chapter_repair_report
                    state.final_rp_report = _pre_rp_report
                    _rp_result_local.current_text = _pre_rp_text
                    _rp_result_local.report = _pre_rp_report
                    _rp_result_local.text_hash = source_text_hash(_pre_rp_text)
                    _rp_result_local.applied = False
                    _rp_result_local.rolled_back = True
                    state.review_warnings.append(
                        "追读力修复已回滚：修复后质量报告刷新失败，保留修复前正文。"
                    )
                    on_step(
                        "reading_power_repair_report_refresh_failed",
                        {
                            "chapter": chapter_number,
                            "reason": "reading_power_repair_text_changed",
                            "error": str(_rp_refresh_exc),
                            "before_hash": source_text_hash(_pre_rp_text),
                            "after_hash": source_text_hash(_attempted_rp_text),
                            "rounds_used": _rp_result_local.rounds_used,
                        },
                    )
            state.cumulative_change_ratio = _text_change_ratio(
                state.baseline_text, state.current_text
            )
        except Exception as _rp_exc:
            _logger.warning(
                "reading_power_repair_loop_failed | chapter=%d | error=%s",
                chapter_number,
                _rp_exc,
            )

    if not skip_repair:
        _rp_payload: dict[str, Any] | None = None
        if state.final_rp_report is not None:
            try:
                _rp_payload = (
                    state.final_rp_report.model_dump(mode="json")
                    if hasattr(state.final_rp_report, "model_dump")
                    else dict(state.final_rp_report)
                )
                _rp_hash = getattr(state.rp_result, "text_hash", None)
                if _rp_hash:
                    _rp_payload["source_text_hash"] = _rp_hash
                _rp_payload["pipeline_stage"] = "rp_repair_checkpoint"
            except Exception as _rp_dump_exc:
                _logger.warning(
                    "reading_power_checkpoint_dump_failed | chapter=%d | %s",
                    chapter_number,
                    _rp_dump_exc,
                )
                _rp_payload = None
        await _async_save_review_progress(
            storage=context.storage,
            layout=bundle.layout,
            chapter_number=chapter_number,
            progress=_ReviewProgressState(
                completed_stage="repair_done",
                current_text=state.current_text,
                performed_edits=state.performed_edits,
                total_repair_rounds_used=state.total_repair_rounds_used,
                alignment_report=state.alignment_report.model_dump(mode="json"),
                continuity_report=state.continuity_report.model_dump(mode="json"),
                chapter_repair_report=(
                    state.chapter_repair_report.model_dump(mode="json")
                    if state.chapter_repair_report is not None
                    else None
                ),
                causal_report=(
                    state.causal_report.model_dump(mode="json")
                    if state.causal_report is not None
                    else None
                ),
                repair_plan=(
                    state.continuity_repair.repair_plan.model_dump(mode="json")
                    if hasattr(state.continuity_repair, "repair_plan")
                    and state.continuity_repair.repair_plan is not None
                    else None
                ),
                reading_power_report=_rp_payload,
                warnings=state.review_warnings,
            ),
        )
        await _persist_stage_artifact_safely(
            context=context,
            bundle=bundle,
            chapter_number=chapter_number,
            artifact_type="repair",
            previous_artifact_type="review",
            payload={
                "text_hash": source_text_hash(state.current_text),
                "text_chars": len(state.current_text),
                "performed_edits": state.performed_edits,
                "total_repair_rounds_used": state.total_repair_rounds_used,
                "total_rounds_cap": state.total_rounds_cap,
                "repair_exhausted": state.repair_exhausted,
                "cumulative_change_ratio": round(float(state.cumulative_change_ratio or 0.0), 4),
                "text_change_history": list(state.text_change_history or []),
                "reports": {
                    "alignment": _artifact_report_summary(state.alignment_report),
                    "continuity": _artifact_report_summary(state.continuity_report),
                    "chapter_repair": _artifact_report_summary(state.chapter_repair_report),
                    "causal": _artifact_report_summary(state.causal_report),
                    "reading_power": _artifact_report_summary(state.final_rp_report),
                },
            },
            event_ledger=[
                {
                    "event": "repair_done",
                    "total_repair_rounds_used": state.total_repair_rounds_used,
                    "repair_exhausted": state.repair_exhausted,
                }
            ],
        )

    return state


async def _run_polish(
    *,
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    state: _ReviewPhaseState,
    trace: PipelineTrace,
    eval_report: EvalReport | None = None,
) -> _ReviewPhaseState:
    """Phase 4 — Polish: post-repair prose refinement."""
    force_polish = bool(getattr(context.settings, "long_polish_enabled", False))
    auto_threshold = _coerce_float(
        getattr(context.settings, "long_polish_auto_trigger_threshold", 7.0),
        0.0,
    )
    auto_polish = False
    eval_is_fallback = _eval_report_is_fallback(eval_report)
    if eval_is_fallback:
        warning = "章节综合评估暂不可用：保留当前已验证正文，跳过基于不可信默认分的自动润色。"
        if warning not in state.review_warnings:
            state.review_warnings.append(warning)
        chapter_outline = getattr(getattr(prepared, "bundle", None), "chapter_outline", None)
        context.on_step(
            "polish_auto_trigger_skipped",
            {
                "chapter": int(getattr(chapter_outline, "chapter_number", 0) or 0),
                "reason": "evaluation_fallback",
                "fallback_reason": str(getattr(eval_report, "fallback_reason", "") or ""),
            },
        )
    if (
        not force_polish
        and not eval_is_fallback
        and auto_threshold > 0.0
        and eval_report is not None
    ):
        auto_polish = (
            _coerce_float(getattr(eval_report, "overall_score", 10.0), 10.0) < auto_threshold
        )

    if force_polish or auto_polish:
        _pre_polish_text = state.current_text
        trigger_reason = "long_polish_enabled" if force_polish else "auto_score_below_threshold"
        (
            state.current_text,
            state.post_repair_polish_modified_text,
        ) = await run_post_repair_polish_layer(
            context,
            prepared,
            state.current_text,
            trace,
            chapter_repair_report=state.chapter_repair_report,
            continuity_report=state.continuity_report,
            causal_report=state.causal_report,
            reading_power_report=state.final_rp_report,
            warnings=state.review_warnings,
            trigger_reason=trigger_reason,
        )
        state.post_repair_polish_modified_text = bool(
            state.post_repair_polish_modified_text and state.current_text != _pre_polish_text
        )
        if state.post_repair_polish_modified_text:
            _record_text_change(
                state,
                stage="post_repair_polish",
                before_text=_pre_polish_text,
                after_text=state.current_text,
                applied=True,
                reason=trigger_reason,
            )
        bundle = getattr(prepared, "bundle", None)
        chapter_outline = getattr(bundle, "chapter_outline", None)
        chapter_number = int(getattr(chapter_outline, "chapter_number", 0) or 0)
        if bundle is not None and chapter_number > 0:
            await _persist_stage_artifact_safely(
                context=context,
                bundle=bundle,
                chapter_number=chapter_number,
                artifact_type="polish",
                previous_artifact_type="repair",
                payload={
                    "text_hash": source_text_hash(state.current_text),
                    "text_chars": len(state.current_text),
                    "changed": state.post_repair_polish_modified_text,
                    "trigger_reason": trigger_reason,
                    "pre_polish_text_hash": source_text_hash(_pre_polish_text),
                    "eval_overall_score": (
                        getattr(eval_report, "overall_score", None)
                        if eval_report is not None
                        else None
                    ),
                },
                event_ledger=[
                    {
                        "event": "polish_done",
                        "changed": state.post_repair_polish_modified_text,
                        "trigger_reason": trigger_reason,
                    }
                ],
            )
    return state


async def review_chapter_draft(
    context: ChapterExecutionContext,
    *,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    include_evaluation: bool,
    emit_evaluation_step: bool = True,
    persist_evaluation: bool = True,
    resume_progress: ReviewProgressState | None = None,
) -> ChapterReviewArtifacts:
    """Run the full review-stage body before final persistence.

    Despite the historical name, this stage does more than "review" a draft:
    it generates/edits the draft when needed, runs quality audits, applies
    bounded repair loops, extracts canon/evaluation artifacts, and returns the
    accepted pre-finalize artifact bundle. The actual archive write happens in
    ``finalize_chapter_result`` after hard gates have passed.

    If *resume_progress* is provided, the pipeline skips stages that have
    already been completed and resumes from the next stage.

    On cancellation (CancelledError / KeyboardInterrupt), an emergency
    checkpoint is saved so that the next invocation can resume from the
    last completed stage rather than re-doing all work from scratch.
    """
    runner = FlowContextAdapter(context)
    bundle = prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    _on_step = _make_on_step_with_tokens(context.on_step, trace)
    _emit_input_integrity_check(
        context,
        stage="review_input",
        chapter_number=chapter_number,
        bundle=bundle,
        packet=prepared.packet,
        bridge=prepared.bridge,
        plan=prepared.plan,
        block_on_error=True,
    )
    if (
        getattr(context.config, "writing_mode", "whole_chapter") == "scene_level"
        and prepared.scene_plan_validation_report is not None
        and not bool(prepared.scene_plan_validation_report.get("valid"))
    ):
        issues = list(prepared.scene_plan_validation_report.get("issues", []) or [])
        messages = [
            str(item.get("message") or item.get("summary") or item.get("code"))
            for item in issues
            if isinstance(item, dict)
        ]
        raise ConsistencyViolationError(
            messages or ["场景计划验证未通过，已停止在计划阶段。"],
            violation_kind="plan_fixable",
            failed_stage="scene_plan_validation",
            replan_target=RecoveryTarget.PLAN,
        )

    _resume = _review_resume_plan(resume_progress.completed_stage if resume_progress else None)
    _resume_stage = _resume.stage

    from novel_forge.core.schemas.review_state import (
        ReviewProgressState as _ReviewProgressState,
    )
    from novel_forge.core.schemas.review_state import (
        clear_review_progress,
    )

    state = await _generate_and_wave(
        runner=runner,
        context=context,
        prepared=prepared,
        trace=trace,
        chapter_number=chapter_number,
        resume_progress=resume_progress,
        skip_draft=_resume.skip_draft,
        resume_stage=_resume_stage,
        on_step=_on_step,
    )

    state = await _run_quality_and_repairs(
        runner=runner,
        context=context,
        prepared=prepared,
        trace=trace,
        chapter_number=chapter_number,
        state=state,
        resume_progress=resume_progress,
        skip_quality=_resume.skip_quality,
        skip_causal_repair=_resume.skip_causal_repair,
        skip_repair=_resume.skip_repair,
        resume_stage=_resume_stage,
        on_step=_on_step,
    )

    pre_polish_eval_report: EvalReport | None = None
    if (
        include_evaluation
        and _resume.run_text_refinement
        and _should_include_pre_final_evaluation(context.settings)
    ):
        _quality_eval = (
            state.quality_lane_eval_report
            if state.quality_lane_eval_text_hash == source_text_hash(state.current_text)
            else None
        )
        pre_polish_eval_report = await evaluate_chapter_text(
            context,
            bundle=bundle,
            chapter_number=chapter_number,
            current_text=state.current_text,
            trace=trace,
            emit_step=False,
            persist=False,
            plan=prepared.plan,
            reuse_report=_quality_eval,
            reuse_label="quality_lane",
        )

    if _resume.run_text_refinement:
        state = await _run_polish(
            context=context,
            prepared=prepared,
            state=state,
            trace=trace,
            eval_report=pre_polish_eval_report,
        )

    current_text = state.current_text
    performed_edits = state.performed_edits
    alignment_report = state.alignment_report
    continuity_report = state.continuity_report
    chapter_repair_report = state.chapter_repair_report
    causal_report = state.causal_report
    continuity_repair = state.continuity_repair
    review_warnings = state.review_warnings
    _repair_exhausted = state.repair_exhausted
    _total_repair_rounds_used = state.total_repair_rounds_used
    _total_rounds_cap = state.total_rounds_cap
    _cumulative_change_ratio = state.cumulative_change_ratio
    _cont_loop_result = state.cont_loop_result
    _causal_result = state.causal_result
    _rp_result = state.rp_result
    _final_rp_report = state.final_rp_report
    _post_repair_polish_modified_text = state.post_repair_polish_modified_text
    _humanize_modified_text = state.humanize_modified_text

    # Word-count polishing is intentionally deferred until the end of the
    # chapter pipeline, after the narrative quality gates have passed.
    _wc_archive_bypass = False

    # ── Resume from canon_done checkpoint: skip re-extraction if outcome is cached ──
    outcome = None
    _eval_task: asyncio.Task[EvalReport] | None = None  # may be started in parallel with extract
    if _resume.skip_extract and resume_progress is not None:
        _co_path = bundle.layout.chapter_canon_outcome_path(chapter_number)
        try:
            _co_data = context.storage.load_json(_co_path)
            from novel_forge.core.schemas.chapter import ChapterOutcome as _ChapterOutcome

            outcome = _ChapterOutcome.model_validate(_co_data)
            _on_step(
                "resume_from_progress",
                {
                    "chapter": chapter_number,
                    "completed_stage": _resume_stage,
                    "skipped": "extract_canon",
                },
            )
        except Exception as _load_exc:
            _logger.warning(
                "canon_outcome_load_failed, re-extracting | chapter=%d | %s",
                chapter_number,
                _load_exc,
            )
            outcome = None  # Fall through to normal extraction

    if outcome is None:
        _emit_input_integrity_check(
            context,
            stage="extract_input",
            chapter_number=chapter_number,
            bundle=bundle,
            packet=prepared.packet,
            bridge=prepared.bridge,
            plan=prepared.plan,
            chapter_text=current_text,
            block_on_error=True,
        )
        _on_step(
            "extract_canon",
            {"chapter": chapter_number, "status": "starting"},
        )

        # ── Parallel execution: extract canon ∥ evaluate (independent tasks) ──
        # extract_and_validate produces canon data from text while
        # evaluate_chapter_text scores text quality — no data dependency.
        # The parallel eval task stays side-effect free until extract succeeds.
        # This prevents stale eval files/events when canon extraction fails.
        if include_evaluation:
            _eval_task = asyncio.create_task(
                evaluate_chapter_text(
                    context,
                    bundle=bundle,
                    chapter_number=chapter_number,
                    current_text=current_text,
                    trace=trace,
                    emit_step=False,
                    persist=False,
                    plan=prepared.plan,
                    reuse_report=pre_polish_eval_report,
                    reuse_label="pre_polish",
                )
            )

        try:
            outcome = await extract_and_validate(
                runner,
                bundle,
                prepared.packet,
                prepared.bridge,
                prepared.plan,
                current_text,
                chapter_number,
                trace,
                continuity_report,
                repair_exhausted=_repair_exhausted,
            )
        except BaseException:
            if _eval_task is not None and not _eval_task.done():
                _eval_task.cancel()
            raise

        # ── Save progress: canon extraction complete ──────────────────────────
        # Persists the outcome so a subsequent cancellation at canon_extract_retry
        # or later can resume without re-running the model call.
        # Each model_dump runs in its own try block so a single serialization
        # failure (e.g. an unexpected nested field) degrades that field to None
        # instead of dropping the entire checkpoint.
        def _safe_dump(value: Any, *, field_name: str) -> dict[str, Any] | None:
            if value is None:
                return None
            try:
                return cast(dict[str, Any], value.model_dump(mode="json"))
            except Exception as dump_exc:
                _logger.warning(
                    "canon_done_field_dump_failed | chapter=%d | field=%s | %s",
                    chapter_number,
                    field_name,
                    dump_exc,
                )
                return None

        alignment_dump = _safe_dump(alignment_report, field_name="alignment_report")
        continuity_dump = _safe_dump(continuity_report, field_name="continuity_report")
        chapter_repair_dump = _safe_dump(chapter_repair_report, field_name="chapter_repair_report")
        causal_dump = _safe_dump(causal_report, field_name="causal_report")
        repair_plan_dump: dict[str, Any] | None = None
        if (
            continuity_repair is not None
            and hasattr(continuity_repair, "repair_plan")
            and continuity_repair.repair_plan is not None
        ):
            try:
                repair_plan_dump = continuity_repair.repair_plan.model_dump(mode="json")
            except Exception as plan_exc:
                _logger.warning(
                    "canon_done_repair_plan_dump_failed | chapter=%d | %s",
                    chapter_number,
                    plan_exc,
                )

        try:
            context.storage.save_json(
                bundle.layout.chapter_canon_outcome_path(chapter_number),
                outcome.model_dump(mode="json"),
            )
            await _async_save_review_progress(
                storage=context.storage,
                layout=bundle.layout,
                chapter_number=chapter_number,
                progress=_ReviewProgressState(
                    completed_stage="canon_done",
                    current_text=current_text,
                    performed_edits=performed_edits,
                    total_repair_rounds_used=_total_repair_rounds_used,
                    alignment_report=alignment_dump,
                    continuity_report=continuity_dump,
                    chapter_repair_report=chapter_repair_dump,
                    causal_report=causal_dump,
                    repair_plan=repair_plan_dump,
                    warnings=review_warnings,
                ),
            )
        except Exception as _save_exc:
            _logger.warning(
                "canon_done_checkpoint_failed | chapter=%d | %s",
                chapter_number,
                _save_exc,
            )

    eval_report: EvalReport | None = None
    if include_evaluation:
        if (
            resume_progress is not None
            and _resume_stage in {"refinement_done", "final_verify_done"}
            and resume_progress.eval_report is not None
        ):
            eval_report = EvalReport.model_validate(resume_progress.eval_report)
            _on_step(
                "resume_from_progress",
                {
                    "chapter": chapter_number,
                    "completed_stage": _resume_stage,
                    "skipped": "evaluate_final_text",
                    "source_text_hash": source_text_hash(current_text),
                },
            )
        elif _eval_task is not None:
            eval_report = await _eval_task
            if persist_evaluation:
                _persist_eval_report_after_extract(
                    context,
                    bundle=bundle,
                    chapter_number=chapter_number,
                    eval_report=eval_report,
                )
            if emit_evaluation_step:
                context.on_step("evaluate", eval_report.model_dump(mode="json"))
        else:
            eval_report = await evaluate_chapter_text(
                context,
                bundle=bundle,
                chapter_number=chapter_number,
                current_text=current_text,
                trace=trace,
                emit_step=emit_evaluation_step,
                persist=persist_evaluation,
                plan=prepared.plan,
                reuse_report=pre_polish_eval_report,
                reuse_label="pre_polish",
            )

    # ── Run eval report checks (word count, revelation density, repair suggestions) ──
    word_count_warning = build_word_count_warning(
        current_text,
        int(getattr(bundle.chapter_outline, "expected_word_count", 0) or 0),
    )
    if word_count_warning:
        review_warnings.append(word_count_warning)
        _on_step(
            "word_count_warning",
            {
                "chapter": chapter_number,
                "message": word_count_warning,
                "current_word_count": count_chapter_words(current_text),
                "target_word_count": int(
                    getattr(bundle.chapter_outline, "expected_word_count", 0) or 0
                ),
            },
        )

    revelation_warning = build_revelation_density_warning(
        current_text,
        max_revelations=context.settings.max_revelations_per_chapter,
    )
    if revelation_warning:
        review_warnings.append(revelation_warning)
        _on_step(
            "revelation_density_warning",
            {"chapter": chapter_number, "message": revelation_warning},
        )

    if eval_report is not None and not _eval_report_is_fallback(eval_report):
        repair_suggestion_warnings = _build_eval_repair_warnings(eval_report)
        for w in repair_suggestion_warnings:
            if w not in review_warnings:
                review_warnings.append(w)
        if repair_suggestion_warnings:
            _on_step(
                "repair_suggestion_warnings",
                {
                    "chapter": chapter_number,
                    "count": len(repair_suggestion_warnings),
                },
            )

    repair_metrics_payload = _build_repair_metrics_payload(
        chapter_number=chapter_number,
        continuity_result=_cont_loop_result,
        continuity_report=continuity_report,
        causal_result=_causal_result,
        causal_report=causal_report,
        reading_power_result=_rp_result,
        total_rounds_used=_total_repair_rounds_used,
        total_rounds_cap=_total_rounds_cap,
        cumulative_change_ratio=_cumulative_change_ratio,
        repair_exhausted=_repair_exhausted,
        skipped_quality_stage=_resume.skip_quality,
        draft_meta=state.draft_meta,
        wave_meta=state.wave_meta,
        pre_wave_chapter_repair_report=state.pre_wave_chapter_repair_report,
        performed_edits=performed_edits,
        writing_mode=str(getattr(context.config, "writing_mode", "whole_chapter") or ""),
        wave_integrity=state.wave_integrity,
        chapter_quality_repair=state.chapter_quality_repair,
        current_text=current_text,
        eval_report=eval_report,
        text_change_history=state.text_change_history,
    )
    _on_step("repair_metrics", repair_metrics_payload)
    _persist_repair_metrics_report(
        context=context,
        bundle=bundle,
        chapter_number=chapter_number,
        payload=repair_metrics_payload,
        on_step=_on_step,
    )

    # Under the single-final-verify rollout, review progress remains the
    # recovery anchor until final verification and idempotent commit succeed.
    # Legacy mode keeps its historical cleanup timing unchanged.
    if not bool(getattr(context.settings, "long_single_final_verify_enabled", False)):
        clear_review_progress(context.storage, bundle.layout, chapter_number)
        try:
            _co_path = bundle.layout.chapter_canon_outcome_path(chapter_number)
            if _co_path.exists():
                _co_path.unlink()
        except Exception:
            pass

    # Determine repair_plan: from resume progress or from live repair
    if resume_progress is not None and resume_progress.repair_plan is not None:
        _final_repair_plan = RepairPlan.model_validate(resume_progress.repair_plan)
    elif hasattr(continuity_repair, "repair_plan"):
        _final_repair_plan = continuity_repair.repair_plan
    else:
        _final_repair_plan = RepairPlan(no_op=True)

    review_artifacts = await _finalize_review_artifacts(
        context,
        prepared=prepared,
        trace=trace,
        current_text=current_text,
        performed_edits=performed_edits,
        outcome=outcome,
        alignment_report=alignment_report,
        chapter_repair_report=chapter_repair_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
        repair_plan=_final_repair_plan,
        eval_report=eval_report,
        review_warnings=review_warnings,
        reading_power_report=_final_rp_report,
        allow_word_count_archive_bypass=_wc_archive_bypass,
        total_repair_rounds_used=_total_repair_rounds_used,
        post_repair_polish_modified_text=_post_repair_polish_modified_text,
        humanize_modified_text=_humanize_modified_text,
        include_evaluation=include_evaluation,
        emit_evaluation_step=emit_evaluation_step,
        persist_evaluation=persist_evaluation,
        pov_drift_findings=state.pov_drift_findings,
        pov_drift_tickets=state.pov_drift_tickets,
    )
    if resume_progress is not None and _resume_stage in {
        "refinement_done",
        "final_verify_done",
    }:
        review_artifacts = dataclasses.replace(
            review_artifacts,
            refinement_done=True,
            final_verify_done=_resume_stage == "final_verify_done",
        )
    return review_artifacts


def _build_ai_flavor_report(chapter_text: str) -> Any:
    """Build a HumanizeReport-shaped object from deterministic prescreen.

    Uses ``HumanizeScanStep.prescreen_text`` (regex-only, no LLM) so the
    ai_flavor gate stays deterministic and free of external IO. The returned
    object exposes ``.pattern_hits`` for ``QualityGate.check_ai_flavor``.

    Returns ``None`` when the text is empty so the gate's None-handling
    path keeps the check out of the report entirely.
    """
    if not chapter_text:
        return None
    try:
        from novel_forge.pipeline.steps.humanize_scan_step import HumanizeScanStep
    except Exception:
        return None

    class _Hit:
        __slots__ = ("pattern_id", "severity", "confidence")

        def __init__(self, d: dict[str, Any]) -> None:
            self.pattern_id = str(d.get("pattern_id", "unknown"))
            self.severity = str(d.get("severity", "medium"))
            try:
                self.confidence = float(d.get("confidence", 0.8) or 0.8)
            except (TypeError, ValueError):
                self.confidence = 0.8

    class _Adapter:
        def __init__(self, hits: list[_Hit]) -> None:
            self.pattern_hits = hits

    try:
        payload = HumanizeScanStep.prescreen_text(chapter_text, filter_dialogue=True)
    except Exception:
        return None

    return _Adapter(hits=[_Hit(p) for p in payload])


@dataclass(frozen=True)
class _BackstoryRevealReport:
    cumulative: dict[str, dict[str, Any]]


def _object_value(value: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def _protagonist_names_from_bundle(bundle: Any) -> list[str]:
    character_bible = getattr(bundle, "character_bible", None)
    characters = getattr(character_bible, "characters", []) or []
    names: list[str] = []
    for profile in characters:
        name = str(_object_value(profile, "name", "") or "").strip()
        role = str(_object_value(profile, "role", "") or "").strip().lower()
        if not name:
            continue
        if role in _PROTAGONIST_ROLE_MARKERS or "主角" in role:
            names.append(name)
    return names


def _backstory_keywords(spec: Any) -> tuple[str, list[str]]:
    topic = str(_object_value(spec, "topic", "") or "").strip()
    raw_triggers = _object_value(spec, "triggers", []) or []
    triggers = (
        [str(item or "").strip() for item in raw_triggers if str(item or "").strip()]
        if isinstance(raw_triggers, list)
        else []
    )
    deduped: list[str] = []
    for keyword in triggers:
        if keyword and keyword not in deduped:
            deduped.append(keyword)
    return topic, deduped


def _load_prior_chapter_texts(layout: Any, chapter_number: int) -> list[str]:
    chapter_path = getattr(layout, "chapter_path", None)
    if not callable(chapter_path) or chapter_number <= 1:
        return []

    texts: list[str] = []
    for num in range(1, chapter_number):
        try:
            path = chapter_path(num)
            if path.exists():
                texts.append(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return texts


def _count_backstory_keyword_segments(
    texts: list[str],
    topic: str,
    triggers: list[str],
) -> tuple[int, int]:
    if not topic and not triggers:
        return 0, 0

    char_count = 0
    hit_count = 0
    seen: set[tuple[int, str]] = set()
    for text_index, text in enumerate(texts):
        for match in _BACKSTORY_SEGMENT_RE.finditer(text):
            segment = match.group(0).strip()
            if not segment:
                continue
            trigger_hits = [trigger for trigger in triggers if trigger in segment]
            has_topic_hit = bool(topic and topic in segment)
            has_trigger_evidence = (
                len(trigger_hits) >= 2 if len(triggers) >= 2 else bool(trigger_hits)
            )
            if not has_topic_hit and not has_trigger_evidence:
                continue
            key = (text_index, segment)
            if key in seen:
                continue
            seen.add(key)
            char_count += len(segment)
            hit_count += 1
    return char_count, hit_count


def _build_backstory_reveal_report(
    *,
    backstory_reveals: list[Any],
    current_text: str,
    layout: Any,
    chapter_number: int,
) -> _BackstoryRevealReport:
    texts = [
        *_load_prior_chapter_texts(layout, chapter_number),
        current_text or "",
    ]
    cumulative: dict[str, dict[str, Any]] = {}
    for spec in backstory_reveals:
        topic, triggers = _backstory_keywords(spec)
        if not topic:
            continue
        char_count, hit_count = _count_backstory_keyword_segments(texts, topic, triggers)
        cumulative[topic] = {
            "current_word_count": char_count,
            "keyword_hit_count": hit_count,
            "keywords": [topic, *triggers],
        }
    return _BackstoryRevealReport(cumulative=cumulative)


def _build_quality_gate(
    review: ChapterReviewArtifacts,
    *,
    settings: Any,
    target_word_count: int,
) -> QualityGate:
    """Build and populate a QualityGate from review artifacts."""
    gate = QualityGate(
        revelation_max=settings.max_revelations_per_chapter,
    )
    # Keep score coercion consistent with the final archive hard gate.  The
    # QualityGate methods still own their respective issue/severity checks.
    dimension_scores = extract_dimension_scores(
        eval_report=review.eval_report,
        continuity_report=review.continuity_report,
        causal_report=review.causal_report,
    )
    if review.eval_report is not None:
        eval_hash = str(getattr(review.eval_report, "source_text_hash", "") or "").strip()
        current_hash = source_text_hash(review.current_text)
        if eval_hash and eval_hash != current_hash:
            gate._checks.append(
                QualityCheckResult(
                    dimension="eval_score_stale",
                    score=0.0,
                    threshold=0.0,
                    passed=True,
                    message="",
                    details={
                        "diagnostic_only": True,
                        "reason": "source_text_hash_mismatch",
                        "eval_source_text_hash": eval_hash,
                        "current_text_hash": current_hash,
                    },
                )
            )
        else:
            gate.check_eval(
                SimpleNamespace(
                    overall_score=dimension_scores["eval"],
                    score_confidence=getattr(review.eval_report, "score_confidence", ""),
                )
            )
            plot_progression_floor = _coerce_float(
                getattr(settings, "long_plot_progression_quality_floor", 5.5),
                5.5,
            )
            if plot_progression_floor > 0.0:
                gate.check_eval_dimension(
                    review.eval_report,
                    "plot_progression",
                    plot_progression_floor,
                )
    if target_word_count > 0 and word_count_archive_gate_enabled(settings):
        wc = count_chapter_words(review.current_text)
        gate.check_word_count(wc, target_word_count)
    gate.check_alignment(review.alignment_report)
    if review.continuity_report is not None:
        gate.check_continuity(
            SimpleNamespace(
                continuity_score=dimension_scores["continuity"],
                issues=getattr(review.continuity_report, "issues", []),
            )
        )
    else:
        gate.check_continuity(None)
    if review.causal_report is not None:
        gate.check_causal(
            SimpleNamespace(
                causal_score=dimension_scores["causal"],
                issues=getattr(review.causal_report, "issues", []),
            )
        )
    gate.check_chapter_quality(review.chapter_repair_report)
    rev_result = check_revelation_density(
        review.current_text, max_revelations=settings.max_revelations_per_chapter
    )
    gate.check_revelation_density(rev_result["revelation_count"])
    # ── AI-flavor check (added in M2 — see docs/ai_flavor_quality.md) ──
    if bool(getattr(settings, "long_ai_flavor_gate_enabled", True)):
        gate.check_ai_flavor(_build_ai_flavor_report(review.current_text))
    bundle = getattr(review.prepared, "bundle", None)
    backstory_reveals = list(getattr(bundle, "backstory_reveals", []) or [])
    if backstory_reveals:
        chapter_number = int(
            getattr(getattr(bundle, "chapter_outline", None), "chapter_number", 0) or 0
        )
        if chapter_number > 0:
            gate.check_backstory_reveals(
                backstory_reveals=backstory_reveals,
                chapter_number=chapter_number,
                backstory_report=_build_backstory_reveal_report(
                    backstory_reveals=backstory_reveals,
                    current_text=review.current_text,
                    layout=getattr(bundle, "layout", None),
                    chapter_number=chapter_number,
                ),
            )
    if review.reading_power_report is not None:
        gate.check_reading_power(review.reading_power_report)
    try:
        narrative_context = getattr(review.prepared.packet, "narrative_context", None)
        target_rhythm = (
            getattr(narrative_context, "target_chapter_rhythm", None)
            if narrative_context is not None
            else None
        )
        if target_rhythm:
            from novel_forge.core.utils.rhythm_metrics import compute_chapter_rhythm_signature

            actual_rhythm = compute_chapter_rhythm_signature(review.current_text)
            gate.check_rhythm_curve(actual_rhythm, target_rhythm)
    except Exception as exc:
        _logger.debug(
            "rhythm_curve_quality_check_skipped | chapter=%s | error=%s",
            getattr(review.prepared.bundle.chapter_outline, "chapter_number", ""),
            exc,
        )
    if review.guard_compliance_report is not None:
        rate_raw = review.guard_compliance_report.get("overall_compliance_rate")
        try:
            rate = float(rate_raw) if rate_raw is not None else 1.0
        except (TypeError, ValueError):
            rate = 1.0
        score = max(0.0, min(10.0, rate * 10.0))
        actionable_low = guard_report_has_actionable_low_compliance(review.guard_compliance_report)
        gate._checks.append(
            QualityCheckResult(
                dimension="guard",
                score=round(score, 1),
                threshold=8.0,
                passed=not actionable_low,
                message="" if not actionable_low else f"AI 护栏合规率 {rate:.0%}，存在可修复违约",
                details={
                    "overall_compliance_rate": rate_raw,
                    "actionable_violation_count": review.guard_compliance_report.get(
                        "actionable_violation_count",
                        0,
                    ),
                },
            )
        )
    knowledge_findings = [
        finding
        for finding in list(getattr(review, "review_findings", []) or [])
        if str(getattr(finding, "dimension", "") or "").strip().lower() == "knowledge_boundary"
    ]
    if knowledge_findings:
        gate.check_review_findings(knowledge_findings, dimension="knowledge_boundary")

    world_rule_findings = [
        finding
        for finding in list(getattr(review, "review_findings", []) or [])
        if str(getattr(finding, "dimension", "") or "").strip().lower() == "world_rule"
    ]
    if world_rule_findings:
        gate.check_review_findings(world_rule_findings, dimension="world_rule")

    # ── POV drift findings ──
    pov_findings = list(getattr(review, "pov_drift_findings", []) or [])
    if pov_findings:
        gate.check_review_findings(pov_findings, dimension="pov")

    # ── Literary contract review mode ──
    literary_gate_mode = (
        str(getattr(settings, "long_literary_contract_review_gate_mode", "warn") or "warn")
        .strip()
        .lower()
    )
    literary_warnings = [
        warning
        for warning in list(getattr(review, "warnings", []) or [])
        if str(warning or "").startswith("theme_arc_alignment:")
        or str(warning or "").startswith("literary_contract:")
    ]
    blocking_literary_warnings = [
        warning
        for warning in literary_warnings
        if str(warning or "").startswith("literary_contract:")
    ]
    diagnostic_literary_warnings = [
        warning
        for warning in literary_warnings
        if str(warning or "").startswith("theme_arc_alignment:")
    ]
    if literary_gate_mode not in {"off", "warn", "critical", "strict"}:
        literary_gate_mode = "warn"
    if literary_gate_mode != "off" and literary_warnings:
        blocks = literary_gate_mode in {"critical", "strict"} and bool(blocking_literary_warnings)
        gate._checks.append(
            QualityCheckResult(
                dimension="literary_contract",
                score=7.5 if not blocks else 0.0,
                threshold=0.0 if not blocks else 10.0,
                passed=not blocks,
                message=""
                if not blocks
                else f"六要素文学合同履约存在 {len(blocking_literary_warnings)} 个待核查问题",
                details={
                    "mode": literary_gate_mode,
                    "warning_count": len(literary_warnings),
                    "blocking_warning_count": len(blocking_literary_warnings),
                    "diagnostic_warning_count": len(diagnostic_literary_warnings),
                    # Keep theme-arc prompts available to diagnostics without
                    # surfacing them in the primary, actionable warning list.
                    "warnings": blocking_literary_warnings[:5],
                    "diagnostic_literary_warnings": diagnostic_literary_warnings[:5],
                    "source": "literary_contract_v1",
                },
            )
        )
    try:
        chapter_number = review.prepared.bundle.chapter_outline.chapter_number
        report_path = review.prepared.bundle.layout.state_adjudication_report_path(chapter_number)
        if report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            gate.check_state_adjudication(payload)
    except Exception:
        pass

    # ── Style metrics: dialogue ratio + repetition + banned phrases ──
    if bool(getattr(settings, "long_style_metrics_enabled", True)):
        style_profile = getattr(bundle, "style_profile", None)
        character_silence = bool(getattr(bundle, "character_silence", False))
        metrics_report = compute_style_metrics(
            review.current_text,
            style_profile,
            protagonist_names=_protagonist_names_from_bundle(bundle),
            character_silence=character_silence,
        )
        gate_mode = str(getattr(settings, "long_style_dialogue_gate_mode", "warn") or "warn")
        repair_threshold = int(getattr(settings, "long_style_dialogue_repair_threshold", 25) or 25)
        style_check = style_metrics_to_quality_check(
            metrics_report,
            gate_mode=gate_mode,
            repair_threshold_pct=float(repair_threshold),
        )
        gate._checks.append(
            QualityCheckResult(
                dimension=style_check.dimension,
                score=style_check.score,
                threshold=style_check.threshold,
                passed=style_check.passed,
                message=style_check.message,
                details=style_check.details,
            )
        )

    # ── Prose quality gate: style dimension floor from eval report ──
    prose_quality_floor = _coerce_float(
        getattr(settings, "long_prose_quality_floor", 0.0),
        0.0,
    )
    if prose_quality_floor > 0.0 and review.eval_report is not None:
        style_score: float | None = None
        for score_item in list(getattr(review.eval_report, "scores", []) or []):
            dim = str(getattr(score_item, "dimension", "") or "").strip().lower()
            if dim == "style":
                try:
                    style_score = float(getattr(score_item, "score", 0.0) or 0.0)
                except (TypeError, ValueError):
                    pass
                break
        if style_score is not None:
            gate._checks.append(
                QualityCheckResult(
                    dimension="prose_quality",
                    score=style_score,
                    threshold=prose_quality_floor,
                    passed=style_score >= prose_quality_floor,
                    message=""
                    if style_score >= prose_quality_floor
                    else f"文学表达质量 {style_score:.1f} 低于出版级底线 {prose_quality_floor:.1f}",
                    details={
                        "eval_style_score": style_score,
                        "floor": prose_quality_floor,
                        "score_scope": "文学表达质量专项分；来自 eval style 维度。",
                    },
                )
            )

    return gate


def _build_normalized_review_contracts(
    review: ChapterReviewArtifacts,
    *,
    chapter_number: int,
) -> tuple[list[Any], list[Any]]:
    """Build normalized findings/tickets from all current review dimensions."""
    text_hash = source_text_hash(review.current_text)
    findings: list[Any] = []
    findings.extend(list(review.review_findings or []))
    findings.extend(
        alignment_report_to_findings(
            review.alignment_report,
            chapter_number=chapter_number,
            current_text_hash=text_hash,
        )
    )
    chapter_quality_findings = list(
        getattr(review.chapter_repair_report, "review_findings", []) or []
    )
    if not chapter_quality_findings:
        chapter_quality_findings = chapter_repair_report_to_findings(
            review.chapter_repair_report,
            chapter_number=chapter_number,
            current_text_hash=text_hash,
        )
    findings.extend(chapter_quality_findings)
    findings.extend(
        continuity_report_to_findings(
            review.continuity_report,
            chapter_number=chapter_number,
            current_text_hash=text_hash,
        )
    )
    findings.extend(
        causal_report_to_findings(
            review.causal_report,
            chapter_number=chapter_number,
            current_text_hash=text_hash,
        )
    )
    reading_power_findings = list(getattr(review.reading_power_report, "review_findings", []) or [])
    if not reading_power_findings:
        reading_power_findings = reading_power_report_to_findings(
            review.reading_power_report,
            chapter_number=chapter_number,
            current_text_hash=text_hash,
        )
    findings.extend(reading_power_findings)

    deduped_findings: list[Any] = []
    seen: set[str] = set()
    for finding in findings:
        signature = str(getattr(finding, "signature", "") or getattr(finding, "finding_id", ""))
        if not signature:
            signature = repr(finding)
        if signature in seen:
            continue
        seen.add(signature)
        deduped_findings.append(finding)

    existing_tickets = list(review.repair_tickets or [])
    covered_finding_ids: set[str] = set()
    for ticket in existing_tickets:
        for finding_id in list(getattr(ticket, "finding_ids", []) or []):
            if str(finding_id or "").strip():
                covered_finding_ids.add(str(finding_id).strip())
    non_guard_findings = [
        finding
        for finding in deduped_findings
        if str(getattr(finding, "dimension", "") or "") not in {"guard", "guard_compliance"}
        and str(getattr(finding, "finding_id", "") or "").strip() not in covered_finding_ids
    ]
    tickets = _dedupe_repair_tickets(
        [*existing_tickets, *compile_repair_tickets_from_findings(non_guard_findings)]
    )
    return deduped_findings, tickets
