"""Unified QualityGate — single quality assessment interface for both pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from novel_forge.core.review.alignment_contracts import (
    alignment_uses_structured_contract,
    verified_alignment_blockers,
)
from novel_forge.core.review.audit_profiles import get_audit_profile
from novel_forge.core.schemas.audit import AuditReportProjection
from novel_forge.core.utils.audit_issue import (
    is_open_issue,
    issue_severity,
    issue_status,
    normalize_audit_issue,
)


class QualityVerdict(str, Enum):
    """Overall quality gate result."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class QualityCheckResult:
    """A single quality dimension check."""

    dimension: str
    score: float
    threshold: float
    passed: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityGateReport:
    """Aggregated quality gate result across all checks."""

    verdict: QualityVerdict
    checks: list[QualityCheckResult]
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict != QualityVerdict.FAIL

    @property
    def failed_checks(self) -> list[QualityCheckResult]:
        return [c for c in self.checks if not c.passed]


# ── Threshold defaults ──────────────────────────────────────────────

DEFAULT_EVAL_PASS_THRESHOLD = 6.0
DEFAULT_EARLY_STOP_SCORE = 8.0
DEFAULT_ALIGNMENT_THRESHOLD = 7.0
DEFAULT_REVELATION_MAX = 2
WORD_COUNT_RATIO_LOW = 0.7
WORD_COUNT_RATIO_HIGH = 1.5


