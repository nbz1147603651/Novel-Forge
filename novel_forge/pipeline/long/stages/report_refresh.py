"""Refresh review reports after semantic chapter text changes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.core.schemas.chapter import AlignmentReport, CausalValidationReport
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.core.user_intent import project_user_intent_guard_constraints
from novel_forge.pipeline.long.run_io import ChapterRunIOContext
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_canon_state_hash_for_bundle,
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.stages.causal_repair import (
    recheck_alignment,
    run_causal_validation,
)
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    attach_guard_repair_metadata,
    guard_constraints_for_packet,
)
from novel_forge.pipeline.long.stages.quality_checks_runner import (
    check_guard_constraint_compliance,
)
from novel_forge.pipeline.long.stages.reading_power_repair import evaluate_and_record_reading_power
from novel_forge.pipeline.long.stages.report_freshness import (
    ReportFreshnessDecision,
    ReportRefreshPlanner,
    quality_report_evidence_binding,
    stamp_report_freshness,
)
from novel_forge.pipeline.steps.continuity_eval_step import (
    ContinuityEvalInput,
    ContinuityEvalStep,
)

_logger = logging.getLogger(__name__)

_GUARD_CHECKED_STATUSES = frozenset({"compliant", "partial", "weak", "non_compliant"})


def _summarize_guard_results(
    *,
    constraints: list[str],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rebuild aggregate counters after separating advisory intent results."""

    checked = [
        result
        for result in results
        if not bool(result.get("check_error"))
        and str(result.get("status") or "").strip().lower() in _GUARD_CHECKED_STATUSES
    ]
    compliant_count = sum(
        1 for result in checked if str(result.get("status") or "").lower() == "compliant"
    )
    actionable_count = sum(1 for result in checked if bool(result.get("repairable")))
    unverified_count = sum(
        1
        for result in results
        if not bool(result.get("check_error"))
        and str(result.get("status") or "").strip().lower() == "unknown"
    )
    failed_count = sum(
        1
        for result in results
        if bool(result.get("check_error"))
        or str(result.get("status") or "").strip().lower() == "check_error"
    )
    rate = compliant_count / len(checked) if checked else None
    summary = f"共 {len(constraints)} 条约束，{compliant_count} 条已遵守"
    if rate is not None:
        summary += f"；合规率 {rate:.0%}"
    else:
        summary += "；合规率未计算"
    if actionable_count:
        summary += f"；{actionable_count} 条需要修复"
    if unverified_count:
        summary += f"；{unverified_count} 条无法确认"
    if failed_count:
        summary += f"；{failed_count} 条检查失败"
    return {
        "constraints": constraints,
        "compliance_results": results,
        "overall_compliance_rate": round(rate, 2) if rate is not None else None,
        "checked_count": len(checked),
        "compliant_count": compliant_count,
        "actionable_violation_count": actionable_count,
        "unverified_count": unverified_count,
        "check_failed_count": failed_count,
        "summary": summary,
    }


def _intent_guard_inputs(runner: Any, bundle: Any) -> tuple[str, list[dict[str, Any]]]:
    mode = str(getattr(getattr(runner, "_settings", None), "chapter_intent_guard_mode", "warn"))
    if mode not in {"off", "warn", "block"}:
        mode = "warn"
    if mode == "off":
        return mode, []
    source_slice = getattr(bundle, "chapter_source_slice", None)
    if source_slice is None:
        return mode, []
    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            project_stage_source_cards,
        )

        user_intent = project_stage_source_cards(source_slice, stage="review").get(
            "user_intent", {}
        )
    except Exception as exc:
        _logger.debug("intent_guard_projection_skipped | error=%s", exc)
        return mode, []
    if not isinstance(user_intent, dict):
        return mode, []
    return mode, project_user_intent_guard_constraints(user_intent)


@dataclass(frozen=True)
class RefreshedReviewReports:
    """Reports rebound to the current text after a semantic text change."""

    current_text_hash: str
    alignment_report: Any
    continuity_report: Any
    causal_report: CausalValidationReport | None = None
    reading_power_report: Any | None = None
    chapter_repair_report: Any | None = None
    guard_compliance_report: dict[str, Any] | None = None
    guard_findings: list[ReviewFinding] = field(default_factory=list)
    guard_tickets: list[RepairTicket] = field(default_factory=list)
    stale_reason: str = ""


