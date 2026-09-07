"""Tests for CausalValidationStep and CausalRepairStep kernel_context integration.

TDD: These tests define the expected behavior of both steps consuming
StoryKernel field slices via ContextComposer before implementation.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.continuity import ChapterBridge
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairInput
from novel_forge.pipeline.steps.causal_validation_step import (
    CausalValidationInput,
    CausalValidationStep,
)
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.contracts import (
    CAUSAL_REPAIR_CONTRACT,
    CAUSAL_VALIDATE_CONTRACT,
)
from novel_forge.story_kernel.schemas import (
    BusinessDependency,
    Entity,
    KnowledgeLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kernel(**overrides: Any) -> StoryKernel:
    """Create a minimal StoryKernel with causal-relevant sample data."""
    defaults: dict[str, Any] = {
        "project_id": "test-project",
        "current_chapter": 3,
        "active_volume": 1,
        "title": "测试小说",
        "premise": "一个关于因果链的故事",
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
                event="王芳决定与李明一起探索古塔",
                characters_involved=["e-1", "e-2"],
            ),
        ],
        "knowledge_ledger": [
            KnowledgeLedger(
                entry_id="k-1",
                entity_id="e-1",
                fact="古塔隐藏着秘密",
                knowledge_type="known",
            ),
        ],
        "promise_ledger": [
            PromiseLedger(
                entry_id="p-1",
                description="古塔的秘密终将揭开",
                promise_type="foreshadow",
                planted_chapter=1,
                status="planted",
            ),
        ],
        "business_dependencies": [
            BusinessDependency(
                dependency_id="bd-1",
                source_id="e-1",
                target_id="p-1",
                dependency_type="triggers",
                description="李明的发现触发了古塔秘密的伏笔",
            ),
        ],
    }
    defaults.update(overrides)
    return StoryKernel(**defaults)


class _MockStore:
    """Mock StoryKernelStore for testing."""

    def __init__(self, kernel: StoryKernel) -> None:
        self._kernel = kernel

    def load_kernel(self, project_id: str) -> StoryKernel:
        return self._kernel


def _make_bridge() -> ChapterBridge:
    """Create a minimal ChapterBridge for testing."""
    return ChapterBridge(
        from_chapter=2,
        to_chapter=3,
        bridge_summary="上章结尾停在门口",
        emotional_carryover="紧张",
        action_handoff="推门",
        opening_location="档案室",
        opening_time="深夜",
    )


def _make_kernel_context(kernel: StoryKernel) -> dict[str, Any]:
    """Compose kernel context for causal_validate step."""
    store = _MockStore(kernel)
    composer = ContextComposer(store, project_id=kernel.project_id)
    return composer.compose_causal_validate_input(kernel.current_chapter)


# ---------------------------------------------------------------------------
# ContextComposer convenience methods
# ---------------------------------------------------------------------------


class TestComposerCausalConvenience:
    """Test compose_causal_validate_input and compose_causal_repair_input."""

    def test_causal_validate_reads_correct_fields(self) -> None:
        """compose_causal_validate_input should return CAUSAL_VALIDATE_CONTRACT.reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_causal_validate_input(3)
        assert isinstance(result, dict)
        for key in result:
            assert key in CAUSAL_VALIDATE_CONTRACT.reads, (
                f"Unexpected key '{key}' in causal_validate result"
            )

    def test_causal_validate_contains_expected_fields(self) -> None:
        """causal_validate should include entities, relationships, timeline, etc."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_causal_validate_input(3)
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "knowledge_ledger" in result
        assert "business_dependencies" in result
        assert "promise_ledger" in result

    def test_causal_validate_excludes_unread_fields(self) -> None:
        """causal_validate should NOT include fields not in its contract."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_causal_validate_input(3)
        assert "world_rules" not in result
        assert "object_ledger" not in result
        assert "access_ledger" not in result
        assert "motif_protocols" not in result
        assert "chapter_summaries" not in result

    def test_causal_repair_reads_correct_fields(self) -> None:
        """compose_causal_repair_input should return CAUSAL_REPAIR_CONTRACT.reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_causal_repair_input(3)
        assert isinstance(result, dict)
        for key in result:
            assert key in CAUSAL_REPAIR_CONTRACT.reads, (
                f"Unexpected key '{key}' in causal_repair result"
            )

    def test_causal_repair_same_fields_as_validate(self) -> None:
        """causal_repair and causal_validate should read the same fields."""
        assert CAUSAL_VALIDATE_CONTRACT.reads == CAUSAL_REPAIR_CONTRACT.reads

    def test_entities_serialized_as_list_of_dicts(self) -> None:
        """Entities in kernel context should be list[dict]."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        assert isinstance(ctx["entities"], list)
        assert len(ctx["entities"]) == 3
        assert isinstance(ctx["entities"][0], dict)
        assert "name" in ctx["entities"][0]

    def test_business_dependencies_serialized(self) -> None:
        """Business dependencies should be serialized as list[dict]."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        assert isinstance(ctx["business_dependencies"], list)
        assert len(ctx["business_dependencies"]) == 1
        assert isinstance(ctx["business_dependencies"][0], dict)
        assert "description" in ctx["business_dependencies"][0]


# ---------------------------------------------------------------------------
# CausalValidationInput — kernel_context field
# ---------------------------------------------------------------------------


class TestCausalValidationInputKernelContext:
    """Test CausalValidationInput accepts kernel_context."""

    def test_accepts_kernel_context(self) -> None:
        """CausalValidationInput should accept an optional kernel_context dict."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
            kernel_context=ctx,
        )
        assert inp.kernel_context == ctx

    def test_kernel_context_defaults_to_empty(self) -> None:
        """kernel_context should default to empty dict when not provided."""
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
        )
        assert inp.kernel_context == {}

    def test_kernel_context_backward_compatible(self) -> None:
        """Existing code without kernel_context should still work."""
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
        )
        # Should not raise
        context = CausalValidationStep._build_llm_context(inp)
        assert "chapter_number" in context


