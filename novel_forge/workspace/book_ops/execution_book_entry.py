"""Main compatibility entry point for whole-book consistency audit."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_audit_checkpoint_store import (
    BookAuditCheckpointStore,
    _book_audit_resume_signature,
    _load_final_audit_result_checkpoint,
    _load_two_phase_summary_checkpoint,
    _read_audit_checkpoint_payload,
    _save_final_audit_result_checkpoint,
    _save_two_phase_summary_checkpoint,
    _update_audit_checkpoint_payload,
    _write_audit_checkpoint_payload,
    book_audit_checkpoint_path,
    create_book_audit_snapshot,
    invalidate_checkpoint,
    restore_book_audit_snapshot,
    summarize_book_audit_checkpoint,
    validate_checkpoint,
)
from novel_forge.workspace.book_ops.execution_book_audit_helpers import (
    _audit_issue_identity,
    _audit_issue_summary,
    _audit_score,
    _audit_severity_counts,
    _audit_severity_weight,
    _classify_audit_exception,
    _compare_audit_reports,
    _derive_book_audit_artifact_summary,
    _extract_flagged_chapters_from_result,
    _file_digest,
    _issue_as_dict,
    _load_chapter_texts_for_numbers,
    _merge_two_phase_audit_results,
    _normalize_audit_report_payload,
    _rank_book_audit_target_chapters,
    _stable_json_digest,
)
from novel_forge.workspace.book_ops.execution_book_audit_legacy import (
    _execute_book_consistency_legacy,
)
from novel_forge.workspace.book_ops.execution_book_audit_post_repair import (
    _applied_book_repair_chapters,
    _lightweight_evidence_verify,
    _post_repair_audit_target_chapters,
    _refresh_book_audit_staleness_markers,
    _run_post_repair_targeted_audit,
)
from novel_forge.workspace.book_ops.execution_book_audit_report import (
    _attach_previous_audit_delta,
    _load_latest_audit_payload,
    _save_canonical_audit_report,
    _save_versioned_audit_report,
    _update_audit_index,
    _update_audit_symlink,
)
from novel_forge.workspace.book_ops.execution_book_audit_runner import run_book_consistency_audit
from novel_forge.workspace.book_ops.execution_book_common import (
    _load_issue_panel_pool_for_chapters,
    _log,
    _resolve_book_audit_analysis_mode,
    _resolve_issue_pool_ttl_hours,
)
from novel_forge.workspace.book_ops.execution_book_precision import (
    prepare_report_issues_for_precision_repair,
)
from novel_forge.workspace.book_ops.execution_book_repair import (
    _build_book_consistency_repair_report_payload,
    _run_book_consistency_auto_repair,
)
from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify
from novel_forge.workspace.contracts import BookConsistencyRequest, GlobalRepairQueueRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_manifest import (
    record_entry_failure,
    record_entry_success,
    wrap_manifest_step_callback,
)
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.runtime import RuntimeServices

__all__ = [
    "BookAuditCheckpointStore",
    "execute_book_consistency",
    "execute_global_repair_queue",
    "run_book_consistency_audit",
    "_applied_book_repair_chapters",
    "_attach_previous_audit_delta",
    "_audit_issue_identity",
    "_audit_issue_summary",
    "_audit_score",
    "_audit_severity_counts",
    "_audit_severity_weight",
    "_book_audit_resume_signature",
    "_build_book_consistency_repair_report_payload",
    "_classify_audit_exception",
    "_compare_audit_reports",
    "_derive_book_audit_artifact_summary",
    "_execute_book_consistency_legacy",
    "_extract_flagged_chapters_from_result",
    "_file_digest",
    "_issue_as_dict",
    "_lightweight_evidence_verify",
    "_load_chapter_texts_for_numbers",
    "_load_final_audit_result_checkpoint",
    "_load_latest_audit_payload",
    "_load_two_phase_summary_checkpoint",
    "_merge_two_phase_audit_results",
    "_normalize_audit_report_payload",
    "_post_repair_audit_target_chapters",
    "_rank_book_audit_target_chapters",
    "_read_audit_checkpoint_payload",
    "_refresh_book_audit_staleness_markers",
    "_run_book_consistency_auto_repair",
    "_run_book_consistency_verify",
    "_run_post_repair_targeted_audit",
    "_save_canonical_audit_report",
    "_save_final_audit_result_checkpoint",
    "_save_two_phase_summary_checkpoint",
    "_save_versioned_audit_report",
    "_stable_json_digest",
    "_update_audit_checkpoint_payload",
    "_update_audit_index",
    "_update_audit_symlink",
    "_write_audit_checkpoint_payload",
    "book_audit_checkpoint_path",
    "create_book_audit_snapshot",
    "invalidate_checkpoint",
    "restore_book_audit_snapshot",
    "summarize_book_audit_checkpoint",
    "validate_checkpoint",
]


async def execute_book_consistency(
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    """Run the global whole-book audit without applying text edits.

    The whole-book entry point is now read-only with respect to chapter text:
    it indexes the project, plans global slices, audits those slices, stores
    evidence and repair queue items, then writes the audit report. Executing
    the repair queue is a separate workflow.
    """

    from novel_forge.workspace.authoring_control import authoring_review

    with authoring_review(runtime, request):
        return await _execute_book_consistency(runtime, request, on_step_progress=on_step_progress)


async def _execute_book_consistency(
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    result: Any
    progress = wrap_manifest_step_callback(
        runtime.storage,
        request.project_id,
        "book_consistency",
        on_step_progress,
    )
    try:
        result = await run_book_consistency_audit(
            runtime=runtime,
            request=request,
            on_step_progress=progress,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        checkpoint_exists = False
        try:
            layout_check = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
            checkpoint_exists = book_audit_checkpoint_path(layout_check).exists()
        except Exception:
            pass
        audit_error = _classify_audit_exception(exc, checkpoint_exists=checkpoint_exists)
        record_entry_failure(
            runtime.storage,
            request.project_id,
            "book_consistency",
            audit_error,
            metadata={"checkpoint_exists": checkpoint_exists},
        )
        raise audit_error from exc

    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    if request.chapter_range:
        completed_chapters = sorted(request.chapter_range)
    else:
        completed_chapters = []
        for path in sorted(layout.chapters_dir.glob("chapter_*.md")):
            try:
                completed_chapters.append(int(path.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue

    analysis_mode = _resolve_book_audit_analysis_mode(runtime, request)
    max_tokens = int(
        request.max_tokens
        if request.max_tokens is not None
        else int(getattr(runtime.settings, "long_book_audit_max_tokens", 8192))
    )
    temperature = float(
        request.temperature
        if request.temperature is not None
        else float(getattr(runtime.settings, "temp_book_consistency", 0.2))
    )
    use_issue_panel_pool = bool(
        getattr(
            request,
            "use_issue_panel_pool",
            bool(getattr(runtime.settings, "long_book_audit_use_issue_panel_pool", True)),
        )
    )
    chapter_issue_pool = (
        _load_issue_panel_pool_for_chapters(
            storage=runtime.storage,
            layout=layout,
            chapter_numbers=completed_chapters,
            ttl_hours=_resolve_issue_pool_ttl_hours(runtime.settings),
        )
        if use_issue_panel_pool
        else []
    )

    payload = (
        result.model_dump(mode="json")
        if hasattr(result, "model_dump")
        else {
            "issues": getattr(result, "issues", []),
            "summary": getattr(result, "summary", ""),
        }
    )
    payload["request"] = {
        "architecture": "global_audit_v1",
        "chapter_range": completed_chapters,
        "analysis_mode": analysis_mode,
        "location_strictness": str(
            getattr(request, "location_strictness", "balanced") or "balanced"
        ),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "audit_max_chapters_per_batch": int(
            getattr(
                request,
                "audit_max_chapters_per_batch",
                int(getattr(runtime.settings, "long_book_audit_max_chapters_per_batch", 12)),
            )
            or 12
        ),
        "audit_max_issues_per_chunk": int(getattr(request, "audit_max_issues_per_chunk", 12) or 12),
        "use_issue_panel_pool": use_issue_panel_pool,
        "issue_pool_size": len(chapter_issue_pool),
        "continue_audit_from_checkpoint": bool(
            getattr(request, "continue_audit_from_checkpoint", False)
        ),
        "global_audit_db_path": str(layout.global_audit_db_path),
        "repair_queue_execution": "separate_workflow",
        "memory_enhancement_failed": bool(getattr(result, "memory_enhancement_failed", False)),
    }
    prepared_issues, repair_readiness = prepare_report_issues_for_precision_repair(
        list(payload.get("issues", []) or []),
        completed_chapters=completed_chapters,
        layout=layout,
        report_payload=payload,
    )
    payload["issues"] = prepared_issues
    payload["repair_readiness"] = repair_readiness
    payload.setdefault("auto_repair", None)
    payload = _normalize_audit_report_payload(payload)
    audit_timestamp = _save_versioned_audit_report(layout, report_payload=payload)
    _update_audit_index(
        layout,
        audit_timestamp,
        chapter_count=len(completed_chapters),
        issue_count=len(payload.get("issues", [])),
        analysis_mode=analysis_mode,
    )
    _update_audit_symlink(layout, audit_timestamp)
    report_path: Path | None = None
    async with _project_lock(runtime, request.project_id):
        report_path = _save_canonical_audit_report(layout, payload)

    if progress:
        progress(
            "book_consistency_report_written",
            {
                "status": "done",
                "path": str(report_path) if report_path else "",
                "architecture": "global_audit_v1",
                "repair_queue_summary": payload.get("repair_queue_summary") or {},
            },
        )

    _refresh_book_audit_staleness_markers(
        storage=runtime.storage,
        layout=layout,
        auto_repair_payload=None,
    )

    record_entry_success(
        runtime.storage,
        request.project_id,
        "book_consistency",
        result=result,
        metadata={
            "chapter_count": len(completed_chapters),
            "issue_count": len(payload.get("issues", [])),
            "analysis_mode": analysis_mode,
        },
    )
    return ExecutionResult(project_id=request.project_id, result=result)


async def execute_global_repair_queue(
    runtime: RuntimeServices,
    request: GlobalRepairQueueRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    """Execute ready repair tickets produced by the global audit queue."""

    from novel_forge.core.review.review_contracts import source_text_hash
    from novel_forge.pipeline.repair_orchestration.mission_factory import issues_repair_mission
    from novel_forge.workspace.repair_ops.execution_repair_v2 import execute_repair

    progress = wrap_manifest_step_callback(
        runtime.storage,
        request.project_id,
        "global_repair_queue",
        on_step_progress,
    )
    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    from novel_forge.workspace.global_audit import GlobalAuditStore

    store = GlobalAuditStore(layout.global_audit_db_path)
    run_id = request.run_id or store.latest_completed_run_id(request.project_id)
    if not run_id:
        exc = ValueError("未找到可执行的全书审计修复队列；请先运行全书审计。")
        record_entry_failure(runtime.storage, request.project_id, "global_repair_queue", exc)
        raise exc

    requested_statuses = [str(status) for status in request.statuses if str(status)]
    unsupported_statuses = sorted({status for status in requested_statuses if status != "ready"})
    if unsupported_statuses:
        exc = ValueError(
            "自动执行修复队列只允许处理 ready 状态；"
            f"不支持的状态：{', '.join(unsupported_statuses)}。"
            "其他状态需人工处理或先运行验证。"
        )
        record_entry_failure(runtime.storage, request.project_id, "global_repair_queue", exc)
        raise exc
    allowed_statuses = [status for status in requested_statuses if status == "ready"]
    if not allowed_statuses:
        exc = ValueError("自动执行修复队列只允许处理 ready 状态；其他状态需人工或验证优先。")
        record_entry_failure(runtime.storage, request.project_id, "global_repair_queue", exc)
        raise exc

    queue_items = store.load_repair_queue_items(
        run_id=run_id,
        statuses=allowed_statuses,
        limit=request.max_items,
    )
    from novel_forge.persistence.authoring_store import AuthoringStore

    if AuthoringStore(layout.root).policy() is not None:
        from novel_forge.workspace.authoring_control import authoring_review
        from novel_forge.workspace.book_ops.execution_book_candidate_batch import (
            prepare_book_repair_candidate_batch,
        )

        target_chapter_set: set[int] = set()
        for item in queue_items:
            try:
                chapter_number = int(item.get("target_chapter") or 0)
            except (TypeError, ValueError):
                continue
            if chapter_number > 0:
                target_chapter_set.add(chapter_number)
        target_chapters = sorted(target_chapter_set)
        review_request = BookConsistencyRequest(
            project_id=request.project_id,
            chapter_range=target_chapters,
            analysis_mode="full_text",
            repair_mode="off",
        )
        with authoring_review(runtime, review_request):
            candidate_summary = await prepare_book_repair_candidate_batch(
                runtime=runtime,
                request=request,
                run_id=run_id,
                queue_items=queue_items,
                on_step_progress=progress,
            )
        record_entry_success(
            runtime.storage,
            request.project_id,
            "global_repair_queue",
            result=candidate_summary,
            metadata={
                "run_id": run_id,
                "mode": "candidate_batch",
                "formal_writes": 0,
                "case_count": len(candidate_summary.get("case_ids", [])),
            },
        )
        return ExecutionResult(project_id=request.project_id, result=candidate_summary)

    from novel_forge.persistence.foundation_guard import require_versioned_maintenance_write

    require_versioned_maintenance_write(layout.root, "直接执行全书修订队列")
    summary: dict[str, Any] = {
        "run_id": run_id,
        "requested": len(queue_items),
        "processed": 0,
        "applied": 0,
        "blocked": 0,
        "failed": 0,
        "skipped": 0,
        "results": [],
        "concurrency": request.concurrency,
    }

    def _ticket_to_issue(ticket: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        metadata = ticket.get("metadata") if isinstance(ticket.get("metadata"), dict) else {}
        paragraph_start = int(ticket.get("target_paragraph_start") or 0)
        paragraph_end = int(ticket.get("target_paragraph_end") or paragraph_start)
        return {
            "issue_id": item.get("finding_id") or (ticket.get("finding_ids") or [""])[0],
            "severity": ticket.get("severity") or "warning",
            "issue_type": ticket.get("issue_type") or ticket.get("dimension") or "global_audit",
            "category": ticket.get("dimension") or "global_audit",
            "description": ticket.get("target_summary") or ticket.get("repair_goal") or "",
            "summary": ticket.get("target_summary") or ticket.get("repair_goal") or "",
            "evidence": metadata.get("evidence_quote") or "",
            "primary_chapter": item.get("target_chapter") or ticket.get("chapter_number") or 0,
            "chapters_involved": [item.get("target_chapter") or ticket.get("chapter_number") or 0],
            "paragraph_index": paragraph_start,
            "paragraph_span": [paragraph_start, max(paragraph_start, paragraph_end)]
            if paragraph_start > 0
            else [],
            "repair_readiness": {
                "status": "ready",
                "auto_repair_eligible": True,
                "reasons": ["global_repair_queue_ready"],
            },
            "repair_anchor": metadata.get("repair_anchor") or {},
            "repair_goal": ticket.get("repair_goal") or ticket.get("target_summary") or "",
            "acceptance_criteria": ticket.get("acceptance_criteria") or [],
            "must_preserve": ticket.get("must_preserve") or [],
            "forbidden_changes": ticket.get("forbidden_changes") or [],
            "source_text_hash": ticket.get("source_text_hash") or item.get("source_hash") or "",
        }

    queue_concurrency = max(1, min(int(request.concurrency or 1), max(1, len(queue_items))))
    summary["concurrency"] = queue_concurrency
    queue_sem = asyncio.Semaphore(queue_concurrency)
    chapter_locks: dict[int, asyncio.Lock] = {}

    def _chapter_lock(chapter_number: int) -> asyncio.Lock:
        lock = chapter_locks.get(chapter_number)
        if lock is None:
            lock = asyncio.Lock()
            chapter_locks[chapter_number] = lock
        return lock

    def _result_with_delta(
        record: dict[str, Any],
        *,
        processed: int = 0,
        applied: int = 0,
        blocked: int = 0,
        failed: int = 0,
        skipped: int = 0,
    ) -> dict[str, Any]:
        result = dict(record)
        result["_summary_delta"] = {
            "processed": processed,
            "applied": applied,
            "blocked": blocked,
            "failed": failed,
            "skipped": skipped,
        }
        return result

    async def _process_queue_item(item: dict[str, Any]) -> dict[str, Any]:
        ticket = item.get("ticket") if isinstance(item.get("ticket"), dict) else {}
        queue_item_id = str(item.get("queue_item_id") or "")
        target_chapter = int(item.get("target_chapter") or 0)
        attempt_count = int(item.get("attempt_count") or 0) + 1
        result_record: dict[str, Any] = {
            "queue_item_id": queue_item_id,
            "target_chapter": target_chapter,
            "status": "skipped",
        }
        if not ticket or target_chapter <= 0:
            result_record["reason"] = "missing_ticket_or_target"
            store.update_repair_queue_item(
                run_id=run_id,
                queue_item_id=queue_item_id,
                status="manual_review",
                attempt_count=attempt_count,
                last_verification_result=result_record,
            )
            return _result_with_delta(result_record, skipped=1)

        chapter_path = layout.chapter_path(target_chapter)
        current_text = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
        current_hash = source_text_hash(current_text)
        expected_hash = str(item.get("source_hash") or ticket.get("source_text_hash") or "")
        if request.verify_before_apply and expected_hash and current_hash != expected_hash:
            summary["blocked"] += 1
            result_record.update(
                {
                    "status": "blocked",
                    "reason": "source_hash_changed",
                    "expected_hash": expected_hash,
                    "current_hash": current_hash,
                }
            )
            store.update_repair_queue_item(
                run_id=run_id,
                queue_item_id=queue_item_id,
                status="blocked",
                attempt_count=attempt_count,
                last_verification_result=result_record,
            )
            store.insert_verification_result(
                run_id=run_id,
                queue_item_id=queue_item_id,
                target_chapter=target_chapter,
                source_hash=current_hash,
                status="blocked",
                result=result_record,
            )
            return _result_with_delta(result_record, blocked=1)

        synthetic_issue = _ticket_to_issue(ticket, item)
        dimension = str(ticket.get("dimension") or "").strip().lower()
        causal_dimensions = {"timeline_arc", "promise_payoff", "plot_thread_liveness"}
        repair_request_kwargs: dict[str, Any] = {
            "project_id": request.project_id,
            "chapter_number": target_chapter,
            "allow_exhausted_retry": False,
        }
        if dimension in causal_dimensions:
            repair_request_kwargs["causal_synthetic_issues"] = [synthetic_issue]
        else:
            repair_request_kwargs["continuity_synthetic_issues"] = [synthetic_issue]

        repair_snapshot: dict[str, Any] | None = None
        try:
            from novel_forge.workspace.contracts import RepairIssuesRequest

            if request.rollback_on_failure:
                repair_snapshot = create_book_audit_snapshot(layout)
            repair_request = RepairIssuesRequest(**repair_request_kwargs)
            repair_mission = issues_repair_mission(repair_request, settings=runtime.settings)
            execution = await execute_repair(
                runtime,
                repair_mission,
                on_step_progress=progress,
            )
            applied = bool(getattr(execution.result, "applied", False))
            final_text = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
            text_changed = current_hash != source_text_hash(final_text)

            # ── Post-repair lightweight verification ──────────────────
            # When repair was applied, check if the original issue evidence
            # is still present in the repaired text. If so, the repair did
            # not resolve the problem and needs re-audit.
            final_status = "completed"
            if applied:
                try:
                    from novel_forge.workspace.book_ops.execution_book_audit_post_repair import (
                        _lightweight_evidence_verify,
                    )

                    _verify_issue = dict(synthetic_issue)
                    _verify_issue.setdefault("primary_chapter", target_chapter)
                    _filtered, _vstats = _lightweight_evidence_verify(
                        [_verify_issue],
                        [target_chapter],
                        layout,
                        prefer_official_text=True,
                    )
                    # If the issue was confirmed (evidence still present),
                    # the repair did not fix the problem.
                    if _vstats.get("confirmed", 0) > 0:
                        final_status = "needs_reaudit"
                        result_record["post_repair_verify"] = "evidence_still_present"
                except Exception:
                    pass  # Verification failure does not block the flow

            if applied and text_changed and final_status == "completed":
                from novel_forge.persistence.project_staleness import InvalidationScope
                from novel_forge.workspace.execution_manual_revision import (
                    _mark_manual_revision_requires_refresh,
                    _write_revision_version,
                )

                version = _write_revision_version(
                    layout=layout,
                    project_id=request.project_id,
                    chapter_number=target_chapter,
                    previous_text=current_text,
                    current_text=final_text,
                    reason=f"global_repair_queue:{queue_item_id}",
                )
                revision_status = _mark_manual_revision_requires_refresh(
                    storage=runtime.storage,
                    layout=layout,
                    project_id=request.project_id,
                    chapter_number=target_chapter,
                    version=version,
                    scope=InvalidationScope.DOWNSTREAM,
                    reason=f"global_repair_queue:{queue_item_id}",
                )
                final_status = "needs_finalize"
                result_record.update(
                    {
                        "requires_humanize": True,
                        "requires_final_verification": True,
                        "requires_state_replay": True,
                        "revision_id": revision_status["revision_id"],
                        "revision_status_path": str(
                            layout.states_dir
                            / "final_revision_status"
                            / f"chapter_{target_chapter:03d}.json"
                        ),
                    }
                )

            result_record.update(
                {"status": final_status, "applied": applied, "text_changed": text_changed}
            )
            store.update_repair_queue_item(
                run_id=run_id,
                queue_item_id=queue_item_id,
                status=final_status,
                attempt_count=attempt_count,
                last_verification_result=result_record,
            )
            store.insert_verification_result(
                run_id=run_id,
                queue_item_id=queue_item_id,
                target_chapter=target_chapter,
                source_hash=source_text_hash(
                    chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
                ),
                status=final_status,
                result=result_record,
            )
            return _result_with_delta(result_record, processed=1, applied=int(applied))
        except Exception as exc:
            rolled_back = False
            rollback_error = ""
            if request.rollback_on_failure and repair_snapshot is not None:
                try:
                    restore_book_audit_snapshot(layout, repair_snapshot)
                    rolled_back = True
                except Exception as rollback_exc:  # pragma: no cover - defensive logging path
                    rollback_error = str(rollback_exc)[:500]
                    _log.warning(
                        "Global repair queue rollback failed: %s",
                        rollback_exc,
                        exc_info=True,
                    )
            result_record.update(
                {
                    "status": "failed",
                    "error": str(exc)[:500],
                    "rolled_back": rolled_back,
                }
            )
            if rollback_error:
                result_record["rollback_error"] = rollback_error
            store.update_repair_queue_item(
                run_id=run_id,
                queue_item_id=queue_item_id,
                status="failed",
                attempt_count=attempt_count,
                last_verification_result=result_record,
            )
            store.insert_verification_result(
                run_id=run_id,
                queue_item_id=queue_item_id,
                target_chapter=target_chapter,
                source_hash=current_hash,
                status="failed",
                result=result_record,
            )
            if request.rollback_on_failure:
                _log.warning("Global repair queue item failed: %s", exc, exc_info=True)
            return _result_with_delta(result_record, failed=1)

    async def _process_with_limits(item: dict[str, Any]) -> dict[str, Any]:
        target_chapter = int(item.get("target_chapter") or 0)
        async with queue_sem:
            async with _chapter_lock(target_chapter):
                return await _process_queue_item(item)

    processed_results = await asyncio.gather(*[_process_with_limits(item) for item in queue_items])
    for result_record in processed_results:
        delta = result_record.pop("_summary_delta", {})
        if isinstance(delta, dict):
            for key in ("processed", "applied", "blocked", "failed", "skipped"):
                summary[key] += int(delta.get(key, 0) or 0)
        summary["results"].append(result_record)

    if progress:
        progress("global_repair_queue_done", summary)

    record_entry_success(
        runtime.storage,
        request.project_id,
        "global_repair_queue",
        result=summary,
        metadata={
            "run_id": run_id,
            "processed": summary["processed"],
            "applied": summary["applied"],
            "failed": summary["failed"],
        },
    )
    return ExecutionResult(project_id=request.project_id, result=summary)
