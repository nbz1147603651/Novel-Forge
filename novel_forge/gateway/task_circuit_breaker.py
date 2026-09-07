"""TaskCircuitBreaker — per-TaskType fault tolerance with CLOSED→OPEN→HALF_OPEN state machine.

Independent of the provider-level CircuitBreaker in ``circuit_breaker.py``.
Tracks consecutive failures per ``TaskType`` and opens the circuit after
``threshold`` consecutive failures within ``cooldown_seconds``.
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import TaskCircuitOpenError


class TaskCircuitState(enum.Enum):
    """Task circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _TaskCircuitEntry:
    """Per-TaskType state tracked by the circuit breaker."""

    state: TaskCircuitState = TaskCircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None


class TaskTypeCircuitBreaker:
    """Per-TaskType circuit breaker.

    State transitions::

        CLOSED ──(failures >= threshold)──▶ OPEN
          ▲                                    │
          │                                    │ (cooldown elapsed)
          │                                    ▼
          │                               HALF_OPEN
          │                                    │
          │  success                           │  failure
          └────────────────────────────────────┘

    Independent per ``TaskType`` — opening one task's circuit does not
    affect any other task type.
    """

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_seconds: float = 60.0,
        enabled: bool = True,
    ) -> None:
        self._threshold = max(1, threshold)
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._enabled = enabled
        self._entries: dict[TaskType, _TaskCircuitEntry] = {}
        self._lock = threading.Lock()

    # ── public API ──────────────────────────────────────────────

    def check(self, task_type: TaskType) -> None:
        """Raise :class:`TaskCircuitOpenError` if the circuit is currently open.

        Must be called at the entry point of any code path that wants to
        make an LLM call for the given task type.
        """
        if not self._enabled:
            return
        with self._lock:
            entry = self._resolve_entry(task_type)
            current = self._compute_state(entry)
            if current == TaskCircuitState.OPEN:
                remaining = self._time_until_half_open(entry)
                raise TaskCircuitOpenError(task_type, retry_after_s=remaining)

    def record_success(self, task_type: TaskType) -> None:
        """Record a successful LLM call.

        - CLOSED: reset failure counter
        - HALF_OPEN: transition to CLOSED
        """
        if not self._enabled:
            return
        with self._lock:
            entry = self._resolve_entry(task_type)
            current = self._compute_state(entry)
            if current == TaskCircuitState.HALF_OPEN:
                entry.state = TaskCircuitState.CLOSED
                entry.consecutive_failures = 0
            elif current == TaskCircuitState.CLOSED:
                entry.consecutive_failures = 0

    def record_failure(self, task_type: TaskType) -> None:
        """Record a failed LLM call.

        - CLOSED: increment failure counter; open circuit if threshold reached
        - HALF_OPEN: transition back to OPEN (restart cooldown timer)
        """
        if not self._enabled:
            return
        with self._lock:
            entry = self._resolve_entry(task_type)
            current = self._compute_state(entry)
            if current == TaskCircuitState.HALF_OPEN:
                entry.state = TaskCircuitState.OPEN
                entry.consecutive_failures = self._threshold
                entry.opened_at = time.monotonic()
            elif current == TaskCircuitState.CLOSED:
                entry.consecutive_failures += 1
                if entry.consecutive_failures >= self._threshold:
                    entry.state = TaskCircuitState.OPEN
                    entry.opened_at = time.monotonic()

    def reset(self, task_type: TaskType | None = None) -> None:
        """Manually reset breaker state.

        If *task_type* is ``None``, reset all entries.
        """
        with self._lock:
            if task_type is None:
                self._entries.clear()
                return
            entry = self._entries.get(task_type)
            if entry is not None:
                entry.state = TaskCircuitState.CLOSED
                entry.consecutive_failures = 0
                entry.opened_at = None

    def get_state(self, task_type: TaskType) -> TaskCircuitState:
        """Return the effective circuit state for *task_type*."""
        with self._lock:
            entry = self._resolve_entry(task_type)
            return self._compute_state(entry)

    def get_failure_count(self, task_type: TaskType) -> int:
        """Return the current consecutive failure count for *task_type*."""
        with self._lock:
            entry = self._resolve_entry(task_type)
            return entry.consecutive_failures

    def time_until_half_open(self, task_type: TaskType) -> float | None:
        """Seconds until the circuit may transition to HALF_OPEN.

        Returns ``None`` if already CLOSED or HALF_OPEN.
        """
        with self._lock:
            entry = self._resolve_entry(task_type)
            current = self._compute_state(entry)
            if current != TaskCircuitState.OPEN:
                return None
            elapsed = time.monotonic() - (entry.opened_at or 0.0)
            remaining = self._cooldown_seconds - elapsed
            return max(0.0, remaining)

    @property
    def enabled(self) -> bool:
        """Whether the breaker is active."""
        return self._enabled

    # ── internal ────────────────────────────────────────────────

    def _resolve_entry(self, task_type: TaskType) -> _TaskCircuitEntry:
        """Get or create the entry for *task_type*. Caller must hold ``self._lock``."""
        if task_type not in self._entries:
            self._entries[task_type] = _TaskCircuitEntry()
        return self._entries[task_type]

    def _compute_state(self, entry: _TaskCircuitEntry) -> TaskCircuitState:
        """Return effective state, auto-transitioning OPEN→HALF_OPEN.

        Caller must hold ``self._lock``.
        """
        if entry.state == TaskCircuitState.OPEN:
            if entry.opened_at is not None:
                elapsed = time.monotonic() - entry.opened_at
                if elapsed >= self._cooldown_seconds:
                    entry.state = TaskCircuitState.HALF_OPEN
        return entry.state

    def _time_until_half_open(self, entry: _TaskCircuitEntry) -> float:
        """Seconds until half-open. Caller must hold ``self._lock`` and guarantee OPEN."""
        if entry.opened_at is None:
            return 0.0
        elapsed = time.monotonic() - entry.opened_at
        return max(0.0, self._cooldown_seconds - elapsed)
