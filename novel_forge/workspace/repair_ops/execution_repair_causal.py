"""Repair execution helpers for causal chain repair."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from novel_forge.core.exceptions import StorageError
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import invalidate_chapter_tts_artifacts
from novel_forge.workspace.audit_callback import _emit_audit_update
from novel_forge.workspace.chapter_run_io import ChapterRunIOContext
from novel_forge.workspace.contracts import RepairCausalRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_helpers import _load_chapter_source_slice_if_available
from novel_forge.workspace.helpers.execution_io import _ChapterDataCache
from novel_forge.workspace.helpers.execution_state import (
    _causal_repair_attempts_path,
    _causal_repair_progress_path,
    _clear_causal_repair_progress,
    _deserialize_causal_repair_result,
    _load_causal_repair_attempts,
    _load_causal_repair_progress,
    _save_causal_repair_progress,
    _source_text_hash,
    _update_causal_repair_attempts,
)
from novel_forge.workspace.repair_ops.execution_repair_common import (
    _project_lock,
    _recheck_sigs_with_fuzzy_match,
)
from novel_forge.workspace.repair_ops.execution_repair_precision import (
    build_ticket_verification_results,
    filter_precision_eligible_synthetic_issues,
    prepare_schema_repair_admission,
)
from novel_forge.workspace.repair_review_verification import (
    build_causal_recheck_payload,
    run_targeted_causal_recheck,
)
from novel_forge.workspace.runtime import RuntimeServices

_log = get_logger("workspace.execution_repair")


async def _execute_chapter_causal_repair_impl(
    runtime: RuntimeServices,
    request: RepairCausalRequest,
    *,
    on_step_progress: StepCallback = None,
    on_audit_update: Callable[[str, int, dict[str, Any]], None] | None = None,
) -> ExecutionResult[Any]:
    """Run targeted causal chain repair for a subset of issues on an existing chapter."""
    from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
    from novel_forge.core.schemas.continuity import ChapterBridge
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.pipeline.steps.causal_repair_step import (
        CausalRepairInput,
        CausalRepairStep,
    )

    async with _project_lock(runtime, request.project_id):
        layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
        chapter_num = request.chapter_number
        io_context = ChapterRunIOContext(
            storage=runtime.storage,
            layout=layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
            source="repair_causal",
        )
        chapter_source_slice = _load_chapter_source_slice_if_available(
            runtime.storage,
            layout,
            project_id=request.project_id,
            chapter_number=chapter_num,
        )
        cache = _ChapterDataCache(runtime.storage, layout, artifact_loader=io_context)
        boundary_prev_tail_paragraphs = getattr(
            runtime.settings,
            "long_boundary_prev_tail_paragraphs",
            5,
        )

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
            raise ValueError(f"第 {chapter_num} 章尚无可用正文，无法执行因果链修复。")

        bridge = io_context.load_model(layout.chapter_bridge_path(chapter_num), ChapterBridge)

        _causal_link_obj = bridge.causal_link
        causal_link: dict[str, Any] = (
            _causal_link_obj.model_dump(mode="json") if _causal_link_obj is not None else {}
        )

        causal_raw = io_context.load_json(layout.chapter_causal_report_path(chapter_num)) or {}
        full_report = CausalValidationReport.model_validate(causal_raw)
        synthetic_issues: list[CausalIssue] = []
        for item in filter_precision_eligible_synthetic_issues(
            getattr(request, "synthetic_issues", [])
        ):
            try:
                synthetic_issues.append(CausalIssue.model_validate(item))
            except (TypeError, ValueError) as exc:
                _log.warning("Ignored invalid synthetic causal issue: %s", exc)
        requested_signatures = {
            str(sig).strip() for sig in getattr(request, "issue_signatures", []) if str(sig).strip()
        }
        if request.issue_indices or requested_signatures or synthetic_issues:
            selected = []
            seen_sigs: set[str] = set()
            for i in request.issue_indices:
                if 0 <= i < len(full_report.issues):
                    issue = full_report.issues[i]
                    sig = CausalRepairStep.issue_signature(issue)
                    selected.append(issue)
                    seen_sigs.add(sig)
            if requested_signatures:
                for issue in full_report.issues:
                    sig = CausalRepairStep.issue_signature(issue)
                    if sig in requested_signatures and sig not in seen_sigs:
                        selected.append(issue)
                        seen_sigs.add(sig)
            for issue in synthetic_issues:
                sig = CausalRepairStep.issue_signature(issue)
                if sig not in seen_sigs:
                    selected.append(issue)
                    seen_sigs.add(sig)
            admission = prepare_schema_repair_admission(
                selected,
                chapter_number=chapter_num,
                current_text=chapter_text,
                source_module="validate_causal",
                dimension="causal",
            )
            selected = admission.issues
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
                source_module="validate_causal",
                dimension="causal",
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
        # When the user manually selected specific issues, ALL of them are must-fix.
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

        # Per-issue escalation counters (manual repair path):
        # only issues that repeatedly remain unresolved are escalated.
        _attempts_path = _causal_repair_attempts_path(layout, chapter_num)
        _attempt_counters = _load_causal_repair_attempts(runtime.storage, _attempts_path)
        _progress_path = _causal_repair_progress_path(layout, chapter_num)
        _resume_payload_seed = _load_causal_repair_progress(runtime.storage, _progress_path)

        # Cap per-issue attempts: after 3 consecutive failures, skip the issue
        # to prevent infinite repair loops during unattended runs.
        # IMPORTANT: only explicit manual runs with allow_exhausted_retry=True
        # can override the cap.
        _MAX_PER_ISSUE_ATTEMPTS = 3
        _manual_issue_selection = bool(getattr(request, "allow_exhausted_retry", False))
        _exhausted_sigs: set[str] = set()
        _pre_filter_count = len(_must_fix_issues)
        _filtered_must_fix: list[CausalIssue] = []
        for iss in _must_fix_issues:
            sig = CausalRepairStep.issue_signature(iss)
            exhausted = int(_attempt_counters.get(sig, 0) or 0) >= _MAX_PER_ISSUE_ATTEMPTS
            if exhausted and not _manual_issue_selection:
                _exhausted_sigs.add(sig)
            else:
                _filtered_must_fix.append(iss)
                if exhausted:
                    _exhausted_sigs.add(sig)
        if _exhausted_sigs:
            if _manual_issue_selection:
                _log.info(
                    "chapter %d: manual selection overrides exhausted causal issues (>= %d): %s",
                    chapter_num,
                    _MAX_PER_ISSUE_ATTEMPTS,
                    list(_exhausted_sigs)[:5],
                )
                if on_step_progress:
                    on_step_progress(
                        "causal_issues_exhausted_override",
                        {
                            "chapter_number": chapter_num,
                            "override_count": len(_exhausted_sigs),
                            "total_count": _pre_filter_count,
                            "max_attempts": _MAX_PER_ISSUE_ATTEMPTS,
                            "manual_selection": True,
                        },
                    )
            else:
                _log.warning(
                    "chapter %d: skipping %d exhausted causal issues (>=%d attempts each): %s",
                    chapter_num,
                    len(_exhausted_sigs),
                    _MAX_PER_ISSUE_ATTEMPTS,
                    list(_exhausted_sigs)[:5],
                )
                if on_step_progress:
                    on_step_progress(
                        "causal_issues_exhausted",
                        {
                            "chapter_number": chapter_num,
                            "skipped_count": len(_exhausted_sigs),
                            "total_count": _pre_filter_count,
                            "max_attempts": _MAX_PER_ISSUE_ATTEMPTS,
                        },
                    )
        _must_fix_issues = _filtered_must_fix

        _selected_issue_signatures = {
            CausalRepairStep.issue_signature(iss) for iss in _must_fix_issues
        }
        # If no issues remain in the current report, allow resume from a valid
        # in-flight progress checkpoint before deciding to skip.
        if not _must_fix_issues:
            from novel_forge.pipeline.steps.causal_repair_step import CausalRepairResult

            _resume_seed_signatures = [
                str(item)
                for item in (_resume_payload_seed or {}).get("issue_signatures", [])
                if str(item).strip()
            ]
            _resume_seed_valid = (
                isinstance(_resume_payload_seed, dict)
                and (
                    _resume_payload_seed.get("stage")
                    in {"repair_done", "reaudit_done", "postprocess_done", "counters_done"}
                )
                and isinstance(_resume_payload_seed.get("result"), dict)
                and _resume_seed_signatures
                and str((_resume_payload_seed or {}).get("current_text_hash", "") or "").strip()
                == _source_text_hash(chapter_text)
            )
            if _resume_seed_valid:
                _selected_issue_signatures = set(_resume_seed_signatures)
            else:
                _clear_causal_repair_progress(runtime.storage, _progress_path)
                _skip_reason = (
                    "未发现需修复的高优先级因果问题，已跳过修复。"
                    if _pre_filter_count == 0
                    else "所有必修因果问题均已超过最大尝试次数，已跳过修复。"
                )
                _skip_result = CausalRepairResult(
                    revised_text=chapter_text,
                    issues=(),
                    applied=False,
                    failure_reason=_skip_reason,
                )
                if on_step_progress:
                    on_step_progress("repair_causal", _skip_result)
                return ExecutionResult(project_id=request.project_id, result=_skip_result)

        _escalate_issue_signatures = sorted(
            sig for sig in _selected_issue_signatures if int(_attempt_counters.get(sig, 0) or 0) > 0
        )

        # Derive repair_round from counter history: counter=0 → round 1 (first try),
        # counter=1 → round 2 (first retry, enables escalation_note), etc.
        # This was previously hardcoded to 1, which meant the LLM never received
        # escalation instructions and kept applying the same shallow strategy.
        _max_attempts = max(
            (int(_attempt_counters.get(sig, 0) or 0) for sig in _selected_issue_signatures),
            default=0,
        )
        _repair_round = _max_attempts + 1

        # Build per-issue round mapping: sig → attempt_count + 1 (1-based round).
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep as _CRS

        _per_issue_rounds: dict[str, int] = {
            sig: int(_attempt_counters.get(sig, 0) or 0) + 1 for sig in _selected_issue_signatures
        }

        # When retrying, pass previous issue summaries so the LLM knows what
        # was already attempted and can try a different approach.
        _prev_round_issues: list[str] | None = None
        if _repair_round > 1:
            # Only include summaries of issues that individually have > 0
            # prior attempts (i.e. actually failed before).
            _failed_sigs = {sig for sig, rnd in _per_issue_rounds.items() if rnd > 1}
            _prev_round_issues = [
                iss.summary
                for iss in _must_fix_issues
                if iss.summary and _CRS.issue_signature(iss) in _failed_sigs
            ]

        # Load chapter plan for scene-level context (helps missing_causal_transition).
        # Extract a compact list of {scene_id, summary, purpose} to stay within token budget.
        _chapter_plan_scenes: list[dict[str, Any]] = []
        _forbidden_elements: list[str] = []
        _forbidden_elements_soft: list[str] = []
        try:
            _plan_path = layout.chapter_plan_path(chapter_num)
            if runtime.storage.exists(_plan_path):
                _plan_raw = io_context.load_json(_plan_path)
                for _scene in (_plan_raw or {}).get("scene_intents", [])[:8]:
                    if isinstance(_scene, dict) and _scene.get("summary"):
                        _chapter_plan_scenes.append(
                            {
                                "scene_id": str(_scene.get("scene_id", "")),
                                "summary": str(_scene.get("summary", ""))[:120],
                                "purpose": str(_scene.get("purpose", ""))[:80],
                            }
                        )
                _forbidden_elements = [
                    str(e)
                    for e in (_plan_raw or {}).get("forbidden_elements", [])
                    if str(e).strip()
                ]
                _forbidden_elements_soft = [
                    str(e)
                    for e in (_plan_raw or {}).get("forbidden_elements_soft", [])
                    if str(e).strip()
                ]
        except (OSError, StorageError):
            _log.debug("chapter plan load failed for causal repair context", exc_info=True)

        _selected_issue_signatures_sorted = sorted(_selected_issue_signatures)
        _resume_stage = ""
        _resumed_from_progress = False
        _recheck_unresolved_signatures: set[str] | None = None

        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairResult

        _resume_payload = _load_causal_repair_progress(runtime.storage, _progress_path)
        if _resume_payload:
            _saved_stage = str(_resume_payload.get("stage", "") or "").strip()
            _saved_signatures = [
                str(item)
                for item in (_resume_payload.get("issue_signatures") or [])
                if str(item).strip()
            ]
            _saved_hash = str(_resume_payload.get("current_text_hash", "") or "").strip()
            _saved_result = _resume_payload.get("result")
            _saved_unresolved = _resume_payload.get("unresolved_signatures", None)
            _stage_ok = _saved_stage in {
                "repair_done",
                "reaudit_done",
                "postprocess_done",
                "counters_done",
            }
            _resume_ok = (
                _stage_ok
                and _saved_signatures == _selected_issue_signatures_sorted
                and _saved_hash == _source_text_hash(chapter_text)
                and isinstance(_saved_result, dict)
            )
            if _resume_ok and isinstance(_saved_result, dict):
                try:
                    result = _deserialize_causal_repair_result(_saved_result)
                    _resume_stage = _saved_stage
                    _resumed_from_progress = True
                    if _saved_unresolved is None:
                        _recheck_unresolved_signatures = None
                    elif not isinstance(_saved_unresolved, (list, tuple, set)):
                        _recheck_unresolved_signatures = None
                    else:
                        _recheck_unresolved_signatures = {
                            str(item) for item in _saved_unresolved if str(item).strip()
                        }
                except (OSError, StorageError):
                    _clear_causal_repair_progress(runtime.storage, _progress_path)
            else:
                _clear_causal_repair_progress(runtime.storage, _progress_path)

        if _resumed_from_progress and on_step_progress:
            on_step_progress(
                "resume_from_progress",
                {
                    "chapter_number": chapter_num,
                    "source": "repair_causal",
                    "completed_stage": _resume_stage,
                },
            )

        if not _resumed_from_progress:
            trace = PipelineTrace()
            step = CausalRepairStep(
                runtime.router,
                runtime.builder,
                settings=runtime.settings,
                trace=trace,
            )
            payload = CausalRepairInput(
                chapter_number=chapter_num,
                chapter_text=chapter_text,
                causal_link=causal_link,
                chapter_bridge=bridge,
                causal_report=filtered_report,
                must_fix_summaries=tuple(iss.summary for iss in _must_fix_issues if iss.summary),
                previous_chapter_ending=cache.get_previous_chapter_ending(
                    chapter_num,
                    paragraphs=boundary_prev_tail_paragraphs,
                ),
                repair_round=_repair_round,
                prev_round_issues=(
                    tuple(_prev_round_issues) if _prev_round_issues is not None else None
                ),
                per_issue_rounds=_per_issue_rounds,
                character_profiles=tuple(cache.get_character_profiles_for_repair()),
                escalate_issue_signatures=tuple(_escalate_issue_signatures),
                chapter_plan_scenes=tuple(_chapter_plan_scenes),
                forbidden_elements=tuple(_forbidden_elements),
                forbidden_elements_soft=tuple(_forbidden_elements_soft),
                style_profile=cache.get_style_profile(),
                chapter_source_slice=chapter_source_slice,
            )

            if on_step_progress:
                on_step_progress("repair_causal_start", {"chapter_number": chapter_num})

            result = await step.run(payload)
            # Defer save: only persist after re-eval confirms acceptance
            _text_after_repair = result.revised_text if result.applied else chapter_text
            _save_causal_repair_progress(
                storage=runtime.storage,
                path=_progress_path,
                chapter_number=chapter_num,
                issue_signatures=_selected_issue_signatures_sorted,
                stage="repair_done",
                current_text_hash=_source_text_hash(_text_after_repair),
                result=result,
            )
            _resume_stage = "repair_done"

        # Re-run causal validation if repair applied OR if "always re-audit" is enabled.
        # Persist checkpoint after this stage so failures in postprocess can resume.
        if _resume_stage == "repair_done":
            _always_reaudit_causal = getattr(runtime.settings, "repair_always_reaudit", False)
            _text_for_causal_reaudit = result.revised_text if result.applied else chapter_text
            if result.applied or _always_reaudit_causal:
                try:
                    _re_report = await run_targeted_causal_recheck(
                        runtime=runtime,
                        cache=cache,
                        chapter_number=chapter_num,
                        current_text=_text_for_causal_reaudit,
                        bridge=bridge,
                        causal_link=causal_link,
                        must_fix_issues=_must_fix_issues,
                        repair_result=result,
                        boundary_prev_tail_paragraphs=boundary_prev_tail_paragraphs,
                        on_step_progress=on_step_progress,
                    )

                    # ── Merge: preserve non-targeted issues from the old report ──
                    _targeted_indices_causal = set(request.issue_indices or [])
                    _non_targeted_causal = [
                        iss
                        for idx, iss in enumerate(full_report.issues)
                        if idx not in _targeted_indices_causal
                    ]
                    _merged_causal = list(_re_report.issues)

                    # ── Repair effect analysis: compare before/after ──
                    _original_causal_count = len(full_report.issues)
                    _targeted_causal_count = len(_targeted_indices_causal)
                    _resolved_causal_count = 0
                    _still_unresolved_causal_count = 0

                    # Track which targeted issues were resolved
                    _targeted_causal_issues = [
                        full_report.issues[i]
                        for i in _targeted_indices_causal
                        if 0 <= i < len(full_report.issues)
                    ]
                    for _tiss in _targeted_causal_issues:
                        _tiss_summary = _tiss.summary
                        _tiss_type = _tiss.issue_type
                        _is_resolved = not any(
                            _tiss_type == ri.issue_type and _tiss_summary == ri.summary
                            for ri in _re_report.issues
                        )
                        if _is_resolved:
                            _resolved_causal_count += 1
                        else:
                            _still_unresolved_causal_count += 1

                    if _non_targeted_causal:
                        _merged_causal += [
                            iss
                            for iss in _non_targeted_causal
                            if not any(
                                iss.issue_type == ri.issue_type and iss.summary == ri.summary
                                for ri in _re_report.issues
                            )
                        ]

                    _new_causal_issues_count = len(_merged_causal) - len(_re_report.issues)

                    # Log repair effect summary
                    _log.info(
                        "因果链修复效果 | 项目=%s | 第%d章 | "
                        "原问题=%d | 选中修复=%d | 已解决=%d | 未解决=%d | "
                        "修复引入新问题=%d | 未选中保留=%d | "
                        "修复后总问题=%d",
                        request.project_id,
                        chapter_num,
                        _original_causal_count,
                        _targeted_causal_count,
                        _resolved_causal_count,
                        _still_unresolved_causal_count,
                        _new_causal_issues_count,
                        len(_non_targeted_causal),
                        len(_merged_causal),
                    )

                    if _new_causal_issues_count > 0:
                        _log.warning(
                            "因果链修复引入新问题提醒 | 项目=%s | 第%d章 | "
                            "修复过程检测到 %d 个新问题（可能是修复操作的副作用或全局守卫检测到的回归）",
                            request.project_id,
                            chapter_num,
                            _new_causal_issues_count,
                        )

                        _mc_score = 10.0
                        for _mi in _merged_causal:
                            _sev = (_mi.severity or "medium").lower()
                            _mc_score -= {
                                "critical": 3.5,
                                "high": 2.5,
                                "medium": 1.0,
                                "low": 0.5,
                            }.get(_sev, 1.0)
                        _mc_score = max(0.0, min(10.0, round(_mc_score, 1)))
                        _re_report = CausalValidationReport(
                            causal_score=_mc_score,
                            summary=_re_report.summary,
                            issues=_merged_causal,
                            causal_link_verified=_re_report.causal_link_verified,
                        )

                    # Always build payload from _merged_causal to ensure disk state
                    # matches the UIStore push below. Previously, _re_report was only
                    # updated when _new_causal_issues_count > 0, causing disk/UI
                    # divergence when merge preserved non-targeted issues without
                    # introducing new ones.
                    _verification_results = build_ticket_verification_results(
                        list(getattr(filtered_report, "repair_tickets", []) or []),
                        remaining_issues=list(getattr(_re_report, "issues", []) or []),
                        current_text=_text_for_causal_reaudit,
                        applied=bool(result.applied),
                        metadata={
                            "chapter_number": chapter_num,
                            "repair_dimension": "causal",
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
                    _causal_payload = build_causal_recheck_payload(
                        _re_report,
                        issues=_merged_causal,
                        current_text=_text_for_causal_reaudit,
                    )
                    runtime.storage.save_json(
                        layout.chapter_causal_report_path(chapter_num),
                        _causal_payload,
                    )
                    _recheck_unresolved_signatures = _recheck_sigs_with_fuzzy_match(
                        getattr(_re_report, "issues", []) or [],
                        _selected_issue_signatures,
                    )
                    if on_step_progress:
                        on_step_progress("causal_eval_after_repair", _re_report)

                    # ── Compute merged audit result (relay via callback) ──
                    # The callback path is the sole delivery mechanism;
                    # DesktopJobManager relays to UIStore.
                    _causal_audit_result: dict[str, Any] | None = None
                    try:
                        from novel_forge.workspace.audit_merger import AuditResultMerger

                        # Merge causal (new) and continuity (from disk) reports
                        _causal_audit_result = AuditResultMerger.merge_reports(
                            layout,
                            chapter_num,
                            runtime.storage,
                            continuity_report_new=None,  # Load continuity report from disk
                            causal_report_new=_re_report,  # Newly generated causal report
                        )
                        _log.debug(
                            "Computed causal repair audit result | project=%s | chapter=%d | issues=%d",
                            request.project_id,
                            chapter_num,
                            len(_causal_audit_result.get("critique", {}).get("issues", [])),
                        )
                    except Exception as _merge_exc:
                        _log.warning("Failed to compute causal repair audit result: %s", _merge_exc)

                    if _causal_audit_result is not None:
                        _emit_audit_update(
                            on_audit_update,
                            request.project_id,
                            chapter_num,
                            _causal_audit_result,
                        )
                    _text_after_reaudit = result.revised_text if result.applied else chapter_text
                    _save_causal_repair_progress(
                        storage=runtime.storage,
                        path=_progress_path,
                        chapter_number=chapter_num,
                        issue_signatures=_selected_issue_signatures_sorted,
                        stage="reaudit_done",
                        current_text_hash=_source_text_hash(_text_after_reaudit),
                        result=result,
                        unresolved_signatures=_recheck_unresolved_signatures,
                    )
                    _resume_stage = "reaudit_done"
                except Exception as exc:
                    from novel_forge.workspace.helpers.execution_helpers import (
                        _emit_noncritical_warning,
                    )

                    result = _emit_noncritical_warning(
                        result=result,
                        on_step_progress=on_step_progress,
                        warning_step="causal_eval_after_repair_warning",
                        warning_payload={
                            "project_id": request.project_id,
                            "chapter_number": chapter_num,
                            "message": "因果链修复已保存，但修复后的因果重评估失败，当前报告可能仍是旧版本。",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                        log_event="causal_recheck_failed",
                        error=exc,
                    )
                    # Preserve the previous report's real provenance on failed recheck.
        else:
            _text_after_reaudit = result.revised_text if result.applied else chapter_text
            _save_causal_repair_progress(
                storage=runtime.storage,
                path=_progress_path,
                chapter_number=chapter_num,
                issue_signatures=_selected_issue_signatures_sorted,
                stage="reaudit_done",
                current_text_hash=_source_text_hash(_text_after_reaudit),
                result=result,
                unresolved_signatures=_recheck_unresolved_signatures,
            )
            _resume_stage = "reaudit_done"

        # Persist repaired text only after re-eval confirms acceptance
        if result.applied:
            runtime.storage.save_text(save_path, result.revised_text)
            invalidate_chapter_tts_artifacts(layout, chapter_num)

        # Update snapshot so the current text (repaired or unchanged) is treated
        # as the new baseline — prevents spurious "manual edit" warnings.
        try:
            from novel_forge.core.utils.edit_tracker import save_snapshot

            save_snapshot(save_path, layout.states_dir, chapter_num)
        except Exception as exc:
            _log.warning("Failed to save edit snapshot after causal repair: %s", exc)

        # Other dimensions retain their original hashes until actually revalidated.

        # Postprocess after text mutation; resumable from this stage onward.
        if _resume_stage == "reaudit_done":
            if result.applied:
                # On resume, the revised text was already persisted to disk in the
                # repair_done stage, so the on-disk text IS the revised text.
                # Using the current disk text as original_text ensures that
                # _run_revised_text_postprocess correctly detects "no further change"
                # and skips redundant exit_state refresh + memory reindex.
                _postprocess_original_text = chapter_text
                if _resumed_from_progress:
                    _postprocess_original_text = result.revised_text
                from novel_forge.workspace.helpers.execution_helpers import (
                    _run_revised_text_postprocess,
                )

                await _run_revised_text_postprocess(
                    runtime,
                    project_id=request.project_id,
                    layout=layout,
                    chapter_number=chapter_num,
                    source="repair_causal",
                    original_text=_postprocess_original_text,
                    revised_text=result.revised_text,
                    repaired_issue_types=result.repaired_issue_types,
                    on_step_progress=on_step_progress,
                )
            _text_after_post = result.revised_text if result.applied else chapter_text
            _save_causal_repair_progress(
                storage=runtime.storage,
                path=_progress_path,
                chapter_number=chapter_num,
                issue_signatures=_selected_issue_signatures_sorted,
                stage="postprocess_done",
                current_text_hash=_source_text_hash(_text_after_post),
                result=result,
                unresolved_signatures=_recheck_unresolved_signatures,
            )
            _resume_stage = "postprocess_done"

        # Update per-issue escalation counters for the selected must-fix issues.
        if _resume_stage == "postprocess_done":
            _update_causal_repair_attempts(
                storage=runtime.storage,
                path=_attempts_path,
                previous_attempts=_attempt_counters,
                selected_issue_signatures=_selected_issue_signatures,
                unresolved_issue_signatures=_recheck_unresolved_signatures,
            )
            _text_after_counters = result.revised_text if result.applied else chapter_text
            _save_causal_repair_progress(
                storage=runtime.storage,
                path=_progress_path,
                chapter_number=chapter_num,
                issue_signatures=_selected_issue_signatures_sorted,
                stage="counters_done",
                current_text_hash=_source_text_hash(_text_after_counters),
                result=result,
                unresolved_signatures=_recheck_unresolved_signatures,
            )
            _resume_stage = "counters_done"

        if on_step_progress:
            on_step_progress("repair_causal", result)
        _clear_causal_repair_progress(runtime.storage, _progress_path)

    return ExecutionResult(project_id=request.project_id, result=result)
