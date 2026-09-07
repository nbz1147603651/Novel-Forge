"""Prompt context helpers for MemoryContext.

Extracted from ``integration.py`` to reduce its size. Handles building
critique context strings and legacy summary context for prompt injection.

Functions taking ``ctx`` use the MemoryContext instance as first argument.
The critique builder is a pure static function (no ctx needed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext


def build_critique_context_for_prompt(
    current_chapter: int,
    *,
    episodic_memory: Any = None,
    max_chars: int = 500,
    include_lessons: bool = True,
    max_entries: int = 8,
    distance_decay: int = 15,
) -> str:
    """Build a compact critique context string from recent critical/high entries.

    Retrieves critical/high severity critiques from EpisodicMemory's
    _critique_index, using a smart selection strategy:

    1. Score entries by relevance (recency + has_lesson + severity)
    2. Prioritize entries with lesson_learned (cross-chapter value)
    3. Apply distance decay (older chapters get lower priority)
    4. Select at most max_entries; preserve every selected entry completely

    ``max_chars`` is retained as a compatibility/advisory argument. It no
    longer authorizes cutting a selected critique or silently dropping the
    remaining selected entries.

    If include_lessons is True and entries have lesson_learned data,
    the lessons are appended to help guide the LLM.

    Returns:
        Compact string like "★ [continuity_error] Ch3: 角色位置矛盾" or empty string.
    """
    if episodic_memory is None:
        return ""

    critique_index = getattr(episodic_memory, "_critique_index", {})
    if not critique_index:
        return ""

    all_entries = [
        entry
        for entry in critique_index.values()
        if getattr(entry, "severity", "").lower() in ("critical", "high")
        and 0 < int(getattr(entry, "chapter_number", 0) or 0) < current_chapter
    ]

    if not all_entries:
        return ""

    def _entry_score(entry: Any) -> tuple[bool, int, bool, int, int]:
        """Higher score = more relevant. Returns tuple for stable sorting."""
        chapter = getattr(entry, "chapter_number", 0)
        distance = max(0, current_chapter - chapter)
        has_lesson = bool(getattr(entry, "lesson_learned", "") or "")
        severity = 1 if getattr(entry, "severity", "").lower() == "critical" else 0
        has_repair_history = len(getattr(entry, "repair_attempts", []) or []) > 0

        distance_penalty = distance // distance_decay if distance_decay > 0 else 0
        return (has_lesson, severity, has_repair_history, -distance_penalty, -distance)

    all_entries.sort(key=_entry_score, reverse=True)

    selected_entries = all_entries[:max_entries]
    selected_entries.sort(key=lambda e: getattr(e, "chapter_number", 0), reverse=True)

    parts: list[str] = []
    for entry in selected_entries:
        issue_type = str(getattr(entry, "issue_type", "") or "").strip()
        chapter = getattr(entry, "chapter_number", 0)
        summary = str(getattr(entry, "summary", "") or "").strip()
        lesson = str(getattr(entry, "lesson_learned", "") or "").strip()
        failure_pattern = str(getattr(entry, "failure_pattern", "") or "").strip()

        if not summary:
            continue

        item = f"★ [{issue_type}] Ch{chapter}: {summary}"

        if include_lessons and failure_pattern:
            item += f" | 失败模式: {failure_pattern}"
        elif include_lessons and lesson:
            item += f" | 教训: {lesson}"

        parts.append(item)

    return "；".join(parts) if parts else ""


def legacy_summary_context_for_prompt(ctx: "MemoryContext", current_chapter: int) -> str:
    summaries: list[str] = []
    for chapter in range(max(1, current_chapter - 5), current_chapter):
        raw_entry = ctx._summary_cache.get(chapter)
        entry = (
            raw_entry
            if isinstance(raw_entry, dict)
            else ctx._normalize_summary_cache_entry(raw_entry)
        )
        if raw_entry is not None and entry is not None and not isinstance(raw_entry, dict):
            ctx._summary_cache[chapter] = entry
        if not entry:
            continue
        text = str(entry.get("text", "") or "").strip()
        if text:
            summaries.append(f"第{chapter}章：{text}")
    return "\n".join(summaries)
