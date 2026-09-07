"""Tests for ModelRouter public introspection helpers."""

from __future__ import annotations

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.profiles import get_model_max_output_tokens
from novel_forge.gateway.router import (
    ModelRouter,
    TaskRouteOverride,
    _extract_provider_error_details,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens


def _build_router() -> ModelRouter:
    return ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
        task_providers={
            TaskType.DRAFT: TaskRouteOverride(provider="mock", model_id="mock-model-v2"),
        },
    )


def test_router_exposes_read_only_views() -> None:
    router = _build_router()

    assert router.default_provider == "mock"
    assert list(router.adapters.keys()) == ["mock"]
    assert TaskType.DRAFT in router.task_route_overrides

    with pytest.raises(TypeError):
        router.adapters["alt"] = MockAdapter()  # type: ignore[index]

    with pytest.raises(TypeError):
        router.task_route_overrides[TaskType.BEATS] = TaskRouteOverride(provider="mock")  # type: ignore[index]


def test_router_get_adapter() -> None:
    router = _build_router()

    assert router.get_adapter("mock") is not None
    assert router.get_adapter("missing") is None


def test_router_resolves_route_output_limit_for_task_override() -> None:
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
        task_providers={
            TaskType.DRAFT_CHAPTER: TaskRouteOverride(provider="mock", model_id="gpt-4o"),
        },
    )

    assert router.resolve_model_id_for_task(TaskType.DRAFT_CHAPTER) == "gpt-4o"
    assert router.output_limit_for_task(TaskType.DRAFT_CHAPTER) == get_model_max_output_tokens(
        "gpt-4o"
    )


def test_route_aware_token_budget_honors_model_limit_and_cap() -> None:
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
        task_providers={
            TaskType.DRAFT_CHAPTER: TaskRouteOverride(provider="mock", model_id="gpt-4o"),
        },
    )

    route_limit = get_model_max_output_tokens("gpt-4o")
    assert (
        calculate_route_aware_max_tokens(
            router,
            TaskType.DRAFT_CHAPTER,
            100_000,
            prompt_overhead=0,
            safety_margin=1.0,
            min_tokens=512,
        )
        == route_limit
    )
    assert (
        calculate_route_aware_max_tokens(
            router,
            TaskType.DRAFT_CHAPTER,
            100_000,
            prompt_overhead=0,
            safety_margin=1.0,
            min_tokens=512,
            max_cap=4096,
        )
        == 4096
    )


def test_route_aware_token_budget_does_not_spend_output_limit_on_prompt_overhead() -> None:
    router = ModelRouter(
        adapters={"mock": MockAdapter()},
        default_provider="mock",
        task_providers={
            TaskType.PLAN_CHAPTER_CONTRACTS: TaskRouteOverride(
                provider="mock",
                model_id="gpt-4o",
            ),
        },
    )

    assert (
        calculate_route_aware_max_tokens(
            router,
            TaskType.PLAN_CHAPTER_CONTRACTS,
            100_000,
            prompt_overhead=12_000,
            safety_margin=1.0,
            min_tokens=512,
        )
        == get_model_max_output_tokens("gpt-4o")
    )


def test_router_default_tiers_cover_newer_workflow_tasks() -> None:
    router = _build_router()

    assert router._resolve_tier(TaskType.POLISH_CHAPTER).value == "premium"
    assert router._resolve_tier(TaskType.BOOK_CONSISTENCY).value == "standard"
    assert router._resolve_tier(TaskType.CHECK_CHAPTER).value == "standard"


def test_extract_provider_error_details_from_embedded_payload() -> None:
    exc = RuntimeError(
        "Error code: 500 - {'error': {'message': 'Request timed out, please try again later.', 'type': 'RequestTimeOut', 'param': None, 'code': 'RequestTimeOut'}, 'request_id': 'req_123'}"
    )

    details = _extract_provider_error_details(exc)

    assert details["type"] == "RuntimeError"
    assert details["request_id"] == "req_123"
    assert details["provider_error_type"] == "RequestTimeOut"
    assert details["provider_error_code"] == "RequestTimeOut"
    assert details["provider_error_message"] == "Request timed out, please try again later."
