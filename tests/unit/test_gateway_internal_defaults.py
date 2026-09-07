"""Tests for internal task route/fallback default synchronization."""

from __future__ import annotations

from novel_forge.core.constants import TaskType
from novel_forge.gateway.factory import (
    _apply_internal_task_fallback_defaults,
    _apply_internal_task_route_defaults,
)
from novel_forge.gateway.router import TaskRouteOverride


def test_profile_structure_inherits_profile_style_route() -> None:
    routes = {
        TaskType.PROFILE_STYLE: TaskRouteOverride(
            provider="deepseek",
            model_id="deepseek-reasoner",
            thinking=True,
            multi_turn=False,
        )
    }

    resolved = _apply_internal_task_route_defaults(routes)

    assert resolved is not None
    assert TaskType.PROFILE_STRUCTURE in resolved
    inherited = resolved[TaskType.PROFILE_STRUCTURE]
    assert inherited.provider == "deepseek"
    assert inherited.model_id == "deepseek-reasoner"
    assert inherited.thinking is True
    assert inherited.multi_turn is False


def test_editorial_contract_parts_inherit_parent_route_without_thinking() -> None:
    routes = {
        TaskType.DERIVE_EDITORIAL_CONTRACT: TaskRouteOverride(
            provider="deepseek",
            model_id="deepseek-reasoner",
            thinking=True,
            multi_turn=True,
        )
    }

    resolved = _apply_internal_task_route_defaults(routes)

    assert resolved is not None
    for task_type in (
        TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
        TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
    ):
        inherited = resolved[task_type]
        assert inherited.provider == "deepseek"
        assert inherited.model_id == "deepseek-reasoner"
        assert inherited.thinking is False
        assert inherited.multi_turn is False


def test_profile_structure_inherits_profile_style_fallback_chain() -> None:
    fallbacks = {
        TaskType.PROFILE_STYLE: [
            TaskRouteOverride(provider="deepseek", model_id="deepseek-reasoner"),
            TaskRouteOverride(provider="tongyi", model_id="qwen-long"),
        ]
    }

    resolved = _apply_internal_task_fallback_defaults(fallbacks)

    assert resolved is not None
    assert TaskType.PROFILE_STRUCTURE in resolved
    inherited = resolved[TaskType.PROFILE_STRUCTURE]
    assert len(inherited) == 2
    assert inherited[0].provider == "deepseek"
    assert inherited[0].model_id == "deepseek-reasoner"
    assert inherited[1].provider == "tongyi"
    assert inherited[1].model_id == "qwen-long"


def test_editorial_contract_parts_inherit_parent_fallbacks_without_thinking() -> None:
    fallbacks = {
        TaskType.DERIVE_EDITORIAL_CONTRACT: [
            TaskRouteOverride(provider="deepseek", model_id="deepseek-reasoner", thinking=True),
            TaskRouteOverride(provider="tongyi", model_id="qwen-long", multi_turn=True),
        ]
    }

    resolved = _apply_internal_task_fallback_defaults(fallbacks)

    assert resolved is not None
    for task_type in (
        TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
        TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
    ):
        inherited = resolved[task_type]
        assert len(inherited) == 2
        assert inherited[0].provider == "deepseek"
        assert inherited[0].model_id == "deepseek-reasoner"
        assert inherited[0].thinking is False
        assert inherited[1].provider == "tongyi"
        assert inherited[1].model_id == "qwen-long"
        assert inherited[1].multi_turn is False


def test_plan_outline_hidden_steps_inherit_outline_fallback_chain() -> None:
    fallbacks = {
        TaskType.PLAN_OUTLINE: [
            TaskRouteOverride(provider="deepseek", model_id="deepseek-reasoner"),
            TaskRouteOverride(provider="tongyi", model_id="qwen-long"),
        ]
    }

    resolved = _apply_internal_task_fallback_defaults(fallbacks)

    assert resolved is not None
    assert TaskType.PLAN_OUTLINE_BATCH in resolved
    assert TaskType.PLAN_OUTLINE_CONTINUE in resolved
    batch = resolved[TaskType.PLAN_OUTLINE_BATCH]
    cont = resolved[TaskType.PLAN_OUTLINE_CONTINUE]
    assert len(batch) == 2
    assert len(cont) == 2
    assert batch[0].provider == "deepseek"
    assert batch[0].model_id == "deepseek-reasoner"
    assert cont[1].provider == "tongyi"
    assert cont[1].model_id == "qwen-long"


def test_plan_outline_continue_prefers_batch_fallback_chain() -> None:
    fallbacks = {
        TaskType.PLAN_OUTLINE: [
            TaskRouteOverride(provider="deepseek", model_id="deepseek-reasoner"),
        ],
        TaskType.PLAN_OUTLINE_BATCH: [
            TaskRouteOverride(provider="tongyi", model_id="qwen-long"),
        ],
    }

    resolved = _apply_internal_task_fallback_defaults(fallbacks)

    assert resolved is not None
    assert TaskType.PLAN_OUTLINE_CONTINUE in resolved
    cont = resolved[TaskType.PLAN_OUTLINE_CONTINUE]
    assert len(cont) == 1
    assert cont[0].provider == "tongyi"
    assert cont[0].model_id == "qwen-long"
