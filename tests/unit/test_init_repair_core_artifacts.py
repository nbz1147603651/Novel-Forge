from __future__ import annotations

import asyncio

import pytest

from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.narrative_state.schemas import EntityRegistry
from novel_forge.pipeline.long.services.init_repair import (
    InitArtifact,
    InitRepairContext,
    InitRepairOrchestrator,
    get_init_repair_policy,
)
from novel_forge.pipeline.long.services.init_repair.policies import (
    CharacterBibleRepairPolicy,
    EntityRegistryRepairPolicy,
    StoryBibleRepairPolicy,
)


def _spec() -> StorySpec:
    return StorySpec(
        title="星河试炼",
        genre="fantasy",
        theme="少年在星河学院寻找失落誓约。",
        tone="明亮",
        characters_hint="林澈、沈微",
        world_hint="星河学院、旧观测塔",
    )


def test_core_artifact_repair_policies_are_registered() -> None:
    assert isinstance(get_init_repair_policy(InitArtifact.STORY_BIBLE), StoryBibleRepairPolicy)
    assert isinstance(
        get_init_repair_policy(InitArtifact.CHARACTER_BIBLE),
        CharacterBibleRepairPolicy,
    )
    assert isinstance(
        get_init_repair_policy(InitArtifact.ENTITY_REGISTRY),
        EntityRegistryRepairPolicy,
    )
    with pytest.raises(KeyError):
        get_init_repair_policy(InitArtifact.OUTLINE)


def test_story_bible_policy_backfills_from_spec() -> None:
    policy = get_init_repair_policy(InitArtifact.STORY_BIBLE)
    outcome = asyncio.run(
        InitRepairOrchestrator(policy).repair(
            {"premise": ""},
            InitRepairContext(
                service_ctx=None,
                outline_ctx={},
                total_chapters=12,
                artifacts={"spec": _spec()},
            ),
        )
    )

    assert outcome.report.is_valid
    assert outcome.repaired is True
    assert isinstance(outcome.payload, StoryBible)
    assert outcome.payload.premise == _spec().theme
    assert outcome.payload.geography == _spec().world_hint


def test_character_bible_policy_backfills_protagonist_from_spec_hint() -> None:
    policy = get_init_repair_policy(InitArtifact.CHARACTER_BIBLE)
    outcome = asyncio.run(
        InitRepairOrchestrator(policy).repair(
            {"characters": []},
            InitRepairContext(
                service_ctx=None,
                outline_ctx={},
                total_chapters=12,
                artifacts={"spec": _spec()},
            ),
        )
    )

    assert outcome.report.is_valid
    assert outcome.repaired is True
    assert isinstance(outcome.payload, CharacterBible)
    assert outcome.payload.characters[0].name == "林澈"
    assert outcome.payload.characters[0].role == "protagonist"


def test_entity_registry_policy_derives_entities_from_bibles() -> None:
    character_bible = CharacterBible.model_validate(
        {
            "characters": [
                {"name": "林澈", "role": "protagonist"},
                {"name": "沈微", "role": "supporting"},
            ]
        }
    )
    story_bible = StoryBible.model_validate(
        {
            "premise": "星河学院的誓约试炼。",
            "geography": "星河学院、旧观测塔",
        }
    )
    policy = get_init_repair_policy(InitArtifact.ENTITY_REGISTRY)

    outcome = asyncio.run(
        InitRepairOrchestrator(policy).repair(
            {"entities": []},
            InitRepairContext(
                service_ctx=None,
                outline_ctx={},
                total_chapters=12,
                artifacts={
                    "character_bible": character_bible,
                    "story_bible": story_bible,
                },
            ),
        )
    )

    assert outcome.report.is_valid
    assert outcome.repaired is True
    assert isinstance(outcome.payload, EntityRegistry)
    names = {entity.name for entity in outcome.payload.entities}
    assert {"林澈", "沈微", "星河学院", "旧观测塔"}.issubset(names)
