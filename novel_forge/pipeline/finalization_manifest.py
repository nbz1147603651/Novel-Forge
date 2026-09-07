"""Naming and persistence helpers for chapter finalization artifacts.

The existing project-local :class:`ArtifactManifest` remains the only recovery
ledger.  This module merely standardizes phase keys and signatures shared by
Pipeline and Workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.pipeline.artifact_manifest import (
    STATUS_NEEDS_REPAIR,
    STATUS_SUCCEEDED,
    ArtifactManifest,
    ArtifactRecord,
)

FINALIZATION_WORKFLOW = "chapter_finalize"
FINALIZATION_WORKFLOW_VERSION = "novel.chapter.finalize.v3"
FINALIZATION_PHASES = frozenset(
    {
        "final_text",
        "reports",
        "narrative_state",
        "story_kernel",
        "publication_projection",
        "memory",
        "archive_committed",
        "planning_horizon",
        "future_planning",
        "post_archive_tts",
    }
)


def chapter_finalization_artifact(chapter_number: int, phase: str) -> str:
    clean_phase = str(phase or "").strip()
    if clean_phase not in FINALIZATION_PHASES:
        raise ValueError(f"Unknown chapter finalization phase: {phase}")
    return f"chapter:{int(chapter_number)}:{clean_phase}"


def chapter_finalization_signature(chapter_number: int, text_hash: str) -> str:
    return f"chapter:{int(chapter_number)}:{str(text_hash or '').strip()}"


def matching_finalization_phase(
    manifest: ArtifactManifest,
    *,
    chapter_number: int,
    phase: str,
    text_hash: str,
) -> ArtifactRecord | None:
    return manifest.matching_record(
        chapter_finalization_artifact(chapter_number, phase),
        input_hashes={"text": text_hash},
        statuses=(STATUS_SUCCEEDED,),
        allow_reusable_failure=False,
        input_signature=chapter_finalization_signature(chapter_number, text_hash),
    )


def tracked_finalization_ready(
    storage: Any, layout: Any, chapter_number: int, *, require_state: bool = True,
    allow_legacy: bool = True,
) -> bool:
    """Do not advance a partially committed new archive as if it were legacy."""
    from novel_forge.core.utils.text_hash import source_text_hash

    if not layout.chapter_path(chapter_number).is_file():
        return False
    manifest = ArtifactManifest(storage, layout)
    phases = ("final_text", "reports", "narrative_state", "story_kernel")
    if allow_legacy and not any(manifest.get(chapter_finalization_artifact(chapter_number, phase))
                                for phase in phases):
        # Imported/old archives predate the ledger; callers still check their
        # canon watermark, exit state and staleness. Never infer this from one
        # missing phase of a tracked archive.
        return True
    text_hash = source_text_hash(storage.load_text(layout.chapter_path(chapter_number)))
    required = phases if require_state else phases[:2]
    return all(matching_finalization_phase(manifest, chapter_number=chapter_number,
                                          phase=phase, text_hash=text_hash) is not None
               for phase in required)


def authoring_chapter_wait_reason(root: Path, chapter: int) -> str:
    """One workflow-state barrier for execution and both authoring projections."""
    from novel_forge.persistence.authoring_store import AuthoringStore

    if chapter <= 1 or AuthoringStore(root).policy() is None:
        return ""
    from novel_forge.core.config import get_settings
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.persistence.project_staleness import canon_watermark, scoped_stale_chapters

    previous = chapter - 1
    storage = FileSystemStorage(root.parent)
    layout = ProjectLayout(root)
    if not layout.chapter_path(previous).is_file() or canon_watermark(storage, layout) < previous:
        return f"请先验收并归档第 {previous} 章，再推进第 {chapter} 章；可继续讨论后续规划"
    if any(number <= previous for number in scoped_stale_chapters(storage, layout)):
        return f"第 {previous} 章或其上游已变化，请先完成修订重验，再推进第 {chapter} 章"
    if not tracked_finalization_ready(
        storage, layout, previous,
        require_state=get_settings().narrative_state_required, allow_legacy=False,
    ):
        return f"第 {previous} 章必需状态尚未完成提交，请先恢复归档，再推进第 {chapter} 章"
    return ""


def require_authoring_chapter_predecessor(root: Path, chapter: int, action: str) -> None:
    # Planning ahead is permitted, but chapter execution must use committed canon.
    if action in {"prepare", "generate", "archive"}:
        if waiting := authoring_chapter_wait_reason(root, chapter):
            from novel_forge.persistence.authoring_store import AuthoringDeniedError

            raise AuthoringDeniedError(waiting)


def record_finalization_success(
    manifest: ArtifactManifest,
    *,
    chapter_number: int,
    phase: str,
    text_hash: str,
    output_hashes: dict[str, str] | None = None,
    paths: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
    quality_status: str = "actual",
    degradation_reason: str = "",
) -> ArtifactRecord:
    return manifest.record_success(
        artifact=chapter_finalization_artifact(chapter_number, phase),
        workflow=FINALIZATION_WORKFLOW,
        step=phase,
        input_hashes={"text": text_hash},
        output_hashes=output_hashes or {phase: text_hash},
        paths=paths,
        metadata=metadata,
        input_signature=chapter_finalization_signature(chapter_number, text_hash),
        schema_version=2,
        workflow_version=FINALIZATION_WORKFLOW_VERSION,
        quality_status=quality_status,
        degradation_reason=degradation_reason,
        derivation_status="fresh",
        reuse_policy="signature_cache",
    )


def record_finalization_pending(
    manifest: ArtifactManifest,
    *,
    chapter_number: int,
    phase: str,
    text_hash: str,
    error: BaseException | str,
    metadata: dict[str, Any] | None = None,
) -> ArtifactRecord:
    error_type = type(error).__name__ if isinstance(error, BaseException) else "PendingError"
    error_text = str(error)
    return manifest.record_failure(
        artifact=chapter_finalization_artifact(chapter_number, phase),
        workflow=FINALIZATION_WORKFLOW,
        step=phase,
        status=STATUS_NEEDS_REPAIR,
        input_hashes={"text": text_hash},
        metadata={
            "error_type": error_type,
            "error": error_text[:1000],
            **(metadata or {}),
        },
        reusable_failure=True,
        input_signature=chapter_finalization_signature(chapter_number, text_hash),
        schema_version=2,
        workflow_version=FINALIZATION_WORKFLOW_VERSION,
        quality_status="degraded",
        degradation_reason=error_text[:500],
        derivation_status="blocked",
        reuse_policy="signature_cache",
    )


__all__ = [
    "FINALIZATION_PHASES",
    "FINALIZATION_WORKFLOW",
    "authoring_chapter_wait_reason",
    "chapter_finalization_artifact",
    "chapter_finalization_signature",
    "matching_finalization_phase",
    "record_finalization_pending",
    "record_finalization_success",
    "require_authoring_chapter_predecessor",
]
