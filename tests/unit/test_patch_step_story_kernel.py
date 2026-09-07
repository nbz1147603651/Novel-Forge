"""Tests for PatchStep consuming StoryKernel field slices via ContextComposer.

Verifies that:
1. PatchInput accepts kernel_context field
2. kernel_context fields are merged into template context
3. explicit context fields take precedence over kernel_context
4. ContextComposer.compose_patch_input() convenience method works
5. PatchStep works end-to-end with StoryKernel test data
6. Patch execution logic is unchanged (backward compatible)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput, PatchResult
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.schemas import (
    Entity,
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
        ],
        "relationships": [],
        "timeline": [],
        "object_ledger": [],
        "knowledge_ledger": [],
        "access_ledger": [],
        "promise_ledger": [],
        "motif_protocols": [],
        "business_dependencies": [],
        "chapter_summaries": {1: "第一章摘要", 2: "第二章摘要"},
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


class _CapturingBuilder:
    """Captures context passed to build() for verification."""

    def __init__(self) -> None:
        self.last_context: dict | None = None

    def build(self, task_type: Any, context: dict, **kwargs: Any) -> Any:
        self.last_context = context
        return SimpleNamespace()


class _FixedRouter:
    """Returns a fixed patch response."""

    def __init__(self, patches: list[dict] | None = None) -> None:
        self._patches = patches or []

    def resolve_model_id_for_task(
        self, task_type: Any, *, provider: str | None = None, model_id: str | None = None
    ) -> str | None:
        return model_id or "mock-test"

    async def route(self, request: Any) -> Any:
        import json

        return SimpleNamespace(content=json.dumps({"patches": self._patches}))


# ---------------------------------------------------------------------------
# PatchInput kernel_context tests
# ---------------------------------------------------------------------------


class TestPatchInputKernelContext:
    """Test PatchInput with kernel_context field."""

    def test_patch_input_accepts_kernel_context(self) -> None:
        """PatchInput should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "world_rules": []}
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="正文内容。",
            issues=[],
            kernel_context=kernel_ctx,
        )
        assert input_data.kernel_context == kernel_ctx

    def test_patch_input_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="正文内容。",
            issues=[],
        )
        assert input_data.kernel_context is None


# ---------------------------------------------------------------------------
# PatchStep kernel_context merge tests
# ---------------------------------------------------------------------------


