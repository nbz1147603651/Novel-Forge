from __future__ import annotations

import json

import pytest

from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairCandidate,
    RepairCase,
    RepairPatchRecord,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.persistence.repair_case_store import (
    RepairCaseConflictError,
    RepairCaseStore,
    repair_content_hash,
)


def _issue() -> AuditIssueV2:
    locator = {
        "target_format": "json_artifact",
        "role": "repair",
        "artifact": "plan",
        "json_pointer": "/duration",
        "field_path": "duration",
        "stable_node_id": "plan:duration",
        "container_hash": repair_content_hash({"duration": "三日"}),
        "comparator_id": "story_duration_v1",
        "expected_raw": "[3,5]日",
        "actual_raw": "[3, 5] 日",
        "expected_normalized": [3, 5],
        "actual_normalized": [3, 5],
    }
    return AuditIssueV2.model_validate(
        {
            "issue_id": "duration-equivalent",
            "dimension": "alignment",
            "issue_type": "duration_conflict",
            "severity": "high",
            "blocking": True,
            "summary": "期限比较异常",
            "description": "原始值不同，但规范化后等价。",
            "evidence": [{"quote": "[3,5]日", "source": "plan", "locator": locator}],
            "repair_targets": [locator],
            "reference_targets": [],
            "repair_intent": {
                "operation": "json_patch",
                "target_policy": "exact_field",
                "rationale": "保留比较器证据。",
            },
        }
    )


def _target(issue: AuditIssueV2) -> ResolvedRepairTarget:
    return ResolvedRepairTarget(
        target_id="target-duration",
        target_format="json_artifact",
        surface="plan",
        locator=issue.repair_targets[0],
        path="/duration",
        current_value="三日",
        current_hash=repair_content_hash("三日"),
        issue_ids=[issue.issue_id],
        allowed_operation="json_patch",
    )


def test_repair_case_events_rebuild_projection_and_keep_comparator_evidence(tmp_path) -> None:
    store = RepairCaseStore(tmp_path)
    issue = _issue()
    case = store.create_case(
        RepairCase(
            case_id="case-1",
            project_id="project-1",
            content_type="init_plan",
            source="init_schema",
            artifact_id="plan",
            source_hash=repair_content_hash({"duration": "三日"}),
            authority="automatic_derived",
            issues=[issue],
        )
    )
    case = store.append_event(
        case.case_id,
        "targets_resolved",
        {"targets": [_target(issue).model_dump(mode="json")]},
        expected_version=case.version,
    )
    candidate_payload = {"duration": "五日"}
    candidate = RepairCandidate(
        case_id=case.case_id,
        version=1,
        base_hash=case.source_hash,
        candidate_hash=repair_content_hash(candidate_payload),
        blob_hash=store.put_blob(candidate_payload),
        origin="deterministic",
        patch_count=1,
        change_ratio=0.1,
        patches=[
            RepairPatchRecord(
                target_id="target-duration",
                expected_hash=repair_content_hash("三日"),
                replacement_hash=repair_content_hash("五日"),
                field_path="duration",
            )
        ],
    )
    case = store.append_event(
        case.case_id,
        "candidate_built",
        {"candidate": candidate.model_dump(mode="json")},
        expected_version=case.version,
    )
    assert case.status == "candidate_ready"
    case = store.append_event(
        case.case_id,
        "verification_requested",
        {},
        expected_version=case.version,
    )
    assert case.status == "needs_verification"
    awaiting_verification = case
    verification = RepairVerificationBundle(
        case_id=case.case_id,
        candidate_version=1,
        candidate_hash=candidate.candidate_hash,
        passed=True,
        resolved_issue_ids=[issue.issue_id],
        validators=[RepairValidatorResult(validator_id="init_schema", passed=True)],
    )
    case = store.append_event(
        case.case_id,
        "verification_completed",
        {"verification": verification.model_dump(mode="json")},
        expected_version=case.version,
    )

    projection_path = tmp_path / ".authoring" / "repair_cases" / "cases" / "case-1.json"
    projection_path.write_text(
        json.dumps(awaiting_verification.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    rebuilt = store.load_case("case-1")
    assert rebuilt == case
    projection_path.unlink()
    rebuilt = store.load_case("case-1")

    assert rebuilt == case
    assert rebuilt is not None
    locator = rebuilt.issues[0].repair_targets[0]
    assert locator.expected_raw == "[3,5]日"
    assert locator.actual_raw == "[3, 5] 日"
    assert locator.expected_normalized == locator.actual_normalized == [3, 5]
    assert locator.comparator_id == "story_duration_v1"
    assert [event.seq for event in store.events(case_id="case-1")] == [1, 2, 3, 4, 5]


def test_repair_case_store_rejects_stale_case_version_and_tampered_blob(tmp_path) -> None:
    store = RepairCaseStore(tmp_path)
    issue = _issue()
    case = store.create_case(
        RepairCase(
            case_id="case-cas",
            project_id="project-1",
            content_type="init_plan",
            source="init_schema",
            artifact_id="plan",
            source_hash=repair_content_hash({"duration": "三日"}),
            authority="automatic_derived",
            issues=[issue],
        )
    )
    case = store.append_event(
        case.case_id,
        "targets_resolved",
        {"targets": [_target(issue).model_dump(mode="json")]},
        expected_version=case.version,
    )
    with pytest.raises(RepairCaseConflictError, match="version changed"):
        store.append_event(
            case.case_id,
            "case_failed",
            {"reason": "stale"},
            expected_version=1,
        )

    digest = store.put_blob({"duration": "五日"})
    blob_path = tmp_path / ".authoring" / "repair_cases" / "blobs" / f"{digest}.json"
    blob_path.write_text(json.dumps({"kind": "repair_candidate", "value": "tampered"}))
    with pytest.raises(ValueError, match="digest mismatch"):
        store.get_blob(digest)
