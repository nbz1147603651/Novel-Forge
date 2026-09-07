"""Real repair shadow calls stay sampled, budgeted and evidence-only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from novel_forge.persistence.repair_shadow import (
    RepairShadowLedger,
    RepairShadowPolicy,
    _logical_shadow_id,
    _sample_value,
)


def _find_sampled_cases(
    project_id: str = "book", *, prefix: str = "case", count: int = 1
) -> list[str]:
    result: list[str] = []
    for index in range(5000):
        case_id = f"{prefix}-{index}"
        if _sample_value(_logical_shadow_id(project_id, case_id, 1)) < 0.05:
            result.append(case_id)
            if len(result) == count:
                return result
    raise AssertionError("expected deterministic samples below 5%")


def test_real_shadow_is_disabled_and_zero_budget_fails_closed(tmp_path: Path) -> None:
    ledger = RepairShadowLedger(tmp_path)
    case_id = _find_sampled_cases()[0]

    disabled = ledger.admit(
        project_id="book",
        case_id=case_id,
        candidate_version=1,
        policy=RepairShadowPolicy(),
    )
    zero_budget = ledger.admit(
        project_id="book",
        case_id=case_id,
        candidate_version=1,
        policy=RepairShadowPolicy(enabled=True, budget_usd_30d=0.0),
    )

    assert disabled.reason == "disabled"
    assert zero_budget.reason == "budget_zero"
    assert ledger.events() == []


def test_shadow_admission_is_one_attempt_and_settlement_is_idempotent(tmp_path: Path) -> None:
    ledger = RepairShadowLedger(tmp_path)
    policy = RepairShadowPolicy(enabled=True, budget_usd_30d=1.0)
    case_id = _find_sampled_cases()[0]
    now = datetime(2026, 9, 1, tzinfo=UTC)

    first = ledger.admit(
        project_id="book",
        case_id=case_id,
        candidate_version=1,
        policy=policy,
        now=now,
    )
    second = ledger.admit(
        project_id="book",
        case_id=case_id,
        candidate_version=1,
        policy=policy,
        now=now,
    )
    ledger.settle(first.logical_id, actual_usd=0.08, status="completed", now=now)
    ledger.settle(first.logical_id, actual_usd=0.08, status="completed", now=now)

    assert first.admitted is True
    assert first.attempts == 1
    assert second.reason == "attempt_already_used"
    assert [event["event"] for event in ledger.events()] == ["admitted", "settled"]
    assert ledger.events()[-1]["published"] is False
    assert ledger.events()[-1]["case_state_changed"] is False


def test_shadow_rolling_cap_and_separate_budget_block_extra_calls(tmp_path: Path) -> None:
    ledger = RepairShadowLedger(tmp_path)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    project_id = "book"
    first_case, second_case = _find_sampled_cases(project_id, count=2)
    cap_policy = RepairShadowPolicy(
        enabled=True,
        max_per_project_30d=1,
        budget_usd_30d=1.0,
    )
    assert ledger.admit(
        project_id=project_id,
        case_id=first_case,
        candidate_version=1,
        policy=cap_policy,
        now=now,
    ).admitted
    capped = ledger.admit(
        project_id=project_id,
        case_id=second_case,
        candidate_version=1,
        policy=cap_policy,
        now=now,
    )
    assert capped.reason == "project_30d_cap"

    later = now + timedelta(days=31)
    budget_policy = RepairShadowPolicy(
        enabled=True,
        max_per_project_30d=10,
        budget_usd_30d=0.05,
        estimated_call_usd=0.10,
    )
    budgeted = ledger.admit(
        project_id=project_id,
        case_id=second_case,
        candidate_version=1,
        policy=budget_policy,
        now=later,
    )
    assert budgeted.reason == "budget_30d_exhausted"


def test_semantic_shadow_judgment_and_review_are_exact_and_evidence_only(
    tmp_path: Path,
) -> None:
    ledger = RepairShadowLedger(tmp_path)
    case_id = _find_sampled_cases(prefix="semantic")[0]
    admission = ledger.admit(
        project_id="book",
        case_id=case_id,
        candidate_version=1,
        policy=RepairShadowPolicy(enabled=True, budget_usd_30d=1.0),
    )
    assert admission.admitted

    ledger.record_semantic_judgment(
        admission.logical_id,
        source_hash="source-v1",
        verdict="ambiguous",
        structured_success=True,
        routed_to_human=True,
        model_call_id="call-v1",
        report_id="report-v1",
    )
    ledger.record_semantic_judgment(
        admission.logical_id,
        source_hash="source-v1",
        verdict="ambiguous",
        structured_success=True,
        routed_to_human=True,
        model_call_id="call-v1",
    )
    with pytest.raises(ValueError, match="source changed"):
        ledger.review_semantic_judgment(
            admission.logical_id,
            source_hash="source-v2",
            expected="ambiguous",
            critical=False,
        )
    ledger.review_semantic_judgment(
        admission.logical_id,
        source_hash="source-v1",
        expected="ambiguous",
        critical=False,
    )

    evaluations = ledger.semantic_evaluations()
    assert len(evaluations) == 1
    assert evaluations[0].origin == "real_shadow"
    assert evaluations[0].routed_to_human is True
    assert evaluations[0].official_write_count == 0
    assert evaluations[0].stale_approval_publish_count == 0
    assert [event["event"] for event in ledger.events()] == [
        "admitted",
        "semantic_judgment",
        "semantic_review",
    ]


def test_main_gate_parallel_evidence_reuses_same_ledger_without_admission(
    tmp_path: Path,
) -> None:
    ledger = RepairShadowLedger(tmp_path)
    ledger.record_semantic_main_gate_judgment(
        "main-1",
        source_hash="source-main-1",
        verdict="needs_repair",
        structured_success=True,
        routed_to_human=False,
        model_call_id="main-call-1",
        legacy_fallback_verdict="conflict",
    )
    ledger.review_semantic_judgment(
        "main-1",
        source_hash="source-main-1",
        expected="conflict",
        critical=True,
    )

    evaluation = ledger.semantic_evaluations()[0]
    assert evaluation.origin == "main_gate"
    assert evaluation.legacy_fallback_verdict == "conflict"
    assert ledger.events()[0]["shadow_only"] is False
    assert ledger.events()[0]["published"] is False
