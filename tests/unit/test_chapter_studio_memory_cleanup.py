"""Tests for chapter-studio memory cleanup filters."""

from __future__ import annotations

from novel_forge.desktop.pages.chapter_studio.page import (
    _filter_episodic_index_payload,
    _filter_outline_payload,
)


def test_filter_episodic_index_payload_prunes_invalidated_chapters() -> None:
    payload = {
        "index": {
            "sig_1": {"chapter_number": 1, "event_summary": "old-1"},
            "sig_2": {"chapter_number": "2", "event_summary": "old-2"},
            "sig_3": {"chapter_number": 3, "event_summary": "stale-3"},
            "sig_bad": {"chapter_number": "x", "event_summary": "broken"},
        },
        "chapter_events": {
            "1": ["sig_1"],
            "2": ["sig_2", "sig_missing"],
            "3": ["sig_3"],
            "invalid": ["sig_1"],
        },
        "outline_data": {
            "outline_index": {
                "ol_1": {"chapter_number": 1, "plot_point": "p1"},
                "ol_3": {"chapter_number": 3, "plot_point": "p3"},
            },
            "chapter_outlines": {
                "1": ["ol_1"],
                "3": ["ol_3"],
            },
            "relationships": {
                "A___B": {
                    "r1": {"chapter": 1, "change_type": "trust_up"},
                    "r3": {"chapter": 3, "change_type": "trust_down"},
                }
            },
            "theme_tracker": {"theme_x": [1, 3]},
            "unresolved_questions": ["第1章悬念", "第3章悬念", "未标章问题"],
        },
    }

    filtered = _filter_episodic_index_payload(payload, from_chapter=3)

    assert set(filtered["index"].keys()) == {"sig_1", "sig_2"}
    assert filtered["chapter_events"] == {"1": ["sig_1"], "2": ["sig_2"]}

    outline_data = filtered["outline_data"]
    assert set(outline_data["outline_index"].keys()) == {"ol_1"}
    assert outline_data["chapter_outlines"] == {"1": ["ol_1"]}
    assert set(outline_data["relationships"]["A___B"].keys()) == {"r1"}
    assert outline_data["theme_tracker"] == {"theme_x": [1]}
    assert outline_data["unresolved_questions"] == ["第1章悬念", "未标章问题"]


def test_filter_outline_payload_from_first_chapter_clears_chapter_bound_data() -> None:
    outline_payload = {
        "outline_index": {
            "ol_1": {"chapter_number": 1, "plot_point": "p1"},
            "ol_2": {"chapter_number": 2, "plot_point": "p2"},
        },
        "chapter_outlines": {"1": ["ol_1"], "2": ["ol_2"]},
        "relationships": {"A___B": {"r1": {"chapter": 1, "change_type": "trust_up"}}},
        "theme_tracker": {"theme_x": [1, 2]},
        "unresolved_questions": ["第1章悬念", "全局问题"],
    }

    filtered = _filter_outline_payload(outline_payload, from_chapter=1)

    assert filtered["outline_index"] == {}
    assert filtered["chapter_outlines"] == {}
    assert filtered["relationships"] == {}
    assert filtered["theme_tracker"] == {}
    # Chapter-bound question removed; non chapter-specific text kept.
    assert filtered["unresolved_questions"] == ["全局问题"]

