"""Tests for cross-round issue ledger identity."""

from __future__ import annotations

from novel_forge.core.utils.issue_ledger import diff_issues


def test_diff_issues_prefers_stable_issue_id_over_rephrased_summary() -> None:
    before = [
        {
            "issue_id": "ch003-cont-stable",
            "issue_type": "opening_gap",
            "severity": "high",
            "summary": "开头没有回扣上一章动作交接。",
            "location": "第1段",
        }
    ]
    after = [
        {
            "issue_id": "ch003-cont-stable",
            "issue_type": "opening_gap",
            "severity": "high",
            "summary": "开场仍缺少上章交接锚点。",
            "location": "第1段",
        }
    ]

    diff = diff_issues(before, after)

    assert len(diff.unresolved) == 1
    assert diff.unresolved[0].issue_id == "ch003-cont-stable"
    assert diff.resolved == []
    assert diff.new_issues == []
