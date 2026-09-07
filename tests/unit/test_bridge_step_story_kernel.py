"""Tests for BridgeStep consuming StoryKernel field slices via ContextComposer.

Verifies that:
1. BridgeInput accepts story_kernel_context
2. BridgeStep uses kernel context instead of canon_context
3. Output format (ChapterBridge) is unchanged
4. The no-kernel path is rejected
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterExitState, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.types import ModelRequest
from novel_forge.pipeline.steps.bridge_step import BridgeInput, BridgeStep
from tests.unit.conftest import _MockRouter

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeBuilder:
    def __init__(self) -> None:
        self.last_context: dict | None = None

    def build(
        self,
        task_type: Any,
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


def _make_packet(
    chapter_number: int = 2,
    previous_exit: ChapterExitState | None = None,
    canon_context: dict[str, Any] | None = None,
) -> ChapterStatePacket:
    kwargs: dict[str, Any] = {
        "chapter_number": chapter_number,
        "chapter_outline": ChapterOutline(
            chapter_number=chapter_number,
            goal="探索新地点",
            setting="废弃图书馆",
            pov_character="林远",
        ),
        "previous_exit_state": previous_exit,
        "previous_chapter_ending": "",
        "previous_bridge": None,
        "accumulated_forbidden_repetition": [],
        "must_carry_forward": ["上一章的悬念"],
    }
    if canon_context is not None:
        kwargs["canon_context"] = canon_context
    return ChapterStatePacket(**kwargs)


def _make_step(json_payload: dict) -> tuple[BridgeStep, _FakeBuilder]:
    builder = _FakeBuilder()
    step = BridgeStep(
        _MockRouter(json_payload=json_payload, completion_tokens=200),
        builder,
        settings=Settings(),
    )
    return step, builder


def _default_bridge_payload() -> dict:
    return {
        "opening_time": "清晨",
        "opening_location": "废弃图书馆",
        "opening_pov": "林远",
        "transition_mode": "action_handoff",
        "emotional_carryover": "紧张与期待",
        "action_handoff": "林远决定前往废弃图书馆寻找线索",
        "causal_link": {
            "previous_event": "上一章发现神秘信件",
            "causal_mechanism": "信件指引前往图书馆",
            "unresolved_question": "信件的来源是什么",
            "open_threads": ["神秘信件", "图书馆的秘密"],
        },
        "pending_questions": ["信件的来源是什么"],
        "forbidden_repetition": [],
        "bridge_summary": "林远前往废弃图书馆",
        "sensory_anchors": ["潮湿的空气", "陈旧的书味"],
        "opening_acceptance_criteria": ["开场承接上一章动作"],
    }


def _make_kernel_context() -> dict[str, Any]:
    """Build a minimal StoryKernel field slice matching BRIDGE_CONTRACT reads."""
    return {
        "entities": [
            {
                "entity_id": "char_lin_yuan",
                "name": "林远",
                "entity_type": "character",
                "aliases": [],
                "status": "active",
                "attributes": {"social_status": "调查员"},
                "source_chapter": 1,
                "last_seen_chapter": 1,
                "notes": "",
            },
        ],
        "relationships": [
            {
                "relationship_id": "rel_001",
                "source_entity_id": "char_lin_yuan",
                "target_entity_id": "char_mentor",
                "relation_type": "mentor_student",
                "label": "师徒",
                "trust": 0.8,
                "tension": 0.2,
                "status": "active",
                "established_chapter": 1,
                "last_shift_chapter": 1,
                "shift_summary": "",
                "notes": "",
            },
        ],
        "timeline": [
            {
                "anchor_id": "evt_001",
                "chapter": 1,
                "event": "林远发现神秘信件",
                "in_story_time": "第一天傍晚",
                "characters_involved": ["char_lin_yuan"],
                "location": "雾霭小镇",
                "significance": "major",
                "tags": ["mystery"],
            },
        ],
        "world_rules": [
            {
                "rule_id": "rule_001",
                "content": "雾霭小镇中存在不可见的结界",
                "category": "physics",
                "severity": "hard",
                "source_chapter": 1,
                "notes": "",
            },
        ],
        "knowledge_ledger": [
            {
                "entry_id": "know_001",
                "entity_id": "char_lin_yuan",
                "fact": "信件指向废弃图书馆",
                "knowledge_type": "known",
                "source_chapter": 1,
                "revealed_in_chapter": 0,
                "visibility": "private",
                "confidence": 0.9,
                "notes": "",
            },
        ],
        "promise_ledger": [
            {
                "entry_id": "prom_001",
                "description": "图书馆中藏有失落的记忆",
                "promise_type": "foreshadow",
                "planted_chapter": 1,
                "status": "planted",
                "payoff_chapter": 0,
                "owner_entity_ids": ["char_lin_yuan"],
                "depends_on": [],
                "visibility": "public",
                "notes": "",
            },
        ],
        "motif_protocols": [
            {
                "entry_id": "motif_001",
                "motif_name": "雾气",
                "description": "笼罩一切的迷雾象征未知",
                "motif_type": "symbol",
                "occurrences": [1],
                "cooldown_chapters": 2,
                "last_used_chapter": 1,
                "intensity": 0.6,
                "channel": "sensory_anchor",
                "examples": ["雾霭弥漫的街道"],
                "notes": "",
            },
        ],
        "chapter_summaries": {
            1: "林远在雾霭小镇发现一封指向废弃图书馆的神秘信件。",
        },
    }


# ---------------------------------------------------------------------------
# Happy path: BridgeStep with story_kernel_context
# ---------------------------------------------------------------------------


async def test_bridge_step_with_story_kernel_context() -> None:
    """BridgeStep produces a valid ChapterBridge when given kernel context."""
    payload = _default_bridge_payload()
    step, _ = _make_step(payload)

    previous_exit = ChapterExitState(
        chapter_number=1,
        time_marker="夜晚",
        location="雾霭小镇",
        pov="林远",
        must_carry_forward=["神秘信件"],
        open_questions=["信件的来源"],
    )
    packet = _make_packet(chapter_number=2, previous_exit=previous_exit)
    kernel_ctx = _make_kernel_context()

    input_data = BridgeInput(
        chapter_state_packet=packet,
        genre="fantasy",
        tone="mysterious",
        story_kernel_context=kernel_ctx,
    )

    result = await step.run(input_data)

    assert isinstance(result, ChapterBridge)
    assert result.to_chapter == 2
    assert result.opening_location == "废弃图书馆"


# ---------------------------------------------------------------------------
# Kernel context replaces canon_context for anchor terms
# ---------------------------------------------------------------------------


def test_normalize_bridge_payload_uses_kernel_context_for_anchor_terms() -> None:
    """_normalize_bridge_payload extracts anchor terms from kernel context, not canon_context."""
    kernel_ctx = _make_kernel_context()

    # Packet with empty canon_context — if kernel context is not used, no terms extracted
    packet = _make_packet(
        chapter_number=2,
        previous_exit=ChapterExitState(
            chapter_number=1,
            location="耳房",
            pov="周明",
            must_carry_forward=["账簿残页"],
            open_questions=["赵成安是否会来"],
        ),
        canon_context={},
    )

    normalized, _issues = BridgeStep._normalize_bridge_payload(
        {
            "opening_location": "耳房",
            "opening_pov": "周明",
            "transition_mode": "direct_continue",
            "action_handoff": "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
            "forbidden_repetition": [
                "避免重复使用「冷白灯光」（近8章平均3.0次/章）",
                "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
                "金丝微颤",
            ],
        },
        packet,
        story_kernel_context=kernel_ctx,
    )

    # The forbidden repetition should be filtered (same behavior as before)
    assert normalized["forbidden_repetition"] == ["冷白灯光", "金丝微颤"]


# ---------------------------------------------------------------------------
# New architecture: StoryKernel context is required
# ---------------------------------------------------------------------------


def test_bridge_input_requires_story_kernel_context() -> None:
    """BridgeInput no longer allows the old no-kernel path."""
    packet = _make_packet()
    with pytest.raises(TypeError):
        BridgeInput(chapter_state_packet=packet)


def test_normalize_bridge_payload_requires_kernel_context() -> None:
    """_normalize_bridge_payload consumes StoryKernel field slices."""
    packet = _make_packet(
        chapter_number=2,
        previous_exit=ChapterExitState(
            chapter_number=1,
            location="耳房",
            pov="周明",
            must_carry_forward=["账簿残页"],
            open_questions=["赵成安是否会来"],
        ),
    )

    normalized, _issues = BridgeStep._normalize_bridge_payload(
        {
            "opening_location": "耳房",
            "opening_pov": "周明",
            "transition_mode": "direct_continue",
            "action_handoff": "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
            "forbidden_repetition": [
                "避免重复使用「冷白灯光」（近8章平均3.0次/章）",
                "周明把账簿残页交给赵成安，确认账簿残页线索落地。",
                "金丝微颤",
            ],
        },
        packet,
        _make_kernel_context(),
    )

    assert normalized["forbidden_repetition"] == ["冷白灯光", "金丝微颤"]


# ---------------------------------------------------------------------------
# Kernel context fields are correct shape
# ---------------------------------------------------------------------------


def test_bridge_input_accepts_story_kernel_context() -> None:
    """BridgeInput dataclass accepts story_kernel_context field."""
    kernel_ctx = _make_kernel_context()
    packet = _make_packet()

    input_data = BridgeInput(
        chapter_state_packet=packet,
        genre="fantasy",
        story_kernel_context=kernel_ctx,
    )

    assert input_data.story_kernel_context is not None
    assert "entities" in input_data.story_kernel_context
    assert "relationships" in input_data.story_kernel_context
    assert "timeline" in input_data.story_kernel_context
    assert "promise_ledger" in input_data.story_kernel_context
    assert "motif_protocols" in input_data.story_kernel_context
    assert "knowledge_ledger" in input_data.story_kernel_context
    assert "chapter_summaries" in input_data.story_kernel_context


# ---------------------------------------------------------------------------
# Kernel context passes through to build_bridge_cards
# ---------------------------------------------------------------------------


async def test_kernel_context_passed_to_build_bridge_cards() -> None:
    """When kernel context is provided, it's used as canon_context in build_bridge_cards."""
    payload = _default_bridge_payload()
    step, builder = _make_step(payload)

    packet = _make_packet()
    kernel_ctx = _make_kernel_context()

    input_data = BridgeInput(
        chapter_state_packet=packet,
        genre="fantasy",
        story_kernel_context=kernel_ctx,
    )

    await step.run(input_data)

    # The builder should have received context (stage_cards)
    assert builder.last_context is not None
    assert "stage_cards" in builder.last_context


