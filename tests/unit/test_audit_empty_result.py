"""Tests for empty result diagnosis in book consistency audit."""

from __future__ import annotations

import json
from typing import Any

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyStep,
)
from novel_forge.prompts.builder import PromptBuilder


class _RouterContractStub:
    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "fake-model"

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192


class _EmptyIssuesRouter(_RouterContractStub):
    """Router that returns valid JSON with empty issues and valid summary."""

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                {
                    "issues": [],
                    "repair_plan": [],
                    "summary": "全书一致性检查完成，未发现问题。",
                    "consistency_score": 9.5,
                },
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


class _RefusalRouter(_RouterContractStub):
    """Router that returns a refusal message."""

    async def route(self, request: ModelRequest) -> ModelResponse:
        raise ModelGatewayError(
            "LLM refused to audit: I cannot analyze this content",
            is_transient=False,
        )


class _ParseFailureRouter(_RouterContractStub):
    """Router that returns malformed JSON."""

    async def route(self, request: ModelRequest) -> ModelResponse:
        raise json.JSONDecodeError("Expecting value", "not json", 0)


class _EmptyIssuesNoSummaryRouter(_RouterContractStub):
    """Router that returns empty issues but no summary or score."""

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                {
                    "issues": [],
                },
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


async def _make_input() -> BookConsistencyInput:
    return BookConsistencyInput(
        chapter_summaries=[
            {"chapter_number": 1, "summary": "起章", "key_events": []},
            {"chapter_number": 2, "summary": "承章", "key_events": []},
        ],
        canon_state_snapshot={},
        character_bible={},
        outline={},
        max_tokens=2048,
        temperature=0.2,
    )


async def test_empty_result_diagnosis(runtime_settings: Any) -> None:
    """Mock LLM returning empty issues sets empty_result_reason to genuinely_no_issues."""
    step = BookConsistencyStep(
        _EmptyIssuesRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(await _make_input())

    assert result.empty_result_reason == "genuinely_no_issues"
    assert len(result.issues) == 0


async def test_genuinely_no_issues(runtime_settings: Any) -> None:
    """Valid response with no issues sets empty_result_reason to genuinely_no_issues."""
    step = BookConsistencyStep(
        _EmptyIssuesRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(await _make_input())

    assert result.empty_result_reason == "genuinely_no_issues"
    assert result.summary == "全书一致性检查完成，未发现问题。"
    assert result.consistency_score == 9.5


async def test_llm_refusal(runtime_settings: Any) -> None:
    """Refusal message sets empty_result_reason to llm_refusal."""
    step = BookConsistencyStep(
        _RefusalRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    with pytest.raises(ModelGatewayError) as exc_info:
        await step.run(await _make_input())

    assert "cannot" in str(exc_info.value).lower() or "refuse" in str(exc_info.value).lower()


async def test_parse_failure(runtime_settings: Any) -> None:
    """Malformed JSON sets empty_result_reason to parse_failure."""
    step = BookConsistencyStep(
        _ParseFailureRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    with pytest.raises(json.JSONDecodeError):
        await step.run(await _make_input())


async def test_empty_result_with_no_summary_is_parse_failure(runtime_settings: Any) -> None:
    """Malformed empty audit envelopes are rejected by the strict contract."""
    step = BookConsistencyStep(
        _EmptyIssuesNoSummaryRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    with pytest.raises(KeyError, match="repair_plan"):
        await step.run(await _make_input())


async def test_empty_result_in_model_dump(runtime_settings: Any) -> None:
    """empty_result_reason appears in model_dump output."""
    step = BookConsistencyStep(
        _EmptyIssuesRouter(),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(await _make_input())

    dump = result.model_dump()
    assert "empty_result_reason" in dump
    assert dump["empty_result_reason"] == "genuinely_no_issues"
