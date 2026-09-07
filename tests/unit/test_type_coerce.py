"""Tests for stringify_text_value in novel_forge.core.utils.type_coerce."""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.utils.type_coerce import coerce_text_list, stringify_text_value


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        # None / empty → ""
        (None, ""),
        ("", ""),
        ([], ""),
        ({}, ""),
        # str pass-through (strip)
        ("hello", "hello"),
        ("  spaced  ", "spaced"),
        ("  ", ""),
        # int / float / bool → str
        (42, "42"),
        (0, "0"),
        (-1, "-1"),
        (3.14, "3.14"),
        (0.0, "0.0"),
        (True, "True"),
        (False, "False"),
        # list with flat items
        (["a", "b"], "a；b"),
        (["a"], "a"),
        # list skips empty/None items
        (["a", None, "b"], "a；b"),
        (["a", "", "b"], "a；b"),
        (["a", [], "b"], "a；b"),
        (["a", {}, "b"], "a；b"),
        # dict flat
        ({"k1": "v1", "k2": "v2"}, "k1: v1；k2: v2"),
        # dict skips empty/None items
        ({"k1": "v1", "k2": None}, "k1: v1"),
        ({"k1": "v1", "k2": ""}, "k1: v1"),
        ({"k1": "v1", "k2": []}, "k1: v1"),
        ({"k1": "v1", "k2": {}}, "k1: v1"),
        # nested dict → recursive key: value
        ({"nested": {"deep": "val"}}, "nested: deep: val"),
        # list of dicts
        ([{"a": "1"}, {"b": "2"}], "a: 1；b: 2"),
        # empty after filtering
        ([None, None, None], ""),
        ({"k1": None, "k2": ""}, ""),
    ],
)
def test_stringify_text_value(input_val: Any, expected: str) -> None:
    assert stringify_text_value(input_val) == expected


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        (None, []),
        ("", []),
        ([], []),
        ({}, []),
        ("  hello  ", ["hello"]),
        (42, ["42"]),
        (True, ["True"]),
        (["a", "b"], ["a", "b"]),
        (["a", None, "b"], ["a", "b"]),
        ([{"a": "1"}, {"b": "2"}], ["a: 1", "b: 2"]),
        ({"k": "v"}, ["k: v"]),
        ({"nested": {"deep": "val"}}, ["nested: deep: val"]),
    ],
)
def test_coerce_text_list(input_val: Any, expected: list[str]) -> None:
    assert coerce_text_list(input_val) == expected
