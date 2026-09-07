"""Structured explanations for automatic repair boundaries."""

from __future__ import annotations

from novel_forge.app_service.contracts import JobRecord, JobState, JobStepEvent
from novel_forge.app_service.task_flow_error_log import entries_from_job_record


def test_known_format_alias_is_reported_as_locally_resolved() -> None:
    record = JobRecord(
        kind="init_long",
        label="长篇立项",
        project_id="sleep-spell",
        status=JobState.SUCCEEDED,
        events=[
            JobStepEvent(
                step="format_repair_local_fallback",
                payload={"task": "init_story_bible", "message": "alias migrated"},
            )
        ],
    )

    entry = entries_from_job_record(record)[0]

    assert entry["cause_code"] == "known_format_alias"
    assert entry["auto_repair_state"] == "resolved"
    assert entry["auto_resolved"] is True


def test_unknown_format_fields_explain_why_retry_stops() -> None:
    record = JobRecord(
        kind="init_long",
        label="长篇立项",
        project_id="sleep-spell",
        status=JobState.FAILED,
        current_step="format_retry_exhausted",
        error_summary={"summary": "ValidationError: extra_forbidden after retry"},
        events=[
            JobStepEvent(
                step="format_retry_exhausted",
                payload={
                    "task": "init_story_bible",
                    "error": "ValidationError: extra_forbidden after retry",
                },
            )
        ],
    )

    entry = entries_from_job_record(record)[0]

    assert entry["cause_code"] == "format_retry_exhausted"
    assert entry["auto_repair_state"] == "exhausted"
    assert "不能被通用删除" in entry["auto_repair_explanation"]
