"""Tests for the unified QualityGate module."""

from __future__ import annotations

from dataclasses import dataclass

from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.pipeline.quality_gate import (
    QualityGate,
    QualityVerdict,
)


@dataclass
class _MockEvalReport:
    overall_score: float = 7.5


@dataclass
class _MockEvalScore:
    dimension: str
    score: float
    comment: str = ""


@dataclass
class _MockEvalWithScores:
    scores: list[_MockEvalScore]


@dataclass
class _MockAlignmentReport:
    alignment_score: float = 8.0
    repair_actions: list = None
    missing_main_points: list = None
    weak_subplot_points: list = None
    conflict_level: str = "low"
    risk_level: str = "low"
    summary: str = ""

    def __post_init__(self):
        if self.repair_actions is None:
            self.repair_actions = []
        if self.missing_main_points is None:
            self.missing_main_points = []
        if self.weak_subplot_points is None:
            self.weak_subplot_points = []


@dataclass
class _MockContinuityReport:
    continuity_score: float = 7.0
    issues: list = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


@dataclass
class _MockCausalReport:
    causal_score: float = 7.0
    issues: list = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


@dataclass
class _MockReadingPowerReport:
    chapter: int = 1
    overall_score: float = 7.0
    hook_type: str = "mystery"
    hook_strength: str = "medium"
    prev_hook_fulfilled: bool = True
    micro_payoffs: list = None
    outline_hook_match: dict | None = None
    is_fallback: bool = False

    def __post_init__(self):
        if self.micro_payoffs is None:
            self.micro_payoffs = ["线索兑现"]


@dataclass
class _MockRepairReport:
    prompt_leaks: list = None
    factual_errors: list = None
    expression_errors: list = None
    continuity_errors: list = None
    forbidden_element_findings: list = None

    def __post_init__(self):
        if self.prompt_leaks is None:
            self.prompt_leaks = []
        if self.factual_errors is None:
            self.factual_errors = []
        if self.expression_errors is None:
            self.expression_errors = []
        if self.continuity_errors is None:
            self.continuity_errors = []
        if self.forbidden_element_findings is None:
            self.forbidden_element_findings = []


