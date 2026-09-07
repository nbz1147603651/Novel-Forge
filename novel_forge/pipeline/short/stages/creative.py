"""Creative stage: creative analysis summary for short story."""

from __future__ import annotations

from typing import TYPE_CHECKING

from novel_forge.core.schemas.short_creative import ShortCreativeSummary
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.short_creative_step import ShortCreativeInput, ShortCreativeStep

if TYPE_CHECKING:
    from novel_forge.core.schemas.beats import StoryBeats
    from novel_forge.core.schemas.short_blueprint import ShortBlueprint
    from novel_forge.core.schemas.spec import StorySpec
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.short_runner import ShortStoryRunner

_log = get_logger("pipeline.short.stages.creative")


async def run_creative_analysis(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    current_text: str,
) -> ShortCreativeSummary | None:
    try:
        step = ShortCreativeStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=runner._trace,
        )
        summary = await step.run(
            ShortCreativeInput(
                final_text=current_text,
                beats=beats,
                genre=spec.genre,
                tone=spec.tone,
                theme=getattr(spec, "theme", ""),
                blueprint=blueprint,
                length_target=spec.length_target,
            )
        )
        runner._storage.save_json(
            layout.short_creative_report_path(),
            summary.model_dump(mode="json"),
        )
        runner._on_step("creative_summary", summary)
        return summary
    except Exception:
        _log.warning("short_creative_analysis_failed | skipping (non-fatal)")
        return None
