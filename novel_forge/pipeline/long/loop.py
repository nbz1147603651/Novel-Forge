"""run_long_chapter — v2 long-form orchestration loop."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from novel_forge.core.exceptions import ConsistencyViolationError
from novel_forge.core.schemas.chapter import ChapterResult
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import (
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    ArtifactManifest,
)
from novel_forge.pipeline.finalization_manifest import (
    chapter_finalization_artifact,
    record_finalization_pending,
)
from novel_forge.pipeline.long.chapter_flow import execute_chapter_pipeline
from novel_forge.pipeline.long.context import StoryMemoryManager
from novel_forge.pipeline.long.preflight import close_long_project, prepare_long_project
from novel_forge.pipeline.long.replan_context import build_replan_context
from novel_forge.pipeline.long.services.init.init_coherence import init_readiness_path
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

if TYPE_CHECKING:
    from novel_forge.pipeline.long.execution_models import ChapterExecutionContext
    from novel_forge.pipeline.long.preflight import LongProjectBundle

logger = logging.getLogger(__name__)

_MEMORY_RETRY_DELAYS = (0.5, 1.5, 3.0, 6.0)  # 指数退避延迟（秒），含随机抖动，最多重试 4 次
_REPLAN_BLOCK_HEADER = "【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实："
_REPLAN_CAUSAL_HEADER = "⚠️ 因果链约束（上次生成遗留问题，本次必须避免）："
_REPLAN_NOTES_MAX_CHARS = 1800
_REPLAN_MAX_ITEMS = 8
_MEMORY_PENDING_TEXT_SNAPSHOT_CHARS = 20000


def _init_readiness_exists(ctx: ChapterExecutionContext, project_id: str) -> bool:
    """Return whether the project already has a readiness report for preflight."""
    layout = ProjectLayout(ctx._storage.existing_project_dir(project_id))
    return bool(ctx._storage.exists(init_readiness_path(layout)))


def _truncate_replan_text(text: str, *, max_chars: int = _REPLAN_NOTES_MAX_CHARS) -> str:
    """Keep replan guidance bounded so retries cannot consume the plan budget."""
    clean = str(text or "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip("，,；;：: \n") + "\n- （其余问题已压缩，优先修复以上高风险项）"


def _extract_existing_replan_items(notes: str) -> list[str]:
    items: list[str] = []
    for line in str(notes or "").splitlines():
        clean = line.strip()
        if clean.startswith("- "):
            item = clean[2:].strip()
            if item:
                items.append(item)
    return items


def _strip_auto_replan_text(text: str) -> str:
    """Remove previous auto-injected replan blocks while keeping user-authored text."""
    value = str(text or "").strip()
    for marker in (
        "\n\n" + _REPLAN_CAUSAL_HEADER,
        "\n" + _REPLAN_CAUSAL_HEADER,
        "\n补充约束：" + _REPLAN_BLOCK_HEADER,
        "\n" + _REPLAN_BLOCK_HEADER,
        _REPLAN_CAUSAL_HEADER,
        _REPLAN_BLOCK_HEADER,
    ):
        if marker in value:
            value = value.split(marker, 1)[0].strip()
    return value


def _artifact_dump(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _artifact_dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_artifact_dump(item) for item in value]
    return str(value)


def _text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _invalidate_book_repair_queue_for_chapter(bundle: Any, chapter_number: int) -> None:
    """Mark pending global-audit repair queue items as stale after chapter regeneration."""
    try:
        from novel_forge.obs.audit_store import GlobalAuditStore

        db_path = getattr(getattr(bundle, "layout", None), "global_audit_db_path", None)
        if db_path is None or not db_path.exists():
            return
        store = GlobalAuditStore(db_path)
        invalidated = store.invalidate_chapter_items(
            chapter_number,
            reason="chapter_regenerated",
        )
        if invalidated > 0:
            logger.info(
                "book_repair_queue_invalidated | chapter=%d | count=%d",
                chapter_number,
                invalidated,
            )
    except Exception as exc:
        logger.debug(
            "book_repair_queue_invalidation_skipped | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


def _persist_canon_memory_artifact(
    ctx: ChapterExecutionContext,
    bundle: Any,
    *,
    chapter_number: int,
    chapter_result: ChapterResult | None,
    memory_update_status: str,
    memory_stats: dict[str, Any] | None = None,
    memory_status: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    try:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            load_stage_artifact,
            persist_stage_artifact,
        )

        text = str(getattr(chapter_result, "text", "") or "") if chapter_result is not None else ""
        canon_delta = (
            getattr(chapter_result, "canon_delta", None) if chapter_result is not None else None
        )
        creative_report = (
            getattr(chapter_result, "creative_report", None) if chapter_result is not None else None
        )
        previous_artifact = load_stage_artifact(
            ctx._storage,
            bundle.layout,
            chapter_number=chapter_number,
            artifact_type="final",
        )
        persist_stage_artifact(
            storage=ctx._storage,
            layout=bundle.layout,
            project_id=getattr(bundle, "project_id", "") or "unknown",
            chapter_number=chapter_number,
            artifact_type="canon_memory",
            payload={
                "final_text_hash": _text_hash(text),
                "final_text_chars": len(text),
                "memory_update_status": memory_update_status,
                "memory_stats": _artifact_dump(memory_stats or {}),
                "memory_status": _artifact_dump(memory_status or {}),
                "error": error[:500],
                "canon_delta_summary": {
                    "source_chapter": getattr(canon_delta, "source_chapter", chapter_number)
                    if canon_delta is not None
                    else chapter_number,
                    "chapter_summary": str(getattr(canon_delta, "chapter_summary", "") or "")[:800]
                    if canon_delta is not None
                    else "",
                    "has_exit_state": bool(
                        getattr(canon_delta, "chapter_exit_state", None)
                        if canon_delta is not None
                        else None
                    ),
                },
                "creative_report_summary": str(getattr(creative_report, "summary", "") or "")[:800]
                if creative_report is not None
                else "",
            },
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            previous_artifact=previous_artifact,
            event_ledger=[
                {
                    "event": "canon_memory_done",
                    "memory_update_status": memory_update_status,
                    "final_text_hash": _text_hash(text),
                }
            ],
        )
    except Exception as exc:
        logger.warning(
            "canon_memory_artifact_persist_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


def _memory_pending_snapshot(
    text: str, *, max_chars: int = _MEMORY_PENDING_TEXT_SNAPSHOT_CHARS
) -> str:
    """Store a bounded text snapshot for memory-gap compensation fallback."""
    clean = str(text or "").strip()
    if len(clean) <= max_chars:
        return clean
    half = max(1, max_chars // 2)
    return (
        clean[:half].rstrip() + "\n\n【中段省略：记忆补偿快照已截断】\n\n" + clean[-half:].lstrip()
    )


def _format_replan_notes(existing_notes: str, violations: list[str]) -> str:
    """Merge consistency violations into replan note text for the chapter outline."""
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*_extract_existing_replan_items(existing_notes), *violations]:
        clean = " ".join(str(item or "").split()).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        merged.append(clean)
        if len(merged) >= _REPLAN_MAX_ITEMS:
            break
    if not merged:
        return ""
    lines = "\n".join(f"- {item}" for item in merged)
    return _truncate_replan_text(f"{_REPLAN_BLOCK_HEADER}\n{lines}")


def _apply_replan_notes_to_bundle(bundle: "LongProjectBundle", notes: str) -> None:
    """Inject replan notes into the chapter outline so the plan step sees them.

    Mirrors the logic in ``chapter_session_state.apply_notes_to_bundle()``
    but is self-contained within the pipeline layer to avoid a cross-layer
    import from ``workspace/``.
    """
    raw = str(notes or "").strip()
    if not raw:
        return

    # Replace the previous auto block instead of appending recursively.
    existing = _strip_auto_replan_text(getattr(bundle.chapter_outline, "notes", ""))
    bundle.chapter_outline.notes = f"{existing}\n\n{raw}".strip() if existing else raw

    # Enrich chapter_outline.goal with structured causal constraints
    goal = _strip_auto_replan_text(bundle.chapter_outline.goal)
    if "需注意以下连贯性问题" in raw or "opening_causal_gap" in raw or "event_without_cause" in raw:
        bundle.chapter_outline.goal = (f"{goal}\n\n{_REPLAN_CAUSAL_HEADER}\n{raw}").strip()
    else:
        bundle.chapter_outline.goal = f"{goal}\n补充约束：{raw}".strip()


def _auto_introduce_limit(settings: Any) -> int:
    try:
        return max(0, int(getattr(settings, "long_auto_introduce_max_new_characters", 2) or 0))
    except (TypeError, ValueError):
        return 2


async def _introduce_characters_for_bundle(
    ctx: ChapterExecutionContext,
    bundle: "LongProjectBundle",
    chapter_number: int,
    trace: PipelineTrace,
    introduced: list[str],
) -> list[str]:
    """Run pre-chapter character introduction and append only newly-created names."""
    if not getattr(ctx._settings, "auto_introduce_characters", True):
        return introduced
    limit = _auto_introduce_limit(ctx._settings)
    remaining_slots = max(0, limit - len(set(introduced)))
    if remaining_slots <= 0:
        return introduced
    try:
        new_names = await introduce_new_characters(
            router=ctx._router,
            builder=ctx._builder,
            storage=ctx._storage,
            settings=ctx._settings,
            bundle=bundle,
            chapter_number=chapter_number,
            trace=trace,
            on_step=ctx._on_step,
            remaining_slots=remaining_slots,
        )
    except Exception as exc:
        # Character intro failure must not block chapter generation.
        logger.warning(
            "introduce_new_characters 在章节 %d 中遇到异常（已跳过）：%s",
            chapter_number,
            exc,
        )
        return introduced
    appended = [name for name in new_names if name not in introduced]
    if appended:
        introduced.extend(appended)
        logger.info(
            "章节 %d 前置角色建档完成：%s",
            chapter_number,
            "、".join(appended),
        )
    return introduced


async def _trigger_memory_update(
    ctx: ChapterExecutionContext,
    bundle: "LongProjectBundle",
    project_id: str,
    chapter_number: int,
    *,
    chapter_result: ChapterResult | None,
) -> None:
    """Delegate the memory phase to the shared manifest-owned finalizer."""
    memory_ctx = ctx.memory_context
    if memory_ctx is None or chapter_result is None:
        _persist_canon_memory_artifact(
            ctx,
            bundle,
            chapter_number=chapter_number,
            chapter_result=chapter_result,
            memory_update_status="skipped",
            error="memory_context_or_chapter_result_missing",
        )
        return

    chapter_text = str(getattr(chapter_result, "text", "") or "")
    if not chapter_text.strip():
        _persist_canon_memory_artifact(
            ctx,
            bundle,
            chapter_number=chapter_number,
            chapter_result=chapter_result,
            memory_update_status="skipped",
            error="empty_final_text",
        )
        return

    on_step = getattr(ctx, "_on_step", None)
    creative_report = getattr(chapter_result, "creative_report", None)
    memory_result = await finalize_chapter_memory_phase(
        storage=ctx._storage,
        layout=bundle.layout,
        memory_context=memory_ctx,
        chapter_number=chapter_number,
        chapter_text=chapter_text,
        creative_report_text=str(getattr(creative_report, "summary", "") or ""),
        chapter_result=chapter_result,
        on_step=on_step if callable(on_step) else None,
        retry_delays=_MEMORY_RETRY_DELAYS,
    )
    _persist_canon_memory_artifact(
        ctx,
        bundle,
        chapter_number=chapter_number,
        chapter_result=chapter_result,
        memory_update_status=(
            "pass"
            if memory_result.completed
            else "skipped"
            if memory_result.status == "skipped"
            else "fail"
        ),
        memory_stats=memory_result.stats,
        memory_status=memory_result.memory_status,
        error=memory_result.error,
    )


async def run_long_chapter(
    runner: Any,
    project_id: str,
    chapter_number: int,
    *,
    force_regenerate: bool = False,
    chapter_instruction: str = "",
) -> ChapterResult:
    """Execute the v2 long-form chapter pipeline.

    This function now delegates to execute_chapter_pipeline() which
    encapsulates the entire chapter generation flow.
    """
    # Create execution context for helper functions
    ctx = runner.create_execution_context()

    # ── Check for and compensate any previous chapter's memory gap ────────────
    await _check_and_compensate_memory_gap(ctx, project_id, chapter_number)
    _detect_kernel_persist_pending_marker(ctx, project_id, chapter_number)
    if not _init_readiness_exists(ctx, project_id):
        await ensure_init_readiness_for_existing_project(runner, project_id=project_id)

    bundle: LongProjectBundle = await prepare_long_project(
        storage=ctx._storage,
        project_id=project_id,
        chapter_number=chapter_number,
        force_regenerate=force_regenerate,
    )
    try:
        return await _run_prepared_long_chapter(
            ctx, bundle, project_id, chapter_number, chapter_instruction=chapter_instruction
        )
    finally:
        await close_long_project(bundle)


async def _run_prepared_long_chapter(
    ctx: ChapterExecutionContext,
    bundle: LongProjectBundle,
    project_id: str,
    chapter_number: int,
    *,
    chapter_instruction: str,
) -> ChapterResult:
    if bundle.chapter_source_slice is not None and str(chapter_instruction or "").strip():
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            attach_chapter_instruction_to_source_slice,
        )

        bundle.chapter_source_slice = attach_chapter_instruction_to_source_slice(
            bundle.chapter_source_slice,
            chapter_instruction,
            chapter_number=chapter_number,
        )
    from novel_forge.pipeline.long.services.context.chapter_research import (
        attach_long_chapter_research,
    )

    await attach_long_chapter_research(ctx=ctx, bundle=bundle, chapter_number=chapter_number)
    trace = PipelineTrace()

    # ── Telemetry: chapter run ID (persists across replans) ──────────────
    chapter_run_id = str(uuid.uuid4())
    trace.chapter_run_id = chapter_run_id
    trace.chapter_number = chapter_number

    # ── Event ledger (append-only telemetry) ─────────────────────────────
    _telemetry_enabled = bool(
        getattr(ctx._settings, "long_repair_effectiveness_telemetry_enabled", True)
    )
    _ledger: Any | None = None
    if _telemetry_enabled:
        try:
            from novel_forge.obs.repair_event_ledger import RepairEventLedger

            _ledger_path = bundle.layout.root / "logs" / chapter_run_id / "repair_events.jsonl"
            _ledger = RepairEventLedger(_ledger_path)
        except Exception as _ledger_exc:
            logger.debug("repair_event_ledger_init_failed | %s", _ledger_exc)
            _ledger = None

    # ── Auto-introduce new characters found in structured chapter inputs ─────
    introduced: list[str] = []
    introduced = await _introduce_characters_for_bundle(
        ctx,
        bundle,
        chapter_number,
        trace,
        introduced,
    )

    memory = StoryMemoryManager(
        storage=ctx._storage,
        retriever=ctx._retriever,
        memory_context=ctx.memory_context,
    )
    context = ctx

    # ── Consistency replan loop ──────────────────────────────────────────────
    # When the quality gate at the end of execute_chapter_pipeline raises
    # ConsistencyViolationError, automatically re-plan and retry instead of
    # aborting.  This mirrors the Desktop session handler's replan behavior,
    # making CLI batch runs (--auto, --end-chapter N) resilient to quality
    # failures.  Each replan injects the violations as constraints into the
    # chapter outline so the plan LLM can address them explicitly.
    replan_count = 0
    max_replans = getattr(ctx._settings, "long_max_consistency_replans", 2)
    replan_notes = ""
    replan_history: list[Any] = []

    while True:
        # Generate a new attempt_id for each replan iteration.
        attempt_id = str(uuid.uuid4())
        trace.attempt_id = attempt_id

        try:
            result = await execute_chapter_pipeline(
                context=context,
                chapter_number=chapter_number,
                bundle=bundle,
                trace=trace,
                memory=memory,
            )
            # ── Record success event ─────────────────────────────────────
            trace.status = "success"
            if _ledger is not None:
                try:
                    from novel_forge.obs.repair_event_ledger import (
                        AttemptStatus,
                        make_attempt_complete_event,
                    )

                    _ledger.append(
                        make_attempt_complete_event(
                            attempt_id=attempt_id,
                            chapter_number=chapter_number,
                            timestamp=time.time(),
                            replan_attempt=replan_count,
                            status=AttemptStatus.SUCCESS,
                        )
                    )
                except Exception as _evt_exc:
                    logger.debug("attempt_complete_event_failed | %s", _evt_exc)
            break  # success — exit the replan loop
        except ConsistencyViolationError as exc:
            # Only an explicitly plan-scoped violation may regenerate Bridge
            # and Plan. Every narrower/manual recovery keeps the expensive
            # source artifacts intact and surfaces the precise failure.
            if not exc.replan_target.permits_plan_replan:
                # ── Record non-replan failure event ──────────────────────
                trace.status = "failed"
                if _ledger is not None:
                    try:
                        from novel_forge.obs.repair_event_ledger import (
                            AttemptStatus,
                            make_attempt_complete_event,
                        )

                        _ledger.append(
                            make_attempt_complete_event(
                                attempt_id=attempt_id,
                                chapter_number=chapter_number,
                                timestamp=time.time(),
                                replan_attempt=replan_count,
                                status=AttemptStatus.FAILED,
                                violation_kind=str(getattr(exc, "violation_kind", "")),
                            )
                        )
                    except Exception:
                        pass
                logger.warning(
                    "章节 %d 因 %s 失败（不可 plan-replan），跳过重规划 | "
                    "failed_stage=%s | issues=%s",
                    chapter_number,
                    exc.violation_kind,
                    exc.failed_stage,
                    exc.violations[:3],
                )
                raise

            replan_count += 1
            from novel_forge.pipeline.long.services.future_planning import (
                record_future_replan_trigger,
            )

            record_future_replan_trigger(ctx._storage, bundle.layout, ctx._settings, chapter_number)
            if replan_count > max_replans:
                # ── Record exhausted replan event ────────────────────────
                trace.status = "failed"
                if _ledger is not None:
                    try:
                        from novel_forge.obs.repair_event_ledger import (
                            AttemptStatus,
                            make_attempt_complete_event,
                        )

                        _ledger.append(
                            make_attempt_complete_event(
                                attempt_id=attempt_id,
                                chapter_number=chapter_number,
                                timestamp=time.time(),
                                replan_attempt=replan_count - 1,
                                status=AttemptStatus.FAILED,
                                violation_kind=str(getattr(exc, "violation_kind", "")),
                            )
                        )
                    except Exception:
                        pass
                logger.error(
                    "章节 %d 一致性校验在 %d 次重规划后仍然失败（共 %d 次尝试），放弃重试",
                    chapter_number,
                    max_replans,
                    replan_count,
                )
                raise

            violations = list(getattr(exc, "violations", []) or [])
            replan_context = build_replan_context(
                exc=exc,
                previous_plan=None,
                replan_history=replan_history,
            )
            replan_history.append(
                SimpleNamespace(
                    attempt_number=replan_context.attempt_number,
                    failed_stage=getattr(exc, "failed_stage", ""),
                    failure_kind=getattr(exc, "violation_kind", ""),
                    violations=violations[:10],
                    actionable_guidance=list(replan_context.actionable_guidance),
                )
            )
            replan_notes = _format_replan_notes(replan_notes, violations)
            logger.warning(
                "章节 %d 一致性校验未通过，开始第 %d/%d 次自动重规划 | violations=%s",
                chapter_number,
                replan_count,
                max_replans,
                violations[:3],
            )

            # ── Record replan event ──────────────────────────────────────
            trace.status = "replanned"
            if _ledger is not None:
                try:
                    from novel_forge.obs.repair_event_ledger import (
                        AttemptStatus,
                        make_attempt_complete_event,
                    )

                    _ledger.append(
                        make_attempt_complete_event(
                            attempt_id=attempt_id,
                            chapter_number=chapter_number,
                            timestamp=time.time(),
                            replan_attempt=replan_count - 1,
                            status=AttemptStatus.REPLANNED,
                            violation_kind=str(getattr(exc, "violation_kind", "")),
                        )
                    )
                except Exception:
                    pass

            # Inject violation constraints into the chapter outline so the
            # plan step can address them explicitly in the next attempt.
            bundle.replan_context = replan_context
            _apply_replan_notes_to_bundle(bundle, replan_notes)
            introduced = await _introduce_characters_for_bundle(
                ctx,
                bundle,
                chapter_number,
                trace,
                introduced,
            )

            # Rebuild context / memory for a clean pipeline execution.
            context = ctx  # frozen dataclass, safe to reuse
            memory = StoryMemoryManager(
                storage=ctx._storage,
                retriever=ctx._retriever,
                memory_context=ctx.memory_context,
            )

    # ── Phase 2: refine newly-introduced profiles from the actual chapter text ──
    if introduced and getattr(ctx._settings, "auto_introduce_characters", True):
        try:
            enriched = await enrich_introduced_characters(
                router=ctx._router,
                builder=ctx._builder,
                storage=ctx._storage,
                settings=ctx._settings,
                bundle=bundle,
                chapter_number=chapter_number,
                chapter_text=result.text,
                introduced_names=introduced,
                trace=trace,
                on_step=ctx._on_step,
            )
            if enriched:
                logger.info(
                    "章节 %d 后置角色档案精化完成：%s",
                    chapter_number,
                    "、".join(enriched),
                )
        except Exception as exc:
            # Enrichment failure must not affect the chapter result
            logger.warning(
                "enrich_introduced_characters 在章节 %d 中遇到异常（已跳过）：%s",
                chapter_number,
                exc,
            )

    # ── Phase 2b: auto-register characters discovered in creative report ──
    # Characters that the draft LLM introduced spontaneously (not from Phase 1
    # blueprint candidates) are listed in creative_report.new_characters with
    # should_add_to_bible=True.  Without this step they would never receive a
    # rich profile, causing "unknown character" inconsistencies in later chapters.
    if getattr(ctx._settings, "auto_introduce_characters", True):
        try:
            registered = await auto_register_from_creative_report(
                router=ctx._router,
                builder=ctx._builder,
                storage=ctx._storage,
                settings=ctx._settings,
                bundle=bundle,
                chapter_number=chapter_number,
                chapter_text=result.text,
                creative_report=result.creative_report,
                trace=trace,
                on_step=ctx._on_step,
                remaining_slots=max(
                    0,
                    _auto_introduce_limit(ctx._settings) - len(set(introduced)),
                ),
            )
            if registered:
                logger.info(
                    "章节 %d 草稿新发现角色自动建档完成：%s",
                    chapter_number,
                    "、".join(registered),
                )
        except Exception as exc:
            # Non-critical: don't let this block the result
            logger.warning(
                "auto_register_from_creative_report 在章节 %d 中遇到异常（已跳过）：%s",
                chapter_number,
                exc,
            )

    await _trigger_memory_update(
        ctx,
        bundle,
        project_id,
        chapter_number,
        chapter_result=result,
    )

    # Invalidate stale book-level repair queue items for this chapter.
    # After regeneration the old tickets target obsolete text.
    _invalidate_book_repair_queue_for_chapter(bundle, chapter_number)

    _emit_budget_status(ctx, chapter_number, trace)
    _check_style_trend(ctx, chapter_number, bundle)

    return result


def _emit_budget_status(
    ctx: ChapterExecutionContext, chapter_number: int, trace: PipelineTrace
) -> None:
    """Emit a budget_status on_step event if spending is approaching or exceeding limits.

    This surfaces the SpendingTracker state to the UI via on_step so users can
    react in real-time, instead of only seeing log warnings.
    """
    on_step = getattr(ctx, "_on_step", None)
    if not callable(on_step):
        return

    router = getattr(ctx, "_router", None)
    if router is None:
        return

    tracker = getattr(router, "spending_tracker", None)
    settings = getattr(ctx, "_settings", None)

    chapter_tokens = trace.total_tokens
    chapter_cost = trace.total_cost

    # Always emit per-chapter cost summary for UI display
    payload: dict[str, object] = {
        "chapter": chapter_number,
        "chapter_tokens": chapter_tokens,
        "chapter_cost_usd": round(chapter_cost, 6),
    }

    if tracker is not None and settings is not None:
        daily_limit = float(getattr(settings, "budget_daily_usd", 0.0) or 0.0)
        monthly_limit = float(getattr(settings, "budget_monthly_usd", 0.0) or 0.0)
        warn_threshold = float(getattr(settings, "budget_warn_threshold", 0.8) or 0.8)

        daily_spent = tracker.daily_spent
        monthly_spent = tracker.monthly_spent

        payload["daily_spent_usd"] = round(daily_spent, 4)
        payload["monthly_spent_usd"] = round(monthly_spent, 4)

        warning_triggered = False
        if daily_limit > 0:
            ratio = daily_spent / daily_limit
            payload["daily_budget_pct"] = round(ratio * 100, 1)
            if ratio >= 1.0:
                payload["budget_alert"] = "daily_exceeded"
                warning_triggered = True
            elif ratio >= warn_threshold:
                payload["budget_alert"] = "daily_warning"
                warning_triggered = True

        if not warning_triggered and monthly_limit > 0:
            ratio = monthly_spent / monthly_limit
            payload["monthly_budget_pct"] = round(ratio * 100, 1)
            if ratio >= 1.0:
                payload["budget_alert"] = "monthly_exceeded"
            elif ratio >= warn_threshold:
                payload["budget_alert"] = "monthly_warning"

    on_step("budget_status", payload)


# ── Style trend tracking ──────────────────────────────────────────────────────

_STYLE_TREND_LOOKBACK: int = 4  # 检查最近 N 章的风格评分
_STYLE_DECLINE_THRESHOLD: float = 1.5  # 下降超过此分值触发警告
_STYLE_MIN_DATA_POINTS: int = 2  # 至少需要几章才触发检测


def _check_style_trend(
    ctx: ChapterExecutionContext,
    chapter_number: int,
    bundle: "LongProjectBundle",
) -> None:
    """Read recent per-chapter eval reports and emit a warning if style score is declining.

    Relies on `EvalReport.scores` (dimension='style') written to disk by the
    evaluate step.  Silently no-ops when eval is disabled or reports are absent.
    """
    on_step = getattr(ctx, "_on_step", None)
    if not callable(on_step):
        return

    storage = getattr(ctx, "_storage", None)
    layout = getattr(bundle, "layout", None)
    if storage is None or layout is None:
        return

    recent_scores: list[tuple[int, float]] = []
    start_ch = max(1, chapter_number - _STYLE_TREND_LOOKBACK + 1)
    for ch in range(start_ch, chapter_number + 1):
        try:
            eval_path = layout.eval_report_path(ch)
            if not eval_path.exists():
                continue
            data: dict[str, Any] = storage.load_json(eval_path)
            for item in data.get("scores") or []:
                if isinstance(item, dict) and item.get("dimension") == "style":
                    score_val = item.get("score")
                    if score_val is not None:
                        recent_scores.append((ch, float(score_val)))
                    break
        except Exception:  # noqa: BLE001
            continue

    if len(recent_scores) < _STYLE_MIN_DATA_POINTS:
        on_step(
            "style_trend_skipped",
            {
                "chapter": chapter_number,
                "reason": "insufficient_data",
                "available_data_points": len(recent_scores),
                "required_minimum": _STYLE_MIN_DATA_POINTS,
                "lookback_window": _STYLE_TREND_LOOKBACK,
                "data_range": (
                    f"{recent_scores[0][0]}-{recent_scores[-1][0]}" if recent_scores else "none"
                ),
            },
        )
        return

    scores_only = [s for _, s in recent_scores]
    drop = scores_only[0] - scores_only[-1]

    if drop < _STYLE_DECLINE_THRESHOLD:
        return

    logger.warning(
        "章节 %d 检测到风格评分下降趋势：%.1f → %.1f（下降 %.1f 分，阈值 %.1f）",
        chapter_number,
        scores_only[0],
        scores_only[-1],
        drop,
        _STYLE_DECLINE_THRESHOLD,
    )
    on_step(
        "style_drift_warning",
        {
            "chapter": chapter_number,
            "recent_style_scores": [
                {"chapter": ch, "score": round(sc, 2)} for ch, sc in recent_scores
            ],
            "drop": round(drop, 2),
            "threshold": _STYLE_DECLINE_THRESHOLD,
        },
    )


# ── Memory pending marker helpers ─────────────────────────────────────────────


def write_memory_pending_marker(
    storage: Any,
    project_id: str,
    chapter_number: int,
    error: BaseException | None,
    *,
    chapter_text: str = "",
    creative_report_text: str = "",
) -> None:
    """Record a replayable memory failure in the project ArtifactManifest."""
    try:
        project_dir = storage.existing_project_dir(project_id)
        layout = ProjectLayout(project_dir)
        manifest = ArtifactManifest(storage, layout)
        resolved_text = str(chapter_text or "")
        if not resolved_text and layout.chapter_path(chapter_number).is_file():
            resolved_text = layout.chapter_path(chapter_number).read_text(encoding="utf-8")
        text_hash = _text_hash(resolved_text)
        record_finalization_pending(
            manifest,
            chapter_number=chapter_number,
            phase="memory",
            text_hash=text_hash,
            error=error or "Unknown error",
            metadata={
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "chapter_text_snapshot": _memory_pending_snapshot(resolved_text),
                "creative_report_text": _memory_pending_snapshot(
                    creative_report_text, max_chars=4000
                ),
            },
        )
        logger.info(
            "已写入记忆更新失败清单 | chapter=%d | path=%s",
            chapter_number,
            layout.states_dir / "artifact_manifest.json",
        )
    except Exception as exc:
        logger.warning(
            "写入记忆更新失败标记时出错 | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


def _write_memory_pending_marker(
    ctx: ChapterExecutionContext,
    project_id: str,
    chapter_number: int,
    error: BaseException | None,
    *,
    chapter_text: str = "",
    creative_report_text: str = "",
) -> None:
    storage = getattr(ctx, "_storage", None)
    if storage is None:
        return
    write_memory_pending_marker(
        storage,
        project_id,
        chapter_number,
        error,
        chapter_text=chapter_text,
        creative_report_text=creative_report_text,
    )


def _detect_kernel_persist_pending_marker(
    ctx: ChapterExecutionContext,
    project_id: str,
    current_chapter_number: int,
) -> None:
    """Emit a preflight warning when prior chapter kernel persistence was deferred."""
    storage = getattr(ctx, "_storage", None)
    on_step = getattr(ctx, "_on_step", None)
    if storage is None or not callable(on_step):
        return
    try:
        project_dir = storage.existing_project_dir(project_id)
    except Exception as exc:
        logger.debug(
            "kernel_persist_pending_detect_failed | chapter=%d | error=%s",
            current_chapter_number,
            exc,
        )
        return
    layout = ProjectLayout(project_dir)
    manifest = ArtifactManifest(storage, layout)
    states_dir = project_dir / "states"
    if not states_dir.exists():
        return

    for lookback in range(1, 6):
        pending_chapter = current_chapter_number - lookback
        if pending_chapter < 1:
            break
        marker_path = states_dir / f"kernel_persist_pending_ch{pending_chapter}.json"
        marker_payload: dict[str, Any] = {}
        if marker_path.exists():
            try:
                raw_marker = json.loads(marker_path.read_text(encoding="utf-8"))
                marker_payload = raw_marker if isinstance(raw_marker, dict) else {}
            except Exception as exc:
                marker_payload = {
                    "chapter_number": pending_chapter,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
        phase_key = chapter_finalization_artifact(pending_chapter, "story_kernel")
        phase_record = manifest.get(phase_key)
        if phase_record is None and marker_payload:
            text_hash = str(marker_payload.get("source_text_hash") or "")
            chapter_path = layout.chapter_path(pending_chapter)
            if not text_hash and chapter_path.is_file():
                text_hash = _text_hash(chapter_path.read_text(encoding="utf-8"))
            record_finalization_pending(
                manifest,
                chapter_number=pending_chapter,
                phase="story_kernel",
                text_hash=text_hash,
                error=str(marker_payload.get("error") or "legacy_kernel_pending"),
                metadata={**marker_payload, "migrated_from_legacy_marker": True},
            )
            phase_record = manifest.get(phase_key)
            try:
                marker_path.unlink()
            except OSError:
                pass
        if phase_record is None or phase_record.status not in {
            STATUS_FAILED,
            STATUS_NEEDS_REPAIR,
        }:
            continue
        marker_payload = {**phase_record.metadata, **marker_payload}
        on_step(
            "kernel_persist_pending_detected",
            {
                "current_chapter": current_chapter_number,
                "pending_chapter": pending_chapter,
                "manifest_path": str(manifest.path),
                "legacy_marker_path": str(marker_path) if marker_path.exists() else "",
                "requires_replay": bool(marker_payload.get("requires_replay", True)),
                "source_text_hash": marker_payload.get("source_text_hash", ""),
                "error_type": marker_payload.get("error_type", ""),
                "error": str(marker_payload.get("error", ""))[:500],
            },
        )
        logger.warning(
            "检测到章节 %d 的内核持久化待恢复清单，章节 %d 将保留状态漂移警告。",
            pending_chapter,
            current_chapter_number,
        )


def _clear_memory_pending_marker(
    ctx: ChapterExecutionContext,
    project_id: str,
    chapter_number: int,
) -> None:
    """Remove the pending marker after successful compensation."""
    try:
        layout = getattr(ctx, "_storage", None)
        if layout is None:
            return

        project_dir = layout.existing_project_dir(project_id)
        if project_dir is None:
            return

        marker_path = project_dir / "states" / f"chapter_{chapter_number}_memory_pending.json"
        if marker_path.exists():
            marker_path.unlink()
            logger.info(
                "已清除记忆更新失败标记 | chapter=%d",
                chapter_number,
            )
    except Exception as exc:
        logger.warning(
            "清除记忆更新失败标记时出错 | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


async def _check_and_compensate_memory_gap(
    ctx: ChapterExecutionContext,
    project_id: str,
    current_chapter_number: int,
) -> None:
    """Check for previous chapter's memory pending marker and attempt compensation.

    If a marker is found for chapter N-1 (or earlier), this function:
    1. Loads the chapter text from disk
    2. Calls finalize_chapter_memory() to index it
    3. Clears the marker on success

    This ensures Canon and Memory stay consistent even when memory update
    initially failed.
    """
    memory_ctx = getattr(ctx, "memory_context", None)
    if memory_ctx is None:
        return

    storage = getattr(ctx, "_storage", None)
    if storage is None:
        return

    try:
        project_dir = storage.existing_project_dir(project_id)
        layout = ProjectLayout(project_dir)
        manifest = ArtifactManifest(storage, layout)

        states_dir = project_dir / "states"
        if not states_dir.exists():
            return

        # ── INTENTIONAL DESIGN: Look back up to 5 chapters ──────────────────
        # Why 5: Memory update runs after every chapter. If we're at chapter N,
        # the most likely gap is at N-1. Chapters N-2 through N-5 are included
        # as a safety net for cases where multiple consecutive chapters failed
        # to update memory (e.g., batch runs with intermittent failures).
        # Going beyond 5 would be wasteful — longer gaps indicate a skipped
        # batch where memory was never needed (e.g., outline-only runs).
        # ─────────────────────────────────────────────────────────────────────
        for lookback in range(1, 6):
            prev_chapter = current_chapter_number - lookback
            if prev_chapter < 1:
                break

            marker_path = states_dir / f"chapter_{prev_chapter}_memory_pending.json"
            marker_data: dict[str, Any] = {}
            if marker_path.exists():
                try:
                    raw_marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    marker_data = raw_marker if isinstance(raw_marker, dict) else {}
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning(
                        "读取记忆更新失败标记时出错 | chapter=%d | error=%s",
                        prev_chapter,
                        exc,
                    )

            phase_key = chapter_finalization_artifact(prev_chapter, "memory")
            phase_record = manifest.get(phase_key)
            if phase_record is not None and phase_record.status not in {
                STATUS_FAILED,
                STATUS_NEEDS_REPAIR,
            }:
                if marker_path.exists():
                    _clear_memory_pending_marker(ctx, project_id, prev_chapter)
                continue
            if phase_record is None and not marker_data:
                continue
            if phase_record is None:
                legacy_text = ""
                chapter_path = layout.chapter_path(prev_chapter)
                if chapter_path.is_file():
                    legacy_text = chapter_path.read_text(encoding="utf-8")
                if not legacy_text:
                    legacy_text = str(marker_data.get("chapter_text_snapshot", "") or "")
                record_finalization_pending(
                    manifest,
                    chapter_number=prev_chapter,
                    phase="memory",
                    text_hash=_text_hash(legacy_text),
                    error=str(marker_data.get("error") or "legacy_memory_pending"),
                    metadata={**marker_data, "migrated_from_legacy_marker": True},
                )
                phase_record = manifest.get(phase_key)
                try:
                    marker_path.unlink()
                except OSError:
                    pass
            if phase_record is not None:
                marker_data = {**phase_record.metadata, **marker_data}

            logger.info(
                "检测到章节 %d 记忆更新失败标记，尝试补偿索引…",
                prev_chapter,
            )

            # Try to load the chapter text from disk
            try:
                chapter_path = layout.chapter_path(prev_chapter)
                chapter_text = ""
                if chapter_path.exists():
                    chapter_text = chapter_path.read_text(encoding="utf-8")
                else:
                    chapter_text = str(marker_data.get("chapter_text_snapshot", "") or "")
                    if chapter_text:
                        logger.warning(
                            "无法找到章节 %d 的文本文件，使用 pending 标记中的正文快照补偿记忆索引",
                            prev_chapter,
                        )
                    else:
                        logger.warning(
                            "无法找到章节 %d 的文本文件，且 pending 标记无正文快照，无法补偿记忆索引",
                            prev_chapter,
                        )
                        break
                if not chapter_text.strip():
                    chapter_text = str(marker_data.get("chapter_text_snapshot", "") or "")
                if not chapter_text.strip():
                    logger.warning(
                        "章节 %d 文本为空，跳过补偿",
                        prev_chapter,
                    )
                    break

                creative_report_text = ""
                try:
                    creative_report = storage.load_json(layout.creative_report_path(prev_chapter))
                    creative_report_text = str(creative_report.get("summary", "") or "")
                except Exception as exc:
                    creative_report_text = str(marker_data.get("creative_report_text", "") or "")
                    logger.debug(
                        "补偿章节 %d 记忆时未能读取创作报告摘要：%s",
                        prev_chapter,
                        exc,
                    )

                memory_result = await finalize_chapter_memory_phase(
                    storage=storage,
                    layout=layout,
                    memory_context=memory_ctx,
                    chapter_number=prev_chapter,
                    chapter_text=chapter_text,
                    creative_report_text=creative_report_text,
                    on_step=None,
                )

                if memory_result.completed:
                    logger.info(
                        "章节 %d 记忆补偿成功 | status=%s | tasks_ok=%s",
                        prev_chapter,
                        memory_result.status,
                        memory_result.stats.get("tasks_ok"),
                    )
                    _clear_memory_pending_marker(ctx, project_id, prev_chapter)

                    # Notify UI
                    on_step = getattr(ctx, "_on_step", None)
                    if callable(on_step):
                        on_step(
                            "memory_gap_compensated",
                            {
                                "chapter": prev_chapter,
                                "current_chapter": current_chapter_number,
                                "stats": memory_result.stats,
                            },
                        )
                else:
                    logger.warning(
                        "章节 %d 记忆补偿失败，保留 pending manifest 供后续重试",
                        prev_chapter,
                    )

            except Exception as exc:
                logger.warning(
                    "章节 %d 记忆补偿时出错，保留标记文件： %s",
                    prev_chapter,
                    exc,
                )

            # Only compensate for the most recent failed chapter
            break

    except Exception as exc:
        logger.warning(
            "检查记忆更新失败标记时出错 | chapter=%d | error=%s",
            current_chapter_number,
            exc,
        )


async def compensate_memory_gap(
    ctx: ChapterExecutionContext,
    project_id: str,
    current_chapter_number: int,
) -> None:
    """Public wrapper for chapter entrypoints that need memory-gap compensation."""
    await _check_and_compensate_memory_gap(ctx, project_id, current_chapter_number)
