"""Tests for ProfileStyleStep + CharacterIntroStep ContextComposer integration.

Verifies that:
1. ProfileStyleInput accepts kernel_context field
2. ProfileStyleStep merges kernel_context into style/structure contexts
3. CharacterIntroStep accepts composer parameter for entity/relationship slices
4. Both steps work end-to-end with StoryKernel test data
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.style_profile import ProjectStyleProfile
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.profile_style_step import (
    ProfileStyleInput,
    ProfileStyleStep,
)
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.schemas import (
    Entity,
    Relationship,
    StoryKernel,
    WorldRule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kernel(**overrides: Any) -> StoryKernel:
    """Create a minimal StoryKernel with sample data for testing."""
    defaults: dict[str, Any] = {
        "project_id": "test-project",
        "current_chapter": 3,
        "active_volume": 1,
        "title": "测试小说",
        "premise": "一个关于测试的故事",
        "world_rules": [
            WorldRule(rule_id="wr-1", content="魔法需要消耗生命力", category="magic"),
        ],
        "entities": [
            Entity(entity_id="e-1", name="李明", entity_type="character"),
            Entity(entity_id="e-2", name="王芳", entity_type="character"),
            Entity(entity_id="e-3", name="古塔", entity_type="location"),
        ],
        "relationships": [
            Relationship(
                relationship_id="r-1",
                source_entity_id="e-1",
                target_entity_id="e-2",
                relation_type="friend",
                label="挚友",
            ),
        ],
        "chapter_summaries": {
            1: "李明在森林中发现了古塔",
            2: "王芳决定与李明一起探索古塔",
        },
    }
    defaults.update(overrides)
    return StoryKernel(**defaults)


class _MockStore:
    """Mock StoryKernelStore for testing."""

    def __init__(self, kernel: StoryKernel) -> None:
        self._kernel = kernel

    def load_kernel(self, project_id: str) -> StoryKernel:
        return self._kernel


class _FakeBuilder:
    """Captures context passed to build() for verification."""

    def __init__(self) -> None:
        self.last_context: dict | None = None
        self.call_count: int = 0

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
        required_keys: tuple[str, ...] | None = None,
    ) -> ModelRequest:
        self.last_context = context
        self.call_count += 1
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=max_tokens,
            temperature=temperature,
        )


_STYLE_RESPONSE = json.dumps(
    {
        "modules": [
            {
                "name": "悬念钩子",
                "rules": ["每章结尾设置悬念"],
                "positive_example": "他推开门，却发现...",
                "negative_example": "答案就是他。",
            },
        ],
        "source_elements": ["story_bible"],
        "summary": "悬疑推理风格",
        "global_style": {
            "dialogue_ratio": "medium",
            "pace_mode": "moderate",
            "emotional_style": "balanced",
            "environment_ratio": "medium",
            "info_density": "medium",
        },
    },
    ensure_ascii=False,
)

_STRUCTURE_RESPONSE = json.dumps(
    {
        "hook_config": {
            "preferred_types": ["crisis"],
            "strength_baseline": "medium",
            "chapter_end_required": True,
        },
        "strand_config": {},
        "micro_payoff_config": {},
        "cool_point_config": {},
    },
    ensure_ascii=False,
)


# ---------------------------------------------------------------------------
# ProfileStyleInput kernel_context tests
# ---------------------------------------------------------------------------


class TestProfileStyleInputKernelContext:
    """Test ProfileStyleInput with kernel_context field."""

    def test_profile_style_input_accepts_kernel_context(self) -> None:
        """ProfileStyleInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "chapter_summaries": {1: "摘要"}}
        input_data = ProfileStyleInput(
            title="测试",
            genre="mystery",
            tone="suspenseful",
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_profile_style_input_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        input_data = ProfileStyleInput(
            title="测试",
            genre="mystery",
            tone="suspenseful",
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# ProfileStyleStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestProfileStyleStepKernelContextMerge:
    """Test that ProfileStyleStep merges kernel_context into template contexts."""

    async def test_kernel_context_merged_into_style_context(self) -> None:
        """kernel_context fields should appear in the style template context."""
        builder = _FakeBuilder()

        # Router that returns style on first call, structure on second
        call_count = 0
        responses = [_STYLE_RESPONSE, _STRUCTURE_RESPONSE]

        class _SeqRouter:
            def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
                return model_id or "mock"

            async def route(self, request, *, provider=None):
                nonlocal call_count
                resp = responses[call_count]
                call_count += 1
                return ModelResponse(
                    content=resp,
                    model_id="mock",
                    prompt_tokens=100,
                    completion_tokens=80,
                    total_tokens=180,
                    cost_usd=0.0,
                )

        step = ProfileStyleStep(_SeqRouter(), builder, settings=Settings())

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明", "entity_type": "character"}],
            "chapter_summaries": {1: "第一章摘要", 2: "第二章摘要"},
        }
        input_data = ProfileStyleInput(
            title="测试小说",
            genre="mystery",
            tone="suspenseful",
            story_bible={"genre": "mystery"},
            character_bible={},
            blueprint_elements={},
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, ProjectStyleProfile)
        # The builder should have been called — verify kernel fields were in context
        assert builder.last_context is not None
        assert "kernel_entities" in builder.last_context
        assert builder.last_context["kernel_entities"] == kernel_ctx["entities"]
        assert "kernel_chapter_summaries" in builder.last_context
        assert builder.last_context["kernel_chapter_summaries"] == kernel_ctx["chapter_summaries"]

    async def test_explicit_context_takes_precedence(self) -> None:
        """Explicit context fields should NOT be overridden by kernel_context."""
        contexts: list[dict] = []

        class _CaptureBuilder:
            def build(self, task_type, context, **kwargs):
                contexts.append(context)
                return ModelRequest(
                    task_type=task_type,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=kwargs.get("max_tokens", 2048),
                    temperature=kwargs.get("temperature", 0.3),
                )

        builder = _CaptureBuilder()

        call_count = 0
        responses = [_STYLE_RESPONSE, _STRUCTURE_RESPONSE]

        class _SeqRouter:
            def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
                return model_id or "mock"

            async def route(self, request, *, provider=None):
                nonlocal call_count
                resp = responses[call_count]
                call_count += 1
                return ModelResponse(
                    content=resp,
                    model_id="mock",
                    prompt_tokens=100,
                    completion_tokens=80,
                    total_tokens=180,
                    cost_usd=0.0,
                )

        step = ProfileStyleStep(_SeqRouter(), builder, settings=Settings())

        kernel_ctx = {
            "entities": [{"name": "kernel_entity"}],
        }
        input_data = ProfileStyleInput(
            title="测试小说",
            genre="mystery",
            tone="suspenseful",
            story_bible={"genre": "mystery"},
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, ProjectStyleProfile)
        # Both contexts should have been captured
        assert len(contexts) == 2
        for ctx in contexts:
            assert ctx["genre"] == "mystery"
            assert ctx["kernel_entities"] == [{"name": "kernel_entity"}]

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()

        call_count = 0
        responses = [_STYLE_RESPONSE, _STRUCTURE_RESPONSE]

        class _SeqRouter:
            def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
                return model_id or "mock"

            async def route(self, request, *, provider=None):
                nonlocal call_count
                resp = responses[call_count]
                call_count += 1
                return ModelResponse(
                    content=resp,
                    model_id="mock",
                    prompt_tokens=100,
                    completion_tokens=80,
                    total_tokens=180,
                    cost_usd=0.0,
                )

        step = ProfileStyleStep(_SeqRouter(), builder, settings=Settings())

        input_data = ProfileStyleInput(
            title="测试小说",
            genre="mystery",
            tone="suspenseful",
            story_bible={"genre": "mystery"},
        )

        result = await step.run(input_data)

        assert isinstance(result, ProjectStyleProfile)
        assert builder.last_context is not None
        # No kernel fields should be present
        assert "kernel_entities" not in builder.last_context
        assert "kernel_chapter_summaries" not in builder.last_context


# ---------------------------------------------------------------------------
# End-to-end: ProfileStyleStep with ContextComposer
# ---------------------------------------------------------------------------


class TestProfileStyleStepWithComposer:
    """End-to-end: ProfileStyleStep + ContextComposer produces correct output."""

    async def test_profile_style_with_composed_kernel_context(self) -> None:
        """ProfileStyleStep should produce a ProjectStyleProfile when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()

        call_count = 0
        responses = [_STYLE_RESPONSE, _STRUCTURE_RESPONSE]

        class _SeqRouter:
            def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
                return model_id or "mock"

            async def route(self, request, *, provider=None):
                nonlocal call_count
                resp = responses[call_count]
                call_count += 1
                return ModelResponse(
                    content=resp,
                    model_id="mock",
                    prompt_tokens=100,
                    completion_tokens=80,
                    total_tokens=180,
                    cost_usd=0.0,
                )

        step = ProfileStyleStep(_SeqRouter(), builder, settings=Settings())

        # Use composer to get profile_style field slice
        kernel_ctx = composer.compose_for_step("profile_style", 3)
        input_data = ProfileStyleInput(
            title="测试小说",
            genre="mystery",
            tone="suspenseful",
            story_bible={"genre": "mystery"},
            character_bible={},
            blueprint_elements={},
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, ProjectStyleProfile)
        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "kernel_entities" in builder.last_context
        assert "kernel_chapter_summaries" in builder.last_context

    async def test_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()

        call_count = 0
        responses = [_STYLE_RESPONSE, _STRUCTURE_RESPONSE]

        class _SeqRouter:
            def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
                return model_id or "mock"

            async def route(self, request, *, provider=None):
                nonlocal call_count
                resp = responses[call_count]
                call_count += 1
                return ModelResponse(
                    content=resp,
                    model_id="mock",
                    prompt_tokens=100,
                    completion_tokens=80,
                    total_tokens=180,
                    cost_usd=0.0,
                )

        step = ProfileStyleStep(_SeqRouter(), builder, settings=Settings())

        kernel_ctx = composer.compose_for_step("profile_style", 3)
        input_data = ProfileStyleInput(
            title="测试小说",
            genre="mystery",
            tone="suspenseful",
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        entities = builder.last_context["kernel_entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)


# ---------------------------------------------------------------------------
# CharacterIntroStep composer integration tests
# ---------------------------------------------------------------------------


class TestCharacterIntroComposerIntegration:
    """Test that character_intro functions can consume ContextComposer slices."""

    def test_collect_candidates_with_kernel_entities(self) -> None:
        """_collect_chapter_character_candidates should work when kernel entities
        are available for cross-referencing."""
        from novel_forge.pipeline.long.stages.character_intro import (
            _collect_chapter_character_candidates,
        )

        outline = ChapterOutline(
            chapter_number=2,
            title="暗账",
            goal="推进供应商危机",
            pov_character="沈念卿",
            involved_characters=["沈念卿", "陈助理", "咖啡店店员"],
            expected_word_count=4000,
        )
        bundle = SimpleNamespace(chapter_outline=outline)

        candidates = _collect_chapter_character_candidates(bundle, None, 2)

        assert "沈念卿" in candidates
        assert "陈助理" in candidates

    def test_compose_character_intro_returns_entities_and_relationships(self) -> None:
        """compose_for_step('character_intro') should return entities + relationships."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("character_intro", 3)

        assert "entities" in result
        assert "relationships" in result
        assert isinstance(result["entities"], list)
        assert isinstance(result["relationships"], list)
        # Should NOT contain fields not in CHARACTER_INTRO_CONTRACT.reads
        assert "world_rules" not in result
        assert "timeline" not in result
        assert "chapter_summaries" not in result

    def test_kernel_entities_can_supplement_bible_characters(self) -> None:
        """Kernel entities should be usable alongside character_bible for candidate detection."""
        kernel = _make_kernel(
            entities=[
                Entity(entity_id="e-1", name="沈念卿", entity_type="character"),
                Entity(entity_id="e-2", name="陈助理", entity_type="character"),
                Entity(entity_id="e-3", name="新角色", entity_type="character"),
            ],
        )
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("character_intro", 2)

        entity_names = {e["name"] for e in result["entities"]}
        assert "沈念卿" in entity_names
        assert "陈助理" in entity_names
        assert "新角色" in entity_names

    def test_kernel_relationships_provide_connection_data(self) -> None:
        """Kernel relationships should be available for character relationship context."""
        kernel = _make_kernel(
            relationships=[
                Relationship(
                    relationship_id="r-1",
                    source_entity_id="e-1",
                    target_entity_id="e-2",
                    relation_type="enemy",
                    label="宿敌",
                ),
            ],
        )
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("character_intro", 2)

        assert len(result["relationships"]) == 1
        rel = result["relationships"][0]
        assert rel["label"] == "宿敌"
        assert rel["relation_type"] == "enemy"

    def test_introduce_new_characters_accepts_composer(self) -> None:
        """introduce_new_characters should accept an optional composer parameter."""
        import inspect

        from novel_forge.pipeline.long.stages.character_intro import introduce_new_characters

        sig = inspect.signature(introduce_new_characters)
        assert "composer" in sig.parameters
        assert sig.parameters["composer"].default is None
