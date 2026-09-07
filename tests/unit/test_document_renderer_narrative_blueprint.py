"""Tests for narrative blueprint renderer helpers."""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderers import (
    _chapter_end,
    _chapter_start,
    _chapter_value,
    _character_arc_span,
    _extract_mainline_nodes,
    _infer_blueprint_total_chapters,
    _normalize_tension_key,
)


def test_infer_blueprint_total_chapters_uses_all_timeline_fields() -> None:
    blueprint = {
        "narrative_phases": [{"chapter_start": 1, "chapter_end": 12}],
        "key_turning_points": [{"chapter_number": 48}],
        "character_arcs": [
            {"character": "A", "milestones": [{"chapter_start": 2, "chapter_end": 63}]}
        ],
        "subplot_plan": [
            {
                "involved_chapters": [5, 82],
                "chapter_events": [{"chapter_number": 77, "event": "节点"}],
                "weave_links": [{"trigger_chapter": 91, "target_subplot": "主线"}],
                "resolution_chapter": 88,
            }
        ],
        "suspense_schedule": [{"introduce_chapter": 4, "resolve_chapter": 95}],
    }

    assert _infer_blueprint_total_chapters(blueprint) == 95


def test_infer_blueprint_total_chapters_falls_back_without_phases() -> None:
    assert _infer_blueprint_total_chapters({"key_turning_points": [{"chapter_number": 82}]}) == 82
    assert _infer_blueprint_total_chapters({}) == 24


def test_chapter_helpers_accept_common_aliases_and_strings() -> None:
    assert _chapter_start({"start_chapter": "7"}) == 7
    assert _chapter_end({"end_chapter": "82"}) == 82
    assert _chapter_value({"chapter": "29"}) == 29


def test_normalize_tension_key_accepts_generated_labels() -> None:
    assert _normalize_tension_key("medium-high") == "高"
    assert _normalize_tension_key("critical") == "高潮"
    assert _normalize_tension_key("medium-resolving") == "回落"
    assert _normalize_tension_key("rising") == "渐升"


def test_extract_mainline_nodes_prefers_turning_points() -> None:
    nodes = _extract_mainline_nodes(
        {
            "narrative_phases": [{"phase_name": "第一幕", "chapter_end": 12}],
            "key_turning_points": [
                {"chapter_number": 9, "description": "真相露出一角", "location": "旧港"},
                {"chapter_number": 3, "description": "主角入局"},
            ],
        }
    )

    assert [node["chapter"] for node in nodes] == [3, 9]
    assert nodes[0]["label"] == "关键转折"
    assert nodes[0]["source"] == "turning_point"


def test_extract_mainline_nodes_falls_back_to_phase_endings() -> None:
    nodes = _extract_mainline_nodes(
        {
            "narrative_phases": [
                {"phase_name": "入局", "chapter_start": 1, "chapter_end": 8},
                {"phase_name": "终局", "chapter_start": 9, "chapter_end": 16},
            ]
        }
    )

    assert [(node["label"], node["chapter"], node["source"]) for node in nodes] == [
        ("入局", 8, "phase"),
        ("终局", 16, "phase"),
    ]


def test_character_arc_span_uses_milestone_range() -> None:
    arc = {
        "character": "A",
        "milestones": [
            {"chapter_start": 9, "chapter_end": 12},
            {"chapter_start": 3, "chapter_end": 5},
            {"chapter_number": 24},
        ],
    }

    assert _character_arc_span(arc, 30) == (3, 24)


def test_character_arc_span_falls_back_to_whole_story() -> None:
    assert _character_arc_span({"character": "A", "milestones": []}, 82) == (1, 82)
