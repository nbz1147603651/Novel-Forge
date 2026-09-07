"""Sub-module of novel_forge.desktop.jobs.

Auto-generated in the M3.5 split. Contains events.py symbols.
"""

from __future__ import annotations

import logging
from typing import Any

from novel_forge.app_service.contracts import (
    JobEvent,
    JobEventType,
)
from novel_forge.core.infra.event_bus import (
    EventBus,
)

logger = logging.getLogger(__name__)

_event_bus: EventBus | None = None


def _get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def _event_type_value(event: JobEvent) -> str:
    event_type = event.type
    return event_type.value if hasattr(event_type, "value") else str(event_type)


def _stream_event_key(event: JobEvent) -> tuple[str, str] | None:
    if _event_type_value(event) != JobEventType.JOB_STEP.value or event.step != "llm_stream_delta":
        return None
    stream_id = str(event.payload.get("stream_id") or "").strip()
    if not stream_id:
        # Fall back to job_id as the coalescing key so that stream deltas
        # without an explicit stream_id are still merged within a drain batch.
        return (event.job_id, "_default")
    return event.job_id, stream_id


def _merge_stream_delta_events(old: JobEvent, new: JobEvent) -> JobEvent:
    payload = dict(old.payload)
    old_segments = old.payload.get("segments")
    new_segments = new.payload.get("segments")
    payload.update(new.payload)

    if isinstance(old_segments, list) or isinstance(new_segments, list):
        segments: list[Any] = []
        if isinstance(old_segments, list):
            segments.extend(old_segments)
        if isinstance(new_segments, list):
            segments.extend(new_segments)
        payload["segments"] = segments
    else:
        old_delta = str(old.payload.get("delta") or "")
        new_delta = str(new.payload.get("delta") or "")
        if old_delta or new_delta:
            payload["delta"] = old_delta + new_delta

    old_text = str(old.payload.get("text") or "")
    new_text = str(new.payload.get("text") or "")
    if new_text:
        payload["text"] = new_text
    elif old_text:
        payload["text"] = old_text + str(new.payload.get("delta") or "")

    return old.model_copy(
        update={
            "event_id": new.event_id,
            "at": new.at,
            "payload": payload,
        }
    )


def _merge_stream_delta_event_group(events: list[JobEvent]) -> JobEvent:
    """Merge one stream's drain-batch events in linear time.

    Repeatedly calling ``_merge_stream_delta_events`` copies the complete
    accumulated delta/segments on every event, which becomes quadratic for a
    fast stream. Collect parts once and materialize the merged payload once.
    """
    if len(events) == 1:
        return events[0]

    first = events[0]
    last = events[-1]
    payload: dict[str, Any] = {}
    delta_parts: list[str] = []
    merged_segments: list[Any] = []
    has_segments = False
    text_base = ""
    text_suffix_parts: list[str] = []

    for event in events:
        event_payload = event.payload
        payload.update(event_payload)
        delta = str(event_payload.get("delta") or "")
        segments = event_payload.get("segments")
        if isinstance(segments, list):
            has_segments = True
            merged_segments.extend(segments)
        elif delta:
            delta_parts.append(delta)

        explicit_text = str(event_payload.get("text") or "")
        if explicit_text:
            text_base = explicit_text
            text_suffix_parts.clear()
        elif text_base and delta:
            text_suffix_parts.append(delta)

    if has_segments:
        payload["segments"] = merged_segments
    elif delta_parts:
        payload["delta"] = "".join(delta_parts)
    if text_base:
        payload["text"] = text_base + "".join(text_suffix_parts)

    return first.model_copy(
        update={
            "event_id": last.event_id,
            "at": last.at,
            "payload": payload,
        }
    )


def _coalesce_stream_delta_events(events: list[JobEvent]) -> list[JobEvent]:
    if not events:
        return []

    result: list[JobEvent] = []
    pending: dict[tuple[str, str], list[JobEvent]] = {}
    pending_order: list[tuple[str, str]] = []

    def flush_pending() -> None:
        if not pending_order:
            return
        for key in pending_order:
            grouped_events = pending.get(key)
            if grouped_events:
                result.append(_merge_stream_delta_event_group(grouped_events))
        pending.clear()
        pending_order.clear()

    for event in events:
        key = _stream_event_key(event)
        if key is None:
            flush_pending()
            result.append(event)
            continue
        grouped_events = pending.get(key)
        if grouped_events is None:
            pending[key] = [event]
            pending_order.append(key)
        else:
            grouped_events.append(event)

    flush_pending()
    return result
