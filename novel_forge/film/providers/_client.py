"""Shared polling client for film generation providers.

Absorbs the ComfyUI API-node client pattern
(``comfy_api_nodes/util/client.py``): normalized terminal/progress status
vocabularies, queued-vs-processing phase reporting with elapsed time and
cooperative cancellation.  Provider-neutral — each provider keeps its own
submit/query wire format and only plugs its status string into
:func:`normalize_task_status` / :func:`poll_until_terminal`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .base import FilmProviderError, FilmProviderTask, FilmProviderTaskState

# Standard status vocabularies shared across platforms (normalized from
# ComfyUI's client.py status sets; MiniMax v2/Hailuo03 uses the same words).
COMPLETED_STATUSES = frozenset(
    {"succeeded", "succeed", "success", "completed", "finished", "done", "complete"}
)
FAILED_STATUSES = frozenset(
    {"fail", "failed", "error", "expired"}
)
CANCELLED_STATUSES = frozenset({"cancelled", "canceled", "canceling"})
QUEUED_STATUSES = frozenset(
    {"created", "queued", "queueing", "submitted", "initializing", "wait", "in_queue", "pending"}
)

# Query function signature shared by all film providers.
QueryFn = Callable[[FilmProviderTask], Awaitable[FilmProviderTask]]
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


def normalize_task_status(raw: str | None) -> FilmProviderTaskState:
    """Map one platform status string onto the shared task-state model.

    Platform-specific spellings (``Success``, ``SUCCEEDED``, ``queueing``,
    ``expired``, …) collapse into the four provider states; anything unknown
    counts as still running.
    """
    status = str(raw or "").strip().lower()
    if status in COMPLETED_STATUSES:
        return FilmProviderTaskState.SUCCEEDED
    if status in CANCELLED_STATUSES:
        return FilmProviderTaskState.CANCELLED
    if status in FAILED_STATUSES:
        return FilmProviderTaskState.FAILED
    if status in QUEUED_STATUSES:
        return FilmProviderTaskState.PENDING
    return FilmProviderTaskState.RUNNING


async def poll_until_terminal(
    query: QueryFn,
    task: FilmProviderTask,
    *,
    is_cancelled: CancelCheck | None = None,
    on_progress: ProgressCallback | None = None,
    poll_interval_s: float = 5.0,
    max_polls: int = 480,
    estimated_duration_s: int | None = None,
    target_id: str = "",
) -> FilmProviderTask:
    """Poll ``query`` until the task reaches a terminal state.

    - transient provider errors back off exponentially instead of failing;
    - ``is_cancelled()`` short-circuits the loop so a cancelled job never
      keeps hitting the provider;
    - ``on_progress`` receives a dict per tick:
      ``{target_id, stage: queued|processing, state, elapsed_s,
      processing_s, estimated_total_s}`` (ComfyUI _PollUIState equivalent).
    """
    started = time.monotonic()
    active_since: float | None = None
    consecutive_errors = 0
    for _attempt in range(max(1, max_polls)):
        if is_cancelled is not None and is_cancelled():
            break
        if task.state in {
            FilmProviderTaskState.SUCCEEDED,
            FilmProviderTaskState.FAILED,
            FilmProviderTaskState.CANCELLED,
        }:
            return task
        try:
            task = await query(task)
            consecutive_errors = 0
        except FilmProviderError as exc:
            consecutive_errors += 1
            if not exc.retryable:
                raise
            await asyncio.sleep(min(60.0, 1.5**consecutive_errors))
            continue
        if on_progress is not None:
            now = time.monotonic()
            if task.state == FilmProviderTaskState.PENDING:
                active_since = None
            elif active_since is None:
                active_since = now
            on_progress(
                {
                    "target_id": target_id,
                    "stage": "queued"
                    if task.state == FilmProviderTaskState.PENDING
                    else "processing",
                    "state": task.state.value,
                    "elapsed_s": int(now - started),
                    "processing_s": int((now - active_since) if active_since else 0),
                    "estimated_total_s": estimated_duration_s,
                }
            )
        if task.state not in {
            FilmProviderTaskState.SUCCEEDED,
            FilmProviderTaskState.FAILED,
            FilmProviderTaskState.CANCELLED,
        }:
            await asyncio.sleep(poll_interval_s)
    return task


__all__ = [
    "COMPLETED_STATUSES",
    "CANCELLED_STATUSES",
    "FAILED_STATUSES",
    "QUEUED_STATUSES",
    "normalize_task_status",
    "poll_until_terminal",
]
