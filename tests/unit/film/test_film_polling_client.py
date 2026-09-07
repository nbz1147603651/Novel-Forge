"""Shared polling client tests (Batch C: ComfyUI poll_op absorption)."""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.film.providers._client import (
    normalize_task_status,
    poll_until_terminal,
)
from novel_forge.film.providers.base import (
    FilmGenerationMode,
    FilmProviderError,
    FilmProviderTask,
    FilmProviderTaskState,
)


def _task(state: FilmProviderTaskState = FilmProviderTaskState.RUNNING) -> FilmProviderTask:
    return FilmProviderTask(
        provider_id="minimax",
        model_id="MiniMax-H3",
        mode=FilmGenerationMode.TEXT_TO_VIDEO,
        state=state,
        task_id="t-1",
        api_version="v2",
    )


def test_normalize_status_vocabularies() -> None:
    assert normalize_task_status("Success") == FilmProviderTaskState.SUCCEEDED
    assert normalize_task_status("succeeded") == FilmProviderTaskState.SUCCEEDED
    assert normalize_task_status("completed") == FilmProviderTaskState.SUCCEEDED
    assert normalize_task_status("FAILED") == FilmProviderTaskState.FAILED
    assert normalize_task_status("cancelled") == FilmProviderTaskState.CANCELLED
    assert normalize_task_status("expired") == FilmProviderTaskState.FAILED
    assert normalize_task_status("queueing") == FilmProviderTaskState.PENDING
    assert normalize_task_status("queued") == FilmProviderTaskState.PENDING
    assert normalize_task_status("submitted") == FilmProviderTaskState.PENDING
    assert normalize_task_status("preparing") == FilmProviderTaskState.RUNNING
    assert normalize_task_status("unknown") == FilmProviderTaskState.RUNNING


async def test_poll_until_terminal_reports_progress_phases() -> None:
    queries = 0
    progress: list[dict[str, Any]] = []

    async def query(task: FilmProviderTask) -> FilmProviderTask:
        nonlocal queries
        queries += 1
        if queries == 1:
            return task.model_copy(update={"state": FilmProviderTaskState.PENDING})
        return task.model_copy(
            update={
                "state": FilmProviderTaskState.SUCCEEDED,
                "asset_urls": ["https://x/final.mp4"],
            }
        )

    result = await poll_until_terminal(
        query,
        _task(),
        on_progress=progress.append,
        poll_interval_s=0,
        estimated_duration_s=180,
        target_id="sh-1",
    )

    assert queries == 2
    assert result.state == FilmProviderTaskState.SUCCEEDED
    assert result.asset_urls == ["https://x/final.mp4"]
    assert progress[0]["target_id"] == "sh-1"
    assert progress[0]["stage"] == "queued"
    assert progress[0]["estimated_total_s"] == 180
    assert progress[-1]["stage"] == "processing"
    assert progress[-1]["state"] == "succeeded"


async def test_poll_until_terminal_stops_on_cancellation() -> None:
    queries = 0

    async def query(task: FilmProviderTask) -> FilmProviderTask:
        nonlocal queries
        queries += 1
        return task.model_copy(update={"state": FilmProviderTaskState.RUNNING})

    result = await poll_until_terminal(
        query,
        _task(),
        is_cancelled=lambda: True,
        poll_interval_s=0,
        max_polls=100,
    )

    assert queries == 0  # cancelled before the first poll
    assert result.state == FilmProviderTaskState.RUNNING


async def test_poll_until_terminal_backs_off_on_transient_errors() -> None:
    attempts = 0

    async def query(task: FilmProviderTask) -> FilmProviderTask:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise FilmProviderError("rate limited", retryable=True)
        return task.model_copy(
            update={
                "state": FilmProviderTaskState.SUCCEEDED,
                "asset_urls": ["https://x/ok.mp4"],
            }
        )

    result = await poll_until_terminal(query, _task(), poll_interval_s=0)

    assert attempts == 3
    assert result.state == FilmProviderTaskState.SUCCEEDED


async def test_poll_until_terminal_raises_on_hard_errors() -> None:
    async def query(_task: FilmProviderTask) -> FilmProviderTask:
        raise FilmProviderError("auth failed", retryable=False)

    with pytest.raises(FilmProviderError, match="auth failed"):
        await poll_until_terminal(query, _task(), poll_interval_s=0)
