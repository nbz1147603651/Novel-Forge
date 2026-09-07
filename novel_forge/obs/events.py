"""Unified pipeline event types (Pi-inspired Observer pattern).

This module defines the typed event vocabulary for the entire novel_forge
pipeline. All events carry a consistent envelope (timestamp, chapter_number,
payload) and are compatible with the existing ``on_step(event_name, payload)``
callback pattern.

Design constraints:
- Payload format is fully backward-compatible with existing on_step calls.
- Events are immutable dataclasses (frozen=True) for safe cross-thread sharing.
- The ``event_type`` string matches the existing on_step event names so the
  EventBus bridge requires zero changes at call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Event Type Constants ──────────────────────────────────────────────────────
# Grouped by pipeline phase for discoverability.

# Lifecycle
STEP_START = "step_start"
STEP_END = "step_end"
PHASE_TRANSITION = "phase_transition"

# LLM interaction
LLM_REQUEST_START = "llm_request_start"
LLM_REQUEST_END = "llm_request_end"
TOKEN_ESCALATION = "token_escalation"
RETRY_TRANSIENT_ERROR = "retry_transient_error"

# Repair loop
REPAIR_ROUND_START = "repair_round_start"
REPAIR_ROUND_END = "repair_round_end"
REPAIR_ROLLBACK = "repair_rollback"

# Quality gates
QUALITY_SCORE = "quality_score"
QUALITY_GATE_REGRESSION = "quality_gate_regression_detected"
CROSS_DIMENSION_REGRESSION = "cross_dimension_regression"
ARCHIVE_HARD_BLOCK = "archive_hard_quality_block"

# Progress & errors
PROGRESS_UPDATE = "progress_update"
ERROR = "error"
STEERING_INJECTED = "steering_injected"

# TTS
TTS_SYNTHESIS_START = "tts_synthesis_start"
TTS_SYNTHESIS_END = "tts_synthesis_end"
TTS_BATCH_PROGRESS = "tts_batch_progress"

# ── All known event types (for filtering/validation) ──────────────────────────
ALL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        STEP_START,
        STEP_END,
        PHASE_TRANSITION,
        LLM_REQUEST_START,
        LLM_REQUEST_END,
        TOKEN_ESCALATION,
        RETRY_TRANSIENT_ERROR,
        REPAIR_ROUND_START,
        REPAIR_ROUND_END,
        REPAIR_ROLLBACK,
        QUALITY_SCORE,
        QUALITY_GATE_REGRESSION,
        CROSS_DIMENSION_REGRESSION,
        ARCHIVE_HARD_BLOCK,
        PROGRESS_UPDATE,
        ERROR,
        STEERING_INJECTED,
        TTS_SYNTHESIS_START,
        TTS_SYNTHESIS_END,
        TTS_BATCH_PROGRESS,
    }
)


# ── Event Envelope ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineEvent:
    """Immutable event envelope for the unified pipeline event stream.

    Attributes:
        event_type: One of the event type constants above (matches on_step name).
        payload: Arbitrary dict — format identical to existing on_step payloads.
        timestamp: UTC timestamp of event creation.
        chapter_number: Chapter context (0 for non-chapter events).
        project_id: Project identifier for per-project filtering.
        source: Originating module/step name (e.g. "continuity_repair").
    """

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    chapter_number: int = 0
    project_id: str = ""
    source: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (for events.jsonl persistence)."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "chapter_number": self.chapter_number,
            "project_id": self.project_id,
            "source": self.source,
            **self.payload,
        }


# ── Listener Type ─────────────────────────────────────────────────────────────

PipelineEventListener = Any  # Callable[[PipelineEvent], None]
