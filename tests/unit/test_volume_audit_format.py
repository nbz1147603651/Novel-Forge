"""Tests for VolumeAuditReport consistency_issues format unification and cross-volume aggregation."""

from __future__ import annotations

from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.pipeline.steps.book_consistency_step import ConsistencyIssue
from novel_forge.pipeline.steps.volume_step import VolumeAuditStep, aggregate_volume_issues


class TestFormatUnification:
    def test_dict_issue_normalized(self) -> None:
        raw = {
            "issue_id": "V1-001",
            "category": "timeline",
            "severity": "critical",
            "chapters_involved": [1, 3],
            "description": "Time jump inconsistency",
            "suggestion": "Fix chapter 3 timeline",
            "confidence": 0.9,
        }
        result = VolumeAuditStep._normalize_consistency_issues([raw])
        assert len(result) == 1
        assert result[0]["category"] == "timeline"
        assert result[0]["description"] == "Time jump inconsistency"

    def test_string_issue_normalized_to_dict(self) -> None:
        result = VolumeAuditStep._normalize_consistency_issues(["Character age changed"])
        assert len(result) == 1
        assert result[0]["category"] == "narrative_drift"
        assert result[0]["severity"] == "warning"
        assert result[0]["description"] == "Character age changed"

    def test_empty_string_skipped(self) -> None:
        result = VolumeAuditStep._normalize_consistency_issues(["", "  ", None])
        assert len(result) == 0

    def test_to_consistency_issue_full_dict(self) -> None:
        issue_dict = {
            "issue_id": "V1-002",
            "category": "worldbuilding",
            "severity": "warning",
            "chapters_involved": [2, 4, 6],
            "description": "Magic system rule violated",
            "suggestion": "Rewrite chapter 4 scene",
            "issue_type": "rule_violation",
            "primary_chapter": 4,
            "location": "Chapter 4, paragraph 3",
            "paragraph_index": 3,
            "paragraph_span": [3, 5],
            "evidence": "Rule states X but chapter shows Y",
            "fix_mode": "repair_continuity",
            "fix_action": "rewrite",
            "confidence": 0.85,
            "verification_status": "suspected",
            "linked_issue_refs": [{"ref_id": "V1-001"}],
        }
        issue = VolumeAuditStep._to_consistency_issue(issue_dict)
        assert isinstance(issue, ConsistencyIssue)
        assert issue.issue_id == "V1-002"
        assert issue.category == "worldbuilding"
        assert issue.severity == "warning"
        assert issue.chapters_involved == [2, 4, 6]
        assert issue.primary_chapter == 4
        assert issue.paragraph_index == 3
        assert issue.paragraph_span == [3, 5]
        assert issue.confidence == 0.85
        assert issue.verification_status == "suspected"
        assert len(issue.linked_issue_refs) == 1

    def test_to_consistency_issue_minimal_dict(self) -> None:
        issue_dict = {
            "category": "naming",
            "severity": "info",
            "chapters_involved": [1],
            "description": "Name variant detected",
        }
        issue = VolumeAuditStep._to_consistency_issue(issue_dict)
        assert isinstance(issue, ConsistencyIssue)
        assert issue.issue_id == ""
        assert issue.category == "naming"
        assert issue.severity == "info"
        assert issue.chapters_involved == [1]
        assert issue.primary_chapter == 1
        assert issue.confidence == 0.0

    def test_volume_audit_report_accepts_dict_issues(self) -> None:
        report = VolumeAuditReport(
            volume_number=1,
            volume_title="Test",
            chapter_range="1-10",
            volume_summary="Summary",
            consistency_score=7.5,
            consistency_issues=[
                {
                    "category": "timeline",
                    "severity": "warning",
                    "chapters_involved": [1, 2],
                    "description": "Timeline gap",
                }
            ],
        )
        assert len(report.consistency_issues) == 1
        assert isinstance(report.consistency_issues[0], dict)
        assert report.consistency_issues[0]["category"] == "timeline"


