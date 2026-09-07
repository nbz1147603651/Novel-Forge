"""Typed context helpers for Repair Orchestration v2 handlers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.models import RepairMission


@dataclass(frozen=True)
class RepairContextSpec:
    """Required context keys for a handler execution path."""

    handler: str
    required_keys: tuple[str, ...]


class RepairContextError(ValueError):
    """Raised when a repair mission lacks the typed context required by a handler."""


@dataclass(frozen=True)
class TextRepairContext:
    """Typed context for in-memory or file-backed text repairs."""

    current_text: str
    repaired_text: str
    text_key: str = "current_text"
    artifact: str = "in_memory:current_text"
    path: Path | None = None
    failure_reason: str = ""


@dataclass(frozen=True)
class ArtifactRepairContext:
    """Typed context for generic JSON/text artifact updates."""

    artifact: str
    path: Path | None = None
    repaired_payload: Any = None
    repaired_text: str = ""
    applied: bool = True
    failure_reason: str = ""


@dataclass(frozen=True)
class BookRepairQueueContext:
    """Typed context for a book-level repair mission that delegates child missions."""

    chapter_missions: tuple[RepairMission, ...]


def require_context(mission: RepairMission, spec: RepairContextSpec) -> dict[str, Any]:
    """Return source_context after validating required keys."""

    missing = [key for key in spec.required_keys if key not in mission.source_context]
    if missing:
        raise RepairContextError(
            f"{spec.handler} repair mission missing context keys: {', '.join(missing)}"
        )
    return mission.source_context


def text_repair_context(
    mission: RepairMission,
    *,
    handler: str,
    require_repaired_text: bool = True,
) -> TextRepairContext:
    """Return typed text repair context from a mission."""

    ctx = require_context(mission, RepairContextSpec(handler=handler, required_keys=("current_text",)))
    repaired = ctx.get("repaired_text", ctx.get("revised_text"))
    if require_repaired_text and repaired is None:
        raise RepairContextError(f"{handler} repair mission missing context keys: repaired_text")
    path = ctx.get("path", ctx.get("artifact_path"))
    return TextRepairContext(
        current_text=str(ctx.get("current_text") or ""),
        repaired_text=str(repaired if repaired is not None else ctx.get("current_text") or ""),
        text_key=str(ctx.get("text_key") or "current_text"),
        artifact=str(ctx.get("artifact") or "in_memory:current_text"),
        path=Path(path) if path else None,
        failure_reason=str(ctx.get("failure_reason") or ""),
    )


def artifact_repair_context(mission: RepairMission, *, handler: str) -> ArtifactRepairContext:
    """Return typed artifact repair context from a mission."""

    ctx = require_context(mission, RepairContextSpec(handler=handler, required_keys=("artifact",)))
    path = ctx.get("path", ctx.get("artifact_path"))
    return ArtifactRepairContext(
        artifact=str(ctx.get("artifact") or ""),
        path=Path(path) if path else None,
        repaired_payload=ctx.get("repaired_payload", ctx.get("payload")),
        repaired_text=str(ctx.get("repaired_text") or ""),
        applied=bool(ctx.get("applied", True)),
        failure_reason=str(ctx.get("failure_reason") or ""),
    )


def book_repair_queue_context(mission: RepairMission, *, handler: str) -> BookRepairQueueContext:
    """Return typed book repair queue context from a mission."""

    ctx = require_context(mission, RepairContextSpec(handler=handler, required_keys=("chapter_missions",)))
    missions = tuple(
        item for item in (ctx.get("chapter_missions") or ()) if isinstance(item, RepairMission)
    )
    return BookRepairQueueContext(chapter_missions=missions)
