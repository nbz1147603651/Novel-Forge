from __future__ import annotations

from novel_forge.narrative_state.schemas import EntityRegistry
from novel_forge.pipeline.long.services.init.init_service import (
    _deterministic_non_character_registry,
    _entity_registry_needs_llm_supplement,
    _merge_entity_registries,
    _supplemental_non_character_registry,
)


def test_deterministic_non_character_registry_covers_core_entity_types() -> None:
    registry = _deterministic_non_character_registry(
        {
            "main_location": "云城医馆",
            "key_item": "银杏叶书签",
            "themes": ["信任修复"],
        },
        {"organizations": ["旧案调查组"]},
    )

    by_type = {entity.entity_type: entity.name for entity in registry.entities}
    assert by_type["location"] == "云城医馆"
    assert by_type["item"] == "银杏叶书签"
    assert by_type["organization"] == "旧案调查组"
    assert by_type["concept"] == "信任修复"
    assert not _entity_registry_needs_llm_supplement(registry)


def test_deterministic_registry_does_not_inherit_ancestor_entity_hints() -> None:
    registry = _deterministic_non_character_registry(
        {
            "world_rule_book": {
                "rules": [
                    {
                        "rule_id": "R001",
                        "consequence": "需转介专业医疗机构",
                        "world_hint": "飘着淡艾草香",
                    }
                ]
            },
            "extension_elements": [{"rationale": "需要精确控制谁知道什么。"}],
            "main_location": "青瓦医馆",
            "themes": ["信任修复：这是一段很长的叙事性主题说明，不应整句进入实体库。"],
        }
    )

    names = {entity.name for entity in registry.entities}
    assert names == {"青瓦医馆", "信任修复"}
    assert {entity.source for entity in registry.entities} == {
        "deterministic_structured_entity_registry"
    }


def test_entity_registry_needs_llm_when_coverage_is_sparse() -> None:
    registry = _deterministic_non_character_registry({"main_location": "云城医馆"})

    assert _entity_registry_needs_llm_supplement(registry)


def test_entity_registry_needs_llm_when_unknown_semantic_slots_remain() -> None:
    registry = _deterministic_non_character_registry(
        {
            "main_location": "云城医馆",
            "key_item": "银杏叶书签",
            "themes": ["信任修复"],
        },
        {"organizations": ["旧案调查组"]},
    )
    registry = EntityRegistry.model_validate(
        {
            "entities": [
                *[entity.model_dump(mode="json") for entity in registry.entities],
                {
                    "entity_id": "ent_gold_bangle",
                    "name": "金镯",
                    "entity_type": "unknown",
                },
            ]
        }
    )

    assert _entity_registry_needs_llm_supplement(registry)


def test_llm_can_replace_unknown_baseline_entity_type() -> None:
    baseline = EntityRegistry.model_validate(
        {
            "entities": [
                {
                    "entity_id": "ent_gold_bangle",
                    "name": "金镯",
                    "entity_type": "unknown",
                }
            ]
        }
    )
    llm_registry = EntityRegistry.model_validate(
        {
            "entities": [
                {
                    "entity_id": "ent_gold_bangle",
                    "name": "金镯",
                    "entity_type": "item",
                    "source": "init_entity_registry",
                }
            ]
        }
    )

    supplemental = _supplemental_non_character_registry(
        llm_registry,
        baseline_registry=baseline,
    )
    merged = _merge_entity_registries(baseline, supplemental)

    assert merged.entities[0].entity_type == "item"


def test_merge_entity_registries_deduplicates_by_name() -> None:
    first = _deterministic_non_character_registry({"key_item": "银杏叶书签"})
    second = _deterministic_non_character_registry({"items": ["银杏叶书签"]})

    merged = _merge_entity_registries(first, second, EntityRegistry())

    assert [entity.name for entity in merged.entities] == ["银杏叶书签"]
