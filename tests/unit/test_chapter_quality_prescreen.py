"""Unit tests for ChapterQualityPrescreen — deterministic format/stylistic gate.

Catches Ch20-style collapses (双句号/双句号, length floor/ceiling, repeated
characters, empty chapter tails) that previously slipped past eval and into
publication. Driven from `scripts/post_format_check.py` for offline auditing
and from the chapter flow orchestrator for runtime gating.
"""

from __future__ import annotations

import pytest

from novel_forge.pipeline.steps.chapter_quality_prescreen import (
    ChapterQualityPrescreen,
    PrescreenHit,
)

# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------


class TestDefaultThresholds:
    def test_defaults_expose_documented_thresholds(self) -> None:
        p = ChapterQualityPrescreen()
        assert p.min_chars == 4000
        assert p.max_chars == 12000

    def test_thresholds_can_be_overridden(self) -> None:
        p = ChapterQualityPrescreen(min_chars=2000, max_chars=30000)
        assert p.min_chars == 2000
        assert p.max_chars == 30000

    def test_quote_balance_check_is_opt_in(self) -> None:
        p = ChapterQualityPrescreen()
        assert p.check_quote_balance is False


# ---------------------------------------------------------------------------
# Double-punctuation detection
# ---------------------------------------------------------------------------


class TestDoublePunctuation:
    """The exact 8 double-period issue observed in 山风与归人2 Ch20."""

    def test_detects_double_period(self) -> None:
        text = "清晨的阳光落在老街上。她被风压住的风铃声引着，那声音闷闷的。。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=20)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" in kinds

    def test_detects_double_comma(self) -> None:
        text = "她点了点头，，又把摄像机挂在肩上。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" in kinds

    def test_detects_double_ideographic_comma(self) -> None:
        text = "风铃、摄像机、、还有门口潮湿的台阶，都留在镜头外。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" in kinds

    def test_detects_period_followed_by_exclaim(self) -> None:
        text = "她停下。！又继续走。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=3)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" in kinds

    def test_does_not_trigger_on_normal_punctuation(self) -> None:
        text = "她停下。风穿过门缝。她没有说话。她把摄像机挂在肩上。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=4)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" not in kinds

    def test_does_not_treat_closing_quote_as_double_punctuation(self) -> None:
        text = "她说：「我听见风铃响了。」"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=4)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" not in kinds
        assert "unbalanced_quote" not in kinds

    def test_severity_is_high(self) -> None:
        text = "她停下。。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=1)
        hits = [h for h in report.hits if h.kind == "double_punctuation"]
        assert hits and hits[0].severity == "high"

    def test_span_metadata_is_recorded(self) -> None:
        text = "她停下。。继续走。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=1)
        hits = [h for h in report.hits if h.kind == "double_punctuation"]
        assert hits
        h = hits[0]
        assert h.span is not None
        start, end = h.span
        assert text[start:end] == "。。"


# ---------------------------------------------------------------------------
# Length floor / ceiling
# ---------------------------------------------------------------------------


class TestLengthFloor:
    """Ch15's 4,952-char collapse is below the default floor; should be flagged."""

    def test_flags_below_min_chars(self) -> None:
        text = "。" * 3000  # 3000 chars, below 4000 default
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=15)
        kinds = [h.kind for h in report.hits]
        assert "length_floor" in kinds

    def test_length_floor_severity_is_critical(self) -> None:
        text = "。" * 2000
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=15)
        hits = [h for h in report.hits if h.kind == "length_floor"]
        assert hits and hits[0].severity == "critical"

    def test_at_threshold_passes(self) -> None:
        text = "。" * 4000
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=10)
        kinds = [h.kind for h in report.hits]
        assert "length_floor" not in kinds

    def test_above_floor_no_hit(self) -> None:
        text = "她停下了。" * 1000  # ~5000 chars
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=10)
        kinds = [h.kind for h in report.hits]
        assert "length_floor" not in kinds


