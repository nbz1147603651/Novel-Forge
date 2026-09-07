"""Just-in-time advancement of progressive long-form planning watermarks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from novel_forge.core.constants import PipelineConstants
from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.core.schemas.init_v2 import CreativeDirectorPacket
from novel_forge.core.schemas.outline import NarrativeBlueprint, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.editorial.schemas import EditorialContract
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_cache import _build_base_ctx
from novel_forge.pipeline.long.services.init.init_context import build_init_context
from novel_forge.pipeline.long.services.init.init_outline_batch import _batched_generate_outline
from novel_forge.pipeline.long.services.init.init_user_intent import build_user_intent_card


@dataclass(frozen=True)
class PlanningHorizonAdvance:
    previous_hard_through: int
    hard_through_chapter: int
    planned_through_chapter: int
    affected_chapters: tuple[int, ...]
    generated_chapters: tuple[int, ...]
    revision_id: str = ""
    published: bool = True
    candidate_version: str = ""


def next_planning_horizon_target(
    *, total_chapters: int, hard_through_chapter: int, current_chapter: int
) -> int | None:
    """Return the next five-chapter hard waterline when two or fewer chapters remain."""

    if hard_through_chapter >= total_chapters or hard_through_chapter - current_chapter > 2:
        return None
    return min(total_chapters, hard_through_chapter + 5)


def _load_optional_model(storage: Any, path: Any, model_type: Any) -> Any | None:
    if not storage.exists(path):
        return None
    try:
        return model_type.model_validate(storage.load_json(path))
    except Exception:
        return None


def _assert_single_pov_intent(outline: StoryOutline, request_payload: dict[str, Any]) -> None:
    init_input = request_payload.get("init_input")
    pov_hint = str((init_input or {}).get("pov_hint") or "").lower()
    single_requested = any(
        marker in pov_hint for marker in ("单pov", "单 pov", "单一视角", "single pov")
    )
    if not single_requested:
        return
    pov_ids = {
        str(chapter.pov_character_id or chapter.pov_character_name or "").strip()
        for chapter in outline.chapters
        if chapter.chapter_number <= int(outline.hard_through_chapter or 0)
    }
    pov_ids.discard("")
    if len(pov_ids) > 1:
        raise ValueError("progressive outline hardening violates the user's single-POV intent")


async def advance_planning_horizon_locked(
    runtime: Any,
    *,
    project_id: str,
    current_chapter: int,
    target_chapter: int | None = None,
    on_step_progress: Any = None,
) -> PlanningHorizonAdvance | None:
    """Advance a progressive hard window by five chapters while under project lock."""

    storage = runtime.storage
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    outline = _load_optional_model(storage, layout.outline_path, StoryOutline)
    if outline is None:
        return None
    original_outline = outline
    hard = int(outline.hard_through_chapter or outline.total_chapters)
    planned = int(outline.planned_through_chapter or outline.total_chapters)
    if target_chapter is not None and not hard <= target_chapter <= outline.total_chapters:
        raise ValueError("planning target must be within the existing book's uncommitted range")
    target = (
        target_chapter
        if target_chapter is not None
        else next_planning_horizon_target(
            total_chapters=outline.total_chapters,
            hard_through_chapter=hard,
            current_chapter=current_chapter,
        )
    )
    if target is None or target == hard:
        return None
    affected = tuple(range(hard + 1, target + 1))
    existing_numbers = {chapter.chapter_number for chapter in outline.chapters}
    missing_numbers = tuple(number for number in affected if number not in existing_numbers)
    generated: tuple[int, ...] = ()

    meta = (
        storage.load_json(layout.init_request_meta_path)
        if storage.exists(layout.init_request_meta_path)
        else {}
    )
    request_payload = meta.get("request") if isinstance(meta, dict) else {}
    if not isinstance(request_payload, dict):
        request_payload = {}

    if affected:
        spec = StorySpec.model_validate(storage.load_json(layout.spec_path))
        story_bible = StoryBible.model_validate(storage.load_json(layout.bible_path))
        character_bible = CharacterBible.model_validate(storage.load_json(layout.characters_path))
        blueprint = NarrativeBlueprint.model_validate(storage.load_json(layout.blueprint_path))
        editorial_contract = _load_optional_model(
            storage, layout.editorial_contract_path, EditorialContract
        )
        creative_packet = _load_optional_model(
            storage,
            layout.plans_dir / "creative_director_packet.json",
            CreativeDirectorPacket,
        )
        runner = runtime.chapter_runner(on_step_progress=on_step_progress)
        ctx = replace(
            build_init_context(runner, project_id),
            storage=storage,
            layout=layout,
            memory_context=None,
        )
        words_per_chapter = max(
            500,
            int((request_payload.get("generation_options") or {}).get("words_per_chapter", 3000)),
        )
        outline_ctx = _build_base_ctx(
            spec,
            outline.total_chapters * words_per_chapter,
            premise=spec.theme,
        )
        outline_ctx.update(
            {
                "spec": spec,
                "story_bible": story_bible,
                "character_bible": character_bible.model_dump(mode="json"),
                "total_chapters": outline.total_chapters,
                "words_per_chapter": words_per_chapter,
                "use_volume_mode": outline.volume_mode,
                "blueprint_element_selection": (
                    blueprint.element_selection.model_dump(mode="json")
                    if blueprint.element_selection is not None
                    else {}
                ),
                "style_profile": storage.load_json(layout.style_profile_path)
                if storage.exists(layout.style_profile_path)
                else None,
                "character_system": {},
                "relationship_overview": [],
                "entity_graph": None,
                "creative_director_packet": creative_packet.model_dump(mode="json")
                if creative_packet is not None
                else None,
                "user_intent": build_user_intent_card(request_payload),
            }
        )
        outline = await _batched_generate_outline(
            ctx,
            existing_outline=outline,
            outline_ctx=outline_ctx,
            blueprint=blueprint,
            total_chapters=outline.total_chapters,
            words_per_chapter=words_per_chapter,
            use_volume_mode=outline.volume_mode,
            effective_chapters_per_volume=max(1, int(ctx.config.default_chapters_per_volume)),
            character_bible=character_bible,
            entity_registry=storage.load_json(layout.narrative_state_dir / "entity_registry.json")
            if storage.exists(layout.narrative_state_dir / "entity_registry.json")
            else None,
            editorial_contract=editorial_contract,
            target_start_chapter=hard + 1,
            target_end_chapter=target,
            design_through_chapter=target,
            preserve_committed_chapters=True,
        )
        available = {
            chapter.chapter_number
            for chapter in outline.chapters
            if chapter.beats_summary and chapter.notes != PipelineConstants.PLACEHOLDER_NOTE
        }
        missing = sorted(set(range(1, target + 1)) - available)
        if missing:
            raise RuntimeError(f"planning horizon outline coverage incomplete: {missing}")
        generated = missing_numbers

    outline = outline.model_copy(
        update={
            "hard_through_chapter": target,
            "planned_through_chapter": max(planned, target),
            "synopsis": original_outline.synopsis,
            "volume_mode": original_outline.volume_mode,
            "volumes": original_outline.volumes,
        }
    )
    _assert_single_pov_intent(outline, request_payload)
    storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    if on_step_progress is not None:
        on_step_progress(
            "planning_horizon_advanced",
            {
                "previous_hard_through": hard,
                "hard_through_chapter": target,
                "planned_through_chapter": max(planned, target),
                "generated_chapters": list(generated),
            },
        )
    return PlanningHorizonAdvance(
        previous_hard_through=hard,
        hard_through_chapter=target,
        planned_through_chapter=max(planned, target),
        affected_chapters=affected,
        generated_chapters=generated,
    )


def assert_chapter_within_hard_window(
    storage: Any, *, project_id: str, chapter_number: int
) -> None:
    """Block only a future chapter that has not yet been hardened."""

    layout = ProjectLayout(storage.existing_project_dir(project_id))
    outline = _load_optional_model(storage, layout.outline_path, StoryOutline)
    if outline is None:
        return
    hard = int(outline.hard_through_chapter or outline.total_chapters)
    if chapter_number > hard:
        raise RuntimeError(
            f"第 {chapter_number} 章尚未硬化；当前规划水位到第 {hard} 章。"
            "请重试规划水位推进，已完成章节不会受影响。"
        )


__all__ = (
    "PlanningHorizonAdvance",
    "advance_planning_horizon_locked",
    "assert_chapter_within_hard_window",
    "next_planning_horizon_target",
)
