"""Tests for subplot chapter-events normalization."""

from __future__ import annotations

from novel_forge.core.schemas.outline import SubplotChapterEvent, SubplotPlan
from novel_forge.pipeline.long.services.blueprint.blueprint_validation import validate_blueprint


def test_subplot_plan_synthesizes_empty_events_from_involved_chapters() -> None:
    subplot = SubplotPlan.model_validate(
        {
            "name": "朝堂线",
            "description": "朝堂博弈",
            "involved_chapters": [9, 3, 3, 1],
        }
    )

    assert subplot.involved_chapters == [1, 3, 9]
    assert [item.chapter_number for item in subplot.chapter_events] == [1, 3, 9]
    assert all(item.event == "" for item in subplot.chapter_events)


def test_subplot_plan_normalizes_chapter_events_and_drops_invalid_values() -> None:
    subplot = SubplotPlan.model_validate(
        {
            "name": "商路线",
            "involved_chapters": [2, 5],
            "chapter_events": [
                {"chapter_number": 5, "event": "在市集完成第一笔交易"},
                {"chapter_number": 2, "event": "主角发现黑市线索"},
                {"chapter_number": 2, "event": "重复章节应被去重"},
            ],
        }
    )

    assert subplot.involved_chapters == [2, 5]
    assert [item.chapter_number for item in subplot.chapter_events] == [2, 5]
    assert subplot.chapter_events[0].event == "主角发现黑市线索"
    assert subplot.chapter_events[1].event == "在市集完成第一笔交易"


def test_subplot_chapter_event_coerces_single_dependency_string() -> None:
    event = SubplotChapterEvent.model_validate(
        {
            "chapter_number": 33,
            "event": "林绾绾的情感选择反向推动主线",
            "depends_on": "林绾绾的感情线:33",
        }
    )

    assert event.depends_on == ["林绾绾的感情线:33"]


def test_subplot_chapter_event_coerces_delimited_dependency_string() -> None:
    event = SubplotChapterEvent.model_validate(
        {
            "chapter_number": 12,
            "event": "多线交汇",
            "depends_on": "身世谜团线:8；黑市线:10、盟约线:11",
        }
    )

    assert event.depends_on == ["身世谜团线:8", "黑市线:10", "盟约线:11"]


def test_subplot_plan_backfills_partial_events_for_orphan_involved_chapters() -> None:
    """Regression test for 弈心锁玉/府兵制改革 bug.

    When ``chapter_events`` is partial (some but not all involved_chapters are
    represented), the schema must backfill empty events for the orphan
    chapters so the timeline visualisation has a dot at the line endpoint.
    The fix must NOT remove any of the pre-existing user-supplied events.
    """
    subplot = SubplotPlan.model_validate(
        {
            "name": "府兵制改革",
            "involved_chapters": [5, 10, 15, 95, 100, 110, 115],
            "chapter_events": [
                {"chapter_number": 5, "event": "首次提出府兵制构想"},
                {"chapter_number": 95, "event": "全国落地完成"},
            ],
        }
    )

    event_chapters = [item.chapter_number for item in subplot.chapter_events]
    # Existing events preserved
    assert 5 in event_chapters
    assert 95 in event_chapters
    assert subplot.chapter_events[event_chapters.index(5)].event == "首次提出府兵制构想"
    assert subplot.chapter_events[event_chapters.index(95)].event == "全国落地完成"
    # Orphan chapters (10, 15, 100, 110, 115) backfilled with empty event text
    assert set(subplot.involved_chapters).issubset(event_chapters)
    for chapter in (10, 15, 100, 110, 115):
        idx = event_chapters.index(chapter)
        assert subplot.chapter_events[idx].event == ""
        # Weave notes must mark the auto-synthesized provenance so a follow-up
        # repair pass can recognise and replace it with a real event if desired.
        assert "auto-synthesized" in subplot.chapter_events[idx].weave_notes


def test_subplot_plan_partial_backfill_is_idempotent() -> None:
    """Re-validating an already-backfilled subplot must not duplicate events."""
    subplot_a = SubplotPlan.model_validate(
        {
            "name": "X",
            "involved_chapters": [1, 2, 3, 4, 5],
            "chapter_events": [{"chapter_number": 1, "event": "A"}],
        }
    )
    dump = subplot_a.model_dump(mode="json")
    subplot_b = SubplotPlan.model_validate(dump)
    chapters_b = [item.chapter_number for item in subplot_b.chapter_events]
    assert chapters_b == sorted(set(chapters_b))
    assert sorted(chapters_b) == [1, 2, 3, 4, 5]


def test_validate_blueprint_passes_after_schema_backfills_tail_orphans() -> None:
    """Once the schema backfills orphan events, validation sees a consistent
    subplot and emits no orphan-related warnings. This pins the contract that
    the schema is the single source of truth for the
    events-superset-of-involved invariant.
    """
    from novel_forge.core.schemas.outline import NarrativeBlueprint

    blueprint = NarrativeBlueprint.model_validate(
        {
            "synopsis": "测试蓝图",
            "volume_mode": False,
            "volumes": [],
            "narrative_phases": [
                {"phase_name": "起", "chapter_start": 1, "chapter_end": 60, "description": "起"},
                {"phase_name": "合", "chapter_start": 61, "chapter_end": 120, "description": "合"},
            ],
            "key_turning_points": [
                {
                    "chapter_number": 5,
                    "description": "起",
                    "location": "",
                    "characters_involved": [],
                },
            ],
            "subplot_plan": [
                {
                    "name": "府兵制改革",
                    "description": "test",
                    "involved_chapters": [5, 10, 15, 95, 100, 110, 115],
                    "chapter_events": [
                        {"chapter_number": 5, "event": "A"},
                        {"chapter_number": 95, "event": "B"},
                    ],
                    "weave_links": [
                        {
                            "source_type": "main_plot",
                            "source_ref": "主线",
                            "target_subplot": "府兵制改革",
                            "trigger_chapter": 5,
                            "link_type": "trigger_start",
                            "description": "trigger",
                        },
                        {
                            "source_type": "subplot",
                            "source_ref": "府兵制改革",
                            "target_subplot": "主线",
                            "trigger_chapter": 95,
                            "link_type": "feed_main",
                            "description": "feedback",
                        },
                    ],
                    "priority": "primary",
                    "resolution_chapter": 95,
                    "resolution_target": "main_turning_point:1",
                    "resolution_type": "resolve",
                },
            ],
            "suspense_schedule": [
                {
                    "suspense_id": "s1",
                    "suspense_type": "mystery",
                    "introduce_chapter": 5,
                    "resolve_chapter": 95,
                    "description": "d",
                    "urgency_level": "normal",
                    "related_subplot": "府兵制改革",
                    "strand_affinity": {"quest": 0.4, "fire": 0.1, "constellation": 0.5},
                },
            ],
            "ending_strategy": "end",
        }
    )

    sp = blueprint.subplot_plan[0]
    event_chapters = {e.chapter_number for e in sp.chapter_events}
    # Schema backfill has run; the 5 orphan chapters now have matching events.
    assert {10, 15, 100, 110, 115}.issubset(event_chapters)

    result = validate_blueprint(blueprint, total_chapters=120, narrative_complexity="standard")
    orphan_warnings = [w for w in result.warnings if "尾段" in w or "chapter_event" in w]
    assert orphan_warnings == []
