"""Evaluate stage: final evaluation of short story."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.evaluate_step import EvaluateStep

if TYPE_CHECKING:
    from novel_forge.core.schemas.beats import StoryBeats
    from novel_forge.core.schemas.short_blueprint import ShortBlueprint
    from novel_forge.core.schemas.spec import StorySpec
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.short_runner import ShortStoryRunner

_log = get_logger("pipeline.short.stages.evaluate")


async def run_final_evaluation(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    current_text: str,
    *,
    require_intent_compliance: bool = False,
    persist: bool = True,
    event_name: str = "evaluate",
) -> EvalReport:
    from novel_forge.pipeline.short.stages.edit import _build_short_eval_context

    eval_context = _build_short_eval_context(
        runner, spec, beats, blueprint, execution_plan, layout
    )
    eval_context["require_intent_compliance"] = require_intent_compliance
    eval_step = EvaluateStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        extra_context=eval_context,
    )
    eval_report = await eval_step.run(current_text)
    eval_report = eval_report.model_copy(
        update={"source_text_hash": source_text_hash(current_text)}
    )
    if persist:
        runner._storage.save_json(layout.eval_report_path(), eval_report.model_dump(mode="json"))
    if persist and event_name == "evaluate":
        runner._on_step(event_name, eval_report)
    else:
        runner._on_step(
            event_name,
            {
                "candidate_only": not persist,
                "source_text_hash": eval_report.source_text_hash,
                "evaluation": eval_report.model_dump(mode="json"),
            },
        )
    return eval_report
