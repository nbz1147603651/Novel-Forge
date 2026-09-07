"""Custom exception hierarchy for Novel Forge.

Unified hierarchy: ``NovelForgeError`` is a single-inheritance alias for
the structured ``NovelForgeException`` framework base class.

Concrete exception types (e.g. ``ConsistencyViolationError``) use
multiple inheritance to combine ``NovelForgeError`` with a semantic
category class (``ValidationException``, ``LLMException``, etc.),
creating a diamond hierarchy resolved cleanly by Python's MRO.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from novel_forge.core.exceptions_framework import (
    ConfigurationException,
    LLMException,
    NovelForgeException,
    PersistenceException,
    TimeoutException,
    ValidationException,
)

__all__ = [
    "AuthenticationError",
    "ARCHIVE_QUALITY_BLOCK_KINDS",
    "BLOCK_KIND_ALIGNMENT_QUALITY",
    "BLOCK_KIND_ARCHIVE_QUALITY",
    "BLOCK_KIND_CARRY_FORWARD",
    "BLOCK_KIND_CHAPTER_SESSION_STALE",
    "BLOCK_KIND_CONTRACT_AUDIT",
    "BLOCK_KIND_EVAL_QUALITY",
    "BLOCK_KIND_INPUT_INTEGRITY",
    "BLOCK_KIND_STATE_ADJUDICATION",
    "BudgetExceededError",
    "ChapterSessionStaleError",
    "ComplianceViolationError",
    "ConfigurationException",
    "ConsistencyError",
    "ConsistencyViolationError",
    "classify_archive_quality_block",
    "ContentFilterError",
    "ContextLengthError",
    "FinalReportFreshnessError",
    "LLMException",
    "ModelGatewayError",
    "NovelForgeError",
    "NovelForgeException",
    "PersistenceException",
    "PipelineError",
    "RateLimitError",
    "RecoveryTarget",
    "RuntimeConfigError",
    "StateError",
    "StorageError",
    "TaskCircuitOpenError",
    "TimeoutException",
    "ValidationError",
    "ValidationException",
]

BLOCK_KIND_ALIGNMENT_QUALITY = "alignment_quality"
"""Archive gate blocked because the verified text misses planned beats."""

BLOCK_KIND_ARCHIVE_QUALITY = "archive_quality"
"""Archive gate blocked by a verified quality issue without a narrower lane."""

BLOCK_KIND_CARRY_FORWARD = "carry_forward"
BLOCK_KIND_CHAPTER_SESSION_STALE = "chapter_session_stale"
"""Chapter-studio session drifted from the on-disk checkpoint; refresh required."""
BLOCK_KIND_CONTRACT_AUDIT = "contract_audit"
BLOCK_KIND_STATE_ADJUDICATION = "state_adjudication"
BLOCK_KIND_EVAL_QUALITY = "eval_quality"
BLOCK_KIND_INPUT_INTEGRITY = "input_integrity"

# Archive guards run only after their reports have been checked against the
# exact text being persisted.  Keep the set and the legacy-message classifier
# in the core exception contract so API, Desktop and CLI cannot drift into
# separate recovery semantics.
ARCHIVE_QUALITY_BLOCK_KINDS = frozenset(
    {
        BLOCK_KIND_ALIGNMENT_QUALITY,
        BLOCK_KIND_ARCHIVE_QUALITY,
        BLOCK_KIND_EVAL_QUALITY,
    }
)


def classify_archive_quality_block(
    messages: list[str],
    *,
    default_to_generic: bool = False,
) -> str:
    """Classify an archive hard-block for the shared recovery state machine.

    ``default_to_generic`` is only for the archive gate itself, where every
    message is known to be a verified quality failure.  Consumers of old
    checkpoints leave it false so an unrelated consistency exception cannot
    accidentally enter a text-repair loop.
    """
    if any("必须承接的开放项" in message for message in messages):
        return BLOCK_KIND_CARRY_FORWARD
    if any("对齐分" in message and "低于归档阈值" in message for message in messages):
        return BLOCK_KIND_ALIGNMENT_QUALITY
    if any("评估分" in message and "低于最低可接受线" in message for message in messages):
        return BLOCK_KIND_EVAL_QUALITY
    if any("连贯分" in message and "低于硬阻断线" in message for message in messages):
        return BLOCK_KIND_ARCHIVE_QUALITY
    if any("因果分" in message and "低于硬阻断线" in message for message in messages):
        return BLOCK_KIND_ARCHIVE_QUALITY
    if any("章节质量仍存在阻断级问题" in message for message in messages):
        return BLOCK_KIND_ARCHIVE_QUALITY
    return BLOCK_KIND_ARCHIVE_QUALITY if default_to_generic else ""


class RecoveryTarget(StrEnum):
    """The only legal recovery scopes for a consistency failure.

    Recovery scope is a correctness decision, not a UI hint.  A text-level
    failure must never silently widen into a fresh plan and full chapter draft.
    """

    PLAN = "plan"
    DRAFT = "draft"
    WAVE = "wave"
    MANUAL = "manual"
    SEMANTIC = "semantic"

    @property
    def permits_plan_replan(self) -> bool:
        """Whether this target authorizes rebuilding Bridge + Plan.

        A recovery target is an ownership boundary, not a severity label.  In
        particular, ``DRAFT``, ``WAVE`` and ``MANUAL`` must never be widened
        into a plan regeneration by a generic exception handler.
        """

        return self is RecoveryTarget.PLAN


class NovelForgeError(NovelForgeException):
    """Base exception for all Novel Forge errors (unified with framework)."""

    def __init__(self, message: str = "", error_code: str = "novel_forge_error", **kwargs: Any) -> None:
        super().__init__(message=message, error_code=error_code, **kwargs)


class ConsistencyViolationError(NovelForgeError, ValidationException):
    """Raised when a CanonDelta violates consistency rules.

    The ``violation_kind`` field classifies the failure so the replan loop
    can route it correctly:

    - ``plan_fixable``: the plan itself caused the violation (e.g. contract
      breach).  ``replan_target=RecoveryTarget.PLAN`` → re-run Bridge + Plan.
    - ``execution_fixable``: a generation-stage execution issue (DRAFT/WAVE
      output quality).  ``replan_target=RecoveryTarget.WAVE`` or ``DRAFT`` keeps
      the plan intact for a stage-scoped recovery; until a caller explicitly
      implements that recovery, it fails closed rather than re-planning.
    - ``wave_post_condition``: WAVE post-condition check failed after the
      repair lane.  ``replan_target=RecoveryTarget.MANUAL`` → preserve the
      checkpoint and surface the exact repair failure; do not replan.
    """

    def __init__(
        self,
        violations: list[str],
        *,
        violation_kind: str = "plan_fixable",
        failed_stage: str = "",
        replan_target: RecoveryTarget = RecoveryTarget.MANUAL,
        block_kind: str = "",
    ) -> None:
        # A verified archive-quality block is always repairable at execution
        # scope.  Enforce that invariant here rather than trusting each
        # producer to remember three coupled arguments.  This is deliberately
        # narrow: other consistency failures keep their existing defaults.
        if block_kind in ARCHIVE_QUALITY_BLOCK_KINDS:
            if violation_kind == "plan_fixable":
                violation_kind = "execution_fixable"
            if not failed_stage:
                failed_stage = "archive_quality_gate"
            if RecoveryTarget(replan_target) is RecoveryTarget.MANUAL:
                replan_target = RecoveryTarget.DRAFT
        self.violations = violations
        self.violation_kind = violation_kind
        self.failed_stage = failed_stage
        self.replan_target = RecoveryTarget(replan_target)
        self.block_kind = block_kind
        msg = (
            f"Consistency violations ({len(violations)}): "
            + "; ".join(violations[:5])
        )
        # Call NovelForgeException.__init__ directly
        NovelForgeException.__init__(
            self, message=msg, error_code="consistency_violation",
            context={
                "violations": violations[:10],
                "violation_kind": violation_kind,
                "failed_stage": failed_stage,
                "replan_target": replan_target,
                "block_kind": block_kind,
            },
        )


class ComplianceViolationError(NovelForgeError, ValidationException):
    """Raised when prompt or output violates compliance rules."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        NovelForgeException.__init__(
            self, message=f"Compliance violation: {reason}",
            error_code="compliance_violation",
            context={"reason": reason},
        )


