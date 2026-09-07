"""Repair execution helpers for continuity and motif history."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from novel_forge.common.severity import SEVERITY_RANK, normalize_severity
from novel_forge.core.exceptions import StorageError, ValidationError
from novel_forge.core.utils.issue_signature import compute_issue_signature
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import invalidate_chapter_tts_artifacts
from novel_forge.workspace.audit_callback import _emit_audit_update
from novel_forge.workspace.chapter_run_io import ChapterRunIOContext
from novel_forge.workspace.contracts import (
    RepairContinuityRequest,
    RepairMotifHistoryRequest,
)
from novel_forge.workspace.execution_memory import require_memory_maintenance_authority
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_helpers import _load_chapter_source_slice_if_available
from novel_forge.workspace.helpers.execution_io import _ChapterDataCache
from novel_forge.workspace.helpers.execution_state import (
    _clear_continuity_repair_progress,
    _continuity_repair_progress_path,
    _deserialize_continuity_repair_result,
    _load_continuity_repair_progress,
    _save_continuity_repair_progress,
    _source_text_hash,
)
from novel_forge.workspace.repair_ops.execution_repair_common import (
    _project_lock,
    _sig_ngram_jaccard,
)
from novel_forge.workspace.repair_ops.execution_repair_precision import (
    build_ticket_verification_results,
    filter_precision_eligible_synthetic_issues,
    prepare_schema_repair_admission,
)
from novel_forge.workspace.repair_review_verification import (
    build_continuity_recheck_payload,
    run_targeted_continuity_recheck,
)
from novel_forge.workspace.runtime import RuntimeServices

_log = get_logger("workspace.execution_repair")


def _continuity_issue_severity_rank(value: Any) -> int:
    return SEVERITY_RANK[normalize_severity(value)]


def _compute_issue_resolution_status(
    original_issue: Any,
    re_report: Any,
    *,
    jaccard_threshold: float = 0.15,
) -> str:
    """Return "resolved", "partial", or "unresolved" for a targeted issue.

    Cascade: signature match → string match → Jaccard n-gram.
    Partial = same issue found but severity downgraded.
    """
    orig_sig = compute_issue_signature(original_issue)

    def _iss_field(iss: Any, name: str, default: Any = "") -> Any:
        return getattr(iss, name, None) if hasattr(iss, name) else iss.get(name, default)

    re_issues = getattr(re_report, "issues", []) or []

    if orig_sig:
        matched = next(
            (ri for ri in re_issues if compute_issue_signature(ri) == orig_sig),
            None,
        )
        if matched is None:
            return "resolved"
        orig_rank = _continuity_issue_severity_rank(_iss_field(original_issue, "severity"))
        re_rank = _continuity_issue_severity_rank(_iss_field(matched, "severity"))
        return "partial" if re_rank < orig_rank else "unresolved"

    orig_type = _iss_field(original_issue, "issue_type", "")
    orig_summary = _iss_field(original_issue, "summary", "")

    if orig_type and orig_summary:
        for ri in re_issues:
            if (
                _iss_field(ri, "issue_type", "") == orig_type
                and _iss_field(ri, "summary", "") == orig_summary
            ):
                orig_rank = _continuity_issue_severity_rank(_iss_field(original_issue, "severity"))
                re_rank = _continuity_issue_severity_rank(_iss_field(ri, "severity"))
                return "partial" if re_rank < orig_rank else "unresolved"

    if len(orig_summary) >= 5:
        for ri in re_issues:
            ri_summary = _iss_field(ri, "summary", "")
            if (
                len(ri_summary) >= 5
                and _sig_ngram_jaccard(orig_summary, ri_summary, n=3) >= jaccard_threshold
            ):
                orig_rank = _continuity_issue_severity_rank(_iss_field(original_issue, "severity"))
                re_rank = _continuity_issue_severity_rank(_iss_field(ri, "severity"))
                return "partial" if re_rank < orig_rank else "unresolved"

    return "resolved"


async def _execute_chapter_continuity_repair_impl(
    runtime: RuntimeServices,
    request: RepairContinuityRequest,
    *,
    on_step_progress: StepCallback = None,
    on_audit_update: Callable[[str, int, dict[str, Any]], None] | None = None,
) -> ExecutionResult[Any]:
    """Run targeted continuity repair for a subset of issues on an existing chapter."""
    from novel_forge.core.schemas.continuity import ContinuityReport
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.pipeline.long.repair import run_continuity_repair
    from novel_forge.pipeline.steps.continuity_repair_step import (
        ContinuityRepairInput,
        ContinuityRepairStep,
    )

    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
        chapter_num = request.chapter_number
        io_context = ChapterRunIOContext(
            storage=runtime.storage,
            layout=layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
            source="repair_continuity",
        )
        chapter_source_slice = _load_chapter_source_slice_if_available(
            runtime.storage,
            layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
        )

        # Load chapter text: prefer final chapter, fall back to review draft
        chapter_text = ""
        chapter_path = layout.chapter_path(chapter_num)
        review_draft_path = layout.chapter_review_draft_path(chapter_num)
        if chapter_path.exists():
            chapter_text = chapter_path.read_text(encoding="utf-8")
            save_path = chapter_path
        elif review_draft_path.exists():
            chapter_text = review_draft_path.read_text(encoding="utf-8")
            save_path = review_draft_path
        else:
            raise ValueError(f"第 {chapter_num} 章尚无可用正文，无法执行连贯性修复。")

        # Load planning artifacts (state packet, bridge, plan)
        from novel_forge.core.schemas.continuity import (
            ChapterBridge,
            ChapterPlan,
            ChapterStatePacket,
            ContinuityIssue,
        )
        from novel_forge.core.schemas.outline import ChapterOutline

        _packet_path = layout.chapter_state_packet_path(chapter_num)
        _bridge_path = layout.chapter_bridge_path(chapter_num)
        _plan_path = layout.chapter_plan_path(chapter_num)
        missing = [
            str(p)
            for p in (_packet_path, _bridge_path, _plan_path)
            if not runtime.storage.exists(p)
        ]
        if missing:
            raise ValueError(
                f"第 {chapter_num} 章缺少方案准备文件，请先执行准备阶段。"
                f"（缺失：{', '.join(missing)}）"
            )

        packet = io_context.load_model(_packet_path, ChapterStatePacket)
        bridge = io_context.load_model(_bridge_path, ChapterBridge)
        plan = io_context.load_model(_plan_path, ChapterPlan)

        # Load chapter outline (provides story-level goal + main_plot_points for drift prevention)
        chapter_outline: ChapterOutline | None = None
        try:
            outline_raw = (
                io_context.load_json(layout.outline_path) if layout.outline_path.exists() else {}
            )
            chapters_list = (outline_raw or {}).get("chapters", [])
            ch_data = next(
                (c for c in chapters_list if c.get("chapter_number") == chapter_num), None
            )
            if ch_data and ch_data.get("goal"):
                chapter_outline = ChapterOutline.model_validate(ch_data)
        except (OSError, StorageError, ValidationError):
            chapter_outline = None  # non-fatal; repair still runs without outline context

        # Load continuity report and filter to selected issue indices
        continuity_raw = io_context.load_json(layout.continuity_report_path(chapter_num)) or {}
        full_report = ContinuityReport.model_validate(continuity_raw)
        synthetic_issues: list[ContinuityIssue] = []
        for item in filter_precision_eligible_synthetic_issues(
            getattr(request, "synthetic_issues", [])
        ):
            try:
                synthetic_issues.append(ContinuityIssue.model_validate(item))
            except (TypeError, ValueError) as exc:
                _log.warning("Ignored invalid synthetic continuity issue: %s", exc)
        requested_signatures = {
            str(sig).strip() for sig in getattr(request, "issue_signatures", []) if str(sig).strip()
        }
        prior_issues_payload: list[dict[str, Any]] = []
        if request.issue_indices or requested_signatures or synthetic_issues:
            selected = []
            seen_sigs: set[str] = set()
            for i in request.issue_indices:
                if 0 <= i < len(full_report.issues):
                    issue = full_report.issues[i]
                    sig = ContinuityRepairStep.issue_signature(issue)
                    selected.append(issue)
                    seen_sigs.add(sig)
            if requested_signatures:
                for issue in full_report.issues:
                    sig = ContinuityRepairStep.issue_signature(issue)
                    if sig in requested_signatures and sig not in seen_sigs:
                        selected.append(issue)
                        seen_sigs.add(sig)
            for issue in synthetic_issues:
                sig = ContinuityRepairStep.issue_signature(issue)
                if sig not in seen_sigs:
                    selected.append(issue)
                    seen_sigs.add(sig)
            admission = prepare_schema_repair_admission(
                selected,
                chapter_number=chapter_num,
                current_text=chapter_text,
                source_module="check_continuity",
                dimension="continuity",
            )
            selected = admission.issues
            prior_issues_payload = [
                issue.model_dump(mode="json") if hasattr(issue, "model_dump") else dict(issue)
                for issue in selected
            ]
            filtered_report = full_report.model_copy(
                update={
                    "issues": selected,
                    "review_findings": admission.review_findings,
                    "repair_tickets": admission.repair_tickets,
                    "repair_readiness": admission.repair_readiness,
                }
            )
        else:
            admission = prepare_schema_repair_admission(
                list(full_report.issues),
                chapter_number=chapter_num,
                current_text=chapter_text,
                source_module="check_continuity",
                dimension="continuity",
            )
            filtered_report = full_report.model_copy(
                update={
                    "issues": admission.issues,
                    "review_findings": admission.review_findings,
                    "repair_tickets": admission.repair_tickets,
                    "repair_readiness": admission.repair_readiness,
                }
            )

        # Determine must-fix issues for double-end focus binding (repair + post-repair eval).
        # When the user manually selected specific issues, ALL of them are must-fix —
        # the explicit selection is a stronger intent signal than severity alone.
        # When no selection was made (auto-run on full report), fall back to critical/high.
        if request.issue_indices or requested_signatures or synthetic_issues:
            _must_fix_issues = list(filtered_report.issues)
        else:
            _MUST_FIX_SEVERITIES = {"critical", "high"}
            _must_fix_issues = [
                iss
                for iss in filtered_report.issues
                if (getattr(iss, "severity", "") or "").lower() in _MUST_FIX_SEVERITIES
            ]

        # Compute issue signatures for progress validation
        _selected_issue_signatures = sorted(
            ContinuityRepairStep.issue_signature(iss) for iss in _must_fix_issues
        )

        # Load progress for resume if available
        _progress_path = _continuity_repair_progress_path(layout, chapter_num)
        _resume_payload = _load_continuity_repair_progress(runtime.storage, _progress_path)
        _resumed_from_progress = False
        _resume_stage = ""
        if _resume_payload:
            _saved_stage = str(_resume_payload.get("stage", "") or "").strip()
            _saved_signatures = [
                str(item)
                for item in (_resume_payload.get("issue_signatures") or [])
                if str(item).strip()
            ]
            _saved_hash = str(_resume_payload.get("current_text_hash", "") or "").strip()
            _saved_result = _resume_payload.get("result")
            _stage_ok = _saved_stage in {"repair_done", "eval_done", "postprocess_done"}
            _resume_ok = (
                _stage_ok
                and _saved_signatures == _selected_issue_signatures
                and _saved_hash == _source_text_hash(chapter_text)
                and isinstance(_saved_result, dict)
            )
            if _resume_ok and isinstance(_saved_result, dict):
                try:
                    result = _deserialize_continuity_repair_result(_saved_result)
                    _resumed_from_progress = True
                    _resume_stage = _saved_stage
                except (OSError, StorageError):
                    _clear_continuity_repair_progress(runtime.storage, _progress_path)
            else:
                _clear_continuity_repair_progress(runtime.storage, _progress_path)

        if _resumed_from_progress and on_step_progress:
            on_step_progress(
                "resume_from_progress",
                {
                    "chapter_number": chapter_num,
                    "source": "repair_continuity",
                    "completed_stage": _resume_stage,
                },
            )

        if not _resumed_from_progress:
            if on_step_progress:
                on_step_progress("repair_continuity_start", {"chapter_number": chapter_num})

            trace = PipelineTrace()
            step = ContinuityRepairStep(
                runtime.router,
                runtime.builder,
                settings=runtime.settings,
                trace=trace,
                on_step=on_step_progress,
            )
            cache = _ChapterDataCache(runtime.storage, layout, artifact_loader=io_context)
            style_profile_payload = cache.get_style_profile()
            previous_chapter_ending = cache.get_previous_chapter_ending(
                chapter_num,
                tail=2400,
                paragraphs=getattr(runtime.settings, "long_boundary_prev_tail_paragraphs", 5),
            )
            if previous_chapter_ending:
                packet = packet.model_copy(
                    update={"previous_chapter_ending": previous_chapter_ending}
                )
            payload = ContinuityRepairInput(
                chapter_number=chapter_num,
                chapter_text=chapter_text,
                chapter_state_packet=packet,
                chapter_bridge=bridge,
                chapter_plan=plan,
                continuity_report=filtered_report,
                chapter_outline=chapter_outline,
                style_profile=style_profile_payload,
                must_fix_issues=tuple(_must_fix_issues),
                boundary_prev_tail_paragraphs=getattr(
                    runtime.settings,
                    "long_boundary_prev_tail_paragraphs",
                    5,
                ),
                boundary_opening_paragraphs=getattr(
                    runtime.settings,
                    "long_boundary_opening_paragraphs",
                    3,
                ),
                chapter_source_slice=chapter_source_slice,
            )

            result = await run_continuity_repair(step, payload)

            _save_continuity_repair_progress(
                storage=runtime.storage,
                path=_progress_path,
                chapter_number=chapter_num,
                issue_signatures=_selected_issue_signatures,
                stage="repair_done",
                current_text_hash=_source_text_hash(
                    result.revised_text if result.applied else chapter_text
                ),
                result=result,
            )

        if result.applied:
            runtime.storage.save_text(save_path, result.revised_text)
            invalidate_chapter_tts_artifacts(layout, chapter_num)
            runtime.storage.save_json(
                layout.repair_plan_path(chapter_num),
                result.repair_plan.model_dump(mode="json"),
            )
            try:
                from novel_forge.core.utils.edit_tracker import save_snapshot as _snap

                _snap(save_path, layout.states_dir, chapter_num)
            except Exception as _snap_exc:
                _log.warning("Early snapshot save failed: %s", _snap_exc)

        # Re-run continuity eval if the repair was applied, OR if "始终重审核" is enabled.
        # When applied=False and repair_always_reaudit=False (default), skip to avoid
        # wasting tokens — the text is unchanged, results would be identical.
        # When repair_always_reaudit=True, always reaudit so the user can confirm a
        # problem is truly gone (uses original chapter_text when nothing was applied).
        _always_reaudit = getattr(runtime.settings, "repair_always_reaudit", False)
        if result.applied or _always_reaudit:
            current_text_for_eval = result.revised_text if result.applied else chapter_text
            try:
                _re_report = await run_targeted_continuity_recheck(
                    runtime=runtime,
                    layout=layout,
                    chapter_number=chapter_num,
                    current_text=current_text_for_eval,
                    packet=packet,
                    bridge=bridge,
                    plan=plan,
                    prior_issues=prior_issues_payload,
                    must_fix_issues=_must_fix_issues,
                    repair_result=result,
                    on_step_progress=on_step_progress,
                )

                # ── Merge: preserve non-targeted issues from the old report ──
                # The recheck only verifies targeted issues + regressions.
                # Non-targeted issues from the full report should be kept as-is
                # so "修复选中" truly only affects what was selected.
                _targeted_indices = set(request.issue_indices or [])
                _non_targeted = [
                    iss
                    for idx, iss in enumerate(full_report.issues)
                    if idx not in _targeted_indices
                ]
                _merged_issues = list(_re_report.issues)

                # ── Repair effect analysis: compare before/after ──
                _original_issue_count = len(full_report.issues)
                _targeted_count = len(_targeted_indices)
                _resolved_count = 0
                _partial_count = 0
                _unresolved_count = 0
                _new_issues_count = 0

                _targeted_issues = [
                    full_report.issues[i]
                    for i in _targeted_indices
                    if 0 <= i < len(full_report.issues)
                ]
                for _tiss in _targeted_issues:
                    _status = _compute_issue_resolution_status(_tiss, _re_report)
                    if _status == "resolved":
                        _resolved_count += 1
                    elif _status == "partial":
                        _partial_count += 1
                    else:
                        _unresolved_count += 1

                if _non_targeted:
                    _merged_issues += [
                        iss
                        for iss in _non_targeted
                        if not any(
                            iss.issue_type == ri.issue_type and iss.summary == ri.summary
                            for ri in _re_report.issues
                        )
                    ]

                _new_issues_count = len(_merged_issues) - len(_re_report.issues)

                # Log repair effect summary
                _log.info(
                    "连贯性修复效果 | 项目=%s | 第%d章 | "
                    "原问题=%d | 选中修复=%d | 已解决=%d | 部分解决=%d | 未解决=%d | "
                    "修复引入新问题=%d | 未选中保留=%d | "
                    "修复后总问题=%d",
                    request.project_id,
                    chapter_num,
                    _original_issue_count,
                    _targeted_count,
                    _resolved_count,
                    _partial_count,
                    _unresolved_count,
                    _new_issues_count,
                    len(_non_targeted),
                    len(_merged_issues),
                )

                if _new_issues_count > 0:
                    _log.warning(
                        "修复引入新问题提醒 | 项目=%s | 第%d章 | "
                        "修复过程检测到 %d 个新问题（可能是修复操作的副作用或全局守卫检测到的回归）",
                        request.project_id,
                        chapter_num,
                        _new_issues_count,
                    )

                    # Recalculate score from merged issue list.
                    _merged_score = 10.0
                    for _mi in _merged_issues:
                        _sev = (_mi.severity or "medium").lower()
                        _merged_score -= {
                            "critical": 3.5,
                            "high": 2.5,
                            "medium": 1.0,
                            "low": 0.5,
                        }.get(_sev, 1.0)
                    _merged_score = max(0.0, min(10.0, round(_merged_score, 1)))
                    from novel_forge.core.schemas.continuity import ContinuityReport as _CReport

                    _re_report = _CReport.model_validate(
                        {
                            "continuity_score": _merged_score,
                            "summary": _re_report.summary,
                            "issues": [
                                iss.model_dump(mode="json")
                                if hasattr(iss, "model_dump")
                                else dict(iss)
                                for iss in _merged_issues
                            ],
                        }
                    )

                if _unresolved_count > 0:
                    warning = (
                        f"连贯性修复已改动正文，但复审仍有 {_unresolved_count} 个目标问题未解决；"
                        "本次结果需要继续升级修复，不能视为已通过点验。"
                    )
                    result = replace(
                        result,
                        failure_reason="targeted_recheck_unresolved",
                        warnings=tuple([*getattr(result, "warnings", ()), warning]),
                    )
                    if on_step_progress:
                        on_step_progress(
                            "continuity_repair_unresolved_after_recheck",
                            {
                                "chapter_number": chapter_num,
                                "unresolved_count": _unresolved_count,
                                "partial_count": _partial_count,
                                "resolved_count": _resolved_count,
                                "message": warning,
                            },
                        )

                # Always build payload from _merged_issues to ensure disk state matches
                # the UIStore push below. Previously, _re_report was only updated when
                # _new_issues_count > 0, causing disk/UI divergence.
                _verification_results = build_ticket_verification_results(
                    list(getattr(filtered_report, "repair_tickets", []) or []),
                    remaining_issues=list(getattr(_re_report, "issues", []) or []),
                    current_text=current_text_for_eval,
                    applied=bool(result.applied),
                    metadata={
                        "chapter_number": chapter_num,
                        "repair_dimension": "continuity",
                    },
                )
                _re_report = _re_report.model_copy(
                    update={
                        "review_findings": list(
                            getattr(filtered_report, "review_findings", []) or []
                        ),
                        "repair_tickets": list(
                            getattr(filtered_report, "repair_tickets", []) or []
                        ),
                        "repair_readiness": dict(
                            getattr(filtered_report, "repair_readiness", {}) or {}
                        ),
                        "verification_results": _verification_results,
                    }
                )
                _cont_payload = build_continuity_recheck_payload(
                    _re_report,
                    issues=_merged_issues,
                    current_text=current_text_for_eval,
                )
                runtime.storage.save_json(
                    layout.continuity_report_path(chapter_num),
                    _cont_payload,
                )
                if on_step_progress:
                    on_step_progress("continuity_eval_after_repair", _re_report)

                # ── Compute merged audit result (relay via callback) ──
                # The callback path is the sole delivery mechanism;
                # DesktopJobManager relays to UIStore.
                _continuity_audit_result: dict[str, Any] | None = None
                try:
                    from novel_forge.workspace.audit_merger import AuditResultMerger

                    # Merge continuity (new) and causal (from disk) reports
                    _continuity_audit_result = AuditResultMerger.merge_reports(
                        layout,
                        chapter_num,
                        runtime.storage,
                        continuity_report_new=_re_report,  # Newly generated continuity report
                        causal_report_new=None,  # Load causal report from disk
                    )
                    _log.debug(
                        "Computed repair audit result | project=%s | chapter=%d | issues=%d",
                        request.project_id,
                        chapter_num,
                        len(_continuity_audit_result.get("critique", {}).get("issues", [])),
                    )
                except Exception as _merge_exc:
                    _log.warning("Failed to compute repair audit result: %s", _merge_exc)

                if _continuity_audit_result is not None:
                    _emit_audit_update(
                        on_audit_update,
                        request.project_id,
                        chapter_num,
                        _continuity_audit_result,
                    )
            except Exception as exc:
                from novel_forge.workspace.helpers.execution_helpers import (
                    _emit_noncritical_warning,
                )

                result = _emit_noncritical_warning(
                    result=result,
                    on_step_progress=on_step_progress,
                    warning_step="continuity_eval_after_repair_warning",
                    warning_payload={
                        "project_id": request.project_id,
                        "chapter_number": chapter_num,
                        "message": "连贯性修复已保存，但修复后的 continuity 重评估失败，当前报告可能仍是旧版本。",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    log_event="continuity_recheck_failed",
                    error=exc,
                )
                # Preserve old report evidence after failure; it is not a new validation.

        # Save progress after eval stage
        if not _resumed_from_progress:
            _save_continuity_repair_progress(
                storage=runtime.storage,
                path=_progress_path,
                chapter_number=chapter_num,
                issue_signatures=_selected_issue_signatures,
                stage="eval_done",
                current_text_hash=_source_text_hash(
                    result.revised_text if result.applied else chapter_text
                ),
                result=result,
            )

        # Update snapshot so the current text (repaired or unchanged) is treated
        # as the new baseline — prevents spurious "manual edit" warnings.
        try:
            from novel_forge.core.utils.edit_tracker import save_snapshot

            save_snapshot(save_path, layout.states_dir, chapter_num)
        except Exception as exc:
            _log.warning("Failed to save edit snapshot: %s", exc)

        # Other dimensions retain their original hashes until actually revalidated.

        if result.applied:
            from novel_forge.workspace.helpers.execution_helpers import (
                _run_revised_text_postprocess,
            )

            await _run_revised_text_postprocess(
                runtime,
                project_id=request.project_id,
                layout=layout,
                chapter_number=chapter_num,
                source="repair_continuity",
                original_text=chapter_text,
                revised_text=result.revised_text,
                repaired_issue_types=result.repaired_issue_types,
                on_step_progress=on_step_progress,
            )

            if not _resumed_from_progress:
                _save_continuity_repair_progress(
                    storage=runtime.storage,
                    path=_progress_path,
                    chapter_number=chapter_num,
                    issue_signatures=_selected_issue_signatures,
                    stage="postprocess_done",
                    current_text_hash=_source_text_hash(result.revised_text),
                    result=result,
                )

        if on_step_progress:
            on_step_progress("repair_continuity", result)

        _clear_continuity_repair_progress(runtime.storage, _progress_path)

    return ExecutionResult(project_id=request.project_id, result=result)


async def execute_repair_motif_history(
    runtime: RuntimeServices,
    request: "RepairMotifHistoryRequest",
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    """Two-layer motif history repair: cache rebuild + optional re-extraction."""
    require_memory_maintenance_authority(runtime, request.project_id, "重建母题历史")
    async with _project_lock(runtime, request.project_id):
        storage = getattr(runtime, "storage", None)
        get_memory_context = getattr(runtime, "get_memory_context", None)
        if not callable(get_memory_context):
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "ok": False,
                    "reason": "memory_context_unavailable",
                    "chapter_number": request.chapter_number,
                },
            )

        memory_ctx = await get_memory_context(project_id=request.project_id, storage=storage)
        if memory_ctx is None:
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "ok": False,
                    "reason": "memory_context_missing",
                    "chapter_number": request.chapter_number,
                },
            )

        repair_fn = getattr(memory_ctx, "repair_motif_history_from_cache", None)
        if not callable(repair_fn):
            return ExecutionResult(
                project_id=request.project_id,
                result={
                    "ok": False,
                    "reason": "repair_function_unavailable",
                    "chapter_number": request.chapter_number,
                },
            )

        if on_step_progress:
            on_step_progress(
                "motif_history_repair_start",
                {
                    "project_id": request.project_id,
                    "chapter_number": request.chapter_number,
                    "force_re_extract": request.force_re_extract,
                    "start_chapter": request.start_chapter,
                    "end_chapter": request.end_chapter,
                },
            )

        try:
            repair_kwargs = {
                "force_re_extract": request.force_re_extract,
                "start_chapter": request.start_chapter,
                "end_chapter": request.end_chapter,
                "on_progress": lambda stage, data: (
                    on_step_progress(f"motif_repair_{stage}", data) if on_step_progress else None
                ),
            }
            try:
                params = inspect.signature(repair_fn).parameters
                accepts_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
                kwargs_to_pass = (
                    repair_kwargs
                    if accepts_kwargs
                    else {name: value for name, value in repair_kwargs.items() if name in params}
                )
            except (TypeError, ValueError):
                kwargs_to_pass = repair_kwargs

            repair_result = repair_fn(**kwargs_to_pass)
            repaired = await repair_result if inspect.isawaitable(repair_result) else repair_result
        except Exception as exc:
            _log.warning(
                "母题历史修补失败 | project=%s | error=%s",
                request.project_id,
                exc,
                exc_info=True,
            )
            error_result = {
                "ok": False,
                "reason": "repair_failed",
                "error": str(exc),
                "chapter_number": request.chapter_number,
            }
            if on_step_progress:
                on_step_progress("motif_history_repair_error", error_result)
            return ExecutionResult(project_id=request.project_id, result=error_result)

        if not isinstance(repaired, dict):
            repaired = {"ok": False, "reason": "invalid_repair_result"}
        repaired = {
            **repaired,
            "chapter_number": request.chapter_number,
        }

        if on_step_progress:
            on_step_progress("motif_history_repair_done", repaired)
        return ExecutionResult(project_id=request.project_id, result=repaired)
