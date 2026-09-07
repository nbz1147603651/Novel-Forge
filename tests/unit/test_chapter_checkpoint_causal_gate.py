"""Tests for guard-checkpoint causal repair selection."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.workspace.sessions.chapter_session_handlers import _select_causal_must_fix_issues


def test_checkpoint_causal_gate_uses_must_fix_severity_not_score_threshold() -> None:
    medium_issue = CausalIssue(
        issue_type="opening_causal_gap",
        severity="medium",
        summary="开头缺少上章承接。",
    )
    low_issue = CausalIssue(
        issue_type="question_ignored",
        severity="low",
        summary="悬念回应偏弱。",
    )
    report = CausalValidationReport(causal_score=8.5, issues=[medium_issue, low_issue])

    selected = _select_causal_must_fix_issues(
        report,
        SimpleNamespace(repair_must_fix_severity="medium"),
    )

    assert selected == [medium_issue]


def test_checkpoint_causal_gate_off_falls_back_to_high_priority() -> None:
    medium_issue = CausalIssue(
        issue_type="opening_causal_gap",
        severity="medium",
        summary="开头缺少上章承接。",
    )
    high_issue = CausalIssue(
        issue_type="event_without_cause",
        severity="high",
        summary="事件缺少原因。",
    )
    report = CausalValidationReport(causal_score=9.0, issues=[medium_issue, high_issue])

    selected = _select_causal_must_fix_issues(
        report,
        SimpleNamespace(repair_must_fix_severity="off"),
    )

    assert selected == [high_issue]


def test_checkpoint_causal_gate_missing_setting_defaults_to_critical() -> None:
    critical_issue = CausalIssue(
        issue_type="event_without_cause",
        severity="critical",
        summary="关键事件缺少原因。",
    )
    high_issue = CausalIssue(
        issue_type="motivation_gap",
        severity="high",
        summary="人物动机支撑偏弱。",
    )
    report = CausalValidationReport(causal_score=8.0, issues=[critical_issue, high_issue])

    selected = _select_causal_must_fix_issues(report, SimpleNamespace())

    assert selected == [critical_issue]
