from __future__ import annotations

from novel_forge.core.review.repair_harness import (
    RepairPatch,
    RepairVerification,
    TextRepairHarness,
    locate_text_fragments,
)
from novel_forge.core.schemas.audit import AuditIssueV2
from novel_forge.core.utils.repair_target_resolver import RepairResolverContext


def _duration_issue(payload: dict[str, object]) -> AuditIssueV2:
    repair = locate_text_fragments(
        artifact="chapter_plan",
        payload=payload,
        fragments=["三日停职结束"],
        role="repair",
    )["三日停职结束"]
    reference = locate_text_fragments(
        artifact="chapter_outline",
        payload={"goal": "停职五日受审"},
        fragments=["停职五日"],
        role="reference",
    )["停职五日"]
    return AuditIssueV2.model_validate(
        {
            "issue_id": "duration_1",
            "dimension": "alignment",
            "issue_type": "plan_outline_duration_conflict",
            "severity": "critical",
            "blocking": True,
            "summary": "期限冲突",
            "description": "Plan 的三日停职与大纲五日停职冲突。",
            "evidence": [
                {
                    "quote": repair[0].quote,
                    "source": "chapter_plan",
                    "locator": repair[0],
                    "confidence": 1.0,
                }
            ],
            "repair_targets": repair,
            "reference_targets": reference,
            "repair_intent": {
                "operation": "replace",
                "target_policy": "exact_text_span",
                "rationale": "修复未发布 Plan。",
            },
        }
    )


def test_locate_text_fragments_returns_pointer_and_exact_span() -> None:
    payload = {"chapters": [{"goal": "她必须在三日停职结束前复验甘松"}]}

    locator = locate_text_fragments(
        artifact="outline",
        payload=payload,
        fragments=["三日停职结束"],
        role="repair",
    )["三日停职结束"][0]

    assert locator.json_pointer == "/chapters/0/goal"
    assert locator.char_start == 4
    assert locator.char_end == 10
    assert locator.quote == "三日停职结束"
    assert locator.text_hash


def test_harness_repairs_isolated_candidate_and_verifies_without_mutating_source() -> None:
    plan = {"required_state_transitions": ["三日停职结束前复验甘松"]}
    issue = _duration_issue(plan)
    harness = TextRepairHarness(
        RepairResolverContext(
            artifacts={
                "chapter_plan": plan,
                "chapter_outline": {"goal": "停职五日受审"},
            }
        )
    )

    def patch_provider(attempt, issue, targets, base, previous):  # type: ignore[no-untyped-def]
        del attempt, issue, base, previous
        return [
            RepairPatch(
                target_id=targets[0].target_id,
                replacement="停职受审第三日结束",
                expected_hash=targets[0].current_hash,
            )
        ]

    def verifier(candidate, issue):  # type: ignore[no-untyped-def]
        del issue
        text = candidate.artifacts["chapter_plan"]["required_state_transitions"][0]
        return RepairVerification(
            passed="三日停职结束" not in text and "停职受审第三日结束" in text,
        )

    result = harness.run(
        issue,
        authority="automatic_candidate",
        patch_provider=patch_provider,
        verifier=verifier,
    )

    assert result.status == "verified_candidate"
    assert result.candidate is not None
    assert plan["required_state_transitions"] == ["三日停职结束前复验甘松"]
    assert result.candidate.artifacts["chapter_plan"]["required_state_transitions"] == [
        "停职受审第三日结束前复验甘松"
    ]
    assert result.attempts[0].status == "verified"
    report = result.model_dump()
    assert "created_at" not in str(report)
    assert "schema_version" not in str(report)


def test_harness_never_executes_patch_provider_when_source_needs_proposal() -> None:
    plan = {"required_state_transitions": ["三日停职结束前复验甘松"]}
    issue = _duration_issue(plan)
    harness = TextRepairHarness(
        RepairResolverContext(
            artifacts={
                "chapter_plan": plan,
                "chapter_outline": {"goal": "停职五日受审"},
            }
        )
    )
    called = False

    def patch_provider(attempt, issue, targets, base, previous):  # type: ignore[no-untyped-def]
        nonlocal called
        del attempt, issue, targets, base, previous
        called = True
        return []

    result = harness.run(
        issue,
        authority="proposal_required",
        patch_provider=patch_provider,
        verifier=lambda candidate, issue: RepairVerification(passed=True),
    )

    assert result.status == "proposal_required"
    assert called is False
    assert result.candidate is None


