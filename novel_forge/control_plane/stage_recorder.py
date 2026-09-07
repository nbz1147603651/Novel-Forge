"""Stage recorder for StageExecution + ArtifactManifest lineage.

Records each pipeline stage's input/output hashes and artifacts to the
control plane, complementing the existing ``persist_stage_artifact`` JSON
files with an immutable, queryable ledger.

Design:
- Wraps :func:`novel_forge.pipeline.long.services.context.source_artifacts.persist_stage_artifact`
  via :meth:`StageRecorder.persist_and_record` - calls the original function
  then records the lineage in the control plane.
- All methods are fire-and-forget (swallow exceptions, log warnings).
- When the control plane is disabled (store=None), behaves as a pure no-op
  pass-through to the original persist function.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from novel_forge.control_plane.enums import ArtifactKind, EventSeverity, StageState
from novel_forge.control_plane.schemas import (
    ArtifactManifestDTO,
    EventLedgerEntryDTO,
    StageExecutionDTO,
    utc_now_iso,
)

if TYPE_CHECKING:
    from novel_forge.control_plane.store import ControlPlaneStore

_log = logging.getLogger("novel_forge.control_plane.stage_recorder")


def _compute_sha256(data: bytes | str) -> str:
    """Compute SHA-256 hex digest of bytes or string."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hash_json(payload: Any) -> str:
    """Deterministic SHA-256 of a JSON-serializable payload."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return _compute_sha256(text)


# ---------------------------------------------------------------------------
# Stage mapping: artifact_type -> (stage_name, task_type_hint)
# ---------------------------------------------------------------------------

_STAGE_NAME_BY_ARTIFACT_TYPE: dict[str, str] = {
    "bridge": "bridge",
    "plan": "plan",
    "scene_draft": "draft",
    "wave": "wave",
    "review": "review",
    "repair": "repair",
    "polish": "polish",
    "humanize": "humanize",
    "final": "finalize",
    "canon_memory": "canon_memory",
}


def stage_name_for_artifact_type(artifact_type: str) -> str | None:
    """Return the declared stage that owns a persisted artifact type."""

    return _STAGE_NAME_BY_ARTIFACT_TYPE.get(artifact_type)


# ---------------------------------------------------------------------------
# StageRecorder
# ---------------------------------------------------------------------------


class StageRecorder:
    """Records StageExecution and ArtifactManifest entries to the control plane.

    Each chapter pipeline stage calls :meth:`begin_stage` when starting and
    :meth:`end_stage` when done. The ``persist_and_record`` method wraps the
    existing ``persist_stage_artifact`` to also record the lineage.
    """

    def __init__(self, store: ControlPlaneStore | None) -> None:
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._store is not None

    def _sync(self) -> Any:
        if self._store is None:
            return None
        return self._store.sync

    # ------------------------------------------------------------------
    # Stage lifecycle
    # ------------------------------------------------------------------

    def begin_stage(
        self,
        run_attempt_id: str,
        stage_name: str,
        task_type: str = "",
        input_artifact_hashes: dict[str, str] | None = None,
        idempotency_key: str = "",
        retry_budget: int = 2,
    ) -> str | None:
        """Record the start of a stage execution. Returns stage_execution_id."""
        if self._store is None:
            return None
        try:
            dto = StageExecutionDTO(
                run_attempt_id=run_attempt_id,
                stage_name=stage_name,
                task_type=task_type,
                input_artifact_hashes=input_artifact_hashes or {},
                idempotency_key=idempotency_key,
                retry_budget=retry_budget,
                state=StageState.RUNNING,
                heartbeat_at=utc_now_iso(),
            )
            self._store.sync.create_stage_execution(dto)
            return dto.id
        except Exception:
            _log.exception("StageRecorder.begin_stage failed for stage %s", stage_name)
            return None

    def end_stage(
        self,
        stage_execution_id: str | None,
        *,
        output_artifact_hash: str = "",
        state: StageState = StageState.DONE,
        retries_used: int = 0,
        committed_version: int = 0,
    ) -> None:
        """Record the completion of a stage execution."""
        if self._store is None or stage_execution_id is None:
            return
        try:
            self._store.sync.update_stage_execution(
                stage_execution_id,
                state=state,
                output_artifact_hash=output_artifact_hash,
                retries_used=retries_used,
                committed_version=committed_version,
                ended_at=utc_now_iso(),
                heartbeat_at=utc_now_iso(),
            )
        except Exception:
            _log.exception("StageRecorder.end_stage failed for stage %s", stage_execution_id)

    def heartbeat(self, stage_execution_id: str | None) -> None:
        """Update heartbeat for a running stage."""
        if self._store is None or stage_execution_id is None:
            return
        try:
            self._store.sync.update_stage_execution(stage_execution_id, heartbeat_at=utc_now_iso())
        except Exception:
            pass  # Don't log heartbeat failures - too noisy

    # ------------------------------------------------------------------
    # Artifact lineage
    # ------------------------------------------------------------------

    def record_artifact(
        self,
        *,
        sha256: str,
        artifact_kind: ArtifactKind,
        project_id: str,
        content_path: str = "",
        content_size: int = 0,
        source_artifact_hashes: dict[str, str] | None = None,
        parent_artifact_id: str = "",
        task_type: str = "",
        route: str = "",
        prompt_hash: str = "",
        response_hash: str = "",
        template_version: str = "",
        model_params: dict[str, Any] | None = None,
        validation_result: dict[str, Any] | None = None,
        parent_artifact_refs: list[str] | None = None,
        source_text_hash: str = "",
        input_signature: str = "legacy_unknown",
        schema_version: int = 1,
        workflow_version: str = "legacy_unknown",
        config_fingerprint: str = "legacy_unknown",
        model_fingerprint: str = "legacy_unknown",
        quality_status: str = "actual",
        degradation_reason: str = "",
        derivation_status: str = "fresh",
        output_version: int = 1,
        created_by_stage_execution_id: str | None = None,
    ) -> str | None:
        """Record an immutable artifact in the manifest. Returns artifact_id."""
        if self._store is None:
            return None
        try:
            dto = ArtifactManifestDTO(
                sha256=sha256,
                artifact_kind=artifact_kind,
                project_id=project_id,
                content_path=content_path,
                content_size=content_size,
                source_artifact_hashes=source_artifact_hashes or {},
                parent_artifact_id=parent_artifact_id,
                task_type=task_type,
                route=route,
                prompt_hash=prompt_hash,
                response_hash=response_hash,
                template_version=template_version,
                model_params=model_params or {},
                validation_result=validation_result or {},
                parent_artifact_refs=parent_artifact_refs or [],
                source_text_hash=source_text_hash,
                input_signature=input_signature,
                schema_version=schema_version,
                workflow_version=workflow_version,
                config_fingerprint=config_fingerprint,
                model_fingerprint=model_fingerprint,
                quality_status=quality_status,
                degradation_reason=degradation_reason,
                derivation_status=derivation_status,
                output_version=output_version,
                created_by_stage_execution_id=created_by_stage_execution_id,
            )
            self._store.sync.record_artifact(dto)
            return dto.id
        except Exception:
            _log.exception("StageRecorder.record_artifact failed")
            return None

    # ------------------------------------------------------------------
    # Convenience: record a stage artifact from persist_stage_artifact output
    # ------------------------------------------------------------------

    def record_stage_artifact(
        self,
        *,
        project_id: str,
        chapter_number: int,
        artifact_type: str,
        artifact_payload: dict[str, Any],
        source_hashes: dict[str, str],
        previous_artifact_id: str = "",
        content_path: str = "",
        run_attempt_id: str = "",
        stage_execution_id: str | None = None,
    ) -> str | None:
        """Record a StageArtifact's lineage in the control plane.

        Called after :func:`persist_stage_artifact` completes. Uses the
        already-computed ``source_hashes`` and the artifact payload to
        create an :class:`ArtifactManifestDTO` entry.
        """
        if self._store is None:
            return None
        try:
            payload_hash = _hash_json(artifact_payload)
            return self.record_artifact(
                sha256=payload_hash,
                artifact_kind=ArtifactKind.STAGE_ARTIFACT,
                project_id=project_id,
                content_path=content_path,
                content_size=len(json.dumps(artifact_payload, ensure_ascii=False).encode("utf-8")),
                source_artifact_hashes=source_hashes,
                parent_artifact_id=previous_artifact_id,
                parent_artifact_refs=list(
                    artifact_payload.get("parent_artifact_versions", {}).keys()
                ),
                source_text_hash=str(artifact_payload.get("source_text_hash", "") or ""),
                input_signature=str(
                    artifact_payload.get("input_signature", "legacy_unknown") or "legacy_unknown"
                ),
                schema_version=int(artifact_payload.get("artifact_schema_version", 1) or 1),
                workflow_version=str(
                    artifact_payload.get("workflow_version", "legacy_unknown") or "legacy_unknown"
                ),
                config_fingerprint=str(
                    artifact_payload.get("config_fingerprint", "legacy_unknown") or "legacy_unknown"
                ),
                model_fingerprint=str(
                    artifact_payload.get("model_fingerprint", "legacy_unknown") or "legacy_unknown"
                ),
                quality_status=str(
                    artifact_payload.get("execution_quality_status", "actual") or "actual"
                ),
                degradation_reason=str(artifact_payload.get("degradation_reason", "") or ""),
                derivation_status=str(
                    artifact_payload.get("derivation_status", "fresh") or "fresh"
                ),
                output_version=int(artifact_payload.get("output_version", 1) or 1),
                task_type=artifact_type,
                created_by_stage_execution_id=stage_execution_id,
            )
        except Exception:
            _log.exception("StageRecorder.record_stage_artifact failed for %s", artifact_type)
            return None

    # ------------------------------------------------------------------
    # Event ledger
    # ------------------------------------------------------------------

    def record_event(
        self,
        *,
        run_attempt_id: str | None = None,
        stage_execution_id: str | None = None,
        event_type: str,
        severity: EventSeverity = EventSeverity.INFO,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append an event to the immutable ledger."""
        if self._store is None:
            return
        try:
            self._store.sync.append_event(
                EventLedgerEntryDTO(
                    run_attempt_id=run_attempt_id,
                    stage_execution_id=stage_execution_id,
                    event_type=event_type,
                    severity=severity,
                    payload=payload or {},
                )
            )
        except Exception:
            _log.exception("StageRecorder.record_event failed for %s", event_type)


# ---------------------------------------------------------------------------
# Module-level convenience: get a StageRecorder from settings
# ---------------------------------------------------------------------------


def get_stage_recorder(settings: Any) -> StageRecorder:
    """Create a StageRecorder bound to the control-plane store (or disabled)."""
    from novel_forge.control_plane.factory import get_control_plane_store

    store = get_control_plane_store(settings)
    return StageRecorder(store)


# Re-export hash helpers for convenience
compute_sha256 = _compute_sha256
hash_json = _hash_json
