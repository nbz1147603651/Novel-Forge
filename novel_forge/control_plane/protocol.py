"""OrchestrationBackend Protocol — pluggable execution backend abstraction.

Defines the lifecycle-hook interface that the pipeline and JobService use to
report job progress.  The current production implementation is
:class:`~novel_forge.control_plane.shadow.ShadowRecorder` (embedded SQLite).

Future implementations (e.g. Temporal, Restate, DBOS) need only satisfy this
Protocol to drop in as the orchestration backend without changing any pipeline
or JobService caller code.

Usage::

    from novel_forge.control_plane.protocol import OrchestrationBackend

    def configure_orchestration(backend: OrchestrationBackend) -> None:
        # JobService and pipeline call these hooks; they don't know
        # whether the backend is SQLite, Temporal, or a no-op stub.
        ...

Design constraints:
- All methods are **fire-and-forget** from the caller's perspective: they
  must not raise into the pipeline (implementations swallow/log errors).
- ``on_submit`` is called once per job creation; ``on_started`` once per
  execution attempt; terminal hooks (``on_succeeded``/``on_failed``/
  ``on_cancelled``) exactly once.
- ``attempt_id_for`` returns a stable identifier for the current execution
  attempt (used for event correlation and FK references).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class JobCommandView(Protocol):
    """Minimal command projection required by orchestration backends."""

    payload: dict[str, Any]


class JobRecordView(Protocol):
    """Minimal job projection required by orchestration backends."""

    kind: Any
    project_id: str
    label: str


@runtime_checkable
class OrchestrationBackend(Protocol):
    """Lifecycle-hook interface for pluggable orchestration backends.

    Implementations record job state transitions for auditing, reconciliation,
    and (in future distributed modes) durable execution coordination.

    The current embedded implementation is ``ShadowRecorder`` (SQLite-backed).
    A future Temporal adapter would translate these hooks into Temporal
    workflow/activity signals without changing any caller.
    """

    @property
    def enabled(self) -> bool:
        """Whether this backend is active (False = no-op stub)."""
        ...

    def on_submit(
        self,
        job_id: str,
        command: JobCommandView,
        record: JobRecordView,
        *,
        intent_payload_path: str = "",
    ) -> None:
        """Record that a new job has been submitted (queued)."""
        ...

    def on_started(self, job_id: str) -> None:
        """Record that a worker has begun executing the job."""
        ...

    def on_step(self, job_id: str, step: str) -> None:
        """Record an incremental progress step within the job."""
        ...

    def on_succeeded(self, job_id: str) -> None:
        """Record that the job completed successfully."""
        ...

    def on_failed(self, job_id: str, error_kind: str, error_summary: dict[str, Any]) -> None:
        """Record that the job failed with a classified error."""
        ...

    def on_paused(self, job_id: str) -> None:
        """Record that the job was paused (e.g. awaiting human decision)."""
        ...

    def on_cancelled(self, job_id: str, reason: str) -> None:
        """Record that the job was cancelled by the user or system."""
        ...

    def mark_degraded(self, job_id: str, reason: str) -> None:
        """Record that the job completed in a degraded state."""
        ...

    def attempt_id_for(self, job_id: str) -> str:
        """Return the current run-attempt identifier for event correlation."""
        ...


class NullOrchestrationBackend:
    """No-op backend used when the control plane is disabled.

    Satisfies :class:`OrchestrationBackend` without any I/O.  This is the
    default when ``runtime_control_enabled = False``.
    """

    @property
    def enabled(self) -> bool:
        return False

    def on_submit(
        self,
        job_id: str,
        command: JobCommandView,
        record: JobRecordView,
        *,
        intent_payload_path: str = "",
    ) -> None:
        pass

    def on_started(self, job_id: str) -> None:
        pass

    def on_step(self, job_id: str, step: str) -> None:
        pass

    def on_succeeded(self, job_id: str) -> None:
        pass

    def on_failed(self, job_id: str, error_kind: str, error_summary: dict[str, Any]) -> None:
        pass

    def on_paused(self, job_id: str) -> None:
        pass

    def on_cancelled(self, job_id: str, reason: str) -> None:
        pass

    def mark_degraded(self, job_id: str, reason: str) -> None:
        pass

    def attempt_id_for(self, job_id: str) -> str:
        return ""
