"""Unified chapter review matrix, repair routing, and judge decisions.

This module is intentionally pure: it does not read files, call LLMs, or mutate
pipeline state.  Backend checkpoints and desktop read models use it as the
single place that answers three questions:

1. What did each review axis evaluate?
2. Which findings are actually repairable by the current finalize flow?
3. Which user-facing checkpoint action should be recommended?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.common.constants import SEVERITY_RANK, severity_at_least
from novel_forge.core.domain.guardrails import (
    AMBIGUOUS_PROMPT_LEAK_VERDICT,
    PROMPT_LEAK_VERDICT,
    classify_reported_prompt_leaks,
)

OPTION_ACCEPT = "accept_and_finalize"
OPTION_APPLY_REPAIRS = "apply_repairs_and_finalize"
OPTION_ADJUST_OUTLINE = "adjust_outline_and_finalize"
OPTION_PAUSE = "pause_for_human"

DEFAULT_OPTION_IDS = frozenset(
    {
        OPTION_ACCEPT,
        OPTION_APPLY_REPAIRS,
        OPTION_ADJUST_OUTLINE,
        OPTION_PAUSE,
    }
)


@dataclass(frozen=True)
class ReviewAxis:
    """One non-overlapping review axis shown to users and used by policy."""

    key: str
    label: str
    score: float | None = None
    status: str = "unknown"  # pass / warn / fail / unknown
    risk_level: str = "low"
    issue_count: int = 0
    blocking_issue_count: int = 0
    repairable_issue_count: int = 0
    summary: str = ""


@dataclass(frozen=True)
class ReviewDecision:
    """Unified AI/user-action decision for a guard checkpoint."""

    recommended_option: str
    allowed_option_ids: frozenset[str]
    risk_level: str
    decision_line: str
    repair_option_description: str
    blocked_reason: str = ""


@dataclass(frozen=True)
class ChapterReviewMatrix:
    """Aggregated review state for one chapter revision."""

    axes: tuple[ReviewAxis, ...]
    decision: ReviewDecision
    score_line: str
    warnings: tuple[str, ...] = ()

    def axis(self, key: str) -> ReviewAxis | None:
        return next((axis for axis in self.axes if axis.key == key), None)


def coerce_score(value: Any) -> float | None:
    """Coerce a score-like value to float without treating missing as zero."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def format_review_score_parts(
    *,
    alignment_score: float | None = None,
    continuity_score: float | None = None,
    overall_score: float | None = None,
    causal_score: float | None = None,
    reading_power_score: float | None = None,
    include_suffix: bool = False,
) -> list[str]:
    """Format scores in the canonical UI order.

    Order is intentionally stable across the app: 对齐 → 连贯 → 质量 → 因果 → 追读.
    """
    suffix = " / 10" if include_suffix else ""
    values = (
        ("对齐", alignment_score),
        ("连贯", continuity_score),
        ("质量", overall_score),
        ("因果", causal_score),
        ("追读", reading_power_score),
    )
    return [
        f"{label} {float(score):.1f}{suffix}"
        for label, score in values
        if score is not None
    ]


def format_review_score_line(
    *,
    alignment_score: float | None = None,
    continuity_score: float | None = None,
    overall_score: float | None = None,
    causal_score: float | None = None,
    reading_power_score: float | None = None,
    include_suffix: bool = False,
) -> str:
    return " / ".join(
        format_review_score_parts(
            alignment_score=alignment_score,
            continuity_score=continuity_score,
            overall_score=overall_score,
            causal_score=causal_score,
            reading_power_score=reading_power_score,
            include_suffix=include_suffix,
        )
    )


def _normalized_severity(value: Any) -> str:
    severity = str(value or "medium").strip().lower()
    if severity == "hard":
        return "critical"
    return severity if severity in SEVERITY_RANK else "medium"


def _issue_summary(issue: Any) -> str:
    if isinstance(issue, dict):
        return str(issue.get("summary") or issue.get("description") or "").strip()
    return str(getattr(issue, "summary", "") or getattr(issue, "description", "") or "").strip()


def _issue_severity(issue: Any) -> str:
    if isinstance(issue, dict):
        return _normalized_severity(issue.get("severity"))
    return _normalized_severity(getattr(issue, "severity", "medium"))


def _issues(report: Any) -> list[Any]:
    return list(getattr(report, "issues", []) or [])


def _reading_power_issues(report: Any | None) -> list[Any]:
    if report is None:
        return []
    findings = list(getattr(report, "review_findings", []) or [])
    if findings:
        return findings
    try:
        from novel_forge.core.schemas.reading_power_repair import _build_reading_power_issues

        return list(_build_reading_power_issues(report))
    except Exception:
        return []


