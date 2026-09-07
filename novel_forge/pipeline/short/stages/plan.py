"""Plan stage: spec, blueprint elements, blueprint, and beats."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.core.schemas.beats import StoryBeats
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.short_blueprint import ShortBlueprint
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.short._shared import clean_text, short_research_prompt_context
from novel_forge.pipeline.steps.beats_step import BeatsStep
from novel_forge.pipeline.steps.blueprint_element_select_step import (
    BlueprintElementSelectInput,
    BlueprintElementSelectStep,
    has_manual_selector_preferences,
)
from novel_forge.pipeline.steps.short_blueprint_step import ShortBlueprintStep
from novel_forge.pipeline.steps.spec_step import SpecStep
from novel_forge.research.chapter_evidence import prepare_chapter_research_evidence

if TYPE_CHECKING:
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.short_runner import ShortStoryRunner

_log = get_logger("pipeline.short.stages.plan")


async def run_spec(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec_input: dict[str, Any],
) -> StorySpec:
    spec_step = SpecStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        extra_context={
            "user_intent": runner._user_intent,
            **short_research_prompt_context(runner, include_inspiration=True),
        },
    )
    spec = await spec_step.run(spec_input)
    runner._storage.save_json(layout.spec_path, spec.model_dump(mode="json"))
    runner._on_step("spec", spec)
    return spec


async def prepare_short_research(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    *,
    enabled: bool,
    provider_name: str,
    query_hint: str,
) -> None:
    """Build the one run-level pack consumed by all later short stages."""

    evidence = await prepare_chapter_research_evidence(
        storage=runner._storage,
        layout=layout,
        settings=runner._settings,
        spec=spec,
        chapter_number=1,
        chapter_outline={"summary": spec.theme, "title": spec.title},
        chapter_contract={},
        user_intent=runner._user_intent,
        enabled=enabled,
        provider_name=provider_name,
        query_hint=query_hint,
        mode="short",
        phase_boundary=True,
        on_step=runner._on_step,
    )
    runner._research_evidence_pack = (
        evidence.pack.model_dump(mode="json") if evidence.pack is not None else {}
    )
    runner._research_uncertainty = evidence.uncertainty


async def resolve_blueprint_elements(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    *,
    preferences: dict[str, Any] | None = None,
) -> BlueprintElementSelection:
    should_reselect = has_manual_selector_preferences(preferences)
    if runner._storage.exists(layout.blueprint_elements_path) and not should_reselect:
        try:
            selection = BlueprintElementSelection.model_validate(
                runner._storage.load_json(layout.blueprint_elements_path)
            )
            runner._on_step("short_blueprint_elements_resumed", {"element_selection": selection})
            return selection
        except Exception:
            _log.warning("short_blueprint_elements_resume_failed", exc_info=True)

    step = BlueprintElementSelectStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
    )
    selection = await step.run(
        BlueprintElementSelectInput(
            spec=spec,
            mode="short",
            preferences=preferences,
        )
    )
    runner._storage.save_json(
        layout.blueprint_elements_path,
        selection.model_dump(mode="json"),
    )
    runner._on_step("short_blueprint_elements", {"element_selection": selection})
    return selection


async def run_blueprint(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    element_selection: BlueprintElementSelection | None = None,
) -> ShortBlueprint | None:
    step = ShortBlueprintStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        element_selection=element_selection,
        extra_context={
            "user_intent": runner._user_intent,
            **short_research_prompt_context(runner, include_inspiration=True),
        },
    )
    try:
        blueprint = await step.run(spec)
        runner._storage.save_json(
            layout.short_blueprint_path(),
            blueprint.model_dump(mode="json"),
        )
        runner._on_step("short_blueprint", blueprint)
        return blueprint
    except Exception:
        _log.warning("short_blueprint_failed", exc_info=True)
        return None


async def run_beats(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    blueprint: ShortBlueprint | None = None,
) -> StoryBeats:
    ctx: dict[str, Any] = {
        "user_intent": runner._user_intent,
        **short_research_prompt_context(runner, include_inspiration=True),
    }
    if blueprint is not None:
        ctx["blueprint"] = blueprint
    beats_step = BeatsStep(
        runner._router,
        runner._builder,
        extra_context=ctx,
        settings=runner._settings,
        trace=runner._trace,
    )
    beats = await beats_step.run(spec)
    runner._storage.save_json(layout.short_beats_path(), beats.model_dump(mode="json"))
    runner._on_step("beats", beats)
    return beats


def blueprint_is_degraded(blueprint: ShortBlueprint | None) -> bool:
    if blueprint is None:
        return True
    has_synopsis = bool(clean_text(getattr(blueprint, "synopsis", "")))
    has_turning_points = bool(getattr(blueprint, "turning_points", []) or [])
    has_arc = bool(clean_text(getattr(blueprint, "emotional_arc", "")))
    return not (has_synopsis and (has_turning_points or has_arc))
