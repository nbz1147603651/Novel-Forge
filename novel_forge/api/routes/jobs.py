"""Asynchronous UI job routes shared by desktop-style clients."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from novel_forge.api.deps import get_job_service
from novel_forge.app_service.contracts import DecisionRequest, JobCommand, JobEvent, JobRecord
from novel_forge.app_service.job_service import JobService

router = APIRouter()
JobServiceDep = Annotated[JobService, Depends(get_job_service)]


@router.post("", response_model=JobRecord)
@router.post("/", response_model=JobRecord)
async def submit_job(command: JobCommand, service: JobServiceDep) -> JobRecord:
    return service.submit(command)


@router.get("", response_model=list[JobRecord])
@router.get("/", response_model=list[JobRecord])
async def list_jobs(service: JobServiceDep, project_id: str | None = None) -> list[JobRecord]:
    return service.list(project_id=project_id)


@router.get("/{job_id}", response_model=JobRecord)
async def get_job(job_id: str, service: JobServiceDep) -> JobRecord:
    record = service.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record


@router.post("/{job_id}/cancel", response_model=JobRecord)
async def cancel_job(
    job_id: str,
    service: JobServiceDep,
    reason: str = "用户已取消",
) -> JobRecord:
    try:
        return service.cancel(job_id, reason=reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@router.post("/{job_id}/resume", response_model=JobRecord)
async def resume_job(job_id: str, service: JobServiceDep) -> JobRecord:
    """Resume a crash-reconciled job using its durable intent payload."""

    try:
        return service.resume(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="resumable job not found") from exc


@router.post("/{job_id}/decision", response_model=JobRecord)
async def provide_job_decision(
    job_id: str,
    request: DecisionRequest,
    service: JobServiceDep,
) -> JobRecord:
    try:
        return service.provide_decision(job_id, request.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job is not awaiting a decision") from exc


def _format_sse(event: JobEvent) -> str:
    event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
    return f"event: {event_type}\ndata: {event.model_dump_json()}\n\n"


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    service: JobServiceDep,
) -> StreamingResponse:
    if service.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def _events() -> AsyncIterator[str]:
        subscription = service.open_subscription(job_id)
        try:
            for replayed in service.replay_events(job_id):
                yield _format_sse(replayed)
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.to_thread(subscription.get, 1.0)
                if event is None:
                    continue
                yield _format_sse(event)
        finally:
            subscription.close()

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
