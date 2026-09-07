"""Typed validation for normalized draft-stage prompt cards."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from novel_forge.common.constants import TaskType
from novel_forge.prompts.context_types.base import PromptContextModel, validate_prompt_context

_DICT_CARD_FIELDS = (
    "chapter",
    "contract",
    "bridge",
    "memory",
    "element",
    "quality",
    "plan",
    "state",
    "style",
    "editorial",
    "narration",
    "knowledge",
    "time",
    "strand",
    "subplot_weave",
    "repair",
    "arc_liveness",
)


class DraftChapterStageCards(PromptContextModel):
    """Normalized cards consumed by writing/draft_chapter.j2."""

    stage: str = "draft"
    chapter: dict[str, Any]
    contract: dict[str, Any]
    bridge: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any]
    element: dict[str, Any]
    quality: dict[str, Any]
    plan: dict[str, Any]
    state: dict[str, Any]
    style: dict[str, Any]
    editorial: dict[str, Any]
    narration: dict[str, Any]
    knowledge: dict[str, Any]
    time: dict[str, Any]
    strand: dict[str, Any]
    subplot_weave: dict[str, Any]
    repair: dict[str, Any]
    arc_liveness: dict[str, Any]
    characters: list[Any] = Field(default_factory=list)

    @field_validator(*_DICT_CARD_FIELDS, mode="before")
    @classmethod
    def _validate_card_dict(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("stage card section must be a dict after normalization")
        return value


def validate_draft_stage_cards(
    stage_cards: dict[str, Any],
    *,
    source: str = "DRAFT_CHAPTER.stage_cards",
) -> DraftChapterStageCards:
    """Validate normalized DRAFT_CHAPTER stage cards."""
    return validate_prompt_context(
        DraftChapterStageCards,
        stage_cards,
        task_type=TaskType.DRAFT_CHAPTER,
        source=source,
    )
