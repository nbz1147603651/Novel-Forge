"""Job binding helpers for the desktop main window shell."""

from __future__ import annotations

from typing import Any

import shiboken6
from PySide6.QtCore import QObject

from novel_forge.desktop.constants import LABEL_CHAPTER_RE
from novel_forge.desktop.jobs import CHAPTER_WRITE_KINDS, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.progress import compute_task_flow_progress, display_step_name_for_job


def schedule_bind_jobs(owner: Any) -> None:
    """Debounce frequent step updates to reduce UI re-render churn."""
    if not owner._jobs_bind_timer.isActive():
        owner._jobs_bind_timer.start()


def handle_task_decision_required(owner: Any, job_id: str, payload: object) -> None:
    """Surface a job decision request in task observation UI."""
    owner._task_observation_store.ingest_decision_required(job_id, payload)
    owner.show_priority_status("任务请求人工确认", 4500, owner._STATUS_WARNING)
    companion = owner._floating_task_companion
    if companion is not None:
        companion.raise_()


def provide_task_decision(
    owner: Any,
    job_id: str,
    decision_id: str,
    choice: str,
    custom_text: str = "",
    approval_version: str = "",
) -> None:
    """Submit a human task decision back to the job manager."""
    ok = owner._job_manager.provide_decision(
        job_id,
        decision_id,
        choice,
        custom_text=custom_text,
        approval_version=approval_version,
    )
    if ok:
        owner._task_observation_store.mark_decision_submitted(job_id, decision_id, choice)
        owner.show_priority_status("确认已提交，任务继续执行", 3500, owner._STATUS_SUCCESS)
    else:
        owner.show_priority_status("确认未送达，任务可能已结束或超时", 5000, owner._STATUS_WARNING)


def bind_jobs_for_page(owner: Any, page_id: str, jobs: list[DesktopJobRecord]) -> None:
    """Bind job records into a page if the page supports job updates."""
    page = owner._pages.get(page_id)
    if page is None:
        return
    if isinstance(page, QObject) and not shiboken6.isValid(page):
        return
    try:
        if hasattr(page, "bind_engine_job_service"):
            page.bind_engine_job_service(owner._job_manager.engine_job_service)
        if hasattr(page, "bind_task_observation_store"):
            page.bind_task_observation_store(owner._task_observation_store)
        if hasattr(page, "supports_job_binding") and page.supports_job_binding():
            page.on_jobs_changed(jobs)
    except RuntimeError as exc:
        if "already deleted" in str(exc) or "Signal source has been deleted" in str(exc):
            return
        raise


def bind_jobs(owner: Any) -> None:
    """Refresh latest jobs and propagate them to the active page and status bar."""
    if not owner._window_callbacks_allowed():
        return
    jobs = owner._job_manager.jobs()
    owner._latest_jobs = jobs
    owner._task_observation_store.ingest_jobs(jobs)
    current_page_id = owner._current_page_id()
    owner._bind_jobs_for_page(current_page_id, jobs)

    chapter_studio = owner._pages.get("chapter_studio")
    if (
        current_page_id != "chapter_studio"
        and chapter_studio is not None
        and chapter_studio.needs_job_binding()
    ):
        chapter_studio.bind_jobs(jobs)
    owner._drive_active_autoruns()
    owner._update_status_bar_labels(jobs)


def update_status_bar_labels(owner: Any, jobs: list[DesktopJobRecord]) -> None:
    """Update step/progress/cost labels from the earliest running job."""
    running_jobs = [job for job in jobs if job.status == DesktopJobState.RUNNING]
    if not running_jobs:
        owner._update_step_name("")
        owner._update_cost_label("", 0, 0.0)
        owner._last_step_text = ""
        return
    earliest = min(running_jobs, key=lambda job: str(job.created_at or ""))
    step_text = display_step_name_for_job(earliest)
    progress_pct = compute_task_flow_progress(earliest)
    owner._update_step_name(f"{step_text} · {progress_pct}%")
    if earliest.cumulative_tokens > 0:
        owner._update_cost_label(
            earliest.project_id,
            earliest.cumulative_tokens,
            earliest.cumulative_cost_usd,
        )
    else:
        owner._update_cost_label("", 0, 0.0)


def job_chapter_number(job: DesktopJobRecord) -> int | None:
    """Return a job's chapter number from result metadata or label text."""
    raw = (job.result or {}).get("chapter_number")
    try:
        if raw is not None and str(raw).strip():
            return int(raw)
    except (TypeError, ValueError):
        pass
    match = LABEL_CHAPTER_RE.search(job.label or "")
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def active_write_job_for_project(
    *,
    jobs: list[DesktopJobRecord],
    project_id: str,
) -> DesktopJobRecord | None:
    """Find the active chapter-writing job for a project."""
    for job in jobs:
        if (
            job.project_id == project_id
            and job.kind in CHAPTER_WRITE_KINDS
            and job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        ):
            return job
    return None


def latest_chapter_job(
    *,
    jobs: list[DesktopJobRecord],
    project_id: str,
    chapter_number: int,
) -> DesktopJobRecord | None:
    """Find the latest chapter-writing job matching a project/chapter pair."""
    for job in jobs:
        if job.project_id != project_id:
            continue
        if job.kind not in CHAPTER_WRITE_KINDS:
            continue
        job_chapter = job_chapter_number(job)
        if job_chapter in {None, 0, chapter_number}:
            return job
    return None


__all__ = (
    "active_write_job_for_project",
    "bind_jobs",
    "bind_jobs_for_page",
    "handle_task_decision_required",
    "job_chapter_number",
    "latest_chapter_job",
    "provide_task_decision",
    "schedule_bind_jobs",
    "update_status_bar_labels",
)
