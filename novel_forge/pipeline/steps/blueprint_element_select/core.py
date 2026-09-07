"""BlueprintElementSelectStep — choose required/extension blueprint elements for current story spec."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep

from .elements import get_element_library_version
from .selection import (
    _normalize_preferences,
    _normalize_selection,
    get_blueprint_element_cards,
    get_blueprint_genre_presets,
)

if TYPE_CHECKING:
    from novel_forge.core.schemas.style_profile import ProjectStyleProfile

_log = get_logger("pipeline.blueprint_elements")


@dataclass(frozen=True)
class BlueprintElementSelectInput:
    spec: StorySpec
    mode: Literal["short", "long"] = "long"
    preferences: dict[str, Any] | None = None
    style_profile: "ProjectStyleProfile | None" = None


class BlueprintElementSelectStep(
    PipelineStep[BlueprintElementSelectInput, BlueprintElementSelection]
):
    @property
    def step_name(self) -> str:
        return "blueprint_element_select"

    async def _execute(
        self,
        input_data: BlueprintElementSelectInput,
    ) -> BlueprintElementSelection:
        required_cards = get_blueprint_element_cards(tier="required")
        extension_cards = get_blueprint_element_cards(tier="extension")
        preference_config = _normalize_preferences(input_data.preferences)
        context: dict[str, Any] = {
            "spec": input_data.spec,
            "mode": "长篇" if input_data.mode == "long" else "短篇",
            "library_version": get_element_library_version(),
            "required_elements": [item.model_dump(mode="json") for item in required_cards],
            "extension_elements": [item.model_dump(mode="json") for item in extension_cards],
            "genre_presets": get_blueprint_genre_presets(),
            "selector_preferences": preference_config.model_dump(mode="json"),
        }
        if input_data.style_profile is not None:
            sp = input_data.style_profile
            context["style_profile_hint"] = {
                "hook_preferred_types": sp.hook_config.preferred_types,
                "hook_strength_baseline": sp.hook_config.strength_baseline,
                "micro_payoff_preferred": sp.micro_payoff_config.preferred_types,
                "min_payoffs_per_chapter": sp.micro_payoff_config.min_per_chapter,
                "cool_point_patterns": sp.cool_point_config.preferred_patterns,
            }

        payload: dict[str, Any] = {}
        llm_failed = False
        try:
            payload = await self._call_with_retry(
                TaskType.BLUEPRINT_ELEMENT_SELECT,
                context,
                max_tokens=self._dynamic_max_tokens(
                    TaskType.BLUEPRINT_ELEMENT_SELECT,
                    max(1800, (len(required_cards) + len(extension_cards)) * 180),
                    prompt_overhead=2600,
                    min_tokens=2048,
                ),
                temperature=self.settings.temp_blueprint_element_select,
                required_keys=(),
                max_retries=2,
            )
        except Exception as exc:
            _log.warning(
                "blueprint_element_selector_failed | mode=%s | error=%s",
                input_data.mode,
                exc,
            )
            llm_failed = True

        result = _normalize_selection(
            payload if isinstance(payload, dict) else {},
            spec=input_data.spec,
            mode=input_data.mode,
            preferences=preference_config.model_dump(mode="json"),
        )
        if llm_failed:
            result.selector_summary = (
                f"{result.selector_summary}（AI 选择失败，已回退至题材启发式匹配）"
            )
        return result
