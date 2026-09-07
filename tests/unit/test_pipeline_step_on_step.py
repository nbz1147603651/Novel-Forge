"""Regression tests for PipelineStep event forwarding."""

from __future__ import annotations

from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk
from novel_forge.pipeline.steps.base import PipelineStep


class _FakeBuilder:
    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "写正文"}],
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _FakeStreamRouter:
    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "mock-stream-model"

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        if on_chunk is not None:
            on_chunk(StreamChunk(content="正文"))
        return ModelResponse(content="正文")


class _StreamingStep(PipelineStep[None, str]):
    @property
    def step_name(self) -> str:
        return "streaming_test"

    async def _execute(self, input_data: None) -> str:
        result = await self._call_with_retry(
            TaskType.DRAFT_CHAPTER,
            {},
            max_tokens=256,
            temperature=0.3,
            max_retries=1,
            validate_text_output=False,
        )
        return str(result)


async def test_pipeline_step_forwards_stream_events_to_on_step() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    step = _StreamingStep(
        _FakeStreamRouter(),  # type: ignore[arg-type]
        _FakeBuilder(),  # type: ignore[arg-type]
        settings=Settings(),
        on_step=lambda event, data: events.append((event, data)),
    )

    result = await step.run(None)

    assert result == "正文"
    event_names = [event for event, _data in events]
    assert "llm_stream_start" in event_names
    assert "llm_stream_delta" in event_names
    assert "llm_stream_end" in event_names
