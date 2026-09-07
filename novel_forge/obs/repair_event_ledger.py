"""Append-only event ledger for repair-round telemetry.

Dual-storage architecture:

1. **Fact ledger** (``logs/<run_id>/repair_events.jsonl``):
   append-only JSONL.  Each event carries a unique ``event_id`` for
   idempotency.  Crash-safe: no read-modify-write cycle.

2. **Aggregate snapshot** (``reports/chapter_NNN_repair_metrics.json``):
   schema v3 JSON rebuilt from the ledger at well-defined points
   (round complete, attempt complete).  Uses ``project_lock`` for
   concurrent-write safety.

``Trace`` remains the authoritative cost source; the ledger only stores
``model_call_ids`` — it never re-sums token/cost figures.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# ── Cost source ──────────────────────────────────────────────────────────────


class CostSource(str, Enum):
    """How a cost figure was obtained."""

    REPORTED = "reported"   # model returned real token/cost
    ESTIMATED = "estimated" # provider/default upper-bound estimate
    UNKNOWN = "unknown"     # no pricing info; conservative reserve deducted


# ── Event types ──────────────────────────────────────────────────────────────

EVENT_MODEL_CALL = "model_call"
EVENT_REPAIR_ROUND_COMPLETE = "repair_round_complete"
EVENT_REPORT_REFRESH = "report_refresh"
EVENT_ATTEMPT_COMPLETE = "attempt_complete"

_VALID_EVENT_TYPES = frozenset(
    {
        EVENT_MODEL_CALL,
        EVENT_REPAIR_ROUND_COMPLETE,
        EVENT_REPORT_REFRESH,
        EVENT_ATTEMPT_COMPLETE,
    }
)

# ── Attempt status ───────────────────────────────────────────────────────────


class AttemptStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REPLANNED = "replanned"
    BUDGET_BLOCKED = "budget_blocked"


# ── Event dataclass ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RepairEvent:
    """One immutable event in the repair telemetry ledger."""

    event_id: str
    type: str
    timestamp: float
    attempt_id: str
    chapter_number: int
    # Type-specific payload (flat dict).
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_json_line(cls, line: str) -> RepairEvent:
        data = json.loads(line)
        return cls(
            event_id=data["event_id"],
            type=data["type"],
            timestamp=data["timestamp"],
            attempt_id=data["attempt_id"],
            chapter_number=data["chapter_number"],
            payload=data.get("payload", {}),
        )


# ── Factory helpers ──────────────────────────────────────────────────────────


def make_model_call_event(
    *,
    attempt_id: str,
    chapter_number: int,
    timestamp: float,
    model_call_id: str = "",
    lane: str = "",
    repair_round: int = -1,
    task_type: str = "",
    provider: str = "",
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    cost_source: str = CostSource.REPORTED.value,
    latency_ms: float = 0.0,
    success: bool = True,
) -> RepairEvent:
    return RepairEvent(
        event_id=str(uuid.uuid4()),
        type=EVENT_MODEL_CALL,
        timestamp=timestamp,
        attempt_id=attempt_id,
        chapter_number=chapter_number,
        payload={
            "model_call_id": model_call_id or str(uuid.uuid4()),
            "lane": lane,
            "repair_round": repair_round,
            "task_type": task_type,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd,
            "cost_source": cost_source,
            "latency_ms": latency_ms,
            "success": success,
        },
    )


def make_repair_round_event(
    *,
    attempt_id: str,
    chapter_number: int,
    timestamp: float,
    lane: str,
    round_index: int,
    ticket_ids: list[str] | None = None,
    issue_ids: list[str] | None = None,
    before_text_hash: str = "",
    after_text_hash: str = "",
    mutation_id: str = "",
    score_before: float = 0.0,
    score_after: float = 0.0,
    issues_before: int = 0,
    issues_after: int = 0,
    resolved_issue_ids: list[str] | None = None,
    introduced_issue_ids: list[str] | None = None,
    applied: bool = False,
    rolled_back: bool = False,
) -> RepairEvent:
    return RepairEvent(
        event_id=str(uuid.uuid4()),
        type=EVENT_REPAIR_ROUND_COMPLETE,
        timestamp=timestamp,
        attempt_id=attempt_id,
        chapter_number=chapter_number,
        payload={
            "lane": lane,
            "round": round_index,
            "ticket_ids": ticket_ids or [],
            "issue_ids": issue_ids or [],
            "before_text_hash": before_text_hash,
            "after_text_hash": after_text_hash,
            "mutation_id": mutation_id,
            "score_before": score_before,
            "score_after": score_after,
            "issues_before": issues_before,
            "issues_after": issues_after,
            "resolved_issue_ids": resolved_issue_ids or [],
            "introduced_issue_ids": introduced_issue_ids or [],
            "applied": applied,
            "rolled_back": rolled_back,
        },
    )


def make_report_refresh_event(
    *,
    attempt_id: str,
    chapter_number: int,
    timestamp: float,
    trigger_mutation_id: str = "",
    dimensions_requested: list[str] | None = None,
    dimensions_refreshed: list[str] | None = None,
    dimensions_reused: list[str] | None = None,
    cost_usd: float = 0.0,
    cost_source: str = CostSource.REPORTED.value,
) -> RepairEvent:
    return RepairEvent(
        event_id=str(uuid.uuid4()),
        type=EVENT_REPORT_REFRESH,
        timestamp=timestamp,
        attempt_id=attempt_id,
        chapter_number=chapter_number,
        payload={
            "trigger_mutation_id": trigger_mutation_id,
            "dimensions_requested": dimensions_requested or [],
            "dimensions_refreshed": dimensions_refreshed or [],
            "dimensions_reused": dimensions_reused or [],
            "cost_usd": cost_usd,
            "cost_source": cost_source,
        },
    )


def make_attempt_complete_event(
    *,
    attempt_id: str,
    chapter_number: int,
    timestamp: float,
    replan_attempt: int,
    status: str | AttemptStatus,
    violation_kind: str = "",
) -> RepairEvent:
    status_value = status.value if isinstance(status, AttemptStatus) else str(status)
    return RepairEvent(
        event_id=str(uuid.uuid4()),
        type=EVENT_ATTEMPT_COMPLETE,
        timestamp=timestamp,
        attempt_id=attempt_id,
        chapter_number=chapter_number,
        payload={
            "replan_attempt": replan_attempt,
            "status": status_value,
            "violation_kind": violation_kind,
        },
    )


# ── Ledger ───────────────────────────────────────────────────────────────────


class RepairEventLedger:
    """Append-only JSONL ledger with idempotent event_id deduplication.

    Usage::

        ledger = RepairEventLedger(logs_dir / "repair_events.jsonl")
        ledger.append(event)
        aggregate = ledger.rebuild_aggregate(chapter_number=12)
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: RepairEvent) -> bool:
        """Append *event* to the JSONL file.

        Returns ``True`` if appended, ``False`` if the ``event_id`` already
        exists (idempotent skip).
        """
        if self._event_id_exists(event.event_id):
            _logger.debug(
                "repair_event_idempotent_skip | event_id=%s | type=%s",
                event.event_id,
                event.type,
            )
            return False

        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = event.to_json_line() + "\n"
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return True

    def _event_id_exists(self, event_id: str) -> bool:
        if not self._path.exists():
            return False
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        data = json.loads(stripped)
                        if data.get("event_id") == event_id:
                            return True
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            return False
        return False

    def read_events(self) -> list[RepairEvent]:
        """Read all events from the ledger.  Tolerates partial writes."""
        if not self._path.exists():
            return []
        events: list[RepairEvent] = []
        seen_ids: set[str] = set()
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        event = RepairEvent.from_json_line(stripped)
                        if event.event_id in seen_ids:
                            continue
                        seen_ids.add(event.event_id)
                        events.append(event)
                    except (json.JSONDecodeError, KeyError):
                        _logger.debug("repair_event_malformed_line_skipped")
                        continue
        except OSError as exc:
            _logger.warning("repair_event_read_failed | path=%s | error=%s", self._path, exc)
        return events

    def rebuild_aggregate(
        self,
        *,
        chapter_run_id: str,
        chapter_number: int,
    ) -> dict[str, Any]:
        """Rebuild the schema v3 aggregate JSON from the event ledger.

        Returns a dict ready for ``storage.save_json()``.
        """
        events = self.read_events()
        chapter_events = [
            e for e in events if e.chapter_number == chapter_number
        ]

        attempts: dict[str, dict[str, Any]] = {}
        final_status = "unknown"

        for event in chapter_events:
            aid = event.attempt_id
            if aid not in attempts:
                attempts[aid] = {
                    "attempt_id": aid,
                    "replan_attempt": 0,
                    "status": "running",
                    "repair_rounds": [],
                    "report_refreshes": [],
                }
            attempt = attempts[aid]

            if event.type == EVENT_REPAIR_ROUND_COMPLETE:
                attempt["repair_rounds"].append(event.payload)
            elif event.type == EVENT_REPORT_REFRESH:
                attempt["report_refreshes"].append(event.payload)
            elif event.type == EVENT_ATTEMPT_COMPLETE:
                attempt["status"] = event.payload.get("status", "unknown")
                attempt["replan_attempt"] = event.payload.get("replan_attempt", 0)
                final_status = event.payload.get("status", final_status)

        ordered = sorted(attempts.values(), key=lambda a: a["replan_attempt"])

        return {
            "schema_version": 3,
            "chapter_run_id": chapter_run_id,
            "chapter": chapter_number,
            "final_status": final_status,
            "attempts": ordered,
        }
