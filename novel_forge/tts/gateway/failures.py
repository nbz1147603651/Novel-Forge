"""Provider-neutral failure classification, retry and circuit-breaker policy."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from novel_forge.core.exceptions import (
    AuthenticationError,
    ContentFilterError,
    ModelGatewayError,
    RateLimitError,
)
from novel_forge.core.exceptions_framework import TimeoutException
from novel_forge.gateway.circuit_breaker import CircuitBreaker, CircuitState

ResultT = TypeVar("ResultT")


class TTSFailureKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    TIMEOUT = "timeout"
    CONTENT_FILTER = "content_filter"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    CIRCUIT_OPEN = "circuit_open"
    SEGMENT_QUALITY = "segment_quality"
    VOICE_IDENTITY = "voice_identity"
    INTERNAL = "internal"


@dataclass(frozen=True)
class TTSFailureDecision:
    kind: TTSFailureKind
    retryable: bool
    counts_toward_breaker: bool
    retry_after_s: float = 0.0
    error_code: str = ""


class TTSProviderFailure(ModelGatewayError):
    """Normalized failure emitted by the TTS provider boundary."""

    def __init__(
        self,
        provider: str,
        decision: TTSFailureDecision,
        message: str,
    ) -> None:
        self.provider = provider
        self.decision = decision
        self.failure_kind = decision.kind
        self.retryable = decision.retryable
        self.retry_after_s = decision.retry_after_s
        super().__init__(
            message,
            is_transient=decision.retryable,
            context={
                "provider": provider,
                "failure_kind": decision.kind.value,
                "retryable": decision.retryable,
                "retry_after_s": decision.retry_after_s,
                "provider_error_code": decision.error_code,
            },
        )
        self.error_code = f"tts_{decision.kind.value}"


def _exception_chain(exc: Exception) -> list[Exception]:
    result: list[Exception] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while isinstance(current, Exception) and id(current) not in seen:
        seen.add(id(current))
        result.append(current)
        current = current.__cause__ or current.__context__
    return result


def _status_code(chain: list[Exception]) -> int:
    for item in chain:
        context = getattr(item, "context", {})
        values: list[Any] = []
        if isinstance(context, dict):
            values.extend((context.get("status_code"), context.get("http_status")))
        response = getattr(item, "response", None)
        values.append(getattr(response, "status_code", None))
        for value in values:
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _retry_after(chain: list[Exception], default: float) -> float:
    for item in chain:
        context = getattr(item, "context", {})
        if not isinstance(context, dict):
            continue
        for key in ("retry_after_s", "retry_after"):
            try:
                value = float(context.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return max(0.0, default)


def classify_tts_failure(
    exc: Exception,
    *,
    provider_boundary: bool = True,
    default_retry_after_s: float = 0.0,
) -> TTSFailureDecision:
    """Classify typed and legacy adapter failures into stable retry semantics."""

    if isinstance(exc, TTSProviderFailure):
        return exc.decision
    chain = _exception_chain(exc)
    message = " ".join(str(item) for item in chain).lower()
    status = _status_code(chain)
    error_code = str(getattr(exc, "error_code", "") or "")

    if (
        any(isinstance(item, RateLimitError) for item in chain)
        or status == 429
        or any(
            marker in message for marker in ("rate limit", "rate_limit", "too many requests", "rpm")
        )
    ):
        return TTSFailureDecision(
            TTSFailureKind.RATE_LIMIT,
            retryable=True,
            counts_toward_breaker=True,
            retry_after_s=_retry_after(chain, default_retry_after_s),
            error_code=error_code or "rate_limit",
        )
    if (
        any(isinstance(item, AuthenticationError) for item in chain)
        or status in {401, 403}
        or any(
            marker in message
            for marker in (
                "authentication",
                "unauthorized",
                "forbidden",
                "api key is required",
                "secretid and secretkey are required",
                "invalid api key",
            )
        )
    ):
        return TTSFailureDecision(
            TTSFailureKind.AUTHENTICATION,
            retryable=False,
            counts_toward_breaker=False,
            error_code=error_code or "authentication_error",
        )
    if any(isinstance(item, ContentFilterError) for item in chain) or any(
        marker in message for marker in ("content filter", "content_filter", "safety block")
    ):
        return TTSFailureDecision(
            TTSFailureKind.CONTENT_FILTER,
            retryable=False,
            counts_toward_breaker=False,
            error_code=error_code or "content_filter",
        )
    if any(isinstance(item, (TimeoutException, asyncio.TimeoutError)) for item in chain) or any(
        marker in message for marker in ("timed out", "timeout", "readtimeout", "connecttimeout")
    ):
        return TTSFailureDecision(
            TTSFailureKind.TIMEOUT,
            retryable=True,
            counts_toward_breaker=True,
            error_code=error_code or "timeout",
        )
    if "片段质量门" in message or "segment quality" in message:
        return TTSFailureDecision(
            TTSFailureKind.SEGMENT_QUALITY,
            retryable=True,
            counts_toward_breaker=False,
            error_code="segment_quality",
        )
    if provider_boundary and (
        any(
            isinstance(item, (ValueError, NotImplementedError, FileNotFoundError)) for item in chain
        )
        or any(
            marker in message
            for marker in (
                "reference audio is missing",
                "reference audio file",
                "voice embedding is missing",
                "does not support",
                "invalid request",
            )
        )
        or 400 <= status < 500
    ):
        return TTSFailureDecision(
            TTSFailureKind.INVALID_REQUEST,
            retryable=False,
            counts_toward_breaker=False,
            error_code=error_code or "invalid_request",
        )
    if not provider_boundary:
        return TTSFailureDecision(
            TTSFailureKind.INTERNAL,
            retryable=True,
            counts_toward_breaker=False,
            error_code=error_code or "internal",
        )
    transient = any(
        isinstance(item, ModelGatewayError) and bool(getattr(item, "is_transient_error", False))
        for item in chain
    )
    unavailable = (
        transient
        or status >= 500
        or any(
            marker in message
            for marker in (
                "connection",
                "network",
                "temporarily unavailable",
                "service unavailable",
                "request error",
                "empty audio",
                "invalid wav",
                "non-binary audio",
            )
        )
    )
    return TTSFailureDecision(
        TTSFailureKind.PROVIDER_UNAVAILABLE if unavailable else TTSFailureKind.PROVIDER_ERROR,
        retryable=True,
        counts_toward_breaker=True,
        error_code=error_code or ("provider_unavailable" if unavailable else "provider_error"),
    )


class TTSProviderFailurePolicy:
    """Apply one retry classification and breaker policy to every adapter."""

    def __init__(
        self,
        provider: str,
        *,
        failure_threshold: int,
        recovery_timeout_s: float,
        enabled: bool,
        rate_limit_cooldown_s: float,
    ) -> None:
        self.provider = provider
        self.rate_limit_cooldown_s = max(0.0, rate_limit_cooldown_s)
        self.breaker = CircuitBreaker(
            provider,
            failure_threshold=failure_threshold,
            recovery_timeout_s=recovery_timeout_s,
            enabled=enabled,
        )

    async def execute(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        if not self.breaker.allow_request():
            retry_after = self.breaker.time_until_half_open() or 0.0
            decision = TTSFailureDecision(
                TTSFailureKind.CIRCUIT_OPEN,
                retryable=True,
                counts_toward_breaker=False,
                retry_after_s=retry_after,
                error_code="circuit_open",
            )
            raise TTSProviderFailure(
                self.provider,
                decision,
                f"TTS provider circuit is open; retry after {retry_after:.1f}s",
            )
        try:
            result = await operation()
        except Exception as exc:
            decision = classify_tts_failure(
                exc,
                default_retry_after_s=self.rate_limit_cooldown_s,
            )
            if decision.counts_toward_breaker or self.breaker.state == CircuitState.HALF_OPEN:
                self.breaker.record_failure()
            raise TTSProviderFailure(self.provider, decision, str(exc)) from exc
        self.breaker.record_success()
        return result
