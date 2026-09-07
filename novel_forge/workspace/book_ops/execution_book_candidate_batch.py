"""Candidate-first whole-book repair batches for configured authoring projects.

The legacy global repair queue writes chapter files as it works.  Configured
projects instead run that proven domain flow against an executor-owned clone,
then project the resulting chapter candidates and verification evidence into
the shared repair-case store.  No path below the live ``chapters/`` directory
is written by this module.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, cast
from uuid import uuid4

from novel_forge.core.schemas.audit import (
    AuditEvidence,
    AuditIssueV2,
    AuditLocator,
    AuditPostcondition,
    AuditRepairIntent,
    AuditSeverity,
    ResolvedRepairTarget,
)
from novel_forge.core.schemas.repair import (
    RepairCandidate,
    RepairCase,
    RepairPatchRecord,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.workspace.contracts import BookConsistencyRequest, GlobalRepairQueueRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.propagation_validator import PropagationValidator
from novel_forge.workspace.regression_detector import RegressionDetector
from novel_forge.workspace.runtime import RuntimeServices

CandidateExecutor = Callable[
    [Any, GlobalRepairQueueRequest, StepCallback], Awaitable[ExecutionResult[Any]]
]
QualityEvaluator = Callable[
    [
        Any,
        GlobalRepairQueueRequest,
        dict[int, str],
        dict[int, str],
        dict[int, list[dict[str, Any]]],
        StepCallback,
    ],
    Awaitable[dict[str, Any]],
]

_PROTECTED_ITEMS = (
    "explicit author intent",
    "locked content",
    "ending",
    "character fate",
    "world rules",
    "archived prose",
)
_CLONE_EXCLUDED_NAMES = {
    ".authoring",
    ".planning_revisions",
    "authoring_policy.json",
    "backups",
    "exports",
    "logs",
}


async def prepare_book_repair_candidate_batch(
    *,
    runtime: RuntimeServices,
    request: GlobalRepairQueueRequest,
    run_id: str,
    queue_items: list[dict[str, Any]],
    on_step_progress: StepCallback = None,
    candidate_executor: CandidateExecutor | None = None,
    quality_evaluator: QualityEvaluator | None = None,
) -> dict[str, Any]:
    """Prepare and verify one non-publishing batch from a live queue snapshot."""

    root = runtime.storage.existing_project_dir(request.project_id)
    authority_store = AuthoringStore(root)
    policy = authority_store.policy()
    if policy is None:
        raise ValueError("candidate-first book repair requires a configured authoring project")
    authority_store.require_enabled()
    if policy.stopped:
        raise ValueError("作者授权已停止；全书候选保留，恢复后须重新准备")

    items_by_chapter = _group_queue_items(queue_items)
    chapter_numbers = sorted(items_by_chapter)
    if not chapter_numbers:
        return {
            "run_id": run_id,
            "mode": "candidate_batch",
            "status": "empty",
            "requested": 0,
            "processed": 0,
            "formal_writes": 0,
            "case_ids": [],
        }
    for chapter_number in chapter_numbers:
        authority_store.require("repair", chapter_number, explicit=True)

    layout = ProjectLayout(root)
    baseline_texts: dict[int, str] = {}
    stale: list[dict[str, Any]] = []
    for chapter_number, items in items_by_chapter.items():
        path = layout.chapter_path(chapter_number)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        baseline_texts[chapter_number] = text
        current_hash = repair_content_hash(text)
        for item in items:
            ticket_value = item.get("ticket")
            ticket: dict[str, Any] = ticket_value if isinstance(ticket_value, dict) else {}
            expected = str(item.get("source_hash") or ticket.get("source_text_hash") or "")
            if expected and expected != current_hash:
                stale.append(
                    {
                        "queue_item_id": str(item.get("queue_item_id") or ""),
                        "chapter_number": chapter_number,
                        "expected_hash": expected,
                        "current_hash": current_hash,
                    }
                )
    if stale:
        return {
            "run_id": run_id,
            "mode": "candidate_batch",
            "status": "stale",
            "requested": len(queue_items),
            "processed": 0,
            "formal_writes": 0,
            "case_ids": [],
            "stale_sources": stale,
        }

    batch_id = uuid4().hex
    batch_dir = root / ".authoring" / "repair_cases" / "batches" / batch_id
    workspace_root = batch_dir / "workspace"
    candidate_root = workspace_root / request.project_id
    manifest_path = batch_dir / "manifest.json"
    input_version = story_input_version(root)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "project_id": request.project_id,
        "run_id": run_id,
        "status": "preparing",
        "policy_version": policy.version,
        "input_version": input_version,
        "chapter_numbers": chapter_numbers,
        "queue_item_ids": [str(item.get("queue_item_id") or "") for item in queue_items],
        "source_hashes": {
            str(chapter): repair_content_hash(text) for chapter, text in baseline_texts.items()
        },
        "case_ids": [],
        "formal_writes": 0,
    }
    atomic_write_json(manifest_path, manifest)
    if on_step_progress:
        on_step_progress(
            "book_repair_candidate_batch_start",
            {
                "batch_id": batch_id,
                "chapters": chapter_numbers,
                "queue_items": len(queue_items),
                "formal_writes": 0,
            },
        )

    try:
        _clone_project_for_candidate(root, candidate_root)
        candidate_runtime = _runtime_for_candidate(runtime, workspace_root)
        executor = candidate_executor or _default_candidate_executor
        candidate_request = request.model_copy(
            update={"verify_before_apply": False, "concurrency": 1}
        )
        candidate_execution = await executor(
            candidate_runtime,
            candidate_request,
            on_step_progress,
        )
        raw_summary = candidate_execution.result
        candidate_summary = (
            dict(raw_summary)
            if isinstance(raw_summary, dict)
            else raw_summary.model_dump(mode="json")
            if hasattr(raw_summary, "model_dump")
            else {}
        )
        candidate_layout = ProjectLayout(candidate_root)
        candidate_texts = {
            chapter: candidate_layout.chapter_path(chapter).read_text(encoding="utf-8")
            if candidate_layout.chapter_path(chapter).is_file()
            else ""
            for chapter in chapter_numbers
        }
        evaluator = quality_evaluator or _default_quality_evaluator
        quality = await evaluator(
            candidate_runtime,
            request,
            baseline_texts,
            candidate_texts,
            items_by_chapter,
            on_step_progress,
        )
        chapter_gates = _chapter_gate_results(
            chapter_numbers=chapter_numbers,
            candidate_summary=candidate_summary,
            baseline_texts=baseline_texts,
            candidate_texts=candidate_texts,
            items_by_chapter=items_by_chapter,
            quality=quality,
        )
        stale_after_candidate = _batch_stale_reasons(
            root=root,
            authority_store=authority_store,
            policy_version=policy.version,
            input_version=input_version,
            baseline_texts=baseline_texts,
        )
        case_ids, precise = _record_book_candidate_cases(
            root=root,
            project_id=request.project_id,
            batch_id=batch_id,
            run_id=run_id,
            policy_version=policy.version,
            input_version=input_version,
            baseline_texts=baseline_texts,
            candidate_texts=candidate_texts,
            items_by_chapter=items_by_chapter,
            chapter_gates=chapter_gates,
            quality=quality,
        )
        if stale_after_candidate:
            _mark_batch_stale(root, case_ids, stale_after_candidate)
        passed = not stale_after_candidate and bool(chapter_gates) and precise and all(
            bool(item.get("passed")) for item in chapter_gates.values()
        )
        if passed:
            _request_batch_approval(root, case_ids, batch_id)
        manifest.update(
            status=(
                "awaiting_approval"
                if passed
                else "stale"
                if stale_after_candidate
                else "manual_required"
            ),
            case_ids=case_ids,
            candidate_hashes={
                str(chapter): repair_content_hash(text)
                for chapter, text in candidate_texts.items()
            },
            chapter_gates={str(key): value for key, value in chapter_gates.items()},
            quality=quality,
            stale_reasons=stale_after_candidate,
        )
        atomic_write_json(manifest_path, manifest)
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:1000])
        atomic_write_json(manifest_path, manifest)
        raise

    summary = {
        "run_id": run_id,
        "batch_id": batch_id,
        "mode": "candidate_batch",
        "status": manifest["status"],
        "requested": len(queue_items),
        "processed": len(chapter_numbers),
        "candidate_chapters": chapter_numbers,
        "case_ids": list(manifest["case_ids"]),
        "formal_writes": 0,
        "proposal_required": True,
        "publish_enabled": False,
        "chapter_gates": manifest["chapter_gates"],
        "stale_reasons": list(manifest.get("stale_reasons", [])),
    }
    if on_step_progress:
        on_step_progress("book_repair_candidate_batch_done", summary)
    return summary


def _group_queue_items(queue_items: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in queue_items:
        try:
            chapter = int(item.get("target_chapter") or 0)
        except (TypeError, ValueError):
            continue
        if chapter > 0:
            grouped.setdefault(chapter, []).append(item)
    return grouped


def _clone_project_for_candidate(source: Path, destination: Path) -> None:
    """Copy project inputs once, excluding authority and unrelated output roots."""

    destination.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        if child.name in _CLONE_EXCLUDED_NAMES or child.is_symlink():
            continue
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, symlinks=False)
        elif child.is_file():
            shutil.copy2(child, target)


def _runtime_for_candidate(runtime: Any, storage_root: Path) -> Any:
    storage = FileSystemStorage(storage_root)
    if dataclasses.is_dataclass(runtime):
        return dataclasses.replace(
            cast(Any, runtime),
            storage=storage,
            _control_plane=None,
            memory_contexts=OrderedDict(),
            _memory_lock=None,
            _memory_context_ref_counts={},
            _memory_context_pending_releases=set(),
            _eviction_tasks=set(),
            _humanize_embedder=None,
        )
    values = dict(vars(runtime))
    values.update(storage=storage, memory_contexts=OrderedDict())
    return SimpleNamespace(**values)


async def _default_candidate_executor(
    runtime: Any,
    request: GlobalRepairQueueRequest,
    on_step_progress: StepCallback,
) -> ExecutionResult[Any]:
    from novel_forge.workspace.book_ops.execution_book_entry import (
        execute_global_repair_queue,
    )

    return await execute_global_repair_queue(
        runtime,
        request,
        on_step_progress=on_step_progress,
    )


async def _default_quality_evaluator(
    runtime: Any,
    request: GlobalRepairQueueRequest,
    baseline_texts: dict[int, str],
    candidate_texts: dict[int, str],
    items_by_chapter: dict[int, list[dict[str, Any]]],
    on_step_progress: StepCallback,
) -> dict[str, Any]:
    from novel_forge.story_kernel.store import StoryKernelStore
    from novel_forge.workspace.book_ops.execution_book_audit_post_repair import (
        _lightweight_evidence_verify,
        _run_post_repair_targeted_audit,
    )
    from novel_forge.workspace.book_ops.execution_book_repair import (
        _evaluate_book_repair_text_guard,
    )

    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    chapter_numbers = sorted(candidate_texts)
    all_texts: dict[int, str] = {}
    for path in sorted(layout.chapters_dir.glob("chapter_*.md")):
        try:
            chapter = int(path.stem.split("_")[-1])
        except (IndexError, ValueError):
            continue
        all_texts[chapter] = path.read_text(encoding="utf-8")

    canon_state: dict[str, Any] = {}
    try:
        kernel = await StoryKernelStore(layout.story_kernel_db_path).load_kernel(
            request.project_id
        )
        if hasattr(kernel, "model_dump"):
            canon_state = kernel.model_dump(mode="json")
    except Exception:
        canon_state = {}

    audit_request = BookConsistencyRequest(
        project_id=request.project_id,
        chapter_range=chapter_numbers,
        analysis_mode="full_text",
        chapter_max_chars=100000,
        repair_mode="off",
        parallel_chunks=False,
        parallel_dimensions=False,
        post_repair_targeted_audit=True,
    )
    guards: dict[str, Any] = {}
    original_rechecks: dict[str, dict[str, Any]] = {}
    regressions: dict[str, list[dict[str, Any]]] = {}
    propagation: dict[str, list[dict[str, Any]]] = {}
    regression_detector = RegressionDetector()
    propagation_validator = PropagationValidator()
    for chapter in chapter_numbers:
        original = baseline_texts[chapter]
        candidate = candidate_texts[chapter]
        guards[str(chapter)] = _evaluate_book_repair_text_guard(
            original,
            candidate,
            request=audit_request,
            runtime=runtime,
        )
        chapter_issues = [_queue_issue_payload(item) for item in items_by_chapter[chapter]]
        try:
            residual_issues, recheck_stats = _lightweight_evidence_verify(
                chapter_issues,
                [chapter],
                layout,
                prefer_official_text=True,
            )
            expected_count = len(chapter_issues)
            original_rechecks[str(chapter)] = {
                "executed": True,
                "expected_count": expected_count,
                "residual_issues": residual_issues,
                "stats": recheck_stats,
                "passed": (
                    int(recheck_stats.get("checked", 0) or 0) == expected_count
                    and int(recheck_stats.get("rejected", 0) or 0) == expected_count
                    and int(recheck_stats.get("confirmed", 0) or 0) == 0
                    and int(recheck_stats.get("suspected", 0) or 0) == 0
                    and int(recheck_stats.get("errors", 0) or 0) == 0
                ),
            }
        except Exception as exc:
            original_rechecks[str(chapter)] = {
                "executed": False,
                "expected_count": len(chapter_issues),
                "residual_issues": chapter_issues,
                "stats": {},
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
        detected = await regression_detector.detect_regressions(
            original_text=original,
            repaired_text=candidate,
            chapter_issues=chapter_issues,
            all_chapter_texts=all_texts,
            canon_state=canon_state,
            chapter_number=chapter,
        )
        regressions[str(chapter)] = [item.model_dump() for item in detected]
        propagated = await propagation_validator.validate_propagation(
            repaired_chapter=chapter,
            original_text=original,
            repaired_text=candidate,
            subsequent_chapters=[number for number in sorted(all_texts) if number > chapter],
            all_chapter_texts=all_texts,
        )
        propagation[str(chapter)] = [item.model_dump() for item in propagated]

    post_audit = await _run_post_repair_targeted_audit(
        runtime=runtime,
        request=audit_request,
        layout=layout,
        target_chapters=chapter_numbers,
        max_tokens=int(getattr(runtime.settings, "long_book_audit_max_tokens", 8192) or 8192),
        temperature=float(getattr(runtime.settings, "temp_book_consistency", 0.2) or 0.2),
        on_step_progress=on_step_progress,
        prefer_official_text=True,
    )
    return {
        "contamination_guards": guards,
        "original_rechecks_by_chapter": original_rechecks,
        "regressions_by_chapter": regressions,
        "propagation_by_chapter": propagation,
        "post_repair_targeted_audit": post_audit,
    }


def _chapter_gate_results(
    *,
    chapter_numbers: list[int],
    candidate_summary: dict[str, Any],
    baseline_texts: dict[int, str],
    candidate_texts: dict[int, str],
    items_by_chapter: dict[int, list[dict[str, Any]]],
    quality: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    results = [item for item in candidate_summary.get("results", []) if isinstance(item, dict)]
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for item in results:
        try:
            chapter = int(item.get("target_chapter") or 0)
        except (TypeError, ValueError):
            continue
        by_chapter.setdefault(chapter, []).append(item)
    post_audit = quality.get("post_repair_targeted_audit")
    post_issues = (
        [item for item in post_audit.get("issues", []) if isinstance(item, dict)]
        if isinstance(post_audit, dict)
        else []
    )
    global_post_issues = [
        item for item in post_issues if int(item.get("primary_chapter", 0) or 0) <= 0
    ]
    gates: dict[int, dict[str, Any]] = {}
    for chapter in chapter_numbers:
        chapter_results = by_chapter.get(chapter, [])
        expected_items = items_by_chapter[chapter]
        original_closed = len(chapter_results) == len(expected_items) and all(
            bool(item.get("applied"))
            and bool(item.get("text_changed"))
            and str(item.get("status") or "") == "needs_finalize"
            and not item.get("post_repair_verify")
            for item in chapter_results
        )
        original_recheck = (quality.get("original_rechecks_by_chapter") or {}).get(
            str(chapter), {}
        )
        original_closed = original_closed and bool(original_recheck.get("passed", False))
        changed = repair_content_hash(baseline_texts[chapter]) != repair_content_hash(
            candidate_texts[chapter]
        )
        guard = (quality.get("contamination_guards") or {}).get(str(chapter), {})
        regressions = (quality.get("regressions_by_chapter") or {}).get(str(chapter), [])
        propagation = (quality.get("propagation_by_chapter") or {}).get(str(chapter), [])
        chapter_post_issues = [
            item
            for item in post_issues
            if int(item.get("primary_chapter", 0) or 0) == chapter
        ]
        reasons: list[str] = []
        if not changed:
            reasons.append("candidate_unchanged")
        if not original_closed:
            reasons.append("original_ticket_recheck_failed")
        if not bool(guard.get("ok", False)):
            reasons.append("contamination_guard_failed")
        if regressions:
            reasons.append("regression_detected")
        if propagation:
            reasons.append("propagation_failed")
        if chapter_post_issues or global_post_issues:
            reasons.append("targeted_reaudit_found_issues")
        gates[chapter] = {
            "passed": not reasons,
            "reasons": reasons,
            "source_hash": repair_content_hash(baseline_texts[chapter]),
            "candidate_hash": repair_content_hash(candidate_texts[chapter]),
            "original_ticket_count": len(expected_items),
            "original_recheck_results": chapter_results,
            "original_issue_recheck": original_recheck,
            "contamination_guard": guard,
            "regressions": regressions,
            "propagation_issues": propagation,
            "targeted_reaudit_issues": [*global_post_issues, *chapter_post_issues],
        }
    return gates


def _record_book_candidate_cases(
    *,
    root: Path,
    project_id: str,
    batch_id: str,
    run_id: str,
    policy_version: int,
    input_version: str,
    baseline_texts: dict[int, str],
    candidate_texts: dict[int, str],
    items_by_chapter: dict[int, list[dict[str, Any]]],
    chapter_gates: dict[int, dict[str, Any]],
    quality: dict[str, Any],
) -> tuple[list[str], bool]:
    store = RepairCaseStore(root)
    case_ids: list[str] = []
    all_precise = True
    for chapter in sorted(items_by_chapter):
        source_text = baseline_texts[chapter]
        candidate_text = candidate_texts[chapter]
        issues = [
            _queue_audit_issue(item, chapter=chapter, source_text=source_text)
            for item in items_by_chapter[chapter]
        ]
        source_hash = repair_content_hash(source_text)
        candidate_hash = repair_content_hash(candidate_text)
        case_id = f"book-{batch_id[:20]}-{chapter}"
        source_blob = store.put_blob(source_text, kind="repair_source")
        case = store.create_case(
            RepairCase(
                case_id=case_id,
                project_id=project_id,
                content_type="book_repair_chapter",
                source="book_consistency_audit",
                artifact_id=f"chapter:{chapter}:official",
                source_version=f"chapter:{chapter}:{source_hash[:16]}",
                source_hash=source_hash,
                authority="proposal_required",
                input_version=input_version,
                policy_version=policy_version,
                title=f"全书审查第 {chapter} 章修复候选",
                chapter_numbers=[chapter],
                issues=issues,
                metadata={
                    "batch_id": batch_id,
                    "run_id": run_id,
                    "source_blob_hash": source_blob,
                    "publication": "batch_proposal_only",
                    "formal_writes": 0,
                },
            )
        )
        targets = [_resolved_issue_target(issue, source_text) for issue in issues]
        precise = bool(targets) and all(item.resolution_status == "resolved" for item in targets)
        if precise:
            targets.append(_full_chapter_target(issues, chapter, source_text))
        case = store.append_event(
            case.case_id,
            "targets_resolved",
            {"targets": [item.model_dump(mode="json") for item in targets]},
            expected_version=case.version,
        )
        case_ids.append(case.case_id)
        if not precise:
            all_precise = False
            continue
        candidate_blob = store.put_blob(candidate_text)
        scope = targets[-1]
        candidate = RepairCandidate(
            case_id=case.case_id,
            version=1,
            base_hash=source_hash,
            candidate_hash=candidate_hash,
            blob_hash=candidate_blob,
            origin="model",
            patch_count=1,
            change_ratio=_text_change_ratio(source_text, candidate_text),
            patches=[
                RepairPatchRecord(
                    target_id=scope.target_id,
                    expected_hash=scope.current_hash,
                    replacement_hash=candidate_hash,
                    operation="chapter_rewrite",
                    field_path="$text",
                    rationale="verified whole-book repair candidate",
                )
            ],
            protected_items=list(_PROTECTED_ITEMS),
            metadata={"batch_id": batch_id, "chapter_number": chapter},
        )
        case = store.append_event(
            case.case_id,
            "candidate_built",
            {"candidate": candidate.model_dump(mode="json")},
            expected_version=case.version,
        )
        case = store.append_event(
            case.case_id,
            "verification_requested",
            {},
            expected_version=case.version,
        )
        gate = chapter_gates[chapter]
        validators = _book_validators(gate)
        passed = bool(gate.get("passed"))
        issue_ids = [issue.issue_id for issue in issues]
        verification = RepairVerificationBundle(
            case_id=case.case_id,
            candidate_version=1,
            candidate_hash=candidate_hash,
            passed=passed,
            resolved_issue_ids=issue_ids if passed else [],
            residual_issue_ids=[] if passed else issue_ids,
            regression_issue_ids=[
                _quality_issue_id("regression", item)
                for item in gate.get("regressions", [])
            ]
            + [
                _quality_issue_id("propagation", item)
                for item in gate.get("propagation_issues", [])
            ],
            validators=validators,
            details=list(gate.get("reasons", [])),
            metadata={
                "batch_id": batch_id,
                "published": False,
                "quality_blob_hash": store.put_blob(quality, kind="repair_verification"),
            },
        )
        case = store.append_event(
            case.case_id,
            "verification_completed",
            {"verification": verification.model_dump(mode="json")},
            expected_version=case.version,
        )
    return case_ids, all_precise


def _request_batch_approval(root: Path, case_ids: list[str], batch_id: str) -> None:
    """Move every verified member together; partial batches stay non-publishable."""

    store = RepairCaseStore(root)
    cases = [store.load_case(case_id) for case_id in case_ids]
    if any(case is None or case.status != "verified" for case in cases):
        raise ValueError("book repair batch cannot request approval unless every case is verified")
    for case in cases:
        assert case is not None
        store.append_event(
            case.case_id,
            "approval_requested",
            {"proposal_id": f"book-batch:{batch_id}"},
            expected_version=case.version,
        )


def _batch_stale_reasons(
    *,
    root: Path,
    authority_store: AuthoringStore,
    policy_version: int,
    input_version: str,
    baseline_texts: dict[int, str],
) -> list[dict[str, Any]]:
    """Recheck live CAS and authority after candidate work, before approval."""

    reasons: list[dict[str, Any]] = []
    try:
        authority_store.require_enabled()
    except Exception as exc:
        reasons.append({"kind": "authority_disabled", "reason": str(exc)})
    current_policy = authority_store.policy()
    if current_policy is None:
        reasons.append({"kind": "policy_removed"})
    elif current_policy.version != policy_version or current_policy.stopped:
        reasons.append(
            {
                "kind": "policy_changed",
                "expected_version": policy_version,
                "current_version": current_policy.version,
                "stopped": current_policy.stopped,
            }
        )
    current_input_version = story_input_version(root)
    if current_input_version != input_version:
        reasons.append(
            {
                "kind": "input_version_changed",
                "expected_version": input_version,
                "current_version": current_input_version,
            }
        )
    layout = ProjectLayout(root)
    for chapter, baseline_text in baseline_texts.items():
        path = layout.chapter_path(chapter)
        current_text = path.read_text(encoding="utf-8") if path.is_file() else ""
        expected_hash = repair_content_hash(baseline_text)
        current_hash = repair_content_hash(current_text)
        if current_hash != expected_hash:
            reasons.append(
                {
                    "kind": "chapter_source_changed",
                    "chapter_number": chapter,
                    "expected_hash": expected_hash,
                    "current_hash": current_hash,
                }
            )
    return reasons


def _mark_batch_stale(
    root: Path,
    case_ids: list[str],
    stale_reasons: list[dict[str, Any]],
) -> None:
    store = RepairCaseStore(root)
    for case_id in case_ids:
        case = store.load_case(case_id)
        if case is None:
            continue
        store.append_event(
            case.case_id,
            "case_stale",
            {
                "reason": "book repair batch changed before approval",
                "stale_reasons": stale_reasons,
            },
            expected_version=case.version,
        )


def _queue_issue_payload(item: dict[str, Any]) -> dict[str, Any]:
    ticket_value = item.get("ticket")
    ticket: dict[str, Any] = ticket_value if isinstance(ticket_value, dict) else {}
    metadata_value = ticket.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    return {
        "issue_id": str(item.get("finding_id") or (ticket.get("finding_ids") or [""])[0]),
        "issue_type": str(ticket.get("issue_type") or ticket.get("dimension") or "global_audit"),
        "severity": str(ticket.get("severity") or "warning"),
        "description": str(ticket.get("target_summary") or ticket.get("repair_goal") or ""),
        "evidence": str(metadata.get("evidence_quote") or ""),
        "primary_chapter": _safe_positive_int(item.get("target_chapter")),
    }


def _queue_audit_issue(
    item: dict[str, Any], *, chapter: int, source_text: str
) -> AuditIssueV2:
    ticket_value = item.get("ticket")
    ticket: dict[str, Any] = ticket_value if isinstance(ticket_value, dict) else {}
    metadata_value = ticket.get("metadata")
    metadata: dict[str, Any] = metadata_value if isinstance(metadata_value, dict) else {}
    raw_id = str(
        item.get("finding_id")
        or (ticket.get("finding_ids") or [""])[0]
        or item.get("queue_item_id")
        or uuid4().hex
    )
    issue_id = "book-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:20]
    summary = str(ticket.get("target_summary") or ticket.get("repair_goal") or "全书审查问题")
    quote = str(metadata.get("evidence_quote") or ticket.get("evidence") or "").strip()
    start = _safe_positive_int(ticket.get("target_paragraph_start"))
    end = _safe_positive_int(ticket.get("target_paragraph_end")) or start
    locator = _prose_locator(
        source_text,
        chapter=chapter,
        stable_node_id=f"chapter:{chapter}:book-issue:{issue_id}",
        quote=quote,
        paragraph_start=start,
        paragraph_end=end,
    )
    severity = _audit_severity(str(ticket.get("severity") or "warning"))
    must_preserve = [str(item) for item in ticket.get("must_preserve") or [] if str(item)]
    return AuditIssueV2(
        issue_id=issue_id,
        dimension=str(ticket.get("dimension") or "book_consistency"),
        issue_type=str(ticket.get("issue_type") or "global_audit"),
        severity=severity,
        blocking=severity in {"critical", "high"},
        summary=summary[:500],
        description=summary,
        evidence=[
            AuditEvidence(
                quote=quote or summary,
                source="book_consistency_audit",
                locator=locator,
                confidence=locator.confidence,
            )
        ],
        repair_targets=[locator],
        reference_targets=[],
        repair_intent=AuditRepairIntent(
            operation="window_rewrite" if locator.target_format == "prose_text" else "manual_review",
            target_policy=(
                "single_exact_target" if locator.target_format == "prose_text" else "manual_only"
            ),
            rationale=str(ticket.get("repair_goal") or summary),
            preserve=list(dict.fromkeys([*_PROTECTED_ITEMS, *must_preserve])),
            allowed_strategies=["patch", "window_rewrite", "manual_review"],
        ),
        postconditions=[
            AuditPostcondition(
                validator_id="book_original_ticket_recheck_v1",
                description="在隔离候选上重跑原队列问题复验",
            )
        ],
        metadata={"queue_item_id": str(item.get("queue_item_id") or ""), "ticket": ticket},
    )


def _prose_locator(
    text: str,
    *,
    chapter: int,
    stable_node_id: str,
    quote: str,
    paragraph_start: int,
    paragraph_end: int,
) -> AuditLocator:
    if quote:
        first = text.find(quote)
        if first >= 0 and text.find(quote, first + 1) < 0:
            return AuditLocator(
                target_format="prose_text",
                surface="chapter_text",
                confidence=1.0,
                stable_node_id=stable_node_id,
                chapter_number=chapter,
                quote=quote,
                text_hash=repair_content_hash(text),
                char_start=first,
                char_end=first + len(quote),
            )
    paragraphs = _paragraph_offsets(text)
    if 0 < paragraph_start <= len(paragraphs):
        end_index = min(max(paragraph_end or paragraph_start, paragraph_start), len(paragraphs))
        char_start = paragraphs[paragraph_start - 1][0]
        char_end = paragraphs[end_index - 1][1]
        return AuditLocator(
            target_format="prose_text",
            surface="chapter_text",
            confidence=0.9,
            stable_node_id=stable_node_id,
            chapter_number=chapter,
            paragraph_start=paragraph_start,
            paragraph_end=end_index,
            quote=text[char_start:char_end],
            text_hash=repair_content_hash(text),
            char_start=char_start,
            char_end=char_end,
        )
    return AuditLocator(
        target_format="manual_only",
        surface="chapter_text",
        confidence=0.0,
        stable_node_id=stable_node_id,
        chapter_number=chapter,
        text_hash=repair_content_hash(text),
        manual_review_reason="book_repair_issue_has_no_unique_quote_or_paragraph_window",
    )


def _paragraph_offsets(text: str) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        content_end = cursor + len(line.rstrip("\r\n"))
        if line.strip():
            offsets.append((cursor, content_end))
        cursor += len(line)
    if text and not offsets:
        offsets.append((0, len(text)))
    return offsets


def _resolved_issue_target(issue: AuditIssueV2, source_text: str) -> ResolvedRepairTarget:
    locator = issue.repair_targets[0]
    if locator.target_format != "prose_text":
        return ResolvedRepairTarget(
            target_id=f"{issue.issue_id}:manual",
            target_format="manual_only",
            surface="chapter_text",
            locator=locator,
            issue_ids=[issue.issue_id],
            allowed_operation="manual_review",
            confidence=0.0,
            resolution_status="manual_required",
            reason=locator.manual_review_reason,
        )
    selected = source_text[locator.char_start : locator.char_end]
    return ResolvedRepairTarget(
        target_id=f"{issue.issue_id}:target",
        target_format="prose_text",
        surface="chapter_text",
        locator=locator,
        window={"char_start": locator.char_start, "char_end": locator.char_end},
        current_value=selected,
        current_hash=repair_content_hash(selected),
        issue_ids=[issue.issue_id],
        allowed_operation="window_rewrite",
        confidence=locator.confidence,
    )


def _full_chapter_target(
    issues: list[AuditIssueV2], chapter: int, source_text: str
) -> ResolvedRepairTarget:
    locator = AuditLocator(
        target_format="prose_text",
        surface="chapter_text",
        confidence=1.0,
        stable_node_id=f"chapter:{chapter}:book-candidate-scope",
        chapter_number=chapter,
        paragraph_start=1,
        paragraph_end=max(1, len(_paragraph_offsets(source_text))),
        quote=source_text,
        text_hash=repair_content_hash(source_text),
        char_start=0,
        char_end=len(source_text),
    )
    return ResolvedRepairTarget(
        target_id=f"book-chapter:{chapter}:candidate-scope",
        target_format="prose_text",
        surface="chapter_text",
        locator=locator,
        window={"char_start": 0, "char_end": len(source_text)},
        current_value=source_text,
        current_hash=repair_content_hash(source_text),
        issue_ids=[issue.issue_id for issue in issues],
        allowed_operation="window_rewrite",
        confidence=1.0,
    )


def _book_validators(gate: dict[str, Any]) -> list[RepairValidatorResult]:
    reasons = set(str(item) for item in gate.get("reasons", []))
    return [
        RepairValidatorResult(
            validator_id="book_original_ticket_recheck_v1",
            passed="original_ticket_recheck_failed" not in reasons,
            evidence={
                "execution_results": gate.get("original_recheck_results", []),
                "issue_recheck": gate.get("original_issue_recheck", {}),
            },
        ),
        RepairValidatorResult(
            validator_id="book_candidate_cas_v1",
            passed=gate.get("source_hash") != gate.get("candidate_hash"),
            evidence={
                "source_hash": gate.get("source_hash"),
                "candidate_hash": gate.get("candidate_hash"),
            },
        ),
        RepairValidatorResult(
            validator_id="book_pollution_guard_v1",
            passed="contamination_guard_failed" not in reasons,
            evidence=dict(gate.get("contamination_guard") or {}),
        ),
        RepairValidatorResult(
            validator_id="book_regression_guard_v1",
            passed="regression_detected" not in reasons,
            evidence={"issues": gate.get("regressions", [])},
        ),
        RepairValidatorResult(
            validator_id="book_propagation_guard_v1",
            passed="propagation_failed" not in reasons,
            evidence={"issues": gate.get("propagation_issues", [])},
        ),
        RepairValidatorResult(
            validator_id="book_targeted_reaudit_v1",
            passed="targeted_reaudit_found_issues" not in reasons,
            evidence={"issues": gate.get("targeted_reaudit_issues", [])},
        ),
    ]


def _quality_issue_id(prefix: str, item: Any) -> str:
    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _text_change_ratio(before: str, after: str) -> float:
    from difflib import SequenceMatcher

    return max(0.0, min(1.0, 1.0 - SequenceMatcher(None, before, after).ratio()))


def _safe_positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _audit_severity(value: str) -> AuditSeverity:
    normalized = value.strip().lower()
    severity_by_name: dict[str, AuditSeverity] = {
        "critical": "critical",
        "high": "high",
        "warning": "high",
        "medium": "medium",
        "info": "low",
        "low": "low",
    }
    return severity_by_name.get(normalized, "medium")


__all__ = ["prepare_book_repair_candidate_batch"]
