"""Shared safety policy for long-chapter repair orchestration.

The repair dimensions differ in domain logic, but failures must follow one
contract: classify the exception, emit a structured event, and return the last
validated text/report pair.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from novel_forge.core.exceptions import ModelGatewayError


class RepairFailureKind(str, Enum):
    """Stable failure categories used in events and outcomes."""

    GATEWAY = "gateway"
    INTERNAL = "internal"


class RepairDimension(str, Enum):
    """Built-in repair dimensions with stable progress-event prefixes."""

    CONTINUITY = "continuity"
    CAUSAL = "causal"
    READING_POWER = "reading_power"
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"
    CONTRACT = "contract"
    WORLD_RULE = "world_rule"
    GENERIC = "generic"
    AI_FLAVOR = "ai_flavor"  # added in M2 — see docs/ai_flavor_quality.md


class RepairFailureOperation(str, Enum):
    """Repair operation phases handled by the safety policy."""

    VALIDATION = "validation"
    REPAIR = "repair"
    RECHECK = "recheck"
    POST_REPAIR = "post_repair"


class RepairFallbackAction(str, Enum):
    """Common fallback actions emitted by repair failure events."""

    SKIP_VALIDATION = "skip_validation"
    SKIP_REPAIR_KEEP_CURRENT_TEXT = "skip_repair_keep_current_text"
    ROLLBACK_TO_PRE_REPAIR_TEXT = "rollback_to_pre_repair_text"


@dataclass(frozen=True)
class RepairRoundSnapshot:
    """Rollback anchor for one repair operation."""

    stage: str | RepairDimension
    chapter_number: int
    round_number: int = 0
    text: str = ""
    report: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    kernel_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class RepairFailureOutcome:
    """Typed outcome after the safety policy handles a failed operation."""

    current_text: str
    report: Any | None
    event_name: str
    failure_reason: str
    warning: str
    action: str
    operation: RepairFailureOperation
    error_kind: RepairFailureKind
    repair_exhausted: bool = True
    is_gateway_error: bool = False


class RepairFailurePolicy:
    """Centralized failure handling for repair loops.

    Active repair loops should call this policy instead of hand-building
    rollback events. That keeps Desktop progress, logs, and text/report
    rollback semantics aligned across continuity, causal, and reading power.
    """

    def __init__(
        self,
        on_step: Callable[[str, Any], None],
        *,
        logger: Any | None = None,
    ) -> None:
        self._on_step = on_step
        self._logger = logger

    @staticmethod
    def failure_kind(exc: Exception) -> RepairFailureKind:
        if isinstance(exc, ModelGatewayError):
            return RepairFailureKind.GATEWAY
        return RepairFailureKind.INTERNAL

    @staticmethod
    def failure_reason(exc: Exception) -> str:
        if isinstance(exc, ModelGatewayError):
            return f"ModelGatewayError: {exc}"
        return f"{type(exc).__name__}: {exc}"

    def validation_failed(
        self,
        *,
        snapshot: RepairRoundSnapshot,
        exc: Exception,
        action: str | RepairFallbackAction = RepairFallbackAction.SKIP_VALIDATION,
        warning: str = "",
        event_stage: str | RepairDimension | None = None,
    ) -> RepairFailureOutcome:
        return self._handle(
            snapshot=snapshot,
            operation=RepairFailureOperation.VALIDATION,
            exc=exc,
            action=action,
            warning=warning,
            event_stage=event_stage,
            repair_exhausted=False,
        )

    def repair_failed(
        self,
        *,
        snapshot: RepairRoundSnapshot,
        exc: Exception,
        action: str | RepairFallbackAction = RepairFallbackAction.SKIP_REPAIR_KEEP_CURRENT_TEXT,
        warning: str = "",
        event_stage: str | RepairDimension | None = None,
        repair_exhausted: bool = True,
    ) -> RepairFailureOutcome:
        return self._handle(
            snapshot=snapshot,
            operation=RepairFailureOperation.REPAIR,
            exc=exc,
            action=action,
            warning=warning,
            event_stage=event_stage,
            repair_exhausted=repair_exhausted,
        )

    def recheck_failed(
        self,
        *,
        snapshot: RepairRoundSnapshot,
        exc: Exception,
        action: str | RepairFallbackAction = RepairFallbackAction.ROLLBACK_TO_PRE_REPAIR_TEXT,
        warning: str = "",
        event_stage: str | RepairDimension | None = None,
        repair_exhausted: bool = True,
    ) -> RepairFailureOutcome:
        return self._handle(
            snapshot=snapshot,
            operation=RepairFailureOperation.RECHECK,
            exc=exc,
            action=action,
            warning=warning,
            event_stage=event_stage,
            repair_exhausted=repair_exhausted,
        )

    def post_repair_check_failed(
        self,
        *,
        snapshot: RepairRoundSnapshot,
        exc: Exception,
        event_name: str,
        action: str | RepairFallbackAction,
        warning: str = "",
        repair_exhausted: bool = True,
    ) -> RepairFailureOutcome:
        return self._handle(
            snapshot=snapshot,
            operation=RepairFailureOperation.POST_REPAIR,
            exc=exc,
            action=action,
            warning=warning,
            event_name=event_name,
            repair_exhausted=repair_exhausted,
        )

    def _handle(
        self,
        *,
        snapshot: RepairRoundSnapshot,
        operation: RepairFailureOperation,
        exc: Exception,
        action: str | RepairFallbackAction,
        warning: str,
        event_stage: str | RepairDimension | None = None,
        event_name: str | None = None,
        repair_exhausted: bool,
    ) -> RepairFailureOutcome:
        kind = self.failure_kind(exc)
        reason = self.failure_reason(exc)
        stage = self._value(event_stage or snapshot.stage)
        action_value = action.value if isinstance(action, RepairFallbackAction) else str(action)
        event = event_name or f"{stage}_{operation.value}_{kind.value}_error"
        if self._logger is not None:
            log_name = (
                "warning"
                if kind == RepairFailureKind.GATEWAY
                else "exception"
                if sys.exc_info()[0] is not None
                else "error"
            )
            log = getattr(self._logger, log_name, None)
            if callable(log):
                log(
                    "%s %s failed | chapter=%s | round=%s | action=%s | %s",
                    stage,
                    operation.value,
                    snapshot.chapter_number,
                    snapshot.round_number,
                    action_value,
                    reason,
                )
        payload: dict[str, Any] = {
            "chapter": snapshot.chapter_number,
            "error": str(exc),
            "error_kind": kind.value,
            "error_type": type(exc).__name__,
            "action": action_value,
        }
        if snapshot.round_number:
            payload["round"] = snapshot.round_number
        payload.update(dict(snapshot.metadata or {}))
        if snapshot.kernel_context:
            for k, v in snapshot.kernel_context.items():
                if k not in payload:
                    payload[k] = v
        self._on_step(event, payload)
        return RepairFailureOutcome(
            current_text=snapshot.text,
            report=snapshot.report,
            event_name=event,
            failure_reason=reason,
            warning=warning or reason,
            action=action_value,
            operation=operation,
            error_kind=kind,
            repair_exhausted=repair_exhausted,
            is_gateway_error=isinstance(exc, ModelGatewayError),
        )

    @staticmethod
    def _value(value: str | RepairDimension) -> str:
        return value.value if isinstance(value, RepairDimension) else str(value)