class QualityGate:
    """Configurable quality gate used by both short and long pipelines.

    Usage::

        gate = QualityGate(eval_threshold=6.0, alignment_threshold=7.0)
        gate.check_eval(eval_report)
        gate.check_word_count(current_words, target_words)
        gate.check_alignment(alignment_report)
        report = gate.report()
    """

    def __init__(
        self,
        *,
        eval_threshold: float = DEFAULT_EVAL_PASS_THRESHOLD,
        early_stop_score: float = DEFAULT_EARLY_STOP_SCORE,
        alignment_threshold: float = DEFAULT_ALIGNMENT_THRESHOLD,
        word_count_low: float = WORD_COUNT_RATIO_LOW,
        word_count_high: float = WORD_COUNT_RATIO_HIGH,
        revelation_max: int = DEFAULT_REVELATION_MAX,
    ) -> None:
        self.eval_threshold = eval_threshold
        self.early_stop_score = early_stop_score
        self.alignment_threshold = alignment_threshold
        self.word_count_low = word_count_low
        self.word_count_high = word_count_high
        self.revelation_max = revelation_max
        self._checks: list[QualityCheckResult] = []

    def reset(self) -> None:
        """Clear accumulated checks for reuse."""
        self._checks.clear()

    # ── Eval score check ──────────────────────────────────────────

    def check_eval(self, eval_report: Any) -> QualityCheckResult:
        """Check overall eval score against pass threshold."""
        score = float(getattr(eval_report, "overall_score", 0.0))
        passed = score >= self.eval_threshold
        result = QualityCheckResult(
            dimension="eval_score",
            score=score,
            threshold=self.eval_threshold,
            passed=passed,
            message="" if passed else f"评估分 {score:.1f} 低于阈值 {self.eval_threshold:.1f}",
            details={
                "score_scope": self._score_scope("eval_score"),
                "score_confidence": str(getattr(eval_report, "score_confidence", "") or ""),
            },
        )
        self._checks.append(result)
        return result

    def check_eval_dimension(
        self,
        eval_report: Any,
        dimension: str,
        threshold: float,
    ) -> QualityCheckResult:
        """Check one evaluation dimension against a configured floor."""
        target = str(dimension or "").strip().lower()
        matched = None
        for item in getattr(eval_report, "scores", []) or []:
            current_dimension = str(getattr(item, "dimension", "") or "").strip().lower()
            if current_dimension == target:
                matched = item
                break

        score = float(getattr(matched, "score", 0.0) or 0.0) if matched is not None else 0.0
        comment = str(getattr(matched, "comment", "") or "") if matched is not None else ""
        passed = matched is not None and score >= threshold
        if matched is None:
            message = f"评估缺少 {target} 维度，无法通过质量门"
        elif passed:
            message = ""
        else:
            message = f"{target} 维度 {score:.1f} 低于阈值 {threshold:.1f}"

        result = QualityCheckResult(
            dimension=f"eval_{target}",
            score=score,
            threshold=threshold,
            passed=passed,
            message=message,
            details={
                "eval_dimension": target,
                "comment": comment,
                "score_scope": self._score_scope("eval_score"),
            },
        )
        self._checks.append(result)
        return result

    def should_early_stop(self, eval_report: Any) -> bool:
        """Return True if eval score is high enough to skip remaining edits."""
        return float(getattr(eval_report, "overall_score", 0.0)) >= self.early_stop_score

    @staticmethod
    def _score_scope(dimension: str) -> str:
        scopes = {
            "eval_score": "综合清单分，用于生成循环判断；不等同文学表达满分。",
            "alignment": "大纲/章节目标履约专项分；不等同文学表达满分。",
            "continuity": "跨章承接和状态连续专项分；不等同文学表达满分。",
            "causal": "事件因果和动机链专项分；不等同文学表达满分。",
            "reading_power": "追读承诺与兑现专项分；不等同文学表达满分。",
            "chapter_quality": "正文内部硬错误与可核验证据门禁；不等同文学表达满分。",
            "knowledge_boundary": "角色认知边界和未来揭示安全专项分；不等同文学表达满分。",
        }
        return scopes.get(
            str(dimension or "").strip().lower(),
            "专项质量检查分；仅代表该检查维度。",
        )

    # ── Word count drift check ────────────────────────────────────

    def check_word_count(
        self,
        current_words: int,
        target_words: int,
    ) -> QualityCheckResult:
        """Check word count ratio against configured bounds."""
        ratio = current_words / max(target_words, 1)
        in_range = self.word_count_low <= ratio <= self.word_count_high
        msg = ""
        if not in_range:
            direction = "偏短" if ratio < self.word_count_low else "偏长"
            msg = f"字数 {current_words} / 目标 {target_words} (比率 {ratio:.2f}), {direction}"
        result = QualityCheckResult(
            dimension="word_count",
            score=ratio,
            threshold=self.word_count_low,
            passed=in_range,
            message=msg,
            details={"current": current_words, "target": target_words, "ratio": round(ratio, 3)},
        )
        self._checks.append(result)
        return result

    # ── Alignment check (long-form) ──────────────────────────────

    def check_alignment(self, alignment_report: Any) -> QualityCheckResult:
        """Check alignment score and alignment findings through the audit gate."""
        profile = get_audit_profile("alignment")
        projection = self._alignment_projection(alignment_report)

        # ── Fallback exemption ───────────────────────────────────────────
        # When the alignment evaluation was unavailable (timeout / LLM error /
        # truncated response), the score and findings are not trustworthy.
        # Exempt the gate so a distorted score does not block chapter archiving.
        if getattr(alignment_report, "is_fallback", False):
            result = QualityCheckResult(
                dimension="alignment",
                score=float(projection.score or 0.0),
                threshold=float(projection.threshold or profile.threshold or 0.0),
                passed=True,
                message="对齐评估不可用（兜底报告），已豁免门禁检查",
                details={
                    "issues_count": len(projection.issues or []),
                    "hard_fail": False,
                    "blocking_count": 0,
                    "is_fallback": True,
                    "evaluation_status": getattr(alignment_report, "evaluation_status", ""),
                    "fallback_reason": getattr(alignment_report, "fallback_reason", ""),
                    "score_scope": self._score_scope("alignment"),
                },
            )
            self._checks.append(result)
            return result

        if alignment_uses_structured_contract(alignment_report):
            blockers = verified_alignment_blockers(alignment_report)
            score = float(projection.score or 0.0)
            threshold = float(projection.threshold or profile.threshold or 0.0)
            passed = not blockers
            result = QualityCheckResult(
                dimension="alignment",
                score=score,
                threshold=threshold,
                passed=passed,
                message=(
                    ""
                    if passed
                    else f"对齐存在 {len(blockers)} 个已验证阻断项（专项分 {score:.1f}）"
                ),
                details={
                    "issues_count": len(
                        list(getattr(alignment_report, "review_findings", []) or [])
                    ),
                    "hard_fail": bool(blockers),
                    "blocking_count": len(blockers),
                    "critical_count": sum(
                        1
                        for finding in blockers
                        if str(getattr(finding, "severity", "") or "").lower()
                        == "critical"
                    ),
                    "high_count": sum(
                        1
                        for finding in blockers
                        if str(getattr(finding, "severity", "") or "").lower() == "high"
                    ),
                    "score_advisory": True,
                    "repair_lane": profile.repair_lane,
                    "reviewer_focus": profile.reviewer_focus,
                    "score_scope": self._score_scope("alignment"),
                    **dict(projection.metadata or {}),
                },
            )
            self._checks.append(result)
            return result

        return self.check_audit_projection(
            projection,
            score_label=profile.score_label,
            hard_severities=profile.hard_severities,
        )

    def _alignment_projection(self, alignment_report: Any) -> AuditReportProjection:
        score = float(getattr(alignment_report, "alignment_score", 0.0) or 0.0)
        raw_issues: list[dict[str, Any]] = []
        for item in list(getattr(alignment_report, "missing_main_points", []) or []):
            text = str(item or "").strip()
            if text:
                raw_issues.append(
                    {
                        "issue_type": "outline_main_point_missing",
                        "severity": "high",
                        "summary": text,
                        "evidence": text,
                        "repair_surface": "chapter_text",
                    }
                )
        for item in list(getattr(alignment_report, "weak_subplot_points", []) or []):
            text = str(item or "").strip()
            if text:
                raw_issues.append(
                    {
                        "issue_type": "outline_subplot_weak",
                        "severity": "medium",
                        "summary": text,
                        "evidence": text,
                        "repair_surface": "chapter_text",
                    }
                )
        conflict_level = str(getattr(alignment_report, "conflict_level", "") or "").strip().lower()
        if conflict_level in {"critical", "high"}:
            raw_issues.append(
                {
                    "issue_type": "alignment_conflict",
                    "severity": "critical" if conflict_level == "critical" else "high",
                    "summary": str(
                        getattr(alignment_report, "summary", "") or "章节对齐存在高风险冲突"
                    ),
                    "repair_surface": "chapter_text",
                }
            )
        issues = [
            normalize_audit_issue(
                item,
                dimension="alignment",
                namespace="alignment",
                repair_surface_default="chapter_text",
            )
            for item in raw_issues
        ]
        return AuditReportProjection(
            dimension="alignment",
            score=score,
            threshold=self.alignment_threshold or get_audit_profile("alignment").threshold,
            score_attr=get_audit_profile("alignment").score_attr,
            source_report_type=type(alignment_report).__name__,
            issues=issues,
            metadata={
                "repair_actions": list(getattr(alignment_report, "repair_actions", []) or [])[:3],
                "risk_level": str(getattr(alignment_report, "risk_level", "") or ""),
                "conflict_level": conflict_level,
            },
        )

    # ── Structured audit checks (long-form) ──────────────────────

    def check_audit_report(
        self,
        audit_report: Any,
        *,
        dimension: str,
        score_attr: str,
        threshold: float,
        score_label: str,
        issue_attr: str = "issues",
        hard_severities: tuple[str, ...] = (),
    ) -> QualityCheckResult:
        """Check a structured audit report with shared issue lifecycle semantics."""
        raw_issues = list(getattr(audit_report, issue_attr, []) or [])
        profile = get_audit_profile(dimension)
        projection = AuditReportProjection(
            dimension=dimension,
            score=float(getattr(audit_report, score_attr or profile.score_attr, 0.0) or 0.0),
            threshold=threshold or profile.threshold,
            score_attr=score_attr or profile.score_attr,
            source_report_type=type(audit_report).__name__,
            issues=[
                normalize_audit_issue(
                    issue,
                    dimension=dimension,
                    namespace=profile.namespace,
                    repair_surface_default=profile.default_repair_surface,
                )
                for issue in raw_issues
            ],
            metadata={
                "repair_lane": profile.repair_lane,
                "reviewer_focus": profile.reviewer_focus,
            },
        )
        return self.check_audit_projection(
            projection,
            score_label=score_label or profile.score_label,
            hard_severities=hard_severities or profile.hard_severities,
        )

    def check_audit_projection(
        self,
        projection: AuditReportProjection,
        *,
        score_label: str,
        hard_severities: tuple[str, ...] = (),
    ) -> QualityCheckResult:
        """Check a normalized audit projection."""
        profile = get_audit_profile(projection.dimension)
        score = float(projection.score or 0.0)
        threshold = float(projection.threshold or profile.threshold or 0.0)
        score_passed = score >= threshold
        issues = [i for i in list(projection.issues or []) if is_open_issue(i)]
        resolved_hard_severities = hard_severities or profile.hard_severities
        hard_set = {str(item or "").strip().lower() for item in resolved_hard_severities}
        hard = [i for i in issues if self._issue_severity(i) in hard_set]
        critical = [i for i in issues if self._issue_severity(i) == "critical"]
        high = [i for i in issues if self._issue_severity(i) == "high"]
        passed = score_passed and not hard
        result = QualityCheckResult(
            dimension=projection.dimension,
            score=score,
            threshold=threshold,
            passed=passed,
            message=""
            if passed
            else f"{(score_label or profile.score_label)} {score:.1f}, 阻断级问题 {len(hard)} 个，高优先级 {len(high)} 个",
            details={
                "issues_count": len(issues),
                "hard_fail": bool(hard),
                "blocking_count": len(hard),
                "critical_count": len(critical),
                "high_count": len(high),
                "repair_lane": profile.repair_lane,
                "reviewer_focus": profile.reviewer_focus,
                "score_scope": self._score_scope(projection.dimension),
                **dict(projection.metadata or {}),
            },
        )
        self._checks.append(result)
        return result

    def check_review_findings(
        self,
        findings: list[Any] | tuple[Any, ...],
        *,
        dimension: str,
    ) -> QualityCheckResult:
        """Check normalized ReviewFinding objects through the audit gate."""

        profile = get_audit_profile(dimension)
        relevant = [
            finding
            for finding in list(findings or [])
            if str(getattr(finding, "dimension", "") or "").strip().lower() == dimension
        ]
        raw_issues: list[dict[str, Any]] = []
        for finding in relevant:
            raw_issues.append(
                {
                    "issue_id": str(getattr(finding, "finding_id", "") or ""),
                    "issue_type": str(getattr(finding, "issue_type", "") or ""),
                    "severity": str(getattr(finding, "severity", "") or "medium"),
                    "summary": str(getattr(finding, "summary", "") or ""),
                    "evidence": str(getattr(finding, "evidence_quote", "") or ""),
                    "confidence": float(getattr(finding, "confidence", 0.0) or 0.0),
                    "status": "open",
                    "blocking": bool(getattr(finding, "blocks_finalize", False)),
                    "repair_surface": profile.default_repair_surface,
                }
            )
        severity_counts = {"critical": 0, "high": 0, "medium": 0}
        for item in raw_issues:
            severity = str(item.get("severity", "") or "").lower()
            if severity in severity_counts:
                severity_counts[severity] += 1
        score = 10.0
        score -= severity_counts["critical"] * 4.0
        score -= severity_counts["high"] * 2.0
        score -= severity_counts["medium"] * 0.75
        projection = AuditReportProjection(
            dimension=dimension,
            score=max(0.0, min(10.0, score)),
            threshold=profile.threshold,
            score_attr=profile.score_attr,
            source_report_type="ReviewFinding",
            issues=[
                normalize_audit_issue(
                    issue,
                    dimension=dimension,
                    namespace=profile.namespace,
                    repair_surface_default=profile.default_repair_surface,
                )
                for issue in raw_issues
            ],
            metadata={
                "finding_count": len(relevant),
                "repair_lane": profile.repair_lane,
                "reviewer_focus": profile.reviewer_focus,
            },
        )
        return self.check_audit_projection(
            projection,
            score_label=profile.score_label,
            hard_severities=profile.hard_severities,
        )

    def check_state_adjudication(
        self,
        report_payload: Any,
        *,
        threshold: float = 0.0,
    ) -> QualityCheckResult:
        """Check final narrative-state adjudication through the audit gate."""
        profile = get_audit_profile("state_adjudication")
        if isinstance(report_payload, dict):
            final = report_payload.get("final_adjudication", report_payload)
        else:
            final = getattr(report_payload, "final_adjudication", report_payload)
        if hasattr(final, "model_dump"):
            final_data = final.model_dump(mode="json")
        elif isinstance(final, dict):
            final_data = dict(final)
        else:
            final_data = {
                "verdict": getattr(final, "verdict", ""),
                "summary": getattr(final, "summary", ""),
                "severity": getattr(final, "severity", ""),
                "confidence": getattr(final, "confidence", 0.0),
                "should_block_archive": getattr(final, "should_block_archive", False),
                "accepted_candidate_ids": getattr(final, "accepted_candidate_ids", []),
                "pending_candidate_ids": getattr(final, "pending_candidate_ids", []),
                "repair_candidate_ids": getattr(final, "repair_candidate_ids", []),
            }
        verdict = str(final_data.get("verdict", "") or "").strip()
        summary = str(final_data.get("summary", "") or verdict or "状态裁判需要复核")
        severity = str(final_data.get("severity", "") or "critical").strip().lower()
        should_block = bool(final_data.get("should_block_archive", False))
        confidence = max(0.0, min(1.0, float(final_data.get("confidence", 0.0) or 0.0)))
        issues = []
        if should_block:
            issues.append(
                normalize_audit_issue(
                    {
                        "issue_type": "state_archive_block",
                        "severity": severity
                        if severity in {"critical", "high", "medium", "low"}
                        else "critical",
                        "summary": summary,
                        "status": "open",
                        "repair_surface": "state_packet",
                        "blocking": True,
                        "confidence": confidence,
                    },
                    dimension="state_adjudication",
                    namespace=profile.namespace,
                    repair_surface_default=profile.default_repair_surface,
                )
            )
        projection = AuditReportProjection(
            dimension="state_adjudication",
            score=confidence * 10,
            threshold=threshold if threshold > 0 else profile.threshold,
            score_attr=profile.score_attr,
            source_report_type=type(report_payload).__name__,
            issues=issues,
            metadata={
                "verdict": verdict,
                "severity": severity,
                "accepted": len(final_data.get("accepted_candidate_ids", []) or []),
                "pending": len(final_data.get("pending_candidate_ids", []) or []),
                "repair": len(final_data.get("repair_candidate_ids", []) or []),
            },
        )
        result = self.check_audit_projection(
            projection,
            score_label=profile.score_label,
            hard_severities=profile.hard_severities,
        )
        if not result.passed and should_block:
            result.message = f"LLM 状态裁判阻断归档：{summary}"
        return result

    # ── Continuity check (long-form) ─────────────────────────────

    def check_continuity(
        self, continuity_report: Any, threshold: float = 6.0
    ) -> QualityCheckResult:
        """Check continuity score."""
        return self.check_audit_report(
            continuity_report,
            dimension="continuity",
            score_attr="continuity_score",
            threshold=threshold,
            score_label="连续性分",
            hard_severities=("critical",),
        )

    def check_causal(self, causal_report: Any, threshold: float = 7.0) -> QualityCheckResult:
        """Check causal score with the shared audit issue semantics."""
        return self.check_audit_report(
            causal_report,
            dimension="causal",
            score_attr="causal_score",
            threshold=threshold,
            score_label="因果分",
            hard_severities=("critical",),
        )

    @staticmethod
    def _issue_severity(issue: Any) -> str:
        """Return a normalized issue severity from dict or schema objects."""
        return issue_severity(issue, default="")

    @staticmethod
    def _issue_status(issue: Any) -> str:
        """Return a normalized issue lifecycle status from dict or schema objects."""
        return issue_status(issue)

    # ── Chapter quality check (long-form) ────────────────────────

    @staticmethod
    def _as_text_list(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item or "").strip() for item in value if str(item or "").strip()]

    @classmethod
    def _contains_any(cls, text: str, markers: tuple[str, ...]) -> bool:
        lowered = str(text or "").lower()
        return any(marker.lower() in lowered for marker in markers)

    @staticmethod
    def _as_dict_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, (list, tuple)):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _forbidden_finding_verified(finding: dict[str, Any]) -> bool:
        if "evidence_verified" not in finding:
            return True
        return bool(finding.get("evidence_verified"))

    def check_chapter_quality(self, chapter_repair_report: Any) -> QualityCheckResult:
        """Check chapter-internal quality findings from CHECK_CHAPTER."""
        if chapter_repair_report is None:
            result = QualityCheckResult(
                dimension="chapter_quality",
                score=10.0,
                threshold=8.0,
                passed=True,
                details={"hard_fail": False},
            )
            self._checks.append(result)
            return result

        leaks = self._as_text_list(getattr(chapter_repair_report, "prompt_leaks", []))
        factual = self._as_text_list(getattr(chapter_repair_report, "factual_errors", []))
        expression = self._as_text_list(getattr(chapter_repair_report, "expression_errors", []))
        continuity = self._as_text_list(getattr(chapter_repair_report, "continuity_errors", []))
        forbidden_findings = self._as_dict_list(
            getattr(chapter_repair_report, "forbidden_element_findings", [])
        )
        verified_forbidden_findings = [
            finding for finding in forbidden_findings if self._forbidden_finding_verified(finding)
        ]
        unverified_forbidden_findings = [
            finding
            for finding in forbidden_findings
            if not self._forbidden_finding_verified(finding)
        ]
        has_structured_forbidden = bool(verified_forbidden_findings)

        hard_factual = [
            item
            for item in factual
            if self._contains_any(item, ("非法时辰", "非法刻度", "时间系统", "事实错误", "严重"))
        ]
        hard_expression = [
            item
            for item in expression
            if self._contains_any(
                item,
                (
                    "提示词泄露",
                    "prompt leak",
                    "pov",
                    "视角侵入",
                ),
            )
            or (not has_structured_forbidden and self._contains_any(item, ("硬禁", "硬禁元素")))
        ]
        hard_forbidden = [
            finding
            for finding in verified_forbidden_findings
            if str(finding.get("verdict", "")).strip().lower() in {"violation", "mechanical_reuse"}
            and (
                bool(finding.get("blocking", False))
                or str(finding.get("severity", "")).strip().lower() in {"high", "critical"}
            )
        ]
        soft_forbidden = [
            finding
            for finding in verified_forbidden_findings
            if str(finding.get("verdict", "")).strip().lower() in {"violation", "mechanical_reuse"}
            and finding not in hard_forbidden
            and str(finding.get("severity", "")).strip().lower() not in {"none", ""}
        ]
        forbidden_expression_count = (
            sum(1 for item in expression if "禁用元素" in item) if has_structured_forbidden else 0
        )
        hard_count = len(leaks) + len(hard_factual) + len(hard_expression) + len(hard_forbidden)
        soft_count = (
            len(factual)
            + len(expression)
            + len(continuity)
            - len(hard_factual)
            - len(hard_expression)
            - forbidden_expression_count
            + len(soft_forbidden)
        )
        issue_count = hard_count + max(0, soft_count)
        score = max(0.0, 10.0 - hard_count * 2.5 - max(0, soft_count) * 0.8)
        passed = issue_count == 0
        hard_fail = hard_count > 0
        if not issue_count:
            message = ""
        elif hard_fail:
            message = f"章节质量存在 {hard_count} 个阻断级问题"
        else:
            message = f"章节质量存在 {soft_count} 个需处理问题"
        result = QualityCheckResult(
            dimension="chapter_quality",
            score=round(score, 1),
            threshold=8.0,
            passed=passed,
            message=message,
            details={
                "hard_fail": hard_fail,
                "prompt_leaks": leaks[:5],
                "factual_errors": factual[:5],
                "expression_errors": expression[:5],
                "continuity_errors": continuity[:5],
                "forbidden_element_findings": verified_forbidden_findings[:5],
                "unverified_forbidden_element_findings": unverified_forbidden_findings[:5],
                "hard_count": hard_count,
                "soft_count": max(0, soft_count),
                "score_scope": self._score_scope("chapter_quality"),
            },
        )
        self._checks.append(result)
        return result

    # ── Revelation density check ─────────────────────────────────

    def check_revelation_density(
        self,
        revelation_count: int,
        *,
        max_revelations: int | None = None,
    ) -> QualityCheckResult:
        """Check revelation marker density against budget.

        Revelation markers ("原来", "竟然", "才发现", etc.) signal plot reveals.
        Excessive use indicates reliance on cheap plot twists rather than
        organic storytelling.
        """
        max_r = max_revelations if max_revelations is not None else self.revelation_max
        passed = revelation_count <= max_r
        if revelation_count == 0:
            msg = ""
        elif passed:
            msg = ""
        elif revelation_count <= max_r * 2:
            msg = f"揭示密度偏高: {revelation_count} 处（上限 {max_r}）"
        else:
            msg = f"揭示密度严重超标: {revelation_count} 处（上限 {max_r}）"
        result = QualityCheckResult(
            dimension="revelation_density",
            score=float(max(0, max_r - revelation_count + 1)),
            threshold=float(max_r),
            passed=passed,
            message=msg,
            details={"revelation_count": revelation_count, "max_allowed": max_r},
        )
        self._checks.append(result)
        return result

    # ── Backstory reveal check (added in M4 — see docs/ai_flavor_quality.md) ──

    #: Number of grace chapters after a backstory deadline before it flips
    #: from "pending" to "overdue". Set to 2 so the orchestrator gets one
    #: full repair cycle before the gate fails hard.
    _BACKSTORY_GRACE_CHAPTERS: int = 2

    def check_backstory_reveals(
        self,
        *,
        backstory_reveals: list[Any] | None,
        chapter_number: int,
        backstory_report: Any,
        threshold: float = 8.0,
    ) -> QualityCheckResult:
        """Verify each backstory's deadline and minimum word count.

        State per reveal:

        - **not_yet** (silent): current chapter < required_first_appearance
        - **pending**:  deadline reached, current_word_count < min_word_count
        - **satisfied**: deadline reached, current_word_count >= min_word_count
        - **overdue**:  chapter_number > deadline + grace_chapters AND not satisfied

        Score (only counts reveals whose deadline has been reached)::

            score = 10 * (satisfied / due_count)

        passed iff ``overdue == 0``. Empty constraints always pass.
        """
        if not backstory_reveals:
            result = QualityCheckResult(
                dimension="backstory_reveal",
                score=10.0,
                threshold=threshold,
                passed=True,
                message="",
                details={
                    "skip_reason": "no_constraints",
                    "satisfied": [],
                    "pending": [],
                    "overdue": [],
                },
            )
            self._checks.append(result)
            return result

        # Build a quick lookup from the report. The report shape is
        # {topic: {"current_word_count": int, ...}}.
        cumulative: dict[str, int] = {}
        if backstory_report is not None:
            raw = getattr(backstory_report, "cumulative", None)
            if isinstance(raw, dict):
                for topic, info in raw.items():
                    if isinstance(info, dict):
                        count = info.get("current_word_count", 0) or 0
                        try:
                            cumulative[str(topic)] = int(count)
                        except (TypeError, ValueError):
                            cumulative[str(topic)] = 0

        satisfied: list[str] = []
        pending: list[str] = []
        overdue: list[str] = []
        due_count = 0

        for spec in backstory_reveals:
            topic = str(self._field_value(spec, "topic", "") or "").strip()
            if not topic:
                continue
            deadline = int(self._field_value(spec, "required_first_appearance", 0) or 0)
            min_words = int(self._field_value(spec, "min_word_count", 200) or 200)
            if deadline <= 0:
                continue

            current = cumulative.get(topic, 0)
            if chapter_number < deadline:
                # Not yet due — silent.
                continue

            due_count += 1
            if current >= min_words:
                satisfied.append(topic)
            elif chapter_number > deadline + self._BACKSTORY_GRACE_CHAPTERS:
                overdue.append(topic)
            else:
                pending.append(topic)

        if due_count == 0:
            score = 10.0
        else:
            score = round(10.0 * len(satisfied) / due_count, 2)

        passed = not overdue
        if passed and not overdue:
            message = ""
        elif overdue:
            message = (
                f"背景展开逾期: {len(overdue)} 项超过 deadline+"
                f"{self._BACKSTORY_GRACE_CHAPTERS} 章仍未达 min_word_count"
            )
        else:
            message = f"背景展开滞后: {len(pending)} 项已达 deadline 但尚未达 min_word_count"

        result = QualityCheckResult(
            dimension="backstory_reveal",
            score=score,
            threshold=threshold,
            passed=passed,
            message=message,
            details={
                "satisfied": satisfied,
                "pending": pending,
                "overdue": overdue,
                "due_count": due_count,
                "current_chapter": chapter_number,
                "grace_chapters": self._BACKSTORY_GRACE_CHAPTERS,
            },
        )
        self._checks.append(result)
        return result

    # ── AI-flavor check (added in M2 — see docs/ai_flavor_quality.md) ──

    #: Severity weight table for AI-flavor scoring. Critical hits dominate
    #: via the 5.0 cap, but their confidence-weighted penalty also feeds in.
    _AI_FLAVOR_SEVERITY_WEIGHTS: dict[str, float] = {
        "critical": 4.0,
        "high": 2.0,
        "medium": 1.0,
        "low": 0.5,
    }

    #: Cap applied when any critical hit is present. Plan §2.2.1.
    _AI_FLAVOR_CRITICAL_CAP: float = 5.0

    def check_ai_flavor(
        self,
        humanize_report: Any,
        *,
        threshold: float = 8.0,
    ) -> QualityCheckResult:
        """Score AI-flavor hits using the severity-weighted penalty formula.

        Formula::

            score = 10 - sum(severity_weight * confidence for hit in hits)
            if any critical hit: score = min(score, 5.0)

        The check accepts any object exposing ``pattern_hits`` (HumanizeReport,
        a list of HumanizePatternHit, or a duck-typed mock). Threshold default
        of 8.0 matches the rubric v3 target.

        Returns a QualityCheckResult with ``dimension="ai_flavor"`` plus
        detail metadata for downstream consumers (repair orchestrator, UI).
        """
        hits = self._extract_ai_flavor_hits(humanize_report)

        penalty = 0.0
        has_critical = False
        by_pattern: dict[str, int] = {}
        weighted_count = 0

        for hit in hits:
            severity = str(self._field_value(hit, "severity", "medium") or "medium").lower()
            confidence = self._safe_float(
                self._field_value(hit, "confidence", 0.8),
                default=0.8,
                lower=0.0,
                upper=1.0,
            )
            weight = self._AI_FLAVOR_SEVERITY_WEIGHTS.get(severity, 1.0)
            penalty += weight * confidence
            weighted_count += 1

            pattern_id = str(self._field_value(hit, "pattern_id", "unknown") or "unknown")
            by_pattern[pattern_id] = by_pattern.get(pattern_id, 0) + 1

            if severity == "critical":
                has_critical = True

        score = max(0.0, 10.0 - penalty)
        if has_critical:
            score = min(score, self._AI_FLAVOR_CRITICAL_CAP)
        score = round(score, 2)

        passed = score >= threshold
        # P1-1: Very low AI-flavor score triggers hard_fail (blocks archive)
        hard_fail_score_floor = threshold * 0.5  # e.g., 4.0 when threshold=8.0
        is_hard_fail = has_critical or score < hard_fail_score_floor
        if passed:
            message = ""
        elif has_critical:
            message = (
                f"AI 味严重: 含 critical 级命中（合作残留、知识截止等），分数被封顶至 {score:.1f}"
            )
        else:
            message = (
                f"AI 味超出阈值: {weighted_count} 处命中, 分数 {score:.1f} < 阈值 {threshold:.1f}"
            )

        result = QualityCheckResult(
            dimension="ai_flavor",
            score=score,
            threshold=threshold,
            passed=passed,
            message=message,
            details={
                "hit_count": weighted_count,
                "by_pattern_id": by_pattern,
                "has_critical": has_critical,
                "hard_fail": is_hard_fail,
                "penalty": round(penalty, 2),
            },
        )
        self._checks.append(result)
        return result

    @staticmethod
    def _extract_ai_flavor_hits(humanize_report: Any) -> list[Any]:
        """Normalize the various shapes callers may pass to check_ai_flavor.

        Accepts:
        - HumanizeReport (with .pattern_hits)
        - list[HumanizePatternHit] / list[dict] / list[mock]
        - dict with "pattern_hits" key
        - None / empty (returns [])
        """
        if humanize_report is None:
            return []
        if isinstance(humanize_report, list):
            return list(humanize_report)
        if isinstance(humanize_report, dict):
            return list(humanize_report.get("pattern_hits", []) or [])
        hits = getattr(humanize_report, "pattern_hits", None)
        if hits is None:
            return []
        return list(hits)

    # ── Reading power check ──────────────────────────────────────

    def check_reading_power(
        self,
        reading_power_report: Any,
        threshold: float = 5.0,
    ) -> QualityCheckResult | None:
        """Check reading power score and issue profile through the audit gate."""
        if reading_power_report is None:
            return None
        profile = get_audit_profile("reading_power")
        try:
            from novel_forge.core.schemas.reading_power_repair import (
                _build_reading_power_issues,
            )

            raw_issues = _build_reading_power_issues(reading_power_report)
        except Exception:
            raw_issues = []
        projection = AuditReportProjection(
            dimension="reading_power",
            score=float(getattr(reading_power_report, profile.score_attr, 0.0) or 0.0),
            threshold=threshold or profile.threshold,
            score_attr=profile.score_attr,
            source_report_type=type(reading_power_report).__name__,
            issues=[
                normalize_audit_issue(
                    issue,
                    dimension="reading_power",
                    namespace=profile.namespace,
                    repair_surface_default=profile.default_repair_surface,
                )
                for issue in raw_issues
            ],
            metadata={
                "repair_lane": profile.repair_lane,
                "reviewer_focus": profile.reviewer_focus,
                "hook_type": str(getattr(reading_power_report, "hook_type", "") or ""),
                "hook_strength": str(getattr(reading_power_report, "hook_strength", "") or ""),
            },
        )
        return self.check_audit_projection(
            projection,
            score_label=profile.score_label,
            hard_severities=profile.hard_severities,
        )

    # ── Chapter rhythm check ──────────────────────────────────────

    @staticmethod
    def _target_pacing_to_avg_len(target_pacing: Any) -> tuple[float, float]:
        """Map 1-5 target_pacing to expected average sentence length range."""
        try:
            level = int(target_pacing)
        except (TypeError, ValueError):
            level = 3
        ranges = {
            1: (20.0, 30.0),
            2: (17.0, 24.0),
            3: (14.0, 20.0),
            4: (10.0, 16.0),
            5: (7.0, 13.0),
        }
        return ranges.get(max(1, min(5, level)), (14.0, 20.0))

    @staticmethod
    def _field_value(source: Any, key: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(key, default)
        return getattr(source, key, default)

    @staticmethod
    def _safe_float(
        value: Any,
        *,
        default: float = 0.0,
        lower: float | None = None,
        upper: float | None = None,
    ) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = default
        if lower is not None:
            result = max(lower, result)
        if upper is not None:
            result = min(upper, result)
        return result

    def check_rhythm_curve(
        self,
        actual_rhythm: Any,
        target_rhythm: Any,
        threshold: float = 6.0,
    ) -> QualityCheckResult:
        """Check actual chapter rhythm against blueprint target.

        Compares the computed average sentence length from the actual rhythm
        signature against the expected range from the blueprint's target_pacing.
        Produces a soft warning (not hard block) when the rhythm deviates
        significantly from the plan.
        """
        if target_rhythm is None or actual_rhythm is None:
            result = QualityCheckResult(
                dimension="rhythm_curve",
                score=10.0,
                threshold=threshold,
                passed=True,
                message="",
                details={"skip_reason": "no_target_or_actual"},
            )
            self._checks.append(result)
            return result

        # Extract target pacing and compute expected range.
        target_pacing = self._field_value(target_rhythm, "target_pacing", 3) or 3
        target_tension = self._field_value(target_rhythm, "target_tension", 3) or 3
        beat_pattern = str(self._field_value(target_rhythm, "beat_pattern", "") or "")
        chapter_number = int(self._field_value(target_rhythm, "chapter_number", 0) or 0)
        expected_low, expected_high = self._target_pacing_to_avg_len(target_pacing)

        # Extract actual average sentence length.
        actual_avg = float(self._field_value(actual_rhythm, "avg_sentence_len", 0.0) or 0.0)
        actual_std = float(self._field_value(actual_rhythm, "std_sentence_len", 0.0) or 0.0)

        # Compute score: 10 if within range, decreasing as we move away.
        if expected_low <= actual_avg <= expected_high:
            score = 10.0
            passed = True
            message = ""
        elif actual_avg < expected_low:
            # Too fast (short sentences).
            deviation = expected_low - actual_avg
            score = max(0.0, 10.0 - deviation * 1.5)
            passed = score >= threshold
            message = f"节奏偏快：实际均句长 {actual_avg:.1f} 字，目标范围 {expected_low:.0f}-{expected_high:.0f} 字"
        else:
            # Too slow (long sentences).
            deviation = actual_avg - expected_high
            score = max(0.0, 10.0 - deviation * 1.5)
            passed = score >= threshold
            message = f"节奏偏慢：实际均句长 {actual_avg:.1f} 字，目标范围 {expected_low:.0f}-{expected_high:.0f} 字"

        result = QualityCheckResult(
            dimension="rhythm_curve",
            score=round(score, 1),
            threshold=threshold,
            passed=passed,
            message=message,
            details={
                "chapter_number": chapter_number,
                "target_pacing": target_pacing,
                "target_tension": target_tension,
                "beat_pattern": beat_pattern,
                "expected_avg_range": [expected_low, expected_high],
                "actual_avg_sentence_len": round(actual_avg, 2),
                "actual_std_sentence_len": round(actual_std, 2),
                "deviation": round(
                    abs(expected_low - actual_avg)
                    if actual_avg < expected_low
                    else abs(actual_avg - expected_high),
                    2,
                ),
                "hard_fail": False,
            },
        )
        self._checks.append(result)
        return result

    def check_chapter_rhythm(
        self,
        actual_rhythm: Any,
        target_rhythm: Any,
        chapter_number: int,
    ) -> QualityCheckResult:
        """Backward-compatible wrapper for the rhythm-curve check."""
        if isinstance(target_rhythm, dict):
            target = {
                **target_rhythm,
                "chapter_number": target_rhythm.get("chapter_number") or chapter_number,
            }
        else:
            target = target_rhythm
        return self.check_rhythm_curve(actual_rhythm, target)

    # ── Aggregate report ─────────────────────────────────────────

    def report(self) -> QualityGateReport:
        """Build aggregate quality gate report from accumulated checks."""
        if not self._checks:
            return QualityGateReport(
                verdict=QualityVerdict.PASS,
                checks=[],
                summary="无质量检查结果",
            )
        failed = [c for c in self._checks if not c.passed]
        if not failed:
            verdict = QualityVerdict.PASS
            summary = f"全部 {len(self._checks)} 项质量检查通过"
        else:
            hard_fail_dims = {"alignment", "state_adjudication"}
            has_hard_fail = any(
                c.dimension in hard_fail_dims or bool(c.details.get("hard_fail")) for c in failed
            )
            verdict = QualityVerdict.FAIL if has_hard_fail else QualityVerdict.WARN
            msgs = [c.message for c in failed if c.message]
            summary = "；".join(msgs) if msgs else f"{len(failed)} 项质量检查未通过"
        return QualityGateReport(verdict=verdict, checks=list(self._checks), summary=summary)
