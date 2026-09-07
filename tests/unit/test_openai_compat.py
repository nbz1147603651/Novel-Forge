from __future__ import annotations

import pytest

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.adapters.openai_compat import (
    _convert_messages,
    _extract_thinking,
    _map_error,
    _normalize_params,
    _parse_stream_chunk,
)
from novel_forge.gateway.types import Message


class TestConvertMessages:
    @pytest.mark.parametrize(
        "messages,expected_len,expected_first_role",
        [
            (
                [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hello"},
                ],
                2,
                "system",
            ),
            (
                [{"role": "user", "content": "hi", "name": "Alice"}],
                1,
                "user",
            ),
            (
                [
                    {"role": "user", "content": "hi"},
                    {"content": "missing role"},
                ],
                1,
                "user",
            ),
            (
                [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant"},
                ],
                1,
                "user",
            ),
            ([], 0, None),
            (
                [
                    {"role": "user", "content": "hi"},
                    None,
                    "bad",
                ],
                1,
                "user",
            ),
        ],
    )
    def test_convert_messages(self, messages, expected_len, expected_first_role) -> None:
        result = _convert_messages(messages)  # type: ignore[arg-type]
        assert len(result) == expected_len
        if expected_first_role and result:
            assert result[0]["role"] == expected_first_role

    def test_tool_calls_preserved(self) -> None:
        messages: list[Message] = [
            {
                "role": "assistant",
                "content": "call",
                "tool_calls": [{"id": "1", "type": "function"}],
            },
        ]
        result = _convert_messages(messages)
        assert result[0]["tool_calls"] == [{"id": "1", "type": "function"}]


class TestParseStreamChunk:
    @pytest.mark.parametrize(
        "chunk,expected",
        [
            (b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n', "hello"),
            (
                b'data: {"choices":['
                b'{"delta":{"content":"A"}},'
                b'{"delta":{"content":"B"}}'
                b']}\n\n',
                "AB",
            ),
            (b"data: [DONE]\n\n", ""),
            (
                b": heartbeat\n\n"
                b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
                "x",
            ),
            (
                b"data: {bad json}\n\n"
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                "ok",
            ),
            (b"", ""),
            (b'data: {"id":"1","object":"chat.completion.chunk"}\n\n', ""),
            (
                b'data: {"choices":[{"delta":{"content":"hello "}}]}\n'
                b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n',
                "hello world",
            ),
        ],
    )
    def test_parse_stream_chunk(self, chunk, expected) -> None:
        assert _parse_stream_chunk(chunk) == expected


class TestMapError:
    @pytest.mark.parametrize(
        "code,is_transient",
        [
            (429, True),
            (502, True),
            (503, True),
            (504, True),
            (401, False),
            (403, False),
            (400, False),
        ],
    )
    def test_error_mapping(self, code, is_transient) -> None:
        exc = _map_error(code, {"error": {"message": f"Error {code}"}})
        assert isinstance(exc, ModelGatewayError)
        assert exc.is_transient_error is is_transient

    def test_fallback_message(self) -> None:
        exc = _map_error(418, {})
        assert "HTTP 418" in str(exc)

    def test_flat_error_message(self) -> None:
        exc = _map_error(500, {"error": "server exploded"})
        assert "server exploded" in str(exc)


class TestNormalizeParams:
    @pytest.mark.parametrize(
        "temperature,max_tokens,expected",
        [
            (None, None, {}),
            (0.5, None, {"temperature": 0.5}),
            (None, 100, {"max_tokens": 100}),
            (-1.0, None, {"temperature": 0.0}),
            (3.0, None, {"temperature": 2.0}),
            (None, 0, {"max_tokens": 1}),
            (None, -5, {"max_tokens": 1}),
            (0.7, 2048, {"temperature": 0.7, "max_tokens": 2048}),
        ],
    )
    def test_normalize_params(self, temperature, max_tokens, expected) -> None:
        assert _normalize_params(temperature, max_tokens) == expected  # type: ignore[arg-type]

    def test_coerces_types(self) -> None:
        assert _normalize_params("0.5", None) == {"temperature": 0.5}  # type: ignore[arg-type]
        assert _normalize_params(None, "100") == {"max_tokens": 100}  # type: ignore[arg-type]


class TestExtractThinking:
    @pytest.mark.parametrize(
        "text,expected_content,expected_thinking",
        [
            ("hello", "hello", ""),
            ("a<think>b</think>c", "ac", "b"),
            ("a<think>b</think>c<think>d</think>e", "ace", "b\n\nd"),
            ("<think>only opening", "<think>only opening", ""),
        ],
    )
    def test_extract_thinking(self, text, expected_content, expected_thinking) -> None:
        assert _extract_thinking(text) == (expected_content, expected_thinking)
