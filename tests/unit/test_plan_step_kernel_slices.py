"""Tests for PlanStep consuming StoryKernel field slices via ContextComposer.

TDD: These tests define the expected behavior of PlanInput with kernel slices
before implementation.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.continuity import ChapterOutline
from novel_forge.pipeline.steps.planning.core import (
    PlanInput,
    _kernel_slices_to_canon_context,
    build_plan_prompt_contexts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_entities() -> list[dict[str, Any]]:
    return [
        {
            "entity_id": "e-1",
            "name": "李明",
            "entity_type": "character",
            "aliases": [],
            "status": "active",
            "attributes": {},
            "source_chapter": 1,
            "last_seen_chapter": 2,
            "notes": "",
        },
        {
            "entity_id": "e-2",
            "name": "王芳",
            "entity_type": "character",
            "aliases": [],
            "status": "active",
            "attributes": {},
            "source_chapter": 1,
            "last_seen_chapter": 2,
            "notes": "",
        },
    ]


def _sample_relationships() -> list[dict[str, Any]]:
    return [
        {
            "relationship_id": "r-1",
            "source_entity_id": "e-1",
            "target_entity_id": "e-2",
            "relation_type": "friend",
            "label": "挚友",
            "trust": 0.8,
            "tension": 0.2,
            "status": "active",
        },
    ]


def _sample_knowledge_ledger() -> list[dict[str, Any]]:
    return [
        {
            "entry_id": "k-1",
            "entity_id": "e-1",
            "fact": "古塔隐藏着秘密",
            "knowledge_type": "known",
            "source_chapter": 1,
            "confidence": 0.9,
        },
    ]


def _sample_object_ledger() -> list[dict[str, Any]]:
    return [
        {
            "entry_id": "o-1",
            "item_name": "玉佩",
            "owner_entity_id": "e-1",
            "state": "intact",
            "description": "一块古老的玉佩",
        },
    ]


def _sample_world_rules() -> list[dict[str, Any]]:
    return [
        {
            "rule_id": "wr-1",
            "content": "魔法需要消耗生命力",
            "category": "magic",
            "severity": "hard",
        },
        {
            "rule_id": "wr-2",
            "content": "时间不可逆转",
            "category": "temporal",
            "severity": "hard",
        },
    ]


def _sample_chapter_outline() -> ChapterOutline:
    return ChapterOutline(
        chapter_number=3,
        title="古塔之谜",
        goal="揭开古塔的秘密",
        pov_character="李明",
        setting="古塔内部",
        expected_word_count=3000,
    )


def _sample_kernel_context() -> dict[str, Any]:
    """Simulate the output of ContextComposer.compose_plan_input()."""
    return {
        "entities": _sample_entities(),
        "relationships": _sample_relationships(),
        "timeline": [
            {
                "anchor_id": "t-1",
                "chapter": 1,
                "event": "李明发现了古塔",
                "characters_involved": ["e-1"],
            },
        ],
        "world_rules": _sample_world_rules(),
        "object_ledger": _sample_object_ledger(),
        "knowledge_ledger": _sample_knowledge_ledger(),
        "promise_ledger": [
            {
                "entry_id": "p-1",
                "description": "古塔的秘密终将揭开",
                "promise_type": "foreshadow",
                "planted_chapter": 1,
                "status": "planted",
            },
        ],
        "motif_protocols": [
            {
                "entry_id": "m-1",
                "motif_name": "月光",
                "motif_type": "symbol",
                "description": "月光象征希望",
            },
        ],
        "business_dependencies": [],
        "chapter_summaries": {1: "李明发现了古塔", 2: "王芳加入冒险"},
    }


# ---------------------------------------------------------------------------
# Tests: PlanInput accepts kernel slices
# ---------------------------------------------------------------------------


class TestPlanInputKernelSlices:
    """Test that PlanInput accepts StoryKernel field slices."""

    def test_plan_input_accepts_character_knowledge_slice(self) -> None:
        """PlanInput should accept character_knowledge_slice field."""
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=None,
            character_knowledge_slice=_sample_knowledge_ledger(),
        )
        assert pi.character_knowledge_slice is not None
        assert len(pi.character_knowledge_slice) == 1

    def test_plan_input_accepts_relationship_slice(self) -> None:
        """PlanInput should accept relationship_slice field."""
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=None,
            relationship_slice=_sample_relationships(),
        )
        assert pi.relationship_slice is not None
        assert len(pi.relationship_slice) == 1

    def test_plan_input_accepts_object_slice(self) -> None:
        """PlanInput should accept object_slice field."""
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=None,
            object_slice=_sample_object_ledger(),
        )
        assert pi.object_slice is not None
        assert len(pi.object_slice) == 1

    def test_plan_input_accepts_forbidden_contradictions(self) -> None:
        """PlanInput should accept forbidden_contradictions field."""
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=None,
            forbidden_contradictions=_sample_world_rules(),
        )
        assert pi.forbidden_contradictions is not None
        assert len(pi.forbidden_contradictions) == 2

    def test_plan_input_accepts_scene_constraints(self) -> None:
        """PlanInput should accept scene_constraints field."""
        constraints = {"max_scenes": 4, "must_include_location": "古塔"}
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=None,
            scene_constraints=constraints,
        )
        assert pi.scene_constraints is not None
        assert pi.scene_constraints["max_scenes"] == 4

    def test_plan_input_accepts_chapter_plan(self) -> None:
        """PlanInput should accept chapter_plan field (plan-level metadata)."""
        plan_meta = {"chapter_number": 3, "act": "rising", "pacing": "moderate"}
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=None,
            chapter_plan=plan_meta,
        )
        assert pi.chapter_plan is not None
        assert pi.chapter_plan["act"] == "rising"

    def test_plan_input_all_slices_none_by_default(self) -> None:
        """All kernel slices should default to None."""
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context={},
        )
        assert pi.character_knowledge_slice is None
        assert pi.relationship_slice is None
        assert pi.object_slice is None
        assert pi.forbidden_contradictions is None
        assert pi.scene_constraints is None
        assert pi.chapter_plan is None

    def test_plan_input_backward_compat_with_canon_context(self) -> None:
        """PlanInput should still work with old canon_context (no slices)."""
        canon = {"characters": {"李明": {"role": "protagonist"}}}
        pi = PlanInput(
            chapter_outline=_sample_chapter_outline(),
            canon_context=canon,
        )
        assert pi.canon_context == canon
        assert pi.character_knowledge_slice is None

    def test_build_plan_prompt_contexts_matches_plan_trim_rules(self) -> None:
        outline = _sample_chapter_outline()
        outline.involved_characters = ["王芳"]
        pi = PlanInput.from_kernel_context(
            outline,
            _sample_kernel_context(),
            memory_hints={
                "relevant_history": [
                    {"chapter_number": 2, "event_summary": "王芳加入冒险", "relevance_score": 0.9}
                ]
            },
        )
        settings = type(
            "SettingsStub",
            (),
            {
                "long_plan_max_foreshadowing": 2,
            },
        )()

        contexts = build_plan_prompt_contexts(pi, settings)

        assert contexts.focus_characters[:2] == ["李明", "王芳"]
        assert set(contexts.plan_canon_context["characters"]) == {"李明", "王芳"}
        assert contexts.plan_memory_hints["relevant_history"][0]["event_summary"] == "王芳加入冒险"


# ---------------------------------------------------------------------------
# Tests: _kernel_slices_to_canon_context
# ---------------------------------------------------------------------------


class TestKernelSlicesToCanonContext:
    """Test conversion from kernel slices to canon_context dict."""

    def test_basic_conversion(self) -> None:
        """Kernel slices should be converted to a canon_context dict."""
        result = _kernel_slices_to_canon_context(
            entities=_sample_entities(),
            relationships=_sample_relationships(),
            knowledge_ledger=_sample_knowledge_ledger(),
            object_ledger=_sample_object_ledger(),
            world_rules=_sample_world_rules(),
            timeline=[
                {
                    "anchor_id": "t-1",
                    "chapter": 1,
                    "event": "李明发现了古塔",
                    "characters_involved": ["e-1"],
                },
            ],
            promise_ledger=[
                {
                    "entry_id": "p-1",
                    "description": "古塔的秘密终将揭开",
                    "promise_type": "foreshadow",
                    "planted_chapter": 1,
                    "status": "planted",
                },
            ],
            chapter_summaries={1: "李明发现了古塔", 2: "王芳加入冒险"},
        )
        assert isinstance(result, dict)
        # Should have characters key (name -> entity dict)
        assert "characters" in result
        assert "李明" in result["characters"]
        # Should have recent_events from timeline
        assert "recent_events" in result
        # Should have active_foreshadowing from promise_ledger
        assert "active_foreshadowing" in result

    def test_empty_slices_produce_empty_context(self) -> None:
        """Empty kernel slices should produce a minimal canon_context."""
        result = _kernel_slices_to_canon_context(
            entities=[],
            relationships=[],
            knowledge_ledger=[],
            object_ledger=[],
            world_rules=[],
            timeline=[],
            promise_ledger=[],
            chapter_summaries={},
        )
        assert isinstance(result, dict)
        assert result.get("characters") == {}

    def test_none_slices_produce_empty_context(self) -> None:
        """None kernel slices should produce a minimal canon_context."""
        result = _kernel_slices_to_canon_context(
            entities=None,
            relationships=None,
            knowledge_ledger=None,
            object_ledger=None,
            world_rules=None,
            timeline=None,
            promise_ledger=None,
            chapter_summaries=None,
        )
        assert isinstance(result, dict)
        assert result.get("characters") == {}

    def test_entities_become_characters(self) -> None:
        """Entities should be indexed by name in characters dict."""
        result = _kernel_slices_to_canon_context(
            entities=_sample_entities(),
            relationships=[],
            knowledge_ledger=[],
            object_ledger=[],
            world_rules=[],
            timeline=[],
            promise_ledger=[],
            chapter_summaries={},
        )
        chars = result["characters"]
        assert "李明" in chars
        assert "王芳" in chars
        assert chars["李明"]["entity_id"] == "e-1"

    def test_timeline_becomes_recent_events(self) -> None:
        """Timeline anchors should become recent_events list."""
        result = _kernel_slices_to_canon_context(
            entities=[],
            relationships=[],
            knowledge_ledger=[],
            object_ledger=[],
            world_rules=[],
            timeline=[
                {
                    "anchor_id": "t-1",
                    "chapter": 1,
                    "event": "李明发现了古塔",
                },
                {
                    "anchor_id": "t-2",
                    "chapter": 2,
                    "event": "王芳加入冒险",
                },
            ],
            promise_ledger=[],
            chapter_summaries={},
        )
        events = result.get("recent_events", [])
        assert len(events) == 2
        assert events[0]["event"] == "李明发现了古塔"

    def test_promise_ledger_becomes_active_foreshadowing(self) -> None:
        """Planted/hinted promises should become active_foreshadowing."""
        result = _kernel_slices_to_canon_context(
            entities=[],
            relationships=[],
            knowledge_ledger=[],
            object_ledger=[],
            world_rules=[],
            timeline=[],
            promise_ledger=[
                {
                    "entry_id": "p-1",
                    "description": "古塔的秘密终将揭开",
                    "status": "planted",
                },
                {
                    "entry_id": "p-2",
                    "description": "已兑现的伏笔",
                    "status": "paid",
                },
            ],
            chapter_summaries={},
        )
        foreshadowing = result.get("active_foreshadowing", [])
        assert len(foreshadowing) == 1
        assert foreshadowing[0]["description"] == "古塔的秘密终将揭开"

    def test_world_rules_become_immutable_facts(self) -> None:
        """World rules should become immutable_facts in canon_context."""
        result = _kernel_slices_to_canon_context(
            entities=[],
            relationships=[],
            knowledge_ledger=[],
            object_ledger=[],
            world_rules=_sample_world_rules(),
            timeline=[],
            promise_ledger=[],
            chapter_summaries={},
        )
        facts = result.get("immutable_facts", [])
        assert len(facts) == 2
        contents = {f["content"] for f in facts}
        assert "魔法需要消耗生命力" in contents
        assert "时间不可逆转" in contents

    def test_knowledge_enriches_character_context(self) -> None:
        """Knowledge ledger entries should enrich character context."""
        result = _kernel_slices_to_canon_context(
            entities=_sample_entities(),
            relationships=[],
            knowledge_ledger=_sample_knowledge_ledger(),
            object_ledger=[],
            world_rules=[],
            timeline=[],
            promise_ledger=[],
            chapter_summaries={},
        )
        chars = result["characters"]
        li_ming = chars.get("李明", {})
        known_facts = li_ming.get("known_facts", [])
        assert any("古塔隐藏着秘密" in str(f) for f in known_facts)

    def test_full_kernel_context_roundtrip(self) -> None:
        """A full ContextComposer output should convert to usable canon_context."""
        kernel_ctx = _sample_kernel_context()
        result = _kernel_slices_to_canon_context(
            entities=kernel_ctx.get("entities"),
            relationships=kernel_ctx.get("relationships"),
            knowledge_ledger=kernel_ctx.get("knowledge_ledger"),
            object_ledger=kernel_ctx.get("object_ledger"),
            world_rules=kernel_ctx.get("world_rules"),
            timeline=kernel_ctx.get("timeline"),
            promise_ledger=kernel_ctx.get("promise_ledger"),
            chapter_summaries=kernel_ctx.get("chapter_summaries"),
        )
        assert isinstance(result, dict)
        assert len(result.get("characters", {})) >= 2
        assert len(result.get("recent_events", [])) >= 1
        assert len(result.get("active_foreshadowing", [])) >= 1
        assert len(result.get("immutable_facts", [])) >= 2


# ---------------------------------------------------------------------------
# Tests: PlanInput.from_kernel_context classmethod
# ---------------------------------------------------------------------------


class TestPlanInputFromKernelContext:
    """Test PlanInput.from_kernel_context() factory method."""

    def test_creates_plan_input_from_kernel_context(self) -> None:
        """from_kernel_context should populate kernel slices."""
        outline = _sample_chapter_outline()
        kernel_ctx = _sample_kernel_context()
        pi = PlanInput.from_kernel_context(
            chapter_outline=outline,
            kernel_context=kernel_ctx,
        )
        assert pi.chapter_outline is outline
        assert pi.character_knowledge_slice is not None
        assert pi.relationship_slice is not None
        assert pi.object_slice is not None
        assert pi.forbidden_contradictions is not None

    def test_from_kernel_context_preserves_other_kwargs(self) -> None:
        """from_kernel_context should pass through additional kwargs."""
        outline = _sample_chapter_outline()
        kernel_ctx = _sample_kernel_context()
        pi = PlanInput.from_kernel_context(
            chapter_outline=outline,
            kernel_context=kernel_ctx,
            pov_hint="从李明视角",
            style_profile={"tone": "dark"},
        )
        assert pi.pov_hint == "从李明视角"
        assert pi.style_profile == {"tone": "dark"}

    def test_from_kernel_context_handles_empty_kernel(self) -> None:
        """from_kernel_context should handle empty kernel context gracefully."""
        outline = _sample_chapter_outline()
        pi = PlanInput.from_kernel_context(
            chapter_outline=outline,
            kernel_context={},
        )
        assert pi.chapter_outline is outline
        # Empty context should still produce None slices
        assert pi.character_knowledge_slice is None or pi.character_knowledge_slice == []
