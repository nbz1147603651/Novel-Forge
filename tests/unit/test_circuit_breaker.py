"""Tests for gateway.circuit_breaker."""

from __future__ import annotations

import time

import pytest

from novel_forge.gateway.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
)


@pytest.fixture()
def breaker() -> CircuitBreaker:
    return CircuitBreaker("test-provider", failure_threshold=3, recovery_timeout_s=0.5)


class TestClosedToOpenAfterThreeFailures:
    def test_closed_to_open_after_three_failures(self, breaker: CircuitBreaker) -> None:
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 1

        breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 2

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3

    def test_closed_stays_closed_on_success(self, breaker: CircuitBreaker) -> None:
        """Successes in CLOSED state keep the circuit closed and reset counter."""
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.failure_count == 2

        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

        breaker.record_failure()
        assert breaker.failure_count == 1
        assert breaker.state == CircuitState.CLOSED


class TestHalfOpenAllowsProbe:
    def test_half_open_allows_probe(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert not breaker.allow_request()

        time.sleep(0.55)
        assert breaker.allow_request()
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_allows_only_one_concurrent_probe(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()

        time.sleep(0.55)
        assert breaker.allow_request()
        assert not breaker.allow_request()


class TestProbeSuccessClosesCircuit:
    def test_probe_success_closes_circuit(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.55)
        assert breaker.allow_request()
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0


class TestProbeFailureReopensCircuit:
    def test_probe_failure_reopens_circuit(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.55)
        assert breaker.allow_request()
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == 3


class TestDisabledBreaker:
    def test_disabled_breaker_always_allows(self) -> None:
        breaker = CircuitBreaker("disabled", failure_threshold=1, enabled=False)
        for _ in range(10):
            breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.allow_request()


class TestReset:
    def test_reset_opens_to_closed(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        breaker.reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0


class TestTimeUntilHalfOpen:
    def test_returns_none_when_closed(self, breaker: CircuitBreaker) -> None:
        assert breaker.time_until_half_open() is None

    def test_returns_remaining_when_open(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        remaining = breaker.time_until_half_open()
        assert remaining is not None
        assert 0.0 < remaining <= 0.5

    def test_returns_none_when_half_open(self, breaker: CircuitBreaker) -> None:
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        time.sleep(0.55)
        assert breaker.allow_request()
        assert breaker.time_until_half_open() is None
