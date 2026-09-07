"""Episodic memory finalize helpers for MemoryContext.

Extracted from ``integration.py`` to reduce its size. Handles the episodic
indexing path during chapter finalization.

All functions take ``ctx`` (the MemoryContext instance) as first argument.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext


async def index_episodic_for_finalize(
    ctx: "MemoryContext",
    *,
    chapter_number: int,
    text: str,
    creative_report_text: str,
    chapter_result: Any | None,
    chapter_plan: Any | None,
) -> Any:
    """Prefer rich ChapterResult indexing; fall back to a coarse chapter outcome."""
    has_rich_fields = chapter_result is not None and hasattr(chapter_result, "chapter_summary")
    canon_delta = ctx._field(chapter_result, "canon_delta", None)
    if has_rich_fields:
        plan = chapter_plan or ctx._load_chapter_plan_for_memory(chapter_number)
        outcome = SimpleNamespace(
            chapter_summary=ctx._field(chapter_result, "chapter_summary", ""),
            source_chapter=ctx._field(chapter_result, "source_chapter", 0),
            character_updates=ctx._field(chapter_result, "character_updates", {}),
            new_events=ctx._field(chapter_result, "new_events", []),
            creative_report=ctx._field(chapter_result, "creative_report", None),
            alignment_report=ctx._field(chapter_result, "alignment_report", None),
            text=text,
            plan=plan,
        )
        return await ctx._episodic_memory.index_chapter(outcome)
    if canon_delta is not None:
        plan = chapter_plan or ctx._load_chapter_plan_for_memory(chapter_number)
        chapter_summary_fallback = creative_report_text or (text[:200] if text else "")
        outcome = SimpleNamespace(
            canon_delta=canon_delta,
            chapter_summary=ctx._field(canon_delta, "chapter_summary", None)
            or chapter_summary_fallback,
            source_chapter=ctx._field(canon_delta, "source_chapter", chapter_number),
            character_updates=ctx._field(canon_delta, "character_updates", {}),
            new_events=ctx._field(canon_delta, "new_events", []),
            creative_report=ctx._field(canon_delta, "creative_report", None),
            alignment_report=ctx._field(canon_delta, "alignment_report", None),
            text=text,
            plan=plan,
        )
        return await ctx._episodic_memory.index_chapter(outcome)

    return await ctx._episodic_memory.index_chapter_outcome(
        chapter_number=chapter_number,
        event_summary=creative_report_text or text[:200],
        full_text=text,
    )
