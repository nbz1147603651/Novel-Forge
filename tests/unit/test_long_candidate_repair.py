"""Long-form candidate-first verification and evidence tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.pipeline.repair_orchestration.domains.long_chapter import (
    record_long_candidate_cases,
    verify_long_working_candidate,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairExecutionResult,
    RepairVerificationResult,
)


def _execution() -> RepairExecutionResult:
    return RepairExecutionResult(
        applied=True,
        change_ratio=0.1,
        payload={"repair_exhausted": False, "needs_human_review": False},
    )


def test_changed_long_candidate_requires_original_recheck() -> None:
    result = SimpleNamespace(
        current_text="修复后",
        verification_evidence={
            "recheck_performed": False,
            "gate_passed": False,
            "candidate_text_hash": repair_content_hash("修复后"),
            "residual_issue_ids": ["issue-1"],
            "regression_issue_ids": [],
        },
    )

    verification = verify_long_working_candidate(
        dimension="continuity",
        loop_result=result,
        execution=_execution(),
    )

    assert verification.verified is False
    assert "original_recheck_missing" in verification.reason
    assert "original_issue_not_closed" in verification.reason


def test_changed_long_candidate_passes_only_when_hash_and_gate_match() -> None:
    result = SimpleNamespace(
        current_text="修复后",
        verification_evidence={
            "recheck_performed": True,
            "gate_passed": True,
            "candidate_text_hash": repair_content_hash("修复后"),
            "residual_issue_ids": [],
            "regression_issue_ids": [],
        },
    )

    verification = verify_long_working_candidate(
        dimension="causal",
        loop_result=result,
        execution=_execution(),
    )

    assert verification.verified is True
    assert verification.reason == "causal_original_recheck_passed"


def test_no_change_does_not_close_an_unverified_original_issue() -> None:
    execution = RepairExecutionResult(
        applied=False,
        change_ratio=0.0,
        payload={"repair_exhausted": False, "needs_human_review": False},
    )
    result = SimpleNamespace(
        current_text="原文",
        verification_evidence={
            "original_issue_ids": ["issue-still-open"],
            "recheck_performed": False,
            "gate_passed": False,
        },
    )

    verification = verify_long_working_candidate(
        dimension="reading_power",
        loop_result=result,
        execution=execution,
    )

    assert verification.verified is False
    assert verification.residual_issues == [{"issue_id": "issue-still-open"}]


def test_long_candidate_case_is_non_published_and_exactly_located(tmp_path: Any) -> None:
    baseline = "第一段。\n线索在第二段断裂。\n第三段。"
    candidate = "第一段。\n线索在第二段得到承接。\n第三段。"
    issue_id = "continuity-issue-1"
    loop_result = SimpleNamespace(
        current_text=candidate,
        rounds_used=1,
        verification_evidence={
            "validator_id": "continuity_original_recheck_v1",
            "initial_issues": [
                {
                    "_repair_issue_id": issue_id,
                    "issue_type": "broken_clue",
                    "severity": "high",
                    "summary": "线索没有承接",
                    "evidence_quote": "线索在第二段断裂。",
                }
            ],
            "recheck_performed": True,
            "gate_passed": True,
            "candidate_text_hash": repair_content_hash(candidate),
            "score": 9.0,
            "score_threshold": 8.0,
            "repair_verdict": "complete",
            "regression_issue_ids": [],
        },
    )
    verification = RepairVerificationResult(
        verified=True,
        confidence=0.95,
        reason="continuity_original_recheck_passed",
    )
    layout = SimpleNamespace(root=tmp_path, project_id="story")

    case_ids = record_long_candidate_cases(
        bundle=SimpleNamespace(project_id="story", layout=layout),
        runner=SimpleNamespace(),
        dimension="continuity",
        chapter_number=2,
        baseline_text=baseline,
        loop_result=loop_result,
        verification=verification,
    )

    assert len(case_ids) == 1
    case = RepairCaseStore(tmp_path).load_case(case_ids[0])
    assert case is not None
    assert case.status == "verified"
    assert case.authority == "automatic_working_candidate"
    assert case.receipt is None
    assert case.latest_candidate is not None
    assert case.latest_candidate.protected_items == [
        "explicit author intent",
        "locked content",
        "ending",
        "character fate",
        "world rules",
        "archived prose",
    ]
    locator = case.issues[0].repair_targets[0]
    assert locator.char_start == baseline.index("线索在第二段断裂。")
    assert locator.char_end == locator.char_start + len("线索在第二段断裂。")
    assert case.verification is not None
    assert case.verification.candidate_hash == repair_content_hash(candidate)


def test_ambiguous_long_issue_is_downgraded_to_manual(tmp_path: Any) -> None:
    baseline = "重复句。\n重复句。"
    candidate = "重复句。\n已修复。"
    loop_result = SimpleNamespace(
        current_text=candidate,
        rounds_used=1,
        verification_evidence={
            "initial_issues": [
                {
                    "_repair_issue_id": "reading-ambiguous",
                    "issue_type": "repetition",
                    "severity": "medium",
                    "summary": "重复位置不唯一",
                    "evidence_quote": "重复句。",
                }
            ],
            "recheck_performed": True,
            "gate_passed": True,
            "candidate_text_hash": repair_content_hash(candidate),
            "regression_issue_ids": [],
        },
    )

    case_ids = record_long_candidate_cases(
        bundle=SimpleNamespace(
            project_id="story",
            layout=SimpleNamespace(root=tmp_path, project_id="story"),
        ),
        runner=SimpleNamespace(),
        dimension="reading_power",
        chapter_number=1,
        baseline_text=baseline,
        loop_result=loop_result,
        verification=RepairVerificationResult(verified=True),
    )

    case = RepairCaseStore(tmp_path).load_case(case_ids[0])
    assert case is not None
    assert case.status == "manual_required"
    assert case.candidates == []
    assert case.targets[0].resolution_status == "manual_required"


@pytest.mark.asyncio
async def test_reading_power_candidate_hook_does_not_write_intermediate_draft() -> None:
    from novel_forge.pipeline.long.stages.reading_power_repair import (
        ReadingPowerRepairRunner,
    )

    runner = object.__new__(ReadingPowerRepairRunner)
    candidate = await runner.on_repair_success(SimpleNamespace(), "未验证候选")

    assert candidate == "未验证候选"
