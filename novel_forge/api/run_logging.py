"""Run-log helpers for API entrypoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from fastapi.encoders import jsonable_encoder

from novel_forge.obs.project_logger import ProjectRunLogger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.execution_result import ExecutionResult

if TYPE_CHECKING:
    from novel_forge.workspace.runtime import RuntimeServices


async def execute_api_with_run_logger(
    runtime: "RuntimeServices",
    *,
    project_id: str,
    command: str,
    metadata: dict[str, Any],
    execute: Callable[[Callable[[str, Any], None]], Awaitable[ExecutionResult[Any]]],
    chapter_number: int | None = None,
) -> ExecutionResult[Any]:
    """Run an API workflow with the same per-project logs as CLI/desktop."""
    layout = ProjectLayout(runtime.storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    run_logger = ProjectRunLogger(
        layout=layout,
        project_id=project_id,
        command=command,
        metadata=metadata,
        keep_runs=getattr(runtime.settings, "log_keep_runs", 20),
    )

    if chapter_number is not None:
        run_logger.log_event("chapter_started", {"chapter": chapter_number})

    def _record_step(step: str, data: Any) -> None:
        run_logger.log_step(step, data)

    try:
        with run_logger.activate(), runtime.router.observe(run_logger.record_router_event):
            execution = await execute(_record_step)
        result = getattr(execution, "result", None)
        run_logger.finalize(
            status="success",
            result={"project_id": getattr(execution, "project_id", project_id)},
            trace_summary=getattr(result, "trace_summary", None),
        )
        return execution
    except BaseException as exc:
        run_logger.finalize(status="failed", error=exc)
        raise


def _sse_frame(event: str, payload: Any) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {data}\n\n"


async def stream_api_with_run_logger(
    runtime: "RuntimeServices",
    *,
    project_id: str,
    command: str,
    metadata: dict[str, Any],
    execute: Callable[[Callable[[str, Any], None]], Awaitable[ExecutionResult[Any]]],
    build_result: Callable[[ExecutionResult[Any]], Any],
    chapter_number: int | None = None,
) -> AsyncIterator[str]:
    """Run an API workflow and stream progress as Server-Sent Events."""
    layout = ProjectLayout(runtime.storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    run_logger = ProjectRunLogger(
        layout=layout,
        project_id=project_id,
        command=command,
        metadata=metadata,
        keep_runs=getattr(runtime.settings, "log_keep_runs", 20),
    )
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _queue_event(event: str, payload: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

    _queue_event(
        "run_log_started",
        {
            "project_id": project_id,
            "command": command,
            "run_id": run_logger.run_id,
            "run_log_dir": str(run_logger.run_dir),
        },
    )

    if chapter_number is not None:
        chapter_payload = {"chapter": chapter_number}
        run_logger.log_event("chapter_started", chapter_payload)
        _queue_event("chapter_started", chapter_payload)

    def _record_step(step: str, data: Any) -> None:
        run_logger.log_step(step, data)
        _queue_event(step, data)

    def _record_router_event(event: str, payload: dict[str, Any]) -> None:
        run_logger.record_router_event(event, payload)
        control_plane = getattr(runtime, "control_plane", None)
        if control_plane is not None:
            control_plane.call_ledger.record_router_event(event, payload, project_id=project_id)
        _queue_event(event, payload)

    async def _run() -> None:
        try:
            with run_logger.activate(), runtime.router.observe(_record_router_event):
                execution = await execute(_record_step)
            result = getattr(execution, "result", None)
            result_payload = build_result(execution)
            run_logger.finalize(
                status="success",
                result={"project_id": getattr(execution, "project_id", project_id)},
                trace_summary=getattr(result, "trace_summary", None),
            )
            _queue_event("result", result_payload)
        except asyncio.CancelledError as exc:
            run_logger.finalize(status="cancelled", error=exc)
            _queue_event(
                "error",
                {"error": "cancelled", "message": "streaming request was cancelled"},
            )
            raise
        except BaseException as exc:
            run_logger.finalize(status="failed", error=exc)
            _queue_event(
                "error",
                {"error": type(exc).__name__, "message": str(exc)},
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            event, payload = item
            yield _sse_frame(event, payload)
    finally:
        if not task.done():
            task.cancel()
            with suppress(BaseException):
                await task

    with suppress(BaseException):
        task.result()
