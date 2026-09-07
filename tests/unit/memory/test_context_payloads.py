from __future__ import annotations

from types import SimpleNamespace

from novel_forge.memory.context_payloads import (
    serialize_character_episodic_results,
    serialize_character_motifs,
    serialize_due_foreshadows,
    serialize_relationship_projection,
)
from novel_forge.memory.integration import MemoryContext


def test_due_foreshadow_payload_filters_and_limits_items() -> None:
    raw = [
        {
            "entry_id": "p1",
            "description": "  纸灰会在渡口兑现  ",
            "planted_chapter": "2",
            "promise_type": "symbol",
        },
        {"entry_id": "blank", "description": "  "},
        "invalid",
        {"entry_id": "p2", "description": "第二个承诺", "planted_chapter": 3},
    ]

    expected = [
        {
            "entry_id": "p1",
            "description": "纸灰会在渡口兑现",
            "planted_chapter": 2,
            "promise_type": "symbol",
        }
    ]
    assert serialize_due_foreshadows(raw, limit=2) == expected
    assert MemoryContext._serialize_due_foreshadows(raw, limit=2) == expected


def test_relationship_projection_payload_keeps_prompt_safe_fields() -> None:
    raw = [
        {
            "relationship_id": "rel-1",
            "source_entity_id": "char-a",
            "target_entity_id": "char-b",
            "relation_type": "mentor",
            "label": "师徒",
            "trust": 0.8,
            "tension": 0.2,
            "dependency": 0.4,
            "status": "active",
            "last_shift_chapter": "6",
            "shift_summary": "信任提升",
        },
        {"relationship_id": "empty"},
    ]

    expected = [
        {
            "relationship_id": "rel-1",
            "source_entity_id": "char-a",
            "target_entity_id": "char-b",
            "relation_type": "mentor",
            "label": "师徒",
            "trust": 0.8,
            "tension": 0.2,
            "dependency": 0.4,
            "status": "active",
            "last_shift_chapter": 6,
            "shift_summary": "信任提升",
        }
    ]
    assert serialize_relationship_projection(raw) == expected
    assert MemoryContext._serialize_relationship_projection(raw) == expected


def test_character_episodic_payload_supports_dicts_and_objects() -> None:
    long_summary = "a" * 240
    raw = [
        {
            "chapter_number": "3",
            "event_summary": long_summary,
            "scene_index": "2",
            "relevance_score": "0.8765",
            "characters_involved": ["A", "B", "C", "D", "E", "F"],
            "timestamp_in_story": "夜",
        },
        SimpleNamespace(
            chapter_number=2,
            event_summary="对象事件",
            scene_index=1,
            relevance_score=0.1234,
            characters_involved=["A"],
            timestamp_in_story="晨",
        ),
        {"event_summary": "  "},
    ]

    payload = serialize_character_episodic_results(raw, limit=3)

    assert payload[0] == {
        "chapter_number": 3,
        "event_summary": "a" * 240,
        "scene_index": 2,
        "relevance_score": 0.876,
        "characters_involved": ["A", "B", "C", "D", "E", "F"],
        "timestamp_in_story": "夜",
    }
    assert payload[1] == {
        "chapter_number": 2,
        "event_summary": "对象事件",
        "scene_index": 1,
        "relevance_score": 0.123,
        "characters_involved": ["A"],
        "timestamp_in_story": "晨",
    }
    assert MemoryContext._serialize_character_episodic_results(raw, limit=3) == payload


def test_character_motif_payload_filters_retired_and_sorts_recent_first() -> None:
    ctx = MemoryContext()
    ctx._motif_tracker = SimpleNamespace(
        motifs={
            "old": {
                "motif_id": "old",
                "name": "旧承诺",
                "category": "promise",
                "associated_characters": ["林远"],
                "last_appearance_chapter": 2,
                "thematic_meaning": "早期伏笔",
                "retired": False,
            },
            "new": SimpleNamespace(
                motif_id="new",
                name="纸灰",
                category="symbol",
                associated_characters=["林远"],
                last_appearance_chapter=8,
                thematic_meaning="真相回流",
                retired=False,
            ),
            "other": {"associated_characters": ["周岚"], "retired": False},
            "retired": {"associated_characters": ["林远"], "retired": True},
        }
    )

    expected = [
        {
            "motif_id": "new",
            "name": "纸灰",
            "category": "symbol",
            "last_seen": 8,
            "thematic_meaning": "真相回流",
        },
        {
            "motif_id": "old",
            "name": "旧承诺",
            "category": "promise",
            "last_seen": 2,
            "thematic_meaning": "早期伏笔",
        },
    ]
    assert serialize_character_motifs(ctx._motif_tracker, "林远") == expected
    assert ctx._serialize_character_motifs("林远") == expected