class TestLengthCeiling:
    """Chapter bloat guard — protect against runaway length."""

    def test_flags_above_max_chars(self) -> None:
        text = "。" * 13000
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        kinds = [h.kind for h in report.hits]
        assert "length_ceiling" in kinds

    def test_length_ceiling_severity_is_medium(self) -> None:
        text = "。" * 20000
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        hits = [h for h in report.hits if h.kind == "length_ceiling"]
        assert hits and hits[0].severity == "medium"


# ---------------------------------------------------------------------------
# Repeated character detection
# ---------------------------------------------------------------------------


class TestRepeatedCharacter:
    """Ch20 contains 「她她绕开」「看看看着」 style repeated-character errors."""

    def test_detects_triple_same_char(self) -> None:
        text = "她她她站在那里，看着他走远。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=20)
        kinds = [h.kind for h in report.hits]
        assert "repeated_char" in kinds

    def test_does_not_trigger_on_double_char(self) -> None:
        # Double characters are common Chinese patterns like 「看看」「想想」
        text = "她看看摄像机，又想想林正的话。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=3)
        kinds = [h.kind for h in report.hits]
        assert "repeated_char" not in kinds

    def test_does_not_trigger_on_ascii_ellipsis(self) -> None:
        text = "她停在门口...没有立刻进去。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=3)
        kinds = [h.kind for h in report.hits]
        assert "repeated_char" not in kinds

    def test_does_not_trigger_on_blank_lines(self) -> None:
        text = "第一段。\n\n\n第二段。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=3)
        repeated = [h for h in report.hits if h.kind == "repeated_char"]
        assert not repeated

    def test_severity_is_high(self) -> None:
        text = "她她她站在那里。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=20)
        hits = [h for h in report.hits if h.kind == "repeated_char"]
        assert hits and hits[0].severity == "high"


# ---------------------------------------------------------------------------
# Quote balance detection
# ---------------------------------------------------------------------------


class TestQuoteBalance:
    """Unbalanced Chinese quote marks are formatting hits, not auto-fixes."""

    def test_flags_unmatched_left_quote(self) -> None:
        text = "她说：「我听见风铃响了。"
        report = ChapterQualityPrescreen(check_quote_balance=True).prescreen(
            text, chapter_number=8
        )
        hits = [h for h in report.hits if h.kind == "unbalanced_quote"]
        assert hits
        assert hits[0].severity == "high"

    def test_flags_unmatched_right_quote(self) -> None:
        text = "她听见风铃响了。」"
        report = ChapterQualityPrescreen(check_quote_balance=True).prescreen(
            text, chapter_number=8
        )
        hits = [h for h in report.hits if h.kind == "unbalanced_quote"]
        assert hits
        assert hits[0].span is not None

    def test_accepts_nested_quote_pairs(self) -> None:
        text = "她说：「他说『风会回来』，我信了。」"
        report = ChapterQualityPrescreen(check_quote_balance=True).prescreen(
            text, chapter_number=8
        )
        assert "unbalanced_quote" not in {h.kind for h in report.hits}

    def test_default_prescreen_allows_open_quote_dialogue_style(self) -> None:
        text = "她停了一下。\n\n“我听见风铃响了。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=8)
        assert "unbalanced_quote" not in {h.kind for h in report.hits}


# ---------------------------------------------------------------------------
# Empty / blank chapter tail
# ---------------------------------------------------------------------------


class TestEmptyTail:
    """Chapters ending in whitespace only violate chapter_end_required hooks."""

    def test_flags_chapter_ending_in_newlines(self) -> None:
        text = "最后一行的正文。" + "\n" * 5
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        kinds = [h.kind for h in report.hits]
        assert "empty_tail" in kinds

    def test_does_not_flag_normal_ending(self) -> None:
        text = "最后一行的正文。"
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        kinds = [h.kind for h in report.hits]
        assert "empty_tail" not in kinds


# ---------------------------------------------------------------------------
# Report-level properties
# ---------------------------------------------------------------------------


