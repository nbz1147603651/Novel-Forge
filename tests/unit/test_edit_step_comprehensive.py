"""Tests for EditStep — happy path, error path, and boundary conditions."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.schemas.draft import EditResult
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.edit_step import EditInput, EditStep
from tests.unit.conftest import _MockRouter

# ── Fixtures / fakes ─────────────────────────────────────────────────────


class _FakeBuilder:
    def __init__(self) -> None:
        self.last_context: dict | None = None

    def build(
        self,
        task_type,
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
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _make_step(response_content: str, finish_reason: str = "stop") -> tuple[EditStep, _FakeBuilder]:
    builder = _FakeBuilder()
    step = EditStep(
        _MockRouter(response_content, finish_reason=finish_reason),
        builder,
        settings=Settings(),
    )
    return step, builder


class _FailingStreamRouter(_MockRouter):
    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_delta=None,
        on_chunk=None,
    ):
        raise ModelGatewayError("provider unavailable", is_transient=True)


# ── Happy Path Tests ─────────────────────────────────────────────────────


async def test_edit_step_happy_path() -> None:
    """EditStep should return EditResult with revised text."""
    step, _ = _make_step("润色后的文本内容。")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="原始草稿内容。",
        context={"chapter_number": 1},
        iteration=1,
    )

    result = await step.run(input_data)

    assert isinstance(result, EditResult)
    assert result.revised_text == "润色后的文本内容。"
    assert result.iteration == 1


async def test_edit_step_short_form() -> None:
    """EditStep should work with short-form EDIT task type."""
    step, _ = _make_step("润色后的短篇。")

    input_data = EditInput(
        task_type=TaskType.EDIT,
        draft_text="原始短篇。",
        context={},
        iteration=1,
    )

    result = await step.run(input_data)

    assert result.revised_text == "润色后的短篇。"


# ── Error Path Tests ─────────────────────────────────────────────────────


async def test_edit_step_empty_content_keeps_previous_draft() -> None:
    """EditStep should keep the previous draft when a TEXT response is empty."""
    step, _ = _make_step("")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="原始草稿",
        context={},
        iteration=1,
    )

    result = await step.run(input_data)

    assert result.revised_text == "原始草稿"
    assert "invalid TEXT output" in result.edit_notes[0]


async def test_edit_step_empty_truncation_keeps_previous_draft() -> None:
    original_draft = "原始草稿" * 20
    step, _ = _make_step("", finish_reason="length")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text=original_draft,
        context={},
        iteration=1,
    )

    result = await step.run(input_data)

    assert result.revised_text == original_draft
    assert "invalid TEXT output" in result.edit_notes[0]


async def test_edit_step_gateway_error_propagates() -> None:
    builder = _FakeBuilder()
    step = EditStep(
        _FailingStreamRouter(""),
        builder,
        settings=Settings(),
    )

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="原始草稿",
        context={},
        iteration=1,
    )

    with pytest.raises(ModelGatewayError, match="provider unavailable"):
        await step.run(input_data)


async def test_edit_step_severe_truncation_keeps_previous_draft() -> None:
    """EditStep should keep the prior draft when output < 50% of draft."""
    original_draft = "这是一段很长的原始草稿内容，用于测试截断检测功能。" * 10
    step, _ = _make_step("短", finish_reason="length")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text=original_draft,
        context={},
        iteration=1,
    )

    result = await step.run(input_data)

    assert result.revised_text == original_draft
    assert "output" in result.edit_notes[0]


# ── Boundary Condition Tests ─────────────────────────────────────────────


async def test_edit_step_truncation_keeps_previous_draft() -> None:
    original_draft = "原始草稿内容" * 20
    # Output shorter than 50% of the draft triggers the regression guard
    # (streaming path lacks finish_reason, so truncation is inferred from
    # text-length regression against the previous draft).
    edited_output = "编辑后的内容"
    step, _ = _make_step(edited_output, finish_reason="length")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text=original_draft,
        context={},
        iteration=1,
    )

    result = await step.run(input_data)

    assert result.revised_text == original_draft


async def test_edit_step_reading_power_hint_merged() -> None:
    """EditStep should merge reading_power_hint into context."""
    step, builder = _make_step("润色后文本")

    hint = {"suggestion": "增加情感深度", "priority": "medium"}
    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿",
        context={"chapter_number": 1},
        iteration=1,
        reading_power_hint=hint,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert builder.last_context.get("reading_power_hint") == hint


async def test_edit_step_capture_raw() -> None:
    """EditStep should capture raw response when capture_raw list provided."""
    step, _ = _make_step("润色后文本")

    capture_raw: list[str] = []
    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿",
        context={},
        iteration=1,
        capture_raw=capture_raw,
    )

    await step.run(input_data)

    assert len(capture_raw) == 1
    assert capture_raw[0] == "润色后文本"


async def test_edit_step_iteration_tracked() -> None:
    """EditStep should track iteration number in EditResult."""
    step, _ = _make_step("第三轮润色")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿",
        context={},
        iteration=3,
    )

    result = await step.run(input_data)

    assert result.iteration == 3
