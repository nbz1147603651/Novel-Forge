"""Tests for RepairEventLedger: append, idempotency, rebuild."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from novel_forge.obs.repair_event_ledger import (
    AttemptStatus,
    CostSource,
    RepairEventLedger,
    make_attempt_complete_event,
    make_model_call_event,
    make_repair_round_event,
    make_report_refresh_event,
)


@pytest.fixture()
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "repair_events.jsonl"


@pytest.fixture()
def ledger(ledger_path: Path) -> RepairEventLedger:
    return RepairEventLedger(ledger_path)


def _ts() -> float:
    return time.time()


class TestRepairEventAppend:
    def test_append_creates_file(self, ledger: RepairEventLedger, ledger_path: Path):
        event = make_model_call_event(
            attempt_id="att-1",
            chapter_number=5,
            timestamp=_ts(),
            task_type="REPAIR_CONTINUITY",
        )
        result = ledger.append(event)
        assert result is True
        assert ledger_path.exists()

    def test_idempotent_skip(self, ledger: RepairEventLedger):
        event = make_model_call_event(
            attempt_id="att-1",
            chapter_number=5,
            timestamp=_ts(),
        )
        assert ledger.append(event) is True
        # Same event_id → idempotent skip.
        assert ledger.append(event) is False

    def test_different_events_all_appended(self, ledger: RepairEventLedger):
        for _ in range(3):
            event = make_model_call_event(
                attempt_id="att-1",
                chapter_number=5,
                timestamp=_ts(),
            )
            assert ledger.append(event) is True
        events = ledger.read_events()
        assert len(events) == 3


class TestRepairEventRead:
    def test_read_empty(self, ledger: RepairEventLedger):
        assert ledger.read_events() == []

    def test_roundtrip_all_event_types(self, ledger: RepairEventLedger):
        ts = _ts()
        events = [
            make_model_call_event(
                attempt_id="att-1", chapter_number=5, timestamp=ts,
                lane="continuity", repair_round=0,
            ),
            make_repair_round_event(
                attempt_id="att-1", chapter_number=5, timestamp=ts,
                lane="continuity", round_index=0,
                score_before=7.0, score_after=8.5,
                applied=True,
            ),
            make_report_refresh_event(
                attempt_id="att-1", chapter_number=5, timestamp=ts,
                dimensions_requested=["continuity", "alignment"],
                dimensions_refreshed=["continuity"],
                dimensions_reused=["alignment"],
            ),
            make_attempt_complete_event(
                attempt_id="att-1", chapter_number=5, timestamp=ts,
                replan_attempt=0, status=AttemptStatus.SUCCESS,
            ),
        ]
        for e in events:
            ledger.append(e)
        read = ledger.read_events()
        assert len(read) == 4
        assert {e.type for e in read} == {
            "model_call", "repair_round_complete",
            "report_refresh", "attempt_complete",
        }

    def test_malformed_line_tolerated(self, ledger: RepairEventLedger, ledger_path: Path):
        event = make_model_call_event(
            attempt_id="att-1", chapter_number=5, timestamp=_ts(),
        )
        ledger.append(event)
        # Append a malformed line.
        with ledger_path.open("a") as fh:
            fh.write("NOT JSON\n")
        # Read should still return the valid event.
        events = ledger.read_events()
        assert len(events) == 1


class TestRepairEventRebuildAggregate:
    def test_rebuild_empty(self, ledger: RepairEventLedger):
        result = ledger.rebuild_aggregate(
            chapter_run_id="cr-1", chapter_number=5,
        )
        assert result["schema_version"] == 3
        assert result["chapter"] == 5
        assert result["attempts"] == []

    def test_rebuild_multi_attempt(self, ledger: RepairEventLedger):
        ts = _ts()
        # Attempt 0: replanned.
        ledger.append(make_repair_round_event(
            attempt_id="att-0", chapter_number=12, timestamp=ts,
            lane="continuity", round_index=0,
            score_before=6.0, score_after=7.5, applied=True,
        ))
        ledger.append(make_attempt_complete_event(
            attempt_id="att-0", chapter_number=12, timestamp=ts,
            replan_attempt=0, status=AttemptStatus.REPLANNED,
            violation_kind="consistency",
        ))
        # Attempt 1: success.
        ledger.append(make_repair_round_event(
            attempt_id="att-1", chapter_number=12, timestamp=ts,
            lane="causal", round_index=0,
            score_before=8.0, score_after=9.5, applied=True,
        ))
        ledger.append(make_attempt_complete_event(
            attempt_id="att-1", chapter_number=12, timestamp=ts,
            replan_attempt=1, status=AttemptStatus.SUCCESS,
        ))

        result = ledger.rebuild_aggregate(
            chapter_run_id="cr-12", chapter_number=12,
        )
        assert result["final_status"] == "success"
        assert len(result["attempts"]) == 2
        assert result["attempts"][0]["replan_attempt"] == 0
        assert result["attempts"][0]["status"] == "replanned"
        assert result["attempts"][1]["replan_attempt"] == 1
        assert result["attempts"][1]["status"] == "success"
        assert len(result["attempts"][0]["repair_rounds"]) == 1
        assert len(result["attempts"][1]["repair_rounds"]) == 1

    def test_rebuild_filters_by_chapter(self, ledger: RepairEventLedger):
        ts = _ts()
        ledger.append(make_model_call_event(
            attempt_id="att-a", chapter_number=5, timestamp=ts,
        ))
        ledger.append(make_model_call_event(
            attempt_id="att-b", chapter_number=10, timestamp=ts,
        ))
        result_5 = ledger.rebuild_aggregate(
            chapter_run_id="cr-5", chapter_number=5,
        )
        result_10 = ledger.rebuild_aggregate(
            chapter_run_id="cr-10", chapter_number=10,
        )
        # Each chapter should have its own attempt.
        assert len(result_5["attempts"]) == 1
        assert result_5["attempts"][0]["attempt_id"] == "att-a"
        assert len(result_10["attempts"]) == 1
        assert result_10["attempts"][0]["attempt_id"] == "att-b"


class TestCostSourceEnum:
    def test_values(self):
        assert CostSource.REPORTED.value == "reported"
        assert CostSource.ESTIMATED.value == "estimated"
        assert CostSource.UNKNOWN.value == "unknown"


class TestAttemptStatusEnum:
    def test_values(self):
        assert AttemptStatus.SUCCESS.value == "success"
        assert AttemptStatus.BUDGET_BLOCKED.value == "budget_blocked"