class ModelGatewayError(NovelForgeError, LLMException):
    """Raised on model provider communication failures."""

    def __init__(self, message: str = "", is_transient: bool = False, **kwargs: Any) -> None:
        self.is_transient_error = is_transient
        # Stream payloads can be large and may contain user-authored material.
        # Keep them off ``context`` (which is rendered by ``__str__`` and copied
        # into durable error summaries) while still making a validated recovery
        # candidate available to the LLM service.
        self.partial_text = ""
        self.partial_reasoning = ""
        self.partial_provider = ""
        self.partial_model_id = ""
        extra_context = kwargs.pop("context", None)
        context: dict[str, Any] = {"is_transient": is_transient}
        if isinstance(extra_context, dict):
            context.update(extra_context)
        # Keep structured gateway diagnostics instead of silently discarding
        # keyword arguments supplied by aggregation/failover layers.
        context.update({str(key): value for key, value in kwargs.items()})
        NovelForgeException.__init__(
            self, message=message, error_code="model_gateway_error",
            context=context,
        )

    def attach_partial_stream(
        self,
        *,
        text: str,
        reasoning: str = "",
        provider: str = "",
        model_id: str = "",
    ) -> ModelGatewayError:
        """Attach the longest observed stream fragment without logging its body."""

        candidate = str(text or "")
        if len(candidate) < len(self.partial_text):
            return self
        self.partial_text = candidate
        self.partial_reasoning = str(reasoning or "")
        self.partial_provider = str(provider or "")
        self.partial_model_id = str(model_id or "")
        if candidate:
            self.context["partial_stream"] = {
                "text_length": len(candidate),
                "reasoning_length": len(self.partial_reasoning),
                "provider": self.partial_provider,
                "model_id": self.partial_model_id,
            }
        return self


