"""Tests for shared audit issue lifecycle helpers."""

from __future__ import annotations

from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.reading_power_repair import _build_reading_power_issues
from novel_forge.core.utils.audit_issue import ensure_issue_id, is_open_issue, normalize_audit_issue


def test_reading_power_issues_have_shared_audit_metadata() -> None:
    report = ReadingPowerReport(
        chapter=3,
        hook_type="none",
        hook_strength="weak",
        prev_hook_fulfilled=False,
    )

    issues = _build_reading_power_issues(report, min_payoffs=1)

    assert issues
    assert all(issue.issue_id.startswith("ch003-reading_power-") for issue in issues)
    assert all(issue.repair_surface == "chapter_text" for issue in issues)
    assert all(issue.status == "open" for issue in issues)
    assert all(issue.postconditions for issue in issues)
    assert {issue.issue_type for issue in issues} >= {
        "hook_missing",
        "hook_too_weak",
        "prev_hook_unfulfilled",
    }


def test_ensure_issue_id_prefers_existing_id_and_open_status_helper() -> None:
    issue = {"issue_id": "manual-stable", "severity": "high", "status": "suppressed"}

    assert ensure_issue_id(issue, "cont", chapter_number=3) == "manual-stable"
    assert not is_open_issue(issue)


def test_dimension_profile_adds_continuity_specific_postconditions() -> None:
    issue = normalize_audit_issue(
        {"issue_type": "opening_gap", "summary": "开场没有承接上章"},
        dimension="continuity",
        chapter_number=3,
    )

    assert issue.issue_id.startswith("ch003-cont-")
    assert issue.postconditions[0].validator_id == "opening_transition_validator"
    assert issue.metadata["audit_profile"]["repair_lane"] == "continuity_repair"
    assert "桥接" in issue.metadata["audit_profile"]["reviewer_focus"]


def test_dimension_profile_preserves_causal_specialization() -> None:
    issue = normalize_audit_issue(
        {"issue_type": "event_without_cause", "summary": "角色突然获得关键线索"},
        dimension="causal",
        chapter_number=4,
    )

    assert issue.issue_id.startswith("ch004-causal-")
    assert issue.postconditions[0].validator_id == "event_cause_validator"
    assert "触发" in issue.metadata["audit_profile"]["repair_focus"]


def test_dimension_profile_routes_guard_unverified_to_manual() -> None:
    issue = normalize_audit_issue(
        {
            "issue_type": "guard_constraint_unverified",
            "summary": "证据不足，无法确认护栏是否违规",
        },
        dimension="guard",
        chapter_number=5,
    )

    assert issue.repair_surface == "manual"
    assert issue.postconditions[0].validator_id == "guard_manual_review_validator"


def test_dimension_profile_routes_state_to_state_packet() -> None:
    issue = normalize_audit_issue(
        {"issue_type": "state_archive_block", "summary": "状态裁判阻断归档"},
        dimension="state_adjudication",
        chapter_number=6,
    )

    assert issue.repair_surface == "state_packet"
    assert issue.postconditions[0].validator_id == "state_archive_block_validator"


def test_dimension_profile_adds_alignment_acceptance_conditions() -> None:
    issue = normalize_audit_issue(
        {"issue_type": "outline_main_point_missing", "summary": "缺少主线推进"},
        dimension="alignment",
        chapter_number=7,
    )

    assert issue.issue_id.startswith("ch007-alignment-")
    assert issue.postconditions[0].validator_id == "alignment_main_point_validator"
    assert issue.metadata["audit_profile"]["repair_lane"] == "alignment_repair"
