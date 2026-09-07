"""Tests for EditStep — Input extension for reading_power_hint (RED phase).

These tests verify that EditInput supports reading_power_hint field
and that it is passed to the template context during prompt building.

Expected to FAIL until EditInput is extended with reading_power_hint.
"""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.edit_step import EditInput, EditStep
from tests.unit.conftest import _MockRouter

# ── Fixtures / fakes ─────────────────────────────────────────────────────


class _FakeBuilder:
    """Captures context passed to build() for verification."""

    def __init__(self) -> None:
        self.last_context: dict | None = None

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
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _make_step(response_content: str) -> tuple[EditStep, _FakeBuilder]:
    """Create EditStep with fake builder/router, return builder for inspection."""
    builder = _FakeBuilder()
    step = EditStep(
        _MockRouter(response_content),
        builder,
        settings=Settings(),
    )
    return step, builder


# ── Input Schema Tests (RED phase) ───────────────────────────────────────


def test_edit_input_has_reading_power_hint_field() -> None:
    """EditInput should accept reading_power_hint parameter.

    This test will FAIL until EditInput is extended with reading_power_hint.
    """
    hint_data = {"suggestion": "建议增加悬念钩子", "priority": "high"}
    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿文本",
        context={"chapter_number": 1},
        reading_power_hint=hint_data,
    )
    assert input_data.reading_power_hint == hint_data


def test_edit_input_reading_power_hint_optional() -> None:
    """reading_power_hint should be optional (None by default).

    This test will FAIL until EditInput is extended with reading_power_hint.
    """
    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿文本",
        context={"chapter_number": 1},
    )
    assert input_data.reading_power_hint is None


# ── Context Propagation Tests (RED phase) ─────────────────────────────────


async def test_reading_power_hint_passed_to_template_context() -> None:
    """reading_power_hint should be merged into template context.

    This test will FAIL until EditStep._execute propagates reading_power_hint.
    """
    step, builder = _make_step("润色后的文本")

    hint_data = {"suggestion": "下章建议：增加危机钩子", "priority": "high"}
    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="原始草稿内容",
        context={"chapter_number": 3, "genre": "fantasy"},
        reading_power_hint=hint_data,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert "reading_power_hint" in builder.last_context
    assert builder.last_context["reading_power_hint"] == hint_data


async def test_reading_power_hint_none_not_added_to_context() -> None:
    """When reading_power_hint is None, it should not pollute context.

    This test will FAIL until EditInput is extended.
    """
    step, builder = _make_step("润色后的文本")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿",
        context={"chapter_number": 2},
        reading_power_hint=None,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert builder.last_context.get("reading_power_hint") is None


async def test_reading_power_hint_preserves_existing_context() -> None:
    """reading_power_hint should be added without overwriting existing keys.

    This test will FAIL until EditInput is extended.
    """
    step, builder = _make_step("润色后的文本")

    hint_data = {"suggestion": "建议强化情感钩子", "priority": "medium"}
    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="草稿内容",
        context={
            "chapter_number": 5,
            "genre": "fantasy",
            "pov_character": "主角",
            "alignment_score": 0.85,
        },
        reading_power_hint=hint_data,
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert builder.last_context["chapter_number"] == 5
    assert builder.last_context["genre"] == "fantasy"
    assert builder.last_context["pov_character"] == "主角"
    assert builder.last_context["alignment_score"] == 0.85
    assert builder.last_context["reading_power_hint"] == hint_data


async def test_draft_text_always_in_context() -> None:
    """draft_text should always be in context (existing behavior).

    This test should PASS as draft_text is already propagated.
    """
    step, builder = _make_step("润色后的文本")

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="这是原始草稿内容",
        context={"chapter_number": 1},
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert "draft_text" in builder.last_context
    assert builder.last_context["draft_text"] == "这是原始草稿内容"


async def test_invalid_text_contract_keeps_previous_draft() -> None:
    step, _builder = _make_step('{"status": "ok", "notes": "不是正文"}')

    input_data = EditInput(
        task_type=TaskType.EDIT_CHAPTER,
        draft_text="这是原始草稿内容",
        context={"chapter_number": 1},
    )

    result = await step.run(input_data)

    assert result.revised_text == "这是原始草稿内容"
    assert "invalid TEXT output" in result.edit_notes[0]