def test_harness_rejects_stale_patch_and_stops_at_bound() -> None:
    plan = {"required_state_transitions": ["三日停职结束前复验甘松"]}
    issue = _duration_issue(plan)
    harness = TextRepairHarness(
        RepairResolverContext(
            artifacts={
                "chapter_plan": plan,
                "chapter_outline": {"goal": "停职五日受审"},
            }
        )
    )

    def patch_provider(attempt, issue, targets, base, previous):  # type: ignore[no-untyped-def]
        del attempt, issue, base, previous
        return [
            RepairPatch(
                target_id=targets[0].target_id,
                replacement="停职受审第三日结束",
                expected_hash="stale",
            )
        ]

    result = harness.run(
        issue,
        authority="automatic_candidate",
        patch_provider=patch_provider,
        verifier=lambda candidate, issue: RepairVerification(passed=True),
        max_attempts=2,
    )

    assert result.status == "verification_failed"
    assert len(result.attempts) == 2
    assert all(attempt.status == "patch_rejected" for attempt in result.attempts)
    assert result.candidate is None


def test_harness_applies_multiple_spans_in_one_field_from_right_to_left() -> None:
    plan = {"goal": "三日停职开始，三日停职结束"}
    locators = locate_text_fragments(
        artifact="chapter_plan",
        payload=plan,
        fragments=["三日停职"],
        role="repair",
    )["三日停职"]
    issue = AuditIssueV2.model_validate(
        {
            "issue_id": "duration_multi",
            "dimension": "alignment",
            "issue_type": "plan_internal_duration_conflict",
            "severity": "critical",
            "blocking": True,
            "summary": "两个期限短语需同步修复",
            "description": "同一字段包含两个需要修复的精确期限短语。",
            "evidence": [
                {
                    "quote": locator.quote,
                    "source": "chapter_plan",
                    "locator": locator,
                    "confidence": 1.0,
                }
                for locator in locators
            ],
            "repair_targets": locators,
            "reference_targets": [],
            "repair_intent": {
                "operation": "replace",
                "target_policy": "all_exact_spans",
                "rationale": "同步修复同字段中的独立跨度。",
            },
        }
    )
    harness = TextRepairHarness(
        RepairResolverContext(artifacts={"chapter_plan": plan})
    )

    def patch_provider(attempt, issue, targets, base, previous):  # type: ignore[no-untyped-def]
        del attempt, issue, base, previous
        return [
            RepairPatch(
                target_id=target.target_id,
                replacement="五日停职",
                expected_hash=target.current_hash,
            )
            for target in targets
        ]

    result = harness.run(
        issue,
        authority="automatic_candidate",
        patch_provider=patch_provider,
        verifier=lambda candidate, issue: RepairVerification(
            passed=candidate.artifacts["chapter_plan"]["goal"]
            == "五日停职开始，五日停职结束"
        ),
    )

    assert result.status == "verified_candidate"
    assert result.candidate is not None
    assert result.candidate.artifacts["chapter_plan"]["goal"] == "五日停职开始，五日停职结束"


def test_harness_rejects_overlapping_span_targets() -> None:
    plan = {"goal": "三日停职结束前复验"}
    locators = [
        *locate_text_fragments(
            artifact="chapter_plan",
            payload=plan,
            fragments=["三日停职"],
            role="repair",
        )["三日停职"],
        *locate_text_fragments(
            artifact="chapter_plan",
            payload=plan,
            fragments=["三日停职结束"],
            role="repair",
        )["三日停职结束"],
    ]
    issue = AuditIssueV2.model_validate(
        {
            "issue_id": "overlap",
            "dimension": "alignment",
            "issue_type": "overlapping_evidence",
            "severity": "critical",
            "blocking": True,
            "summary": "重叠证据",
            "description": "重叠字符跨度不能作为两个独立补丁执行。",
            "evidence": [
                {"quote": item.quote, "source": "chapter_plan", "locator": item}
                for item in locators
            ],
            "repair_targets": locators,
            "reference_targets": [],
            "repair_intent": {
                "operation": "replace",
                "target_policy": "exact_text_span",
                "rationale": "安全测试",
            },
        }
    )
    harness = TextRepairHarness(RepairResolverContext(artifacts={"chapter_plan": plan}))

    def patch_provider(attempt, issue, targets, base, previous):  # type: ignore[no-untyped-def]
        del attempt, issue, base, previous
        return [
            RepairPatch(
                target_id=target.target_id,
                replacement="停职第三日",
                expected_hash=target.current_hash,
            )
            for target in targets
        ]

    result = harness.run(
        issue,
        authority="automatic_candidate",
        patch_provider=patch_provider,
        verifier=lambda candidate, issue: RepairVerification(passed=True),
        max_attempts=1,
    )

    assert result.status == "verification_failed"
    assert result.attempts[0].status == "patch_rejected"
    assert "overlapping repair spans" in result.attempts[0].details[0]
