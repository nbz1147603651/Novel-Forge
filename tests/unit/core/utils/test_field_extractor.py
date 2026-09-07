"""Tests for novel_forge.core.utils.field_extractor."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from novel_forge.core.utils.field_extractor import field

# ---------------------------------------------------------------------------
# Basic dict access
# ---------------------------------------------------------------------------


class TestBasicDictAccess:
    """field() on flat dicts."""

    def test_simple_key(self) -> None:
        assert field({"a": 1}, "a") == 1

    def test_missing_key_returns_default(self) -> None:
        assert field({"a": 1}, "x", default="missing") == "missing"

    def test_missing_key_default_none(self) -> None:
        assert field({"a": 1}, "x") is None

    def test_value_is_none_returns_none_not_default(self) -> None:
        """Explicit None value should be returned, not the default."""
        assert field({"a": None}, "a", default="fallback") is None

    def test_empty_dict(self) -> None:
        assert field({}, "a", default=42) == 42


# ---------------------------------------------------------------------------
# Nested dict access via dot path
# ---------------------------------------------------------------------------


class TestNestedDictAccess:
    """field() with dot-separated paths."""

    def test_two_level(self) -> None:
        assert field({"a": {"b": 2}}, "a.b") == 2

    def test_three_level(self) -> None:
        assert field({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_nested_missing_intermediate_returns_default(self) -> None:
        assert field({"a": {"b": 2}}, "a.x.y", default="nope") == "nope"

    def test_nested_missing_leaf_returns_default(self) -> None:
        assert field({"a": {"b": 2}}, "a.b.c", default=-1) == -1

    def test_intermediate_not_dict_returns_default(self) -> None:
        """If an intermediate value is a scalar, can't descend further."""
        assert field({"a": 1}, "a.b", default="bad") == "bad"


# ---------------------------------------------------------------------------
# Object attribute access (backward compat with existing _field copies)
# ---------------------------------------------------------------------------


class TestObjectAccess:
    """field() on arbitrary objects via getattr."""

    def test_simple_attr(self) -> None:
        obj = SimpleNamespace(name="alice")
        assert field(obj, "name") == "alice"

    def test_missing_attr_returns_default(self) -> None:
        obj = SimpleNamespace(name="alice")
        assert field(obj, "age", default=0) == 0

    def test_nested_object_attr(self) -> None:
        inner = SimpleNamespace(value=99)
        outer = SimpleNamespace(inner=inner)
        assert field(outer, "inner.value") == 99

    def test_dataclass_access(self) -> None:
        @dataclass
        class Point:
            x: int
            y: int

        p = Point(3, 4)
        assert field(p, "x") == 3
        assert field(p, "y") == 4


# ---------------------------------------------------------------------------
# Mixed dict + object access
# ---------------------------------------------------------------------------


class TestMixedAccess:
    """Dot paths crossing dict ↔ object boundaries."""

    def test_dict_then_object(self) -> None:
        obj = SimpleNamespace(val="hello")
        data = {"nested": obj}
        assert field(data, "nested.val") == "hello"

    def test_object_then_dict(self) -> None:
        obj = SimpleNamespace(data={"key": "found"})
        assert field(obj, "data.key") == "found"


# ---------------------------------------------------------------------------
# Type casting
# ---------------------------------------------------------------------------


class TestTypeCast:
    """Optional type_cast keyword."""

    def test_cast_str_to_int(self) -> None:
        assert field({"a": "42"}, "a", type_cast=int) == 42

    def test_cast_int_to_str(self) -> None:
        assert field({"a": 42}, "a", type_cast=str) == "42"

    def test_cast_float_to_int(self) -> None:
        assert field({"a": 3.7}, "a", type_cast=int) == 3

    def test_cast_failure_returns_default(self) -> None:
        assert field({"a": "not_a_number"}, "a", default=0, type_cast=int) == 0

    def test_cast_none_value_returns_default(self) -> None:
        """Casting None should return default, not crash."""
        assert field({"a": None}, "a", default=-1, type_cast=int) == -1

    def test_no_cast_returns_raw(self) -> None:
        assert field({"a": "42"}, "a") == "42"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary / edge-case behavior."""

    def test_single_segment_path(self) -> None:
        """No dots — same as simple key."""
        assert field({"x": 10}, "x") == 10

    def test_bool_value_not_confused_with_missing(self) -> None:
        assert field({"flag": False}, "flag") is False
        assert field({"flag": False}, "flag", default=True) is False

    def test_zero_value_not_confused_with_missing(self) -> None:
        assert field({"n": 0}, "n") == 0
        assert field({"n": 0}, "n", default=99) == 0

    def test_empty_string_value(self) -> None:
        assert field({"s": ""}, "s") == ""
        assert field({"s": ""}, "s", default="fallback") == ""

    def test_list_value_returned_as_is(self) -> None:
        data = {"items": [1, 2, 3]}
        assert field(data, "items") == [1, 2, 3]
