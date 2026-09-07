"""CircuitBreaker — per-provider fault tolerance with CLOSED→OPEN→HALF_OPEN state machine."""

from __future__ import annotations

import enum
import threading
import time


class CircuitState(enum.Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a request is rejected because the circuit breaker is open."""

    def __init__(self, provider: str, retry_after_s: float) -> None:
        self.provider = provider
        self.retry_after_s = retry_after_s
        super().__init__(
            f"Circuit breaker OPEN for provider '{provider}'. Retry after {retry_after_s:.1f}s."
        )


class CircuitBreaker:
    """Per-provider circuit breaker.

    State transitions::

        CLOSED ──(failures >= threshold)──▶ OPEN
          ▲                                    │
          │                                    │ (recovery_timeout elapsed)
          │                                    ▼
          │                               HALF_OPEN
          │                                    │
          │  success                           │  failure
          └────────────────────────────────────┘
    """

    def __init__(
        self,
        provider: str,
        *,
        failure_threshold: int = 3,
        recovery_timeout_s: float = 30.0,
        enabled: bool = True,
    ) -> None:
        self._provider = provider
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_timeout_s = max(0.0, recovery_timeout_s)
        self._enabled = enabled

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def provider(self) -> str:
        """Provider name this breaker protects."""
        return self._provider

    @property
    def state(self) -> CircuitState:
        """Current circuit state (thread-safe, auto-transitions OPEN→HALF_OPEN)."""
        with self._lock:
            return self._compute_state()

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count."""
        with self._lock:
            return self._failure_count

    @property
    def enabled(self) -> bool:
        """Whether the breaker is active."""
        return self._enabled

    def _compute_state(self) -> CircuitState:
        """Return effective state. Must be called with ``self._lock`` held.

        Auto-transitions OPEN → HALF_OPEN when recovery timeout elapses.
        """
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout_s:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        """Check whether a request should be allowed.

        Returns True if the request can proceed, False if it should be rejected.
        Thread-safe.
        """
        with self._lock:
            if not self._enabled:
                return True
            current = self._compute_state()
            if current == CircuitState.CLOSED:
                return True
            if current == CircuitState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    return False
                self._half_open_probe_in_flight = True
                return True
            # OPEN — check if recovery timeout has elapsed
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout_s:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        """Record a successful request.

        - CLOSED: reset failure counter
        - HALF_OPEN: transition to CLOSED
        """
        if not self._enabled:
            return

        with self._lock:
            current = self._compute_state()
            if current == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_probe_in_flight = False
            elif current == CircuitState.CLOSED:
                self._failure_count = 0
            # OPEN: ignore (shouldn't happen in normal flow)

    def record_failure(self) -> None:
        """Record a failed request.

        - CLOSED: increment failure counter; open circuit if threshold reached
        - HALF_OPEN: transition back to OPEN (restart recovery timer)
        """
        if not self._enabled:
            return

        with self._lock:
            current = self._compute_state()
            self._last_failure_time = time.monotonic()

            if current == CircuitState.HALF_OPEN:
                # Probe failed — reopen circuit
                self._state = CircuitState.OPEN
                self._failure_count = self._failure_threshold
                self._half_open_probe_in_flight = False
            elif current == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                self._half_open_probe_in_flight = False

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = time.monotonic()
            self._half_open_probe_in_flight = False

    def time_until_half_open(self) -> float | None:
        """Seconds until the circuit may transition to HALF_OPEN.

        Returns None if already CLOSED or HALF_OPEN.
        """
        with self._lock:
            current = self._compute_state()
            if current in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
                return None
            elapsed = time.monotonic() - self._last_failure_time
            remaining = self._recovery_timeout_s - elapsed
            return max(0.0, remaining)
