"""Tests for the book-audit CLI helpers."""

from __future__ import annotations

import pytest
import typer

from novel_forge.cli.commands.maintenance import _parse_chapter_range


def test_parse_chapter_range_supports_commas_and_ranges() -> None:
    assert _parse_chapter_range("1, 3-5，7") == [1, 3, 4, 5, 7]


def test_parse_chapter_range_rejects_descending_ranges() -> None:
    with pytest.raises(typer.BadParameter, match="正序正整数"):
        _parse_chapter_range("5-3")
