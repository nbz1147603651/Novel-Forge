"""Consistency checks across TaskType, prompt registry, contracts and tier defaults."""

from __future__ import annotations

from novel_forge.common.constants import ModelTier, TaskType
from novel_forge.core.format_contracts import _TASK_FORMAT_CONTRACTS
from novel_forge.core.task_catalog import DEFAULT_TASK_TIERS, ROUTING_GROUPS
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP

_NON_LLM_TASK_TYPES = {
    TaskType.TTS_BUILD_VOICE_TEAM,
    TaskType.TTS_SYNTHESIZE_SEGMENT,
    TaskType.TTS_ASSEMBLE_AUDIO,
}


def test_all_task_types_are_registered_in_prompt_registry() -> None:
    assert set(TaskType) - _NON_LLM_TASK_TYPES == set(_TASK_TEMPLATE_MAP)


def test_all_task_types_have_format_contracts() -> None:
    assert set(TaskType) - _NON_LLM_TASK_TYPES == set(_TASK_FORMAT_CONTRACTS)


def test_all_task_types_have_default_tiers() -> None:
    assert set(TaskType) - _NON_LLM_TASK_TYPES == set(DEFAULT_TASK_TIERS)


def test_routing_tasks_have_registry_contract_and_tier_entries() -> None:
    routing_tasks = {task.task_type for group in ROUTING_GROUPS for task in group.tasks}
    assert routing_tasks <= set(_TASK_TEMPLATE_MAP)
    assert routing_tasks <= set(_TASK_FORMAT_CONTRACTS)
    assert routing_tasks <= set(DEFAULT_TASK_TIERS)


def test_routing_subgroups_start_at_visible_tasks() -> None:
    for group in ROUTING_GROUPS:
        group_task_types = [task.task_type for task in group.tasks]
        subgroup_starts = [subgroup.start_task_type for subgroup in group.subgroups]
        assert subgroup_starts == list(dict.fromkeys(subgroup_starts))
        assert set(subgroup_starts) <= set(group_task_types)
        assert subgroup_starts == sorted(
            subgroup_starts,
            key=group_task_types.index,
        )


def test_edit_chapter_defaults_to_standard_tier() -> None:
    assert DEFAULT_TASK_TIERS[TaskType.EDIT_CHAPTER] is ModelTier.STANDARD
