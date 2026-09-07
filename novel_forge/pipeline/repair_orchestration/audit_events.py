"""Shared repair audit event payload helpers.

These helpers keep audit/repair observability on the existing ``on_step`` /
``ProjectRunLogger`` path.  Payloads intentionally carry identity, location,
hashes, and outcome metadata only; they do not include full repaired text.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from novel_forge.core.utils.audit_issue import (
    VALID_REPAIR_SURFACES,
    ensure_issue_id,
    issue_field,
    normalize_audit_issue,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairDomain,
    RepairMission,
    RepairPlanCandidate,
    RepairSurface,
    RepairTarget,
)

StepCallback = Callable[[str, Any], None] | None


def text_hash(text: str) -> str:
    """Return a stable hash for text without logging the text itself."""

    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _value(value: Any) -> str:
    if isinstance(value, (RepairDomain, RepairSurface)):
        return value.value
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _payload(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if hasattr(obj, "__dict__"):
        return {
            key: value
            for key, value in vars(obj).items()
            if not key.startswith("_")
        }
    return {}


def _audit_surface_default(surface: str) -> str:
    value = str(surface or "").strip()
    return value if value in VALID_REPAIR_SURFACES else "chapter_text"


def _synthetic_issue_from_target(target: RepairTarget) -> dict[str, Any]:
    synthetic = target.payload.get("synthetic_issues") or []
    if synthetic and isinstance(synthetic[0], dict):
        return dict(synthetic[0])
    return {}


def build_repair_audit_event(
    *,
    event_type: str,
    dimension: str,
    surface: str = "",
    chapter: int = 0,
    round_number: int = 0,
    issue_id: str = "",
    issue_type: str = "",
    severity: str = "",
    status: str = "",
    paragraph_start: int = 0,
    paragraph_end: int = 0,
    artifact_path: str = "",
    json_path: str = "",
    source_text_hash: str = "",
    target_text_hash: str = "",
    repair_action: str = "",
    failure_kind: str = "",
    fallback_action: str = "",
    identity_mode: str = "",
    location_mode: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical event payload used by audit and repair modules."""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "event_type": event_type,
        "issue_id": str(issue_id or ""),
        "dimension": str(dimension or ""),
        "surface": str(surface or ""),
        "chapter": int(chapter or 0),
        "round": int(round_number or 0),
        "issue_type": str(issue_type or ""),
        "severity": str(severity or ""),
        "status": str(status or ""),
        "paragraph_start": int(paragraph_start or 0),
        "paragraph_end": int(paragraph_end or 0),
        "artifact_path": str(artifact_path or ""),
        "json_path": str(json_path or ""),
        "source_text_hash": str(source_text_hash or ""),
        "target_text_hash": str(target_text_hash or ""),
        "repair_action": str(repair_action or ""),
        "failure_kind": str(failure_kind or ""),
        "fallback_action": str(fallback_action or ""),
        "identity_mode": str(identity_mode or ""),
        "location_mode": str(location_mode or ""),
    }
    if extra:
        for key, value in extra.items():
            if key not in payload:
                payload[key] = value
    return payload


