"""Tests for trace-level token and cost propagation."""

from __future__ import annotations

from novel_forge.core.constants import TaskType
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest
from novel_forge.obs.context import current_log_context
from novel_forge.obs.tracer import PipelineTrace


async def test_pipeline_trace_collects_model_metrics_from_router_calls() -> None:
    trace = PipelineTrace()
    router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")

    with trace.step("spec_enrich"):
        assert current_log_context()["step"] == "spec_enrich"
        await router.route(
            ModelRequest(
                task_type=TaskType.SPEC_ENRICH,
                messages=[{"role": "user", "content": "请生成一个故事规格"}],
                max_tokens=128,
                temperature=0.3,
            )
        )

    assert "step" not in current_log_context()

    summary = trace.summary()
    assert summary["total_tokens"] > 0
    assert summary["total_prompt_tokens"] > 0
    assert summary["step_count"] == 1
    step = summary["steps"][0]
    assert step["name"] == "spec_enrich"
    assert step["tokens"] > 0
    assert step["model_call_count"] == 1
    call = step["model_calls"][0]
    assert call["task"] == TaskType.SPEC_ENRICH.value
    assert call["provider"] == "mock"
    assert call["success"] is True
