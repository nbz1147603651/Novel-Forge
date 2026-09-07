"""Tests for novel_forge.core.utils.coerce — coerce_float and siblings."""

from __future__ import annotations

from novel_forge.core.utils.coerce import coerce_float, coerce_int, coerce_optional_int

# ── coerce_float ──────────────────────────────────────────────────────────


class TestCoerceFloatBasicConversion:
    """Basic float conversion without clamping."""

    def test_string_float(self):
        assert coerce_float("3.14") == 3.14

    def test_string_int(self):
        assert coerce_float("42") == 42.0

    def test_float_passthrough(self):
        assert coerce_float(3.14) == 3.14

    def test_int_to_float(self):
        assert coerce_float(42) == 42.0

    def test_zero(self):
        assert coerce_float(0) == 0.0

    def test_negative(self):
        assert coerce_float("-2.5") == -2.5

    def test_boolean_true(self):
        assert coerce_float(True) == 1.0

    def test_boolean_false(self):
        assert coerce_float(False) == 0.0


class TestCoerceFloatDefault:
    """Default value returned on conversion failure."""

    def test_none_returns_default_zero(self):
        assert coerce_float(None) == 0.0

    def test_none_returns_custom_default(self):
        assert coerce_float(None, default=-1.0) == -1.0

    def test_empty_string_returns_default(self):
        assert coerce_float("", default=-1.0) == -1.0

    def test_empty_string_returns_default_zero(self):
        assert coerce_float("") == 0.0

    def test_unparseable_string(self):
        assert coerce_float("abc", default=-1.0) == -1.0

    def test_list_returns_default(self):
        assert coerce_float([], default=99.0) == 99.0


class TestCoerceFloatNoClampByDefault:
    """Default signature must NOT clamp — equivalence with private copies."""

    def test_large_value_not_clamped(self):
        assert coerce_float("999.9") == 999.9

    def test_negative_value_not_clamped(self):
        assert coerce_float("-50.0") == -50.0

    def test_above_unit_not_clamped(self):
        assert coerce_float(5.0) == 5.0

    def test_below_zero_not_clamped(self):
        assert coerce_float(-5.0) == -5.0


class TestCoerceFloatClampOptIn:
    """Clamp only applied when explicitly passed."""

    def test_clamp_upper(self):
        assert coerce_float(1.5, clamp=(0.0, 1.0)) == 1.0

    def test_clamp_lower(self):
        assert coerce_float(-0.5, clamp=(0.0, 1.0)) == 0.0

    def test_clamp_within_range(self):
        assert coerce_float(0.5, clamp=(0.0, 1.0)) == 0.5

    def test_clamp_custom_range(self):
        assert coerce_float(15.0, clamp=(0.0, 10.0)) == 10.0

    def test_clamp_custom_range_lower(self):
        assert coerce_float(-5.0, clamp=(0.0, 10.0)) == 0.0

    def test_clamp_with_default_on_failure(self):
        # When conversion fails, default is returned (not clamped)
        assert coerce_float(None, default=0.5, clamp=(0.0, 1.0)) == 0.5

    def test_clamp_none_explicit(self):
        assert coerce_float(5.0, clamp=None) == 5.0


# ── coerce_int ────────────────────────────────────────────────────────────


class TestCoerceInt:
    def test_string_int(self):
        assert coerce_int("42", 0) == 42

    def test_none_returns_default(self):
        assert coerce_int(None, -1) == -1

    def test_empty_string_returns_default(self):
        assert coerce_int("", -1) == -1

    def test_float_string_returns_default(self):
        # int("3.7") raises ValueError, so default is returned
        assert coerce_int("3.7", 0) == 0


# ── coerce_optional_int ──────────────────────────────────────────────────


class TestCoerceOptionalInt:
    def test_string_int(self):
        assert coerce_optional_int("42") == 42

    def test_none_returns_none(self):
        assert coerce_optional_int(None) is None

    def test_empty_string_returns_none(self):
        assert coerce_optional_int("") is None

    def test_unparseable_returns_none(self):
        assert coerce_optional_int("abc") is None