class TestReportProperties:
    def test_passed_true_when_no_critical(self) -> None:
        text = "她停下。" * 1000  # ~5000 chars, no formatting issues
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        assert report.passed is True

    def test_passed_false_on_critical(self) -> None:
        text = "。" * 2000  # critical length_floor
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=15)
        assert report.passed is False

    def test_passed_false_on_formatting(self) -> None:
        # length fine but double-period is high (not critical) — passed stays True
        text = "她停下。。" + "正常段落。" * 1000  # ~5000+ chars
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=5)
        # high-severity non-critical still passes overall
        assert report.passed is True

    def test_passed_false_on_high_and_critical(self) -> None:
        text = "她停下。。" + "。" * 2000  # both double-period AND length_floor
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=20)
        assert report.passed is False

    def test_char_count_recorded(self) -> None:
        text = "她停下。" * 500  # 2000 chars
        report = ChapterQualityPrescreen().prescreen(text, chapter_number=1)
        assert report.char_count == 2000

    def test_chapter_number_recorded(self) -> None:
        report = ChapterQualityPrescreen().prescreen("她停下。" * 1000, chapter_number=7)
        assert report.chapter_number == 7


# ---------------------------------------------------------------------------
# 山风与归人2 sample regressions
# ---------------------------------------------------------------------------


SHANFENG_CH20_EXCERPT = (
    "清晨的阳光落在老街上，把青石板的每一道裂缝都晒得发白。"
    "沈鹿溪从民宿出来时，影子在身前拉成一道长线。。"
    "她走过风铃下方，铜片相互碰撞的声音闷闷的——她被风压住的风铃声引着，那声音闷闷的。。"
)


class TestShanfengCh20Regression:
    """Reproduce the Ch20 collapse from the existing project."""

    def test_ch20_excerpt_has_double_periods(self) -> None:
        report = ChapterQualityPrescreen().prescreen(SHANFENG_CH20_EXCERPT, chapter_number=20)
        kinds = [h.kind for h in report.hits]
        assert "double_punctuation" in kinds


# ---------------------------------------------------------------------------
# apply_format_fixes helper
# ---------------------------------------------------------------------------


class TestApplyFormatFixes:
    """The runtime/inline fix path for non-critical formatting hits."""

    def test_collapses_double_period(self) -> None:
        text = "她停下。。继续走。"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert "。。" not in fixed
        assert "她停下。继续走。" in fixed

    def test_collapses_double_comma(self) -> None:
        text = "她点点头，，把摄像机挂在肩上。"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert "，，" not in fixed
        assert fixed == "她点点头，把摄像机挂在肩上。"

    def test_collapses_repeated_punctuation_run_once(self) -> None:
        text = "她停下。。。继续走。"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert fixed == "她停下。继续走。"

    def test_collapses_triple_repeated_char(self) -> None:
        text = "她她她站在那里。"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert "她她她" not in fixed
        assert "她" in fixed  # retains a single occurrence

    def test_does_not_remove_closing_quote_after_sentence_punctuation(self) -> None:
        text = "她说：「我听见风铃响了。」"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert fixed == text

    def test_does_not_remove_curly_closing_quote_after_sentence_punctuation(self) -> None:
        text = "她说：“我听见风铃响了。”"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert fixed == text

    def test_does_not_remove_corner_closing_quote_after_question_mark(self) -> None:
        text = "她问：「你听见风了吗？」"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert fixed == text

    def test_does_not_collapse_blank_lines(self) -> None:
        text = "第一段。\n\n\n第二段。"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert fixed == text

    def test_does_not_touch_normal_text(self) -> None:
        text = "她停下。风穿过门缝。她没有说话。"
        fixed = ChapterQualityPrescreen.apply_format_fixes(text)
        assert fixed == text


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_prescreen_hit_is_frozen(self) -> None:
        hit = PrescreenHit(
            kind="double_punctuation",
            severity="high",
            span=(0, 2),
            quote="。。",
            fix_suggestion="删除重复标点",
        )
        with pytest.raises((AttributeError, Exception)):
            hit.severity = "low"  # type: ignore[misc]

    def test_prescreen_report_includes_metadata(self) -> None:
        report = ChapterQualityPrescreen().prescreen("她停下。" * 1000, chapter_number=3)
        assert report.chapter_number == 3
        assert isinstance(report.char_count, int)
        # hits is a tuple (frozen dataclass); consumers should iterate, not mutate.
        assert isinstance(report.hits, (list, tuple))
        assert isinstance(report.passed, bool)
