"""State management helpers for causal repair checkpoint/resume.

This module contains all state-management logic related to:
- checkpoint/resume/state file paths
- hash computation for progress validation
- causal repair progress serialization/deserialization
- attempt counter persistence
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.core.exceptions import StorageError, ValidationError
from novel_forge.core.utils.text_hash import source_text_hash as _canonical_source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import FileSystemStorage

_log = get_logger("workspace.execution_state")
_source_text_hash = _canonical_source_text_hash


def _causal_repair_progress_path(layout: Any, chapter_num: int) -> Any:
    """Stage-checkpoint file for causal repair resume."""
    return layout.states_dir / f"chapter_{chapter_num:03d}_repair_causal_progress.json"


def _causal_repair_attempts_path(layout: Any, chapter_num: int) -> Any:
    """State file path for per-issue causal repair escalation counters."""
    return layout.states_dir / f"chapter_{chapter_num:03d}_causal_repair_attempts.json"


def _stamp_source_text_hash(
    storage: FileSystemStorage,
    path: Path,
    text_hash: str,
) -> None:
    """Stamp *source_text_hash* onto an existing JSON report file.

    Used when a re-evaluation sub-step is skipped (missing artifacts) or fails
    (exception).  The report content is preserved but the hash is updated so
    the UI stale-report check no longer flags it as based on old text.
    """
    try:
        if not storage.exists(path):
            return
        payload = storage.load_json(path)
        if isinstance(payload, dict):
            payload["source_text_hash"] = text_hash
            storage.save_json(path, payload)
    except (OSError, json.JSONDecodeError):
        _log.debug("Failed to save guard hash", exc_info=True)


def _serialize_causal_repair_result(result: Any) -> dict[str, Any]:
    issues_payload: list[dict[str, Any]] = []
    for issue in getattr(result, "issues", []) or []:
        if hasattr(issue, "model_dump"):
            issues_payload.append(issue.model_dump(mode="json"))
    return {
        "revised_text": str(getattr(result, "revised_text", "") or ""),
        "issues": issues_payload,
        "applied": bool(getattr(result, "applied", False)),
        "patches_applied": int(getattr(result, "patches_applied", 0) or 0),
        "patches_attempted": int(getattr(result, "patches_attempted", 0) or 0),
        "failure_reason": getattr(result, "failure_reason", None),
        "repaired_issue_types": list(getattr(result, "repaired_issue_types", []) or []),
        "warnings": list(getattr(result, "warnings", []) or []),
    }


def _deserialize_causal_repair_result(payload: dict[str, Any]) -> Any:
    from novel_forge.core.schemas.chapter import CausalIssue
    from novel_forge.pipeline.steps.causal_repair_step import CausalRepairResult

    issues: list[CausalIssue] = []
    for raw_issue in payload.get("issues") or []:
        if isinstance(raw_issue, dict):
            try:
                issues.append(CausalIssue.model_validate(raw_issue))
            except (TypeError, ValueError, ValidationError):
                continue
    return CausalRepairResult(
        revised_text=str(payload.get("revised_text", "") or ""),
        issues=tuple(issues),
        applied=bool(payload.get("applied", False)),
        patches_applied=int(payload.get("patches_applied", 0) or 0),
        patches_attempted=int(payload.get("patches_attempted", 0) or 0),
        failure_reason=(
            str(payload["failure_reason"]) if payload.get("failure_reason") is not None else None
        ),
        repaired_issue_types=tuple(
            str(item).strip()
            for item in (payload.get("repaired_issue_types") or [])
            if str(item).strip()
        ),
    )


def _save_causal_repair_progress(
    *,
    storage: Any,
    path: Any,
    chapter_number: int,
    issue_signatures: list[str],
    stage: str,
    current_text_hash: str,
    result: Any,
    unresolved_signatures: set[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "chapter_number": int(chapter_number),
        "issue_signatures": list(issue_signatures),
        "stage": str(stage),
        "current_text_hash": str(current_text_hash),
        "result": _serialize_causal_repair_result(result),
    }
    if unresolved_signatures is None:
        payload["unresolved_signatures"] = None
    else:
        payload["unresolved_signatures"] = sorted(str(item) for item in unresolved_signatures)
    storage.save_json(path, payload)


def _load_causal_repair_progress(storage: Any, path: Any) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = storage.load_json(path)
    except (OSError, StorageError):
        _log.debug("_load_causal_repair_progress failed for %s", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _clear_causal_repair_progress(_storage: Any, path: Any) -> None:
    if path.exists():
        path.unlink()


# ── Continuity repair progress (checkpoint/resume) ──────────────────────────────────


def _continuity_repair_progress_path(layout: Any, chapter_num: int) -> Any:
    return layout.states_dir / f"chapter_{chapter_num:03d}_repair_continuity_progress.json"


def _serialize_continuity_repair_result(result: Any) -> dict[str, Any]:
    issues_payload: list[dict[str, Any]] = []
    for issue in getattr(result, "issues", []) or []:
        if hasattr(issue, "model_dump"):
            issues_payload.append(issue.model_dump(mode="json"))
    payload: dict[str, Any] = {
        "revised_text": str(getattr(result, "revised_text", "") or ""),
        "issues": issues_payload,
        "applied": bool(getattr(result, "applied", False)),
        "rounds": int(getattr(result, "rounds", 0) or 0),
        "drift_detected": bool(getattr(result, "drift_detected", False)),
        "rollback_count": int(getattr(result, "rollback_count", 0) or 0),
        "final_accepted": bool(getattr(result, "final_accepted", False)),
        "best_effort_reason": str(getattr(result, "best_effort_reason", "") or ""),
        "needs_human_review": bool(getattr(result, "needs_human_review", False)),
        "warnings": list(getattr(result, "warnings", []) or []),
        "patch_only": bool(getattr(result, "patch_only", False)),
        "repaired_issue_types": list(getattr(result, "repaired_issue_types", []) or []),
        "failure_reason": str(getattr(result, "failure_reason", "") or ""),
    }
    report_dump = getattr(getattr(result, "report", None), "model_dump", None)
    payload["report"] = report_dump(mode="json") if callable(report_dump) else None
    repair_plan_dump = getattr(getattr(result, "repair_plan", None), "model_dump", None)
    payload["repair_plan"] = (
        repair_plan_dump(mode="json") if callable(repair_plan_dump) else None
    )
    return payload


def _deserialize_continuity_repair_result(payload: dict[str, Any]) -> Any:
    from novel_forge.core.schemas.continuity import RepairPlan
    from novel_forge.pipeline.steps.continuity_repair_step import ContinuityRepairResult

    repair_plan = RepairPlan()
    if payload.get("repair_plan"):
        try:
            repair_plan = RepairPlan.model_validate(payload["repair_plan"])
        except ValidationError:
            repair_plan = RepairPlan()
    return ContinuityRepairResult(
        revised_text=str(payload.get("revised_text", "") or ""),
        repair_plan=repair_plan,
        applied=bool(payload.get("applied", False)),
        failure_reason=str(payload.get("failure_reason", "") or "") or None,
        warnings=tuple(str(item) for item in payload.get("warnings", []) or []),
        patch_only=bool(payload.get("patch_only", False)),
        repaired_issue_types=tuple(
            str(item) for item in payload.get("repaired_issue_types", []) or []
        ),
    )


def _save_continuity_repair_progress(
    *,
    storage: Any,
    path: Any,
    chapter_number: int,
    issue_signatures: list[str],
    stage: str,
    current_text_hash: str,
    result: Any,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "chapter_number": int(chapter_number),
        "issue_signatures": list(issue_signatures),
        "stage": str(stage),
        "current_text_hash": str(current_text_hash),
        "result": _serialize_continuity_repair_result(result),
    }
    storage.save_json(path, payload)


def _load_continuity_repair_progress(storage: Any, path: Any) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = storage.load_json(path)
    except (OSError, StorageError):
        _log.debug("_load_continuity_repair_progress failed for %s", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _clear_continuity_repair_progress(_storage: Any, path: Any) -> None:
    if path.exists():
        path.unlink()


def _load_causal_repair_attempts(storage: Any, path: Any) -> dict[str, int]:
    """Load per-issue consecutive unresolved-attempt counters."""
    if not path.exists():
        return {}
    try:
        raw = storage.load_json(path) or {}
    except (OSError, StorageError):
        _log.debug("_load_causal_repair_attempts failed for %s", path, exc_info=True)
        return {}
    payload = raw.get("attempts", raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        return {}
    attempts: dict[str, int] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            attempts[key] = count
    return attempts


def _save_causal_repair_attempts(storage: Any, path: Any, attempts: dict[str, int]) -> None:
    """Persist per-issue unresolved counters for future escalation decisions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    storage.save_json(
        path,
        {
            "schema_version": "1.0",
            "attempts": dict(sorted((k, int(v)) for k, v in attempts.items() if int(v) > 0)),
        },
    )


def _update_causal_repair_attempts(
    *,
    storage: Any,
    path: Any,
    previous_attempts: dict[str, int],
    selected_issue_signatures: set[str],
    unresolved_issue_signatures: set[str] | None,
) -> None:
    """Update counters:
    - ``unresolved_issue_signatures is None`` => verification unavailable, treat selected
      issues as unresolved (increment counters to trigger conservative escalation next run).
    - otherwise: increment unresolved, clear resolved.
    """
    if not selected_issue_signatures:
        return

    updated = dict(previous_attempts)
    if unresolved_issue_signatures is None:
        for sig in selected_issue_signatures:
            updated[sig] = int(updated.get(sig, 0) or 0) + 1
    else:
        for sig in selected_issue_signatures:
            if sig in unresolved_issue_signatures:
                updated[sig] = int(updated.get(sig, 0) or 0) + 1
            else:
                updated.pop(sig, None)

    if updated == previous_attempts:
        return
    _save_causal_repair_attempts(storage, path, updated)
