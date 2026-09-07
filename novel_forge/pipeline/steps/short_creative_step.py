"""ShortCreativeStep — extracts creative analysis from a finished short story."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.short_blueprint import ShortBlueprint
from novel_forge.core.schemas.short_creative import (
    BeatFulfillment,
    CharacterAnalysis,
    CharacterRelationship,
    NarrativeAnalysis,
    ShortCreativeSummary,
    ThematicAnalysis,
    TurningPoint,
)
from novel_forge.gateway.router import ModelRouter
from novel_forge.obs.logger import get_logger
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step
from novel_forge.prompts.builder import PromptBuilder

_log = get_logger("pipeline.short_creative")


@dataclass
class ShortCreativeInput:
    """Input for the short creative analysis step."""

    final_text: str
    beats: StoryBeats
    genre: str = ""
    tone: str = ""
    theme: str = ""
    blueprint: ShortBlueprint | None = None
    length_target: int = 0


@register_step("short_creative")
class ShortCreativeStep(PipelineStep[ShortCreativeInput, ShortCreativeSummary]):
    """Final text + beats → LLM → ShortCreativeSummary."""

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        *,
        settings: Settings,
        trace: PipelineTrace | None = None,
    ) -> None:
        super().__init__(router, builder, settings=settings, trace=trace)

    @property
    def step_name(self) -> str:
        return "short_creative_summary"

    async def _execute(self, input_data: ShortCreativeInput) -> ShortCreativeSummary:
        ctx: dict[str, Any] = {
            "final_text": input_data.final_text,
            "beats": [b.model_dump(mode="json") for b in input_data.beats.beats],
            "genre": input_data.genre,
            "tone": input_data.tone,
            "theme": input_data.theme,
            "blueprint": input_data.blueprint,
            "length_target": input_data.length_target,
        }

        try:
            data = await self._call_with_retry(
                TaskType.SHORT_CREATIVE_SUMMARY,
                ctx,
                max_tokens=self._dynamic_max_tokens(
                    TaskType.SHORT_CREATIVE_SUMMARY,
                    max(1800, len(input_data.final_text) // 3),
                    prompt_overhead=2200,
                    min_tokens=2048,
                ),
                temperature=self.settings.temp_evaluate,
            )
            return _parse_creative_summary(data)
        except Exception:
            _log.warning("short_creative_summary_parse_failed | using empty fallback")
            return ShortCreativeSummary()


def _parse_creative_summary(data: dict[str, Any]) -> ShortCreativeSummary:
    """Safely parse LLM JSON into a ShortCreativeSummary."""
    characters = []
    for c in data.get("characters", []):
        if not isinstance(c, dict):
            continue
        rels = []
        for r in c.get("relationships", []):
            if isinstance(r, dict):
                rels.append(
                    CharacterRelationship(
                        target=str(r.get("target", "")),
                        type=str(r.get("type", "")),
                        note=str(r.get("note", "")),
                    )
                )
        characters.append(
            CharacterAnalysis(
                name=str(c.get("name", "")),
                role=str(c.get("role", "")),
                arc_summary=str(c.get("arc_summary", "")),
                key_traits=[str(t) for t in c.get("key_traits", [])],
                relationships=rels,
            )
        )

    raw_narr = data.get("narrative_analysis", {}) or {}
    turning_points = []
    for tp in raw_narr.get("turning_points", []):
        if isinstance(tp, dict):
            turning_points.append(
                TurningPoint(
                    description=str(tp.get("description", "")),
                    effectiveness=str(tp.get("effectiveness", "")),
                )
            )
    narrative = NarrativeAnalysis(
        pacing_assessment=str(raw_narr.get("pacing_assessment", "")),
        tension_curve=str(raw_narr.get("tension_curve", "")),
        tension_curve_note=str(raw_narr.get("tension_curve_note", "")),
        structure_type=str(raw_narr.get("structure_type", "")),
        turning_points=turning_points,
        opening_hook=str(raw_narr.get("opening_hook", "")),
        ending_impact=str(raw_narr.get("ending_impact", "")),
    )

    raw_theme = data.get("thematic_analysis", {}) or {}
    thematic = ThematicAnalysis(
        core_theme=str(raw_theme.get("core_theme", "")),
        theme_delivery=str(raw_theme.get("theme_delivery", "")),
        symbolic_elements=[str(s) for s in raw_theme.get("symbolic_elements", [])],
        emotional_resonance=str(raw_theme.get("emotional_resonance", "")),
    )

    beat_fulfillment = []
    for bf in data.get("beat_fulfillment", []):
        if isinstance(bf, dict):
            beat_fulfillment.append(
                BeatFulfillment(
                    sequence=int(bf.get("sequence", 0)),
                    fulfilled=bool(bf.get("fulfilled", False)),
                    note=str(bf.get("note", "")),
                )
            )

    return ShortCreativeSummary(
        characters=characters,
        narrative_analysis=narrative,
        thematic_analysis=thematic,
        creative_highlights=[str(h) for h in data.get("creative_highlights", [])],
        improvement_suggestions=[str(s) for s in data.get("improvement_suggestions", [])],
        beat_fulfillment=beat_fulfillment,
    )
