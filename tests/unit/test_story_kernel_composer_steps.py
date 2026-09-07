"""Tests for DraftStep/EditStep/PolishStep/EvaluateStep/VolumeStep ContextComposer integration.

Verifies that:
1. All step inputs accept kernel_context field
2. kernel_context fields are merged into template context
3. explicit context fields take precedence over kernel_context
4. ContextComposer convenience methods work for each step
5. All steps work end-to-end with StoryKernel test data
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.draft_step import DraftInput, DraftStep
from novel_forge.pipeline.steps.edit_step import EditInput, EditStep
from novel_forge.pipeline.steps.evaluate_step import EvaluateStep
from novel_forge.pipeline.steps.polish_step import PolishInput, PolishResult, PolishStep
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput, VolumeAuditStep
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.schemas import (
    Entity,
    KnowledgeLedger,
    MotifProtocol,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)
from tests.unit.conftest import _MockRouter

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
            WorldRule(rule_id="wr-2", content="时间不可逆转", category="temporal"),
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
        "timeline": [
            TimelineAnchor(
                anchor_id="t-1",
                chapter=1,
                event="李明发现了古塔",
                characters_involved=["e-1"],
            ),
            TimelineAnchor(
                anchor_id="t-2",
                chapter=2,
                event="王芳加入冒险",
                characters_involved=["e-1", "e-2"],
            ),
        ],
        "object_ledger": [],
        "knowledge_ledger": [
            KnowledgeLedger(
                entry_id="k-1",
                entity_id="e-1",
                fact="古塔隐藏着秘密",
                knowledge_type="known",
            ),
        ],
        "access_ledger": [],
        "promise_ledger": [
            PromiseLedger(
                entry_id="p-1",
                description="古塔的秘密终将揭开",
                promise_type="foreshadow",
                planted_chapter=1,
                status="planted",
            ),
        ],
        "motif_protocols": [
            MotifProtocol(
                entry_id="m-1",
                motif_name="月光",
                motif_type="symbol",
                description="月光象征希望",
            ),
        ],
        "business_dependencies": [],
        "chapter_summaries": {
            1: "李明在森林中发现了古塔",
            2: "王芳决定与李明一起探索古塔",
        },
        "banned_phrases": ["他不禁想到"],
        "notes": "测试笔记",
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


# ---------------------------------------------------------------------------
# DraftInput kernel_context tests
# ---------------------------------------------------------------------------


class TestDraftInputKernelContext:
    """Test DraftInput with kernel_context field."""

    def test_draft_input_accepts_kernel_context(self) -> None:
        """DraftInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "world_rules": []}
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_draft_input_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 1},
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# EditInput kernel_context tests
# ---------------------------------------------------------------------------


