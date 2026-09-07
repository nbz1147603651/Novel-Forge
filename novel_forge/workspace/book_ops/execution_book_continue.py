"""Continue-repair: resume from a prior audit report."""

from __future__ import annotations

from typing import Any

from novel_forge.core.utils.coerce import coerce_float
from novel_forge.workspace.book_ops.execution_book_common import (
    _load_issue_panel_pool_for_chapters,
    _resolve_issue_pool_ttl_hours,
)
from novel_forge.workspace.book_ops.execution_book_precision import (
    prepare_report_issues_for_precision_repair,
    recover_completed_audit_payload_from_checkpoint,
)
from novel_forge.workspace.book_ops.execution_book_recovery import (
    create_book_audit_snapshot,
    restore_book_audit_snapshot,
)
from novel_forge.workspace.book_ops.execution_book_repair import (
    _build_book_consistency_repair_report_payload,
    _run_book_consistency_auto_repair,
)
from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify
from novel_forge.workspace.book_repair_metrics import (
    book_repair_detail_was_applied,
    dedupe_book_repair_details,
    summarize_book_repair_details,
)
from novel_forge.workspace.contracts import BookConsistencyRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_helpers import _build_numbered_paragraph_text
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.runtime import RuntimeServices

_book_repair_detail_was_applied = book_repair_detail_was_applied


