"""Tests for gateway factory parsing, router error classification, and spending tracker."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.circuit_breaker import CircuitBreaker, CircuitState
from novel_forge.gateway.factory import (
    ModelRouterBuilder,
    TaskRoutingParseError,
    _apply_internal_task_fallback_defaults,
    _apply_internal_task_route_defaults,
    _split_provider_model,
    parse_task_fallback_routing,
    parse_task_routing,
)
from novel_forge.gateway.pricing import SpendingTracker, estimate_cost, get_known_models
from novel_forge.gateway.router import (
    ModelRouter,
    TaskRouteOverride,
    _is_retriable_error,
)
from novel_forge.gateway.task_circuit_breaker import TaskTypeCircuitBreaker
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk


class _StaticAdapter(ProviderAdapter):
    def __init__(self, provider_name: str, response: ModelResponse) -> None:
        self._provider_name = provider_name
        self._response = response
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return f"{self._provider_name}-default"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return self._response.model_copy(
            update={"model_id": request.model_id or self.default_model}
        )


class _FailingAdapter(ProviderAdapter):
    def __init__(self, provider_name: str, exc: Exception) -> None:
        self._provider_name = provider_name
        self._exc = exc
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return f"{self._provider_name}-default"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        raise self._exc


async def _no_retry_delay(_seconds: float) -> None:
    """Keep router retry tests deterministic and fast."""

    return None


class _StreamingAdapter(ProviderAdapter):
    def __init__(self, provider_name: str, chunks: list[str]) -> None:
        self._provider_name = provider_name
        self._chunks = chunks
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return f"{self._provider_name}-default"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(content="".join(self._chunks), model_id=request.model_id or "")

    async def stream(self, request: ModelRequest, *, on_final=None):
        self.calls.append(request)
        for chunk in self._chunks:
            yield chunk
        response = ModelResponse(
            content="".join(self._chunks),
            model_id=request.model_id or self.default_model or "",
            prompt_tokens=3,
            completion_tokens=len(self._chunks),
            total_tokens=3 + len(self._chunks),
        )
        self._last_stream_response = response
        if on_final is not None:
            on_final(response)


class _RacyStreamingAdapter(_StreamingAdapter):
    async def stream(self, request: ModelRequest, *, on_final=None):
        self.calls.append(request)
        for chunk in self._chunks:
            yield chunk
        response = ModelResponse(
            content="".join(self._chunks),
            model_id=request.model_id or self.default_model or "",
            prompt_tokens=5,
            completion_tokens=len(self._chunks),
            total_tokens=5 + len(self._chunks),
        )
        self._last_stream_response = ModelResponse(
            content="stale response from another stream",
            model_id="wrong-model",
            total_tokens=999,
        )
        if on_final is not None:
            on_final(response)


class _FailingStreamingAdapter(_StreamingAdapter):
    def __init__(self, provider_name: str, chunks: list[str], exc: Exception) -> None:
        super().__init__(provider_name, chunks)
        self._exc = exc

    async def stream(self, request: ModelRequest, *, on_final=None):
        del on_final
        self.calls.append(request)
        for chunk in self._chunks:
            yield chunk
        raise self._exc


class _LengthUntilBudgetAdapter(ProviderAdapter):
    def __init__(
        self,
        provider_name: str,
        *,
        threshold: int,
        model_id: str,
        truncated_content: str = '{"partial": true',
    ) -> None:
        self._provider_name = provider_name
        self._threshold = threshold
        self._model_id = model_id
        self._truncated_content = truncated_content
        self.calls: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def default_model(self) -> str | None:
        return self._model_id

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if request.max_tokens < self._threshold:
            return ModelResponse(
                content=self._truncated_content,
                model_id=request.model_id or self._model_id,
                prompt_tokens=80,
                completion_tokens=request.max_tokens,
                total_tokens=80 + request.max_tokens,
                finish_reason="length",
            )
        return ModelResponse(
            content='{"ok": true}',
            model_id=request.model_id or self._model_id,
            prompt_tokens=80,
            completion_tokens=256,
            total_tokens=336,
            finish_reason="stop",
        )


# ═══════════════════════════════════════════════════════════════════════════
# _split_provider_model
# ═══════════════════════════════════════════════════════════════════════════


class TestSplitProviderModel:
    @pytest.mark.parametrize(
        "spec,expected",
        [
            ("openai", ("openai", None, False, False)),
            ("openai:gpt-4o", ("openai", "gpt-4o", False, False)),
            ("openai:gpt-4o,thinking", ("openai", "gpt-4o", True, False)),
            ("openai:gpt-4o,thinking,multi", ("openai", "gpt-4o", True, True)),
            ("openai:gpt-4o:thinking", ("openai", "gpt-4o", True, False)),  # backward compat
            ("", ("", None, False, False)),
            ("   ", ("", None, False, False)),
            ("openai:gpt-4o,think", ("openai", "gpt-4o", True, False)),
            ("openai:gpt-4o,THINKING,MULTI", ("openai", "gpt-4o", True, True)),
        ],
    )
    def test_split_provider_model(self, spec, expected) -> None:
        assert _split_provider_model(spec) == expected

    @pytest.mark.parametrize("alias", ("multi_turn", "multi-turn", "multiturn"))
    def test_multi_turn_aliases(self, alias) -> None:
        result = _split_provider_model(f"openai:gpt-4o,thinking,{alias}")
        assert result[3] is True


class TestStreamRoute:
    def test_openai_connection_errors_are_transient_network_failures(self) -> None:
        from novel_forge.gateway.router import (
            _aggregate_route_failure_is_transient,
            _failure_category,
        )

        class APIConnectionError(Exception):
            pass

        error = APIConnectionError(
            "Connection error: [Errno 8] nodename nor servname provided, or not known"
        )

        assert _failure_category(error) == "network_error"
        assert _aggregate_route_failure_is_transient(["timeout", "network_error"]) is True

    def test_partial_stream_body_stays_out_of_durable_error_context(self) -> None:
        error = ModelGatewayError(
            "stream failed",
            is_transient=True,
            failure_categories=["network_error"],
        ).attach_partial_stream(
            text='{"chapter_contracts":[{"chapter_number":29}]}',
            reasoning="reasoning",
            provider="minimax",
            model_id="MiniMax-M3",
        )

        assert error.partial_text.startswith('{"chapter_contracts"')
        assert error.context["partial_stream"]["text_length"] == len(error.partial_text)
        assert error.partial_text not in str(error)
        assert "text" not in error.context["partial_stream"]

    async def test_stream_fallback_preserves_longest_partial_from_failed_route(self) -> None:
        api_connection_error = type("APIConnectionError", (Exception,), {})
        primary = _FailingStreamingAdapter(
            "primary",
            ['{"chapter_contracts":[', '{"chapter_number":29}'],
            asyncio.TimeoutError("stream idle timeout"),
        )
        fallback = _FailingStreamingAdapter(
            "fallback",
            [],
            api_connection_error("Connection error."),
        )
        router = ModelRouter(
            adapters={"primary": primary, "fallback": fallback},
            default_provider="primary",
            task_fallbacks={
                TaskType.PLAN_CHAPTER_CONTRACTS: [
                    TaskRouteOverride(provider="fallback", model_id="fallback-model")
                ]
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        with pytest.raises(ModelGatewayError) as exc_info:
            await router.stream_route(
                ModelRequest(
                    task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
                    messages=[{"role": "user", "content": "plan"}],
                    max_tokens=64,
                )
            )

        error = exc_info.value
        assert error.is_transient_error is True
        assert error.context["failure_categories"] == ["timeout", "network_error"]
        assert error.partial_text == '{"chapter_contracts":[{"chapter_number":29}'
        assert error.partial_provider == "primary"
        assert error.partial_model_id

    async def test_stream_route_resets_partial_output_before_fallback_chunks(self) -> None:
        primary = _FailingStreamingAdapter(
            "primary",
            ["旧", "片段"],
            asyncio.TimeoutError("stream idle timeout"),
        )
        fallback = _StreamingAdapter("fallback", ["新", "正文"])
        router = ModelRouter(
            adapters={"primary": primary, "fallback": fallback},
            default_provider="primary",
            task_fallbacks={
                TaskType.DRAFT_CHAPTER: [
                    TaskRouteOverride(provider="fallback", model_id="fallback-model")
                ]
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        observed: list[StreamChunk] = []

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "写一章"}],
                max_tokens=64,
            ),
            on_chunk=observed.append,
        )

        visible = ""
        for chunk in observed:
            if chunk.reset:
                visible = ""
            visible += chunk.content
        assert response.content == "新正文"
        assert visible == response.content
        assert [(chunk.content, chunk.reset) for chunk in observed] == [
            ("旧", False),
            ("片段", False),
            ("", True),
            ("新", False),
            ("正文", False),
        ]
        assert observed[2].reset_reason

    def test_context_skip_does_not_make_later_timeout_non_transient(self) -> None:
        from novel_forge.gateway.router import _aggregate_route_failure_is_transient

        assert _aggregate_route_failure_is_transient(["context_length"]) is False
        assert _aggregate_route_failure_is_transient(["context_length", "timeout"]) is True
        assert (
            _aggregate_route_failure_is_transient(["context_length", "circuit_open", "timeout"])
            is True
        )

    async def test_stream_route_fallback_adapter_yields_single_chunk(self) -> None:
        adapter = _StaticAdapter("mock", ModelResponse(content="完整文本", model_id=""))
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
        deltas: list[str] = []

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "写一章"}],
                max_tokens=64,
                temperature=0.7,
            ),
            on_delta=deltas.append,
        )

        assert response.content == "完整文本"
        assert deltas == ["完整文本"]
        assert len(adapter.calls) == 1

    async def test_stream_route_skips_prompt_only_provider_for_native_json_task(self) -> None:
        minimax = _StaticAdapter("minimax", ModelResponse(content="不应调用", model_id=""))
        openai = _StaticAdapter("openai", ModelResponse(content='{"ok": true}', model_id=""))
        router = ModelRouter(
            adapters={"minimax": minimax, "openai": openai},
            default_provider="minimax",
            task_fallbacks={
                TaskType.EXTRACT_CANON: [
                    TaskRouteOverride(provider="openai", model_id="gpt-4o"),
                ],
            },
        )

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.EXTRACT_CANON,
                messages=[{"role": "user", "content": "extract"}],
                response_json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["ok"],
                    "properties": {"ok": {"type": "boolean"}},
                },
                require_native_structured_output=True,
            )
        )

        assert response.content == '{"ok": true}'
        assert minimax.calls == []
        assert len(openai.calls) == 1

    async def test_native_json_task_fails_before_call_when_only_prompt_only_routes_exist(
        self,
    ) -> None:
        minimax = _StaticAdapter("minimax", ModelResponse(content="不应调用", model_id=""))
        router = ModelRouter(adapters={"minimax": minimax}, default_provider="minimax")

        with pytest.raises(ModelGatewayError, match="requires native structured output"):
            await router.route(
                ModelRequest(
                    task_type=TaskType.EXTRACT_CANON,
                    messages=[{"role": "user", "content": "extract"}],
                    response_json_schema={
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["ok"],
                        "properties": {"ok": {"type": "boolean"}},
                    },
                    require_native_structured_output=True,
                )
            )

        assert minimax.calls == []

    async def test_native_json_task_accepts_profile_id_route_with_json_capable_fallback(
        self,
    ) -> None:
        """Regression: profile-based routing stores the full profile_id (e.g.
        ``"minimax:MiniMax-M3"``) in ``TaskRouteOverride.provider``.

        The structured-output capability resolver must normalize this to the
        bare provider name so it can match provider defaults.  Before the fix,
        the profile_id fell through to ``provider_prompt_only_default`` and
        rejected *every* route -- including JSON-capable fallbacks -- causing
        ``ModelGatewayError`` on tasks like ``extract_candidate_state_deltas``.
        """
        # Primary: minimax profile_id -> prompt-only (correctly rejected)
        # Fallback: volcengine_ark profile_id -> JSON_OBJECT (must be accepted)
        minimax = _StaticAdapter("minimax", ModelResponse(content="不应调用", model_id=""))
        volcengine = _StaticAdapter(
            "volcengine_ark", ModelResponse(content='{"candidates": []}', model_id="")
        )
        router = ModelRouter(
            adapters={
                "minimax:MiniMax-M3": minimax,
                "minimax": minimax,
                "volcengine_ark:deepseek-v4-flash": volcengine,
                "volcengine_ark": volcengine,
            },
            default_provider="minimax:MiniMax-M3",
            task_providers={
                TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: TaskRouteOverride(
                    provider="minimax:MiniMax-M3",
                    model_id="MiniMax-M3",
                ),
            },
            task_fallbacks={
                TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: [
                    TaskRouteOverride(
                        provider="volcengine_ark:deepseek-v4-flash",
                        model_id="deepseek-v4-flash",
                    ),
                ],
            },
        )

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
                messages=[{"role": "user", "content": "extract"}],
                response_json_schema={
                    "type": "object",
                    "additionalProperties": True,
                    "required": ["candidates"],
                    "properties": {"candidates": {"type": "array"}},
                },
                require_native_structured_output=True,
            )
        )

        assert response.content == '{"candidates": []}'
        assert minimax.calls == []
        assert len(volcengine.calls) == 1

    async def test_native_json_task_rejects_all_profile_id_routes_when_none_json_capable(
        self,
    ) -> None:
        """When all routes (including profile_id-style) are genuinely prompt-only,
        the error still fires with a clear reason -- not the generic fallback.
        """
        minimax = _StaticAdapter("minimax", ModelResponse(content="不应调用", model_id=""))
        router = ModelRouter(
            adapters={"minimax:MiniMax-M3": minimax, "minimax": minimax},
            default_provider="minimax:MiniMax-M3",
            task_providers={
                TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: TaskRouteOverride(
                    provider="minimax:MiniMax-M3",
                    model_id="MiniMax-M3",
                ),
            },
        )

        with pytest.raises(
            ModelGatewayError, match="requires native structured output"
        ) as exc_info:
            await router.route(
                ModelRequest(
                    task_type=TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
                    messages=[{"role": "user", "content": "extract"}],
                    response_json_schema={
                        "type": "object",
                        "additionalProperties": True,
                        "required": ["candidates"],
                        "properties": {"candidates": {"type": "array"}},
                    },
                    require_native_structured_output=True,
                )
            )

        # The reason must reference the matched profile entry, not the generic
        # "provider_prompt_only_default" tail that indicates the provider name
        # was not recognized at all.
        assert "provider_prompt_only_default" not in str(exc_info.value)
        assert "minimax" in str(exc_info.value)
        assert minimax.calls == []

    async def test_stream_route_true_stream_aggregates_multiple_chunks(self) -> None:
        adapter = _StreamingAdapter("mock", ["第一段", "第二段", "第三段"])
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
        deltas: list[str] = []

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.WAVE_CHAPTER,
                messages=[{"role": "user", "content": "串联场景"}],
                max_tokens=64,
                temperature=0.7,
            ),
            on_delta=deltas.append,
        )

        assert response.content == "第一段第二段第三段"
        assert deltas == ["第一段", "第二段", "第三段"]
        assert response.total_tokens == 6

    async def test_stream_route_uses_per_call_final_response(self) -> None:
        adapter = _RacyStreamingAdapter("mock", ["正确", "正文"])
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")

        response = await router.stream_route(
            ModelRequest(
                task_type=TaskType.WAVE_CHAPTER,
                messages=[{"role": "user", "content": "串联场景"}],
                max_tokens=64,
                temperature=0.7,
            )
        )

        assert response.content == "正确正文"
        assert response.model_id == "gpt-4o"
        assert response.total_tokens == 7


# ═══════════════════════════════════════════════════════════════════════════
# parse_task_routing
# ═══════════════════════════════════════════════════════════════════════════


class TestParseTaskRouting:
    def test_empty_string(self) -> None:
        assert parse_task_routing("") == {}

    def test_simple_string_spec(self) -> None:
        result = parse_task_routing('{"draft_chapter":"openai:gpt-4o"}')
        assert TaskType.DRAFT_CHAPTER in result
        override = result[TaskType.DRAFT_CHAPTER]
        assert override.provider == "openai"
        assert override.model_id == "gpt-4o"

    def test_dict_spec(self) -> None:
        result = parse_task_routing(
            '{"draft_chapter":{"provider":"deepseek","model_id":"deepseek-chat","thinking":true}}'
        )
        override = result[TaskType.DRAFT_CHAPTER]
        assert override.provider == "deepseek"
        assert override.model_id == "deepseek-chat"
        assert override.thinking is True

    @pytest.mark.parametrize("invalid_input", ("{invalid json}", "[]"))
    def test_invalid_json_raises(self, invalid_input) -> None:
        with pytest.raises(TaskRoutingParseError):
            parse_task_routing(invalid_input)

    def test_unknown_task_type_skipped(self) -> None:
        result = parse_task_routing('{"unknown_task_xyz":"openai:gpt-4o"}')
        assert result == {}

    def test_empty_provider_skipped(self) -> None:
        result = parse_task_routing('{"draft_chapter":""}')
        assert result == {}

    def test_dict_spec_model_alias(self) -> None:
        result = parse_task_routing('{"draft_chapter":{"provider":"openai","model":"gpt-4o"}}')
        assert result[TaskType.DRAFT_CHAPTER].model_id == "gpt-4o"

    @pytest.mark.parametrize("key", ("multi_turn", "multi", "multiTurn"))
    def test_dict_spec_multi_turn_variants(self, key) -> None:
        result = parse_task_routing(
            json.dumps({"draft_chapter": {"provider": "openai", key: True}})
        )
        assert result[TaskType.DRAFT_CHAPTER].multi_turn is True

    def test_draft_chapter_route_inherits_to_wave_by_default(self) -> None:
        resolved = _apply_internal_task_route_defaults(
            {
                TaskType.DRAFT_CHAPTER: TaskRouteOverride(
                    provider="openai",
                    model_id="gpt-4o",
                    thinking=True,
                    multi_turn=True,
                )
            }
        )

        assert resolved is not None
        wave = resolved[TaskType.WAVE_CHAPTER]
        assert wave.provider == "openai"
        assert wave.model_id == "gpt-4o"
        assert wave.thinking is True
        assert wave.multi_turn is False

    def test_explicit_wave_route_is_not_overwritten_by_draft_default(self) -> None:
        resolved = _apply_internal_task_route_defaults(
            {
                TaskType.DRAFT_CHAPTER: TaskRouteOverride(
                    provider="openai",
                    model_id="gpt-4o",
                ),
                TaskType.WAVE_CHAPTER: TaskRouteOverride(
                    provider="deepseek",
                    model_id="deepseek-chat",
                ),
            }
        )

        assert resolved is not None
        wave = resolved[TaskType.WAVE_CHAPTER]
        assert wave.provider == "deepseek"
        assert wave.model_id == "deepseek-chat"


# ═══════════════════════════════════════════════════════════════════════════
# parse_task_fallback_routing
# ═══════════════════════════════════════════════════════════════════════════


class TestParseTaskFallbackRouting:
    def test_empty_string(self) -> None:
        assert parse_task_fallback_routing("") == {}

    def test_string_specs(self) -> None:
        result = parse_task_fallback_routing(
            '{"draft_chapter":["openai:gpt-4o","deepseek:deepseek-chat,thinking"]}'
        )
        routes = result[TaskType.DRAFT_CHAPTER]
        assert len(routes) == 2
        assert routes[0].provider == "openai"
        assert routes[1].thinking is True

    def test_dict_specs(self) -> None:
        result = parse_task_fallback_routing(
            '{"draft_chapter":[{"provider":"tongyi","model_id":"qwen-max"}]}'
        )
        assert len(result[TaskType.DRAFT_CHAPTER]) == 1

    def test_invalid_json(self) -> None:
        with pytest.raises(TaskRoutingParseError):
            parse_task_fallback_routing("{bad}")

    def test_non_list_value_skipped(self) -> None:
        result = parse_task_fallback_routing('{"draft_chapter":"not-a-list"}')
        assert result == {}

    def test_max_3_routes(self) -> None:
        specs = json.dumps(
            {"draft_chapter": ["openai:gpt-4o", "deepseek:chat", "tongyi:qwen", "kimi:moonshot"]}
        )
        result = parse_task_fallback_routing(specs)
        assert len(result[TaskType.DRAFT_CHAPTER]) == 3

    def test_draft_chapter_fallbacks_inherit_to_wave_by_default(self) -> None:
        resolved = _apply_internal_task_fallback_defaults(
            {
                TaskType.DRAFT_CHAPTER: [
                    TaskRouteOverride(
                        provider="openai",
                        model_id="gpt-4o",
                        thinking=True,
                        multi_turn=True,
                    )
                ]
            }
        )

        assert resolved is not None
        wave_routes = resolved[TaskType.WAVE_CHAPTER]
        assert len(wave_routes) == 1
        assert wave_routes[0].provider == "openai"
        assert wave_routes[0].model_id == "gpt-4o"
        assert wave_routes[0].thinking is True
        assert wave_routes[0].multi_turn is False


# ═══════════════════════════════════════════════════════════════════════════
# SpendingTracker
# ═══════════════════════════════════════════════════════════════════════════


class TestTaskCircuitBreakerFactory:
    def test_opt_in_builds_shared_task_breaker(self) -> None:
        builder = ModelRouterBuilder(
            Settings(
                _env_file=None,
                task_circuit_breaker_enabled=True,
                task_circuit_breaker_threshold=4,
                task_circuit_breaker_recovery_s=75,
            )
        )

        breaker = builder._build_task_circuit_breaker()

        assert isinstance(breaker, TaskTypeCircuitBreaker)
        assert breaker.get_failure_count(TaskType.CHECK_CONTINUITY) == 0

    def test_disabled_task_breaker_is_not_constructed(self) -> None:
        builder = ModelRouterBuilder(Settings(_env_file=None, task_circuit_breaker_enabled=False))

        assert builder._build_task_circuit_breaker() is None


class TestSpendingTracker:
    def test_no_limits_no_error(self) -> None:
        tracker = SpendingTracker()
        tracker.record(1.0)
        tracker.check_budget()

    def test_record_zero_ignored(self) -> None:
        tracker = SpendingTracker(daily_limit=10.0)
        tracker.record(0.0)
        tracker.record(-1.0)
        assert tracker.daily_spent == 0.0

    @pytest.mark.parametrize(
        "daily_limit,monthly_limit,amounts,should_exceed",
        [
            (10.0, 0.0, [5.0, 5.0], True),  # daily exceeded
            (0.0, 100.0, [50.0, 50.0], True),  # monthly exceeded
            (10.0, 0.0, [10.0], True),  # at exact limit
            (100.0, 0.0, [50.0], False),  # under threshold
        ],
    )
    def test_budget_limits(self, daily_limit, monthly_limit, amounts, should_exceed) -> None:
        from novel_forge.core.exceptions import BudgetExceededError

        tracker = SpendingTracker(daily_limit=daily_limit, monthly_limit=monthly_limit)
        for amount in amounts:
            tracker.record(amount)
        if should_exceed:
            with pytest.raises(BudgetExceededError):
                tracker.check_budget()
        else:
            tracker.check_budget()  # should not raise

    def test_daily_spent_property(self) -> None:
        tracker = SpendingTracker()
        tracker.record(3.5)
        tracker.record(2.5)
        assert tracker.daily_spent == 6.0

    def test_monthly_spent_property(self) -> None:
        tracker = SpendingTracker()
        tracker.record(7.0)
        assert tracker.monthly_spent == 7.0


# ═══════════════════════════════════════════════════════════════════════════
# _is_retriable_error
# ═══════════════════════════════════════════════════════════════════════════


class TestIsRetriableError:
    @pytest.mark.parametrize(
        "status_code,expected",
        [
            (429, True),
            (500, True),
            (503, True),
            (504, True),
            (501, False),
            (400, False),
        ],
    )
    def test_status_code_retriability(self, status_code, expected) -> None:
        exc = Exception()
        exc.status_code = status_code
        assert _is_retriable_error(exc) is expected

    def test_status_attribute(self) -> None:
        exc = Exception()
        exc.status = 429
        assert _is_retriable_error(exc) is True

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Rate limited: 429 Too Many Requests", True),
            ("Error 503 Service Unavailable", True),
            ("some error", False),
        ],
    )
    def test_message_parsing(self, message, expected) -> None:
        exc = RuntimeError(message)
        assert _is_retriable_error(exc) is expected

    def test_no_status_not_retriable(self) -> None:
        exc = ValueError("some error")
        assert _is_retriable_error(exc) is False

    def test_model_gateway_transient_flag(self) -> None:
        exc = ModelGatewayError("provider temporarily unavailable", is_transient=True)
        assert _is_retriable_error(exc) is True


# ═══════════════════════════════════════════════════════════════════════════
# estimate_cost edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEstimateCostEdgeCases:
    def test_only_prompt_tokens(self) -> None:
        cost = estimate_cost("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0)
        assert abs(cost - 0.15) < 1e-10

    def test_only_completion_tokens(self) -> None:
        cost = estimate_cost("gpt-4o-mini", prompt_tokens=0, completion_tokens=1_000_000)
        assert abs(cost - 0.60) < 1e-10

    def test_large_token_counts(self) -> None:
        cost = estimate_cost("gpt-4", prompt_tokens=100_000, completion_tokens=50_000)
        assert cost > 0

    def test_hunyuan_model(self) -> None:
        cost = estimate_cost("hunyuan-turbos-latest", prompt_tokens=1000, completion_tokens=500)
        assert cost > 0

    def test_qwen_coder_model(self) -> None:
        cost = estimate_cost("qwen3-coder-plus", prompt_tokens=1000, completion_tokens=500)
        assert cost > 0


class TestGetKnownModels:
    def test_returns_sorted_list(self) -> None:
        models = get_known_models()
        assert models == sorted(models)
        assert len(models) > 20


# ═══════════════════════════════════════════════════════════════════════════
# ModelRouter — route resolution and fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestModelRouterRouteResolution:
    def _make_router(self, **kwargs) -> ModelRouter:
        return ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
            **kwargs,
        )

    def test_normalize_provider_strips_profile_id_suffix(self) -> None:
        from novel_forge.gateway.router import _normalize_provider_for_capability

        assert _normalize_provider_for_capability("minimax:MiniMax-M3") == "minimax"
        assert (
            _normalize_provider_for_capability("volcengine_ark:deepseek-v4-flash")
            == "volcengine_ark"
        )
        assert _normalize_provider_for_capability("tongyi:deepseek-v4-flash") == "tongyi"
        # Bare provider names pass through unchanged
        assert _normalize_provider_for_capability("minimax") == "minimax"
        assert _normalize_provider_for_capability("OPENAI") == "openai"
        assert _normalize_provider_for_capability("") == ""

    def test_task_override_takes_precedence(self) -> None:
        router = self._make_router(
            task_providers={
                TaskType.DRAFT_CHAPTER: TaskRouteOverride(
                    provider="mock", model_id="override-model"
                ),
            },
        )
        assert router._task_providers[TaskType.DRAFT_CHAPTER].model_id == "override-model"

    def test_fallback_chain_configured(self) -> None:
        router = self._make_router(
            task_fallbacks={
                TaskType.DRAFT_CHAPTER: [
                    TaskRouteOverride(provider="mock", model_id="fb1"),
                    TaskRouteOverride(provider="mock", model_id="fb2"),
                ],
            },
        )
        fallbacks = router._task_fallbacks[TaskType.DRAFT_CHAPTER]
        assert len(fallbacks) == 2

    async def test_context_overflow_skips_primary_and_uses_larger_fallback(
        self,
        monkeypatch,
    ) -> None:
        adapter = _StaticAdapter(
            "mock",
            ModelResponse(content="完整上下文已处理", model_id=""),
        )
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
            task_providers={
                TaskType.DRAFT_CHAPTER: TaskRouteOverride(
                    provider="mock",
                    model_id="tiny-context",
                ),
            },
            task_fallbacks={
                TaskType.DRAFT_CHAPTER: [
                    TaskRouteOverride(provider="mock", model_id="large-context")
                ],
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        monkeypatch.setattr(
            "novel_forge.gateway.router.get_model_context_window",
            lambda model_id: 120 if model_id == "tiny-context" else 8192,
        )
        events: list[tuple[str, dict]] = []
        router.add_observer(lambda event, payload: events.append((event, payload)))

        response = await router.route(
            ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "必要上下文" * 200}],
                max_tokens=128,
            )
        )

        assert response.content == "完整上下文已处理"
        assert [call.model_id for call in adapter.calls] == ["large-context"]
        skipped = [payload for event, payload in events if event == "api_route_context_skipped"]
        assert skipped[0]["hard_truncation_allowed"] is False
        assert skipped[0]["required_context_preserved"] is True

    def test_resolve_tier_default(self) -> None:
        router = self._make_router()
        tier = router._resolve_tier(TaskType.DRAFT_CHAPTER)
        assert tier is not None

    def test_default_tier_for_unknown_task(self) -> None:
        from novel_forge.common.constants import ModelTier

        router = self._make_router(default_tier=ModelTier.BUDGET)
        tier = router._resolve_tier(TaskType.SUMMARIZE_SCENE)
        assert tier == ModelTier.BUDGET

    def test_tier_to_model_mapping(self) -> None:
        from novel_forge.common.constants import ModelTier

        router = self._make_router(
            tier_to_model={
                ModelTier.PREMIUM: "premium-model",
                ModelTier.STANDARD: "standard-model",
                ModelTier.BUDGET: "budget-model",
            },
        )
        assert router._tier_to_model[ModelTier.PREMIUM] == "premium-model"

    async def test_result_only_structured_tasks_disable_thinking_override(self) -> None:
        adapter = _StaticAdapter(
            "mock",
            ModelResponse(
                content='{"claims":[],"summary":"ok"}',
                model_id="",
                finish_reason="stop",
            ),
        )
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
            task_providers={
                TaskType.EXTRACT_INIT_COHERENCE_CLAIMS: TaskRouteOverride(
                    provider="mock",
                    model_id="claims-model",
                    thinking=True,
                    multi_turn=True,
                )
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        await router.route(
            ModelRequest(
                task_type=TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                messages=[{"role": "user", "content": "extract"}],
                thinking=True,
            )
        )

        assert adapter.calls[0].model_id == "claims-model"
        assert adapter.calls[0].thinking is False
        assert adapter.calls[0].multi_turn is True

    def test_observers_add_remove(self) -> None:
        router = self._make_router()
        events = []

        def observer(event, payload) -> None:
            events.append((event, payload))

        router.add_observer(observer)
        assert observer in router._observers
        router.remove_observer(observer)
        assert observer not in router._observers

    def test_observers_context_manager(self) -> None:
        router = self._make_router()
        events = []

        def observer(event, payload) -> None:
            events.append((event, payload))

        with router.observe(observer):
            assert observer in router._observers
        assert observer not in router._observers

    def test_remove_nonexistent_observer(self) -> None:
        router = self._make_router()
        router.remove_observer(lambda e, p: None)

    def test_duplicate_observer_ignored(self) -> None:
        router = self._make_router()

        def observer(event, payload) -> None:
            return None

        router.add_observer(observer)
        router.add_observer(observer)
        assert router._observers.count(observer) == 1

    async def test_empty_response_uses_fallback_route(self) -> None:
        primary = _StaticAdapter(
            "primary",
            ModelResponse(
                content="",
                model_id="primary-model",
                prompt_tokens=100,
                completion_tokens=9900,
                total_tokens=10000,
                finish_reason="length",
            ),
        )
        fallback = _StaticAdapter(
            "fallback",
            ModelResponse(
                content="有效草稿正文",
                model_id="fallback-model",
                prompt_tokens=80,
                completion_tokens=120,
                total_tokens=200,
                finish_reason="stop",
            ),
        )
        router = ModelRouter(
            adapters={"primary": primary, "fallback": fallback},
            default_provider="primary",
            task_fallbacks={
                TaskType.DRAFT_CHAPTER: [
                    TaskRouteOverride(provider="fallback", model_id="fallback-model"),
                ],
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        response = await router.route(
            ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "draft"}],
                max_tokens=9900,
            )
        )

        assert response.content == "有效草稿正文"
        assert len(primary.calls) == 2
        assert [call.max_tokens for call in primary.calls] == [9900, 16384]
        assert len(fallback.calls) == 1

    async def test_route_retry_loop_timeout_allows_length_retry_budget(self) -> None:
        adapter = _StaticAdapter(
            "primary",
            ModelResponse(content='{"unused": true}', model_id="gpt-4o"),
        )
        router = ModelRouter(
            adapters={"primary": adapter},
            default_provider="primary",
            request_timeout_s=0.01,
            workflow_timeout_s=0.08,
        )
        router._total_request_timeout_s = 0.015

        async def delayed_retry_loop(*_args, **_kwargs) -> ModelResponse:
            await asyncio.sleep(0.03)
            return ModelResponse(
                content='{"ok": true}',
                model_id="gpt-4o",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                finish_reason="stop",
            )

        router._retry_loop = delayed_retry_loop  # type: ignore[method-assign]

        assert router._total_request_timeout_s == pytest.approx(0.015)
        assert router._route_retry_loop_timeout_s(2) == pytest.approx(0.08)

        response = await router.route(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "evaluate"}],
                model_id="gpt-4o",
                max_tokens=2048,
            )
        )

        assert response.content == '{"ok": true}'

    async def test_provider_error_uses_fallback_route(self) -> None:
        primary = _FailingAdapter(
            "primary",
            ModelGatewayError("primary model failed", is_transient=False),
        )
        fallback = _StaticAdapter(
            "fallback",
            ModelResponse(
                content="备用模型正文",
                model_id="fallback-model",
                prompt_tokens=80,
                completion_tokens=120,
                total_tokens=200,
                finish_reason="stop",
            ),
        )
        router = ModelRouter(
            adapters={"primary": primary, "fallback": fallback},
            default_provider="primary",
            task_fallbacks={
                TaskType.DRAFT_CHAPTER: [
                    TaskRouteOverride(provider="fallback", model_id="fallback-model"),
                ],
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        response = await router.route(
            ModelRequest(
                task_type=TaskType.DRAFT_CHAPTER,
                messages=[{"role": "user", "content": "draft"}],
                max_tokens=9900,
            )
        )

        assert response.content == "备用模型正文"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1

    async def test_timeout_counts_once_per_logical_route_and_is_not_rate_limited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three adapter retries represent one provider-health failure."""
        monkeypatch.setattr("novel_forge.gateway.router.asyncio.sleep", _no_retry_delay)
        adapter = _FailingAdapter("minimax", asyncio.TimeoutError("connect timed out"))
        breaker = CircuitBreaker("minimax", failure_threshold=3, recovery_timeout_s=30)
        router = ModelRouter(
            adapters={"minimax": adapter},
            default_provider="minimax",
            circuit_breakers={"minimax": breaker},
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        events: list[tuple[str, dict[str, object]]] = []
        router.add_observer(lambda event, payload: events.append((event, payload)))

        with pytest.raises(ModelGatewayError):
            await router.route(
                ModelRequest(
                    task_type=TaskType.CHECK_CONTINUITY,
                    messages=[{"role": "user", "content": "check"}],
                )
            )

        assert len(adapter.calls) == 3
        assert breaker.failure_count == 1
        all_failed = next(
            payload for event, payload in events if event == "api_call_all_routes_failed"
        )
        assert all_failed["is_rate_limit"] is False
        assert all_failed["failure_categories"] == ["timeout"]

    async def test_half_open_probe_can_retry_and_reopens_after_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An internally retried probe must not strand the breaker in HALF_OPEN."""
        monkeypatch.setattr("novel_forge.gateway.router.asyncio.sleep", _no_retry_delay)
        adapter = _FailingAdapter("minimax", asyncio.TimeoutError("connect timed out"))
        breaker = CircuitBreaker("minimax", failure_threshold=1, recovery_timeout_s=0.01)
        breaker.record_failure()
        time.sleep(0.02)
        router = ModelRouter(
            adapters={"minimax": adapter},
            default_provider="minimax",
            circuit_breakers={"minimax": breaker},
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        with pytest.raises(ModelGatewayError):
            await router.route(
                ModelRequest(
                    task_type=TaskType.CHECK_CONTINUITY,
                    messages=[{"role": "user", "content": "check"}],
                )
            )

        assert len(adapter.calls) == 3
        assert breaker.state == CircuitState.OPEN

    async def test_rejected_half_open_waiter_does_not_fail_the_active_probe(self) -> None:
        """Only the request that acquired HALF_OPEN may close or reopen it."""
        adapter = _StaticAdapter(
            "minimax",
            ModelResponse(content="not reached", model_id="minimax-model"),
        )
        breaker = CircuitBreaker("minimax", failure_threshold=1, recovery_timeout_s=0.01)
        breaker.record_failure()
        time.sleep(0.02)
        assert breaker.allow_request()  # reserve the single probe for another request
        router = ModelRouter(
            adapters={"minimax": adapter},
            default_provider="minimax",
            circuit_breakers={"minimax": breaker},
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        events: list[tuple[str, dict[str, object]]] = []
        router.add_observer(lambda event, payload: events.append((event, payload)))

        with pytest.raises(ModelGatewayError) as exc_info:
            await router.route(
                ModelRequest(
                    task_type=TaskType.CHECK_CONTINUITY,
                    messages=[{"role": "user", "content": "check"}],
                )
            )

        assert adapter.calls == []
        assert breaker.state == CircuitState.HALF_OPEN
        assert not any(event == "api_call_start" for event, _payload in events)
        assert any(event == "circuit_rejected" for event, _payload in events)
        assert exc_info.value.context["is_transient"] is True
        assert exc_info.value.context["failure_categories"] == ["circuit_open"]


# ═══════════════════════════════════════════════════════════════════════════
# _is_unusable_empty_response — P0-6 fix: length finish_reason triggers retry
# ═══════════════════════════════════════════════════════════════════════════


class TestIsUnusableEmptyResponse:
    """Empty-response classification stays separate from length truncation.

    Non-empty ``finish_reason="length"`` responses are now handled by the
    router's token escalation branch so callers can retry with a larger
    completion budget instead of repeating the same too-small request.
    """

    def test_empty_content_always_unusable(self) -> None:
        from novel_forge.gateway.router import _is_unusable_empty_response

        for finish_reason in ("stop", "length", "content_filter"):
            response = ModelResponse(
                content="",
                model_id="m",
                finish_reason=finish_reason,
            )
            assert _is_unusable_empty_response(response) is True, (
                f"empty content should be unusable regardless of finish_reason={finish_reason!r}"
            )

    def test_whitespace_only_content_unusable(self) -> None:
        from novel_forge.gateway.router import _is_unusable_empty_response

        response = ModelResponse(
            content="   \n\t  ",
            model_id="m",
            finish_reason="stop",
        )
        assert _is_unusable_empty_response(response) is True

    def test_length_finish_reason_with_content_is_not_empty_response(self) -> None:
        from novel_forge.gateway.router import _is_unusable_empty_response

        truncated_json = '{"verdict": "accept", "pending": ['
        response = ModelResponse(
            content=truncated_json,
            model_id="MiniMax-M3",
            finish_reason="length",
        )
        assert _is_unusable_empty_response(response) is False

    def test_normal_stop_response_usable(self) -> None:
        from novel_forge.gateway.router import _is_unusable_empty_response

        response = ModelResponse(
            content='{"verdict": "accept"}',
            model_id="m",
            finish_reason="stop",
        )
        assert _is_unusable_empty_response(response) is False

    async def test_length_response_escalates_tokens_before_returning(self) -> None:
        adapter = _LengthUntilBudgetAdapter(
            "primary",
            threshold=6000,
            model_id="gpt-4o",
        )
        router = ModelRouter(
            adapters={"primary": adapter},
            default_provider="primary",
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        events: list[tuple[str, dict[str, object]]] = []
        router.add_observer(lambda event, payload: events.append((event, payload)))

        response = await router.route(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "evaluate"}],
                model_id="gpt-4o",
                max_tokens=2048,
            )
        )

        assert response.content == '{"ok": true}'
        assert [call.max_tokens for call in adapter.calls] == [2048, 4096, 8192]
        truncated = [payload for event, payload in events if event == "api_call_length_truncated"]
        assert [payload["attempt"] for payload in truncated] == [1, 2]
        assert all(payload["max_attempts"] == 9 for payload in truncated)
        assert all(payload["will_retry"] is True for payload in truncated)
        done = next(payload for event, payload in events if event == "api_call_done")
        assert done["attempt"] == 3
        assert done["max_tokens"] == 8192

    async def test_length_expansion_is_reused_for_later_same_route_call(self) -> None:
        """A semantic retry must not restart at a budget proven too small."""
        adapter = _LengthUntilBudgetAdapter(
            "primary",
            threshold=6000,
            model_id="gpt-4o",
        )
        router = ModelRouter(
            adapters={"primary": adapter},
            default_provider="primary",
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        request = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "evaluate"}],
            model_id="gpt-4o",
            max_tokens=2048,
        )

        first = await router.route(request)
        second = await router.route(request)

        assert first.effective_max_tokens == 8192
        assert first.length_retry_count == 2
        assert second.effective_max_tokens == 8192
        assert second.length_retry_count == 0
        assert [call.max_tokens for call in adapter.calls] == [2048, 4096, 8192, 8192]

    async def test_empty_length_response_escalates_tokens_before_empty_retry(self) -> None:
        adapter = _LengthUntilBudgetAdapter(
            "primary",
            threshold=6000,
            model_id="gpt-4o",
            truncated_content="",
        )
        router = ModelRouter(
            adapters={"primary": adapter},
            default_provider="primary",
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        response = await router.route(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "evaluate"}],
                model_id="gpt-4o",
                max_tokens=2048,
            )
        )

        assert response.content == '{"ok": true}'
        assert [call.max_tokens for call in adapter.calls] == [2048, 4096, 8192]

    async def test_length_response_at_model_limit_uses_fallback_route(self) -> None:
        primary = _LengthUntilBudgetAdapter(
            "primary",
            threshold=4097,
            model_id="hunyuan-pro",
        )
        fallback = _StaticAdapter(
            "fallback",
            ModelResponse(
                content='{"ok": true}',
                model_id="gpt-4o",
                prompt_tokens=80,
                completion_tokens=120,
                total_tokens=200,
                finish_reason="stop",
            ),
        )
        router = ModelRouter(
            adapters={"primary": primary, "fallback": fallback},
            default_provider="primary",
            task_fallbacks={
                TaskType.EVALUATE: [
                    TaskRouteOverride(provider="fallback", model_id="gpt-4o"),
                ],
            },
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        response = await router.route(
            ModelRequest(
                task_type=TaskType.EVALUATE,
                messages=[{"role": "user", "content": "evaluate"}],
                model_id="hunyuan-pro",
                max_tokens=4096,
            )
        )

        assert response.model_id == "gpt-4o"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1


# ═══════════════════════════════════════════════════════════════════════════
# CachePolicy integration in ModelRouter
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterCacheIntegration:
    """Tests for CachePolicy wired into ModelRouter.route()."""

    def _make_router_with_cache(self, **kwargs) -> tuple[ModelRouter, _StaticAdapter]:
        from novel_forge.gateway.cache import CachePolicy

        cache = CachePolicy(enabled=True, max_size=50, telemetry_enabled=False)
        adapter = _StaticAdapter(
            "mock",
            ModelResponse(
                content="cached response",
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                finish_reason="stop",
            ),
        )
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
            cache=cache,
            request_timeout_s=0,
            workflow_timeout_s=0,
            **kwargs,
        )
        return router, adapter

    async def test_evaluate_same_input_hits_cache(self):
        router, adapter = self._make_router_with_cache()

        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "evaluate this"}],
            max_tokens=256,
            temperature=0.3,
        )
        resp1 = await router.route(req)
        resp2 = await router.route(req)

        assert resp1.content == "cached response"
        assert resp2.content == "cached response"
        assert len(adapter.calls) == 1

    async def test_different_top_p_misses_cache(self):
        router, adapter = self._make_router_with_cache()

        req1 = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "evaluate this"}],
            max_tokens=256,
            temperature=0.3,
            top_p=0.5,
        )
        req2 = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "evaluate this"}],
            max_tokens=256,
            temperature=0.3,
            top_p=1.0,
        )
        await router.route(req1)
        await router.route(req2)

        assert len(adapter.calls) == 2

    async def test_same_model_across_providers_misses_cache(self):
        from novel_forge.gateway.cache import CachePolicy

        cache = CachePolicy(enabled=True, max_size=50, telemetry_enabled=False)
        primary = _StaticAdapter(
            "primary",
            ModelResponse(content="primary response", model_id="shared-model"),
        )
        fallback = _StaticAdapter(
            "fallback",
            ModelResponse(content="fallback response", model_id="shared-model"),
        )
        router = ModelRouter(
            adapters={"primary": primary, "fallback": fallback},
            default_provider="primary",
            cache=cache,
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "evaluate this"}],
            model_id="shared-model",
            max_tokens=256,
            temperature=0.3,
        )

        resp1 = await router.route(req, provider="primary")
        resp2 = await router.route(req, provider="fallback")

        assert resp1.content == "primary response"
        assert resp2.content == "fallback response"
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1

    async def test_draft_chapter_does_not_use_cache(self):
        router, adapter = self._make_router_with_cache()

        req = ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "draft this"}],
            max_tokens=256,
            temperature=1.0,
        )
        await router.route(req)
        await router.route(req)

        assert len(adapter.calls) == 2
        assert router.cache is not None
        assert router.cache.size == 0

    async def test_critic_tasks_use_cache(self):
        router, adapter = self._make_router_with_cache()

        for task_type in (
            TaskType.CRITIC_CONTINUITY,
            TaskType.CRITIC_CHARACTER,
            TaskType.CRITIC_CAUSAL,
        ):
            cache = router.cache
            assert cache is not None
            cache.clear()

            req = ModelRequest(
                task_type=task_type,
                messages=[{"role": "user", "content": "critique"}],
                max_tokens=256,
                temperature=0.2,
            )
            await router.route(req)
            await router.route(req)

            assert len(adapter.calls) == 1, (
                f"Expected 1 call for {task_type}, got {len(adapter.calls)}"
            )
            adapter.calls.clear()

    async def test_disabled_cache_passes_through(self):
        from novel_forge.gateway.cache import CachePolicy

        cache = CachePolicy(enabled=False, telemetry_enabled=False)
        adapter = _StaticAdapter(
            "mock",
            ModelResponse(
                content="no cache",
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                finish_reason="stop",
            ),
        )
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
            cache=cache,
            request_timeout_s=0,
            workflow_timeout_s=0,
        )

        req = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=256,
            temperature=0.3,
        )
        await router.route(req)
        await router.route(req)

        assert len(adapter.calls) == 2

    async def test_no_cache_property_returns_none(self):
        router = ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
            request_timeout_s=0,
            workflow_timeout_s=0,
        )
        assert router.cache is None
