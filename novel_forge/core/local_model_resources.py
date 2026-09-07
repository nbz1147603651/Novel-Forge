"""Process-wide admission control for heavyweight local model work.

Desktop jobs intentionally run in separate threads and asyncio event loops, so
an ``asyncio.Semaphore`` cannot coordinate Ollama, TTS, ASR, alignment and
sound-generation workloads.  This broker keeps only thread-safe state and uses
short cancellable async polling while a request is queued.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any


class LocalResourcePriority(IntEnum):
    """Lower values are admitted first when resources become available."""

    INTERACTIVE = 0
    FOREGROUND = 10
    BACKGROUND = 20
    MAINTENANCE = 30


class LocalMemoryClass(StrEnum):
    LIGHT = "light"
    MEDIUM = "medium"
    HIGH = "high"


_MEMORY_UNITS = {
    LocalMemoryClass.LIGHT: 1,
    LocalMemoryClass.MEDIUM: 2,
    LocalMemoryClass.HIGH: 4,
}
_BUDGET_CAPACITY = {
    "light": (1, 1),
    "medium": (2, 2),
    "high": (4, 4),
}


@dataclass(frozen=True)
class LocalResourceRequest:
    """One local workload's resource and scheduling requirements."""

    workload: str
    label: str
    memory_class: LocalMemoryClass = LocalMemoryClass.MEDIUM
    accelerator: bool = True
    cpu_heavy: bool = False
    priority: LocalResourcePriority = LocalResourcePriority.FOREGROUND
    timeout_s: float | None = None


@dataclass(frozen=True)
class LocalResourceItemSnapshot:
    request_id: int
    workload: str
    label: str
    priority: str
    memory_class: str
    accelerator_units: int
    cpu_units: int
    age_s: float


@dataclass(frozen=True)
class LocalResourceSnapshot:
    budget: str
    accelerator_capacity: int
    accelerator_used: int
    cpu_capacity: int
    cpu_used: int
    active: tuple[LocalResourceItemSnapshot, ...]
    waiting: tuple[LocalResourceItemSnapshot, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "accelerator": {
                "used": self.accelerator_used,
                "capacity": self.accelerator_capacity,
            },
            "cpu": {"used": self.cpu_used, "capacity": self.cpu_capacity},
            "active": [item.__dict__ for item in self.active],
            "waiting": [item.__dict__ for item in self.waiting],
        }


@dataclass
class _QueuedRequest:
    request_id: int
    request: LocalResourceRequest
    enqueued_at: float


@dataclass
class _ActiveRequest:
    queued: _QueuedRequest
    accelerator_units: int
    cpu_units: int
    acquired_at: float


class LocalResourceLease:
    """Idempotently releases one broker admission."""

    def __init__(self, broker: LocalModelResourceBroker, request_id: int) -> None:
        self._broker = broker
        self.request_id = request_id
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._broker.release(self.request_id)


