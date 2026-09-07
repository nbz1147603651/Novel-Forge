"""TTS project lock management.

Serializes TTS mutations per-project using a dedicated ResourceName.TTS scope
and a separate fcntl lock file, decoupled from the chapter pipeline's STATE lock.

Author: novel-forge
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from novel_forge.core.infra.resource_locks import (
    ResourceLockType,
    ResourceName,
    get_resource_lock_manager,
)
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    project_file_lock_owner,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.async_context import sync_to_async_context

_log = get_logger("workspace.tts.locks")

_TTS_LOCKED_PROJECTS: ContextVar[frozenset[str]] = ContextVar(
    "tts_locked_projects",
    default=frozenset(),
)
_P = ParamSpec("_P")
_R = TypeVar("_R")


@asynccontextmanager
async def tts_project_lock(
    project_id: str,
    layout: ProjectLayout,
) -> AsyncIterator[None]:
    """Serialize TTS mutations across every TTS entrypoint.

    Uses a dedicated ``ResourceName.TTS`` scope and a separate fcntl lock
    file (``<project>_tts.lock``) so TTS operations do not block on — or
    get blocked by — the chapter pipeline's ``ResourceName.STATE`` lock.

    Author: novel-forge
    """
    held_projects = _TTS_LOCKED_PROJECTS.get()
    if project_id in held_projects:
        yield
        return

    owner_id = f"async-task:{id(asyncio.current_task())}"
    with project_file_lock_owner(owner_id):
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(
                    get_resource_lock_manager().lock(
                        ResourceName.TTS,
                        ResourceLockType.EXCLUSIVE,
                        project_id=project_id,
                    )
                )
            except Exception as exc:
                _log.warning(
                    "TTS resource lock unavailable; falling back to filesystem lock | "
                    "project=%s error=%s",
                    project_id,
                    exc,
                )

            storage = FileSystemStorage(Path(layout.root).parent)
            tts_lock_id = f"{project_id}_tts"
            await stack.enter_async_context(
                sync_to_async_context(storage.project_lock(tts_lock_id))
            )
            token = _TTS_LOCKED_PROJECTS.set(held_projects.union({project_id}))
            try:
                yield
            finally:
                _TTS_LOCKED_PROJECTS.reset(token)


def with_tts_project_lock(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Decorate a keyword-only workspace TTS mutation with the shared project lock.

    Author: novel-forge
    """

    @wraps(func)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        call_kwargs = dict(kwargs)
        project_id = str(call_kwargs.get("project_id") or "").strip()
        layout = call_kwargs.get("layout")
        if not project_id or not isinstance(layout, ProjectLayout):
            return await func(*args, **kwargs)
        async with tts_project_lock(project_id, layout):
            return await func(*args, **kwargs)

    return wrapped


def bounded_preview_timeout(
    value: Any,
    *,
    default: float,
    maximum: float,
) -> float:
    """Return a safe interactive-preview timeout from a settings snapshot.

    Author: novel-forge
    """
    try:
        return max(0.1, min(float(value), maximum))
    except (TypeError, ValueError):
        return default


def with_interactive_preview_tts_lock(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Acquire the shared TTS lock for an audition without silently queueing forever.

    Author: novel-forge
    """

    @wraps(func)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        call_kwargs = dict(kwargs)
        project_id = str(call_kwargs.get("project_id") or "").strip()
        layout = call_kwargs.get("layout")
        settings = call_kwargs.get("settings")
        if not project_id or not isinstance(layout, ProjectLayout):
            return await func(*args, **kwargs)

        wait_timeout_s = bounded_preview_timeout(
            getattr(settings, "tts_preview_lock_wait_timeout_s", 8.0),
            default=8.0,
            maximum=60.0,
        )
        async with AsyncExitStack() as lock_stack:
            try:
                async with asyncio.timeout(wait_timeout_s):
                    await lock_stack.enter_async_context(tts_project_lock(project_id, layout))
            except TimeoutError as exc:
                raise TimeoutError(
                    "当前项目的自动配音仍在处理，试听等待超过 "
                    f"{wait_timeout_s:g} 秒；请稍后重试，或先在章台停止自动配音。"
                ) from exc
            return await func(*args, **kwargs)

    return wrapped


@asynccontextmanager
async def tts_voice_library_lock(storage_root: Path) -> AsyncIterator[None]:
    """Serialize the read-modify-write cycle for the cross-project voice library.

    Author: novel-forge
    """
    storage = FileSystemStorage(storage_root)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            sync_to_async_context(storage.project_lock("_global_tts_voice_library"))
        )
        yield