def _blocking_issues(report: Any, *, must_fix_severity: str) -> list[Any]:
    if must_fix_severity == "off":
        return []
    return [
        issue
        for issue in _issues(report)
        if severity_at_least(_issue_severity(issue), must_fix_severity)
    ]


def _guard_warning_is_actionable(value: Any) -> bool:
    text = str(value or "").strip()
    return text.startswith("AI护栏约束合规率过低:")


def _guard_warning_is_incomplete(value: Any) -> bool:
    text = str(value or "").strip()
    return text.startswith("AI护栏约束检查未完成:")


def _guard_ticket_actionable(ticket: Any) -> bool:
    metadata = dict(getattr(ticket, "metadata", {}) or {})
    if metadata.get("repairable") is False or metadata.get("check_error") is True:
        return False
    issue_type = str(getattr(ticket, "issue_type", "") or "").lower()
    if issue_type in {"guard_constraint_unverified", "guard_constraint_check_failed"}:
        return False
    status = str(metadata.get("status", "") or "").strip().lower().replace("-", "_")
    return not status or status in {"non_compliant", "partial", "weak"}


def _risk_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(
        str(value or "").lower(),
        1,
    )


def _max_risk(*values: str) -> str:
    ordered = ("low", "medium", "high", "critical")
    return ordered[max(_risk_rank(value) for value in values)]


def _score_axis_status(
    score: float | None,
    *,
    threshold: float,
    risk_level: str = "low",
    passed: bool | None = None,
) -> str:
    if passed is False:
        return "fail"
    if score is None:
        return "unknown"
    if score < threshold:
        return "fail"
    if score < threshold + 1.0 or _risk_rank(risk_level) >= _risk_rank("medium"):
        return "warn"
    return "pass"


def _summaries(issues: list[Any], *, limit: int = 3) -> str:
    values = [_issue_summary(issue) for issue in issues]
    return "；".join(value for value in values if value)[:240]


def _build_repair_description(
    *,
    alignment_axis: ReviewAxis,
    continuity_report: Any,
    causal_report: Any | None,
    reading_power_issues: list[Any],
    guard_repairable_count: int,
    guard_actionable_warning: bool,
    prompt_leak_count: int,
) -> str:
    parts: list[str] = []

    if prompt_leak_count:
        parts.append(f"清理 {prompt_leak_count} 处提示词/规划语句泄露")

    if alignment_axis.repairable_issue_count:
        parts.append("补齐大纲对齐缺口")

    continuity_issues = _issues(continuity_report)
    if continuity_issues:
        severe = sum(
            1
            for issue in continuity_issues
            if _normalized_severity(_issue_severity(issue)) in {"high", "critical"}
        )
        detail = f"{severe} 条高风险" if severe else f"{len(continuity_issues)} 条"
        summary = _summaries(continuity_issues)
        parts.append(
            f"自动修复 {len(continuity_issues)} 条连贯性问题（{detail}）"
            + (f"：{summary}" if summary else "")
        )

    causal_issues = _issues(causal_report)
    if causal_issues:
        blocking = [
            issue
            for issue in causal_issues
            if _normalized_severity(_issue_severity(issue)) in {"high", "critical"}
        ]
        if blocking:
            summary = _summaries(blocking)
            parts.append(
                f"修复 {len(blocking)} 条高风险因果问题"
                + (f"：{summary}" if summary else "")
            )

    if reading_power_issues:
        summary = _summaries(reading_power_issues)
        parts.append(
            f"补强 {len(reading_power_issues)} 条追读力问题"
            + (f"：{summary}" if summary else "")
        )

    if guard_repairable_count:
        parts.append(f"回收 {guard_repairable_count} 条 AI 护栏修复票")
    elif guard_actionable_warning:
        parts.append("优先回收 AI 护栏未兑现的关键约束")

    if not parts:
        parts.append("当前无可自动修复项，跳过文本修复")
    parts.append("归档本章")
    return "，再".join(parts) + "。"


