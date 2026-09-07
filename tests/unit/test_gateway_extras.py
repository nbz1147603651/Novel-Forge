"""Additional gateway tests for router error extraction, base adapter, types, circuit breaker, DLQ, and secure_keys."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from novel_forge.gateway.dead_letter_queue import DeadLetterQueue
from novel_forge.gateway.router import (
    ModelRouter,
    _extract_embedded_error_payload,
    _extract_provider_error_details,
)
from novel_forge.gateway.secure_keys import get_key, keyring_available
from novel_forge.gateway.types import ModelRequest, ModelResponse

# ═══════════════════════════════════════════════════════════════════════════
# _extract_embedded_error_payload
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractEmbeddedErrorPayload:
    def test_valid_json_payload(self) -> None:
        msg = "Error code: 500 - {'error': {'message': 'timeout'}}"
        result = _extract_embedded_error_payload(msg)
        assert result is not None
        assert result["error"]["message"] == "timeout"

    def test_too_long_message(self) -> None:
        result = _extract_embedded_error_payload("x" * 10001)
        assert result is None

    def test_no_match(self) -> None:
        result = _extract_embedded_error_payload("plain error message")
        assert result is None

    def test_invalid_json_and_invalid_eval(self) -> None:
        msg = "Error code: 500 - {not valid json or python}"
        result = _extract_embedded_error_payload(msg)
        assert result is None

    def test_non_dict_result(self) -> None:
        msg = "Error code: 500 - [1, 2, 3]"
        result = _extract_embedded_error_payload(msg)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# _extract_provider_error_details
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractProviderErrorDetails:
    def test_basic_exception(self) -> None:
        exc = ValueError("simple error")
        details = _extract_provider_error_details(exc)
        assert details["type"] == "ValueError"
        assert details["message"] == "simple error"
        assert details["status_code"] is None

    def test_exception_with_status_code(self) -> None:
        exc = Exception("rate limited")
        exc.status_code = 429
        exc.request_id = "req-abc"
        details = _extract_provider_error_details(exc)
        assert details["status_code"] == 429
        assert details["request_id"] == "req-abc"

    def test_exception_with_provider_attrs(self) -> None:
        exc = Exception("bad request")
        exc.type = "invalid_request_error"
        exc.code = "invalid_param"
        exc.param = "model"
        details = _extract_provider_error_details(exc)
        assert details["provider_error_type"] == "invalid_request_error"
        assert details["provider_error_code"] == "invalid_param"
        assert details["provider_error_param"] == "model"


# ═══════════════════════════════════════════════════════════════════════════
# ModelRequest / ModelResponse types
# ═══════════════════════════════════════════════════════════════════════════


class TestModelTypes:
    def test_model_request_has_defaults(self) -> None:
        from novel_forge.core.constants import TaskType

        req = ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "test"}],
        )
        assert req.temperature == 0.7
        assert req.temperature_jitter_allowed is True
        assert req.max_tokens == 4096
        assert req.thinking is False
        assert req.multi_turn is False

    def test_model_response(self) -> None:
        resp = ModelResponse(
            content="hello",
            model_id="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
            latency_ms=500.0,
        )
        assert resp.content == "hello"
        assert resp.total_tokens == 150

    def test_model_response_dump(self) -> None:
        resp = ModelResponse(
            content="test",
            model_id="mock",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.0,
            latency_ms=100.0,
        )
        dumped = resp.model_dump(mode="json")
        assert isinstance(dumped, dict)


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker
# ═══════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    def test_initial_state_closed(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=3, recovery_timeout_s=30)
        assert cb.allow_request() is True

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=2, recovery_timeout_s=30)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_half_open_after_recovery(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=1, recovery_timeout_s=0.1)
        cb.record_failure()
        assert cb.allow_request() is False
        time.sleep(0.15)
        assert cb.allow_request() is True

    def test_record_success_resets(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=2, recovery_timeout_s=30)
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.allow_request() is True

    def test_time_until_half_open(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=1, recovery_timeout_s=60)
        cb.record_failure()
        remaining = cb.time_until_half_open()
        assert remaining is not None
        assert 0 < remaining <= 60

    def test_time_until_half_open_when_closed(self) -> None:
        cb = CircuitBreaker(provider="test", failure_threshold=1, recovery_timeout_s=1)
        assert cb.time_until_half_open() is None

    def test_circuit_breaker_open_error(self) -> None:
        err = CircuitBreakerOpenError("test-provider", 30.0)
        assert err.provider == "test-provider"
        assert err.retry_after_s == 30.0


# ═══════════════════════════════════════════════════════════════════════════
# DeadLetterQueue
# ═══════════════════════════════════════════════════════════════════════════


class TestDeadLetterQueue:
    def test_enqueue_and_size(self, tmp_path) -> None:
        dlq = DeadLetterQueue(storage_dir=tmp_path, max_entries=10)
        dlq.enqueue(
            task_type="draft_chapter", request={"msg": "test"}, error="timeout", routes_tried=[]
        )
        assert dlq.size() == 1

    def test_max_entries_limit(self, tmp_path) -> None:
        dlq = DeadLetterQueue(storage_dir=tmp_path, max_entries=2)
        dlq.enqueue(task_type="t1", request={}, error="e1", routes_tried=[])
        dlq.enqueue(task_type="t2", request={}, error="e2", routes_tried=[])
        dlq.enqueue(task_type="t3", request={}, error="e3", routes_tried=[])
        assert dlq.size() == 2

    def test_purge(self, tmp_path) -> None:
        dlq = DeadLetterQueue(storage_dir=tmp_path, max_entries=10)
        dlq.enqueue(task_type="t1", request={}, error="e1", routes_tried=[])
        dlq.purge()
        assert dlq.size() == 0


# ═══════════════════════════════════════════════════════════════════════════
# mask_api_key
# ═══════════════════════════════════════════════════════════════════════════


class TestSecureKeys:
    def test_keyring_availability(self) -> None:
        result = keyring_available()
        assert isinstance(result, bool)

    def test_get_key_returns_string(self) -> None:
        result = get_key("some_key")
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════
# ProviderAdapter base class
# ═══════════════════════════════════════════════════════════════════════════


class TestProviderAdapterBase:
    def test_default_model_property(self) -> None:
        adapter = MockAdapter()
        assert isinstance(adapter.provider_name, str)

    def test_provider_name(self) -> None:
        adapter = MockAdapter()
        assert adapter.provider_name == "mock"

    def test_health_check(self) -> None:
        adapter = MockAdapter()
        import asyncio

        result = asyncio.run(adapter.health_check())
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
# ModelRouter — observer events and shutdown
# ═══════════════════════════════════════════════════════════════════════════


class TestModelRouterObservers:
    def test_emit_event_with_no_observers(self) -> None:
        router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")
        router._emit_event("test", {"key": "value"})

    def test_emit_event_with_failing_observer(self) -> None:
        router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")

        def bad_observer(event: str, payload: dict[str, object]) -> None:
            _ = (event, payload)
            raise ZeroDivisionError

        router.add_observer(bad_observer)
        router._emit_event("test", {})

    def test_shutdown_with_no_shutdown_hook(self) -> None:
        adapter = MockAdapter()
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
        import asyncio

        asyncio.run(router.shutdown())

    def test_shutdown_with_failing_adapter(self) -> None:
        adapter = MockAdapter()
        adapter.shutdown = MagicMock(side_effect=RuntimeError("fail"))
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
        import asyncio

        asyncio.run(router.shutdown())

    def test_aclose_alias(self) -> None:
        router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")
        import asyncio

        asyncio.run(router.aclose())

    def test_duplicate_adapter_shutdown(self) -> None:
        adapter = MockAdapter()
        router = ModelRouter(
            adapters={"mock1": adapter, "mock2": adapter},
            default_provider="mock1",
        )
        import asyncio

        asyncio.run(router.shutdown())
