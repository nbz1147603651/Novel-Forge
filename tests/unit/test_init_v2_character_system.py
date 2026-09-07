"""Tests for V2 initialization character and entity artifacts."""

from __future__ import annotations

from novel_forge.core.domain.character_relationships import iter_character_relationship_projections
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.narrative_state.schemas import EntityRegistry
from novel_forge.pipeline.long.services.init.init_v2 import (
    InitV2BlockCache,
    build_character_system,
    build_creative_director_packet,
    build_entity_graph,
    build_relationship_prompt_overview,
    hash_payload,
    project_character_bible,
)


def test_character_system_filters_non_character_relationships() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈念卿",
                role="protagonist",
                gender="女",
                relationships={
                    "陆云峥": "职场对手到命定羁绊",
                    "金镯": "前世记忆触发物",
                    "陆云峥/陆少爷": "今生与前世称谓混杂",
                },
            ),
            CharacterProfile(name="陆云峥", role="deuteragonist", gender="男"),
        ]
    )

    system = build_character_system(bible)
    projected = project_character_bible(bible, system)

    assert [(edge.source_name, edge.target_name) for edge in system.relationship_edges] == [
        ("沈念卿", "陆云峥")
    ]
    assert projected.characters[0].relationships == {"陆云峥": "职场对手到命定羁绊"}
    assert {issue.code for issue in system.audit} == {
        "non_character_relationship_target",
        "compound_relationship_key",
    }


def test_relationship_projection_keeps_only_character_targets() -> None:
    profiles = [
        CharacterProfile(
            name="沈念卿",
            relationships={"陆云峥": "职场对手", "金镯": "前世记忆触发物"},
        ),
        CharacterProfile(name="陆云峥"),
    ]

    projections = iter_character_relationship_projections(profiles)

    assert [(item.source_name, item.target_name, item.description) for item in projections] == [
        ("沈念卿", "陆云峥", "职场对手")
    ]


def test_character_system_uses_llm_relationship_matrix_relation_types() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈知微",
                role="protagonist",
                relationships={
                    "林晚": "间接触发与被触发的关系。她无意触发心动主线，但二人没有情感张力。",
                    "陈默": "同门师兄妹与事业合伙人关系。",
                },
            ),
            CharacterProfile(name="林晚", role="supporting"),
            CharacterProfile(name="陈默", role="supporting"),
        ]
    )
    matrix = [
        {
            "character_a": "沈知微",
            "character_b": "林晚",
            "relation_type": "relationship",
            "description": "信息触发与被触发关系，不是情感线。",
            "confidence": 0.94,
        },
        {
            "character_a": "沈知微",
            "character_b": "陈默",
            "relation_type": "professional",
            "description": "同门合伙经营知微堂。",
            "confidence": 0.91,
        },
    ]

    system = build_character_system(bible, relationship_matrix=matrix)
    by_pair = {
        frozenset((edge.source_name, edge.target_name)): edge for edge in system.relationship_edges
    }

    assert by_pair[frozenset(("沈知微", "林晚"))].relation_type == "relationship"
    assert by_pair[frozenset(("沈知微", "陈默"))].relation_type == "professional"
    assert by_pair[frozenset(("沈知微", "陈默"))].source == "relationship_matrix"
    assert by_pair[frozenset(("沈知微", "林晚"))].confidence == 0.94


def test_llm_authored_identity_link_is_not_overwritten_by_local_keywords() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈念卿", role="protagonist", gender="女"),
            CharacterProfile(name="陆云峥", role="deuteragonist", gender="男"),
        ]
    )

    system = build_character_system(
        bible,
        relationship_matrix=[
            {
                "character_a": "沈念卿",
                "character_b": "陆云峥",
                "relation_type": "identity_link",
                "identity_link_type": "reincarnation_of",
                "description": "今生CP，前世为1923年上海滩相爱的顾锦绣与陆云峥，今生重逢后感情升华。",
            }
        ],
    )

    assert system.relationship_edges == []
    assert [
        (link.source_name, link.target_name, link.link_type) for link in system.identity_links
    ] == [("沈念卿", "陆云峥", "reincarnation_of")]


def test_direct_identity_link_matrix_entry_is_kept() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈念卿", role="protagonist", gender="女"),
            CharacterProfile(name="顾锦绣", role="minor", gender="女", status="retired"),
        ]
    )

    system = build_character_system(
        bible,
        relationship_matrix=[
            {
                "character_a": "沈念卿",
                "character_b": "顾锦绣",
                "relation_type": "identity_link",
                "identity_link_type": "reincarnation_of",
                "description": "沈念卿前世为顾锦绣，两者是同一灵魂的前世今生映射。",
            }
        ],
    )

    assert system.relationship_edges == []
    assert [
        (link.source_name, link.target_name, link.link_type) for link in system.identity_links
    ] == [("沈念卿", "顾锦绣", "reincarnation_of")]


