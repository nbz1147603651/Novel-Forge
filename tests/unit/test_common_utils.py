"""Regression tests for common normalization helpers."""

from __future__ import annotations

import pytest

from novel_forge.common.utils import normalize_gender_value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("男", "男"),
        ("male", "男"),
        ("M", "男"),
        ("女", "女"),
        ("female", "女"),
        ("F", "女"),
        (None, ""),
        ("", ""),
        ("unknown", ""),
        ("非二元", ""),
    ],
)
def test_normalize_gender_value_accepts_only_canonical_values(raw: object, expected: str) -> None:
    """Unknown LLM labels must not be persisted as character identity facts."""
    assert normalize_gender_value(raw) == expected
