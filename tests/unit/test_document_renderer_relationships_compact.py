"""Tests for compact relationship card rendering in chapter studio."""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderer.relationships import (
    render_relationship_compact,
)
from novel_forge.story_kernel.relationship_tracker import (
    RelationshipOverview,
    RelationshipSnapshot,
    RelationshipTimeline,
)


def test_compact_relationships_show_ongoing_states_when_no_current_delta() -> None:
    overview = RelationshipOverview(
        total_relationships=1,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="盟友",
                current_trust=0.78,
                current_tension=0.22,
                snapshots=[
                    RelationshipSnapshot(
                        chapter_number=1,
                        public_status="盟友",
                        trust=0.78,
                        tension=0.22,
                        dependency=0.40,
                        shift_event="初次并肩",
                    )
                ],
            )
        ],
    )

    html = render_relationship_compact(overview, current_chapter=5)

    assert "本章相关关系（状态延续）" in html
    assert "风伏京↔罗浮" in html
    assert "本章暂无关系变化" not in html


def test_compact_relationships_use_current_state_when_snapshot_missing() -> None:
    overview = RelationshipOverview(
        total_relationships=1,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="同盟但互相试探",
                current_trust=0.61,
                current_tension=0.48,
                snapshots=[],
            )
        ],
    )

    html = render_relationship_compact(overview, current_chapter=5)

    assert "本章相关关系（状态延续）" in html
    assert "同盟但互相试探" in html
    assert "信任61%" in html
    assert "张力48%" in html


def test_compact_relationships_do_not_mark_unchanged_snapshot_as_delta() -> None:
    overview = RelationshipOverview(
        total_relationships=1,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="盟友",
                current_trust=0.70,
                current_tension=0.30,
                snapshots=[
                    RelationshipSnapshot(
                        chapter_number=4,
                        public_status="盟友",
                        trust=0.70,
                        tension=0.30,
                        dependency=0.40,
                        shift_event="",
                    ),
                    RelationshipSnapshot(
                        chapter_number=5,
                        public_status="盟友",
                        trust=0.70,
                        tension=0.30,
                        dependency=0.40,
                        shift_event="",
                    ),
                ],
            )
        ],
    )

    html = render_relationship_compact(overview, current_chapter=5)

    assert "本章关系变化" not in html
    assert "本章相关关系（状态延续）" in html


def test_compact_relationships_still_show_current_chapter_delta() -> None:
    overview = RelationshipOverview(
        total_relationships=1,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="敌对",
                current_trust=0.20,
                current_tension=0.82,
                snapshots=[
                    RelationshipSnapshot(
                        chapter_number=4,
                        public_status="盟友",
                        trust=0.72,
                        tension=0.28,
                        dependency=0.35,
                        shift_event="",
                    ),
                    RelationshipSnapshot(
                        chapter_number=5,
                        public_status="敌对",
                        trust=0.20,
                        tension=0.82,
                        dependency=0.35,
                        shift_event="误会升级",
                    ),
                ],
            )
        ],
    )

    html = render_relationship_compact(overview, current_chapter=5)

    assert "本章关系变化" in html
    assert "误会升级" in html


def test_compact_relationships_prioritize_context_matched_pairs() -> None:
    overview = RelationshipOverview(
        total_relationships=2,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="盟友",
                current_trust=0.76,
                current_tension=0.26,
                snapshots=[],
            ),
            RelationshipTimeline(
                pair_id="萧临__沈砚",
                character_a="萧临",
                character_b="沈砚",
                current_status="试探",
                current_trust=0.48,
                current_tension=0.62,
                snapshots=[],
            ),
        ],
    )

    html = render_relationship_compact(
        overview,
        current_chapter=5,
        chapter_context_texts=[
            "本章目标：风伏京在宫变余波中与罗浮对齐立场",
            "本章提要：两人夜谈，确认后续合作。",
        ],
    )

    assert "本章相关关系（按本章上下文匹配）" in html
    assert "风伏京↔罗浮" in html
    assert "萧临↔沈砚" not in html
    assert "已匹配 1 对本章相关关系" in html


def test_compact_relationships_fallback_when_no_context_match() -> None:
    overview = RelationshipOverview(
        total_relationships=2,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="盟友",
                current_trust=0.76,
                current_tension=0.26,
                snapshots=[],
            ),
            RelationshipTimeline(
                pair_id="萧临__沈砚",
                character_a="萧临",
                character_b="沈砚",
                current_status="试探",
                current_trust=0.48,
                current_tension=0.62,
                snapshots=[],
            ),
        ],
    )

    html = render_relationship_compact(
        overview,
        current_chapter=5,
        chapter_context_texts=["本章聚焦边境粮道与军需问题。"],
    )

    assert "本章相关关系（状态延续）" in html
    assert "风伏京↔罗浮" in html
    assert "萧临↔沈砚" in html


def test_compact_relationships_prefer_focus_characters_over_context() -> None:
    overview = RelationshipOverview(
        total_relationships=2,
        timelines=[
            RelationshipTimeline(
                pair_id="风伏京__罗浮",
                character_a="风伏京",
                character_b="罗浮",
                current_status="盟友",
                current_trust=0.76,
                current_tension=0.26,
                snapshots=[],
            ),
            RelationshipTimeline(
                pair_id="萧临__沈砚",
                character_a="萧临",
                character_b="沈砚",
                current_status="试探",
                current_trust=0.48,
                current_tension=0.62,
                snapshots=[],
            ),
        ],
    )

    html = render_relationship_compact(
        overview,
        current_chapter=5,
        chapter_context_texts=[
            "本章提要：风伏京与罗浮试探合作。",
            "另外提到萧临。",
        ],
        focus_characters=["萧临", "沈砚"],
    )

    assert "本章相关关系（按本章计划角色）" in html
    assert "已匹配 1 对计划角色关系" in html
    assert "萧临↔沈砚" in html
    assert "风伏京↔罗浮" not in html
