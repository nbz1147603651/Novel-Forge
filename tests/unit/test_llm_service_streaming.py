"""Regression tests for LLMService streaming event payloads."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk
from novel_forge.model_runtime.streaming import route_with_observed_stream
from novel_forge.pipeline.long.services.generation.llm_helpers import route_json_object_with_retry
from novel_forge.pipeline.long.services.generation.llm_service import LLMService
from novel_forge.pipeline.long.services.task_output_adapters import TaskOutputAdapterResult


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
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192

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
            on_chunk(StreamChunk(reasoning="思考"))
            on_chunk(StreamChunk(content="正文"))
        if self.fail:
            raise ModelGatewayError("stream failed", is_transient=False)
        return ModelResponse(content="正文", thinking_content="")


class _LargeChunkRouter(_FakeStreamRouter):
    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        text = "x" * 300
        if on_chunk is not None:
            on_chunk(StreamChunk(content=text))
        return ModelResponse(content=text, thinking_content="")


class _ResetThenTextRouter(_FakeStreamRouter):
    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        if on_chunk is not None:
            on_chunk(StreamChunk(reasoning="旧思考", content="旧正文"))
            on_chunk(StreamChunk(reset=True, reset_reason="切换备用路由"))
            on_chunk(StreamChunk(reasoning="新思考", content="新正文"))
        return ModelResponse(content="新正文", thinking_content="新思考")


class _RetryThenTextRouter(_FakeStreamRouter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            if on_chunk is not None:
                on_chunk(StreamChunk(content="失败片段"))
            raise ModelGatewayError("temporary failure", is_transient=True)
        if on_chunk is not None:
            on_chunk(StreamChunk(content="重试成功"))
        return ModelResponse(content="重试成功", thinking_content="")


class _JsonStreamRouter(_FakeStreamRouter):
    def __init__(self, *, content: str = '{"summary":"雨夜"}') -> None:
        super().__init__()
        self.content = content
        self.stream_calls = 0
        self.route_calls = 0

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        self.stream_calls += 1
        if on_chunk is not None:
            on_chunk(StreamChunk(content=self.content[:12]))
            on_chunk(StreamChunk(content=self.content[12:]))
        return ModelResponse(content=self.content, thinking_content="")

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.route_calls += 1
        return ModelResponse(content=self.content, thinking_content="")


class _FailedPartialJsonStreamRouter(_JsonStreamRouter):
    def __init__(self, *, content: str, category: str = "network_error") -> None:
        super().__init__(content=content)
        self.category = category

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        self.stream_calls += 1
        if on_chunk is not None:
            on_chunk(StreamChunk(content=self.content))
        raise ModelGatewayError(
            "stream transport failed",
            is_transient=True,
            failure_categories=[self.category],
        )


async def test_validation_feedback_isolated_for_parallel_calls_of_same_task() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _JsonStreamRouter(), _FakeBuilder(), lambda step, data: events.append((step, data))
    )
    results = await asyncio.gather(
        *(service.call_with_retry(TaskType.SUMMARIZE_CHAPTER, {}, max_retries=1) for _ in range(2))
    )
    starts = [data for step, data in events if step == "llm_stream_start"]
    verdicts = [
        data
        for step, data in events
        if step == "llm_stream_validation" and data["validation_status"] == "validated"
    ]
    assert len({row["operation_id"] for row in starts}) == 2
    assert {row["stream_id"] for row in starts} == {row["stream_id"] for row in verdicts}
    for verdict, result in zip(verdicts, results, strict=True):
        assert json.loads(verdict["text"]) == result
        assert verdict["text_length"] == len(verdict["text"])


async def test_local_repair_reports_validated_snapshot_only_after_contract_passes() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _JsonStreamRouter(content='{"summary":"雨夜",}'),
        _FakeBuilder(),
        lambda step, data: events.append((step, data)),
    )
    result = await service.call_with_retry(TaskType.SUMMARIZE_CHAPTER, {}, max_retries=1)
    verdicts = [data for step, data in events if step == "llm_stream_validation"]
    assert any(row["validation_status"] == "repairing" for row in verdicts)
    assert verdicts[-1]["validation_status"] == "validated"
    assert verdicts[-1]["repair_source"] == "local"
    assert json.loads(verdicts[-1]["text"]) == result == {"summary": "雨夜"}
    raw_end = next(data for step, data in events if step == "llm_stream_end")
    assert raw_end["text"] == '{"summary":"雨夜",}'
    assert raw_end["validation_status"] == "validating"


async def test_exhausted_invalid_response_emits_failure_not_validated(monkeypatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _JsonStreamRouter(content='{"other":"并非缺少闭合符号"}'),
        _FakeBuilder(),
        lambda step, data: events.append((step, data)),
        settings=type("Settings", (), {"llm_format_repair_enabled": False})(),
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", no_sleep
    )
    with pytest.raises((ValueError, KeyError, TypeError)):
        await service.call_with_retry(
            TaskType.SUMMARIZE_CHAPTER, {}, required_keys=("summary",), max_retries=2
        )
    verdicts = [data for step, data in events if step == "llm_stream_validation"]
    assert len({row["operation_id"] for row in verdicts}) == 1
    assert len({row["stream_id"] for row in verdicts}) == 2
    assert any(row["validation_status"] == "retrying" for row in verdicts)
    assert verdicts[-1]["validation_status"] == "failed"
    assert not any(row["validation_status"] == "validated" for row in verdicts)


class _TimeoutThenRouteJsonRouter(_JsonStreamRouter):
    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk: Any = None,
    ) -> ModelResponse:
        self.stream_calls += 1
        raise ModelGatewayError(
            "stream idle timeout",
            is_transient=True,
            failure_categories=["timeout"],
        )


class _RouteOnlyJsonRouter:
    def __init__(self, *, content: str = '{"summary":"雨夜"}') -> None:
        self.content = content
        self.route_calls = 0

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "mock-route-model"

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.route_calls += 1
        return ModelResponse(content=self.content, thinking_content="")


class _SequencedBeatsRouter(_RouteOnlyJsonRouter):
    def __init__(self) -> None:
        super().__init__()
        self.contents = (
            '{"beats":[{"sequence":1,"summary":"开场","tension_level":"渐升"}]}',
            '{"beats":[{"sequence":1,"summary":"开场","tension_level":4}]}',
        )

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.route_calls += 1
        content = self.contents[min(self.route_calls - 1, len(self.contents) - 1)]
        return ModelResponse(content=content, thinking_content="")


async def test_stream_end_uses_accumulated_reasoning_when_response_omits_it() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _FakeStreamRouter(),
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    text = await service.call_text_stream_with_retry(
        TaskType.DRAFT_CHAPTER,
        {},
        max_retries=1,
        validate_text_output=False,
    )

    assert text == "正文"
    end_payload = next(payload for step, payload in events if step == "llm_stream_end")
    assert end_payload["reasoning"] == "思考"
    assert end_payload["reasoning_length"] == 2


async def test_transport_reset_discards_old_content_and_reasoning_in_same_stream() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _ResetThenTextRouter(),
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    text = await service.call_text_stream_with_retry(
        TaskType.DRAFT_CHAPTER,
        {},
        max_retries=1,
        validate_text_output=False,
    )

    assert text == "新正文"
    restart = next(payload for step, payload in events if step == "llm_stream_restart")
    end = next(payload for step, payload in events if step == "llm_stream_end")
    assert restart["reset_output"] is True
    assert restart["message"] == "切换备用路由"
    assert end["text"] == "新正文"
    assert end["reasoning"] == "新思考"
    assert end["text_length"] == len("新正文")
    assert end["reasoning_length"] == len("新思考")


async def test_text_retry_streams_share_one_operation_id(monkeypatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _RetryThenTextRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep",
        no_sleep,
    )
    text = await service.call_text_stream_with_retry(
        TaskType.DRAFT_CHAPTER,
        {},
        max_retries=2,
        validate_text_output=False,
    )

    starts = [payload for step, payload in events if step == "llm_stream_start"]
    assert text == "重试成功"
    assert router.calls == 2
    assert [payload["attempt"] for payload in starts] == [1, 2]
    assert len({payload["stream_id"] for payload in starts}) == 2
    assert len({payload["operation_id"] for payload in starts}) == 1
    assert all(
        payload["operation_id"] == starts[0]["operation_id"]
        for step, payload in events
        if step.startswith("llm_stream_")
    )
    assert all(payload["output_kind"] == "text" for payload in starts)


async def test_shared_runtime_stream_reset_replaces_old_route_output() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    response = await route_with_observed_stream(
        _ResetThenTextRouter(),
        ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "写一章"}],
        ),
        on_step=lambda step, payload: events.append((step, payload)),
        task_type=TaskType.DRAFT_CHAPTER,
        chapter=1,
        attempt=1,
        max_attempts=1,
        stream_kind="domain_text",
        output_kind="text",
        operation_id="runtime-op",
    )

    restart = next(payload for step, payload in events if step == "llm_stream_restart")
    end = next(payload for step, payload in events if step == "llm_stream_end")
    assert response.content == "新正文"
    assert restart["reset_output"] is True
    assert restart["operation_id"] == "runtime-op"
    assert end["text"] == "新正文"
    assert end["reasoning"] == "新思考"


async def test_call_with_retry_text_only_delegates_to_streaming_path() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _FakeStreamRouter(),
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    text = await service.call_with_retry(
        TaskType.DRAFT_CHAPTER,
        {},
        max_retries=1,
    )

    assert text == "正文"
    event_names = [step for step, _payload in events]
    assert "llm_stream_start" in event_names
    assert "llm_stream_delta" in event_names
    assert "llm_stream_end" in event_names


async def test_beats_nested_type_error_triggers_format_retry() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _SequencedBeatsRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    result = await service.call_with_retry(TaskType.BEATS, {}, max_retries=2)

    assert isinstance(result, dict)
    assert result["beats"][0]["tension_level"] == 4
    assert router.route_calls == 2
    assert any(step == "format_retry" for step, _payload in events)


async def test_stream_error_keeps_reasoning_already_flushed_to_ui() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _FakeStreamRouter(fail=True),
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    with pytest.raises(ModelGatewayError):
        await service.call_text_stream_with_retry(
            TaskType.DRAFT_CHAPTER,
            {},
            max_retries=1,
            validate_text_output=False,
        )

    error_payload = next(payload for step, payload in events if step == "llm_stream_error")
    assert error_payload["text"] == "正文"
    assert error_payload["reasoning"] == "思考"
    assert error_payload["reasoning_length"] == 2


async def test_large_stream_chunk_flushes_without_waiting_for_interval() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    service = LLMService(
        _LargeChunkRouter(),
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    text = await service.call_text_stream_with_retry(
        TaskType.DRAFT_CHAPTER,
        {},
        max_retries=1,
        validate_text_output=False,
    )

    assert text == "x" * 300
    delta_payloads = [payload for step, payload in events if step == "llm_stream_delta"]
    assert len(delta_payloads) == 1
    assert delta_payloads[0]["segments"] == [{"kind": "content", "text": "x" * 300}]


async def test_call_with_retry_json_emits_observation_stream_before_validation() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _JsonStreamRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    payload = await service.call_with_retry(
        TaskType.SUMMARIZE_CHAPTER,
        {},
        max_retries=1,
    )

    assert payload == {"summary": "雨夜"}
    assert router.stream_calls == 1
    event_names = [step for step, _payload in events]
    assert "llm_stream_start" in event_names
    assert "llm_stream_delta" in event_names
    assert "llm_stream_end" in event_names
    start_payload = next(payload for step, payload in events if step == "llm_stream_start")
    assert start_payload["stream_kind"] == "json_observation"
    assert start_payload["output_kind"] == "json"
    delta_payloads = [payload for step, payload in events if step == "llm_stream_delta"]
    assert (
        "".join(
            segment["text"]
            for event_payload in delta_payloads
            for segment in event_payload["segments"]
        )
        == '{"summary":"雨夜"}'
    )
    assert "format_validation_success" in event_names


async def test_json_stream_recovers_validated_payload_from_terminal_partial() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _FailedPartialJsonStreamRouter(content='{"summary":"雨夜"}')
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    payload = await service.call_with_retry(
        TaskType.SUMMARIZE_CHAPTER,
        {},
        max_retries=1,
    )

    assert payload == {"summary": "雨夜"}
    assert router.stream_calls == 1
    assert router.route_calls == 0
    event_names = [step for step, _payload in events]
    assert "json_stream_partial_recovery_started" in event_names
    assert "json_stream_partial_recovery_succeeded" in event_names
    assert "format_validation_success" in event_names
    recovery = next(data for step, data in events if step == "json_stream_partial_recovery_started")
    assert recovery["text_length"] == len('{"summary":"雨夜"}')
    assert "text" not in recovery


async def test_wrapped_stream_timeout_retries_via_non_stream_route(monkeypatch: Any) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _TimeoutThenRouteJsonRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep",
        _no_sleep,
    )
    payload = await service.call_with_retry(
        TaskType.SUMMARIZE_CHAPTER,
        {},
        max_retries=2,
    )

    assert payload == {"summary": "雨夜"}
    assert router.stream_calls == 1
    assert router.route_calls == 1
    fallback = next(data for step, data in events if step == "json_stream_fallback_to_route")
    assert fallback["reason"] == "stream_timeout"


async def test_invalid_terminal_partial_reraises_transport_error() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _FailedPartialJsonStreamRouter(content="{")
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
        settings=type("Settings", (), {"llm_format_repair_enabled": False})(),
    )

    with pytest.raises(ModelGatewayError, match="stream transport failed"):
        await service.call_with_retry(
            TaskType.SUMMARIZE_CHAPTER,
            {},
            max_retries=1,
        )

    assert any(step == "json_stream_partial_recovery_rejected" for step, _ in events)


async def test_truncated_chapter_contract_stream_recovers_complete_rows_only() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    complete_rows = [
        {
            "chapter_number": chapter_number,
            "title": f"第{chapter_number}章",
            "source": "plan_chapter_contracts",
        }
        for chapter_number in (29, 30, 31)
    ]
    complete_json = json.dumps({"chapter_contracts": complete_rows}, ensure_ascii=False)
    truncated = complete_json[:-2] + ', {"chapter_number": 32, "title": "正在生成但连接中断'
    router = _FailedPartialJsonStreamRouter(content=truncated, category="timeout")
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
        settings=type("Settings", (), {"llm_format_repair_enabled": False})(),
    )
    outline_context = {
        "chapters": [{"chapter_number": number} for number in (29, 30, 31, 32)],
        "contract_scaffold": [{"chapter_number": number} for number in (29, 30, 31, 32)],
        "contract_batch": {"chapter_numbers": [29, 30, 31, 32]},
    }

    payload = await service.call_with_retry(
        TaskType.PLAN_CHAPTER_CONTRACTS,
        {"outline": outline_context},
        required_keys=("chapter_contracts",),
        max_retries=1,
    )

    assert isinstance(payload, dict)
    assert [row["chapter_number"] for row in payload["chapter_contracts"]] == [29, 30, 31]
    assert payload["coverage"]["local_fallback_accepted"] is True
    assert payload["coverage"]["partial_format_repair_accepted"] is True
    assert any(step == "json_stream_partial_recovery_succeeded" for step, _ in events)


async def test_call_with_retry_json_observation_stream_can_be_disabled() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _JsonStreamRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
        settings=type("Settings", (), {"long_streaming_json_observation_enabled": False})(),
    )

    payload = await service.call_with_retry(
        TaskType.SUMMARIZE_CHAPTER,
        {},
        max_retries=1,
    )

    assert payload == {"summary": "雨夜"}
    assert router.stream_calls == 0
    assert router.route_calls == 1
    assert not any(step in {"llm_stream_start", "llm_stream_delta"} for step, _ in events)
    verdict = [data for step, data in events if step == "llm_stream_validation"][-1]
    assert verdict["validation_status"] == "validated"
    assert json.loads(verdict["text"]) == payload


async def test_json_observation_stream_respects_configured_extra_excluded_tasks() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _JsonStreamRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
        settings=type(
            "Settings",
            (),
            {
                "long_streaming_json_excluded_tasks": "summarize_chapter",
            },
        )(),
    )

    # SUMMARIZE_CHAPTER is not in the built-in exclusion set, so the setting
    # must turn off its observation stream.
    payload = await service.call_with_retry(
        TaskType.SUMMARIZE_CHAPTER,
        {},
        max_retries=1,
    )

    assert payload == {"summary": "雨夜"}
    assert router.stream_calls == 0
    assert router.route_calls == 1
    assert not any(step in {"llm_stream_start", "llm_stream_delta"} for step, _ in events)
    assert any(
        step == "llm_stream_validation" and data["validation_status"] == "validated"
        for step, data in events
    )


async def test_json_observation_stream_builtin_exclusions_always_apply() -> None:
    router = _JsonStreamRouter()
    service = LLMService(
        router,
        _FakeBuilder(),
        lambda step, payload: None,
    )

    # Built-in exclusions always apply and user config only appends; it can
    # never remove a built-in task from the excluded set.
    excluded = service._stream_observation_excluded_tasks()
    assert "extract_canon" in excluded
    assert "adjudicate_state_delta" in excluded
    service._settings = type(
        "Settings",
        (),
        {"long_streaming_json_excluded_tasks": "extract_canon"},
    )()
    assert "extract_canon" in service._stream_observation_excluded_tasks()
    assert "adjudicate_state_delta" in service._stream_observation_excluded_tasks()


async def test_json_observation_stream_falls_back_when_router_has_no_stream_route() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    router = _RouteOnlyJsonRouter()
    service = LLMService(
        router,  # type: ignore[arg-type]
        _FakeBuilder(),
        lambda step, payload: events.append((step, payload)),
    )

    payload = await service.call_with_retry(
        TaskType.SUMMARIZE_CHAPTER,
        {},
        max_retries=1,
    )

    assert payload == {"summary": "雨夜"}
    assert router.route_calls == 1
    assert not any(step in {"llm_stream_start", "llm_stream_delta"} for step, _ in events)
    assert any(
        step == "llm_stream_validation" and data["validation_status"] == "validated"
        for step, data in events
    )


async def test_direct_json_helper_can_use_the_shared_observation_stream() -> None:
    """Direct structured callers receive the same desktop stream contract."""
    events: list[tuple[str, dict[str, Any]]] = []
    router = _JsonStreamRouter()
    request = ModelRequest(
        task_type=TaskType.SUMMARIZE_CHAPTER,
        messages=[{"role": "user", "content": "总结"}],
    )

    payload = await route_json_object_with_retry(
        router,
        request,
        task_type=TaskType.SUMMARIZE_CHAPTER,
        on_step=lambda step, data: events.append((step, data)),
        observe_stream=True,
        chapter=9,
    )

    assert payload == {"summary": "雨夜"}
    assert router.stream_calls == 1
    assert router.route_calls == 0
    start = next(data for step, data in events if step == "llm_stream_start")
    assert start["stream_kind"] == "json_observation"
    assert start["output_kind"] == "json"
    assert start["chapter"] == 9
    validation = [data for step, data in events if step == "llm_stream_validation"][-1]
    assert validation["stream_id"] == start["stream_id"]
    assert validation["operation_id"] == start["operation_id"]
    assert validation["validation_status"] == "validated"
    assert json.loads(validation["text"]) == payload


async def test_direct_json_helper_reports_task_adapter_local_repair(monkeypatch) -> None:
    """Known task-envelope cleanup is visible before strict validation passes."""
    from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h

    events: list[tuple[str, dict[str, Any]]] = []

    def _adapter(data: dict[str, Any], *_args: Any, **_kwargs: Any) -> TaskOutputAdapterResult:
        data.pop("copied_input", None)
        return TaskOutputAdapterResult(
            changed=True,
            adapter="test_adapter",
            changed_keys=("copied_input",),
        )

    monkeypatch.setattr(llm_h, "apply_task_output_adapter", _adapter)
    payload = await route_json_object_with_retry(
        _JsonStreamRouter(content='{"summary":"雨夜","copied_input":"只读"}'),
        ModelRequest(
            task_type=TaskType.SUMMARIZE_CHAPTER,
            messages=[{"role": "user", "content": "总结"}],
        ),
        task_type=TaskType.SUMMARIZE_CHAPTER,
        on_step=lambda step, data: events.append((step, data)),
        observe_stream=True,
    )

    verdicts = [data for step, data in events if step == "llm_stream_validation"]
    assert [row["validation_status"] for row in verdicts][-3:] == [
        "validating",
        "repairing",
        "validated",
    ]
    assert verdicts[-2]["repair_source"] == "local"
    assert payload == {"summary": "雨夜"}
