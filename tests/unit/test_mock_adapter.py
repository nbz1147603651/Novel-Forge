"""Tests for MockAdapter."""

from __future__ import annotations

import json

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.format_contracts import (
    OutputKind,
    resolve_task_format_contract,
    validate_json_output_contract,
)
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
    coerce_world_rule_book,
    validate_world_rule_book,
)
from novel_forge.pipeline.long.services.generation.llm_helpers import apply_task_response_defaults
from novel_forge.pipeline.long.services.task_output_adapters import apply_task_output_adapter


class TestMockAdapter:
    @pytest.fixture
    def adapter(self) -> MockAdapter:
        return MockAdapter()

    async def test_provider_name(self, adapter: MockAdapter) -> None:
        assert adapter.provider_name == "mock"

    async def test_health_check(self, adapter: MockAdapter) -> None:
        assert await adapter.health_check() is True

    async def test_beats_response(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.BEATS,
            messages=[{"role": "user", "content": "generate beats"}],
        )
        response = await adapter.complete(request)
        assert "beats" in response.content
        assert response.total_tokens > 0
        assert response.cost_usd == 0.0

    async def test_draft_response(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.DRAFT,
            messages=[{"role": "user", "content": "write draft"}],
        )
        response = await adapter.complete(request)
        assert "林远" in response.content

    async def test_long_prose_tasks_respect_target_length(self, adapter: MockAdapter) -> None:
        for task_type in (TaskType.DRAFT_CHAPTER, TaskType.WAVE_CHAPTER, TaskType.POLISH_CHAPTER):
            request = ModelRequest(
                task_type=task_type,
                messages=[{"role": "user", "content": "目标字数：3000"}],
            )
            response = await adapter.complete(request)

            assert not response.content.strip().startswith("{")
            assert len(response.content) >= 2600
            assert "林远" in response.content

    async def test_eval_response(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "evaluate"}],
        )
        response = await adapter.complete(request)
        assert "scores" in response.content

    async def test_canon_delta_response(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.EXTRACT_CANON,
            messages=[{"role": "user", "content": "extract"}],
        )
        response = await adapter.complete(request)
        assert "source_chapter" in response.content

    async def test_story_bible_response(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.INIT_STORY_BIBLE,
            messages=[{"role": "user", "content": "init story bible"}],
        )
        response = await adapter.complete(request)
        assert "story_bible" in response.content
        payload = json.loads(response.content)["story_bible"]
        rule_book = coerce_world_rule_book(payload["world_rule_book"])
        assert (
            validate_world_rule_book(
                rule_book,
                magic_or_tech=payload["magic_or_tech"],
            )
            == []
        )

    async def test_story_world_rules_response_has_valid_rule_book(
        self,
        adapter: MockAdapter,
    ) -> None:
        request = ModelRequest(
            task_type=TaskType.INIT_STORY_WORLD_RULES,
            messages=[{"role": "user", "content": "init story world rules"}],
        )
        response = await adapter.complete(request)
        payload = json.loads(response.content)["world_rules"]
        rule_book = coerce_world_rule_book(payload["world_rule_book"])
        assert (
            validate_world_rule_book(
                rule_book,
                magic_or_tech=payload["magic_or_tech"],
            )
            == []
        )

    async def test_profile_structure_response_has_required_keys(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.PROFILE_STRUCTURE,
            messages=[{"role": "user", "content": "profile structure"}],
        )
        response = await adapter.complete(request)
        payload = json.loads(response.content)
        assert {"hook_config", "strand_config", "micro_payoff_config", "cool_point_config"} <= set(
            payload
        )

    async def test_editorial_character_voices_respect_runtime_enum(
        self,
        adapter: MockAdapter,
    ) -> None:
        context = {"voice_character_whitelist": ["林远", "老守夜人"]}
        contract = resolve_task_format_contract(TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES, context)
        assert contract is not None
        request = ModelRequest(
            task_type=TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
            messages=[{"role": "user", "content": "derive character voices"}],
            response_json_schema=contract.json_schema,
        )

        response = await adapter.complete(request)
        payload = json.loads(response.content)
        characters = [item["character"] for item in payload["character_voices"]]

        assert characters == ["林远", "老守夜人"]
        assert "苏晚" not in characters
        validate_json_output_contract(
            TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
            payload,
            context=context,
        )

    @pytest.mark.parametrize(
        ("known_characters", "expected_characters"),
        [
            (["林远"], []),
            (["林远", "苏晚"], ["林远", "苏晚"]),
        ],
    )
    async def test_relationship_deltas_respect_runtime_character_enum(
        self,
        adapter: MockAdapter,
        known_characters: list[str],
        expected_characters: list[str],
    ) -> None:
        context = {"known_characters": known_characters}
        contract = resolve_task_format_contract(TaskType.EXTRACT_RELATIONSHIP_DELTAS, context)
        assert contract is not None
        request = ModelRequest(
            task_type=TaskType.EXTRACT_RELATIONSHIP_DELTAS,
            messages=[{"role": "user", "content": "extract relationship deltas"}],
            response_json_schema=contract.json_schema,
        )

        response = await adapter.complete(request)
        payload = json.loads(response.content)
        deltas = payload["relationship_deltas"]
        characters = deltas[0]["relationship"]["characters"] if deltas else []

        assert characters == expected_characters
        validate_json_output_contract(
            TaskType.EXTRACT_RELATIONSHIP_DELTAS,
            payload,
            context=context,
        )

    async def test_reading_power_response_has_required_keys(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.EVALUATE_READING_POWER,
            messages=[{"role": "user", "content": "evaluate reading power"}],
        )
        response = await adapter.complete(request)
        payload = json.loads(response.content)
        assert {"hook_type", "hook_strength", "micro_payoffs"} <= set(payload)

    async def test_book_consistency_response_has_required_keys(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.BOOK_CONSISTENCY,
            messages=[{"role": "user", "content": "book consistency"}],
        )
        response = await adapter.complete(request)
        payload = json.loads(response.content)
        assert {"issues", "summary", "consistency_score"} <= set(payload)

    async def test_adjust_outline_response_has_required_keys(self, adapter: MockAdapter) -> None:
        request = ModelRequest(
            task_type=TaskType.ADJUST_OUTLINE,
            messages=[{"role": "user", "content": "adjust outline"}],
        )
        response = await adapter.complete(request)
        payload = json.loads(response.content)
        assert {"adjusted_chapters", "adjustment_summary"} <= set(payload)
        assert isinstance(payload["adjusted_chapters"][0]["chapter_number"], int)

    async def test_all_json_mock_responses_close_contract_loop(
        self,
        adapter: MockAdapter,
    ) -> None:
        failures: list[str] = []

        for task_type in TaskType:
            base_contract = resolve_task_format_contract(task_type)
            if base_contract is None or base_contract.output_kind != OutputKind.JSON:
                continue

            contexts: list[dict[str, object]] = [{}]
            if task_type in {TaskType.GENERATE_CONFIG, TaskType.POLISH_CONFIG}:
                contexts.extend(({"mode": "short"}, {"mode": "long"}))

            for context in contexts:
                response = await adapter.complete(
                    ModelRequest(
                        task_type=task_type,
                        messages=[{"role": "user", "content": f"contract loop {task_type.value}"}],
                    )
                )
                try:
                    payload = json.loads(response.content)
                    assert isinstance(payload, dict)
                    apply_task_output_adapter(payload, task_type, context=context)
                    validate_response_schema(payload, task_type)
                    apply_task_response_defaults(payload, task_type)
                    validate_json_output_contract(task_type, payload, context=context)
                except Exception as exc:
                    label = f"{task_type.value} context={context or '{}'}"
                    failures.append(f"{label}: {type(exc).__name__}: {exc}")

        assert failures == []
