"""Tests for mode="before" field validators on init_v2 schema classes.

Covers: CreativeDirectorPacket.notes, RelationshipEdge.description,
EntityLink.description, BlueprintFragments.synopsis, BlueprintFragments.ending_strategy.

Validators call stringify_text_value to flatten nested dict/list
structures that LLM outputs sometimes produce for str fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from novel_forge.core.schemas.init_v2 import (
    BlueprintFragments,
    CreativeDirectorPacket,
    EntityLink,
    RelationshipEdge,
)

# ── helpers ──────────────────────────────────────────────────────────


def _make_edge(**overrides: Any) -> RelationshipEdge:
    defaults: dict[str, Any] = {
        "source_id": "char_1",
        "target_id": "char_2",
        "source_name": "Alice",
        "target_name": "Bob",
    }
    defaults.update(overrides)
    return RelationshipEdge(**defaults)


def _make_entity_link(**overrides: Any) -> EntityLink:
    defaults: dict[str, Any] = {
        "source_id": "loc_1",
        "target_id": "item_1",
    }
    defaults.update(overrides)
    return EntityLink(**defaults)


def _make_packet(**overrides: Any) -> CreativeDirectorPacket:
    return CreativeDirectorPacket(**overrides)


def _make_fragments(**overrides: Any) -> BlueprintFragments:
    return BlueprintFragments(**overrides)


# ── RelationshipEdge.description ─────────────────────────────────────


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        # plain strings pass through
        ("亦敌亦友的关系", "亦敌亦友的关系"),
        # None → empty
        (None, ""),
        # int → str
        (42, "42"),
        # dict → flattened
        ({"type": "rival", "tension": "high"}, "type: rival；tension: high"),
        # list → joined
        (["青梅竹马", "宿命对决"], "青梅竹马；宿命对决"),
        # empty containers → ""
        ("", ""),
        ([], ""),
        ({}, ""),
    ],
)
def test_relationship_edge_description_coercion(input_val: Any, expected: str) -> None:
    """mode='before' validator on RelationshipEdge.description."""
    edge = _make_edge(description=input_val)
    assert edge.description == expected


def test_relationship_edge_description_default() -> None:
    """Default is empty string."""
    edge = _make_edge()
    assert edge.description == ""


# ── EntityLink.description ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("藏宝图与古墓的关联", "藏宝图与古墓的关联"),
        (None, ""),
        (99, "99"),
        ({"link": "钥匙与锁"}, "link: 钥匙与锁"),
        (["线索A", "线索B"], "线索A；线索B"),
        ("", ""),
        ([], ""),
        ({}, ""),
    ],
)
def test_entity_link_description_coercion(input_val: Any, expected: str) -> None:
    """mode='before' validator on EntityLink.description."""
    link = _make_entity_link(description=input_val)
    assert link.description == expected


def test_entity_link_description_default() -> None:
    """Default is empty string."""
    link = _make_entity_link()
    assert link.description == ""


# ── CreativeDirectorPacket.notes ──────────────────────────────────────


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("整体风格偏黑暗", "整体风格偏黑暗"),
        (None, ""),
        (True, "True"),
        ({"theme": "背叛", "tone": "沉重"}, "theme: 背叛；tone: 沉重"),
        (["核心冲突：信任缺失"], "核心冲突：信任缺失"),
        ("", ""),
        ([], ""),
        ({}, ""),
    ],
)
def test_creative_director_packet_notes_coercion(input_val: Any, expected: str) -> None:
    """mode='before' validator on CreativeDirectorPacket.notes."""
    packet = _make_packet(notes=input_val)
    assert packet.notes == expected


def test_creative_director_packet_notes_default() -> None:
    """Default is empty string."""
    packet = _make_packet()
    assert packet.notes == ""


# ── BlueprintFragments.synopsis ───────────────────────────────────────


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("这是一个关于救赎的故事", "这是一个关于救赎的故事"),
        (None, ""),
        (2024, "2024"),
        ({"hook": "主角失忆", "world": "赛博朋克"}, "hook: 主角失忆；world: 赛博朋克"),
        (["第一卷：觉醒", "第二卷：抗争"], "第一卷：觉醒；第二卷：抗争"),
        ("", ""),
        ([], ""),
        ({}, ""),
    ],
)
def test_blueprint_fragments_synopsis_coercion(input_val: Any, expected: str) -> None:
    """mode='before' validator on BlueprintFragments.synopsis."""
    fragments = _make_fragments(synopsis=input_val)
    assert fragments.synopsis == expected


def test_blueprint_fragments_synopsis_default() -> None:
    """Default is empty string."""
    fragments = _make_fragments()
    assert fragments.synopsis == ""


# ── BlueprintFragments.ending_strategy ────────────────────────────────


@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("开放式结局", "开放式结局"),
        (None, ""),
        (3, "3"),
        ({"type": "反转", "reveal": "身世"}, "type: 反转；reveal: 身世"),
        (["悬念保留", "番外篇暗示"], "悬念保留；番外篇暗示"),
        ("", ""),
        ([], ""),
        ({}, ""),
    ],
)
def test_blueprint_fragments_ending_strategy_coercion(input_val: Any, expected: str) -> None:
    """mode='before' validator on BlueprintFragments.ending_strategy."""
    fragments = _make_fragments(ending_strategy=input_val)
    assert fragments.ending_strategy == expected


def test_blueprint_fragments_ending_strategy_default() -> None:
    """Default is empty string."""
    fragments = _make_fragments()
    assert fragments.ending_strategy == ""


# ── BlueprintFragments non-target fields unchanged ────────────────────


def test_blueprint_fragments_assembly_mode_untouched() -> None:
    """assembly_mode is not validated and passes through unchanged."""
    fragments = _make_fragments(assembly_mode="auto")
    assert fragments.assembly_mode == "auto"


def test_blueprint_fragments_volume_mode_untouched() -> None:
    """volume_mode (bool) is not validated and passes through unchanged."""
    fragments = _make_fragments(volume_mode=True)
    assert fragments.volume_mode is True
