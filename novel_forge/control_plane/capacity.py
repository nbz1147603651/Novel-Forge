"""Capacity scheduler - priority-based admission control.

Uses :class:`RoutingHealthRegistry` to enforce capacity reservation:
- P0: local commits, recovery, verification, archival (always admitted)
- P1: user-initiated prose generation, necessary repair, core TTS
- P2: planning, necessary quality checks
- P3: critic, repeated evaluation, polish, humanize, macro audit, ambient audio

When health capacity is below the P0/P1 channel threshold, P3 tasks are
rejected. When all providers are unavailable, tasks enter degraded mode
(retry_wait) rather than hard failure.

Only explicitly-allowed deterministic local steps may continue committing
in degraded mode - never silently skip Canon hard gates.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from novel_forge.control_plane.enums import AssuranceLevel, Priority
from novel_forge.control_plane.health import (
    AdmitDecision,
    RoutingHealthRegistry,
    get_global_health_registry,
)

if TYPE_CHECKING:
    pass

_log = logging.getLogger("novel_forge.control_plane.capacity")

# Task types that are allowed to commit in degraded mode (deterministic local steps)
_DEGRADED_COMMIT_ALLOWED: frozenset[str] = frozenset(
    {
        "resolve_chapter_checkpoint_finalize",  # Local commit of already-generated content
        "export_book",  # Read-only export
        "rebuild_memory_vectors",  # Local vector rebuild
    }
)


class CapacityScheduler:
    """Priority-based capacity scheduler.

    Wraps :class:`RoutingHealthRegistry` to provide admission decisions
    for job submissions. The scheduler is advisory: it returns decisions
    but does not itself block or queue tasks.
    """

    def __init__(self, registry: RoutingHealthRegistry) -> None:
        self._registry = registry

    def can_admit(
        self,
        priority: Priority | str,
        task_type: str = "",
        provider: str = "",
    ) -> AdmitDecision:
        """Check if a task can be admitted based on priority and health."""
        return self._registry.can_admit(priority, task_type, provider)

    def should_degrade_commit(self, job_kind: str) -> bool:
        """Check if a job kind is allowed to commit in degraded mode.

        Only deterministic local steps may continue committing when
        providers are unavailable. Creative generation, repair, and
        Canon writes are NEVER allowed to commit in degraded mode.
        """
        return job_kind in _DEGRADED_COMMIT_ALLOWED

    def admit_or_degrade(
        self,
        job_kind: str,
        priority: Priority,
        task_type: str = "",
        provider: str = "",
    ) -> AdmitDecision:
        """Admit a job, or return a degraded decision if health is low.

        For P0 jobs that are allowed to commit in degraded mode, always
        returns admitted=True even if providers are down.

        For P1+ jobs, checks health and may return admitted=False or
        admitted=True with degraded assurance.
        """
        decision = self.can_admit(priority, task_type, provider)

        # P0 degraded-commit-allowed jobs always proceed
        if priority == Priority.P0 and self.should_degrade_commit(job_kind):
            if not decision.admitted:
                return AdmitDecision(
                    admitted=True,
                    reason=f"P0 {job_kind} admitted in degraded mode (local commit)",
                    assurance=AssuranceLevel.DEGRADED,
                    degraded_reason=decision.degraded_reason or "provider unavailable",
                )

        return decision


# Process-level singleton
_global_scheduler: CapacityScheduler | None = None
_global_scheduler_lock = threading.Lock()


def get_global_capacity_scheduler() -> CapacityScheduler:
    """Return the process-wide CapacityScheduler singleton."""
    global _global_scheduler
    with _global_scheduler_lock:
        if _global_scheduler is None:
            _global_scheduler = CapacityScheduler(get_global_health_registry())
        return _global_scheduler
