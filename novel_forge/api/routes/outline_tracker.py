"""Routes for outline tracker operations."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from novel_forge.api.deps import get_runtime_services
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]
_log = logging.getLogger(__name__)


class RelationshipEntryResponse(BaseModel):
    """Response for a relationship entry."""

    character_a: str
    character_b: str
    description: str
    chapter_introduced: int
    relationship_type: str
    trust_level: float
    tension_level: float
    shift_events: list[str]


class CharacterStateResponse(BaseModel):
    """Response for character state."""

    name: str
    role: str
    first_appearance: int
    appearances: list[int]
    relationships: list[str]


class ThemeOccurrenceResponse(BaseModel):
    """Response for theme occurrence."""

    theme: str
    chapter: int
    context: str


class PlotThreadResponse(BaseModel):
    """Response for plot thread."""

    thread_id: str
    name: str
    introduced_chapter: int
    status: str
    resolved_chapter: int
    key_events: list[str]


class KeyEventResponse(BaseModel):
    """Response for key event."""

    chapter: int
    event_type: str
    description: str
    characters_involved: list[str]


class OutlineTrackerContextResponse(BaseModel):
    """Response for outline tracker context."""

    relationships: list[RelationshipEntryResponse]
    character_states: dict[str, CharacterStateResponse]
    themes: list[ThemeOccurrenceResponse]
    threads: list[PlotThreadResponse]
    key_events: list[KeyEventResponse]
    unresolved_questions: list[str]


class OutlineTrackerSummaryResponse(BaseModel):
    """Response for outline tracker summary."""

    relationship_summary: str
    themes_summary: str
    key_events_summary: str
    unresolved_summary: str
    recent_chapters_summary: str


class OutlineTrackerStatusResponse(BaseModel):
    """Response for outline tracker status."""

    enabled: bool
    total_relationships: int
    total_characters: int
    total_themes: int
    total_threads: int
    total_key_events: int
    unresolved_count: int


def _get_outline_tracker(runtime: RuntimeServices, project_id: str) -> Any | None:
    """Get outline tracker from runtime services or project storage."""
    outline_trackers = getattr(runtime, "_outline_trackers", {})
    if isinstance(outline_trackers, dict):
        outline_tracker = outline_trackers.get(project_id)
        if outline_tracker is not None:
            return outline_tracker

    try:
        from novel_forge.story_kernel.outline_tracker import (
            HybridOutlineTracker,
            OutlineTracker,
        )

        project_dir = runtime.storage.existing_project_dir(project_id)
        layout = ProjectLayout(project_dir)
        tracker_file = layout.outline_tracker_path
        legacy_tracker_file = project_dir / "canon" / "outline_tracker.json"

        if runtime.storage.exists(tracker_file):
            data = runtime.storage.load_json(tracker_file)
        elif runtime.storage.exists(legacy_tracker_file):
            data = runtime.storage.load_json(legacy_tracker_file)
        else:
            return OutlineTracker()

        if "hybrid_config" in data:
            return HybridOutlineTracker.from_dict(dict(data))
        return OutlineTracker.from_dict(data)
    except FileNotFoundError:
        return None
    except Exception as exc:
        _log.warning(
            "outline_tracker_load_failed | project_id=%s | error=%s",
            project_id,
            exc,
        )
        return None


def _save_outline_tracker(runtime: RuntimeServices, project_id: str, tracker: Any) -> None:
    """Save outline tracker to project storage."""
    project_dir = runtime.storage.ensure_project_dir(project_id)
    tracker_file = ProjectLayout(project_dir).outline_tracker_path
    runtime.storage.save_json(tracker_file, tracker.to_dict())


def _rule_tracker(tracker: Any) -> Any:
    """Return the rule tracker for plain and hybrid tracker instances."""
    return getattr(tracker, "_rule_tracker", tracker)


@router.get("/status/{project_id}", response_model=OutlineTrackerStatusResponse)
async def get_outline_tracker_status(
    project_id: str,
    runtime: RuntimeDep,
) -> OutlineTrackerStatusResponse:
    """Get outline tracker status for a project."""
    tracker = _get_outline_tracker(runtime, project_id)

    if tracker is None:
        return OutlineTrackerStatusResponse(
            enabled=False,
            total_relationships=0,
            total_characters=0,
            total_themes=0,
            total_threads=0,
            total_key_events=0,
            unresolved_count=0,
        )

    rule_tracker = _rule_tracker(tracker)
    return OutlineTrackerStatusResponse(
        enabled=True,
        total_relationships=len(rule_tracker._relationships),
        total_characters=len(rule_tracker._character_states),
        total_themes=len(rule_tracker._themes),
        total_threads=len(rule_tracker._threads),
        total_key_events=len(rule_tracker._key_events),
        unresolved_count=len(rule_tracker._unresolved_questions),
    )


@router.get("/context/{project_id}", response_model=OutlineTrackerContextResponse)
async def get_outline_tracker_context(
    project_id: str,
    current_chapter: int,
    runtime: RuntimeDep,
) -> OutlineTrackerContextResponse:
    """Get full outline tracker context for a chapter."""
    tracker = _get_outline_tracker(runtime, project_id)

    if tracker is None:
        raise HTTPException(status_code=404, detail="Outline tracker not available for this project")

    context = tracker.get_context_for_chapter(current_chapter)

    return OutlineTrackerContextResponse(
        relationships=[
            RelationshipEntryResponse(
                character_a=r.character_a,
                character_b=r.character_b,
                description=r.description,
                chapter_introduced=r.chapter_introduced,
                relationship_type=r.relationship_type,
                trust_level=r.trust_level,
                tension_level=r.tension_level,
                shift_events=r.shift_events,
            )
            for r in context.relationships
        ],
        character_states={
            name: CharacterStateResponse(
                name=s.name,
                role=s.role,
                first_appearance=s.first_appearance,
                appearances=s.appearances,
                relationships=s.relationships,
            )
            for name, s in context.character_states.items()
        },
        themes=[
            ThemeOccurrenceResponse(
                theme=t.theme,
                chapter=t.chapter,
                context=t.context,
            )
            for t in context.themes
        ],
        threads=[
            PlotThreadResponse(
                thread_id=t.thread_id,
                name=t.name,
                introduced_chapter=t.introduced_chapter,
                status=t.status,
                resolved_chapter=t.resolved_chapter,
                key_events=t.key_events,
            )
            for t in context.threads
        ],
        key_events=[
            KeyEventResponse(
                chapter=e.chapter,
                event_type=e.event_type,
                description=e.description,
                characters_involved=e.characters_involved,
            )
            for e in context.key_events
        ],
        unresolved_questions=context.unresolved_questions,
    )


@router.get("/summary/{project_id}", response_model=OutlineTrackerSummaryResponse)
async def get_outline_tracker_summary(
    project_id: str,
    current_chapter: int,
    runtime: RuntimeDep,
) -> OutlineTrackerSummaryResponse:
    """Get outline tracker summary for prompt injection."""
    tracker = _get_outline_tracker(runtime, project_id)

    if tracker is None:
        raise HTTPException(status_code=404, detail="Outline tracker not available for this project")

    summary = tracker.get_context_for_prompt(current_chapter)

    rule_tracker = _rule_tracker(tracker)
    recent_chapters = [ch for ch in rule_tracker._character_states.values() if ch.appearances]
    recent_summary_parts = []
    for state in sorted(recent_chapters, key=lambda s: max(s.appearances) if s.appearances else 0, reverse=True)[:5]:
        recent_summary_parts.append(
            f"{state.name}(第{max(state.appearances)}章): {', '.join(str(a) for a in sorted(state.appearances)[-3:])}"
        )
    recent_chapters_str = "\n".join(recent_summary_parts) if recent_summary_parts else "暂无章节记录。"

    return OutlineTrackerSummaryResponse(
        relationship_summary=summary["relationship_summary"],
        themes_summary=summary["themes_summary"],
        key_events_summary=summary["key_events_summary"],
        unresolved_summary=summary["unresolved_summary"],
        recent_chapters_summary=recent_chapters_str,
    )


@router.post("/update/{project_id}")
async def update_outline_tracker(
    project_id: str,
    chapter_number: int,
    chapter_data: dict[str, Any],
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Update outline tracker with new chapter data."""
    tracker = _get_outline_tracker(runtime, project_id)

    if tracker is None:
        raise HTTPException(status_code=404, detail="Outline tracker not available for this project")

    from novel_forge.core.schemas.outline import ChapterOutline

    try:
        chapter = ChapterOutline.model_validate(chapter_data)
        if chapter.chapter_number != chapter_number:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"chapter_number 不一致：query={chapter_number}，"
                    f"chapter_data.chapter_number={chapter.chapter_number}"
                ),
            )
        if hasattr(tracker, "update_from_chapter"):
            tracker.update_from_chapter(chapter)
        elif hasattr(tracker, "update_from_batch"):
            tracker.update_from_batch([chapter])
        else:
            raise TypeError(f"Unsupported outline tracker type: {type(tracker).__name__}")
        _save_outline_tracker(runtime, project_id, tracker)

        return {
            "success": True,
            "chapter_number": chapter_number,
            "tracker_updated": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update tracker: {str(e)}") from e


@router.post("/reset/{project_id}")
async def reset_outline_tracker(
    project_id: str,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Reset outline tracker for a project."""
    tracker = _get_outline_tracker(runtime, project_id)

    if tracker is None:
        raise HTTPException(status_code=404, detail="Outline tracker not available for this project")

    rule_tracker = _rule_tracker(tracker)
    rule_tracker._relationships.clear()
    rule_tracker._character_states.clear()
    rule_tracker._themes.clear()
    rule_tracker._threads.clear()
    rule_tracker._key_events.clear()
    rule_tracker._unresolved_questions.clear()

    _save_outline_tracker(runtime, project_id, tracker)

    return {"success": True, "message": "Outline tracker reset successfully"}