# ---------------------------------------------------------------------------
# CausalValidationStep._build_llm_context — kernel_context injection
# ---------------------------------------------------------------------------


class TestCausalValidationBuildContext:
    """Test that _build_llm_context includes kernel_context fields."""

    def test_kernel_context_included_in_llm_context(self) -> None:
        """_build_llm_context should include kernel_context fields."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
            kernel_context=ctx,
        )
        context = CausalValidationStep._build_llm_context(inp)
        assert "kernel_context" in context
        assert context["kernel_context"] == ctx

    def test_kernel_context_has_entities(self) -> None:
        """kernel_context in LLM context should contain entities."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
            kernel_context=ctx,
        )
        context = CausalValidationStep._build_llm_context(inp)
        kc = context["kernel_context"]
        assert "entities" in kc
        assert len(kc["entities"]) == 3

    def test_kernel_context_has_business_dependencies(self) -> None:
        """kernel_context in LLM context should contain business_dependencies."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
            kernel_context=ctx,
        )
        context = CausalValidationStep._build_llm_context(inp)
        kc = context["kernel_context"]
        assert "business_dependencies" in kc
        assert len(kc["business_dependencies"]) == 1

    def test_empty_kernel_context_omitted(self) -> None:
        """When kernel_context is empty, it should not appear in LLM context."""
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
        )
        context = CausalValidationStep._build_llm_context(inp)
        assert context.get("kernel_context") == {}

    def test_existing_context_fields_preserved(self) -> None:
        """All existing context fields should remain unchanged."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
            kernel_context=ctx,
        )
        context = CausalValidationStep._build_llm_context(inp)
        # Verify all original fields are still present
        assert "chapter_number" in context
        assert "chapter_text" in context
        assert "numbered_chapter_text" in context
        assert "chapter_bridge" in context
        assert "causal_link" in context
        assert "must_resolve_summaries" in context
        assert "character_notes" in context
        assert "previous_chapter_ending" in context
        assert "recheck_mode" in context
        assert "prior_issues" in context


# ---------------------------------------------------------------------------
# CausalRepairInput — kernel_context field
# ---------------------------------------------------------------------------


