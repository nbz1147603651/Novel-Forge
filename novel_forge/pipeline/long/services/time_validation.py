"""Time-constraint validation for chapter-level temporal consistency.

Detects common long-form serial errors:
- Time regression (backward jumps without flashback markers)
- Countdown discontinuities (D-5 → D-2 with no explanation)
- Large time gaps (>3 day-equivalents) without transition acknowledgement

All checks are rule-based and return structured violations that can be
surfaced in quality-check reports or injected into planning prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.obs.logger import get_logger

_log = get_logger("pipeline.long.services.time_validation")

# ── Countdown pattern: matches "D-N", "D-{N}", "第N天/共M天" ────────────
_COUNTDOWN_RE = re.compile(r"D-(\d+)", re.IGNORECASE)
_DAY_COUNTER_RE = re.compile(r"第\s*(\d+)\s*天")

# ── Time-gap magnitude heuristics ────────────────────────────────────────
_LARGE_GAP_KEYWORDS = frozenset(
    {
        "三天后",
        "数日后",
        "一周后",
        "数周后",
        "数月后",
        "半月后",
        "月余后",
        "多日后",
        "几天后",
        "翌年",
    }
)
_SKIP_KEYWORDS = frozenset({"次日", "翌日", "第二天", "隔天", "跨夜"})

# Transitional legacy fallback used by outline initialization and the
# chapter-planning preflight until the model-backed coherence compiler clears
# its shadow acceptance gates.  Do not extend this subject/number vocabulary:
# new semantic coverage belongs in the existing claim extraction/adjudication
# chain.  The fallback stays deliberately narrow so it cannot silently become
# a second domain ontology.
_DURATION_SUBJECTS = ("停职", "受审", "禁足", "隔离", "封锁")
_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass(frozen=True)
class TimeViolation:
    """A single temporal consistency violation."""

    severity: str  # "critical" | "warning"
    violation_type: str
    chapter: int
    message: str
    suggestion: str = ""


@dataclass
class TimeValidationReport:
    """Aggregated time-validation result for a chapter transition."""

    chapter: int = 0
    violations: list[TimeViolation] = field(default_factory=list)
    time_summary: str = ""

    @property
    def has_critical(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0


def _iter_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for item in value.values() for part in _iter_text(item)]
    if isinstance(value, (list, tuple, set)):
        return [part for item in value for part in _iter_text(item)]
    if hasattr(value, "model_dump"):
        try:
            return _iter_text(value.model_dump(mode="json"))
        except TypeError:
            return _iter_text(value.model_dump())
    if hasattr(value, "__dict__"):
        return _iter_text(vars(value))
    return [str(value)]


def _duration_number(value: str) -> int | None:
    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(value) == 1:
        return _CN_DIGITS.get(value)
    return None


def extract_explicit_duration_claims(value: Any) -> dict[str, dict[int, set[str]]]:
    """Extract the legacy fallback's explicit total-duration evidence.

    The negative ``第`` look-behind is intentional: ``停职第三日`` is a position
    inside a longer suspension, while ``三日停职结束`` asserts a three-day total.
    This helper is not a general semantic parser and must not grow new domain
    subjects or interpretations.
    """

    text = "\n".join(part for part in _iter_text(value) if part)
    number = r"(?P<number>[0-9零一二两三四五六七八九十]{1,3})"
    claims: dict[str, dict[int, set[str]]] = {}
    for subject in _DURATION_SUBJECTS:
        patterns = (
            rf"{re.escape(subject)}(?:期|期限|为期)?{number}(?:日|天)",
            rf"(?<!第){number}(?:日|天){re.escape(subject)}(?:期|期限|结束|期满)?",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                parsed = _duration_number(match.group("number"))
                if parsed is not None:
                    claims.setdefault(subject, {}).setdefault(parsed, set()).add(match.group(0))
    return claims


def find_internal_duration_claim_conflicts(value: Any) -> list[dict[str, Any]]:
    """Return conflicts proven by the narrow transitional fallback only."""

    conflicts: list[dict[str, Any]] = []
    for subject, by_day in extract_explicit_duration_claims(value).items():
        if len(by_day) <= 1:
            continue
        conflicts.append(
            {
                "subject": subject,
                "days": sorted(by_day),
                "evidence": {
                    str(day): sorted(phrases) for day, phrases in sorted(by_day.items())
                },
            }
        )
    return conflicts


def _extract_countdown_value(text: str) -> int | None:
    """Extract the leading countdown integer from text like 'D-5' or '第3天'."""
    m = _COUNTDOWN_RE.search(text)
    if m:
        return int(m.group(1))
    m = _DAY_COUNTER_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def _has_large_gap_hint(
    gap_text: str,
    *,
    large_gap_keywords: frozenset[str] | None = None,
) -> bool:
    """Heuristic: does the gap description imply >3 story-days?"""
    keywords = large_gap_keywords or _LARGE_GAP_KEYWORDS
    return any(kw in gap_text for kw in keywords)


def validate_time_consistency(
    current: ChapterOutline,
    previous: ChapterOutline | None,
    *,
    time_keywords: dict[str, frozenset[str]] | None = None,
) -> TimeValidationReport:
    """Validate temporal consistency between consecutive chapters.

    Args:
        current: The chapter being validated.
        previous: The immediately preceding chapter (None for chapter 1).
        time_keywords: Optional dict with keys 'large_gap_keywords' and 'skip_keywords'.

    Returns:
        TimeValidationReport with any detected violations.
    """
    large_gap_kw = (
        time_keywords.get("large_gap_keywords", _LARGE_GAP_KEYWORDS)
        if time_keywords
        else _LARGE_GAP_KEYWORDS
    )

    report = TimeValidationReport(chapter=current.chapter_number)
    violations: list[TimeViolation] = []

    if previous is None:
        report.time_summary = (
            f"Ch{current.chapter_number}: 首章，时间锚点={current.time_anchor or '未设定'}"
        )
        report.violations = violations
        return report

    # ── 1. Time regression check ─────────────────────────────────────────
    if current.time_anchor and previous.time_anchor and not current.is_flashback:
        cur_day = _extract_countdown_value(current.time_anchor)
        prev_day = _extract_countdown_value(previous.time_anchor)
        if cur_day is not None and prev_day is not None:
            # For "第N天" style: current day should >= previous day
            if _DAY_COUNTER_RE.search(current.time_anchor) and _DAY_COUNTER_RE.search(
                previous.time_anchor
            ):
                if cur_day < prev_day:
                    violations.append(
                        TimeViolation(
                            severity="critical",
                            violation_type="TIME_REGRESSION",
                            chapter=current.chapter_number,
                            message=(
                                f"时间回跳：第{previous.chapter_number}章为「{previous.time_anchor}」，"
                                f"第{current.chapter_number}章为「{current.time_anchor}」，"
                                f"但本章未标注闪回(is_flashback=False)"
                            ),
                            suggestion="若为闪回章节请设置 is_flashback=True，否则修正时间锚点。",
                        )
                    )

    # ── 2. Countdown continuity check ────────────────────────────────────
    if current.countdown_state and previous.countdown_state:
        cur_cd = _extract_countdown_value(current.countdown_state)
        prev_cd = _extract_countdown_value(previous.countdown_state)
        if cur_cd is not None and prev_cd is not None:
            # D-N format: value should decrease by exactly 1 per chapter
            if _COUNTDOWN_RE.search(current.countdown_state) and _COUNTDOWN_RE.search(
                previous.countdown_state
            ):
                expected = prev_cd - 1
                if cur_cd < expected:
                    violations.append(
                        TimeViolation(
                            severity="critical",
                            violation_type="COUNTDOWN_JUMP",
                            chapter=current.chapter_number,
                            message=(
                                f"倒计时跳跃：第{previous.chapter_number}章为 D-{prev_cd}，"
                                f"第{current.chapter_number}章为 D-{cur_cd}，"
                                f"跳过了 {prev_cd - cur_cd - 1} 个计数"
                            ),
                            suggestion="检查是否遗漏中间章节的倒计时推进，或补充说明跳跃原因。",
                        )
                    )

    # ── 3. Large time gap without transition ─────────────────────────────
    gap_text = current.time_gap_from_prev
    if gap_text and _has_large_gap_hint(gap_text, large_gap_keywords=large_gap_kw):
        violations.append(
            TimeViolation(
                severity="warning",
                violation_type="LARGE_TIME_GAP",
                chapter=current.chapter_number,
                message=(f"大跨度时间推进（{gap_text}），建议在章首补充过渡说明。"),
                suggestion="在开篇或过渡章中交代时间跨度内发生的关键事件。",
            )
        )

    report.violations = violations
    prev_anchor = previous.time_anchor or "未知"
    cur_anchor = current.time_anchor or "未知"
    gap = gap_text or "未标注"
    report.time_summary = (
        f"Ch{previous.chapter_number}({prev_anchor}) → "
        f"Ch{current.chapter_number}({cur_anchor})，间隔={gap}"
    )
    return report


def build_time_context_for_planning(
    current_outline: ChapterOutline,
    previous_outline: ChapterOutline | None,
) -> dict[str, str]:
    """Build a compact time-context dict for injection into planning prompts.

    Returns a dict with keys that can be directly merged into the template context.
    """
    ctx: dict[str, str] = {}
    if previous_outline:
        ctx["prev_time_anchor"] = previous_outline.time_anchor or "未标注"
        ctx["prev_countdown"] = previous_outline.countdown_state or "无"
    else:
        ctx["prev_time_anchor"] = "（首章）"
        ctx["prev_countdown"] = "无"

    ctx["current_time_anchor"] = current_outline.time_anchor or "待规划"
    ctx["current_time_span"] = current_outline.time_span or "待规划"
    ctx["time_gap_from_prev"] = current_outline.time_gap_from_prev or "待规划"
    ctx["countdown_state"] = current_outline.countdown_state or "无"
    ctx["is_flashback"] = "是" if current_outline.is_flashback else "否"
    return ctx
