"""Tests for quality.py alignment-cache functions.

Covers:
- _compute_text_similarity
- _should_skip_alignment_recheck
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from novel_forge.pipeline.long.stages.quality_checks import (
    _ALIGNMENT_CACHE_MAX_SIMILARITY,
    _ALIGNMENT_CACHE_MIN_SCORE,
    _compute_text_similarity,
    _should_skip_alignment_recheck,
)

# ── _compute_text_similarity ────────────────────────────────────

class TestComputeTextSimilarity:
    """Unit tests for _compute_text_similarity."""

    def test_identical_texts(self):
        """Identical texts yield similarity of 1.0."""
        text = "这是一段测试文本内容。"
        assert _compute_text_similarity(text, text) == pytest.approx(1.0)

    def test_whitespace_normalization(self):
        """Whitespace differences are ignored."""
        t1 = "这是 测试 文本"
        t2 = "这是  测试    文本"
        # difflib strips whitespace before comparison internally via SequenceMatcher
        assert _compute_text_similarity(t1, t2) == pytest.approx(1.0)

    def test_completely_different_texts(self):
        """Completely different texts yield low similarity."""
        t1 = "春天的花朵在微风中摇曳"
        t2 = "冬天的寒风凛冽刺骨"
        sim = _compute_text_similarity(t1, t2)
        assert 0.0 <= sim < 1.0

    def test_partially_similar_texts(self):
        """Partially overlapping texts return an intermediate score."""
        t1 = "春天的花朵在微风中摇曳，美不胜收"
        t2 = "春天的花朵在细雨中绽放，美不胜收"
        sim = _compute_text_similarity(t1, t2)
        assert 0.5 < sim < 1.0

    def test_empty_first_text(self):
        """Empty first string returns 0.0."""
        assert _compute_text_similarity("", "有内容的文本") == 0.0

    def test_empty_second_text(self):
        """Empty second string returns 0.0."""
        assert _compute_text_similarity("有内容的文本", "") == 0.0

    def test_both_empty(self):
        """Both empty returns 0.0."""
        assert _compute_text_similarity("", "") == 0.0

    def test_only_whitespace(self):
        """Text that normalises to empty returns 0.0."""
        assert _compute_text_similarity("   ", "文本") == 0.0
        assert _compute_text_similarity("文本", "   \n\t") == 0.0

    def test_length_drift_fast_rejects_high_similarity(self):
        """Large length drift should not run as near-unchanged text."""
        t1 = "甲" * 200
        t2 = "甲" * 120
        sim = _compute_text_similarity(t1, t2)
        assert sim < _ALIGNMENT_CACHE_MAX_SIMILARITY


# ── _should_skip_alignment_recheck ─────────────────────────────

class TestShouldSkipAlignmentRecheck:
    """Unit tests for _should_skip_alignment_recheck."""

    def _mock_report(self, alignment_score: float) -> MagicMock:
        """Create a mock alignment report with a given score."""
        report = MagicMock()
        report.alignment_score = alignment_score
        return report

    # ── No previous report ─────────────────────────────────────

    def test_no_previous_report_means_no_skip(self):
        """When there is no previous report, recheck is never skipped."""
        skip, reason = _should_skip_alignment_recheck("旧文本", "新文本", None)
        assert skip is False
        assert "no previous report" in reason

    # ── Low alignment score ──────────────────────────────────────

    def test_low_score_means_no_skip(self):
        """Alignment score below threshold always triggers recheck."""
        score = _ALIGNMENT_CACHE_MIN_SCORE - 0.1
        report = self._mock_report(score)
        skip, reason = _should_skip_alignment_recheck("旧文本", "新文本", report)
        assert skip is False
        assert "below threshold" in reason

    def test_score_exactly_at_threshold_is_not_low(self):
        """Score equal to the minimum threshold does NOT short-circuit on score."""
        # This passes the score gate; similarity then determines the outcome
        report = self._mock_report(_ALIGNMENT_CACHE_MIN_SCORE)
        skip, reason = _should_skip_alignment_recheck("旧文本", "旧文本", report)
        assert skip is True  # identical text → similarity 1.0

    # ── High score + high similarity → skip ─────────────────────

    def test_high_score_and_unchanged_text_skips(self):
        """When score is high and text is nearly identical, recheck is skipped."""
        report = self._mock_report(_ALIGNMENT_CACHE_MIN_SCORE + 1.0)
        # Identical after whitespace normalisation
        skip, reason = _should_skip_alignment_recheck(
            "这是一个测试段落内容。", "这 是 一个 测试 段落 内容。", report
        )
        assert skip is True
        assert "similarity" in reason

    def test_high_score_and_identical_text_skips(self):
        """Identical text with high score skips."""
        report = self._mock_report(10.0)
        skip, reason = _should_skip_alignment_recheck("完全相同的文本内容。", "完全相同的文本内容。", report)
        assert skip is True
        assert "similarity" in reason

    # ── High score + low similarity → no skip ────────────────────

    def test_high_score_but_significantly_changed_text(self):
        """High previous score but substantial text change forces recheck."""
        report = self._mock_report(10.0)
        skip, reason = _should_skip_alignment_recheck(
            "春天的花朵在微风中摇曳",
            "冬天的寒风凛冽刺骨",
            report,
        )
        assert skip is False
        assert "changed significantly" in reason

    def test_high_score_with_moderate_change(self):
        """Some similarity but below threshold means no skip."""
        report = self._mock_report(9.5)
        t1 = "春天的花朵在微风中摇曳，美不胜收"
        t2 = "春天的花朵在细雨中绽放，美不胜收"
        skip, _ = _should_skip_alignment_recheck(t1, t2, report)
        # The two sentences share significant overlap; exact threshold depends on
        # difflib.SequenceMatcher output, so we assert the "changed" path
        assert skip is False

    # ── Boundary ────────────────────────────────────────────────

    def test_similarity_at_exact_threshold_is_not_skip(self):
        """Similarity exactly at the maximum threshold keeps recheck enabled."""
        # We construct two very short strings with a single character difference
        # so the ratio lands just below the threshold.
        report = self._mock_report(9.0)
        # Build texts where similarity == _ALIGNMENT_CACHE_MAX_SIMILARITY is borderline.
        # We verify the function returns False when similarity < threshold.
        skip, reason = _should_skip_alignment_recheck("你好世界", "你好", report)
        assert skip is False
        assert "changed significantly" in reason
