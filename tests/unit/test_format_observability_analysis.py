"""Tests for format observability log aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_format_observability import analyze_format_observability


def _write_event(handle, event: str, data: dict[str, object]) -> None:
    handle.write(json.dumps({"event": event, "data": data}, ensure_ascii=False) + "\n")


def test_analyze_format_observability_aggregates_format_and_mode_metrics(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "logs" / "20260705-test"
    run_dir.mkdir(parents=True)
    events_path = run_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as handle:
        _write_event(
            handle,
            "api_call_done",
            {
                "task": "plan_chapter_contracts",
                "structured_output_mode": "json_schema",
                "structured_output_downgraded_from": "json_schema_strict",
            },
        )
        _write_event(
            handle,
            "step",
            {
                "step": "format_retry",
                "data": {
                    "task": "plan_chapter_contracts",
                    "schema_issues": [
                        {"path": "$.chapter_contracts", "issue_type": "missing_key"}
                    ],
                },
            },
        )
        _write_event(
            handle,
            "step",
            {
                "step": "format_validation_success",
                "data": {
                    "task": "plan_chapter_contracts",
                    "parse_source": "strict_json",
                },
            },
        )
        _write_event(
            handle,
            "step",
            {
                "step": "format_repaired",
                "data": {
                    "task": "plan_outline",
                    "repair_source": "local_repair",
                    "repair_risk": "safe",
                    "schema_issues": [
                        {"path": "$.ending_strategy", "issue_type": "type_mismatch"}
                    ],
                },
            },
        )
        _write_event(
            handle,
            "step",
            {
                "step": "format_retry_exhausted",
                "data": {"task": "plan_outline"},
            },
        )

    summary = analyze_format_observability(tmp_path)

    assert summary.runs_scanned == 1
    assert summary.json_model_calls == 1
    assert summary.format_validation_success_count == 1
    assert summary.strict_parse_success_count == 1
    assert summary.strict_parse_success_rate == 1.0
    assert summary.format_retry_count == 1
    assert summary.local_safe_repair_count == 1
    assert summary.final_failure_count == 1
    assert summary.structured_mode_counts == {"json_schema": 1}
    assert summary.structured_downgrade_count == 1
    assert summary.schema_issue_counts == {"missing_key": 1, "type_mismatch": 1}
