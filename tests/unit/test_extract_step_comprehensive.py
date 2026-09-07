"""Tests for ExtractCanonDeltaStep — happy path, error path, and boundary conditions."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep, ExtractInput
from tests.unit.conftest import _MockRouter


class _FakeBuilder:
    def __init__(self) -> None:
        self.last_context: dict | None = None
        self.last_max_tokens: int = 0

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
        self.last_max_tokens = max_tokens
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _make_step(json_payload: dict) -> tuple[ExtractCanonDeltaStep, _FakeBuilder]:
    builder = _FakeBuilder()
    step = ExtractCanonDeltaStep(
        _MockRouter(json_payload=json_payload, completion_tokens=200),
        builder,
        # These tests exercise the explicit legacy path. The production default
        # uses the split DAG, which is covered separately below.
        settings=Settings(split_tasks_enabled=False),
    )
    return step, builder


def _base_payload(**overrides) -> dict:
    base = {
        "canon_delta": {
            "source_chapter": 1,
            "character_updates": {},
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "摘要",
        },
        "creative_report": {
            "new_characters": [],
            "new_locations": [],
            "new_key_items": [],
            "plot_deviations": [],
            "suggestions_for_next_chapter": [],
            "creative_highlights": [],
            "structured_summary": "摘要",
            "must_carry_forward": [],
            "bridge_hints": [],
        },
        "chapter_exit_state": {
            "chapter_number": 1,
            "time_marker": "",
            "location": "",
            "pov": "",
            "must_carry_forward": [],
            "open_questions": [],
            "character_states": {},
        },
        "character_state_deltas": [],
        "relationship_deltas": [],
        "plot_thread_deltas": [],
        "structured_summary": "摘要",
    }
    base.update(overrides)
    return base


async def test_extract_step_happy_path_minimal() -> None:
    payload = _base_payload(
        canon_delta={
            "source_chapter": 1,
            "character_updates": {},
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "第一章摘要",
        },
    )
    step, _ = _make_step(payload)

    input_data = ExtractInput(
        chapter_number=1,
        chapter_text="这是第一章的正文内容。",
        known_characters=["主角"],
    )

    result = await step.run(input_data)

    assert isinstance(result, ChapterOutcome)
    assert result.source_chapter == 1
    assert result.chapter_summary == "第一章摘要"


async def test_split_extract_uses_dependency_dag() -> None:
    class _SplitStep(ExtractCanonDeltaStep):
        def __init__(self) -> None:
            super().__init__(_MockRouter(json_payload={}), _FakeBuilder(), settings=Settings())
            self.calls: list[tuple[TaskType, dict]] = []

        async def _call_split_fragment(
            self,
            task_type: TaskType,
            context: dict,
            max_tokens: int,
            temperature: float,
            required_keys: tuple[str, ...],
            max_retries: int,
        ) -> dict:
            self.calls.append((task_type, dict(context)))
            if task_type is TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT:
                return {
                    "chapter_exit_state": {
                        "chapter_number": 1,
                        "time_marker": "",
                        "location": "",
                        "pov": "",
                        "must_carry_forward": [],
                        "open_questions": [],
                        "character_states": {},
                    },
                    "structured_summary": "摘要",
                }
            if task_type is TaskType.EXTRACT_CHARACTER_STATE_DELTAS:
                return {"character_state_deltas": [{"name": "主角", "change_summary": "更冷静"}]}
            if task_type is TaskType.EXTRACT_RELATIONSHIP_DELTAS:
                assert context["character_state_deltas"] == [
                    {"name": "主角", "change_summary": "更冷静"}
                ]
                return {"relationship_deltas": []}
            if task_type is TaskType.EXTRACT_CANON_DELTA:
                return {
                    "canon_delta": {
                        "source_chapter": 1,
                        "character_updates": {},
                        "new_events": [],
                        "foreshadowing_updates": [],
                        "new_world_facts": {},
                        "chapter_summary": "摘要",
                    }
                }
            if task_type is TaskType.EXTRACT_CREATIVE_REPORT:
                return {
                    "creative_report": {
                        "new_characters": [],
                        "new_locations": [],
                        "new_key_items": [],
                        "plot_deviations": [],
                        "suggestions_for_next_chapter": "",
                        "creative_highlights": [],
                        "structured_summary": "摘要",
                        "must_carry_forward": [],
                        "bridge_hints": [],
                    }
                }
            if task_type is TaskType.EXTRACT_PLOT_THREAD_DELTAS:
                assert "relationship_deltas" in context
                return {"plot_thread_deltas": []}
            raise AssertionError(f"unexpected task: {task_type}")

    step = _SplitStep()
    await step._execute_split(
        ExtractInput(
            chapter_number=1,
            chapter_text="主角在雨夜做出选择。",
            known_characters=["主角"],
        ),
        {
            "chapter_number": 1,
            "chapter_text": "主角在雨夜做出选择。",
            "known_characters": ["主角"],
        },
    )

    positions = {task_type: i for i, (task_type, _) in enumerate(step.calls)}
    assert (
        positions[TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT]
        < positions[TaskType.EXTRACT_CHARACTER_STATE_DELTAS]
    )
    assert (
        positions[TaskType.EXTRACT_CHARACTER_STATE_DELTAS]
        < positions[TaskType.EXTRACT_RELATIONSHIP_DELTAS]
    )
    assert (
        positions[TaskType.EXTRACT_RELATIONSHIP_DELTAS]
        < positions[TaskType.EXTRACT_PLOT_THREAD_DELTAS]
    )


def test_split_extract_context_contains_empty_first_chapter_baselines() -> None:
    step = ExtractCanonDeltaStep(
        _MockRouter(json_payload={}),
        _FakeBuilder(),
        settings=Settings(),
    )

    context = step._build_context(
        ExtractInput(
            chapter_number=1,
            chapter_text="第一章正文。",
            known_characters=["主角"],
        )
    )

    assert context["prior_violations"] == []
    assert context["existing_thread_ids"] == []
    assert context["prior_relationships"] == []
    assert context["prior_character_snapshots"] == []
    assert context["prior_plot_threads"] == []
    assert context["authoritative_character_genders"] == {}


async def test_split_extract_failure_does_not_fallback_to_monolithic_request() -> None:
    class _NoFallbackStep(ExtractCanonDeltaStep):
        def __init__(self) -> None:
            super().__init__(_MockRouter(json_payload={}), _FakeBuilder(), settings=Settings())
            self.legacy_called = False

        async def _execute_split(self, input_data: ExtractInput, ctx: dict) -> ChapterOutcome:
            raise RuntimeError("fragment context failure")

        async def _execute_legacy(self, input_data: ExtractInput, ctx: dict) -> ChapterOutcome:
            self.legacy_called = True
            raise AssertionError("legacy extraction must not be called")

    step = _NoFallbackStep()

    with pytest.raises(RuntimeError, match="fragment context failure"):
        await step._execute(
            ExtractInput(
                chapter_number=1,
                chapter_text="第一章正文。",
                known_characters=["主角"],
            )
        )

    assert step.legacy_called is False


async def test_extract_step_with_character_updates() -> None:
    payload = _base_payload(
        canon_delta={
            "source_chapter": 2,
            "character_updates": {
                "林远": {
                    "name": "林远",
                    "alive": True,
                    "location": "废弃图书馆",
                    "emotional_state": "坚定",
                    "inventory": ["铜质怀表"],
                }
            },
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "林远进入图书馆",
        },
        chapter_number=2,
    )
    step, _ = _make_step(payload)

    input_data = ExtractInput(
        chapter_number=2,
        chapter_text="林远走进了废弃图书馆。",
        known_characters=["林远"],
    )

    result = await step.run(input_data)

    assert "林远" in result.character_updates


async def test_extract_step_rejects_missing_top_level_deltas() -> None:
    payload = _base_payload(
        canon_delta={
            "source_chapter": 2,
            "character_updates": {
                "林远": {
                    "name": "林远",
                    "alive": True,
                    "location": "废弃图书馆",
                    "emotional_state": "坚定",
                    "inventory": ["铜质怀表"],
                }
            },
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "林远进入图书馆",
        },
        chapter_number=2,
    )
    payload.pop("character_state_deltas")
    payload.pop("relationship_deltas")
    payload.pop("plot_thread_deltas")
    payload.pop("structured_summary")
    step, _ = _make_step(payload)

    with pytest.raises(ValueError, match="JSON schema validation failed"):
        await step.run(
            ExtractInput(
                chapter_number=2,
                chapter_text="林远走进了废弃图书馆。",
                known_characters=["林远"],
            )
        )


async def test_extract_step_rejects_late_sections_without_top_level_contract() -> None:
    payload = _base_payload(
        canon_delta={
            "source_chapter": 2,
            "character_updates": {},
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "图书馆追查怀表",
        },
        chapter_exit_state={
            "chapter_number": 2,
            "time_marker": "夜",
            "location": "废弃图书馆",
            "pov": "林远",
            "must_carry_forward": ["铜质怀表继续发烫"],
            "open_questions": [],
            "character_end_states": {},
            "character_state_deltas": [
                {
                    "name": "林远",
                    "change_summary": "决定继续追查怀表来源",
                    "state": {
                        "alive": True,
                        "location": "废弃图书馆",
                        "emotional_state": "坚定",
                        "inventory": ["铜质怀表"],
                    },
                }
            ],
            "relationship_deltas": [
                {
                    "pair_id": "林远__周岚",
                    "characters": ["林远", "周岚"],
                    "change_summary": "初步建立互信",
                    "relationship": {
                        "pair_id": "林远__周岚",
                        "characters": ["林远", "周岚"],
                        "public_status": "合作",
                    },
                }
            ],
            "plot_thread_deltas": [
                {
                    "thread_id": "watch_origin",
                    "change_summary": "怀表来源被重新提出",
                    "thread": {
                        "thread_id": "watch_origin",
                        "title": "怀表来源",
                        "status": "active",
                    },
                }
            ],
            "structured_summary": "林远在图书馆决定继续追查怀表来源。",
        },
    )
    payload.pop("character_state_deltas")
    payload.pop("relationship_deltas")
    payload.pop("plot_thread_deltas")
    payload.pop("structured_summary")
    step, _ = _make_step(payload)

    with pytest.raises(ValueError, match="JSON schema validation failed"):
        await step.run(
            ExtractInput(
                chapter_number=2,
                chapter_text="林远握紧铜质怀表，决定继续追查它的来源。",
                known_characters=["林远", "周岚"],
            )
        )


async def test_extract_step_empty_chapter_text() -> None:
    payload = _base_payload(
        canon_delta={
            "source_chapter": 1,
            "character_updates": {},
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "",
        },
        creative_report={
            "new_characters": [],
            "new_locations": [],
            "new_key_items": [],
            "plot_deviations": [],
            "suggestions_for_next_chapter": [],
            "creative_highlights": [],
            "structured_summary": "",
            "must_carry_forward": [],
            "bridge_hints": [],
        },
        chapter_exit_state={
            "chapter_number": 1,
            "time_marker": "",
            "location": "",
            "pov": "",
            "must_carry_forward": [],
            "open_questions": [],
            "character_states": {},
        },
        structured_summary="",
    )
    step, _ = _make_step(payload)

    input_data = ExtractInput(
        chapter_number=1,
        chapter_text="",
        known_characters=[],
    )

    result = await step.run(input_data)
    assert isinstance(result, ChapterOutcome)


async def test_extract_step_prior_state_baseline_passed_to_context() -> None:
    payload = _base_payload(
        canon_delta={
            "source_chapter": 3,
            "character_updates": {},
            "new_events": [],
            "foreshadowing_updates": [],
            "new_world_facts": {},
            "chapter_summary": "第三章摘要",
        },
    )
    step, builder = _make_step(payload)

    input_data = ExtractInput(
        chapter_number=3,
        chapter_text="第三章正文内容。",
        known_characters=["主角", "配角"],
        prior_relationships=[{"pair_id": "1", "characters": ["A", "B"]}],
        prior_character_snapshots=[{"name": "A", "alive": True}],
        prior_plot_threads=[{"id": "thread1", "status": "active"}],
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert "prior_relationships" in builder.last_context
    assert "prior_character_snapshots" in builder.last_context
    assert "prior_plot_threads" in builder.last_context
    assert builder.last_context.get("max_character_state_deltas") == 8
    assert builder.last_context.get("max_relationship_deltas") == 8
    assert builder.last_context.get("max_plot_thread_deltas") == 8
    assert builder.last_context.get("max_exit_state_characters") == 6


async def test_extract_step_token_estimation_scales_with_chapter_length() -> None:
    payload = _base_payload()

    step_short, builder_short = _make_step(payload)
    await step_short.run(
        ExtractInput(
            chapter_number=1,
            chapter_text="短文本",
            known_characters=["A"],
        )
    )

    step_long, builder_long = _make_step(payload)
    await step_long.run(
        ExtractInput(
            chapter_number=1,
            chapter_text="长文本" * 5000,
            known_characters=["A", "B", "C", "D", "E"],
        )
    )

    assert builder_long.last_max_tokens >= builder_short.last_max_tokens


async def test_extract_step_with_outline_summary() -> None:
    payload = _base_payload()
    step, builder = _make_step(payload)

    input_data = ExtractInput(
        chapter_number=1,
        chapter_text="正文",
        known_characters=[],
        chapter_outline_summary="本章大纲：主角发现秘密",
    )

    await step.run(input_data)

    assert builder.last_context is not None
    assert builder.last_context.get("chapter_outline_summary") == "本章大纲：主角发现秘密"


async def test_extract_step_passes_authoritative_character_genders_to_context() -> None:
    payload = _base_payload()
    step, builder = _make_step(payload)

    await step.run(
        ExtractInput(
            chapter_number=1,
            chapter_text="正文",
            known_characters=["沈照夜"],
            authoritative_character_genders={"沈照夜": "男"},
        )
    )

    assert builder.last_context is not None
    assert builder.last_context.get("authoritative_character_genders") == {"沈照夜": "男"}