def build_chapter_review_matrix(
    *,
    current_text: str = "",
    alignment_report: Any,
    continuity_report: Any,
    eval_report: Any,
    causal_report: Any | None = None,
    reading_power_report: Any | None = None,
    chapter_repair_report: Any | None = None,
    guard_decision: Any | None = None,
    warnings: tuple[str, ...] | list[str] = (),
    repair_tickets: tuple[Any, ...] | list[Any] = (),
    must_fix_severity: str = "critical",
    alignment_threshold: float = 7.0,
    reading_power_threshold: float = 5.0,
) -> ChapterReviewMatrix:
    """Build a unified review matrix and checkpoint policy."""
    must_fix = str(must_fix_severity or "critical").lower()
    prompt_leak_verdicts = classify_reported_prompt_leaks(
        current_text,
        getattr(chapter_repair_report, "prompt_leaks", []) if chapter_repair_report is not None else (),
    )
    confirmed_prompt_leaks = prompt_leak_verdicts[PROMPT_LEAK_VERDICT]
    prompt_leak_count = len(confirmed_prompt_leaks)
    ambiguous_prompt_leak_count = len(prompt_leak_verdicts[AMBIGUOUS_PROMPT_LEAK_VERDICT])

    alignment_score = coerce_score(getattr(alignment_report, "alignment_score", None))
    continuity_score = coerce_score(getattr(continuity_report, "continuity_score", None))
    overall_score = coerce_score(getattr(eval_report, "overall_score", None))
    causal_score = coerce_score(
        getattr(causal_report, "causal_score", None) if causal_report is not None else None
    )
    reading_power_score = coerce_score(
        getattr(reading_power_report, "overall_score", None)
        if reading_power_report is not None
        else None
    )
    eval_threshold = coerce_score(getattr(eval_report, "threshold", 6.0)) or 6.0
    eval_passed = bool(getattr(eval_report, "passed", False))

    alignment_risk = str(getattr(alignment_report, "risk_level", "low") or "low").lower()
    alignment_repairable = int(
        bool(
            alignment_score is not None
            and alignment_score < alignment_threshold
            and (
                getattr(alignment_report, "repair_actions", None)
                or getattr(alignment_report, "missing_main_points", None)
            )
        )
    )
    alignment_axis = ReviewAxis(
        key="alignment",
        label="大纲对齐",
        score=alignment_score,
        status=_score_axis_status(
            alignment_score,
            threshold=alignment_threshold,
            risk_level=alignment_risk,
        ),
        risk_level=alignment_risk,
        issue_count=len(getattr(alignment_report, "missing_main_points", []) or []),
        repairable_issue_count=alignment_repairable,
        summary=str(getattr(alignment_report, "summary", "") or ""),
    )

    continuity_blocking = _blocking_issues(continuity_report, must_fix_severity=must_fix)
    continuity_axis = ReviewAxis(
        key="continuity",
        label="连贯性",
        score=continuity_score,
        status="fail" if continuity_blocking else ("warn" if _issues(continuity_report) else "pass"),
        risk_level="high" if continuity_blocking else ("medium" if _issues(continuity_report) else "low"),
        issue_count=len(_issues(continuity_report)),
        blocking_issue_count=len(continuity_blocking),
        repairable_issue_count=len(_issues(continuity_report)),
        summary=str(getattr(continuity_report, "summary", "") or ""),
    )

    quality_axis = ReviewAxis(
        key="quality",
        label="质量",
        score=overall_score,
        status=_score_axis_status(
            overall_score,
            threshold=eval_threshold,
            passed=eval_passed,
        ),
        risk_level="low" if eval_passed else "high",
        issue_count=len(getattr(eval_report, "repair_suggestions", []) or []),
        summary=str(getattr(eval_report, "summary", "") or ""),
    )

    causal_blocking = _blocking_issues(causal_report, must_fix_severity=must_fix)
    causal_axis = ReviewAxis(
        key="causal",
        label="因果链",
        score=causal_score,
        status=(
            "unknown"
            if causal_report is None
            else ("fail" if causal_blocking else ("warn" if _issues(causal_report) else "pass"))
        ),
        risk_level=(
            "low"
            if causal_report is None
            else ("high" if causal_blocking else ("medium" if _issues(causal_report) else "low"))
        ),
        issue_count=len(_issues(causal_report)),
        blocking_issue_count=len(causal_blocking),
        repairable_issue_count=len(causal_blocking),
        summary=str(getattr(causal_report, "summary", "") or "") if causal_report else "",
    )

    rp_issues = _reading_power_issues(reading_power_report)
    rp_status = _score_axis_status(
        reading_power_score,
        threshold=reading_power_threshold,
        risk_level="medium" if rp_issues else "low",
    )
    if reading_power_report is None:
        rp_status = "unknown"
    reading_power_axis = ReviewAxis(
        key="reading_power",
        label="追读力",
        score=reading_power_score,
        status=rp_status,
        risk_level=(
            "low"
            if reading_power_report is None
            else (
                "high"
                if rp_status == "fail"
                else ("medium" if rp_issues or rp_status == "warn" else "low")
            )
        ),
        issue_count=len(rp_issues),
        blocking_issue_count=0,
        repairable_issue_count=len(rp_issues),
        summary=str(getattr(reading_power_report, "next_chapter_reason", "") or "")
        if reading_power_report is not None
        else "",
    )

    actionable_guard_warning = any(
        _guard_warning_is_actionable(item) for item in (warnings or ())
    )
    incomplete_guard_warning = any(
        _guard_warning_is_incomplete(item) for item in (warnings or ())
    )
    guard_repairable_count = sum(1 for ticket in (repair_tickets or ()) if _guard_ticket_actionable(ticket))
    guard_risk = str(getattr(guard_decision, "risk_level", "low") or "low").lower()
    guard_axis = ReviewAxis(
        key="guard",
        label="AI 护栏",
        status=(
            "fail"
            if guard_repairable_count or actionable_guard_warning
            else ("warn" if incomplete_guard_warning or guard_decision is not None else "pass")
        ),
        risk_level=_max_risk(guard_risk, "high" if guard_repairable_count else "low"),
        issue_count=guard_repairable_count + int(actionable_guard_warning),
        blocking_issue_count=guard_repairable_count,
        repairable_issue_count=guard_repairable_count + int(actionable_guard_warning),
        summary=str(getattr(guard_decision, "reasoning_brief", "") or ""),
    )

    axes = (
        alignment_axis,
        continuity_axis,
        quality_axis,
        causal_axis,
        reading_power_axis,
        guard_axis,
    )
    score_line = format_review_score_line(
        alignment_score=alignment_score,
        continuity_score=continuity_score,
        overall_score=overall_score,
        causal_score=causal_score,
        reading_power_score=reading_power_score,
    )

    allowed = set(DEFAULT_OPTION_IDS)
    recommended = OPTION_ACCEPT
    blocked_reason = ""

    if (
        prompt_leak_count
        or alignment_axis.repairable_issue_count
        or continuity_axis.blocking_issue_count
        or causal_axis.blocking_issue_count
        or reading_power_axis.repairable_issue_count
        or guard_axis.repairable_issue_count
    ):
        recommended = OPTION_APPLY_REPAIRS
        if prompt_leak_count:
            allowed.discard(OPTION_ACCEPT)
            blocked_reason = "检测到可自动清理的提示词/规划语句泄露，需先修复后归档。"

    if ambiguous_prompt_leak_count:
        recommended = OPTION_PAUSE
        allowed.discard(OPTION_ACCEPT)
        blocked_reason = "检测到疑似提示词/规划语句泄露，但无法确认；需人工复核后再归档。"

    if quality_axis.status == "fail":
        recommended = OPTION_PAUSE
        allowed.discard(OPTION_ACCEPT)
        blocked_reason = "质量评估未通过，当前不可直接归档。"

    if guard_decision is not None:
        decision = str(getattr(guard_decision, "decision", "") or "")
        if decision in {"adjust_outline_fast", "adjust_outline_smart"}:
            recommended = OPTION_ADJUST_OUTLINE
            allowed.discard(OPTION_ACCEPT)
            blocked_reason = "AI 护栏要求先处理后续大纲，当前不可直接归档。"
        elif decision in {"pause_for_human", "rollback_and_regen"}:
            recommended = OPTION_PAUSE
            allowed.discard(OPTION_ACCEPT)
            blocked_reason = "AI 护栏将本章标记为高风险，当前不可直接归档。"

    overall_risk = _max_risk(*(axis.risk_level for axis in axes))
    repair_description = _build_repair_description(
        alignment_axis=alignment_axis,
        continuity_report=continuity_report,
        causal_report=causal_report,
        reading_power_issues=rp_issues,
        guard_repairable_count=guard_repairable_count,
        guard_actionable_warning=actionable_guard_warning,
        prompt_leak_count=prompt_leak_count,
    )
    guard_verdict = (
        str(getattr(guard_decision, "decision", "") or "").strip()
        if guard_decision is not None
        else ""
    )
    if guard_verdict and guard_verdict != recommended:
        decision_line = f"AI 判定：{guard_verdict} / 建议 {recommended} / 风险 {overall_risk}。"
    else:
        decision_line = f"AI 判定：{recommended} / 风险 {overall_risk}。"
    if blocked_reason:
        decision_line = f"{decision_line} {blocked_reason}"

    return ChapterReviewMatrix(
        axes=axes,
        score_line=score_line,
        warnings=tuple(str(item) for item in (warnings or ()) if str(item).strip()),
        decision=ReviewDecision(
            recommended_option=recommended,
            allowed_option_ids=frozenset(allowed),
            risk_level=overall_risk,
            decision_line=decision_line,
            repair_option_description=repair_description,
            blocked_reason=blocked_reason,
        ),
    )


def alignment_requires_repair(
    alignment_report: Any,
    *,
    alignment_threshold: float = 7.0,
) -> bool:
    """Return True when the current finalize flow can run one alignment repair."""
    score = coerce_score(getattr(alignment_report, "alignment_score", None))
    if score is None or score >= alignment_threshold:
        return False
    return bool(
        getattr(alignment_report, "repair_actions", None)
        or getattr(alignment_report, "missing_main_points", None)
    )
