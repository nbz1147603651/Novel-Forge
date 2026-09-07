"""Plan and guard checkpoint handlers for chapter studio sessions."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

from novel_forge.common.plot_guard import (
    _apply_outline_adjustment,
    _apply_plot_guard_to_creative_report,
    _run_plot_guard_judge,
    record_plot_guard_handoff,
)
from novel_forge.core.exceptions import (
    ARCHIVE_QUALITY_BLOCK_KINDS,
    BLOCK_KIND_ALIGNMENT_QUALITY,
    BLOCK_KIND_STATE_ADJUDICATION,
    ConsistencyViolationError,
    classify_archive_quality_block,
)
from novel_forge.core.repair_attempt_guidance import build_repair_attempt_guidance
from novel_forge.core.review.review_contracts import build_ticket_verification_results
from novel_forge.core.review.review_orchestration import alignment_requires_repair
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
    ChapterRepairReport,
    PlotGuardDecision,
)
from novel_forge.core.schemas.continuity import ChapterBridge, ContinuityIssue, ContinuityReport
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.core.utils import normalize_threshold
from novel_forge.core.utils.macro_guard_helpers import record_macro_guard_adjustment
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.chapter_flow import (
    finalize_chapter_result,
    is_carry_forward_block_exception,
    load_prepared_chapter_artifacts,
    prepare_chapter_plan,
    review_chapter_draft,
)
from novel_forge.pipeline.long.context import StoryMemoryManager
from novel_forge.pipeline.long.execution_models import ChapterReviewArtifacts
from novel_forge.pipeline.long.loop import compensate_memory_gap
from novel_forge.pipeline.long.preflight import (
    LongProjectBundle,
    close_long_project,
    prepare_long_project,
)
from novel_forge.pipeline.long.services.contract_execution_repair import (
    compile_contract_audit_repair_ticket,
    has_contract_audit_repair_ticket,
    is_contract_audit_block_exception,
)
from novel_forge.pipeline.long.services.init.init_service import (
    ensure_init_readiness_for_existing_project,
)
from novel_forge.pipeline.long.services.memory_finalization import (
    finalize_chapter_memory_phase,
)
from novel_forge.pipeline.long.stages.character_intro import (
    auto_register_from_creative_report,
    enrich_introduced_characters,
    introduce_new_characters,
)
from novel_forge.pipeline.long.stages.dedup_pronoun import (
    run_final_dedup,
    run_pronoun_check,
    run_self_repetition_check,
)
from novel_forge.pipeline.long.stages.finalize_checks import _sync_or_mark_report_hashes
from novel_forge.pipeline.long.stages.finalize_report import extract_and_validate
from novel_forge.pipeline.long.stages.quality_checks_lib import (
    attach_guard_repair_metadata,
    guard_report_has_actionable_low_compliance,
    guard_report_incomplete_warning,
)
from novel_forge.pipeline.long.stages.quality_checks_runner import (
    check_guard_constraint_compliance,
)
from novel_forge.pipeline.long.stages.word_count import (
    clear_word_count_rejections,
    record_word_count_rejection,
    run_word_count_restructure,
    word_count_archive_gate_enabled,
    word_count_archive_gate_max_rejections,
    word_count_rejection_limit_reached,
)
from novel_forge.pipeline.repair_orchestration.domains.prompt_leak import (
    run_prompt_leak_repair_v2 as repair_confirmed_prompt_leaks_with_patch,
)
from novel_forge.workspace.chapter_run_io import ChapterRunIOContext
from novel_forge.workspace.contracts import (
    ChapterSessionResult,
    DecisionCheckpoint,
    PrepareChapterRequest,
    PrepareChapterResponse,
    ResolveChapterCheckpointRequest,
)
from novel_forge.workspace.memory_warmup import warm_start_first_chapter_memory
from novel_forge.workspace.repair_review_verification import (
    build_causal_recheck_payload,
    build_continuity_recheck_payload,
    run_alignment_check,
    run_causal_recheck,
    run_continuity_recheck,
    run_targeted_continuity_recheck,
)
from novel_forge.workspace.repair_review_verification import (
    run_alignment_recheck_and_save as _run_alignment_recheck_and_save,
)
from novel_forge.workspace.rewrite_strategy import (
    build_rewrite_strategy_plan,
    notes_with_rewrite_strategy,
    rewrite_context_path,
)
from novel_forge.workspace.runtime import RuntimeServices
from novel_forge.workspace.sessions.chapter_session_report_refresh import (
    refresh_pending_reports_for_text,
)
from novel_forge.workspace.sessions.chapter_session_results import (
    build_completed_session_result,
    build_paused_session_result,
    build_prepare_response,
    build_session_checkpoint_result,
    build_session_result_from_prepare,
)
from novel_forge.workspace.sessions.chapter_session_state import (
    GuardCheckpointSessionState,
    PendingChapterReviewState,
    PlanCheckpointSessionState,
    apply_notes_to_bundle,
    build_guard_checkpoint,
    build_plan_checkpoint,
    clear_session_state,
    deserialize_pending_result,
    load_creative_report_payload,
    load_review_progress,
    save_guard_session_state,
    save_plan_session_state,
    summarize_exit_state,
)

_logger = logging.getLogger(__name__)
_GUARD_WARNING_PREFIX = "AI护栏约束合规率过低:"
_GUARD_INCOMPLETE_WARNING_PREFIX = "AI护栏约束检查未完成:"


async def recheck_alignment(
    runner: Any,
    bundle: LongProjectBundle,
    packet: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    trace: PipelineTrace,
) -> Any:
    """Compatibility patch point for session alignment rechecks."""

    return await _run_alignment_recheck_and_save(
        runner=runner,
        bundle=bundle,
        packet=packet,
        plan=plan,
        current_text=current_text,
        chapter_number=chapter_number,
        trace=trace,
    )


_DEFAULT_RECHECK_ALIGNMENT = recheck_alignment


def _alignment_recheck_callback_if_overridden() -> Any | None:
    if recheck_alignment is _DEFAULT_RECHECK_ALIGNMENT:
        return None
    return recheck_alignment


async def alignment_repair_edit(
    runner: Any,
    *,
    bundle: LongProjectBundle,
    packet: Any,
    bridge: Any,
    plan: Any,
    current_text: str,
    chapter_number: int,
    alignment_report: Any,
    trace: PipelineTrace,
) -> str:
    """Compatibility patch point for targeted alignment repair edits."""

    from novel_forge.pipeline.long.stages.causal_repair import (
        alignment_repair_edit as _alignment_repair_edit,
    )

    return await _alignment_repair_edit(
        runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        current_text=current_text,
        chapter_number=chapter_number,
        alignment_report=alignment_report,
        trace=trace,
    )


def _chapter_session_artifacts(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    *,
    source: str,
) -> ChapterRunIOContext | None:
    storage = getattr(runtime, "storage", None)
    if storage is None:
        return None
    return ChapterRunIOContext(
        storage=storage,
        layout=ProjectLayout(storage.existing_project_dir(request.project_id)),
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        source=source,
    )


def _is_state_adjudication_block_exception(exc: BaseException) -> bool:
    """Return whether *exc* is a narrative-state-adjudication archive block.

    Mirrors :func:`is_contract_audit_block_exception` but matches the
    "LLM 状态裁判要求阻断归档" message produced by
    :func:`_adjudicate_state_before_archive` when the final adjudication
    still requests a block after the repair loop is exhausted.
    """
    if getattr(exc, "block_kind", "") == BLOCK_KIND_STATE_ADJUDICATION:
        return True
    messages = list(getattr(exc, "violations", []) or [])
    if not messages:
        messages = [str(exc)]
    return any("状态裁判" in str(message) and "阻断归档" in str(message) for message in messages)


def _extract_state_adjudication_block_summary(exc: BaseException) -> str:
    """Extract a human-readable summary from a state-adjudication block exception."""
    messages = list(getattr(exc, "violations", []) or [])
    if not messages:
        return str(exc)[:500]
    raw = str(messages[0])
    prefix = "LLM 状态裁判要求阻断归档："
    if raw.startswith(prefix):
        raw = raw[len(prefix) :]
    return raw.strip()[:500]


def _is_archive_quality_retry_block_exception(exc: BaseException) -> bool:
    """Return whether a verified archive-quality block can enter repair.

    New hard gates carry an explicit ``block_kind``.  The message fallback is
    deliberately retained for checkpoints created by versions that emitted
    the old unclassified ``ConsistencyViolationError``; otherwise the first
    historical low-alignment failure after an upgrade would still escape as a
    failed task.
    """
    if getattr(exc, "block_kind", "") in ARCHIVE_QUALITY_BLOCK_KINDS:
        return True
    messages = list(getattr(exc, "violations", []) or [])
    if not messages:
        messages = [str(exc)]
    return classify_archive_quality_block([str(message) for message in messages]) in (
        ARCHIVE_QUALITY_BLOCK_KINDS
    )


def _is_archive_eval_quality_block_exception(exc: BaseException) -> bool:
    """Compatibility alias for callers/tests predating typed archive routes."""
    return _is_archive_quality_retry_block_exception(exc)


def _is_carry_forward_retry_block_exception(exc: BaseException) -> bool:
    return is_carry_forward_block_exception(exc)


def _extract_archive_quality_block_summary(exc: BaseException) -> str:
    messages = list(getattr(exc, "violations", []) or [])
    if not messages:
        return str(exc)[:500]
    return str(messages[0]).strip()[:500]


def _archive_quality_auto_repair_enabled(
    *,
    request: ResolveChapterCheckpointRequest,
    settings: Any,
) -> bool:
    """Whether this continuation explicitly permits autonomous repair."""
    configured = request.repair_control_mode
    mode = (
        configured
        if configured is not None
        else getattr(settings, "repair_control_mode", "ai_assisted")
    )
    mode_value = getattr(mode, "value", mode)
    return str(mode_value or "").strip().lower() == "ai_auto"


def _archive_quality_auto_repair_limit(settings: Any) -> int:
    try:
        return max(1, int(getattr(settings, "max_auto_repair_attempts", 2) or 2))
    except (TypeError, ValueError):
        return 2


def _open_issue_count(issues: Any) -> int:
    if not isinstance(issues, list):
        return 0
    count = 0
    for issue in issues:
        if isinstance(issue, dict):
            status = issue.get("status", "open")
        else:
            status = getattr(issue, "status", "open")
        if str(status or "open").strip().lower() == "open":
            count += 1
    return count


def _build_guard_checkpoint_result(
    *,
    request: ResolveChapterCheckpointRequest,
    checkpoint: DecisionCheckpoint,
    pending: PendingChapterReviewState,
    text: str,
    bridge_summary: str,
    metadata: dict[str, Any],
) -> ChapterSessionResult:
    """Build a checkpoint result with standard fields extracted from pending state.

    This helper eliminates duplication across the multiple guard checkpoint
    result construction sites. All sites share the same field extraction logic
    but differ in metadata and bridge_summary.
    """
    return build_session_checkpoint_result(
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        checkpoint=checkpoint,
        applied_option_id=request.option_id,
        word_count=display_word_count(text),
        overall_score=pending.eval_report.overall_score,
        continuity_score=pending.continuity_report.continuity_score,
        continuity_issue_count=_open_issue_count(pending.continuity_report.issues),
        bridge_summary=bridge_summary,
        chapter_exit_summary=summarize_exit_state(pending.outcome),
        preview_text=text,
        guard_decision=pending.guard_decision,
        metadata=metadata,
    )


def _guard_checkpoint_metadata(
    pending: PendingChapterReviewState,
    block_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(block_metadata or {})
    metadata.update(
        {
            "alignment_score": pending.alignment_report.alignment_score,
            "causal_score": (
                pending.causal_report.causal_score if pending.causal_report is not None else None
            ),
            "causal_issue_count": (
                len(pending.causal_report.issues) if pending.causal_report is not None else 0
            ),
            "warnings": list(pending.warnings or ()),
        }
    )
    return metadata


def _persist_guard_checkpoint_and_build_result(
    *,
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    bundle: LongProjectBundle,
    session_state: GuardCheckpointSessionState,
    guard_checkpoint: DecisionCheckpoint,
    pending_state: PendingChapterReviewState,
    text: str,
    bridge_summary: str,
    metadata: dict[str, Any],
    on_step: Any | None = None,
) -> ChapterSessionResult:
    # Persist the decision context on the checkpoint itself so the auto-pilot
    # and the UI can read why an option is recommended (e.g. archive-gate retry
    # state) directly from the loaded DecisionCheckpoint.
    guard_checkpoint.metadata = dict(metadata)
    runtime.storage.save_json(
        bundle.layout.chapter_checkpoint_path(request.chapter_number),
        guard_checkpoint.model_dump(mode="json"),
    )
    save_guard_session_state(
        storage=runtime.storage,
        bundle=bundle,
        chapter_number=request.chapter_number,
        checkpoint_id=guard_checkpoint.checkpoint_id,
        project_id=request.project_id,
        canon_watermark=bundle.canon_state.current_chapter,
        notes=request.notes,
        rewrite_strategy=session_state.rewrite_strategy,
        writing_mode=session_state.writing_mode,
        introduced_characters=session_state.introduced_characters,
        replan_history=session_state.replan_history,
        pending_state=pending_state,
    )
    if callable(on_step):
        on_step("guard_checkpoint", guard_checkpoint.model_dump(mode="json"))
    return _build_guard_checkpoint_result(
        request=request,
        checkpoint=guard_checkpoint,
        pending=pending_state,
        text=text,
        bridge_summary=bridge_summary,
        metadata=metadata,
    )


def _build_block_checkpoint_result(
    *,
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    bundle: LongProjectBundle,
    session_state: GuardCheckpointSessionState,
    pending: PendingChapterReviewState,
    finalize_text: str,
    ch_bridge: Any,
    block_key: str,
    block_metadata: dict[str, Any],
    prompt_text: str,
    recommended_option_id: str,
    option_descriptions: dict[str, str],
    on_step: Any | None = None,
) -> ChapterSessionResult:
    guard_checkpoint = build_guard_checkpoint(
        bundle,
        request.chapter_number,
        current_text=finalize_text,
        alignment_report=pending.alignment_report,
        continuity_report=pending.continuity_report,
        eval_report=pending.eval_report,
        causal_report=pending.causal_report,
        guard_decision=pending.guard_decision,
        reading_power_report=pending.reading_power_report,
        chapter_repair_report=pending.chapter_repair_report,
        warnings=pending.warnings,
        repair_tickets=pending.repair_tickets,
    )
    guard_checkpoint.prompt = prompt_text
    for option in guard_checkpoint.options:
        option.is_recommended = option.option_id == recommended_option_id
        if option.option_id in option_descriptions:
            option.description = option_descriptions[option.option_id]
    metadata = _guard_checkpoint_metadata(
        pending,
        {
            block_key: True,
            **block_metadata,
        },
    )
    return _persist_guard_checkpoint_and_build_result(
        runtime=runtime,
        request=request,
        bundle=bundle,
        session_state=session_state,
        guard_checkpoint=guard_checkpoint,
        pending_state=pending,
        text=finalize_text,
        bridge_summary=getattr(ch_bridge, "bridge_summary", "") or "",
        metadata=metadata,
        on_step=on_step,
    )


def _load_chapter_bridge_summary(
    *,
    runtime: RuntimeServices,
    bundle: LongProjectBundle,
    chapter_number: int,
) -> str:
    try:
        bridge_payload = runtime.storage.load_json(
            bundle.layout.chapter_bridge_path(chapter_number)
        )
        ch_bridge = ChapterBridge.model_validate(bridge_payload)
        return getattr(ch_bridge, "bridge_summary", "") or ""
    except Exception as exc:
        _logger.debug("Failed to load chapter bridge summary for checkpoint: %s", exc)
        return ""


def _is_reading_power_stale(
    rp_data: dict[str, Any] | None,
    current_text: str | None,
) -> tuple[bool, str | None, str | None]:
    """P0-C mirror of finalize.py:_is_reading_power_stale: a reading_power
    report whose source_text_hash does not match the current chapter text
    is from a prior cancelled run and must not be trusted as the current
    evaluation. Returns ``(is_stale, stored_hash, current_hash)``.
    """
    if not rp_data or not current_text:
        return False, None, None
    stored_hash = rp_data.get("source_text_hash")
    if not stored_hash:
        return False, None, None
    current_hash = source_text_hash(current_text)
    if stored_hash == current_hash:
        return False, stored_hash, current_hash
    return True, stored_hash, current_hash


def _backfill_reading_power_score(
    *,
    storage: Any,
    layout: Any,
    chapter_number: int,
    current_text: str,
) -> tuple[float | None, str]:
    """P0-C: when checkpoint-resume skipped the quality stage
    (so ``result.reading_power_report is None``), the report file from a
    prior evaluate run is still on disk. Read it, validate via the
    same source_text_hash check used in finalize.py:2227, and return
    ``(overall_score, summary)``. Returns ``(None, "")`` if no usable
    report exists.
    """
    try:
        report_path = layout.reading_power_report_path(chapter_number)
    except Exception:
        return None, ""
    if not (hasattr(storage, "exists") and storage.exists(report_path)):
        return None, ""
    try:
        rp_data = storage.load_json(report_path)
    except Exception:
        return None, ""
    if not isinstance(rp_data, dict):
        return None, ""
    is_stale, _stored, _current = _is_reading_power_stale(rp_data, current_text)
    if is_stale:
        return None, ""
    raw_score = rp_data.get("overall_score")
    try:
        score: float | None = float(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        score = None
    summary = str(rp_data.get("hook_description", "") or "")
    return score, summary


def _load_latest_eval_report(
    *,
    storage: Any,
    layout: Any,
    chapter_number: int,
    fallback: EvalReport,
) -> EvalReport:
    try:
        path = layout.eval_report_path(chapter_number)
        if hasattr(storage, "exists") and storage.exists(path):
            payload = storage.load_json(path)
            if isinstance(payload, dict):
                return EvalReport.model_validate(payload)
    except Exception as exc:
        _logger.debug("Failed to load latest eval report for checkpoint: %s", exc)
    return fallback


def _load_latest_report_for_archive_retry(
    *,
    storage: Any,
    path: Any,
    model: Any,
    current_text: str,
    fallback: Any,
) -> Any:
    """Load a report written by the archive gate, rejecting known stale data.

    The finalizer can refresh reports after terminal humanization.  If its hard
    gate then blocks, the persisted reports—not the pre-humanize checkpoint—are
    the authoritative inputs for the next repair.  Reading through storage
    avoids the request artifact cache, whose older snapshot is intentionally
    still valid for the running attempt but wrong for a new checkpoint.
    """
    try:
        if not (hasattr(storage, "exists") and storage.exists(path)):
            return fallback
        payload = storage.load_json(path)
        if not isinstance(payload, dict):
            return fallback
        report = model.model_validate(payload)
        stored_hash = str(getattr(report, "source_text_hash", "") or "").strip()
        current_hash = source_text_hash(current_text) if current_text else ""
        if stored_hash and current_hash and stored_hash != current_hash:
            _logger.debug(
                "Archive retry ignored stale report | path=%s | stored=%s | current=%s",
                path,
                stored_hash,
                current_hash,
            )
            return fallback
        return report
    except Exception as exc:
        _logger.debug(
            "Failed to load archive-gate report for retry | path=%s | error=%s", path, exc
        )
        return fallback


def _refresh_pending_from_archive_gate_reports(
    *,
    storage: Any,
    layout: Any,
    chapter_number: int,
    current_text: str,
    pending: PendingChapterReviewState,
) -> PendingChapterReviewState:
    """Promote verified archive-gate reports into the durable checkpoint.

    This is the ownership hand-off between ``persist_results`` and the next
    checkpoint resolution.  Without it, a terminal humanize edit could be
    checked as low-alignment while the following repair pass still sees the
    pre-edit high score and skips its specialised alignment repair.
    """
    alignment_report = _load_latest_report_for_archive_retry(
        storage=storage,
        path=layout.alignment_report_path(chapter_number),
        model=AlignmentReport,
        current_text=current_text,
        fallback=pending.alignment_report,
    )
    continuity_report = _load_latest_report_for_archive_retry(
        storage=storage,
        path=layout.continuity_report_path(chapter_number),
        model=ContinuityReport,
        current_text=current_text,
        fallback=pending.continuity_report,
    )
    causal_report = _load_latest_report_for_archive_retry(
        storage=storage,
        path=layout.chapter_causal_report_path(chapter_number),
        model=CausalValidationReport,
        current_text=current_text,
        fallback=pending.causal_report,
    )
    chapter_repair_report = _load_latest_report_for_archive_retry(
        storage=storage,
        path=layout.chapter_repair_report_path(chapter_number),
        model=ChapterRepairReport,
        current_text=current_text,
        fallback=pending.chapter_repair_report,
    )
    return dataclasses.replace(
        pending,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        causal_report=causal_report,
        chapter_repair_report=chapter_repair_report,
        eval_report=_load_latest_eval_report(
            storage=storage,
            layout=layout,
            chapter_number=chapter_number,
            fallback=pending.eval_report,
        ),
    )


def _report_is_bound_to_text(report: Any, current_text: str) -> bool:
    """Whether a report is safe evidence for an autonomous text repair."""
    stored_hash = str(getattr(report, "source_text_hash", "") or "").strip()
    return bool(current_text and stored_hash and stored_hash == source_text_hash(current_text))


def _load_latest_orphan_draft_text(
    *,
    layout: Any,
    chapter_number: int,
    fallback: str,
) -> str:
    try:
        orphan_dir = (
            layout.root / "states" / "orphan_chapter_drafts" / f"chapter_{chapter_number:03d}"
        )
        if not orphan_dir.exists():
            return fallback
        candidates = sorted(
            orphan_dir.glob("*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return fallback
        text = candidates[0].read_text(encoding="utf-8")
        return text if text.strip() else fallback
    except Exception as exc:
        _logger.debug("Failed to load latest orphan draft for checkpoint: %s", exc)
    return fallback


_DECISION_OPTION_ID_PATTERN = (
    r"(?:accept_and_finalize|apply_repairs_and_finalize"
    r"|adjust_outline_and_finalize|pause_for_human)"
)


def _rewrite_summary_recommendation(summary: str, recommended_option_id: str) -> str:
    """Sync the decision_line "建议 X" text with the actual ``is_recommended``.

    The retry checkpoint overrides each option's ``is_recommended`` flag after
    ``build_guard_checkpoint`` has already baked the review-matrix recommendation
    into the summary's decision_line.  Without this rewrite the UI would show
    "建议 apply_repairs_and_finalize" while the autopilot reads a
    ``pause_for_human`` recommendation (or vice versa), which is exactly the
    misleading "AI suggests continue but the run stopped" symptom.
    """
    # Format A: "AI 判定：{verdict} / 建议 {recommended} / 风险 ...".
    rewritten, count = re.subn(
        rf"(建议 ){_DECISION_OPTION_ID_PATTERN}",
        rf"\g<1>{recommended_option_id}",
        summary,
        count=1,
    )
    if count:
        return rewritten
    # Format B: "AI 判定：{recommended} / 风险 ..." (no separate verdict).
    rewritten, _ = re.subn(
        rf"(AI 判定：){_DECISION_OPTION_ID_PATTERN}( / 风险)",
        rf"\g<1>{recommended_option_id}\g<2>",
        summary,
        count=1,
    )
    return rewritten


def _build_archive_quality_retry_checkpoint_result(
    *,
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    bundle: LongProjectBundle,
    session_state: GuardCheckpointSessionState,
    pending: PendingChapterReviewState,
    current_text: str,
    bridge_summary: str,
    reason: str,
    block_kind: str = "",
    checkpoint_step: str = "archive_quality_retry_checkpoint",
    metadata_key: str = "archive_quality_block",
    summary_key: str = "archive_quality_block_summary",
    on_step: Any | None = None,
) -> ChapterSessionResult:
    current_text = _load_latest_orphan_draft_text(
        layout=bundle.layout,
        chapter_number=request.chapter_number,
        fallback=current_text,
    )
    save_path = bundle.layout.chapter_review_draft_path(request.chapter_number)
    runtime.storage.save_text(save_path, current_text)
    pending = _refresh_pending_from_archive_gate_reports(
        storage=runtime.storage,
        layout=bundle.layout,
        chapter_number=request.chapter_number,
        current_text=current_text,
        pending=pending,
    )
    warning = f"归档前硬门阻断：{reason}"
    repair_attempts = max(0, int(pending.archive_quality_repair_attempts or 0))
    # An initial "accept" that hits the guard did not execute a repair and
    # must not consume the retry budget.  Only a completed repair pass that
    # still fails the verified archive gate advances it.
    if request.option_id == "apply_repairs_and_finalize":
        repair_attempts += 1
    auto_repair_enabled = _archive_quality_auto_repair_enabled(
        request=request,
        settings=runtime.settings,
    )
    auto_repair_limit = _archive_quality_auto_repair_limit(runtime.settings)
    effective_block_kind = block_kind or classify_archive_quality_block([reason])
    # Alignment repair is evidence-driven: do not let the model guess from a
    # score-only legacy error.  The report must be bound to the orphan draft
    # that the archive gate actually rejected, otherwise preserve the draft
    # and require a fresh review instead of making an unverified edit.
    repair_evidence_bound = (
        effective_block_kind != BLOCK_KIND_ALIGNMENT_QUALITY
        or _report_is_bound_to_text(pending.alignment_report, current_text)
    )
    auto_repair_scheduled = (
        auto_repair_enabled and repair_attempts < auto_repair_limit and repair_evidence_bound
    )
    # When the alignment evidence is NOT bound to the rejected draft we used to
    # fall straight through to pause_for_human, which in book-auto mode surfaced
    # as a hard stop even though a fully autonomous path still exists: refresh
    # the review reports against the current text first (re-establishing the
    # evidence binding), then let the recheck decide whether the gate already
    # passes or a targeted repair is still required.  Only when auto repair is
    # disabled or the budget is truly exhausted do we pause for a human.
    refresh_before_repair = (
        auto_repair_enabled and repair_attempts < auto_repair_limit and not repair_evidence_bound
    )
    proceed_with_repair = auto_repair_scheduled or refresh_before_repair
    # Structured diagnosis event: expose each retry condition so the Desktop
    # and logs can tell exactly why the checkpoint recommends repair vs pause,
    # without having to re-derive the state from disk.
    if callable(on_step):
        on_step(
            "archive_quality_retry_diagnosis",
            {
                "chapter": request.chapter_number,
                "block_kind": effective_block_kind,
                "reason": reason,
                "auto_repair_enabled": auto_repair_enabled,
                "repair_attempts": repair_attempts,
                "auto_repair_limit": auto_repair_limit,
                "repair_evidence_bound": repair_evidence_bound,
                "auto_repair_scheduled": auto_repair_scheduled,
                "refresh_before_repair": refresh_before_repair,
                "proceed_with_repair": proceed_with_repair,
                "recommended_option": (
                    "apply_repairs_and_finalize" if proceed_with_repair else "pause_for_human"
                ),
                "alignment_report_hash": str(
                    getattr(pending.alignment_report, "source_text_hash", "") or ""
                ),
                "text_hash": source_text_hash(current_text),
            },
        )
    retry_pending = dataclasses.replace(
        pending,
        current_text=current_text,
        warnings=tuple(pending.warnings or ()) + (warning,),
        archive_quality_repair_attempts=repair_attempts,
    )
    guard_checkpoint = build_guard_checkpoint(
        bundle,
        request.chapter_number,
        current_text=current_text,
        alignment_report=retry_pending.alignment_report,
        continuity_report=retry_pending.continuity_report,
        eval_report=retry_pending.eval_report,
        causal_report=retry_pending.causal_report,
        guard_decision=retry_pending.guard_decision,
        reading_power_report=retry_pending.reading_power_report,
        chapter_repair_report=retry_pending.chapter_repair_report,
        warnings=tuple(retry_pending.warnings or ()),
        repair_tickets=tuple(retry_pending.repair_tickets or ()),
    )
    if auto_repair_scheduled:
        guard_checkpoint.prompt = (
            "归档前硬门阻断，已保留孤儿草稿。AI 将依据验证报告进行第 "
            f"{repair_attempts + 1}/{auto_repair_limit} 次定向修复，并重新校验后再归档。"
        )
    elif refresh_before_repair:
        guard_checkpoint.prompt = (
            "归档前硬门阻断，已保留孤儿草稿。用于对齐修复的报告未与该正文版本绑定，"
            "AI 将先刷新验证报告并复检：复检通过则直接归档，否则执行定向修复后再校验。"
        )
    else:
        guard_checkpoint.prompt = (
            "归档前硬门阻断，已保留孤儿草稿。自动修复次数已耗尽或当前不是 AI 自动模式，"
            "请查看相关报告后决定下一步。"
        )
    for option in guard_checkpoint.options:
        option.is_recommended = (
            option.option_id == "apply_repairs_and_finalize"
            if proceed_with_repair
            else option.option_id == "pause_for_human"
        )
        if option.option_id == "pause_for_human":
            option.description = (
                "自动修复仍在额度内；如需人工介入可暂停。"
                if proceed_with_repair
                else "归档硬门阻断且自动修复不可继续，请人工查看相关报告后处理。"
            )
        elif option.option_id == "apply_repairs_and_finalize":
            if refresh_before_repair:
                option.description = (
                    "先刷新验证报告并复检当前正文；复检通过直接归档，"
                    f"否则执行第 {repair_attempts + 1}/{auto_repair_limit} 次定向修复。"
                )
            else:
                option.description = (
                    f"执行第 {repair_attempts + 1}/{auto_repair_limit} 次定向修复；"
                    "只有复检通过同一正文版本才会归档。"
                    if auto_repair_scheduled
                    else "自动修复额度已耗尽；可在人工调整后再次尝试。"
                )
        elif option.option_id == "accept_and_finalize":
            option.description = "归档硬门未通过，当前不可直接归档。"
    # Keep the UI decision_line consistent with the option the autopilot will
    # actually pick: the review-matrix recommendation baked into the summary may
    # differ from the overridden is_recommended flag computed above.
    guard_checkpoint.summary = _rewrite_summary_recommendation(
        guard_checkpoint.summary,
        "apply_repairs_and_finalize" if proceed_with_repair else "pause_for_human",
    )
    return _persist_guard_checkpoint_and_build_result(
        runtime=runtime,
        request=request,
        bundle=bundle,
        session_state=session_state,
        guard_checkpoint=guard_checkpoint,
        pending_state=retry_pending,
        text=current_text,
        bridge_summary=bridge_summary,
        metadata=_guard_checkpoint_metadata(
            retry_pending,
            {
                metadata_key: True,
                summary_key: reason,
                "checkpoint_step": checkpoint_step,
                "archive_quality_repair_attempts": repair_attempts,
                "archive_quality_auto_repair_enabled": auto_repair_enabled,
                "archive_quality_auto_repair_scheduled": auto_repair_scheduled,
                "archive_quality_refresh_before_repair": refresh_before_repair,
                "archive_quality_proceed_with_repair": proceed_with_repair,
                "archive_quality_auto_repair_exhausted": (
                    auto_repair_enabled and repair_attempts >= auto_repair_limit
                ),
                "archive_quality_auto_repair_limit": auto_repair_limit,
                "archive_quality_repair_evidence_bound": repair_evidence_bound,
                "archive_quality_text_hash": source_text_hash(current_text),
                "archive_quality_block_kind": effective_block_kind,
            },
        ),
    )


def _auto_introduce_limit(settings: Any) -> int:
    try:
        return max(0, int(getattr(settings, "long_auto_introduce_max_new_characters", 2) or 0))
    except (TypeError, ValueError):
        return 2


def _raise_if_parallel_recheck_failed(*results: Any) -> None:
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            raise result


def _guard_ticket_is_actionable(ticket: RepairTicket) -> bool:
    metadata = dict(getattr(ticket, "metadata", {}) or {})
    if metadata.get("auto_repair_eligible") is False:
        return False
    readiness = metadata.get("repair_readiness")
    if isinstance(readiness, dict) and readiness.get("auto_repair_eligible") is False:
        return False
    source_module = str(getattr(ticket, "source_module", "") or "")
    dimension = str(getattr(ticket, "dimension", "") or "").strip().lower()
    if source_module != "guard_constraint_compliance" and dimension not in {
        "guard",
        "guard_compliance",
    }:
        return False
    if metadata.get("check_error") is True or metadata.get("repairable") is False:
        return False
    evidence_quote = str(metadata.get("evidence_quote") or "").strip()
    if evidence_quote and metadata.get("evidence_exact") is not True:
        return False
    issue_type = str(getattr(ticket, "issue_type", "") or "").lower()
    if issue_type in {"guard_constraint_unverified", "guard_constraint_check_failed"}:
        return False
    status = str(metadata.get("status", "") or "").strip().lower().replace("-", "_")
    return status in {"non_compliant", "partial", "weak"}


def _guard_rate_for_progress(report: dict[str, Any] | None) -> float | None:
    if not isinstance(report, dict):
        return None
    rate = report.get("overall_compliance_rate")
    if rate is None:
        return None
    try:
        return round(float(rate), 3)
    except (TypeError, ValueError):
        return None


def _alignment_score(report: Any) -> float:
    try:
        return float(getattr(report, "alignment_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _raise_if_guard_ticket_alignment_regression(
    *,
    before: Any,
    after: Any,
    settings: Any,
    chapter_number: int,
) -> None:
    logger = logging.getLogger(__name__)
    before_score = _alignment_score(before)
    after_score = _alignment_score(after)
    threshold = normalize_threshold(getattr(settings, "long_alignment_threshold", 7.0))
    regression_threshold = getattr(settings, "guardrail_regression_threshold", 0.5)
    score_drop = before_score - after_score

    logger.info(
        "护栏 ticket 对齐分检查: 第 %d 章 修复前 %.1f → 修复后 %.1f (阈值 %.1f, 下降幅度 %.1f, 回归阈值 %.1f)",
        chapter_number,
        before_score,
        after_score,
        threshold,
        score_drop,
        regression_threshold,
    )

    # Pass if after score meets threshold
    if after_score >= threshold:
        logger.info(
            "护栏 ticket 对齐分检查通过: 第 %d 章 修复后分数 %.1f >= 阈值 %.1f",
            chapter_number,
            after_score,
            threshold,
        )
        return

    # Below threshold - check regression severity
    if score_drop < regression_threshold:
        logger.warning(
            "护栏 ticket 修复后第 %d 章对齐分从 %.1f 降至 %.1f，低于阈值 %.1f，"
            "但下降幅度 %.1f < 回归阈值 %.1f，允许继续（警告）",
            chapter_number,
            before_score,
            after_score,
            threshold,
            score_drop,
            regression_threshold,
        )
        return

    logger.error(
        "护栏 ticket 修复后第 %d 章对齐分从 %.1f 降至 %.1f，低于阈值 %.1f，"
        "下降幅度 %.1f >= 回归阈值 %.1f，阻断归档",
        chapter_number,
        before_score,
        after_score,
        threshold,
        score_drop,
        regression_threshold,
    )
    raise RuntimeError(
        f"护栏 ticket 修复后第 {chapter_number} 章对齐分从 {before_score:.1f} "
        f"降至 {after_score:.1f}，低于阈值 {threshold:.1f}；已停止自动归档。"
    )


def _guard_ticket_alignment_followup_required(
    *,
    before: Any,
    after: Any,
    settings: Any,
) -> bool:
    before_score = _alignment_score(before)
    after_score = _alignment_score(after)
    threshold = normalize_threshold(getattr(settings, "long_alignment_threshold", 7.0))
    regression_threshold = getattr(settings, "guardrail_regression_threshold", 0.5)
    if after_score >= threshold:
        return False
    if before_score - after_score < regression_threshold:
        return False
    return alignment_requires_repair(after, alignment_threshold=threshold)


def _restore_pending_text_after_guard_regression(
    *,
    runtime: RuntimeServices,
    bundle: LongProjectBundle,
    chapter_number: int,
    pending: PendingChapterReviewState,
) -> None:
    save_path = bundle.layout.chapter_path(chapter_number)
    if not save_path.exists():
        save_path = bundle.layout.chapter_review_draft_path(chapter_number)
    runtime.storage.save_text(save_path, pending.current_text)
    text_hash = source_text_hash(pending.current_text)

    def _save_report(path: Any, report: Any | None) -> None:
        if report is None:
            return
        payload = report.model_dump(mode="json")
        payload["source_text_hash"] = text_hash
        runtime.storage.save_json(path, payload)

    _save_report(bundle.layout.alignment_report_path(chapter_number), pending.alignment_report)
    _save_report(bundle.layout.continuity_report_path(chapter_number), pending.continuity_report)
    _save_report(bundle.layout.chapter_causal_report_path(chapter_number), pending.causal_report)
    _save_report(bundle.layout.eval_report_path(chapter_number), pending.eval_report)

    if pending.guard_compliance_report is not None:
        guard_payload = dict(pending.guard_compliance_report)
        guard_payload["source_text_hash"] = text_hash
        guard_payload["_text_hash"] = text_hash
        runtime.storage.save_json(bundle.layout.guard_report_path(chapter_number), guard_payload)

    try:
        from novel_forge.core.utils.edit_tracker import save_snapshot

        save_snapshot(save_path, bundle.layout.states_dir, chapter_number)
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to restore edit snapshot after guard regression: %s", exc)


def _guard_ticket_alignment_retry_limit(settings: Any) -> int:
    try:
        return max(
            1, int(getattr(settings, "guard_ticket_alignment_followup_max_attempts", 2) or 2)
        )
    except (TypeError, ValueError):
        return 2


def _build_guard_alignment_retry_checkpoint_result(
    *,
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    bundle: LongProjectBundle,
    session_state: GuardCheckpointSessionState,
    pending: PendingChapterReviewState,
    current_text: str,
    alignment_report: AlignmentReport,
    continuity_report: ContinuityReport,
    reason: str,
) -> ChapterSessionResult:
    save_path = bundle.layout.chapter_path(request.chapter_number)
    if not save_path.exists():
        save_path = bundle.layout.chapter_review_draft_path(request.chapter_number)
    runtime.storage.save_text(save_path, current_text)
    text_hash = source_text_hash(current_text)
    alignment_payload = alignment_report.model_dump(mode="json")
    alignment_payload["source_text_hash"] = text_hash
    runtime.storage.save_json(
        bundle.layout.alignment_report_path(request.chapter_number),
        alignment_payload,
    )
    continuity_payload = continuity_report.model_dump(mode="json")
    continuity_payload["source_text_hash"] = text_hash
    runtime.storage.save_json(
        bundle.layout.continuity_report_path(request.chapter_number),
        continuity_payload,
    )
    retry_pending = dataclasses.replace(
        pending,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        warnings=tuple(pending.warnings or ()) + (reason,),
    )
    retry_checkpoint = build_guard_checkpoint(
        bundle,
        request.chapter_number,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        eval_report=retry_pending.eval_report,
        causal_report=retry_pending.causal_report,
        guard_decision=retry_pending.guard_decision,
        reading_power_report=retry_pending.reading_power_report,
        chapter_repair_report=retry_pending.chapter_repair_report,
        warnings=tuple(retry_pending.warnings or ()),
        repair_tickets=tuple(retry_pending.repair_tickets or ()),
    )
    return _persist_guard_checkpoint_and_build_result(
        runtime=runtime,
        request=request,
        bundle=bundle,
        session_state=session_state,
        guard_checkpoint=retry_checkpoint,
        pending_state=retry_pending,
        text=current_text,
        bridge_summary=_load_chapter_bridge_summary(
            runtime=runtime,
            bundle=bundle,
            chapter_number=request.chapter_number,
        ),
        metadata=_guard_checkpoint_metadata(
            retry_pending,
            {
                "guard_alignment_retry": True,
            },
        ),
    )


async def _repair_or_raise_guard_ticket_alignment_regression(
    *,
    context: Any,
    runtime: RuntimeServices,
    runner: Any,
    bundle: LongProjectBundle,
    packet: Any,
    chapter_bridge: Any,
    chapter_plan: Any,
    pending: PendingChapterReviewState,
    current_text: str,
    alignment_report: AlignmentReport,
    continuity_report: ContinuityReport,
    chapter_number: int,
    trace: PipelineTrace,
    on_step_progress: Any = None,
) -> tuple[str, AlignmentReport, ContinuityReport, bool, str | None]:
    followup_applied = False
    threshold = normalize_threshold(getattr(context.settings, "long_alignment_threshold", 7.0))
    max_attempts = _guard_ticket_alignment_retry_limit(context.settings)
    regression_threshold = getattr(context.settings, "guardrail_regression_threshold", 0.5)
    initial_before_score = _alignment_score(pending.alignment_report)
    initial_after_score = _alignment_score(alignment_report)
    blocking_regression_seen = (
        initial_after_score < threshold
        and initial_before_score - initial_after_score >= regression_threshold
    )
    best_text = pending.current_text
    best_alignment = pending.alignment_report
    best_continuity = pending.continuity_report
    if _alignment_score(alignment_report) >= _alignment_score(best_alignment):
        best_text = current_text
        best_alignment = alignment_report
        best_continuity = continuity_report
    attempts = 0
    while attempts < max_attempts and _guard_ticket_alignment_followup_required(
        before=pending.alignment_report,
        after=alignment_report,
        settings=context.settings,
    ):
        attempts += 1
        before_score = _alignment_score(pending.alignment_report)
        after_score = _alignment_score(alignment_report)
        payload = {
            "chapter": chapter_number,
            "attempt": attempts,
            "max_attempts": max_attempts,
            "previous_score": round(before_score, 2),
            "current_score": round(after_score, 2),
            "threshold": threshold,
        }
        context.on_step("guard_ticket_alignment_followup_start", payload)
        if callable(on_step_progress):
            on_step_progress("guard_ticket_alignment_followup_start", payload)

        repaired_text = await alignment_repair_edit(
            runner,
            bundle=bundle,
            packet=packet,
            bridge=chapter_bridge,
            plan=chapter_plan,
            current_text=current_text,
            chapter_number=chapter_number,
            alignment_report=alignment_report,
            trace=trace,
        )
        followup_applied = followup_applied or repaired_text != current_text
        current_text = repaired_text
        save_path = bundle.layout.chapter_path(chapter_number)
        if not save_path.exists():
            save_path = bundle.layout.chapter_review_draft_path(chapter_number)
        runtime.storage.save_text(save_path, current_text)

        async def _refresh_alignment_after_followup(text: str = current_text) -> Any:
            return await recheck_alignment(
                runner,
                bundle,
                packet,
                chapter_plan,
                text,
                chapter_number,
                trace,
            )

        async def _refresh_continuity_after_followup(
            text: str = current_text,
        ) -> Any:
            return await run_continuity_recheck(
                services=context,
                chapter_number=chapter_number,
                current_text=text,
                packet=packet,
                bridge=chapter_bridge,
                plan=chapter_plan,
                trace=trace,
                pov_switch=getattr(bundle.chapter_outline, "pov_switch", False),
                project_path=getattr(bundle.layout, "root", None),
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            )

        raw_alignment: Any
        raw_continuity: Any
        raw_alignment, raw_continuity = await asyncio.gather(
            _refresh_alignment_after_followup(),
            _refresh_continuity_after_followup(),
            return_exceptions=True,
        )
        _raise_if_parallel_recheck_failed(raw_alignment, raw_continuity)
        alignment_report = cast(AlignmentReport, raw_alignment)
        continuity_report = cast(ContinuityReport, raw_continuity)
        if _alignment_score(alignment_report) >= _alignment_score(best_alignment):
            best_text = current_text
            best_alignment = alignment_report
            best_continuity = continuity_report
        cont_payload = continuity_report.model_dump(mode="json")
        cont_payload["source_text_hash"] = source_text_hash(current_text)
        runtime.storage.save_json(
            bundle.layout.alignment_report_path(chapter_number),
            {
                **alignment_report.model_dump(mode="json"),
                "source_text_hash": source_text_hash(current_text),
            },
        )
        runtime.storage.save_json(
            bundle.layout.continuity_report_path(chapter_number),
            cont_payload,
        )
        complete_payload = {
            "chapter": chapter_number,
            "attempt": attempts,
            "applied": followup_applied,
            "score": round(_alignment_score(alignment_report), 2),
            "best_score": round(_alignment_score(best_alignment), 2),
        }
        context.on_step("guard_ticket_alignment_followup_complete", complete_payload)
        if callable(on_step_progress):
            on_step_progress("guard_ticket_alignment_followup_complete", complete_payload)

    if _alignment_score(best_alignment) > _alignment_score(alignment_report):
        current_text = best_text
        alignment_report = best_alignment
        continuity_report = best_continuity
        save_path = bundle.layout.chapter_path(chapter_number)
        if not save_path.exists():
            save_path = bundle.layout.chapter_review_draft_path(chapter_number)
        runtime.storage.save_text(save_path, current_text)
        cont_payload = continuity_report.model_dump(mode="json")
        cont_payload["source_text_hash"] = source_text_hash(current_text)
        alignment_payload = alignment_report.model_dump(mode="json")
        alignment_payload["source_text_hash"] = source_text_hash(current_text)
        runtime.storage.save_json(
            bundle.layout.alignment_report_path(chapter_number),
            alignment_payload,
        )
        runtime.storage.save_json(
            bundle.layout.continuity_report_path(chapter_number),
            cont_payload,
        )
        context.on_step(
            "guard_ticket_alignment_best_version_restored",
            {
                "chapter": chapter_number,
                "score": round(_alignment_score(alignment_report), 2),
                "attempts": attempts,
            },
        )

    if (
        blocking_regression_seen
        and _alignment_score(alignment_report) <= initial_before_score
        and _alignment_score(alignment_report) < threshold
    ):
        reason = (
            f"护栏 ticket 修复后的自动对齐补救已尝试 {attempts}/{max_attempts} 轮，"
            f"最佳对齐分 {_alignment_score(alignment_report):.1f} 仍未超过修复前 "
            f"{initial_before_score:.1f}，且低于阈值 {threshold:.1f}；"
            "已保留当前最高分版本并回到归档选择，可继续应用修复或人工补充关键主线锚点。"
        )
        context.on_step(
            "guard_ticket_alignment_retry_checkpoint",
            {
                "chapter": chapter_number,
                "attempts": attempts,
                "max_attempts": max_attempts,
                "score": round(_alignment_score(alignment_report), 2),
                "threshold": threshold,
                "reason": reason,
            },
        )
        if callable(on_step_progress):
            on_step_progress(
                "guard_ticket_alignment_retry_checkpoint",
                {
                    "chapter": chapter_number,
                    "attempts": attempts,
                    "score": round(_alignment_score(alignment_report), 2),
                    "threshold": threshold,
                },
            )
        return current_text, alignment_report, continuity_report, followup_applied, reason

    try:
        _raise_if_guard_ticket_alignment_regression(
            before=pending.alignment_report,
            after=alignment_report,
            settings=context.settings,
            chapter_number=chapter_number,
        )
    except RuntimeError:
        reason = (
            f"护栏 ticket 修复后的自动对齐补救已尝试 {attempts}/{max_attempts} 轮，"
            f"最佳对齐分 {_alignment_score(alignment_report):.1f} 仍低于阈值 {threshold:.1f}；"
            "已保留当前最高分版本并回到归档选择，可继续应用修复或人工补充关键主线锚点。"
        )
        context.on_step(
            "guard_ticket_alignment_retry_checkpoint",
            {
                "chapter": chapter_number,
                "attempts": attempts,
                "max_attempts": max_attempts,
                "score": round(_alignment_score(alignment_report), 2),
                "threshold": threshold,
                "reason": reason,
            },
        )
        if callable(on_step_progress):
            on_step_progress(
                "guard_ticket_alignment_retry_checkpoint",
                {
                    "chapter": chapter_number,
                    "attempts": attempts,
                    "score": round(_alignment_score(alignment_report), 2),
                    "threshold": threshold,
                },
            )
        return current_text, alignment_report, continuity_report, followup_applied, reason
    return current_text, alignment_report, continuity_report, followup_applied, None


def _select_causal_must_fix_issues(causal_report: Any, settings: Any) -> list[Any]:
    """Select causal issues that must be repaired before checkpoint finalization."""
    if causal_report is None:
        return []
    from novel_forge.common.constants import severity_at_least

    must_fix_sev = (getattr(settings, "repair_must_fix_severity", "critical") or "critical").lower()
    if must_fix_sev == "off":
        must_fix_sev = "high"
    return [
        issue
        for issue in (getattr(causal_report, "issues", []) or [])
        if severity_at_least(
            (getattr(issue, "severity", "") or "").lower(),
            must_fix_sev,
        )
    ]


_GUARD_PUNCT_RE = re.compile(
    r"[，。！？；：、\"'“”‘’（）《》〈〉【】『』「」·,.!?;:()\[\]{}<>\-—~…\s]+"
)
_GUARD_BASE_SPLIT_RE = re.compile(r"(?:——|--|—|：|:|（|\(|【|\[)")
_GUARD_SOFT_FORBIDDEN_MARKERS = frozenset(
    {
        "仅用一次",
        "控制频率",
        "频率控制",
        "可沿用",
        "可复用",
        "有意回环",
        "刻意复用",
        "后续",
        "本章作为",
        "场景锚点",
        "剧情锚点",
    }
)
_GUARD_ANCHOR_STOPWORDS = frozenset(
    {
        "AI",
        "ai",
        "guard",
        "constraint",
        "partial",
        "weak",
        "non",
        "compliant",
        "护栏",
        "约束",
        "合规",
        "修复",
        "目标",
        "标准",
        "接受",
        "正文",
        "章节",
        "本章",
        "问题",
        "需要",
        "必须",
        "应当",
        "体现",
        "落实",
        "不足",
        "缺失",
        "保持",
        "保留",
        "不得",
        "不能",
        "已经",
        "出现",
        "说明",
    }
)


def _guard_compact_text(text: str) -> str:
    return _GUARD_PUNCT_RE.sub("", str(text or ""))


def _guard_base_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    base = _GUARD_BASE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    return base or text


def _guard_has_soft_forbidden_marker(value: Any) -> bool:
    text = str(value or "")
    return any(marker in text for marker in _GUARD_SOFT_FORBIDDEN_MARKERS)


def _split_guard_paragraphs(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in raw.splitlines() if part.strip()]
    return paragraphs or [raw]


def _guard_ticket_texts(ticket: RepairTicket) -> list[str]:
    metadata = dict(getattr(ticket, "metadata", {}) or {})
    values: list[str] = [
        getattr(ticket, "target_summary", ""),
        getattr(ticket, "repair_goal", ""),
        getattr(ticket, "issue_type", ""),
        metadata.get("constraint", ""),
        metadata.get("evidence_quote", ""),
        metadata.get("evidence", ""),
        metadata.get("remaining_evidence", ""),
        metadata.get("notes", ""),
    ]
    values.extend(getattr(ticket, "acceptance_criteria", []) or [])
    values.extend(getattr(ticket, "must_preserve", []) or [])
    values.extend(getattr(ticket, "forbidden_changes", []) or [])
    return [str(item).strip() for item in values if str(item or "").strip()]


def _guard_ticket_terms(ticket: RepairTicket) -> list[str]:
    terms: dict[str, int] = {}

    def _record_term(value: str, score: int) -> None:
        cleaned = value.strip()
        compact = _guard_compact_text(cleaned)
        if (
            len(compact) < 2
            or cleaned in _GUARD_ANCHOR_STOPWORDS
            or compact in _GUARD_ANCHOR_STOPWORDS
        ):
            return
        terms[cleaned] = max(terms.get(cleaned, 0), score)

    for text in _guard_ticket_texts(ticket):
        for quoted in re.findall(r"[“\"'「『]([^”\"'」』]{2,36})[”\"'」』]", text):
            cleaned = quoted.strip()
            compact = _guard_compact_text(cleaned)
            if len(compact) >= 2:
                _record_term(cleaned, len(compact) + 8)
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9·]{2,14}", text):
            cleaned = token.strip()
            compact = _guard_compact_text(cleaned)
            _record_term(cleaned, min(8, len(compact)))
            if re.fullmatch(r"[\u4e00-\u9fff]{5,}", compact):
                for width in (5, 4, 3, 2):
                    for start in range(0, len(compact) - width + 1):
                        gram = compact[start : start + width]
                        _record_term(gram, width + 2)
    return [
        term
        for term, _score in sorted(terms.items(), key=lambda item: (-item[1], item[0]))
        if _guard_compact_text(term)
    ][:160]


def _resolve_guard_ticket_anchor(ticket: RepairTicket, current_text: str) -> dict[str, Any]:
    paragraphs = _split_guard_paragraphs(current_text)
    total = len(paragraphs)
    explicit_start = int(getattr(ticket, "target_paragraph_start", 0) or 0)
    explicit_end = int(getattr(ticket, "target_paragraph_end", 0) or explicit_start or 0)
    if total > 0 and explicit_start > 0:
        start = max(1, min(explicit_start, total))
        end = max(start, min(explicit_end or start, total))
        return {
            "paragraph_start": start,
            "paragraph_end": end,
            "location": _guard_ticket_location(ticket),
            "location_confidence": 0.9,
            "anchor_type": "explicit_para",
            "evidence_quote": "",
        }

    metadata = dict(getattr(ticket, "metadata", {}) or {})
    evidence_quote = str(metadata.get("evidence_quote") or "").strip()
    if evidence_quote and paragraphs:
        for idx, paragraph in enumerate(paragraphs, 1):
            if evidence_quote in paragraph:
                return {
                    "paragraph_start": idx,
                    "paragraph_end": idx,
                    "location": f"第{idx}段",
                    "location_confidence": 0.86,
                    "anchor_type": "evidence_match",
                    "evidence_quote": evidence_quote,
                }
        if metadata.get("evidence_exact") is True:
            return {
                "paragraph_start": 0,
                "paragraph_end": 0,
                "location": "",
                "location_confidence": 0.0,
                "anchor_type": "unresolved",
                "evidence_quote": evidence_quote,
            }

    terms = _guard_ticket_terms(ticket)
    if not terms or not paragraphs:
        return {
            "paragraph_start": 0,
            "paragraph_end": 0,
            "location": "",
            "location_confidence": 0.0,
            "anchor_type": "unresolved",
            "evidence_quote": evidence_quote,
        }

    best_idx = 0
    best_score = 0
    best_hits: list[str] = []
    for idx, paragraph in enumerate(paragraphs, 1):
        compact_para = _guard_compact_text(paragraph)
        hits = [
            term
            for term in terms
            if (compact := _guard_compact_text(term))
            and len(compact) >= 2
            and compact in compact_para
        ]
        if not hits:
            continue
        high_signal_hits = [term for term in hits if len(_guard_compact_text(term)) >= 3]
        if not high_signal_hits:
            continue
        score = sum(min(8, len(_guard_compact_text(term))) for term in set(hits))
        if score > best_score:
            best_idx = idx
            best_score = score
            best_hits = hits

    if best_idx <= 0 or best_score < 4:
        return {
            "paragraph_start": 0,
            "paragraph_end": 0,
            "location": "",
            "location_confidence": 0.0,
            "anchor_type": "unresolved",
            "evidence_quote": evidence_quote,
        }

    paragraph = paragraphs[best_idx - 1]
    confidence = min(0.82, 0.56 + 0.05 * len(set(best_hits)) + 0.01 * best_score)
    return {
        "paragraph_start": best_idx,
        "paragraph_end": best_idx,
        "location": f"第{best_idx}段",
        "location_confidence": round(confidence, 2),
        "anchor_type": "semantic_terms",
        "evidence_quote": paragraph[:160],
    }


def _guard_protected_plan_texts(chapter_plan: Any, tickets: tuple[RepairTicket, ...]) -> list[str]:
    texts: list[str] = []

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if text:
            texts.append(text)

    if chapter_plan is not None:
        for attr in (
            "opening_contract",
            "closing_contract",
            "emotional_arc",
        ):
            _add(getattr(chapter_plan, attr, ""))
        for attr in (
            "required_state_transitions",
            "foreshadowing_plan",
            "key_revelations",
            "relationship_evolution",
            "intentional_callbacks",
        ):
            for item in getattr(chapter_plan, attr, []) or []:
                _add(item)
        for scene in getattr(chapter_plan, "scene_intents", []) or []:
            for attr in (
                "summary",
                "purpose",
                "conflict",
                "required_outcome",
                "exit_target_state",
                "location",
                "relationship_dynamics",
                "emotional_beat",
                "sensory_notes",
            ):
                _add(getattr(scene, attr, ""))
            for item in getattr(scene, "required_characters", []) or []:
                _add(item)
            # Scene-owned anchors are executable plan commitments. They can
            # legitimately overlap a broad source-level "forbidden" phrase,
            # so preserve them when a resumed guard flow reclassifies rules.
            for attr in ("owned_events", "owned_revelations", "owned_state_changes"):
                for item in getattr(scene, attr, []) or []:
                    _add(item)

    for ticket in tickets:
        for text in _guard_ticket_texts(ticket):
            _add(text)

    return texts


def _guard_texts_contain_term(texts: list[str], term: str) -> bool:
    compact_term = _guard_compact_text(term)
    if len(compact_term) < 2:
        return False
    return any(compact_term in _guard_compact_text(text) for text in texts)


def _sanitize_guard_repair_plan(chapter_plan: Any, tickets: tuple[RepairTicket, ...]) -> Any:
    if chapter_plan is None:
        return chapter_plan
    hard = [
        str(item).strip()
        for item in (getattr(chapter_plan, "forbidden_elements", []) or [])
        if str(item).strip()
    ]
    if not hard:
        return chapter_plan

    protected_texts = _guard_protected_plan_texts(chapter_plan, tickets)
    soft = [
        str(item).strip()
        for item in (getattr(chapter_plan, "forbidden_elements_soft", []) or [])
        if str(item).strip()
    ]
    quota = [
        str(item).strip()
        for item in (getattr(chapter_plan, "forbidden_elements_quota", []) or [])
        if str(item).strip()
    ]
    soft_seen = set(soft)
    quota_seen = set(quota)
    sanitized_hard: list[str] = []
    moved = False
    for item in hard:
        base = _guard_base_text(item)
        conflicts_with_contract = _guard_texts_contain_term(protected_texts, base)
        should_soften = conflicts_with_contract or _guard_has_soft_forbidden_marker(item)
        if should_soften:
            moved = True
            if _guard_has_soft_forbidden_marker(item):
                if item not in quota_seen:
                    quota.append(item)
                    quota_seen.add(item)
            elif item not in soft_seen:
                soft.append(item)
                soft_seen.add(item)
            continue
        sanitized_hard.append(item)

    if not moved:
        return chapter_plan
    update = {
        "forbidden_elements": sanitized_hard,
        "forbidden_elements_soft": soft,
        "forbidden_elements_quota": quota,
    }
    if hasattr(chapter_plan, "model_copy"):
        return chapter_plan.model_copy(update=update)
    if dataclasses.is_dataclass(chapter_plan):
        return dataclasses.replace(cast(Any, chapter_plan), **update)
    return SimpleNamespace(**{**getattr(chapter_plan, "__dict__", {}), **update})


def _guard_ticket_issue_type(ticket: RepairTicket) -> str:
    issue_type = str(ticket.issue_type or "").strip().lower()
    if issue_type in {"guard_constraint_partial", "guard_constraint_note"}:
        return "bridge_contract_not_followed"
    return "carry_forward_missing"


def _guard_ticket_location(ticket: RepairTicket) -> str:
    start = int(ticket.target_paragraph_start or 0)
    end = int(ticket.target_paragraph_end or 0)
    if start > 0 and end > 0 and end != start:
        return f"第{start}-{end}段"
    if start > 0:
        return f"第{start}段"
    return ""


def _guard_ticket_fix_actions(ticket: RepairTicket) -> list[str]:
    actions = []
    if ticket.repair_goal:
        actions.append(ticket.repair_goal)
    actions.extend(str(item) for item in (ticket.acceptance_criteria or []) if str(item).strip())
    actions.extend(
        f"必须保留：{item}" for item in (ticket.must_preserve or []) if str(item).strip()
    )
    return actions


def _guard_report_actionable_count(report: dict[str, Any] | None) -> int:
    if not isinstance(report, dict):
        return 0
    try:
        return int(report.get("actionable_violation_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _guard_report_rate(report: dict[str, Any] | None) -> float | None:
    if not isinstance(report, dict):
        return None
    value = report.get("overall_compliance_rate")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _report_score(report: Any, attr: str) -> float:
    try:
        return float(getattr(report, attr, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _hard_issue_count(report: Any) -> int:
    issues = list(getattr(report, "issues", []) or [])
    return sum(
        1
        for issue in issues
        if str(getattr(issue, "severity", "") or "").lower() in {"critical", "high", "hard"}
    )


def _guard_ticket_recheck_regressed(
    *,
    before_guard: dict[str, Any] | None,
    after_guard: dict[str, Any] | None,
    before_continuity: Any,
    after_continuity: Any,
    before_alignment: Any,
    after_alignment: Any,
) -> str:
    before_rate = _guard_report_rate(before_guard)
    after_rate = _guard_report_rate(after_guard)
    if before_rate is not None and after_rate is not None and after_rate < before_rate - 0.01:
        return f"guard_rate_drop:{before_rate:.2f}->{after_rate:.2f}"
    if _guard_report_actionable_count(after_guard) > _guard_report_actionable_count(before_guard):
        return (
            "guard_actionable_increase:"
            f"{_guard_report_actionable_count(before_guard)}"
            f"->{_guard_report_actionable_count(after_guard)}"
        )

    before_cont_score = _report_score(before_continuity, "continuity_score")
    after_cont_score = _report_score(after_continuity, "continuity_score")
    if after_cont_score < before_cont_score - 0.1:
        return f"continuity_score_drop:{before_cont_score:.1f}->{after_cont_score:.1f}"
    if _hard_issue_count(after_continuity) > _hard_issue_count(before_continuity):
        return "continuity_hard_issue_increase"

    before_align_score = _report_score(before_alignment, "alignment_score")
    after_align_score = _report_score(after_alignment, "alignment_score")
    if after_align_score < before_align_score - 0.1:
        return f"alignment_score_drop:{before_align_score:.1f}->{after_align_score:.1f}"

    return ""


def _ticket_to_continuity_issue(
    ticket: RepairTicket,
    current_text: str = "",
) -> ContinuityIssue:
    anchor = _resolve_guard_ticket_anchor(ticket, current_text)
    paragraph_start = int(anchor.get("paragraph_start") or ticket.target_paragraph_start or 0)
    paragraph_end = int(
        anchor.get("paragraph_end") or ticket.target_paragraph_end or paragraph_start or 0
    )
    evidence_quote = str(ticket.metadata.get("evidence_quote") or "").strip()
    anchored_evidence = str(anchor.get("evidence_quote") or "").strip()
    constraint = str(ticket.metadata.get("constraint") or "").strip()
    rewrite_scope = "paragraph" if paragraph_start > 0 else "chapter"
    return ContinuityIssue(
        issue_type=_guard_ticket_issue_type(ticket),
        severity=str(ticket.severity or "medium"),
        summary=str(
            ticket.target_summary or ticket.repair_goal or constraint or "AI护栏约束未充分兑现"
        ),
        evidence=anchored_evidence or evidence_quote or constraint,
        location=str(anchor.get("location") or _guard_ticket_location(ticket)),
        location_confidence=float(anchor.get("location_confidence") or 0.0),
        anchor_type=str(anchor.get("anchor_type") or "unresolved"),
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
        evidence_quote=anchored_evidence or evidence_quote,
        fix_mode=str(ticket.repair_mode or "window"),
        rewrite_scope=rewrite_scope,
        fix_actions=_guard_ticket_fix_actions(ticket),
    )


async def _run_guard_ticket_repair(
    *,
    context: Any,
    bundle: LongProjectBundle,
    packet: Any,
    chapter_bridge: Any,
    chapter_plan: Any,
    pending: PendingChapterReviewState,
    current_text: str,
    chapter_number: int,
    trace: PipelineTrace,
) -> tuple[str, Any | None, bool]:
    tickets = tuple(
        ticket for ticket in (pending.repair_tickets or ()) if _guard_ticket_is_actionable(ticket)
    )
    if not tickets:
        return current_text, None, False

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
        on_step=context.on_step,
    )
    current = current_text
    current_guard_report = (
        dict(pending.guard_compliance_report)
        if isinstance(pending.guard_compliance_report, dict)
        else None
    )
    if current_guard_report is None:
        current_guard_report = await check_guard_constraint_compliance(
            runner=SimpleNamespace(
                _router=context.router,
                _builder=context.builder,
                _settings=context.settings,
            ),
            bundle=bundle,
            packet=packet,
            current_text=current,
            chapter_number=chapter_number,
        )
    current_continuity = pending.continuity_report
    current_alignment = pending.alignment_report
    last_repair_plan: Any | None = None
    applied_any = False
    anchored_count = 0

    for index, ticket in enumerate(tickets, start=1):
        issue = _ticket_to_continuity_issue(ticket, current)
        if issue.paragraph_start <= 0 and issue.location_confidence < 0.6:
            _logger.warning(
                "guard_ticket_skipped_unanchored | chapter=%d | ticket=%s | index=%d",
                chapter_number,
                ticket.ticket_id or "",
                index,
            )
            continue
        anchored_count += 1
        ticket_issues = (issue,)
        safe_chapter_plan = _sanitize_guard_repair_plan(chapter_plan, (ticket,))
        _repair_attempt_guidance = build_repair_attempt_guidance(
            domain="guard",
            round_number=index,
            max_rounds=len(tickets),
            issues=list(ticket_issues),
            current_score=_report_score(current_alignment, "alignment_score"),
            score_threshold=normalize_threshold(
                getattr(context.settings, "long_alignment_threshold", 7.0)
            ),
        )
        context.on_step(
            "repair_attempt_guidance",
            {
                "chapter": chapter_number,
                "dimension": "guard",
                "round": index,
                "strategy": _repair_attempt_guidance.get("strategy_id"),
            },
        )
        repair_result = await run_continuity_repair(
            repair_step,
            ContinuityRepairInput(
                chapter_number=chapter_number,
                chapter_text=current,
                chapter_state_packet=packet,
                chapter_bridge=chapter_bridge,
                chapter_plan=safe_chapter_plan,
                continuity_report=ContinuityReport(
                    continuity_score=float(
                        getattr(current_continuity, "continuity_score", 0.0) or 0.0
                    ),
                    summary=f"AI护栏定向修复，第 {index}/{len(tickets)} 张 repair ticket",
                    issues=list(ticket_issues),
                ),
                chapter_outline=bundle.chapter_outline,
                style="",
                style_profile=getattr(bundle, "style_profile", None),
                must_fix_issues=ticket_issues,
                memory_context={"repair_attempt_guidance": _repair_attempt_guidance},
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            ),
        )
        if not repair_result.applied or repair_result.revised_text == current:
            last_repair_plan = repair_result.repair_plan
            continue

        candidate = repair_result.revised_text
        recheck_runner = SimpleNamespace(
            _router=context.router,
            _builder=context.builder,
            _settings=context.settings,
        )

        async def _check_candidate_guard(
            *,
            _runner: Any = recheck_runner,
            _candidate: str = candidate,
        ) -> dict[str, Any]:
            return cast(
                dict[str, Any],
                await check_guard_constraint_compliance(
                    runner=_runner,
                    bundle=bundle,
                    packet=packet,
                    current_text=_candidate,
                    chapter_number=chapter_number,
                ),
            )

        async def _check_candidate_continuity(
            *,
            _candidate: str = candidate,
            _plan: Any = safe_chapter_plan,
        ) -> Any:
            return await run_continuity_recheck(
                services=context,
                chapter_number=chapter_number,
                current_text=_candidate,
                packet=packet,
                bridge=chapter_bridge,
                plan=_plan,
                trace=trace,
                pov_switch=getattr(getattr(bundle, "chapter_outline", None), "pov_switch", False),
                project_path=getattr(bundle.layout, "root", None),
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            )

        async def _check_candidate_alignment(
            *,
            _candidate: str = candidate,
            _plan: Any = safe_chapter_plan,
        ) -> Any:
            return await run_alignment_check(
                services=context,
                chapter_outline=bundle.chapter_outline,
                chapter_plan=_plan,
                current_text=_candidate,
                trace=trace,
            )

        raw_after_guard, raw_after_continuity, raw_after_alignment = await asyncio.gather(
            _check_candidate_guard(),
            _check_candidate_continuity(),
            _check_candidate_alignment(),
            return_exceptions=True,
        )
        _raise_if_parallel_recheck_failed(
            raw_after_guard,
            raw_after_continuity,
            raw_after_alignment,
        )
        after_guard = cast(dict[str, Any], raw_after_guard)
        after_continuity = cast(ContinuityReport, raw_after_continuity)
        after_alignment = cast(AlignmentReport, raw_after_alignment)
        regression_reason = _guard_ticket_recheck_regressed(
            before_guard=current_guard_report,
            after_guard=after_guard,
            before_continuity=current_continuity,
            after_continuity=after_continuity,
            before_alignment=current_alignment,
            after_alignment=after_alignment,
        )
        if regression_reason:
            _logger.warning(
                "guard_ticket_rolled_back | chapter=%d | ticket=%s | index=%d | reason=%s",
                chapter_number,
                ticket.ticket_id or "",
                index,
                regression_reason,
            )
            last_repair_plan = repair_result.repair_plan
            continue

        current = candidate
        current_guard_report = after_guard
        current_continuity = after_continuity
        current_alignment = after_alignment
        last_repair_plan = repair_result.repair_plan
        applied_any = True

    if anchored_count == 0:
        _logger.warning(
            "guard_ticket_repair_skipped_unanchored | chapter=%d | tickets=%d",
            chapter_number,
            len(tickets),
        )
    return current, last_repair_plan, applied_any


def _refresh_guard_warnings(
    warnings: tuple[str, ...] | list[str],
    guard_compliance_report: dict[str, Any] | None,
) -> tuple[str, ...]:
    filtered = [
        str(item)
        for item in (warnings or ())
        if str(item).strip()
        and not str(item).startswith(_GUARD_WARNING_PREFIX)
        and not str(item).startswith(_GUARD_INCOMPLETE_WARNING_PREFIX)
    ]
    if guard_compliance_report is not None:
        incomplete_warning = guard_report_incomplete_warning(guard_compliance_report)
        if incomplete_warning:
            filtered.append(incomplete_warning)
        if guard_report_has_actionable_low_compliance(guard_compliance_report):
            filtered.append(f"{_GUARD_WARNING_PREFIX} {guard_compliance_report.get('summary', '')}")
    return tuple(filtered)


async def _refresh_guard_compliance_state(
    *,
    runner: Any,
    bundle: LongProjectBundle,
    packet: Any,
    pending: PendingChapterReviewState,
    current_text: str,
    chapter_number: int,
) -> PendingChapterReviewState:
    if pending.guard_compliance_report is None and not pending.repair_tickets:
        return pending
    # Secondary guard: if the existing report was already generated from the same
    # text (identified by the internal _text_hash tag), skip the model call.
    _current_hash = source_text_hash(current_text)
    if (
        pending.guard_compliance_report is not None
        and pending.guard_compliance_report.get("_text_hash") == _current_hash
    ):
        return pending
    try:
        guard_compliance_report = await check_guard_constraint_compliance(
            runner=runner,
            bundle=bundle,
            packet=packet,
            current_text=current_text,
            chapter_number=chapter_number,
        )
        review_findings, repair_tickets = attach_guard_repair_metadata(
            guard_compliance_report,
            chapter_number=chapter_number,
            current_text=current_text,
        )
        previous_tickets = [
            ticket
            for ticket in tuple(pending.repair_tickets or ())
            if getattr(ticket, "source_module", "") == "guard_constraint_compliance"
        ]
        verification_results = build_ticket_verification_results(
            list(previous_tickets),
            remaining_issues=list(review_findings),
            current_text=current_text,
            applied=_current_hash != source_text_hash(pending.current_text),
            metadata={
                "chapter_number": chapter_number,
                "repair_dimension": "guard",
            },
        )
        if verification_results:
            guard_compliance_report["verification_results"] = [
                result.model_dump(mode="json") for result in verification_results
            ]
    except Exception as exc:
        _logger.warning(
            "guard_constraint_recheck_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return pending
    # Tag the new report with the source text hash so future calls can detect
    # an unchanged-text situation without re-running the model.
    guard_compliance_report["_text_hash"] = _current_hash
    return dataclasses.replace(
        pending,
        guard_compliance_report=guard_compliance_report,
        review_findings=tuple(review_findings),
        repair_tickets=tuple(repair_tickets),
        warnings=_refresh_guard_warnings(pending.warnings, guard_compliance_report),
    )


def _format_consistency_replan_notes(existing_notes: str, violations: list[str]) -> str:
    """Merge consistency violations into a concise re-plan note block."""
    note_text = (existing_notes or "").strip()
    clean_violations = [item.strip() for item in violations if str(item).strip()]
    if not clean_violations:
        return note_text
    lines = "\n".join(f"- {item}" for item in clean_violations[:5])
    block = f"【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n{lines}"
    if note_text:
        return f"{note_text}\n\n{block}"
    return block


def _clear_memory_progress_callback(memory_ctx: Any | None) -> None:
    if memory_ctx is None:
        return
    set_callback = getattr(memory_ctx, "set_progress_callback", None)
    if not callable(set_callback):
        return
    try:
        set_callback(None)
    except Exception as exc:
        _logger.debug("Failed to clear memory progress callback: %s", exc)


def _bind_memory_progress_callback(
    *,
    memory_ctx: Any | None,
    on_step_progress: Any = None,
    chapter_number: int,
) -> None:
    """Forward memory stage progress to UI callbacks."""
    if memory_ctx is None or on_step_progress is None:
        return

    def _memory_progress_callback(stage: str, data: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "chapter": data.get("chapter", chapter_number),
            **{k: v for k, v in data.items() if k != "chapter"},
        }
        if stage in {
            "indexing_complete",
            "summary_generated",
            "motifs_extracted",
            "motifs_completed",
        }:
            try:
                payload["memory_status"] = memory_ctx.get_memory_status_for_ui()
            except Exception as exc:
                _logger.debug("Failed to get memory status for UI: %s", exc)
        on_step_progress(f"memory_{stage}", payload)

    set_callback = getattr(memory_ctx, "set_progress_callback", None)
    if callable(set_callback):
        set_callback(_memory_progress_callback)


async def _create_runner_with_memory(
    runtime: RuntimeServices,
    *,
    project_id: str,
    chapter_number: int,
    writing_mode: str | None = None,
    on_step_progress: Any = None,
) -> tuple[Any, Any | None]:
    """Create a ChapterRunner wired with MemoryContext when available."""
    memory_ctx = None
    get_memory_context = getattr(runtime, "get_memory_context", None)
    if callable(get_memory_context):
        memory_ctx = await get_memory_context(
            project_id=project_id,
            storage=runtime.storage,
        )
    await warm_start_first_chapter_memory(
        memory_ctx,
        project_id=project_id,
        chapter_number=chapter_number,
    )
    _bind_memory_progress_callback(
        memory_ctx=memory_ctx,
        on_step_progress=on_step_progress,
        chapter_number=chapter_number,
    )
    runner = runtime.chapter_runner(
        writing_mode=writing_mode,
        on_step_progress=on_step_progress,
        memory_context=memory_ctx,
    )
    return runner, memory_ctx


async def _acquire_session_memory_context(
    runtime: RuntimeServices,
    *,
    project_id: str,
) -> tuple[Any | None, bool]:
    """Return a memory context and whether the caller owns a runtime lease."""
    storage = getattr(runtime, "storage", None)
    acquire_memory_context = getattr(runtime, "acquire_memory_context", None)
    if callable(acquire_memory_context):
        memory_ctx = await acquire_memory_context(
            project_id=project_id,
            storage=storage,
        )
        return memory_ctx, memory_ctx is not None

    get_memory_context = getattr(runtime, "get_memory_context", None)
    if callable(get_memory_context):
        return await get_memory_context(project_id=project_id, storage=storage), False
    return None, False


def _release_session_memory_context_lease(
    runtime: RuntimeServices,
    *,
    project_id: str,
) -> None:
    release_lease = getattr(runtime, "release_memory_context_lease", None)
    if not callable(release_lease):
        return
    try:
        release_lease(project_id)
    except Exception as exc:
        _logger.debug("Failed to release memory context lease: %s", exc)


@asynccontextmanager
async def _chapter_runner_memory_lifecycle(
    runtime: RuntimeServices,
    *,
    project_id: str,
    chapter_number: int,
    writing_mode: str | None = None,
    on_step_progress: Any = None,
) -> Any:
    """Create a chapter runner and close the session-scoped memory lifecycle."""
    memory_ctx, lease_acquired = await _acquire_session_memory_context(
        runtime,
        project_id=project_id,
    )
    await warm_start_first_chapter_memory(
        memory_ctx,
        project_id=project_id,
        chapter_number=chapter_number,
    )
    _bind_memory_progress_callback(
        memory_ctx=memory_ctx,
        on_step_progress=on_step_progress,
        chapter_number=chapter_number,
    )
    runner = runtime.chapter_runner(
        writing_mode=writing_mode,
        on_step_progress=on_step_progress,
        memory_context=memory_ctx,
    )
    try:
        yield runner, memory_ctx
    finally:
        _clear_memory_progress_callback(memory_ctx)
        if lease_acquired:
            _release_session_memory_context_lease(runtime, project_id=project_id)


async def _index_finalized_chapter_memory(
    *,
    memory_ctx: Any | None,
    storage: Any | None = None,
    project_id: str | None = None,
    chapter_number: int,
    chapter_result: Any,
    on_step: Any = None,
) -> None:
    """Compatibility entrypoint delegating to the shared memory phase owner."""
    if memory_ctx is None or chapter_result is None or storage is None or not project_id:
        return
    chapter_text = str(getattr(chapter_result, "text", "") or "")
    if not chapter_text.strip():
        return
    creative_report = getattr(chapter_result, "creative_report", None)
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    await finalize_chapter_memory_phase(
        storage=storage,
        layout=layout,
        memory_context=memory_ctx,
        chapter_number=chapter_number,
        chapter_text=chapter_text,
        creative_report_text=str(getattr(creative_report, "summary", "") or ""),
        chapter_result=chapter_result,
        on_step=on_step if callable(on_step) else None,
    )


async def prepare_plan_checkpoint(
    runtime: RuntimeServices,
    request: PrepareChapterRequest,
    *,
    on_step_progress: Any = None,
    replan_history: list[Any] | None = None,
    replan_context: Any | None = None,
) -> PrepareChapterResponse:
    _memory_lifecycle = _chapter_runner_memory_lifecycle(
        runtime,
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        writing_mode=request.writing_mode,
        on_step_progress=on_step_progress,
    )
    runner, memory_ctx = await _memory_lifecycle.__aenter__()
    try:
        context = runner.create_execution_context()
        context.on_step(
            "writing_mode",
            {"chapter": request.chapter_number, "writing_mode": context.config.writing_mode},
        )
        await ensure_init_readiness_for_existing_project(runner, project_id=request.project_id)
        bundle = await prepare_long_project(
            storage=runtime.storage,
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            force_regenerate=request.force,
        )
        # Attach replan context to the bundle so the planning stage can
        # inject it into PlanInput for the planner LLM to see.
        if replan_context is not None:
            bundle.replan_context = replan_context
        await compensate_memory_gap(
            runner.create_execution_context(), request.project_id, request.chapter_number
        )
        rewrite_plan = build_rewrite_strategy_plan(
            storage=runtime.storage,
            layout=bundle.layout,
            chapter_number=request.chapter_number,
            force=request.force,
            requested_strategy=request.rewrite_strategy,
        )
        effective_notes = notes_with_rewrite_strategy(request.notes, rewrite_plan)
        base_chapter_goal = str(getattr(bundle.chapter_outline, "goal", "") or "")
        current_notes = effective_notes
        apply_notes_to_bundle(bundle, current_notes)
        clear_session_state(bundle, request.chapter_number)

        trace = PipelineTrace()
        introduced_characters: list[str] = []
        context_settings = getattr(context, "settings", None)
        if context_settings is not None and getattr(
            context_settings,
            "auto_introduce_characters",
            True,
        ):
            try:
                introduced_characters = await introduce_new_characters(
                    router=context.router,
                    builder=context.builder,
                    storage=context.storage,
                    settings=context_settings,
                    bundle=bundle,
                    chapter_number=request.chapter_number,
                    trace=trace,
                    on_step=context.on_step,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "introduce_new_characters 在章节工作室第 %d 章中遇到异常（已跳过）：%s",
                    request.chapter_number,
                    exc,
                )

        memory = StoryMemoryManager(
            storage=runner._storage,
            retriever=runner._retriever,
            memory_context=memory_ctx,
        )
        should_persist_rewrite_context = (
            request.force
            or rewrite_plan.effective_strategy != "sequential"
            or rewrite_plan.requested_strategy != "auto"
        )
        if should_persist_rewrite_context and rewrite_plan.metadata:
            runtime.storage.save_json(
                rewrite_context_path(bundle.layout, request.chapter_number),
                rewrite_plan.metadata,
            )
            context.on_step("rewrite_strategy", rewrite_plan.metadata)
        max_prepare_replans = int(
            getattr(getattr(context, "settings", None), "long_max_consistency_replans", 2) or 0
        )
        prepare_replan_count = 0
        while True:
            try:
                prepared = await prepare_chapter_plan(
                    context,
                    bundle=bundle,
                    chapter_number=request.chapter_number,
                    trace=trace,
                    memory=memory,
                )
                break
            except ConsistencyViolationError as exc:
                # Planning may discover an invalid source artifact before it
                # produces Bridge/Plan.  Only a failure explicitly owned by
                # PLAN may retry here; source/text/manual failures must retain
                # their narrow recovery scope instead of spending another
                # planning attempt on unchanged inputs.
                if not exc.replan_target.permits_plan_replan:
                    raise
                violations = list(getattr(exc, "violations", []) or [])
                prior_replans = current_notes.count("【自动修复提示】")
                if prior_replans >= max_prepare_replans:
                    raise
                prepare_replan_count += 1
                context.on_step(
                    "consistency_replan",
                    {
                        "chapter": request.chapter_number,
                        "stage": "prepare",
                        "violation_count": len(violations),
                        "violations": violations[:5],
                    },
                )
                current_notes = _format_consistency_replan_notes(current_notes, violations)
                bundle.chapter_outline.goal = base_chapter_goal
                apply_notes_to_bundle(bundle, current_notes)
        checkpoint = build_plan_checkpoint(
            bundle,
            prepared.packet,
            prepared.bridge,
            prepared.plan,
            scene_plan_validation_report=prepared.scene_plan_validation_report,
        )
        if prepare_replan_count and checkpoint is not None:
            violation_summary = "；".join(
                line.removeprefix("- ").strip()
                for line in current_notes.splitlines()
                if line.strip().startswith("- ")
            )
            checkpoint.prompt = (
                f"⚠️ 上一轮桥接/方案未通过上游一致性校验"
                f"（{violation_summary or '桥接或方案承接不完整'}），已自动重新规划。"
                f"第 {prepare_replan_count}/{max_prepare_replans} 次重试。\n"
                "请确认方案后继续写作。"
            )
        runtime.storage.save_json(
            bundle.layout.chapter_checkpoint_path(request.chapter_number),
            checkpoint.model_dump(mode="json"),
        )
        save_plan_session_state(
            storage=runtime.storage,
            bundle=bundle,
            chapter_number=request.chapter_number,
            checkpoint_id=checkpoint.checkpoint_id,
            project_id=request.project_id,
            canon_watermark=bundle.canon_state.current_chapter,
            notes=current_notes,
            rewrite_strategy=rewrite_plan.effective_strategy,
            writing_mode=context.config.writing_mode,
            trace_summary=trace.summary(),
            introduced_characters=introduced_characters,
            replan_history=replan_history,
        )
        context.on_step("plan_checkpoint", checkpoint.model_dump(mode="json"))
        return build_prepare_response(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            checkpoint=checkpoint,
        )
    finally:
        # Clean up transient bundle state without masking earlier failures.
        if replan_context is not None and "bundle" in locals():
            bundle.replan_context = None
        if "bundle" in locals():
            await close_long_project(bundle)
        await _memory_lifecycle.__aexit__(None, None, None)


async def resolve_plan_checkpoint(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    *,
    bundle: LongProjectBundle,
    session_state: PlanCheckpointSessionState,
    on_step_progress: Any = None,
) -> ChapterSessionResult:
    if request.option_id in {"regenerate_plan", "regenerate_plan_with_notes"}:
        prepare_response = await prepare_plan_checkpoint(
            runtime,
            PrepareChapterRequest(
                project_id=request.project_id,
                chapter_number=request.chapter_number,
                force=False,
                notes=request.notes if request.option_id == "regenerate_plan_with_notes" else "",
                rewrite_strategy=session_state.rewrite_strategy,
                writing_mode=session_state.writing_mode,
            ),
            on_step_progress=on_step_progress,
        )
        return build_session_result_from_prepare(
            response=prepare_response,
            applied_option_id=request.option_id,
        )
    if request.option_id == "switch_to_whole_chapter":
        prepare_response = await prepare_plan_checkpoint(
            runtime,
            PrepareChapterRequest(
                project_id=request.project_id,
                chapter_number=request.chapter_number,
                force=False,
                notes=request.notes,
                rewrite_strategy=session_state.rewrite_strategy,
                writing_mode="whole_chapter",
            ),
            on_step_progress=on_step_progress,
        )
        return build_session_result_from_prepare(
            response=prepare_response,
            applied_option_id=request.option_id,
        )
    if request.option_id not in {"write_now", "edit_plan_and_write", "resume_from_progress"}:
        raise ValueError(f"未知的 plan checkpoint 操作：{request.option_id}")

    # Load review progress for resume-from-failure.
    # Transparent resume: when option_id is "write_now" or "edit_plan_and_write"
    # and a review_progress checkpoint exists on disk (from a prior partial run
    # with the same plan), automatically resume from there instead of starting
    # from scratch.  This ensures the "写这一章" button after a network failure
    # picks up where the pipeline left off without requiring the user to
    # explicitly click "从断点恢复".
    # Note: clear_session_state() deletes review_progress when a new plan is
    # prepared, so stale cross-plan checkpoints cannot interfere here.
    _resume_progress = None
    artifact_loader = _chapter_session_artifacts(
        runtime,
        request,
        source="resolve_plan_checkpoint",
    )
    if request.option_id == "resume_from_progress":
        _resume_progress = load_review_progress(
            runtime.storage,
            bundle.layout,
            request.chapter_number,
            artifact_loader=artifact_loader,
        )
        if _resume_progress is None:
            raise ValueError("没有可恢复的断点进度，请重新执行")
    elif request.option_id in {"write_now", "edit_plan_and_write"}:
        # Auto-detect: transparently resume if partial progress exists
        try:
            _auto_progress = load_review_progress(
                runtime.storage,
                bundle.layout,
                request.chapter_number,
                artifact_loader=artifact_loader,
            )
            if _auto_progress is not None:
                _resume_progress = _auto_progress
                _logger.info(
                    "auto_resume_from_progress | chapter=%d | stage=%s | option=%s",
                    request.chapter_number,
                    _auto_progress.completed_stage,
                    request.option_id,
                )
        except Exception as exc:
            _logger.debug("Could not load auto-resume progress: %s", exc)
            # Proceed fresh if progress cannot be loaded

    _memory_lifecycle = _chapter_runner_memory_lifecycle(
        runtime,
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        writing_mode=session_state.writing_mode,
        on_step_progress=on_step_progress,
    )
    runner, _memory_ctx = await _memory_lifecycle.__aenter__()
    try:
        context = runner.create_execution_context()
        trace = PipelineTrace()
        prepared = await load_prepared_chapter_artifacts(
            context,
            bundle=bundle,
            chapter_number=request.chapter_number,
        )
        # Emit an immediate step so the UI never gets stuck showing "任务启动中"
        # while waiting for the draft LLM call (which can take several minutes).
        context.on_step(request.option_id, {"chapter_number": request.chapter_number})
        try:
            review = await review_chapter_draft(
                context,
                prepared=prepared,
                trace=trace,
                include_evaluation=True,
                resume_progress=_resume_progress,
            )
        except ConsistencyViolationError as exc:
            # Plan regeneration is allowed only when the failure explicitly
            # invalidates the plan. Text-stage failures retain their checkpoint
            # and must never silently restart Bridge + Plan + DRAFT.
            if not exc.replan_target.permits_plan_replan:
                _logger.warning(
                    "chapter %d: %s failure (recovery_target=%s, not plan-replannable) | "
                    "failed_stage=%s | issues=%s",
                    request.chapter_number,
                    exc.violation_kind,
                    exc.replan_target,
                    exc.failed_stage,
                    exc.violations[:3],
                )
                raise

            # Keep the chapter in the same session flow: regenerate plan with
            # violation hints instead of surfacing a hard FAILED task state.
            # Safety net: limit replans to prevent infinite loops.  Count
            # from the persistent ``replan_history`` in session_state (which
            # survives across Desktop runs), falling back to the legacy
            # notes-based count for backward compatibility.
            _max_replans = context.settings.long_max_consistency_replans
            _persistent_replans = len(getattr(session_state, "replan_history", []) or [])
            _notes_replans = (request.notes or "").count("【自动修复提示】")
            _prior_replans = max(_persistent_replans, _notes_replans)
            violations = list(getattr(exc, "violations", []) or [])
            if _prior_replans >= _max_replans:
                _logger.warning(
                    "chapter %d: consistency_replan limit reached (%d/%d attempts); "
                    "re-raising to surface as FAILED job instead of infinite loop",
                    request.chapter_number,
                    _prior_replans,
                    _max_replans,
                )
                raise
            context.on_step(
                "consistency_replan",
                {
                    "chapter": request.chapter_number,
                    "violation_count": len(violations),
                    "violations": violations[:5],
                },
            )
            # Build structured context from the history before appending the
            # current failure, so attempt numbers align with the UI.
            from novel_forge.pipeline.long.replan_context import build_replan_context
            from novel_forge.workspace.sessions.chapter_session_state import ReplanHistoryEntry

            _previous_plan = getattr(prepared, "plan", None) if "prepared" in dir() else None
            _existing_history = list(getattr(session_state, "replan_history", []) or [])
            _replan_context = build_replan_context(
                exc=exc,
                previous_plan=_previous_plan,
                replan_history=_existing_history,
            )
            # Append a ReplanHistoryEntry so the count persists across runs.
            _new_history_entry = ReplanHistoryEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                attempt_number=_replan_context.attempt_number,
                failed_stage=getattr(exc, "failed_stage", ""),
                failure_kind=getattr(exc, "violation_kind", ""),
                violations=violations[:10],
                actionable_guidance=list(_replan_context.actionable_guidance),
            )
            _updated_history = [*_existing_history, _new_history_entry]
            prepare_response = await prepare_plan_checkpoint(
                runtime,
                PrepareChapterRequest(
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    force=False,
                    notes=_format_consistency_replan_notes(request.notes, violations),
                    rewrite_strategy=session_state.rewrite_strategy,
                    writing_mode=session_state.writing_mode,
                ),
                on_step_progress=on_step_progress,
                replan_history=_updated_history,
                replan_context=_replan_context,
            )
            # Enrich the checkpoint prompt so the UI clearly shows the replan reason.
            if prepare_response.checkpoint is not None:
                _violation_summary = "；".join(violations[:3]) if violations else "一致性校验未通过"
                prepare_response.checkpoint.prompt = (
                    f"⚠️ 上一轮写作未通过质量校验（{_violation_summary}），"
                    f"已自动重新规划。第 {_prior_replans + 1}/{_max_replans} 次重试。\n"
                    f"确认方案后将重新生成正文。"
                )
            return build_session_result_from_prepare(
                response=prepare_response,
                applied_option_id=request.option_id,
            )
    finally:
        await _memory_lifecycle.__aexit__(None, None, None)
    runtime.storage.save_text(
        bundle.layout.chapter_review_draft_path(request.chapter_number),
        review.current_text,
    )
    eval_report = review.eval_report
    if eval_report is None:
        raise RuntimeError("review stage must produce eval_report for guard checkpoint")

    guard_decision: PlotGuardDecision | None = None
    if (
        context.settings.long_plot_guard_mode == "ai_judge"
        and review.outcome.creative_report is not None
    ):
        result_proxy = SimpleNamespace(
            alignment_report=review.alignment_report,
            continuity_report=review.continuity_report,
            causal_report=review.causal_report,
            eval_report=eval_report,
        )
        guard_decision = await _run_plot_guard_judge(
            chapter_number=request.chapter_number,
            report=review.outcome.creative_report,
            result=result_proxy,
            layout=bundle.layout,
            storage=runtime.storage,
            router=context.router,
            builder=context.builder,
            settings=context.settings,
        )
        runtime.storage.save_json(
            bundle.layout.guard_report_path(request.chapter_number),
            {
                "chapter_number": request.chapter_number,
                "source_text_hash": source_text_hash(review.current_text),
                "decision": guard_decision.model_dump(mode="json"),
            },
        )

    guard_checkpoint = build_guard_checkpoint(
        bundle,
        request.chapter_number,
        current_text=review.current_text,
        alignment_report=review.alignment_report,
        continuity_report=review.continuity_report,
        eval_report=eval_report,
        causal_report=review.causal_report,
        guard_decision=guard_decision,
        reading_power_report=review.reading_power_report,
        chapter_repair_report=review.chapter_repair_report,
        warnings=tuple(review.warnings or ()),
        repair_tickets=tuple(review.repair_tickets or ()),
    )
    runtime.storage.save_json(
        bundle.layout.chapter_checkpoint_path(request.chapter_number),
        guard_checkpoint.model_dump(mode="json"),
    )
    save_guard_session_state(
        storage=runtime.storage,
        bundle=bundle,
        chapter_number=request.chapter_number,
        checkpoint_id=guard_checkpoint.checkpoint_id,
        project_id=request.project_id,
        canon_watermark=bundle.canon_state.current_chapter,
        notes=request.notes,
        rewrite_strategy=session_state.rewrite_strategy,
        writing_mode=session_state.writing_mode,
        introduced_characters=session_state.introduced_characters,
        replan_history=session_state.replan_history,
        pending_state=PendingChapterReviewState(
            current_text=review.current_text,
            performed_edits=review.performed_edits,
            outcome=review.outcome,
            alignment_report=review.alignment_report,
            chapter_repair_report=review.chapter_repair_report,
            causal_report=review.causal_report,
            continuity_report=review.continuity_report,
            repair_plan=review.repair_plan,
            eval_report=eval_report,
            guard_decision=guard_decision,
            warnings=tuple(review.warnings or []),
            reading_power_report=review.reading_power_report,
            guard_compliance_report=review.guard_compliance_report,
            review_findings=tuple(review.review_findings or ()),
            repair_tickets=tuple(review.repair_tickets or ()),
        ),
    )
    context.on_step("guard_checkpoint", guard_checkpoint.model_dump(mode="json"))
    return build_session_checkpoint_result(
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        checkpoint=guard_checkpoint,
        applied_option_id=request.option_id,
        word_count=display_word_count(review.current_text),
        overall_score=eval_report.overall_score,
        continuity_score=review.continuity_report.continuity_score,
        continuity_issue_count=_open_issue_count(review.continuity_report.issues),
        bridge_summary=review.prepared.bridge.bridge_summary,
        chapter_exit_summary=summarize_exit_state(review.outcome),
        preview_text=review.current_text,
        guard_decision=guard_decision,
        metadata={
            "alignment_score": review.alignment_report.alignment_score,
            "causal_score": (
                review.causal_report.causal_score if review.causal_report is not None else None
            ),
            "causal_issue_count": (
                len(review.causal_report.issues) if review.causal_report is not None else 0
            ),
            "warnings": list(review.warnings or []),
        },
    )


async def resolve_guard_checkpoint(
    runtime: RuntimeServices,
    request: ResolveChapterCheckpointRequest,
    *,
    bundle: LongProjectBundle,
    session_state: GuardCheckpointSessionState,
    checkpoint: DecisionCheckpoint,
    on_step_progress: Any = None,
) -> ChapterSessionResult:
    allowed_option_ids = {option.option_id for option in checkpoint.options}
    if request.option_id not in allowed_option_ids:
        raise ValueError(f"当前 guard checkpoint 不允许操作：{request.option_id}")

    if request.option_id == "pause_for_human":
        pending = deserialize_pending_result(session_state.pending_result)
        return build_paused_session_result(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            checkpoint=checkpoint,
            applied_option_id=request.option_id,
            guard_decision=pending.guard_decision,
        )

    if request.option_id not in {
        "accept_and_finalize",
        "apply_repairs_and_finalize",
        "adjust_outline_and_finalize",
    }:
        raise ValueError(f"未知的 guard checkpoint 操作：{request.option_id}")

    # Emit option_id as first step so the progress bar immediately advances to
    # the "归档决策" milestone instead of staying at the misleading 3% start.
    if callable(on_step_progress):
        on_step_progress(request.option_id, {"chapter": request.chapter_number})

    _memory_lifecycle = _chapter_runner_memory_lifecycle(
        runtime,
        project_id=request.project_id,
        chapter_number=request.chapter_number,
        writing_mode=session_state.writing_mode,
        on_step_progress=on_step_progress,
    )
    runner, memory_ctx = await _memory_lifecycle.__aenter__()
    try:
        context = runner.create_execution_context()
        trace = PipelineTrace()
        artifact_loader = _chapter_session_artifacts(
            runtime,
            request,
            source="resolve_guard_checkpoint",
        )
        pending = deserialize_pending_result(session_state.pending_result)

        report_data = load_creative_report_payload(
            pending.outcome.creative_report,
            storage=runtime.storage,
            layout=bundle.layout,
            chapter_number=request.chapter_number,
            artifact_loader=artifact_loader,
        )

        # Track whether the chapter text was updated by inline continuity repair
        finalize_text = pending.current_text
        finalize_repair_plan = pending.repair_plan
        finalize_eval_report: Any = pending.eval_report
        # Whether guard ticket repair actually modified the text (used later to
        # decide if a guard-compliance recheck is warranted).
        _guard_repair_applied = False
        _semantic_text_changed_before_finalize = False
        _reports_refreshed_for_finalize_text = False
        ch_num = request.chapter_number
        from novel_forge.core.schemas.continuity import (
            ChapterBridge,
            ChapterPlan,
            ChapterStatePacket,
        )

        packet = artifact_loader.load_model(
            bundle.layout.chapter_state_packet_path(ch_num),
            ChapterStatePacket,
        )
        ch_bridge = artifact_loader.load_model(
            bundle.layout.chapter_bridge_path(ch_num),
            ChapterBridge,
        )
        ch_plan = artifact_loader.load_model(
            bundle.layout.chapter_plan_path(ch_num),
            ChapterPlan,
        )

        _prompt_leak_repair = await repair_confirmed_prompt_leaks_with_patch(
            router=context.router,
            builder=context.builder,
            settings=context.settings,
            trace=trace,
            chapter_number=ch_num,
            current_text=finalize_text,
            chapter_repair_report=pending.chapter_repair_report,
            style_profile=getattr(bundle, "style_profile", None),
            on_step=on_step_progress,
            allow_deterministic_fallback=True,
        )
        if _prompt_leak_repair.applied or _prompt_leak_repair.report_updated:
            _prompt_leak_semantic_changed = (
                _prompt_leak_repair.applied
                and _prompt_leak_repair.text != finalize_text
                and not _prompt_leak_repair.used_deterministic_fallback
            )
            if _prompt_leak_semantic_changed:
                _semantic_text_changed_before_finalize = True
            finalize_text = _prompt_leak_repair.text
            pending = dataclasses.replace(
                pending,
                chapter_repair_report=_prompt_leak_repair.chapter_repair_report,
            )
            finalize_eval_report = None
            save_path = bundle.layout.chapter_path(ch_num)
            if not save_path.exists():
                save_path = bundle.layout.chapter_review_draft_path(ch_num)
            runtime.storage.save_text(save_path, finalize_text)
            if _prompt_leak_repair.chapter_repair_report is not None:
                runtime.storage.save_json(
                    bundle.layout.chapter_repair_report_path(ch_num),
                    _prompt_leak_repair.chapter_repair_report.model_dump(mode="json")
                    if hasattr(_prompt_leak_repair.chapter_repair_report, "model_dump")
                    else _prompt_leak_repair.chapter_repair_report,
                )

        if (
            _semantic_text_changed_before_finalize
            and request.option_id != "apply_repairs_and_finalize"
        ):
            pending = await refresh_pending_reports_for_text(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=ch_bridge,
                plan=ch_plan,
                current_text=finalize_text,
                chapter_number=ch_num,
                trace=trace,
                pending=pending,
                stale_reason="prompt_leak_repair_text_changed",
                alignment_recheck=_alignment_recheck_callback_if_overridden(),
            )
            refreshed_outcome = await extract_and_validate(
                runner,
                bundle,
                packet,
                ch_bridge,
                ch_plan,
                finalize_text,
                ch_num,
                trace,
                pending.continuity_report,
                repair_exhausted=True,
            )
            pending = dataclasses.replace(pending, outcome=refreshed_outcome)
            finalize_eval_report = None
            save_path = bundle.layout.chapter_path(ch_num)
            if not save_path.exists():
                save_path = bundle.layout.chapter_review_draft_path(ch_num)
            runtime.storage.save_text(save_path, finalize_text)
            pending = await _refresh_guard_compliance_state(
                runner=runner,
                bundle=bundle,
                packet=packet,
                pending=pending,
                current_text=finalize_text,
                chapter_number=ch_num,
            )
            _reports_refreshed_for_finalize_text = True

        if request.option_id == "apply_repairs_and_finalize":
            # Emit a dedicated step so the progress bar advances to the
            # "应用修复" node (92%) before the silent repair work begins.
            if callable(on_step_progress):
                on_step_progress("post_guard_repair_start", {"chapter": request.chapter_number})
            _alignment_threshold = normalize_threshold(
                getattr(context.settings, "long_alignment_threshold", 7.0)
            )
            if alignment_requires_repair(
                pending.alignment_report,
                alignment_threshold=_alignment_threshold,
            ):
                # ── Evidence refresh before repair ────────────────────────
                # This retry may have been scheduled via the
                # refresh_before_repair path: the alignment report that the
                # archive gate blocked on is not bound to the current text
                # (missing or mismatched source_text_hash).  Re-run the review
                # against the live text first so the repair operates on bound
                # evidence instead of guessing.  If the refreshed report
                # already passes the threshold, skip the repair entirely and
                # let the verified archive gate finalize the chapter.
                if not _report_is_bound_to_text(pending.alignment_report, finalize_text):
                    if callable(on_step_progress):
                        on_step_progress(
                            "archive_retry_refresh_reports",
                            {"chapter": ch_num, "reason": "evidence_unbound"},
                        )
                    pending = await refresh_pending_reports_for_text(
                        runner=runner,
                        bundle=bundle,
                        packet=packet,
                        bridge=ch_bridge,
                        plan=ch_plan,
                        current_text=finalize_text,
                        chapter_number=ch_num,
                        trace=trace,
                        pending=pending,
                        stale_reason="archive_retry_evidence_unbound",
                        alignment_recheck=_alignment_recheck_callback_if_overridden(),
                    )
                    _reports_refreshed_for_finalize_text = True
                if alignment_requires_repair(
                    pending.alignment_report,
                    alignment_threshold=_alignment_threshold,
                ):
                    finalize_text = await alignment_repair_edit(
                        runner,
                        bundle=bundle,
                        packet=packet,
                        bridge=ch_bridge,
                        plan=ch_plan,
                        current_text=finalize_text,
                        chapter_number=ch_num,
                        alignment_report=pending.alignment_report,
                        trace=trace,
                    )
                    save_path = bundle.layout.chapter_path(ch_num)
                    if not save_path.exists():
                        save_path = bundle.layout.chapter_review_draft_path(ch_num)
                    runtime.storage.save_text(save_path, finalize_text)
                    if callable(on_step_progress):
                        on_step_progress(
                            "alignment_repair_complete",
                            {
                                "chapter": ch_num,
                                "previous_score": round(
                                    float(pending.alignment_report.alignment_score), 2
                                ),
                            },
                        )
            if pending.repair_tickets:
                (
                    finalize_text,
                    _guard_repair_plan,
                    _guard_repair_applied,
                ) = await _run_guard_ticket_repair(
                    context=context,
                    bundle=bundle,
                    packet=packet,
                    chapter_bridge=ch_bridge,
                    chapter_plan=ch_plan,
                    pending=pending,
                    current_text=finalize_text,
                    chapter_number=ch_num,
                    trace=trace,
                )
                if _guard_repair_applied and _guard_repair_plan is not None:
                    finalize_repair_plan = _guard_repair_plan
                if callable(on_step_progress):
                    on_step_progress(
                        "guard_ticket_repair_complete",
                        {
                            "chapter": ch_num,
                            "applied": bool(_guard_repair_applied),
                            "tickets_consumed": len(pending.repair_tickets or ()),
                        },
                    )
            # ── Step 1: Run continuity repair on remaining issues (if any) ──────
            if pending.continuity_report.issues:
                from novel_forge.core.schemas.outline import ChapterOutline
                from novel_forge.pipeline.long.repair import run_continuity_repair
                from novel_forge.pipeline.steps.continuity_repair_step import (
                    ContinuityRepairInput,
                    ContinuityRepairStep,
                )

                ch_outline: ChapterOutline | None = None
                try:
                    outline_raw = artifact_loader.load_json(bundle.layout.outline_path) or {}
                    ch_data = next(
                        (
                            c
                            for c in outline_raw.get("chapters", [])
                            if c.get("chapter_number") == ch_num
                        ),
                        None,
                    )
                    if ch_data:
                        ch_outline = ChapterOutline.model_validate(ch_data)
                except Exception as exc:
                    _logger.debug("Failed to load chapter outline for repair: %s", exc)

                repair_step = ContinuityRepairStep(
                    context.router,
                    context.builder,
                    settings=context.settings,
                    trace=trace,
                    on_step=context.on_step,
                )
                from novel_forge.common.constants import severity_at_least

                _must_fix_sev = (
                    getattr(context.settings, "repair_must_fix_severity", "critical") or "critical"
                ).lower()
                _must_fix_issues = [
                    issue
                    for issue in (pending.continuity_report.issues or [])
                    if _must_fix_sev != "off"
                    and severity_at_least(
                        (getattr(issue, "severity", "") or "").lower(),
                        _must_fix_sev,
                    )
                ]
                repair_payload = ContinuityRepairInput(
                    chapter_number=ch_num,
                    chapter_text=finalize_text,
                    chapter_state_packet=packet,
                    chapter_bridge=ch_bridge,
                    chapter_plan=ch_plan,
                    continuity_report=pending.continuity_report,
                    chapter_outline=ch_outline,
                    style="",
                    style_profile=getattr(bundle, "style_profile", None),
                    must_fix_issues=tuple(_must_fix_issues),
                    chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
                    memory_context=(
                        {
                            "force_patch_only": True,
                            "escalation_note": (
                                "护栏 ticket 已修改正文；后续归档前修复只能做局部补丁，"
                                "不得在补丁失败后升级为全文重写。"
                            ),
                        }
                        if _guard_repair_applied
                        else None
                    ),
                )
                repair_result = await run_continuity_repair(repair_step, repair_payload)
                if repair_result.applied:
                    finalize_text = repair_result.revised_text
                    finalize_repair_plan = repair_result.repair_plan
                    # Persist repaired text so the finalization stage reads the right file
                    save_path = bundle.layout.chapter_path(ch_num)
                    if not save_path.exists():
                        save_path = bundle.layout.chapter_review_draft_path(ch_num)
                    runtime.storage.save_text(save_path, finalize_text)
                    runtime.storage.save_json(
                        bundle.layout.repair_plan_path(ch_num),
                        finalize_repair_plan.model_dump(mode="json"),
                    )
                    # Update snapshot so this repair isn't flagged as a manual edit
                    try:
                        from novel_forge.core.utils.edit_tracker import save_snapshot

                        save_snapshot(save_path, bundle.layout.states_dir, ch_num)
                    except Exception as exc:
                        _logger.warning("Failed to save edit snapshot: %s", exc)

                    # ── Step 1b: Targeted continuity verification after repair ───
                    # This is a point verification, not a full continuity score.
                    # Keep it as a sidecar status and let the later full refresh
                    # write the canonical continuity report/score.
                    try:
                        _prior_cont_issues = [
                            {
                                "issue_type": (getattr(i, "issue_type", "") or "").lower(),
                                "severity": (getattr(i, "severity", "") or "medium").lower(),
                                "location": getattr(i, "rewrite_scope", "") or "",
                                "summary": getattr(i, "summary", "") or "",
                            }
                            for i in (pending.continuity_report.issues or [])
                        ]
                        _must_fix_cont_issues = [
                            i
                            for i in (pending.continuity_report.issues or [])
                            if (i.severity or "").lower() in {"critical", "high", "hard"}
                            and (i.summary or "")
                        ]
                        # packet / ch_bridge / ch_plan already loaded above
                        _recheck_cont_report = await run_targeted_continuity_recheck(
                            runtime=runtime,
                            layout=bundle.layout,
                            chapter_number=ch_num,
                            current_text=finalize_text,
                            packet=packet,
                            bridge=ch_bridge,
                            plan=ch_plan,
                            prior_issues=_prior_cont_issues,
                            must_fix_issues=_must_fix_cont_issues,
                            repair_result=repair_result,
                            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
                            start_step_name=None,
                        )
                        cont_payload = build_continuity_recheck_payload(
                            _recheck_cont_report,
                            issues=list(getattr(_recheck_cont_report, "issues", []) or []),
                            current_text=finalize_text,
                        )
                        cont_payload["pipeline_stage"] = "targeted_repair_verification"
                        cont_payload["verification_status"] = (
                            "resolved"
                            if not getattr(_recheck_cont_report, "issues", [])
                            else "unresolved"
                        )
                        runtime.storage.save_json(
                            bundle.layout.reports_dir
                            / f"chapter_{ch_num:03d}_continuity_verification.json",
                            cont_payload,
                        )
                        if callable(on_step_progress):
                            on_step_progress(
                                "continuity_recheck_after_repair",
                                {
                                    "chapter": ch_num,
                                    "verification_status": cont_payload["verification_status"],
                                    "targeted": True,
                                    "remaining_issues": len(
                                        getattr(_recheck_cont_report, "issues", []) or []
                                    ),
                                },
                            )
                    except Exception as exc:
                        _logger.warning(
                            "continuity_recheck_after_repair_failed | chapter=%d | error=%s",
                            ch_num,
                            exc,
                        )

            # ── Step 2: Apply causal repair if high-priority issues remain ──
            causal_report = pending.causal_report
            if causal_report is not None and context.settings.long_causal_repair_enabled:
                causal_must_fix = _select_causal_must_fix_issues(causal_report, context.settings)
                if causal_must_fix:
                    from novel_forge.pipeline.long.stages.causal_repair import causal_repair_edit

                    causal_link = getattr(ch_bridge, "causal_link", None)
                    causal_link_payload = {}
                    if causal_link is not None:
                        if hasattr(causal_link, "model_dump"):
                            causal_link_payload = causal_link.model_dump(mode="json")
                        elif isinstance(causal_link, dict):
                            causal_link_payload = causal_link

                    # Use the specialized causal_repair_edit (with typed template)
                    finalize_text = await causal_repair_edit(
                        runner,
                        bundle=bundle,
                        packet=packet,
                        bridge=ch_bridge,
                        current_text=finalize_text,
                        chapter_number=ch_num,
                        causal_report=causal_report,
                        trace=trace,
                        must_fix_summaries=[
                            getattr(i, "summary", "")
                            for i in causal_must_fix
                            if getattr(i, "summary", "")
                        ],
                    )

                    # Re-validate with recheck_mode to verify fixes
                    _must_resolve = [
                        getattr(i, "summary", "")
                        for i in causal_must_fix
                        if getattr(i, "summary", "")
                    ]
                    _prior_issues = [
                        {
                            "issue_type": (getattr(i, "issue_type", "") or "").lower(),
                            "severity": (getattr(i, "severity", "") or "medium").lower(),
                            "location": getattr(i, "location", "") or "",
                            "paragraph_start": getattr(i, "paragraph_start", 0) or 0,
                            "paragraph_end": getattr(i, "paragraph_end", 0) or 0,
                            "summary": getattr(i, "summary", "") or "",
                        }
                        for i in causal_must_fix
                    ]
                    _repaired_types = sorted(
                        {
                            (getattr(i, "issue_type", "") or "").lower()
                            for i in causal_must_fix
                            if (getattr(i, "issue_type", "") or "").strip()
                        }
                    )
                    from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

                    _patch_only_types = set(CausalRepairStep.patch_only_issue_types())
                    _patch_only_repair = bool(_repaired_types) and all(
                        t in _patch_only_types for t in _repaired_types
                    )
                    _recheck_causal_report = await run_causal_recheck(
                        services=context,
                        chapter_number=ch_num,
                        current_text=finalize_text,
                        bridge=ch_bridge,
                        causal_link=causal_link_payload,
                        trace=trace,
                        must_resolve_summaries=_must_resolve,
                        recheck_strategy=cast(
                            Any,
                            getattr(
                                context.settings,
                                "recheck_strategy",
                                "targeted_with_global_guard",
                            ),
                        ),
                        prior_issues=_prior_issues,
                        repaired_issue_types=_repaired_types,
                        patch_only_repair=_patch_only_repair,
                    )
                    # PendingChapterReviewState is a frozen dataclass; use replace() to update.
                    pending = dataclasses.replace(pending, causal_report=_recheck_causal_report)
                    causal_payload = build_causal_recheck_payload(
                        _recheck_causal_report,
                        issues=list(getattr(_recheck_causal_report, "issues", []) or []),
                        current_text=finalize_text,
                    )
                    runtime.storage.save_json(
                        bundle.layout.chapter_causal_report_path(ch_num),
                        causal_payload,
                    )

                    save_path = bundle.layout.chapter_path(ch_num)
                    if not save_path.exists():
                        save_path = bundle.layout.chapter_review_draft_path(ch_num)
                    runtime.storage.save_text(save_path, finalize_text)

            # ── Step 2b: Post-repair cleanup + state resync ────────────────────
            # Keep checkpoint finalize flow aligned with the main pipeline:
            # 1) run dedup / self-repetition / pronoun cleanup on repaired text
            # 2) refresh alignment/continuity/causal reports against latest text
            # 3) re-extract outcome and force re-eval to avoid stale state reuse
            if finalize_text != pending.current_text:
                finalize_text = run_final_dedup(
                    runner,
                    finalize_text,
                    getattr(packet, "previous_chapter_ending", "") or "",
                )
                finalize_text, _ = await asyncio.to_thread(
                    run_self_repetition_check,
                    runner,
                    finalize_text,
                )
                finalize_text, _ = await run_pronoun_check(
                    runner,
                    bundle,
                    packet,
                    finalize_text,
                    ch_num,
                    trace,
                    chapter_plan=ch_plan,
                )
                before_refresh_pending = pending
                pending = await refresh_pending_reports_for_text(
                    runner=runner,
                    bundle=bundle,
                    packet=packet,
                    bridge=ch_bridge,
                    plan=ch_plan,
                    current_text=finalize_text,
                    chapter_number=ch_num,
                    trace=trace,
                    pending=pending,
                    stale_reason="checkpoint_finalize_text_changed",
                    alignment_recheck=_alignment_recheck_callback_if_overridden(),
                )
                refreshed_alignment = pending.alignment_report
                refreshed_continuity = pending.continuity_report
                if _guard_repair_applied:
                    (
                        finalize_text,
                        refreshed_alignment,
                        refreshed_continuity,
                        _,
                        _guard_alignment_retry_reason,
                    ) = await _repair_or_raise_guard_ticket_alignment_regression(
                        context=context,
                        runtime=runtime,
                        runner=runner,
                        bundle=bundle,
                        packet=packet,
                        chapter_bridge=ch_bridge,
                        chapter_plan=ch_plan,
                        pending=before_refresh_pending,
                        current_text=finalize_text,
                        alignment_report=refreshed_alignment,
                        continuity_report=refreshed_continuity,
                        chapter_number=ch_num,
                        trace=trace,
                        on_step_progress=on_step_progress,
                    )
                    if _guard_alignment_retry_reason:
                        return _build_guard_alignment_retry_checkpoint_result(
                            runtime=runtime,
                            request=request,
                            bundle=bundle,
                            session_state=session_state,
                            pending=pending,
                            current_text=finalize_text,
                            alignment_report=refreshed_alignment,
                            continuity_report=refreshed_continuity,
                            reason=_guard_alignment_retry_reason,
                        )
                pending = dataclasses.replace(
                    pending,
                    alignment_report=refreshed_alignment,
                    continuity_report=refreshed_continuity,
                )

                # ── Step 2b-repair: attempt one continuity repair round if the
                #    fresh eval found high-severity issues introduced by post-
                #    guard edits (causal repair / dedup / pronoun cleanup). ──────
                from novel_forge.common.constants import severity_at_least as _sev_ge

                _must_fix_sev_2b = (
                    getattr(context.settings, "repair_must_fix_severity", "critical") or "critical"
                ).lower()
                _must_fix_2b = [
                    issue
                    for issue in (refreshed_continuity.issues or [])
                    if _must_fix_sev_2b != "off"
                    and _sev_ge(
                        (getattr(issue, "severity", "") or "").lower(),
                        _must_fix_sev_2b,
                    )
                ]
                if _must_fix_2b:
                    _repair_result_2b = None
                    try:
                        from novel_forge.pipeline.long.repair import run_continuity_repair
                        from novel_forge.pipeline.steps.continuity_repair_step import (
                            ContinuityRepairInput,
                            ContinuityRepairStep,
                        )

                        _repair_step_2b = ContinuityRepairStep(
                            context.router,
                            context.builder,
                            settings=context.settings,
                            trace=trace,
                            on_step=context.on_step,
                        )
                        _repair_result_2b = await run_continuity_repair(
                            _repair_step_2b,
                            ContinuityRepairInput(
                                chapter_number=ch_num,
                                chapter_text=finalize_text,
                                chapter_state_packet=packet,
                                chapter_bridge=ch_bridge,
                                chapter_plan=ch_plan,
                                continuity_report=refreshed_continuity,
                                chapter_outline=None,
                                style="",
                                style_profile=getattr(bundle, "style_profile", None),
                                must_fix_issues=tuple(_must_fix_2b),
                                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
                                memory_context=(
                                    {
                                        "force_patch_only": True,
                                        "escalation_note": (
                                            "护栏 ticket 已参与本轮归档修复；后续补救只能做局部补丁，"
                                            "不得在补丁失败后升级为全文重写。"
                                        ),
                                    }
                                    if _guard_repair_applied
                                    else None
                                ),
                            ),
                        )
                    except Exception as exc:
                        _logger.warning(
                            "step_2b_continuity_repair_failed | chapter=%d | error=%s",
                            ch_num,
                            exc,
                        )
                        _repair_result_2b = None

                    if _repair_result_2b is not None and _repair_result_2b.applied:
                        finalize_text = _repair_result_2b.revised_text
                        try:
                            before_step_2b_refresh_pending = pending
                            pending = await refresh_pending_reports_for_text(
                                runner=runner,
                                bundle=bundle,
                                packet=packet,
                                bridge=ch_bridge,
                                plan=ch_plan,
                                current_text=finalize_text,
                                chapter_number=ch_num,
                                trace=trace,
                                pending=pending,
                                stale_reason="step_2b_repair_text_changed",
                                alignment_recheck=_alignment_recheck_callback_if_overridden(),
                            )
                        except Exception as exc:
                            _logger.error(
                                "step_2b_report_refresh_failed | chapter=%d | error=%s",
                                ch_num,
                                exc,
                            )
                            raise
                        refreshed_alignment = pending.alignment_report
                        refreshed_continuity = pending.continuity_report
                        if _guard_repair_applied:
                            (
                                finalize_text,
                                refreshed_alignment,
                                refreshed_continuity,
                                _,
                                _guard_alignment_retry_reason,
                            ) = await _repair_or_raise_guard_ticket_alignment_regression(
                                context=context,
                                runtime=runtime,
                                runner=runner,
                                bundle=bundle,
                                packet=packet,
                                chapter_bridge=ch_bridge,
                                chapter_plan=ch_plan,
                                pending=before_step_2b_refresh_pending,
                                current_text=finalize_text,
                                alignment_report=refreshed_alignment,
                                continuity_report=refreshed_continuity,
                                chapter_number=ch_num,
                                trace=trace,
                                on_step_progress=on_step_progress,
                            )
                            if _guard_alignment_retry_reason:
                                return _build_guard_alignment_retry_checkpoint_result(
                                    runtime=runtime,
                                    request=request,
                                    bundle=bundle,
                                    session_state=session_state,
                                    pending=pending,
                                    current_text=finalize_text,
                                    alignment_report=refreshed_alignment,
                                    continuity_report=refreshed_continuity,
                                    reason=_guard_alignment_retry_reason,
                                )
                        pending = dataclasses.replace(
                            pending,
                            alignment_report=refreshed_alignment,
                            continuity_report=refreshed_continuity,
                        )
                        _save = bundle.layout.chapter_path(ch_num)
                        if not _save.exists():
                            _save = bundle.layout.chapter_review_draft_path(ch_num)
                        runtime.storage.save_text(_save, finalize_text)

                    if _repair_result_2b is not None and callable(on_step_progress):
                        on_step_progress(
                            "step_2b_continuity_repair",
                            {
                                "chapter": ch_num,
                                "applied": _repair_result_2b.applied,
                                "score": round(
                                    float(
                                        getattr(
                                            refreshed_continuity,
                                            "continuity_score",
                                            0.0,
                                        )
                                    ),
                                    2,
                                ),
                            },
                        )

                refreshed_outcome = await extract_and_validate(
                    runner,
                    bundle,
                    packet,
                    ch_bridge,
                    ch_plan,
                    finalize_text,
                    ch_num,
                    trace,
                    pending.continuity_report,
                    repair_exhausted=True,
                )
                pending = dataclasses.replace(pending, outcome=refreshed_outcome)
                finalize_eval_report = None
                _reports_refreshed_for_finalize_text = True

                save_path = bundle.layout.chapter_path(ch_num)
                if not save_path.exists():
                    save_path = bundle.layout.chapter_review_draft_path(ch_num)
                runtime.storage.save_text(save_path, finalize_text)

            # Guard compliance recheck is only meaningful when repairs actually
            # modified the text — if nothing changed, the prior compliance result
            # is still valid and we can skip the model call entirely.
            if finalize_text != pending.current_text:
                pending = await _refresh_guard_compliance_state(
                    runner=runner,
                    bundle=bundle,
                    packet=packet,
                    pending=pending,
                    current_text=finalize_text,
                    chapter_number=request.chapter_number,
                )
                if callable(on_step_progress) and pending.guard_compliance_report is not None:
                    on_step_progress(
                        "guard_compliance_recheck",
                        {
                            "chapter": request.chapter_number,
                            "compliance_rate": _guard_rate_for_progress(
                                pending.guard_compliance_report
                            ),
                            "remaining_tickets": len(pending.repair_tickets or ()),
                        },
                    )

        # ── Step 2c: keep guard-checkpoint finalization aligned with the
        # direct chapter pipeline's pre-archive word-count transaction.  Guard
        # repairs can push text outside the structural archive range; handle
        # that here before persist_results' hard guard rejects the chapter.
        _wc_gate_enabled = word_count_archive_gate_enabled(context.settings)
        _wc_restructure_changed = False
        _wc_archive_bypass = False
        if _wc_gate_enabled:
            _wc_result = await run_word_count_restructure(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=ch_bridge,
                plan=ch_plan,
                current_text=finalize_text,
                chapter_number=ch_num,
                trace=trace,
            )
            _wc_payload = {"chapter": ch_num, **_wc_result.event_payload()}
            context.on_step("word_count_archive_gate", _wc_payload)
            if callable(on_step_progress):
                on_step_progress("word_count_archive_gate", _wc_payload)
            if not _wc_result.accepted and _wc_result.before.band in {"structural", "hard_reject"}:
                _wc_rejections = record_word_count_rejection(
                    runtime.storage,
                    bundle.layout,
                    ch_num,
                    _wc_result,
                )
                _wc_limit = word_count_archive_gate_max_rejections(context.settings)
                if word_count_rejection_limit_reached(context.settings, _wc_rejections):
                    _wc_archive_bypass = True
                    _wc_warning = (
                        f"归档前字数闸门已达到拒绝上限：当前 {_wc_result.before.actual}/"
                        f"{_wc_result.before.target} 字，处于 {_wc_result.before.band} 区间；"
                        f"自动重整仍未达标（原因：{_wc_result.reason}）。本次降级为警告归档。"
                    )
                    pending = dataclasses.replace(
                        pending,
                        warnings=tuple(pending.warnings or ()) + (_wc_warning,),
                    )
                    _limit_payload = {
                        "chapter": ch_num,
                        "rejection_count": _wc_rejections,
                        "max_rejections": _wc_limit,
                        "reason": _wc_result.reason,
                        "before": _wc_result.before.as_dict(),
                    }
                    context.on_step("word_count_archive_gate_limit_reached", _limit_payload)
                    if callable(on_step_progress):
                        on_step_progress("word_count_archive_gate_limit_reached", _limit_payload)
                else:
                    raise RuntimeError(
                        f"章节 {ch_num} 末端字数精修未达标："
                        f"{_wc_result.before.actual}/{_wc_result.before.target}，"
                        f"原因：{_wc_result.reason}（第 {_wc_rejections}/{_wc_limit} 次）。"
                    )
            elif _wc_result.accepted:
                clear_word_count_rejections(runtime.storage, bundle.layout, ch_num)
            if _wc_result.changed:
                finalize_text = _wc_result.text
                finalize_eval_report = None
                pending = dataclasses.replace(
                    pending,
                    performed_edits=pending.performed_edits + 1,
                    warnings=tuple(pending.warnings or ())
                    + (
                        f"归档前字数重整：{_wc_result.before.actual} → {_wc_result.after.actual} "
                        f"（目标 {_wc_result.after.target}，{_wc_result.reason}）。",
                    ),
                )
                save_path = bundle.layout.chapter_path(ch_num)
                if not save_path.exists():
                    save_path = bundle.layout.chapter_review_draft_path(ch_num)
                runtime.storage.save_text(save_path, finalize_text)
                runtime.storage.save_text(
                    bundle.layout.chapter_draft_path(ch_num, 96),
                    finalize_text,
                )
                context.on_step(
                    "word_count_restructure_applied",
                    {
                        "chapter": ch_num,
                        "mode": _wc_result.mode,
                        "before": _wc_result.before.as_dict(),
                        "after": _wc_result.after.as_dict(),
                    },
                )
                _wc_restructure_changed = True
        if not _wc_gate_enabled:
            _wc_payload = {"chapter": ch_num, "reason": "config_disabled"}
            context.on_step("word_count_archive_gate_skipped", _wc_payload)
            if callable(on_step_progress):
                on_step_progress("word_count_archive_gate_skipped", _wc_payload)

        if _wc_restructure_changed:
            pending = await refresh_pending_reports_for_text(
                runner=runner,
                bundle=bundle,
                packet=packet,
                bridge=ch_bridge,
                plan=ch_plan,
                current_text=finalize_text,
                chapter_number=ch_num,
                trace=trace,
                pending=pending,
                stale_reason="word_count_restructure_text_changed",
                alignment_recheck=_alignment_recheck_callback_if_overridden(),
            )

            refreshed_outcome = await extract_and_validate(
                runner,
                bundle,
                packet,
                ch_bridge,
                ch_plan,
                finalize_text,
                ch_num,
                trace,
                pending.continuity_report,
                repair_exhausted=True,
            )
            pending = dataclasses.replace(pending, outcome=refreshed_outcome)
            _reports_refreshed_for_finalize_text = True
            if pending.outcome.creative_report is not None:
                report_data = pending.outcome.creative_report.model_dump(mode="json")
                runtime.storage.save_json(
                    bundle.layout.creative_report_path(request.chapter_number),
                    report_data,
                )
            pending = await _refresh_guard_compliance_state(
                runner=runner,
                bundle=bundle,
                packet=packet,
                pending=pending,
                current_text=finalize_text,
                chapter_number=request.chapter_number,
            )
            if callable(on_step_progress) and pending.guard_compliance_report is not None:
                on_step_progress(
                    "word_count_restructure_guard_verify",
                    {
                        "chapter": request.chapter_number,
                        "compliance_rate": _guard_rate_for_progress(
                            pending.guard_compliance_report
                        ),
                        "remaining_tickets": len(pending.repair_tickets or ()),
                    },
                )

        # ── Step 3: Accept the Plot Guard handoff only with chapter finalization ──
        if pending.guard_decision is not None:
            _apply_plot_guard_to_creative_report(report_data, pending.guard_decision)
            _updated_outcome = pending.outcome.model_copy(
                update={"creative_report": CreativeReport.model_validate(report_data)}
            )
            pending = dataclasses.replace(pending, outcome=_updated_outcome)
            runtime.storage.save_json(
                bundle.layout.creative_report_path(request.chapter_number),
                report_data,
            )
            record_plot_guard_handoff(
                storage=runtime.storage,
                layout=bundle.layout,
                chapter_number=request.chapter_number,
                decision=pending.guard_decision,
                accepted=True,
                recorded_by="chapter_session_finalize",
            )

        if request.option_id == "adjust_outline_and_finalize":
            _outline_updated = await _apply_outline_adjustment(
                layout=bundle.layout,
                storage=runtime.storage,
                router=context.router,
                builder=context.builder,
                settings=context.settings,
                report=pending.outcome.creative_report,
                report_data=report_data,
                completed_chapter=request.chapter_number,
                smart=bool(
                    pending.guard_decision is not None
                    and (
                        pending.guard_decision.outline_action == "smart"
                        or pending.guard_decision.decision == "adjust_outline_smart"
                    )
                ),
            )
            if _outline_updated:
                record_macro_guard_adjustment(
                    runtime.storage,
                    bundle.layout,
                    request.chapter_number,
                    context.settings,
                )

        # ── Hash sync: deterministic cleanup may rebind reports, but a
        #    semantic text change must have refreshed hard reports first.
        _sync_hash = source_text_hash(finalize_text)
        _sync_or_mark_report_hashes(
            runner,
            bundle.layout,
            request.chapter_number,
            _sync_hash,
            allow_restamp=not (
                _semantic_text_changed_before_finalize and not _reports_refreshed_for_finalize_text
            ),
            stale_reason="semantic_text_changed_before_checkpoint_finalize",
        )

        prepared = await load_prepared_chapter_artifacts(
            context,
            bundle=bundle,
            chapter_number=request.chapter_number,
        )

        # ── Refresh warnings based on post-repair causal report ──────────
        # pending.warnings was baked at guard_checkpoint time and may be stale
        # after causal/continuity repairs updated pending.causal_report.
        _final_warnings: list[str] = [w for w in (pending.warnings or []) if "因果链校验" not in w]
        if pending.causal_report is not None:
            _post_issues = pending.causal_report.issues or []
            _post_high = sum(
                1
                for i in _post_issues
                if (getattr(i, "severity", "") or "").lower() in {"critical", "high"}
            )
            if _post_high:
                _final_warnings.append(
                    f"因果链校验发现 {len(_post_issues)} 个问题，"
                    f"其中高优先级 {_post_high} 个，建议人工复核。"
                )
            elif _post_issues:
                _final_warnings.append(
                    f"因果链校验发现 {len(_post_issues)} 个问题，均为中低优先级。"
                )

        review = ChapterReviewArtifacts(
            prepared=prepared,
            current_text=finalize_text,
            refinement_done=bool(
                pending.authoring_refined_text_hash
                and pending.authoring_refined_text_hash == source_text_hash(finalize_text)
            ),
            performed_edits=pending.performed_edits,
            outcome=pending.outcome,
            alignment_report=pending.alignment_report,
            chapter_repair_report=pending.chapter_repair_report,
            causal_report=pending.causal_report,
            continuity_report=pending.continuity_report,
            repair_plan=finalize_repair_plan,
            eval_report=finalize_eval_report,
            guard_compliance_report=pending.guard_compliance_report,
            review_findings=list(pending.review_findings or ()),
            repair_tickets=list(pending.repair_tickets or ()),
            warnings=_final_warnings,
            allow_word_count_archive_bypass=_wc_archive_bypass,
        )
        from novel_forge.persistence.authoring_store import AuthoringAcceptanceRequired

        try:
            result = await finalize_chapter_result(
                context,
                review=review,
                trace=trace,
                eval_report=review.eval_report,
                emit_evaluate_step=False,
                allow_contract_audit_auto_repair=False,
                allow_carry_forward_auto_repair=True,
            )
        except AuthoringAcceptanceRequired as exc:
            pending = dataclasses.replace(
                pending,
                authoring_refined_text_hash=source_text_hash(exc.text),
                **{key: value for key, value in exc.review_state.items() if value is not None},
            )
            return _build_block_checkpoint_result(
                runtime=runtime,
                request=request,
                bundle=bundle,
                session_state=session_state,
                pending=pending,
                finalize_text=exc.text,
                ch_bridge=ch_bridge,
                block_key="authoring_final_acceptance",
                block_metadata={"accepted_text_changed": True},
                prompt_text="必要的归档前检查已更新正文。请阅读此最终候选后明确验收；尚未写入正式正文或正史。",
                recommended_option_id="pause_for_human",
                option_descriptions={
                    "accept_and_finalize": "验收当前最终候选；事实和一致性门禁仍生效"
                },
                on_step=context.on_step,
            )
        except ConsistencyViolationError as exc:
            if not is_contract_audit_block_exception(exc):
                if _is_archive_quality_retry_block_exception(exc):
                    _quality_block_summary = _extract_archive_quality_block_summary(exc)
                    context.on_step(
                        "archive_quality_retry_checkpoint",
                        {
                            "chapter": request.chapter_number,
                            "summary": _quality_block_summary,
                            "block_kind": getattr(exc, "block_kind", ""),
                            "violation_kind": getattr(exc, "violation_kind", ""),
                            "recovery_target": str(getattr(exc, "replan_target", "")),
                        },
                    )
                    return _build_archive_quality_retry_checkpoint_result(
                        runtime=runtime,
                        request=request,
                        bundle=bundle,
                        session_state=session_state,
                        pending=pending,
                        current_text=finalize_text,
                        bridge_summary=getattr(ch_bridge, "bridge_summary", "") or "",
                        reason=_quality_block_summary,
                        block_kind=getattr(exc, "block_kind", ""),
                        on_step=context.on_step,
                    )
                if _is_carry_forward_retry_block_exception(exc):
                    _carry_block_summary = _extract_archive_quality_block_summary(exc)
                    context.on_step(
                        "carry_forward_retry_checkpoint",
                        {
                            "chapter": request.chapter_number,
                            "summary": _carry_block_summary,
                        },
                    )
                    return _build_archive_quality_retry_checkpoint_result(
                        runtime=runtime,
                        request=request,
                        bundle=bundle,
                        session_state=session_state,
                        pending=pending,
                        current_text=finalize_text,
                        bridge_summary=getattr(ch_bridge, "bridge_summary", "") or "",
                        reason=_carry_block_summary,
                        checkpoint_step="carry_forward_retry_checkpoint",
                        metadata_key="carry_forward_block",
                        summary_key="carry_forward_block_summary",
                        on_step=context.on_step,
                    )
                if not _is_state_adjudication_block_exception(exc):
                    raise
                _state_block_summary = _extract_state_adjudication_block_summary(exc)
                pending = dataclasses.replace(
                    pending,
                    warnings=tuple(
                        [
                            *(pending.warnings or ()),
                            f"LLM 状态裁判要求阻断归档：{_state_block_summary}",
                        ]
                    ),
                )
                return _build_block_checkpoint_result(
                    runtime=runtime,
                    request=request,
                    bundle=bundle,
                    session_state=session_state,
                    pending=pending,
                    finalize_text=finalize_text,
                    ch_bridge=ch_bridge,
                    block_key="state_adjudication_block",
                    block_metadata={"state_adjudication_summary": _state_block_summary},
                    prompt_text=(
                        "归档前的 LLM 状态裁判要求阻断归档，但 pipeline 内的自动修复"
                        "已耗尽仍未能解决（通常是契约目标在正文中无证据支撑，"
                        "需要人工调整正文或契约）。请人工查看状态裁判报告后选择处理方式。"
                    ),
                    recommended_option_id="pause_for_human",
                    option_descriptions={
                        "pause_for_human": "状态裁判阻断无法自动修复，请人工查看报告后决定下一步。",
                        "accept_and_finalize": (
                            "忽略状态裁判阻断，强制归档正文（不推荐，但可避免流程卡死）。"
                        ),
                    },
                    on_step=context.on_step,
                )
            report_payload: dict[str, Any] = {}
            try:
                report_payload = artifact_loader.load_json(
                    bundle.layout.contract_execution_report_path(request.chapter_number)
                )
            except Exception:
                report_payload = {}
            ticket = compile_contract_audit_repair_ticket(
                report_payload=report_payload if isinstance(report_payload, dict) else {},
                chapter_number=request.chapter_number,
                current_text=finalize_text,
            )
            already_tried_contract_repair = has_contract_audit_repair_ticket(pending.repair_tickets)
            if already_tried_contract_repair:
                raise
            if ticket is None:
                if callable(on_step_progress):
                    on_step_progress(
                        "contract_execution_audit_unrepairable",
                        {
                            "chapter": request.chapter_number,
                            "severity": (
                                report_payload.get("severity")
                                if isinstance(report_payload, dict)
                                else None
                            ),
                            "verdict": (
                                report_payload.get("verdict")
                                if isinstance(report_payload, dict)
                                else None
                            ),
                            "rationale_excerpt": (
                                str(report_payload.get("rationale") or "")[:200]
                                if isinstance(report_payload, dict)
                                else ""
                            ),
                            "reason": "compile_contract_audit_repair_ticket_returned_none",
                        },
                    )
                pending = dataclasses.replace(
                    pending,
                    warnings=tuple(
                        [
                            *(pending.warnings or ()),
                            "章节契约执行审计无法自动编译为修复工单（缺少可定位的命中片段），已暂停等待人工处理。",
                        ]
                    ),
                )
                return _build_block_checkpoint_result(
                    runtime=runtime,
                    request=request,
                    bundle=bundle,
                    session_state=session_state,
                    pending=pending,
                    finalize_text=finalize_text,
                    ch_bridge=ch_bridge,
                    block_key="contract_execution_audit_unrepairable",
                    block_metadata={
                        "contract_execution_report": str(
                            bundle.layout.contract_execution_report_path(request.chapter_number)
                        ),
                        "contract_execution_severity": (
                            report_payload.get("severity")
                            if isinstance(report_payload, dict)
                            else None
                        ),
                    },
                    prompt_text=(
                        "归档前的章节契约执行审计发现阻断问题，但当前审计结果缺少可定位的命中片段，"
                        "无法自动编译为修复工单。请人工查看审计报告后选择处理方式。"
                    ),
                    recommended_option_id="pause_for_human",
                    option_descriptions={
                        "pause_for_human": (
                            "章节契约审计无法自动定位并修复，请人工查看审计报告后决定下一步。"
                        ),
                        "accept_and_finalize": (
                            "忽略审计阻断，强制归档正文（不推荐，但可避免流程卡死）。"
                        ),
                    },
                    on_step=context.on_step,
                )
            if callable(on_step_progress):
                on_step_progress(
                    "contract_execution_repair_checkpoint",
                    {
                        "chapter": request.chapter_number,
                        "severity": report_payload.get("severity"),
                        "future_leak_hits": len(report_payload.get("future_leak_hits", []) or []),
                        "forbidden_hits": len(
                            report_payload.get("forbidden_progression_hits", []) or []
                        ),
                    },
                )
            pending = dataclasses.replace(
                pending,
                repair_tickets=tuple([*(pending.repair_tickets or ()), ticket]),
                warnings=tuple(
                    [
                        *(pending.warnings or ()),
                        "章节契约执行审计要求先局部修复，再尝试归档。",
                    ]
                ),
            )
            return _build_block_checkpoint_result(
                runtime=runtime,
                request=request,
                bundle=bundle,
                session_state=session_state,
                pending=pending,
                finalize_text=finalize_text,
                ch_bridge=ch_bridge,
                block_key="contract_execution_repair_checkpoint",
                block_metadata={
                    "contract_execution_report": str(
                        bundle.layout.contract_execution_report_path(request.chapter_number)
                    ),
                },
                prompt_text=(
                    "归档前的章节契约执行审计发现需要局部修复的问题。"
                    "请选择应用修复后归档；修复会限制在命中句或相邻段落。"
                ),
                recommended_option_id="apply_repairs_and_finalize",
                option_descriptions={
                    "apply_repairs_and_finalize": (
                        "修复契约审计命中的未来泄露/禁行进展后，再重新归档本章。"
                    ),
                },
                on_step=context.on_step,
            )
        if result.eval_report is None:
            raise RuntimeError("finalized chapter must include eval_report")
        if result.continuity_report is None:
            raise RuntimeError("finalized chapter must include continuity_report")
        if result.bridge is None:
            raise RuntimeError("finalized chapter must include bridge")

        if getattr(context.settings, "auto_introduce_characters", True):
            introduced_names = list(session_state.introduced_characters or [])
            if introduced_names:
                try:
                    enriched = await enrich_introduced_characters(
                        router=context.router,
                        builder=context.builder,
                        storage=runtime.storage,
                        settings=context.settings,
                        bundle=bundle,
                        chapter_number=request.chapter_number,
                        chapter_text=result.text,
                        introduced_names=introduced_names,
                        trace=trace,
                        on_step=context.on_step,
                    )
                    if enriched:
                        _logger.info(
                            "章节工作室第 %d 章后置角色档案精化完成：%s",
                            request.chapter_number,
                            "、".join(enriched),
                        )
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "enrich_introduced_characters 在章节工作室第 %d 章中遇到异常（已跳过）：%s",
                        request.chapter_number,
                        exc,
                    )

            try:
                registered = await auto_register_from_creative_report(
                    router=context.router,
                    builder=context.builder,
                    storage=runtime.storage,
                    settings=context.settings,
                    bundle=bundle,
                    chapter_number=request.chapter_number,
                    chapter_text=result.text,
                    creative_report=result.creative_report,
                    trace=trace,
                    on_step=context.on_step,
                    remaining_slots=max(
                        0,
                        _auto_introduce_limit(context.settings) - len(set(introduced_names)),
                    ),
                )
                if registered:
                    _logger.info(
                        "章节工作室第 %d 章草稿新发现角色自动建档完成：%s",
                        request.chapter_number,
                        "、".join(registered),
                    )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "auto_register_from_creative_report 在章节工作室第 %d 章中遇到异常（已跳过）：%s",
                    request.chapter_number,
                    exc,
                )

        await _index_finalized_chapter_memory(
            memory_ctx=memory_ctx,
            storage=runtime.storage,
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            chapter_result=result,
            on_step=context.on_step,
        )

        clear_session_state(bundle, request.chapter_number)
        # ── P0-C backfill: when checkpoint-resume skipped the quality stage
        # (so result.reading_power_report is None), the reading_power
        # report file from a prior evaluate run can still be read and its
        # overall_score surfaced in the session result. The same hash
        # check as finalize.py:2227 guards against stale data.
        rp_backfill_score, rp_backfill_summary = _backfill_reading_power_score(
            storage=runtime.storage,
            layout=bundle.layout,
            chapter_number=request.chapter_number,
            current_text=result.text,
        )

        return build_completed_session_result(
            project_id=request.project_id,
            chapter_number=request.chapter_number,
            word_count=result.meta.word_count,
            overall_score=result.eval_report.overall_score,
            continuity_score=result.continuity_report.continuity_score,
            continuity_issue_count=_open_issue_count(result.continuity_report.issues),
            bridge_summary=result.bridge.bridge_summary,
            chapter_exit_summary=summarize_exit_state(pending.outcome),
            preview_text=result.text,
            applied_option_id=request.option_id,
            guard_decision=pending.guard_decision,
            reading_power_score=rp_backfill_score,
            reading_power_summary=rp_backfill_summary,
            metadata={
                "alignment_score": (
                    result.alignment_report.alignment_score
                    if result.alignment_report is not None
                    else None
                ),
                "causal_score": (
                    result.causal_report.causal_score if result.causal_report is not None else None
                ),
                "causal_issue_count": (
                    len(result.causal_report.issues) if result.causal_report is not None else 0
                ),
                "guard_compliance_rate": (
                    pending.guard_compliance_report.get("overall_compliance_rate")
                    if pending.guard_compliance_report is not None
                    else None
                ),
                "remaining_guard_ticket_count": len(pending.repair_tickets or ()),
                "guard_finding_count": len(pending.review_findings or ()),
                "warnings": list(result.warnings or []),
            },
        )
    finally:
        await _memory_lifecycle.__aexit__(None, None, None)
