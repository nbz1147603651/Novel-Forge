"""Routes for short-mode story generation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from novel_forge.api.deps import get_runtime_services
from novel_forge.api.run_logging import execute_api_with_run_logger, stream_api_with_run_logger
from novel_forge.workspace.contracts import RunShortRequest, RunShortResponse
from novel_forge.workspace.execution import execute_run_short
from novel_forge.workspace.result_payloads import build_run_short_result_payload
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]


@router.post("/run", response_model=RunShortResponse)
async def run_short_story(
    req: RunShortRequest,
    runtime: RuntimeDep,
) -> RunShortResponse:
    """Generate a short story end-to-end."""
    project_id = req.project_id.strip() or runtime.create_project_id("short")
    effective_req = req.model_copy(update={"project_id": project_id})
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=project_id,
        command="api:short.run",
        metadata=effective_req.model_dump(mode="json"),
        execute=lambda on_step: execute_run_short(
            runtime,
            effective_req,
            on_step_progress=on_step,
        ),
    )
    return RunShortResponse.model_validate(
        build_run_short_result_payload(
            execution.project_id,
            execution.result,
        )
    )


@router.post("/run/stream")
async def run_short_story_stream(
    req: RunShortRequest,
    runtime: RuntimeDep,
) -> StreamingResponse:
    """Generate a short story and stream progress as SSE."""
    project_id = req.project_id.strip() or runtime.create_project_id("short")
    effective_req = req.model_copy(update={"project_id": project_id})
    events = stream_api_with_run_logger(
        runtime,
        project_id=project_id,
        command="api:short.run",
        metadata=effective_req.model_dump(mode="json"),
        execute=lambda on_step: execute_run_short(
            runtime,
            effective_req,
            on_step_progress=on_step,
        ),
        build_result=lambda execution: RunShortResponse.model_validate(
            build_run_short_result_payload(
                execution.project_id,
                execution.result,
            )
        ).model_dump(mode="json"),
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
