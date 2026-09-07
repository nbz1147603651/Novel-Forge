"""Steering queue for interactive repair (Pi-inspired message injection).

Allows external actors (Desktop UI, API) to inject feedback into a running
repair loop. Two priority levels:

- Steering (LIFO, high priority): "Interrupt current direction, respond to this."
- Follow-up (FIFO, low priority): "After current round completes, also handle this."

Quality constraints:
- Steering only ADDS repair constraints; it never lowers quality thresholds.
- RepairThresholds (score_threshold, rollback_delta) are immutable.
- Global repair budget (total_rounds_cap) is not bypassed by steering.
- Every steering injection is recorded to events.jsonl for auditability.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Message Types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SteeringMessage:
    """High-priority message injected during repair loop execution.

    Attributes:
        content: The user feedback / additional constraint text.
        timestamp: When the message was created.
        source: Origin identifier (e.g. "desktop_ui", "api").
        metadata: Arbitrary extra data for audit.
    """

    content: str
    timestamp: float = field(default_factory=time.time)
    source: str = "desktop_ui"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FollowUpMessage:
    """Low-priority message queued for after current repair round.

    Attributes:
        content: The follow-up instruction text.
        timestamp: When the message was created.
        source: Origin identifier.
        metadata: Arbitrary extra data.
    """

    content: str
    timestamp: float = field(default_factory=time.time)
    source: str = "desktop_ui"
    metadata: dict[str, Any] = field(default_factory=dict)


# ── SteeringQueue ─────────────────────────────────────────────────────────────


class SteeringQueue:
    """Thread-safe dual-priority message queue for repair loop interaction.

    Usage::

        queue = SteeringQueue()

        # From Desktop UI thread:
        queue.steer(SteeringMessage(content="这个角色不应该这样说话"))

        # From repair loop (each round):
        msg = queue.poll()
        if msg:
            extra_constraints.append(msg.content)
    """

    def __init__(self, *, steering_mode: str = "replace") -> None:
        """Initialize the steering queue.

        Args:
            steering_mode: "replace" (new steering replaces old, default) or
                "append" (new steering appends to queue).
        """
        self._lock = threading.Lock()
        self._steering: deque[SteeringMessage] = deque()
        self._follow_ups: deque[FollowUpMessage] = deque()
        self._steering_mode = steering_mode
        self._injection_log: list[dict[str, Any]] = []

    # ── Injection ─────────────────────────────────────────────────────────

    def steer(self, message: SteeringMessage) -> None:
        """Inject a high-priority steering message.

        In "replace" mode, replaces any pending steering messages.
        In "append" mode, adds to the steering queue.
        """
        with self._lock:
            if self._steering_mode == "replace":
                self._steering.clear()
            self._steering.append(message)
            self._injection_log.append({
                "type": "steering",
                "content": message.content[:100],
                "source": message.source,
                "timestamp": message.timestamp,
            })
        logger.info(
            "SteeringQueue: steering injected from '%s': '%s'",
            message.source,
            message.content[:80],
        )

    def follow_up(self, message: FollowUpMessage) -> None:
        """Queue a low-priority follow-up message (FIFO)."""
        with self._lock:
            self._follow_ups.append(message)
            self._injection_log.append({
                "type": "follow_up",
                "content": message.content[:100],
                "source": message.source,
                "timestamp": message.timestamp,
            })
        logger.info(
            "SteeringQueue: follow-up queued from '%s': '%s'",
            message.source,
            message.content[:80],
        )

    # ── Polling ───────────────────────────────────────────────────────────

    def poll(self) -> SteeringMessage | FollowUpMessage | None:
        """Retrieve the next message (steering first, then follow-up).

        Returns None if both queues are empty (repair loop continues normally).
        """
        with self._lock:
            if self._steering:
                return self._steering.pop()  # LIFO: latest steering wins
            if self._follow_ups:
                return self._follow_ups.popleft()  # FIFO: first follow-up first
        return None

    def poll_all(self) -> list[SteeringMessage | FollowUpMessage]:
        """Retrieve ALL pending messages (steering first, then follow-ups).

        Useful when the repair loop wants to batch-process all pending input.
        """
        with self._lock:
            messages: list[SteeringMessage | FollowUpMessage] = []
            while self._steering:
                messages.append(self._steering.pop())
            while self._follow_ups:
                messages.append(self._follow_ups.popleft())
            return messages

    # ── Introspection ─────────────────────────────────────────────────────

    @property
    def has_pending(self) -> bool:
        """True if any messages are waiting."""
        with self._lock:
            return bool(self._steering) or bool(self._follow_ups)

    @property
    def pending_count(self) -> int:
        """Total number of pending messages."""
        with self._lock:
            return len(self._steering) + len(self._follow_ups)

    @property
    def injection_log(self) -> list[dict[str, Any]]:
        """Audit log of all injections (for events.jsonl persistence)."""
        with self._lock:
            return list(self._injection_log)

    def clear(self) -> None:
        """Clear all pending messages. Useful on repair loop completion."""
        with self._lock:
            self._steering.clear()
            self._follow_ups.clear()
