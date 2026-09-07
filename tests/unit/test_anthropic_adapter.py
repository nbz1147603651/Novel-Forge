"""Tests for AnthropicAdapter."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, patch

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.adapters.anthropic import AnthropicAdapter
from novel_forge.gateway.types import ModelRequest, StreamChunk


class MockResponse:
    def __init__(
        self,
        text: str = "test response",
        input_tokens: int = 10,
        output_tokens: int = 20,
        stop_reason: str = "end_turn",
    ) -> None:
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.stop_reason = stop_reason


class _MockStreamContext:
    def __init__(self, events: list[object], *, error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> bool:
        return False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    async def get_final_message(self):
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(input_tokens=3, output_tokens=2)
        )


class _MockStreamFactory:
    def __init__(self, contexts: list[_MockStreamContext]) -> None:
        self._contexts = contexts
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self._contexts.pop(0)


class TestAnthropicAdapter:
    def test_default_initialization(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")
        assert adapter.provider_name == "anthropic"
        assert adapter.default_model == "claude-sonnet-4-6"
        assert adapter.connect_timeout_s == 30.0
        assert adapter._write_timeout_s == 120.0

    def test_custom_model_initialization(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key", default_model="claude-3-5-sonnet-20241022")
        assert adapter.default_model == "claude-3-5-sonnet-20241022"

    def test_last_health_error_initial_empty(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")
        assert adapter.last_health_error == ""

    @pytest.mark.asyncio
    async def test_complete_normal_response(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        mock_response = MockResponse(text="Hello, world!")
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "say hello"}],
                max_tokens=100,
            )
            response = await adapter.complete(request)

        assert response.content == "Hello, world!"
        assert response.finish_reason == "stop"
        assert response.model_id == "claude-sonnet-4-6"
        assert response.prompt_tokens == 10
        assert response.completion_tokens == 20
        assert response.total_tokens == 30
        assert response.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_complete_with_system_message(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        mock_response = MockResponse(text="Response with system context")
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "say hi"},
                ],
                max_tokens=100,
            )
            response = await adapter.complete(request)

        assert response.content == "Response with system context"
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == [
            {
                "type": "text",
                "text": "You are a helpful assistant.",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @pytest.mark.asyncio
    async def test_complete_with_model_override(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        mock_response = MockResponse(text="Using claude-3-5")
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                model_id="claude-3-5-sonnet-20241022",
            )
            await adapter.complete(request)

        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-3-5-sonnet-20241022"

    @pytest.mark.asyncio
    async def test_complete_max_tokens(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        mock_response = MockResponse(text="Short response")
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=500,
            )
            await adapter.complete(request)

        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 500

    @pytest.mark.asyncio
    async def test_complete_finish_reason_length(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        mock_response = MockResponse(text="Truncated response", stop_reason="max_tokens")
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=10,
            )
            response = await adapter.complete(request)

        assert response.finish_reason == "length"

    @pytest.mark.asyncio
    async def test_complete_error_401(self) -> None:
        adapter = AnthropicAdapter(api_key="bad-key")

        mock_client = AsyncMock()
        exc = Exception("Invalid API key")
        exc.status_code = 401
        mock_client.messages.create = AsyncMock(side_effect=exc)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
            )
            with pytest.raises(ModelGatewayError) as exc_info:
                await adapter.complete(request)
            assert exc_info.value.is_transient_error is False

    @pytest.mark.asyncio
    async def test_complete_error_429(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        exc = Exception("Rate limited")
        exc.status_code = 429
        mock_client.messages.create = AsyncMock(side_effect=exc)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
            )
            with pytest.raises(ModelGatewayError) as exc_info:
                await adapter.complete(request)
            assert exc_info.value.is_transient_error is True

    @pytest.mark.asyncio
    async def test_complete_error_500(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        exc = Exception("Internal server error")
        exc.status_code = 500
        mock_client.messages.create = AsyncMock(side_effect=exc)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
            )
            with pytest.raises(ModelGatewayError) as exc_info:
                await adapter.complete(request)
            assert exc_info.value.is_transient_error is False

    @pytest.mark.asyncio
    async def test_complete_cache_error_fallback(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        cache_error = Exception("cache_control_error")
        mock_client.messages.create = AsyncMock(
            side_effect=[cache_error, MockResponse(text="Success")]
        )

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "test"},
                ],
                max_tokens=100,
            )
            response = await adapter.complete(request)

        assert response.content == "Success"
        assert mock_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_stream_cache_fallback_emits_reset_before_replacement_chunks(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")
        old_event = types.SimpleNamespace(
            type="content_block_delta",
            delta=types.SimpleNamespace(type="text_delta", text="旧正文"),
        )
        new_event = types.SimpleNamespace(
            type="content_block_delta",
            delta=types.SimpleNamespace(type="text_delta", text="新正文"),
        )
        stream_factory = _MockStreamFactory(
            [
                _MockStreamContext(
                    [old_event],
                    error=Exception("cache_control_error"),
                ),
                _MockStreamContext([new_event]),
            ]
        )
        mock_client = types.SimpleNamespace(
            messages=types.SimpleNamespace(stream=stream_factory)
        )
        finals = []
        chunks: list[StreamChunk] = []
        request = ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "写正文"},
            ],
            max_tokens=100,
        )

        with patch.object(adapter, "_get_client", return_value=mock_client):
            async for chunk in adapter.stream(request, on_final=finals.append):
                assert isinstance(chunk, StreamChunk)
                chunks.append(chunk)

        assert [(chunk.content, chunk.reset) for chunk in chunks] == [
            ("旧正文", False),
            ("", True),
            ("新正文", False),
        ]
        assert chunks[1].reset_reason
        assert len(stream_factory.calls) == 2
        assert stream_factory.calls[0]["system"] != stream_factory.calls[1]["system"]
        assert finals[0].content == "新正文"

    @pytest.mark.asyncio
    async def test_complete_retries_and_remembers_unsupported_structured_output(self) -> None:
        adapter = AnthropicAdapter(
            api_key="test-key",
            default_model="claude-sonnet-4-5-no-structured-test",
        )

        mock_client = AsyncMock()
        structured_error = Exception("output_config json_schema is not supported")
        mock_client.messages.create = AsyncMock(
            side_effect=[
                structured_error,
                MockResponse(text="Structured fallback"),
                MockResponse(text="Cached fallback"),
            ]
        )

        request = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            max_tokens=100,
            response_json_schema={"type": "object"},
        )
        with patch.object(adapter, "_get_client", return_value=mock_client):
            first_response = await adapter.complete(request)
            second_response = await adapter.complete(request)

        assert first_response.content == "Structured fallback"
        assert second_response.content == "Cached fallback"
        first_call = mock_client.messages.create.call_args_list[0].kwargs
        fallback_call = mock_client.messages.create.call_args_list[1].kwargs
        cached_call = mock_client.messages.create.call_args_list[2].kwargs
        assert "output_config" in first_call
        assert "output_config" not in fallback_call
        assert "output_config" not in cached_call
        assert first_response.structured_output_mode == "prompt_only"
        assert first_response.structured_output_downgraded_from == "json_schema"
        assert first_response.structured_output_reason == "runtime_unsupported_fallback"
        assert second_response.structured_output_mode == "prompt_only"
        assert second_response.structured_output_downgraded_from == "json_schema"
        assert second_response.structured_output_reason == "runtime_unsupported_cache"

    @pytest.mark.asyncio
    async def test_complete_non_cache_error_raises(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        exc = Exception("some other error")
        exc.status_code = 500
        mock_client.messages.create = AsyncMock(side_effect=exc)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            request = ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
            )
            with pytest.raises(ModelGatewayError) as exc_info:
                await adapter.complete(request)
            assert exc_info.value.is_transient_error is False

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        mock_response = MockResponse(text="pong")
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.health_check()

        assert result is True
        assert adapter.last_health_error == ""

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        adapter = AnthropicAdapter(api_key="bad-key")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.object(adapter, "_get_client", return_value=mock_client):
            result = await adapter.health_check()

        assert result is False
        assert "Connection refused" in adapter.last_health_error

    @pytest.mark.asyncio
    async def test_shutdown_closes_client(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")

        mock_client = AsyncMock()
        adapter._client = mock_client

        await adapter.shutdown()

        assert adapter._client is None

    @pytest.mark.asyncio
    async def test_shutdown_when_no_client(self) -> None:
        adapter = AnthropicAdapter(api_key="test-key")
        adapter._client = None

        await adapter.shutdown()

        assert adapter._client is None
