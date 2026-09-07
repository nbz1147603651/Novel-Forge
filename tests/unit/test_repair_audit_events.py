"""Tests for unified repair audit event payloads."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.artifacts import ArtifactIssue
from novel_forge.pipeline.repair_orchestration.audit_events import (
    artifact_issue_audit_payload,
    book_issue_audit_payload,
    issue_audit_payload,
    repair_target_audit_payload,
    state_issue_audit_payload,
    text_hash,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairDomain,
    RepairMission,
    RepairSurface,
    RepairTarget,
)


def test_issue_audit_payload_tracks_issue_id_and_paragraph_window() -> None:
    payload = issue_audit_payload(
        SimpleNamespace(
            issue_id="issue-body-1",
            issue_type="causal_gap",
            severity="critical",
            summary="动机缺口",
            paragraph_start=4,
            paragraph_end=5,
        ),
        event_type="selected",
        dimension="causal",
        chapter=3,
        source_text_hash=text_hash("原文"),
        target_text_hash=text_hash("修复后"),
    )

    assert payload["issue_id"] == "issue-body-1"
    assert payload["dimension"] == "causal"
    assert payload["chapter"] == 3
    assert payload["paragraph_start"] == 4
    assert payload["paragraph_end"] == 5
    assert payload["identity_mode"] == "issue_id"
    assert payload["location_mode"] == "paragraph_window"
    assert "原文" not in payload.values()


def test_artifact_issue_payload_uses_json_path_location() -> None:
    issue = ArtifactIssue(
        code="missing_payoff",
        message="缺少章节契约回收字段",
        severity="high",
        source="chapter_contracts",
        path="$.chapters[3].payoffs",
    )

    payload = artifact_issue_audit_payload(
        issue,
        artifact_path="plans/chapter_contracts.json",
        surface="chapter_contracts",
        status="attempted",
    )

    assert payload["issue_id"] == "missing_payoff"
    assert payload["artifact_path"] == "plans/chapter_contracts.json"
    assert payload["json_path"] == "$.chapters[3].payoffs"
    assert payload["identity_mode"] == "artifact_path"
    assert payload["location_mode"] == "artifact_json_path"


def test_book_issue_payload_preserves_queue_target_and_chapter() -> None:
    payload = book_issue_audit_payload(
        {
            "issue_id": "book-ticket-9",
            "issue_type": "cross_chapter_conflict",
            "severity": "high",
            "chapter_number": 12,
            "paragraph_index": 8,
        },
        queue_target="chapter:12:continuity",
        status="selected",
    )

    assert payload["issue_id"] == "book-ticket-9"
    assert payload["chapter"] == 12
    assert payload["queue_target"] == "chapter:12:continuity"
    assert payload["paragraph_start"] == 8
    assert payload["identity_mode"] == "ticket_id"
    assert payload["location_mode"] == "book_queue"


def test_state_issue_payload_uses_candidate_and_delta_targets() -> None:
    payload = state_issue_audit_payload(
        {
            "candidate_id": "cand-7",
            "delta_id": "delta-7",
            "repair_kind": "mapping",
            "severity": "critical",
        },
        chapter=6,
        surface="chapter_contracts",
        status="selected",
    )

    assert payload["issue_id"] == "delta-7"
    assert payload["candidate_id"] == "cand-7"
    assert payload["delta_id"] == "delta-7"
    assert payload["repair_surface"] == "chapter_contracts"
    assert payload["identity_mode"] == "state_target"
    assert payload["location_mode"] == "state_target"


def test_repair_target_payload_uses_protocol_and_runtime_location_modes() -> None:
    mission = RepairMission(project_id="p")

    format_payload = repair_target_audit_payload(
        mission,
        RepairTarget(
            domain=RepairDomain.FORMAT_RESPONSE,
            surface=RepairSurface.RESPONSE_JSON,
            issue_ref="format:SAMPLE",
            payload={"task_type": "SAMPLE", "json_path": "$.field"},
        ),
        event_type="selected",
    )
    assert format_payload["identity_mode"] == "protocol_task"
    assert format_payload["location_mode"] == "protocol_response"
    assert format_payload["json_path"] == "$.field"

    runtime_payload = repair_target_audit_payload(
        mission,
        RepairTarget(
            domain=RepairDomain.RUNTIME_CONTRACT,
            surface=RepairSurface.CHAPTER_CONTRACTS,
            issue_ref="ticket-1",
            chapter_number=4,
            payload={"artifact_path": "plans/chapter_contracts.json", "json_path": "$.chapters[4]"},
        ),
        event_type="selected",
    )
    assert runtime_payload["identity_mode"] == "ticket_id"
    assert runtime_payload["location_mode"] == "artifact_json_path"
    assert runtime_payload["artifact_path"] == "plans/chapter_contracts.json"