class ReportRefreshFailurePolicy(str, Enum):
    """Failure semantics for callers with different consistency boundaries."""

    BEST_EFFORT = "best_effort"
    FAIL_CLOSED = "fail_closed"


def _path_exists(path: Any) -> bool:
    exists = getattr(path, "exists", None)
    if callable(exists):
        try:
            return bool(exists())
        except Exception:
            return False
    return False


def _drop_stale_chapter_repair_report(
    report: Any | None,
    *,
    current_hash: str,
) -> Any | None:
    if report is None:
        return None
    stored_hash = str(getattr(report, "source_text_hash", "") or "").strip()
    if stored_hash and stored_hash != current_hash:
        return None
    return report


def _persist_current_report(
    *,
    storage: Any,
    path: Any,
    report: Any,
    current_hash: str,
    context_hash: str,
    evidence_hashes: dict[str, str] | None = None,
    extra_payload: dict[str, Any] | None = None,
    preserve_persisted_keys: tuple[str, ...] = (),
) -> Any:
    """Bind one authoritative result to the reviewed text and persist it."""

    if report is None:
        return None
    if isinstance(report, dict):
        payload = dict(report)
    elif hasattr(report, "model_dump"):
        payload = report.model_dump(mode="json")
    else:
        raise TypeError(f"Unsupported review report type: {type(report).__name__}")
    if path is not None and preserve_persisted_keys:
        try:
            persisted = storage.load_json(path)
        except Exception:
            persisted = None
        if isinstance(persisted, dict):
            for key in preserve_persisted_keys:
                if key in persisted:
                    payload[key] = persisted[key]
    stamp_report_freshness(
        payload,
        current_hash=current_hash,
        context_hash=context_hash,
        evidence_hashes=evidence_hashes,
    )
    if extra_payload:
        payload.update(extra_payload)
    if path is not None:
        storage.save_json(path, payload)
    if isinstance(report, dict):
        return payload
    model_copy = getattr(report, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"source_text_hash": current_hash})
    return report


