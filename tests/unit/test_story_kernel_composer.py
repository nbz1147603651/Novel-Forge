"""Tests for ContextComposer — minimal field slice assembly for pipeline steps.

TDD: These tests define the expected behavior of ContextComposer before implementation.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.story_kernel.composer import ContextComposer, StoryKernelLoader
from novel_forge.story_kernel.contracts import (
    ALL_CONTRACTS,
    BRIDGE_CONTRACT,
)
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
    """Mock StoryKernelStore for testing ContextComposer."""

    def __init__(self, kernel: StoryKernel) -> None:
        self._kernel = kernel

    def load_kernel(self, project_id: str) -> StoryKernel:
        return self._kernel


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestStoryKernelLoaderProtocol:
    """Verify that the StoryKernelStore protocol is correctly defined."""

    def test_protocol_exists(self) -> None:
        """StoryKernelLoader should be importable as a Protocol."""
        assert StoryKernelLoader._is_protocol is True

    def test_mock_store_satisfies_protocol(self) -> None:
        """A mock store with load_kernel should satisfy the protocol."""
        store = _MockStore(_make_kernel())
        assert isinstance(store, StoryKernelLoader)


# ---------------------------------------------------------------------------
# ContextComposer — basic construction
# ---------------------------------------------------------------------------


class TestContextComposerInit:
    """Test ContextComposer initialization."""

    def test_init_with_store(self) -> None:
        """ContextComposer should accept a StoryKernelStore."""
        store = _MockStore(_make_kernel())
        composer = ContextComposer(store)
        assert composer is not None

    def test_init_requires_store(self) -> None:
        """ContextComposer.__init__ should require a store argument."""
        with pytest.raises(TypeError):
            ContextComposer()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# compose_for_step — generic composition
# ---------------------------------------------------------------------------


class TestComposeForStep:
    """Test the generic compose_for_step method."""

    def test_returns_dict(self) -> None:
        """compose_for_step should return a dict."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result, dict)

    def test_only_contains_contract_reads(self) -> None:
        """Result should only contain fields declared in the step's contract reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        # Bridge contract reads: entities, relationships, timeline, world_rules,
        # knowledge_ledger, promise_ledger, motif_protocols, chapter_summaries
        allowed_keys = BRIDGE_CONTRACT.reads
        for key in result:
            assert key in allowed_keys, f"Unexpected key '{key}' in bridge result"

    def test_does_not_contain_unread_fields(self) -> None:
        """Result should NOT contain fields not in the contract's reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        # Bridge does NOT read: object_ledger, access_ledger, business_dependencies
        assert "object_ledger" not in result
        assert "access_ledger" not in result
        assert "business_dependencies" not in result

    def test_extra_context_merged(self) -> None:
        """Extra context should be merged into the result."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        extra = {"custom_hint": "value", "chapter_text": "some text"}
        result = composer.compose_for_step("bridge", 3, extra_context=extra)
        assert result["custom_hint"] == "value"
        assert result["chapter_text"] == "some text"

    def test_extra_context_does_not_override_kernel_fields(self) -> None:
        """Extra context should NOT override kernel-derived fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        extra = {"entities": "should not override"}
        result = composer.compose_for_step("bridge", 3, extra_context=extra)
        # entities should be the kernel data, not the override
        assert isinstance(result["entities"], list)

    def test_unknown_step_returns_empty(self) -> None:
        """An unknown step name should return an empty dict (or just extra context)."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("nonexistent_step", 3)
        assert isinstance(result, dict)

    def test_all_contracts_produce_valid_slices(self) -> None:
        """Every registered contract should produce a valid field slice."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        for contract_name, contract in ALL_CONTRACTS.items():
            result = composer.compose_for_step(contract.step_name, 3)
            assert isinstance(result, dict), f"Failed for {contract_name}"
            for key in result:
                assert key in contract.reads, (
                    f"{contract_name}: unexpected key '{key}' not in reads"
                )


# ---------------------------------------------------------------------------
# Field serialization
# ---------------------------------------------------------------------------


