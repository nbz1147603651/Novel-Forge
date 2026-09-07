"""Work unit resume and idempotent submit.

Provides:
- :func:`check_idempotent_submit`: returns a cached result if a WorkUnit
  with the same idempotency_key has already been committed.
- :class:`WorkUnitResumer`: rebuilds a JobCommand from a recovered WorkUnit
  and resubmits it to JobService.

Key rule: replay means restoring state and continuing. If a model must be
re-called, only context consistency is guaranteed, not word-for-word
reproducibility (Anthropic harness experience).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.app_service.contracts import JobCommand
from novel_forge.control_plane.enums import WorkUnitState

if TYPE_CHECKING:
    from novel_forge.app_service.job_service import JobService
    from novel_forge.control_plane.store import ControlPlaneStore

_log = logging.getLogger("novel_forge.control_plane.resume")


def choice_of(decision: dict[str, Any]) -> str:
    """Return the non-empty choice of a decision payload, else ''."""
    return str(decision.get("choice", "") or "").strip()


#: Work-unit states that may be rebuilt into a resumable command.
_RESUMABLE_STATES = {WorkUnitState.RETRY_WAIT, WorkUnitState.WAITING_HUMAN}


class IdempotencyCheckResult:
    """Result of an idempotency check."""

    def __init__(
        self,
        *,
        is_duplicate: bool,
        existing_work_unit_id: str = "",
        state: WorkUnitState | None = None,
    ) -> None:
        self.is_duplicate = is_duplicate
        self.existing_work_unit_id = existing_work_unit_id
        self.state = state

    @property
    def should_skip_submit(self) -> bool:
        """True if the submit should be skipped (already committed)."""
        return self.is_duplicate and self.state == WorkUnitState.COMMITTED

    @property
    def is_active_duplicate(self) -> bool:
        """True if a duplicate WorkUnit exists in a non-terminal (active) state.

        An active duplicate (queued/running/retry_wait/waiting_human) means the same
        intent is already tracked by the control plane.  Callers must REUSE the
        existing work unit rather than creating a new row — otherwise a retry loop
        can accumulate thousands of duplicate ``work_units`` rows for one intent.
        """
        return self.is_duplicate and self.state is not None and not self.state.is_terminal


def check_idempotent_submit(
    store: ControlPlaneStore,
    idempotency_key: str,
) -> IdempotencyCheckResult:
    """Check if a WorkUnit with the given idempotency_key already exists.

    Returns:
        - If a committed WorkUnit exists: ``should_skip_submit=True``
        - If an active WorkUnit exists: ``is_duplicate=True`` and
          ``is_active_duplicate=True`` but ``should_skip_submit=False``.  The caller
          should reuse the existing in-flight work unit instead of creating a new
          row (the shadow recorder enforces this at the work-unit level).
        - If no WorkUnit exists: ``is_duplicate=False``
    """
    if not idempotency_key:
        return IdempotencyCheckResult(is_duplicate=False)

    try:
        wu = store.sync.find_by_idempotency_key(idempotency_key)
        if wu is None:
            return IdempotencyCheckResult(is_duplicate=False)
        return IdempotencyCheckResult(
            is_duplicate=True,
            existing_work_unit_id=wu.id,
            state=wu.state,
        )
    except Exception:
        _log.exception("Failed to check idempotency for key %s", idempotency_key)
        return IdempotencyCheckResult(is_duplicate=False)


class WorkUnitResumer:
    """Rebuilds and resubmits WorkUnits that need resumption.

    Used after StartupReconciler marks work units as retry_wait or
    waiting_human. The resumer can:
    - Rebuild a JobCommand from the WorkUnit's intent payload
    - Submit it to JobService for re-execution
    """

    def __init__(self, store: ControlPlaneStore) -> None:
        self._store = store

    def list_resumable_work_units(self) -> list[Any]:
        """Return all work units in retry_wait state."""
        try:
            active = self._store.sync.list_active_work_units()
            return [wu for wu in active if wu.state == WorkUnitState.RETRY_WAIT]
        except Exception:
            _log.exception("Failed to list resumable work units")
            return []

    def list_awaiting_human_work_units(self) -> list[Any]:
        """Return all work units paused on a human gate (waiting_human).

        These are the "第 N 章确认后继续" style gates: the flow is paused
        until the author provides a decision, and may outlive an app restart.
        """
        try:
            active = self._store.sync.list_active_work_units()
            return [wu for wu in active if wu.state == WorkUnitState.WAITING_HUMAN]
        except Exception:
            _log.exception("Failed to list waiting-human work units")
            return []

    def get_work_unit(self, work_unit_id: str) -> Any | None:
        """Get a work unit by ID."""
        try:
            return self._store.sync.get_work_unit(work_unit_id)
        except Exception:
            _log.exception("Failed to get work unit %s", work_unit_id)
            return None

    def rebuild_command(self, work_unit_id: str) -> JobCommand | None:
        """Rebuild a resumable command from the project-owned intent envelope.

        Covers both crash recovery (``RETRY_WAIT``) and human-gate replay
        (``WAITING_HUMAN``): the latter rebuilds after the author's decision
        was recorded into the durable intent payload via
        :meth:`record_decision`, so the replayed run continues past the gate
        instead of asking again.
        """

        work_unit = self.get_work_unit(work_unit_id)
        if work_unit is None or work_unit.state not in _RESUMABLE_STATES:
            return None
        raw_path = str(getattr(work_unit, "intent_payload_path", "") or "").strip()
        if not raw_path:
            _log.warning("WorkUnit %s has no persisted intent payload", work_unit_id)
            return None
        try:
            payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("intent payload must be an object")
            payload["job_id"] = work_unit_id
            return JobCommand.model_validate(payload)
        except (OSError, ValueError, TypeError):
            _log.exception("Failed to rebuild command for WorkUnit %s", work_unit_id)
            return None

    def resume(self, job_service: JobService, work_unit_id: str) -> Any | None:
        """Submit a recovered WorkUnit as its next attempt, if it is resumable."""

        command = self.rebuild_command(work_unit_id)
        if command is None:
            return None
        return job_service.submit(command)

    def record_decision(self, work_unit_id: str, decision: dict[str, Any]) -> bool:
        """Persist a human decision into the durable intent payload.

        The decision is appended to ``metadata.resolved_decisions`` of the
        persisted JobCommand envelope; on replay the job worker preseeds its
        decision provider with these entries so the flow continues past the
        gate (e.g. "第 N 章确认后继续") without asking the author again.
        """

        work_unit = self.get_work_unit(work_unit_id)
        if work_unit is None or work_unit.state != WorkUnitState.WAITING_HUMAN:
            return False
        raw_path = str(getattr(work_unit, "intent_payload_path", "") or "").strip()
        if not raw_path or not choice_of(decision):
            return False
        try:
            payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("intent payload must be an object")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                payload["metadata"] = metadata
            resolved = metadata.get("resolved_decisions")
            if not isinstance(resolved, list):
                resolved = []
                metadata["resolved_decisions"] = resolved
            resolved.append({key: value for key, value in decision.items() if value is not None})
            Path(raw_path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except (OSError, ValueError, TypeError):
            _log.exception("Failed to record decision for WorkUnit %s", work_unit_id)
            return False

    def resume_after_decision(
        self,
        job_service: JobService,
        work_unit_id: str,
        decision: dict[str, Any],
    ) -> Any | None:
        """Record the author's decision and resume the gated work unit.

        Covers the "确认第 N 章后继续" interaction surviving an app restart:
        the paused WAITING_HUMAN unit receives the decision durably, then is
        resubmitted; the replayed run consumes the decision from its
        preseeded provider instead of blocking on the UI again.
        """

        if not self.record_decision(work_unit_id, decision):
            return None
        return self.resume(job_service, work_unit_id)

    def mark_resumed(self, work_unit_id: str) -> None:
        """Mark a work unit as queued for its next attempt.

        This transitions a ``RETRY_WAIT`` unit to ``QUEUED``, matching the
        state that :meth:`ShadowRecorder.on_submit` applies when a recovered
        unit is resubmitted.  Call this only if you need to update the control
        plane without going through :meth:`resume` (which already handles the
        transition via ``JobService.submit`` -> ``on_submit``).
        """
        try:
            self._store.sync.update_work_unit_state(work_unit_id, WorkUnitState.QUEUED)
        except Exception:
            _log.exception("Failed to mark work unit %s as queued", work_unit_id)
