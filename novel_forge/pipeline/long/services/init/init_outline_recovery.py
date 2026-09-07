"""Audit and repair resumable init-outline state."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from novel_forge.core.constants import PipelineConstants
from novel_forge.core.domain.character_boundary import canonical_character_names
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.blueprint import outline_helpers as outline_h
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _load_outline_session_accepted_batches,
    _load_partial_outline_chapters_from_session,
    _outline_batch_checkpoint_dir,
    _save_outline_resume_state,
)


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _load_json(storage: Any, path: Path) -> dict[str, Any]:
    try:
        if not storage.exists(path):
            return {}
        payload = storage.load_json(path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_outline(storage: Any, path: Path) -> tuple[StoryOutline | None, str]:
    payload = _load_json(storage, path)
    if not payload:
        return None, "missing"
    try:
        return StoryOutline.model_validate(payload), ""
    except Exception as exc:  # noqa: BLE001 - surfaced in audit report
        return None, f"{type(exc).__name__}: {exc}"


def _outline_progress(outline: StoryOutline | None, total_chapters: int | None) -> dict[str, Any]:
    if outline is None:
        return {
            "total_chapters": int(total_chapters or 0),
            "chapter_numbers": [],
            "chapters_done": 0,
            "complete": False,
            "partial": False,
            "missing_chapters": [],
        }
    total = int(total_chapters or outline.total_chapters or 0)
    numbers = sorted(
        {
            int(chapter.chapter_number)
            for chapter in outline.chapters
            if chapter.notes != PipelineConstants.PLACEHOLDER_NOTE and chapter.beats_summary
        }
    )
    missing = [number for number in range(1, total + 1) if number not in set(numbers)]
    complete = bool(total > 0 and not missing and outline_h.is_outline_complete(outline))
    return {
        "total_chapters": total,
        "chapter_numbers": numbers,
        "chapters_done": len(numbers),
        "complete": complete,
        "partial": bool(numbers and not complete),
        "missing_chapters": missing,
    }


def _checkpoint_summaries(storage: Any, layout: ProjectLayout) -> list[dict[str, Any]]:
    checkpoint_dir = _outline_batch_checkpoint_dir(layout)
    if not checkpoint_dir.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(checkpoint_dir.glob("batch_*.json")):
        payload = _load_json(storage, path)
        if not payload:
            summaries.append({"path": str(path), "status": "unreadable"})
            continue
        summaries.append(
            {
                "path": str(path),
                "status": str(payload.get("status") or ""),
                "batch_start": payload.get("batch_start"),
                "batch_end": payload.get("batch_end"),
                "total_chapters": payload.get("total_chapters"),
                "accepted_chapters": payload.get("accepted_chapters") or [],
                "missing_chapters": payload.get("missing_chapters") or [],
                "content_hash": payload.get("content_hash") or "",
                "source_hash": payload.get("source_hash") or "",
                "session_id": payload.get("session_id") or "",
            }
        )
    return summaries


def _checkpoint_filename(item: dict[str, Any]) -> str:
    try:
        return Path(str(item.get("path") or "")).name
    except Exception:
        return ""


def _derive_total_chapters(
    *,
    requested_total: int | None,
    outline: StoryOutline | None,
    session_payload: dict[str, Any],
    checkpoints: list[dict[str, Any]],
) -> int:
    if requested_total:
        return int(requested_total)
    if outline is not None and int(outline.total_chapters or 0) > 0:
        return int(outline.total_chapters)
    try:
        session_total = int(session_payload.get("total_chapters") or 0)
    except (TypeError, ValueError):
        session_total = 0
    if session_total > 0:
        return session_total
    for item in checkpoints:
        try:
            total = int(item.get("total_chapters") or 0)
        except (TypeError, ValueError):
            total = 0
        if total > 0:
            return total
    return 0


def _load_character_bible(storage: Any, layout: ProjectLayout) -> CharacterBible | None:
    payload = _load_json(storage, layout.characters_path)
    if not payload:
        return None
    try:
        return CharacterBible.model_validate(payload)
    except Exception:
        return None


def _audit_outline_entity_names(
    *,
    outline: StoryOutline | None,
    canonical_names: set[str],
) -> list[dict[str, Any]]:
    if outline is None or not canonical_names:
        return []
    issues: list[dict[str, Any]] = []
    for chapter in outline.chapters:
        observed: list[tuple[str, str]] = []
        pov = str(chapter.pov_character or "").strip()
        if pov:
            observed.append(("pov_character", pov))
        for name in chapter.involved_characters or []:
            clean = str(name or "").strip()
            if clean:
                observed.append(("involved_characters", clean))
        for field, name in observed:
            if name in canonical_names:
                continue
            issues.append(
                {
                    "artifact": "outline",
                    "chapter": chapter.chapter_number,
                    "field": field,
                    "observed_name": name,
                    "message": f"结构化角色字段出现非 canonical 名称：{name}",
                }
            )
    return issues


def _derived_artifact_paths(layout: ProjectLayout) -> list[Path]:
    return [
        layout.narrative_contract_path,
        layout.plans_dir / "chapter_contracts.json",
        layout.reports_dir / "contract_coherence.json",
        layout.reports_dir / "init_readiness.json",
        layout.init_readiness_artifact_path,
        layout.memory_dir / "outline_episodic.json",
        layout.memory_dir / "init_coherence_claims.jsonl",
        layout.memory_dir / "init_coherence_claim_ledger.json",
        layout.memory_dir / "init_coherence_index.json",
    ]


def _outline_regeneration_paths(layout: ProjectLayout) -> list[Path]:
    """Artifacts that must be quarantined before forcing outline regeneration."""
    return [
        layout.outline_path,
        layout.outline_session_path,
        layout.outline_tracker_path,
        _outline_batch_checkpoint_dir(layout),
        layout.memory_dir / "outline_episodic.json",
        *_derived_artifact_paths(layout),
        layout.canon_dir,
        layout.narrative_state_dir,
        layout.plans_dir / "plot_milestone_index.json",
        layout.plans_dir / "element_progress.json",
    ]


def audit_outline_resume_state(
    storage: Any,
    layout: ProjectLayout,
    *,
    total_chapters: int | None = None,
) -> dict[str, Any]:
    """Return a non-mutating audit of outline resume/canonical state."""
    outline, outline_error = _load_outline(storage, layout.outline_path)
    session_payload = _load_json(storage, layout.outline_session_path)
    checkpoints = _checkpoint_summaries(storage, layout)
    total = _derive_total_chapters(
        requested_total=total_chapters,
        outline=outline,
        session_payload=session_payload,
        checkpoints=checkpoints,
    )
    session_id = str(session_payload.get("session_id") or "") if session_payload else ""
    ledger_records = (
        _load_outline_session_accepted_batches(
            storage,
            layout,
            total_chapters=total,
            session_payload=session_payload,
        )
        if total > 0 and session_payload
        else []
    )
    accepted_chapters = (
        _load_partial_outline_chapters_from_session(
            storage,
            layout,
            total_chapters=total,
        )
        if total > 0 and session_payload
        else []
    )
    accepted_numbers = sorted({chapter.chapter_number for chapter in accepted_chapters})
    accepted_missing = [
        number for number in range(1, total + 1) if total > 0 and number not in accepted_numbers
    ]
    ledger_checkpoint_names = {
        str(record.get("checkpoint_file") or "")
        for record in ledger_records
        if isinstance(record, dict)
    }
    orphan_checkpoints = [
        item
        for item in checkpoints
        if session_payload
        and str(item.get("status") or "") == "accepted"
        and _checkpoint_filename(item) not in ledger_checkpoint_names
    ]
    dirty_checkpoints = [
        item for item in checkpoints if str(item.get("status") or "") != "accepted"
    ]
    character_bible = _load_character_bible(storage, layout)
    canonical_names = set(canonical_character_names(character_bible))
    outline_progress = _outline_progress(outline, total)
    derived = [
        {"path": str(path), "exists": path.exists()}
        for path in _derived_artifact_paths(layout)
    ]
    entity_issues = _audit_outline_entity_names(
        outline=outline,
        canonical_names=canonical_names,
    )
    needs_persistence_repair = bool(
        outline_progress["partial"]
        or dirty_checkpoints
        or orphan_checkpoints
        or (layout.outline_path.exists() and outline is None)
    )
    needs_entity_repair = bool(entity_issues)
    return {
        "schema_version": "1.0",
        "project_dir": str(layout.root),
        "canonical_outline": {
            "path": str(layout.outline_path),
            "exists": layout.outline_path.exists(),
            "valid": outline is not None,
            "error": outline_error,
            **outline_progress,
        },
        "session": {
            "path": str(layout.outline_session_path),
            "exists": layout.outline_session_path.exists(),
            "session_id": session_id,
            "status": session_payload.get("status") if session_payload else "",
            "chapters_done": session_payload.get("chapters_done") if session_payload else 0,
            "latest_chapter_number": session_payload.get("latest_chapter_number")
            if session_payload
            else 0,
            "last_safe_chapter": session_payload.get("last_safe_chapter")
            if session_payload
            else 0,
            "pending_batch": session_payload.get("pending_batch") if session_payload else None,
        },
        "checkpoints": {
            "count": len(checkpoints),
            "ledger_records": ledger_records,
            "ledger_checkpoint_count": len(ledger_records),
            "accepted_chapters": accepted_numbers,
            "accepted_chapters_done": len(accepted_numbers),
            "missing_from_accepted": accepted_missing,
            "dirty": dirty_checkpoints,
            "orphan": orphan_checkpoints,
            "items": checkpoints,
        },
        "derived_artifacts": derived,
        "entity_name_audit": {
            "canonical_name_count": len(canonical_names),
            "issues": entity_issues,
        },
        "needs_repair": needs_persistence_repair,
        "needs_persistence_repair": needs_persistence_repair,
        "needs_entity_repair": needs_entity_repair,
        "needs_attention": bool(needs_persistence_repair or needs_entity_repair),
    }


def _quarantine_path(path: Path, quarantine_root: Path) -> str | None:
    if not path.exists():
        return None
    dest = quarantine_root / path.name
    if path.parent.name in {"plans", "reports", "memory", "source_artifacts"}:
        dest = quarantine_root / path.parent.name / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return str(dest)


def _quarantine_checkpoint_items(
    items: list[dict[str, Any]],
    *,
    quarantine_root: Path,
) -> list[str]:
    moved: list[str] = []
    for item in items:
        path_text = str(item.get("path") or "").strip()
        if not path_text:
            continue
        moved_path = _quarantine_path(Path(path_text), quarantine_root / "outline_batches")
        if moved_path:
            moved.append(moved_path)
    return moved


def repair_outline_resume_state(
    storage: Any,
    layout: ProjectLayout,
    *,
    apply: bool = False,
    total_chapters: int | None = None,
) -> dict[str, Any]:
    """Audit or repair outline resume state.

    When ``apply`` is false, this is a pure dry-run. When true, partial
    canonical outline and downstream derived artifacts are quarantined while
    accepted checkpoints are kept as the only recovery source.
    """
    audit = audit_outline_resume_state(
        storage,
        layout,
        total_chapters=total_chapters,
    )
    if not apply:
        return {**audit, "applied": False, "moved_artifacts": []}
    if not audit.get("needs_persistence_repair"):
        return {
            **audit,
            "applied": False,
            "moved_artifacts": [],
            "skipped_reason": "no_persistence_repair_needed",
        }

    layout.ensure_dirs()
    quarantine_root = layout.states_dir / "outline_recovery" / _now_stamp()
    moved: list[str] = []
    canonical = audit["canonical_outline"]
    should_quarantine_outline = bool(canonical.get("partial") or not canonical.get("valid"))
    if should_quarantine_outline:
        moved_path = _quarantine_path(layout.outline_path, quarantine_root)
        if moved_path:
            moved.append(moved_path)
        for path in _derived_artifact_paths(layout):
            moved_path = _quarantine_path(path, quarantine_root)
            if moved_path:
                moved.append(moved_path)
    checkpoints = audit.get("checkpoints") if isinstance(audit.get("checkpoints"), dict) else {}
    dirty_items = checkpoints.get("dirty") if isinstance(checkpoints, dict) else []
    orphan_items = checkpoints.get("orphan") if isinstance(checkpoints, dict) else []
    if isinstance(dirty_items, list):
        moved.extend(_quarantine_checkpoint_items(dirty_items, quarantine_root=quarantine_root))
    if isinstance(orphan_items, list):
        moved.extend(_quarantine_checkpoint_items(orphan_items, quarantine_root=quarantine_root))

    total = int(canonical.get("total_chapters") or total_chapters or 0)
    accepted_chapters = (
        _load_partial_outline_chapters_from_session(
            storage,
            layout,
            total_chapters=total,
        )
        if total > 0 and layout.outline_session_path.exists()
        else []
    )
    chapter_map: dict[int, ChapterOutline] = {
        chapter.chapter_number: chapter for chapter in accepted_chapters
    }
    session_id = str(audit.get("session", {}).get("session_id") or "")
    accepted_batches = audit.get("checkpoints", {}).get("ledger_records", [])
    _save_outline_resume_state(
        storage,
        layout,
        total_chapters=total,
        chapter_map=chapter_map,
        conversation_history=[],
        history_window_rounds=2,
        session_metadata={
            "status": "recoverable" if chapter_map else "cancelled",
            "session_id": session_id,
            "pending_batch": None,
            "cancel_reason": "outline_resume_repair",
            "accepted_batches": accepted_batches if isinstance(accepted_batches, list) else [],
        },
    )
    result = audit_outline_resume_state(
        storage,
        layout,
        total_chapters=total,
    )
    result = {**result, "applied": True, "moved_artifacts": moved}
    storage.save_json(layout.reports_dir / "outline_resume_repair.json", result)
    return result


def prepare_outline_regeneration(
    storage: Any,
    layout: ProjectLayout,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Quarantine outline and downstream init artifacts so init-long regenerates outline.

    This preserves upstream init artifacts such as spec, story bible, character
    bible, style profile, and narrative blueprint. It removes only the outline
    stage and artifacts derived from it.
    """
    paths = _outline_regeneration_paths(layout)
    existing = [
        {
            "path": str(path),
            "kind": "dir" if path.is_dir() else "file",
        }
        for path in paths
        if path.exists()
    ]
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "project_dir": str(layout.root),
        "mode": "apply" if apply else "dry-run",
        "artifact_count": len(existing),
        "artifacts": existing,
        "moved_artifacts": [],
        "applied": False,
    }
    if not apply:
        return result

    layout.ensure_dirs()
    quarantine_root = layout.states_dir / "outline_regeneration" / _now_stamp()
    moved: list[str] = []
    for path in paths:
        moved_path = _quarantine_path(path, quarantine_root)
        if moved_path:
            moved.append(moved_path)
    result["moved_artifacts"] = moved
    result["applied"] = True
    storage.save_json(layout.reports_dir / "outline_regeneration_prepare.json", result)
    return result
