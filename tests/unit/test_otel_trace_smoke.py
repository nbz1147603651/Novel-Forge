"""Smoke test for obs/otel_export.py — PipelineTrace to OTEL span mapping."""

from __future__ import annotations

from novel_forge.obs.otel_export import (
    assert_no_content_in_spans,
    is_otel_available,
    trace_to_span_names,
    trace_to_spans,
)
from novel_forge.obs.tracer import (
    ModelCallTrace,
    PipelineTrace,
)


def _build_trace() -> PipelineTrace:
    trace = PipelineTrace()
    with trace.step("draft") as step:
        step.record_model_call(
            ModelCallTrace(
                task="draft_chapter",
                provider="mock",
                model="gpt-4o",
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
                cost_usd=0.01,
                latency_ms=150.0,
            )
        )
    with trace.step("edit") as step:
        step.record_model_call(
            ModelCallTrace(
                task="edit_chapter",
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=50,
                completion_tokens=100,
                total_tokens=150,
                cost_usd=0.005,
                latency_ms=80.0,
            )
        )
    return trace


class TestTraceToSpans:
    def test_returns_one_span_per_step(self) -> None:
        trace = _build_trace()
        spans = trace_to_spans(trace)
        assert len(spans) == 2
        assert spans[0]["name"] == "draft"
        assert spans[1]["name"] == "edit"

    def test_span_attrs_contain_token_counts(self) -> None:
        trace = _build_trace()
        spans = trace_to_spans(trace)
        attrs = spans[0]["attrs"]
        assert "novel_forge.prompt_tokens" in attrs
        assert attrs["novel_forge.prompt_tokens"] == 100
        assert attrs["novel_forge.completion_tokens"] == 200
        assert attrs["novel_forge.cost_usd"] == 0.01

    def test_model_call_children_present(self) -> None:
        trace = _build_trace()
        spans = trace_to_spans(trace)
        assert len(spans[0]["children"]) == 1
        child = spans[0]["children"][0]
        assert child["name"] == "draft_chapter:mock:gpt-4o"
        assert child["attrs"]["novel_forge.task"] == "draft_chapter"
        assert child["attrs"]["novel_forge.provider"] == "mock"

    def test_span_names_preserve_order(self) -> None:
        trace = _build_trace()
        names = trace_to_span_names(trace)
        assert names == ["draft", "edit"]


class TestNoContentLeakage:
    def test_no_prompt_or_completion_in_attrs(self) -> None:
        trace = _build_trace()
        spans = trace_to_spans(trace)
        assert_no_content_in_spans(spans)

    def test_error_message_included_but_not_content(self) -> None:
        trace = PipelineTrace()
        with trace.step("failed_step") as step:
            step.success = False
            step.error = "Connection timeout"
        spans = trace_to_spans(trace)
        attrs = spans[0]["attrs"]
        assert attrs["novel_forge.error"] == "Connection timeout"
        assert_no_content_in_spans(spans)


class TestOTELAvailability:
    def test_is_otel_available_returns_bool(self) -> None:
        result = is_otel_available()
        assert isinstance(result, bool)

    def test_trace_to_spans_works_without_otel(self) -> None:
        trace = _build_trace()
        spans = trace_to_spans(trace)
        assert len(spans) == 2
