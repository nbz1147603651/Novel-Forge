"""Tests for CLI failure trace summary helper."""

from __future__ import annotations

from novel_forge.cli.main import _build_error_trace_summary


def test_build_error_trace_summary_with_steps() -> None:
    summary = _build_error_trace_summary(["spec", "init_story_bible"])

    assert summary is not None
    assert summary["completed_step_count"] == 2
    assert summary["last_step"] == "init_story_bible"


def test_build_error_trace_summary_without_steps() -> None:
    assert _build_error_trace_summary([]) is None