# ---------------------------------------------------------------------------
# Output format unchanged
# ---------------------------------------------------------------------------


async def test_bridge_output_format_stable_with_kernel_context() -> None:
    """ChapterBridge output fields stay stable when kernel context is present."""
    payload = _default_bridge_payload()
    previous_exit = ChapterExitState(
        chapter_number=1,
        time_marker="夜晚",
        location="雾霭小镇",
        pov="林远",
        must_carry_forward=["神秘信件"],
        open_questions=["信件的来源"],
    )
    packet = _make_packet(chapter_number=2, previous_exit=previous_exit)
    kernel_ctx = _make_kernel_context()

    step1, _ = _make_step(payload)
    result_a = await step1.run(
        BridgeInput(
            chapter_state_packet=packet,
            genre="fantasy",
            tone="mysterious",
            story_kernel_context=kernel_ctx,
        )
    )

    step2, _ = _make_step(payload)
    result_b = await step2.run(
        BridgeInput(
            chapter_state_packet=packet,
            genre="fantasy",
            tone="mysterious",
            story_kernel_context=kernel_ctx,
        )
    )

    assert isinstance(result_a, ChapterBridge)
    assert isinstance(result_b, ChapterBridge)
    assert result_a.to_chapter == result_b.to_chapter
    assert result_a.from_chapter == result_b.from_chapter
    assert result_a.opening_pov == result_b.opening_pov


