"""Policy registry for initialization repair."""

from __future__ import annotations

from collections.abc import Callable

from novel_forge.pipeline.long.services.init_repair.models import InitArtifact
from novel_forge.pipeline.long.services.init_repair.orchestrator import InitArtifactRepairPolicy
from novel_forge.pipeline.long.services.init_repair.policies import (
    BlueprintRepairPolicy,
    ChapterContractsRepairPolicy,
    CharacterBibleRepairPolicy,
    EntityRegistryRepairPolicy,
    ScenePlanRepairPolicy,
    StoryBibleRepairPolicy,
)

_POLICY_FACTORIES: dict[InitArtifact, Callable[[], InitArtifactRepairPolicy]] = {
    InitArtifact.BLUEPRINT: BlueprintRepairPolicy,
    InitArtifact.STORY_BIBLE: StoryBibleRepairPolicy,
    InitArtifact.CHARACTER_BIBLE: CharacterBibleRepairPolicy,
    InitArtifact.CHAPTER_CONTRACTS: ChapterContractsRepairPolicy,
    InitArtifact.SCENE_PLAN: ScenePlanRepairPolicy,
    InitArtifact.ENTITY_REGISTRY: EntityRegistryRepairPolicy,
}


def get_init_repair_policy(artifact: InitArtifact) -> InitArtifactRepairPolicy:
    """Return the repair policy for an initialization artifact."""
    try:
        factory = _POLICY_FACTORIES[artifact]
    except KeyError as exc:
        raise KeyError(f"No init repair policy registered for artifact: {artifact.value}") from exc
    return factory()