class TestCrossVolumeAggregation:
    def test_aggregate_single_volume(self) -> None:
        report = VolumeAuditReport(
            volume_number=1,
            volume_title="Vol 1",
            chapter_range="1-10",
            volume_summary="Summary",
            consistency_score=8.0,
            consistency_issues=[
                {
                    "category": "timeline",
                    "severity": "critical",
                    "chapters_involved": [1, 3],
                    "description": "Timeline error",
                }
            ],
        )
        issues = aggregate_volume_issues([report])
        assert len(issues) == 1
        assert isinstance(issues[0], ConsistencyIssue)
        assert issues[0].category == "timeline"
        assert issues[0].severity == "critical"

    def test_aggregate_multiple_volumes(self) -> None:
        reports = [
            VolumeAuditReport(
                volume_number=1,
                volume_title="Vol 1",
                chapter_range="1-10",
                volume_summary="Summary 1",
                consistency_score=8.0,
                consistency_issues=[
                    {
                        "category": "timeline",
                        "severity": "warning",
                        "chapters_involved": [1, 3],
                        "description": "Vol 1 timeline issue",
                    }
                ],
            ),
            VolumeAuditReport(
                volume_number=2,
                volume_title="Vol 2",
                chapter_range="11-20",
                volume_summary="Summary 2",
                consistency_score=7.5,
                consistency_issues=[
                    {
                        "category": "character_state",
                        "severity": "critical",
                        "chapters_involved": [12, 15],
                        "description": "Vol 2 character state issue",
                    },
                    {
                        "category": "worldbuilding",
                        "severity": "info",
                        "chapters_involved": [18],
                        "description": "Vol 2 worldbuilding note",
                    },
                ],
            ),
        ]
        issues = aggregate_volume_issues(reports)
        assert len(issues) == 3
        assert all(isinstance(i, ConsistencyIssue) for i in issues)
        categories = {i.category for i in issues}
        assert categories == {"timeline", "character_state", "worldbuilding"}

    def test_aggregate_empty_volumes(self) -> None:
        reports = [
            VolumeAuditReport(
                volume_number=1,
                volume_title="Vol 1",
                chapter_range="1-10",
                volume_summary="Summary",
                consistency_score=9.0,
                consistency_issues=[],
            ),
        ]
        issues = aggregate_volume_issues(reports)
        assert len(issues) == 0

    def test_aggregate_preserves_all_fields(self) -> None:
        report = VolumeAuditReport(
            volume_number=1,
            volume_title="Vol 1",
            chapter_range="1-10",
            volume_summary="Summary",
            consistency_score=8.0,
            consistency_issues=[
                {
                    "issue_id": "V1-001",
                    "category": "naming",
                    "severity": "warning",
                    "chapters_involved": [1, 2, 3],
                    "description": "Name inconsistency",
                    "suggestion": "Standardize name",
                    "issue_type": "variant",
                    "primary_chapter": 1,
                    "location": "Chapter 1",
                    "paragraph_index": 5,
                    "paragraph_span": [5, 7],
                    "evidence": "Name X vs Name Y",
                    "fix_mode": "repair_continuity",
                    "fix_action": "rewrite",
                    "confidence": 0.75,
                    "verification_status": "confirmed",
                    "linked_issue_refs": [{"ref": "prior"}],
                }
            ],
        )
        issues = aggregate_volume_issues([report])
        assert len(issues) == 1
        issue = issues[0]
        assert issue.issue_id == "V1-001"
        assert issue.suggestion == "Standardize name"
        assert issue.issue_type == "variant"
        assert issue.location == "Chapter 1"
        assert issue.paragraph_index == 5
        assert issue.paragraph_span == [5, 7]
        assert issue.evidence == "Name X vs Name Y"
        assert issue.fix_mode == "repair_continuity"
        assert issue.fix_action == "rewrite"
        assert issue.confidence == 0.75
        assert issue.verification_status == "confirmed"
        assert len(issue.linked_issue_refs) == 1
