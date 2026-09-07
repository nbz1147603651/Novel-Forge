"""Shadow recorder for WorkUnit/Attempt dual-write.

Records job lifecycle events to the control plane *without* affecting
existing JobService behavior. All methods swallow exceptions and log -
shadow writes must never break the main pipeline.

Lifecycle mapping (JobState -> control plane):
  QUEUED        -> WorkUnit(queued)
  RUNNING       -> WorkUnit(running) + heartbeat update
  SUCCEEDED     -> RunAttempt(committed) + WorkUnit(committed)
  FAILED        -> RunAttempt(failed) + WorkUnit(failed)
  PAUSED        -> RunAttempt(waiting_human) + WorkUnit(waiting_human)
  cancelled     -> RunAttempt(cancelled) + WorkUnit(cancelled)
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from novel_forge.control_plane.enums import (
    AssuranceLevel,
    Priority,
    RunAttemptState,
    WorkUnitState,
)
from novel_forge.control_plane.schemas import (
    EventLedgerEntryDTO,
    RunAttemptDTO,
    WorkUnitDTO,
    utc_now_iso,
)

if TYPE_CHECKING:
    from novel_forge.app_service.contracts import JobCommand, JobRecord
    from novel_forge.control_plane.store import ControlPlaneStore

_log = logging.getLogger("novel_forge.control_plane.shadow")


# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------

_JOB_KIND_PRIORITY: dict[str, Priority] = {
    # P0: local commits, recovery, verification
    "resolve_chapter_checkpoint_finalize": Priority.P0,
    "resolve_chapter_checkpoint": Priority.P0,
    # P1: user-initiated prose generation, necessary repair, core TTS
    "run_chapter": Priority.P1,
    "run_short": Priority.P1,
    "init_long": Priority.P1,
    "prepare_chapter": Priority.P1,
    "repair_continuity": Priority.P1,
    "repair_causal": Priority.P1,
    "repair_issues": Priority.P1,
    "polish_chapter": Priority.P1,
    "tts_synthesize": Priority.P1,
    "tts_full_pipeline": Priority.P1,
    # P2: planning, necessary quality checks
    "reevaluate_chapter": Priority.P2,
    "sync_chapter_contracts": Priority.P2,
    "extend_outline": Priority.P2,
    "planning_horizon": Priority.P2,
    "semantic_consistency": Priority.P1,
    "polish_outline": Priority.P2,
    "book_consistency": Priority.P2,
    "book_editorial_audit": Priority.P2,
    "global_repair_queue": Priority.P1,
    "reextract_relationships": Priority.P2,
    "repair_motif_history": Priority.P2,
    "rebuild_memory_vectors": Priority.P2,
}

# JobKinds that are "write" operations (must match _WRITE_JOB_KINDS)
_WRITE_JOB_KINDS: frozenset[str] = frozenset(
    {
        "init_long",
        "run_short",
        "run_chapter",
        "prepare_chapter",
        "reevaluate_chapter",
        "repair_continuity",
        "repair_issues",
        "polish_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "repair_causal",
        "reextract_relationships",
        "repair_motif_history",
        "rebuild_memory_vectors",
        "book_consistency",
        "book_editorial_audit",
        "global_repair_queue",
        "sync_chapter_contracts",
        "extend_outline",
        "polish_outline",
        "tts_synthesize",
        "tts_full_pipeline",
    }
)


def _priority_for_kind(kind: str) -> Priority:
    return _JOB_KIND_PRIORITY.get(kind, Priority.P1)


def _is_write_kind(kind: str) -> bool:
    return kind in _WRITE_JOB_KINDS


def _compute_idempotency_key(kind: str, project_id: str, payload: dict[str, Any]) -> str:
    """Stable idempotency key for a job submission.

    Format: ``{kind}:{project_id}:{sha256(payload)[:16]}``
    """
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{project_id}:{payload_hash}"


# ---------------------------------------------------------------------------
# ShadowRecorder
# ---------------------------------------------------------------------------


class ShadowRecorder:
    """Shadow-writes job lifecycle to the control plane.

    Wraps a :class:`ControlPlaneStore` (or ``None`` when disabled).
    Every method is fire-and-forget: exceptions are logged but never
    propagated to the caller, so the main pipeline is unaffected.
    """

    def __init__(self, store: ControlPlaneStore | None) -> None:
        self._store = store
        # Maps job_id -> (work_unit_id, run_attempt_id) for tracking.  A
        # queued WorkUnit deliberately has an empty attempt id: a RunAttempt
        # represents an execution that has actually started, not a queued job.
        self._mapping: dict[str, tuple[str, str]] = {}

    @property
    def enabled(self) -> bool:
        return self._store is not None

    def _sync(self) -> Any:
        """Return the SyncFacade, or None if disabled."""
        if self._store is None:
            return None
        return self._store.sync

    # ------------------------------------------------------------------
    # Submit (job queued)
    # ------------------------------------------------------------------

    def on_submit(
        self,
        job_id: str,
        command: JobCommand,
        record: JobRecord,
        *,
        intent_payload_path: str = "",
    ) -> None:
        """Create a queued WorkUnit when a write-kind job is submitted."""
        if self._store is None:
            return
        kind = record.kind.value if hasattr(record.kind, "value") else str(record.kind)
        if not _is_write_kind(kind):
            return
        try:
            idempotency_key = _compute_idempotency_key(kind, record.project_id, command.payload)
            existing = self._store.sync.get_work_unit(job_id)
            if existing is None:
                # Idempotency guard: if an ACTIVE work unit with the same intent
                # (idempotency_key) already exists under a different job_id, reuse it
                # instead of inserting a duplicate row.  Previously each resubmit of a
                # failed job carried a fresh job_id, so the lookup above always missed
                # and a new work_units row was created every time — a runaway loop could
                # then accumulate thousands of duplicate rows for one logical intent.
                duplicate = (
                    self._store.sync.find_by_idempotency_key(idempotency_key)
                    if idempotency_key
                    else None
                )
                if (
                    duplicate is not None
                    and duplicate.id != job_id
                    and not duplicate.state.is_terminal
                ):
                    _log.info(
                        "Shadow submit reused active work unit %s (state=%s) for "
                        "idempotency key %s; skipped duplicate creation for job %s",
                        duplicate.id,
                        duplicate.state.value,
                        idempotency_key,
                        job_id,
                    )
                    return
                wu = WorkUnitDTO(
                    id=job_id,
                    kind=kind,
                    project_id=record.project_id,
                    priority=_priority_for_kind(kind),
                    state=WorkUnitState.QUEUED,
                    idempotency_key=idempotency_key,
                    label=record.label,
                    intent_payload_path=intent_payload_path,
                )
                self._store.sync.create_work_unit(wu)
            elif existing.state == WorkUnitState.RETRY_WAIT:
                # A recovered WorkUnit keeps its intent identity and starts a
                # new RunAttempt only once the worker actually runs.
                self._store.sync.update_work_unit_state(job_id, WorkUnitState.QUEUED)
            else:
                _log.warning(
                    "Shadow submit ignored for existing non-resumable work unit %s (state=%s)",
                    job_id,
                    existing.state.value,
                )
                return

            self._mapping[job_id] = (job_id, "")
        except Exception:
            _log.exception("ShadowRecorder.on_submit failed for job %s", job_id)

    # ------------------------------------------------------------------
    # Started (worker begins running)
    # ------------------------------------------------------------------

    def on_started(self, job_id: str) -> None:
        """Create the actual attempt and mark its WorkUnit running."""
        if self._store is None:
            return
        try:
            mapping = self._mapping.get(job_id)
            if mapping is None:
                # A process may have loaded a queued WorkUnit from disk before
                # its in-memory mapping was rebuilt.
                if self._store.sync.get_work_unit(job_id) is None:
                    return
                mapping = (job_id, "")
                self._mapping[job_id] = mapping
            work_unit_id, current_attempt_id = mapping
            if not current_attempt_id:
                attempts = self._store.sync.list_run_attempts(work_unit_id)
                attempt_number = max((attempt.attempt_number for attempt in attempts), default=0) + 1
                attempt = RunAttemptDTO(
                    id=f"{job_id}_a{attempt_number}",
                    work_unit_id=work_unit_id,
                    attempt_number=attempt_number,
                    state=RunAttemptState.RUNNING,
                    heartbeat_at=utc_now_iso(),
                )
                self._store.sync.create_run_attempt(attempt)
                current_attempt_id = attempt.id
                self._mapping[job_id] = (work_unit_id, current_attempt_id)
            self._store.sync.update_work_unit_state(
                job_id, WorkUnitState.RUNNING, heartbeat_at=utc_now_iso()
            )
        except Exception:
            _log.exception("ShadowRecorder.on_started failed for job %s", job_id)

    # ------------------------------------------------------------------
    # Step progress (heartbeat)
    # ------------------------------------------------------------------

    def on_step(self, job_id: str, step: str) -> None:
        """Update heartbeat on each step progress callback."""
        if self._store is None:
            return
        mapping = self._mapping.get(job_id)
        if mapping is None:
            return
        _, attempt_id = mapping
        if not attempt_id:
            return
        try:
            self._store.sync.update_run_attempt(attempt_id, heartbeat_at=utc_now_iso())
        except Exception:
            # Don't log every step failure - too noisy
            pass

    # ------------------------------------------------------------------
    # Terminal states
    # ------------------------------------------------------------------

    def on_succeeded(self, job_id: str) -> None:
        """Mark RunAttempt + WorkUnit as committed."""
        if self._store is None:
            return
        mapping = self._mapping.get(job_id)
        attempt_id = mapping[1] if mapping else ""
        now = utc_now_iso()
        try:
            if attempt_id:
                self._store.sync.update_run_attempt(
                    attempt_id,
                    state=RunAttemptState.COMMITTED,
                    ended_at=now,
                    heartbeat_at=now,
                )
            self._store.sync.update_work_unit_state(job_id, WorkUnitState.COMMITTED)
        except Exception:
            _log.exception("ShadowRecorder.on_succeeded failed for job %s", job_id)

    def on_failed(self, job_id: str, error_kind: str, error_summary: dict[str, Any]) -> None:
        """Mark RunAttempt + WorkUnit as failed."""
        if self._store is None:
            return
        mapping = self._mapping.get(job_id)
        attempt_id = mapping[1] if mapping else ""
        now = utc_now_iso()
        try:
            if attempt_id:
                self._store.sync.update_run_attempt(
                    attempt_id,
                    state=RunAttemptState.FAILED,
                    error_kind=error_kind,
                    error_summary=error_summary,
                    ended_at=now,
                    heartbeat_at=now,
                )
            self._store.sync.update_work_unit_state(job_id, WorkUnitState.FAILED)
        except Exception:
            _log.exception("ShadowRecorder.on_failed failed for job %s", job_id)

    def on_paused(self, job_id: str) -> None:
        """Mark RunAttempt + WorkUnit as waiting_human (checkpoint pause)."""
        if self._store is None:
            return
        mapping = self._mapping.get(job_id)
        attempt_id = mapping[1] if mapping else ""
        now = utc_now_iso()
        try:
            if attempt_id:
                self._store.sync.update_run_attempt(
                    attempt_id,
                    state=RunAttemptState.WAITING_HUMAN,
                    heartbeat_at=now,
                )
            self._store.sync.update_work_unit_state(job_id, WorkUnitState.WAITING_HUMAN)
        except Exception:
            _log.exception("ShadowRecorder.on_paused failed for job %s", job_id)

    def on_cancelled(self, job_id: str, reason: str) -> None:
        """Mark RunAttempt + WorkUnit as cancelled."""
        if self._store is None:
            return
        mapping = self._mapping.get(job_id)
        attempt_id = mapping[1] if mapping else ""
        now = utc_now_iso()
        try:
            if attempt_id:
                self._store.sync.update_run_attempt(
                    attempt_id,
                    state=RunAttemptState.CANCELLED,
                    ended_at=now,
                    heartbeat_at=now,
                )
            self._store.sync.update_work_unit_state(job_id, WorkUnitState.CANCELLED)
        except Exception:
            _log.exception("ShadowRecorder.on_cancelled failed for job %s", job_id)

    # ------------------------------------------------------------------
    # Degraded mode (provider health)
    # ------------------------------------------------------------------

    def mark_degraded(self, job_id: str, reason: str) -> None:
        """Set WorkUnit assurance to degraded (does not change run state)."""
        if self._store is None:
            return
        try:
            mapping = self._mapping.get(job_id)
            attempt_id = mapping[1] if mapping else ""
            self._store.sync.update_work_unit_state(
                job_id,
                WorkUnitState.RETRY_WAIT,
                assurance=AssuranceLevel.DEGRADED,
            )
            if attempt_id:
                self._store.sync.update_run_attempt(
                    attempt_id,
                    state=RunAttemptState.RETRY_WAIT,
                    heartbeat_at=utc_now_iso(),
                )
                self._store.sync.append_event(
                    EventLedgerEntryDTO(
                        run_attempt_id=attempt_id,
                        event_type="degraded_mode",
                        severity="warn",
                        payload={"reason": reason},
                    )
                )
        except Exception:
            _log.exception("ShadowRecorder.mark_degraded failed for job %s", job_id)

    def attempt_id_for(self, job_id: str) -> str:
        """Return the active RunAttempt id for an already-started JobService job."""

        mapping = self._mapping.get(job_id)
        return mapping[1] if mapping is not None else ""
