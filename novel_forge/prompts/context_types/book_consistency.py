"""Typed context for BOOK_CONSISTENCY prompt rendering."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from novel_forge.common.constants import TaskType
from novel_forge.prompts.context_types.base import PromptContextModel, validate_prompt_context

_BOOK_CONSISTENCY_TASKS = {
    TaskType.BOOK_CONSISTENCY,
    TaskType.BOOK_CONSISTENCY_NAMING,
    TaskType.BOOK_CONSISTENCY_TIMELINE,
    TaskType.BOOK_CONSISTENCY_WORLD_RULE,
    TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
    TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
    TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
}


class BookConsistencyContext(PromptContextModel):
    """Context consumed by checking/book_consistency.j2."""

    chapter_summaries: list[dict[str, Any]]
    chapter_texts: list[dict[str, Any]] = Field(default_factory=list)
    chapter_issue_pool: list[dict[str, Any]] = Field(default_factory=list)
    analysis_mode: Literal["summary", "full_text"] = "summary"
    location_strictness: Literal["strict", "balanced", "loose"] = "balanced"
    audit_slices: list[dict[str, Any]] = Field(default_factory=list)
    active_audit_dimension: str = ""
    dimension_focus: dict[str, Any] = Field(default_factory=dict)
    dimension_audit_bundle: dict[str, Any] = Field(default_factory=dict)
    shared_evidence_anchor: dict[str, Any] = Field(default_factory=dict)


def validate_book_consistency_context(
    context: dict[str, Any],
    *,
    task_type: TaskType = TaskType.BOOK_CONSISTENCY,
    source: str = "book_consistency",
) -> BookConsistencyContext:
    """Validate a BOOK_CONSISTENCY family prompt context."""
    if task_type not in _BOOK_CONSISTENCY_TASKS:
        raise ValueError(f"unsupported book consistency task: {task_type}")
    return validate_prompt_context(
        BookConsistencyContext,
        context,
        task_type=task_type,
        source=source,
    )
