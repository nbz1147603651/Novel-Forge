"""Tests for TimeValidation service."""

from __future__ import annotations

from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.pipeline.long.services.time_validation import (
    TimeValidationReport,
    TimeViolation,
    build_time_context_for_planning,
    validate_time_consistency,
)


def _make_outline(
    chapter_number: int = 1,
    title: str = "测试章",
    *,
    time_anchor: str = "",
    time_span: str = "",
    time_gap_from_prev: str = "",
    countdown_state: str = "",
    is_flashback: bool = False,
) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=chapter_number,
        title=title,
        goal="测试用章节目标",
        time_anchor=time_anchor,
        time_span=time_span,
        time_gap_from_prev=time_gap_from_prev,
        countdown_state=countdown_state,
        is_flashback=is_flashback,
    )


# ── validate_time_consistency ────────────────────────────────────────────


def test_first_chapter_no_violations() -> None:
    ch1 = _make_outline(1, time_anchor="第1天")
    report = validate_time_consistency(ch1, None)
    assert report.is_clean
    assert report.chapter == 1
    assert "首章" in report.time_summary


def test_normal_day_progression_no_violation() -> None:
    prev = _make_outline(1, time_anchor="第1天")
    curr = _make_outline(2, time_anchor="第2天")
    report = validate_time_consistency(curr, prev)
    assert report.is_clean


def test_time_regression_detected() -> None:
    prev = _make_outline(1, time_anchor="第5天")
    curr = _make_outline(2, time_anchor="第3天")
    report = validate_time_consistency(curr, prev)
    assert report.has_critical
    regression = [v for v in report.violations if v.violation_type == "TIME_REGRESSION"]
    assert len(regression) == 1


def test_flashback_chapter_skips_regression_check() -> None:
    prev = _make_outline(1, time_anchor="第5天")
    curr = _make_outline(2, time_anchor="第1天", is_flashback=True)
    report = validate_time_consistency(curr, prev)
    regression = [v for v in report.violations if v.violation_type == "TIME_REGRESSION"]
    assert len(regression) == 0


def test_countdown_jump_detected() -> None:
    prev = _make_outline(1, countdown_state="D-10")
    curr = _make_outline(2, countdown_state="D-7")  # jumped by 3, expected D-9
    report = validate_time_consistency(curr, prev)
    assert report.has_critical
    jump = [v for v in report.violations if v.violation_type == "COUNTDOWN_JUMP"]
    assert len(jump) == 1


def test_countdown_normal_decrement_no_violation() -> None:
    prev = _make_outline(1, countdown_state="D-10")
    curr = _make_outline(2, countdown_state="D-9")  # normal -1
    report = validate_time_consistency(curr, prev)
    countdown_violations = [v for v in report.violations if v.violation_type == "COUNTDOWN_JUMP"]
    assert len(countdown_violations) == 0


def test_large_time_gap_warning() -> None:
    prev = _make_outline(1)
    curr = _make_outline(2, time_gap_from_prev="三天后")
    report = validate_time_consistency(curr, prev)
    gap_warnings = [v for v in report.violations if v.violation_type == "LARGE_TIME_GAP"]
    assert len(gap_warnings) == 1
    assert gap_warnings[0].severity == "warning"


def test_next_day_gap_no_large_gap_warning() -> None:
    prev = _make_outline(1)
    curr = _make_outline(2, time_gap_from_prev="次日")
    report = validate_time_consistency(curr, prev)
    gap_warnings = [v for v in report.violations if v.violation_type == "LARGE_TIME_GAP"]
    assert len(gap_warnings) == 0


def test_time_summary_format() -> None:
    prev = _make_outline(1, time_anchor="第1天")
    curr = _make_outline(2, time_anchor="第2天", time_gap_from_prev="次日")
    report = validate_time_consistency(curr, prev)
    assert "Ch1" in report.time_summary
    assert "Ch2" in report.time_summary


def test_report_is_clean_property() -> None:
    report = TimeValidationReport(chapter=1)
    assert report.is_clean
    report.violations.append(
        TimeViolation(
            severity="warning",
            violation_type="LARGE_TIME_GAP",
            chapter=1,
            message="test",
        )
    )
    assert not report.is_clean


def test_report_has_critical_only_for_critical_severity() -> None:
    report = TimeValidationReport(chapter=1)
    report.violations.append(
        TimeViolation(
            severity="warning",
            violation_type="LARGE_TIME_GAP",
            chapter=1,
            message="test",
        )
    )
    assert not report.has_critical
    report.violations.append(
        TimeViolation(
            severity="critical",
            violation_type="TIME_REGRESSION",
            chapter=1,
            message="critical test",
        )
    )
    assert report.has_critical


# ── build_time_context_for_planning ─────────────────────────────────────


def test_build_time_context_first_chapter() -> None:
    curr = _make_outline(1, time_anchor="第1天", time_span="一天")
    ctx = build_time_context_for_planning(curr, None)
    assert ctx["prev_time_anchor"] == "（首章）"
    assert ctx["current_time_anchor"] == "第1天"
    assert ctx["is_flashback"] == "否"
    assert ctx["prev_countdown"] == "无"


def test_build_time_context_with_previous() -> None:
    prev = _make_outline(1, time_anchor="第3天", countdown_state="D-5")
    curr = _make_outline(
        2,
        time_anchor="第4天",
        time_span="一天",
        time_gap_from_prev="次日",
        countdown_state="D-4",
        is_flashback=False,
    )
    ctx = build_time_context_for_planning(curr, prev)
    assert ctx["prev_time_anchor"] == "第3天"
    assert ctx["prev_countdown"] == "D-5"
    assert ctx["current_time_anchor"] == "第4天"
    assert ctx["countdown_state"] == "D-4"
    assert ctx["time_gap_from_prev"] == "次日"
    assert ctx["is_flashback"] == "否"


def test_build_time_context_flashback_chapter() -> None:
    prev = _make_outline(5, time_anchor="第10天")
    curr = _make_outline(6, time_anchor="第1天", is_flashback=True)
    ctx = build_time_context_for_planning(curr, prev)
    assert ctx["is_flashback"] == "是"


def test_build_time_context_unset_fields_return_placeholder() -> None:
    curr = _make_outline(1)  # no time_anchor, no time_span, etc.
    ctx = build_time_context_for_planning(curr, None)
    assert ctx["current_time_anchor"] == "待规划"
    assert ctx["current_time_span"] == "待规划"
    assert ctx["time_gap_from_prev"] == "待规划"