class TestCausalRepairInputKernelContext:
    """Test CausalRepairInput accepts kernel_context."""

    def test_accepts_kernel_context(self) -> None:
        """CausalRepairInput should accept an optional kernel_context dict."""
        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)
        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=None,  # type: ignore[arg-type]
            kernel_context=ctx,
        )
        assert inp.kernel_context == ctx

    def test_kernel_context_defaults_to_empty(self) -> None:
        """kernel_context should default to empty dict when not provided."""
        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=None,  # type: ignore[arg-type]
        )
        assert inp.kernel_context == {}

    def test_kernel_context_backward_compatible(self) -> None:
        """Existing code without kernel_context should still work."""
        from novel_forge.core.schemas.chapter import CausalValidationReport

        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=CausalValidationReport(
                causal_score=10.0,
                summary="无问题",
                issues=[],
                causal_link_verified=True,
            ),
        )
        # kernel_context should be empty dict
        assert inp.kernel_context == {}


# ---------------------------------------------------------------------------
# CausalRepairStep.build_repair_context — kernel_context injection
# ---------------------------------------------------------------------------


class TestCausalRepairBuildContext:
    """Test that build_repair_context includes kernel_context fields."""

    def test_kernel_context_included_in_repair_context(self) -> None:
        """build_repair_context should include kernel_context."""
        from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep
        from novel_forge.prompts.builder import PromptBuilder

        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)

        # Create a mock step (we only need build_repair_context, not _execute)
        from unittest.mock import MagicMock

        mock_router = MagicMock(spec=ModelRouter)
        mock_builder = MagicMock(spec=PromptBuilder)
        mock_settings = MagicMock()

        step = CausalRepairStep(
            mock_router,
            mock_builder,
            settings=mock_settings,
        )

        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=CausalValidationReport(
                causal_score=7.0,
                summary="发现问题",
                issues=[
                    CausalIssue(
                        issue_type="event_without_cause",
                        severity="high",
                        location="第2段",
                        summary="事件缺乏因果铺垫",
                        paragraph_start=2,
                        paragraph_end=2,
                    ),
                ],
                causal_link_verified=False,
            ),
            kernel_context=ctx,
        )

        issues = list(inp.causal_report.issues)
        context = step.build_repair_context(inp, issues=issues)

        assert "kernel_context" in context
        assert context["kernel_context"] == ctx

    def test_kernel_context_has_entities_in_repair(self) -> None:
        """kernel_context in repair context should contain entities."""
        from unittest.mock import MagicMock

        from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep
        from novel_forge.prompts.builder import PromptBuilder

        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)

        mock_router = MagicMock(spec=ModelRouter)
        mock_builder = MagicMock(spec=PromptBuilder)
        mock_settings = MagicMock()

        step = CausalRepairStep(mock_router, mock_builder, settings=mock_settings)

        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=CausalValidationReport(
                causal_score=7.0,
                summary="发现问题",
                issues=[
                    CausalIssue(
                        issue_type="event_without_cause",
                        severity="high",
                        location="第2段",
                        summary="事件缺乏因果铺垫",
                        paragraph_start=2,
                        paragraph_end=2,
                    ),
                ],
                causal_link_verified=False,
            ),
            kernel_context=ctx,
        )

        context = step.build_repair_context(inp, issues=list(inp.causal_report.issues))
        kc = context["kernel_context"]
        assert "entities" in kc
        assert len(kc["entities"]) == 3

    def test_empty_kernel_context_in_repair(self) -> None:
        """When kernel_context is empty, repair context should still work."""
        from unittest.mock import MagicMock

        from novel_forge.core.schemas.chapter import CausalValidationReport
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep
        from novel_forge.prompts.builder import PromptBuilder

        mock_router = MagicMock(spec=ModelRouter)
        mock_builder = MagicMock(spec=PromptBuilder)
        mock_settings = MagicMock()

        step = CausalRepairStep(mock_router, mock_builder, settings=mock_settings)

        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=CausalValidationReport(
                causal_score=10.0,
                summary="无问题",
                issues=[],
                causal_link_verified=True,
            ),
        )

        context = step.build_repair_context(inp, issues=[])
        assert context.get("kernel_context") == {}

    def test_existing_repair_context_fields_preserved(self) -> None:
        """All existing repair context fields should remain unchanged."""
        from unittest.mock import MagicMock

        from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep
        from novel_forge.prompts.builder import PromptBuilder

        kernel = _make_kernel()
        ctx = _make_kernel_context(kernel)

        mock_router = MagicMock(spec=ModelRouter)
        mock_builder = MagicMock(spec=PromptBuilder)
        mock_settings = MagicMock()

        step = CausalRepairStep(mock_router, mock_builder, settings=mock_settings)

        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=CausalValidationReport(
                causal_score=7.0,
                summary="发现问题",
                issues=[
                    CausalIssue(
                        issue_type="event_without_cause",
                        severity="high",
                        location="第2段",
                        summary="事件缺乏因果铺垫",
                        paragraph_start=2,
                        paragraph_end=2,
                    ),
                ],
                causal_link_verified=False,
            ),
            kernel_context=ctx,
        )

        context = step.build_repair_context(inp, issues=list(inp.causal_report.issues))
        # Verify all original fields are still present
        assert "chapter_number" in context
        assert "chapter_text" in context
        assert "numbered_chapter_text" in context
        assert "issue_types" in context
        assert "typed_issues" in context
        assert "causal_link" in context
        assert "word_count_min" in context
        assert "word_count_max" in context


