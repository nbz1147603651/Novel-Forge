"""Typed context for PLAN_OUTLINE prompt rendering."""

from __future__ import annotations

from typing import Any

from pydantic import Field, StrictBool, StrictInt, field_validator

from novel_forge.common.constants import TaskType
from novel_forge.prompts.context_types.base import (
    PromptContextModel,
    has_prompt_fields,
    is_prompt_mapping,
    validate_prompt_context,
)


class PlanOutlineContext(PromptContextModel):
    """Context consumed by planning/plan_outline.j2."""

    spec: Any
    story_bible: Any
    character_bible: Any
    total_chapters: StrictInt
    use_volume_mode: StrictBool
    blueprint_fragment_request: dict[str, Any] | None = None
    blueprint_generation_mode: str = ""
    blueprint_fragments_so_far: dict[str, Any] = Field(default_factory=dict)

    @field_validator("spec")
    @classmethod
    def _validate_spec(cls, value: Any) -> Any:
        required = ("genre", "theme", "tone", "length_target", "language")
        if not is_prompt_mapping(value) or not has_prompt_fields(value, required):
            raise ValueError(f"spec must expose fields: {', '.join(required)}")
        return value

    @field_validator("story_bible")
    @classmethod
    def _validate_story_bible(cls, value: Any) -> Any:
        if not is_prompt_mapping(value):
            raise ValueError("story_bible must be a mapping or model-like object")
        return value

    @field_validator("character_bible")
    @classmethod
    def _validate_character_bible(cls, value: Any) -> Any:
        if not is_prompt_mapping(value):
            raise ValueError("character_bible must be a mapping or model-like object")
        return value

    @field_validator("blueprint_fragment_request")
    @classmethod
    def _validate_fragment_request(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        if not isinstance(value.get("block_key"), str) or not value.get("block_key", "").strip():
            raise ValueError("blueprint_fragment_request.block_key must be a non-empty string")
        required_keys = value.get("required_keys")
        if not isinstance(required_keys, list) or not all(
            isinstance(item, str) and item.strip() for item in required_keys
        ):
            raise ValueError("blueprint_fragment_request.required_keys must be a list of strings")
        return value


def validate_plan_outline_context(
    context: dict[str, Any],
    *,
    source: str = "PLAN_OUTLINE",
) -> PlanOutlineContext:
    """Validate a PLAN_OUTLINE context at its production construction point."""
    return validate_prompt_context(
        PlanOutlineContext,
        context,
        task_type=TaskType.PLAN_OUTLINE,
        source=source,
    )
