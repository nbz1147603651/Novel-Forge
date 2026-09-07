"""Tests for init-time narrative contract persistence."""

from __future__ import annotations

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_service import (
    _build_and_persist_narrative_contract,
    _seed_canon_from_character_bible,
)
from novel_forge.story_kernel.schemas import StoryKernel


def test_narrative_contract_persists_continuity_protocol(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("init_contract"))
    layout.ensure_dirs()

    story_bible = StoryBible(
        title="Test",
        premise="Premise",
        era="Era",
        geography="City",
        culture="Culture",
        magic_or_tech="None",
        rules=["Rule A", "Rule B"],
        tone="tense",
        themes=["truth"],
    )
    character_bible = CharacterBible(
        characters=[
            CharacterProfile(name="A", role="protagonist", arc="grow up"),
            CharacterProfile(name="B", role="antagonist", arc="fall down"),
        ]
    )

    _build_and_persist_narrative_contract(
        storage=tmp_storage,
        layout=layout,
        story_bible=story_bible,
        character_bible=character_bible,
        blueprint_data={"ending_strategy": "bittersweet", "key_turning_points": []},
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    assert "continuity_protocol" in contract
    assert contract["continuity_protocol"]["location_transition_required"] is True
    assert contract["continuity_protocol"]["traditional_time_ke_range"] == "一至四刻"


def test_canon_seed_includes_narrative_contract_plot_threads() -> None:
    story_bible = StoryBible(
        title="Test",
        premise="Premise",
        rules=["Rule A"],
    )
    character_bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="A",
                gender="女",
                relationships={"B": "旧识"},
            ),
            CharacterProfile(name="B", gender="男"),
        ]
    )
    canon_state = StoryKernel(project_id="seed_demo")

    seeded_canon = _seed_canon_from_character_bible(
        canon_state,
        character_bible,
        story_bible,
        narrative_contract={
            "plot_threads": [
                {
                    "thread_id": "thread_ring",
                    "thread_name": "银镯刻字",
                    "description": "信物串联误认与真相。",
                    "chapters": "2、5、9",
                    "resolution": "第 9 章兑现",
                }
            ],
            "promise_plan": [
                {
                    "promise_id": "promise_father",
                    "promise": "父殇阴影",
                    "setup_chapter": 1,
                    "payoff_chapter": 12,
                    "payoff_rule": "第 12 章揭示父亲死亡真相。",
                }
            ],
        },
    )

    assert canon_state.entities == []
    assert canon_state.relationships == []
    assert canon_state.world_facts == {}
    assert canon_state.plot_threads == []

    # Verify characters seeded into entities list
    char_entities = {e.name: e for e in seeded_canon.entities if e.entity_type == "character"}
    assert "A" in char_entities
    assert char_entities["A"].attributes.get("gender") == "女"
    # Verify relationships seeded
    rel_ids = {r.relationship_id for r in seeded_canon.relationships}
    assert any("A" in rid and "B" in rid for rid in rel_ids)
    assert seeded_canon.world_facts.get("world_rule_1") == "Rule A"
    # Verify plot threads seeded into list
    thread_by_id = {t.thread_id: t for t in seeded_canon.plot_threads}
    assert "thread_ring" in thread_by_id
    assert thread_by_id["thread_ring"].title == "银镯刻字"
    assert thread_by_id["thread_ring"].last_touched_chapter == 0
    assert "promise_father" in thread_by_id
    assert thread_by_id["promise_father"].next_payoff_window == "1-12"
    assert seeded_canon.foreshadowing == []


def test_narrative_contract_persists_world_context(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("init_contract_world_context"))
    layout.ensure_dirs()

    story_bible = StoryBible(
        title="Test",
        premise="Premise",
        era="武周末年",
        geography="长安",
        culture="士族与寒门对峙",
        magic_or_tech="无超自然",
        rules=["朝堂行动需符合礼法"],
        tone="tense",
        themes=["truth"],
        social_hierarchy="皇权、内廷、外朝、士族门第层级分明",
        address_rules=["臣下面圣称陛下，不直呼帝王名讳"],
        self_reference_rules=["官员在朝堂以臣自称，私下可用我"],
        etiquette_rules=["越级进言必须有召见或紧急军情支撑"],
        institution_terms=["鸾台", "御史台"],
        material_culture=["笏板", "铜鱼符"],
        anachronism_blacklist=["手机", "打卡"],
        dialogue_register_rules=["朝堂对白庄重克制，私谈可略白话"],
    )
    character_bible = CharacterBible(
        characters=[CharacterProfile(name="A", role="protagonist", arc="grow up")]
    )

    _build_and_persist_narrative_contract(
        storage=tmp_storage,
        layout=layout,
        story_bible=story_bible,
        character_bible=character_bible,
        blueprint_data={"ending_strategy": "bittersweet", "key_turning_points": []},
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    world_context = contract["world_context"]
    assert world_context["social_hierarchy"] == "皇权、内廷、外朝、士族门第层级分明"
    assert world_context["address_rules"] == ["臣下面圣称陛下，不直呼帝王名讳"]
    assert world_context["anachronism_blacklist"] == ["手机", "打卡"]


def test_narrative_contract_protocol_supports_blueprint_overrides(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("init_contract_override"))
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
            "continuity_protocol": {
                "location_transition_window_sentences": 5,
                "bridge_echo_window_chars": 1200,
            }
        },
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    protocol = contract["continuity_protocol"]
    assert protocol["location_transition_window_sentences"] == 5
    assert protocol["bridge_echo_window_chars"] == 1200


def test_narrative_contract_protocol_is_language_aware(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("init_contract_en"))
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
        blueprint_data=None,
        language="en",
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    protocol = contract["continuity_protocol"]
    assert protocol["time_notation_profile"] == "locale_default"
    assert "traditional_time_ke_range" not in protocol


def test_narrative_contract_protocol_uses_runtime_defaults(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("init_contract_runtime_defaults"))
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
        blueprint_data=None,
        words_per_chapter=3000,
        continuity_defaults={
            "location_transition_window_sentences": 4,
            "bridge_echo_ratio": 0.20,
            "bridge_echo_min_chars": 600,
            "bridge_echo_max_chars": 1000,
            "time_notation_profile": "traditional_cn",
        },
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    protocol = contract["continuity_protocol"]
    assert protocol["location_transition_window_sentences"] == 4
    assert protocol["bridge_echo_window_chars"] == 600  # 3000*0.2 clipped by min=600
    assert protocol["time_notation_profile"] == "traditional_cn"
    assert protocol["traditional_time_ke_range"] == "一至四刻"


def test_narrative_contract_protocol_can_be_disabled(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("init_contract_disabled"))
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
        blueprint_data=None,
        continuity_defaults={"enabled": False},
    )

    contract = tmp_storage.load_json(layout.narrative_contract_path)
    assert "continuity_protocol" not in contract
