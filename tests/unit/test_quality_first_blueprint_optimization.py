"""Regression tests for quality-first blueprint generation helpers."""

from __future__ import annotations

from novel_forge.pipeline.long.services.init.init_service import (
    _blueprint_spine_budget,
    _compact_blueprint_prompt_snapshot,
    _partial_blueprint_validation_report,
)


def test_blueprint_spine_budget_scales_with_complexity() -> None:
    standard_target, standard_min = _blueprint_spine_budget(
        total_chapters=80,
        narrative_complexity="standard",
    )
    epic_target, epic_min = _blueprint_spine_budget(
        total_chapters=80,
        narrative_complexity="epic",
    )

    assert standard_target >= 11200
    assert standard_min >= 6144
    assert epic_target > standard_target
    assert epic_min >= standard_min


def test_compact_blueprint_prompt_snapshot_preserves_anchors_without_full_payload() -> None:
    fragments = {
        "synopsis": "主线因果" * 400,
        "subplot_plan": [
            {
                "name": "记忆追索线",
                "description": "通过失物找回真实记忆",
                "priority": "primary",
                "involved_chapters": list(range(1, 41)),
                "chapter_events": [
                    {
                        "chapter_number": index,
                        "event": f"event-{index}-" + "细节" * 80,
                        "weave_notes": "反哺主线" * 40,
                    }
                    for index in range(1, 21)
                ],
                "weave_links": [
                    {
                        "source_type": "main_plot",
                        "source_ref": f"main-{index}",
                        "target_subplot": "记忆追索线",
                        "trigger_chapter": index,
                        "link_type": "trigger_start" if index == 1 else "feed_main",
                        "description": "交织说明" * 40,
                    }
                    for index in range(1, 21)
                ],
                "resolution_chapter": 40,
                "resolution_target": "main_turning_point:4",
                "resolution_type": "reveal",
            }
        ],
    }

    snapshot = _compact_blueprint_prompt_snapshot(fragments, fragment_key="suspense")
    subplot = snapshot["anchors"]["subplot_plan"][0]

    assert snapshot["snapshot_mode"] == "compact_causal_anchors"
    assert snapshot["available_fields"] == ["synopsis", "subplot_plan"]
    assert len(snapshot["anchors"]["synopsis"]) <= 500
    assert subplot["involved_chapters"]["count"] == 40
    assert len(subplot["involved_chapters"]["sample"]) == 18
    assert len(subplot["chapter_events"]) == 8
    assert len(subplot["weave_links"]) == 8


def test_partial_blueprint_validation_report_filters_to_current_fragment() -> None:
    report = _partial_blueprint_validation_report(
        {
            "narrative_phases": [
                {
                    "phase_name": "开端",
                    "chapter_start": 1,
                    "chapter_end": 12,
                    "description": "建立主线",
                }
            ],
            "character_arcs": [
                {
                    "character": "沈知微",
                    "arc_summary": "从旁观到主动选择",
                    "milestones": [
                        {
                            "chapter_start": 13,
                            "chapter_end": 14,
                            "description": "越界的里程碑",
                        }
                    ],
                }
            ],
        },
        block_key="character_arcs",
        total_chapters=12,
        narrative_complexity="standard",
    )

    assert report["status"] == "issues_found"
    assert any("角色弧光" in item for item in report["errors"])
    assert all("支线数量不足" not in item for item in report["errors"])
