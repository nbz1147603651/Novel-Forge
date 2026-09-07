"""Tests for desktop job trace-summary extraction used by run logger."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.desktop import jobs as desktop_jobs


def test_extract_trace_summary_from_execution_result_nested_payload() -> None:
    nested_trace = {"total_tokens": 62843, "step_count": 3}
    execution_result = SimpleNamespace(
        project_id="demo",
        result=SimpleNamespace(trace_summary=nested_trace),
    )

    assert desktop_jobs._extract_trace_summary(execution_result) == nested_trace


def test_extract_trace_summary_prefers_direct_trace_when_available() -> None:
    direct_trace = {"total_tokens": 1200}
    result = SimpleNamespace(trace_summary=direct_trace)

    assert desktop_jobs._extract_trace_summary(result) == direct_trace
