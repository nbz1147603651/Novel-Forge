"""Execution-scoped control-plane lineage context.

The control plane remains metadata-only, but model-call and stage records need
to know which application-service attempt produced them.  A ``ContextVar``
keeps that correlation thread- and task-safe for Desktop, CLI, and API paths
without changing individual pipeline-step signatures.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class ControlPlaneExecutionContext:
    """Immutable identity of the currently executing application-service run."""

    work_unit_id: str = ""
    run_attempt_id: str = ""
    project_id: str = ""


_CURRENT_EXECUTION: ContextVar[ControlPlaneExecutionContext | None] = ContextVar(
    "novel_forge_control_plane_execution",
    default=None,
)


def get_current_execution_context() -> ControlPlaneExecutionContext | None:
    """Return the current execution context, if the caller runs under a WorkUnit."""

    return _CURRENT_EXECUTION.get()


def set_execution_context(
    *,
    work_unit_id: str,
    run_attempt_id: str,
    project_id: str,
) -> Token[ControlPlaneExecutionContext | None]:
    """Set the current execution context and return the reset token."""

    return _CURRENT_EXECUTION.set(
        ControlPlaneExecutionContext(
            work_unit_id=work_unit_id,
            run_attempt_id=run_attempt_id,
            project_id=project_id,
        )
    )


def reset_execution_context(token: Token[ControlPlaneExecutionContext | None]) -> None:
    """Restore the execution context that preceded ``set_execution_context``."""

    _CURRENT_EXECUTION.reset(token)


@contextmanager
def bind_execution_context(
    *,
    work_unit_id: str,
    run_attempt_id: str,
    project_id: str,
) -> Iterator[ControlPlaneExecutionContext]:
    """Bind a control-plane identity for the duration of a job execution."""

    context = ControlPlaneExecutionContext(
        work_unit_id=work_unit_id,
        run_attempt_id=run_attempt_id,
        project_id=project_id,
    )
    token = _CURRENT_EXECUTION.set(context)
    try:
        yield context
    finally:
        _CURRENT_EXECUTION.reset(token)
