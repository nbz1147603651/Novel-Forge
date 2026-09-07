"""Core layer: schemas, config, exceptions, constants, and utilities."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.core import utils
    from novel_forge.core.context import (
        CanonContext,
        ChapterExecutionContext,
        InitLongContext,
        LLMServiceContext,
        OutlineContext,
        PromptRenderContext,
        VolumeContext,
    )

__all__ = [
    # Context objects
    "CanonContext",
    "ChapterExecutionContext",
    "InitLongContext",
    "LLMServiceContext",
    "OutlineContext",
    "PromptRenderContext",
    "VolumeContext",
    # Utilities
    "utils",
]


def __getattr__(name: str) -> Any:
    """Load core convenience exports lazily to avoid package import cycles."""
    if name == "utils":
        return importlib.import_module("novel_forge.core.utils")

    if name in {
        "CanonContext",
        "ChapterExecutionContext",
        "InitLongContext",
        "LLMServiceContext",
        "OutlineContext",
        "PromptRenderContext",
        "VolumeContext",
    }:
        context = importlib.import_module("novel_forge.core.context")
        return getattr(context, name)

    raise AttributeError(name)
