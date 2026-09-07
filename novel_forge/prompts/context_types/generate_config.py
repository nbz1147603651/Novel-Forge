"""Typed context for GENERATE_CONFIG and POLISH_CONFIG prompt rendering."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, field_validator

from novel_forge.common.constants import TaskType
from novel_forge.prompts.context_types.base import PromptContextModel, validate_prompt_context


class GenerateConfigContext(PromptContextModel):
    """Context consumed by initialization/generate_config.j2."""

    mode: Literal["short", "long"]
    mode_label: str
    operation: Literal["generate", "polish"]
    user_hint: str = ""
    current_config_json: str = ""
    allow_partial_config_output: StrictBool = False
    selected_suggestions: list[str] = Field(default_factory=list)
    focus_fields: list[str] = Field(default_factory=list)

    @field_validator("mode_label")
    @classmethod
    def _validate_mode_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("mode_label must be non-empty")
        return value


def validate_generate_config_context(
    context: dict[str, Any],
    *,
    task_type: TaskType,
    source: str = "generate_config",
) -> GenerateConfigContext:
    """Validate a GENERATE_CONFIG/POLISH_CONFIG context."""
    if task_type not in {TaskType.GENERATE_CONFIG, TaskType.POLISH_CONFIG}:
        raise ValueError(f"unsupported config task: {task_type}")
    return validate_prompt_context(
        GenerateConfigContext,
        context,
        task_type=task_type,
        source=source,
    )