class LocalModelResourceBroker:
    """Fair, process-wide resource admission across independent event loops."""

    def __init__(self, *, budget: str = "medium", poll_interval_s: float = 0.05) -> None:
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._waiting: dict[int, _QueuedRequest] = {}
        self._active: dict[int, _ActiveRequest] = {}
        self._poll_interval_s = max(0.01, poll_interval_s)
        self._budget = "medium"
        self._accelerator_capacity = 2
        self._cpu_capacity = 2
        self.configure(budget)

    def configure(self, budget: str) -> None:
        normalized = str(budget or "medium").strip().lower()
        if normalized not in _BUDGET_CAPACITY:
            normalized = "medium"
        accelerator_capacity, cpu_capacity = _BUDGET_CAPACITY[normalized]
        with self._lock:
            self._budget = normalized
            self._accelerator_capacity = accelerator_capacity
            self._cpu_capacity = cpu_capacity

    async def acquire(
        self,
        request: LocalResourceRequest,
        *,
        on_wait: Callable[[LocalResourceSnapshot], None] | None = None,
    ) -> LocalResourceLease:
        request_id = next(self._ids)
        queued = _QueuedRequest(
            request_id=request_id,
            request=request,
            enqueued_at=time.monotonic(),
        )
        with self._lock:
            self._waiting[request_id] = queued
        deadline = (
            queued.enqueued_at + request.timeout_s
            if request.timeout_s is not None and request.timeout_s > 0
            else None
        )
        wait_notified = False
        try:
            while True:
                with self._lock:
                    if self._next_admissible_id() == request_id:
                        accelerator_units, cpu_units = self._required_units(request)
                        self._waiting.pop(request_id, None)
                        self._active[request_id] = _ActiveRequest(
                            queued=queued,
                            accelerator_units=accelerator_units,
                            cpu_units=cpu_units,
                            acquired_at=time.monotonic(),
                        )
                        return LocalResourceLease(self, request_id)
                    snapshot = self._snapshot_locked() if on_wait and not wait_notified else None
                if snapshot is not None:
                    on_wait(snapshot)
                    wait_notified = True
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"等待本地模型资源超时：{request.label}（{request.timeout_s:.0f} 秒）"
                    )
                await asyncio.sleep(self._poll_interval_s)
        except BaseException:
            with self._lock:
                self._waiting.pop(request_id, None)
            raise

    def release(self, request_id: int) -> None:
        with self._lock:
            self._active.pop(request_id, None)

    @asynccontextmanager
    async def lease(
        self,
        request: LocalResourceRequest,
        *,
        on_wait: Callable[[LocalResourceSnapshot], None] | None = None,
    ) -> AsyncIterator[LocalResourceLease]:
        lease = await self.acquire(request, on_wait=on_wait)
        try:
            yield lease
        finally:
            lease.release()

    def snapshot(self) -> LocalResourceSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def reset(self, *, budget: str = "medium") -> None:
        """Clear state for deterministic tests; active production leases must not use this."""
        with self._lock:
            self._waiting.clear()
            self._active.clear()
            self.configure(budget)

    def _required_units(self, request: LocalResourceRequest) -> tuple[int, int]:
        weight = _MEMORY_UNITS[request.memory_class]
        accelerator = min(weight, self._accelerator_capacity) if request.accelerator else 0
        cpu = min(weight, self._cpu_capacity) if request.cpu_heavy else 0
        return accelerator, cpu

    def _usage(self) -> tuple[int, int]:
        return (
            sum(item.accelerator_units for item in self._active.values()),
            sum(item.cpu_units for item in self._active.values()),
        )

    def _fits(self, request: LocalResourceRequest) -> bool:
        accelerator_used, cpu_used = self._usage()
        accelerator_units, cpu_units = self._required_units(request)
        return (
            accelerator_used + accelerator_units <= self._accelerator_capacity
            and cpu_used + cpu_units <= self._cpu_capacity
        )

    def _next_admissible_id(self) -> int | None:
        now = time.monotonic()
        ordered = sorted(
            self._waiting.values(),
            key=lambda queued: (
                max(
                    int(LocalResourcePriority.INTERACTIVE),
                    int(queued.request.priority) - int((now - queued.enqueued_at) / 30.0),
                ),
                queued.request_id,
            ),
        )
        for queued in ordered:
            if self._fits(queued.request):
                return queued.request_id
        return None

    def _snapshot_locked(self) -> LocalResourceSnapshot:
        now = time.monotonic()
        accelerator_used, cpu_used = self._usage()

        def item(
            queued: _QueuedRequest,
            accelerator_units: int,
            cpu_units: int,
            since: float,
        ) -> LocalResourceItemSnapshot:
            return LocalResourceItemSnapshot(
                request_id=queued.request_id,
                workload=queued.request.workload,
                label=queued.request.label,
                priority=queued.request.priority.name.lower(),
                memory_class=queued.request.memory_class.value,
                accelerator_units=accelerator_units,
                cpu_units=cpu_units,
                age_s=round(max(0.0, now - since), 3),
            )

        active = tuple(
            item(
                entry.queued,
                entry.accelerator_units,
                entry.cpu_units,
                entry.acquired_at,
            )
            for entry in sorted(self._active.values(), key=lambda value: value.queued.request_id)
        )
        waiting = tuple(
            item(
                queued,
                *self._required_units(queued.request),
                queued.enqueued_at,
            )
            for queued in sorted(self._waiting.values(), key=lambda value: value.request_id)
        )
        return LocalResourceSnapshot(
            budget=self._budget,
            accelerator_capacity=self._accelerator_capacity,
            accelerator_used=accelerator_used,
            cpu_capacity=self._cpu_capacity,
            cpu_used=cpu_used,
            active=active,
            waiting=waiting,
        )


_broker = LocalModelResourceBroker()


def get_local_model_resource_broker() -> LocalModelResourceBroker:
    return _broker


def configure_local_model_resources(settings_or_budget: object) -> LocalModelResourceBroker:
    """Apply the global policy from Settings or a direct budget string."""
    if isinstance(settings_or_budget, str):
        budget = settings_or_budget
    else:
        budget = str(
            getattr(settings_or_budget, "local_model_resource_budget", "")
            or getattr(settings_or_budget, "audio_memory_budget", "medium")
        )
    _broker.configure(budget)
    return _broker


__all__ = [
    "LocalMemoryClass",
    "LocalModelResourceBroker",
    "LocalResourceItemSnapshot",
    "LocalResourceLease",
    "LocalResourcePriority",
    "LocalResourceRequest",
    "LocalResourceSnapshot",
    "configure_local_model_resources",
    "get_local_model_resource_broker",
]
