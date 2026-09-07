"""Typed prompt-context validation helpers for high-risk render paths."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ValidationError as NovelForgeValidationError


class PromptContextModel(BaseModel):
    """Base model for prompt contexts that intentionally allow extra fields."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


ContextModelT = TypeVar("ContextModelT", bound=PromptContextModel)


def validate_prompt_context(
    model_type: type[ContextModelT],
    context: Mapping[str, Any],
    *,
    task_type: TaskType,
    source: str,
) -> ContextModelT:
    """Validate a prompt context and raise a structured project exception."""
    try:
        return model_type.model_validate(dict(context))
    except PydanticValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False)
        summary = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', ()))}: {item.get('msg')}"
            for item in errors[:8]
        )
        raise NovelForgeValidationError(
            (
                "Prompt context validation failed "
                f"for {task_type.value} from {source}: {summary}"
            ),
            field="prompt_context",
            value={
                "task": task_type.value,
                "source": source,
                "errors": errors[:12],
            },
            cause=exc,
        ) from exc


def is_prompt_mapping(value: Any) -> bool:
    """Return whether a value can be consumed by Jinja2 as a mapping-like object."""
    return isinstance(value, Mapping) or isinstance(value, BaseModel) or hasattr(value, "__dict__")


def has_prompt_fields(value: Any, required_fields: tuple[str, ...]) -> bool:
    """Check required prompt fields across dicts, Pydantic models, and plain objects."""
    if isinstance(value, Mapping):
        return all(field in value for field in required_fields)
    if isinstance(value, BaseModel):
        fields = set(type(value).model_fields)
        return all(field in fields or hasattr(value, field) for field in required_fields)
    return all(hasattr(value, field) for field in required_fields)
