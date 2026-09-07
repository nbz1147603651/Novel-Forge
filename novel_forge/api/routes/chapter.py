"""Routes for long-mode chapter generation."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from novel_forge.api.deps import get_runtime_services, get_storage
from novel_forge.api.run_logging import execute_api_with_run_logger, stream_api_with_run_logger
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.repair_orchestration.mission_factory import (
    causal_repair_mission,
    continuity_repair_mission,
    issues_repair_mission,
)
from novel_forge.workspace.contracts import (
    ChapterPublicationView,
    ChapterSessionResult,
    ChapterWorkspaceSnapshot,
    InitLongRequest,
    InitLongResponse,
    ManualRevisionRequest,
    PrepareChapterRequest,
    PrepareChapterResponse,
    ReevaluateChapterRequest,
    RepairCausalRequest,
    RepairChapterResponse,
    RepairContinuityRequest,
    RepairIssuesRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunChapterResponse,
)
from novel_forge.workspace.execution import (
    execute_init_long,
    execute_manual_revision,
    execute_prepare_chapter,
    execute_reevaluate_chapter,
    execute_repair,
    execute_resolve_chapter_checkpoint,
    execute_run_chapter,
)
from novel_forge.workspace.projects import ProjectInspector
from novel_forge.workspace.publication import build_chapter_publication_view
from novel_forge.workspace.result_payloads import (
    build_init_long_result_payload,
    build_repair_chapter_result_payload,
    build_run_chapter_result_payload,
)
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]
StorageDep = Annotated[FileSystemStorage, Depends(get_storage)]
_log = logging.getLogger(__name__)


async def _background_reevaluate_manual_revision(
    runtime: RuntimeServices,
    *,
    project_id: str,
    chapter_number: int,
) -> None:
    request = ReevaluateChapterRequest(project_id=project_id, chapter_number=chapter_number)
    try:
        await execute_api_with_run_logger(
            runtime,
            project_id=project_id,
            command="api:chapter.manual_revision.reevaluate",
            metadata=request.model_dump(mode="json"),
            chapter_number=chapter_number,
            execute=lambda on_step: execute_reevaluate_chapter(
                runtime,
                request,
                on_step_progress=on_step,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - background task must not affect the response
        _log.warning(
            "manual_revision_background_reevaluate_failed | project=%s | chapter=%s | error=%s",
            project_id,
            chapter_number,
            exc,
            exc_info=True,
        )


@router.post("/init", response_model=InitLongResponse)
async def init_long_project(
    req: InitLongRequest,
    runtime: RuntimeDep,
) -> InitLongResponse:
    """Initialize a long-mode project."""
    project_id = req.project_id.strip() or runtime.create_project_id("long")
    effective_req = req.model_copy(update={"project_id": project_id})
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=project_id,
        command="api:chapter.init",
        metadata=effective_req.model_dump(mode="json"),
        execute=lambda on_step: execute_init_long(
            runtime,
            effective_req,
            on_step_progress=on_step,
        ),
    )
    return InitLongResponse.model_validate(
        build_init_long_result_payload(
            execution.project_id,
            execution.result,
        )
    )


@router.post("/run", response_model=RunChapterResponse)
async def run_chapter(
    req: RunChapterRequest,
    runtime: RuntimeDep,
) -> RunChapterResponse:
    """Generate a single chapter."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.run",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_run_chapter(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return RunChapterResponse.model_validate(
        build_run_chapter_result_payload(
            execution.project_id,
            execution.result,
            chapter_number=req.chapter_number,
        )
    )


@router.post("/run/stream")
async def run_chapter_stream(
    req: RunChapterRequest,
    runtime: RuntimeDep,
) -> StreamingResponse:
    """Generate a single chapter and stream progress as SSE."""
    events = stream_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.run",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_run_chapter(
            runtime,
            req,
            on_step_progress=on_step,
        ),
        build_result=lambda execution: RunChapterResponse.model_validate(
            build_run_chapter_result_payload(
                execution.project_id,
                execution.result,
                chapter_number=req.chapter_number,
            )
        ).model_dump(mode="json"),
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{project_id}/{chapter_number}/workspace", response_model=ChapterWorkspaceSnapshot)
async def get_chapter_workspace(
    project_id: str,
    chapter_number: int,
    storage: StorageDep,
) -> ChapterWorkspaceSnapshot:
    """Return chapter-studio snapshot data for one chapter."""
    inspector = ProjectInspector(storage)
    try:
        return inspector.get_chapter_workspace_snapshot(project_id, chapter_number)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{project_id}/{chapter_number}/publication",
    response_model=ChapterPublicationView,
)
async def get_chapter_publication(
    project_id: str,
    chapter_number: int,
    storage: StorageDep,
) -> ChapterPublicationView:
    """Return the stable final-chapter contract consumed by voice and film."""

    try:
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        return build_chapter_publication_view(layout, project_id, chapter_number)
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/prepare", response_model=PrepareChapterResponse)
async def prepare_chapter(
    req: PrepareChapterRequest,
    runtime: RuntimeDep,
) -> PrepareChapterResponse:
    """Prepare one chapter until the planning checkpoint."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.prepare",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_prepare_chapter(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    result = execution.result
    return (
        result
        if isinstance(result, PrepareChapterResponse)
        else PrepareChapterResponse.model_validate(result)
    )


@router.post("/manual-revision", response_model=dict)
async def manual_revision(
    req: ManualRevisionRequest,
    background_tasks: BackgroundTasks,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Apply a manually supplied final chapter revision."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.manual_revision",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_manual_revision(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    result = dict(execution.result)
    if req.background_reevaluate:
        background_tasks.add_task(
            _background_reevaluate_manual_revision,
            runtime,
            project_id=req.project_id,
            chapter_number=req.chapter_number,
        )
        result["background_reevaluate_scheduled"] = True
    return result


@router.post("/resolve-checkpoint", response_model=ChapterSessionResult)
async def resolve_chapter_checkpoint(
    req: ResolveChapterCheckpointRequest,
    runtime: RuntimeDep,
) -> ChapterSessionResult:
    """Resolve one chapter-studio checkpoint.

    Set ``force=True`` in the request body to re-enter a chapter whose canon
    watermark has already advanced (recovery path when the chapter pipeline
    rejected the archive but the canon write happened earlier in a previous
    bug). Without ``force``, attempting to resolve a checkpoint for an
    already-completed chapter raises HTTP 409.
    """
    try:
        execution = await execute_api_with_run_logger(
            runtime,
            project_id=req.project_id,
            command="api:chapter.resolve_checkpoint",
            metadata=req.model_dump(mode="json"),
            chapter_number=req.chapter_number,
            execute=lambda on_step: execute_resolve_chapter_checkpoint(
                runtime,
                req,
                on_step_progress=on_step,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = execution.result
    return (
        result
        if isinstance(result, ChapterSessionResult)
        else ChapterSessionResult.model_validate(result)
    )


@router.get("/{project_id}/{chapter_number}/continuity")
async def get_chapter_continuity(
    project_id: str,
    chapter_number: int,
    storage: StorageDep,
) -> dict[str, object]:
    """Return continuity artifacts for one chapter."""
    layout = ProjectLayout(storage.project_path(project_id))
    report_path = layout.continuity_report_path(chapter_number)
    repair_path = layout.repair_plan_path(chapter_number)
    bridge_path = layout.chapter_bridge_path(chapter_number)
    if not storage.exists(report_path):
        raise HTTPException(status_code=404, detail="continuity report not found")
    return {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "continuity_report": storage.load_json(report_path),
        "repair_plan": storage.load_json(repair_path) if storage.exists(repair_path) else None,
        "bridge": storage.load_json(bridge_path) if storage.exists(bridge_path) else None,
    }


@router.get("/{project_id}/{chapter_number}/state")
async def get_chapter_state(
    project_id: str,
    chapter_number: int,
    storage: StorageDep,
) -> dict[str, object]:
    """Return chapter state artifacts for one chapter."""
    layout = ProjectLayout(storage.project_path(project_id))
    packet_path = layout.chapter_state_packet_path(chapter_number)
    exit_path = layout.chapter_exit_state_path(chapter_number)
    if not storage.exists(packet_path):
        raise HTTPException(status_code=404, detail="state packet not found")
    return {
        "project_id": project_id,
        "chapter_number": chapter_number,
        "state_packet": storage.load_json(packet_path),
        "chapter_exit_state": storage.load_json(exit_path) if storage.exists(exit_path) else None,
    }


@router.post("/repair-continuity", response_model=RepairChapterResponse)
async def repair_continuity(
    req: RepairContinuityRequest,
    runtime: RuntimeDep,
) -> RepairChapterResponse:
    """Run targeted continuity repair for selected issues in one chapter."""
    mission = continuity_repair_mission(req, settings=runtime.settings)
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.repair_continuity",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_repair(
            runtime,
            mission,
            on_step_progress=on_step,
        ),
    )
    return RepairChapterResponse.model_validate(
        build_repair_chapter_result_payload(
            execution.project_id,
            execution.result,
            chapter_number=req.chapter_number,
        )
    )


@router.post("/repair-causal", response_model=RepairChapterResponse)
async def repair_causal(
    req: RepairCausalRequest,
    runtime: RuntimeDep,
) -> RepairChapterResponse:
    """Run targeted causal chain repair for selected issues in one chapter."""
    mission = causal_repair_mission(req, settings=runtime.settings)
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.repair_causal",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_repair(
            runtime,
            mission,
            on_step_progress=on_step,
        ),
    )
    return RepairChapterResponse.model_validate(
        build_repair_chapter_result_payload(
            execution.project_id,
            execution.result,
            chapter_number=req.chapter_number,
        )
    )


@router.post("/repair-issues", response_model=RepairChapterResponse)
async def repair_issues(
    req: RepairIssuesRequest,
    runtime: RuntimeDep,
) -> RepairChapterResponse:
    """Run targeted repair for both continuity and causal issues in one chapter."""
    mission = issues_repair_mission(req, settings=runtime.settings)
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter.repair_issues",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_repair(
            runtime,
            mission,
            on_step_progress=on_step,
        ),
    )
    return RepairChapterResponse.model_validate(
        build_repair_chapter_result_payload(
            execution.project_id,
            execution.result,
            chapter_number=req.chapter_number,
        )
    )
