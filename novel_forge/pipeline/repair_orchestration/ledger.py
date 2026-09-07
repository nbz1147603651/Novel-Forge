"""Repair Orchestration v2 ledger persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.pipeline.repair_orchestration.models import RepairMission, RepairOutcome

REPAIR_V2_LEDGER_FILENAME = "repair_v2_ledger.jsonl"


def repair_ledger_record(mission: RepairMission, outcome: RepairOutcome) -> dict[str, Any]:
    """Return a JSON-serializable v2 ledger record."""

    return {
        "trace_id": mission.trace_id,
        "project_id": mission.project_id,
        "mode": mission.control_mode.value,
        "status": outcome.status,
        "applied": outcome.applied,
        "targets_resolved": list(outcome.targets_resolved),
        "targets_remaining": list(outcome.targets_remaining),
        "needs_human_review": outcome.needs_human_review,
        "attempts": [attempt.model_dump(mode="json") for attempt in outcome.attempts],
        "artifacts_changed": [change.model_dump(mode="json") for change in outcome.artifacts_changed],
        "warnings": list(outcome.warnings),
    }


def append_repair_ledger_record(
    ledger_path: Path,
    mission: RepairMission,
    outcome: RepairOutcome,
) -> None:
    """Append one repair v2 record to a JSONL ledger."""

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    record = repair_ledger_record(mission, outcome)
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def repair_ledger_path_for_layout(layout: Any) -> Path | None:
    """Return the v2 ledger path for a project layout-like object."""

    states_dir = getattr(layout, "states_dir", None)
    if states_dir is None:
        return None
    return Path(states_dir) / REPAIR_V2_LEDGER_FILENAME


def append_repair_ledger_record_for_layout(
    layout: Any,
    mission: RepairMission,
    outcome: RepairOutcome,
) -> None:
    """Append a v2 ledger record under a project layout, if possible."""

    ledger_path = repair_ledger_path_for_layout(layout)
    if ledger_path is None:
        return
    append_repair_ledger_record(ledger_path, mission, outcome)