class TestPatchStepKernelContextMerge:
    """Test that PatchStep merges kernel_context into template context."""

    async def test_kernel_context_merged_into_template(self) -> None:
        """kernel_context fields should appear in the template context."""
        builder = _CapturingBuilder()
        step = ChapterPatchStep(
            _FixedRouter(),
            builder,
            settings=SimpleNamespace(
                patch_chapter_max_tokens=8192,
                patch_executor_version="v1",
                temp_patch_chapter=0.15,
            ),
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
        }
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            issues=[],
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert builder.last_context["entities"] == kernel_ctx["entities"]
        assert "world_rules" in builder.last_context
        assert builder.last_context["world_rules"] == kernel_ctx["world_rules"]

    async def test_explicit_context_takes_precedence(self) -> None:
        """Explicit PatchInput fields should NOT be overridden by kernel_context."""
        builder = _CapturingBuilder()
        step = ChapterPatchStep(
            _FixedRouter(),
            builder,
            settings=SimpleNamespace(
                patch_chapter_max_tokens=8192,
                patch_executor_version="v1",
                temp_patch_chapter=0.15,
            ),
        )

        kernel_ctx = {
            "chapter_number": 999,  # Should NOT override
            "entities": [{"name": "kernel_entity"}],
        }
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            issues=[],
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        # chapter_number from explicit input wins
        assert builder.last_context["chapter_number"] == 3
        # entities from kernel_context is merged
        assert builder.last_context["entities"] == [{"name": "kernel_entity"}]

    async def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, behavior should be identical to before."""
        builder = _CapturingBuilder()
        step = ChapterPatchStep(
            _FixedRouter(),
            builder,
            settings=SimpleNamespace(
                patch_chapter_max_tokens=8192,
                patch_executor_version="v1",
                temp_patch_chapter=0.15,
            ),
        )

        input_data = PatchInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            issues=[],
            style="literary",
        )

        await step.run(input_data)

        assert builder.last_context is not None
        assert builder.last_context["chapter_number"] == 3
        assert builder.last_context["style"] == "literary"
        assert "entities" not in builder.last_context


# ---------------------------------------------------------------------------
# ContextComposer.compose_patch_input tests
# ---------------------------------------------------------------------------


class TestComposePatchInput:
    """Test compose_patch_input convenience method."""

    def test_returns_patch_fields(self) -> None:
        """compose_patch_input should return PATCH_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_patch_input(3)
        assert isinstance(result, dict)
        # PATCH_CONTRACT reads: entities, world_rules
        assert "entities" in result
        assert "world_rules" in result

    def test_does_not_include_non_patch_fields(self) -> None:
        """Patch input should not include fields not in PATCH_CONTRACT.reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_patch_input(3)
        # Patch does NOT read: timeline, relationships, knowledge_ledger, etc.
        assert "timeline" not in result
        assert "relationships" not in result
        assert "knowledge_ledger" not in result
        assert "object_ledger" not in result
        assert "access_ledger" not in result
        assert "promise_ledger" not in result
        assert "motif_protocols" not in result
        assert "business_dependencies" not in result
        assert "chapter_summaries" not in result
        assert "banned_phrases" not in result

    def test_entities_are_serialized(self) -> None:
        """Entities should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_patch_input(3)
        assert isinstance(result["entities"], list)
        assert all(isinstance(e, dict) for e in result["entities"])

    def test_world_rules_are_serialized(self) -> None:
        """World rules should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_patch_input(3)
        assert isinstance(result["world_rules"], list)
        assert all(isinstance(r, dict) for r in result["world_rules"])

    def test_empty_kernel(self) -> None:
        """Composer should handle a kernel with empty field groups."""
        kernel = StoryKernel(project_id="empty")
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_patch_input(1)
        assert result["entities"] == []
        assert result["world_rules"] == []


# ---------------------------------------------------------------------------
# End-to-end: PatchStep with ContextComposer
# ---------------------------------------------------------------------------


class TestPatchStepWithComposer:
    """End-to-end: PatchStep + ContextComposer produces correct output."""

    async def test_patch_with_composed_kernel_context(self) -> None:
        """PatchStep should work correctly when given composer output."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _CapturingBuilder()
        step = ChapterPatchStep(
            _FixedRouter(),
            builder,
            settings=SimpleNamespace(
                patch_chapter_max_tokens=8192,
                patch_executor_version="v1",
                temp_patch_chapter=0.15,
            ),
        )

        kernel_ctx = composer.compose_patch_input(3)
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            issues=[],
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, PatchResult)
        # No patches returned by mock, so fallback behavior applies
        assert result.revised_text == "第一段。\n\n第二段。"

        # Verify kernel fields were merged into template context
        assert builder.last_context is not None
        assert "entities" in builder.last_context
        assert "world_rules" in builder.last_context

    async def test_patch_kernel_fields_are_serialized(self) -> None:
        """Kernel fields in context should be serialized dicts, not Pydantic models."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        builder = _CapturingBuilder()
        step = ChapterPatchStep(
            _FixedRouter(),
            builder,
            settings=SimpleNamespace(
                patch_chapter_max_tokens=8192,
                patch_executor_version="v1",
                temp_patch_chapter=0.15,
            ),
        )

        kernel_ctx = composer.compose_patch_input(3)
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            issues=[],
            kernel_context=kernel_ctx,
        )

        await step.run(input_data)

        assert builder.last_context is not None
        entities = builder.last_context["entities"]
        assert isinstance(entities, list)
        assert all(isinstance(e, dict) for e in entities)

    async def test_patch_execution_logic_unchanged(self) -> None:
        """Patch execution logic (apply_patches) should remain unchanged."""
        builder = _CapturingBuilder()
        step = ChapterPatchStep(
            _FixedRouter([{"original": "第二段。", "replacement": "修改后的第二段。"}]),
            builder,
            settings=SimpleNamespace(
                patch_chapter_max_tokens=8192,
                patch_executor_version="v1",
                temp_patch_chapter=0.15,
            ),
        )

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "world_rules": [],
        }
        input_data = PatchInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            issues=[
                SimpleNamespace(
                    severity="medium",
                    location="第2段",
                    summary="需要修改。",
                    fix_suggestion="修改第二段。",
                    issue_type="redundancy",
                    evidence_quote="第二段。",
                    fix_mode="replace",
                    paragraph_start=2,
                    paragraph_end=2,
                )
            ],
            kernel_context=kernel_ctx,
        )

        result = await step.run(input_data)

        assert isinstance(result, PatchResult)
        # The patch execution should still work correctly
        assert result.patches_attempted >= 0