def test_direct_identity_link_accepts_qualified_roster_names() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈念卿（现代）", role="protagonist", gender="女"),
            CharacterProfile(name="顾锦绣（过去）", role="minor", gender="女", status="retired"),
        ]
    )

    system = build_character_system(
        bible,
        relationship_matrix=[
            {
                "character_a": "沈念卿（现代）",
                "character_b": "顾锦绣（过去）",
                "relation_type": "identity_link",
                "identity_link_type": "reincarnation_of",
                "description": "沈念卿前世为顾锦绣，是跨越百年的身份映射。",
            }
        ],
    )

    assert system.relationship_edges == []
    assert [
        (link.source_name, link.target_name, link.link_type) for link in system.identity_links
    ] == [("沈念卿（现代）", "顾锦绣（过去）", "reincarnation_of")]


def test_character_profile_normalizes_legacy_role_status_aliases() -> None:
    profile = CharacterProfile(
        name="沈父",
        role="mentioned",
        age="已故",
        gender="男",
        status="deceased",
        time_layer="memory",
    )

    assert profile.role == "minor"
    assert profile.status == "retired"
    assert profile.time_layer == "memory_only"


def test_project_character_bible_backfills_llm_matrix_descriptions() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈知微", role="protagonist", relationships={}),
            CharacterProfile(name="陈默", role="supporting", relationships={}),
        ]
    )
    system = build_character_system(
        bible,
        relationship_matrix=[
            {
                "character_a": "沈知微",
                "character_b": "陈默",
                "relation_type": "professional",
                "description": "同门合伙经营知微堂。",
            }
        ],
    )

    projected = project_character_bible(bible, system)

    assert projected.characters[0].relationships == {"陈默": "同门合伙经营知微堂。"}
    assert projected.characters[1].relationships == {"沈知微": "同门合伙经营知微堂。"}


def test_relationship_prompt_overview_preserves_endpoints_and_priority() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈知微", role="protagonist"),
            CharacterProfile(name="陈默", role="supporting"),
            CharacterProfile(name="林晚", role="antagonist"),
        ]
    )
    system = build_character_system(
        bible,
        relationship_matrix=[
            {
                "character_a": "沈知微",
                "character_b": "陈默",
                "relation_type": "alliance",
                "description": "共同追查旧案。",
            },
            {
                "character_a": "沈知微",
                "character_b": "林晚",
                "relation_type": "antagonism",
                "description": "围绕证据互相阻挠。",
            },
        ],
    )

    overview = build_relationship_prompt_overview(system, limit=2)
    packet = build_creative_director_packet(
        story_bible=StoryBible(premise="旧案牵动三人。"),
        character_system=system,
    )

    assert overview[0] == "沈知微↔林晚：围绕证据互相阻挠。"
    assert overview[1] == "沈知微↔陈默：共同追查旧案。"
    assert packet.relationship_tensions[:2] == overview


def test_entity_graph_uses_llm_registry_type_and_keeps_aliases() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈念卿",
                role="protagonist",
                gender="女",
                relationships={
                    "陆云峥": "职场对手到命定羁绊",
                    "金镯": "前世记忆触发物",
                    "陆云峥/陆少爷": "今生与前世称谓混杂",
                },
            ),
            CharacterProfile(name="陆云峥", role="deuteragonist", gender="男"),
        ]
    )
    system = build_character_system(bible)

    registry, graph = build_entity_graph(
        registry=EntityRegistry.model_validate(
            {
                "entities": [
                    {
                        "entity_id": "item_gold_bangle",
                        "name": "金镯",
                        "entity_type": "item",
                        "source": "init_entity_registry",
                    }
                ]
            }
        ),
        character_system=system,
        original_character_bible=bible,
    )

    by_name = {entity.name: entity for entity in registry.entities}
    assert by_name["金镯"].entity_type == "item"
    assert "陆少爷" in by_name
    assert any(link.target_name == "金镯" for link in graph.entity_links)
    assert any(
        link.link_type == "alias_of" and link.source_name == "陆少爷" for link in graph.entity_links
    )


def test_entity_registry_accepts_dynamic_entity_key_records() -> None:
    registry = EntityRegistry.model_validate(
        {
            "entities": [
                {
                    "item_medical_journal": "医学期刊",
                    "entity_type": "item",
                    "aliases": ["期刊"],
                    "source": "init",
                    "notes": "周正阳开会必带，代表其唯科学论立场",
                },
                {
                    "item_acupuncture_needle": {
                        "name": "银针",
                        "entity_type": "item",
                        "notes": "筑信任亦为疗愈媒介",
                    }
                },
            ]
        }
    )

    by_id = registry.by_id()
    assert by_id["item_medical_journal"].name == "医学期刊"
    assert by_id["item_medical_journal"].aliases == ["期刊"]
    assert by_id["item_acupuncture_needle"].name == "银针"
    assert by_id["item_acupuncture_needle"].entity_type == "item"


