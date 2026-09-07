"""ModelRouter — maps TaskType to adapter + model_id."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterator

from novel_forge.common.constants import ModelTier, TaskType
from novel_forge.common.interfaces import ModelRouterProtocol
from novel_forge.core.authoring_context import (
    AuthoringAuthorityError,
    begin_authoring_call,
    check_authoring_authority,
    has_authoring_budget,
    settle_authoring_call,
)
from novel_forge.core.exceptions import BudgetExceededError, ContextLengthError, ModelGatewayError
from novel_forge.core.parsing.temperature_jitter import resolve_temperature_jitter
from novel_forge.core.parsing.token_utils import count_message_tokens
from novel_forge.core.task_catalog import DEFAULT_TASK_TIERS
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.cache import CachePolicy
from novel_forge.gateway.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from novel_forge.gateway.pricing import SpendingTracker, estimate_cost, get_pricing
from novel_forge.gateway.profiles import (
    get_model_context_window,
    get_model_max_output_tokens,
)
from novel_forge.gateway.rate_limiter import RateLimiterRegistry
from novel_forge.gateway.reasoning import normalize_thinking_mode, thinking_mode_enabled
from novel_forge.gateway.structured_output import (
    StructuredOutputMode,
    is_strict_json_schema_compatible,
    is_structured_output_cached_unsupported,
    resolve_structured_output_policy,
)
from novel_forge.gateway.task_circuit_breaker import TaskTypeCircuitBreaker
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk, to_stream_chunk
from novel_forge.obs.logger import get_logger
from novel_forge.obs.tracer import record_model_error, record_model_response


@dataclass
class TaskRouteOverride:
    """Per-task override: which provider + model to use for a specific TaskType.

    Example::

        TaskRouteOverride(provider="deepseek", model_id="deepseek-chat")
        TaskRouteOverride(provider="tongyi", model_id="qwq-32b", thinking=True)
    """

    provider: str
    model_id: str | None = None
    thinking: bool = False
    thinking_mode: str = ""
    multi_turn: bool = False


@dataclass(frozen=True)
class _RequestContextFit:
    """Lossless preflight result for one concrete provider/model route."""

    fits: bool
    prompt_tokens: int
    context_window: int
    output_reserve: int
    available_for_prompt: int
    overshoot_tokens: int
    token_count_method: str
    tokenizer_name: str
    tokenizer_backed: bool
    token_count_exact: bool

    def event_payload(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "context_window": self.context_window,
            "output_reserve": self.output_reserve,
            "available_for_prompt": self.available_for_prompt,
            "overshoot_tokens": self.overshoot_tokens,
            "token_count_method": self.token_count_method,
            "tokenizer_name": self.tokenizer_name,
            "tokenizer_backed": self.tokenizer_backed,
            "token_count_exact": self.token_count_exact,
            "required_context_preserved": True,
            "hard_truncation_allowed": False,
            "overflow_action": "route_larger_context_or_partition_complete_coverage",
        }


def _measure_request_context(
    request: ModelRequest,
    *,
    provider: str,
    model_id: str,
) -> _RequestContextFit:
    """Measure a fully rendered request without altering any message content."""

    count = count_message_tokens(
        request.messages,
        provider=provider,
        model_id=model_id,
    )
    context_window = max(1, int(get_model_context_window(model_id)))
    output_reserve = max(0, int(request.max_tokens or 0))
    available_for_prompt = max(0, context_window - output_reserve)
    overshoot_tokens = max(0, count.tokens - available_for_prompt)
    return _RequestContextFit(
        fits=overshoot_tokens == 0,
        prompt_tokens=count.tokens,
        context_window=context_window,
        output_reserve=output_reserve,
        available_for_prompt=available_for_prompt,
        overshoot_tokens=overshoot_tokens,
        token_count_method=count.method,
        tokenizer_name=count.tokenizer_name,
        tokenizer_backed=count.tokenizer_backed,
        token_count_exact=count.exact,
    )


_log = get_logger("gateway.router")


def _begin_authoring_model_call(call_id: str, request: ModelRequest, provider: str) -> None:
    estimate: float | None = None
    if has_authoring_budget():
        pricing = get_pricing(str(request.model_id or ""))
        if provider == "mock":
            estimate = 0.0
        elif pricing is not None and request.max_tokens:
            # Conservative text-token bound using serialized UTF-8 bytes plus
            # framing allowance. Use the existing price table, not a new tariff.
            size = len(json.dumps(request.messages, ensure_ascii=False).encode()) + 512 * len(
                request.messages
            )
            estimate = (size * pricing[0] + request.max_tokens * pricing[1]) / 1_000_000
    begin_authoring_call(
        call_id,
        estimate,
        {
            "provider": provider,
            "model": request.model_id,
            "task": request.task_type.value,
            "max_tokens": request.max_tokens,
        },
    )


def _settle_authoring_model_call(
    call_id: str, response: ModelResponse | None, provider: str
) -> None:
    cost: float | None = None
    if response is not None:
        if response.cost_usd > 0:
            cost = response.cost_usd
        elif provider == "mock":
            cost = 0.0
        elif response.total_tokens > 0 and get_pricing(response.model_id) is not None:
            cost = estimate_cost(
                response.model_id, response.prompt_tokens, response.completion_tokens
            )
    settle_authoring_call(call_id, cost)


RouteObserver = Callable[[str, dict[str, Any]], None]


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield an exception and its explicit/implicit causes exactly once."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, BaseException) else None


def _failure_category(exc: BaseException) -> str:
    """Classify a terminal route failure without treating all transient errors as 429s."""
    for item in _iter_exception_chain(exc):
        if isinstance(item, CircuitBreakerOpenError):
            return "circuit_open"
        if type(item).__name__ == "StreamOutputInflationError":
            # Model repetition loop / runaway JSON output: temperature-0 retry
            # may help, but a different model usually hits the same wall.
            return "stream_inflation"
        details = _extract_provider_error_details(item)
        status_raw = details.get("status_code")
        try:
            status_code = int(status_raw) if status_raw is not None else None
        except (TypeError, ValueError):
            status_code = None
        text = str(item).lower()
        type_name = type(item).__name__.lower()
        if status_code == 429 or "quota" in text or "rate limit" in text:
            return "rate_limit"
        if isinstance(item, (asyncio.TimeoutError, TimeoutError)) or "timeout" in type_name:
            return "timeout"
        if any(token in text for token in ("timed out", "timeout")):
            return "timeout"
        if isinstance(item, ConnectionError) or type_name in {
            "apiconnectionerror",
            "connecterror",
            "connectionerror",
            "networkerror",
            "readerror",
            "remoteprotocolerror",
            "transporterror",
        }:
            return "network_error"
        if any(
            token in text
            for token in (
                "connection error",
                "connection failed",
                "connection refused",
                "connection reset",
                "connecterror",
                "name resolution",
                "network is unreachable",
                "nodename nor servname",
                "remote protocol error",
                "temporary failure in name resolution",
            )
        ):
            return "network_error"
        if status_code is not None and status_code >= 500:
            return "server_error"
        if status_code in (401, 403) or "authentication" in text or "unauthorized" in text:
            return "authentication"
        if status_code is not None and 400 <= status_code < 500:
            return "client_error"
    return "other"


def _aggregate_route_failure_is_transient(failure_categories: list[str]) -> bool:
    """Classify attempted failures without treating capacity skips as attempts."""
    transient_categories = {
        "timeout",
        "network_error",
        "rate_limit",
        "server_error",
        "circuit_open",
    }
    attempted_failures = [
        category for category in failure_categories if category != "context_length"
    ]
    return bool(attempted_failures) and all(
        category in transient_categories for category in attempted_failures
    )


def _is_provider_health_failure(category: str) -> bool:
    """Return whether one exhausted logical route should consume breaker budget."""
    return category in {"timeout", "network_error", "rate_limit", "server_error"}


_RESULT_ONLY_STRUCTURED_TASKS = frozenset(
    {
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
    }
)

_CACHEABLE_TASK_TYPES: frozenset[TaskType] = frozenset(
    {
        TaskType.EVALUATE,
        TaskType.EVALUATE_READING_POWER,
        TaskType.CRITIC_CONTINUITY,
        TaskType.CRITIC_CHARACTER,
        TaskType.CRITIC_CAUSAL,
        TaskType.CRITIC_STRENGTHS,
        TaskType.CHECK_CHAPTER,
        TaskType.CHECK_ALIGNMENT,
        TaskType.CHECK_CONTINUITY,
        TaskType.CHECK_EDITORIAL,
        TaskType.VALIDATE_CAUSAL,
        TaskType.VALIDATE_SCENE_PLAN,
        TaskType.EXTRACT_CANON,
        TaskType.EXTRACT_CANON_DELTA,
        TaskType.EXTRACT_CREATIVE_REPORT,
        TaskType.EXTRACT_MOTIFS,
        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
        TaskType.EXTRACT_RELATIONSHIP_DELTAS,
        TaskType.EXTRACT_PLOT_THREAD_DELTAS,
        TaskType.EXTRACT_EXPRESSION_OBSERVATIONS,
        TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
        TaskType.SUMMARIZE_CHAPTER,
        TaskType.SUMMARIZE_VOLUME,
        TaskType.SUMMARIZE_ARC,
        TaskType.SUMMARIZE_SCENE,
        TaskType.BOOK_CONSISTENCY,
        TaskType.BOOK_CONSISTENCY_NAMING,
        TaskType.BOOK_CONSISTENCY_TIMELINE,
        TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
        TaskType.BOOK_CONSISTENCY_VERIFY,
        TaskType.GUARD_CONSTRAINT_CHECK,
        TaskType.MACRO_GUARD_AUDIT,
        TaskType.ELEMENT_PROGRESS_ARBITER,
        TaskType.VERIFY_COMPRESSION,
        TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
        TaskType.BOOK_EDITORIAL_AUDIT,
        TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT,
        TaskType.BOOK_EDITORIAL_VOICE_AUDIT,
        TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT,
        TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT,
        TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        TaskType.ADJUDICATE_FACT_CONFLICT,
        TaskType.ADJUDICATE_FINAL_STATE,
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        TaskType.ADJUDICATE_BLUEPRINT_COHERENCE,
        TaskType.ADJUDICATE_OUTLINE_INHERITANCE,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
        TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
        TaskType.VOLUME_AUDIT,
    }
)

_LENGTH_TRUNCATION_MAX_RETRIES = 8

# Optional openai SDK / httpx transport exception types used to treat
# SDK-level timeouts the same as asyncio.TimeoutError in the retry loop.
#
# These SDKs are heavy (``openai`` pulls in pydantic + httpx + anyio, ~330ms
# import time).  They are imported lazily on first use so that simply importing
# ``novel_forge.gateway.router`` (which happens at desktop startup, before any
# model call) does not pay that cost.  Callers go through ``_transport_timeout_errors()``
# / ``_httpx_transport_errors()`` / ``_openai_connection_error()`` instead of
# touching the modules at import time.
_OPENAI_TIMEOUT_ERRORS: tuple[type[Exception], ...] | None = None
_HTTPX_TRANSPORT_ERRORS: tuple[type[Exception], ...] | None = None
_OAI_CONNECTION_ERROR: type[Exception] | None = None
_TRANSPORT_TIMEOUT_ERRORS_CACHE: tuple[type[Exception], ...] | None = None


def _openai_timeout_errors() -> tuple[type[Exception], ...]:
    """Lazily resolve the openai SDK timeout/connection exception types."""
    global _OPENAI_TIMEOUT_ERRORS
    if _OPENAI_TIMEOUT_ERRORS is not None:
        return _OPENAI_TIMEOUT_ERRORS
    try:
        from openai import APIConnectionError as _OAIConnectionError
        from openai import APITimeoutError as _OAITimeoutError

        global _OAI_CONNECTION_ERROR
        _OAI_CONNECTION_ERROR = _OAIConnectionError
        _OPENAI_TIMEOUT_ERRORS = (_OAITimeoutError, _OAIConnectionError)
    except ImportError:  # openai not installed
        _OPENAI_TIMEOUT_ERRORS = ()
    return _OPENAI_TIMEOUT_ERRORS


def _httpx_transport_errors() -> tuple[type[Exception], ...]:
    """Lazily resolve the httpx transport-level exception types.

    httpx transport-level errors (e.g. ReadError during streaming) are treated
    as transient timeouts.  These occur when the provider drops the connection
    mid-stream (common with Tongyi / DashScope long responses).
    """
    global _HTTPX_TRANSPORT_ERRORS
    if _HTTPX_TRANSPORT_ERRORS is not None:
        return _HTTPX_TRANSPORT_ERRORS
    try:
        import httpx as _httpx

        _HTTPX_TRANSPORT_ERRORS = (
            _httpx.ReadError,
            _httpx.RemoteProtocolError,
            _httpx.ConnectError,
            _httpx.ReadTimeout,
            _httpx.WriteTimeout,
            _httpx.ConnectTimeout,
        )
    except ImportError:
        _HTTPX_TRANSPORT_ERRORS = ()
    return _HTTPX_TRANSPORT_ERRORS


def _openai_connection_error() -> type[Exception] | None:
    """Return the openai ``APIConnectionError`` type, resolving it lazily.

    Guaranteed to be populated after :func:`_openai_timeout_errors` has run;
    callers that need this type should call this function (which itself warms
    the openai import) rather than reading the module global directly.
    """
    if _OAI_CONNECTION_ERROR is None:
        _openai_timeout_errors()
    return _OAI_CONNECTION_ERROR


def _transport_timeout_errors() -> tuple[type[Exception], ...]:
    """Union of asyncio.TimeoutError + openai + httpx transport errors.

    Built once and cached.  Used as the ``except`` tuple in the retry loop so
    we cannot simply inline the lazy resolvers there.
    """
    global _TRANSPORT_TIMEOUT_ERRORS_CACHE
    if _TRANSPORT_TIMEOUT_ERRORS_CACHE is not None:
        return _TRANSPORT_TIMEOUT_ERRORS_CACHE
    _TRANSPORT_TIMEOUT_ERRORS_CACHE = (
        asyncio.TimeoutError,
        *_openai_timeout_errors(),
        *_httpx_transport_errors(),
    )
    return _TRANSPORT_TIMEOUT_ERRORS_CACHE


def _normalize_provider_for_capability(provider: str) -> str:
    """Extract the bare provider name from a route provider field.

    ``TaskRouteOverride.provider`` is *usually* the bare provider name (e.g.
    ``"minimax"``), but profile-based routing historically stored the full
    ``profile_id`` (e.g. ``"minimax:MiniMax-M3"``) there.  The structured-output
    capability resolver and the runtime-unsupported cache both key on the bare
    provider name, so a profile_id-style value would silently fall through to
    the ``provider_prompt_only_default`` tail and reject every route.

    This helper strips a ``:model`` suffix (if present) so capability lookups
    work regardless of which form the caller stored.
    """
    name = (provider or "").strip()
    if ":" in name:
        name = name.split(":", 1)[0].strip()
    return name.lower()


def _native_structured_output_skip_reason(
    request: ModelRequest,
    *,
    provider: str,
    model_id: str | None,
    thinking: bool,
) -> str | None:
    """Explain why one route cannot serve a native-JSON-required request.

    The adapter will independently build the provider request plan immediately
    before calling the API.  This preflight uses the same capability resolver
    so a prompt-only route can be skipped *before* it consumes a large strict
    JSON request.  JSON-object mode qualifies: it guarantees valid JSON even
    where the provider cannot enforce the complete schema.
    """
    if not request.require_native_structured_output:
        return None
    if not request.response_json_schema:
        return "request_has_no_json_schema"

    # Normalize in case the route stored a profile_id (e.g. "minimax:MiniMax-M3")
    # instead of the bare provider name.  See _normalize_provider_for_capability.
    provider_key = _normalize_provider_for_capability(provider)
    decision = resolve_structured_output_policy(
        provider=provider_key,
        model_id=model_id,
        has_schema=True,
        response_schema_strict=bool(request.response_schema_strict),
        strict_schema_compatible=is_strict_json_schema_compatible(request.response_json_schema),
        thinking=thinking,
        runtime_unsupported=is_structured_output_cached_unsupported(provider_key, model_id),
    )
    if decision.mode != StructuredOutputMode.PROMPT_ONLY:
        return None
    return decision.reason or StructuredOutputMode.PROMPT_ONLY.value


def _extract_embedded_error_payload(message: str) -> dict[str, Any] | None:
    """Extract provider error payload from stringified exception text.

    OpenAI-compatible SDK errors are often formatted like:
    "Error code: 500 - {'error': {...}, 'request_id': '...'}"
    """
    if len(message) > 10000:
        return None
    match = re.search(r"-\s*(\{.*\})\s*$", message, flags=re.DOTALL)
    if not match:
        return None
    raw_payload = match.group(1)
    try:
        parsed = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Some SDKs embed Python-dict style payloads with single quotes/None.
        try:
            parsed = ast.literal_eval(raw_payload)
        except (SyntaxError, ValueError, TypeError, MemoryError):
            return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _extract_provider_error_details(exc: Exception) -> dict[str, Any]:
    """Normalize provider exceptions into structured fields for logs."""
    msg = str(exc)
    details: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": msg,
        "status_code": getattr(exc, "status_code", None),
        "request_id": getattr(exc, "request_id", None),
        "provider_error_type": getattr(exc, "type", None),
        "provider_error_code": getattr(exc, "code", None),
        "provider_error_param": getattr(exc, "param", None),
        "provider_error_message": None,
    }

    embedded = _extract_embedded_error_payload(msg)
    if embedded:
        details["status_code"] = details.get("status_code") or embedded.get("status_code")
        details["request_id"] = details.get("request_id") or embedded.get("request_id")
        embedded_error = embedded.get("error")
        error_obj: dict[str, Any] = embedded_error if isinstance(embedded_error, dict) else {}
        details["provider_error_type"] = details.get("provider_error_type") or error_obj.get("type")
        details["provider_error_code"] = details.get("provider_error_code") or error_obj.get("code")
        details["provider_error_param"] = details.get("provider_error_param") or error_obj.get(
            "param"
        )
        details["provider_error_message"] = error_obj.get("message")

    if details.get("provider_error_message") is None:
        details["provider_error_message"] = msg

    return details


def _is_retriable_error(exc: Exception) -> bool:
    """Return True for transient errors worth retrying (429, 5xx excluding 501)."""
    if bool(getattr(exc, "is_transient_error", False)):
        return True

    # httpx transport errors are always transient (connection drops, read failures)
    _httpx_errs = _httpx_transport_errors()
    if _httpx_errs and isinstance(exc, _httpx_errs):
        return True

    # Check explicit status_code/status attribute (most provider SDKs set this)
    for attr in ("status_code", "status"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code == 429 or (500 <= code < 600 and code != 501)

    # Fall back to parsing the exception message for embedded status codes
    msg = str(exc)
    # Match patterns like "status_code=429", "HTTP 503", "Error 500", or bare "429"
    matches = re.findall(r"\b(429|500|502|503|504)\b", msg)
    return bool(matches)


def _is_unusable_empty_response(response: ModelResponse) -> bool:
    """Return True when a provider response has no usable final content.

    Length-truncated non-empty responses are handled by the token escalation
    branch in ``_retry_loop``.  Keeping this helper scoped to genuinely empty
    content lets higher layers inspect a truncated payload when they need to
    apply task-specific format repair or text guards.
    """
    if not (response.content or "").strip():
        return True
    return False


def _is_length_truncated_response(response: ModelResponse) -> bool:
    """Return True for responses that stopped at ``max_tokens``."""
    return response.finish_reason == "length"


def _next_length_retry_tokens(current_max_tokens: int, model_limit: int) -> int:
    """Double completion budget for a length retry without exceeding model limit."""
    current = max(1, int(current_max_tokens or 1))
    limit = max(1, int(model_limit or current))
    if current >= limit:
        return current
    return min(limit, max(current + 1, current * 2))


class ModelRouter(ModelRouterProtocol):
    """Selects the right adapter and model for each task.

    Parameters
    ----------
    adapters : dict[str, ProviderAdapter]
        Registered provider adapters keyed by name.
    tier_to_model : dict[ModelTier, str] | None
        Mapping from tier level to default model ID.
    task_tiers : dict[TaskType, ModelTier] | None
        Which tier each task type defaults to.
    default_provider : str
        Fallback provider when nothing else is specified.
    task_providers : dict[TaskType, TaskRouteOverride] | None
        **Per-task routing overrides.** When set, the given task type
        will always be routed to the specified provider (and optionally
        a fixed model_id), ignoring the tier system.
    """

    def __init__(
        self,
        adapters: dict[str, ProviderAdapter],
        *,
        tier_to_model: dict[ModelTier, str] | None = None,
        task_tiers: dict[TaskType, ModelTier] | None = None,
        default_tier: ModelTier = ModelTier.STANDARD,
        default_provider: str = "mock",
        task_providers: dict[TaskType, TaskRouteOverride] | None = None,
        task_fallbacks: dict[TaskType, list[TaskRouteOverride]] | None = None,
        observers: list[RouteObserver] | None = None,
        rate_limiter: RateLimiterRegistry | None = None,
        request_timeout_s: float = 300.0,
        stream_idle_timeout_s: float = 120.0,
        spending_tracker: SpendingTracker | None = None,
        workflow_timeout_s: float = 3600.0,
        circuit_breakers: dict[str, CircuitBreaker] | None = None,
        task_circuit_breaker: TaskTypeCircuitBreaker | None = None,
        dlq: Any | None = None,
        creative_temperature_jitter_enabled: bool = False,
        creative_temperature_jitter_up_delta: float = 0.1,
        creative_temperature_jitter_down_delta: float = 0.3,
        creative_temperature_jitter_scope: str = "recommended",
        creative_temperature_jitter_custom_tasks: str = "",
        cache: CachePolicy | None = None,
    ) -> None:
        self._adapters = adapters
        # Layer 2: per-attempt read timeout (SDK-level, controls individual API call)
        self._request_timeout_s = max(0.0, request_timeout_s)
        # Layer 2b: inter-chunk idle timeout for streaming calls.  If no new
        # chunk arrives within this window the stream is considered stalled and
        # is cancelled early — much faster than waiting for the total timeout.
        self._stream_idle_timeout_s = max(0.0, stream_idle_timeout_s)
        # Layer 3: route retry-loop timeout.  This is computed per call because
        # length-truncated structured outputs are allowed more retries than
        # ordinary transport failures.
        self._total_request_timeout_s = (
            self._request_timeout_s + 5.0 if self._request_timeout_s > 0 else 0.0
        )
        # Layer 4: workflow timeout (wraps entire route() including all routes/failovers)
        self._workflow_timeout_s = max(0.0, workflow_timeout_s)
        self._tier_to_model = tier_to_model or {
            ModelTier.PREMIUM: "gpt-4o",
            ModelTier.STANDARD: "gpt-4o-mini",
            ModelTier.BUDGET: "gpt-3.5-turbo",
        }
        self._task_tiers = task_tiers or dict(DEFAULT_TASK_TIERS)
        self._default_tier = default_tier
        self._default_provider = default_provider
        self._task_providers = task_providers or {}
        self._task_fallbacks = task_fallbacks or {}
        self._observers = list(observers or [])
        self._rate_limiter = rate_limiter or RateLimiterRegistry(enabled=False)
        self._spending_tracker = spending_tracker
        self._circuit_breakers = circuit_breakers or {}
        self._task_circuit_breaker = task_circuit_breaker
        self._dlq = dlq
        self._creative_temperature_jitter_enabled = bool(creative_temperature_jitter_enabled)
        self._creative_temperature_jitter_up_delta = max(
            0.0, float(creative_temperature_jitter_up_delta)
        )
        self._creative_temperature_jitter_down_delta = max(
            0.0, float(creative_temperature_jitter_down_delta)
        )
        self._creative_temperature_jitter_scope = str(creative_temperature_jitter_scope or "")
        self._creative_temperature_jitter_custom_tasks = str(
            creative_temperature_jitter_custom_tasks or ""
        )
        self._cache = cache
        # Successful length-expansion retries reveal that a route/task needs a
        # larger completion budget than its static estimate. Reuse that fact
        # for later calls in this router lifetime instead of restarting from a
        # known-too-small value on semantic/format retries.
        self._learned_output_budgets: dict[tuple[str, str, str], int] = {}

    def _route_retry_loop_timeout_s(self, max_retries: int) -> float:
        """Return the timeout budget for one resolved route's retry loop.

        Each individual adapter call is already bounded by ``_request_timeout_s``.
        The retry-loop wrapper must therefore allow enough wall time for token
        expansion retries after ``finish_reason=length``; otherwise a successful
        first truncation can consume the fixed route budget and prevent the
        larger retry from completing.
        """

        if self._request_timeout_s <= 0:
            return 0.0
        retry_attempts = max(max_retries, _LENGTH_TRUNCATION_MAX_RETRIES) + 1
        timeout_s = (self._request_timeout_s * retry_attempts) + max(
            5.0,
            float(retry_attempts),
        )
        if self._workflow_timeout_s > 0:
            timeout_s = min(timeout_s, self._workflow_timeout_s)
        return timeout_s

    @property
    def default_provider(self) -> str:
        """Configured default provider name."""
        return self._default_provider

    @property
    def adapters(self) -> Mapping[str, ProviderAdapter]:
        """Read-only view of registered adapters."""
        return MappingProxyType(self._adapters)

    @property
    def task_route_overrides(self) -> Mapping[TaskType, TaskRouteOverride]:
        """Read-only view of per-task provider/model overrides."""
        return MappingProxyType(self._task_providers)

    @property
    def task_fallback_routes(self) -> Mapping[TaskType, list[TaskRouteOverride]]:
        """Read-only view of per-task fallback route chains."""
        return MappingProxyType(self._task_fallbacks)

    @property
    def spending_tracker(self) -> SpendingTracker | None:
        """Expose the spending tracker for external budget checks."""
        return self._spending_tracker

    @property
    def cache(self) -> CachePolicy | None:
        return self._cache

    def get_adapter(self, provider: str) -> ProviderAdapter | None:
        """Return adapter by provider name, or None if missing."""
        return self._adapters.get(provider)

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str | None:
        """Resolve the model id that the primary route would use for a task.

        This mirrors the primary route selection used by ``route()`` without
        making a network call, allowing steps to size output budgets before
        sending a request.
        """
        if model_id:
            return model_id

        task_override = self._task_providers.get(task_type)
        resolved_provider = provider
        resolved_model: str | None = None
        if resolved_provider is None and task_override is not None:
            resolved_provider = task_override.provider
            resolved_model = task_override.model_id
        if resolved_model:
            return resolved_model

        try:
            adapter = self._get_adapter(resolved_provider)
        except KeyError:
            adapter = None
        tier = self._resolve_tier(task_type)
        return self._tier_to_model.get(tier) or getattr(adapter, "default_model", None)

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        """Return the maximum output tokens for a task's primary route model."""
        resolved_model = self.resolve_model_id_for_task(
            task_type,
            provider=provider,
            model_id=model_id,
        )
        return get_model_max_output_tokens(resolved_model or "")

    @staticmethod
    def _output_budget_key(
        task_type: TaskType,
        provider: str,
        model_id: str | None,
    ) -> tuple[str, str, str]:
        return (
            task_type.value,
            str(provider or "").strip().lower(),
            str(model_id or "").strip().lower(),
        )

    def _learned_output_budget_for(
        self,
        task_type: TaskType,
        *,
        provider: str,
        model_id: str | None,
    ) -> int:
        return self._learned_output_budgets.get(
            self._output_budget_key(task_type, provider, model_id),
            0,
        )

    # Maximum entries for learned output budgets (LRU eviction when exceeded).
    _LEARNED_BUDGET_MAX_ENTRIES = 1024

    def _remember_output_budget(
        self,
        task_type: TaskType,
        *,
        provider: str,
        model_id: str | None,
        max_tokens: int,
        reason: str,
    ) -> None:
        model_limit = get_model_max_output_tokens(str(model_id or ""))
        learned = min(max(1, int(max_tokens or 1)), model_limit)
        key = self._output_budget_key(task_type, provider, model_id)
        previous = self._learned_output_budgets.get(key, 0)
        if learned <= previous:
            return
        # LRU eviction: remove oldest entries when exceeding max size
        if len(self._learned_output_budgets) >= self._LEARNED_BUDGET_MAX_ENTRIES:
            # Remove first 10% of entries (approximate LRU)
            keys_to_remove = list(self._learned_output_budgets.keys())[
                : self._LEARNED_BUDGET_MAX_ENTRIES // 10
            ]
            for k in keys_to_remove:
                del self._learned_output_budgets[k]
        self._learned_output_budgets[key] = learned
        self._emit_event(
            "api_output_budget_learned",
            {
                "task": task_type.value,
                "provider": provider,
                "model": model_id or "",
                "previous_max_tokens": previous,
                "learned_max_tokens": learned,
                "reason": reason,
            },
        )

    def _with_learned_output_budget(
        self,
        request: ModelRequest,
        *,
        provider: str,
        model_id: str | None,
        event: str,
    ) -> ModelRequest:
        learned = self._learned_output_budget_for(
            request.task_type,
            provider=provider,
            model_id=model_id,
        )
        if learned <= request.max_tokens:
            return request
        model_limit = get_model_max_output_tokens(str(model_id or ""))
        effective = min(learned, model_limit)
        if effective <= request.max_tokens:
            return request
        self._emit_event(
            event,
            {
                "task": request.task_type.value,
                "provider": provider,
                "model": model_id or "",
                "requested_max_tokens": request.max_tokens,
                "reused_max_tokens": effective,
            },
        )
        return request.model_copy(update={"max_tokens": effective})

    def add_observer(self, observer: RouteObserver) -> None:
        """Register a callback for request lifecycle events."""
        if observer not in self._observers:
            self._observers.append(observer)

    def remove_observer(self, observer: RouteObserver) -> None:
        """Unregister a previously registered observer callback."""
        try:
            self._observers.remove(observer)
        except ValueError:
            return

    @contextmanager
    def observe(self, observer: RouteObserver) -> Iterator[None]:
        """Temporarily register an observer for the duration of a context."""
        self.add_observer(observer)
        try:
            yield
        finally:
            self.remove_observer(observer)

    async def shutdown(self) -> None:
        """Best-effort shutdown for provider adapters and cache."""
        if self._cache is not None:
            self._cache.close()
        seen_ids: set[int] = set()
        for adapter in self._adapters.values():
            adapter_id = id(adapter)
            if adapter_id in seen_ids:
                continue
            seen_ids.add(adapter_id)

            close_hook = getattr(adapter, "shutdown", None)
            if not callable(close_hook):
                close_hook = getattr(adapter, "aclose", None)
            if not callable(close_hook):
                continue

            try:
                result = close_hook()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                _log.debug("adapter_shutdown_failed | error=%s", exc, exc_info=True)

    async def aclose(self) -> None:
        """Alias for ``shutdown`` to support generic runtime cleanup."""
        await self.shutdown()

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        if not self._observers:
            return
        for observer in list(self._observers):
            try:
                observer(event, payload)
            except Exception as exc:
                _log.warning("router_observer_failed | event=%s | error=%s", event, exc)

    @property
    def task_circuit_breaker(self) -> TaskTypeCircuitBreaker | None:
        """Shared task-level breaker for callers that use :class:`LLMService`."""
        return self._task_circuit_breaker

    def provider_health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return an operator-safe snapshot of provider circuit health."""
        snapshot: dict[str, dict[str, Any]] = {}
        for provider, breaker in self._circuit_breakers.items():
            state = breaker.state
            snapshot[str(provider)] = {
                "state": state.value,
                "failure_count": breaker.failure_count,
                "retry_after_s": (
                    round(float(breaker.time_until_half_open() or 0.0), 3)
                    if state == CircuitState.OPEN
                    else 0.0
                ),
            }
        return snapshot

    def _record_provider_route_failure(
        self,
        *,
        route_item: Any,
        provider_name: str,
        request: ModelRequest,
        category: str,
        logical_attempt_count: int,
    ) -> None:
        """Count one exhausted logical route, never each internal retry."""
        breaker = self._circuit_breakers.get(route_item.provider)
        if breaker is None:
            return
        # A request rejected before it acquired the breaker cannot be the
        # HALF_OPEN probe.  Recording it as a failure would let a concurrent
        # waiter reopen the circuit while the real probe is still running.
        if category == "circuit_open":
            return
        # A HALF_OPEN request is the breaker probe.  It must resolve the
        # state even for a malformed/empty response, otherwise the probe flag
        # would remain held and every later request would be rejected.
        is_half_open_probe = breaker.state == CircuitState.HALF_OPEN
        if not _is_provider_health_failure(category) and not is_half_open_probe:
            return
        previous_state = breaker.state
        breaker.record_failure()
        current_state = breaker.state
        payload = {
            "provider": provider_name,
            "model": route_item.model_id,
            "task": request.task_type.value,
            "route": route_item.source,
            "failure_category": category,
            "logical_attempt_count": logical_attempt_count,
            "failure_count": breaker.failure_count,
            "old_state": previous_state.value,
            "new_state": current_state.value,
        }
        self._emit_event("circuit_failure_recorded", payload)
        if current_state != previous_state:
            self._emit_event("circuit_transition", payload)

    def _emit_circuit_rejected(
        self,
        *,
        route_item: Any,
        provider_name: str,
        request: ModelRequest,
        retry_after_s: float,
    ) -> None:
        self._emit_event(
            "circuit_rejected",
            {
                "provider": provider_name,
                "model": route_item.model_id,
                "task": request.task_type.value,
                "route": route_item.source,
                "failure_category": "circuit_open",
                "retry_after_s": retry_after_s,
            },
        )

    def _resolve_tier(self, task_type: TaskType) -> ModelTier:
        """Determine effective tier for a task type."""
        return self._task_tiers.get(task_type, self._default_tier)

    def _get_adapter(self, provider: str | None = None) -> ProviderAdapter:
        key = provider or self._default_provider
        if key not in self._adapters:
            raise KeyError(f"Provider '{key}' not registered. Available: {list(self._adapters)}")
        return self._adapters[key]

    async def _retry_loop(
        self,
        adapter: ProviderAdapter,
        routed_request: ModelRequest,
        route_item: Any,
        provider_name: str,
        call_id: str,
        call_start: float,
        limiter: Any,
        estimated_tokens: int | None,
        max_retries: int,
        request: ModelRequest,
    ) -> ModelResponse | None:
        length_max_retries = max(max_retries, _LENGTH_TRUNCATION_MAX_RETRIES)
        for attempt in range(length_max_retries + 1):
            # Includes length escalation and transport retries after an await.
            # Authority waits are not provider failures and must not fail over.
            check_authoring_authority()
            if self._spending_tracker is not None:
                self._spending_tracker.check_budget()
            attempt_id = f"{call_id}:{attempt}"
            _begin_authoring_model_call(attempt_id, routed_request, provider_name)
            try:
                received: ModelResponse | None = None
                try:
                    _coro = adapter.complete(routed_request)
                    if self._request_timeout_s > 0:
                        response = await asyncio.wait_for(_coro, timeout=self._request_timeout_s)
                    else:
                        response = await _coro
                    received = response
                finally:
                    _settle_authoring_model_call(attempt_id, received, provider_name)

                call_elapsed = time.monotonic() - call_start
                usage_recorded = False
                cost_recorded = False

                if _is_length_truncated_response(response):
                    if limiter is not None:
                        await limiter.report_usage(response.total_tokens, estimated_tokens)
                        usage_recorded = True
                    if self._spending_tracker is not None and response.cost_usd > 0:
                        self._spending_tracker.record(response.cost_usd)
                        cost_recorded = True

                    model_output_limit = get_model_max_output_tokens(
                        str(response.model_id or route_item.model_id or "")
                    )
                    new_max_tokens = _next_length_retry_tokens(
                        routed_request.max_tokens,
                        model_output_limit,
                    )
                    self._emit_event(
                        "api_call_length_truncated",
                        {
                            "call_id": call_id,
                            "provider": provider_name,
                            "model": route_item.model_id,
                            "task": request.task_type.value,
                            "latency_ms": round(call_elapsed * 1000, 2),
                            "prompt_tokens": response.prompt_tokens,
                            "completion_tokens": response.completion_tokens,
                            "total_tokens": response.total_tokens,
                            "finish_reason": response.finish_reason,
                            "max_tokens": routed_request.max_tokens,
                            "next_max_tokens": new_max_tokens,
                            "model_output_limit": model_output_limit,
                            "route": route_item.source,
                            "attempt": attempt + 1,
                            "max_attempts": length_max_retries + 1,
                            "will_retry": (
                                new_max_tokens > routed_request.max_tokens
                                and attempt < length_max_retries
                            ),
                        },
                    )

                    if new_max_tokens > routed_request.max_tokens and attempt < length_max_retries:
                        _log.warning(
                            "api_call_length_retry | provider=%s | model=%s | task=%s | "
                            "attempt=%d/%d | max_tokens=%d -> %d | completion_tokens=%d | route=%s",
                            provider_name,
                            route_item.model_id,
                            request.task_type.value,
                            attempt + 1,
                            length_max_retries,
                            routed_request.max_tokens,
                            new_max_tokens,
                            response.completion_tokens,
                            route_item.source,
                        )
                        routed_request = routed_request.model_copy(
                            update={"max_tokens": new_max_tokens}
                        )
                        estimated_tokens = new_max_tokens
                        await asyncio.sleep(0)
                        continue

                    if new_max_tokens <= routed_request.max_tokens:
                        _log.error(
                            "api_call_length_exhausted | provider=%s | model=%s | task=%s | "
                            "max_tokens=%d | model_output_limit=%d | completion_tokens=%d | route=%s",
                            provider_name,
                            route_item.model_id,
                            request.task_type.value,
                            routed_request.max_tokens,
                            model_output_limit,
                            response.completion_tokens,
                            route_item.source,
                        )
                        record_model_error(
                            task=request.task_type.value,
                            provider=provider_name,
                            model=response.model_id or str(route_item.model_id or ""),
                            latency_ms=response.latency_ms or round(call_elapsed * 1000, 2),
                            thinking=bool(routed_request.thinking),
                            multi_turn=bool(routed_request.multi_turn),
                            error=(
                                "length-truncated model response exhausted output budget "
                                f"(completion_tokens={response.completion_tokens}, "
                                f"max_tokens={routed_request.max_tokens}, "
                                f"model_output_limit={model_output_limit})"
                            ),
                        )
                        return None

                    _log.error(
                        "api_call_length_retry_budget_exhausted | provider=%s | model=%s | task=%s | "
                        "attempts=%d | max_tokens=%d | next_max_tokens=%d | "
                        "model_output_limit=%d | route=%s",
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        attempt + 1,
                        routed_request.max_tokens,
                        new_max_tokens,
                        model_output_limit,
                        route_item.source,
                    )
                    record_model_error(
                        task=request.task_type.value,
                        provider=provider_name,
                        model=response.model_id or str(route_item.model_id or ""),
                        latency_ms=response.latency_ms or round(call_elapsed * 1000, 2),
                        thinking=bool(routed_request.thinking),
                        multi_turn=bool(routed_request.multi_turn),
                        error=(
                            "length-truncated model response exhausted router retry budget "
                            f"(completion_tokens={response.completion_tokens}, "
                            f"max_tokens={routed_request.max_tokens}, "
                            f"next_max_tokens={new_max_tokens}, "
                            f"model_output_limit={model_output_limit})"
                        ),
                    )
                    return None

                if _is_unusable_empty_response(response):
                    if limiter is not None:
                        await limiter.report_usage(response.total_tokens, estimated_tokens)
                        usage_recorded = True
                    if self._spending_tracker is not None and response.cost_usd > 0:
                        self._spending_tracker.record(response.cost_usd)
                        cost_recorded = True
                    empty_reason = (
                        f"empty model response (finish_reason={response.finish_reason}, "
                        f"completion_tokens={response.completion_tokens}, "
                        f"max_tokens={routed_request.max_tokens})"
                    )
                    record_model_error(
                        task=request.task_type.value,
                        provider=provider_name,
                        model=response.model_id or str(route_item.model_id or ""),
                        latency_ms=response.latency_ms or round(call_elapsed * 1000, 2),
                        thinking=bool(routed_request.thinking),
                        multi_turn=bool(routed_request.multi_turn),
                        error=empty_reason,
                    )
                    self._emit_event(
                        "api_call_empty_response",
                        {
                            "call_id": call_id,
                            "provider": provider_name,
                            "model": route_item.model_id,
                            "task": request.task_type.value,
                            "latency_ms": round(call_elapsed * 1000, 2),
                            "prompt_tokens": response.prompt_tokens,
                            "completion_tokens": response.completion_tokens,
                            "total_tokens": response.total_tokens,
                            "finish_reason": response.finish_reason,
                            "route": route_item.source,
                            "max_tokens": routed_request.max_tokens,
                            "attempt": attempt + 1,
                            "max_attempts": max_retries + 1,
                            "will_retry": attempt < max_retries,
                        },
                    )
                    if attempt < max_retries:
                        _log.warning(
                            "api_call_empty_response_retry | provider=%s | model=%s | task=%s | "
                            "attempt=%d/%d | finish_reason=%s | completion_tokens=%d | route=%s",
                            provider_name,
                            route_item.model_id,
                            request.task_type.value,
                            attempt + 1,
                            max_retries,
                            response.finish_reason,
                            response.completion_tokens,
                            route_item.source,
                        )
                        await asyncio.sleep(0)
                        continue
                    _log.error(
                        "api_call_empty_response_exhausted | provider=%s | model=%s | task=%s | "
                        "attempts=%d | finish_reason=%s | completion_tokens=%d | route=%s",
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        max_retries + 1,
                        response.finish_reason,
                        response.completion_tokens,
                        route_item.source,
                    )
                    return None

                if limiter is not None and not usage_recorded:
                    await limiter.report_usage(response.total_tokens, estimated_tokens)
                response = response.model_copy(
                    update={
                        "requested_max_tokens": int(request.max_tokens or 0),
                        "effective_max_tokens": int(routed_request.max_tokens or 0),
                        "length_retry_count": attempt,
                    }
                )
                if attempt > 0:
                    self._remember_output_budget(
                        request.task_type,
                        provider=route_item.provider,
                        model_id=route_item.model_id,
                        max_tokens=routed_request.max_tokens,
                        reason="length_retry_succeeded",
                    )
                _log.info(
                    "api_call_done | provider=%s | model=%s | task=%s | latency_s=%.1f | tokens=%s | route=%s",
                    provider_name,
                    route_item.model_id,
                    request.task_type.value,
                    call_elapsed,
                    response.total_tokens,
                    route_item.source,
                )
                self._emit_event(
                    "api_call_done",
                    {
                        "call_id": call_id,
                        "provider": provider_name,
                        "model": route_item.model_id,
                        "task": request.task_type.value,
                        "latency_ms": round(call_elapsed * 1000, 2),
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                        "total_tokens": response.total_tokens,
                        "cost_usd": response.cost_usd,
                        "route": route_item.source,
                        "max_tokens": routed_request.max_tokens,
                        "attempt": attempt + 1,
                        "max_attempts": length_max_retries + 1,
                        "response": response.model_dump(mode="json"),
                    },
                )
                record_model_response(
                    task=request.task_type.value,
                    provider=provider_name,
                    model=response.model_id or str(route_item.model_id or ""),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=response.total_tokens,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms or round(call_elapsed * 1000, 2),
                    thinking=bool(routed_request.thinking),
                    multi_turn=bool(routed_request.multi_turn),
                )
                if (
                    self._spending_tracker is not None
                    and response.cost_usd > 0
                    and not cost_recorded
                ):
                    self._spending_tracker.record(response.cost_usd)
                cb = self._circuit_breakers.get(route_item.provider)
                if cb is not None:
                    cb.record_success()
                return response
            except _transport_timeout_errors() as transport_exc:
                # _OAIConnectionError wraps httpx.ConnectError (e.g. DNS failure)
                # so treat it as "connect", not "read", for accurate diagnosis.
                _httpx_errs = _httpx_transport_errors()
                _oai_conn_err = _openai_connection_error()
                is_connect_timeout = (
                    isinstance(transport_exc, _httpx_errs)
                    and type(transport_exc).__name__.startswith("Connect")
                ) or (_oai_conn_err is not None and isinstance(transport_exc, _oai_conn_err))
                timeout_layer = "connect" if is_connect_timeout else "read"
                effective_timeout_s = (
                    float(getattr(adapter, "connect_timeout_s", 0.0) or 0.0)
                    if is_connect_timeout
                    else self._request_timeout_s
                )

                if attempt < max_retries:
                    retry_delay = 2.0**attempt
                    _log.warning(
                        "api_call_%s_timeout_retry | provider=%s | model=%s | task=%s | "
                        "attempt=%d/%d | timeout_s=%.0f | retry_delay=%.1fs | route=%s",
                        timeout_layer,
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        attempt + 1,
                        max_retries,
                        effective_timeout_s,
                        retry_delay,
                        route_item.source,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                call_elapsed = time.monotonic() - call_start
                _log.error(
                    "api_call_%s_timeout_exhausted | provider=%s | model=%s | task=%s | attempts=%d | timeout_s=%.0f | route=%s",
                    timeout_layer,
                    provider_name,
                    route_item.model_id,
                    request.task_type.value,
                    max_retries + 1,
                    effective_timeout_s,
                    route_item.source,
                )
                record_model_error(
                    task=request.task_type.value,
                    provider=provider_name,
                    model=str(route_item.model_id or ""),
                    latency_ms=round(call_elapsed * 1000, 2),
                    thinking=bool(routed_request.thinking),
                    multi_turn=bool(routed_request.multi_turn),
                    error=(
                        f"{timeout_layer} timed out after {effective_timeout_s:.0f}s "
                        f"after {max_retries + 1} attempts"
                    ),
                )
                self._emit_event(
                    "api_call_timeout",
                    {
                        "call_id": call_id,
                        "provider": provider_name,
                        "model": route_item.model_id,
                        "task": request.task_type.value,
                        "latency_ms": round(call_elapsed * 1000, 2),
                        "timeout_s": effective_timeout_s,
                        "timeout_layer": timeout_layer,
                        "request_timeout_s": self._request_timeout_s,
                        "attempts": max_retries + 1,
                        "route": route_item.source,
                    },
                )
                raise ModelGatewayError(
                    f"route timed out after {max_retries + 1} attempts for "
                    f"task={request.task_type.value} provider={provider_name}",
                    is_transient=True,
                    timeout_layer=timeout_layer,
                    timeout_s=effective_timeout_s,
                ) from transport_exc
            except CircuitBreakerOpenError as cb_exc:
                call_elapsed = time.monotonic() - call_start
                _log.warning(
                    "api_call_circuit_breaker_rejected | provider=%s | model=%s | task=%s | retry_after=%.1fs | route=%s",
                    provider_name,
                    route_item.model_id,
                    request.task_type.value,
                    cb_exc.retry_after_s,
                    route_item.source,
                )
                if limiter is not None:
                    await limiter.report_usage(0, estimated_tokens)
                self._emit_circuit_rejected(
                    route_item=route_item,
                    provider_name=provider_name,
                    request=request,
                    retry_after_s=cb_exc.retry_after_s,
                )
                raise
            except Exception as exc:
                call_elapsed = time.monotonic() - call_start
                error_details = _extract_provider_error_details(exc)
                record_model_error(
                    task=request.task_type.value,
                    provider=provider_name,
                    model=str(route_item.model_id or ""),
                    latency_ms=round(call_elapsed * 1000, 2),
                    thinking=bool(routed_request.thinking),
                    multi_turn=bool(routed_request.multi_turn),
                    error=str(exc),
                )
                self._emit_event(
                    "api_call_error",
                    {
                        "call_id": call_id,
                        "provider": provider_name,
                        "model": route_item.model_id,
                        "task": request.task_type.value,
                        "latency_ms": round(call_elapsed * 1000, 2),
                        "route": route_item.source,
                        "error": error_details,
                    },
                )
                is_retriable = _is_retriable_error(exc)
                if is_retriable and attempt < max_retries:
                    status_code = error_details.get("status_code")
                    if status_code == 429:
                        retry_delay = min(5.0 * (2**attempt), 30.0)
                    else:
                        retry_delay = 2.0**attempt
                    _log.warning(
                        "api_call_retriable_error | provider=%s | model=%s | task=%s | "
                        "attempt=%d/%d | status=%s | retry_delay=%.1fs | route=%s",
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        attempt + 1,
                        max_retries,
                        status_code,
                        retry_delay,
                        route_item.source,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                if not is_retriable:
                    if limiter is not None:
                        await limiter.report_usage(0, estimated_tokens)
                    raise
                raise
        return None

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        """Route a request through adapter selection → call."""

        # Layer 4: workflow timeout — wraps entire route() including all failovers
        if self._workflow_timeout_s > 0:
            return await asyncio.wait_for(
                self._execute_route(request, provider=provider),
                timeout=self._workflow_timeout_s,
            )
        return await self._execute_route(request, provider=provider)

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_chunk: Callable[[StreamChunk], None] | None = None,
    ) -> ModelResponse:
        """Route a streaming request and return the final aggregated response.

        ``on_chunk`` is the preferred callback: it receives :class:`StreamChunk`
        objects that may carry both ``content`` and ``reasoning``, preserving
        the real interleaving order from the model.  A chunk with ``reset=True``
        invalidates previously delivered chunks when a transport attempt is
        replaced.  ``on_delta`` is kept for backward compatibility — it receives
        only content text and cannot represent a reset boundary.  When both are
        provided, ``on_chunk`` is invoked first with the full chunk, then
        ``on_delta`` with the content portion (if any).
        """
        if self._workflow_timeout_s > 0:
            return await asyncio.wait_for(
                self._execute_stream_route(
                    request,
                    provider=provider,
                    on_delta=on_delta,
                    on_chunk=on_chunk,
                ),
                timeout=self._workflow_timeout_s,
            )
        return await self._execute_stream_route(
            request,
            provider=provider,
            on_delta=on_delta,
            on_chunk=on_chunk,
        )

    async def _execute_stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_chunk: Callable[[StreamChunk], None] | None = None,
    ) -> ModelResponse:
        task_override = self._task_providers.get(request.task_type)
        fallback_routes = list(self._task_fallbacks.get(request.task_type, []))

        @dataclass
        class _ResolvedRoute:
            provider: str
            model_id: str | None
            thinking: bool
            thinking_mode: str
            multi_turn: bool
            source: str

        def _resolve_route(
            provider_hint: str | None,
            model_hint: str | None,
            route_override: TaskRouteOverride | None,
            source: str,
        ) -> _ResolvedRoute:
            resolved_provider = provider_hint
            resolved_model = model_hint
            thinking = bool(request.thinking)
            thinking_mode = normalize_thinking_mode(
                request.thinking_mode,
                thinking=thinking,
            )
            multi_turn = bool(request.multi_turn)
            if resolved_provider is None and route_override is not None:
                resolved_provider = route_override.provider
                resolved_model = resolved_model or route_override.model_id
                if not request.thinking_mode:
                    thinking_mode = normalize_thinking_mode(
                        route_override.thinking_mode,
                        thinking=route_override.thinking,
                    )
                thinking = thinking_mode_enabled(thinking_mode)
                multi_turn = multi_turn or bool(route_override.multi_turn)
            adapter = self._get_adapter(resolved_provider)
            if resolved_model is None:
                tier = self._resolve_tier(request.task_type)
                resolved_model = (
                    self._tier_to_model.get(tier) or adapter.default_model or "gpt-4o-mini"
                )
            if request.task_type in _RESULT_ONLY_STRUCTURED_TASKS:
                thinking = False
                thinking_mode = "off"
            return _ResolvedRoute(
                provider=resolved_provider or self._default_provider,
                model_id=resolved_model,
                thinking=thinking,
                thinking_mode=thinking_mode,
                multi_turn=multi_turn,
                source=source,
            )

        candidate_routes: list[_ResolvedRoute] = []
        try:
            candidate_routes.append(
                _resolve_route(provider, request.model_id, task_override, "primary")
            )
        except KeyError as exc:
            _log.error(
                "api_stream_primary_invalid | task=%s | error=%s", request.task_type.value, exc
            )
        for idx, fb in enumerate(fallback_routes, start=1):
            try:
                candidate_routes.append(
                    _resolve_route(fb.provider, fb.model_id, fb, f"fallback_{idx}")
                )
            except KeyError as exc:
                _log.warning(
                    "api_stream_fallback_invalid_skipped | task=%s | fallback_index=%d | error=%s",
                    request.task_type.value,
                    idx,
                    exc,
                )
        if not candidate_routes:
            raise ModelGatewayError(
                f"no valid stream routes available for task={request.task_type.value}"
            )

        if request.require_native_structured_output:
            eligible_routes: list[_ResolvedRoute] = []
            rejected_routes: list[dict[str, str]] = []
            for route_item in candidate_routes:
                reason = _native_structured_output_skip_reason(
                    request,
                    provider=route_item.provider,
                    model_id=route_item.model_id,
                    thinking=route_item.thinking,
                )
                if reason is None:
                    eligible_routes.append(route_item)
                    continue
                rejected_routes.append(
                    {
                        "provider": route_item.provider,
                        "model": str(route_item.model_id or ""),
                        "route": route_item.source,
                        "reason": reason,
                    }
                )
            if rejected_routes:
                self._emit_event(
                    "api_route_capability_skipped",
                    {
                        "task": request.task_type.value,
                        "require_native_structured_output": True,
                        "rejected_routes": rejected_routes,
                    },
                )
            if not eligible_routes:
                details = ", ".join(
                    f"{item['provider']}/{item['model']} ({item['reason']})"
                    for item in rejected_routes
                )
                raise ModelGatewayError(
                    "task requires native structured output but no configured stream route "
                    f"supports it: task={request.task_type.value}; rejected=[{details}]"
                )
            candidate_routes = eligible_routes

        check_authoring_authority()
        if self._spending_tracker is not None:
            self._spending_tracker.check_budget()

        temperature_jitter = resolve_temperature_jitter(
            task_type=request.task_type,
            base_temperature=request.temperature,
            enabled=self._creative_temperature_jitter_enabled,
            up_delta=self._creative_temperature_jitter_up_delta,
            down_delta=self._creative_temperature_jitter_down_delta,
            scope=self._creative_temperature_jitter_scope,
            custom_tasks=self._creative_temperature_jitter_custom_tasks,
            allowed=request.temperature_jitter_allowed,
        )
        final_error: Exception | None = None
        failure_categories: list[str] = []
        best_partial_text = ""
        best_partial_reasoning = ""
        best_partial_provider = ""
        best_partial_model_id = ""
        for route_index, route_item in enumerate(candidate_routes):
            adapter = self._get_adapter(route_item.provider)
            update_fields: dict[str, object] = {
                "model_id": route_item.model_id,
                "provider_id": route_item.provider,
                "thinking": route_item.thinking,
                "thinking_mode": route_item.thinking_mode,
                "multi_turn": route_item.multi_turn,
                "temperature": temperature_jitter.actual_temperature,
            }
            if request.max_tokens is not None and route_item.model_id is not None:
                model_output_limit = get_model_max_output_tokens(route_item.model_id)
                if request.max_tokens > model_output_limit:
                    update_fields["max_tokens"] = model_output_limit
            routed_request = request.model_copy(update=update_fields)
            routed_request = self._with_learned_output_budget(
                routed_request,
                provider=route_item.provider,
                model_id=route_item.model_id,
                event="api_stream_output_budget_reused",
            )
            context_fit = _measure_request_context(
                routed_request,
                provider=route_item.provider,
                model_id=str(route_item.model_id or ""),
            )
            if not context_fit.fits:
                payload = {
                    "provider": route_item.provider,
                    "model": route_item.model_id,
                    "task": request.task_type.value,
                    "route": route_item.source,
                    **context_fit.event_payload(),
                }
                self._emit_event("api_stream_context_skipped", payload)
                final_error = ContextLengthError(
                    "complete prompt does not fit the stream route context window",
                    context=payload,
                )
                failure_categories.append("context_length")
                has_next_route = route_index + 1 < len(candidate_routes)
                if has_next_route:
                    next_route = candidate_routes[route_index + 1]
                    _log.warning(
                        "api_stream_context_failover | task=%s | from=%s/%s | "
                        "to=%s/%s | overshoot_tokens=%d",
                        request.task_type.value,
                        route_item.provider,
                        route_item.model_id,
                        next_route.provider,
                        next_route.model_id,
                        context_fit.overshoot_tokens,
                    )
                    continue
                break
            call_id = uuid.uuid4().hex
            provider_name = getattr(adapter, "provider_name", route_item.provider)
            call_start = time.monotonic()
            limiter = self._rate_limiter.get(provider_name)
            estimated_tokens = context_fit.prompt_tokens + max(
                0, int(routed_request.max_tokens or 0)
            )
            chunks: list[str] = []
            reasoning_chunks: list[str] = []
            try:
                if limiter is not None:
                    await limiter.acquire(estimated_tokens)
                cb = self._circuit_breakers.get(route_item.provider)
                if cb is not None and not cb.allow_request():
                    retry_after = cb.time_until_half_open() or 30.0
                    _log.warning(
                        "api_stream_circuit_open | provider=%s | model=%s | task=%s | "
                        "retry_after=%.1fs | route=%s",
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        retry_after,
                        route_item.source,
                    )
                    self._emit_circuit_rejected(
                        route_item=route_item,
                        provider_name=provider_name,
                        request=request,
                        retry_after_s=retry_after,
                    )
                    raise CircuitBreakerOpenError(route_item.provider, retry_after)
                self._emit_event(
                    "api_stream_start",
                    {
                        "call_id": call_id,
                        "provider": provider_name,
                        "model": route_item.model_id,
                        "task": request.task_type.value,
                        "route": route_item.source,
                        "max_tokens": routed_request.max_tokens,
                        "temperature": routed_request.temperature,
                        "request": routed_request.model_dump(mode="json"),
                    },
                )
                final_response: ModelResponse | None = None

                def _on_final(
                    response: ModelResponse,
                    _call_id: str = call_id,
                    _provider_name: str = provider_name,
                ) -> None:
                    nonlocal final_response
                    final_response = response
                    _settle_authoring_model_call(_call_id, response, _provider_name)

                async def _consume(
                    _adapter: ProviderAdapter = adapter,
                    _routed_request: ModelRequest = routed_request,
                    _chunks: list[str] = chunks,
                    _reasoning_chunks: list[str] = reasoning_chunks,
                    _provider_name: str = provider_name,
                    _task_value: str = request.task_type.value,
                    _call_id: str = call_id,
                ) -> None:
                    check_authoring_authority()
                    if self._spending_tracker is not None:
                        self._spending_tracker.check_budget()
                    _begin_authoring_model_call(_call_id, _routed_request, _provider_name)
                    aiter = _adapter.stream(_routed_request, on_final=_on_final).__aiter__()
                    idle_timeout = self._stream_idle_timeout_s
                    while True:
                        try:
                            if idle_timeout > 0:
                                raw = await asyncio.wait_for(
                                    aiter.__anext__(), timeout=idle_timeout
                                )
                            else:
                                raw = await aiter.__anext__()
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            raise asyncio.TimeoutError(
                                f"Stream idle timeout: no chunk received for {idle_timeout}s "
                                f"(task={_task_value}, provider={_provider_name})"
                            ) from None
                        chunk = to_stream_chunk(raw)
                        if chunk.is_empty():
                            continue
                        if chunk.reset:
                            _chunks.clear()
                            _reasoning_chunks.clear()
                        if chunk.content:
                            _chunks.append(chunk.content)
                        if chunk.reasoning:
                            _reasoning_chunks.append(chunk.reasoning)
                        if on_chunk is not None:
                            try:
                                on_chunk(chunk)
                            except Exception as exc:
                                # Allow stream-level abort signals to propagate.
                                if type(exc).__name__ == "StreamOutputInflationError":
                                    raise
                                _log.debug("stream_chunk_callback_failed | error=%s", exc)
                        if on_delta is not None and chunk.content:
                            try:
                                on_delta(chunk.content)
                            except Exception as exc:
                                _log.debug("stream_delta_callback_failed | error=%s", exc)

                if self._request_timeout_s > 0:
                    await asyncio.wait_for(_consume(), timeout=self._request_timeout_s)
                else:
                    await _consume()
                response = final_response
                if response is None:
                    elapsed_ms = round((time.monotonic() - call_start) * 1000, 2)
                    response = ModelResponse(
                        content="".join(chunks),
                        model_id=str(route_item.model_id or ""),
                        latency_ms=elapsed_ms,
                    )
                call_elapsed = time.monotonic() - call_start
                if _is_length_truncated_response(response):
                    self._remember_output_budget(
                        request.task_type,
                        provider=route_item.provider,
                        model_id=route_item.model_id,
                        max_tokens=_next_length_retry_tokens(
                            routed_request.max_tokens,
                            get_model_max_output_tokens(
                                str(response.model_id or route_item.model_id or "")
                            ),
                        ),
                        reason="stream_length_truncated",
                    )
                response = response.model_copy(
                    update={
                        "requested_max_tokens": int(request.max_tokens or 0),
                        "effective_max_tokens": int(routed_request.max_tokens or 0),
                        "length_retry_count": 0,
                    }
                )
                if limiter is not None:
                    await limiter.report_usage(response.total_tokens, estimated_tokens)
                if self._spending_tracker is not None and response.cost_usd > 0:
                    self._spending_tracker.record(response.cost_usd)
                cb = self._circuit_breakers.get(route_item.provider)
                if cb is not None:
                    cb.record_success()
                self._emit_event(
                    "api_stream_done",
                    {
                        "call_id": call_id,
                        "provider": provider_name,
                        "model": route_item.model_id,
                        "task": request.task_type.value,
                        "latency_ms": round(call_elapsed * 1000, 2),
                        "prompt_tokens": response.prompt_tokens,
                        "completion_tokens": response.completion_tokens,
                        "total_tokens": response.total_tokens,
                        "cost_usd": response.cost_usd,
                        "route": route_item.source,
                        "response": response.model_dump(mode="json"),
                    },
                )
                return response
            except (AuthoringAuthorityError, BudgetExceededError):
                raise
            except asyncio.CancelledError:
                _settle_authoring_model_call(call_id, None, provider_name)
                raise
            except Exception as exc:
                _settle_authoring_model_call(call_id, None, provider_name)
                category = _failure_category(exc)
                failure_categories.append(category)
                partial_text = "".join(chunks)
                if len(partial_text) > len(best_partial_text):
                    best_partial_text = partial_text
                    best_partial_reasoning = "".join(reasoning_chunks)
                    best_partial_provider = provider_name
                    best_partial_model_id = str(route_item.model_id or "")
                self._record_provider_route_failure(
                    route_item=route_item,
                    provider_name=provider_name,
                    request=request,
                    category=category,
                    logical_attempt_count=1,
                )
                final_error = exc
                self._emit_event(
                    "api_stream_error",
                    {
                        "call_id": call_id,
                        "provider": provider_name,
                        "model": route_item.model_id,
                        "task": request.task_type.value,
                        "route": route_item.source,
                        "error": _extract_provider_error_details(exc),
                    },
                )
                if limiter is not None:
                    await limiter.report_usage(0, estimated_tokens)
                if route_index + 1 < len(candidate_routes):
                    if (chunks or reasoning_chunks) and on_chunk is not None:
                        try:
                            on_chunk(
                                StreamChunk(
                                    reset=True,
                                    reset_reason="模型连接中断，正在切换备用路由。",
                                )
                            )
                        except Exception as callback_exc:
                            _log.debug(
                                "stream_reset_callback_failed | error=%s",
                                callback_exc,
                            )
                    continue
                break
        is_transient = _aggregate_route_failure_is_transient(failure_categories)
        error = ModelGatewayError(
            f"all stream route attempts failed for task={request.task_type.value}",
            is_transient=is_transient,
            task=request.task_type.value,
            failure_categories=failure_categories,
            provider_health=self.provider_health_snapshot(),
        )
        error.attach_partial_stream(
            text=best_partial_text,
            reasoning=best_partial_reasoning,
            provider=best_partial_provider,
            model_id=best_partial_model_id,
        )
        raise error from final_error

    async def _execute_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        task_override = self._task_providers.get(request.task_type)
        fallback_routes = list(self._task_fallbacks.get(request.task_type, []))

        @dataclass
        class _ResolvedRoute:
            provider: str
            model_id: str | None
            thinking: bool
            thinking_mode: str
            multi_turn: bool
            source: str

        def _dedupe_key(route: _ResolvedRoute) -> tuple[str, str, str, bool]:
            return (
                route.provider.strip().lower(),
                str(route.model_id or "").strip().lower(),
                route.thinking_mode,
                bool(route.multi_turn),
            )

        def _resolve_route(
            *,
            provider_hint: str | None,
            model_hint: str | None,
            route_override: TaskRouteOverride | None,
            source: str,
        ) -> _ResolvedRoute:
            resolved_provider = provider_hint
            resolved_model = model_hint
            thinking = bool(request.thinking)
            thinking_mode = normalize_thinking_mode(
                request.thinking_mode,
                thinking=thinking,
            )
            multi_turn = bool(request.multi_turn)

            if resolved_provider is None and route_override is not None:
                resolved_provider = route_override.provider
                if resolved_model is None and route_override.model_id:
                    resolved_model = route_override.model_id
                if not request.thinking_mode:
                    thinking_mode = normalize_thinking_mode(
                        route_override.thinking_mode,
                        thinking=route_override.thinking,
                    )
                thinking = thinking_mode_enabled(thinking_mode)
                if route_override.multi_turn and not multi_turn:
                    multi_turn = True

            adapter = self._get_adapter(resolved_provider)
            if resolved_model is None:
                tier = self._resolve_tier(request.task_type)
                resolved_model = (
                    self._tier_to_model.get(tier) or adapter.default_model or "gpt-4o-mini"
                )
            if request.task_type in _RESULT_ONLY_STRUCTURED_TASKS:
                thinking = False
                thinking_mode = "off"

            return _ResolvedRoute(
                provider=(resolved_provider or self._default_provider),
                model_id=resolved_model,
                thinking=thinking,
                thinking_mode=thinking_mode,
                multi_turn=multi_turn,
                source=source,
            )

        candidate_routes: list[_ResolvedRoute] = []
        try:
            candidate_routes.append(
                _resolve_route(
                    provider_hint=provider,
                    model_hint=request.model_id,
                    route_override=task_override,
                    source="primary",
                )
            )
        except KeyError as exc:
            _log.error(
                "api_route_primary_invalid | task=%s | provider=%s | error=%s",
                request.task_type.value,
                provider or (task_override.provider if task_override else self._default_provider),
                exc,
            )
        for idx, fb in enumerate(fallback_routes, start=1):
            try:
                candidate_routes.append(
                    _resolve_route(
                        provider_hint=fb.provider,
                        model_hint=fb.model_id,
                        route_override=fb,
                        source=f"fallback_{idx}",
                    )
                )
            except KeyError as exc:
                _log.warning(
                    "api_route_fallback_invalid_skipped | task=%s | fallback_index=%d | provider=%s | error=%s",
                    request.task_type.value,
                    idx,
                    fb.provider,
                    exc,
                )

        if not candidate_routes:
            raise ModelGatewayError(f"no valid routes available for task={request.task_type.value}")

        deduped_routes: list[_ResolvedRoute] = []
        seen_route_keys: set[tuple[str, str, str, bool]] = set()
        for route_item in candidate_routes:
            route_key = _dedupe_key(route_item)
            if route_key in seen_route_keys:
                continue
            seen_route_keys.add(route_key)
            deduped_routes.append(route_item)

        if request.require_native_structured_output:
            eligible_routes: list[_ResolvedRoute] = []
            rejected_routes: list[dict[str, str]] = []
            for route_item in deduped_routes:
                reason = _native_structured_output_skip_reason(
                    request,
                    provider=route_item.provider,
                    model_id=route_item.model_id,
                    thinking=route_item.thinking,
                )
                if reason is None:
                    eligible_routes.append(route_item)
                    continue
                rejected_routes.append(
                    {
                        "provider": route_item.provider,
                        "model": str(route_item.model_id or ""),
                        "route": route_item.source,
                        "reason": reason,
                    }
                )
            if rejected_routes:
                self._emit_event(
                    "api_route_capability_skipped",
                    {
                        "task": request.task_type.value,
                        "require_native_structured_output": True,
                        "rejected_routes": rejected_routes,
                    },
                )
            if not eligible_routes:
                details = ", ".join(
                    f"{item['provider']}/{item['model']} ({item['reason']})"
                    for item in rejected_routes
                )
                raise ModelGatewayError(
                    "task requires native structured output but no configured route "
                    f"supports it: task={request.task_type.value}; rejected=[{details}]"
                )
            deduped_routes = eligible_routes

        # Budget gate — reject before incurring cost
        check_authoring_authority()
        if self._spending_tracker is not None:
            self._spending_tracker.check_budget()

        temperature_jitter = resolve_temperature_jitter(
            task_type=request.task_type,
            base_temperature=request.temperature,
            enabled=self._creative_temperature_jitter_enabled,
            up_delta=self._creative_temperature_jitter_up_delta,
            down_delta=self._creative_temperature_jitter_down_delta,
            scope=self._creative_temperature_jitter_scope,
            custom_tasks=self._creative_temperature_jitter_custom_tasks,
            allowed=request.temperature_jitter_allowed,
        )

        max_retries = 2
        final_error: Exception | None = None
        failure_categories: list[str] = []

        for route_index, route_item in enumerate(deduped_routes):
            adapter = self._get_adapter(route_item.provider)
            update_fields: dict[str, object] = {
                "model_id": route_item.model_id,
                "provider_id": route_item.provider,
                "thinking": route_item.thinking,
                "thinking_mode": route_item.thinking_mode,
                "multi_turn": route_item.multi_turn,
                "temperature": temperature_jitter.actual_temperature,
            }
            if request.max_tokens is not None and route_item.model_id is not None:
                model_output_limit = get_model_max_output_tokens(route_item.model_id)
                if request.max_tokens > model_output_limit:
                    _log.info(
                        "max_tokens_clamped | model=%s | requested=%d | limit=%d",
                        route_item.model_id,
                        request.max_tokens,
                        model_output_limit,
                    )
                    update_fields["max_tokens"] = model_output_limit
            routed_request = request.model_copy(update=update_fields)
            routed_request = self._with_learned_output_budget(
                routed_request,
                provider=route_item.provider,
                model_id=route_item.model_id,
                event="api_call_output_budget_reused",
            )
            context_fit = _measure_request_context(
                routed_request,
                provider=route_item.provider,
                model_id=str(route_item.model_id or ""),
            )
            if not context_fit.fits:
                payload = {
                    "provider": route_item.provider,
                    "model": route_item.model_id,
                    "task": request.task_type.value,
                    "route": route_item.source,
                    **context_fit.event_payload(),
                }
                self._emit_event("api_route_context_skipped", payload)
                final_error = ContextLengthError(
                    "complete prompt does not fit the route context window",
                    context=payload,
                )
                failure_categories.append("context_length")
                has_next_route = route_index + 1 < len(deduped_routes)
                if has_next_route:
                    next_route = deduped_routes[route_index + 1]
                    _log.warning(
                        "api_route_context_failover | task=%s | from=%s/%s | "
                        "to=%s/%s | overshoot_tokens=%d",
                        request.task_type.value,
                        route_item.provider,
                        route_item.model_id,
                        next_route.provider,
                        next_route.model_id,
                        context_fit.overshoot_tokens,
                    )
                    self._emit_event(
                        "api_call_failover",
                        {
                            "task": request.task_type.value,
                            "from_provider": route_item.provider,
                            "from_model": route_item.model_id,
                            "to_provider": next_route.provider,
                            "to_model": next_route.model_id,
                            "reason": "context_length",
                        },
                    )
                    continue
                break
            call_id = uuid.uuid4().hex
            provider_name = getattr(adapter, "provider_name", route_item.provider)

            # ── Cache check (idempotent tasks only) ──
            if self._cache is not None and request.task_type in _CACHEABLE_TASK_TYPES:
                cached = self._cache.get(routed_request)
                if cached is not None:
                    _log.info(
                        "api_call_cache_hit | provider=%s | model=%s | task=%s | route=%s",
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        route_item.source,
                    )
                    self._emit_event(
                        "api_call_cache_hit",
                        {
                            "call_id": call_id,
                            "provider": provider_name,
                            "model": route_item.model_id,
                            "task": request.task_type.value,
                            "route": route_item.source,
                        },
                    )
                    return cached

            limiter = self._rate_limiter.get(provider_name)
            estimated_tokens = context_fit.prompt_tokens + max(
                0, int(routed_request.max_tokens or 0)
            )
            if limiter is not None:
                await limiter.acquire(estimated_tokens)

            # Reserve the provider breaker before recording a model-call start.
            # A rejected route did not make an HTTP request and must not leave an
            # orphaned ``*_incomplete.json`` artifact in the project run log.
            cb = self._circuit_breakers.get(route_item.provider)
            if cb is not None and not cb.allow_request():
                retry_after = cb.time_until_half_open() or 30.0
                route_error = CircuitBreakerOpenError(route_item.provider, retry_after)
                failure_categories.append("circuit_open")
                final_error = route_error
                _log.warning(
                    "api_call_circuit_open | provider=%s | model=%s | task=%s | "
                    "retry_after=%.1fs | route=%s",
                    provider_name,
                    route_item.model_id,
                    request.task_type.value,
                    retry_after,
                    route_item.source,
                )
                self._emit_circuit_rejected(
                    route_item=route_item,
                    provider_name=provider_name,
                    request=request,
                    retry_after_s=retry_after,
                )
                if route_index + 1 < len(deduped_routes):
                    next_route = deduped_routes[route_index + 1]
                    _log.warning(
                        "api_call_failover | task=%s | from=%s/%s | to=%s/%s",
                        request.task_type.value,
                        provider_name,
                        route_item.model_id,
                        next_route.provider,
                        next_route.model_id,
                    )
                    self._emit_event(
                        "api_call_failover",
                        {
                            "task": request.task_type.value,
                            "from_provider": provider_name,
                            "from_model": route_item.model_id,
                            "to_provider": next_route.provider,
                            "to_model": next_route.model_id,
                            "reason": "circuit_open",
                        },
                    )
                    continue
                break

            _log.info(
                "api_call_start | provider=%s | model=%s | task=%s | max_tokens=%s | temperature=%s | route=%s",
                provider_name,
                route_item.model_id,
                request.task_type.value,
                routed_request.max_tokens,
                routed_request.temperature,
                route_item.source,
            )
            self._emit_event(
                "api_call_start",
                {
                    "call_id": call_id,
                    "provider": provider_name,
                    "model": route_item.model_id,
                    "task": request.task_type.value,
                    "max_tokens": routed_request.max_tokens,
                    "temperature": routed_request.temperature,
                    "temperature_base": temperature_jitter.base_temperature,
                    "temperature_jitter_enabled": temperature_jitter.enabled,
                    "temperature_jitter_range": [
                        temperature_jitter.range_min,
                        temperature_jitter.range_max,
                    ],
                    "temperature_jitter_reason": temperature_jitter.reason,
                    "route": route_item.source,
                    "request": routed_request.model_dump(mode="json"),
                },
            )

            call_start = time.monotonic()
            route_response: ModelResponse | None = None
            route_error: Exception | None = None
            route_retry_timeout_s = self._route_retry_loop_timeout_s(max_retries)
            # Layer 3: retry-loop timeout wraps all attempts for this route.  It
            # must account for length-expansion retries, while each individual
            # adapter call remains bounded by _request_timeout_s.
            if route_retry_timeout_s > 0:
                try:
                    route_response = await asyncio.wait_for(
                        self._retry_loop(
                            adapter,
                            routed_request,
                            route_item,
                            provider_name,
                            call_id,
                            call_start,
                            limiter,
                            estimated_tokens,
                            max_retries,
                            request,
                        ),
                        timeout=route_retry_timeout_s,
                    )
                except asyncio.TimeoutError:
                    total_elapsed = time.monotonic() - call_start
                    _log.error(
                        "api_call_total_timeout | provider=%s | model=%s | task=%s | "
                        "total_timeout_s=%.0f | elapsed_s=%.1f | route=%s",
                        provider_name,
                        route_item.model_id,
                        request.task_type.value,
                        route_retry_timeout_s,
                        total_elapsed,
                        route_item.source,
                    )
                    route_response = None
                    route_error = asyncio.TimeoutError(
                        f"Total request timeout ({route_retry_timeout_s:.0f}s) exceeded "
                        f"after {total_elapsed:.1f}s for task={request.task_type.value}"
                    )
                    self._emit_event(
                        "api_call_timeout",
                        {
                            "call_id": call_id,
                            "provider": provider_name,
                            "model": route_item.model_id,
                            "task": request.task_type.value,
                            "latency_ms": round(total_elapsed * 1000, 2),
                            "timeout_s": route_retry_timeout_s,
                            "timeout_layer": "route_retry_loop",
                            "request_timeout_s": self._request_timeout_s,
                            "attempts": max_retries + 1,
                            "route": route_item.source,
                        },
                    )
                except Exception as exc:
                    route_error = exc
                    route_response = None
            else:
                try:
                    route_response = await self._retry_loop(
                        adapter,
                        routed_request,
                        route_item,
                        provider_name,
                        call_id,
                        call_start,
                        limiter,
                        estimated_tokens,
                        max_retries,
                        request,
                    )
                except Exception as exc:
                    route_error = exc
                    route_response = None

            if route_response is not None:
                if self._cache is not None and request.task_type in _CACHEABLE_TASK_TYPES:
                    self._cache.put(routed_request, route_response)
                return route_response

            if isinstance(route_error, (AuthoringAuthorityError, BudgetExceededError)):
                raise route_error

            if route_error is None:
                route_error = ModelGatewayError(
                    f"route failed for task={request.task_type.value} "
                    f"provider={provider_name} model={route_item.model_id}",
                    is_transient=True,
                )
            final_error = route_error
            failure_category = _failure_category(route_error)
            failure_categories.append(failure_category)
            self._record_provider_route_failure(
                route_item=route_item,
                provider_name=provider_name,
                request=request,
                category=failure_category,
                logical_attempt_count=max_retries + 1,
            )

            if limiter is not None:
                await limiter.report_usage(0, estimated_tokens)

            has_next_route = route_index + 1 < len(deduped_routes)
            if has_next_route:
                next_route = deduped_routes[route_index + 1]
                _log.warning(
                    "api_call_failover | task=%s | from=%s/%s | to=%s/%s",
                    request.task_type.value,
                    provider_name,
                    route_item.model_id,
                    next_route.provider,
                    next_route.model_id,
                )
                self._emit_event(
                    "api_call_failover",
                    {
                        "task": request.task_type.value,
                        "from_provider": provider_name,
                        "from_model": route_item.model_id,
                        "to_provider": next_route.provider,
                        "to_model": next_route.model_id,
                    },
                )
                continue

            break

        if self._dlq is not None and final_error is not None:
            self._dlq.enqueue(
                task_type=request.task_type.value,
                request=request.model_dump(mode="json"),
                error=str(final_error) if final_error else "unknown",
                routes_tried=[
                    {"provider": r.provider, "model": r.model_id, "source": r.source}
                    for r in deduped_routes
                ],
            )
            _log.warning(
                "dlq_enqueued | task=%s | error=%s | routes=%d",
                request.task_type.value,
                final_error,
                len(deduped_routes),
            )

        final_error_text = str(final_error or "")
        is_rate_limit = "rate_limit" in failure_categories
        distinct_providers = sorted(
            {str(route.provider) for route in deduped_routes if route.provider}
        )
        self._emit_event(
            "api_call_all_routes_failed",
            {
                "task": request.task_type.value,
                "routes_tried": [
                    {
                        "provider": route.provider,
                        "model": route.model_id,
                        "source": route.source,
                    }
                    for route in deduped_routes
                ],
                "distinct_providers": distinct_providers,
                "is_rate_limit": is_rate_limit,
                "failure_categories": failure_categories,
                "provider_health": self.provider_health_snapshot(),
                "error_type": type(final_error).__name__ if final_error else "",
                "error_message": final_error_text[:512],
                "recovery_hint": (
                    "配置 NOVEL_FORGE_TASK_FALLBACK_ROUTING 加入其他 provider，"
                    "或等待当前 provider 配额恢复后重跑。"
                )
                if is_rate_limit
                else (
                    "检查网络连接、代理与 provider 可用性。"
                    if "timeout" in failure_categories
                    else "检查 provider 配置与 API key。"
                ),
            },
        )
        _log.error(
            "api_call_all_routes_failed | task=%s | providers=%s | rate_limit=%s | error_type=%s",
            request.task_type.value,
            distinct_providers,
            is_rate_limit,
            type(final_error).__name__ if final_error else "",
        )

        is_transient = _aggregate_route_failure_is_transient(failure_categories)
        raise ModelGatewayError(
            f"all route attempts failed for task={request.task_type.value}",
            is_transient=is_transient,
            task=request.task_type.value,
            failure_categories=failure_categories,
            provider_health=self.provider_health_snapshot(),
            routes_tried=[
                {"provider": route.provider, "model": route.model_id, "source": route.source}
                for route in deduped_routes
            ],
        ) from final_error