@dataclass(frozen=True)
class ReviewReportService:
    """Ensure review reports are current for a chapter text/context pair."""

    runner: Any
    bundle: Any
    packet: Any
    bridge: Any
    plan: Any
    chapter_number: int
    trace: Any

    async def ensure_current(
        self,
        *,
        current_text: str,
        alignment_report: Any,
        continuity_report: Any,
        causal_report: CausalValidationReport | None = None,
        chapter_repair_report: Any | None = None,
        reading_power_report: Any | None = None,
        window_manager: Any | None = None,
        window_config: Any | None = None,
        stale_reason: str = "semantic_text_changed",
        continuity_recheck_mode: bool = False,
        continuity_strict_review: bool = False,
        causal_recheck_mode: bool = False,
        causal_strict_review: bool = False,
        force_refresh: bool = False,
        failure_policy: ReportRefreshFailurePolicy = ReportRefreshFailurePolicy.BEST_EFFORT,
        report_kinds: tuple[str, ...] = (
            "alignment",
            "continuity",
            "causal",
            "reading_power",
        ),
        mutation: Any | None = None,
        allow_mutation_narrowing: bool = True,
    ) -> RefreshedReviewReports:
        """Re-run or reuse hard quality reports for the current text.

        Alignment, continuity, causal, and enabled reading-power checks are
        refreshed together by default. ``BEST_EFFORT`` keeps the last report for
        recoverable intermediate stages; ``FAIL_CLOSED`` propagates any failed
        dimension so an archive caller cannot accept stale evidence.

        When *mutation* is provided and ``long_mutation_refresh_mode`` is
        ``"shadow"`` or ``"enforce"``, the mutation contract narrows (or logs)
        the set of dimensions to refresh.  ``allow_mutation_narrowing=False``
        (e.g. at the archive gate) forces a full refresh regardless.
        """

        requested = frozenset(report_kinds)

        # ── Mutation-aware report_kinds (Phase 3 shadow/enforce) ────────────
        mutation_mode = str(
            getattr(
                getattr(self.runner, "_settings", None),
                "long_mutation_refresh_mode",
                "off",
            )
            or "off"
        )
        if (
            mutation is not None
            and mutation_mode in ("shadow", "enforce")
            and allow_mutation_narrowing
        ):
            from novel_forge.pipeline.long.stages.text_mutation import (
                TextMutation,
                mutation_report_kinds,
            )

            mutations = mutation if isinstance(mutation, list) else [mutation]
            _typed = [m for m in mutations if isinstance(m, TextMutation)]
            if _typed:
                mutation_kinds = frozenset(
                    mutation_report_kinds(_typed, allow_narrowing=True)
                )
                if mutation_mode == "shadow":
                    # Log suggested narrowing but keep original requested set.
                    _logger.info(
                        "mutation_refresh_shadow | chapter=%d | original=%s | suggested=%s",
                        self.chapter_number,
                        sorted(requested),
                        sorted(mutation_kinds & requested),
                    )
                    on_step = getattr(self.runner, "_on_step", None)
                    if callable(on_step):
                        on_step(
                            "mutation_refresh_shadow",
                            {
                                "chapter": self.chapter_number,
                                "original_kinds": sorted(requested),
                                "suggested_kinds": sorted(mutation_kinds & requested),
                                "mutation_sources": [
                                    m.kind.value for m in _typed
                                ],
                            },
                        )
                else:
                    # enforce: narrow to mutation-derived subset of original.
                    requested = mutation_kinds & requested

        return await _refresh_quality_reports_after_semantic_text_change_impl(
            runner=self.runner,
            bundle=self.bundle,
            packet=self.packet,
            bridge=self.bridge,
            plan=self.plan,
            current_text=current_text,
            chapter_number=self.chapter_number,
            trace=self.trace,
            alignment_report=alignment_report,
            continuity_report=continuity_report,
            causal_report=causal_report,
            chapter_repair_report=chapter_repair_report,
            reading_power_report=reading_power_report,
            window_manager=window_manager,
            window_config=window_config,
            stale_reason=stale_reason,
            continuity_recheck_mode=continuity_recheck_mode,
            continuity_strict_review=continuity_strict_review,
            causal_recheck_mode=causal_recheck_mode,
            causal_strict_review=causal_strict_review,
            force_refresh=force_refresh,
            failure_policy=failure_policy,
            report_kinds=requested,
        )


