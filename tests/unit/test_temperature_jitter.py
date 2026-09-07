"""Tests for creative temperature jitter policy and routing."""

from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.parsing import temperature_jitter as jitter_mod
from novel_forge.core.parsing.temperature_jitter import (
    is_creative_temperature_task,
    resolve_temperature_jitter,
    resolve_temperature_jitter_tasks,
)
from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.long.services.generation.llm_helpers import route_json_object_with_retry


class _CaptureAdapter(ProviderAdapter):
    def __init__(self, content: str = "{}") -> None:
        self.requests: list[ModelRequest] = []
        self._content = content

    @property
    def provider_name(self) -> str:
        return "capture"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self._content, model_id=request.model_id or "capture-model")


def test_temperature_jitter_respects_creative_range() -> None:
    result = resolve_temperature_jitter(
        task_type=TaskType.DRAFT_CHAPTER,
        base_temperature=1.0,
        enabled=True,
        up_delta=0.1,
        down_delta=0.3,
        sampler=lambda low, high: (low + high) / 2,
    )

    assert is_creative_temperature_task(TaskType.DRAFT_CHAPTER)
    assert result.enabled is True
    assert result.range_min == pytest.approx(0.7)
    assert result.range_max == pytest.approx(1.1)
    assert result.actual_temperature == pytest.approx(0.9)


def test_temperature_jitter_rounds_sampled_temperature_to_one_decimal() -> None:
    result = resolve_temperature_jitter(
        task_type=TaskType.DRAFT_CHAPTER,
        base_temperature=1.0,
        enabled=True,
        up_delta=0.1,
        down_delta=0.3,
        sampler=lambda _low, _high: 0.9555105222739317,
    )
    half_step = resolve_temperature_jitter(
        task_type=TaskType.DRAFT_CHAPTER,
        base_temperature=1.0,
        enabled=True,
        up_delta=0.1,
        down_delta=0.3,
        sampler=lambda _low, _high: 0.95,
    )

    assert result.actual_temperature == pytest.approx(1.0)
    assert half_step.actual_temperature == pytest.approx(1.0)


def test_temperature_jitter_keeps_fixed_tasks_unchanged() -> None:
    result = resolve_temperature_jitter(
        task_type=TaskType.CHECK_CHAPTER,
        base_temperature=0.2,
        enabled=True,
        up_delta=0.1,
        down_delta=0.3,
        sampler=lambda _low, high: high,
    )

    assert result.enabled is False
    assert result.reason == "protected_task"
    assert result.actual_temperature == pytest.approx(0.2)


def test_temperature_jitter_scope_filters_presets_and_protected_tasks() -> None:
    chapter_core = resolve_temperature_jitter_tasks(scope="chapter_core")
    init_and_chapter = resolve_temperature_jitter_tasks(scope="init_and_chapter")

    assert TaskType.DRAFT_CHAPTER in chapter_core
    assert TaskType.INIT_STORY_BIBLE not in chapter_core
    assert TaskType.INIT_STORY_BIBLE in init_and_chapter
    assert TaskType.CHECK_CHAPTER not in init_and_chapter


def test_temperature_jitter_custom_scope_filters_protected_tasks() -> None:
    tasks = resolve_temperature_jitter_tasks(
        scope="custom",
        custom_tasks="draft_chapter,check_chapter,repair_continuity,validate_scene_plan",
    )
    iterable_tasks = resolve_temperature_jitter_tasks(
        scope="custom",
        custom_tasks=[TaskType.EDIT_CHAPTER, TaskType.CHECK_CHAPTER],
    )

    assert tasks == frozenset({TaskType.DRAFT_CHAPTER})
    assert iterable_tasks == frozenset({TaskType.EDIT_CHAPTER})


def test_temperature_jitter_custom_scope_can_be_empty() -> None:
    tasks = resolve_temperature_jitter_tasks(scope="custom", custom_tasks="")
    result = resolve_temperature_jitter(
        task_type=TaskType.DRAFT_CHAPTER,
        base_temperature=1.0,
        enabled=True,
        up_delta=0.1,
        down_delta=0.3,
        scope="custom",
        custom_tasks="",
        sampler=lambda _low, high: high,
    )

    assert tasks == frozenset()
    assert result.enabled is False
    assert result.reason == "fixed_task"
    assert result.actual_temperature == pytest.approx(1.0)


