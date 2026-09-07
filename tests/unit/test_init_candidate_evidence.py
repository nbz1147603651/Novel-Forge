"""Candidate-first evidence and isolation tests for initialization repair."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from novel_forge.pipeline.long.services.init_repair import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairOrchestrator,
    InitRepairReport,
)
from novel_forge.pipeline.long.services.init_repair.policies import StoryBibleRepairPolicy
from novel_forge.pipeline.repair_orchestration.domains.initialization import (
    build_initialization_audit_issues,
    normalize_init_comparison_value,
)


def _ctx() -> InitRepairContext:
    return InitRepairContext(
        service_ctx=None,
        outline_ctx={},
        total_chapters=1,
    )


class _BaselinePolicy:
    artifact = InitArtifact.STORY_BIBLE

    def __init__(self, *, fallback_state: str = "good") -> None:
        self.fallback_state = fallback_state
        self.llm_input: dict[str, Any] | None = None
        self.fallback_input: dict[str, Any] | None = None

    def normalize(self, payload: Any, ctx: InitRepairContext) -> dict[str, Any]:
        del ctx
        return deepcopy(payload)

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        del ctx
        if payload.get("state") == "good":
            return InitRepairReport()
        issue = InitRepairIssue(
            kind=InitRepairIssueKind.SCHEMA_SHAPE,
            message="state must be good",
            field="state",
            metadata={
                "json_pointer": "/state",
                "expected_raw": "good",
                "actual_raw": payload.get("state"),
                "comparator_id": "exact_state_v1",
            },
        )
        return InitRepairReport(errors=[issue.message], issues=[issue])

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> dict[str, Any]:
        del report, ctx
        self.llm_input = deepcopy(payload)
        payload["state"] = "llm-invalid"
        payload["llm_only"] = True
        return payload

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> dict[str, Any]:
        del report, ctx
        self.fallback_input = deepcopy(payload)
        payload["state"] = self.fallback_state
        payload["fallback_only"] = True
        return payload


def test_init_candidates_restart_from_immutable_baseline_and_original_validator() -> None:
    source = {"state": "bad", "author_note": "keep"}
    policy = _BaselinePolicy()

    outcome = asyncio.run(InitRepairOrchestrator(policy).repair(source, _ctx()))

    assert source == {"state": "bad", "author_note": "keep"}
    assert policy.llm_input == source
    assert policy.fallback_input == source
    assert outcome.payload == {
        "state": "good",
        "author_note": "keep",
        "fallback_only": True,
    }
    assert [candidate.version for candidate in outcome.candidates] == [1, 2]
    assert [candidate.origin for candidate in outcome.candidates] == ["model", "deterministic"]
    assert [bundle.passed for bundle in outcome.verifications] == [False, True]
    assert outcome.latest_candidate is not None
    assert outcome.latest_verification is not None
    assert outcome.latest_candidate.candidate_hash == outcome.latest_verification.candidate_hash
    locator = outcome.audit_issues[0].repair_targets[0]
    assert locator.json_pointer == "/state"
    assert locator.actual_raw == "bad"
    assert locator.expected_raw == "good"
    assert locator.comparator_id == "exact_state_v1"


def test_init_all_failed_candidates_return_original_payload() -> None:
    source = {"state": "bad", "author_note": "keep"}
    policy = _BaselinePolicy(fallback_state="still-invalid")

    outcome = asyncio.run(InitRepairOrchestrator(policy).repair(source, _ctx()))

    assert outcome.report.is_valid is False
    assert outcome.payload == source
    assert len(outcome.candidates) == 2
    assert all(bundle.passed is False for bundle in outcome.verifications)


def test_init_duration_comparator_keeps_raw_and_normalized_values() -> None:
    raw = "[3, 5] 日"
    expected = "[3,5]日"
    issue = build_initialization_audit_issues(
        artifact="blueprint",
        payload={"probation": raw},
        source_version="source-v1",
        issues=[
            {
                "id": "duration-equivalence",
                "kind": "semantic_conflict",
                "message": "停职期限比较",
                "field": "probation",
                "metadata": {
                    "json_pointer": "/probation",
                    "expected_raw": expected,
                    "comparator_id": "init_duration_range_v1",
                },
            }
        ],
    )[0]

    locator = issue.repair_targets[0]
    assert locator.actual_raw == raw
    assert locator.expected_raw == expected
    assert locator.actual_normalized == expected
    assert locator.expected_normalized == expected
    assert locator.comparator_id == "init_duration_range_v1"
    assert locator.source_version == "source-v1"


def test_pydantic_schema_error_projects_precise_json_pointer() -> None:
    payload = {"premise": []}
    report = StoryBibleRepairPolicy().validate(payload, _ctx())

    assert report.is_valid is False
    issues = build_initialization_audit_issues(
        artifact="story_bible",
        payload=payload,
        issues=report.issues,
    )

    assert issues
    locator = issues[0].repair_targets[0]
    assert locator.target_format == "json_artifact"
    assert locator.json_pointer == "/premise"
    assert locator.actual_raw == []


def test_normalize_init_comparison_value_is_evidence_only() -> None:
    source = {"deadline": "【3， 5】 天", "nested": ["  保留   空格  "]}
    snapshot = deepcopy(source)

    normalized = normalize_init_comparison_value(source)

    assert normalized == {"deadline": "[3,5]天", "nested": ["保留 空格"]}
    assert source == snapshot