async def _refresh_quality_reports_after_semantic_text_change_impl(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    alignment_report: Any,
    continuity_report: Any,
    causal_report: CausalValidationReport | None = None,
    chapter_repair_report: Any | None = None,
    reading_power_report: Any | None = None,
    window_manager: Any | None = None,
    window_config: Any | None = None,
    stale_reason: str = "semantic_text_changed",
    continuity_recheck_mode: bool = False,
    continuity_strict_review: bool = False,
    causal_recheck_mode: bool = False,
    causal_strict_review: bool = False,
    force_refresh: bool = False,
    failure_policy: ReportRefreshFailurePolicy = ReportRefreshFailurePolicy.BEST_EFFORT,
    report_kinds: frozenset[str] = frozenset(
        ("alignment", "continuity", "causal", "reading_power")
    ),
) -> RefreshedReviewReports:
    """Implementation behind :class:`ReviewReportService` and the legacy function."""

    current_hash = source_text_hash(current_text)
    fail_closed = failure_policy is ReportRefreshFailurePolicy.FAIL_CLOSED
    evidence_binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )
    context_hash = str(evidence_binding["context_hash"])
    evidence_hashes = dict(evidence_binding["evidence_hashes"])
    io_context = ChapterRunIOContext(
        storage=runner._storage,
        layout=bundle.layout,
        project_id=str(getattr(bundle, "project_id", "") or ""),
        chapter_number=chapter_number,
        source="report_refresh",
    )
    on_step = getattr(runner, "_on_step", None)
    if callable(on_step):
        on_step(
            "quality_reports_refresh_after_text_change",
            {
                "chapter": chapter_number,
                "reason": stale_reason,
                "source_text_hash": current_hash,
                "report_context_hash": context_hash,
            },
        )

    planner = ReportRefreshPlanner(
        storage=runner._storage,
        current_text_hash=current_hash,
        context_hash=context_hash,
        artifact_loader=io_context,
    )

    def _forced_decision(dimension: str) -> ReportFreshnessDecision:
        return ReportFreshnessDecision(
            dimension=dimension,
            action="refresh",
            reason=stale_reason or "forced_refresh",
            report=None,
            source="forced",
            source_text_hash=current_hash,
            context_hash=context_hash,
        )

    alignment_decision = (
        _forced_decision("alignment")
        if force_refresh and "alignment" in report_kinds
        else planner.decide(
            dimension="alignment",
            provided=alignment_report,
            path=bundle.layout.alignment_report_path(chapter_number),
            model=AlignmentReport,
        )
        if "alignment" in report_kinds
        else None
    )
    continuity_decision = (
        _forced_decision("continuity")
        if force_refresh and "continuity" in report_kinds
        else planner.decide(
            dimension="continuity",
            provided=continuity_report,
            path=bundle.layout.continuity_report_path(chapter_number),
            model=ContinuityReport,
        )
        if "continuity" in report_kinds
        else None
    )
    causal_path = getattr(bundle.layout, "chapter_causal_report_path", lambda _ch: None)(
        chapter_number
    )
    should_refresh_causal = "causal" in report_kinds and (
        force_refresh or causal_report is not None or _path_exists(causal_path)
    )
    causal_decision = (
        _forced_decision("causal")
        if force_refresh and should_refresh_causal
        else planner.decide(
            dimension="causal",
            provided=causal_report,
            path=causal_path,
            model=CausalValidationReport,
        )
        if should_refresh_causal
        else None
    )

    def _emit_decision(decision: ReportFreshnessDecision | None) -> None:
        if decision is None or not callable(on_step):
            return
        event = (
            "quality_report_reused" if decision.should_reuse else "quality_report_refresh_required"
        )
        on_step(
            event,
            {
                "dimension": decision.dimension,
                "reason": decision.reason,
                "source": decision.source,
                "source_text_hash": decision.source_text_hash,
                "report_context_hash": decision.context_hash,
            },
        )

    for decision in (alignment_decision, continuity_decision, causal_decision):
        _emit_decision(decision)

    async def _refresh_alignment() -> Any:
        if alignment_decision is None:
            return alignment_report
        if alignment_decision.should_reuse:
            return _persist_current_report(
                storage=runner._storage,
                path=bundle.layout.alignment_report_path(chapter_number),
                report=alignment_decision.report,
                current_hash=current_hash,
                context_hash=context_hash,
                evidence_hashes=evidence_hashes,
            )
        try:
            report = await recheck_alignment(
                runner,
                bundle,
                packet,
                plan,
                current_text,
                chapter_number,
                trace,
            )
        except Exception as exc:  # noqa: BLE001
            if callable(on_step):
                on_step(
                    (
                        "alignment_refresh_failed_blocking"
                        if fail_closed
                        else "alignment_refresh_failed_fallback"
                    ),
                    {
                        "chapter": chapter_number,
                        "reason": stale_reason,
                        "error": str(exc),
                        "action": "block_archive" if fail_closed else "reuse_previous_report",
                    },
                )
            if fail_closed:
                raise
            return alignment_report
        return _persist_current_report(
            storage=runner._storage,
            path=bundle.layout.alignment_report_path(chapter_number),
            report=report,
            current_hash=current_hash,
            context_hash=context_hash,
            evidence_hashes=evidence_hashes,
        )

    async def _refresh_continuity() -> Any:
        if continuity_decision is None:
            return continuity_report
        if continuity_decision.should_reuse:
            return _persist_current_report(
                storage=runner._storage,
                path=bundle.layout.continuity_report_path(chapter_number),
                report=continuity_decision.report,
                current_hash=current_hash,
                context_hash=context_hash,
                evidence_hashes=evidence_hashes,
            )
        try:
            continuity_step = ContinuityEvalStep(
                runner._router,
                runner._builder,
                settings=runner._settings,
                trace=trace,
            )
            kernel_composer = await load_story_kernel_composer(runner, bundle)
            continuity_kernel_context = (
                kernel_composer.compose_continuity_eval_input(chapter_number, current_text)
                if kernel_composer is not None
                else {}
            )
            report = await continuity_step.run(
                ContinuityEvalInput(
                    chapter_number=chapter_number,
                    chapter_text=current_text,
                    chapter_state_packet=packet,
                    chapter_bridge=bridge,
                    chapter_plan=plan,
                    recheck_mode=continuity_recheck_mode,
                    strict_review=continuity_strict_review,
                    pov_switch=getattr(
                        getattr(bundle, "chapter_outline", None), "pov_switch", False
                    ),
                    project_path=getattr(getattr(bundle, "layout", None), "root", None),
                    kernel_context=continuity_kernel_context,
                    chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
                )
            )
        except Exception as exc:  # noqa: BLE001
            if callable(on_step):
                on_step(
                    (
                        "continuity_refresh_failed_blocking"
                        if fail_closed
                        else "continuity_refresh_failed_fallback"
                    ),
                    {
                        "chapter": chapter_number,
                        "reason": stale_reason,
                        "error": str(exc),
                        "action": "block_archive" if fail_closed else "reuse_previous_report",
                    },
                )
            if fail_closed:
                raise
            return continuity_report
        report = _persist_current_report(
            storage=runner._storage,
            path=bundle.layout.continuity_report_path(chapter_number),
            report=report,
            current_hash=current_hash,
            context_hash=context_hash,
            evidence_hashes=evidence_hashes,
            extra_payload={
                "canon_state_hash": await load_canon_state_hash_for_bundle(runner, bundle)
            },
        )
        if callable(on_step):
            on_step("continuity_eval_after_text_change", report)
        return report

    async def _refresh_causal() -> CausalValidationReport | None:
        if not should_refresh_causal:
            return causal_report
        if causal_decision is not None and causal_decision.should_reuse:
            return cast(
                CausalValidationReport,
                _persist_current_report(
                    storage=runner._storage,
                    path=causal_path,
                    report=causal_decision.report,
                    current_hash=current_hash,
                    context_hash=context_hash,
                    evidence_hashes=evidence_hashes,
                ),
            )
        try:
            refreshed = await run_causal_validation(
                runner,
                bundle,
                bridge,
                current_text,
                chapter_number,
                trace,
                previous_chapter_ending=getattr(packet, "previous_chapter_ending", "") or "",
                recheck_mode=causal_recheck_mode,
                strict_review=causal_strict_review,
            )
        except Exception as exc:  # noqa: BLE001
            if callable(on_step):
                on_step(
                    (
                        "causal_refresh_failed_blocking"
                        if fail_closed
                        else "causal_refresh_failed_fallback"
                    ),
                    {
                        "chapter": chapter_number,
                        "reason": stale_reason,
                        "error": str(exc),
                        "action": "block_archive" if fail_closed else "reuse_previous_report",
                    },
                )
            if fail_closed:
                raise
            return causal_report
        return cast(
            CausalValidationReport,
            _persist_current_report(
                storage=runner._storage,
                path=causal_path,
                report=refreshed,
                current_hash=current_hash,
                context_hash=context_hash,
                evidence_hashes=evidence_hashes,
            ),
        )

    _gather_results = await asyncio.gather(
        _refresh_alignment(),
        _refresh_continuity(),
        _refresh_causal(),
        return_exceptions=True,
    )

    if fail_closed:
        refresh_errors = [
            result for result in _gather_results if isinstance(result, BaseException)
        ]
        if refresh_errors:
            gateway_error = next(
                (error for error in refresh_errors if isinstance(error, ModelGatewayError)),
                None,
            )
            raise gateway_error or refresh_errors[0]

    def _safe_gather_result(idx: int, fallback: Any, dimension: str) -> Any:
        result = _gather_results[idx]
        if isinstance(result, BaseException):
            if callable(on_step):
                on_step(
                    f"{dimension}_refresh_gather_exception",
                    {"chapter": chapter_number, "error": str(result)},
                )
            return fallback
        return result

    refreshed_alignment = _safe_gather_result(0, alignment_report, "alignment")
    refreshed_continuity = _safe_gather_result(1, continuity_report, "continuity")
    refreshed_causal = _safe_gather_result(2, causal_report, "causal")

    refreshed_reading_power = reading_power_report
    if "reading_power" not in report_kinds:
        refreshed_reading_power = reading_power_report
    else:
        reading_power_path = getattr(bundle.layout, "reading_power_report_path", lambda _ch: None)(
            chapter_number
        )
        reading_power_decision = (
            _forced_decision("reading_power")
            if force_refresh
            else planner.decide(
                dimension="reading_power",
                provided=reading_power_report,
                path=reading_power_path,
                model=ReadingPowerReport,
            )
        )
        if reading_power_decision.should_reuse:
            _emit_decision(reading_power_decision)
            refreshed_reading_power = _persist_current_report(
                storage=runner._storage,
                path=reading_power_path,
                report=reading_power_decision.report,
                current_hash=current_hash,
                context_hash=context_hash,
                evidence_hashes=evidence_hashes,
                preserve_persisted_keys=("pipeline_stage",),
            )
        elif (
            force_refresh
            or reading_power_report is not None
            or window_manager is not None
            or _path_exists(reading_power_path)
        ):
            _emit_decision(reading_power_decision)
            try:
                refreshed_reading_power = await evaluate_and_record_reading_power(
                    runner=runner,
                    bundle=bundle,
                    packet=packet,
                    bridge=bridge,
                    plan=plan,
                    current_text=current_text,
                    chapter_number=chapter_number,
                    trace=trace,
                    window_manager=window_manager,
                    pipeline_stage="final_review_text",
                    step_name="reading_power_after_text_change",
                    update_window=False,
                )
                refreshed_reading_power = _persist_current_report(
                    storage=runner._storage,
                    path=reading_power_path,
                    report=refreshed_reading_power,
                    current_hash=current_hash,
                    context_hash=context_hash,
                    evidence_hashes=evidence_hashes,
                    preserve_persisted_keys=("pipeline_stage",),
                )
            except Exception as exc:  # noqa: BLE001
                if callable(on_step):
                    on_step(
                        (
                            "reading_power_refresh_after_text_change_failed_blocking"
                            if fail_closed
                            else "reading_power_refresh_after_text_change_failed"
                        ),
                        {
                            "chapter": chapter_number,
                            "reason": stale_reason,
                            "error": str(exc),
                            "action": "block_archive" if fail_closed else "omit_refresh",
                        },
                    )
                if fail_closed:
                    raise

    return RefreshedReviewReports(
        current_text_hash=current_hash,
        alignment_report=refreshed_alignment,
        continuity_report=refreshed_continuity,
        causal_report=refreshed_causal,
        reading_power_report=refreshed_reading_power,
        chapter_repair_report=_drop_stale_chapter_repair_report(
            chapter_repair_report,
            current_hash=current_hash,
        ),
        stale_reason=stale_reason,
    )


