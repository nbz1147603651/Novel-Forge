"""Golden tests for the declarative repair-dimension coordination layer.

These tests pin the coordination behavior that used to be hand-coded inline in
``chapter_flow_review._run_quality_and_repairs``: total-cap gating events and
post-run outcome absorption.  Event names and payload keys are telemetry
contracts consumed by Desktop and run logs — they must not drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.pipeline.long.repair_dimensions import (
    CAUSAL_DIMENSION,
    CONTINUITY_DIMENSION,
    READING_POWER_DIMENSION,
    absorb_dimension_result,
    emit_dimension_cap_skip,
    gate_dimension_rounds,
    remaining_rounds,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeState:
    """Duck-typed stand-in for _ReviewPhaseState (coordination fields only)."""

    current_text: str = "原始正文"
    baseline_text: str = "原始正文"
    text_change_history: list[dict[str, Any]] = field(default_factory=list)
    review_warnings: list[str] = field(default_factory=list)
    repair_exhausted: bool = False
    total_repair_rounds_used: int = 0
    cumulative_change_ratio: float = 0.0


@dataclass
class _FakeResult:
    current_text: str
    rounds_used: int = 1
    repair_exhausted: bool = False
    best_effort_accepted: bool = False
    best_effort_reason: str = ""


def _capture_events() -> tuple[list[tuple[str, dict[str, Any]]], Any]:
    events: list[tuple[str, dict[str, Any]]] = []

    def on_step(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    return events, on_step


# ---------------------------------------------------------------------------
# Policy declarations (data-level invariants)
# ---------------------------------------------------------------------------


class TestDimensionPolicies:
    def test_chain_policies_match_legacy_inline_behavior(self) -> None:
        # Continuity runs first: propagates exhaustion downstream, and is NOT
        # compressed to remaining budget (legacy behavior — skip only).
        assert CONTINUITY_DIMENSION.name == "continuity"
        assert CONTINUITY_DIMENSION.propagate_exhausted is True
        assert CONTINUITY_DIMENSION.compress_to_remaining is False
        # Causal compresses to remaining rounds and does NOT propagate
        # exhaustion (its rollback path sets repair_exhausted explicitly).
        assert CAUSAL_DIMENSION.name == "causal"
        assert CAUSAL_DIMENSION.compress_to_remaining is True
        assert CAUSAL_DIMENSION.propagate_exhausted is False
        assert READING_POWER_DIMENSION.name == "reading_power"

    def test_text_stage_labels_match_legacy_history_entries(self) -> None:
        assert CONTINUITY_DIMENSION.text_stage == "continuity_repair"
        assert CAUSAL_DIMENSION.text_stage == "causal_repair"
        assert READING_POWER_DIMENSION.text_stage == "reading_power_repair"


# ---------------------------------------------------------------------------
# gate_dimension_rounds
# ---------------------------------------------------------------------------


class TestGateDimensionRounds:
    def test_cap_reached_zeroes_rounds_and_emits_skip_event(self) -> None:
        events, on_step = _capture_events()
        rounds = gate_dimension_rounds(
            policy=CONTINUITY_DIMENSION,
            max_rounds=1,
            total_rounds_used=5,
            total_rounds_cap=5,
            chapter_number=3,
            on_step=on_step,
        )
        assert rounds == 0
        assert events == [
            (
                "repair_dimension_skipped_total_cap",
                {
                    "chapter": 3,
                    "total_rounds_used": 5,
                    "cap": 5,
                    "skipped_dimension": "continuity",
                },
            )
        ]

    def test_continuity_is_not_compressed_below_cap(self) -> None:
        events, on_step = _capture_events()
        rounds = gate_dimension_rounds(
            policy=CONTINUITY_DIMENSION,
            max_rounds=1,
            total_rounds_used=1,
            total_rounds_cap=5,
            chapter_number=1,
            on_step=on_step,
        )
        assert rounds == 1
        assert events == []

    def test_causal_compresses_to_remaining_and_emits_capped_event(self) -> None:
        events, on_step = _capture_events()
        rounds = gate_dimension_rounds(
            policy=CAUSAL_DIMENSION,
            max_rounds=2,
            total_rounds_used=4,
            total_rounds_cap=5,
            chapter_number=2,
            on_step=on_step,
        )
        assert rounds == 1
        assert events == [
            (
                "repair_dimension_rounds_capped",
                {
                    "chapter": 2,
                    "dimension": "causal",
                    "total_rounds_used": 4,
                    "cap": 5,
                    "max_rounds": 1,
                },
            )
        ]

    def test_causal_within_remaining_is_untouched(self) -> None:
        events, on_step = _capture_events()
        rounds = gate_dimension_rounds(
            policy=CAUSAL_DIMENSION,
            max_rounds=1,
            total_rounds_used=1,
            total_rounds_cap=5,
            chapter_number=2,
            on_step=on_step,
        )
        assert rounds == 1
        assert events == []

    def test_disabled_cap_never_gates(self) -> None:
        events, on_step = _capture_events()
        rounds = gate_dimension_rounds(
            policy=CAUSAL_DIMENSION,
            max_rounds=2,
            total_rounds_used=99,
            total_rounds_cap=0,
            chapter_number=1,
            on_step=on_step,
        )
        assert rounds == 2
        assert events == []


# ---------------------------------------------------------------------------
# remaining_rounds / emit_dimension_cap_skip
# ---------------------------------------------------------------------------


class TestRemainingAndSkipEvent:
    def test_remaining_rounds(self) -> None:
        assert remaining_rounds(2, 5) == 3
        assert remaining_rounds(5, 5) == 0
        assert remaining_rounds(7, 5) == 0
        assert remaining_rounds(2, 0) is None

    def test_emit_dimension_cap_skip_payload(self) -> None:
        events, on_step = _capture_events()
        emit_dimension_cap_skip(
            policy=READING_POWER_DIMENSION,
            total_rounds_used=5,
            total_rounds_cap=5,
            chapter_number=4,
            on_step=on_step,
        )
        assert events == [
            (
                "repair_dimension_skipped_total_cap",
                {
                    "chapter": 4,
                    "total_rounds_used": 5,
                    "cap": 5,
                    "skipped_dimension": "reading_power",
                },
            )
        ]


# ---------------------------------------------------------------------------
# absorb_dimension_result
# ---------------------------------------------------------------------------


class TestAbsorbDimensionResult:
    def test_text_change_records_provenance_and_accounts_rounds(self) -> None:
        state = _FakeState()
        absorb_dimension_result(
            policy=CONTINUITY_DIMENSION,
            state=state,
            result=_FakeResult(current_text="修复后正文", rounds_used=2),
        )
        assert state.current_text == "修复后正文"
        assert len(state.text_change_history) == 1
        entry = state.text_change_history[0]
        assert entry["stage"] == "continuity_repair"
        assert entry["changed"] is True
        assert entry["applied"] is True
        assert entry["before_chars"] == len("原始正文")
        assert state.total_repair_rounds_used == 2
        assert state.cumulative_change_ratio > 0.0

    def test_unchanged_text_records_nothing(self) -> None:
        state = _FakeState()
        absorb_dimension_result(
            policy=CONTINUITY_DIMENSION,
            state=state,
            result=_FakeResult(current_text="原始正文", rounds_used=0),
        )
        assert state.text_change_history == []
        assert state.cumulative_change_ratio == 0.0

    def test_continuity_propagates_exhaustion(self) -> None:
        state = _FakeState()
        absorb_dimension_result(
            policy=CONTINUITY_DIMENSION,
            state=state,
            result=_FakeResult(current_text="新", repair_exhausted=True),
        )
        assert state.repair_exhausted is True

    def test_causal_does_not_propagate_exhaustion(self) -> None:
        state = _FakeState()
        absorb_dimension_result(
            policy=CAUSAL_DIMENSION,
            state=state,
            result=_FakeResult(current_text="新", repair_exhausted=True),
        )
        assert state.repair_exhausted is False

    def test_best_effort_warning_uses_dimension_label(self) -> None:
        state = _FakeState()
        absorb_dimension_result(
            policy=CAUSAL_DIMENSION,
            state=state,
            result=_FakeResult(
                current_text="新",
                best_effort_accepted=True,
                best_effort_reason="达到硬底线",
            ),
        )
        assert state.review_warnings == ["因果链修复已尽力接受：达到硬底线"]


# ---------------------------------------------------------------------------
# Chain-level golden: gate → run → absorb in legacy order
# ---------------------------------------------------------------------------


class TestChainCoordinationGolden:
    def test_continuity_then_causal_shared_budget_sequence(self) -> None:
        """Simulate the legacy inline sequence: continuity exhausts budget,
        causal is then gated to zero with the exact legacy event stream."""
        events, on_step = _capture_events()
        state = _FakeState()
        cap = 3

        # ── Continuity dimension ──
        cont_rounds = gate_dimension_rounds(
            policy=CONTINUITY_DIMENSION,
            max_rounds=3,
            total_rounds_used=state.total_repair_rounds_used,
            total_rounds_cap=cap,
            chapter_number=1,
            on_step=on_step,
        )
        assert cont_rounds == 3
        absorb_dimension_result(
            policy=CONTINUITY_DIMENSION,
            state=state,
            result=_FakeResult(
                current_text="连贯性修复后",
                rounds_used=3,
                repair_exhausted=True,
                best_effort_accepted=True,
                best_effort_reason="仍低于阈值",
            ),
        )

        # ── Causal dimension: budget exhausted → gated to zero ──
        causal_rounds = gate_dimension_rounds(
            policy=CAUSAL_DIMENSION,
            max_rounds=2,
            total_rounds_used=state.total_repair_rounds_used,
            total_rounds_cap=cap,
            chapter_number=1,
            on_step=on_step,
        )
        assert causal_rounds == 0

        assert [e for e, _ in events] == ["repair_dimension_skipped_total_cap"]
        assert events[0][1]["skipped_dimension"] == "causal"
        assert state.repair_exhausted is True
        assert state.review_warnings == ["连续性修复已尽力接受：仍低于阈值"]
        assert state.total_repair_rounds_used == 3
        assert [h["stage"] for h in state.text_change_history] == ["continuity_repair"]
