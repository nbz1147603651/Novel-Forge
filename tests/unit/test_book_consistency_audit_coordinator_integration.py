"""Tests for AuditCoordinator integration in BookConsistencyStep."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.core.config import Settings
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyResult,
    BookConsistencyStep,
)


@dataclass
class _MockSettings:
    long_book_audit_memory_enhancement_enabled: bool = False
    long_book_audit_prompt_char_budget: int = 48_000
    long_book_audit_max_tokens: int = 4096
    temp_book_consistency: float = 0.3
    long_book_audit_two_phase_enabled: bool = True
    long_book_audit_two_phase_threshold: float = 0.7
    long_book_audit_two_phase_max_target_chapters: int = 24
    long_book_audit_max_issues_per_chunk: int = 12
    long_book_audit_issue_pool_max_items: int = 160
    storage_root: str = "/tmp/test_storage"
    storage_cache_enabled: bool = True
    storage_cache_max_entries: int = 256
    default_model_tier: str = "standard"
    premium_model: str = "gpt-4o"
    standard_model: str = "gpt-4o-mini"
    budget_model: str = "gpt-3.5-turbo"


class _FakeRouter:
    def __init__(self, response_content: str | None = None) -> None:
        self.calls: list[ModelRequest] = []
        self._response = response_content or json.dumps(
            {
                "issues": [],
                "repair_plan": [],
                "summary": "未发现问题",
                "consistency_score": 9.5,
            },
            ensure_ascii=False,
        )

    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(content=self._response, model_id="fake-model")


def _make_minimal_input(**overrides: Any) -> BookConsistencyInput:
    kwargs: dict[str, Any] = {
        "chapter_summaries": [
            {"chapter_number": 1, "summary": "第一章摘要", "key_events": ["事件1"]},
            {"chapter_number": 2, "summary": "第二章摘要", "key_events": ["事件2"]},
        ],
        "canon_state_snapshot": {
            "characters": {"张三": {"name": "张三"}},
            "relationships": [],
        },
        "character_bible": {"characters": [{"name": "张三"}]},
        "outline": {"premise": "故事前提"},
        "chapter_texts": [
            {"chapter_number": 1, "text": "第一章正文", "paragraph_count": 1, "paragraphs": ["段落1"]},
            {"chapter_number": 2, "text": "第二章正文", "paragraph_count": 1, "paragraphs": ["段落1"]},
        ],
    }
    kwargs.update(overrides)
    return BookConsistencyInput(**kwargs)


@pytest.fixture
def fake_router() -> _FakeRouter:
    return _FakeRouter()


@pytest.fixture
def mock_settings() -> _MockSettings:
    return _MockSettings()


class TestMemoryEnhancementDisabledByDefault:

    @pytest.mark.asyncio
    async def test_no_audit_coordinator_when_flag_false(
        self, fake_router: _FakeRouter, builder: Any, runtime_settings: Settings
    ) -> None:
        runtime_settings.long_book_audit_memory_enhancement_enabled = False
        step = BookConsistencyStep(fake_router, builder, settings=runtime_settings)
        input_data = _make_minimal_input()
        result = await step.run(input_data)
        assert isinstance(result, BookConsistencyResult)


class TestMemoryEnhancementEnabled:

    @pytest.mark.asyncio
    async def test_audit_coordinator_called_when_flag_true(
        self, fake_router: _FakeRouter, builder: Any, runtime_settings: Settings
    ) -> None:
        runtime_settings.long_book_audit_memory_enhancement_enabled = True

        mock_audit_ctx = MagicMock()
        mock_audit_ctx.get_summary_for_prompt.return_value = "跨章节语义上下文：角色张三在第1章出现"

        mock_coordinator = AsyncMock()
        mock_coordinator.prepare_audit_context.return_value = mock_audit_ctx

        mock_memory_ctx = MagicMock()
        mock_memory_ctx.project_id = "test_project"

        factory_called = False

        def memory_factory() -> Any:
            nonlocal factory_called
            factory_called = True
            return mock_memory_ctx

        input_data = _make_minimal_input(memory_context_factory=memory_factory)

        step = BookConsistencyStep(fake_router, builder, settings=runtime_settings)

        with patch(
            "novel_forge.memory.audit_coordinator.AuditCoordinator",
            return_value=mock_coordinator,
        ) as MockCoordinatorClass:
            result = await step.run(input_data)

        assert factory_called
        MockCoordinatorClass.assert_called_once_with(mock_memory_ctx)
        mock_coordinator.prepare_audit_context.assert_awaited_once_with(
            chapter_number=input_data.chapter_number,
            chapter_text="",
        )
        assert isinstance(result, BookConsistencyResult)

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_audit_coordinator_error(
        self, fake_router: _FakeRouter, builder: Any, runtime_settings: Settings
    ) -> None:
        runtime_settings.long_book_audit_memory_enhancement_enabled = True

        def memory_factory() -> Any:
            return MagicMock(project_id="test_project")

        input_data = _make_minimal_input(memory_context_factory=memory_factory)
        step = BookConsistencyStep(fake_router, builder, settings=runtime_settings)

        with patch(
            "novel_forge.memory.audit_coordinator.AuditCoordinator",
            side_effect=RuntimeError("Memory service unavailable"),
        ):
            result = await step.run(input_data)

        assert isinstance(result, BookConsistencyResult)

    @pytest.mark.asyncio
    async def test_no_factory_no_crash(
        self, fake_router: _FakeRouter, builder: Any, runtime_settings: Settings
    ) -> None:
        runtime_settings.long_book_audit_memory_enhancement_enabled = True
        input_data = _make_minimal_input()
        step = BookConsistencyStep(fake_router, builder, settings=runtime_settings)
        result = await step.run(input_data)
        assert isinstance(result, BookConsistencyResult)

    @pytest.mark.asyncio
    async def test_factory_returns_none_no_crash(
        self, fake_router: _FakeRouter, builder: Any, runtime_settings: Settings
    ) -> None:
        runtime_settings.long_book_audit_memory_enhancement_enabled = True
        input_data = _make_minimal_input(memory_context_factory=lambda: None)
        step = BookConsistencyStep(fake_router, builder, settings=runtime_settings)
        result = await step.run(input_data)
        assert isinstance(result, BookConsistencyResult)


class TestConfigSetting:

    def test_default_value_is_false(self) -> None:
        field_info = Settings.model_fields.get("long_book_audit_memory_enhancement_enabled")
        assert field_info is not None
        assert field_info.default is False

    def test_setting_has_description(self) -> None:
        field_info = Settings.model_fields.get("long_book_audit_memory_enhancement_enabled")
        assert field_info is not None
        assert field_info.description is not None
        assert len(field_info.description) > 10
