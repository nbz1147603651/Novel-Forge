"""Tests for ContinuityEvalStep and ContinuityRepairStep consuming StoryKernel field slices.

TDD: These tests define the expected behavior of kernel field integration
before implementation.  Both steps should accept an optional kernel_context
dict (from ContextComposer) and merge its fields into LLM/repair context.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
    SceneIntent,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.pipeline.steps.continuity_eval.context import (
    ContinuityEvalInput,
    build_llm_context,
)
from novel_forge.pipeline.steps.continuity_repair_step import (
    ContinuityRepairInput,
    ContinuityRepairStep,
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kernel(**overrides: Any) -> StoryKernel:
    """Create a minimal StoryKernel with continuity-relevant sample data."""
    defaults: dict[str, Any] = {
        "project_id": "test-novel",
        "current_chapter": 3,
        "active_volume": 1,
        "title": "测试小说",
        "premise": "一个关于测试的故事",
        "world_rules": [
            WorldRule(
                rule_id="wr-1",
                content="魔法需要消耗生命力",
                category="magic",
                severity="hard",
            ),
            WorldRule(
                rule_id="wr-2",
                content="时间不可逆转",
                category="temporal",
                severity="hard",
            ),
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
        "object_ledger": [
            ObjectLedger(
                entry_id="obj-1",
                item_name="古塔钥匙",
                owner_entity_id="e-1",
                state="intact",
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
        "motif_protocols": [
            MotifProtocol(
                entry_id="m-1",
                motif_name="月光",
                motif_type="symbol",
                description="月光象征希望",
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


def _build_eval_input(**overrides: Any) -> ContinuityEvalInput:
    """Build a ContinuityEvalInput for testing."""
    packet = ChapterStatePacket(
        chapter_number=3,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="账簿",
            goal="推进账簿造假线",
            pov_character="周明",
            setting="西官仓耳房",
            expected_word_count=2500,
        ),
        canon_context={},
        previous_exit_state=ChapterExitState(
            chapter_number=2,
            time_marker="未时初",
            location="西官仓耳房",
            pov="周明",
            must_carry_forward=["周明与赵成安的合作关系"],
        ),
        previous_chapter_ending="周明把残页压在青砖下，仍留在耳房等更夫报时。",
        must_carry_forward=["周明与赵成安的合作关系"],
    )
    bridge = ChapterBridge(
        from_chapter=2,
        to_chapter=3,
        opening_time="未时初",
        opening_location="西官仓耳房",
        opening_pov="周明",
        transition_mode="direct_continue",
        action_handoff="周明守着账簿，等下一步消息。",
    )
    plan = ChapterPlan(
        opening_contract="承接耳房里的等待状态。",
        closing_contract="留下下一章可直接承接的证据交接。",
    )
    defaults = dict(
        chapter_number=3,
        chapter_text="周明守着账簿，仍在耳房等下一步消息，想着赵成安会不会按约来人。",
        chapter_state_packet=packet,
        chapter_bridge=bridge,
        chapter_plan=plan,
    )
    defaults.update(overrides)
    return ContinuityEvalInput(**defaults)


def _build_repair_input(**overrides: Any) -> ContinuityRepairInput:
    """Build a ContinuityRepairInput for testing."""
    packet = ChapterStatePacket(
        chapter_number=3,
        chapter_outline=ChapterOutline(
            chapter_number=3,
            title="账簿",
            goal="推进账簿造假线",
            pov_character="周明",
            setting="西官仓耳房",
            expected_word_count=2500,
        ),
        canon_context={},
        must_carry_forward=["周明与赵成安的合作关系"],
    )
    bridge = ChapterBridge(
        from_chapter=2,
        to_chapter=3,
        opening_time="未时初",
        opening_location="西官仓耳房",
        opening_pov="周明",
    )
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(scene_id="s1", summary="开场承接"),
            SceneIntent(scene_id="s2", summary="推进主线"),
        ],
        opening_contract="承接耳房里的等待状态。",
        closing_contract="留下下一章可直接承接的证据交接。",
    )
    defaults = dict(
        chapter_number=3,
        chapter_text="周明守着账簿，仍在耳房等下一步消息。",
        chapter_state_packet=packet,
        chapter_bridge=bridge,
        chapter_plan=plan,
        continuity_report=ContinuityReport(continuity_score=9.0, issues=[]),
    )
    defaults.update(overrides)
    return ContinuityRepairInput(**defaults)


def _get_kernel_context() -> dict[str, Any]:
    """Get a kernel field slice via ContextComposer for testing."""
    kernel = _make_kernel()
    store = _MockStore(kernel)
    composer = ContextComposer(store, project_id="test-novel")
    return composer.compose_continuity_eval_input(3, "章节文本")


def _get_repair_kernel_context() -> dict[str, Any]:
    """Get a kernel field slice for the repair step via ContextComposer."""
    kernel = _make_kernel()
    store = _MockStore(kernel)
    composer = ContextComposer(store, project_id="test-novel")
    return composer.compose_for_step("continuity_repair", 3)


# ---------------------------------------------------------------------------
# ContinuityEvalInput — kernel_context field
# ---------------------------------------------------------------------------


class TestContinuityEvalInputKernelContext:
    """Test that ContinuityEvalInput accepts optional kernel_context."""

    def test_accepts_kernel_context(self) -> None:
        """ContinuityEvalInput should accept an optional kernel_context dict."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        assert inp.kernel_context is not None
        assert isinstance(inp.kernel_context, dict)

    def test_kernel_context_defaults_to_none(self) -> None:
        """kernel_context should default to None."""
        inp = _build_eval_input()
        assert inp.kernel_context is None

    def test_kernel_context_preserves_field_data(self) -> None:
        """kernel_context should preserve the field slice data."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        assert "entities" in inp.kernel_context
        assert "relationships" in inp.kernel_context
        assert "timeline" in inp.kernel_context
        assert "world_rules" in inp.kernel_context


# ---------------------------------------------------------------------------
# ContinuityRepairInput — kernel_context field
# ---------------------------------------------------------------------------


class TestContinuityRepairInputKernelContext:
    """Test that ContinuityRepairInput accepts optional kernel_context."""

    def test_accepts_kernel_context(self) -> None:
        """ContinuityRepairInput should accept an optional kernel_context dict."""
        ctx = _get_repair_kernel_context()
        inp = _build_repair_input(kernel_context=ctx)
        assert inp.kernel_context is not None
        assert isinstance(inp.kernel_context, dict)

    def test_kernel_context_defaults_to_none(self) -> None:
        """kernel_context should default to None."""
        inp = _build_repair_input()
        assert inp.kernel_context is None

    def test_kernel_context_contains_expected_fields(self) -> None:
        """kernel_context should contain the expected StoryKernel fields."""
        ctx = _get_repair_kernel_context()
        inp = _build_repair_input(kernel_context=ctx)
        kc = inp.kernel_context
        assert "entities" in kc
        assert "relationships" in kc
        assert "timeline" in kc
        assert "world_rules" in kc
        assert "knowledge_ledger" in kc
        assert "object_ledger" in kc
        assert "chapter_summaries" in kc


# ---------------------------------------------------------------------------
# build_llm_context — kernel field integration
# ---------------------------------------------------------------------------


class TestBuildLlmContextWithKernelFields:
    """Test that build_llm_context merges kernel fields into the LLM context."""

    def _number_paragraphs(self, text: str) -> str:
        """Simple paragraph numberer for testing."""
        paragraphs = text.split("\n\n")
        return "\n\n".join(
            f"[{i + 1}] {p}" for i, p in enumerate(paragraphs)
        )

    def test_kernel_context_merged_into_llm_context(self) -> None:
        """build_llm_context should merge kernel_context into the result."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        # Kernel fields should be present in the LLM context
        assert "kernel_entities" in llm_ctx
        assert "kernel_relationships" in llm_ctx
        assert "kernel_timeline" in llm_ctx
        assert "kernel_world_rules" in llm_ctx

    def test_kernel_context_prefixes_fields_to_avoid_collision(self) -> None:
        """Kernel fields should be prefixed with 'kernel_' to avoid collision."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        # All kernel fields should have 'kernel_' prefix
        kernel_keys = [k for k in llm_ctx if k.startswith("kernel_")]
        assert len(kernel_keys) >= 5  # entities, relationships, timeline, world_rules, knowledge_ledger

    def test_kernel_context_does_not_override_existing_keys(self) -> None:
        """Kernel fields should not override existing LLM context keys."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        # chapter_number should still be the original value
        assert llm_ctx["chapter_number"] == 3
        # chapter_text should still be the original value
        assert "周明守着账簿" in llm_ctx["chapter_text"]

    def test_without_kernel_context_no_kernel_keys(self) -> None:
        """Without kernel_context, no kernel_ keys should appear."""
        inp = _build_eval_input()
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        kernel_keys = [k for k in llm_ctx if k.startswith("kernel_")]
        assert len(kernel_keys) == 0

    def test_kernel_entities_contain_names(self) -> None:
        """Kernel entities should contain entity names for continuity checking."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        entities = llm_ctx["kernel_entities"]
        assert isinstance(entities, list)
        entity_names = [e.get("name", "") for e in entities]
        assert "李明" in entity_names
        assert "王芳" in entity_names

    def test_kernel_timeline_contains_events(self) -> None:
        """Kernel timeline should contain events for continuity checking."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        timeline = llm_ctx["kernel_timeline"]
        assert isinstance(timeline, list)
        events = [t.get("event", "") for t in timeline]
        assert any("古塔" in e for e in events)

    def test_kernel_promise_ledger_included(self) -> None:
        """Kernel promise_ledger should be included for foreshadowing tracking."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        assert "kernel_promise_ledger" in llm_ctx
        promises = llm_ctx["kernel_promise_ledger"]
        assert isinstance(promises, list)
        assert len(promises) >= 1

    def test_kernel_motif_protocols_included(self) -> None:
        """Kernel motif_protocols should be included for motif tracking."""
        ctx = _get_kernel_context()
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        assert "kernel_motif_protocols" in llm_ctx
        motifs = llm_ctx["kernel_motif_protocols"]
        assert isinstance(motifs, list)
        assert len(motifs) >= 1


# ---------------------------------------------------------------------------
# ContinuityRepairStep — build_repair_context with kernel fields
# ---------------------------------------------------------------------------


class TestContinuityRepairContextWithKernelFields:
    """Test that ContinuityRepairStep.build_repair_context merges kernel fields."""

    def test_build_repair_context_includes_kernel_fields(
        self, router: Any, builder: Any
    ) -> None:
        """build_repair_context should merge kernel_context into repair context."""
        from novel_forge.core.config import Settings

        ctx = _get_repair_kernel_context()
        inp = _build_repair_input(kernel_context=ctx)
        step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
        repair_ctx = step.build_repair_context(inp)
        assert "kernel_entities" in repair_ctx
        assert "kernel_relationships" in repair_ctx
        assert "kernel_timeline" in repair_ctx

    def test_build_repair_context_without_kernel_fields(
        self, router: Any, builder: Any
    ) -> None:
        """build_repair_context should work without kernel_context."""
        from novel_forge.core.config import Settings

        inp = _build_repair_input()
        step = ContinuityRepairStep(router, builder, settings=Settings(_env_file=None))
        repair_ctx = step.build_repair_context(inp)
        # Should not crash, no kernel keys
        kernel_keys = [k for k in repair_ctx if k.startswith("kernel_")]
        assert len(kernel_keys) == 0


# ---------------------------------------------------------------------------
# ContextComposer integration — compose + consume roundtrip
# ---------------------------------------------------------------------------


class TestContextComposerContinuityRoundtrip:
    """Test full roundtrip: compose kernel fields → feed to steps."""

    def test_compose_continuity_eval_fields(self) -> None:
        """ContextComposer.compose_continuity_eval_input returns correct fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-novel")
        ctx = composer.compose_continuity_eval_input(3, "测试文本")
        assert "entities" in ctx
        assert "relationships" in ctx
        assert "timeline" in ctx
        assert "world_rules" in ctx
        assert "knowledge_ledger" in ctx
        assert "object_ledger" in ctx
        assert "chapter_summaries" in ctx
        assert "chapter_text" in ctx

    def test_compose_continuity_eval_via_generic(self) -> None:
        """ContextComposer.compose_for_step('continuity_eval', ...) works."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-novel")
        ctx = composer.compose_for_step(
            "continuity_eval", 3, extra_context={"chapter_text": "文本"}
        )
        assert "entities" in ctx
        assert "chapter_text" in ctx

    def test_compose_continuity_repair_via_generic(self) -> None:
        """ContextComposer.compose_for_step('continuity_repair', ...) works."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-novel")
        ctx = composer.compose_for_step("continuity_repair", 3)
        assert "entities" in ctx
        assert "relationships" in ctx
        assert "timeline" in ctx

    def test_eval_input_with_composed_context(self) -> None:
        """ContinuityEvalInput works with composed kernel context."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-novel")
        ctx = composer.compose_continuity_eval_input(3, "测试文本")
        inp = _build_eval_input(kernel_context=ctx)
        assert inp.kernel_context is not None
        assert inp.kernel_context["entities"] == ctx["entities"]

    def test_repair_input_with_composed_context(self) -> None:
        """ContinuityRepairInput works with composed kernel context."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-novel")
        ctx = composer.compose_for_step("continuity_repair", 3)
        inp = _build_repair_input(kernel_context=ctx)
        assert inp.kernel_context is not None
        assert inp.kernel_context["entities"] == ctx["entities"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestKernelFieldEdgeCases:
    """Test edge cases for kernel field integration."""

    def _number_paragraphs(self, text: str) -> str:
        paragraphs = text.split("\n\n")
        return "\n\n".join(
            f"[{i + 1}] {p}" for i, p in enumerate(paragraphs)
        )

    def test_empty_kernel_context(self) -> None:
        """Empty kernel_context dict should not break LLM context building."""
        inp = _build_eval_input(kernel_context={})
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        kernel_keys = [k for k in llm_ctx if k.startswith("kernel_")]
        assert len(kernel_keys) == 0

    def test_kernel_with_empty_field_groups(self) -> None:
        """Kernel with empty field groups should not break context building."""
        kernel = StoryKernel(project_id="empty")
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="empty")
        ctx = composer.compose_continuity_eval_input(1, "文本")
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        # Empty lists should still be present
        assert llm_ctx.get("kernel_entities") == []
        assert llm_ctx.get("kernel_world_rules") == []

    def test_kernel_context_none_field_skipped(self) -> None:
        """None-valued kernel fields should be skipped."""
        kernel = _make_kernel(chapter_summaries={})
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-novel")
        ctx = composer.compose_continuity_eval_input(3, "文本")
        inp = _build_eval_input(kernel_context=ctx)
        llm_ctx, _ = build_llm_context(
            inp,
            number_paragraphs_fn=self._number_paragraphs,
        )
        # chapter_summaries is empty dict, should still be present
        assert "kernel_chapter_summaries" in llm_ctx
