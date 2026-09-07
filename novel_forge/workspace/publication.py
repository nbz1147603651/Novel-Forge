"""Stable chapter publication projection and cross-media freshness ledger."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import revision_stale_chapters
from novel_forge.tts.schemas import ChapterTTSMetadata, DubbingScript
from novel_forge.tts.script_integrity import source_text_hash_matches
from novel_forge.workspace.contracts import (
    ChapterPublicationView,
    PublicationDerivationStatus,
    PublicationQualityStatus,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _hash_payload(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_tts_metadata(layout: ProjectLayout, chapter_number: int) -> dict[str, Any] | None:
    payload = _load_json(layout.reports_dir / f"chapter_{chapter_number:03d}_tts_metadata.json")
    if not payload:
        return None
    try:
        return ChapterTTSMetadata.model_validate(payload).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _revision_blockers(
    layout: ProjectLayout,
    chapter_number: int,
    final_text_hash: str,
) -> list[str]:
    status = _load_json(
        layout.states_dir / "final_revision_status" / f"chapter_{chapter_number:03d}.json"
    )
    if not status or str(status.get("current_hash") or "") != final_text_hash:
        return []
    blockers: list[str] = []
    for field, reason in (
        ("requires_humanize", "humanize_required"),
        ("requires_final_verification", "final_verification_required"),
        ("requires_state_reextract", "state_replay_required"),
        ("requires_canon_reextract", "canon_replay_required"),
    ):
        if bool(status.get(field)):
            blockers.append(reason)
    if str(status.get("publication_status") or "") == "blocked_pending_finalize":
        blockers.append("revision_pending_finalize")
    return list(dict.fromkeys(blockers))


def build_chapter_publication_view(
    layout: ProjectLayout,
    project_id: str,
    chapter_number: int,
    *,
    check_persisted: bool = True,
) -> ChapterPublicationView:
    """Read one final chapter through a stable, hash-verified contract."""

    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.is_file():
        raise FileNotFoundError(f"Final chapter {chapter_number} does not exist")
    text = chapter_path.read_text(encoding="utf-8")
    final_hash = source_text_hash(text)
    artifact = _load_json(layout.chapter_artifact_path(chapter_number, "final"))
    persisted = _load_json(layout.chapter_publication_path(chapter_number))

    signed = bool(
        artifact
        and artifact.get("artifact_type") == "final"
        and str(artifact.get("input_signature") or "") not in {"", "legacy_unknown"}
    )
    raw_payload = artifact.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    artifact_hash = str(payload.get("text_hash") or artifact.get("source_text_hash") or "")
    blockers = _revision_blockers(layout, chapter_number, final_hash)
    if chapter_number in revision_stale_chapters(FileSystemStorage(layout.root.parent), layout):
        blockers.append("upstream_revision_requires_review")
    derivation_status = str(artifact.get("derivation_status") or "legacy_unknown")
    quality_status = str(artifact.get("execution_quality_status") or "legacy_unknown")
    if signed and artifact_hash != final_hash:
        derivation_status = "conflict"
        quality_status = "blocked"
        blockers.append("final_artifact_hash_mismatch")
    if str(artifact.get("quality_status") or "pass") == "fail":
        quality_status = "blocked"
        blockers.append("final_artifact_quality_failed")

    persisted_hash = str(persisted.get("final_text_hash") or "")
    if check_persisted and persisted_hash and persisted_hash != final_hash:
        derivation_status = "stale"
        blockers.append("publication_projection_stale")

    publication_status = (
        "blocked_pending_finalize" if blockers else "ready" if signed else "legacy_unknown"
    )
    parents = [str(value) for value in artifact.get("source_artifact_ids") or [] if str(value)]
    previous = str(artifact.get("previous_artifact_id") or "")
    if previous and previous not in parents:
        parents.append(previous)
    return ChapterPublicationView(
        project_id=project_id,
        chapter_number=chapter_number,
        chapter_path=str(chapter_path),
        text=text,
        final_text_hash=final_hash,
        word_count=count_chapter_words(text),
        workflow_version=str(artifact.get("workflow_version") or "legacy_unknown"),
        artifact_schema_version=int(artifact.get("artifact_schema_version") or 1),
        final_artifact_id=str(artifact.get("artifact_id") or ""),
        input_signature=str(artifact.get("input_signature") or "legacy_unknown"),
        output_version=int(artifact.get("output_version") or 0),
        parent_artifact_refs=parents,
        parent_artifact_versions={
            str(key): int(value)
            for key, value in (artifact.get("parent_artifact_versions") or {}).items()
        },
        quality_status=cast(PublicationQualityStatus, quality_status),
        derivation_status=cast(PublicationDerivationStatus, derivation_status),
        publication_status=publication_status,
        degradation_reason=str(artifact.get("degradation_reason") or ""),
        blocking_reasons=list(dict.fromkeys(blockers)),
        tts_metadata=_load_tts_metadata(layout, chapter_number),
        published_at=str(persisted.get("published_at") or ""),
    )


def persist_chapter_publication_view(
    layout: ProjectLayout,
    project_id: str,
    chapter_number: int,
    *,
    finalized: bool = False,
) -> ChapterPublicationView:
    """Persist the current projection after a successful archive or revision."""

    if finalized:
        _complete_revision_status(layout, chapter_number)
    view = build_chapter_publication_view(
        layout,
        project_id,
        chapter_number,
        check_persisted=False,
    )
    published_at = _utc_now()
    view = view.model_copy(update={"published_at": published_at})
    path = layout.chapter_publication_path(chapter_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, view.model_dump(mode="json", exclude={"text"}))
    return view


def _complete_revision_status(layout: ProjectLayout, chapter_number: int) -> None:
    """Close a pending revision only after the normal finalizer succeeds."""

    path = layout.states_dir / "final_revision_status" / f"chapter_{chapter_number:03d}.json"
    status = _load_json(path)
    chapter_path = layout.chapter_path(chapter_number)
    if not status or not chapter_path.is_file():
        return
    current_hash = source_text_hash(chapter_path.read_text(encoding="utf-8"))
    if str(status.get("current_hash") or "") != current_hash:
        return
    completed = {
        **status,
        "requires_reevaluation": False,
        "requires_state_reextract": False,
        "requires_canon_reextract": False,
        "requires_humanize": False,
        "requires_final_verification": False,
        "publication_status": "ready",
        "finalized_hash": current_hash,
        "finalized_at": _utc_now(),
    }
    atomic_write_json(path, completed)


def _tts_stage_lineage(
    layout: ProjectLayout,
    publication: ChapterPublicationView,
) -> dict[str, dict[str, Any]]:
    chapter_number = publication.chapter_number
    script_payload = _load_json(layout.tts_dubbing_script_path(chapter_number))
    script_source_hash = ""
    script_hash = ""
    if script_payload:
        try:
            script = DubbingScript.model_validate(script_payload)
            script_source_hash = str(script.source_text_hash or "")
            script_hash = str(script.script_hash or _hash_payload(script_payload))
        except (TypeError, ValueError):
            pass
    result = _load_json(layout.tts_audio_result_path(chapter_number))
    raw_metadata = result.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_embedded_script = result.get("script")
    embedded_script: dict[str, Any] = (
        raw_embedded_script if isinstance(raw_embedded_script, dict) else {}
    )
    audio_source_hash = str(
        metadata.get("source_text_hash") or embedded_script.get("source_text_hash") or ""
    )
    audio_script_hash = str(metadata.get("script_hash") or embedded_script.get("script_hash") or "")
    script_source_current = bool(
        script_payload and source_text_hash_matches(script_source_hash, publication.text)
    )
    audio_source_current = bool(
        result and source_text_hash_matches(audio_source_hash, publication.text)
    )
    voice_team = _load_json(layout.tts_voice_team_path)
    mix_plan = _load_json(layout.tts_mix_plan_path(chapter_number))
    stage_payloads = {
        "script": {
            "exists": bool(script_payload),
            "source_text_hash": script_source_hash or "legacy_unknown",
            "input_signature": _hash_payload(
                {
                    "publication_hash": publication.final_text_hash,
                    "tts_metadata": publication.tts_metadata or {},
                }
            ),
            "output_hash": script_hash or "legacy_unknown",
            "status": "fresh"
            if script_source_current
            else "stale"
            if script_payload
            else "missing",
        },
        "casting": {
            "exists": bool(voice_team),
            "input_signature": _hash_payload(
                {"voice_team": voice_team, "tts_metadata": publication.tts_metadata or {}}
            ),
            "output_hash": _hash_payload(voice_team) if voice_team else "legacy_unknown",
            "status": "fresh" if voice_team else "missing",
        },
        "synthesis": {
            "exists": bool(result),
            "source_text_hash": audio_source_hash or "legacy_unknown",
            "input_signature": _hash_payload(
                {"script_hash": script_hash, "voice_team_hash": _hash_payload(voice_team)}
            ),
            "output_hash": _hash_payload(result) if result else "legacy_unknown",
            "status": "fresh"
            if result
            and audio_source_current
            and (not script_hash or audio_script_hash == script_hash)
            else "stale"
            if result
            else "missing",
        },
        "mix": {
            "exists": bool(mix_plan),
            "input_signature": _hash_payload(
                {"audio_result": _hash_payload(result), "mix_plan": mix_plan}
            ),
            "output_hash": _hash_payload(mix_plan) if mix_plan else "legacy_unknown",
            "status": "stale"
            if mix_plan
            and (not audio_source_current or (script_hash and audio_script_hash != script_hash))
            else "fresh"
            if mix_plan
            else "missing",
        },
    }
    return stage_payloads


def record_cross_media_freshness(
    layout: ProjectLayout,
    publication: ChapterPublicationView,
    *,
    reason: str,
) -> dict[str, Any]:
    """Record non-destructive TTS/film invalidation for one novel revision."""

    existing = _load_json(layout.cross_media_lineage_path)
    raw_chapters = existing.get("chapters")
    chapters: dict[str, Any] = raw_chapters if isinstance(raw_chapters, dict) else {}
    tts_stages = _tts_stage_lineage(layout, publication)
    has_tts = any(bool(stage["exists"]) for stage in tts_stages.values())
    has_stale_tts = any(stage["status"] == "stale" for stage in tts_stages.values())
    tts_status = "missing" if not has_tts else "stale" if has_stale_tts else "fresh"
    chapter_payload = {
        "chapter_number": publication.chapter_number,
        "publication_hash": publication.final_text_hash,
        "publication_status": publication.publication_status,
        "reason": reason,
        "tts": {
            "status": tts_status,
            "stages": tts_stages,
            "actions": ["preserve_old_version", "regenerate", "rebind"]
            if tts_status == "stale"
            else [],
            "delivery_blocked": tts_status == "stale" or not publication.deliverable,
        },
        "film": {
            "status": "pending_source_reconciliation",
            "actions": ["preserve_old_version", "regenerate", "rebind"],
            "delivery_blocked": not publication.deliverable,
        },
        "updated_at": _utc_now(),
    }
    chapters[str(publication.chapter_number)] = chapter_payload
    payload = {
        "schema_version": "1.0",
        "project_id": publication.project_id,
        "chapters": chapters,
        "updated_at": _utc_now(),
    }
    atomic_write_json(layout.cross_media_lineage_path, payload)
    return chapter_payload


def reconcile_pending_publication_projections(storage_root: Path) -> dict[str, int]:
    """Rebuild missing derived publication views from committed final artifacts."""

    from novel_forge.pipeline.artifact_manifest import MANIFEST_FILENAME, ArtifactManifest
    from novel_forge.pipeline.finalization_manifest import (
        matching_finalization_phase,
        record_finalization_pending,
        record_finalization_success,
    )

    stats = {"projects": 0, "scanned": 0, "recovered": 0, "failed": 0}
    root = Path(storage_root)
    if not root.is_dir():
        return stats
    storage = FileSystemStorage(root)
    final_key = re.compile(r"^chapter:(\d+):final_text$")
    for project_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        layout = ProjectLayout(project_dir)
        raw_manifest = _load_json(layout.states_dir / MANIFEST_FILENAME)
        raw_artifacts = raw_manifest.get("artifacts")
        if not isinstance(raw_artifacts, dict):
            continue
        candidates: list[tuple[int, str]] = []
        for key, raw_record in raw_artifacts.items():
            match = final_key.match(str(key))
            if match is None or not isinstance(raw_record, dict):
                continue
            if str(raw_record.get("status") or "") != "succeeded":
                continue
            input_hashes = raw_record.get("input_hashes")
            text_hash = (
                str(input_hashes.get("text") or "") if isinstance(input_hashes, dict) else ""
            )
            if text_hash:
                candidates.append((int(match.group(1)), text_hash))
        if not candidates:
            continue
        stats["projects"] += 1
        manifest = ArtifactManifest(storage, layout)
        project_id = project_dir.name
        for chapter_number, text_hash in candidates:
            stats["scanned"] += 1
            if (
                matching_finalization_phase(
                    manifest,
                    chapter_number=chapter_number,
                    phase="publication_projection",
                    text_hash=text_hash,
                )
                is not None
                and layout.chapter_publication_path(chapter_number).is_file()
            ):
                continue
            try:
                publication = persist_chapter_publication_view(
                    layout,
                    project_id,
                    chapter_number,
                    finalized=True,
                )
                record_cross_media_freshness(
                    layout,
                    publication,
                    reason="startup_publication_recovery",
                )
                record_finalization_success(
                    manifest,
                    chapter_number=chapter_number,
                    phase="publication_projection",
                    text_hash=publication.final_text_hash,
                    output_hashes={"publication": publication.final_text_hash},
                    paths={
                        "publication": str(layout.chapter_publication_path(chapter_number)),
                        "cross_media": str(layout.cross_media_lineage_path),
                    },
                    metadata={"recovered_on_startup": True},
                )
                stats["recovered"] += 1
            except Exception as exc:
                record_finalization_pending(
                    manifest,
                    chapter_number=chapter_number,
                    phase="publication_projection",
                    text_hash=text_hash,
                    error=exc,
                    metadata={"startup_recovery": True},
                )
                stats["failed"] += 1
    return stats


__all__ = [
    "build_chapter_publication_view",
    "persist_chapter_publication_view",
    "reconcile_pending_publication_projections",
    "record_cross_media_freshness",
]
