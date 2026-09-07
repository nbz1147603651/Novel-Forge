"""Tests for ChapterRepairStep consuming StoryKernel field slices via ContextComposer.

TDD: These tests define the expected behavior before implementation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.pipeline.steps.check_chapter_step import (
    ChapterRepairInput,
    ChapterRepairStep,
)

# ---------------------------------------------------------------------------
# ChapterRepairInput — kernel_context field
# ---------------------------------------------------------------------------


class TestChapterRepairInputKernelContext:
    """Test that ChapterRepairInput accepts optional kernel_context."""

    def test_kernel_context_defaults_to_none(self) -> None:
        """ChapterRepairInput should have kernel_context=None by default."""
        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文",
            canon_context={},
        )
        assert input_data.kernel_context is None

    def test_kernel_context_can_be_set(self) -> None:
        """ChapterRepairInput should accept a kernel_context dict."""
        ctx = {"entities": [], "relationships": [], "timeline": []}
        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文",
            canon_context={},
            kernel_context=ctx,
        )
        assert input_data.kernel_context is ctx

    def test_kernel_context_fields_match_check_chapter_contract(self) -> None:
        """kernel_context should contain fields from CHECK_CHAPTER_CONTRACT.reads."""
        from novel_forge.story_kernel.contracts import CHECK_CHAPTER_CONTRACT

        expected_fields = CHECK_CHAPTER_CONTRACT.reads
        # The contract reads: entities, relationships, timeline, world_rules,
        # knowledge_ledger, object_ledger, promise_ledger, banned_phrases
        assert "entities" in expected_fields
        assert "relationships" in expected_fields
        assert "timeline" in expected_fields
        assert "world_rules" in expected_fields
        assert "knowledge_ledger" in expected_fields
        assert "object_ledger" in expected_fields
        assert "promise_ledger" in expected_fields
        assert "banned_phrases" in expected_fields


# ---------------------------------------------------------------------------
# ChapterRepairStep._execute — kernel_context integration
# ---------------------------------------------------------------------------


class TestChapterRepairStepKernelContextIntegration:
    """Test that ChapterRepairStep._execute passes kernel_context to LLM."""

    async def test_kernel_context_merged_into_llm_context(self, monkeypatch: Any) -> None:
        """When kernel_context is provided, it should be merged into LLM context."""
        captured: dict[str, Any] = {}
        step = object.__new__(ChapterRepairStep)
        step._settings = SimpleNamespace(
            temp_check_chapter=0.15,
            local_check_as_prescreen=False,
            local_check_confidence_threshold=0.7,
        )

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
                "risk_level": "low",
                "summary": "无问题。",
                "prompt_leaks": [],
                "factual_errors": [],
                "continuity_errors": [],
                "expression_errors": [],
                "repair_actions": [],
            }

        monkeypatch.setattr(ChapterRepairStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
        monkeypatch.setattr(ChapterRepairStep, "_call_with_retry", fake_call_with_retry)

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明", "entity_type": "character"}],
            "relationships": [],
            "timeline": [{"anchor_id": "t-1", "chapter": 1, "event": "事件"}],
            "world_rules": [{"rule_id": "wr-1", "content": "规则"}],
            "knowledge_ledger": [],
            "object_ledger": [],
            "promise_ledger": [],
            "banned_phrases": ["禁用词"],
        }
        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文内容",
            canon_context=SimpleNamespace(characters={}),
            kernel_context=kernel_ctx,
        )

        await step._execute(input_data)

        ctx = captured["context"]
        # kernel_context fields should be present in the LLM context
        assert "kernel_context" in ctx
        assert ctx["kernel_context"]["entities"] == kernel_ctx["entities"]
        assert ctx["kernel_context"]["world_rules"] == kernel_ctx["world_rules"]
        assert ctx["kernel_context"]["banned_phrases"] == kernel_ctx["banned_phrases"]

    async def test_no_kernel_context_preserves_original_behavior(self, monkeypatch: Any) -> None:
        """When kernel_context is None, LLM context should be unchanged."""
        captured: dict[str, Any] = {}
        step = object.__new__(ChapterRepairStep)
        step._settings = SimpleNamespace(
            temp_check_chapter=0.15,
            local_check_as_prescreen=False,
            local_check_confidence_threshold=0.7,
        )

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
                "risk_level": "low",
                "summary": "无问题。",
                "prompt_leaks": [],
                "factual_errors": [],
                "continuity_errors": [],
                "expression_errors": [],
                "repair_actions": [],
            }

        monkeypatch.setattr(ChapterRepairStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
        monkeypatch.setattr(ChapterRepairStep, "_call_with_retry", fake_call_with_retry)

        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文内容",
            canon_context=SimpleNamespace(characters={}),
            kernel_context=None,
        )

        await step._execute(input_data)

        ctx = captured["context"]
        # kernel_context should NOT be present
        assert "kernel_context" not in ctx
        # Original fields should be present
        assert "chapter_number" in ctx
        assert "chapter_text" in ctx
        assert "canon_context" in ctx

    async def test_empty_kernel_context_not_merged(self, monkeypatch: Any) -> None:
        """When kernel_context is an empty dict, it should not be merged."""
        captured: dict[str, Any] = {}
        step = object.__new__(ChapterRepairStep)
        step._settings = SimpleNamespace(
            temp_check_chapter=0.15,
            local_check_as_prescreen=False,
            local_check_confidence_threshold=0.7,
        )

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
                "risk_level": "low",
                "summary": "无问题。",
                "prompt_leaks": [],
                "factual_errors": [],
                "continuity_errors": [],
                "expression_errors": [],
                "repair_actions": [],
            }

        monkeypatch.setattr(ChapterRepairStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
        monkeypatch.setattr(ChapterRepairStep, "_call_with_retry", fake_call_with_retry)

        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文内容",
            canon_context=SimpleNamespace(characters={}),
            kernel_context={},
        )

        await step._execute(input_data)

        ctx = captured["context"]
        # Empty dict should not be merged
        assert "kernel_context" not in ctx


# ---------------------------------------------------------------------------
# Local check logic unchanged
# ---------------------------------------------------------------------------


class TestChapterRepairLocalChecksUnchanged:
    """Verify that local check logic is unaffected by kernel_context."""

    def test_local_quality_payload_unchanged(self) -> None:
        """_build_local_quality_payload should work the same with kernel_context."""
        repeated = "周明抬眼看向门缝，听见风里夹着铁器摩擦声。"
        input_data = ChapterRepairInput(
            chapter_number=2,
            chapter_text=(
                f"{repeated}\n"
                "亥初六刻，金丝微颤一下，冷白灯光在墙面滑过去。\n"
                f"{repeated}\n"
                f"{repeated}"
            ),
            canon_context={},
            forbidden_elements=["金丝微颤"],
            forbidden_elements_soft=["冷白灯光"],
            kernel_context={"entities": [], "relationships": []},
        )
        local_quality = ChapterRepairStep._build_local_quality_payload(input_data)

        assert "factual_errors" in local_quality
        assert "expression_errors" in local_quality
        assert "repair_actions" in local_quality
        assert any("非法时辰" in item for item in local_quality["factual_errors"])

    def test_normalize_payload_unchanged(self) -> None:
        """_normalize_payload should work the same regardless of kernel_context."""
        report = ChapterRepairStep._normalize_payload(
            {"risk_level": "low", "summary": "", "prompt_leaks": []},
            local_prompt_leaks=["【交接】", "预期扰动路径"],
        )

        assert report.risk_level == "medium"
        assert report.prompt_leaks == ["【交接】", "预期扰动路径"]

    def test_build_llm_context_kernel_context_added(self) -> None:
        """_build_llm_context should include kernel_context when present."""
        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "banned_phrases": ["禁用词"],
        }
        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文",
            canon_context=SimpleNamespace(characters={}),
            kernel_context=kernel_ctx,
        )
        context = ChapterRepairStep._build_llm_context(input_data, check_mode="full")

        assert "kernel_context" in context
        assert context["kernel_context"]["entities"] == kernel_ctx["entities"]
        assert context["kernel_context"]["banned_phrases"] == kernel_ctx["banned_phrases"]

    def test_build_llm_context_no_kernel_context(self) -> None:
        """_build_llm_context should not include kernel_context when None."""
        input_data = ChapterRepairInput(
            chapter_number=1,
            chapter_text="正文",
            canon_context=SimpleNamespace(characters={}),
            kernel_context=None,
        )
        context = ChapterRepairStep._build_llm_context(input_data, check_mode="full")

        assert "kernel_context" not in context