class TaskCircuitOpenError(ModelGatewayError):
    """Raised when a task's circuit breaker is open and LLM calls are rejected.

    This is distinct from the provider-level ``CircuitBreakerOpenError``
    in ``gateway/circuit_breaker.py`` — it protects individual ``TaskType``
    paths rather than specific providers.
    """

    def __init__(self, task_type: Any, retry_after_s: float) -> None:
        from novel_forge.common.constants import TaskType  # local import to avoid circular
        self.task_type: TaskType = task_type
        self.retry_after_s = retry_after_s
        super().__init__(
            message=(
                f"Task circuit breaker OPEN for '{task_type.value}'. "
                f"Retry after {retry_after_s:.1f}s."
            ),
        )
        self.error_code = "task_circuit_open"
        self.context.update({
            "task_type": task_type.value,
            "retry_after_s": retry_after_s,
        })


class RateLimitError(ModelGatewayError):
    """Raised when API rate limit is exceeded (HTTP 429)."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(message, is_transient=True, **kwargs)
        self.error_code = "rate_limit"
        self.context["error_type"] = "rate_limit"


class AuthenticationError(ModelGatewayError):
    """Raised when API authentication fails (HTTP 401/403)."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(message, is_transient=False, **kwargs)
        self.error_code = "authentication_error"
        self.context["error_type"] = "authentication_error"


