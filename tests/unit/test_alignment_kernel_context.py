"""Tests for AlignmentStep consuming StoryKernel field slices via ContextComposer.

TDD: These tests define the expected behavior before implementation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.pipeline.steps.alignment_step import AlignmentInput, AlignmentStep

# ---------------------------------------------------------------------------
# AlignmentInput — kernel_context field
# ---------------------------------------------------------------------------


class TestAlignmentInputKernelContext:
    """Test that AlignmentInput accepts optional kernel_context."""

    def test_kernel_context_defaults_to_none(self) -> None:
        """AlignmentInput should have kernel_context=None by default."""
        input_data = AlignmentInput(
            chapter_outline=SimpleNamespace(
                chapter_number=1,
                title="测试",
                goal="目标",
                main_plot_points=[],
                subplot_points=[],
                beats_summary=[],
            ),
            chapter_plan=SimpleNamespace(scene_intents=[]),
            chapter_text="正文",
        )
        assert input_data.kernel_context is None

    def test_kernel_context_can_be_set(self) -> None:
        """AlignmentInput should accept a kernel_context dict."""
        ctx = {"entities": [], "relationships": [], "timeline": []}
        input_data = AlignmentInput(
            chapter_outline=SimpleNamespace(
                chapter_number=1,
                title="测试",
                goal="目标",
                main_plot_points=[],
                subplot_points=[],
                beats_summary=[],
            ),
            chapter_plan=SimpleNamespace(scene_intents=[]),
            chapter_text="正文",
            kernel_context=ctx,
        )
        assert input_data.kernel_context is ctx

    def test_kernel_context_fields_match_alignment_contract(self) -> None:
        """kernel_context should contain fields from ALIGNMENT_CONTRACT.reads."""
        from novel_forge.story_kernel.contracts import ALIGNMENT_CONTRACT

        expected_fields = ALIGNMENT_CONTRACT.reads
        # The contract reads: entities, relationships, timeline, world_rules,
        # promise_ledger, motif_protocols, chapter_summaries
        assert "entities" in expected_fields
        assert "relationships" in expected_fields
        assert "timeline" in expected_fields
        assert "world_rules" in expected_fields
        assert "promise_ledger" in expected_fields
        assert "motif_protocols" in expected_fields
        assert "chapter_summaries" in expected_fields


# ---------------------------------------------------------------------------
# AlignmentStep._execute — kernel_context integration
# ---------------------------------------------------------------------------


class TestAlignmentStepKernelContextIntegration:
    """Test that AlignmentStep._execute passes kernel_context to LLM."""

    async def test_kernel_context_merged_into_llm_context(self, monkeypatch: Any) -> None:
        """When kernel_context is provided, it should be merged into LLM context."""
        captured: dict[str, Any] = {}
        step = object.__new__(AlignmentStep)
        step._settings = SimpleNamespace(temp_check_alignment=0.15)

        def fake_dynamic_max_tokens(
            self: Any,
            task_type: Any,
            target_output_chars: int,
            *,
            prompt_overhead: int,
            min_tokens: int,
        ) -> int:
            return 4096

        async def fake_call_with_retry(
            self: Any, task_type: Any, context: Any, **kwargs: Any
        ) -> dict[str, Any]:
            captured["context"] = context
            return {
                "alignment_score": 9.0,
                "risk_level": "low",
                "summary": "一致。",
                "missing_main_points": [],
                "supportive_subplot_points": [],
                "weak_subplot_points": [],
                "repair_actions": [],
            }

        monkeypatch.setattr(AlignmentStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
        monkeypatch.setattr(AlignmentStep, "_call_with_retry", fake_call_with_retry)

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明", "entity_type": "character"}],
            "relationships": [{"relationship_id": "r-1", "source_entity_id": "e-1", "target_entity_id": "e-2"}],
            "timeline": [{"anchor_id": "t-1", "chapter": 1, "event": "事件"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
            "promise_ledger": [{"entry_id": "p-1", "description": "伏笔"}],
            "motif_protocols": [{"entry_id": "m-1", "motif_name": "月光"}],
            "chapter_summaries": {1: "第一章摘要"},
        }
        input_data = AlignmentInput(
            chapter_outline=SimpleNamespace(
                chapter_number=1,
                title="测试",
                goal="目标",
                main_plot_points=["主线点"],
                subplot_points=[],
                beats_summary=[],
            ),
            chapter_plan=SimpleNamespace(
                scene_intents=[
                    SimpleNamespace(summary="场景", required_outcome="结果")
                ]
            ),
            chapter_text="正文内容",
            kernel_context=kernel_ctx,
        )

        await step._execute(input_data)

        ctx = captured["context"]
        # kernel_context fields should be present in the LLM context
        assert "kernel_context" in ctx
        assert ctx["kernel_context"]["entities"] == kernel_ctx["entities"]
        assert ctx["kernel_context"]["world_rules"] == kernel_ctx["world_rules"]
        assert ctx["kernel_context"]["chapter_summaries"] == kernel_ctx["chapter_summaries"]

    async def test_no_kernel_context_preserves_original_behavior(self, monkeypatch: Any) -> None:
        """When kernel_context is None, LLM context should be unchanged."""
        captured: dict[str, Any] = {}
        step = object.__new__(AlignmentStep)
        step._settings = SimpleNamespace(temp_check_alignment=0.15)

        def fake_dynamic_max_tokens(
            self: Any,
            task_type: Any,
            target_output_chars: int,
            *,
            prompt_overhead: int,
            min_tokens: int,
        ) -> int:
            return 4096

        async def fake_call_with_retry(
            self: Any, task_type: Any, context: Any, **kwargs: Any
        ) -> dict[str, Any]:
            captured["context"] = context
            return {
                "alignment_score": 9.0,
                "risk_level": "low",
                "summary": "一致。",
                "missing_main_points": [],
                "supportive_subplot_points": [],
                "weak_subplot_points": [],
                "repair_actions": [],
            }

        monkeypatch.setattr(AlignmentStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
        monkeypatch.setattr(AlignmentStep, "_call_with_retry", fake_call_with_retry)

        input_data = AlignmentInput(
            chapter_outline=SimpleNamespace(
                chapter_number=1,
                title="测试",
                goal="目标",
                main_plot_points=["主线点"],
                subplot_points=[],
                beats_summary=[],
            ),
            chapter_plan=SimpleNamespace(
                scene_intents=[
                    SimpleNamespace(summary="场景", required_outcome="结果")
                ]
            ),
            chapter_text="正文内容",
            kernel_context=None,
        )

        await step._execute(input_data)

        ctx = captured["context"]
        # kernel_context should NOT be present
        assert "kernel_context" not in ctx
        # Original fields should be present
        assert "chapter_outline" in ctx
        assert "chapter_plan" in ctx
        assert "chapter_text" in ctx

    async def test_empty_kernel_context_not_merged(self, monkeypatch: Any) -> None:
        """When kernel_context is an empty dict, it should not be merged."""
        captured: dict[str, Any] = {}
        step = object.__new__(AlignmentStep)
        step._settings = SimpleNamespace(temp_check_alignment=0.15)

        def fake_dynamic_max_tokens(
            self: Any,
            task_type: Any,
            target_output_chars: int,
            *,
            prompt_overhead: int,
            min_tokens: int,
        ) -> int:
            return 4096

        async def fake_call_with_retry(
            self: Any, task_type: Any, context: Any, **kwargs: Any
        ) -> dict[str, Any]:
            captured["context"] = context
            return {
                "alignment_score": 9.0,
                "risk_level": "low",
                "summary": "一致。",
                "missing_main_points": [],
                "supportive_subplot_points": [],
                "weak_subplot_points": [],
                "repair_actions": [],
            }

        monkeypatch.setattr(AlignmentStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
        monkeypatch.setattr(AlignmentStep, "_call_with_retry", fake_call_with_retry)

        input_data = AlignmentInput(
            chapter_outline=SimpleNamespace(
                chapter_number=1,
                title="测试",
                goal="目标",
                main_plot_points=["主线点"],
                subplot_points=[],
                beats_summary=[],
            ),
            chapter_plan=SimpleNamespace(
                scene_intents=[
                    SimpleNamespace(summary="场景", required_outcome="结果")
                ]
            ),
            chapter_text="正文内容",
            kernel_context={},
        )

        await step._execute(input_data)

        ctx = captured["context"]
        # Empty dict should not be merged
        assert "kernel_context" not in ctx


# ---------------------------------------------------------------------------
# ContextComposer convenience methods
# ---------------------------------------------------------------------------


class TestComposeAlignmentInput:
    """Test ContextComposer.compose_alignment_input convenience method."""

    def test_compose_alignment_input_returns_contract_reads(self) -> None:
        """compose_alignment_input should return ALIGNMENT_CONTRACT.reads fields."""
        from novel_forge.story_kernel.composer import ContextComposer
        from novel_forge.story_kernel.contracts import ALIGNMENT_CONTRACT
        from novel_forge.story_kernel.schemas import (
            Entity,
            MotifProtocol,
            PromiseLedger,
            StoryKernel,
            TimelineAnchor,
            WorldRule,
        )

        kernel = StoryKernel(
            project_id="test",
            entities=[Entity(entity_id="e-1", name="李明", entity_type="character")],
            relationships=[],
            timeline=[TimelineAnchor(anchor_id="t-1", chapter=1, event="事件")],
            world_rules=[WorldRule(rule_id="wr-1", content="规则")],
            promise_ledger=[PromiseLedger(entry_id="p-1", description="伏笔", promise_type="foreshadow", planted_chapter=1, status="planted")],
            motif_protocols=[MotifProtocol(entry_id="m-1", motif_name="月光", motif_type="symbol", description="希望")],
            chapter_summaries={1: "摘要"},
        )

        class _Store:
            def __init__(self, k: StoryKernel) -> None:
                self._k = k

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._k

        composer = ContextComposer(_Store(kernel), project_id="test")
        result = composer.compose_alignment_input(1)

        assert isinstance(result, dict)
        for field_name in ALIGNMENT_CONTRACT.reads:
            assert field_name in result, f"Missing field '{field_name}' in alignment input"

    def test_compose_alignment_input_does_not_include_non_contract_fields(self) -> None:
        """compose_alignment_input should not include fields not in the contract."""
        from novel_forge.story_kernel.composer import ContextComposer
        from novel_forge.story_kernel.schemas import StoryKernel

        kernel = StoryKernel(project_id="test")

        class _Store:
            def __init__(self, k: StoryKernel) -> None:
                self._k = k

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._k

        composer = ContextComposer(_Store(kernel), project_id="test")
        result = composer.compose_alignment_input(1)

        # These fields are NOT in ALIGNMENT_CONTRACT.reads
        assert "object_ledger" not in result
        assert "access_ledger" not in result
        assert "knowledge_ledger" not in result
        assert "business_dependencies" not in result
        assert "banned_phrases" not in result


class TestComposeCheckChapterInput:
    """Test ContextComposer.compose_check_chapter_input convenience method."""

    def test_compose_check_chapter_input_returns_contract_reads(self) -> None:
        """compose_check_chapter_input should return CHECK_CHAPTER_CONTRACT.reads fields."""
        from novel_forge.story_kernel.composer import ContextComposer
        from novel_forge.story_kernel.contracts import CHECK_CHAPTER_CONTRACT
        from novel_forge.story_kernel.schemas import (
            Entity,
            KnowledgeLedger,
            PromiseLedger,
            StoryKernel,
            TimelineAnchor,
            WorldRule,
        )

        kernel = StoryKernel(
            project_id="test",
            entities=[Entity(entity_id="e-1", name="李明", entity_type="character")],
            relationships=[],
            timeline=[TimelineAnchor(anchor_id="t-1", chapter=1, event="事件")],
            world_rules=[WorldRule(rule_id="wr-1", content="规则")],
            knowledge_ledger=[KnowledgeLedger(entry_id="k-1", entity_id="e-1", fact="事实", knowledge_type="known")],
            promise_ledger=[PromiseLedger(entry_id="p-1", description="伏笔", promise_type="foreshadow", planted_chapter=1, status="planted")],
            banned_phrases=["禁用词"],
        )

        class _Store:
            def __init__(self, k: StoryKernel) -> None:
                self._k = k

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._k

        composer = ContextComposer(_Store(kernel), project_id="test")
        result = composer.compose_check_chapter_input(1)

        assert isinstance(result, dict)
        for field_name in CHECK_CHAPTER_CONTRACT.reads:
            assert field_name in result, f"Missing field '{field_name}' in check_chapter input"

    def test_compose_check_chapter_input_does_not_include_non_contract_fields(self) -> None:
        """compose_check_chapter_input should not include fields not in the contract."""
        from novel_forge.story_kernel.composer import ContextComposer
        from novel_forge.story_kernel.schemas import StoryKernel

        kernel = StoryKernel(project_id="test")

        class _Store:
            def __init__(self, k: StoryKernel) -> None:
                self._k = k

            def load_kernel(self, project_id: str) -> StoryKernel:
                return self._k

        composer = ContextComposer(_Store(kernel), project_id="test")
        result = composer.compose_check_chapter_input(1)

        # These fields are NOT in CHECK_CHAPTER_CONTRACT.reads
        assert "access_ledger" not in result
        assert "motif_protocols" not in result
        assert "business_dependencies" not in result
        assert "chapter_summaries" not in result
