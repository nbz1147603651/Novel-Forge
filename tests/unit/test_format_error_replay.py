"""Tests for sanitized format-error replay fixtures."""

from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.replay_format_errors import replay_format_errors


def test_replay_format_errors_passes_sanitized_fixtures() -> None:
    results = asyncio.run(replay_format_errors(Path("tests/fixtures/format_errors")))

    assert results
    assert all(result.passed for result in results)
    assert {
        result.strategy for result in results
    } == {"plan_outline_fragment_array_sibling_repair"}
