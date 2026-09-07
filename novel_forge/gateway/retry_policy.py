"""Provider-neutral retry and output-budget policy.

This module belongs to the transport boundary.  Higher-level model runtimes
and pipeline services may reuse it, while the gateway never imports pipeline
implementation details.
"""

from __future__ import annotations

import asyncio

from novel_forge.core.constants import PipelineConstants
from novel_forge.core.exceptions import (
    AuthenticationError,
    ContentFilterError,
    ContextLengthError,
    ModelGatewayError,
    RateLimitError,
)


def is_transient_llm_error(exc: Exception) -> bool:
    """Return whether a provider failure is safe to retry."""

    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, ModelGatewayError):
        return exc.is_transient_error

    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return True
    except ImportError:
        pass

    text = f"{type(exc).__name__}: {exc}".lower()
    transient_markers = (
        "timeout",
        "timed out",
        "requesttimeout",
        "internalservererror",
        "service unavailable",
        "temporarily unavailable",
        "connection",
        "network",
        "rate limit",
        "too many requests",
        "readerror",
        "read error",
        "429",
        "500",
        "502",
        "503",
        "504",
    )
    return any(marker in text for marker in transient_markers)


def classify_llm_error(exc: Exception) -> ModelGatewayError:
    """Normalize an arbitrary provider error to the gateway exception family."""

    if isinstance(exc, ModelGatewayError):
        return exc

    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in ("rate limit", "too many requests", "429")):
        return RateLimitError(str(exc))
    if any(marker in text for marker in ("auth", "unauthorized", "401", "403", "api key")):
        return AuthenticationError(str(exc))
    if any(marker in text for marker in ("context length", "token limit", "too long", "413")):
        return ContextLengthError(str(exc))
    if any(
        marker in text for marker in ("content filter", "safety", "moderation", "blocked", "422")
    ):
        return ContentFilterError(str(exc))
    return ModelGatewayError(str(exc), is_transient=is_transient_llm_error(exc))


def compute_retry_backoff(attempt: int) -> float:
    """Return exponential backoff seconds for a one-based attempt."""

    return float(
        PipelineConstants.RETRY_BACKOFF_BASE
        * (PipelineConstants.RETRY_EXPONENTIAL_BASE ** (attempt - 1))
    )


def compute_transient_retry_backoff(
    exc: Exception,
    attempt: int,
    *,
    max_wait_s: float = 600.0,
) -> float:
    """Return transport-aware backoff without applying domain policy."""

    base = compute_retry_backoff(attempt)
    if not isinstance(exc, ModelGatewayError):
        return base
    context = getattr(exc, "context", None)
    if not isinstance(context, dict):
        return base
    categories = [
        str(item or "").strip()
        for item in context.get("failure_categories", []) or []
        if str(item or "").strip()
    ]
    transient_categories = {
        "timeout",
        "network_error",
        "rate_limit",
        "server_error",
        "circuit_open",
    }
    if categories and "network_error" in categories and all(
        category in transient_categories for category in categories
    ):
        base = max(base, min(base * 5.0, max(0.0, float(max_wait_s))))
    if not categories or any(category != "circuit_open" for category in categories):
        return base
    provider_health = context.get("provider_health")
    if not isinstance(provider_health, dict):
        return base
    waits: list[float] = []
    for raw_health in provider_health.values():
        if not isinstance(raw_health, dict) or str(raw_health.get("state") or "") != "open":
            continue
        try:
            wait_s = float(raw_health.get("retry_after_s") or 0.0)
        except (TypeError, ValueError):
            continue
        if wait_s > 0:
            waits.append(wait_s)
    if not waits:
        return base
    return max(base, min(min(waits), max(0.0, float(max_wait_s))))


def escalate_retry_tokens(
    current_max_tokens: int,
    task_type: str = "",
    model_max_tokens: int | None = None,
) -> int:
    """Double output budget without exceeding the task/model ceiling."""

    task_cap = PipelineConstants.TASK_TOKEN_CAPS.get(task_type, PipelineConstants.MAX_TOKEN_CAP)
    effective_cap = max(task_cap, model_max_tokens) if model_max_tokens else task_cap
    return max(current_max_tokens, min(current_max_tokens * 2, effective_cap))


__all__ = [
    "classify_llm_error",
    "compute_retry_backoff",
    "compute_transient_retry_backoff",
    "escalate_retry_tokens",
    "is_transient_llm_error",
]
