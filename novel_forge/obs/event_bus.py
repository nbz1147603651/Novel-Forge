"""Unified pipeline event stream with Pi-style Observer pattern.

This module provides ``PipelineEventBus`` — a thin, synchronous Observer layer
on top of the existing ``core.infra.event_bus.EventBus``. It adds:

1. Pi-style ``subscribe(listener) -> unsubscribe`` (returns a callable).
2. Optional event-type filtering per subscriber.
3. Synchronous delivery (safe for Desktop Qt signal bridging).
4. A ``bridge_on_step()`` adapter that converts the legacy
   ``on_step(event_name, payload)`` calls into ``PipelineEvent`` publications.

Design constraints:
- Thread-safe (Desktop worker threads + main thread).
- Listener exceptions are isolated (one bad listener cannot break others).
- Zero breaking changes: existing on_step call sites need no modification.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from novel_forge.obs.events import PipelineEvent

logger = logging.getLogger(__name__)

# ── Listener type ─────────────────────────────────────────────────────────────

PipelineListener = Callable[[PipelineEvent], None]
Unsubscribe = Callable[[], None]


# ── PipelineEventBus ──────────────────────────────────────────────────────────


class PipelineEventBus:
    """Synchronous Observer-pattern event bus for pipeline events.

    Usage::

        bus = PipelineEventBus()

        # Subscribe to all events
        unsub = bus.subscribe(lambda e: print(e.event_type))

        # Subscribe to specific event types only
        unsub2 = bus.subscribe(
            lambda e: handle_quality(e),
            event_types={"quality_score", "quality_gate_regression_detected"},
        )

        # Publish
        bus.emit(PipelineEvent(event_type="quality_score", payload={"score": 8.5}))

        # Unsubscribe
        unsub()
        unsub2()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # listener_id -> (listener, event_types_filter or None)
        self._subscribers: dict[int, tuple[PipelineListener, frozenset[str] | None]] = {}
        self._next_id: int = 0

    def subscribe(
        self,
        listener: PipelineListener,
        *,
        event_types: set[str] | frozenset[str] | None = None,
    ) -> Unsubscribe:
        """Register a listener. Returns an unsubscribe function.

        Args:
            listener: Callable invoked for each matching event.
            event_types: If provided, the listener only receives events whose
                ``event_type`` is in this set. If None, receives ALL events.

        Returns:
            A zero-argument callable that removes this subscription.
        """
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            frozen_types = frozenset(event_types) if event_types is not None else None
            self._subscribers[sub_id] = (listener, frozen_types)

        def _unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(sub_id, None)

        return _unsubscribe

    def emit(self, event: PipelineEvent) -> None:
        """Publish an event to all matching subscribers (synchronous).

        Listeners are invoked in subscription order. Exceptions in one
        listener are caught, logged, and do not affect other listeners.
        """
        with self._lock:
            snapshot = list(self._subscribers.values())

        for listener, type_filter in snapshot:
            if type_filter is not None and event.event_type not in type_filter:
                continue
            try:
                listener(event)
            except Exception:
                logger.debug(
                    "PipelineEventBus listener %s failed for event %s",
                    getattr(listener, "__name__", repr(listener)),
                    event.event_type,
                    exc_info=True,
                )

    def emit_step(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        chapter_number: int = 0,
        project_id: str = "",
        source: str = "",
    ) -> None:
        """Convenience: construct a PipelineEvent and emit it."""
        event = PipelineEvent(
            event_type=event_type,
            payload=payload,
            chapter_number=chapter_number,
            project_id=project_id,
            source=source,
        )
        self.emit(event)

    @property
    def subscriber_count(self) -> int:
        """Number of active subscriptions."""
        with self._lock:
            return len(self._subscribers)

    def clear(self) -> None:
        """Remove all subscriptions. Useful in tests."""
        with self._lock:
            self._subscribers.clear()


# ── on_step Bridge ────────────────────────────────────────────────────────────


def bridge_on_step(
    bus: PipelineEventBus,
    *,
    project_id: str = "",
    source: str = "",
) -> Callable[[str, dict[str, Any]], None]:
    """Create an ``on_step(event_name, payload)`` callback that publishes to bus.

    This bridges the legacy on_step pattern into the unified event stream
    without modifying any existing call sites::

        bus = PipelineEventBus()
        on_step = bridge_on_step(bus, project_id="my_project")
        # Now pass on_step to any runner/step as the event callback
        on_step("quality_score", {"score": 8.5, "chapter": 3})
        # → bus emits PipelineEvent(event_type="quality_score", ...)

    Args:
        bus: The PipelineEventBus to publish into.
        project_id: Default project_id for events.
        source: Default source identifier.

    Returns:
        A callback with signature ``(str, dict[str, Any]) -> None``.
    """

    def _on_step(event_name: str, payload: dict[str, Any]) -> None:
        chapter = 0
        if isinstance(payload, dict):
            chapter = int(payload.get("chapter", 0) or 0)
        bus.emit_step(
            event_name,
            payload,
            chapter_number=chapter,
            project_id=project_id,
            source=source,
        )

    return _on_step


# ── Module-level default bus (singleton) ──────────────────────────────────────

_default_bus: PipelineEventBus | None = None
_default_bus_lock = threading.Lock()


def get_pipeline_event_bus() -> PipelineEventBus:
    """Get or create the module-level default PipelineEventBus singleton."""
    global _default_bus  # noqa: PLW0603
    if _default_bus is None:
        with _default_bus_lock:
            if _default_bus is None:
                _default_bus = PipelineEventBus()
    return _default_bus
