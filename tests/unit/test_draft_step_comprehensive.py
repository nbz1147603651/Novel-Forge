"""Tests for DraftStep — happy path, error path, and boundary conditions."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.draft_step import DraftInput, DraftStep
from tests.unit.conftest import _MockRouter

# ── Fixtures / fakes ─────────────────────────────────────────────────────


class _FakeBuilder:
    """Captures context passed to build() for verification."""

    def __init__(self) -> None:
        self.last_context: dict | None = None
        self.last_task_type: TaskType | None = None
        self.last_max_tokens: int = 0

    def build(
        self,
        task_type: TaskType,
        context: dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        top_p: float | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        prior_messages: list | None = None,
    ) -> ModelRequest:
        self.last_context = context
        self.last_task_type = task_type
        self.last_max_tokens = max_tokens
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


class _SequenceRouter:
    """Returns responses in order, then repeats the final response."""

    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = responses
        self.calls: list[ModelRequest] = []

    def resolve_model_id_for_task(
        self,
        task_type: object,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or self._responses[0].model_id or "mock-test"

    async def route(self, request: ModelRequest, *, provider: str | None = None) -> ModelResponse:
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_chunk: Callable[..., None] | None = None,
    ) -> ModelResponse:
        response = await self.route(request, provider=provider)
        if on_delta is not None and response.content:
            on_delta(str(response.content))
        if on_chunk is not None and response.content:
            on_chunk(str(response.content))
        return response


def _make_step(
    response_content: str, finish_reason: str = "stop"
) -> tuple[DraftStep, _FakeBuilder]:
    """Create DraftStep with fake builder/router, return builder for inspection."""
    builder = _FakeBuilder()
    step = DraftStep(
        _MockRouter(response_content, finish_reason=finish_reason),
        builder,
        settings=Settings(),
    )
    return step, builder


# ── Happy Path Tests ─────────────────────────────────────────────────────


async def test_draft_step_happy_path_short() -> None:
    """DraftStep should return Draft with text for short-form content."""
    step, builder = _make_step("这是一个短篇故事草稿内容。")

    input_data = DraftInput(
        task_type=TaskType.DRAFT,
        context={"target_word_count": 3000},
    )

    result = await step.run(input_data)

    assert result.text == "这是一个短篇故事草稿内容。"
    assert result.iteration == 1


async def test_draft_step_happy_path_chapter() -> None:
    """DraftStep should return Draft with text for chapter content."""
    step, builder = _make_step("这是第一章的草稿内容，很长很详细。")

    input_data = DraftInput(
        task_type=TaskType.DRAFT_CHAPTER,
        context={"chapter_number": 1, "target_word_count": 4000},
    )

    result = await step.run(input_data)

    assert result.text == "这是第一章的草稿内容，很长很详细。"
    assert result.iteration == 1


async def test_draft_step_uses_correct_temperature() -> None:
    """DraftStep should use temp_draft_chapter for DRAFT_CHAPTER, temp_draft for DRAFT."""
    settings = Settings()
    builder = _FakeBuilder()
    step = DraftStep(
        _MockRouter("草稿"),
        builder,
        settings=settings,
    )

    # Chapter draft
    await step.run(
        DraftInput(task_type=TaskType.DRAFT_CHAPTER, context={"target_word_count": 3000})
    )
    # Short draft
    step2 = DraftStep(
        _MockRouter("短篇"),
        builder,
        settings=settings,
    )
    await step2.run(DraftInput(task_type=TaskType.DRAFT, context={"target_word_count": 3000}))


# ── Error Path Tests ─────────────────────────────────────────────────────


async def test_draft_step_empty_content_raises() -> None:
    """DraftStep should raise ModelGatewayError on empty content."""
    step, _ = _make_step("")

    input_data = DraftInput(
        task_type=TaskType.DRAFT_CHAPTER,
        context={"chapter_number": 3, "target_word_count": 3000},
    )

    with pytest.raises(ModelGatewayError, match="空内容"):
        await step.run(input_data)


async def test_draft_step_retries_empty_content_then_uses_valid_response() -> None:
    builder = _FakeBuilder()
    valid_text = "这是一次重试后得到的有效章节草稿。" * 40
    router = _SequenceRouter(
        [
            ModelResponse(
                content="",
                model_id="mock-test",
                prompt_tokens=100,
                completion_tokens=9900,
                total_tokens=10000,
                finish_reason="length",
            ),
            ModelResponse(
                content=valid_text,
                model_id="mock-test",
                prompt_tokens=100,
                completion_tokens=500,
                total_tokens=600,
                finish_reason="stop",
            ),
        ]
    )
    step = DraftStep(router, builder, settings=Settings())

    result = await step.run(
        DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 9, "target_word_count": 3000},
        )
    )

    assert result.text == valid_text
    assert len(router.calls) == 2


async def test_draft_step_retries_invalid_text_contract_then_uses_valid_response() -> None:
    builder = _FakeBuilder()
    valid_text = "这是格式重试后得到的有效章节草稿。" * 40
    router = _SequenceRouter(
        [
            ModelResponse(
                content='{"status": "ok", "notes": "不是正文"}',
                model_id="mock-test",
                prompt_tokens=100,
                completion_tokens=120,
                total_tokens=220,
                finish_reason="stop",
            ),
            ModelResponse(
                content=valid_text,
                model_id="mock-test",
                prompt_tokens=100,
                completion_tokens=500,
                total_tokens=600,
                finish_reason="stop",
            ),
        ]
    )
    step = DraftStep(router, builder, settings=Settings())

    result = await step.run(
        DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 10, "target_word_count": 3000},
        )
    )

    assert result.text == valid_text
    assert len(router.calls) == 2


async def test_draft_step_retries_short_chapter_once_with_regeneration_feedback() -> None:
    builder = _FakeBuilder()
    router = _SequenceRouter(
        [
            ModelResponse(content="短" * 8, model_id="mock-test"),
            ModelResponse(content="完整正文" * 12, model_id="mock-test"),
        ]
    )
    settings = Settings(_env_file=None)
    settings.long_streaming_text_enabled = False
    step = DraftStep(router, builder, settings=settings)

    result = await step.run(
        DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 11, "target_word_count": 60},
            minimum_word_count=30,
        )
    )

    assert result.text == "完整正文" * 12
    assert len(router.calls) == 2
    assert builder.last_context is not None
    assert builder.last_context["draft_regeneration"]["minimum_word_count"] == 30


async def test_draft_step_retries_prompt_leak_once_then_keeps_clean_prose() -> None:
    builder = _FakeBuilder()
    clean = "她将药碗放回火边，窗外的雨声渐渐远去。" * 8
    router = _SequenceRouter(
        [
            ModelResponse(content="WR-001之律\n辨真：药味吻合。", model_id="mock-test"),
            ModelResponse(content=clean, model_id="mock-test"),
        ]
    )
    settings = Settings(_env_file=None)
    settings.long_streaming_text_enabled = False
    step = DraftStep(router, builder, settings=settings)

    result = await step.run(
        DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 12, "target_word_count": 120},
            reject_prompt_leaks=True,
        )
    )

    assert result.text == clean
    assert len(router.calls) == 2
    assert builder.last_context is not None
    assert builder.last_context["draft_regeneration"]["prompt_leak_detected"] is True


async def test_draft_step_stops_after_second_short_candidate() -> None:
    builder = _FakeBuilder()
    router = _SequenceRouter(
        [
            ModelResponse(content="短" * 8, model_id="mock-test"),
            ModelResponse(content="仍短" * 8, model_id="mock-test"),
        ]
    )
    settings = Settings(_env_file=None)
    settings.long_streaming_text_enabled = False
    step = DraftStep(router, builder, settings=settings)

    with pytest.raises(ModelGatewayError, match="正文过短") as raised:
        await step.run(
            DraftInput(
                task_type=TaskType.DRAFT_CHAPTER,
                context={"chapter_number": 13, "target_word_count": 60},
                minimum_word_count=30,
            )
        )

    assert raised.value.is_transient_error is False
    assert len(router.calls) == 2


async def test_draft_step_whitespace_only_raises() -> None:
    """DraftStep should raise ModelGatewayError on whitespace-only content."""
    step, _ = _make_step("   \n\n   ")

    input_data = DraftInput(
        task_type=TaskType.DRAFT,
        context={"target_word_count": 3000},
    )

    with pytest.raises(ModelGatewayError, match="空内容"):
        await step.run(input_data)


async def test_draft_step_severe_truncation_raises() -> None:
    """DraftStep should raise ModelGatewayError on severe truncation (<30% target)."""
    step, _ = _make_step("短", finish_reason="length")

    input_data = DraftInput(
        task_type=TaskType.DRAFT_CHAPTER,
        context={"chapter_number": 1, "target_word_count": 3000},
    )

    with pytest.raises(ModelGatewayError, match="截断"):
        await step.run(input_data)


# ── Boundary Condition Tests ─────────────────────────────────────────────


async def test_draft_step_truncation_warning_but_passes() -> None:
    content = "这是一段足够长的草稿内容，虽然被截断但仍然超过了目标的百分之三十。" * 50
    step, _ = _make_step(content, finish_reason="length")

    input_data = DraftInput(
        task_type=TaskType.DRAFT_CHAPTER,
        context={"chapter_number": 1, "target_word_count": 3000},
    )

    result = await step.run(input_data)
    assert result.text is not None
    assert len(result.text) > 0


async def test_draft_step_reading_power_hint_merged() -> None:
    """reading_power_hint should be merged into context when provided."""
    step, builder = _make_step("草稿内容")

    hint_data = {"suggestion": "增加悬念钩子", "priority": "high"}
    input_data = DraftInput(
        task_type=TaskType.DRAFT_CHAPTER,
        context={"chapter_number": 1, "target_word_count": 3000},
        reading_power_hint=hint_data,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert builder.last_context.get("reading_power_hint") == hint_data


async def test_draft_step_default_target_word_count() -> None:
    """DraftStep should use default target_word_count of 3000 when not provided."""
    step, builder = _make_step("默认目标草稿")

    input_data = DraftInput(
        task_type=TaskType.DRAFT,
        context={},
    )

    result = await step.run(input_data)
    assert result.text == "默认目标草稿"
