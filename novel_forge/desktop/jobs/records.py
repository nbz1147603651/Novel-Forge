"""Sub-module of novel_forge.desktop.jobs.

Auto-generated in the M3.5 split. Contains records.py symbols.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from novel_forge.app_service.contracts import (
    JobCommand,
)
from novel_forge.app_service.contracts import (
    JobRecord as AppJobRecord,
)
from novel_forge.app_service.contracts import (
    JobState as AppJobState,
)
from novel_forge.app_service.contracts import (
    JobStepEvent as AppJobStepEvent,
)
from novel_forge.app_service.error_diagnostics import prompt_task_from_error_payload
from novel_forge.core.infra.event_bus import (
    EventBus,
)
from novel_forge.desktop.constants import LABEL_CHAPTER_RE as _LABEL_CHAPTER_RE
from novel_forge.pipeline.progress import compact_progress_event_history

logger = logging.getLogger(__name__)

_event_bus: EventBus | None = None


def _job_chapter_num(record: "DesktopJobRecord") -> int | None:
    """Best-effort chapter number extraction from a job record."""
    raw = (record.result or {}).get("chapter_number")
    try:
        if raw is not None and str(raw).strip():
            return int(raw)
    except (TypeError, ValueError):
        pass
    m = _LABEL_CHAPTER_RE.search(record.label or "")
    if m is None:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _infer_failed_step(record: "DesktopJobRecord" | None, payload: dict[str, Any]) -> str:
    """Best-effort failed step for UI progress when worker errors asynchronously."""
    if record is None:
        return "failed"

    current_step = str(record.current_step or "").strip()
    if current_step in {"", "failed"} and record.events:
        current_step = str(record.events[-1].step or "").strip()

    raw_category = payload.get("category", "")
    category = str(getattr(raw_category, "value", raw_category) or "").lower()
    title = str(payload.get("title", "") or "")
    summary = str(payload.get("summary", "") or "")
    detail = str(payload.get("detail", "") or "")
    raw_chain = payload.get("chain")
    chain = raw_chain if isinstance(raw_chain, list) else []
    haystack = "\n".join([title, summary, detail, *(str(item) for item in chain)]).lower()

    prompt_task = prompt_task_from_error_payload(payload)
    if prompt_task:
        return prompt_task

    if category == "model" and ("json" in haystack or "json" in title.lower()):
        for event_index in range(len(record.events) - 1, -1, -1):
            event = record.events[event_index]
            if event.step != "extract_canon":
                continue
            later_steps = {item.step for item in record.events[event_index + 1 :]}
            if not later_steps.intersection(
                {"extract_canon_normalized", "creative_report", "persist"}
            ):
                return "extract_canon"
            break
        for marker in (
            "causal_validation",
            "evaluate",
            "plan",
            "bridge",
            "draft",
        ):
            if marker in haystack:
                return marker

    if "draftstep" in haystack or "draft_chapter" in haystack:
        return "draft"
    if "extractcanon" in haystack or "extract_canon" in haystack:
        return "extract_canon"
    return current_step or "failed"


def _job_kind_to_section(kind: str) -> str | None:
    """Return the workspace section name for *kind*, or ``None`` if irrelevant."""
    return _JOB_KIND_TO_SECTION.get(kind)


# Module-local constant used by payload (de)serialization below.
_MAX_JOB_EVENTS: int = 160

# Mapping from job kind to the workspace section that should be refreshed
# when the job completes. Used by ``DesktopJobManager._publish_section_change``
# to emit granular ``SECTION_CHANGED`` events so only affected pages rebind.
_JOB_KIND_TO_SECTION: dict[str, str] = {
    # Chapter-affecting jobs → "chapters" section
    "run_chapter": "chapters",
    "prepare_chapter": "chapters",
    "polish_chapter": "chapters",
    "repair_continuity": "chapters",
    "repair_causal": "chapters",
    "repair_issues": "chapters",
    "reevaluate_chapter": "chapters",
    "resolve_chapter_checkpoint": "chapters",
    "resolve_chapter_checkpoint_finalize": "chapters",
    "reextract_relationships": "chapters",
    # Project structure-affecting jobs → "projects" section
    "init_long": "projects",
    "run_short": "projects",
    # Detail/report-affecting jobs → "details" section
    "book_consistency": "details",
    "rebuild_memory_vectors": "details",
    "sync_chapter_contracts": "details",
    "tts_synthesize": "details",
    "tts_full_pipeline": "details",
    "tts_post_archive": "details",
    "extend_outline": "projects",
    # export_book, repair_motif_history → no event (irrelevant to snapshot)
}


class DesktopJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class DesktopJobEvent:
    at: str
    step: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DesktopJobRecord:
    job_id: str
    kind: str
    label: str
    project_id: str = ""
    status: DesktopJobState = DesktopJobState.QUEUED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_step: str = ""
    current_step_payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_summary: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    events: list[DesktopJobEvent] = field(default_factory=list)
    resolved_error_entry_ids: set[str] = field(default_factory=set)
    cumulative_tokens: int = 0
    cumulative_cost_usd: float = 0.0


@dataclass
class WaitingJob:
    """A job that is queued waiting for dependencies to be satisfied."""

    record: DesktopJobRecord
    app_command: JobCommand


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_event_to_payload(event: DesktopJobEvent) -> dict[str, Any]:
    return {
        "at": str(event.at or ""),
        "step": str(event.step or ""),
        "payload": event.payload if isinstance(event.payload, dict) else {},
    }


def _job_to_payload(record: DesktopJobRecord) -> dict[str, Any]:
    resolved_error_entry_ids = getattr(record, "resolved_error_entry_ids", set())
    return {
        "job_id": record.job_id,
        "kind": record.kind,
        "label": record.label,
        "project_id": record.project_id,
        "status": record.status.value
        if isinstance(record.status, DesktopJobState)
        else str(record.status),
        "created_at": str(record.created_at or ""),
        "updated_at": str(record.updated_at or ""),
        "current_step": str(record.current_step or ""),
        "current_step_payload": (
            record.current_step_payload if isinstance(record.current_step_payload, dict) else {}
        ),
        "error": str(record.error or ""),
        "error_summary": record.error_summary if isinstance(record.error_summary, dict) else {},
        "result": record.result if isinstance(record.result, dict) else {},
        "events": [
            _job_event_to_payload(event)
            for event in compact_progress_event_history(record.events, limit=_MAX_JOB_EVENTS)
            if isinstance(record.events, list)
            if isinstance(event, DesktopJobEvent)
        ],
        "resolved_error_entry_ids": sorted(
            str(entry_id) for entry_id in resolved_error_entry_ids if str(entry_id or "").strip()
        )
        if isinstance(resolved_error_entry_ids, (set, list, tuple))
        else [],
        "cumulative_tokens": record.cumulative_tokens,
        "cumulative_cost_usd": record.cumulative_cost_usd,
    }


def _job_from_payload(data: dict[str, Any]) -> DesktopJobRecord | None:
    try:
        status_raw = str(data.get("status", "") or "").strip().lower()
        status = (
            DesktopJobState(status_raw)
            if status_raw in DesktopJobState._value2member_map_
            else DesktopJobState.SUCCEEDED
        )
        events_raw = data.get("events", [])
        events: list[DesktopJobEvent] = []
        if isinstance(events_raw, list):
            for event in events_raw:
                if not isinstance(event, dict):
                    continue
                events.append(
                    DesktopJobEvent(
                        at=str(event.get("at", "") or ""),
                        step=str(event.get("step", "") or ""),
                        payload=event.get("payload", {})
                        if isinstance(event.get("payload"), dict)
                        else {},
                    )
                )
            events = compact_progress_event_history(events, limit=_MAX_JOB_EVENTS)
        resolved_raw = data.get("resolved_error_entry_ids", [])
        resolved_error_entry_ids: set[str] = set()
        if isinstance(resolved_raw, (list, tuple, set)):
            resolved_error_entry_ids = {
                str(item).strip() for item in resolved_raw if str(item or "").strip()
            }
        return DesktopJobRecord(
            job_id=str(data.get("job_id", "") or ""),
            kind=str(data.get("kind", "") or ""),
            label=str(data.get("label", "") or ""),
            project_id=str(data.get("project_id", "") or ""),
            status=status,
            created_at=str(data.get("created_at", "") or _now_iso()),
            updated_at=str(data.get("updated_at", "") or _now_iso()),
            current_step=str(data.get("current_step", "") or ""),
            current_step_payload=(
                data.get("current_step_payload", {})
                if isinstance(data.get("current_step_payload"), dict)
                else {}
            ),
            error=str(data.get("error", "") or ""),
            error_summary=data.get("error_summary", {})
            if isinstance(data.get("error_summary"), dict)
            else {},
            result=data.get("result", {}) if isinstance(data.get("result"), dict) else {},
            events=events,
            resolved_error_entry_ids=resolved_error_entry_ids,
            cumulative_tokens=int(data.get("cumulative_tokens", 0) or 0),
            cumulative_cost_usd=float(data.get("cumulative_cost_usd", 0.0) or 0.0),
        )
    except Exception:
        return None


def desktop_job_record_to_app_record(record: DesktopJobRecord) -> AppJobRecord:
    """Project a Qt desktop job record into the shared app-service contract."""

    status_value = (
        record.status.value if isinstance(record.status, DesktopJobState) else str(record.status)
    )
    status = (
        AppJobState(status_value)
        if status_value in AppJobState._value2member_map_
        else AppJobState.SUCCEEDED
    )
    return AppJobRecord(
        job_id=record.job_id,
        kind=record.kind,
        label=record.label,
        project_id=record.project_id,
        status=status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        current_step=record.current_step,
        current_step_payload=(
            record.current_step_payload if isinstance(record.current_step_payload, dict) else {}
        ),
        error=record.error,
        error_summary=record.error_summary if isinstance(record.error_summary, dict) else {},
        result=record.result if isinstance(record.result, dict) else {},
        events=[
            AppJobStepEvent(
                at=event.at,
                step=event.step,
                payload=event.payload if isinstance(event.payload, dict) else {},
            )
            for event in compact_progress_event_history(record.events, limit=_MAX_JOB_EVENTS)
        ],
        resolved_error_entry_ids=set(record.resolved_error_entry_ids),
        cumulative_tokens=record.cumulative_tokens,
        cumulative_cost_usd=record.cumulative_cost_usd,
    )


def app_job_record_to_desktop_record(record: AppJobRecord) -> DesktopJobRecord:
    """Project an app-service job record back into the legacy desktop dataclass."""

    status_value = (
        record.status.value if isinstance(record.status, AppJobState) else str(record.status)
    )
    status = (
        DesktopJobState(status_value)
        if status_value in DesktopJobState._value2member_map_
        else DesktopJobState.SUCCEEDED
    )
    kind = record.kind.value if hasattr(record.kind, "value") else str(record.kind)
    return DesktopJobRecord(
        job_id=record.job_id,
        kind=kind,
        label=record.label,
        project_id=record.project_id,
        status=status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        current_step=record.current_step,
        current_step_payload=dict(record.current_step_payload),
        error=record.error,
        error_summary=dict(record.error_summary),
        result=dict(record.result),
        events=[
            DesktopJobEvent(
                at=event.at,
                step=event.step,
                payload=event.payload if isinstance(event.payload, dict) else {},
            )
            for event in compact_progress_event_history(record.events, limit=_MAX_JOB_EVENTS)
        ],
        resolved_error_entry_ids=set(record.resolved_error_entry_ids),
        cumulative_tokens=record.cumulative_tokens,
        cumulative_cost_usd=record.cumulative_cost_usd,
    )