async def refresh_quality_reports_after_semantic_text_change(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: Any,
    alignment_report: Any,
    continuity_report: Any,
    causal_report: CausalValidationReport | None = None,
    chapter_repair_report: Any | None = None,
    reading_power_report: Any | None = None,
    window_manager: Any | None = None,
    window_config: Any | None = None,
    stale_reason: str = "semantic_text_changed",
    continuity_recheck_mode: bool = False,
    continuity_strict_review: bool = False,
    causal_recheck_mode: bool = False,
    causal_strict_review: bool = False,
    force_refresh: bool = False,
    failure_policy: ReportRefreshFailurePolicy = ReportRefreshFailurePolicy.BEST_EFFORT,
    report_kinds: tuple[str, ...] = (
        "alignment",
        "continuity",
        "causal",
        "reading_power",
    ),
    mutation: Any | None = None,
    allow_mutation_narrowing: bool = True,
) -> RefreshedReviewReports:
    """Functional entry point for the unified review report service."""

    service = ReviewReportService(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=chapter_number,
        trace=trace,
    )
    return await service.ensure_current(
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
        chapter_repair_report=chapter_repair_report,
        reading_power_report=reading_power_report,
        window_manager=window_manager,
        window_config=window_config,
        stale_reason=stale_reason,
        continuity_recheck_mode=continuity_recheck_mode,
        continuity_strict_review=continuity_strict_review,
        causal_recheck_mode=causal_recheck_mode,
        causal_strict_review=causal_strict_review,
        force_refresh=force_refresh,
        failure_policy=failure_policy,
        report_kinds=report_kinds,
        mutation=mutation,
        allow_mutation_narrowing=allow_mutation_narrowing,
    )


