"""Canonical projection boundary for EVALUATE prompt context."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


def _to_prompt_dto(value: Any, *, path: str) -> Any:
    """Convert application/domain values to JSON-shaped prompt DTO values.

    Prompt templates must never depend on Pydantic iteration or arbitrary
    object attribute access.  Models are projected once at the EvaluateStep
    boundary; unsupported objects fail before prompt rendering and routing.
    """

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_prompt_dto(item, path=f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_prompt_dto(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"Unsupported EVALUATE context value at {path}: {type(value).__name__}")


def project_evaluate_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return the sole canonical DTO shape consumed by ``EvaluateDraftPromptContext``."""

    source = dict(context)
    raw_beats = source.get("beats")
    if isinstance(raw_beats, BaseModel):
        beats_container = raw_beats.model_dump(mode="json")
        if not isinstance(beats_container, dict) or not isinstance(
            beats_container.get("beats"), list
        ):
            raise TypeError("EVALUATE beats model must expose a beats list")
        source["beats"] = beats_container["beats"]
    elif raw_beats is not None and not isinstance(raw_beats, list):
        raise TypeError("EVALUATE beats must be a list or StoryBeats model")

    projected = _to_prompt_dto(source, path="evaluate_context")
    if not isinstance(projected, dict):  # defensive; source is always a dict
        raise TypeError("EVALUATE context projection must produce a mapping")
    return projected


__all__ = ["project_evaluate_context"]
