"""Provider-neutral TTS failure classification and breaker tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from novel_forge.core.exceptions import AuthenticationError, ModelGatewayError, RateLimitError
from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.gateway.failures import (
    TTSFailureKind,
    TTSProviderFailure,
    TTSProviderFailurePolicy,
    classify_tts_failure,
)
from novel_forge.tts.gateway.fault_tolerant import FailureManagedTTSAdapter


def test_typed_failures_have_stable_retry_semantics() -> None:
    auth = classify_tts_failure(AuthenticationError("invalid api key"))
    limited = classify_tts_failure(RateLimitError("too many requests"), default_retry_after_s=12)

    assert auth.kind == TTSFailureKind.AUTHENTICATION
    assert auth.retryable is False
    assert auth.counts_toward_breaker is False
    assert limited.kind == TTSFailureKind.RATE_LIMIT
    assert limited.retryable is True
    assert limited.retry_after_s == 12


async def test_nonretryable_authentication_does_not_poison_provider_breaker() -> None:
    policy = TTSProviderFailurePolicy(
        "acme",
        failure_threshold=1,
        recovery_timeout_s=30,
        enabled=True,
        rate_limit_cooldown_s=10,
    )

    async def fail_auth() -> None:
        raise AuthenticationError("invalid api key")

    with pytest.raises(TTSProviderFailure) as exc_info:
        await policy.execute(fail_auth)

    assert exc_info.value.failure_kind == TTSFailureKind.AUTHENTICATION
    assert policy.breaker.failure_count == 0
    assert policy.breaker.allow_request() is True


async def test_transient_failures_open_circuit_and_fail_fast() -> None:
    policy = TTSProviderFailurePolicy(
        "acme",
        failure_threshold=1,
        recovery_timeout_s=30,
        enabled=True,
        rate_limit_cooldown_s=10,
    )

    async def fail_transient() -> None:
        raise ModelGatewayError("service unavailable", is_transient=True)

    with pytest.raises(TTSProviderFailure) as first:
        await policy.execute(fail_transient)
    with pytest.raises(TTSProviderFailure) as second:
        await policy.execute(fail_transient)

    assert first.value.failure_kind == TTSFailureKind.PROVIDER_UNAVAILABLE
    assert second.value.failure_kind == TTSFailureKind.CIRCUIT_OPEN
    assert second.value.retry_after_s > 0


async def test_adapter_decorator_normalizes_non_synthesis_operations() -> None:
    adapter = MockTTSAdapter(latency_ms=0)
    adapter.list_system_voices = AsyncMock(side_effect=AuthenticationError("invalid api key"))
    managed = FailureManagedTTSAdapter(
        adapter,
        TTSProviderFailurePolicy(
            "mock",
            failure_threshold=2,
            recovery_timeout_s=30,
            enabled=True,
            rate_limit_cooldown_s=10,
        ),
    )

    with pytest.raises(TTSProviderFailure) as exc_info:
        await managed.list_system_voices()

    assert exc_info.value.failure_kind == TTSFailureKind.AUTHENTICATION
    assert exc_info.value.retryable is False
