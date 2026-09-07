"""SpecStep — validates and enriches a StorySpec via LLM."""

from __future__ import annotations

from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.language import normalize_payload_for_language
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.narrative_person import (
    build_narrative_person_rule,
    clean_pov_hint,
    pov_allows_first_person,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step

_log = get_logger("spec_step")


@register_step("spec")
class SpecStep(PipelineStep[dict[str, Any], StorySpec]):
    """Validate raw input, then call LLM to enrich the spec.

    This is a standard pipeline step — the enrichment call goes through
    the ModelRouter like every other step, so it respects task routing,
    budget degradation, and provider overrides.
    """

    def __init__(
        self, *args: Any, extra_context: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._extra_context = extra_context or {}

    @property
    def step_name(self) -> str:
        return "spec"

    async def _execute(self, input_data: dict[str, Any]) -> StorySpec:
        # ① Validate: ensure the raw input is at least structurally valid
        spec = StorySpec.model_validate(input_data)

        # ② Enrich: send to LLM to fill in missing creative elements
        try:
            enriched_data = await self._call_with_retry(
                TaskType.SPEC_ENRICH,
                {**spec.model_dump(mode="json"), **self._extra_context},
                max_tokens=self._dynamic_max_tokens(
                    TaskType.SPEC_ENRICH,
                    1200,
                    prompt_overhead=1200,
                    min_tokens=1024,
                ),
                temperature=self.settings.temp_spec_enrich,
            )

            # Merge: LLM output overrides only empty/default fields
            normalized_data = normalize_payload_for_language(enriched_data, spec.language)
            merged = self._merge_spec(spec.model_dump(mode="json"), normalized_data)
            spec = StorySpec.model_validate(merged)
        except Exception as exc:
            # Enrichment is best-effort; if it fails, use the original spec
            _log.warning("Spec enrichment failed, using original: %s", exc)

        return spec

    @staticmethod
    def _merge_spec(
        original: dict[str, Any],
        enriched: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge enriched data into original, only overriding empty/default fields."""
        result = dict(original)
        # Fields that should be overridden only when originally empty
        fillable_fields = (
            "title",
            "characters_hint",
            "world_hint",
            "conflict_hint",
            "pov_hint",
            "opening_style",
            "ending_style",
        )
        for key in fillable_fields:
            orig_val = original.get(key, "")
            new_val = enriched.get(key, "")
            if key == "pov_hint":
                result[key] = SpecStep._merge_pov_hint(orig_val, new_val)
                continue
            if not orig_val and new_val:
                result[key] = new_val
            # If user provided something, keep it (don't overwrite)

        return result

    @staticmethod
    def _merge_pov_hint(original_value: Any, enriched_value: Any) -> str:
        """Preserve user POV intent while making narrative person explicit."""
        original = clean_pov_hint(original_value)
        enriched = clean_pov_hint(enriched_value)
        if not original:
            return build_narrative_person_rule(enriched) if enriched else ""
        if (
            pov_allows_first_person(original)
            or "第三人称" in original
            or "三人称" in original
            or "叙事人称硬约束" in original
        ):
            return original
        if (
            enriched
            and original in enriched
            and ("第三人称" in enriched or "三人称" in enriched or "叙事人称硬约束" in enriched)
        ):
            return enriched
        return build_narrative_person_rule(original)
