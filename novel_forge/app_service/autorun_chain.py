"""Engine-level autorun chaining after a successful long initialization.

The ``创建并连跑`` flow previously relied on the PySide6 window dispatcher to
watch ``init_long`` completion and start chapter autopilot.  API/nimo
sessions had no equivalent, so the workflow silently stopped right after
initialization.  This module closes that gap at the engine layer: when an
``init_long`` job submitted with ``autorun_after_init`` succeeds, JobService
chains a ``prepare_chapter`` command for the first unwritten chapter.  The
chained job pauses at the plan checkpoint exactly like a manual prepare, so
the human-in-the-loop plan confirmation is preserved.

Author: Novel Forge Team
"""

from __future__ import annotations

import logging
from pathlib import Path

from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord

logger = logging.getLogger(__name__)


def next_autorun_chapter_number(storage_root: Path | None, project_id: str) -> int:
    """Resolve the first unwritten chapter for the chained prepare job.

    Falls back to chapter 1 when project state cannot be inspected; the
    prepare flow itself re-validates the chapter against the outline.
    """
    if storage_root is None or not project_id:
        return 1
    try:
        from novel_forge.persistence.filesystem import FileSystemStorage
        from novel_forge.workspace.projects import ProjectInspector

        inspector = ProjectInspector(FileSystemStorage(storage_root))
        detail = inspector.get_project_detail(project_id)
        return max(1, int(detail.completed_chapters or 0) + 1)
    except Exception:
        logger.debug(
            "Autorun chapter inference failed for %s; defaulting to chapter 1",
            project_id,
            exc_info=True,
        )
        return 1


def build_autorun_followup_command(
    record: JobRecord, storage_root: Path | None
) -> JobCommand | None:
    """Build the chained prepare command for a succeeded autorun init job.

    Returns ``None`` when the record is not a long initialization or lacks a
    project id, so callers can skip chaining without extra guards.
    """
    kind_value = record.kind.value if hasattr(record.kind, "value") else str(record.kind)
    project_id = str(record.project_id or "").strip()
    if kind_value != JobKind.INIT_LONG.value or not project_id:
        return None
    chapter_number = next_autorun_chapter_number(storage_root, project_id)
    return JobCommand(
        kind=JobKind.PREPARE_CHAPTER,
        project_id=project_id,
        payload={
            "project_id": project_id,
            "chapter_number": chapter_number,
            "force": False,
            "notes": "",
            "rewrite_strategy": "auto",
            "writing_mode": "whole_chapter",
        },
        metadata={
            "workflow_type": "long_chapter",
            "run_mode": "autorun",
            "autorun_chained_from": record.job_id,
        },
    )