class TestQualityGate:

    def test_all_pass(self):
        gate = QualityGate()
        gate.check_eval(_MockEvalReport(overall_score=8.0))
        gate.check_alignment(_MockAlignmentReport(alignment_score=9.0))
        gate.check_word_count(3000, 3000)
        report = gate.report()
        assert report.verdict == QualityVerdict.PASS
        assert report.passed

    def test_eval_fail_is_warn(self):
        gate = QualityGate()
        gate.check_eval(_MockEvalReport(overall_score=4.0))
        report = gate.report()
        assert report.verdict == QualityVerdict.WARN

    def test_eval_dimension_pass(self):
        gate = QualityGate()
        report = _MockEvalWithScores(
            scores=[_MockEvalScore(dimension="style", score=7.8, comment="风格稳定")]
        )
        result = gate.check_eval_dimension(report, "style", 7.0)
        assert result.passed
        assert result.dimension == "eval_style"

    def test_eval_dimension_fail_when_missing(self):
        gate = QualityGate()
        report = _MockEvalWithScores(scores=[])
        result = gate.check_eval_dimension(report, "engagement", 7.0)
        assert not result.passed
        assert "缺少 engagement 维度" in result.message

    def test_alignment_fail_is_hard_fail(self):
        gate = QualityGate()
        gate.check_alignment(_MockAlignmentReport(alignment_score=3.0))
        report = gate.report()
        assert report.verdict == QualityVerdict.FAIL
        assert not report.passed

    def test_alignment_high_issue_blocks_even_when_score_passes(self):
        gate = QualityGate()
        result = gate.check_alignment(
            _MockAlignmentReport(
                alignment_score=8.5,
                missing_main_points=["缺少章节主线承诺"],
            )
        )
        assert not result.passed
        assert result.details["high_count"] == 1
        assert result.details["hard_fail"] is True
        assert result.details["repair_lane"] == "alignment_repair"

    def test_structured_alignment_low_score_without_verified_blocker_is_advisory(self):
        gate = QualityGate()
        result = gate.check_alignment(
            AlignmentReport(
                review_contract_version=1,
                alignment_score=2.6,
                weak_subplot_points=["局部表达可更明确"],
            )
        )
        assert result.passed
        assert result.details["score_advisory"] is True
        assert result.details["blocking_count"] == 0
        assert gate.report().verdict == QualityVerdict.PASS

    def test_structured_alignment_verified_blocker_fails_even_when_score_passes(self):
        gate = QualityGate()
        result = gate.check_alignment(
            AlignmentReport(
                review_contract_version=1,
                alignment_score=9.6,
                review_findings=[
                    ReviewFinding(
                        finding_id="alignment:plan.required_outcome:missing",
                        source_module="alignment",
                        dimension="alignment",
                        issue_type="required_outcome_missing",
                        severity="high",
                        confidence=0.92,
                        summary="未落实审期约束",
                        blocks_finalize=True,
                        metadata={
                            "source_ref": "plan.required_outcome",
                            "source_evidence_verified": True,
                            "coverage_status": "missing",
                        },
                    )
                ],
            )
        )
        assert not result.passed
        assert result.details["score_advisory"] is True
        assert result.details["blocking_count"] == 1
        assert gate.report().verdict == QualityVerdict.FAIL

    def test_state_adjudication_block_uses_audit_gate(self):
        gate = QualityGate()
        result = gate.check_state_adjudication(
            {
                "final_adjudication": {
                    "verdict": "needs_repair",
                    "summary": "权威状态和正文结尾冲突",
                    "severity": "high",
                    "confidence": 0.82,
                    "should_block_archive": True,
                    "accepted_candidate_ids": ["a"],
                    "pending_candidate_ids": [],
                    "repair_candidate_ids": ["r"],
                }
            }
        )
        assert not result.passed
        assert result.dimension == "state_adjudication"
        assert result.details["blocking_count"] == 1
        assert result.details["repair_lane"] == "state_adjudication_repair"
        assert "阻断归档" in result.message

    def test_state_adjudication_non_block_passes_even_low_confidence(self):
        gate = QualityGate()
        result = gate.check_state_adjudication(
            {
                "final_adjudication": {
                    "verdict": "accept",
                    "summary": "状态可归档",
                    "confidence": 0.3,
                    "should_block_archive": False,
                }
            }
        )
        assert result.passed

    def test_reading_power_check_keeps_reader_pull_profile(self):
        gate = QualityGate()
        result = gate.check_reading_power(
            _MockReadingPowerReport(
                chapter=2,
                overall_score=7.0,
                hook_type="none",
                hook_strength="weak",
                micro_payoffs=[],
            )
        )
        assert result is not None
        assert not result.passed
        assert result.details["high_count"] >= 1
        assert result.details["repair_lane"] == "reading_power_repair"

    def test_word_count_in_range(self):
        gate = QualityGate()
        result = gate.check_word_count(2500, 3000)
        assert result.passed

    def test_word_count_too_short(self):
        gate = QualityGate()
        result = gate.check_word_count(1500, 3000)
        assert not result.passed

    def test_word_count_too_long(self):
        gate = QualityGate()
        result = gate.check_word_count(5000, 3000)
        assert not result.passed

    def test_chapter_quality_prompt_leak_is_hard_fail(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(prompt_leaks=["系统:", "你是一个"])
        )
        assert not result.passed
        assert result.details["hard_fail"] is True
        assert gate.report().verdict == QualityVerdict.FAIL

    def test_chapter_quality_clean(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(_MockRepairReport())
        assert result.passed

    def test_chapter_quality_hard_bans_and_invalid_time_are_hard_fail(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(
                factual_errors=["非法时辰：正文使用酉时"],
                expression_errors=["硬禁元素：冷白灯光"],
            )
        )
        assert not result.passed
        assert result.details["hard_fail"] is True
        assert gate.report().verdict == QualityVerdict.FAIL

    def test_chapter_quality_soft_expression_warns_without_hard_fail(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(expression_errors=["一般表达重复：连续三段使用同一动作"])
        )
        assert not result.passed
        assert result.details["hard_fail"] is False
        assert gate.report().verdict == QualityVerdict.WARN

    def test_chapter_quality_ignores_unverified_forbidden_findings(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(
                forbidden_element_findings=[
                    {
                        "forbidden": "这是一个未决的线索",
                        "matched": "这是一个未决的线索",
                        "verdict": "violation",
                        "severity": "critical",
                        "blocking": True,
                        "evidence_verified": False,
                    }
                ]
            )
        )

        assert result.passed
        assert result.details["hard_count"] == 0
        assert result.details["unverified_forbidden_element_findings"]

    def test_chapter_quality_structured_forbidden_benign_does_not_penalize(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(
                forbidden_element_findings=[
                    {
                        "forbidden": "账簿残页",
                        "matched": "账簿残页",
                        "verdict": "story_anchor",
                        "severity": "none",
                        "blocking": False,
                    }
                ]
            )
        )
        assert result.passed
        assert result.details["hard_fail"] is False

    def test_chapter_quality_structured_forbidden_medium_warns(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(
                expression_errors=["禁用元素机械复用：'冷白灯光'"],
                forbidden_element_findings=[
                    {
                        "forbidden": "冷白灯光",
                        "matched": "冷白灯光",
                        "verdict": "mechanical_reuse",
                        "severity": "medium",
                        "blocking": False,
                    }
                ],
            )
        )
        assert not result.passed
        assert result.details["hard_fail"] is False
        assert result.details["soft_count"] == 1
        assert gate.report().verdict == QualityVerdict.WARN

    def test_chapter_quality_structured_forbidden_high_blocks(self):
        gate = QualityGate()
        result = gate.check_chapter_quality(
            _MockRepairReport(
                expression_errors=["禁用元素语义违规：'金丝微颤'"],
                forbidden_element_findings=[
                    {
                        "forbidden": "金丝微颤",
                        "matched": "金丝微颤",
                        "verdict": "violation",
                        "severity": "high",
                        "blocking": True,
                    }
                ],
            )
        )
        assert not result.passed
        assert result.details["hard_fail"] is True
        assert result.details["hard_count"] == 1
        assert gate.report().verdict == QualityVerdict.FAIL

    def test_continuity_check(self):
        gate = QualityGate()
        result = gate.check_continuity(_MockContinuityReport(continuity_score=8.0))
        assert result.passed

    def test_continuity_check_reports_high_priority_count(self):
        gate = QualityGate()
        result = gate.check_continuity(
            _MockContinuityReport(
                continuity_score=5.5,
                issues=[
                    {"severity": "high", "summary": "开场跳切"},
                    {"severity": "medium", "summary": "承接偏弱"},
                ],
            )
        )
        assert not result.passed
        assert result.details["critical_count"] == 0
        assert result.details["high_count"] == 1
        assert "高优先级 1 个" in result.message

    def test_continuity_check_ignores_suppressed_issue(self):
        gate = QualityGate()
        result = gate.check_continuity(
            _MockContinuityReport(
                continuity_score=8.0,
                issues=[{"severity": "critical", "status": "suppressed", "summary": "本地误判"}],
            )
        )
        assert result.passed
        assert result.details["issues_count"] == 0

    def test_causal_check_uses_shared_audit_gate(self):
        gate = QualityGate()
        result = gate.check_causal(
            _MockCausalReport(
                causal_score=8.5,
                issues=[{"severity": "critical", "status": "open", "summary": "因果断裂"}],
            )
        )
        assert not result.passed
        assert result.details["hard_fail"] is True
        assert gate.report().verdict == QualityVerdict.FAIL

    def test_early_stop(self):
        gate = QualityGate(early_stop_score=8.0)
        assert gate.should_early_stop(_MockEvalReport(overall_score=9.0))
        assert not gate.should_early_stop(_MockEvalReport(overall_score=7.0))

    def test_reset(self):
        gate = QualityGate()
        gate.check_eval(_MockEvalReport(overall_score=4.0))
        assert len(gate._checks) == 1
        gate.reset()
        assert len(gate._checks) == 0

    def test_empty_report(self):
        gate = QualityGate()
        report = gate.report()
        assert report.verdict == QualityVerdict.PASS

    def test_revelation_density_in_range(self):
        gate = QualityGate()
        result = gate.check_revelation_density(1, max_revelations=2)
        assert result.passed
        assert result.dimension == "revelation_density"
        assert result.details["revelation_count"] == 1

    def test_revelation_density_over_budget(self):
        gate = QualityGate()
        result = gate.check_revelation_density(3, max_revelations=2)
        assert not result.passed
        assert "偏高" in result.message
        assert result.details["max_allowed"] == 2

    def test_revelation_density_severely_over(self):
        gate = QualityGate()
        result = gate.check_revelation_density(6, max_revelations=2)
        assert not result.passed
        assert "严重超标" in result.message

    def test_revelation_density_zero(self):
        gate = QualityGate()
        result = gate.check_revelation_density(0)
        assert result.passed
        assert result.message == ""

    def test_revelation_density_uses_default_max(self):
        gate = QualityGate(revelation_max=4)
        result = gate.check_revelation_density(4)
        assert result.passed
        result2 = gate.check_revelation_density(5)
        assert not result2.passed

    def test_revelation_density_contributes_to_warn_verdict(self):
        gate = QualityGate()
        gate.check_revelation_density(3, max_revelations=2)
        report = gate.report()
        assert report.verdict == QualityVerdict.WARN
