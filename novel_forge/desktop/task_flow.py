"""Shared task-flow semantics for every Desktop task surface.

The job manager owns execution and persistence.  This module owns the small,
stable projection that UI surfaces need in common: scope membership, chapter
identity, terminal outcome, and user-facing status.  Workflow (``机杼``),
Chapter Studio (``章台``), and Task Focus intentionally keep different
layouts, but must not reinterpret the same job independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from novel_forge.desktop.constants import JOB_STATUS_TEXT, JOB_STATUS_TONE


class TaskFlowScope(StrEnum):
    """Supported projections of the shared Desktop job collection."""

    GLOBAL = "global"
    WORKFLOW = "workflow"
    CHAPTER = "chapter"


class TaskFlowOutcome(StrEnum):
    """Semantic job outcome independent of the persistence enum.

    Cancellation is persisted as ``failed + current_step=cancelled`` for
    backwards compatibility.  Keeping it as a first-class outcome here stops
    presentation code from calling a user cancellation an error.
    """

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskFlowStatusSpec:
    """One canonical status projection shared by cards and task focus."""

    outcome: TaskFlowOutcome
    label: str
    tone: str
    focus_reason: str
    is_terminal: bool
    is_error: bool


AUDIO_TASK_KINDS: frozenset[str] = frozenset(
    {
        "tts_synthesize",
        "tts_full_pipeline",
        "tts_post_archive",
    }
)


# These catalogs describe where a task is presented, not how it executes.
# ``DesktopJobManager`` re-exports the workflow set for backwards
# compatibility, while Chapter Studio and Task Focus consume the same source.
WORKFLOW_TASK_KINDS: frozenset[str] = frozenset(
    {
        "init_long",
        "run_short",
        "book_consistency",
        "export_book",
        *AUDIO_TASK_KINDS,
    }
)

CHAPTER_TASK_KINDS: frozenset[str] = frozenset(
    {
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "run_chapter",
        "reevaluate_chapter",
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "polish_chapter",
        "reextract_relationships",
        "repair_motif_history",
    }
)

BOOK_TASK_KINDS: frozenset[str] = frozenset(
    {
        "book_consistency",
        "export_book",
    }
)

CHAPTER_EXECUTION_TASK_KINDS: frozenset[str] = frozenset(
    {
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "run_chapter",
    }
)

CHAPTER_TASK_FLOW_KINDS: frozenset[str] = CHAPTER_TASK_KINDS | AUDIO_TASK_KINDS

_ATTENTION_OUTCOMES: frozenset[TaskFlowOutcome] = frozenset(
    {
        TaskFlowOutcome.QUEUED,
        TaskFlowOutcome.RUNNING,
        TaskFlowOutcome.PAUSED,
        TaskFlowOutcome.FAILED,
    }
)


def task_flow_status_value(job: Any) -> str:
    """Return a normalized persisted status string for a job-like object."""

    status = getattr(job, "status", "")
    return str(getattr(status, "value", status) or "").strip().lower()


def task_flow_is_cancelled(job: Any) -> bool:
    """Whether a persisted failed job actually represents cancellation."""

    return (
        task_flow_status_value(job) == TaskFlowOutcome.FAILED.value
        and str(getattr(job, "current_step", "") or "").strip().lower() == "cancelled"
    )


def task_flow_outcome(job: Any) -> TaskFlowOutcome:
    """Project the persistence status into a semantic task-flow outcome."""

    if task_flow_is_cancelled(job):
        return TaskFlowOutcome.CANCELLED
    raw = task_flow_status_value(job)
    try:
        return TaskFlowOutcome(raw)
    except ValueError:
        return TaskFlowOutcome.SUCCEEDED


def task_flow_needs_attention(job: Any) -> bool:
    """Whether a job belongs in an actionable task-flow view."""

    return task_flow_outcome(job) in _ATTENTION_OUTCOMES


def task_flow_is_clearable(job: Any, *, include_paused: bool = True) -> bool:
    """Whether history cleanup may remove the job without stopping execution."""

    outcome = task_flow_outcome(job)
    if outcome in {
        TaskFlowOutcome.SUCCEEDED,
        TaskFlowOutcome.FAILED,
        TaskFlowOutcome.CANCELLED,
    }:
        return True
    return include_paused and outcome == TaskFlowOutcome.PAUSED


def task_flow_job_chapter_number(job: Any) -> int | None:
    """Best-effort chapter identity shared by cards, scopes, and archives."""

    result = getattr(job, "result", {})
    if isinstance(result, dict):
        chapter = _positive_int(result.get("chapter_number"))
        if chapter is not None:
            return chapter
    for event in reversed(list(getattr(job, "events", []) or [])[-8:]):
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            continue
        chapter = _positive_int(payload.get("chapter_number", payload.get("chapter")))
        if chapter is not None:
            return chapter

    match = re.search(r"第\s*(\d+)\s*章", str(getattr(job, "label", "") or ""))
    return _positive_int(match.group(1)) if match is not None else None


def task_flow_matches_scope(
    job: Any,
    scope: TaskFlowScope | str,
    *,
    project_id: str = "",
    chapter_number: int = 0,
) -> bool:
    """Return whether *job* belongs to one of the shared UI scopes.

    Chapter scope follows the same policy as Chapter Studio: current-chapter
    history plus actionable jobs from another chapter in the same project, so
    auto-run remains visible when the editor is not following the active
    chapter.  Book-level work stays visible for the selected project.
    """

    resolved_scope = TaskFlowScope(scope)
    if resolved_scope == TaskFlowScope.GLOBAL:
        return True
    kind = str(getattr(job, "kind", "") or "").strip()
    if resolved_scope == TaskFlowScope.WORKFLOW:
        return kind in WORKFLOW_TASK_KINDS
    if not project_id or str(getattr(job, "project_id", "") or "").strip() != project_id:
        return False
    if kind in BOOK_TASK_KINDS:
        return True
    if kind not in CHAPTER_TASK_FLOW_KINDS:
        return False
    if chapter_number <= 0:
        return True
    job_chapter = task_flow_job_chapter_number(job)
    return job_chapter == chapter_number or task_flow_needs_attention(job)


def task_flow_status_spec(job: Any, *, has_pending_decision: bool = False) -> TaskFlowStatusSpec:
    """Return the canonical status copy and tone for a job-like object."""

    if has_pending_decision:
        return TaskFlowStatusSpec(
            outcome=task_flow_outcome(job),
            label="待确认",
            tone="warning",
            focus_reason="需要确认",
            is_terminal=False,
            is_error=False,
        )

    outcome = task_flow_outcome(job)
    if outcome == TaskFlowOutcome.CANCELLED:
        return TaskFlowStatusSpec(
            outcome=outcome,
            label="已取消",
            tone="muted",
            focus_reason="最近取消",
            is_terminal=True,
            is_error=False,
        )

    status_key = outcome.value
    focus_reason = {
        TaskFlowOutcome.QUEUED: "正在排队",
        TaskFlowOutcome.RUNNING: "正在执行",
        TaskFlowOutcome.PAUSED: "等待继续",
        TaskFlowOutcome.SUCCEEDED: "最近完成",
        TaskFlowOutcome.FAILED: "最近失败",
    }.get(outcome, "当前关注")
    return TaskFlowStatusSpec(
        outcome=outcome,
        label=JOB_STATUS_TEXT.get(status_key, status_key),
        tone=JOB_STATUS_TONE.get(status_key, "default"),
        focus_reason=focus_reason,
        is_terminal=outcome
        in {
            TaskFlowOutcome.SUCCEEDED,
            TaskFlowOutcome.FAILED,
        },
        is_error=outcome == TaskFlowOutcome.FAILED,
    )


def task_flow_failure_text(job: Any) -> tuple[str, str]:
    """Return title/detail for a real failure; cancellation deliberately returns empty."""

    status = task_flow_status_spec(job)
    if not status.is_error:
        return "", ""
    summary = getattr(job, "error_summary", {})
    if isinstance(summary, dict) and summary:
        title = str(summary.get("title") or "错误").strip()
        message = str(summary.get("summary") or getattr(job, "error", "") or "").strip()
        detail = str(summary.get("detail") or "").strip()
        return f"{title}：{message}" if message else title, detail
    error = str(getattr(job, "error", "") or "").strip()
    return (f"错误：{error}" if error else ""), ""


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value) if value is not None and str(value).strip() else 0
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "AUDIO_TASK_KINDS",
    "BOOK_TASK_KINDS",
    "CHAPTER_EXECUTION_TASK_KINDS",
    "CHAPTER_TASK_FLOW_KINDS",
    "CHAPTER_TASK_KINDS",
    "TaskFlowOutcome",
    "TaskFlowScope",
    "TaskFlowStatusSpec",
    "WORKFLOW_TASK_KINDS",
    "task_flow_failure_text",
    "task_flow_is_cancelled",
    "task_flow_is_clearable",
    "task_flow_job_chapter_number",
    "task_flow_matches_scope",
    "task_flow_needs_attention",
    "task_flow_outcome",
    "task_flow_status_spec",
    "task_flow_status_value",
]