class TestEditInputKernelContext:
    """Test EditInput with kernel_context field."""

    def test_edit_input_accepts_kernel_context(self) -> None:
        """EditInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "banned_phrases": ["禁词"]}
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="草稿",
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_edit_input_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="草稿",
            context={"chapter_number": 1},
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# DraftStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestDraftStepKernelContextMerge:
    """Test that DraftStep merges kernel_context into template context."""

    async def test_kernel_context_merged_into_template(self) -> None:
        """kernel_context fields should appear in the template context."""
        builder = _FakeBuilder()
        step = DraftStep(_MockRouter("草稿内容", capture_calls=False), builder, settings=Settings())

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
        }
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3, "target_word_count": 3000},
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert builder.last_context["entities"] == kernel_ctx["entities"]
        assert "world_rules" in builder.last_context
        assert builder.last_context["world_rules"] == kernel_ctx["world_rules"]

    async def test_explicit_context_takes_precedence(self) -> None:
        """Explicit context fields should NOT be overridden by kernel_context."""
        builder = _FakeBuilder()
        step = DraftStep(_MockRouter("草稿内容", capture_calls=False), builder, settings=Settings())

        kernel_ctx = {
            "entities": [{"name": "kernel_entity"}],
            "chapter_number": 999,  # Should NOT override
        }
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3, "target_word_count": 3000},
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        # chapter_number from explicit context wins
        assert builder.last_context["chapter_number"] == 3
        # entities from kernel_context is merged
        assert builder.last_context["entities"] == [{"name": "kernel_entity"}]

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = DraftStep(_MockRouter("草稿内容", capture_calls=False), builder, settings=Settings())

        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3, "target_word_count": 3000, "genre": "fantasy"},
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert builder.last_context["chapter_number"] == 3
        assert builder.last_context["genre"] == "fantasy"
        assert "entities" not in builder.last_context

    async def test_kernel_context_with_reading_power_hint(self) -> None:
        """kernel_context and reading_power_hint should both be merged."""
        builder = _FakeBuilder()
        step = DraftStep(_MockRouter("草稿内容", capture_calls=False), builder, settings=Settings())

        kernel_ctx = {"entities": [{"name": "李明"}]}
        hint = {"suggestion": "增加悬念"}
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
            reading_power_hint=hint,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "reading_power_hint" in builder.last_context
        assert builder.last_context["reading_power_hint"] == hint


# ---------------------------------------------------------------------------
# EditStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestEditStepKernelContextMerge:
    """Test that EditStep merges kernel_context into template context."""

    async def test_kernel_context_merged_into_template(self) -> None:
        """kernel_context fields should appear in the template context."""
        builder = _FakeBuilder()
        step = EditStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "banned_phrases": ["他不禁想到"],
        }
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="原始草稿",
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert builder.last_context["entities"] == kernel_ctx["entities"]
        assert "banned_phrases" in builder.last_context
        assert builder.last_context["banned_phrases"] == ["他不禁想到"]

    async def test_explicit_context_takes_precedence(self) -> None:
        """Explicit context fields should NOT be overridden by kernel_context."""
        builder = _FakeBuilder()
        step = EditStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = {
            "entities": [{"name": "kernel_entity"}],
        }
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="原始草稿",
            context={"chapter_number": 3, "genre": "fantasy"},
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert builder.last_context["chapter_number"] == 3
        assert builder.last_context["genre"] == "fantasy"
        assert builder.last_context["entities"] == [{"name": "kernel_entity"}]

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = EditStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="原始草稿",
            context={"chapter_number": 3},
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert builder.last_context["draft_text"] == "原始草稿"
        assert builder.last_context["chapter_number"] == 3
        assert "entities" not in builder.last_context

    async def test_kernel_context_with_reading_power_hint(self) -> None:
        """kernel_context and reading_power_hint should both be merged."""
        builder = _FakeBuilder()
        step = EditStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = {"entities": [{"name": "李明"}]}
        hint = {"suggestion": "增加悬念"}
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="原始草稿",
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
            reading_power_hint=hint,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "reading_power_hint" in builder.last_context


# ---------------------------------------------------------------------------
# ContextComposer.compose_edit_input tests
# ---------------------------------------------------------------------------


class TestComposeEditInput:
    """Test compose_edit_input convenience method."""

    def test_returns_edit_fields(self) -> None:
        """compose_edit_input should return EDIT_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_edit_input(3)
        assert isinstance(result, dict)
        # Edit contract reads: entities, relationships, timeline, world_rules,
        # knowledge_ledger, access_ledger, banned_phrases.
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result
        assert "knowledge_ledger" in result
        assert "access_ledger" in result
        assert "banned_phrases" in result

    def test_does_not_include_draft_only_fields(self) -> None:
        """Edit input should not include fields unrelated to edit constraints."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_edit_input(3)
        # Edit does NOT read object/promise/motif ledgers.
        assert "object_ledger" not in result
        assert "promise_ledger" not in result
        assert "motif_protocols" not in result

    def test_banned_phrases_present(self) -> None:
        """Edit contract reads banned_phrases — should be in result."""
        kernel = _make_kernel(banned_phrases=["禁词A", "禁词B"])
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_edit_input(3)
        assert "banned_phrases" in result
        assert result["banned_phrases"] == ["禁词A", "禁词B"]


# ---------------------------------------------------------------------------
# End-to-end integration: DraftStep with ContextComposer
# ---------------------------------------------------------------------------


class TestDraftStepWithComposer:
    """End-to-end: DraftStep + ContextComposer produces correct output."""

    async def test_draft_with_composed_kernel_context(self) -> None:
        """DraftStep should produce a Draft when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = DraftStep(
            _MockRouter("这是一段测试草稿内容。", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = composer.compose_draft_input(3)
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3, "target_word_count": 3000},
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert result.text == "这是一段测试草稿内容。"
        assert result.iteration == 1

        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "world_rules" in builder.last_context
        assert "timeline" in builder.last_context

    async def test_draft_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = DraftStep(_MockRouter("草稿", capture_calls=False), builder, settings=Settings())

        kernel_ctx = composer.compose_draft_input(3)
        input_data = DraftInput(
            task_type=TaskType.DRAFT_CHAPTER,
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        # Entities should be list of dicts, not Entity instances
        entities = builder.last_context["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)


# ---------------------------------------------------------------------------
# End-to-end integration: EditStep with ContextComposer
# ---------------------------------------------------------------------------


class TestEditStepWithComposer:
    """End-to-end: EditStep + ContextComposer produces correct output."""

    async def test_edit_with_composed_kernel_context(self) -> None:
        """EditStep should produce an EditResult when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = EditStep(
            _MockRouter("润色后的文本内容。", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = composer.compose_edit_input(3)
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="原始草稿文本",
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert result.revised_text == "润色后的文本内容。"
        assert result.iteration == 1

        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "world_rules" in builder.last_context
        assert "banned_phrases" in builder.last_context

    async def test_edit_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = EditStep(_MockRouter("润色后", capture_calls=False), builder, settings=Settings())

        kernel_ctx = composer.compose_edit_input(3)
        input_data = EditInput(
            task_type=TaskType.EDIT_CHAPTER,
            draft_text="草稿",
            context={"chapter_number": 3},
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        entities = builder.last_context["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)


# ---------------------------------------------------------------------------
# PolishInput kernel_context tests
# ---------------------------------------------------------------------------


class TestPolishInputKernelContext:
    """Test PolishInput with kernel_context field."""

    def test_polish_input_accepts_kernel_context(self) -> None:
        """PolishInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "banned_phrases": ["禁词"]}
        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="章节内容",
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_polish_input_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        input_data = PolishInput(
            chapter_number=1,
            chapter_title="第一章",
            chapter_text="章节内容",
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# PolishStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestPolishStepKernelContextMerge:
    """Test that PolishStep merges kernel_context into template context."""

    async def test_kernel_context_merged_into_template(self) -> None:
        """kernel_context fields should appear in the template context."""
        builder = _FakeBuilder()
        step = PolishStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "banned_phrases": ["他不禁想到"],
        }
        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="原始章节文本",
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert builder.last_context["entities"] == kernel_ctx["entities"]
        assert "banned_phrases" in builder.last_context
        assert builder.last_context["banned_phrases"] == ["他不禁想到"]

    async def test_explicit_context_takes_precedence(self) -> None:
        """Explicit context fields should NOT be overridden by kernel_context."""
        builder = _FakeBuilder()
        step = PolishStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = {
            "chapter_number": 999,  # Should NOT override
        }
        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="原始章节文本",
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        # chapter_number from PolishInput fields wins
        assert builder.last_context["chapter_number"] == 3

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = PolishStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="原始章节文本",
            genre="fantasy",
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert builder.last_context["chapter_number"] == 3
        assert builder.last_context["genre"] == "fantasy"
        assert "entities" not in builder.last_context

    async def test_kernel_context_with_motif_protocols(self) -> None:
        """kernel_context with motif_protocols should be merged."""
        builder = _FakeBuilder()
        step = PolishStep(
            _MockRouter("润色后的文本", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = {
            "motif_protocols": [{"entry_id": "m-1", "motif_name": "月光"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
        }
        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="原始章节文本",
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "motif_protocols" in builder.last_context
        assert "world_rules" in builder.last_context


# ---------------------------------------------------------------------------
# ContextComposer.compose_polish_input tests
# ---------------------------------------------------------------------------


class TestComposePolishInput:
    """Test compose_polish_input convenience method."""

    def test_returns_polish_fields(self) -> None:
        """compose_polish_input should return POLISH_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_polish_input(3)
        assert isinstance(result, dict)
        # Polish contract reads: entities, relationships, timeline, world_rules,
        # banned_phrases, motif_protocols
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result
        assert "banned_phrases" in result
        assert "motif_protocols" in result

    def test_does_not_include_unread_fields(self) -> None:
        """Polish input should not include fields not in the contract's reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_polish_input(3)
        assert "object_ledger" not in result
        assert "knowledge_ledger" not in result
        assert "access_ledger" not in result
        assert "promise_ledger" not in result
        assert "business_dependencies" not in result
        assert "chapter_summaries" not in result

    def test_banned_phrases_present(self) -> None:
        """Polish contract reads banned_phrases — should be in result."""
        kernel = _make_kernel(banned_phrases=["禁词A", "禁词B"])
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_polish_input(3)
        assert "banned_phrases" in result
        assert result["banned_phrases"] == ["禁词A", "禁词B"]


# ---------------------------------------------------------------------------
# End-to-end integration: PolishStep with ContextComposer
# ---------------------------------------------------------------------------


class TestPolishStepWithComposer:
    """End-to-end: PolishStep + ContextComposer produces correct output."""

    async def test_polish_with_composed_kernel_context(self) -> None:
        """PolishStep should merge kernel_context into template context."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = PolishStep(
            _MockRouter("润色后的文本内容。", capture_calls=False), builder, settings=Settings()
        )

        kernel_ctx = composer.compose_polish_input(3)
        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="原始章节文本",
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, PolishResult)

        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "world_rules" in builder.last_context
        assert "banned_phrases" in builder.last_context

    async def test_polish_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = PolishStep(_MockRouter("润色后", capture_calls=False), builder, settings=Settings())

        kernel_ctx = composer.compose_polish_input(3)
        input_data = PolishInput(
            chapter_number=3,
            chapter_title="第三章",
            chapter_text="草稿",
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        entities = builder.last_context["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)


# ---------------------------------------------------------------------------
# EvaluateStep kernel_context tests
# ---------------------------------------------------------------------------


def _make_eval_payload() -> dict:
    """Standard evaluation payload for tests."""
    return {
        "scores": [
            {"dimension": "consistency", "score": 8.0, "comment": "一致性好"},
            {"dimension": "continuity", "score": 7.0, "comment": "连贯性良好"},
            {"dimension": "plot_progression", "score": 7.5, "comment": "本章有明确推进"},
            {"dimension": "character", "score": 7.5, "comment": "角色塑造不错"},
            {"dimension": "style", "score": 7.0, "comment": "风格一致"},
            {"dimension": "engagement", "score": 8.0, "comment": "吸引力强"},
            {"dimension": "pacing", "score": 7.5, "comment": "节奏适中"},
        ],
        "overall_score": 7.5,
        "passed": True,
        "threshold": 6.0,
        "summary": "评估通过",
        "repair_suggestions": [],
    }


class TestEvaluateStepKernelContext:
    """Test EvaluateStep with kernel_context parameter."""

    async def test_evaluate_step_accepts_kernel_context(self) -> None:
        """EvaluateStep should accept kernel_context in constructor."""
        builder = _FakeBuilder()
        step = EvaluateStep(
            _MockRouter(json_payload=_make_eval_payload(), completion_tokens=200),
            builder,
            settings=Settings(),
            kernel_context={"entities": [{"name": "李明"}]},
        )
        assert step._kernel_context == {"entities": [{"name": "李明"}]}

    async def test_kernel_context_merged_into_extra_context(self) -> None:
        """kernel_context fields should be merged into the evaluator context."""
        builder = _FakeBuilder()
        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "chapter_summaries": {1: "第一章摘要"},
        }
        step = EvaluateStep(
            _MockRouter(json_payload=_make_eval_payload(), completion_tokens=200),
            builder,
            settings=Settings(),
            kernel_context=kernel_ctx,
        )

        result = await step.run("测试文本")

        assert isinstance(result, EvalReport)
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert builder.last_context["entities"] == kernel_ctx["entities"]
        assert "chapter_summaries" in builder.last_context

    async def test_explicit_extra_context_takes_precedence(self) -> None:
        """Explicit extra_context should NOT be overridden by kernel_context."""
        builder = _FakeBuilder()
        kernel_ctx = {
            "entities": [{"name": "kernel_entity"}],
            "chapter_summaries": {999: "不应该覆盖"},
        }
        step = EvaluateStep(
            _MockRouter(json_payload=_make_eval_payload(), completion_tokens=200),
            builder,
            settings=Settings(),
            kernel_context=kernel_ctx,
            extra_context={"chapter_summaries": {1: "第一章摘要"}},
        )

        result = await step.run("测试文本")

        assert isinstance(result, EvalReport)
        assert builder.last_context is not None
        # chapter_summaries from extra_context wins (since it's merged first)
        # Actually, the kernel_context is merged first, then extra_context overrides
        assert builder.last_context["chapter_summaries"] == {"1": "第一章摘要"}

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = EvaluateStep(
            _MockRouter(json_payload=_make_eval_payload(), completion_tokens=200),
            builder,
            settings=Settings(),
            extra_context={"target_word_count": 3000},
        )

        result = await step.run("测试文本")

        assert isinstance(result, EvalReport)
        assert builder.last_context is not None
        assert builder.last_context["draft_text"] == "测试文本"
        assert builder.last_context["target_word_count"] == 3000
        assert "entities" not in builder.last_context


# ---------------------------------------------------------------------------
# ContextComposer.compose_evaluate_input tests
# ---------------------------------------------------------------------------


class TestComposeEvaluateInput:
    """Test compose_evaluate_input convenience method."""

    def test_returns_evaluate_fields(self) -> None:
        """compose_evaluate_input should return EVALUATE_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_evaluate_input(3)
        assert isinstance(result, dict)
        # Evaluate contract reads: entities, relationships, timeline, world_rules,
        # chapter_summaries
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result
        assert "chapter_summaries" in result

    def test_does_not_include_unread_fields(self) -> None:
        """Evaluate input should not include fields not in the contract's reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_evaluate_input(3)
        assert "object_ledger" not in result
        assert "knowledge_ledger" not in result
        assert "access_ledger" not in result
        assert "promise_ledger" not in result
        assert "motif_protocols" not in result
        assert "banned_phrases" not in result
        assert "business_dependencies" not in result


# ---------------------------------------------------------------------------
# End-to-end integration: EvaluateStep with ContextComposer
# ---------------------------------------------------------------------------


class TestEvaluateStepWithComposer:
    """End-to-end: EvaluateStep + ContextComposer produces correct output."""

    async def test_evaluate_with_composed_kernel_context(self) -> None:
        """EvaluateStep should produce an EvalReport when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = EvaluateStep(
            _MockRouter(json_payload=_make_eval_payload(), completion_tokens=200),
            builder,
            settings=Settings(),
            kernel_context=composer.compose_evaluate_input(3),
        )

        result = await step.run("测试章节文本")

        assert isinstance(result, EvalReport)
        assert result.overall_score > 0

        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "world_rules" in builder.last_context
        assert "chapter_summaries" in builder.last_context

    async def test_evaluate_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = EvaluateStep(
            _MockRouter(json_payload=_make_eval_payload(), completion_tokens=200),
            builder,
            settings=Settings(),
            kernel_context=composer.compose_evaluate_input(3),
        )

        await step.run("测试文本")

        assert builder.last_context is not None
        entities = builder.last_context["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)


# ---------------------------------------------------------------------------
# VolumeAuditInput kernel_context tests
# ---------------------------------------------------------------------------


class TestVolumeAuditInputKernelContext:
    """Test VolumeAuditInput with kernel_context field."""

    def test_volume_input_accepts_kernel_context(self) -> None:
        """VolumeAuditInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "chapter_summaries": {1: "摘要"}}
        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[{"chapter": 1, "summary": "第一章"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_volume_input_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# VolumeAuditStep kernel_context merge tests
# ---------------------------------------------------------------------------


_VOLUME_AUDIT_PAYLOAD = {
    "volume_number": 1,
    "volume_summary": "第一卷整体质量良好，主线推进顺利",
    "milestone_status": [],
    "carry_over_characters": [],
    "retire_characters": [],
    "carry_over_items": [],
    "retire_items": [],
    "carry_over_world_fact_keys": [],
    "retire_world_fact_keys": [],
    "carry_over_foreshadowing_ids": [],
    "resolved_foreshadowing_ids": [],
    "next_volume_focus": "继续推进主线",
}


class TestVolumeAuditStepKernelContextMerge:
    """Test that VolumeAuditStep merges kernel_context into payload."""

    async def test_kernel_context_merged_into_payload(self) -> None:
        """kernel_context fields should appear in the LLM payload."""
        builder = _FakeBuilder()
        step = VolumeAuditStep(
            _MockRouter(json_payload=_VOLUME_AUDIT_PAYLOAD, completion_tokens=200),
            builder,
            settings=Settings(),
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "promise_ledger": [{"entry_id": "p-1", "description": "伏笔"}],
        }
        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[{"chapter": 1, "summary": "第一章"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, VolumeAuditReport)
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert builder.last_context["entities"] == kernel_ctx["entities"]
        assert "promise_ledger" in builder.last_context

    async def test_explicit_input_takes_precedence(self) -> None:
        """Explicit input fields should NOT be overridden by kernel_context."""
        builder = _FakeBuilder()
        step = VolumeAuditStep(
            _MockRouter(json_payload=_VOLUME_AUDIT_PAYLOAD, completion_tokens=200),
            builder,
            settings=Settings(),
        )

        kernel_ctx = {
            "chapter_summaries": {999: "不应该覆盖"},
        }
        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[{"chapter": 1, "summary": "第一章摘要"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, VolumeAuditReport)
        assert builder.last_context is not None
        # chapter_summaries from explicit input wins
        assert builder.last_context["chapter_summaries"] == [
            {"chapter": 1, "summary": "第一章摘要"}
        ]

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = VolumeAuditStep(
            _MockRouter(json_payload=_VOLUME_AUDIT_PAYLOAD, completion_tokens=200),
            builder,
            settings=Settings(),
        )

        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
        )

        result = await step.run(input_data)

        assert isinstance(result, VolumeAuditReport)
        assert builder.last_context is not None
        assert "entities" not in builder.last_context


# ---------------------------------------------------------------------------
# ContextComposer.compose_volume_input tests
# ---------------------------------------------------------------------------


class TestComposeVolumeInput:
    """Test compose_volume_input convenience method."""

    def test_returns_volume_fields(self) -> None:
        """compose_volume_input should return VOLUME_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_volume_input(1)
        assert isinstance(result, dict)
        # Volume contract reads: entities, relationships, timeline, world_rules,
        # promise_ledger, motif_protocols, chapter_summaries
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result
        assert "promise_ledger" in result
        assert "motif_protocols" in result
        assert "chapter_summaries" in result

    def test_does_not_include_unread_fields(self) -> None:
        """Volume input should not include fields not in the contract's reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_volume_input(1)
        assert "object_ledger" not in result
        assert "knowledge_ledger" not in result
        assert "access_ledger" not in result
        assert "banned_phrases" not in result
        assert "business_dependencies" not in result


# ---------------------------------------------------------------------------
# End-to-end integration: VolumeAuditStep with ContextComposer
# ---------------------------------------------------------------------------


class TestVolumeAuditStepWithComposer:
    """End-to-end: VolumeAuditStep + ContextComposer produces correct output."""

    async def test_volume_with_composed_kernel_context(self) -> None:
        """VolumeAuditStep should produce a VolumeAuditReport when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = VolumeAuditStep(
            _MockRouter(json_payload=_VOLUME_AUDIT_PAYLOAD, completion_tokens=200),
            builder,
            settings=Settings(),
        )

        kernel_ctx = composer.compose_volume_input(1)
        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[{"chapter": 1, "summary": "第一章"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, VolumeAuditReport)

        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "world_rules" in builder.last_context
        assert "promise_ledger" in builder.last_context

    async def test_volume_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = VolumeAuditStep(
            _MockRouter(json_payload=_VOLUME_AUDIT_PAYLOAD, completion_tokens=200),
            builder,
            settings=Settings(),
        )

        kernel_ctx = composer.compose_volume_input(1)
        input_data = VolumeAuditInput(
            volume=VolumeOutline(volume_number=1, title="第一卷", start_chapter=1, end_chapter=10),
            story_synopsis="故事梗概",
            chapter_summaries=[],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        entities = builder.last_context["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)
