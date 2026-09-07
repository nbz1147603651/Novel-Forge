"""Tests for domestic LLM adapters and task routing."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.adapters.deepseek import DeepSeekAdapter
from novel_forge.gateway.adapters.kimi import KimiAdapter
from novel_forge.gateway.adapters.mimo import MiMoAdapter
from novel_forge.gateway.adapters.minimax import MiniMaxAdapter
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.adapters.opencode import OpenCodeAdapter
from novel_forge.gateway.adapters.tongyi import TongyiAdapter
from novel_forge.gateway.adapters.volcengine_ark import VolcengineArkAdapter
from novel_forge.gateway.factory import (
    ModelRouterBuilder,
    TaskRoutingParseError,
    parse_task_routing,
)
from novel_forge.gateway.router import ModelRouter, TaskRouteOverride
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    build_structured_output_request_plan,
)
from novel_forge.gateway.types import ModelRequest


def _structured_output_kwargs(
    request: ModelRequest,
    provider: str,
    model_id: str | None = None,
) -> dict[str, object]:
    return build_structured_output_request_plan(
        request,
        provider=provider,
        model_id=model_id,
        dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
    ).kwargs


def _anthropic_structured_output_kwargs(
    request: ModelRequest,
    model_id: str,
) -> dict[str, object]:
    return build_structured_output_request_plan(
        request,
        provider="anthropic",
        model_id=model_id,
        dialect=StructuredOutputDialect.ANTHROPIC_OUTPUT_CONFIG,
    ).kwargs


# ── OpenAI-compatible base adapter ──────────────────────


class TestOpenAICompatibleAdapter:
    """Tests for the OpenAI-compatible base adapter."""

    def test_provider_name(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="test",
            base_url="https://api.example.com",
            provider="example",
            default_model="example-model",
        )
        assert adapter.provider_name == "example"
        assert adapter._write_timeout_s == 120.0

    def test_base_url_trailing_slash(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="test",
            base_url="https://api.example.com/",
            provider="example",
            default_model="example-model",
        )
        assert adapter._base_url == "https://api.example.com"

    @pytest.mark.asyncio
    async def test_health_check_uses_normal_streaming_completion_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys
        import types

        captured: dict[str, object] = {}

        class _AsyncStreamIterator:
            def __init__(self) -> None:
                self._done = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._done:
                    raise StopAsyncIteration
                self._done = True
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            delta=types.SimpleNamespace(content="o"),
                            finish_reason="stop",
                        )
                    ],
                    usage=None,
                )

        class _AsyncOpenAI:
            def __init__(
                self,
                *,
                api_key: str,
                base_url: str,
                timeout: object,
                max_retries: int,
            ) -> None:
                captured["api_key"] = api_key
                captured["base_url"] = base_url
                captured["timeout"] = timeout
                captured["max_retries"] = max_retries
                captured["closed"] = False
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: object):
                captured["create_kwargs"] = kwargs
                return _AsyncStreamIterator()

            async def aclose(self) -> None:
                captured["closed"] = True

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        adapter = OpenAICompatibleAdapter(
            api_key="test",
            base_url="https://api.example.com",
            provider="example",
            default_model="example-model",
        )

        assert await adapter.health_check() is True

        create_kwargs = captured["create_kwargs"]
        assert isinstance(create_kwargs, dict)
        assert captured["api_key"] == "test"
        assert captured["base_url"] == "https://api.example.com/v1"
        assert captured["max_retries"] == 0
        assert create_kwargs["model"] == "example-model"
        assert create_kwargs["stream"] is True
        assert create_kwargs["max_tokens"] == 8
        assert captured["closed"] is False
        assert adapter._client is not None

        await adapter.shutdown()

        assert captured["closed"] is True
        assert adapter._client is None

    @pytest.mark.asyncio
    async def test_complete_requires_model_when_no_default_is_configured(self) -> None:
        adapter = OpenAICompatibleAdapter(
            api_key="test",
            base_url="https://api.example.com",
            provider="example",
            default_model="",
        )

        with pytest.raises(ModelGatewayError, match="No model_id configured"):
            await adapter.complete(
                ModelRequest(
                    task_type=TaskType.EVALUATE,
                    messages=[{"role": "user", "content": "hello"}],
                    max_tokens=16,
                    temperature=0.0,
                )
            )

    @pytest.mark.asyncio
    async def test_complete_discards_visible_reasoning_text(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys
        import types

        class _AsyncStreamIterator:
            """Simulates an OpenAI streaming response."""

            def __init__(self, chunks: list) -> None:
                self._chunks = chunks
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._chunks):
                    raise StopAsyncIteration
                chunk = self._chunks[self._idx]
                self._idx += 1
                return chunk

        class _AsyncOpenAI:
            def __init__(
                self, *, api_key: str, base_url: str, timeout: float, max_retries: int = 2
            ):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **_: object):
                chunks = [
                    types.SimpleNamespace(
                        choices=[
                            types.SimpleNamespace(
                                delta=types.SimpleNamespace(
                                    content="final answer",
                                    reasoning_content="hidden reasoning",
                                ),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    ),
                    types.SimpleNamespace(
                        choices=[],
                        usage=types.SimpleNamespace(
                            prompt_tokens=4,
                            completion_tokens=3,
                            total_tokens=7,
                        ),
                    ),
                ]
                return _AsyncStreamIterator(chunks)

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        adapter = OpenAICompatibleAdapter(
            api_key="test",
            base_url="https://api.example.com",
            provider="example",
            default_model="example-model",
        )
        response = await adapter.complete(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=16,
                temperature=0.0,
                thinking=True,
            )
        )

        assert response.content == "final answer"
        assert response.thinking_content == "hidden reasoning"

    @pytest.mark.asyncio
    async def test_complete_retries_without_structured_output_when_unsupported(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys
        import types

        calls: list[dict[str, object]] = []

        class _AsyncStreamIterator:
            def __init__(self, chunks: list) -> None:
                self._chunks = chunks
                self._idx = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._idx >= len(self._chunks):
                    raise StopAsyncIteration
                chunk = self._chunks[self._idx]
                self._idx += 1
                return chunk

        class _AsyncOpenAI:
            def __init__(
                self,
                *,
                api_key: str,
                base_url: str,
                timeout: object,
                max_retries: int = 0,
            ) -> None:
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: object):
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    raise ValueError("response_format json_schema is not supported")
                return _AsyncStreamIterator(
                    [
                        types.SimpleNamespace(
                            choices=[
                                types.SimpleNamespace(
                                    delta=types.SimpleNamespace(content="ok"),
                                    finish_reason="stop",
                                )
                            ],
                            usage=None,
                        )
                    ]
                )

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        adapter = OpenAICompatibleAdapter(
            api_key="test",
            base_url="https://api.example.com",
            provider="ollama",
            default_model="llama-no-structured-test",
        )
        response = await adapter.complete(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "JSON"}],
                response_json_schema={"type": "object"},
            )
        )
        second_response = await adapter.complete(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "JSON"}],
                response_json_schema={"type": "object"},
            )
        )

        assert "response_format" in calls[0]
        assert "response_format" not in calls[1]
        assert response.content == "ok"
        assert response.structured_output_mode == "prompt_only"
        assert response.structured_output_downgraded_from == "json_schema"
        assert response.structured_output_reason == "runtime_unsupported_fallback"
        assert "response_format" not in calls[2]
        assert second_response.content == "ok"
        assert second_response.structured_output_mode == "prompt_only"
        assert second_response.structured_output_downgraded_from == "json_schema"
        assert second_response.structured_output_reason == "runtime_unsupported_cache"

    def test_structured_output_kwargs_for_openai_json_schema(self) -> None:
        schema = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
            "additionalProperties": False,
        }
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
            response_schema_name="test_schema",
            response_schema_strict=True,
        )

        kwargs = _structured_output_kwargs(req, "openai")

        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["name"] == "test_schema"
        assert kwargs["response_format"]["json_schema"]["strict"] is True
        assert kwargs["response_format"]["json_schema"]["schema"] == schema

    def test_structured_output_kwargs_downgrades_openai_json_object_model(
        self,
    ) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
        )

        assert _structured_output_kwargs(req, "openai", "gpt-4-turbo") == {
            "response_format": {"type": "json_object"}
        }

    def test_structured_output_kwargs_downgrades_non_strict_schema(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
            response_schema_name="test_schema",
            response_schema_strict=True,
        )

        kwargs = _structured_output_kwargs(req, "openai")

        assert kwargs["response_format"]["json_schema"]["strict"] is False

    def test_structured_output_kwargs_for_tongyi_json_object(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
            response_schema_name="test_schema",
        )

        kwargs = _structured_output_kwargs(req, "tongyi")

        assert kwargs == {"response_format": {"type": "json_object"}}

    def test_structured_output_kwargs_skips_tongyi_thinking(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
            thinking=True,
        )

        assert _structured_output_kwargs(req, "tongyi") == {}

    def test_structured_output_kwargs_for_deepseek_json_object(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
        )

        kwargs = _structured_output_kwargs(req, "deepseek")

        assert kwargs == {"response_format": {"type": "json_object"}}

    def test_structured_output_kwargs_for_kimi_json_object(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
            response_schema_name="test_schema",
            response_schema_strict=True,
        )

        kwargs = _structured_output_kwargs(req, "kimi", "moonshot-v1-8k")

        assert kwargs == {"response_format": {"type": "json_object"}}

    def test_structured_output_kwargs_uses_json_object_for_unknown_kimi_model(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
        )

        assert _structured_output_kwargs(req, "kimi", "kimi-thinking-preview") == {
            "response_format": {"type": "json_object"}
        }

    def test_structured_output_kwargs_for_ollama_json_schema(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
            response_schema_name="test_schema",
        )

        kwargs = _structured_output_kwargs(req, "ollama", "llama3.2")

        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["schema"] == schema

    def test_structured_output_kwargs_for_unknown_custom_endpoint_is_prompt_only(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
            response_schema_name="test_schema",
        )

        kwargs = _structured_output_kwargs(req, "custom", "Qwen3-235B-A22B")

        assert kwargs == {}

    def test_structured_output_kwargs_for_minimax_text_schema(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
            response_schema_name="test_schema",
        )

        kwargs = _structured_output_kwargs(req, "minimax", "MiniMax-Text-01")

        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["schema"] == schema

    def test_structured_output_kwargs_skips_minimax_m2(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
        )

        assert _structured_output_kwargs(req, "minimax", "MiniMax-M2.7-highspeed") == {}

    def test_structured_output_kwargs_for_volcengine_ark_seed_2_lite(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
            response_schema_name="test_schema",
        )

        kwargs = _structured_output_kwargs(req, "volcengine_ark", "doubao-seed-2.0-lite")

        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["schema"] == schema

    def test_structured_output_kwargs_falls_back_to_json_object_for_unsupported_model(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
        )

        kwargs = _structured_output_kwargs(req, "volcengine_ark", "deepseek-v3.2")

        assert kwargs["response_format"]["type"] == "json_object"

    def test_anthropic_structured_output_kwargs_for_supported_model(self) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema=schema,
        )

        kwargs = _anthropic_structured_output_kwargs(req, "claude-sonnet-5")

        assert kwargs == {
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": schema,
                },
            },
        }

    def test_anthropic_structured_output_skips_model_without_schema_capability(self) -> None:
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "JSON"}],
            response_json_schema={"type": "object"},
        )

        assert _anthropic_structured_output_kwargs(req, "claude-3-sonnet-20240229") == {}


# ── Domestic adapter identity checks ────────────────────


class TestOpenCodeAdapter:
    def test_provider_name(self) -> None:
        adapter = OpenCodeAdapter(api_key="test-key")
        assert adapter.provider_name == "opencode"

    def test_default_model(self) -> None:
        adapter = OpenCodeAdapter(api_key="test-key")
        assert adapter.default_model == "deepseek-v4-flash"

    def test_base_url(self) -> None:
        adapter = OpenCodeAdapter(api_key="test-key")
        assert adapter._base_url == "https://opencode.ai/zen/go/v1"

    def test_configured_default_model(self) -> None:
        adapter = OpenCodeAdapter(api_key="test-key", default_model="glm-5.2")
        assert adapter.default_model == "glm-5.2"

    def test_thinking_mode_passthrough(self) -> None:
        """OpenCodeAdapter 不重写 _thinking_request_kwargs, 透传(返回空 dict)。"""
        adapter = OpenCodeAdapter(api_key="test-key")
        request = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
            thinking_mode="high",
        )
        # 基类 OpenAICompatibleAdapter 默认不注入思考参数
        assert adapter._thinking_request_kwargs(request, "glm-5.2") == {}


class TestDeepSeekAdapter:
    def test_provider_name(self) -> None:
        adapter = DeepSeekAdapter(api_key="test-key")
        assert adapter.provider_name == "deepseek"

    def test_default_model(self) -> None:
        adapter = DeepSeekAdapter(api_key="test-key")
        assert adapter.default_model == "deepseek-v4-flash"

    def test_base_url(self) -> None:
        adapter = DeepSeekAdapter(api_key="test-key")
        assert "deepseek" in adapter._base_url

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("off", {"extra_body": {"thinking": {"type": "disabled"}}}),
            (
                "high",
                {
                    "extra_body": {
                        "thinking": {"type": "enabled"},
                        "reasoning_effort": "high",
                    }
                },
            ),
            (
                "max",
                {
                    "extra_body": {
                        "thinking": {"type": "enabled"},
                        "reasoning_effort": "max",
                    }
                },
            ),
        ],
    )
    def test_v4_reasoning_mode_maps_to_official_request_fields(
        self,
        mode: str,
        expected: dict[str, object],
    ) -> None:
        adapter = DeepSeekAdapter(api_key="test-key")
        request = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
            thinking_mode=mode,
        )

        assert adapter._thinking_request_kwargs(request, "deepseek-v4-pro") == expected


class TestTongyiAdapter:
    def test_provider_name(self) -> None:
        adapter = TongyiAdapter(api_key="test-key")
        assert adapter.provider_name == "tongyi"

    def test_default_model(self) -> None:
        adapter = TongyiAdapter(api_key="test-key")
        assert adapter.default_model == ""

    def test_configured_default_model(self) -> None:
        adapter = TongyiAdapter(api_key="test-key", default_model="qwen-turbo")
        assert adapter.default_model == "qwen-turbo"

    def test_base_url(self) -> None:
        adapter = TongyiAdapter(api_key="test-key")
        assert "dashscope" in adapter._base_url


class TestKimiAdapter:
    def test_provider_name(self) -> None:
        adapter = KimiAdapter(api_key="test-key")
        assert adapter.provider_name == "kimi"

    def test_default_model(self) -> None:
        adapter = KimiAdapter(api_key="test-key")
        assert adapter.default_model == "kimi-k2.6"

    def test_base_url(self) -> None:
        adapter = KimiAdapter(api_key="test-key")
        assert "moonshot" in adapter._base_url


class TestVolcengineArkAdapter:
    def test_provider_name(self) -> None:
        adapter = VolcengineArkAdapter(api_key="test-key")
        assert adapter.provider_name == "volcengine_ark"
        assert adapter._write_timeout_s == 120.0

    def test_default_model(self) -> None:
        adapter = VolcengineArkAdapter(api_key="test-key")
        assert adapter.default_model == "doubao-seed-2.0-pro"

    def test_default_base_url(self) -> None:
        adapter = VolcengineArkAdapter(api_key="test-key")
        assert adapter._base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"

    def test_base_url_trailing_slash_stripped(self) -> None:
        adapter = VolcengineArkAdapter(
            api_key="test-key",
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3/",
        )
        assert adapter._base_url == "https://ark.cn-beijing.volces.com/api/plan/v3"

    def test_custom_base_url(self) -> None:
        adapter = VolcengineArkAdapter(
            api_key="test-key",
            base_url="https://ark.ap-southeast-1.volces.com/api/plan/v3",
            default_model="doubao-seed-2.0-lite",
        )
        assert adapter._base_url == "https://ark.ap-southeast-1.volces.com/api/plan/v3"
        assert adapter.default_model == "doubao-seed-2.0-lite"

    def test_thinking_extra_body(self) -> None:
        adapter = VolcengineArkAdapter(api_key="test-key")
        assert adapter._thinking_extra_body() == {"thinking": {"type": "enabled"}}

    def test_health_error_property(self) -> None:
        adapter = VolcengineArkAdapter(api_key="test-key")
        assert adapter.last_health_error == ""


class TestMiMoAdapter:
    def test_provider_defaults(self) -> None:
        adapter = MiMoAdapter(api_key="test-key")

        assert adapter.provider_name == "mimo"
        assert adapter.default_model == "mimo-v2.5-pro"
        assert adapter._base_url == "https://token-plan-cn.xiaomimimo.com/v1"

    def test_request_controls_follow_mimo_api(self) -> None:
        adapter = MiMoAdapter(api_key="test-key")

        assert adapter._completion_token_limit_kwargs(4096) == {"max_completion_tokens": 4096}
        assert adapter._thinking_extra_body_for_request(True) == {"thinking": {"type": "enabled"}}
        assert adapter._thinking_extra_body_for_request(False) == {"thinking": {"type": "disabled"}}

    @pytest.mark.parametrize(("mode", "state"), [("off", "disabled"), ("on", "enabled")])
    def test_route_mode_is_sent_explicitly(self, mode: str, state: str) -> None:
        adapter = MiMoAdapter(api_key="test-key")
        request = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
            thinking_mode=mode,
        )

        assert adapter._thinking_request_kwargs(request, "mimo-v2.5-pro") == {
            "extra_body": {"thinking": {"type": state}}
        }


class TestMiniMaxAdapter:
    def test_m27_is_forced_thinking_and_m3_supports_adaptive_toggle(self) -> None:
        adapter = MiniMaxAdapter("test-key")

        m27_off = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
            thinking_mode="off",
        )
        m3_off = m27_off.model_copy(update={"model_id": "MiniMax-M3"})
        m3_adaptive = m3_off.model_copy(update={"thinking_mode": "adaptive"})

        assert adapter._thinking_request_kwargs(m27_off, "MiniMax-M2.7-highspeed") == {
            "extra_body": {"reasoning_split": True}
        }
        assert adapter._thinking_request_kwargs(m3_off, "MiniMax-M3") == {
            "extra_body": {"thinking": {"type": "disabled"}}
        }
        assert adapter._thinking_request_kwargs(m3_adaptive, "MiniMax-M3") == {
            "extra_body": {
                "thinking": {"type": "adaptive"},
                "reasoning_split": True,
            }
        }

    @pytest.mark.asyncio
    async def test_complete_passes_structured_output_for_text_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys
        import types

        captured: dict[str, object] = {}

        class _AsyncOpenAI:
            def __init__(
                self,
                *,
                api_key: str,
                base_url: str,
                timeout: object,
                max_retries: int = 0,
            ) -> None:
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: object):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(content='{"answer":"ok"}'),
                        )
                    ],
                    usage=types.SimpleNamespace(
                        prompt_tokens=3,
                        completion_tokens=2,
                        total_tokens=5,
                    ),
                )

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_AsyncOpenAI))

        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        adapter = MiniMaxAdapter("test-key", default_model="MiniMax-Text-01")
        response = await adapter.complete(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "JSON"}],
                response_json_schema=schema,
                response_schema_name="test_schema",
            )
        )

        response_format = captured["response_format"]
        assert isinstance(response_format, dict)
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"] == schema
        assert captured["max_completion_tokens"] == 4096
        assert "max_tokens" not in captured
        assert response.content == '{"answer":"ok"}'
        assert response.structured_output_mode == "json_schema"
        assert response.structured_output_reason == "profile:minimax/MiniMax-Text-01"


# ── _parse_task_routing helper ───────────────────────────


class TestParseTaskRouting:
    """Tests for CLI _parse_task_routing()."""

    def test_full_spec(self) -> None:
        result = parse_task_routing(
            '{"draft":"deepseek:deepseek-chat","evaluate":"tongyi:qwen-plus"}'
        )
        assert TaskType.DRAFT in result
        assert result[TaskType.DRAFT].provider == "deepseek"
        assert result[TaskType.DRAFT].model_id == "deepseek-chat"
        assert result[TaskType.EVALUATE].provider == "tongyi"
        assert result[TaskType.EVALUATE].model_id == "qwen-plus"

    def test_provider_only(self) -> None:
        result = parse_task_routing('{"draft":"deepseek"}')
        assert result[TaskType.DRAFT].provider == "deepseek"
        assert result[TaskType.DRAFT].model_id is None

    def test_dict_spec(self) -> None:
        result = parse_task_routing(
            '{"check_alignment":{"provider":"Tongyi","model_id":"qwen-plus"}}'
        )
        assert result[TaskType.CHECK_ALIGNMENT].provider == "tongyi"
        assert result[TaskType.CHECK_ALIGNMENT].model_id == "qwen-plus"

    def test_whitespace_spec(self) -> None:
        result = parse_task_routing('{"draft":"  deepseek:deepseek-chat  "}')
        assert result[TaskType.DRAFT].provider == "deepseek"
        assert result[TaskType.DRAFT].model_id == "deepseek-chat"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(TaskRoutingParseError):
            parse_task_routing("not-json")

    def test_non_object_json_raises(self) -> None:
        with pytest.raises(TaskRoutingParseError):
            parse_task_routing('["draft:deepseek"]')

    def test_unknown_task_name_skipped(self) -> None:
        result = parse_task_routing('{"nonexistent_task":"deepseek:x"}')
        assert result == {}

    def test_empty_string_returns_empty(self) -> None:
        assert parse_task_routing("") == {}

    def test_capability_flags_in_csv_syntax(self) -> None:
        result = parse_task_routing('{"evaluate":"tongyi:qwen-turbo,thinking,multi"}')
        route = result[TaskType.EVALUATE]
        assert route.provider == "tongyi"
        assert route.model_id == "qwen-turbo"
        assert route.thinking is True
        assert route.multi_turn is True

    def test_colon_thinking_capability_syntax_is_supported(self) -> None:
        result = parse_task_routing('{"evaluate":"tongyi:qwen-turbo:thinking"}')
        route = result[TaskType.EVALUATE]
        assert route.provider == "tongyi"
        assert route.model_id == "qwen-turbo"
        assert route.thinking is True
        assert route.multi_turn is False

    def test_dict_spec_supports_multi_alias(self) -> None:
        result = parse_task_routing(
            '{"evaluate":{"provider":"tongyi","model":"qwen-turbo","multi":true}}'
        )
        route = result[TaskType.EVALUATE]
        assert route.provider == "tongyi"
        assert route.model_id == "qwen-turbo"
        assert route.multi_turn is True


# ── Task routing in ModelRouter ──────────────────────────


class TestTaskRouting:
    """Tests for per-task provider routing in ModelRouter."""

    @pytest.fixture
    def multi_router(self) -> ModelRouter:
        """Router with mock adapters for 'mock' and 'alt' providers."""
        main = MockAdapter()
        alt = MockAdapter()
        return ModelRouter(
            adapters={"mock": main, "alt": alt},
            default_provider="mock",
            task_providers={
                TaskType.DRAFT: TaskRouteOverride(provider="alt", model_id="alt-model-v1"),
                TaskType.EVALUATE: TaskRouteOverride(provider="alt"),
            },
        )

    async def test_task_override_routes_to_alt_provider(self, multi_router: ModelRouter) -> None:
        """DRAFT task should be routed to 'alt' provider with specific model."""
        request = ModelRequest(
            task_type=TaskType.DRAFT,
            messages=[{"role": "user", "content": "test"}],
        )
        response = await multi_router.route(request)
        # Router injects "alt-model-v1" into request, mock echoes it back
        assert response.model_id == "alt-model-v1"

    async def test_task_without_override_uses_default(self, multi_router: ModelRouter) -> None:
        """BEATS task (no override) should use default provider."""
        request = ModelRequest(
            task_type=TaskType.BEATS,
            messages=[{"role": "user", "content": "test"}],
        )
        response = await multi_router.route(request)
        # BEATS tier is Standard → gpt-4o-mini
        assert response.model_id == "gpt-4o-mini"

    async def test_explicit_provider_overrides_task_routing(
        self, multi_router: ModelRouter
    ) -> None:
        """Explicit provider= argument should take priority over task_providers."""
        request = ModelRequest(
            task_type=TaskType.DRAFT,
            messages=[{"role": "user", "content": "test"}],
        )
        response = await multi_router.route(request, provider="mock")
        # explicit provider wins → model falls through to tier → PREMIUM → gpt-4o
        assert response.model_id == "gpt-4o"

    def test_task_route_override_dataclass(self) -> None:
        override = TaskRouteOverride(provider="deepseek", model_id="deepseek-chat")
        assert override.provider == "deepseek"
        assert override.model_id == "deepseek-chat"

    def test_task_route_override_no_model(self) -> None:
        override = TaskRouteOverride(provider="tongyi")
        assert override.provider == "tongyi"
        assert override.model_id is None

    async def test_task_override_injects_capabilities(self) -> None:
        class _SpyAdapter(MockAdapter):
            def __init__(self) -> None:
                super().__init__()
                self.last_request: ModelRequest | None = None

            async def complete(self, request: ModelRequest):  # type: ignore[override]
                self.last_request = request
                return await super().complete(request)

        spy = _SpyAdapter()
        router = ModelRouter(
            adapters={"mock": spy},
            default_provider="mock",
            task_providers={
                TaskType.EVALUATE: TaskRouteOverride(
                    provider="mock",
                    model_id="mock-eval",
                    thinking=True,
                    multi_turn=True,
                )
            },
        )

        await router.route(
            ModelRequest(task_type=TaskType.EVALUATE, messages=[{"role": "user", "content": "t"}])
        )

        assert spy.last_request is not None
        assert spy.last_request.model_id == "mock-eval"
        assert spy.last_request.thinking is True
        assert spy.last_request.thinking_mode == "on"
        assert spy.last_request.multi_turn is True

    async def test_task_override_preserves_reasoning_effort_mode(self) -> None:
        class _SpyAdapter(MockAdapter):
            def __init__(self) -> None:
                super().__init__()
                self.last_request: ModelRequest | None = None

            async def complete(self, request: ModelRequest):  # type: ignore[override]
                self.last_request = request
                return await super().complete(request)

        spy = _SpyAdapter()
        router = ModelRouter(
            adapters={"mock": spy},
            default_provider="mock",
            task_providers={
                TaskType.DRAFT: TaskRouteOverride(
                    provider="mock",
                    model_id="mock-draft",
                    thinking=True,
                    thinking_mode="max",
                )
            },
        )

        await router.route(
            ModelRequest(task_type=TaskType.DRAFT, messages=[{"role": "user", "content": "t"}])
        )

        assert spy.last_request is not None
        assert spy.last_request.thinking is True
        assert spy.last_request.thinking_mode == "max"


def test_default_provider_accepts_provider_model_syntax() -> None:
    settings = Settings(default_provider="deepseek:deepseek-chat")
    builder = ModelRouterBuilder(settings)
    builder.adapters = {"deepseek": object()}
    assert builder._determine_default_provider() == "deepseek"
