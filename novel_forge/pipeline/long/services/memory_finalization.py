"""Manifest-driven chapter memory finalization shared by Pipeline and Workspace."""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    matching_finalization_phase,
    record_finalization_pending,
    record_finalization_success,
)

MemoryFinalizationStatus = Literal["succeeded", "reused", "pending", "skipped"]


@dataclass(frozen=True)
class MemoryFinalizationResult:
    """Result of one manifest-owned memory phase attempt."""

    status: MemoryFinalizationStatus
    text_hash: str
    stats: dict[str, Any] = field(default_factory=dict)
    memory_status: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    error: str = ""

    @property
    def completed(self) -> bool:
        return self.status in {"succeeded", "reused"}


def _pending_snapshot(text: str, *, max_chars: int) -> str:
    clean = str(text or "")
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars]


def _finalize_accepts_chapter_result(finalize: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(finalize).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "chapter_result"
        for parameter in parameters
    )


async def finalize_chapter_memory_phase(
    *,
    storage: Any,
    layout: Any,
    memory_context: Any | None,
    chapter_number: int,
    chapter_text: str,
    creative_report_text: str = "",
    chapter_result: Any | None = None,
    on_step: Callable[[str, dict[str, Any]], None] | None = None,
    retry_delays: Sequence[float] = (),
) -> MemoryFinalizationResult:
    """Finalize memory exactly once for a final-text hash.

    The existing project ArtifactManifest is the only ownership ledger.  A
    succeeded record is reused across Pipeline, Workspace, and crash recovery;
    failures remain pending so a later caller can compensate without rerunning
    chapter generation, review, or final verification.
    """

    text = str(chapter_text or "")
    text_hash = source_text_hash(text) if text else ""
    if memory_context is None or not text.strip():
        return MemoryFinalizationResult(status="skipped", text_hash=text_hash)

    manifest = ArtifactManifest(storage, layout)
    if (
        matching_finalization_phase(
            manifest,
            chapter_number=chapter_number,
            phase="memory",
            text_hash=text_hash,
        )
        is not None
    ):
        if callable(on_step):
            on_step(
                "finalization_phase_reused",
                {"chapter": chapter_number, "phase": "memory", "text_hash": text_hash},
            )
        return MemoryFinalizationResult(status="reused", text_hash=text_hash)

    finalize = getattr(memory_context, "finalize_chapter_memory", None)
    if not callable(finalize):
        error = "memory context does not expose finalize_chapter_memory"
        record_finalization_pending(
            manifest,
            chapter_number=chapter_number,
            phase="memory",
            text_hash=text_hash,
            error=error,
        )
        return MemoryFinalizationResult(status="pending", text_hash=text_hash, error=error)

    last_error = ""
    attempts = 0
    for attempts, delay in enumerate((0.0, *retry_delays), start=1):
        if delay > 0:
            await asyncio.sleep(float(delay) + random.uniform(0, float(delay) * 0.5))
        try:
            kwargs: dict[str, Any] = {
                "chapter_number": chapter_number,
                "text": text,
                "creative_report_text": str(creative_report_text or ""),
            }
            if chapter_result is not None and _finalize_accepts_chapter_result(finalize):
                kwargs["chapter_result"] = chapter_result
            raw_stats = await finalize(**kwargs)
            stats = dict(raw_stats or {})
            if not bool(stats.get("saved", False)):
                last_error = "memory finalization returned saved=false"
                break

            get_status = getattr(memory_context, "get_memory_status_for_ui", None)
            memory_status = dict(get_status() or {}) if callable(get_status) else {}
            memory_status.update({"save_success": True, "chapter": chapter_number})
            if callable(on_step):
                on_step("memory_updated", memory_status)
            record_finalization_success(
                manifest,
                chapter_number=chapter_number,
                phase="memory",
                text_hash=text_hash,
                output_hashes={"memory": text_hash},
                metadata={
                    "saved": True,
                    "tasks_ok": stats.get("tasks_ok"),
                    "tasks_failed": stats.get("tasks_failed"),
                    "attempts": attempts,
                },
            )
            return MemoryFinalizationResult(
                status="succeeded",
                text_hash=text_hash,
                stats=stats,
                memory_status=memory_status,
                attempts=attempts,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)

    error = last_error or "memory finalization failed"
    record_finalization_pending(
        manifest,
        chapter_number=chapter_number,
        phase="memory",
        text_hash=text_hash,
        error=error,
        metadata={
            "chapter_text_snapshot": _pending_snapshot(text, max_chars=20000),
            "creative_report_text": _pending_snapshot(
                str(creative_report_text or ""),
                max_chars=4000,
            ),
            "attempts": attempts,
        },
    )
    if callable(on_step):
        on_step("memory_update_failed", {"chapter": chapter_number, "error": error})
    return MemoryFinalizationResult(
        status="pending",
        text_hash=text_hash,
        attempts=attempts,
        error=error,
    )


__all__ = [
    "MemoryFinalizationResult",
    "MemoryFinalizationStatus",
    "finalize_chapter_memory_phase",
]
