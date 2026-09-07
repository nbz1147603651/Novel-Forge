"""Tests for compact trace-step token extraction in result payloads."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.workspace.result_payloads import extract_trace_step_usage, extract_usage_metrics


def test_extract_trace_step_usage_reads_tokens_from_trace_summary() -> None:
    result = SimpleNamespace(
        trace_summary={
            "steps": [
                {
                    "name": "draft",
                    "tokens": 1800,
                    "prompt_tokens": 1200,
                    "completion_tokens": 600,
                    "cost": 0.01,
                    "model_call_count": 2,
                },
                {
                    "name": "evaluate",
                    "tokens": 900,
                    "prompt_tokens": 700,
                    "completion_tokens": 200,
                    "cost": 0.004,
                    "model_call_count": 1,
                },
            ]
        }
    )

    rows = extract_trace_step_usage(result)

    assert [item["step"] for item in rows] == ["draft", "evaluate"]
    assert rows[0]["tokens"] == 1800
    assert rows[0]["prompt_tokens"] == 1200
    assert rows[0]["completion_tokens"] == 600
    assert rows[0]["model_call_count"] == 2


def test_extract_trace_step_usage_falls_back_to_prompt_plus_completion() -> None:
    result = SimpleNamespace(
        trace_summary={
            "steps": [
                {
                    "name": "plan",
                    "tokens": 0,
                    "prompt_tokens": 450,
                    "completion_tokens": 350,
                    "cost": 0.0,
                },
                {
                    "name": "noop",
                    "tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                },
            ]
        }
    )

    rows = extract_trace_step_usage(result)

    assert len(rows) == 1
    assert rows[0]["step"] == "plan"
    assert rows[0]["tokens"] == 800


def test_extract_usage_metrics_prefers_trace_totals_when_available() -> None:
    result = SimpleNamespace(
        meta=SimpleNamespace(tokens_used=100, cost_usd=0.1),
        trace_summary={
            "total_tokens": 250,
            "total_cost_usd": 0.25,
        },
    )

    usage = extract_usage_metrics(result)

    assert usage.tokens_used == 250
    assert usage.cost_usd == 0.25