async def run_guard_compliance_for_final_text(
    *,
    runner: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    storage: Any | None = None,
) -> RefreshedReviewReports:
    """Run guard compliance against the final text for this stage."""

    current_hash = source_text_hash(current_text)
    base_constraints = guard_constraints_for_packet(packet)
    intent_mode, intent_inputs = _intent_guard_inputs(runner, bundle)
    intent_constraints = [item["constraint"] for item in intent_inputs]
    if not base_constraints and not intent_constraints:
        return RefreshedReviewReports(
            current_text_hash=current_hash,
            alignment_report=None,
            continuity_report=None,
        )

    report = await check_guard_constraint_compliance(
        runner=runner,
        bundle=bundle,
        packet=packet,
        current_text=current_text,
        chapter_number=chapter_number,
        constraints=[*base_constraints, *intent_constraints],
    )
    intent_by_constraint = {item["constraint"]: item for item in intent_inputs}
    base_results: list[dict[str, Any]] = []
    intent_results: list[dict[str, Any]] = []
    for raw_result in report.get("compliance_results", []):
        if not isinstance(raw_result, dict):
            continue
        result = dict(raw_result)
        intent_input = intent_by_constraint.get(str(result.get("constraint") or ""))
        if intent_input is None:
            result["constraint_origin"] = "chapter_guard"
            base_results.append(result)
            continue
        result.update(
            {
                "constraint_origin": "user_intent",
                "intent_id": intent_input["intent_id"],
                "intent_field": intent_input["field"],
                "intent_ids": list(intent_input.get("intent_ids") or []),
                "intent_fields": list(intent_input.get("fields") or []),
            }
        )
        intent_results.append(result)

    intent_report = _summarize_guard_results(
        constraints=intent_constraints,
        results=intent_results,
    )
    verified_intent_conflicts = [
        result for result in intent_results if bool(result.get("repairable"))
    ]
    if intent_mode == "warn":
        report = _summarize_guard_results(
            constraints=base_constraints,
            results=base_results,
        )
    else:
        report = _summarize_guard_results(
            constraints=[*base_constraints, *intent_constraints],
            results=[*base_results, *intent_results],
        )
    report["intent_guard_mode"] = intent_mode
    report["intent_guard"] = intent_report
    report["intent_conflicts"] = [
        {
            "intent_id": str(result.get("intent_id") or ""),
            "field": str(result.get("intent_field") or ""),
            "intent_ids": list(result.get("intent_ids") or []),
            "fields": list(result.get("intent_fields") or []),
            "evidence": str(result.get("evidence") or ""),
            "status": str(result.get("status") or ""),
        }
        for result in verified_intent_conflicts
    ]
    report["source_text_hash"] = current_hash
    report["_text_hash"] = current_hash
    findings, tickets = attach_guard_repair_metadata(
        report,
        chapter_number=chapter_number,
        current_text=current_text,
    )

    target_storage = storage if storage is not None else getattr(runner, "_storage", None)
    guard_path_fn = getattr(getattr(bundle, "layout", None), "guard_report_path", None)
    if target_storage is not None and callable(guard_path_fn):
        try:
            target_storage.save_json(guard_path_fn(chapter_number), report)
        except Exception:
            pass

    on_step = getattr(runner, "_on_step", None)
    if callable(on_step):
        on_step(
            "guard_constraint_compliance_check",
            {
                "chapter": chapter_number,
                "compliance_rate": report.get("overall_compliance_rate"),
                "summary": report.get("summary", ""),
                "details": report.get("compliance_results", []),
                "findings": [f.model_dump(mode="json") for f in findings],
                "repair_tickets": [t.model_dump(mode="json") for t in tickets],
                "intent_guard_mode": intent_mode,
                "intent_checked_count": intent_report["checked_count"],
                "intent_conflicts": report["intent_conflicts"],
                "source_text_hash": current_hash,
            },
        )

    return RefreshedReviewReports(
        current_text_hash=current_hash,
        alignment_report=None,
        continuity_report=None,
        guard_compliance_report=report,
        guard_findings=findings,
        guard_tickets=tickets,
    )


__all__ = [
    "RefreshedReviewReports",
    "ReportRefreshFailurePolicy",
    "ReviewReportService",
    "refresh_quality_reports_after_semantic_text_change",
    "run_guard_compliance_for_final_text",
]
