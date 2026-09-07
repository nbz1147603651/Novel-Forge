"""Unified artifact manifest for project workflows.

The manifest is intentionally small and boring: workflow code remains the
source of behavior, while this module records enough state to decide whether
an artifact, a reusable failure, or a repair round can be trusted on resume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable

MANIFEST_FILENAME = "artifact_manifest.json"
MANIFEST_SCHEMA_VERSION = 2

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_SKIPPED = "skipped"
STATUS_BLOCKED = "blocked"
STATUS_NEEDS_REPAIR = "needs_repair"
STATUS_FAILED = "failed"
STATUS_REPAIRED = "repaired"

REUSABLE_STATUSES = frozenset(
    {
        STATUS_SUCCEEDED,
        STATUS_SKIPPED,
        STATUS_BLOCKED,
        STATUS_NEEDS_REPAIR,
        STATUS_REPAIRED,
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(raw) for key, raw in value.items() if str(key).strip()}


def _plain_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_int(value: Any, *, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return max(minimum, default)


def _matches_hashes(recorded: dict[str, str], expected: dict[str, str] | None) -> bool:
    if expected is None or not expected:
        return True
    if not recorded:
        return False
    return all(str(recorded.get(key) or "") == str(value) for key, value in expected.items())


@dataclass(frozen=True)
class ArtifactRecord:
    artifact: str
    workflow: str = ""
    step: str = ""
    status: str = STATUS_SUCCEEDED
    input_hashes: dict[str, str] = field(default_factory=dict)
    output_hashes: dict[str, str] = field(default_factory=dict)
    output_version: int = 0
    input_signature: str = "legacy_unknown"
    parent_artifact_versions: dict[str, int] = field(default_factory=dict)
    schema_version: int = 1
    workflow_version: str = "legacy_unknown"
    quality_status: str = "legacy_unknown"
    degradation_reason: str = ""
    derivation_status: str = "legacy_unknown"
    reuse_policy: str = "same_run_resume"
    run_attempt_id: str = ""
    paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    reusable_failure: bool = False
    repair_rounds: dict[str, int] = field(default_factory=dict)
    updated_at: str = ""

    @classmethod
    def from_payload(cls, artifact: str, payload: Any) -> "ArtifactRecord" | None:
        if not isinstance(payload, dict):
            return None
        artifact_key = str(payload.get("artifact") or artifact).strip()
        if not artifact_key:
            return None
        repair_rounds: dict[str, int] = {}
        raw_rounds = payload.get("repair_rounds")
        if isinstance(raw_rounds, dict):
            for issue_id, round_value in raw_rounds.items():
                try:
                    repair_rounds[str(issue_id)] = max(0, int(round_value))
                except (TypeError, ValueError):
                    continue
        parent_versions: dict[str, int] = {}
        raw_parent_versions = payload.get("parent_artifact_versions")
        if isinstance(raw_parent_versions, dict):
            for parent_id, version in raw_parent_versions.items():
                try:
                    parent_versions[str(parent_id)] = max(0, int(version))
                except (TypeError, ValueError):
                    continue
        return cls(
            artifact=artifact_key,
            workflow=str(payload.get("workflow") or "").strip(),
            step=str(payload.get("step") or "").strip(),
            status=str(payload.get("status") or "").strip() or STATUS_SUCCEEDED,
            input_hashes=_string_dict(payload.get("input_hashes")),
            output_hashes=_string_dict(payload.get("output_hashes")),
            output_version=_safe_int(payload.get("output_version"), default=0),
            input_signature=str(payload.get("input_signature") or "legacy_unknown"),
            parent_artifact_versions=parent_versions,
            schema_version=_safe_int(payload.get("schema_version"), default=1, minimum=1),
            workflow_version=str(payload.get("workflow_version") or "legacy_unknown"),
            quality_status=str(payload.get("quality_status") or "legacy_unknown"),
            degradation_reason=str(payload.get("degradation_reason") or ""),
            derivation_status=str(payload.get("derivation_status") or "legacy_unknown"),
            reuse_policy=str(payload.get("reuse_policy") or "same_run_resume"),
            run_attempt_id=str(payload.get("run_attempt_id") or ""),
            paths=_string_dict(payload.get("paths")),
            metadata=_plain_dict(payload.get("metadata")),
            reusable_failure=bool(payload.get("reusable_failure", False)),
            repair_rounds=repair_rounds,
            updated_at=str(payload.get("updated_at") or "").strip(),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "workflow": self.workflow,
            "step": self.step,
            "status": self.status,
            "input_hashes": dict(self.input_hashes),
            "output_hashes": dict(self.output_hashes),
            "output_version": self.output_version,
            "input_signature": self.input_signature,
            "parent_artifact_versions": dict(self.parent_artifact_versions),
            "schema_version": self.schema_version,
            "workflow_version": self.workflow_version,
            "quality_status": self.quality_status,
            "degradation_reason": self.degradation_reason,
            "derivation_status": self.derivation_status,
            "reuse_policy": self.reuse_policy,
            "run_attempt_id": self.run_attempt_id,
            "paths": dict(self.paths),
            "metadata": dict(self.metadata),
            "reusable_failure": self.reusable_failure,
            "repair_rounds": dict(self.repair_rounds),
            "updated_at": self.updated_at,
        }

    def matches(
        self,
        *,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
        statuses: Iterable[str] | None = None,
        allow_reusable_failure: bool = False,
        input_signature: str | None = None,
        run_attempt_id: str | None = None,
    ) -> bool:
        allowed_statuses = {str(item) for item in statuses} if statuses is not None else None
        if allowed_statuses is not None and self.status not in allowed_statuses:
            return False
        if self.status in {STATUS_BLOCKED, STATUS_NEEDS_REPAIR, STATUS_FAILED}:
            if not allow_reusable_failure:
                return False
            if not self.reusable_failure:
                return False
        if self.derivation_status in {"stale", "conflict"}:
            return False
        if self.derivation_status == "blocked" and not (
            allow_reusable_failure and self.reusable_failure
        ):
            return False
        if input_signature is not None and self.input_signature != input_signature:
            return False
        if self.reuse_policy == "same_run_resume" and run_attempt_id is not None:
            if not self.run_attempt_id or self.run_attempt_id != run_attempt_id:
                return False
        return _matches_hashes(self.input_hashes, input_hashes) and _matches_hashes(
            self.output_hashes,
            output_hashes,
        )


class ArtifactManifest:
    """Project-local record of workflow artifacts and resume-safe failures."""

    def __init__(self, storage: Any, layout: Any) -> None:
        self.storage = storage
        self.layout = layout
        self.path = layout.states_dir / MANIFEST_FILENAME
        self._records: dict[str, ArtifactRecord] | None = None

    def _load(self) -> dict[str, ArtifactRecord]:
        if self._records is not None:
            return self._records
        records: dict[str, ArtifactRecord] = {}
        try:
            exists = self.storage.exists(self.path)
        except Exception:
            exists = self.path.exists()
        if exists:
            try:
                payload = self.storage.load_json(self.path)
            except Exception:
                payload = {}
            raw_records = payload.get("artifacts") if isinstance(payload, dict) else {}
            if isinstance(raw_records, dict):
                for artifact, raw_record in raw_records.items():
                    record = ArtifactRecord.from_payload(str(artifact), raw_record)
                    if record is not None:
                        records[record.artifact] = record
        self._records = records
        return records

    def _reload_for_update(self) -> dict[str, ArtifactRecord]:
        """Reload the latest manifest before a read-modify-write mutation.

        A workflow callback and the finalization service may legitimately own
        different ``ArtifactManifest`` instances for the same project.  Keeping
        an instance cache is useful for reads, but using that cache as the base
        of a later write can discard records written by the other instance.
        Normal workflow mutations are serialized by the existing project lock;
        reloading here therefore closes the in-process lost-update window
        without introducing another lock or state store.
        """

        self._records = None
        return self._load()

    def _save(self) -> None:
        records = self._load()
        self.storage.save_json(
            self.path,
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "updated_at": _utc_now(),
                "artifacts": {
                    artifact: record.to_payload()
                    for artifact, record in sorted(records.items(), key=lambda item: item[0])
                },
            },
        )

    def get(self, artifact: str) -> ArtifactRecord | None:
        return self._load().get(str(artifact))

    def matching_record(
        self,
        artifact: str,
        *,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
        statuses: Iterable[str] | None = REUSABLE_STATUSES,
        allow_reusable_failure: bool = True,
        input_signature: str | None = None,
        run_attempt_id: str | None = None,
    ) -> ArtifactRecord | None:
        record = self.get(artifact)
        if record is None:
            return None
        if record.matches(
            input_hashes=input_hashes,
            output_hashes=output_hashes,
            statuses=statuses,
            allow_reusable_failure=allow_reusable_failure,
            input_signature=input_signature,
            run_attempt_id=run_attempt_id,
        ):
            return record
        return None

    def record(
        self,
        *,
        artifact: str,
        workflow: str,
        step: str,
        status: str,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
        output_version: int | None = None,
        paths: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        reusable_failure: bool = False,
        input_signature: str = "legacy_unknown",
        parent_artifact_versions: dict[str, int] | None = None,
        schema_version: int = 1,
        workflow_version: str = "legacy_unknown",
        quality_status: str = "legacy_unknown",
        degradation_reason: str = "",
        derivation_status: str = "fresh",
        reuse_policy: str = "same_run_resume",
        run_attempt_id: str = "",
    ) -> ArtifactRecord:
        records = self._reload_for_update()
        existing = records.get(str(artifact))
        normalized_output_hashes = _string_dict(output_hashes)
        if output_version is None:
            if existing is None:
                resolved_output_version = 1 if normalized_output_hashes else 0
            elif normalized_output_hashes and normalized_output_hashes != existing.output_hashes:
                resolved_output_version = max(1, existing.output_version + 1)
            else:
                resolved_output_version = existing.output_version
        else:
            resolved_output_version = max(0, int(output_version))
        record = ArtifactRecord(
            artifact=str(artifact),
            workflow=str(workflow),
            step=str(step),
            status=str(status),
            input_hashes=_string_dict(input_hashes),
            output_hashes=normalized_output_hashes,
            output_version=resolved_output_version,
            input_signature=str(input_signature or "legacy_unknown"),
            parent_artifact_versions={
                str(key): max(0, int(value))
                for key, value in (parent_artifact_versions or {}).items()
            },
            schema_version=max(1, int(schema_version or 1)),
            workflow_version=str(workflow_version or "legacy_unknown"),
            quality_status=str(quality_status or "legacy_unknown"),
            degradation_reason=str(degradation_reason or ""),
            derivation_status=str(derivation_status or "fresh"),
            reuse_policy=str(reuse_policy or "same_run_resume"),
            run_attempt_id=str(run_attempt_id or ""),
            paths=_string_dict(paths),
            metadata=_plain_dict(metadata),
            reusable_failure=bool(reusable_failure),
            repair_rounds=dict(existing.repair_rounds) if existing else {},
            updated_at=_utc_now(),
        )
        records[record.artifact] = record
        self._save()
        return record

    def record_success(
        self,
        *,
        artifact: str,
        workflow: str,
        step: str,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
        paths: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = STATUS_SUCCEEDED,
        input_signature: str = "legacy_unknown",
        parent_artifact_versions: dict[str, int] | None = None,
        schema_version: int = 1,
        workflow_version: str = "legacy_unknown",
        quality_status: str = "actual",
        degradation_reason: str = "",
        derivation_status: str = "fresh",
        reuse_policy: str = "same_run_resume",
        run_attempt_id: str = "",
    ) -> ArtifactRecord:
        return self.record(
            artifact=artifact,
            workflow=workflow,
            step=step,
            status=status,
            input_hashes=input_hashes,
            output_hashes=output_hashes,
            paths=paths,
            metadata=metadata,
            reusable_failure=False,
            input_signature=input_signature,
            parent_artifact_versions=parent_artifact_versions,
            schema_version=schema_version,
            workflow_version=workflow_version,
            quality_status=quality_status,
            degradation_reason=degradation_reason,
            derivation_status=derivation_status,
            reuse_policy=reuse_policy,
            run_attempt_id=run_attempt_id,
        )

    def record_failure(
        self,
        *,
        artifact: str,
        workflow: str,
        step: str,
        status: str,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
        paths: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        reusable_failure: bool = True,
        input_signature: str = "legacy_unknown",
        parent_artifact_versions: dict[str, int] | None = None,
        schema_version: int = 1,
        workflow_version: str = "legacy_unknown",
        quality_status: str = "blocked",
        degradation_reason: str = "",
        derivation_status: str = "blocked",
        reuse_policy: str = "same_run_resume",
        run_attempt_id: str = "",
    ) -> ArtifactRecord:
        return self.record(
            artifact=artifact,
            workflow=workflow,
            step=step,
            status=status,
            input_hashes=input_hashes,
            output_hashes=output_hashes,
            paths=paths,
            metadata=metadata,
            reusable_failure=reusable_failure,
            input_signature=input_signature,
            parent_artifact_versions=parent_artifact_versions,
            schema_version=schema_version,
            workflow_version=workflow_version,
            quality_status=quality_status,
            degradation_reason=degradation_reason,
            derivation_status=derivation_status,
            reuse_policy=reuse_policy,
            run_attempt_id=run_attempt_id,
        )

    def record_step(
        self,
        *,
        workflow: str,
        step: str,
        metadata: dict[str, Any] | None = None,
        status: str = STATUS_RUNNING,
    ) -> ArtifactRecord:
        return self.record(
            artifact=f"workflow:{workflow}:latest_step",
            workflow=workflow,
            step=step,
            status=status,
            metadata=metadata,
            reusable_failure=False,
        )

    def record_repair_round(
        self,
        *,
        artifact: str,
        workflow: str,
        issue_ids: Iterable[str],
        round_index: int,
    ) -> ArtifactRecord:
        records = self._reload_for_update()
        artifact_key = str(artifact)
        record = records.get(artifact_key) or ArtifactRecord(
            artifact=artifact_key,
            workflow=workflow,
            step="repair",
            status=STATUS_REPAIRED,
            updated_at=_utc_now(),
        )
        rounds = dict(record.repair_rounds)
        safe_round = max(0, int(round_index or 0))
        for issue_id in issue_ids:
            issue_key = str(issue_id or "").strip()
            if issue_key:
                rounds[issue_key] = max(rounds.get(issue_key, 0), safe_round)
        updated = ArtifactRecord(
            artifact=record.artifact,
            workflow=record.workflow or workflow,
            step=record.step,
            status=record.status,
            input_hashes=dict(record.input_hashes),
            output_hashes=dict(record.output_hashes),
            output_version=record.output_version,
            input_signature=record.input_signature,
            parent_artifact_versions=dict(record.parent_artifact_versions),
            schema_version=record.schema_version,
            workflow_version=record.workflow_version,
            quality_status=record.quality_status,
            degradation_reason=record.degradation_reason,
            derivation_status=record.derivation_status,
            reuse_policy=record.reuse_policy,
            run_attempt_id=record.run_attempt_id,
            paths=dict(record.paths),
            metadata=dict(record.metadata),
            reusable_failure=record.reusable_failure,
            repair_rounds=rounds,
            updated_at=_utc_now(),
        )
        records[artifact_key] = updated
        self._save()
        return updated


def manifest_for_context(ctx: Any) -> ArtifactManifest:
    return ArtifactManifest(ctx.storage, ctx.layout)
