"""Runtime Control Plane enums.

Separates run-state from assurance-level so that a "degraded" provider
condition is never conflated with a "failed" execution state.
"""

from __future__ import annotations

from enum import Enum


class WorkUnitState(str, Enum):
    """Lifecycle state of a user-intent WorkUnit."""

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    WAITING_HUMAN = "waiting_human"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """True if the WorkUnit will no longer transition."""
        return self in {
            WorkUnitState.COMMITTED,
            WorkUnitState.CANCELLED,
            WorkUnitState.FAILED,
        }

    @property
    def is_active(self) -> bool:
        """True if the WorkUnit is non-terminal (may need reconciliation).

        Matches :meth:`ControlPlaneStore.list_active_work_units` which
        queries the same four states.
        """
        return self in {
            WorkUnitState.QUEUED,
            WorkUnitState.RUNNING,
            WorkUnitState.RETRY_WAIT,
            WorkUnitState.WAITING_HUMAN,
        }

    def can_transition_to(self, target: "WorkUnitState") -> bool:
        """Return True if transitioning from *self* to *target* is valid."""
        return target in _VALID_WORK_UNIT_TRANSITIONS.get(self, frozenset())


# Valid state transitions.  Terminal states (COMMITTED, CANCELLED, FAILED)
# have no outgoing edges.  QUEUED and RUNNING may transition to most
# non-terminal states; RETRY_WAIT and WAITING_HUMAN may resume to QUEUED
# or be cancelled.
_VALID_WORK_UNIT_TRANSITIONS: dict[WorkUnitState, frozenset[WorkUnitState]] = {
    WorkUnitState.QUEUED: frozenset(
        {WorkUnitState.RUNNING, WorkUnitState.CANCELLED}
    ),
    WorkUnitState.RUNNING: frozenset(
        {
            WorkUnitState.COMMITTED,
            WorkUnitState.FAILED,
            WorkUnitState.CANCELLED,
            WorkUnitState.RETRY_WAIT,
            WorkUnitState.WAITING_HUMAN,
        }
    ),
    WorkUnitState.RETRY_WAIT: frozenset(
        {WorkUnitState.QUEUED, WorkUnitState.CANCELLED}
    ),
    # A paused WorkUnit may resume (QUEUED), be abandoned (CANCELLED), or
    # be resolved externally and committed / failed directly.
    WorkUnitState.WAITING_HUMAN: frozenset(
        {
            WorkUnitState.QUEUED,
            WorkUnitState.CANCELLED,
            WorkUnitState.COMMITTED,
            WorkUnitState.FAILED,
        }
    ),
    # Terminal states: no valid outgoing transitions.
    WorkUnitState.COMMITTED: frozenset(),
    WorkUnitState.CANCELLED: frozenset(),
    WorkUnitState.FAILED: frozenset(),
}


class RunAttemptState(str, Enum):
    """Lifecycle state of a single RunAttempt within a WorkUnit."""

    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    WAITING_HUMAN = "waiting_human"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {
            RunAttemptState.COMMITTED,
            RunAttemptState.CANCELLED,
            RunAttemptState.FAILED,
        }


class StageState(str, Enum):
    """Lifecycle state of a single stage execution."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class AssuranceLevel(str, Enum):
    """Assurance level - orthogonal to run-state.

    ``normal`` = all providers healthy, full quality pipeline.
    ``degraded`` = provider/capacity shortage; only explicitly-allowed
    deterministic local steps may continue committing.
    """

    NORMAL = "normal"
    DEGRADED = "degraded"


class Priority(str, Enum):
    """Scheduling priority for capacity reservation.

    P0: local commits, recovery, verification, archival.
    P1: user-initiated prose generation, necessary repair, core TTS.
    P2: planning, necessary quality checks.
    P3: critic, repeated evaluation, polish, humanize, macro audit, ambient audio.
    """

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"

    @property
    def rank(self) -> int:
        """Lower rank = higher priority (admitted first, protected last)."""
        return {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2, Priority.P3: 3}[self]


class ResourceKind(str, Enum):
    """Kind of project resource a commit targets."""

    CHAPTER = "chapter"
    CANON = "canon"
    REPORT = "report"
    TTS_AUDIO = "tts_audio"
    VOICE_LIB = "voice_lib"


class ArtifactKind(str, Enum):
    """Kind of immutable artifact recorded in the manifest."""

    CHAPTER_SOURCE_SLICE = "chapter_source_slice"
    STAGE_ARTIFACT = "stage_artifact"
    MODEL_CALL = "model_call"
    CHAPTER_TEXT = "chapter_text"
    REPORT_JSON = "report_json"
    TTS_AUDIO = "tts_audio"
    INIT_SOURCE = "init_source"


class EventSeverity(str, Enum):
    """Severity levels for EventLedger entries."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"
