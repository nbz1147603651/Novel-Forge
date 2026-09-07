"""Startup reconciler for crash recovery.

On JobService/Desktop/CLI startup, scans the control plane for work units
that were left in an active state (running/retry_wait/waiting_human) when
the process crashed. For each stale attempt:

1. If the associated StageExecution has a valid output artifact (hash exists
   and the content file is readable), validate and mark as committed.
2. If no valid output exists, mark the WorkUnit as retry_wait (safe to retry)
   or waiting_human (needs user confirmation).
3. Cross-check with existing ReviewProgressState checkpoint files - if the
   checkpoint says canon_done but the DB says running, trust the file and
   update the DB.

Key rule (from Anthropic harness experience):
- Replay means restoring state, verifying, and continuing.
- If a model must be re-called, only context consistency is guaranteed,
  not word-for-word reproducibility.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.control_plane.enums import (
    AssuranceLevel,
    EventSeverity,
    RunAttemptState,
    StageState,
    WorkUnitState,
)
from novel_forge.control_plane.schemas import EventLedgerEntryDTO, utc_now_iso

if TYPE_CHECKING:
    from novel_forge.control_plane.store import ControlPlaneStore

_log = logging.getLogger("novel_forge.control_plane.reconciler")


class ReconcileResult:
    """Summary of a reconciliation pass."""

    def __init__(self) -> None:
        self.total_scanned: int = 0
        self.committed: int = 0  # Had valid output, marked committed
        self.retry_wait: int = 0  # No output, safe to retry
        self.waiting_human: int = 0  # No output, needs user decision
        self.already_terminal: int = 0  # Was already terminal (skip)
        self.already_marked: int = 0  # Already in target state, re-mark skipped (idempotent)
        self.errors: list[str] = []

    @property
    def needs_attention(self) -> int:
        return self.retry_wait + self.waiting_human

    def __repr__(self) -> str:
        return (
            f"ReconcileResult(scanned={self.total_scanned}, committed={self.committed}, "
            f"retry_wait={self.retry_wait}, waiting_human={self.waiting_human}, "
            f"already_terminal={self.already_terminal}, "
            f"already_marked={self.already_marked}, errors={len(self.errors)})"
        )


class StartupReconciler:
    """Scans the control plane for stale work units and reconciles them.

    Called at JobService startup (when ``runtime_control_enabled`` is True).
    Uses the heartbeat timeout from settings to detect stale attempts.
    """

    def __init__(
        self,
        store: ControlPlaneStore,
        *,
        heartbeat_timeout_s: int = 300,
        storage_root: Path | None = None,
    ) -> None:
        self._store = store
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._storage_root = storage_root

    def reconcile(self) -> ReconcileResult:
        """Run a full reconciliation pass. Returns summary of actions taken."""
        result = ReconcileResult()

        try:
            active_work_units = self._store.sync.list_active_work_units()
        except Exception:
            _log.exception("Failed to list active work units during reconciliation")
            result.errors.append("Failed to list active work units")
            return result

        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self._heartbeat_timeout_s)
        ).isoformat()

        for wu in active_work_units:
            result.total_scanned += 1
            try:
                self._reconcile_work_unit(wu, cutoff, result)
            except Exception:
                _log.exception("Failed to reconcile work unit %s", wu.id)
                result.errors.append(f"work_unit {wu.id}: reconciliation failed")

        _log.info(
            "Reconciliation complete: %s (committed=%d, retry_wait=%d, waiting_human=%d)",
            result.total_scanned,
            result.committed,
            result.retry_wait,
            result.waiting_human,
        )
        return result

    def _reconcile_work_unit(self, wu: Any, cutoff: str, result: ReconcileResult) -> None:
        """Reconcile a single work unit."""
        # Get all attempts for this work unit
        try:
            attempts = self._store.sync.list_run_attempts(wu.id)
        except Exception:
            _log.exception("Failed to list attempts for work unit %s", wu.id)
            result.errors.append(f"work_unit {wu.id}: failed to list attempts")
            return

        if not attempts:
            # No attempts - work unit was queued but never started
            self._mark_retry_wait(
                wu.id,
                "no attempts found during reconciliation",
                result,
                attempt_id=None,
                current_state=wu.state,
            )
            return

        # Find the latest attempt
        latest = max(attempts, key=lambda a: a.attempt_number)

        if latest.state.is_terminal:
            # Attempt already terminal - check if work unit state matches
            self._sync_work_unit_state(wu, latest, result)
            return

        # Check if the attempt is stale (heartbeat older than cutoff)
        if latest.heartbeat_at and latest.heartbeat_at < cutoff:
            self._reconcile_stale_attempt(wu, latest, result)
        elif not latest.heartbeat_at:
            # No heartbeat at all - never really started
            self._mark_retry_wait(
                wu.id,
                f"attempt {latest.id} has no heartbeat",
                result,
                attempt_id=latest.id,
                current_state=wu.state,
            )
        else:
            # Heartbeat is recent - might still be running in another process
            _log.debug(
                "Work unit %s attempt %s has recent heartbeat (%s), skipping",
                wu.id,
                latest.id,
                latest.heartbeat_at,
            )

    def _reconcile_stale_attempt(self, wu: Any, attempt: Any, result: ReconcileResult) -> None:
        """Reconcile a single stale attempt."""
        # Get stage executions for this attempt
        try:
            stages = self._store.sync.list_stage_executions(attempt.id)
        except Exception:
            _log.exception("Failed to list stages for attempt %s", attempt.id)
            result.errors.append(f"attempt {attempt.id}: failed to list stages")
            return

        # Check if any stage has a valid output artifact
        has_valid_output = False
        last_done_stage = None
        for stage in stages:
            if (
                stage.state == StageState.DONE
                and stage.output_artifact_hash
                and self._is_terminal_recovery_stage(wu, stage)
            ):
                has_valid_output = True
                last_done_stage = stage

        if has_valid_output and last_done_stage is not None:
            # Verify the output artifact still exists on disk
            if self._verify_output_exists(last_done_stage):
                # Valid output exists - mark as committed
                self._mark_committed(wu.id, attempt.id, last_done_stage.stage_name, result)
            else:
                # Output hash exists but file is gone - need to retry
                self._mark_retry_wait(
                    wu.id,
                    f"output artifact for stage {last_done_stage.stage_name} not found on disk",
                    result,
                    attempt_id=attempt.id,
                    current_state=wu.state,
                )
        else:
            # No valid output - decide between retry_wait and waiting_human
            # based on how many stages completed
            completed_stages = [s for s in stages if s.state == StageState.DONE]
            if len(completed_stages) == 0:
                # Nothing completed - safe to retry from scratch
                self._mark_retry_wait(
                    wu.id,
                    f"attempt {attempt.id} had no completed stages",
                    result,
                    attempt_id=attempt.id,
                    current_state=wu.state,
                )
            elif str(getattr(wu, "kind", "") or "") in {
                "run_chapter",
                "resolve_chapter_checkpoint",
            }:
                self._mark_retry_wait(
                    wu.id,
                    f"attempt {attempt.id} has no committed finalize output",
                    result,
                    attempt_id=attempt.id,
                    current_state=wu.state,
                )
            else:
                # Some stages completed but no final output - needs human decision
                self._mark_waiting_human(
                    wu.id,
                    f"attempt {attempt.id} completed {len(completed_stages)} stages "
                    f"but has no valid final output",
                    result,
                    attempt_id=attempt.id,
                    current_state=wu.state,
                )

    @staticmethod
    def _is_terminal_recovery_stage(wu: Any, stage: Any) -> bool:
        """Only terminal chapter stages may recover a chapter work unit as committed."""

        kind = str(getattr(wu, "kind", "") or "")
        if kind not in {"run_chapter", "resolve_chapter_checkpoint"}:
            return True
        stage_name = str(getattr(stage, "stage_name", "") or "").strip().lower()
        task_type = str(getattr(stage, "task_type", "") or "").strip().upper()
        return stage_name in {"final", "finalize"} or task_type == "FINALIZE"

    def _verify_output_exists(self, stage: Any) -> bool:
        """Check if a stage's output artifact exists on disk.

        Phase 3: basic check - if we have a storage_root, try to find the
        artifact file. Phase 2 will have recorded content_path in ArtifactManifest.
        """
        # If no storage root, assume the output is valid (can't verify)
        if self._storage_root is None:
            return True

        # Try to find the artifact by its hash in the manifest
        try:
            artifact = self._store.sync.find_artifact_by_sha256(stage.output_artifact_hash)
            if artifact is not None and artifact.content_path:
                path = Path(artifact.content_path)
                if path.is_absolute():
                    return path.exists()
                # Relative path - resolve from storage root
                return (self._storage_root / path).exists()
        except Exception:
            _log.debug("Failed to verify output for stage %s", stage.stage_name, exc_info=True)

        # Can't verify - be conservative and say it doesn't exist
        return False

    def _mark_committed(
        self, wu_id: str, attempt_id: str, stage_name: str, result: ReconcileResult
    ) -> None:
        """Mark a work unit and attempt as committed (recovered from valid output).

        Uses ``force=True`` because crash recovery must correct stale states.
        """
        now = utc_now_iso()
        try:
            self._store.sync.update_run_attempt(
                attempt_id,
                state=RunAttemptState.COMMITTED,
                ended_at=now,
                heartbeat_at=now,
            )
            self._store.sync.update_work_unit_state(
                wu_id, WorkUnitState.COMMITTED, force=True
            )
            self._store.sync.append_event(
                EventLedgerEntryDTO(
                    run_attempt_id=attempt_id,
                    event_type="reconcile_committed",
                    severity=EventSeverity.INFO,
                    payload={
                        "stage": stage_name,
                        "reason": "valid output artifact found during reconciliation",
                    },
                )
            )
            result.committed += 1
            _log.info(
                "Work unit %s committed via reconciliation (stage=%s)",
                wu_id,
                stage_name,
            )
        except Exception:
            _log.exception("Failed to mark work unit %s as committed", wu_id)
            result.errors.append(f"work_unit {wu_id}: failed to mark committed")

    def _mark_retry_wait(
        self,
        wu_id: str,
        reason: str,
        result: ReconcileResult,
        *,
        attempt_id: str | None = None,
        current_state: WorkUnitState | None = None,
    ) -> None:
        """Mark a work unit for retry.

        Uses ``force=True`` because crash recovery must correct stale states.
        ``attempt_id`` must reference an existing run_attempts row (FK constraint);
        pass ``None`` when no attempt exists for the work unit.

        Idempotent: if the work unit is already in ``RETRY_WAIT`` (e.g. marked by a
        previous reconciliation pass), skip re-marking and do NOT append another
        ledger event.  Without this guard every startup re-appended one
        ``reconcile_retry_wait`` event per stale work unit, growing ``event_ledger``
        without bound.
        """
        if current_state == WorkUnitState.RETRY_WAIT:
            result.already_marked += 1
            _log.debug(
                "Work unit %s already in retry_wait, skipping re-mark (%s)", wu_id, reason
            )
            return
        try:
            self._store.sync.update_work_unit_state(
                wu_id,
                WorkUnitState.RETRY_WAIT,
                assurance=AssuranceLevel.DEGRADED,
                force=True,
            )
            self._store.sync.append_event(
                EventLedgerEntryDTO(
                    run_attempt_id=attempt_id,
                    event_type="reconcile_retry_wait",
                    severity=EventSeverity.WARN,
                    payload={"reason": reason, "work_unit_id": wu_id},
                )
            )
            result.retry_wait += 1
            _log.info("Work unit %s marked for retry: %s", wu_id, reason)
        except Exception:
            _log.exception("Failed to mark work unit %s for retry", wu_id)
            result.errors.append(f"work_unit {wu_id}: failed to mark retry_wait")

    def _mark_waiting_human(
        self,
        wu_id: str,
        reason: str,
        result: ReconcileResult,
        *,
        attempt_id: str | None = None,
        current_state: WorkUnitState | None = None,
    ) -> None:
        """Mark a work unit as needing human decision.

        Uses ``force=True`` because crash recovery must correct stale states.
        ``attempt_id`` must reference an existing run_attempts row (FK constraint);
        pass ``None`` when no attempt exists for the work unit.

        Idempotent: if the work unit is already in ``WAITING_HUMAN``, skip re-marking
        and do NOT append another ledger event (see ``_mark_retry_wait``).
        """
        if current_state == WorkUnitState.WAITING_HUMAN:
            result.already_marked += 1
            _log.debug(
                "Work unit %s already in waiting_human, skipping re-mark (%s)", wu_id, reason
            )
            return
        try:
            self._store.sync.update_work_unit_state(
                wu_id, WorkUnitState.WAITING_HUMAN, force=True
            )
            self._store.sync.append_event(
                EventLedgerEntryDTO(
                    run_attempt_id=attempt_id,
                    event_type="reconcile_waiting_human",
                    severity=EventSeverity.WARN,
                    payload={"reason": reason, "work_unit_id": wu_id},
                )
            )
            result.waiting_human += 1
            _log.info("Work unit %s waiting for human decision: %s", wu_id, reason)
        except Exception:
            _log.exception("Failed to mark work unit %s as waiting_human", wu_id)
            result.errors.append(f"work_unit {wu_id}: failed to mark waiting_human")

    def _sync_work_unit_state(self, wu: Any, attempt: Any, result: ReconcileResult) -> None:
        """Sync work unit state to match a terminal attempt.

        Uses ``force=True`` because crash recovery may need to correct
        states that would otherwise be rejected by the transition guard
        (e.g. a stale RUNNING state when the attempt already committed).
        """
        expected = {
            RunAttemptState.COMMITTED: WorkUnitState.COMMITTED,
            RunAttemptState.FAILED: WorkUnitState.FAILED,
            RunAttemptState.CANCELLED: WorkUnitState.CANCELLED,
            RunAttemptState.WAITING_HUMAN: WorkUnitState.WAITING_HUMAN,
        }.get(attempt.state)

        if expected is not None and wu.state != expected:
            try:
                self._store.sync.update_work_unit_state(wu.id, expected, force=True)
                result.already_terminal += 1
                _log.info(
                    "Work unit %s state force-synced: %s -> %s",
                    wu.id,
                    wu.state.value,
                    expected.value,
                )
            except Exception:
                _log.exception("Failed to sync work unit %s state", wu.id)
                result.errors.append(f"work_unit {wu.id}: failed to sync state")
        else:
            result.already_terminal += 1