class ContextLengthError(ModelGatewayError):
    """Raised when prompt exceeds model context window."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(message, is_transient=False, **kwargs)
        self.error_code = "context_length"
        self.context["error_type"] = "context_length"


class ContentFilterError(ModelGatewayError):
    """Raised when content is blocked by provider safety filters."""

    def __init__(self, message: str = "", **kwargs: Any) -> None:
        super().__init__(message, is_transient=False, **kwargs)
        self.error_code = "content_filter"
        self.context["error_type"] = "content_filter"


class StorageError(NovelForgeError, PersistenceException):
    """Raised on persistence layer failures."""

    def __init__(self, message: str = "", operation: str | None = None, **kwargs: Any) -> None:
        NovelForgeException.__init__(
            self, message=message, error_code="storage_error",
            context={"operation": operation},
        )


class PipelineError(NovelForgeError):
    """Raised when a pipeline step fails irrecoverably."""

    def __init__(self, step_name: str, detail: str) -> None:
        self.step_name = step_name
        self.detail = detail
        NovelForgeException.__init__(
            self, message=f"Pipeline step '{step_name}' failed: {detail}",
            error_code="pipeline_error",
            context={"step_name": step_name, "detail": detail},
        )


class FinalReportFreshnessError(NovelForgeError):
    """Raised when final prose cannot be paired with authoritative review reports."""

    def __init__(
        self,
        *,
        chapter_number: int,
        expected_text_hash: str,
        dimensions: list[str],
        detail: str,
        cause: Exception | None = None,
    ) -> None:
        self.chapter_number = chapter_number
        self.expected_text_hash = expected_text_hash
        self.dimensions = list(dimensions)
        message = (
            f"第 {chapter_number} 章终稿质量报告不可用，已阻止归档：{detail}"
        )
        NovelForgeException.__init__(
            self,
            message=message,
            error_code="final_report_freshness",
            context={
                "chapter_number": chapter_number,
                "expected_text_hash": expected_text_hash,
                "dimensions": self.dimensions,
                "retryable": False,
                "recovery_actions": [
                    {
                        "action": "retry_final_review",
                        "description": "保留当前终稿，重试终稿质量复检后再归档。",
                    },
                    {
                        "action": "inspect_report_refresh",
                        "description": "查看失败维度的模型输出与运行日志。",
                    },
                ],
            },
            cause=cause,
        )


class ChapterSessionStaleError(NovelForgeError):
    """章节工作台 session 与磁盘 checkpoint 不一致，需要刷新章节快照后重试。

    当后台 asyncio 任务推进了 checkpoint（如 plan_checkpoint -> guard_checkpoint），
    或上游 canon 水位前进，但前端仍持有旧 checkpoint_id 提交决策时触发。
    前端识别 ``error_code="chapter_session_stale"`` 后可自动重拉章节上下文快照，
    刷新 checkpoint 选项后让用户重试，而非弹"请刷新后重试"裸错误。
    """

    def __init__(
        self,
        *,
        message: str,
        kind: str,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.kind = kind
        full_context: dict[str, Any] = {
            "block_kind": BLOCK_KIND_CHAPTER_SESSION_STALE,
            "stale_kind": kind,
            "retryable": True,
            "recovery_actions": [
                {
                    "action": "refresh_chapter_studio",
                    "description": "自动重新拉取章节上下文快照，刷新 checkpoint 选项后重试。",
                }
            ],
        }
        if context:
            full_context.update(context)
        NovelForgeException.__init__(
            self,
            message=message,
            error_code="chapter_session_stale",
            context=full_context,
            cause=cause,
        )


class RuntimeConfigError(NovelForgeError, ConfigurationException):
    """Raised when runtime config cannot be loaded or validated."""

    def __init__(self, message: str = "", config_key: str | None = None, **kwargs: Any) -> None:
        NovelForgeException.__init__(
            self, message=message, error_code="runtime_config_error",
            context={"config_key": config_key},
        )


class BudgetExceededError(NovelForgeError):
    """Raised when API spending exceeds the configured budget limit."""

    def __init__(self, period: str, spent: float, limit: float) -> None:
        self.period = period
        self.spent = spent
        self.limit = limit
        NovelForgeException.__init__(
            self,
            message=(
                f"预算超限（{period}）：已花费 ${spent:.4f}，"
                f"上限 ${limit:.2f}。请调整 NOVEL_FORGE_BUDGET_{period.upper()}_USD 或等待周期重置。"
            ),
            error_code="budget_exceeded",
            context={"period": period, "spent": spent, "limit": limit},
        )


class ValidationError(NovelForgeError, ValidationException):
    """Raised when input data fails validation rules.

    Example:
        >>> raise ValidationError("age must be positive", field="age", value=-5)
    """

    def __init__(self, message: str, field: str | None = None, value: Any = None, cause: Exception | None = None) -> None:
        self.field = field
        self.value = value
        NovelForgeException.__init__(
            self,
            message=message,
            error_code="validation_error",
            context={"field": field, "value": value},
            cause=cause,
        )


class StateError(NovelForgeError):
    """Raised when an operation encounters an invalid or inconsistent state.

    Example:
        >>> raise StateError("chapter_session", expected="draft", actual="finalized")
    """

    def __init__(self, state_name: str, expected: str | None = None, actual: str | None = None, cause: Exception | None = None) -> None:
        self.state_name = state_name
        self.expected = expected
        self.actual = actual
        msg = f"Invalid state for '{state_name}'"
        if expected and actual:
            msg = f"Invalid state for '{state_name}': expected '{expected}', got '{actual}'"
        elif expected:
            msg = f"Invalid state for '{state_name}': expected '{expected}'"
        elif actual:
            msg = f"Invalid state for '{state_name}': got '{actual}'"
        NovelForgeException.__init__(
            self,
            message=msg,
            error_code="state_error",
            context={"state_name": state_name, "expected": expected, "actual": actual},
            cause=cause,
        )


class ConsistencyError(NovelForgeError, ValidationException):
    """Raised when a consistency check fails.

    Distinct from :class:`ConsistencyViolationError` in that it is intended for
    general consistency failures rather than CanonDelta-specific violations.

    Example:
        >>> raise ConsistencyError("timeline sequence broken", details=["event A after event B"])
    """

    def __init__(self, message: str, details: list[str] | None = None, cause: Exception | None = None) -> None:
        self.details = details or []
        NovelForgeException.__init__(
            self,
            message=message,
            error_code="consistency_error",
            context={"details": self.details},
            cause=cause,
        )
