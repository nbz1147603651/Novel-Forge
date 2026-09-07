"""Load historical stream data from project run logs (events.jsonl).

The desktop ``TaskObservationStore`` only keeps the latest state per stream
in memory (and job events are capped at 160 by a sliding window).  For
reliable multi-round stream history (including discarded retries), this
module reads the persistent ``events.jsonl`` written by
:class:`ProjectRunLogger`.

Each line in ``events.jsonl`` is a JSON object with this shape::

    {"ts": "...", "level": "INFO", "event": "step",
     "data": {"step": "llm_stream_delta", "data": {"stream_id": "...", ...}}}

This module extracts all ``llm_stream_*`` events for a given job and
reconstructs :class:`HistoricalStream` objects — one per stream_id,
including discarded retries.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoricalSegment:
    """One segment of a historical stream."""

    kind: str
    text: str


@dataclass(frozen=True)
class HistoricalStream:
    """One complete stream round (possibly a discarded retry)."""

    stream_id: str
    task: str = ""
    attempt: int = 0
    status: str = "complete"
    segments: tuple[HistoricalSegment, ...] = ()
    started_at: str = ""
    ended_at: str = ""
    error: str = ""
    discarded: bool = False
    text: str = ""
    reasoning_text: str = ""


def load_stream_history(run_log_dir: str | Path) -> list[HistoricalStream]:
    """Load all stream rounds from a project run's ``events.jsonl``.

    Returns a list of :class:`HistoricalStream` objects sorted by
    ``started_at``.  Each stream_id produces one entry; discarded retries
    (``llm_stream_restart``) are included with ``discarded=True``.

    If the run log directory or events.jsonl does not exist, returns an
    empty list.
    """
    run_dir = Path(run_log_dir)
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return []

    streams: dict[str, _StreamAccum] = {}
    try:
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                _process_event_entry(entry, streams)
    except OSError as exc:
        _logger.warning("stream_history_read_failed | path=%s | error=%s", events_path, exc)
        return []

    result = [accum.to_historical() for accum in streams.values()]
    result.sort(key=lambda s: s.started_at)
    return result


def _process_event_entry(
    entry: dict[str, Any], streams: dict[str, _StreamAccum]
) -> None:
    """Process one line from events.jsonl."""
    data = entry.get("data")
    if not isinstance(data, dict):
        return
    step = data.get("step", "")
    if not step.startswith("llm_stream_"):
        return
    payload = data.get("data")
    if not isinstance(payload, dict):
        return
    stream_id = str(payload.get("stream_id") or "").strip()
    if not stream_id:
        return
    ts = str(entry.get("ts") or "")
    accum = streams.get(stream_id)
    if accum is None:
        accum = _StreamAccum(stream_id=stream_id, started_at=ts)
        streams[stream_id] = accum
    accum.apply(step, payload, ts)


@dataclass
class _StreamAccum:
    """Mutable accumulator for building a HistoricalStream."""

    stream_id: str
    task: str = ""
    attempt: int = 0
    status: str = "streaming"
    segments: list[HistoricalSegment] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    error: str = ""
    discarded: bool = False

    def apply(self, step: str, payload: dict[str, Any], ts: str) -> None:
        self.task = str(payload.get("task") or self.task or "")
        self.attempt = int(payload.get("attempt") or self.attempt or 0)
        if step == "llm_stream_start":
            self.status = "streaming"
            self.started_at = ts
            self.segments = []
            self.error = ""
            self.discarded = False
        elif step == "llm_stream_delta":
            self.status = "streaming"
            segments_raw = payload.get("segments")
            if isinstance(segments_raw, list):
                for seg in segments_raw:
                    if not isinstance(seg, dict):
                        continue
                    kind = str(seg.get("kind") or "content")
                    text = str(seg.get("text") or "")
                    if kind not in ("content", "reasoning"):
                        kind = "content"
                    self._append(kind, text)
            else:
                delta = str(payload.get("delta") or "")
                if delta:
                    self._append("content", delta)
        elif step == "llm_stream_end":
            self.status = "complete"
            self.ended_at = ts
            self.error = ""
        elif step == "llm_stream_error":
            self.status = "error"
            self.ended_at = ts
            self.error = str(payload.get("error") or payload.get("message") or "")
        elif step == "llm_stream_restart":
            self.status = "restarted"
            reset_output = bool(payload.get("reset_output"))
            self.discarded = not reset_output
            if reset_output:
                self.segments = []
            self.ended_at = ts
            self.error = str(payload.get("error") or payload.get("message") or "")

    def _append(self, kind: str, text: str) -> None:
        if not text:
            return
        if self.segments and self.segments[-1].kind == kind:
            last = self.segments[-1]
            self.segments[-1] = HistoricalSegment(kind=kind, text=last.text + text)
        else:
            self.segments.append(HistoricalSegment(kind=kind, text=text))

    def to_historical(self) -> HistoricalStream:
        text = "".join(s.text for s in self.segments if s.kind == "content")
        reasoning = "".join(s.text for s in self.segments if s.kind == "reasoning")
        return HistoricalStream(
            stream_id=self.stream_id,
            task=self.task,
            attempt=self.attempt,
            status=self.status,
            segments=tuple(self.segments),
            started_at=self.started_at,
            ended_at=self.ended_at,
            error=self.error,
            discarded=self.discarded,
            text=text,
            reasoning_text=reasoning,
        )