def test_legacy_past_life_text_is_preserved_without_local_identity_inference() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈念卿",
                role="protagonist",
                gender="女",
                relationships={"顾锦绣": "前世今生映射，同一灵魂的记忆回声"},
            ),
            CharacterProfile(name="顾锦绣", role="supporting", gender="女"),
        ]
    )

    system = build_character_system(bible)
    projected = project_character_bible(bible, system)

    assert [(edge.source_name, edge.target_name) for edge in system.relationship_edges] == [
        ("沈念卿", "顾锦绣")
    ]
    assert system.identity_links == []
    assert projected.characters[0].relationships == {
        "顾锦绣": "前世今生映射，同一灵魂的记忆回声"
    }


def test_cross_life_romance_remains_character_relationship() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈念卿",
                role="protagonist",
                gender="女",
                relationships={"陆云峥": "命定的两世恋人，今生重逢后互相试探"},
            ),
            CharacterProfile(name="陆云峥", role="protagonist", gender="男"),
        ]
    )

    system = build_character_system(bible)
    projected = project_character_bible(bible, system)

    assert [(edge.source_name, edge.target_name) for edge in system.relationship_edges] == [
        ("沈念卿", "陆云峥")
    ]
    assert system.identity_links == []
    assert projected.characters[0].relationships == {"陆云峥": "命定的两世恋人，今生重逢后互相试探"}


def test_legacy_same_soul_text_remains_untyped_until_llm_matrix_exists() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="今生念卿",
                role="protagonist",
                gender="女",
                relationships={"民国念卿": "同一灵魂的前世身份映射"},
            ),
            CharacterProfile(name="民国念卿", role="supporting", gender="女"),
        ]
    )

    system = build_character_system(bible)
    projected = project_character_bible(bible, system)

    assert [(edge.source_name, edge.target_name) for edge in system.relationship_edges] == [
        ("今生念卿", "民国念卿")
    ]
    assert system.identity_links == []
    assert projected.characters[0].relationships == {
        "民国念卿": "同一灵魂的前世身份映射"
    }


def test_past_role_description_does_not_become_identity_link() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈鹤卿",
                role="supporting",
                relationships={"程砚秋": "程砚秋前世是沈鹤卿的技术总监，今生对他有熟悉感。"},
            ),
            CharacterProfile(name="程砚秋", role="supporting"),
        ]
    )

    system = build_character_system(bible)

    assert system.identity_links == []
    assert [(edge.source_name, edge.target_name) for edge in system.relationship_edges] == [
        ("沈鹤卿", "程砚秋")
    ]


def test_character_system_warns_about_isolated_active_characters() -> None:
    bible = CharacterBible(
        characters=[
            CharacterProfile(name="沈念卿", role="protagonist", relationships={"陆云峥": "核心CP"}),
            CharacterProfile(name="陆云峥", role="deuteragonist"),
            CharacterProfile(name="林素素", role="supporting", status="active"),
        ]
    )

    system = build_character_system(bible)

    isolated = [issue for issue in system.audit if issue.code == "isolated_active_character"]
    assert [(issue.character_name, issue.severity) for issue in isolated] == [("林素素", "warning")]


def test_time_layer_prefers_explicit_and_detects_cross_temporal_roles() -> None:
    explicit = CharacterBible(
        characters=[
            CharacterProfile(
                name="顾锦绣",
                role="supporting",
                gender="女",
                time_layer="memory_only",
                backstory="民国线女工，已在战火中牺牲。",
            )
        ]
    )
    inferred = CharacterBible(
        characters=[
            CharacterProfile(
                name="沈念卿",
                role="protagonist",
                gender="女",
                backstory="2025年上海职场精英，因民国前世记忆产生窒息感。",
            )
        ]
    )

    assert build_character_system(explicit).roster[0].time_layer == "memory_only"
    assert build_character_system(inferred).roster[0].time_layer == "cross_temporal"


def test_init_v2_cache_invalidates_on_upstream_change(tmp_path) -> None:
    cache = InitV2BlockCache(tmp_path)
    fingerprint = hash_payload({"project": "demo"})
    upstream = {"a": hash_payload({"value": 1})}

    cache.save_success(
        "demo_block",
        request_fingerprint=fingerprint,
        upstream_hashes=upstream,
        payload={"ok": True},
    )

    assert cache.load_success(
        "demo_block",
        request_fingerprint=fingerprint,
        upstream_hashes=upstream,
    ) == {"ok": True}
    assert (
        cache.load_success(
            "demo_block",
            request_fingerprint=fingerprint,
            upstream_hashes={"a": hash_payload({"value": 2})},
        )
        is None
    )