def _coerce_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        values = value
    else:
        values = [value]

    result: list[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _reconstruct_book_consistency_issues(items: list[Any]) -> list[Any]:
    """Rebuild ConsistencyIssue objects from an audit JSON report."""
    from novel_forge.pipeline.steps.book_consistency_step import ConsistencyIssue

    issues: list[ConsistencyIssue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chapters = _coerce_int_list(item.get("chapters_involved"))
        try:
            primary_chapter = int(item.get("primary_chapter", 0) or 0)
        except (TypeError, ValueError):
            primary_chapter = 0
        if primary_chapter <= 0 and chapters:
            primary_chapter = chapters[0]
        elif primary_chapter > 0 and primary_chapter not in chapters:
            chapters.insert(0, primary_chapter)

        paragraph_span = _coerce_int_list(item.get("paragraph_span"))
        paragraph_index_raw = item.get("paragraph_index", 0)
        if isinstance(paragraph_index_raw, list):
            paragraph_index_raw = paragraph_index_raw[0] if paragraph_index_raw else 0
        try:
            paragraph_index = int(paragraph_index_raw or 0)
        except (TypeError, ValueError):
            paragraph_index = 0

        confidence = coerce_float(item.get("confidence", 0.0) or 0.0)

        linked_refs = item.get("linked_issue_refs")
        if not isinstance(linked_refs, list):
            linked_refs = []

        issues.append(
            ConsistencyIssue(
                issue_id=str(item.get("issue_id", "") or ""),
                category=str(item.get("category") or item.get("issue_type") or "unknown"),
                severity=str(item.get("severity") or "warning"),
                chapters_involved=chapters,
                description=str(item.get("description") or item.get("evidence") or ""),
                suggestion=str(item.get("suggestion", "") or ""),
                issue_type=str(item.get("issue_type", "") or ""),
                primary_chapter=primary_chapter,
                location=str(item.get("location", "") or ""),
                paragraph_index=paragraph_index,
                paragraph_span=paragraph_span,
                evidence=str(item.get("evidence", "") or ""),
                fix_mode=str(item.get("fix_mode", "repair_continuity") or "repair_continuity"),
                fix_action=str(item.get("fix_action", "rewrite") or "rewrite"),
                confidence=confidence,
                linked_issue_refs=[ref for ref in linked_refs if isinstance(ref, dict)],
            )
        )
    return issues


async def _continue_repair_from_audit(
    *,
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    layout: Any,
    completed_chapters: list[int],
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    """Load a prior audit report and run repair (+ optional verify) for remaining chapters.

    When a previous audit found more issue chapters than ``repair_max_chapters``
    allowed, the ``excluded_chapters`` list records what was skipped.  This
    function loads that report and runs repair only on those previously excluded
    chapters (or all issue chapters if the user raised max_chapters).
    """
    from novel_forge.pipeline.steps.book_consistency_step import BookConsistencyResult

    audit_path = layout.reports_dir / "book_consistency_audit.json"
    audit_data = runtime.storage.load_json(audit_path)
    if not isinstance(audit_data, dict):
        raise ValueError("审计报告格式异常。")

    recovered_audit_data = recover_completed_audit_payload_from_checkpoint(layout, audit_data)
    if recovered_audit_data is not None:
        current_issue_count = len(audit_data.get("issues") or [])
        recovered_issue_count = len(recovered_audit_data.get("issues") or [])
        if recovered_issue_count > current_issue_count:
            audit_data = recovered_audit_data
            runtime.storage.save_json(audit_path, audit_data)

    # Reconstruct a lightweight BookConsistencyResult for the return value
    issues_raw = audit_data.get("issues", [])
    if not isinstance(issues_raw, list):
        issues_raw = []
    issues_raw, repair_readiness = prepare_report_issues_for_precision_repair(
        issues_raw,
        completed_chapters=completed_chapters,
        layout=layout,
        report_payload=audit_data,
    )
    audit_data["issues"] = issues_raw
    audit_data["repair_readiness"] = repair_readiness
    if repair_readiness.get("status") == "needs_canonical_report":
        raise ValueError("审计报告不是完整 canonical 报告，请先从 checkpoint 恢复或重新运行审计。")
    runtime.storage.save_json(audit_path, audit_data)
    reconstructed_issues = _reconstruct_book_consistency_issues(issues_raw)
    result = BookConsistencyResult(
        issues=reconstructed_issues,
        summary=str(audit_data.get("summary", "") or ""),
        consistency_score=coerce_float(audit_data.get("consistency_score", 0.0) or 0.0),
        analysis_mode=str(audit_data.get("analysis_mode", "summary") or "summary"),
        chapters_audited=_coerce_int_list(audit_data.get("chapters_audited", [])),
        truncated_chapters=_coerce_int_list(audit_data.get("truncated_chapters", [])),
        repair_plan=(
            list(audit_data.get("repair_plan", []) or [])
            if isinstance(audit_data.get("repair_plan"), list)
            else []
        ),
    )

    if on_step_progress:
        on_step_progress(
            "book_consistency_start",
            {
                "chapters": completed_chapters,
                "count": len(completed_chapters),
                "repair_mode": "continue",
            },
        )

    # Determine which chapters to repair this round
    prev_auto = audit_data.get("auto_repair")
    previously_repaired: set[int] = set()
    if isinstance(prev_auto, dict):
        for det in prev_auto.get("details", []):
            if isinstance(det, dict) and book_repair_detail_was_applied(det):
                try:
                    chapter_number = int(det.get("chapter_number", 0) or 0)
                except (TypeError, ValueError):
                    chapter_number = 0
                if chapter_number > 0:
                    previously_repaired.add(chapter_number)
        result.auto_repair = prev_auto

    # Filter issues: remove chapters already repaired in previous runs
    report_issues: list[dict[str, Any]] = []
    for item in issues_raw:
        if not isinstance(item, dict):
            continue
        try:
            pch = int(item.get("primary_chapter", 0) or 0)
        except (TypeError, ValueError):
            pch = 0
        if pch in previously_repaired:
            continue
        report_issues.append(item)

    if not report_issues:
        if isinstance(prev_auto, dict) and bool(getattr(request, "generate_repair_report", True)):
            repair_report_path = layout.reports_dir / "book_consistency_repair_report.json"
            repair_report_payload = _build_book_consistency_repair_report_payload(
                request=request,
                audit_result=result,
                auto_repair=prev_auto,
            )
            runtime.storage.save_json(repair_report_path, repair_report_payload)
            if on_step_progress:
                on_step_progress(
                    "book_consistency_repair_report_written",
                    {"status": "done", "path": str(repair_report_path), "resumed": True},
                )
        if on_step_progress:
            on_step_progress(
                "book_consistency_report_written",
                {
                    "status": "done",
                    "path": str(audit_path),
                    "note": "所有问题章节已在此前修复完毕。",
                },
            )
        return ExecutionResult(project_id=request.project_id, result=result)

    # Load issue panel pool
    use_issue_panel_pool = bool(getattr(request, "use_issue_panel_pool", True))
    chapter_issue_pool: list[dict[str, Any]] = []
    if use_issue_panel_pool:
        chapter_issue_pool = _load_issue_panel_pool_for_chapters(
            storage=runtime.storage,
            layout=layout,
            chapter_numbers=completed_chapters,
            ttl_hours=_resolve_issue_pool_ttl_hours(runtime.settings),
        )

    # Optional: Stage-2 verify
    analysis_mode = str(audit_data.get("analysis_mode", "summary") or "summary")
    should_verify = (
        bool(getattr(request, "verify_before_repair", True)) and analysis_mode == "full_text"
    )
    verify_stats_continue: dict[str, Any] = {}
    if should_verify and report_issues:
        # Build chapter texts for verification
        chapter_texts: list[dict[str, Any]] = []
        max_chars = int(
            request.chapter_max_chars
            if request.chapter_max_chars is not None
            else int(getattr(runtime.settings, "long_book_audit_chapter_max_chars", 12000))
        )
        max_chars = max(1000, min(max_chars, 100000))
        issue_chapters = {int(i.get("primary_chapter", 0) or 0) for i in report_issues}
        for ch_num in sorted(issue_chapters):
            if ch_num <= 0:
                continue
            text = ""
            chapter_path = layout.chapter_path(ch_num)
            review_draft_path = layout.chapter_review_draft_path(ch_num)
            if chapter_path.exists():
                text = chapter_path.read_text(encoding="utf-8")
            elif review_draft_path.exists():
                text = review_draft_path.read_text(encoding="utf-8")
            source_chars = len(text)
            truncated = source_chars > max_chars
            if truncated:
                text = text[:max_chars]
            numbered_text, paragraph_count, paragraphs = _build_numbered_paragraph_text(text)
            chapter_texts.append(
                {
                    "chapter_number": ch_num,
                    "numbered_text": numbered_text,
                    "paragraph_count": paragraph_count,
                    "paragraphs": paragraphs,
                    "source_chars": source_chars,
                    "truncated": truncated,
                }
            )

        # Build summaries for verify context
        chapter_summaries: list[dict[str, Any]] = []
        for ch_num in sorted(issue_chapters):
            if ch_num <= 0:
                continue
            entry: dict[str, Any] = {"chapter_number": ch_num}
            rp = layout.creative_report_path(ch_num)
            if rp.exists():
                raw = runtime.storage.load_json(rp)
                _ss = raw.get("structured_summary", "")
                if isinstance(_ss, dict):
                    entry["summary"] = _ss.get("one_line_summary", "")
                elif isinstance(_ss, str):
                    entry["summary"] = _ss[:200]
            chapter_summaries.append(entry)

        from novel_forge.story_kernel.store import StoryKernelStore

        canon_store = StoryKernelStore(layout.story_kernel_db_path)
        canon_state = await canon_store.load_kernel(request.project_id)
        canon_snap = (
            canon_state.model_dump(mode="json") if hasattr(canon_state, "model_dump") else {}
        )

        max_tokens = int(
            audit_data.get("request", {}).get("max_tokens")
            or int(getattr(runtime.settings, "long_book_audit_max_tokens", 8192))
        )
        temperature = float(audit_data.get("request", {}).get("temperature") or 0.2)

        report_issues, verify_stats_continue = await _run_book_consistency_verify(
            runtime=runtime,
            report_issues=report_issues,
            chapter_texts=chapter_texts,
            chapter_summaries=chapter_summaries,
            canon_state_snapshot=canon_snap,
            max_tokens=max(2048, max_tokens // 2),
            temperature=temperature,
            concurrency=max(1, min(int(getattr(request, "repair_concurrency", 1) or 1), 4)),
            on_step_progress=on_step_progress,
            checkpoint_path=layout.states_dir / "book_consistency_verify_checkpoint.json",
            parallel_chunks=bool(
                getattr(runtime.settings, "long_book_verify_parallel_chunks", True)
            ),
            max_parallel=int(getattr(runtime.settings, "long_book_verify_max_parallel", 5)),
        )

    # Run repair
    repair_snapshot = create_book_audit_snapshot(layout)
    repair_backup_payload = {
        "schema_version": int(repair_snapshot.get("schema_version", 1) or 1),
        "created_at": str(repair_snapshot.get("created_at", "") or ""),
        "snapshot_root": str(repair_snapshot.get("snapshot_root", "") or ""),
        "captured": list(repair_snapshot.get("captured", []) or []),
        "missing": list(repair_snapshot.get("missing", []) or []),
        "auto_rollback_on_failure": bool(getattr(request, "rollback_on_failure", True)),
        "source": "continue_repair",
    }
    if on_step_progress:
        on_step_progress(
            "book_consistency_backup_created",
            {
                "status": "done",
                "snapshot": repair_backup_payload["snapshot_root"],
                "auto_rollback_on_failure": repair_backup_payload[
                    "auto_rollback_on_failure"
                ],
                "captured": repair_backup_payload["captured"],
                "source": "continue_repair",
            },
        )
    try:
        auto_repair_payload = await _run_book_consistency_auto_repair(
            runtime=runtime,
            request=request,
            report_issues=report_issues,
            chapter_issue_pool=chapter_issue_pool,
            on_step_progress=on_step_progress,
            checkpoint_path=layout.states_dir / "book_consistency_repair_checkpoint.json",
        )
    except Exception:
        if bool(getattr(request, "rollback_on_failure", True)):
            async with _project_lock(runtime, request.project_id):
                restore_book_audit_snapshot(layout, repair_snapshot)
            if on_step_progress:
                on_step_progress(
                    "book_consistency_rollback",
                    {
                        "status": "done",
                        "reason": "continue_repair_failed",
                        "snapshot": repair_backup_payload["snapshot_root"],
                    },
                )
        raise
    auto_repair_payload["backup"] = repair_backup_payload
    if verify_stats_continue:
        auto_repair_payload["verify"] = verify_stats_continue
    result.auto_repair = auto_repair_payload

    # Merge new repair details into the existing audit report
    report_path = audit_path
    async with _project_lock(runtime, request.project_id):
        # Reload to get fresh state
        existing = runtime.storage.load_json(audit_path) if audit_path.exists() else {}
        merged_repair = auto_repair_payload  # default: no merge
        if isinstance(existing, dict):
            prev_repair = existing.get("auto_repair", {})
            if isinstance(prev_repair, dict):
                prev_details = list(prev_repair.get("details", []) or [])
                new_details = list(auto_repair_payload.get("details", []) or [])
                merged_details = dedupe_book_repair_details(prev_details + new_details)
                merged_repair = {**auto_repair_payload, "details": merged_details}
                repair_metrics = summarize_book_repair_details(
                    merged_details,
                    targeted_chapters=int(merged_repair.get("targeted_chapters", 0) or 0),
                )
                merged_repair.update(repair_metrics)
                # Re-aggregate post_repair_summary across all details
                _merged_pr: dict[str, int] = {
                    "issues_checked": 0,
                    "issues_closed": 0,
                    "issues_remaining": 0,
                }
                for d in merged_details:
                    prc = d.get("post_repair_check") if isinstance(d, dict) else None
                    if isinstance(prc, dict):
                        _merged_pr["issues_checked"] += int(prc.get("issues_checked", 0))
                        _merged_pr["issues_closed"] += int(prc.get("issues_closed", 0))
                        _merged_pr["issues_remaining"] += int(prc.get("issues_remaining", 0))
                merged_repair["post_repair_summary"] = _merged_pr
                # Re-aggregate needs_manual_review across all details
                merged_repair["needs_manual_review"] = sorted(
                    int(d.get("chapter_number", 0))
                    for d in merged_details
                    if isinstance(d, dict) and d.get("needs_manual_review")
                )
                existing["auto_repair"] = merged_repair
            else:
                existing["auto_repair"] = auto_repair_payload
            existing["backup"] = repair_backup_payload
            runtime.storage.save_json(audit_path, existing)

        should_generate_report = bool(getattr(request, "generate_repair_report", True))
        if should_generate_report:
            repair_report_path = layout.reports_dir / "book_consistency_repair_report.json"
            repair_report_payload = _build_book_consistency_repair_report_payload(
                request=request,
                audit_result=result,
                auto_repair=merged_repair,
            )
            runtime.storage.save_json(repair_report_path, repair_report_payload)
            auto_repair_payload["repair_report_path"] = str(repair_report_path)
            if on_step_progress:
                on_step_progress(
                    "book_consistency_repair_report_written",
                    {"status": "done", "path": str(repair_report_path)},
                )

    if on_step_progress:
        on_step_progress(
            "book_consistency_report_written",
            {"status": "done", "path": str(report_path)},
        )

    return ExecutionResult(project_id=request.project_id, result=result)
