"""Finalize-stage orchestration: persistence, canon extraction, terminal polish/humanize.

Extracted from ``chapter_flow.py`` during the Task-17 decomposition.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import inspect
import logging
from typing import Any, Protocol, cast

from novel_forge.common.severity import severity_at_least
from novel_forge.core.exceptions import (
    BLOCK_KIND_CARRY_FORWARD,
    ConsistencyViolationError,
    ModelGatewayError,
    RecoveryTarget,
)
from novel_forge.core.review.alignment_contracts import (
    adjudicate_targeted_alignment_recheck,
    alignment_main_gap_count,
    alignment_uses_structured_contract,
    verified_alignment_blockers,
)
from novel_forge.core.review.review_contracts import (
    compile_repair_tickets_from_findings,
    repair_ticket_matches_remaining_issue,
    source_text_hash,
)
from novel_forge.core.schemas.chapter import (
    ChapterMeta,
    ChapterRepairReport,
    ChapterResult,
    MacroGuardReport,
)
from novel_forge.core.schemas.continuity import (
    ContinuityIssue,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.review_state import (
    ReviewProgressState,
    clear_review_progress,
    save_review_progress,
)
from novel_forge.core.utils.pipeline_helpers import (
    has_prompt_leaks,
    join_repair_actions,
    normalize_threshold,
)
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.chapter_flow_orchestrate import (
    _alignment_meets_threshold,
    _coerce_float,
    _make_on_step_with_tokens,
    _run_knowledge_boundary_verification,
)
from novel_forge.pipeline.long.execution_models import (
    ChapterExecutionContext,
    ChapterReviewArtifacts,
    FlowContextAdapter,
    PreparedChapterArtifacts,
)
from novel_forge.pipeline.long.finalize_tts_metadata import extract_tts_metadata
from novel_forge.pipeline.long.preflight import LongProjectBundle
from novel_forge.pipeline.long.repair_safety import RepairFailurePolicy, RepairRoundSnapshot
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.services.contract_execution_repair import (
    compile_contract_audit_repair_ticket,
    contract_audit_ticket_to_continuity_issue,
    has_contract_audit_repair_ticket,
    is_contract_audit_block_exception,
)
from novel_forge.pipeline.long.services.guidance_contract_audit import (
    audit_report_consistency,
    blocking_messages,
    compile_guidance_repair_tickets,
)
from novel_forge.pipeline.long.services.runtime_contract_repair import (
    classify_contract_execution_blocker,
)
from novel_forge.pipeline.long.services.upstream_compass import (
    find_outline_plan_duration_conflicts,
)
from novel_forge.pipeline.long.stages.finalize_persist import (
    ArchivePreflightResult,
    ReportRefreshDecision,
    persist_results,
    run_archive_preflight_repairs,
)
from novel_forge.pipeline.long.stages.finalize_report import (
    evaluate_chapter_text,
    extract_and_validate,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    guard_report_has_actionable_low_compliance,
    guard_report_incomplete_warning,
)
from novel_forge.pipeline.long.stages.report_freshness import (
    quality_report_evidence_binding,
    stamp_report_freshness,
)
from novel_forge.pipeline.long.stages.report_refresh import (
    ReviewReportService,
    refresh_quality_reports_after_semantic_text_change,
    run_guard_compliance_for_final_text,
)
from novel_forge.pipeline.quality_gate import QualityGate
from novel_forge.pipeline.repair_orchestration.domains.knowledge_boundary import (
    run_knowledge_boundary_repair_v2 as run_knowledge_boundary_repair_loop,
)
from novel_forge.pipeline.repair_orchestration.domains.runtime_contract import (
    run_runtime_contract_repair_v2,
)

_logger = logging.getLogger(__name__)
_SINGLE_FINAL_VERIFY_PIPELINE_VERSION = "long-single-final-verify-v1"


@dataclasses.dataclass(frozen=True)
class AlignmentRepairStageResult:
    """Outcome from the bounded alignment repair sub-stage."""

    current_text: str
    alignment_report: Any
    rounds_used: int = 0
    repair_exhausted: bool = False
    warning: str = ""


class _ContinuityRepairLike(Protocol):
    applied: bool
    repair_plan: RepairPlan


@dataclasses.dataclass(frozen=True)
class _ArchiveRepairAttempt:
    status: str
    review: ChapterReviewArtifacts | None = None


@dataclasses.dataclass(frozen=True)
class ArchivePreflightRepairSpec:
    """Shared inputs for a targeted archive preflight text repair."""

    issue: ContinuityIssue
    continuity_summary: str
    escalation_note: str
    stale_reason: str
    start_event: str
    skipped_event: str
    complete_event: str
    start_payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    skipped_payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    complete_payload: dict[str, Any] = dataclasses.field(default_factory=dict)
    repair_tickets: tuple[Any, ...] = ()
    total_round_increment: int = 0


def _guidance_report_mismatch_sources(findings: list[Any]) -> set[str]:
    """Return report sources whose summary/issue contract was self-contradictory."""
    sources: set[str] = set()
    for finding in findings:
        if getattr(finding, "issue_type", "") != "report_issue_summary_mismatch":
            continue
        metadata = dict(getattr(finding, "metadata", {}) or {})
        source = str(metadata.get("source_module") or getattr(finding, "source_module", "") or "")
        if source:
            sources.add(source)
    return sources


def _should_include_pre_final_evaluation(settings: Any) -> bool:
    """Run pre-final eval whenever it can affect polish decisions."""
    if bool(getattr(settings, "long_polish_enabled", False)):
        return True
    auto_threshold = _coerce_float(
        getattr(settings, "long_polish_auto_trigger_threshold", 7.0),
        0.0,
    )
    return auto_threshold > 0.0


def _should_skip_post_repair_checks(
    *,
    continuity_repair: _ContinuityRepairLike,
    alignment_score: float,
    alignment_threshold: float,
    chapter_repair_report: ChapterRepairReport | None,
) -> bool:
    """Skip expensive re-checks when no rewrite happened and quality already passes."""
    if continuity_repair.applied:
        return False
    if not continuity_repair.repair_plan.no_op:
        return False
    if has_prompt_leaks(chapter_repair_report):
        return False
    return _alignment_meets_threshold(alignment_score, alignment_threshold)


def _change_ratio(before: str, after: str) -> float:
    """Estimate text change ratio: 0.0 = identical, 1.0 = total rewrite.

    Delegates to the canonical implementation in ``core.utils.text_validation``.
    """
    from novel_forge.core.utils.text_validation import text_change_ratio

    return text_change_ratio(before, after)


def _enforce_alignment_threshold(
    *,
    runner: Any,
    chapter_number: int,
    alignment_score: float,
    repair_actions: list[str],
    alignment_report: Any | None = None,
    is_retry: bool = False,
) -> None:
    """Fail fast when final alignment is below configured threshold or has missing main points."""
    # ── Fallback exemption ────────────────────────────────────────────────
    # When the alignment evaluation was unavailable (timeout / LLM error /
    # truncated response), the score is not trustworthy.  Exempt it from
    # hard-blocking to prevent unnecessary repair loops or chapter replans
    # triggered by a distorted score.
    if alignment_report is not None and getattr(alignment_report, "is_fallback", False):
        runner._on_step(
            "alignment_fallback_exempt",
            {
                "chapter": chapter_number,
                "score": round(float(alignment_score), 2),
                "evaluation_status": getattr(alignment_report, "evaluation_status", ""),
                "fallback_reason": getattr(alignment_report, "fallback_reason", ""),
                "message": "对齐评估不可用（兜底报告），已豁免阈值检查以避免失真分数触发修复/重写",
            },
        )
        return

    threshold = normalize_threshold(runner._config.alignment_threshold)
    passed = _alignment_meets_threshold(alignment_score, threshold)

    has_missing_main = False
    structured_contract = alignment_uses_structured_contract(alignment_report)
    verified_blockers = verified_alignment_blockers(alignment_report)
    if alignment_report is not None:
        if structured_contract:
            has_missing_main = bool(verified_blockers)
        else:
            missing_points = getattr(alignment_report, "missing_main_points", None)
            if missing_points and len(missing_points) > 0:
                has_missing_main = True

    runner._on_step(
        "alignment_threshold_check",
        {
            "chapter": chapter_number,
            "score": round(float(alignment_score), 2),
            "threshold": threshold,
            "passed": passed,
            "has_missing_main": has_missing_main,
            "review_contract_version": int(
                getattr(alignment_report, "review_contract_version", 0) or 0
            ),
            "verified_blocker_count": len(verified_blockers),
        },
    )

    if structured_contract and not has_missing_main:
        if not passed:
            runner._on_step(
                "alignment_low_score_without_verified_blocker",
                {
                    "chapter": chapter_number,
                    "score": round(float(alignment_score), 2),
                    "threshold": threshold,
                    "action": "advisory_only",
                    "message": "分数偏低但无经证据核验的阻断项，不为找问题而启动修复。",
                },
            )
        return

    if passed and not has_missing_main:
        return

    # When score passes the threshold, missing_main_points are likely
    # false positives from the LLM evaluator.  Treat them as warnings
    # rather than hard blockers to prevent infinite replan loops.
    if passed and has_missing_main and not structured_contract:
        runner._on_step(
            "alignment_missing_main_warn",
            {
                "chapter": chapter_number,
                "score": round(float(alignment_score), 2),
                "message": "对齐分达标但仍有主线缺失标记，视为评估器偏差，放行",
            },
        )
        return

    # On retry (after alignment_repair_edit), accept "moderate" scores
    # to prevent infinite replan loops. A score >= 70% of threshold
    # indicates the chapter is close enough; full replans are unlikely
    # to improve it further when beats are very detailed.
    moderate_floor = max(round(threshold * 0.7, 1), 5.0)
    if is_retry and alignment_score >= moderate_floor and not verified_blockers:
        runner._on_step(
            "alignment_moderate_pass",
            {
                "chapter": chapter_number,
                "score": round(float(alignment_score), 2),
                "threshold": threshold,
                "moderate_floor": moderate_floor,
                "message": "重试后对齐分处于中等区间，放行以避免无限重规划",
            },
        )
        return

    hints = join_repair_actions(repair_actions)
    messages = []

    if not passed:
        messages.append(f"章节对齐分 {alignment_score:.1f} 低于阈值 {threshold:.1f}")
    if has_missing_main:
        if verified_blockers:
            preview = "；".join(finding.summary for finding in verified_blockers[:3])
            messages.append(f"存在 {len(verified_blockers)} 个已核验对齐阻断项：{preview}")
        else:
            messages.append("存在主线推进点缺失")

    message = "；".join(messages) + "，请先修复后再继续。"
    if hints:
        message += " 建议：" + hints
    raise ConsistencyViolationError([message])


async def _run_alignment_repair_stage(
    *,
    runner: Any,
    bundle: LongProjectBundle,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    alignment_report: Any,
    trace: PipelineTrace,
    on_step: Any,
    total_rounds_used: int,
    total_rounds_cap: int,
) -> AlignmentRepairStageResult:
    """Run bounded, evidence-driven alignment repair with rollback anchors."""
    from novel_forge.pipeline.long.chapter_flow import _should_skip_alignment_recheck
    from novel_forge.pipeline.long.services.plan_obligations import (
        assess_plan_literal_coverage,
    )

    runner = FlowContextAdapter(runner)
    best_text = current_text
    best_report = alignment_report

    upstream_conflicts = find_outline_plan_duration_conflicts(
        outline=getattr(bundle, "chapter_outline", None),
        plan=plan,
    )
    if upstream_conflicts:
        requires_source_revision = any(
            str(item.get("recovery_target")) == "manual" for item in upstream_conflicts
        )
        on_step(
            "alignment_repair_blocked_by_upstream_evidence_conflict",
            {
                "chapter": chapter_number,
                "conflicts": upstream_conflicts,
                "action": (
                    "revise_upstream_source_before_repair"
                    if requires_source_revision
                    else "replan_before_repair"
                ),
            },
        )
        raise ConsistencyViolationError(
            [
                (
                    f"大纲与 Plan 的「{item['subject']}」期限冲突："
                    f"大纲={item['outline_days']}日，Plan={item['plan_days']}日。"
                    + (
                        "大纲自身已冲突，请先修订并审批源证据，再重新审查。"
                        if str(item.get("recovery_target")) == "manual"
                        else "请先修复 Plan，再对当前正文重新审查。"
                    )
                )
                for item in upstream_conflicts
            ],
            violation_kind=(
                "upstream_source_conflict"
                if requires_source_revision
                else "upstream_plan_conflict"
            ),
            failed_stage="alignment_repair_preflight",
            replan_target=(RecoveryTarget.MANUAL if requires_source_revision else RecoveryTarget.PLAN),
        )

    evidence_binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )

    def candidate_rank(text: str, report: Any) -> tuple[int, int, int, int, float]:
        literal_gaps = int(
            assess_plan_literal_coverage(plan, text)["missing_required_literal_count"]
        )
        main_gaps = alignment_main_gap_count(report)
        weak_gaps = len(list(getattr(report, "weak_subplot_points", []) or []))
        score = float(getattr(report, "alignment_score", 0.0) or 0.0)
        # Literal contracts and verified semantic blockers are both hard
        # obligations.  Ranking either family lexicographically first can roll
        # back a candidate that fixes several real defects while exposing one
        # smaller remaining obligation.
        return literal_gaps + main_gaps, literal_gaps, main_gaps, weak_gaps, -score

    def persist_report_snapshot(text: str, report: Any) -> None:
        if hasattr(report, "model_dump"):
            payload = report.model_dump(mode="json")
        elif isinstance(report, dict):
            payload = dict(report)
        else:
            payload = dict(vars(report)) if report is not None else {}
        stamp_report_freshness(
            payload,
            current_hash=source_text_hash(text),
            context_hash=str(evidence_binding["context_hash"]),
            evidence_hashes=dict(evidence_binding["evidence_hashes"]),
        )
        try:
            storage = runner._storage
            path_factory = bundle.layout.alignment_report_path
        except AttributeError:
            return
        storage.save_json(path_factory(chapter_number), payload)

    best_rank = candidate_rank(best_text, best_report)
    try:
        _enforce_alignment_threshold(
            runner=runner,
            chapter_number=chapter_number,
            alignment_score=alignment_report.alignment_score,
            repair_actions=alignment_report.repair_actions,
            alignment_report=alignment_report,
        )
        if (
            best_rank[0] == 0
            and not list(getattr(best_report, "repair_actions", []) or [])
        ):
            return AlignmentRepairStageResult(
                current_text=current_text,
                alignment_report=alignment_report,
            )
    except ConsistencyViolationError:
        if total_rounds_cap > 0 and total_rounds_used >= total_rounds_cap:
            on_step(
                "repair_dimension_skipped_total_cap",
                {
                    "chapter": chapter_number,
                    "total_rounds_used": total_rounds_used,
                    "cap": total_rounds_cap,
                    "skipped_dimension": "alignment_repair",
                },
            )
            raise

    failure_policy = RepairFailurePolicy(on_step, logger=_logger)
    runner_settings = getattr(runner, "_settings", None)
    configured_attempts = max(
        1,
        int(getattr(runner_settings, "guard_ticket_alignment_followup_max_attempts", 2) or 2),
    )

    # ── Critical alignment escalation ──────────────────────────────────────
    # When alignment score is far below threshold (e.g. 2.0 vs 7.0), the
    # standard 2 attempts are insufficient for structural repair.  Escalate
    # attempts and allow exceeding the global cap by a bounded margin so
    # that critically misaligned chapters get a fair repair chance.
    threshold = normalize_threshold(runner._config.alignment_threshold)
    moderate_floor = max(round(threshold * 0.7, 1), 5.0)
    is_critical_alignment = float(alignment_report.alignment_score) < moderate_floor
    if is_critical_alignment:
        configured_attempts = max(
            configured_attempts,
            int(getattr(runner_settings, "long_alignment_critical_max_attempts", 3) or 3),
        )

    available_rounds = configured_attempts
    if total_rounds_cap > 0:
        remaining = max(0, total_rounds_cap - total_rounds_used)
        if is_critical_alignment:
            # Allow exceeding global cap by up to 2 rounds for critical alignment
            critical_overflow = int(
                getattr(runner_settings, "long_alignment_critical_cap_overflow", 2) or 2
            )
            remaining = max(remaining, min(critical_overflow, configured_attempts))
            on_step(
                "alignment_critical_escalation",
                {
                    "chapter": chapter_number,
                    "score": round(float(alignment_report.alignment_score), 2),
                    "moderate_floor": moderate_floor,
                    "configured_attempts": configured_attempts,
                    "remaining_after_escalation": remaining,
                },
            )
        available_rounds = min(configured_attempts, remaining)
    if available_rounds <= 0:
        raise ConsistencyViolationError(["对齐修复已达全局修复轮次上限。"])

    rounds_used = 0
    last_warning = ""
    for repair_round in range(1, available_rounds + 1):
        round_base_text = best_text
        round_base_report = best_report
        try:
            from novel_forge.pipeline.long.chapter_flow import alignment_repair_edit

            repaired_text = await alignment_repair_edit(
                runner,
                bundle=bundle,
                packet=packet,
                bridge=bridge,
                plan=plan,
                current_text=round_base_text,
                chapter_number=chapter_number,
                alignment_report=round_base_report,
                trace=trace,
                repair_round=repair_round,
                max_rounds=available_rounds,
            )
        except ModelGatewayError as exc:
            outcome = failure_policy.repair_failed(
                snapshot=RepairRoundSnapshot(
                    stage="alignment_repair",
                    chapter_number=chapter_number,
                    round_number=repair_round,
                    text=best_text,
                    report=best_report,
                ),
                exc=exc,
                action="skip_alignment_repair_keep_best_text",
                warning=f"对齐修复模型调用失败，已保留当前最优正文：{type(exc).__name__}: {exc}",
                repair_exhausted=True,
            )
            last_warning = outcome.warning
            on_step(
                "alignment_repair_gateway_error",
                {
                    "chapter": chapter_number,
                    "repair_round": repair_round,
                    "error": str(exc),
                    "error_kind": outcome.error_kind.value,
                    "error_type": type(exc).__name__,
                    "action": outcome.action,
                },
            )
            return AlignmentRepairStageResult(
                current_text=best_text,
                alignment_report=best_report,
                rounds_used=rounds_used,
                repair_exhausted=True,
                warning=last_warning,
            )

        rounds_used += 1
        skip_recheck, skip_reason = _should_skip_alignment_recheck(
            round_base_text,
            repaired_text,
            round_base_report,
        )
        text_changed = round_base_text != repaired_text
        if skip_recheck and text_changed:
            skip_recheck = False
            skip_reason = "changed prose requires an exact text-bound alignment report"
            on_step(
                "alignment_recheck_cache_bypassed",
                {
                    "chapter": chapter_number,
                    "repair_round": repair_round,
                    "reason": skip_reason,
                },
            )
        if skip_recheck:
            next_rank = candidate_rank(repaired_text, round_base_report)
            on_step(
                "alignment_recheck_skipped",
                {
                    "chapter": chapter_number,
                    "repair_round": repair_round,
                    "reason": skip_reason,
                    "cached_score": round(
                        float(getattr(round_base_report, "alignment_score", 0.0)),
                        2,
                    ),
                    "candidate_rank": list(next_rank),
                    "best_rank": list(best_rank),
                },
            )
            if next_rank < best_rank:
                best_text = repaired_text
                best_report = round_base_report
                best_rank = next_rank
                persist_report_snapshot(best_text, best_report)
                on_step(
                    "alignment_repair_candidate_accepted",
                    {
                        "chapter": chapter_number,
                        "repair_round": repair_round,
                        "rank": list(best_rank),
                        "score": float(getattr(best_report, "alignment_score", 0.0) or 0.0),
                        "validation_mode": "cached_semantic_report_with_local_postconditions",
                    },
                )
                if (
                    best_rank[0] == 0
                    and not list(getattr(best_report, "repair_actions", []) or [])
                ):
                    try:
                        _enforce_alignment_threshold(
                            runner=runner,
                            chapter_number=chapter_number,
                            alignment_score=best_report.alignment_score,
                            repair_actions=best_report.repair_actions,
                            alignment_report=best_report,
                            is_retry=True,
                        )
                    except ConsistencyViolationError:
                        continue
                    return AlignmentRepairStageResult(
                        current_text=best_text,
                        alignment_report=best_report,
                        rounds_used=rounds_used,
                    )
            else:
                persist_report_snapshot(best_text, best_report)
                on_step(
                    "alignment_repair_candidate_rolled_back",
                    {
                        "chapter": chapter_number,
                        "repair_round": repair_round,
                        "candidate_rank": list(next_rank),
                        "best_rank": list(best_rank),
                        "action": "rollback_no_local_postcondition_improvement",
                    },
                )
            continue

        try:
            from novel_forge.pipeline.long.chapter_flow import recheck_alignment

            candidate_report = await recheck_alignment(
                runner,
                bundle,
                packet,
                plan,
                repaired_text,
                chapter_number,
                trace,
            )
            candidate_report = adjudicate_targeted_alignment_recheck(
                previous_report=round_base_report,
                candidate_report=candidate_report,
                before_text=round_base_text,
                after_text=repaired_text,
                repair_round=repair_round,
            )
        except ModelGatewayError as exc:
            outcome = failure_policy.recheck_failed(
                snapshot=RepairRoundSnapshot(
                    stage="alignment_repair",
                    chapter_number=chapter_number,
                    round_number=repair_round,
                    text=round_base_text,
                    report=round_base_report,
                ),
                exc=exc,
                warning=f"对齐修复后复查失败，本轮修复已回滚：{type(exc).__name__}: {exc}",
            )
            last_warning = outcome.warning
            persist_report_snapshot(best_text, best_report)
            return AlignmentRepairStageResult(
                current_text=best_text,
                alignment_report=best_report,
                rounds_used=rounds_used,
                repair_exhausted=True,
                warning=last_warning,
            )

        next_rank = candidate_rank(repaired_text, candidate_report)
        if next_rank < best_rank:
            best_text = repaired_text
            best_report = candidate_report
            best_rank = next_rank
            persist_report_snapshot(best_text, best_report)
            on_step(
                "alignment_repair_candidate_accepted",
                {
                    "chapter": chapter_number,
                    "repair_round": repair_round,
                    "rank": list(best_rank),
                    "score": float(getattr(best_report, "alignment_score", 0.0) or 0.0),
                },
            )
        else:
            persist_report_snapshot(best_text, best_report)
            on_step(
                "alignment_repair_candidate_rolled_back",
                {
                    "chapter": chapter_number,
                    "repair_round": repair_round,
                    "candidate_rank": list(next_rank),
                    "best_rank": list(best_rank),
                    "action": "rollback_to_best_validated_alignment_text",
                },
            )

        if (
            best_rank[0] == 0
            and not list(getattr(best_report, "repair_actions", []) or [])
        ):
            try:
                _enforce_alignment_threshold(
                    runner=runner,
                    chapter_number=chapter_number,
                    alignment_score=best_report.alignment_score,
                    repair_actions=best_report.repair_actions,
                    alignment_report=best_report,
                    is_retry=True,
                )
            except ConsistencyViolationError:
                continue
            return AlignmentRepairStageResult(
                current_text=best_text,
                alignment_report=best_report,
                rounds_used=rounds_used,
            )

    if best_rank[1] > 0:
        remaining_literals = list(
            assess_plan_literal_coverage(plan, best_text)["missing_required_literals"]
        )
        details = "；".join(
            f"{item.get('scene_id', '?')}/{item.get('source_field', '?')}："
            f"「{item.get('literal', '')}」"
            for item in remaining_literals
        )
        raise ConsistencyViolationError(
            [
                f"章节仍缺少 {best_rank[1]} 个 Plan 字面合同，"
                f"拒绝使用概括或同义改写放行。具体缺项：{details}"
            ]
        )
    _enforce_alignment_threshold(
        runner=runner,
        chapter_number=chapter_number,
        alignment_score=best_report.alignment_score,
        repair_actions=best_report.repair_actions,
        alignment_report=best_report,
        is_retry=True,
    )
    return AlignmentRepairStageResult(
        current_text=best_text,
        alignment_report=best_report,
        rounds_used=rounds_used,
        repair_exhausted=rounds_used >= available_rounds,
        warning=last_warning,
    )


async def _run_humanize_pass(
    runner: Any,
    prepared: PreparedChapterArtifacts,
    *,
    current_text: str,
    chapter_number: int,
    trace: PipelineTrace,
    context: ChapterExecutionContext,
    polish_modified: bool,
) -> Any:
    """Run the humanize layer and persist its review artifacts."""
    from novel_forge.pipeline.long.chapter_flow import (
        run_humanize_layer,
    )

    runner = FlowContextAdapter(runner)
    bundle = prepared.bundle
    expression_channel_records = list(
        (getattr(prepared, "memory_hints", {}) or {}).get("expression_channel_records", []) or []
    )
    humanize_kwargs: dict[str, Any] = {"settings": context.settings}
    try:
        signature = inspect.signature(run_humanize_layer)
    except (TypeError, ValueError):
        signature = None
    params = signature.parameters if signature is not None else {}
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())
    if accepts_kwargs or "polish_modified" in params:
        humanize_kwargs["polish_modified"] = polish_modified
    if accepts_kwargs or "expression_channel_records" in params:
        humanize_kwargs["expression_channel_records"] = expression_channel_records

    if getattr(context.settings, "humanize_enabled", False):
        context.on_step("humanize_start", {"chapter": chapter_number})
    humanize_result = await run_humanize_layer(
        runner,
        bundle,
        prepared.packet,
        current_text,
        chapter_number,
        trace,
        **humanize_kwargs,
    )
    if getattr(humanize_result, "skipped_reason", ""):
        outcome_event = (
            "humanize_rolled_back"
            if humanize_result.skipped_reason
            in {
                "change_ratio_exceeded",
                "semantic_drift",
                "quality_regression",
            }
            else "humanize_skipped"
        )
        context.on_step(
            outcome_event,
            {
                "chapter": chapter_number,
                "reason": humanize_result.skipped_reason,
            },
        )
    if humanize_result.current_text != current_text:
        candidate_path = None
        try:
            draft_dir = getattr(bundle.layout, "chapter_draft_dir", None)
            if callable(draft_dir):
                candidate_path = draft_dir(chapter_number) / "v_humanize_candidate.md"
            save_text = getattr(context.storage, "save_text", None)
            if candidate_path is not None and callable(save_text):
                save_text(candidate_path, humanize_result.current_text)
        except Exception as exc:
            _logger.warning(
                "humanize_candidate_persist_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
        else:
            if candidate_path is not None:
                comparison = getattr(humanize_result, "comparison", None)
                if isinstance(comparison, dict):
                    metadata = comparison.setdefault("metadata", {})
                    if isinstance(metadata, dict):
                        metadata.update(
                            {
                                "candidate_text_path": str(candidate_path),
                                "candidate_text_hash": source_text_hash(
                                    humanize_result.current_text
                                ),
                                "archive_status": "pending_archive_gate",
                            }
                        )
                runner._on_step(
                    "humanize_candidate_saved",
                    {
                        "chapter": chapter_number,
                        "path": str(candidate_path),
                        "text_hash": source_text_hash(humanize_result.current_text),
                        "archive_status": "pending_archive_gate",
                    },
                )
    if humanize_result.report:
        humanize_report = humanize_result.report.model_dump(mode="json")
        humanize_report_path = bundle.layout.humanize_report_path(chapter_number)
        try:
            context.storage.save_json(humanize_report_path, humanize_report)
        except Exception as exc:
            _logger.warning(
                "humanize_report_persist_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
        else:
            humanize_report["path"] = str(humanize_report_path)
        runner._on_step("humanize_scan", humanize_report)
    if humanize_result.comparison is not None:
        humanize_diff_path = (
            bundle.layout.reports_dir
            / "revisions"
            / f"chapter_{chapter_number:03d}_humanize_layer.json"
        )
        try:
            context.storage.save_json(humanize_diff_path, humanize_result.comparison)
        except Exception as exc:
            _logger.warning(
                "humanize_revision_diff_persist_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
        else:
            runner._on_step(
                "humanize_revision_diff",
                {
                    "path": str(humanize_diff_path),
                    "status": humanize_result.comparison.get("status", ""),
                    "patches_applied": humanize_result.comparison.get("patches_applied", 0),
                },
            )
    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            hash_payload,
            load_stage_artifact,
            persist_stage_artifact,
        )

        previous_artifact = None
        for artifact_type in ("polish", "repair", "review", "wave"):
            previous_artifact = load_stage_artifact(
                context.storage,
                bundle.layout,
                chapter_number=chapter_number,
                artifact_type=artifact_type,
            )
            if previous_artifact is not None:
                break
        skipped_reason = str(getattr(humanize_result, "skipped_reason", "") or "")
        report_summary = str(getattr(humanize_result.report, "summary", "") or "")
        local_fallback = "reason=llm_failed" in report_summary or (
            "reason=llm_invalid_report" in report_summary
        )
        safety_rollback = skipped_reason in {
            "change_ratio_exceeded",
            "semantic_drift",
            "quality_regression",
        }
        execution_quality_status = (
            "fallback" if safety_rollback else "degraded" if local_fallback else "actual"
        )
        degradation_reason = (
            skipped_reason
            if safety_rollback
            else ("humanize_local_fallback" if local_fallback else "")
        )
        humanize_config_fingerprint = hash_payload(
            {
                "enabled": bool(getattr(context.settings, "humanize_enabled", False)),
                "model": str(getattr(context.settings, "humanize_model", "") or ""),
                "max_rounds": int(getattr(context.settings, "humanize_max_rounds", 2) or 2),
                "change_ratio_cap": float(
                    getattr(context.settings, "humanize_change_ratio_cap", 0.05) or 0.05
                ),
                "structural_change_ratio_cap": float(
                    getattr(context.settings, "humanize_structural_change_ratio_cap", 0.15) or 0.15
                ),
            }
        )
        persist_stage_artifact(
            storage=context.storage,
            layout=bundle.layout,
            project_id=getattr(bundle, "project_id", "") or "unknown",
            chapter_number=chapter_number,
            artifact_type="humanize",
            payload={
                "source_text_hash": source_text_hash(current_text),
                "text_hash": source_text_hash(humanize_result.current_text),
                "text_chars": len(humanize_result.current_text),
                "patches_applied": int(getattr(humanize_result, "patches_applied", 0) or 0),
                "paragraph_rewrites_applied": int(
                    getattr(humanize_result, "paragraph_rewrites_applied", 0) or 0
                ),
                "skipped_reason": skipped_reason,
                "comparison_status": str(
                    (humanize_result.comparison or {}).get("status", "")
                    if isinstance(humanize_result.comparison, dict)
                    else ""
                ),
            },
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            previous_artifact=previous_artifact,
            config_fingerprint=humanize_config_fingerprint,
            model_fingerprint=(
                str(getattr(context.settings, "humanize_model", "") or "").strip()
                or "router_managed"
            ),
            execution_quality_status=execution_quality_status,
            degradation_reason=degradation_reason,
            derivation_status="fresh",
        )
    except Exception as exc:
        _logger.warning(
            "humanize_stage_artifact_persist_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
    return humanize_result


def _humanize_result_changed(
    humanize_result: Any,
    *,
    before_text: str,
    after_text: str,
) -> bool:
    """Return True when the accepted humanize result changed chapter prose."""
    patches = int(getattr(humanize_result, "patches_applied", 0) or 0)
    paragraph_rewrites = int(getattr(humanize_result, "paragraph_rewrites_applied", 0) or 0)
    skipped_reason = str(getattr(humanize_result, "skipped_reason", "") or "")
    return bool(
        after_text != before_text and ((patches + paragraph_rewrites) > 0 or not skipped_reason)
    )


async def _finalize_review_artifacts(
    context: ChapterExecutionContext,
    *,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    current_text: str,
    performed_edits: int,
    outcome: Any,
    alignment_report: Any,
    chapter_repair_report: Any,
    continuity_report: Any,
    causal_report: Any,
    repair_plan: RepairPlan,
    eval_report: EvalReport | None,
    review_warnings: list[str],
    reading_power_report: Any | None,
    allow_word_count_archive_bypass: bool,
    total_repair_rounds_used: int,
    post_repair_polish_modified_text: bool,
    humanize_modified_text: bool,
    include_evaluation: bool,
    emit_evaluation_step: bool,
    persist_evaluation: bool,
    pov_drift_findings: list[Any] | None = None,
    pov_drift_tickets: list[Any] | None = None,
) -> ChapterReviewArtifacts:
    """Bind final review artifacts to the final review-stage text.

    Ordered pipeline with strict data dependencies — do NOT reorder phases:

      Phase 1: Knowledge boundary verification + repair
               -> may modify current_text, sets _kb_text_changed
      Phase 2: Report refresh (conditional on semantic_text_changed)
               -> depends on Phase 1's _kb_text_changed flag
               -> if _kb_text_changed: re-extract outcome + re-evaluate
      Phase 3: Guard compliance check
               -> uses final current_text (post Phase 1/2)
      Phase 4: Guidance contract audit
               -> audits Phase 2's refreshed reports for internal consistency
               -> on mismatch: targeted re-refresh + re-audit
      Phase 5: Assemble ChapterReviewArtifacts
               -> aggregates all findings/tickets from Phases 1-4
    """
    runner = FlowContextAdapter(context)
    bundle = prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    on_step = _make_on_step_with_tokens(context.on_step, trace)
    guard_compliance_report: dict[str, Any] | None = None
    guard_review_findings: list[Any] = []
    guard_repair_tickets: list[Any] = []
    world_rule_report: Any | None = None
    world_rule_findings: list[Any] = []
    world_rule_tickets: list[Any] = []

    # ── Phase 1: Knowledge boundary verification + repair ────────────────────
    knowledge_boundary_findings = await _run_knowledge_boundary_verification(
        context,
        prepared,
        current_text,
        chapter_number,
    )

    _kb_blocking = [
        f
        for f in knowledge_boundary_findings
        if f.blocks_finalize or severity_at_least(f.severity, "high")
    ]
    _kb_text_changed = False
    if _kb_blocking:
        _pre_kb_text = current_text
        _kb_result = await run_knowledge_boundary_repair_loop(
            runner=runner,
            storage=context.storage,
            bundle=prepared.bundle,
            packet=prepared.packet,
            current_text=current_text,
            chapter_number=chapter_number,
            findings=knowledge_boundary_findings,
            trace=trace,
            max_rounds=2,
        )
        current_text = _kb_result.current_text
        _kb_text_changed = current_text != _pre_kb_text
        knowledge_boundary_findings = list(_kb_result.findings_after)
        runner._on_step(
            "knowledge_boundary_repair_completed",
            {
                "chapter": chapter_number,
                "repair_attempted": _kb_result.repair_attempted,
                "repair_exhausted": _kb_result.repair_exhausted,
                "rounds_used": _kb_result.rounds_used,
                "findings_before": len(_kb_result.findings_before),
                "findings_after": len(_kb_result.findings_after),
            },
        )

    # ── Phase 2: Report refresh (conditional on semantic_text_changed) ───────
    semantic_text_changed_after_quality = (
        post_repair_polish_modified_text or humanize_modified_text or _kb_text_changed
    )
    if semantic_text_changed_after_quality:
        refresh_reason_parts = []
        if post_repair_polish_modified_text:
            refresh_reason_parts.append("post_repair_polish")
        if humanize_modified_text:
            refresh_reason_parts.append("humanize")
        if _kb_text_changed:
            refresh_reason_parts.append("knowledge_boundary_repair")
        refresh_reason = "_and_".join(refresh_reason_parts) or "semantic_text_changed"
        refreshed_reports = await refresh_quality_reports_after_semantic_text_change(
            runner=runner,
            bundle=bundle,
            packet=prepared.packet,
            bridge=prepared.bridge,
            plan=prepared.plan,
            current_text=current_text,
            chapter_number=chapter_number,
            trace=trace,
            alignment_report=alignment_report,
            continuity_report=continuity_report,
            causal_report=causal_report,
            chapter_repair_report=chapter_repair_report,
            reading_power_report=reading_power_report,
            window_manager=prepared.window_manager,
            window_config=prepared.window_config,
            stale_reason=refresh_reason,
        )
        alignment_report = refreshed_reports.alignment_report
        continuity_report = normalize_continuity_report_repair_state(
            refreshed_reports.continuity_report
        )
        causal_report = refreshed_reports.causal_report
        chapter_repair_report = refreshed_reports.chapter_repair_report
        reading_power_report = refreshed_reports.reading_power_report
        if _kb_text_changed:
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
                repair_exhausted=True,
            )
            if include_evaluation:
                eval_report = await evaluate_chapter_text(
                    context,
                    bundle=bundle,
                    chapter_number=chapter_number,
                    current_text=current_text,
                    trace=trace,
                    emit_step=emit_evaluation_step,
                    persist=persist_evaluation,
                )

    # ── Phase 3: Guard compliance check ─────────────────────────────────────
    try:
        guard_refresh = await run_guard_compliance_for_final_text(
            runner=runner,
            bundle=bundle,
            packet=prepared.packet,
            current_text=current_text,
            chapter_number=chapter_number,
            storage=context.storage,
        )
        guard_compliance_report = guard_refresh.guard_compliance_report
        guard_review_findings = guard_refresh.guard_findings
        guard_repair_tickets = guard_refresh.guard_tickets
        incomplete_warning = guard_report_incomplete_warning(guard_compliance_report)
        if incomplete_warning:
            review_warnings.append(incomplete_warning)
        if guard_report_has_actionable_low_compliance(guard_compliance_report):
            summary = (guard_compliance_report or {}).get("summary", "")
            review_warnings.append(f"AI护栏约束合规率过低: {summary}")
    except Exception as guard_exc:
        _logger.warning(
            "guard_constraint_compliance_check_failed | chapter=%d | error=%s",
            chapter_number,
            guard_exc,
        )

    # World rules are source-owned P0 constraints.  Review records actionable
    # tickets here; the Finalize quality gate below is responsible for blocking
    # an unresolved hard conflict after all normal repair surfaces have run.
    try:
        from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
            check_world_rule_compliance,
            world_rule_report_to_findings,
        )
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            project_stage_source_cards,
        )

        source_slice = getattr(bundle, "chapter_source_slice", None)
        card = (
            project_stage_source_cards(source_slice, stage="review").get("world_rule_card")
            if source_slice is not None
            else None
        )
        if card:
            world_rule_report = await check_world_rule_compliance(
                runner=runner,
                chapter_text=current_text,
                chapter_number=chapter_number,
                world_rule_card=card,
                applications=list(getattr(prepared.plan, "world_rule_applications", []) or []),
            )
            world_rule_findings = world_rule_report_to_findings(world_rule_report)
            world_rule_tickets = compile_repair_tickets_from_findings(world_rule_findings)
            runner._on_step(
                "world_rule_compliance_check",
                {
                    "chapter": chapter_number,
                    "rule_book_hash": world_rule_report.rule_book_hash,
                    "checked_rule_ids": world_rule_report.checked_rule_ids,
                    "issues": [item.model_dump(mode="json") for item in world_rule_report.issues],
                    "blocking": world_rule_report.has_blocking_conflict,
                },
            )
    except Exception as world_rule_exc:
        _logger.warning(
            "world_rule_compliance_check_failed | chapter=%d | error=%s",
            chapter_number,
            world_rule_exc,
        )

    # ── Phase 4: Guidance contract audit ─────────────────────────────────────
    knowledge_boundary_tickets = compile_repair_tickets_from_findings(knowledge_boundary_findings)

    continuity_report = normalize_continuity_report_repair_state(continuity_report)

    guidance_findings = []
    guidance_findings.extend(
        audit_report_consistency(
            continuity_report,
            source_module="continuity_report",
            chapter_number=chapter_number,
        )
    )
    guidance_findings.extend(
        audit_report_consistency(
            causal_report,
            source_module="causal_report",
            chapter_number=chapter_number,
        )
    )
    guidance_findings.extend(
        audit_report_consistency(
            alignment_report,
            source_module="alignment_report",
            chapter_number=chapter_number,
        )
    )
    mismatch_sources = _guidance_report_mismatch_sources(guidance_findings)
    if mismatch_sources:
        (
            alignment_report,
            continuity_report,
            causal_report,
        ) = await _refresh_mismatched_guidance_reports(
            context,
            prepared=prepared,
            current_text=current_text,
            chapter_number=chapter_number,
            trace=trace,
            mismatch_sources=mismatch_sources,
            alignment_report=alignment_report,
            continuity_report=continuity_report,
            causal_report=causal_report,
        )
        guidance_findings = []
        continuity_report = normalize_continuity_report_repair_state(continuity_report)
        guidance_findings.extend(
            audit_report_consistency(
                continuity_report,
                source_module="continuity_report",
                chapter_number=chapter_number,
            )
        )
        guidance_findings.extend(
            audit_report_consistency(
                causal_report,
                source_module="causal_report",
                chapter_number=chapter_number,
            )
        )
        guidance_findings.extend(
            audit_report_consistency(
                alignment_report,
                source_module="alignment_report",
                chapter_number=chapter_number,
            )
        )
    guidance_tickets = compile_guidance_repair_tickets(guidance_findings)
    guidance_reports_dir = getattr(bundle.layout, "reports_dir", None)
    guidance_report_path = (
        guidance_reports_dir / f"chapter_{chapter_number:03d}_guidance_contract_audit.json"
        if guidance_reports_dir is not None
        else None
    )
    if guidance_findings:
        guidance_payload = {
            "chapter": chapter_number,
            "stage": "review_finalize",
            "findings": [finding.model_dump(mode="json") for finding in guidance_findings],
            "repair_tickets": [ticket.model_dump(mode="json") for ticket in guidance_tickets],
        }
        if guidance_report_path is not None:
            try:
                context.storage.save_json(guidance_report_path, guidance_payload)
            except Exception as exc:
                _logger.warning(
                    "guidance_contract_audit_report_save_failed | chapter=%d | error=%s",
                    chapter_number,
                    exc,
                )
        on_step("guidance_contract_audit", guidance_payload)
    elif guidance_report_path is not None and context.storage.exists(guidance_report_path):
        try:
            guidance_report_path.unlink()
        except ConsistencyViolationError:
            raise
        except Exception as exc:
            _logger.warning(
                "guidance_contract_audit_report_clear_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
    guidance_blockers = blocking_messages(guidance_findings)
    if guidance_blockers:
        raise ConsistencyViolationError(guidance_blockers)

    # ── Phase 5: Assemble ChapterReviewArtifacts ─────────────────────────────
    return ChapterReviewArtifacts(
        prepared=prepared,
        current_text=current_text,
        performed_edits=performed_edits,
        outcome=outcome,
        alignment_report=alignment_report,
        chapter_repair_report=chapter_repair_report,
        causal_report=causal_report,
        continuity_report=continuity_report,
        repair_plan=repair_plan,
        eval_report=eval_report,
        guard_compliance_report=guard_compliance_report,
        world_rule_report=world_rule_report,
        review_findings=[
            *guard_review_findings,
            *world_rule_findings,
            *guidance_findings,
            *knowledge_boundary_findings,
            *(pov_drift_findings or []),
        ],
        repair_tickets=[
            *guard_repair_tickets,
            *world_rule_tickets,
            *guidance_tickets,
            *knowledge_boundary_tickets,
            *(pov_drift_tickets or []),
        ],
        warnings=review_warnings,
        reading_power_report=reading_power_report,
        allow_word_count_archive_bypass=allow_word_count_archive_bypass,
        quality_reports_stale_after_text_change=False,
        quality_reports_stale_reason="",
        total_repair_rounds_used=total_repair_rounds_used,
        pov_drift_findings=pov_drift_findings or [],
        pov_drift_tickets=pov_drift_tickets or [],
    )


async def _run_archive_preflight_text_repair(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
    spec: ArchivePreflightRepairSpec,
) -> ChapterReviewArtifacts | None:
    """Run the shared continuity-repair path for archive preflight text blockers."""

    bundle = review.prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    context.on_step(
        spec.start_event,
        {
            "chapter": chapter_number,
            **dict(spec.start_payload),
        },
    )

    from novel_forge.pipeline.long.repair import run_continuity_repair
    from novel_forge.pipeline.steps.continuity_repair_step import (
        ContinuityRepairInput,
        ContinuityRepairStep,
    )

    repair_step = ContinuityRepairStep(
        context.router,
        context.builder,
        settings=context.settings,
        trace=trace,
        on_step=getattr(context, "on_step", None),
    )
    kernel_composer = await load_story_kernel_composer(context, bundle)
    kernel_context = (
        kernel_composer.compose_for_step("continuity_repair", chapter_number)
        if kernel_composer is not None
        else {}
    )
    repair_result = await run_continuity_repair(
        repair_step,
        ContinuityRepairInput(
            chapter_number=chapter_number,
            chapter_text=review.current_text,
            chapter_state_packet=review.prepared.packet,
            chapter_bridge=review.prepared.bridge,
            chapter_plan=review.prepared.plan,
            continuity_report=ContinuityReport(
                continuity_score=float(
                    getattr(review.continuity_report, "continuity_score", 8.0) or 8.0
                ),
                summary=spec.continuity_summary,
                issues=[spec.issue],
            ),
            chapter_outline=bundle.chapter_outline,
            style="",
            style_profile=getattr(bundle, "style_profile", None),
            editorial_contract=getattr(bundle, "editorial_contract", None),
            must_fix_issues=(spec.issue,),
            memory_context={"escalation_note": spec.escalation_note},
            kernel_context=kernel_context,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
        ),
    )
    if not repair_result.applied or repair_result.revised_text == review.current_text:
        context.on_step(
            spec.skipped_event,
            {
                "chapter": chapter_number,
                "reason": getattr(repair_result, "failure_reason", "") or "not_applied",
                **dict(spec.skipped_payload),
            },
        )
        return None

    repaired_text = repair_result.revised_text
    change_ratio = _change_ratio(review.current_text, repaired_text)
    reports_stale = change_ratio > 0.01
    repaired_outcome = await extract_and_validate(
        context,
        bundle,
        review.prepared.packet,
        review.prepared.bridge,
        review.prepared.plan,
        repaired_text,
        chapter_number,
        trace,
        review.continuity_report,
        repair_exhausted=True,
    )
    rounds_used = int(getattr(review, "total_repair_rounds_used", 0) or 0) + max(
        0, spec.total_round_increment
    )
    context.on_step(
        spec.complete_event,
        {
            "chapter": chapter_number,
            "change_ratio": round(change_ratio, 4),
            **dict(spec.complete_payload),
        },
    )
    return dataclasses.replace(
        review,
        current_text=repaired_text,
        outcome=repaired_outcome,
        repair_plan=repair_result.repair_plan or review.repair_plan,
        repair_tickets=[*(review.repair_tickets or []), *spec.repair_tickets],
        eval_report=None if reports_stale else review.eval_report,
        quality_reports_stale_after_text_change=(
            review.quality_reports_stale_after_text_change or reports_stale
        ),
        quality_reports_stale_reason=(
            review.quality_reports_stale_reason or (spec.stale_reason if reports_stale else "")
        ),
        total_repair_rounds_used=rounds_used,
    )


async def _repair_contract_execution_audit_block(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
) -> _ArchiveRepairAttempt:
    """Run one bounded text repair when the final contract audit asks for repair."""
    bundle = review.prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    contract_runtime_marker = "contract_runtime_repair_attempted"
    contract_runtime_attempted = contract_runtime_marker in set(review.warnings or [])
    if has_contract_audit_repair_ticket(review.repair_tickets):
        return _ArchiveRepairAttempt(status="already_has_ticket")
    try:
        report_payload = context.storage.load_json(
            bundle.layout.contract_execution_report_path(chapter_number)
        )
    except Exception:
        report_payload = {}
    if not isinstance(report_payload, dict):
        return _ArchiveRepairAttempt(status="repair_failed")

    ticket = compile_contract_audit_repair_ticket(
        report_payload=report_payload,
        chapter_number=chapter_number,
        current_text=review.current_text,
    )
    if ticket is None:
        return _ArchiveRepairAttempt(status="repair_failed")

    blocker_kind = classify_contract_execution_blocker(report_payload, ticket=ticket)
    if (
        blocker_kind == "contract_source_error"
        and not contract_runtime_attempted
        and bool(getattr(context.settings, "long_contract_runtime_repair_enabled", True))
    ):
        if (
            int(getattr(review, "total_repair_rounds_used", 0) or 0)
            >= int(getattr(context.settings, "long_total_repair_rounds_cap", 0) or 0)
            > 0
        ):
            context.on_step(
                "contract_runtime_repair_skipped",
                {
                    "chapter": chapter_number,
                    "ticket_id": ticket.ticket_id,
                    "reason": "total_rounds_cap_reached",
                },
            )
        else:
            context.on_step(
                "contract_runtime_repair_start",
                {
                    "chapter": chapter_number,
                    "ticket_id": ticket.ticket_id,
                    "issue_type": ticket.issue_type,
                },
            )
            runtime_repair = await run_runtime_contract_repair_v2(
                context=context,
                review=review,
                trace=trace,
                ticket=ticket,
            )
            if runtime_repair.applied:
                if runtime_repair.chapter_source_slice is not None:
                    try:
                        bundle.chapter_source_slice = runtime_repair.chapter_source_slice
                    except Exception:
                        pass
                rounds_used = int(getattr(review, "total_repair_rounds_used", 0) or 0) + 1
                context.on_step(
                    "contract_runtime_repair_complete",
                    {
                        "chapter": chapter_number,
                        "ticket_id": ticket.ticket_id,
                        "total_repair_rounds_used": rounds_used,
                    },
                )
                return _ArchiveRepairAttempt(
                    status="repaired",
                    review=dataclasses.replace(
                        review,
                        warnings=[*(review.warnings or []), contract_runtime_marker],
                        total_repair_rounds_used=rounds_used,
                    ),
                )
            if runtime_repair.reason == "runtime_contract_repair_requires_human_review":
                context.on_step(
                    "contract_runtime_repair_review_required",
                    {
                        "chapter": chapter_number,
                        "ticket_id": ticket.ticket_id,
                        "reason": runtime_repair.reason,
                    },
                )
                return _ArchiveRepairAttempt(status="repair_failed")
            context.on_step(
                "contract_runtime_repair_failed",
                {
                    "chapter": chapter_number,
                    "ticket_id": ticket.ticket_id,
                    "reason": runtime_repair.reason,
                },
            )

    issue = contract_audit_ticket_to_continuity_issue(ticket, review.current_text)
    repaired_review = await _run_archive_preflight_text_repair(
        context,
        review=review,
        trace=trace,
        spec=ArchivePreflightRepairSpec(
            issue=issue,
            continuity_summary="章节契约执行审计归档前定向修复",
            escalation_note=(
                "这是归档前契约执行审计的定向修复；只改写命中句或相邻段落，"
                "不得新增后续章节计划或扩大情节进展。"
            ),
            stale_reason="contract_execution_repair_before_archive",
            start_event="contract_execution_repair_start",
            skipped_event="contract_execution_repair_skipped",
            complete_event="contract_execution_repair_complete",
            start_payload={
                "ticket_id": ticket.ticket_id,
                "issue_type": ticket.issue_type,
                "anchored": bool(issue.paragraph_start),
            },
            skipped_payload={"ticket_id": ticket.ticket_id},
            complete_payload={"ticket_id": ticket.ticket_id},
            repair_tickets=(ticket,),
        ),
    )
    if repaired_review is None:
        return _ArchiveRepairAttempt(status="repair_failed")
    return _ArchiveRepairAttempt(status="repaired", review=repaired_review)


def is_carry_forward_block_exception(exc: BaseException) -> bool:
    """Classify a ``ConsistencyViolationError`` raised by the carry-forward gate.

    The hard gate in ``_enforce_archive_hard_quality_blocks`` emits a message
    containing "必须承接的开放项"; this classifier lets the finalize loop
    route such blocks to the dedicated carry-forward repair instead of
    bubbling up as an unrecoverable replan.
    """
    if getattr(exc, "block_kind", "") == BLOCK_KIND_CARRY_FORWARD:
        return True
    messages = list(getattr(exc, "violations", []) or [])
    if not messages:
        messages = [str(exc)]
    return any("必须承接的开放项" in str(message) for message in messages)


def is_post_humanize_semantic_block_exception(exc: BaseException) -> bool:
    """Return whether read-only post-Humanize verification requested repair."""

    return str(getattr(exc, "violation_kind", "") or "").strip() == (
        "post_humanize_semantic_verification"
    )


async def _repair_unclosed_carry_forward(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
) -> ChapterReviewArtifacts | None:
    """Run one bounded text repair to land an unclosed carry-forward item.

    Mirrors ``_repair_contract_execution_audit_block``: builds a critical
    ``ContinuityIssue`` from the unclosed items, then runs
    ``ContinuityRepairStep`` in insert mode to weave an explicit response
    into the opening or a key transition. Returns the repaired review, or
    ``None`` if repair did not apply (so the caller re-raises the block).
    """
    bundle = review.prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    packet = review.prepared.packet

    from novel_forge.pipeline.long.stages.finalize_checks import _unclosed_carry_forward_items

    unclosed = _unclosed_carry_forward_items(packet, review.current_text)
    if not unclosed:
        return None

    # Respect the global repair-rounds cap shared across all repair dimensions.
    rounds_cap = int(getattr(context.settings, "long_total_repair_rounds_cap", 0) or 0)
    rounds_used = int(getattr(review, "total_repair_rounds_used", 0) or 0)
    if rounds_cap > 0 and rounds_used >= rounds_cap:
        context.on_step(
            "carry_forward_repair_skipped",
            {"chapter": chapter_number, "reason": "total_rounds_cap_reached"},
        )
        return None

    issue = ContinuityIssue(
        issue_type="carry_forward_missing",
        severity="critical",
        source="postcondition",
        blocking=True,
        summary="上一章必须承接的开放项未在本章落地，需补写显式回应。",
        evidence="；".join(unclosed),
        location="开场或关键转折处（承接上一章遗留信息）",
        location_confidence=0.6,
        anchor_type="inferred_scope",
        paragraph_start=1,
        paragraph_end=3,
        evidence_quote="；".join(unclosed[:2]),
        fix_mode="insert",
        rewrite_scope="opening",
        fix_actions=[
            "在开场前几段补上对上一章遗留状态的显式回应（动作、物件或对话承载）。",
            "不得新增后续章节计划或扩大情节进展，仅补写承接。",
        ],
    )
    return await _run_archive_preflight_text_repair(
        context,
        review=review,
        trace=trace,
        spec=ArchivePreflightRepairSpec(
            issue=issue,
            continuity_summary="归档前承接硬门定向修复",
            escalation_note=(
                "这是归档前承接硬门的定向修复；只补写对上一章遗留状态的回应，"
                "不得新增后续章节计划或扩大情节进展。"
            ),
            stale_reason="carry_forward_repair_before_archive",
            start_event="carry_forward_repair_start",
            skipped_event="carry_forward_repair_skipped",
            complete_event="carry_forward_repair_complete",
            start_payload={"unclosed_count": len(unclosed)},
            complete_payload={"total_repair_rounds_used": rounds_used + 1},
            total_round_increment=1,
        ),
    )


async def _repair_post_humanize_semantic_block(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
) -> ChapterReviewArtifacts | None:
    """Repair a post-Humanize knowledge-boundary block outside persistence.

    Final verification is deliberately read-only.  When it detects a hard
    blocker, this helper re-enters the existing Repair v2 lane and returns a
    stale review candidate.  The next archive attempt must therefore rerun
    terminal Humanize and all final reports before it can persist anything.
    """

    chapter_number = review.prepared.bundle.chapter_outline.chapter_number
    rounds_cap = int(getattr(context.settings, "long_total_repair_rounds_cap", 0) or 0)
    rounds_used = int(getattr(review, "total_repair_rounds_used", 0) or 0)
    if rounds_cap > 0 and rounds_used >= rounds_cap:
        context.on_step(
            "post_humanize_semantic_repair_skipped",
            {
                "chapter": chapter_number,
                "reason": "total_rounds_cap_reached",
                "rounds_used": rounds_used,
                "rounds_cap": rounds_cap,
            },
        )
        return None

    findings = await _run_knowledge_boundary_verification(
        context,
        review.prepared,
        review.current_text,
        chapter_number,
    )
    blocking_findings = [
        finding
        for finding in findings
        if bool(getattr(finding, "blocks_finalize", False))
        and severity_at_least(getattr(finding, "severity", ""), "high")
        and float(getattr(finding, "confidence", 0.0) or 0.0) >= 0.9
    ]
    if not blocking_findings:
        context.on_step(
            "post_humanize_semantic_repair_skipped",
            {
                "chapter": chapter_number,
                "reason": "blocking_finding_not_reproduced",
            },
        )
        return None

    max_rounds = 1 if rounds_cap <= 0 else min(1, rounds_cap - rounds_used)
    runner = FlowContextAdapter(context)
    repair_result = await run_knowledge_boundary_repair_loop(
        runner=runner,
        storage=context.storage,
        bundle=review.prepared.bundle,
        packet=review.prepared.packet,
        current_text=review.current_text,
        chapter_number=chapter_number,
        findings=blocking_findings,
        trace=trace,
        max_rounds=max_rounds,
    )
    repaired_text = repair_result.current_text
    if repaired_text == review.current_text:
        context.on_step(
            "post_humanize_semantic_repair_skipped",
            {
                "chapter": chapter_number,
                "reason": "repair_did_not_change_text",
                "repair_exhausted": repair_result.repair_exhausted,
            },
        )
        return None

    refreshed_outcome = await extract_and_validate(
        runner,
        review.prepared.bundle,
        review.prepared.packet,
        review.prepared.bridge,
        review.prepared.plan,
        repaired_text,
        chapter_number,
        trace,
        review.continuity_report,
        repair_exhausted=True,
    )
    used = max(1, int(getattr(repair_result, "rounds_used", 0) or 0))
    context.on_step(
        "post_humanize_semantic_repair_complete",
        {
            "chapter": chapter_number,
            "rounds_used": used,
            "total_repair_rounds_used": rounds_used + used,
            "requires_rehumanize": True,
        },
    )
    remaining_findings = [
        finding
        for finding in list(review.review_findings or [])
        if str(getattr(finding, "dimension", "") or "").strip().lower() != "knowledge_boundary"
    ]
    repaired_findings = list(getattr(repair_result, "findings_after", []) or [])
    remaining_tickets = [
        ticket
        for ticket in list(review.repair_tickets or [])
        if str(getattr(ticket, "dimension", "") or "").strip().lower() != "knowledge_boundary"
    ]
    return dataclasses.replace(
        review,
        current_text=repaired_text,
        performed_edits=review.performed_edits + 1,
        outcome=refreshed_outcome,
        eval_report=None,
        review_findings=[*remaining_findings, *repaired_findings],
        repair_tickets=[
            *remaining_tickets,
            *compile_repair_tickets_from_findings(repaired_findings),
        ],
        total_repair_rounds_used=rounds_used + used,
        quality_reports_stale_after_text_change=True,
        quality_reports_stale_reason="post_humanize_semantic_repair_before_rehumanize",
    )


def _checkpoint_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="json"))
    return None


def _save_finalization_review_progress(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    completed_stage: str,
    source_hash: str,
) -> None:
    """Persist the last safe finalization hash before any commit side effects."""

    chapter_number = review.prepared.bundle.chapter_outline.chapter_number
    layout = review.prepared.bundle.layout
    current_hash = source_text_hash(review.current_text)
    context.storage.save_json(
        layout.chapter_canon_outcome_path(chapter_number),
        review.outcome.model_dump(mode="json"),
    )
    save_review_progress(
        storage=context.storage,
        layout=layout,
        chapter_number=chapter_number,
        progress=ReviewProgressState(
            completed_stage=cast(Any, completed_stage),
            current_text=review.current_text,
            performed_edits=review.performed_edits,
            total_repair_rounds_used=review.total_repair_rounds_used,
            alignment_report=_checkpoint_payload(review.alignment_report),
            continuity_report=_checkpoint_payload(review.continuity_report),
            chapter_repair_report=_checkpoint_payload(review.chapter_repair_report),
            causal_report=_checkpoint_payload(review.causal_report),
            repair_plan=_checkpoint_payload(review.repair_plan),
            reading_power_report=_checkpoint_payload(review.reading_power_report),
            eval_report=_checkpoint_payload(review.eval_report),
            warnings=list(review.warnings or []),
            pipeline_version=_SINGLE_FINAL_VERIFY_PIPELINE_VERSION,
            source_text_hash=source_hash,
            refinement_text_hash=current_hash,
            final_verify_text_hash=(current_hash if completed_stage == "final_verify_done" else ""),
        ),
    )


def _clear_finalization_review_progress(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
) -> None:
    chapter_number = review.prepared.bundle.chapter_outline.chapter_number
    layout = review.prepared.bundle.layout
    clear_review_progress(context.storage, layout, chapter_number)
    try:
        outcome_path = layout.chapter_canon_outcome_path(chapter_number)
        if outcome_path.exists():
            outcome_path.unlink()
    except OSError:
        _logger.warning(
            "finalization_canon_checkpoint_cleanup_failed | chapter=%d",
            chapter_number,
        )


def _is_terminal_humanize_regression(exc: BaseException) -> bool:
    kind = str(getattr(exc, "violation_kind", "") or "").strip()
    stage = str(getattr(exc, "failed_stage", "") or "").strip()
    return kind in {
        "post_humanize_semantic_verification",
        "terminal_humanize_postcondition",
    } or stage.startswith("terminal_humanize")


async def _run_single_final_refinement(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
) -> tuple[ChapterReviewArtifacts, ArchivePreflightResult]:
    """Converge all remaining text mutations before the archive verifier."""

    if review.refinement_done:
        decision = ReportRefreshDecision.empty()
        return review, ArchivePreflightResult(
            current_text=review.current_text,
            outcome=review.outcome,
            eval_report=review.eval_report,
            chapter_repair_report=review.chapter_repair_report,
            state_adjudication_report=None,
            knowledge_boundary_findings=[],
            terminal_humanize_metadata={"applied": False, "resumed": True},
            refresh_decision=decision,
        )

    runner = FlowContextAdapter(context)
    bundle = review.prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    source_review = review
    source_hash = source_text_hash(review.current_text)
    decision = ReportRefreshDecision.empty()
    if review.quality_reports_stale_after_text_change:
        decision.mark_existing_stale(
            review.quality_reports_stale_reason or "semantic_text_changed_before_final_refinement"
        )

    pre_humanize_review = review

    async def _terminal_humanize(current_text: str) -> str:
        nonlocal review, pre_humanize_review
        from novel_forge.pipeline.long.chapter_flow_orchestrate import (
            _apply_terminal_humanize,
        )

        pre_humanize_review = dataclasses.replace(review, current_text=current_text)
        review = await _apply_terminal_humanize(
            context,
            pre_humanize_review,
            trace,
        )
        return review.current_text

    try:
        preflight = await run_archive_preflight_repairs(
            runner=runner,
            bundle=bundle,
            packet=review.prepared.packet,
            bridge=review.prepared.bridge,
            plan=review.prepared.plan,
            outcome=review.outcome,
            current_text=review.current_text,
            chapter_number=chapter_number,
            trace=trace,
            continuity_report=review.continuity_report,
            eval_report=review.eval_report,
            chapter_repair_report=review.chapter_repair_report,
            memory_hints=review.prepared.memory_hints,
            allow_word_count_archive_bypass=review.allow_word_count_archive_bypass,
            terminal_humanize=_terminal_humanize,
            decision=decision,
        )
    except ConsistencyViolationError as exc:
        if not _is_terminal_humanize_regression(exc):
            raise
        # Humanize is optional surface refinement. A hard semantic regression
        # rejects that candidate and restores the already validated input hash;
        # it never triggers another semantic patch cycle.
        review = pre_humanize_review
        rollback_decision = ReportRefreshDecision.empty()
        if source_text_hash(review.current_text) != source_hash:
            rollback_decision.mark_text_change(
                runner=runner,
                chapter_number=chapter_number,
                reason="pre_humanize_refinement_retained_after_humanize_rollback",
                before_text=source_review.current_text,
                after_text=review.current_text,
            )
        context.on_step(
            "terminal_humanize_rolled_back",
            {
                "chapter": chapter_number,
                "reason": str(getattr(exc, "violation_kind", "") or type(exc).__name__),
                "restored_text_hash": source_text_hash(review.current_text),
                "action": "keep_pre_humanize_validated_text",
            },
        )
        preflight = await run_archive_preflight_repairs(
            runner=runner,
            bundle=bundle,
            packet=review.prepared.packet,
            bridge=review.prepared.bridge,
            plan=review.prepared.plan,
            outcome=review.outcome,
            current_text=review.current_text,
            chapter_number=chapter_number,
            trace=trace,
            continuity_report=review.continuity_report,
            eval_report=review.eval_report,
            chapter_repair_report=review.chapter_repair_report,
            memory_hints=review.prepared.memory_hints,
            allow_word_count_archive_bypass=review.allow_word_count_archive_bypass,
            allow_semantic_pre_archive_repairs=False,
            allow_prompt_leak_patch_repair=False,
            allow_narrative_state_repair=False,
            allow_knowledge_boundary_repair=False,
            terminal_humanize=None,
            decision=rollback_decision,
        )

    performed_edits = review.performed_edits
    if (
        preflight.current_text != source_review.current_text
        and performed_edits == source_review.performed_edits
    ):
        performed_edits += 1
    review = dataclasses.replace(
        review,
        current_text=preflight.current_text,
        outcome=preflight.outcome,
        eval_report=preflight.eval_report,
        chapter_repair_report=preflight.chapter_repair_report,
        performed_edits=performed_edits,
        refinement_done=True,
        final_verify_done=False,
        quality_reports_stale_after_text_change=preflight.refresh_decision.refresh_quality,
        quality_reports_stale_reason=preflight.refresh_decision.refresh_reason(),
    )
    return review, preflight


def _single_final_verify_budget_available(
    context: ChapterExecutionContext,
    review: ChapterReviewArtifacts,
) -> bool:
    cap = int(getattr(context.settings, "long_total_repair_rounds_cap", 0) or 0)
    return cap <= 0 or int(review.total_repair_rounds_used or 0) < cap


async def _persist_single_final_verify(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
    eval_report: EvalReport | None,
    emit_evaluate_step: bool,
    allow_contract_audit_auto_repair: bool,
    allow_carry_forward_auto_repair: bool,
) -> tuple[Any, ChapterReviewArtifacts]:
    """Run one final verifier, with at most one targeted repair re-entry."""

    runner = FlowContextAdapter(context)
    chapter_number = review.prepared.bundle.chapter_outline.chapter_number
    source_hash = source_text_hash(review.current_text)
    carry_forward_repaired = False

    async def _persist(preflight: ArchivePreflightResult) -> Any:
        return await persist_results(
            runner,
            review.prepared.bundle,
            review.prepared.packet,
            review.prepared.bridge,
            review.prepared.plan,
            preflight.outcome,
            preflight.current_text,
            review.performed_edits,
            review.alignment_report,
            preflight.chapter_repair_report,
            review.continuity_report,
            review.causal_report,
            review.repair_plan,
            trace,
            chapter_number,
            eval_report=preflight.eval_report or eval_report or review.eval_report,
            emit_evaluate_step=emit_evaluate_step,
            memory_hints=review.prepared.memory_hints,
            window_manager=review.prepared.window_manager,
            allow_word_count_archive_bypass=review.allow_word_count_archive_bypass,
            force_mark_quality_reports_stale=preflight.refresh_decision.refresh_quality,
            quality_reports_stale_reason=preflight.refresh_decision.refresh_reason(),
            allow_carry_forward_archive_bypass=carry_forward_repaired,
            precomputed_preflight=preflight,
        )

    review, preflight = await _run_single_final_refinement(
        context,
        review=review,
        trace=trace,
    )
    _save_finalization_review_progress(
        context,
        review=review,
        completed_stage="refinement_done",
        source_hash=source_hash,
    )
    try:
        persisted = await _persist(preflight)
    except ConsistencyViolationError as exc:
        is_carry = is_carry_forward_block_exception(exc)
        is_contract = is_contract_audit_block_exception(exc)
        can_repair = _single_final_verify_budget_available(context, review) and (
            (is_carry and allow_carry_forward_auto_repair)
            or (is_contract and allow_contract_audit_auto_repair)
        )
        if not can_repair:
            raise
        if is_carry:
            repaired_review = await _repair_unclosed_carry_forward(
                context,
                review=review,
                trace=trace,
            )
            carry_forward_repaired = True
        else:
            repair_attempt = await _repair_contract_execution_audit_block(
                context,
                review=review,
                trace=trace,
            )
            repaired_review = repair_attempt.review
            if repair_attempt.status != "repaired":
                repaired_review = None
        if repaired_review is None:
            raise
        review = dataclasses.replace(
            repaired_review,
            refinement_done=False,
            final_verify_done=False,
        )
        review, preflight = await _run_single_final_refinement(
            context,
            review=review,
            trace=trace,
        )
        _save_finalization_review_progress(
            context,
            review=review,
            completed_stage="refinement_done",
            source_hash=source_hash,
        )
        # Deliberately no recursive catch: this is the single allowed re-entry.
        persisted = await _persist(preflight)

    review = dataclasses.replace(
        review,
        current_text=persisted.current_text,
        outcome=persisted.outcome,
        eval_report=persisted.eval_report,
        alignment_report=persisted.alignment_report,
        chapter_repair_report=persisted.chapter_repair_report,
        continuity_report=persisted.continuity_report,
        causal_report=persisted.causal_report,
        reading_power_report=persisted.reading_power_report or review.reading_power_report,
        refinement_done=True,
        final_verify_done=True,
    )
    final_hash = source_text_hash(review.current_text)
    _save_finalization_review_progress(
        context,
        review=review,
        completed_stage="final_verify_done",
        source_hash=source_hash,
    )
    context.on_step(
        "final_text_hash_verified",
        {
            "chapter": chapter_number,
            "source_text_hash": final_hash,
            "pipeline_version": _SINGLE_FINAL_VERIFY_PIPELINE_VERSION,
            "semantic_mutation_allowed_after": False,
        },
    )
    _clear_finalization_review_progress(context, review=review)
    return persisted, review


async def finalize_chapter_result(
    context: ChapterExecutionContext,
    *,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
    eval_report: EvalReport | None = None,
    emit_evaluate_step: bool = True,
    allow_contract_audit_auto_repair: bool = True,
    allow_carry_forward_auto_repair: bool = True,
) -> ChapterResult:
    """Persist reviewed artifacts, update canon, and build the final chapter result.

    Retry state machine (NOT a linear 3-step sequence):

        persist_results (1st attempt)
            |
            +--> Success -> proceed to Quality Gate + ChapterResult
            |
            +--> ConsistencyViolationError
                 |-- Classify exception:
                 |   is_carry_forward_block?  -> _repair_unclosed_carry_forward
                 |   is_contract_audit_block? -> _repair_contract_execution_audit_block
                 |   post-Humanize semantic?  -> Repair v2 outside persistence
                 |   neither?                 -> re-raise (unrecoverable)
                 |
                 +--> Repair applied
                 |    |
                 |    persist_results (2nd attempt)
                 |        |
                 |        +--> Success -> proceed
                 |        +--> ConsistencyViolationError (contract audit or
                 |             post-Humanize semantic verification)
                 |             |
                 |             +--> _repair_contract_execution_audit_block (2nd)
                 |                  |
                 |                  persist_results (3rd attempt, final)
                 |
                 +--> Repair failed (returned None) -> re-raise

    The 2nd persist can trigger a 3rd attempt for contract-audit or
    post-Humanize semantic repair. Carry-forward repair remains one-shot.
    Every successful text repair enters a fresh persist attempt, whose terminal
    Humanize callback runs again before the read-only archive gate.
    """
    runner = FlowContextAdapter(context)
    chapter_number = review.prepared.bundle.chapter_outline.chapter_number

    async def _terminal_humanize_text(current_text: str) -> str:
        nonlocal review
        from novel_forge.pipeline.long.chapter_flow_orchestrate import (
            _apply_terminal_humanize,
        )

        review = await _apply_terminal_humanize(
            context,
            dataclasses.replace(review, current_text=current_text),
            trace,
        )
        return review.current_text

    single_final_verify = bool(getattr(context.settings, "long_single_final_verify_enabled", False))
    try:
        if single_final_verify:
            persisted, review = await _persist_single_final_verify(
                context,
                review=review,
                trace=trace,
                eval_report=eval_report,
                emit_evaluate_step=emit_evaluate_step,
                allow_contract_audit_auto_repair=allow_contract_audit_auto_repair,
                allow_carry_forward_auto_repair=allow_carry_forward_auto_repair,
            )
        else:
            persisted = await persist_results(
                runner,
                review.prepared.bundle,
                review.prepared.packet,
                review.prepared.bridge,
                review.prepared.plan,
                review.outcome,
                review.current_text,
                review.performed_edits,
                review.alignment_report,
                review.chapter_repair_report,
                review.continuity_report,
                review.causal_report,
                review.repair_plan,
                trace,
                chapter_number,
                eval_report=eval_report or review.eval_report,
                emit_evaluate_step=emit_evaluate_step,
                memory_hints=review.prepared.memory_hints,
                window_manager=review.prepared.window_manager,
                allow_word_count_archive_bypass=review.allow_word_count_archive_bypass,
                force_mark_quality_reports_stale=review.quality_reports_stale_after_text_change,
                quality_reports_stale_reason=review.quality_reports_stale_reason,
                terminal_humanize=None if review.refinement_done else _terminal_humanize_text,
            )
    except ConsistencyViolationError as exc:
        if single_final_verify:
            raise
        is_carry_forward_block = is_carry_forward_block_exception(exc)
        is_contract_block = is_contract_audit_block_exception(exc)
        is_post_humanize_block = is_post_humanize_semantic_block_exception(exc)
        can_auto_repair = (
            (is_contract_block and allow_contract_audit_auto_repair)
            or (is_carry_forward_block and allow_carry_forward_auto_repair)
            or is_post_humanize_block
        )
        if not can_auto_repair:
            raise
        if is_carry_forward_block:
            repaired_review = await _repair_unclosed_carry_forward(
                context,
                review=review,
                trace=trace,
            )
        elif is_contract_block:
            repair_attempt = await _repair_contract_execution_audit_block(
                context,
                review=review,
                trace=trace,
            )
            if repair_attempt.status != "repaired" or repair_attempt.review is None:
                raise
            repaired_review = repair_attempt.review
        else:
            repaired_review = await _repair_post_humanize_semantic_block(
                context,
                review=review,
                trace=trace,
            )
        if repaired_review is None:
            raise
        review = repaired_review
        carry_forward_repaired = is_carry_forward_block
        try:
            persisted = await persist_results(
                runner,
                review.prepared.bundle,
                review.prepared.packet,
                review.prepared.bridge,
                review.prepared.plan,
                review.outcome,
                review.current_text,
                review.performed_edits,
                review.alignment_report,
                review.chapter_repair_report,
                review.continuity_report,
                review.causal_report,
                review.repair_plan,
                trace,
                chapter_number,
                eval_report=review.eval_report,
                emit_evaluate_step=emit_evaluate_step,
                memory_hints=review.prepared.memory_hints,
                window_manager=review.prepared.window_manager,
                allow_word_count_archive_bypass=review.allow_word_count_archive_bypass,
                force_mark_quality_reports_stale=review.quality_reports_stale_after_text_change,
                quality_reports_stale_reason=review.quality_reports_stale_reason,
                allow_carry_forward_archive_bypass=carry_forward_repaired,
                terminal_humanize=_terminal_humanize_text,
            )
        except ConsistencyViolationError as second_exc:
            is_contract_block = is_contract_audit_block_exception(second_exc)
            is_post_humanize_block = is_post_humanize_semantic_block_exception(second_exc)
            if not (
                (allow_contract_audit_auto_repair and is_contract_block) or is_post_humanize_block
            ):
                raise
            if is_contract_block:
                repair_attempt = await _repair_contract_execution_audit_block(
                    context,
                    review=review,
                    trace=trace,
                )
                repaired_review = repair_attempt.review
                if repair_attempt.status != "repaired" or repaired_review is None:
                    raise
            else:
                repaired_review = await _repair_post_humanize_semantic_block(
                    context,
                    review=review,
                    trace=trace,
                )
            if repaired_review is None:
                raise
            review = repaired_review
            persisted = await persist_results(
                runner,
                review.prepared.bundle,
                review.prepared.packet,
                review.prepared.bridge,
                review.prepared.plan,
                review.outcome,
                review.current_text,
                review.performed_edits,
                review.alignment_report,
                review.chapter_repair_report,
                review.continuity_report,
                review.causal_report,
                review.repair_plan,
                trace,
                chapter_number,
                eval_report=review.eval_report,
                emit_evaluate_step=emit_evaluate_step,
                memory_hints=review.prepared.memory_hints,
                window_manager=review.prepared.window_manager,
                allow_word_count_archive_bypass=review.allow_word_count_archive_bypass,
                force_mark_quality_reports_stale=review.quality_reports_stale_after_text_change,
                quality_reports_stale_reason=review.quality_reports_stale_reason,
                allow_carry_forward_archive_bypass=carry_forward_repaired,
                terminal_humanize=_terminal_humanize_text,
            )
    final_eval_report = persisted.eval_report
    review = dataclasses.replace(
        review,
        current_text=persisted.current_text,
        outcome=persisted.outcome,
        eval_report=final_eval_report,
        alignment_report=persisted.alignment_report,
        chapter_repair_report=persisted.chapter_repair_report,
        continuity_report=persisted.continuity_report,
        causal_report=persisted.causal_report,
        reading_power_report=persisted.reading_power_report or review.reading_power_report,
        guard_compliance_report=(
            persisted.guard_compliance_report or review.guard_compliance_report
        ),
        review_findings=[
            *(review.review_findings or []),
            *(persisted.review_findings or []),
        ],
        repair_tickets=[
            *(review.repair_tickets or []),
            *(persisted.repair_tickets or []),
        ],
    )
    meta = ChapterMeta(
        chapter_number=chapter_number,
        title=review.prepared.bundle.chapter_outline.title,
        word_count=count_chapter_words(review.current_text),
        edit_rounds=review.performed_edits,
        tokens_used=trace.total_tokens,
        cost_usd=trace.total_cost,
    )

    from novel_forge.pipeline.long.chapter_flow_review import (
        _build_normalized_review_contracts,
        _build_quality_gate,
    )

    quality_gate = _build_quality_gate(
        review,
        settings=context.settings,
        target_word_count=int(
            getattr(review.prepared.bundle.chapter_outline, "expected_word_count", 0) or 0
        ),
    )
    normalized_findings, normalized_tickets = _build_normalized_review_contracts(
        review,
        chapter_number=chapter_number,
    )
    quality_gate_payload = _persist_quality_gate_report(
        context.storage,
        review.prepared.bundle.layout,
        chapter_number,
        quality_gate,
        guard_compliance_report=review.guard_compliance_report,
        review_findings=normalized_findings,
        repair_tickets=normalized_tickets,
    )
    context.on_step(
        "quality_gate",
        {
            "chapter": chapter_number,
            "verdict": quality_gate_payload["verdict"],
            "summary": quality_gate_payload["summary"],
        },
    )

    # ── TTS metadata extraction (deterministic, zero LLM cost) ──────────────
    tts_metadata_dict = extract_tts_metadata(
        plan=review.prepared.plan,
        editorial_contract=getattr(review.prepared.bundle, "editorial_contract", None),
        reading_power_report=review.reading_power_report,
        chapter_number=chapter_number,
        source_text=review.current_text,
    )

    # Persist TTS metadata as standalone report for downstream TTS pipeline
    if tts_metadata_dict is not None:
        try:
            _reports_dir = getattr(review.prepared.bundle.layout, "reports_dir", None)
            if _reports_dir is not None:
                _tts_report_path = _reports_dir / f"chapter_{chapter_number:03d}_tts_metadata.json"
                context.storage.save_json(_tts_report_path, tts_metadata_dict)
                context.on_step(
                    "tts_metadata_extracted",
                    {
                        "chapter": chapter_number,
                        "scenes": len(tts_metadata_dict.get("scene_emotion_map", [])),
                        "path": str(_tts_report_path),
                    },
                )
        except Exception as exc:
            _logger.warning(
                "tts_metadata_persist_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

    return ChapterResult(
        meta=meta,
        text=review.current_text,
        canon_delta=review.outcome,
        creative_report=review.outcome.creative_report,
        eval_report=final_eval_report,
        alignment_report=review.alignment_report,
        chapter_repair_report=review.chapter_repair_report,
        causal_report=review.causal_report,
        continuity_report=review.continuity_report,
        repair_plan=review.repair_plan,
        bridge=review.prepared.bridge,
        chapter_exit_state=review.outcome.chapter_exit_state,
        warnings=list(review.warnings or []),
        quality_gate_report=quality_gate_payload,
        trace_summary=trace.summary(),
        reading_power_report=review.reading_power_report,
        tts_metadata=tts_metadata_dict,
    )


def _dedupe_repair_tickets(tickets: list[Any]) -> list[Any]:
    """Keep one repair ticket per finding/issue target across staged and final reports."""

    def _ticket_key(ticket: Any) -> tuple[Any, ...]:
        finding_ids = tuple(
            str(item or "").strip() for item in getattr(ticket, "finding_ids", []) or []
        )
        if finding_ids:
            return ("findings", finding_ids)
        ticket_id = str(getattr(ticket, "ticket_id", "") or "").strip()
        if ticket_id:
            return ("ticket", ticket_id)
        return (
            "target",
            int(getattr(ticket, "chapter_number", 0) or 0),
            str(getattr(ticket, "dimension", "") or "").strip(),
            str(getattr(ticket, "issue_type", "") or "").strip(),
            str(getattr(ticket, "target_summary", "") or "").strip(),
            str(getattr(ticket, "repair_goal", "") or "").strip(),
        )

    result: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for ticket in tickets:
        key = _ticket_key(ticket)
        if key in seen:
            continue
        seen.add(key)
        result.append(ticket)
    return result


def _continuity_issue_key(issue: Any) -> tuple[Any, ...]:
    issue_id = str(getattr(issue, "issue_id", "") or "").strip()
    if issue_id:
        return ("issue_id", issue_id)
    return (
        "content",
        str(getattr(issue, "issue_type", "") or "").strip().lower(),
        str(getattr(issue, "summary", "") or "").strip(),
        str(getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "") or "").strip(),
        str(getattr(issue, "location", "") or "").strip(),
        int(getattr(issue, "paragraph_start", 0) or 0),
        int(getattr(issue, "paragraph_end", 0) or 0),
    )


def _dedupe_continuity_issues(issues: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[tuple[Any, ...]] = set()
    for issue in issues:
        key = _continuity_issue_key(issue)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def normalize_continuity_report_repair_state(report: Any) -> Any:
    """Normalize continuity report issues/tickets to the final remaining targets.

    Continuity rechecks return the remaining issue list. After local merge points
    append tickets/issues, this keeps one copy of each remaining issue and drops
    repair tickets that no longer match a remaining issue.
    """

    if report is None:
        return report

    issues = _dedupe_continuity_issues(list(getattr(report, "issues", []) or []))
    tickets = _dedupe_repair_tickets(list(getattr(report, "repair_tickets", []) or []))
    if tickets:
        if issues:
            tickets = [
                ticket
                for ticket in tickets
                if any(repair_ticket_matches_remaining_issue(ticket, issue) for issue in issues)
            ]
        else:
            tickets = []

    update = {
        "issues": issues,
        "repair_tickets": tickets,
    }
    if hasattr(report, "model_copy"):
        return report.model_copy(update=update)
    try:
        copied_report = copy.copy(report)
        for key, value in update.items():
            setattr(copied_report, key, value)
        return copied_report
    except Exception as exc:
        raise TypeError(
            "continuity_report must support model_copy or shallow copy for normalization"
        ) from exc


async def _refresh_mismatched_guidance_reports(
    context: ChapterExecutionContext,
    *,
    prepared: PreparedChapterArtifacts,
    current_text: str,
    chapter_number: int,
    trace: PipelineTrace,
    mismatch_sources: set[str],
    alignment_report: Any,
    continuity_report: Any,
    causal_report: Any,
) -> tuple[Any, Any, Any]:
    """Re-run malformed reports once before escalating to a chapter replan.

    This compatibility wrapper preserves the old guidance-audit events while
    routing refresh decisions through ``ReviewReportService`` so hash/context
    stamping and report construction stay centralized.
    """
    if not mismatch_sources:
        return alignment_report, continuity_report, causal_report

    runner = FlowContextAdapter(context)
    context.on_step(
        "guidance_report_recheck",
        {"chapter": chapter_number, "sources": sorted(mismatch_sources)},
    )

    service = ReviewReportService(
        runner=runner,
        bundle=prepared.bundle,
        packet=prepared.packet,
        bridge=prepared.bridge,
        plan=prepared.plan,
        chapter_number=chapter_number,
        trace=trace,
    )

    async def _refresh_one(source: str, kind: str) -> Any:
        refreshed = await service.ensure_current(
            current_text=current_text,
            alignment_report=alignment_report,
            continuity_report=continuity_report,
            causal_report=causal_report,
            report_kinds=(kind,),
            stale_reason=f"guidance_report_mismatch:{source}",
            continuity_recheck_mode=True,
            continuity_strict_review=True,
            causal_recheck_mode=True,
            causal_strict_review=True,
            force_refresh=True,
        )
        if kind == "alignment":
            return refreshed.alignment_report
        if kind == "continuity":
            report = refreshed.continuity_report
            context.on_step(
                "continuity_eval_guidance_recheck",
                {
                    "chapter": chapter_number,
                    "score": round(float(getattr(report, "continuity_score", 0.0)), 2),
                    "issues": len(getattr(report, "issues", []) or []),
                },
            )
            return report
        report = refreshed.causal_report
        context.on_step(
            "causal_eval_guidance_recheck",
            {
                "chapter": chapter_number,
                "score": round(float(getattr(report, "causal_score", 0.0)), 2),
                "issues": len(getattr(report, "issues", []) or []),
            },
        )
        return report

    async def _refresh_alignment() -> Any:
        if "alignment_report" not in mismatch_sources:
            return alignment_report
        return await _refresh_one("alignment_report", "alignment")

    async def _refresh_continuity() -> Any:
        if "continuity_report" not in mismatch_sources:
            return continuity_report
        return await _refresh_one("continuity_report", "continuity")

    async def _refresh_causal() -> Any:
        if "causal_report" not in mismatch_sources:
            return causal_report
        return await _refresh_one("causal_report", "causal")

    results = await asyncio.gather(
        _refresh_alignment(),
        _refresh_continuity(),
        _refresh_causal(),
        return_exceptions=True,
    )
    labels = ("alignment_report", "continuity_report", "causal_report")
    current = [alignment_report, continuity_report, causal_report]
    for index, result in enumerate(results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            context.on_step(
                "guidance_report_recheck_failed",
                {
                    "chapter": chapter_number,
                    "source": labels[index],
                    "error": str(result),
                },
            )
            continue
        current[index] = result
    return current[0], current[1], current[2]


def _persist_quality_gate_report(
    storage: Any,
    layout: Any,
    chapter_number: int,
    gate: QualityGate,
    *,
    guard_compliance_report: dict[str, Any] | None = None,
    review_findings: list[Any] | None = None,
    repair_tickets: list[Any] | None = None,
) -> dict[str, Any]:
    """Persist quality-gate report and return the serialized dict."""
    report = gate.report()
    failed_dimensions = [c.dimension for c in report.checks if not c.passed]
    payload: dict[str, Any] = {
        "report_type": "post_review_quality_report",
        "archive_blocking": False,
        "blocking_policy": (
            "归档硬阻断在最终持久化前执行；此报告用于汇总质量维度、审查问题与修复票据，"
            "verdict=fail 表示需要人工关注或后续修复，不等同于此报告再次阻断归档。"
        ),
        "verdict": report.verdict.value,
        "summary": report.summary,
        "failed_dimensions": failed_dimensions,
        "dimension_scores": {
            c.dimension: {
                "score": c.score,
                "threshold": c.threshold,
                "passed": c.passed,
            }
            for c in report.checks
        },
        "checks": [
            {
                "dimension": c.dimension,
                "score": c.score,
                "threshold": c.threshold,
                "passed": c.passed,
                "message": c.message,
                "details": c.details,
            }
            for c in report.checks
        ],
        "review_findings": [
            finding.model_dump(mode="json") if hasattr(finding, "model_dump") else finding
            for finding in (review_findings or [])
        ],
        "repair_tickets": [
            ticket.model_dump(mode="json") if hasattr(ticket, "model_dump") else ticket
            for ticket in (repair_tickets or [])
        ],
    }
    if guard_compliance_report is not None:
        payload["guard_compliance"] = guard_compliance_report
    path = layout.quality_gate_report_path(chapter_number)
    storage.save_json(path, payload)
    return payload


def _persist_macro_guard_result(
    storage: Any,
    layout: Any,
    chapter_number: int,
    report: MacroGuardReport,
) -> None:
    """Persist MacroGuard report and write action hints for downstream chapters."""
    report_path = layout.root / "states" / f"macro_guard_report_ch{chapter_number}.json"
    storage.save_json(report_path, report.model_dump(mode="json"))

    if report.recommended_action == "warning_hint":
        hints = {
            "source_chapter": chapter_number,
            "drift_score": report.drift_score,
            "reasoning": report.reasoning,
            "findings": [f.model_dump(mode="json") for f in report.findings],
        }
        next_chapter = chapter_number + 1
        hint_path = layout.root / "states" / f"macro_guard_hint_ch{next_chapter}.json"
        storage.save_json(hint_path, hints)

    elif report.recommended_action in {"alert_plan", "critical_rollback"}:
        alert_payload = {
            "source_chapter": chapter_number,
            "drift_score": report.drift_score,
            "action": report.recommended_action,
            "reasoning": report.reasoning,
            "adjustment_plan": (
                report.adjustment_plan.model_dump(mode="json") if report.adjustment_plan else None
            ),
        }
        alert_path = layout.root / "states" / f"macro_guard_alert_ch{chapter_number}.json"
        storage.save_json(alert_path, alert_payload)
        if report.recommended_action == "critical_rollback":
            marker_payload = {
                **alert_payload,
                "target_chapter": chapter_number + 1,
                "requires_replan": True,
                "marker_type": "macro_guard_critical_rollback",
            }
            marker_path = (
                layout.root / "states" / f"macro_guard_replan_required_ch{chapter_number + 1}.json"
            )
            storage.save_json(marker_path, marker_payload)