# ---------------------------------------------------------------------------
# End-to-end: ContextComposer → Step input
# ---------------------------------------------------------------------------


class TestEndToEndComposerToStep:
    """Test the full flow: StoryKernel → ContextComposer → Step input."""

    def test_validation_flow(self) -> None:
        """Full flow: kernel → composer → CausalValidationInput → LLM context."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        kernel_context = composer.compose_causal_validate_input(3)

        inp = CausalValidationInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            chapter_bridge=_make_bridge(),
            causal_link={"previous_event": "发现密信"},
            kernel_context=kernel_context,
        )

        llm_context = CausalValidationStep._build_llm_context(inp)

        # Verify kernel context is in the LLM context
        assert "kernel_context" in llm_context
        kc = llm_context["kernel_context"]
        assert "entities" in kc
        assert "relationships" in kc
        assert "timeline" in kc
        assert "knowledge_ledger" in kc
        assert "business_dependencies" in kc
        assert "promise_ledger" in kc

    def test_repair_flow(self) -> None:
        """Full flow: kernel → composer → CausalRepairInput → repair context."""
        from unittest.mock import MagicMock

        from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
        from novel_forge.gateway.router import ModelRouter
        from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep
        from novel_forge.prompts.builder import PromptBuilder

        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        kernel_context = composer.compose_causal_repair_input(3)

        inp = CausalRepairInput(
            chapter_number=3,
            chapter_text="第一段。\n\n第二段。",
            causal_link={"previous_event": "发现密信"},
            chapter_bridge=_make_bridge(),
            causal_report=CausalValidationReport(
                causal_score=7.0,
                summary="发现问题",
                issues=[
                    CausalIssue(
                        issue_type="event_without_cause",
                        severity="high",
                        location="第2段",
                        summary="事件缺乏因果铺垫",
                        paragraph_start=2,
                        paragraph_end=2,
                    ),
                ],
                causal_link_verified=False,
            ),
            kernel_context=kernel_context,
        )

        mock_router = MagicMock(spec=ModelRouter)
        mock_builder = MagicMock(spec=PromptBuilder)
        mock_settings = MagicMock()

        step = CausalRepairStep(mock_router, mock_builder, settings=mock_settings)
        repair_context = step.build_repair_context(inp, issues=list(inp.causal_report.issues))

        assert "kernel_context" in repair_context
        kc = repair_context["kernel_context"]
        assert "entities" in kc
        assert "relationships" in kc
        assert "timeline" in kc
        assert "knowledge_ledger" in kc
        assert "business_dependencies" in kc
        assert "promise_ledger" in kc

    def test_both_steps_receive_same_kernel_fields(self) -> None:
        """Both steps should receive the same kernel fields (same contract reads)."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        validate_ctx = composer.compose_causal_validate_input(3)
        repair_ctx = composer.compose_causal_repair_input(3)

        # Both should have the same keys
        assert set(validate_ctx.keys()) == set(repair_ctx.keys())
