"""Workspace helpers for recording workflow progress into the artifact manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    ArtifactManifest,
)
from novel_forge.workspace.execution_result import StepCallback

_WORKFLOW_VERSION_BY_KIND: dict[str, str] = {
    "init_long": "novel.init.v2",
    "run_short": "novel.short.v2",
    "run_chapter": "novel.chapter.v2",
    "prepare_chapter": "novel.chapter.prepare.v2",
    "resolve_chapter_checkpoint": "novel.chapter.finalize.v2",
    "book_consistency": "novel.book-review.v2",
    "global_repair_queue": "novel.book-revision.v2",
}


def _hash_payload(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _manifest_layout(storage: Any, project_id: str) -> ProjectLayout:
    try:
        root = storage.existing_project_dir(project_id)
    except Exception:
        root = storage.ensure_project_dir(project_id)
    layout = ProjectLayout(root)
    layout.ensure_dirs()
    return layout


def manifest_for_project(storage: Any, project_id: str) -> ArtifactManifest | None:
    if storage is None or not project_id:
        return None
    try:
        return ArtifactManifest(storage, _manifest_layout(storage, project_id))
    except Exception:
        return None


def _compact_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        keys = sorted(str(key) for key in payload)[:24]
        compact: dict[str, Any] = {
            "payload_type": "dict",
            "payload_hash": _hash_payload(payload),
            "keys": keys,
        }
        for key in (
            "chapter",
            "chapter_number",
            "status",
            "stage",
            "artifact",
            "verdict",
            "blocked",
            "source",
        ):
            if key in payload:
                compact[key] = payload.get(key)
        return compact
    return {
        "payload_type": type(payload).__name__,
        "payload_hash": _hash_payload(payload),
    }


def wrap_manifest_step_callback(
    storage: Any,
    project_id: str,
    workflow: str,
    callback: StepCallback,
) -> StepCallback:
    manifest = manifest_for_project(storage, project_id)
    if manifest is None:
        return callback

    def _wrapped(step: str, payload: Any) -> None:
        try:
            manifest.record_step(
                workflow=workflow,
                step=str(step or ""),
                metadata=_compact_payload(payload),
            )
        finally:
            if callback is not None:
                callback(step, payload)

    return _wrapped


def record_workflow_result(
    storage: Any,
    project_id: str,
    workflow: str,
    *,
    status: str,
    result: Any = None,
    paths: dict[str, Path | str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    manifest = manifest_for_project(storage, project_id)
    if manifest is None:
        return
    path_payload = {key: str(value) for key, value in (paths or {}).items()}
    output_hashes = {"result": _hash_payload(result)}
    result_payload = (
        result.model_dump(mode="json")
        if hasattr(result, "model_dump")
        else result if isinstance(result, dict) else {}
    )
    result_mapping = result_payload if isinstance(result_payload, dict) else {}
    metadata_mapping = metadata or {}
    quality_status = str(
        result_mapping.get("execution_quality_status")
        or result_mapping.get("quality_status")
        or metadata_mapping.get("quality_status")
        or ("actual" if status == STATUS_SUCCEEDED else "blocked")
    )
    if quality_status not in {"actual", "degraded", "fallback", "blocked"}:
        quality_status = "actual" if status == STATUS_SUCCEEDED else "blocked"
    degradation_reason = str(
        result_mapping.get("degradation_reason")
        or result_mapping.get("degraded_reason")
        or metadata_mapping.get("degradation_reason")
        or ""
    )
    raw_parent_versions = metadata_mapping.get("parent_artifact_versions")
    parent_versions = raw_parent_versions if isinstance(raw_parent_versions, dict) else {}
    manifest.record(
        artifact=f"workflow:{workflow}:result",
        workflow=workflow,
        step="completed" if status == STATUS_SUCCEEDED else "failed",
        status=status,
        output_hashes=output_hashes,
        paths=path_payload,
        metadata=metadata or {},
        reusable_failure=status == STATUS_FAILED,
        input_signature=str(
            result_mapping.get("input_signature")
            or metadata_mapping.get("input_signature")
            or "legacy_unknown"
        ),
        parent_artifact_versions=parent_versions,
        schema_version=2,
        workflow_version=_WORKFLOW_VERSION_BY_KIND.get(workflow, f"{workflow}.v2"),
        quality_status=quality_status,
        degradation_reason=degradation_reason,
        derivation_status="fresh" if status == STATUS_SUCCEEDED else "blocked",
        reuse_policy="same_run_resume",
        run_attempt_id=str(metadata_mapping.get("run_attempt_id") or ""),
    )


def record_workflow_success(
    storage: Any,
    project_id: str,
    workflow: str,
    *,
    result: Any = None,
    paths: dict[str, Path | str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record_workflow_result(
        storage,
        project_id,
        workflow,
        status=STATUS_SUCCEEDED,
        result=result,
        paths=paths,
        metadata=metadata,
    )


def record_workflow_failure(
    storage: Any,
    project_id: str,
    workflow: str,
    exc: BaseException,
    *,
    paths: dict[str, Path | str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    merged_metadata = {
        "error_type": type(exc).__name__,
        "error": str(exc),
        **(metadata or {}),
    }
    record_workflow_result(
        storage,
        project_id,
        workflow,
        status=STATUS_FAILED,
        result=merged_metadata,
        paths=paths,
        metadata=merged_metadata,
    )


def workflow_paths(
    layout: ProjectLayout, workflow: str, *, chapter_number: int | None = None
) -> dict[str, Path]:
    if workflow == "init_long":
        return {
            "spec": layout.spec_path,
            "story_bible": layout.bible_path,
            "character_bible": layout.characters_path,
            "blueprint": layout.blueprint_path,
            "outline": layout.outline_path,
            "chapter_contracts": layout.plans_dir / "chapter_contracts.json",
            "readiness": layout.reports_dir / "init_readiness.json",
        }
    if workflow == "run_short":
        return {
            "spec": layout.spec_path,
            "blueprint": layout.short_blueprint_path(),
            "draft": layout.short_draft_path(0),
            "eval": layout.eval_report_path(),
            "creative": layout.short_creative_report_path(),
        }
    if workflow == "run_chapter" and chapter_number is not None:
        return {
            "chapter": layout.chapter_path(chapter_number),
            "plan": layout.chapter_plan_path(chapter_number),
            "draft": layout.chapter_wave_draft_path(chapter_number),
            "eval": layout.eval_report_path(chapter_number),
            "quality_gate": layout.quality_gate_report_path(chapter_number),
        }
    if workflow == "prepare_chapter" and chapter_number is not None:
        return {
            "session": layout.chapter_session_path(chapter_number),
            "checkpoint": layout.chapter_checkpoint_path(chapter_number),
            "state_packet": layout.chapter_state_packet_path(chapter_number),
            "plan": layout.chapter_plan_path(chapter_number),
        }
    if workflow == "resolve_chapter_checkpoint" and chapter_number is not None:
        return {
            "session": layout.chapter_session_path(chapter_number),
            "checkpoint": layout.chapter_checkpoint_path(chapter_number),
            "chapter": layout.chapter_path(chapter_number),
            "review_progress": layout.chapter_review_progress_path(chapter_number),
            "quality_gate": layout.quality_gate_report_path(chapter_number),
        }
    if workflow == "book_consistency":
        return {
            "audit": layout.reports_dir / "book_consistency_audit.json",
            "latest": layout.reports_dir / "book_consistency_audit_latest.json",
            "repair_report": layout.reports_dir / "book_consistency_repair_report.json",
            "audit_checkpoint": layout.states_dir / "book_consistency_audit_checkpoint.json",
            "repair_checkpoint": layout.states_dir / "book_consistency_repair_checkpoint.json",
        }
    if workflow == "global_repair_queue":
        return {
            "audit": layout.reports_dir / "book_consistency_audit.json",
            "repair_report": layout.reports_dir / "book_consistency_repair_report.json",
            "repair_checkpoint": layout.states_dir / "book_consistency_repair_checkpoint.json",
            "verify_checkpoint": layout.states_dir / "book_consistency_verify_checkpoint.json",
            "global_audit_db": layout.global_audit_db_path,
        }
    return {}


def record_entry_success(
    storage: Any,
    project_id: str,
    workflow: str,
    *,
    result: Any,
    chapter_number: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if storage is None:
        return
    layout = _manifest_layout(storage, project_id)
    record_workflow_success(
        storage,
        project_id,
        workflow,
        result=result,
        paths=workflow_paths(layout, workflow, chapter_number=chapter_number),
        metadata=metadata,
    )


def record_entry_failure(
    storage: Any,
    project_id: str,
    workflow: str,
    exc: BaseException,
    *,
    chapter_number: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if storage is None:
        return
    layout = _manifest_layout(storage, project_id)
    record_workflow_failure(
        storage,
        project_id,
        workflow,
        exc,
        paths=workflow_paths(layout, workflow, chapter_number=chapter_number),
        metadata=metadata,
    )
