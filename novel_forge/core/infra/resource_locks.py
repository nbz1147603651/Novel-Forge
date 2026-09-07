from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator


class _SharedLock:
    def __init__(self) -> None:
        self._count: int = 0
        self._exclusive_waiting: int = 0
        self._cond: asyncio.Condition = asyncio.Condition()

    async def acquire_shared(self) -> None:
        async with self._cond:
            while self._count < 0 or self._exclusive_waiting > 0:
                await self._cond.wait()
            self._count += 1

    async def release_shared(self) -> None:
        async with self._cond:
            self._count -= 1
            if self._count == 0:
                self._cond.notify_all()

    async def acquire_exclusive(self) -> None:
        async with self._cond:
            self._exclusive_waiting += 1
            while self._count != 0:
                await self._cond.wait()
            self._exclusive_waiting -= 1
            self._count = -1

    async def release_exclusive(self) -> None:
        async with self._cond:
            self._count = 0
            self._cond.notify_all()

    @property
    def is_locked(self) -> bool:
        return self._count != 0 or self._exclusive_waiting > 0


class ResourceName(str, Enum):
    STORY_BIBLE = "story_bible"
    CHARACTER_BIBLE = "character_bible"
    OUTLINE = "outline"
    CANON = "canon"
    STATE = "state"
    TTS = "tts"


class ResourceLockType(str, Enum):
    EXCLUSIVE = "exclusive"
    SHARED = "shared"


@dataclass(frozen=True)
class LockKey:
    resource: ResourceName
    chapter: int | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.chapter is not None and self.chapter < 0:
            raise ValueError(f"chapter must be non-negative, got {self.chapter}")
        if self.project_id is not None:
            clean_project_id = str(self.project_id).strip()
            object.__setattr__(self, "project_id", clean_project_id or None)

    @property
    def is_outline_segment(self) -> bool:
        return self.resource == ResourceName.OUTLINE and self.chapter is not None

    def as_outline_segment(self) -> str:
        if not self.is_outline_segment:
            raise ValueError(f"LockKey {self!r} is not an outline segment")
        return f"{self._project_prefix()}outline_chapter_{self.chapter}"

    def _project_prefix(self) -> str:
        return f"{self.project_id}:" if self.project_id else ""

    def __str__(self) -> str:
        if self.chapter is None:
            return f"{self._project_prefix()}{self.resource.value}"
        return f"{self._project_prefix()}{self.resource.value}_chapter_{self.chapter}"


class ResourceLockManager:
    def __init__(self) -> None:
        self._locks: dict[LockKey, _SharedLock] = {}
        self._held_locks: ContextVar[
            tuple[tuple[asyncio.Task[Any] | None, LockKey, ResourceLockType], ...]
        ] = ContextVar(
            "novel_forge_held_resource_locks",
            default=(),
        )

    def _current_task_lock_type(self, key: LockKey) -> ResourceLockType | None:
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        for owner, held_key, lock_type in reversed(self._held_locks.get()):
            if owner is current_task and held_key == key:
                return lock_type
        return None

    @asynccontextmanager
    async def _reentrant_guard(
        self,
        key: LockKey,
        lock_type: ResourceLockType,
    ) -> AsyncIterator[bool]:
        held_type = self._current_task_lock_type(key)
        if held_type is None:
            yield False
            return
        if held_type == ResourceLockType.SHARED and lock_type == ResourceLockType.EXCLUSIVE:
            raise RuntimeError(f"Cannot upgrade shared lock to exclusive in the same task: {key}")
        yield True

    def _get_or_create_lock(self, key: LockKey) -> _SharedLock:
        if key not in self._locks:
            self._locks[key] = _SharedLock()
        return self._locks[key]

    def _lock_key(
        self,
        resource: ResourceName,
        *,
        chapter: int | None = None,
        project_id: str | None = None,
    ) -> LockKey:
        return LockKey(resource=resource, chapter=chapter, project_id=project_id)

    @asynccontextmanager
    async def lock(
        self,
        resource: ResourceName,
        lock_type: ResourceLockType,
        *,
        chapter: int | None = None,
        project_id: str | None = None,
    ) -> AsyncIterator[None]:
        key = self._lock_key(resource, chapter=chapter, project_id=project_id)
        lock_obj = self._get_or_create_lock(key)

        async with self._reentrant_guard(key, lock_type) as already_held:
            if already_held:
                yield
                return

        if lock_type == ResourceLockType.SHARED:
            await lock_obj.acquire_shared()
            token = self._held_locks.set(
                (*self._held_locks.get(), (asyncio.current_task(), key, lock_type))
            )
            try:
                yield
            finally:
                self._held_locks.reset(token)
                await lock_obj.release_shared()
        else:
            await lock_obj.acquire_exclusive()
            token = self._held_locks.set(
                (*self._held_locks.get(), (asyncio.current_task(), key, lock_type))
            )
            try:
                yield
            finally:
                self._held_locks.reset(token)
                await lock_obj.release_exclusive()

    @asynccontextmanager
    async def acquire_outline_chapter(
        self,
        chapter: int,
        lock_type: ResourceLockType,
        *,
        project_id: str | None = None,
    ) -> AsyncIterator[None]:
        key = self._lock_key(ResourceName.OUTLINE, chapter=chapter, project_id=project_id)
        lock_obj = self._get_or_create_lock(key)

        async with self._reentrant_guard(key, lock_type) as already_held:
            if already_held:
                yield
                return

        if lock_type == ResourceLockType.SHARED:
            await lock_obj.acquire_shared()
            token = self._held_locks.set(
                (*self._held_locks.get(), (asyncio.current_task(), key, lock_type))
            )
            try:
                yield
            finally:
                self._held_locks.reset(token)
                await lock_obj.release_shared()
        else:
            await lock_obj.acquire_exclusive()
            token = self._held_locks.set(
                (*self._held_locks.get(), (asyncio.current_task(), key, lock_type))
            )
            try:
                yield
            finally:
                self._held_locks.reset(token)
                await lock_obj.release_exclusive()

    def is_locked(
        self,
        resource: ResourceName,
        *,
        chapter: int | None = None,
        project_id: str | None = None,
    ) -> bool:
        lock_obj = self._locks.get(self._lock_key(resource, chapter=chapter, project_id=project_id))

        if lock_obj is None:
            return False
        return lock_obj.is_locked

    def get_shared_count(
        self,
        resource: ResourceName,
        *,
        chapter: int | None = None,
        project_id: str | None = None,
    ) -> int:
        lock_obj = self._locks.get(self._lock_key(resource, chapter=chapter, project_id=project_id))

        if lock_obj is None:
            return 0
        return max(0, lock_obj._count)

    def current_task_holds(
        self,
        resource: ResourceName,
        *,
        chapter: int | None = None,
        project_id: str | None = None,
    ) -> bool:
        key = self._lock_key(resource, chapter=chapter, project_id=project_id)
        return self._current_task_lock_type(key) is not None


_resource_lock_manager: ResourceLockManager | None = None


def get_resource_lock_manager() -> ResourceLockManager:
    global _resource_lock_manager
    if _resource_lock_manager is None:
        _resource_lock_manager = ResourceLockManager()
    return _resource_lock_manager


def reset_resource_lock_manager() -> None:
    global _resource_lock_manager
    _resource_lock_manager = None


__all__ = [
    "ResourceLockManager",
    "ResourceName",
    "ResourceLockType",
    "LockKey",
    "get_resource_lock_manager",
    "reset_resource_lock_manager",
]
