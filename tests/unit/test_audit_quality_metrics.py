"""Tests for AuditQualityMetrics and its integration with BookConsistencyResult."""

from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyResult,
    _compute_quality_metrics_from_issues,
)
from novel_forge.workspace.audit_quality_metrics import AuditQualityMetrics


class TestAuditQualityMetricsDefaultValues:
    def test_default_values(self):
        metrics = AuditQualityMetrics()
        assert metrics.coverage_ratio == 0.0
        assert metrics.estimated_miss_rate == 0.0
        assert metrics.dimension_scores == {}
        assert metrics.audit_depth == "quick"
        assert metrics.total_issues_found == 0
        assert metrics.critical_issues_found == 0


class TestBookConsistencyResultDump:
    def test_result_dump_includes_quality_metrics(self):
        result = BookConsistencyResult()
        dump = result.model_dump()
        assert "quality_metrics" in dump
        assert dump["quality_metrics"] is None

    def test_result_dump_accepts_dict_issues_from_two_phase_merge(self):
        issue = {
            "issue_id": "ch52_timeline_01",
            "category": "timeline",
            "severity": "critical",
            "chapters_involved": [51, 52],
            "description": "前后章节时间线断裂。",
        }
        result = BookConsistencyResult(issues=[issue])  # type: ignore[list-item]
        dump = result.model_dump(mode="json")
        assert dump["issues"] == [issue]

    def test_result_dump_with_quality_metrics(self):
        metrics = AuditQualityMetrics(
            coverage_ratio=0.9,
            estimated_miss_rate=0.1,
            dimension_scores={"naming": 9.0},
            audit_depth="full",
            total_issues_found=5,
            critical_issues_found=2,
        )
        result = BookConsistencyResult(quality_metrics=metrics)
        dump = result.model_dump()
        assert "quality_metrics" in dump
        assert dump["quality_metrics"]["coverage_ratio"] == 0.9
        assert dump["quality_metrics"]["estimated_miss_rate"] == 0.1
        assert dump["quality_metrics"]["dimension_scores"] == {"naming": 9.0}
        assert dump["quality_metrics"]["audit_depth"] == "full"
        assert dump["quality_metrics"]["total_issues_found"] == 5
        assert dump["quality_metrics"]["critical_issues_found"] == 2


class TestCoverageRatioCalculation:
    def test_coverage_ratio_no_issues(self):
        metrics = _compute_quality_metrics_from_issues([])
        assert metrics.coverage_ratio == 1.0

    def test_coverage_ratio_with_critical_issues(self):
        issues = [{"category": "naming", "severity": "critical"}] * 3
        metrics = _compute_quality_metrics_from_issues(issues)
        assert metrics.coverage_ratio < 1.0
        assert metrics.estimated_miss_rate > 0.1


class TestDimensionScoresGrouping:
    def test_dimension_scores_has_six_keys(self):
        metrics = _compute_quality_metrics_from_issues([])
        assert len(metrics.dimension_scores) == 6
        assert set(metrics.dimension_scores.keys()) == {
            "naming",
            "timeline",
            "worldbuilding",
            "character_state",
            "plot_thread",
            "narrative_drift",
        }

    def test_dimension_scores_with_naming_issues(self):
        issues = [
            {"category": "naming", "severity": "critical"},
            {"category": "naming", "severity": "critical"},
        ]
        metrics = _compute_quality_metrics_from_issues(issues)
        assert metrics.dimension_scores["naming"] == 5.0
        assert metrics.dimension_scores["timeline"] == 10.0

    def test_dimension_scores_with_plot_thread_issues(self):
        issues = [{"category": "plot_thread", "severity": "warning"}] * 2
        metrics = _compute_quality_metrics_from_issues(issues)
        assert metrics.dimension_scores["plot_thread"] == 9.4
        assert metrics.dimension_scores["narrative_drift"] == 10.0


class TestEstimatedMissRate:
    def test_miss_rate_no_issues(self):
        metrics = _compute_quality_metrics_from_issues([])
        assert metrics.estimated_miss_rate == 0.1

    def test_miss_rate_increases_with_critical_issues(self):
        issues = [{"category": "naming", "severity": "critical"}]
        metrics = _compute_quality_metrics_from_issues(issues)
        assert metrics.estimated_miss_rate > 0.1
        assert metrics.estimated_miss_rate <= 0.5

    def test_miss_rate_caps_at_half(self):
        issues = [{"category": "naming", "severity": "critical"}] * 20
        metrics = _compute_quality_metrics_from_issues(issues)
        assert metrics.estimated_miss_rate <= 0.5
