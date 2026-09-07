"""Backward-compatible job helper imports for the desktop window shell.

The implementation lives in :mod:`novel_forge.desktop.window._jobs`.
"""

from __future__ import annotations

from novel_forge.desktop.window._jobs import (
    active_write_job_for_project,
    bind_jobs,
    bind_jobs_for_page,
    handle_task_decision_required,
    job_chapter_number,
    latest_chapter_job,
    provide_task_decision,
    schedule_bind_jobs,
    update_status_bar_labels,
)

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
