"""Focused tests for narrative-contract plot thread normalization."""

from __future__ import annotations

from novel_forge.desktop.pages.document_renderer_reports import render_llm_narrative_contract
from novel_forge.pipeline.long.services.arc_liveness import build_arc_liveness_report
from novel_forge.pipeline.long.services.init.init_contract import (
    _build_and_persist_narrative_contract,
    build_adjudication_narrative_contract,
    normalize_llm_narrative_contract,
)


def test_normalize_llm_narrative_contract_derives_thread_chapters() -> None:
    payload = {
        "world_rules": [],
        "character_arcs": [],
        "plot_threads": [
            {
                "thread_name": "银镯刻字",
                "priority": "primary",
                "key_scenes": [
                    {"chapter": 3, "title": "发现刻字"},
                    {"chapter": 9, "title": "刻字反转"},
                ],
                "resolution": "第九章完成反转",
            }
        ],
    }

    normalized = normalize_llm_narrative_contract(payload)

    thread = normalized["plot_threads"][0]
    assert thread["chapters"] == "3、9"
    assert thread["chapter_numbers"] == [3, 9]


def test_normalize_llm_narrative_contract_prefers_explicit_range() -> None:
    normalized = normalize_llm_narrative_contract(
        {
            "world_rules": [],
            "character_arcs": [],
            "plot_threads": [
                {
                    "thread_name": "父殇阴影",
                    "setup_chapter": 1,
                    "payoff_chapter": 12,
                    "resolution": "真相公开",
                }
            ],
        }
    )

    assert normalized["plot_threads"][0]["chapters"] == "1-12"


def test_arc_liveness_scans_nested_llm_contract_plot_threads() -> None:
    report = build_arc_liveness_report(
        chapter_number=8,
        narrative_contract={
            "world_rules": [],
            "llm_contract": {
                "plot_threads": [
                    {"thread_name": "父殇阴影", "chapters": "1-12", "priority": "primary"}
                ]
            },
        },
        progression_ledger={"entries": [], "last_chapter": 0},
        window=3,
    )

    names = [item.name for item in report.dormant_arcs]
    assert "父殇阴影" in names


def test_llm_narrative_contract_renderer_falls_back_to_key_scene_chapters(qapp) -> None:
    widget = render_llm_narrative_contract(
        {
            "plot_threads": [
                {
                    "thread_name": "断供危机",
                    "key_scenes": [
                        {"chapter": 18, "title": "危机爆发", "description": "断供发生"},
                        {"chapter": 22, "title": "危机收束", "description": "供应恢复"},
                    ],
                    "resolution": "恢复供应",
                }
            ]
        }
    )

    plain_text = widget.toPlainText()
    assert "章节范围：第 18、22 章" in plain_text


def test_deterministic_contract_projects_to_adjudication_shape() -> None:
    projected = build_adjudication_narrative_contract(
        {
            "world_rules": ["医案必须有证据链"],
            "character_arcs": [{"name": "沈知微", "role": "protagonist", "arc": "从自证到共证"}],
            "plot_threads": [
                {
                    "thread_id": "silver_bangle",
                    "thread_name": "银镯刻字",
                    "chapter_numbers": [2, 5, 9],
                    "resolution": "第九章确认刻字来源",
                }
            ],
        }
    )

    assert projected["world_rules"][0]["rule_id"] == "WR001"
    assert projected["world_rules"][0]["content"] == "医案必须有证据链"
    assert projected["character_arcs"][0]["char_name"] == "沈知微"
    assert projected["plot_threads"][0]["chapters"] == "2、5、9"
    assert projected["promise_plan"][0]["setup_chapter"] == 2
    assert projected["promise_plan"][0]["payoff_chapter"] == 9


def test_deterministic_contract_seeds_blueprint_plot_threads(tmp_storage) -> None:
    from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
    from novel_forge.persistence.models import ProjectLayout

    layout = ProjectLayout(tmp_storage.project_dir("init_contract_plot_threads"))
    layout.ensure_dirs()
    story_bible = StoryBible(
        title="Test",
        premise="Premise",
        era="Era",
        geography="City",
        culture="Culture",
        magic_or_tech="None",
        rules=["Rule A"],
        tone="neutral",
        themes=["truth"],
    )
    character_bible = CharacterBible(
        characters=[CharacterProfile(name="A", role="protagonist", arc="grow up")]
    )

    _build_and_persist_narrative_contract(
        storage=tmp_storage,
        layout=layout,
        story_bible=story_bible,
        character_bible=character_bible,
        blueprint_data={
            "subplot_plan": [
                {
                    "name": "银镯刻字",
                    "description": "信物串联误认与真相。",
                    "involved_chapters": [2, 5],
                    "resolution_chapter": 9,
                    "chapter_events": [
                        {"chapter_number": 2, "event": "银镯出现"},
                        {"chapter_number": 9, "event": "刻字兑现"},
                    ],
                }
            ],
            "suspense_schedule": [
                {
                    "suspense_id": "父殇阴影",
                    "introduce_chapter": 1,
                    "resolve_chapter": 12,
                    "description": "父亲死亡真相延迟揭示。",
                }
            ],
        },
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    names = {item["thread_name"] for item in contract["plot_threads"]}
    assert {"银镯刻字", "父殇阴影"} <= names
    assert contract["plot_threads"][0]["chapters"] == "2、5、9"
    suspense = next(item for item in contract["plot_threads"] if item["thread_name"] == "父殇阴影")
    assert suspense["chapters"] == "1-12"
