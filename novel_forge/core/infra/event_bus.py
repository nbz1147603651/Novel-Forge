"""Event bus for project lifecycle events.

This module provides a pub/sub event system for project-level events,
supporting both global subscriptions (receive all events) and
per-project subscriptions (receive only events for a specific project).

Example:
    >>> bus = EventBus()
    >>> received = []
    >>> async def handler(e):
    ...     received.append(e)
    >>> bus.subscribe(CHAPTER_COMPLETED, handler)
    >>> await bus.publish(ProjectEvent(project_id="p1", event_type=CHAPTER_COMPLETED))
    >>> assert len(received) == 1
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Event Types ────────────────────────────────────────────────────────────────

INIT_STARTED = "init_started"
INIT_COMPLETED = "init_completed"
OUTLINE_UPDATED = "outline_updated"
CHAPTER_COMPLETED = "chapter_completed"
CHAPTER_CONTRACTS_SYNCED = "chapter_contracts_synced"
SECTION_CHANGED = "section_changed"

# ── Pipeline event types (Phase 6) ────────────────────────────────────────────
STEP_STARTED = "step_started"
STEP_COMPLETED = "step_completed"
STEP_FAILED = "step_failed"
REPAIR_ROUND_STARTED = "repair_round_started"
REPAIR_ROUND_COMPLETED = "repair_round_completed"
PHASE_TRANSITION = "phase_transition"
MODEL_CALL_COMPLETED = "model_call_completed"

__all__ = [
    "EventBus",
    "ProjectEvent",
    "StepEventData",
    "INIT_STARTED",
    "INIT_COMPLETED",
    "OUTLINE_UPDATED",
    "CHAPTER_COMPLETED",
    "CHAPTER_CONTRACTS_SYNCED",
    "SECTION_CHANGED",
    "STEP_STARTED",
    "STEP_COMPLETED",
    "STEP_FAILED",
    "REPAIR_ROUND_STARTED",
    "REPAIR_ROUND_COMPLETED",
    "PHASE_TRANSITION",
    "MODEL_CALL_COMPLETED",
]


# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class StepEventData:
    """Structured payload for pipeline step events."""

    step_name: str = ""
    chapter_number: int = 0
    phase: str = ""  # planning, generate, review, polish, humanize, finalize
    duration_ms: float = 0.0
    error: str = ""
    repair_dimension: str = ""
    repair_round: int = 0
    quality_score: float = 0.0
    model: str = ""
    token_count: int = 0
    retry_count: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectEvent:
    """Event emitted during project operations."""

    project_id: Optional[str]
    event_type: str
    data: Any = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ── Type Alias ────────────────────────────────────────────────────────────────

EventCallback = Callable[[ProjectEvent], Awaitable[Any]]


# ── EventBus ──────────────────────────────────────────────────────────────────


class EventBus:
    """Thread-safe async event bus with global and per-project subscriptions.

    Subscribers can register for:
    - Global events (project_id=None): receive ALL events regardless of project
    - Project-specific events (project_id="xyz"): receive only events for that project

    When an event is published:
    1. All global subscribers for that event_type are called
    2. All project-specific subscribers matching the event's project_id are called
    3. Errors in one callback do not affect other callbacks (error isolation)

    All callbacks are invoked concurrently and awaited by ``publish``. This keeps
    delivery deterministic for desktop code that bridges sync Qt slots into the
    async bus with ``asyncio.run``.
    """

    def __init__(self) -> None:
        # Global subscribers: event_type -> list of callbacks
        self._global_subs: Dict[str, List[EventCallback]] = {}
        # Per-project subscribers: project_id -> event_type -> list of callbacks
        self._project_subs: Dict[str, Dict[str, List[EventCallback]]] = {}
        # Lock for thread-safe subscription management
        self._lock = Lock()

    # ── subscribe ──────────────────────────────────────────────────────────

    def subscribe(
        self,
        event_type: str,
        callback: EventCallback,
        *,
        project_id: Optional[str] = None,
    ) -> None:
        """Register ``callback`` to be invoked when ``event_type`` is published.

        Args:
            event_type: One of the event type constants
                (INIT_STARTED, INIT_COMPLETED, OUTLINE_UPDATED, CHAPTER_COMPLETED).
            callback: Async or sync callback invoked when event is published.
                Sync callbacks are automatically wrapped in a coroutine.
            project_id: If provided, the callback only receives events whose
                ``project_id`` matches this value. If None, the callback receives
                events from ALL projects (global subscription).

        Raises:
            TypeError: If ``callback`` is not callable.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")

        with self._lock:
            if project_id is None:
                subs = self._global_subs
            else:
                if project_id not in self._project_subs:
                    self._project_subs[project_id] = {}
                subs = self._project_subs[project_id]

            if event_type not in subs:
                subs[event_type] = []

            # Avoid duplicate registration of the same callback
            if callback not in subs[event_type]:
                subs[event_type].append(callback)

    # ── unsubscribe ────────────────────────────────────────────────────────

    def unsubscribe(
        self,
        event_type: str,
        callback: EventCallback,
        *,
        project_id: Optional[str] = None,
    ) -> None:
        """Remove ``callback`` from the subscription list for ``event_type``.

        Args:
            event_type: Event type constant.
            callback: The callback that was previously registered.
            project_id: If provided, removes from the per-project subscription.
                If None, removes from global subscriptions.
        """
        with self._lock:
            if project_id is None:
                subs = self._global_subs
            else:
                subs = self._project_subs.get(project_id, {})

            if event_type in subs:
                try:
                    subs[event_type].remove(callback)
                except ValueError:
                    pass  # Not registered – ignore silently

                # Clean up empty lists / dicts
                if not subs[event_type]:
                    del subs[event_type]
                if project_id is not None and not subs:
                    del self._project_subs[project_id]

    # ── publish ────────────────────────────────────────────────────────────

    async def publish(self, event: ProjectEvent) -> None:
        """Publish ``event`` to all matching subscribers.

        Async callbacks run concurrently. Errors raised by any individual
        callback are caught, logged, and do not affect other subscribers.

        Args:
            event: The event to broadcast.
        """
        # Collect callbacks under lock to avoid mutation during iteration
        callbacks: List[EventCallback] = []
        with self._lock:
            # Global subscribers always receive the event
            global_cbs = self._global_subs.get(event.event_type, [])
            callbacks.extend(global_cbs)

            # Per-project subscribers receive the event only if project_id matches
            if event.project_id is not None:
                proj_cbs = self._project_subs.get(event.project_id, {}).get(event.event_type, [])
                callbacks.extend(proj_cbs)

        if not callbacks:
            return

        await asyncio.gather(
            *(self._safe_invoke(cb, event) for cb in callbacks),
            return_exceptions=True,
        )

    # ── helpers ───────────────────────────────────────────────────────────

    async def _safe_invoke(self, callback: EventCallback, event: ProjectEvent) -> None:
        """Invoke ``callback`` for ``event`` with full error isolation.

        Catches any exception raised by the callback and logs it without
        re-raising, ensuring one bad subscriber cannot break the bus.
        """
        try:
            # Support both async and sync callbacks
            result = callback(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "EventBus callback %s raised for event %s: %s",
                callback.__name__,
                event,
                exc,
            )

    # ── introspection ─────────────────────────────────────────────────────

    def get_subscribers(
        self,
        event_type: str,
        *,
        project_id: Optional[str] = None,
    ) -> List[EventCallback]:
        """Return a snapshot list of subscribers for ``event_type``.

        Useful for debugging and testing.
        """
        with self._lock:
            if project_id is None:
                return list(self._global_subs.get(event_type, []))
            return list(self._project_subs.get(project_id, {}).get(event_type, []))

    def clear(self) -> None:
        """Remove all subscriptions. Useful in tests."""
        with self._lock:
            self._global_subs.clear()
            self._project_subs.clear()

    @property
    def subscription_count(self) -> int:
        """Total number of active subscriptions."""
        with self._lock:
            total = sum(len(subs) for subs in self._global_subs.values())
            for project_subs in self._project_subs.values():
                total += sum(len(subs) for subs in project_subs.values())
            return total
