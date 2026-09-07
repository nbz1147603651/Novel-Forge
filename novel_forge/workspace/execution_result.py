"""Shared execution result contracts for workspace entrypoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")
StepCallback = Callable[[str, Any], None] | None


@dataclass(frozen=True)
class ExecutionResult(Generic[T]):
    """Project-aware wrapper around a workspace runner result."""

    project_id: str
    result: T