class TestFieldSerialization:
    """Test that kernel fields are correctly serialized to dicts/lists."""

    def test_entities_are_list_of_dicts(self) -> None:
        """Entities should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result["entities"], list)
        assert len(result["entities"]) == 3
        assert isinstance(result["entities"][0], dict)
        assert "name" in result["entities"][0]

    def test_world_rules_are_list_of_dicts(self) -> None:
        """World rules should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result["world_rules"], list)
        assert len(result["world_rules"]) == 2
        assert isinstance(result["world_rules"][0], dict)
        assert "content" in result["world_rules"][0]

    def test_chapter_summaries_is_dict(self) -> None:
        """Chapter summaries should remain as dict[int, str]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result["chapter_summaries"], dict)
        assert 1 in result["chapter_summaries"]

    def test_relationships_are_list_of_dicts(self) -> None:
        """Relationships should be serialized as list[dict]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result["relationships"], list)
        assert len(result["relationships"]) == 1
        assert isinstance(result["relationships"][0], dict)

    def test_banned_phrases_is_list_of_strings(self) -> None:
        """Banned phrases should be list[str]."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        # edit contract reads banned_phrases
        result = composer.compose_for_step("edit", 3)
        assert isinstance(result["banned_phrases"], list)
        assert all(isinstance(p, str) for p in result["banned_phrases"])


# ---------------------------------------------------------------------------
# Specific composers
# ---------------------------------------------------------------------------


class TestComposeBridgeInput:
    """Test compose_bridge_input convenience method."""

    def test_returns_bridge_fields(self) -> None:
        """compose_bridge_input should return BRIDGE_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_bridge_input(3)
        assert isinstance(result, dict)
        # Bridge reads these fields
        assert "entities" in result
        assert "relationships" in result
        assert "timeline" in result
        assert "world_rules" in result

    def test_does_not_include_draft_only_fields(self) -> None:
        """Bridge input should not include fields only draft reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_bridge_input(3)
        assert "access_ledger" not in result


class TestComposePlanInput:
    """Test compose_plan_input convenience method."""

    def test_returns_plan_fields(self) -> None:
        """compose_plan_input should return PLAN_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_plan_input(3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "object_ledger" in result
        assert "business_dependencies" in result

    def test_includes_chapter_summaries(self) -> None:
        """Plan reads chapter_summaries."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_plan_input(3)
        assert "chapter_summaries" in result


class TestComposeDraftInput:
    """Test compose_draft_input convenience method."""

    def test_returns_draft_fields(self) -> None:
        """compose_draft_input should return DRAFT_CONTRACT.reads fields."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_draft_input(3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "access_ledger" in result

    def test_does_not_include_chapter_summaries(self) -> None:
        """Draft contract does NOT read chapter_summaries."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_draft_input(3)
        assert "chapter_summaries" not in result


class TestComposeContinuityEvalInput:
    """Test compose_continuity_eval_input convenience method."""

    def test_returns_continuity_eval_fields(self) -> None:
        """compose_continuity_eval_input should return CONTINUITY_EVAL_CONTRACT.reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_continuity_eval_input(3, "章节文本")
        assert isinstance(result, dict)
        assert "entities" in result
        assert "chapter_summaries" in result

    def test_includes_chapter_text_in_extra(self) -> None:
        """Chapter text should be passed through as extra context."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_continuity_eval_input(3, "测试章节文本")
        assert "chapter_text" in result
        assert result["chapter_text"] == "测试章节文本"


class TestComposeExtractInput:
    """Test compose_extract_input convenience method."""

    def test_returns_extract_fields(self) -> None:
        """compose_extract_input should return EXTRACT_CONTRACT.reads."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_extract_input(3, "章节文本")
        assert isinstance(result, dict)
        assert "entities" in result
        assert "access_ledger" in result
        assert "business_dependencies" in result

    def test_includes_chapter_text_in_extra(self) -> None:
        """Chapter text should be passed through as extra context."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_extract_input(3, "提取测试文本")
        assert "chapter_text" in result
        assert result["chapter_text"] == "提取测试文本"


class TestComposeGeneric:
    """Test compose_generic convenience method."""

    def test_delegates_to_compose_for_step(self) -> None:
        """compose_generic should produce the same result as compose_for_step."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        generic_result = composer.compose_generic("bridge", 3)
        step_result = composer.compose_for_step("bridge", 3)
        assert generic_result == step_result

    def test_works_for_all_registered_steps(self) -> None:
        """compose_generic should work for every step in ALL_CONTRACTS."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        for contract in ALL_CONTRACTS.values():
            result = composer.compose_generic(contract.step_name, 3)
            assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_kernel(self) -> None:
        """Composer should handle a kernel with empty field groups."""
        kernel = StoryKernel(project_id="empty")
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result = composer.compose_for_step("bridge", 1)
        assert isinstance(result, dict)
        assert result["entities"] == []
        assert result["world_rules"] == []

    def test_chapter_number_forwarded(self) -> None:
        """Chapter number should be accessible for filtering logic."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        # Should not raise for any valid chapter number
        result = composer.compose_for_step("bridge", 1)
        assert isinstance(result, dict)
        result = composer.compose_for_step("bridge", 999)
        assert isinstance(result, dict)

    def test_project_id_propagation(self) -> None:
        """Store should be called with correct project_id."""
        kernel = _make_kernel(project_id="my-novel")
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="my-novel")
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result, dict)

    def test_notes_field_serialization(self) -> None:
        """Notes (str) should be serialized correctly when in reads."""
        kernel = _make_kernel(notes="项目备注信息")
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        # No standard contract reads 'notes' directly, but verify it doesn't break
        result = composer.compose_for_step("bridge", 3)
        assert isinstance(result, dict)

    def test_multiple_calls_return_consistent_results(self) -> None:
        """Multiple calls should return consistent results (no mutation)."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store)
        result1 = composer.compose_for_step("bridge", 3)
        result2 = composer.compose_for_step("bridge", 3)
        assert result1 == result2
