"""One completion boundary for direct, checkpoint and autorun chapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from novel_forge.core.infra.resource_locks import ResourceName
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    matching_finalization_phase,
    record_finalization_pending,
    record_finalization_success,
)


async def complete_chapter_archive(
    runtime: Any,
    *,
    project_id: str,
    chapter_number: int,
    on_step_progress: Any = None,
    defer_post_archive_tts: bool = False,
) -> None:
    from novel_forge.workspace.helpers.execution_runners import (
        _ensure_required_finalization_phases,
        _project_finalized_chapter,
        _project_lock,
    )

    # Never dispatch future work just because a file exists or a model returned.
    async with _project_lock(runtime, project_id, ResourceName.CANON):
        _ensure_required_finalization_phases(
            runtime, project_id=project_id, chapter_number=chapter_number
        )
        layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
        text_hash = source_text_hash(runtime.storage.load_text(layout.chapter_path(chapter_number)))
        manifest = ArtifactManifest(runtime.storage, layout)
        if (
            matching_finalization_phase(
                manifest,
                chapter_number=chapter_number,
                phase="archive_committed",
                text_hash=text_hash,
            )
            is None
        ):
            record_finalization_success(
                manifest,
                chapter_number=chapter_number,
                phase="archive_committed",
                text_hash=text_hash,
            )
        _project_finalized_chapter(
            runtime,
            project_id=project_id,
            chapter_number=chapter_number,
            on_step_progress=on_step_progress,
        )

    async def followup(phase: str, operation: Callable[[], Awaitable[Any]]) -> None:
        if (
            matching_finalization_phase(
                manifest, chapter_number=chapter_number, phase=phase, text_hash=text_hash
            )
            is not None
        ):
            return
        try:
            result = await operation()
            if isinstance(result, dict) and result.get("status") in {
                "queued",
                "running",
                "candidate",
                "paused",
            }:
                # An independent task owns completion. Queueing is not publishing.
                if (
                    matching_finalization_phase(
                        ArtifactManifest(runtime.storage, layout),
                        chapter_number=chapter_number,
                        phase=phase,
                        text_hash=text_hash,
                    )
                    is None
                ):
                    record_finalization_pending(
                        manifest,
                        chapter_number=chapter_number,
                        phase=phase,
                        text_hash=text_hash,
                        error="规划任务待完成",
                        metadata={
                            "job_id": result.get("job_id", ""),
                            "planning_status": result["status"],
                        },
                    )
                return
            if isinstance(result, dict) and (
                result.get("status") in {"failed", "invalid_policy", "budget_skipped"}
                or (result.get("status") == "kept_original" and result.get("reason"))
            ):
                raise RuntimeError(str(result.get("reason") or result.get("status")))
        except Exception as exc:
            record_finalization_pending(
                manifest, chapter_number=chapter_number, phase=phase, text_hash=text_hash, error=exc
            )
            if on_step_progress is not None:
                on_step_progress(
                    "chapter_followup_pending",
                    {
                        "chapter": chapter_number,
                        "text_hash": text_hash,
                        "phase": phase,
                        "message": "章节已归档，规划待重试"
                        if phase != "post_archive_tts"
                        else "章节已归档，音频待重试",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "action": "retry_followup_only",
                    },
                )
            return
        record_finalization_success(
            manifest, chapter_number=chapter_number, phase=phase, text_hash=text_hash
        )

    from novel_forge.workspace.execution_future_planning import execute_future_planning
    from novel_forge.workspace.planning_jobs import request_planning_horizon

    await followup(
        "planning_horizon",
        lambda: request_planning_horizon(
            runtime,
            project_id=project_id,
            current_chapter=chapter_number,
            on_step_progress=on_step_progress,
        ),
    )
    await followup(
        "future_planning",
        lambda: execute_future_planning(
            runtime,
            project_id=project_id,
            completed_chapter=chapter_number,
            on_step_progress=on_step_progress,
        ),
    )
    if not defer_post_archive_tts:
        from novel_forge.workspace.post_archive_tts import run_post_archive_tts

        await followup(
            "post_archive_tts",
            lambda: run_post_archive_tts(
                runtime,
                project_id=project_id,
                chapter_number=chapter_number,
                on_step_progress=on_step_progress,
            ),
        )
