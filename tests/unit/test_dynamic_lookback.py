"""Tests for dynamic lookback calculation."""

from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    _calculate_dynamic_lookback,
)


class TestCalculateDynamicLookback:
    """Test _calculate_dynamic_lookback formula and caps."""

    def test_base_case_no_factors(self):
        """All zeros returns base_lookback (8)."""
        result = _calculate_dynamic_lookback(0, 0, 0)
        assert result == 8

    def test_unresolved_questions_only(self):
        """5 unresolved questions: base(8) + min(5,3) = 11."""
        result = _calculate_dynamic_lookback(5, 0, 0)
        assert result == 11

    def test_all_factors_capped(self):
        """All factors maxed: base(8) + 3 + 2 + 2 = 15 (cap)."""
        result = _calculate_dynamic_lookback(15, 5, 5)
        assert result == 15

    def test_custom_base_and_max(self):
        """Custom base_lookback and max_lookback parameters."""
        result = _calculate_dynamic_lookback(2, 1, 1, base_lookback=10, max_lookback=12)
        assert result == 12

    def test_partial_factors(self):
        """Partial factors: base(8) + min(1,3) + min(1,2) + min(0,2) = 10."""
        result = _calculate_dynamic_lookback(1, 1, 0)
        assert result == 10

    def test_negative_values_reduce_below_base(self):
        """Negative values reduce result below base (formula is additive)."""
        result = _calculate_dynamic_lookback(-1, -1, -1)
        assert result == 5

    def test_max_lookback_cap_enforced(self):
        """Result never exceeds max_lookback."""
        result = _calculate_dynamic_lookback(100, 100, 100)
        assert result == 15

    def test_default_parameters(self):
        """Calling with no arguments returns base_lookback."""
        result = _calculate_dynamic_lookback()
        assert result == 8