def issue_audit_payload(
    issue: Any,
    *,
    event_type: str,
    dimension: str,
    surface: str = "chapter_text",
    chapter: int = 0,
    round_number: int = 0,
    status: str = "",
    repair_action: str = "",
    source_text_hash: str = "",
    target_text_hash: str = "",
    failure_kind: str = "",
    fallback_action: str = "",
    identity_mode: str = "issue_id",
    location_mode: str = "paragraph_window",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt a chapter-text issue object into the canonical audit event payload."""

    audit_issue = normalize_audit_issue(
        issue,
        dimension=dimension,
        namespace=dimension or "repair",
        chapter_number=chapter,
        repair_surface_default=_audit_surface_default(surface),
        source_module=dimension,
    )
    paragraph_start = audit_issue.paragraph_start
    paragraph_end = audit_issue.paragraph_end
    raw_span = issue_field(issue, "paragraph_span", [])
    if isinstance(raw_span, list) and raw_span:
        paragraph_start = _int(raw_span[0], paragraph_start)
        paragraph_end = _int(raw_span[-1], paragraph_end or paragraph_start)
    if paragraph_end < paragraph_start:
        paragraph_end = paragraph_start
    return build_repair_audit_event(
        event_type=event_type,
        issue_id=audit_issue.issue_id,
        dimension=dimension or audit_issue.dimension,
        surface=surface or audit_issue.repair_surface,
        chapter=chapter,
        round_number=round_number,
        issue_type=audit_issue.issue_type,
        severity=audit_issue.severity,
        status=status or audit_issue.status,
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
        source_text_hash=source_text_hash,
        target_text_hash=target_text_hash,
        repair_action=repair_action,
        failure_kind=failure_kind,
        fallback_action=fallback_action,
        identity_mode=identity_mode,
        location_mode=location_mode,
        extra=extra,
    )


def artifact_issue_audit_payload(
    issue: Any,
    *,
    event_type: str = "artifact_issue",
    dimension: str = "init_artifact",
    surface: str = "",
    artifact_path: str = "",
    json_path: str = "",
    round_number: int = 0,
    status: str = "",
    repair_action: str = "",
    source_text_hash: str = "",
    target_text_hash: str = "",
    failure_kind: str = "",
    fallback_action: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt artifact-scoped issues using surface + JSON path location."""

    data = _payload(issue)
    issue_id = _first_nonempty(
        data.get("issue_id"),
        data.get("id"),
        data.get("ticket_id"),
        data.get("code"),
    )
    if not issue_id:
        issue_id = ensure_issue_id(
            data,
            dimension,
            repair_surface=surface or data.get("surface") or data.get("source") or "artifact",
        )
    resolved_path = _first_nonempty(json_path, data.get("json_path"), data.get("path"))
    resolved_artifact = _first_nonempty(
        artifact_path,
        data.get("artifact_path"),
        data.get("artifact"),
        data.get("source"),
        surface,
    )
    return build_repair_audit_event(
        event_type=event_type,
        issue_id=issue_id,
        dimension=dimension,
        surface=surface or _first_nonempty(data.get("surface"), data.get("source")),
        issue_type=_first_nonempty(data.get("issue_type"), data.get("type"), data.get("code")),
        severity=_first_nonempty(data.get("severity"), "high"),
        status=status or _first_nonempty(data.get("status"), "open"),
        artifact_path=resolved_artifact,
        json_path=resolved_path,
        source_text_hash=source_text_hash,
        target_text_hash=target_text_hash,
        repair_action=repair_action,
        failure_kind=failure_kind,
        fallback_action=fallback_action,
        identity_mode="artifact_path",
        location_mode="artifact_json_path",
        round_number=round_number,
        extra=extra,
    )


def book_issue_audit_payload(
    issue: Any,
    *,
    event_type: str = "book_issue",
    dimension: str = "book_consistency",
    surface: str = "book_chapter_set",
    chapter_number: int = 0,
    queue_target: str = "",
    round_number: int = 0,
    status: str = "",
    repair_action: str = "",
    failure_kind: str = "",
    fallback_action: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt book-consistency queue issues into canonical audit events."""

    data = _payload(issue)
    chapter = _int(
        chapter_number
        or data.get("chapter_number")
        or data.get("primary_chapter")
        or data.get("chapter"),
        0,
    )
    issue_id = _first_nonempty(
        data.get("issue_id"),
        data.get("ticket_id"),
        data.get("id"),
        data.get("issue_ref"),
        queue_target,
    )
    if not issue_id:
        issue_id = ensure_issue_id(data, dimension, chapter_number=chapter, repair_surface=surface)
    return build_repair_audit_event(
        event_type=event_type,
        issue_id=issue_id,
        dimension=dimension,
        surface=surface,
        chapter=chapter,
        round_number=round_number,
        issue_type=_first_nonempty(data.get("issue_type"), data.get("type"), data.get("category")),
        severity=_first_nonempty(data.get("severity"), "high"),
        status=status or _first_nonempty(data.get("status"), "open"),
        paragraph_start=_int(data.get("paragraph_index") or data.get("paragraph_start"), 0),
        paragraph_end=_int(data.get("paragraph_end") or data.get("paragraph_index"), 0),
        repair_action=repair_action,
        failure_kind=failure_kind,
        fallback_action=fallback_action,
        identity_mode="ticket_id",
        location_mode="book_queue",
        extra={"queue_target": queue_target or issue_id, **dict(extra or {})},
    )


def state_issue_audit_payload(
    issue: Any,
    *,
    event_type: str = "state_issue",
    dimension: str = "state_adjudication",
    surface: str = "chapter_text",
    chapter: int = 0,
    candidate_id: str = "",
    delta_id: str = "",
    round_number: int = 0,
    status: str = "",
    repair_action: str = "",
    failure_kind: str = "",
    fallback_action: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt narrative-state repair targets into canonical audit events."""

    data = _payload(issue)
    resolved_candidate = _first_nonempty(candidate_id, data.get("candidate_id"))
    resolved_delta = _first_nonempty(delta_id, data.get("delta_id"), data.get("state_delta_id"))
    issue_id = _first_nonempty(
        data.get("issue_id"),
        data.get("ticket_id"),
        data.get("id"),
        resolved_delta,
        resolved_candidate,
    )
    if not issue_id:
        issue_id = ensure_issue_id(data, dimension, chapter_number=chapter, repair_surface=surface)
    return build_repair_audit_event(
        event_type=event_type,
        issue_id=issue_id,
        dimension=dimension,
        surface=surface,
        chapter=chapter,
        round_number=round_number,
        issue_type=_first_nonempty(data.get("issue_type"), data.get("repair_kind"), data.get("type")),
        severity=_first_nonempty(data.get("severity"), "high"),
        status=status or _first_nonempty(data.get("status"), "open"),
        repair_action=repair_action,
        failure_kind=failure_kind,
        fallback_action=fallback_action,
        identity_mode="state_target",
        location_mode="state_target",
        extra={
            "candidate_id": resolved_candidate,
            "delta_id": resolved_delta,
            "repair_surface": surface,
            **dict(extra or {}),
        },
    )


def repair_target_audit_payload(
    mission: RepairMission,
    target: RepairTarget,
    *,
    event_type: str,
    round_number: int = 0,
    status: str = "",
    repair_action: str = "",
    plan: RepairPlanCandidate | None = None,
    failure_kind: str = "",
    fallback_action: str = "",
    source_text_hash: str = "",
    target_text_hash: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt a v2 repair target into the canonical audit event payload."""

    synthetic = _synthetic_issue_from_target(target)
    base_extra = {
        "trace_id": mission.trace_id,
        "project_id": mission.project_id,
        "target_id": target.id,
        "issue_ref": target.issue_ref,
        "strategy": plan.strategy.value if plan is not None else "",
        **dict(extra or {}),
    }
    payload_issue = synthetic or target.payload
    issue_id = _first_nonempty(
        synthetic.get("issue_id"),
        synthetic.get("id"),
        target.payload.get("issue_id"),
        target.payload.get("ticket_id"),
        target.issue_ref,
        target.id,
    )

    if target.domain == RepairDomain.FORMAT_RESPONSE:
        return build_repair_audit_event(
            event_type=event_type,
            issue_id=issue_id,
            dimension=target.domain.value,
            surface=target.surface.value,
            round_number=round_number,
            issue_type=_first_nonempty(target.payload.get("issue_type"), target.issue_ref),
            severity=target.severity,
            status=status or target.payload.get("status") or "open",
            artifact_path=_first_nonempty(target.payload.get("task_type"), target.surface.value),
            json_path=_first_nonempty(target.payload.get("json_path"), target.payload.get("path")),
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            repair_action=repair_action,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            identity_mode="protocol_task",
            location_mode="protocol_response",
            extra=base_extra,
        )

    if target.domain == RepairDomain.INIT_ARTIFACT:
        return artifact_issue_audit_payload(
            {
                **target.payload,
                "issue_id": issue_id,
                "issue_type": target.payload.get("issue_type") or target.issue_ref,
                "severity": target.severity,
                "status": status or target.payload.get("status") or "open",
            },
            event_type=event_type,
            dimension=target.domain.value,
            surface=target.surface.value,
            artifact_path=_first_nonempty(
                target.payload.get("artifact_path"),
                target.payload.get("artifact"),
                mission.source_context.get("artifact"),
                target.surface.value,
            ),
            json_path=_first_nonempty(target.payload.get("json_path"), target.payload.get("path")),
            round_number=round_number,
            status=status,
            repair_action=repair_action,
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            extra=base_extra,
        )

    if target.domain == RepairDomain.RUNTIME_CONTRACT:
        return build_repair_audit_event(
            event_type=event_type,
            issue_id=issue_id,
            dimension=target.domain.value,
            surface=target.surface.value,
            chapter=target.chapter_number or _int(target.payload.get("chapter_number"), 0),
            round_number=round_number,
            issue_type=_first_nonempty(target.payload.get("issue_type"), target.issue_ref),
            severity=target.severity,
            status=status or target.payload.get("status") or "open",
            artifact_path=_first_nonempty(
                target.payload.get("artifact_path"),
                target.payload.get("artifact"),
                target.surface.value,
            ),
            json_path=_first_nonempty(target.payload.get("json_path"), target.payload.get("path")),
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            repair_action=repair_action,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            identity_mode="ticket_id",
            location_mode="artifact_json_path",
            extra=base_extra,
        )

    if target.domain == RepairDomain.BOOK_CONSISTENCY:
        return book_issue_audit_payload(
            {
                **target.payload,
                "issue_id": issue_id,
                "issue_type": target.payload.get("issue_type") or target.issue_ref,
                "severity": target.severity,
                "status": status or target.payload.get("status") or "open",
            },
            event_type=event_type,
            surface=target.surface.value,
            chapter_number=target.chapter_number or _int(target.payload.get("chapter_number"), 0),
            queue_target=_first_nonempty(target.payload.get("queue_target"), target.issue_ref),
            round_number=round_number,
            status=status,
            repair_action=repair_action,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            extra=base_extra,
        )

    if target.domain == RepairDomain.STATE_ADJUDICATION:
        return state_issue_audit_payload(
            {
                **target.payload,
                "issue_id": issue_id,
                "issue_type": target.payload.get("issue_type") or target.issue_ref,
                "severity": target.severity,
                "status": status or target.payload.get("status") or "open",
            },
            event_type=event_type,
            surface=target.surface.value,
            chapter=target.chapter_number or _int(target.payload.get("chapter_number"), 0),
            candidate_id=_first_nonempty(
                target.payload.get("candidate_id"),
                target.payload.get("candidate_ids"),
            ),
            delta_id=_first_nonempty(target.payload.get("delta_id"), target.payload.get("state_delta_id")),
            round_number=round_number,
            status=status,
            repair_action=repair_action,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            extra=base_extra,
        )

    return issue_audit_payload(
        {
            **payload_issue,
            "issue_id": issue_id,
            "issue_type": _first_nonempty(
                payload_issue.get("issue_type"),
                payload_issue.get("type"),
                target.issue_ref,
            ),
            "severity": target.severity,
            "summary": _first_nonempty(
                payload_issue.get("summary"),
                payload_issue.get("description"),
                target.summary,
                target.issue_ref,
            ),
            "evidence": _first_nonempty(payload_issue.get("evidence"), target.evidence),
            "paragraph_start": payload_issue.get("paragraph_start")
            or payload_issue.get("paragraph_index"),
            "paragraph_end": payload_issue.get("paragraph_end")
            or payload_issue.get("paragraph_index"),
        },
        event_type=event_type,
        dimension=target.domain.value,
        surface=target.surface.value,
        chapter=target.chapter_number or 0,
        round_number=round_number,
            status=status,
            repair_action=repair_action,
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            identity_mode="issue_id" if target.domain != RepairDomain.GUARDRAIL else "ticket_id",
            location_mode=(
                "domain_batch"
                if target.domain
                in {RepairDomain.KNOWLEDGE_BOUNDARY, RepairDomain.PROMPT_LEAK}
                else
                "whole_text"
                if target.domain == RepairDomain.SHORT_STORY
                else
                "paragraph_window"
                if target.surface in {RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_WINDOW}
                else "whole_text"
            ),
            extra=base_extra,
    )


def build_repair_audit_summary(
    *,
    dimension: str,
    surface: str = "",
    chapter: int = 0,
    rounds_used: int = 0,
    status: str = "",
    selected_issue_ids: list[str] | None = None,
    attempted_issue_ids: list[str] | None = None,
    verified_issue_ids: list[str] | None = None,
    finalized_issue_ids: list[str] | None = None,
    remaining_issue_ids: list[str] | None = None,
    failure_kind: str = "",
    fallback_action: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact end-of-repair replay summary."""

    selected = list(selected_issue_ids or [])
    attempted = list(attempted_issue_ids or [])
    verified = list(verified_issue_ids or [])
    finalized = list(finalized_issue_ids or [])
    remaining = list(remaining_issue_ids or [])
    payload: dict[str, Any] = {
        "schema_version": 1,
        "event_type": "repair_audit_summary",
        "dimension": str(dimension or ""),
        "surface": str(surface or ""),
        "chapter": int(chapter or 0),
        "rounds_used": int(rounds_used or 0),
        "status": str(status or ""),
        "selected_issue_ids": selected,
        "attempted_issue_ids": attempted,
        "verified_issue_ids": verified,
        "finalized_issue_ids": finalized,
        "remaining_issue_ids": remaining,
        "selected_count": len(selected),
        "attempted_count": len(attempted),
        "verified_count": len(verified),
        "finalized_count": len(finalized),
        "remaining_count": len(remaining),
        "failure_kind": str(failure_kind or ""),
        "fallback_action": str(fallback_action or ""),
    }
    if extra:
        for key, value in extra.items():
            if key not in payload:
                payload[key] = value
    return payload


def emit_repair_audit_event(on_step: StepCallback, payload: Mapping[str, Any]) -> None:
    """Emit one canonical repair audit event through the existing step logger."""

    if on_step is not None:
        on_step("repair_audit_event", dict(payload))


def emit_repair_audit_summary(on_step: StepCallback, payload: Mapping[str, Any]) -> None:
    """Emit one end-of-repair audit summary through the existing step logger."""

    if on_step is not None:
        on_step("repair_audit_summary", dict(payload))
