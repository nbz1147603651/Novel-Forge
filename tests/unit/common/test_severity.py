"""Tests for novel_forge.common.severity canonical module."""

from __future__ import annotations

from novel_forge.common.severity import (
    SEVERITY_RANK,
    normalize_severity,
    severity_at_least,
)


class TestSeverityRank:
    """Tests for SEVERITY_RANK dictionary."""

    def test_has_all_eight_keys(self) -> None:
        """SEVERITY_RANK must contain all 8 canonical severity levels."""
        expected_keys = {"critical", "high", "major", "warning", "medium", "low", "minor", "info"}
        assert set(SEVERITY_RANK.keys()) == expected_keys

    def test_critical_is_highest(self) -> None:
        """Critical must have the highest rank value."""
        assert SEVERITY_RANK["critical"] == 5

    def test_high_rank(self) -> None:
        """High must have rank 4."""
        assert SEVERITY_RANK["high"] == 4

    def test_major_and_warning_equal(self) -> None:
        """Major and warning must have equal rank (both 3)."""
        assert SEVERITY_RANK["major"] == 3
        assert SEVERITY_RANK["warning"] == 3
        assert SEVERITY_RANK["major"] == SEVERITY_RANK["warning"]

    def test_medium_rank(self) -> None:
        """Medium must have rank 2."""
        assert SEVERITY_RANK["medium"] == 2

    def test_low_and_minor_equal(self) -> None:
        """Low and minor must have equal rank (both 1)."""
        assert SEVERITY_RANK["low"] == 1
        assert SEVERITY_RANK["minor"] == 1
        assert SEVERITY_RANK["low"] == SEVERITY_RANK["minor"]

    def test_info_is_lowest(self) -> None:
        """Info must have the lowest rank value (0)."""
        assert SEVERITY_RANK["info"] == 0

    def test_rank_ordering(self) -> None:
        """Verify the overall ordering: critical > high > major=warning > medium > low=minor > info."""
        assert SEVERITY_RANK["critical"] > SEVERITY_RANK["high"]
        assert SEVERITY_RANK["high"] > SEVERITY_RANK["major"]
        assert SEVERITY_RANK["major"] == SEVERITY_RANK["warning"]
        assert SEVERITY_RANK["warning"] > SEVERITY_RANK["medium"]
        assert SEVERITY_RANK["medium"] > SEVERITY_RANK["low"]
        assert SEVERITY_RANK["low"] == SEVERITY_RANK["minor"]
        assert SEVERITY_RANK["minor"] > SEVERITY_RANK["info"]


class TestNormalizeSeverity:
    """Tests for normalize_severity function."""

    def test_canonical_labels_unchanged(self) -> None:
        """All 8 canonical labels should normalize to themselves."""
        for label in ("critical", "high", "major", "warning", "medium", "low", "minor", "info"):
            assert normalize_severity(label) == label

    def test_case_insensitive(self) -> None:
        """Normalization should be case-insensitive."""
        assert normalize_severity("CRITICAL") == "critical"
        assert normalize_severity("High") == "high"
        assert normalize_severity("MEDIUM") == "medium"
        assert normalize_severity("Info") == "info"

    def test_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace should be stripped."""
        assert normalize_severity("  critical  ") == "critical"
        assert normalize_severity("\thigh\n") == "high"

    def test_none_returns_default(self) -> None:
        """None input should return a sensible default."""
        result = normalize_severity(None)
        assert result in SEVERITY_RANK

    def test_empty_string_returns_default(self) -> None:
        """Empty string should return a sensible default."""
        result = normalize_severity("")
        assert result in SEVERITY_RANK

    def test_unknown_label_returns_default(self) -> None:
        """Unknown labels should return a sensible default."""
        result = normalize_severity("unknown_severity")
        assert result in SEVERITY_RANK

    def test_unknown_label_can_use_explicit_default(self) -> None:
        """Callers with legacy fallback semantics can choose an explicit default."""
        assert normalize_severity("unknown_severity", default="info") == "info"
        assert normalize_severity(None, default="warning") == "warning"

    def test_invalid_explicit_default_falls_back_to_medium(self) -> None:
        """Invalid explicit defaults should not leak non-canonical labels."""
        assert normalize_severity("unknown_severity", default="not-a-level") == "medium"

    def test_numeric_string_returns_default(self) -> None:
        """Numeric strings should return a sensible default."""
        result = normalize_severity("42")
        assert result in SEVERITY_RANK


class TestSeverityAtLeast:
    """Tests for severity_at_least function."""

    def test_critical_meets_any_threshold(self) -> None:
        """Critical should meet any threshold."""
        for threshold in ("critical", "high", "major", "warning", "medium", "low", "minor", "info"):
            assert severity_at_least("critical", threshold) is True

    def test_info_meets_only_info(self) -> None:
        """Info should only meet info threshold."""
        assert severity_at_least("info", "info") is True
        assert severity_at_least("info", "low") is False
        assert severity_at_least("info", "medium") is False
        assert severity_at_least("info", "critical") is False

    def test_high_meets_medium(self) -> None:
        """High should meet medium threshold."""
        assert severity_at_least("high", "medium") is True

    def test_medium_does_not_meet_high(self) -> None:
        """Medium should not meet high threshold."""
        assert severity_at_least("medium", "high") is False

    def test_equal_severity_meets_threshold(self) -> None:
        """Same severity should meet its own threshold."""
        for level in ("critical", "high", "major", "warning", "medium", "low", "minor", "info"):
            assert severity_at_least(level, level) is True

    def test_major_equals_warning(self) -> None:
        """Major and warning have equal rank, so they should meet each other."""
        assert severity_at_least("major", "warning") is True
        assert severity_at_least("warning", "major") is True

    def test_low_equals_minor(self) -> None:
        """Low and minor have equal rank, so they should meet each other."""
        assert severity_at_least("low", "minor") is True
        assert severity_at_least("minor", "low") is True

    def test_case_insensitive_comparison(self) -> None:
        """Comparison should be case-insensitive."""
        assert severity_at_least("HIGH", "medium") is True
        assert severity_at_least("high", "MEDIUM") is True
        assert severity_at_least("CRITICAL", "HIGH") is True