# ---------------------------------------------------------------------------
# Empty kernel context handling
# ---------------------------------------------------------------------------


async def test_bridge_step_rejects_empty_kernel_context() -> None:
    """BridgeStep requires a non-empty StoryKernel slice."""
    payload = _default_bridge_payload()
    step, _ = _make_step(payload)

    packet = _make_packet()

    input_data = BridgeInput(
        chapter_state_packet=packet,
        genre="fantasy",
        story_kernel_context={},
    )

    with pytest.raises(ValueError, match="StoryKernel context"):
        await step.run(input_data)


# ---------------------------------------------------------------------------
# Kernel context with minimal fields
# ---------------------------------------------------------------------------


async def test_bridge_step_partial_kernel_context() -> None:
    """BridgeStep handles kernel context with only some fields present."""
    payload = _default_bridge_payload()
    step, _ = _make_step(payload)

    packet = _make_packet()

    # Only entities and relationships — missing other fields
    partial_ctx = {
        "entities": _make_kernel_context()["entities"],
        "relationships": _make_kernel_context()["relationships"],
    }

    input_data = BridgeInput(
        chapter_state_packet=packet,
        genre="fantasy",
        story_kernel_context=partial_ctx,
    )

    result = await step.run(input_data)
    assert isinstance(result, ChapterBridge)