def test_temperature_jitter_clamps_bounds() -> None:
    result = resolve_temperature_jitter(
        task_type=TaskType.DRAFT_CHAPTER,
        base_temperature=1.95,
        enabled=True,
        up_delta=0.5,
        down_delta=3.0,
        sampler=lambda low, high: high if high > low else low,
    )

    assert result.range_min == pytest.approx(0.0)
    assert result.range_max == pytest.approx(2.0)
    assert result.actual_temperature == pytest.approx(2.0)


async def test_router_applies_jitter_only_to_creative_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jitter_mod.random, "uniform", lambda _low, high: high)
    adapter = _CaptureAdapter(content='{"ok": true}')
    router = ModelRouter(
        adapters={"capture": adapter},
        default_provider="capture",
        creative_temperature_jitter_enabled=True,
        creative_temperature_jitter_up_delta=0.1,
        creative_temperature_jitter_down_delta=0.3,
    )

    events: list[tuple[str, dict[str, object]]] = []
    router.add_observer(lambda event, payload: events.append((event, payload)))

    await router.route(
        ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "写一段"}],
            temperature=1.0,
            max_tokens=32,
        )
    )
    await router.route(
        ModelRequest(
            task_type=TaskType.CHECK_CHAPTER,
            messages=[{"role": "user", "content": "检查"}],
            temperature=0.2,
            max_tokens=32,
        )
    )

    assert adapter.requests[0].temperature == pytest.approx(1.1)
    assert adapter.requests[1].temperature == pytest.approx(0.2)
    start_payload = events[0][1]
    assert start_payload["temperature"] == pytest.approx(1.1)
    assert start_payload["temperature_base"] == pytest.approx(1.0)
    assert start_payload["temperature_jitter_enabled"] is True


async def test_router_uses_custom_scope_and_protects_stable_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jitter_mod.random, "uniform", lambda _low, high: high)
    adapter = _CaptureAdapter(content='{"ok": true}')
    router = ModelRouter(
        adapters={"capture": adapter},
        default_provider="capture",
        creative_temperature_jitter_enabled=True,
        creative_temperature_jitter_up_delta=0.1,
        creative_temperature_jitter_down_delta=0.3,
        creative_temperature_jitter_scope="custom",
        creative_temperature_jitter_custom_tasks="edit_chapter,check_chapter",
    )

    await router.route(
        ModelRequest(
            task_type=TaskType.DRAFT_CHAPTER,
            messages=[{"role": "user", "content": "写一段"}],
            temperature=1.0,
            max_tokens=32,
        )
    )
    await router.route(
        ModelRequest(
            task_type=TaskType.EDIT_CHAPTER,
            messages=[{"role": "user", "content": "改一段"}],
            temperature=0.5,
            max_tokens=32,
        )
    )
    await router.route(
        ModelRequest(
            task_type=TaskType.CHECK_CHAPTER,
            messages=[{"role": "user", "content": "检查"}],
            temperature=0.2,
            max_tokens=32,
        )
    )

    assert adapter.requests[0].temperature == pytest.approx(1.0)
    assert adapter.requests[1].temperature == pytest.approx(0.6)
    assert adapter.requests[2].temperature == pytest.approx(0.2)


async def test_format_retry_disables_temperature_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jitter_mod.random, "uniform", lambda _low, high: high)

    class _RetryRouter:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse(content="{bad", model_id="capture-model")
            return ModelResponse(content='{"ok": true}', model_id="capture-model")

    router = _RetryRouter()
    request = ModelRequest(
        task_type=TaskType.DRAFT_CHAPTER,
        messages=[{"role": "user", "content": "写一段"}],
        temperature=1.0,
        max_tokens=32,
    )

    result = await route_json_object_with_retry(
        router,
        request,
        task_type=TaskType.DRAFT_CHAPTER,
        include_contract_required_keys=False,
        retry_temperature=0.0,
    )

    assert result == {"ok": True}
    assert router.requests[1].temperature == pytest.approx(0.0)
    assert router.requests[1].temperature_jitter_allowed is False
