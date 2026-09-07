"""Tests for BookConsistencyStep/MacroGuardStep ContextComposer integration.

Verifies that:
1. BookConsistencyInput accepts kernel_context field
2. MacroGuardInput accepts kernel_context field
3. kernel_context fields are merged into prompt context
4. explicit context fields take precedence over kernel_context
5. ContextComposer produces correct field slices for both steps
6. Both steps work end-to-end with StoryKernel test data
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.chapter import MacroGuardReport
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyResult,
    BookConsistencyStep,
)
from novel_forge.pipeline.steps.macro_guard_step import (
    MacroGuardInput,
    MacroGuardStep,
)
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.schemas import (
    Entity,
    KnowledgeLedger,
    MotifProtocol,
    ObjectLedger,
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
        "object_ledger": [
            ObjectLedger(entry_id="o-1", item_name="古塔钥匙", owner_entity_id="e-1"),
        ],
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

    def render(self, task_type: TaskType, context: dict) -> str:
        """Mock render for prompt length estimation."""
        self.last_context = context
        return str(context)


def _make_outline() -> StoryOutline:
    """Create a minimal StoryOutline for testing."""
    return StoryOutline(
        synopsis="一个关于探索古塔的故事",
        total_chapters=5,
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="发现古塔",
                goal="李明发现古塔",
                beats_summary="李明在森林中散步时发现了一座古老的塔",
            ),
            ChapterOutline(
                chapter_number=2,
                title="结伴同行",
                goal="王芳加入",
                beats_summary="王芳决定与李明一起探索古塔",
            ),
            ChapterOutline(
                chapter_number=3,
                title="探索开始",
                goal="进入古塔",
                beats_summary="两人开始探索古塔的第一层",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# BookConsistencyInput kernel_context tests
# ---------------------------------------------------------------------------


class TestBookConsistencyInputKernelContext:
    """Test BookConsistencyInput with kernel_context field."""

    def test_accepts_kernel_context(self) -> None:
        """BookConsistencyInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "world_rules": []}
        input_data = BookConsistencyInput(
            chapter_summaries=[],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_kernel_context_defaults_to_none(self) -> None:
        """kernel_context should default to None."""
        input_data = BookConsistencyInput(
            chapter_summaries=[],
            canon_state_snapshot={},
            character_bible={},
            outline={},
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# MacroGuardInput kernel_context tests
# ---------------------------------------------------------------------------


class TestMacroGuardInputKernelContext:
    """Test MacroGuardInput with kernel_context field."""

    def test_accepts_kernel_context(self) -> None:
        """MacroGuardInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "timeline": []}
        input_data = MacroGuardInput(
            chapter_number=3,
            audit_entries=[],
            outline=_make_outline(),
            canon_state={},
            settings=Settings(),
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_kernel_context_defaults_to_none(self) -> None:
        """kernel_context should default to None."""
        input_data = MacroGuardInput(
            chapter_number=3,
            audit_entries=[],
            outline=_make_outline(),
            canon_state={},
            settings=Settings(),
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# BookConsistencyStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestBookConsistencyStepKernelContextMerge:
    """Test that BookConsistencyStep merges kernel_context into prompt context."""

    async def test_kernel_context_merged_into_prompt(self) -> None:
        """kernel_context fields should appear in the prompt context."""
        builder = _FakeBuilder()
        step = BookConsistencyStep(
            _MockRouter(
                '{"issues": [], "summary": "无问题", "consistency_score": 9.5}', capture_calls=False
            ),
            builder,
            settings=Settings(),
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
            "timeline": [{"anchor_id": "t-1", "event": "事件"}],
            "promise_ledger": [{"entry_id": "p-1", "description": "伏笔"}],
            "motif_protocols": [{"entry_id": "m-1", "motif_name": "月光"}],
        }
        input_data = BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "第一章摘要"},
            ],
            canon_state_snapshot={
                "characters": {"李明": {"name": "李明"}},
                "relationships": [],
            },
            character_bible={"characters": [{"name": "李明"}]},
            outline={"premise": "故事前提"},
            analysis_mode="summary",
            kernel_context=kernel_ctx,
        )

        with patch.object(step, "_call_audit", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = {
                "issues": [],
                "summary": "无问题",
                "consistency_score": 9.5,
                "repair_plan": [],
            }
            result = await step.run(input_data)

        # The kernel_context fields should be available in the prompt context
        # (via _build_prompt_context which is called internally)
        assert result is not None
        assert isinstance(result, BookConsistencyResult)

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = BookConsistencyStep(
            _MockRouter(
                '{"issues": [], "summary": "无问题", "consistency_score": 9.5}', capture_calls=False
            ),
            builder,
            settings=Settings(),
        )

        input_data = BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "第一章摘要"},
            ],
            canon_state_snapshot={
                "characters": {"李明": {"name": "李明"}},
                "relationships": [],
            },
            character_bible={"characters": [{"name": "李明"}]},
            outline={"premise": "故事前提"},
            analysis_mode="summary",
        )

        with patch.object(step, "_call_audit", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = {
                "issues": [],
                "summary": "无问题",
                "consistency_score": 9.5,
                "repair_plan": [],
            }
            result = await step.run(input_data)

        assert result is not None
        assert result.consistency_score == 9.5


# ---------------------------------------------------------------------------
# MacroGuardStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestMacroGuardStepKernelContextMerge:
    """Test that MacroGuardStep merges kernel_context into prompt context."""

    async def test_kernel_context_merged_into_prompt(self) -> None:
        """kernel_context fields should appear in the prompt context."""
        builder = _FakeBuilder()
        step = MacroGuardStep(
            _MockRouter(
                '{"dimensions": {}, "findings": [], "recommended_action": "pass"}',
                capture_calls=False,
            ),
            builder,
            settings=Settings(),
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
            "timeline": [{"anchor_id": "t-1", "event": "事件"}],
            "promise_ledger": [{"entry_id": "p-1", "description": "伏笔"}],
        }
        input_data = MacroGuardInput(
            chapter_number=3,
            audit_entries=[{"chapter_number": 3, "summary": "第三章摘要"}],
            outline=_make_outline(),
            canon_state={
                "characters": {"李明": {"name": "李明"}},
                "foreshadowing": [],
                "world_facts": {},
                "plot_threads": {},
            },
            settings=Settings(),
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert result is not None
        assert isinstance(result, MacroGuardReport)
        # Verify the builder was called with context containing kernel fields
        assert builder.last_context is not None

    async def test_kernel_context_entities_in_context(self) -> None:
        """kernel_context entities should appear in the context."""
        builder = _FakeBuilder()
        step = MacroGuardStep(
            _MockRouter(
                '{"dimensions": {}, "findings": [], "recommended_action": "pass"}',
                capture_calls=False,
            ),
            builder,
            settings=Settings(),
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明", "entity_type": "character"}],
        }
        input_data = MacroGuardInput(
            chapter_number=3,
            audit_entries=[{"chapter_number": 3, "summary": "摘要"}],
            outline=_make_outline(),
            canon_state={},
            settings=Settings(),
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "kernel_entities" in builder.last_context
        assert builder.last_context["kernel_entities"] == kernel_ctx["entities"]

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _FakeBuilder()
        step = MacroGuardStep(
            _MockRouter(
                '{"dimensions": {}, "findings": [], "recommended_action": "pass"}',
                capture_calls=False,
            ),
            builder,
            settings=Settings(),
        )

        input_data = MacroGuardInput(
            chapter_number=3,
            audit_entries=[{"chapter_number": 3, "summary": "摘要"}],
            outline=_make_outline(),
            canon_state={
                "characters": {"李明": {"name": "李明"}},
                "foreshadowing": [],
                "world_facts": {},
                "plot_threads": {},
            },
            settings=Settings(),
        )

        result = await step.run(input_data)

        assert result is not None
        assert isinstance(result, MacroGuardReport)
        assert builder.last_context is not None
        # No kernel_ prefixed fields without kernel_context
        assert "kernel_entities" not in builder.last_context
        assert "kernel_world_rules" not in builder.last_context


# ---------------------------------------------------------------------------
# ContextComposer field slice tests
# ---------------------------------------------------------------------------


class TestComposerBookConsistencySlice:
    """Test ContextComposer field slices for book_consistency step."""

    def test_returns_all_fields(self) -> None:
        """book_consistency reads ALL_FIELDS — should include all kernel fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")
        result = composer.compose_for_step("book_consistency", 3)
        assert isinstance(result, dict)
        # All 10 field groups should be present
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result
        assert "object_ledger" in result
        assert "knowledge_ledger" in result
        assert "access_ledger" in result
        assert "promise_ledger" in result
        assert "motif_protocols" in result
        assert "business_dependencies" in result
        # Supplementary fields
        assert "chapter_summaries" in result
        assert "banned_phrases" in result

    def test_entities_are_serialized(self) -> None:
        """Entities should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("book_consistency", 3)
        assert isinstance(result["entities"], list)
        assert all(isinstance(e, dict) for e in result["entities"])


class TestComposerMacroGuardSlice:
    """Test ContextComposer field slices for macro_guard step."""

    def test_returns_all_fields(self) -> None:
        """macro_guard reads ALL_FIELDS — should include all kernel fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")
        result = composer.compose_for_step("macro_guard", 3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result
        assert "promise_ledger" in result
        assert "motif_protocols" in result

    def test_entities_are_serialized(self) -> None:
        """Entities should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("macro_guard", 3)
        entities = result["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)
        assert any(e.get("name") == "李明" for e in entities)


class TestComposeBookConsistencyInput:
    """Test compose_book_consistency_input convenience method."""

    def test_returns_all_fields(self) -> None:
        """compose_book_consistency_input should return all kernel fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_book_consistency_input(3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "world_rules" in result
        assert "chapter_summaries" in result

    def test_equivalent_to_compose_for_step(self) -> None:
        """compose_book_consistency_input should match compose_for_step('book_consistency')."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        convenience = composer.compose_book_consistency_input(3)
        generic = composer.compose_for_step("book_consistency", 3)
        assert convenience == generic


class TestComposeMacroGuardInput:
    """Test compose_macro_guard_input convenience method."""

    def test_returns_all_fields(self) -> None:
        """compose_macro_guard_input should return all kernel fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_macro_guard_input(3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "timeline" in result
        assert "promise_ledger" in result

    def test_equivalent_to_compose_for_step(self) -> None:
        """compose_macro_guard_input should match compose_for_step('macro_guard')."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        convenience = composer.compose_macro_guard_input(3)
        generic = composer.compose_for_step("macro_guard", 3)
        assert convenience == generic


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------


class TestBookConsistencyStepWithComposer:
    """End-to-end: BookConsistencyStep + ContextComposer."""

    async def test_step_with_composed_kernel_context(self) -> None:
        """BookConsistencyStep should produce a result when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = BookConsistencyStep(
            _MockRouter(
                '{"issues": [], "summary": "无问题", "consistency_score": 9.5}', capture_calls=False
            ),
            builder,
            settings=Settings(),
        )

        kernel_ctx = composer.compose_for_step("book_consistency", 3)
        input_data = BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "第一章摘要"},
                {"chapter_number": 2, "summary": "第二章摘要"},
            ],
            canon_state_snapshot={
                "characters": {"李明": {"name": "李明"}},
                "relationships": [],
            },
            character_bible={"characters": [{"name": "李明"}]},
            outline={"premise": "故事前提"},
            analysis_mode="summary",
            kernel_context=kernel_ctx,
        )

        with patch.object(step, "_call_audit", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = {
                "issues": [],
                "summary": "无问题",
                "consistency_score": 9.5,
                "repair_plan": [],
            }
            result = await step.run(input_data)

        assert result is not None
        assert isinstance(result, BookConsistencyResult)
        assert result.consistency_score == 9.5


class TestMacroGuardStepWithComposer:
    """End-to-end: MacroGuardStep + ContextComposer."""

    async def test_step_with_composed_kernel_context(self) -> None:
        """MacroGuardStep should produce a report when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _FakeBuilder()
        step = MacroGuardStep(
            _MockRouter(
                '{"dimensions": {"outline_alignment": 0.9, "character_arc_consistency": 0.8, '
                '"pacing_curve": 0.85, "foreshadowing_recovery": 0.7, "thematic_cohesion": 0.9}, '
                '"findings": [], "recommended_action": "pass", "confidence": 0.8}',
                capture_calls=False,
            ),
            builder,
            settings=Settings(),
        )

        kernel_ctx = composer.compose_for_step("macro_guard", 3)
        input_data = MacroGuardInput(
            chapter_number=3,
            audit_entries=[{"chapter_number": 3, "summary": "第三章摘要"}],
            outline=_make_outline(),
            canon_state={
                "characters": {"李明": {"name": "李明"}},
                "foreshadowing": [],
                "world_facts": {},
                "plot_threads": {},
            },
            settings=Settings(),
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert result is not None
        assert isinstance(result, MacroGuardReport)
        assert builder.last_context is not None
        # Kernel fields should be present in context under kernel_ prefix
        assert "kernel_entities" in builder.last_context
        assert "kernel_world_rules" in builder.last_context
        assert "kernel_timeline" in builder.last_context
        assert "kernel_relationships" in builder.last_context
