"""Minimal OTEL span mapping for PipelineTrace / StepTrace.

Maps the existing in-process trace structures to OpenTelemetry spans so that
refactoring periods can export trace data for before/after comparison.

Design constraints:
- OpenTelemetry is an OPTIONAL dependency. If opentelemetry-sdk is not
  installed, all functions are no-ops.
- Prompt and completion text is NEVER exported — only task/provider/model
  metadata and token/cost counts.
- Spans use the step_name as the span name and model_calls as child spans.

Usage::

    from novel_forge.obs.otel_export import trace_to_spans, is_otel_available

    if is_otel_available():
        trace_to_spans(pipeline_trace, service_name="novel-forge-refactor")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.obs.tracer import PipelineTrace

_OTEL_AVAILABLE: bool | None = None

_SPAN_ATTR_PREFIX = "novel_forge."

_NO_CONTENT_FIELDS: frozenset[str] = frozenset({
    "messages",
    "prompt",
    "completion",
    "content",
    "text",
    "response",
    "request_body",
    "response_body",
})


def is_otel_available() -> bool:
    global _OTEL_AVAILABLE
    if _OTEL_AVAILABLE is None:
        try:
            import opentelemetry  # noqa: F401
            _OTEL_AVAILABLE = True
        except ImportError:
            _OTEL_AVAILABLE = False
    return _OTEL_AVAILABLE


def _safe_attr(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_attr(v) for v in value]
    return str(value)


def _model_call_attrs(call: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        f"{_SPAN_ATTR_PREFIX}task": call.task,
        f"{_SPAN_ATTR_PREFIX}provider": call.provider,
        f"{_SPAN_ATTR_PREFIX}model": call.model,
        f"{_SPAN_ATTR_PREFIX}prompt_tokens": call.prompt_tokens,
        f"{_SPAN_ATTR_PREFIX}completion_tokens": call.completion_tokens,
        f"{_SPAN_ATTR_PREFIX}total_tokens": call.total_tokens,
        f"{_SPAN_ATTR_PREFIX}cost_usd": round(call.cost_usd, 6),
        f"{_SPAN_ATTR_PREFIX}latency_ms": round(call.latency_ms, 2),
        f"{_SPAN_ATTR_PREFIX}thinking": call.thinking,
        f"{_SPAN_ATTR_PREFIX}multi_turn": call.multi_turn,
        f"{_SPAN_ATTR_PREFIX}success": call.success,
    }
    if call.error:
        attrs[f"{_SPAN_ATTR_PREFIX}error"] = call.error
    # Phase 7c: enriched attributes
    if hasattr(call, "retry_count"):
        attrs[f"{_SPAN_ATTR_PREFIX}retry_count"] = call.retry_count
    if hasattr(call, "token_escalation_level"):
        attrs[f"{_SPAN_ATTR_PREFIX}token_escalation_level"] = call.token_escalation_level
    return attrs


def _step_attrs(step: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        f"{_SPAN_ATTR_PREFIX}step_name": step.step_name,
        f"{_SPAN_ATTR_PREFIX}prompt_tokens": step.prompt_tokens,
        f"{_SPAN_ATTR_PREFIX}completion_tokens": step.completion_tokens,
        f"{_SPAN_ATTR_PREFIX}tokens_used": step.tokens_used,
        f"{_SPAN_ATTR_PREFIX}cost_usd": round(step.cost_usd, 6),
        f"{_SPAN_ATTR_PREFIX}success": step.success,
        f"{_SPAN_ATTR_PREFIX}retry_count": step.retry_count,
        f"{_SPAN_ATTR_PREFIX}model_call_count": len(step.model_calls),
    }
    if step.error:
        attrs[f"{_SPAN_ATTR_PREFIX}error"] = step.error
    # Phase 7c: enriched attributes
    if hasattr(step, "repair_dimension") and step.repair_dimension:
        attrs[f"{_SPAN_ATTR_PREFIX}repair_dimension"] = step.repair_dimension
    if hasattr(step, "repair_round") and step.repair_round:
        attrs[f"{_SPAN_ATTR_PREFIX}repair_round"] = step.repair_round
    if hasattr(step, "quality_score") and step.quality_score is not None:
        attrs[f"{_SPAN_ATTR_PREFIX}quality_score"] = step.quality_score
    return attrs


def trace_to_spans(
    pipeline_trace: "PipelineTrace",
    *,
    service_name: str = "novel-forge",
) -> list[dict[str, Any]]:
    """Convert a PipelineTrace to a list of span dictionaries.

    If OpenTelemetry is available, also emits real OTEL spans via the global
    TracerProvider.  The returned list is a lightweight representation for
    testing and logging — each dict has ``name``, ``attrs``, ``duration_ms``,
    and ``children`` keys.

    Prompt/completion text is never included in attrs.
    """
    spans: list[dict[str, Any]] = []

    tracer = None
    if is_otel_available():
        try:
            from opentelemetry import trace as otel_trace

            tracer = otel_trace.get_tracer(service_name)
        except Exception:
            tracer = None

    for step in pipeline_trace.steps:
        span_dict: dict[str, Any] = {
            "name": step.step_name,
            "attrs": _step_attrs(step),
            "duration_ms": round(step.duration_ms, 2),
            "children": [],
        }
        for call in step.model_calls:
            child = {
                "name": f"{call.task}:{call.provider}:{call.model}",
                "attrs": _model_call_attrs(call),
                "duration_ms": round(call.latency_ms, 2),
                "children": [],
            }
            span_dict["children"].append(child)

        if tracer is not None:
            try:
                with tracer.start_as_current_span(step.step_name) as otel_span:
                    for k, v in span_dict["attrs"].items():
                        otel_span.set_attribute(k, _safe_attr(v))
                    for call in step.model_calls:
                        with tracer.start_as_current_span(
                            f"{call.task}:{call.provider}:{call.model}"
                        ) as call_span:
                            for k, v in _model_call_attrs(call).items():
                                call_span.set_attribute(k, _safe_attr(v))
            except Exception:
                pass

        spans.append(span_dict)

    return spans


def trace_to_span_names(pipeline_trace: "PipelineTrace") -> list[str]:
    """Return ordered list of span names (step names) for sequence comparison."""
    return [step.step_name for step in pipeline_trace.steps]


def assert_no_content_in_spans(spans: list[dict[str, Any]]) -> None:
    """Verify that no span attrs contain prompt/completion content fields."""
    def _check(span: dict[str, Any]) -> None:
        attrs = span.get("attrs", {})
        for key in attrs:
            suffix = key.rsplit(".", 1)[-1] if "." in key else key
            assert suffix not in _NO_CONTENT_FIELDS, (
                f"Span '{span['name']}' attr '{key}' may contain content"
            )
        for child in span.get("children", []):
            _check(child)

    for span in spans:
        _check(span)


# ── Phase 7b: Auto-export integration ─────────────────────────────────────────

import os as _os  # noqa: E402


def is_otel_export_enabled() -> bool:
    """Check if OTel export is enabled via environment variable."""
    return _os.environ.get("NOVEL_FORGE_OTEL_ENABLED", "").lower() in ("1", "true", "yes")


def auto_export_trace(
    pipeline_trace: "PipelineTrace",
    *,
    service_name: str = "novel-forge",
) -> list[dict[str, Any]] | None:
    """Export trace if OTel is enabled and available.

    Returns the span list if exported, None otherwise.
    Controlled by NOVEL_FORGE_OTEL_ENABLED environment variable.
    """
    if not is_otel_export_enabled():
        return None
    if not is_otel_available():
        return None
    return trace_to_spans(pipeline_trace, service_name=service_name)
