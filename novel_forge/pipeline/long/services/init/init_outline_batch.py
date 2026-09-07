"""Batched outline generation for init service."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.domain.character_boundary import canonical_character_names
from novel_forge.core.domain.entity_references import EntityReferenceIndex
from novel_forge.core.schemas.outline import (
    ChapterCastPlan,
    ChapterDesignMatrix,
    ChapterDesignMatrixEntry,
    ChapterEmotionalBrief,
    ChapterOutline,
    NarrativeBlueprint,
    StoryOutline,
    outline_chapter_title_repair_reason,
)
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.narrative_state.schemas import stable_id
from novel_forge.pipeline.artifact_manifest import manifest_for_context
from novel_forge.pipeline.long.services.blueprint import outline_helpers as outline_h
from novel_forge.pipeline.long.services.constraints.constraint_router import (
    build_outline_reveal_window,
)
from novel_forge.pipeline.long.services.init.init_batch_sizing import (
    effective_outline_batch_size,
    outline_batch_target_output_chars,
    record_effective_init_batch_size,
)
from novel_forge.pipeline.long.services.init.init_cache import _get_embedding_config
from novel_forge.pipeline.long.services.init.init_context import InitLongServiceContext
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _build_outline_tracker_context,
    _index_chapter_to_episodic_memory,
    _initialize_outline_tracker,
    _load_outline_conversation_history,
    _load_outline_session_accepted_batches,
    _load_outline_tracker_state,
    _load_partial_outline_chapters_from_session,
    _persist_partial_outline_state,
    _record_outline_exchange,
    _save_episodic_memory_outline_data,
    _save_outline_batch_checkpoint,
    _save_outline_tracker_state,
)
from novel_forge.pipeline.long.services.init.init_v2 import hash_payload
from novel_forge.pipeline.long.services.time_validation import (
    find_internal_duration_claim_conflicts,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.story_kernel.outline_tracker import HybridOutlineTracker

_log = logging.getLogger(__name__)

OUTLINE_REVEAL_GUARD_VERSION = "outline_reveal_guard_v1"
OUTLINE_IDENTITY_CONTRACT_VERSION = "outline_identity_v2"


def outline_reveal_guard_input_hashes(editorial_contract: Any | None) -> dict[str, str]:
    """Return cache fingerprints for outline generation guarded by revelation ladder."""
    ladder = _editorial_revelation_ladder_payload(editorial_contract)
    if not ladder:
        return {}
    return {
        "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
        "editorial_revelation_ladder": hash_payload(ladder),
    }


def _editorial_revelation_ladder_payload(editorial_contract: Any | None) -> list[Any]:
    if editorial_contract is None:
        return []
    if hasattr(editorial_contract, "model_dump"):
        data = editorial_contract.model_dump(mode="json")
    elif isinstance(editorial_contract, dict):
        data = editorial_contract
    else:
        data = {
            "revelation_ladder": getattr(editorial_contract, "revelation_ladder", []),
        }
    raw = data.get("revelation_ladder") if isinstance(data, dict) else []
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    return [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        for item in values
        if item
    ]


def _outline_session_matches_reveal_guard(
    storage: Any,
    layout: Any,
    expected_hashes: dict[str, str],
) -> bool:
    if not expected_hashes:
        return True
    if not storage.exists(layout.outline_session_path):
        return False
    try:
        payload = storage.load_json(layout.outline_session_path)
    except Exception:
        return False
    recorded = payload.get("reveal_guard_input_hashes") if isinstance(payload, dict) else None
    return isinstance(recorded, dict) and {
        str(key): str(value) for key, value in recorded.items()
    } == expected_hashes


def _clamped_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _outline_density_settings(settings: Any) -> dict[str, int]:
    """Return normalized chapter-outline density limits for prompt rendering."""
    beats_min = _clamped_int(
        getattr(settings, "init_outline_beats_min", 4),
        default=4,
        minimum=1,
        maximum=20,
    )
    beats_max = _clamped_int(
        getattr(settings, "init_outline_beats_max", 8),
        default=8,
        minimum=beats_min,
        maximum=30,
    )
    main_min = _clamped_int(
        getattr(settings, "init_outline_main_plot_points_min", 2),
        default=2,
        minimum=1,
        maximum=12,
    )
    main_max = _clamped_int(
        getattr(settings, "init_outline_main_plot_points_max", 4),
        default=4,
        minimum=main_min,
        maximum=20,
    )
    payoffs_min = _clamped_int(
        getattr(settings, "init_outline_expected_payoffs_min", 1),
        default=1,
        minimum=0,
        maximum=8,
    )
    payoffs_max = _clamped_int(
        getattr(settings, "init_outline_expected_payoffs_max", 3),
        default=3,
        minimum=payoffs_min,
        maximum=12,
    )
    return {
        "beats_min": beats_min,
        "beats_max": beats_max,
        "main_plot_points_min": main_min,
        "main_plot_points_max": main_max,
        "subplot_points_max": _clamped_int(
            getattr(settings, "init_outline_subplot_points_max", 3),
            default=3,
            minimum=0,
            maximum=12,
        ),
        "element_focus_max": _clamped_int(
            getattr(settings, "init_outline_element_focus_max", 3),
            default=3,
            minimum=0,
            maximum=3,
        ),
        "expected_payoffs_min": payoffs_min,
        "expected_payoffs_max": payoffs_max,
    }


def _clean_outline_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _unique_strings(values: list[Any] | tuple[Any, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_outline_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _entity_registry_payload(entity_registry: Any | None) -> dict[str, Any]:
    if entity_registry is None:
        return {}
    if hasattr(entity_registry, "model_dump"):
        data = entity_registry.model_dump(mode="json")
    elif isinstance(entity_registry, dict):
        data = entity_registry
    else:
        data = {"entities": getattr(entity_registry, "entities", [])}
    return data if isinstance(data, dict) else {}


def _outline_entity_catalog(
    *,
    entity_registry: Any | None,
    character_bible: Any,
) -> list[dict[str, Any]]:
    """Return canonical prompt-facing entities, adding deterministic character fallbacks."""
    registry = _entity_registry_payload(entity_registry)
    raw_entities = registry.get("entities") if isinstance(registry, dict) else []
    catalog: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    for raw in raw_entities or []:
        item = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
        if not isinstance(item, dict):
            continue
        entity_id = _clean_outline_text(item.get("entity_id"))
        name = _clean_outline_text(item.get("name"))
        if not entity_id or not name or entity_id in seen_ids:
            continue
        aliases = _unique_strings(list(item.get("aliases") or []))
        entity_type = _clean_outline_text(item.get("entity_type")) or "unknown"
        catalog.append(
            {
                "entity_id": entity_id,
                "name": name,
                "entity_type": entity_type,
                "aliases": aliases,
                "notes": _clean_outline_text(item.get("notes")),
            }
        )
        seen_ids.add(entity_id)
        seen_names.add(name)

    for name in canonical_character_names(character_bible):
        if name in seen_names:
            continue
        entity_id = stable_id("char", name)
        if entity_id in seen_ids:
            continue
        catalog.append(
            {
                "entity_id": entity_id,
                "name": name,
                "entity_type": "character",
                "aliases": [],
                "notes": "角色圣经确定性补入。",
            }
        )
        seen_ids.add(entity_id)
        seen_names.add(name)

    return catalog


def _character_entity_catalog(
    *,
    entity_registry: Any | None,
    character_bible: Any,
) -> list[dict[str, Any]]:
    return [
        item
        for item in _outline_entity_catalog(
            entity_registry=entity_registry,
            character_bible=character_bible,
        )
        if item.get("entity_type") == "character"
    ]


def _catalog_indexes(
    catalog: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    by_id = {
        str(item.get("entity_id") or ""): item
        for item in catalog
        if str(item.get("entity_id") or "")
    }
    name_to_id: dict[str, str] = {}
    for item in catalog:
        entity_id = str(item.get("entity_id") or "")
        if not entity_id:
            continue
        for raw in [item.get("name"), *(item.get("aliases") or [])]:
            name = _clean_outline_text(raw)
            if name and name not in name_to_id:
                name_to_id[name] = entity_id
    return by_id, name_to_id


def _entity_ids_from_texts(texts: list[Any], name_to_id: dict[str, str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = _clean_outline_text(raw)
        if not text:
            continue
        matched: list[tuple[int, str]] = []
        for name, entity_id in name_to_id.items():
            if name == text:
                matched.append((0, entity_id))
            else:
                pos = text.find(name)
                if pos >= 0:
                    matched.append((pos + 1, entity_id))
        for _, entity_id in sorted(matched):
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                result.append(entity_id)
    return result


def _phase_for_chapter(blueprint: NarrativeBlueprint, chapter_number: int) -> Any | None:
    for phase in blueprint.narrative_phases or []:
        start = int(getattr(phase, "chapter_start", 0) or 0)
        end = int(getattr(phase, "chapter_end", 0) or 0)
        if start <= chapter_number <= end:
            return phase
    return None


def _turning_points_for_chapter(blueprint: NarrativeBlueprint, chapter_number: int) -> list[Any]:
    return [
        item
        for item in blueprint.key_turning_points or []
        if int(getattr(item, "chapter_number", 0) or 0) == chapter_number
    ]


def _arc_ids_for_chapter(
    blueprint: NarrativeBlueprint,
    chapter_number: int,
    name_to_id: dict[str, str],
) -> list[str]:
    ids: list[str] = []
    for arc in blueprint.character_arcs or []:
        in_range = False
        for milestone in getattr(arc, "milestones", []) or []:
            start = int(getattr(milestone, "chapter_start", 0) or 0)
            end = int(getattr(milestone, "chapter_end", 0) or 0)
            if start <= chapter_number <= end:
                in_range = True
                break
        if in_range:
            ids.extend(_entity_ids_from_texts([getattr(arc, "character", "")], name_to_id))
    return _unique_strings(ids)


def _first_planned_chapters(
    *,
    total_chapters: int,
    blueprint: NarrativeBlueprint,
    character_ids: list[str],
    name_to_id: dict[str, str],
) -> dict[str, int]:
    first = {entity_id: total_chapters + 1 for entity_id in character_ids}
    for phase in blueprint.narrative_phases or []:
        start = int(getattr(phase, "chapter_start", 1) or 1)
        ids = _entity_ids_from_texts(list(getattr(phase, "key_characters", []) or []), name_to_id)
        for entity_id in ids:
            first[entity_id] = min(first.get(entity_id, total_chapters + 1), start)
    for point in blueprint.key_turning_points or []:
        chapter = int(getattr(point, "chapter_number", 0) or 0)
        ids = _entity_ids_from_texts(
            list(getattr(point, "characters_involved", []) or []),
            name_to_id,
        )
        for entity_id in ids:
            if chapter > 0:
                first[entity_id] = min(first.get(entity_id, total_chapters + 1), chapter)
    for arc in blueprint.character_arcs or []:
        ids = _entity_ids_from_texts([getattr(arc, "character", "")], name_to_id)
        starts = [
            int(getattr(milestone, "chapter_start", 0) or 0)
            for milestone in getattr(arc, "milestones", []) or []
        ]
        start = min([item for item in starts if item > 0], default=1)
        for entity_id in ids:
            first[entity_id] = min(first.get(entity_id, total_chapters + 1), start)
    for entity_id, chapter in list(first.items()):
        if chapter > total_chapters:
            first[entity_id] = 1
    return first


def _emotional_arc_hint(blueprint: NarrativeBlueprint, chapter_number: int) -> str:
    hints: list[str] = []
    for arc in blueprint.emotional_arcs or []:
        peaks = {int(item) for item in getattr(arc, "peak_chapters", []) or []}
        valleys = {int(item) for item in getattr(arc, "valley_chapters", []) or []}
        label = _clean_outline_text(getattr(arc, "emotion_type", ""))
        desc = _clean_outline_text(getattr(arc, "description", ""))
        if chapter_number in peaks:
            hints.append(f"{label or '情绪'}高峰：{desc}")
        elif chapter_number in valleys:
            hints.append(f"{label or '情绪'}低谷：{desc}")
    return "；".join(hints[:2])


def _non_character_mentions(
    *,
    catalog: list[dict[str, Any]],
    texts: list[Any],
) -> list[str]:
    non_character = [
        item for item in catalog if item.get("entity_type") and item.get("entity_type") != "character"
    ]
    _by_id, name_to_id = _catalog_indexes(non_character)
    return _entity_ids_from_texts(texts, name_to_id)[:8]


def build_chapter_design_matrix(
    *,
    blueprint: NarrativeBlueprint,
    total_chapters: int,
    entity_registry: Any | None,
    character_bible: Any,
) -> ChapterDesignMatrix:
    """Build structural cast constraints and evidence briefs before outline LLM calls."""
    entity_catalog = _outline_entity_catalog(
        entity_registry=entity_registry,
        character_bible=character_bible,
    )
    character_catalog = [item for item in entity_catalog if item.get("entity_type") == "character"]
    _by_id, name_to_id = _catalog_indexes(character_catalog)
    character_ids = [str(item.get("entity_id") or "") for item in character_catalog]
    character_ids = [item for item in character_ids if item]
    primary_character_id = character_ids[0] if character_ids else ""
    first_chapters = _first_planned_chapters(
        total_chapters=total_chapters,
        blueprint=blueprint,
        character_ids=character_ids,
        name_to_id=name_to_id,
    )

    entries: list[ChapterDesignMatrixEntry] = []
    for chapter_number in range(1, total_chapters + 1):
        phase = _phase_for_chapter(blueprint, chapter_number)
        turning_points = _turning_points_for_chapter(blueprint, chapter_number)
        phase_texts: list[Any] = []
        if phase is not None:
            phase_texts.extend(
                [
                    getattr(phase, "description", ""),
                    getattr(phase, "tension_level", ""),
                    getattr(phase, "time_context", ""),
                    *list(getattr(phase, "key_events", []) or []),
                    *list(getattr(phase, "key_characters", []) or []),
                    *list(getattr(phase, "primary_locations", []) or []),
                ]
            )
        turning_texts: list[Any] = []
        for point in turning_points:
            turning_texts.extend(
                [
                    getattr(point, "description", ""),
                    getattr(point, "location", ""),
                    *list(getattr(point, "characters_involved", []) or []),
                ]
            )

        phase_ids = [
            item
            for item in _entity_ids_from_texts(phase_texts, name_to_id)
            if item in character_ids
        ]
        turning_ids = [
            item
            for item in _entity_ids_from_texts(turning_texts, name_to_id)
            if item in character_ids
        ]
        arc_ids = [
            item
            for item in _arc_ids_for_chapter(blueprint, chapter_number, name_to_id)
            if item in character_ids
        ]
        active_seed = _unique_strings([*turning_ids, *arc_ids, *phase_ids])
        if not active_seed and primary_character_id:
            active_seed = [primary_character_id]
        pov_entity_id = active_seed[0] if active_seed else ""
        required_ids = _unique_strings([pov_entity_id, *turning_ids, *arc_ids])[:4]
        support_ids = [
            item for item in _unique_strings([*phase_ids, *active_seed]) if item not in required_ids
        ][:5]
        active_ids = set(required_ids) | set(support_ids)
        forbidden_ids = [
            entity_id
            for entity_id in character_ids
            if entity_id not in active_ids and first_chapters.get(entity_id, 1) > chapter_number
        ][:12]
        mention_only_ids = _non_character_mentions(
            catalog=entity_catalog,
            texts=[*phase_texts, *turning_texts, blueprint.synopsis],
        )

        phase_goal = _clean_outline_text(getattr(phase, "description", "")) if phase else ""
        turn_goal = "；".join(
            _clean_outline_text(getattr(point, "description", ""))
            for point in turning_points
            if _clean_outline_text(getattr(point, "description", ""))
        )
        emotion_hint = _emotional_arc_hint(blueprint, chapter_number)
        phase_tension = _clean_outline_text(getattr(phase, "tension_level", "")) if phase else ""
        emotional_brief = ChapterEmotionalBrief(
            subject_entity_id=pov_entity_id,
            pressure_evidence=_unique_strings([turn_goal, phase_tension, phase_goal])[:4],
            arc_evidence=_unique_strings([emotion_hint])[:2],
        )
        plot_duties = _unique_strings(
            [
                turn_goal,
                phase_goal,
                *(
                    _clean_outline_text(getattr(point, "description", ""))
                    for point in turning_points
                ),
            ]
        )[:5]
        if not plot_duties and blueprint.synopsis:
            plot_duties = [_clean_outline_text(blueprint.synopsis)[:120]]
        entries.append(
            ChapterDesignMatrixEntry(
                chapter_number=chapter_number,
                plot_duties=plot_duties,
                cast_plan=ChapterCastPlan(
                    pov_entity_id=pov_entity_id,
                    required_character_ids=required_ids,
                    support_character_ids=support_ids,
                    mention_only_entity_ids=mention_only_ids,
                    forbidden_active_character_ids=forbidden_ids,
                ),
                emotional_brief=emotional_brief,
                knowledge_boundary=[],
                hook_payoff_duties=[],
                scene_design_goals=[],
            )
        )

    return ChapterDesignMatrix(
        total_chapters=total_chapters,
        entity_catalog=entity_catalog,
        chapters=entries,
    )


def reconcile_outline_with_chapter_design_matrix(
    *,
    outline: StoryOutline,
    blueprint: NarrativeBlueprint,
    entity_registry: Any | None,
    character_bible: Any,
) -> tuple[StoryOutline, ChapterDesignMatrix, list[int]]:
    """Rebuild the deterministic matrix and migrate cached outline identity fields.

    Late-init resume may load an outline created against an older entity graph.
    Reusing its persisted ``cast_plan`` verbatim can make one entity id point to
    a different canonical entity after the graph is rebuilt.  The matrix is
    deterministic, so rebuild it from the current registry and project its
    identity fields back onto every cached chapter before contracts are reused.
    """

    matrix = build_chapter_design_matrix(
        blueprint=blueprint,
        total_chapters=int(outline.hard_through_chapter or outline.total_chapters),
        entity_registry=entity_registry,
        character_bible=character_bible,
    )
    design_by_number = matrix.by_chapter()
    character_whitelist = list(canonical_character_names(character_bible))
    index = EntityReferenceIndex(matrix.entity_catalog)
    normalized_chapters: list[ChapterOutline] = []
    changed_chapters: list[int] = []
    for chapter in outline.chapters:
        # Older projections could copy a mention-only non-character into active
        # cast. Its explicit old role is sufficient evidence to remove that copy.
        mention_only = set(chapter.cast_plan.mention_only_entity_ids)
        stale_mentions = {
            item for item in mention_only if index.resolve(item, entity_type="character") is None
        }
        migration_input = chapter.model_copy(
            update={
                "involved_character_ids": [
                    item for item in chapter.involved_character_ids if item not in stale_mentions
                ],
                "support_character_ids": [
                    item for item in chapter.support_character_ids if item not in stale_mentions
                ],
            }
        )
        normalized = _normalize_outline_character_fields(
            migration_input,
            character_whitelist=character_whitelist,
            entity_catalog=matrix.entity_catalog,
            design_entry=design_by_number.get(int(chapter.chapter_number)),
        )
        normalized = _apply_progressive_commitment_boundary(
            [normalized], hard_through_chapter=matrix.total_chapters
        )[0]
        # ``created_at`` is lineage metadata, not identity.  Preserve it when
        # migrating a cached chapter so an unchanged matrix does not invalidate
        # the outline and every downstream contract on each resume.
        if normalized.cast_plan.created_at != chapter.cast_plan.created_at:
            normalized = normalized.model_copy(
                update={
                    "cast_plan": normalized.cast_plan.model_copy(
                        update={"created_at": chapter.cast_plan.created_at}
                    )
                }
            )
        normalized_chapters.append(normalized)
        if normalized != chapter:
            changed_chapters.append(int(chapter.chapter_number))
    audit = _outline_entity_audit(
        normalized_chapters, character_bible, entity_catalog=matrix.entity_catalog
    )
    if not audit["ok"]:
        raise ValueError(f"缓存大纲实体引用未通过校验：{audit['issues']}")
    if not changed_chapters:
        return outline, matrix, []
    return (
        outline.model_copy(update={"chapters": normalized_chapters}),
        matrix,
        changed_chapters,
    )


def _scoped_design_matrix_payload(
    matrix: ChapterDesignMatrix | None,
    *,
    batch_start: int,
    batch_end: int,
) -> dict[str, Any] | None:
    if matrix is None:
        return None
    entries = [
        item
        for item in matrix.chapters
        if batch_start <= int(item.chapter_number) <= batch_end
    ]
    if not entries:
        return None
    return {
        "total_chapters": matrix.total_chapters,
        "entity_catalog": matrix.entity_catalog,
        "chapters": [
            {
                "chapter_number": item.chapter_number,
                "plot_duties": list(item.plot_duties),
                "cast_plan": item.cast_plan.model_dump(
                    mode="json",
                    exclude={"schema_version", "created_at"},
                ),
                "emotional_brief": item.emotional_brief.model_dump(
                    mode="json",
                    exclude={"schema_version", "created_at"},
                ),
                "knowledge_boundary": list(item.knowledge_boundary),
                "hook_payoff_duties": list(item.hook_payoff_duties),
                "scene_design_goals": list(item.scene_design_goals),
            }
            for item in entries
        ],
    }


def _merge_outline_chapters(
    *,
    raw_chapters: Any,
    chapter_map: dict[int, ChapterOutline],
    batch_start: int,
    batch_end: int,
    character_whitelist: list[str] | None = None,
    entity_catalog: list[dict[str, Any]] | None = None,
    design_by_chapter: dict[int, ChapterDesignMatrixEntry] | None = None,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Validate and merge model-returned chapters for one requested range."""
    accepted: list[int] = []
    skipped: list[dict[str, Any]] = []
    if not isinstance(raw_chapters, list):
        return accepted, [{"reason": "chapters_not_list"}]

    for index, raw_chapter in enumerate(raw_chapters):
        if not isinstance(raw_chapter, dict):
            skipped.append({"index": index, "reason": "chapter_not_object"})
            continue
        raw_number = raw_chapter.get("chapter_number")
        try:
            chapter = ChapterOutline.model_validate(raw_chapter)
        except (ValueError, KeyError) as exc:
            skipped.append(
                {
                    "index": index,
                    "chapter_number": raw_number,
                    "reason": "schema_validation_failed",
                    "error": str(exc),
                }
            )
            _log.debug("Skipping invalid chapter outline in batch: %s", exc)
            continue
        if not batch_start <= chapter.chapter_number <= batch_end:
            skipped.append(
                {
                    "index": index,
                    "chapter_number": chapter.chapter_number,
                    "reason": "chapter_out_of_requested_range",
                }
            )
            continue
        chapter = _normalize_outline_character_fields(
            chapter,
            character_whitelist=character_whitelist,
            entity_catalog=entity_catalog,
            design_entry=(design_by_chapter or {}).get(chapter.chapter_number),
        )
        chapter_map[chapter.chapter_number] = outline_h.normalize_chapter_outline_beats(chapter)
        accepted.append(chapter.chapter_number)

    return accepted, skipped


def _apply_progressive_commitment_boundary(
    chapters: list[ChapterOutline],
    *,
    hard_through_chapter: int,
) -> list[ChapterOutline]:
    """Remove cast/POV commitments from adjustable preview chapters."""

    bounded: list[ChapterOutline] = []
    for chapter in chapters:
        if chapter.chapter_number <= hard_through_chapter:
            bounded.append(chapter)
            continue
        bounded.append(
            chapter.model_copy(
                update={
                    "pov_character": "",
                    "pov_character_id": "",
                    "pov_character_name": "",
                    "pov_switch": False,
                    "required_character_ids": [],
                    "support_character_ids": [],
                    "cast_plan": ChapterCastPlan(created_at=chapter.cast_plan.created_at),
                }
            )
        )
    return bounded


def _outline_title_repair_numbers(raw_chapters: Any, *, batch_start: int, batch_end: int) -> list[int]:
    if not isinstance(raw_chapters, list):
        return []
    numbers: list[int] = []
    for raw_chapter in raw_chapters:
        if not isinstance(raw_chapter, dict):
            continue
        try:
            chapter_number = int(raw_chapter.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        if not batch_start <= chapter_number <= batch_end:
            continue
        reason = outline_chapter_title_repair_reason(
            raw_chapter.get("title"),
            goal=raw_chapter.get("goal"),
            main_plot_points=raw_chapter.get("main_plot_points"),
            beats_summary=raw_chapter.get("beats_summary"),
        )
        if reason:
            numbers.append(chapter_number)
    return sorted(set(numbers))


async def _repair_outline_batch_titles(
    ctx: InitLongServiceContext,
    *,
    batch_data: dict[str, Any],
    batch_start: int,
    batch_end: int,
    total_chapters: int,
    synopsis: str,
    volume_mode_flag: bool,
    volumes: list[Any],
    raw_sink: list[str] | None = None,
) -> None:
    """Use POLISH_OUTLINE as a narrow title repair pass before merge/persistence."""
    raw_chapters = batch_data.get("chapters")
    repair_numbers = _outline_title_repair_numbers(
        raw_chapters,
        batch_start=batch_start,
        batch_end=batch_end,
    )
    if not repair_numbers or not isinstance(raw_chapters, list):
        return

    chapters: list[ChapterOutline] = []
    for raw_chapter in raw_chapters:
        if not isinstance(raw_chapter, dict):
            continue
        try:
            chapter = ChapterOutline.model_validate(raw_chapter)
        except Exception as exc:
            _log.debug("outline_title_repair_skipped_invalid_chapter | error=%s", exc)
            continue
        if batch_start <= chapter.chapter_number <= batch_end:
            chapters.append(chapter)
    if not chapters:
        return

    try:
        from novel_forge.pipeline.steps.polish_outline_step import (
            PolishOutlineInput,
            PolishOutlineStep,
        )

        polish_step = PolishOutlineStep(
            ctx.router,
            ctx.builder,
            settings=ctx.settings,
            trace=ctx.trace,
        )
        polish_result = await polish_step.run(
            PolishOutlineInput(
                story_outline=StoryOutline(
                    total_chapters=total_chapters,
                    synopsis=synopsis,
                    volume_mode=volume_mode_flag,
                    volumes=volumes,
                    chapters=chapters,
                ),
                user_hint=(
                    "只为标题缺失或不像标题的章节补写章名；"
                    "每个标题 2-10 个汉字，像目录标题，不要复制剧情句。"
                ),
                selected_suggestions=["仅补 title，不修改 goal、beats_summary 或其他剧情字段"],
                focus_fields=["title"],
                chapter_range=repair_numbers,
                polish_mode="title_repair",
            )
        )
    except Exception as exc:
        _log.warning("outline_title_repair_failed | chapters=%s | error=%s", repair_numbers, exc)
        return

    adjusted = polish_result.adjusted_outline
    if adjusted is None:
        return
    repaired_titles = {chapter.chapter_number: chapter.title for chapter in adjusted.chapters}
    changed_numbers: list[int] = []
    for raw_chapter in raw_chapters:
        if not isinstance(raw_chapter, dict):
            continue
        try:
            chapter_number = int(raw_chapter.get("chapter_number"))
        except (TypeError, ValueError):
            continue
        if chapter_number not in repair_numbers:
            continue
        title = repaired_titles.get(chapter_number, "")
        if outline_chapter_title_repair_reason(
            title,
            goal=raw_chapter.get("goal"),
            main_plot_points=raw_chapter.get("main_plot_points"),
            beats_summary=raw_chapter.get("beats_summary"),
        ):
            continue
        if raw_chapter.get("title") != title:
            raw_chapter["title"] = title
            changed_numbers.append(chapter_number)

    if changed_numbers and raw_sink is not None and raw_sink:
        raw_sink[0] = json.dumps(batch_data, ensure_ascii=False)
    if changed_numbers:
        ctx.on_step(
            "outline_title_repair_applied",
            {"chapters": changed_numbers, "mode": "title_repair"},
        )


_GENERIC_GOALS = frozenset(
    {
        "推进剧情",
        "推进故事",
        "继续故事",
        "发展情节",
        "故事推进",
        "情节推进",
        "继续推进",
        "剧情推进",
        "发展剧情",
        "advance the plot",
        "continue the story",
        "move the story forward",
    }
)
_PLACEHOLDER_TITLE_RE = re.compile(r"^(?:章名\d+|第\s*\d+\s*章(?:（?mock）?)?)$", re.IGNORECASE)
_GENERIC_GOAL_RE = re.compile(
    r"^(?:完成)?第\d+章(?:推进|继续|完成)?(?:故事)?(?:主线|剧情|情节)(?:推进)?[。.]?$"
)
_PLACEHOLDER_POINT_RE = re.compile(r"^(?:主线推进|节拍)\d+(?:-[A-Za-z])?[。.]?$")
_MIN_WORD_COUNT = 1000
_MAX_WORD_COUNT = 15000


def _outline_quality_text(value: Any) -> str:
    return "".join(str(value or "").split()).strip()


def _is_placeholder_title(value: Any) -> bool:
    return bool(_PLACEHOLDER_TITLE_RE.fullmatch(_outline_quality_text(value)))


def _is_generic_goal(value: Any) -> bool:
    text = _outline_quality_text(value)
    return bool(text in _GENERIC_GOALS or _GENERIC_GOAL_RE.fullmatch(text))


def _is_placeholder_point(value: Any) -> bool:
    return bool(_PLACEHOLDER_POINT_RE.fullmatch(_outline_quality_text(value)))


def _hook_description(chapter: ChapterOutline) -> str:
    hook = chapter.expected_hook
    if hook is None:
        return ""
    return str(
        getattr(hook, "hook_description", "")
        or getattr(hook, "description", "")
        or ""
    ).strip()


def _payoff_descriptions(chapter: ChapterOutline) -> list[str]:
    descriptions: list[str] = []
    for payoff in chapter.expected_payoffs or []:
        text = str(getattr(payoff, "description", "") or "").strip()
        if text:
            descriptions.append(text)
    return descriptions


def _outline_chapter_placeholder_score(chapter: ChapterOutline) -> int:
    score = 0
    if _is_placeholder_title(chapter.title):
        score += 2
    if _is_generic_goal(chapter.goal):
        score += 2
    if chapter.main_plot_points and all(_is_placeholder_point(p) for p in chapter.main_plot_points):
        score += 2
    if chapter.beats_summary and all(_is_placeholder_point(p) for p in chapter.beats_summary):
        score += 2
    hook_text = _hook_description(chapter)
    if hook_text and _is_placeholder_point(hook_text):
        score += 1
    payoff_texts = _payoff_descriptions(chapter)
    if payoff_texts and all(_is_placeholder_point(text) for text in payoff_texts):
        score += 1
    return score


def _critical_outline_quality_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [issue for issue in issues if issue.get("severity") == "critical"]


def _format_outline_quality_feedback(issues: list[dict[str, Any]]) -> str:
    lines = []
    for issue in issues[:12]:
        chapter = int(issue.get("chapter") or 0)
        chapter_label = f"第{chapter}章" if chapter > 0 else "本批"
        field = str(issue.get("field") or "unknown")
        message = str(issue.get("message") or "").strip()
        lines.append(f"- {chapter_label} / {field}: {message}")
    return "\n".join(lines)


def _extract_whitelisted_character_names(raw: Any, whitelist: list[str]) -> list[str]:
    text = str(raw or "").strip()
    if not text or not whitelist:
        return []
    if text in whitelist:
        return [text]
    ranked: list[tuple[int, int, str]] = []
    for order, name in enumerate(whitelist):
        pos = text.find(name)
        if pos >= 0:
            ranked.append((pos, order, name))
    if not ranked:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for _, _, name in sorted(ranked):
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _chapter_cast_evidence_values(chapter: ChapterOutline) -> list[Any]:
    """Return executable outline fields that can prove chapter participation."""
    values: list[Any] = [
        chapter.goal,
        *chapter.beats_summary,
        *chapter.main_plot_points,
        *chapter.subplot_points,
        *chapter.scene_design_goals,
        chapter.emotional_plan,
        chapter.expected_hook,
        *chapter.expected_payoffs,
    ]
    flattened: list[Any] = []

    def _append(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for item in value.values():
                _append(item)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                _append(item)
            return
        if hasattr(value, "model_dump"):
            _append(value.model_dump(mode="json", exclude={"schema_version", "created_at"}))
            return
        if _clean_outline_text(value):
            flattened.append(value)

    for value in values:
        _append(value)
    return flattened


def _normalize_catalog_character_fields(
    chapter: ChapterOutline,
    catalog: list[dict[str, Any]],
    design_entry: ChapterDesignMatrixEntry | None,
) -> ChapterOutline:
    """Resolve selected cast, never infer participation from arbitrary narrative mentions."""
    index = EntityReferenceIndex(catalog)
    remapped: dict[str, str] = {}
    ambiguous: set[str] = set()

    def remember(raw: str, name: str) -> None:
        resolved = index.resolve(name, entity_type="character")
        # Never reinterpret a registered location/item ID as a character.
        if not raw or index.contains(raw) or not resolved or raw in ambiguous:
            return
        if raw in remapped and remapped[raw] != resolved:
            ambiguous.add(raw)
            remapped.pop(raw)
        else:
            remapped[raw] = resolved

    display_names = chapter.involved_character_names or chapter.involved_characters
    if len(chapter.involved_character_ids) == len(display_names):
        for raw, name in zip(chapter.involved_character_ids, display_names, strict=True):
            remember(raw, name)
    pov_name = chapter.pov_character_name or chapter.pov_character
    remember(chapter.pov_character_id, pov_name)
    remember(chapter.cast_plan.pov_entity_id, pov_name)

    def resolve(raw: str, *, character: bool = True) -> str:
        value = _clean_outline_text(raw)
        return (
            index.resolve(value, entity_type="character" if character else None)
            or (remapped.get(value) if character else None)
            or value
        )

    def resolve_many(values: list[str], *, character: bool = True) -> list[str]:
        return _unique_strings([resolve(value, character=character) for value in values])

    pov_id = resolve(chapter.pov_character_id or chapter.cast_plan.pov_entity_id or pov_name)
    selected = resolve_many(chapter.involved_character_ids or display_names)
    required = resolve_many(
        chapter.required_character_ids or chapter.cast_plan.required_character_ids
    )
    support = resolve_many(chapter.support_character_ids or chapter.cast_plan.support_character_ids)
    cast = chapter.cast_plan
    if design_entry is not None:
        cast = design_entry.cast_plan
        pov_id = resolve(cast.pov_entity_id)
        required = resolve_many(cast.required_character_ids)
        # A textual match can corroborate a model-selected actor, but cannot
        # promote an unselected remembered/background entity into active cast.
        character_names = {
            name: entity_id
            for item in catalog
            for name in [str(item.get("name") or ""), *(item.get("aliases") or [])]
            if (entity_id := index.resolve(name, entity_type="character"))
        }
        evidence_ids = set(
            _entity_ids_from_texts(_chapter_cast_evidence_values(chapter), character_names)
        )
        support = [
            item
            for item in _unique_strings([*selected, *support])
            if item in evidence_ids or index.resolve(item, entity_type="character") is None
        ]
    else:
        support = _unique_strings([*selected, *support])
    required = _unique_strings([pov_id, *required])
    support = [item for item in support if item not in required]
    involved = _unique_strings([*required, *support])
    names = [
        name for item in involved if (name := index.display_name(item, entity_type="character"))
    ]
    resolved_pov_name = index.display_name(pov_id, entity_type="character")
    emotional = chapter.emotional_plan.model_copy(
        update={
            "subject_entity_id": resolve(chapter.emotional_plan.subject_entity_id or pov_id),
        }
    )
    return chapter.model_copy(
        update={
            "pov_character_id": pov_id,
            "pov_character_name": resolved_pov_name,
            "pov_character": resolved_pov_name,
            "involved_character_ids": involved,
            "required_character_ids": required,
            "support_character_ids": support,
            "involved_character_names": names,
            "involved_characters": names,
            "emotional_plan": emotional,
            "cast_plan": cast.model_copy(
                update={
                    "created_at": chapter.cast_plan.created_at,
                    "pov_entity_id": pov_id,
                    "required_character_ids": required,
                    "support_character_ids": support,
                    "mention_only_entity_ids": resolve_many(
                        cast.mention_only_entity_ids, character=False
                    ),
                    "forbidden_active_character_ids": resolve_many(
                        cast.forbidden_active_character_ids
                    ),
                }
            ),
        }
    )


def _normalize_outline_character_fields(
    chapter: ChapterOutline,
    *,
    character_whitelist: list[str] | None,
    entity_catalog: list[dict[str, Any]] | None = None,
    design_entry: ChapterDesignMatrixEntry | None = None,
) -> ChapterOutline:
    """Constrain structured outline identity fields to canonical upstream entities."""
    catalog = entity_catalog or []
    if catalog:
        return _normalize_catalog_character_fields(chapter, catalog, design_entry)

    whitelist = [
        str(name or "").strip() for name in (character_whitelist or []) if str(name or "").strip()
    ]
    if not whitelist:
        return chapter

    raw_pov = str(chapter.pov_character or "").strip()
    pov_matches = _extract_whitelisted_character_names(raw_pov, whitelist)
    normalized_pov = (
        raw_pov if raw_pov in whitelist else (pov_matches[0] if pov_matches else raw_pov)
    )

    normalized_involved = [normalized_pov] if normalized_pov in whitelist else []
    for value in _chapter_cast_evidence_values(chapter):
        for name in _extract_whitelisted_character_names(value, whitelist):
            if name not in normalized_involved:
                normalized_involved.append(name)

    # Legacy name-only callers have no authoritative IDs; do not mint any.
    return chapter.model_copy(
        update={
            "pov_character": normalized_pov,
            "pov_character_name": normalized_pov,
            "pov_character_id": "",
            "involved_characters": normalized_involved,
            "involved_character_names": normalized_involved,
            "involved_character_ids": [],
            "required_character_ids": [],
            "support_character_ids": [],
            "cast_plan": ChapterCastPlan(created_at=chapter.cast_plan.created_at),
        }
    )


def _outline_entity_audit(
    chapters: list[ChapterOutline],
    character_bible: Any,
    *,
    entity_catalog: list[dict[str, Any]] | None = None,
    design_by_chapter: dict[int, ChapterDesignMatrixEntry] | None = None,
) -> dict[str, Any]:
    """Audit structured character fields against canonical init names."""
    catalog = entity_catalog or []
    if catalog:
        by_id, _name_to_id = _catalog_indexes(catalog)
        character_ids = {
            entity_id for entity_id, item in by_id.items() if item.get("entity_type") == "character"
        }
        issues: list[dict[str, Any]] = []
        for chapter in chapters:
            observed: list[tuple[str, str]] = []
            if chapter.pov_character_id:
                observed.append(("pov_character_id", chapter.pov_character_id))
            for field_name, values in (
                ("involved_character_ids", chapter.involved_character_ids),
                ("required_character_ids", chapter.required_character_ids),
                ("support_character_ids", chapter.support_character_ids),
                ("cast_plan.required_character_ids", chapter.cast_plan.required_character_ids),
                ("cast_plan.support_character_ids", chapter.cast_plan.support_character_ids),
                (
                    "cast_plan.forbidden_active_character_ids",
                    chapter.cast_plan.forbidden_active_character_ids,
                ),
            ):
                for entity_id in values or []:
                    if entity_id:
                        observed.append((field_name, entity_id))
            if chapter.cast_plan.pov_entity_id:
                observed.append(("cast_plan.pov_entity_id", chapter.cast_plan.pov_entity_id))
            if chapter.emotional_plan.subject_entity_id:
                observed.append(
                    ("emotional_plan.subject_entity_id", chapter.emotional_plan.subject_entity_id)
                )

            for field, entity_id in observed:
                if entity_id in character_ids:
                    continue
                issues.append(
                    {
                        "field": field,
                        "chapter": chapter.chapter_number,
                        "severity": "critical",
                        "message": (
                            f"结构化角色字段出现未知或非角色 entity_id '{entity_id}'；"
                            "请使用 EntityRegistry 中的角色 entity_id。"
                        ),
                        "observed_entity_id": entity_id,
                    }
                )

            for entity_id in chapter.cast_plan.mention_only_entity_ids:
                if entity_id not in by_id:
                    issues.append(
                        {
                            "field": "cast_plan.mention_only_entity_ids",
                            "chapter": chapter.chapter_number,
                            "severity": "critical",
                            "message": f"仅提及实体未注册：'{entity_id}'。",
                            "observed_entity_id": entity_id,
                        }
                    )
            active_ids = set(chapter.involved_character_ids)
            forbidden = active_ids.intersection(chapter.cast_plan.forbidden_active_character_ids)
            if forbidden:
                issues.append(
                    {
                        "field": "cast_plan.forbidden_active_character_ids",
                        "chapter": chapter.chapter_number,
                        "severity": "critical",
                        "message": f"禁止主动出场的角色进入了 active cast：{sorted(forbidden)}",
                    }
                )
            design = (design_by_chapter or {}).get(chapter.chapter_number)
            if design is None:
                continue
            required = set(design.cast_plan.required_character_ids)
            involved = set(chapter.involved_character_ids)
            missing_required = sorted(required - involved)
            if missing_required:
                issues.append(
                    {
                        "field": "involved_character_ids",
                        "chapter": chapter.chapter_number,
                        "severity": "critical",
                        "message": (
                            f"章节缺少 ChapterDesignMatrix 要求的 active cast：{missing_required}"
                        ),
                        "missing_entity_ids": missing_required,
                    }
                )
            if chapter.pov_character_id != design.cast_plan.pov_entity_id:
                issues.append(
                    {
                        "field": "pov_character_id",
                        "chapter": chapter.chapter_number,
                        "severity": "critical",
                        "message": (
                            "章节 POV entity_id 与 ChapterDesignMatrix 不一致："
                            f"{chapter.pov_character_id} != {design.cast_plan.pov_entity_id}"
                        ),
                    }
                )
            emotional = chapter.emotional_plan
            if not (
                emotional.subject_entity_id
                and emotional.pressure_source
                and emotional.relationship_choice
                and emotional.exit_aftertaste
            ):
                issues.append(
                    {
                        "field": "emotional_plan",
                        "chapter": chapter.chapter_number,
                        "severity": "critical",
                        "message": ("emotional_plan 必须包含主体、压力来源、关系选择和退出余韵。"),
                    }
                )
            allowed_emotional_subjects = {
                design.cast_plan.pov_entity_id,
                *design.cast_plan.required_character_ids,
            }
            if emotional.subject_entity_id not in allowed_emotional_subjects:
                issues.append(
                    {
                        "field": "emotional_plan.subject_entity_id",
                        "chapter": chapter.chapter_number,
                        "severity": "critical",
                        "message": (
                            "emotional_plan 主体必须是本章 POV 或 required cast："
                            f"{emotional.subject_entity_id}"
                        ),
                    }
                )
        return {
            "ok": not issues,
            "issues": issues,
            "canonical_entity_ids": sorted(character_ids),
        }

    canonical_names = set(canonical_character_names(character_bible))
    if not canonical_names:
        return {"ok": True, "issues": [], "canonical_names": []}

    issues: list[dict[str, Any]] = []
    for chapter in chapters:
        observed: list[tuple[str, str]] = []
        pov = str(chapter.pov_character or "").strip()
        if pov:
            observed.append(("pov_character", pov))
        for name in chapter.involved_characters or []:
            clean_name = str(name or "").strip()
            if clean_name:
                observed.append(("involved_characters", clean_name))

        for field, name in observed:
            if name in canonical_names:
                continue
            issues.append(
                {
                    "field": field,
                    "chapter": chapter.chapter_number,
                    "severity": "critical",
                    "message": (
                        f"结构化角色字段出现非 canonical 名称 '{name}'；"
                        "请使用角色圣经/实体注册表中的正式名称。"
                    ),
                    "observed_name": name,
                }
            )

    return {
        "ok": not issues,
        "issues": issues,
        "canonical_names": sorted(canonical_names),
    }


def _revalidate_resumed_outline_identity(
    ctx: InitLongServiceContext,
    *,
    chapters: list[ChapterOutline],
    records: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
    design_by_chapter: dict[int, ChapterDesignMatrixEntry],
    hard_through_chapter: int,
    source_hashes: dict[str, str],
    session_payload: dict[str, Any],
) -> tuple[list[ChapterOutline], list[dict[str, Any]]]:
    """Revalidate before reuse, preserving old bytes before changing the accepted ledger.

    Checkpoint and session writes are atomic individually. If interrupted between
    them, the existing hash gate rejects the uncommitted migration; backups retain
    the prior checkpoint and ledger instead of silently accepting mismatched data.
    """
    if not catalog or not chapters:
        return chapters, records
    by_number = {chapter.chapter_number: chapter for chapter in chapters}
    audits = {
        number: _outline_entity_audit(
            [chapter], {}, entity_catalog=catalog, design_by_chapter=design_by_chapter
        )
        for number, chapter in by_number.items()
    }
    first_invalid = min((number for number, audit in audits.items() if not audit["ok"]), default=0)
    if first_invalid:
        # Rebuild the whole containing batch; no partially accepted batch enters
        # the transactional ledger, even if its preceding chapters were valid.
        first_invalid = next(
            (
                int(record["batch_start"])
                for record in records
                if int(record["batch_start"]) <= first_invalid <= int(record["batch_end"])
            ),
            first_invalid,
        )
        ctx.on_step(
            "plan_outline_resume_identity_rejected",
            {
                "batch_start": first_invalid,
                "action": "regenerate_from_invalid_batch",
                "issues": [issue for audit in audits.values() for issue in audit["issues"]],
            },
        )
    accepted = [
        chapter
        for chapter in chapters
        if not first_invalid or chapter.chapter_number < first_invalid
    ]
    accepted = _apply_progressive_commitment_boundary(
        accepted, hard_through_chapter=hard_through_chapter
    )
    accepted_by_number = {chapter.chapter_number: chapter for chapter in accepted}
    kept_records: list[dict[str, Any]] = []
    changed = bool(first_invalid)
    backup_dir = ctx.layout.states_dir / "outline_identity_backups"

    def backup_session() -> None:
        if session_payload:
            path = backup_dir / f"session_{hash_payload(session_payload)}.json"
            if not ctx.storage.exists(path):
                ctx.storage.save_json(path, session_payload)

    if changed:
        backup_session()
    for record in records:
        start, end = int(record["batch_start"]), int(record["batch_end"])
        if first_invalid and end >= first_invalid:
            break
        batch = [accepted_by_number[number] for number in range(start, end + 1)]
        path = ctx.layout.states_dir / "outline_batches" / f"batch_{start:03d}_{end:03d}.json"
        previous = ctx.storage.load_json(path)
        payload = [chapter.model_dump(mode="json") for chapter in batch]
        if previous.get("chapters") == payload and previous.get("source_hashes") == source_hashes:
            kept_records.append(record)
            continue
        backup_session()
        backup = backup_dir / f"{path.stem}_{previous['content_hash']}.json"
        if not ctx.storage.exists(backup):
            ctx.storage.save_json(backup, previous)
        audit = _outline_entity_audit(
            batch, {}, entity_catalog=catalog, design_by_chapter=design_by_chapter
        )
        checkpoint = _save_outline_batch_checkpoint(
            ctx.storage,
            ctx.layout,
            total_chapters=int(previous["total_chapters"]),
            batch_start=start,
            batch_end=end,
            chapters=batch,
            missing_chapters=[],
            status="accepted",
            raw_response=previous.get("raw_response"),
            source_hashes=source_hashes,
            entity_audit=audit,
            metadata={
                **(previous.get("metadata") or {}),
                "identity_migration": OUTLINE_IDENTITY_CONTRACT_VERSION,
                "previous_content_hash": previous["content_hash"],
            },
            session_id=str(previous.get("session_id") or ""),
        )
        kept_records.append(
            {
                **record,
                "content_hash": checkpoint["content_hash"],
                "source_hash": checkpoint.get("source_hash", ""),
                "entity_audit": {"ok": True, "issue_count": 0},
            }
        )
        changed = True
    if changed and session_payload:
        updated_session = {
            **session_payload,
            "accepted_batches": kept_records,
            "chapters_done": len(accepted),
            "latest_chapter_number": max(accepted_by_number, default=0),
            "last_safe_chapter": max(accepted_by_number, default=0),
            "conversation_history": [],
            "identity_contract_version": OUTLINE_IDENTITY_CONTRACT_VERSION,
        }
        ctx.storage.save_json(ctx.layout.outline_session_path, updated_session)
        session_payload.update(updated_session)
        # These caches derive from the old projections and are rebuilt below.
        if ctx.layout.outline_tracker_path.exists():
            ctx.layout.outline_tracker_path.unlink()
        ctx.on_step(
            "plan_outline_resume_identity_migrated",
            {
                "safe_chapters_done": len(accepted),
                "backup_path": str(backup_dir),
                "identity_contract_version": OUTLINE_IDENTITY_CONTRACT_VERSION,
            },
        )
    return accepted, kept_records


def _safe_committed_chapter_map(
    chapter_map: dict[int, ChapterOutline],
    *,
    batch_start: int,
    batch_end: int,
) -> dict[int, ChapterOutline]:
    """Return the map without the active unaccepted batch range."""
    return {
        number: chapter
        for number, chapter in chapter_map.items()
        if number < batch_start or number > batch_end
    }


def _outline_safe_progress_payload(
    chapter_map: dict[int, ChapterOutline],
    *,
    total_chapters: int,
    batch_start: int | None = None,
    batch_end: int | None = None,
    current_batch_status: str = "",
) -> dict[str, Any]:
    """Describe the transactional outline resume point.

    Only the continuous accepted prefix is safe to advertise as resumable.
    The active batch may have produced visible text, but it is not a recovery
    point until its checkpoint is accepted and recorded in the session ledger.
    """
    numbers = {
        int(number)
        for number, chapter in chapter_map.items()
        if 1 <= int(number) <= total_chapters
        and chapter.notes != PipelineConstants.PLACEHOLDER_NOTE
        and chapter.beats_summary
    }
    safe_saved_chapter = 0
    for number in range(1, total_chapters + 1):
        if number not in numbers:
            break
        safe_saved_chapter = number

    payload: dict[str, Any] = {
        "chapters_done": safe_saved_chapter,
        "chapters_total": total_chapters,
        "total_chapters": total_chapters,
        "safe_chapters_done": safe_saved_chapter,
        "safe_saved_chapter": safe_saved_chapter,
        "safe_next_chapter": min(safe_saved_chapter + 1, total_chapters)
        if total_chapters > 0
        else 1,
    }
    if batch_start is not None and batch_end is not None:
        payload.update(
            {
                "batch_start": batch_start,
                "batch_end": batch_end,
                "current_batch_status": current_batch_status,
            }
        )
        if safe_saved_chapter > 0:
            prefix = f"章节大纲已安全保存到第{safe_saved_chapter}章"
        else:
            prefix = "章节大纲尚无安全保存章节"
        if current_batch_status == "accepted":
            suffix = f"第{batch_start}-{batch_end}章已提交为可恢复批次。"
        elif current_batch_status in {"incomplete", "rejected"}:
            suffix = (
                f"第{batch_start}-{batch_end}章未提交；"
                f"断点续跑将从第{payload['safe_next_chapter']}章继续。"
            )
        elif current_batch_status == "quality_retry":
            suffix = (
                f"第{batch_start}-{batch_end}章正在重试；"
                "当前批次未验收，不计入断点恢复点。"
            )
        else:
            suffix = (
                f"正在生成第{batch_start}-{batch_end}章；"
                "当前批次未验收，不计入断点恢复点。"
            )
        payload["message"] = f"{prefix}；{suffix}"
    return payload


def _check_batch_quality(
    *,
    chapters: list[ChapterOutline],
    blueprint: NarrativeBlueprint,
    prev_chapters: list[ChapterOutline],
) -> list[dict[str, Any]]:
    """Lightweight rule-based quality check for a batch of outline chapters.

    Pure function — zero LLM calls, zero I/O.  Returns a list of issue dicts
    with keys ``field``, ``chapter``, ``severity``, ``message``.
    """
    issues: list[dict[str, Any]] = []
    if not chapters:
        return issues

    # ── 1. Goal specificity ──────────────────────────────────────────
    for chapter in chapters:
        goal = " ".join(str(chapter.goal or "").split()).strip()
        if not goal:
            issues.append({
                "field": "goal",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 goal 为空",
            })
        elif _is_generic_goal(goal):
            issues.append({
                "field": "goal",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 goal 疑似模板占位: '{goal}'",
            })
        elif len(goal) < 4 or goal in _GENERIC_GOALS:
            issues.append({
                "field": "goal",
                "chapter": chapter.chapter_number,
                "severity": "warning",
                "message": f"章节 {chapter.chapter_number} 的 goal 过于笼统: '{goal}'",
            })

        if _is_placeholder_title(chapter.title):
            issues.append({
                "field": "title",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 title 疑似模板占位: '{chapter.title}'",
            })
        if chapter.main_plot_points and all(
            _is_placeholder_point(point) for point in chapter.main_plot_points
        ):
            issues.append({
                "field": "main_plot_points",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 main_plot_points 疑似模板占位",
            })
        if chapter.beats_summary and all(_is_placeholder_point(beat) for beat in chapter.beats_summary):
            issues.append({
                "field": "beats_summary",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 beats_summary 疑似模板占位",
            })
        hook_text = _hook_description(chapter)
        if hook_text and _is_placeholder_point(hook_text):
            issues.append({
                "field": "expected_hook",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 expected_hook 复用模板占位内容",
            })
        payoff_texts = _payoff_descriptions(chapter)
        if payoff_texts and all(_is_placeholder_point(text) for text in payoff_texts):
            issues.append({
                "field": "expected_payoffs",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 expected_payoffs 复用模板占位内容",
            })
        for conflict in find_internal_duration_claim_conflicts(chapter):
            days = " / ".join(f"{day}日" for day in conflict["days"])
            evidence = "；".join(
                f"{day}日={','.join(phrases)}"
                for day, phrases in conflict["evidence"].items()
            )
            issues.append({
                "field": "duration_consistency",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": (
                    f"章节 {chapter.chapter_number} 对「{conflict['subject']}」同时声明了"
                    f" {days} 两组总期限（{evidence}）。"
                    "若其中一处表示已过进度，请改写为「第 N 日」，"
                    "不要写成「N 日停职/受审结束」。"
                ),
            })

    # ── 2. Time anchor monotonicity ──────────────────────────────────
    all_chapters = sorted(
        list(prev_chapters) + list(chapters),
        key=lambda c: c.chapter_number,
    )
    last_anchor_chapter: int | None = None
    last_anchor_text: str = ""
    for chapter in all_chapters:
        if chapter.is_flashback:
            continue
        anchor = " ".join(str(chapter.time_anchor or "").split()).strip()
        if not anchor:
            continue
        if last_anchor_chapter is not None and anchor == last_anchor_text:
            # identical anchor — not a regression, just skip
            continue
        # Only flag when the anchor text is identical to a *previous* chapter's
        # anchor (exact-match regression).  Semantic ordering of free-text
        # anchors (e.g. "末世第3天" vs "末世第5天") is beyond a pure-rule check.
        for prev in all_chapters:
            if prev.chapter_number >= chapter.chapter_number:
                break
            if prev.is_flashback:
                continue
            prev_anchor = " ".join(str(prev.time_anchor or "").split()).strip()
            if prev_anchor and anchor == prev_anchor:
                issues.append({
                    "field": "time_anchor",
                    "chapter": chapter.chapter_number,
                    "severity": "warning",
                    "message": (
                        f"章节 {chapter.chapter_number} 的 time_anchor '{anchor}'"
                        f" 与章节 {prev.chapter_number} 完全相同，可能存在时间线回退"
                    ),
                })
                break
        last_anchor_chapter = chapter.chapter_number
        last_anchor_text = anchor

    # ── 3. POV consistency ───────────────────────────────────────────
    blueprint_povs: set[str] = set()
    for arc in blueprint.character_arcs or []:
        name = str(getattr(arc, "character", "") or "").strip()
        if name:
            blueprint_povs.add(name)
    for phase in blueprint.narrative_phases or []:
        for name in getattr(phase, "key_characters", []) or []:
            name_str = str(name or "").strip()
            if name_str:
                blueprint_povs.add(name_str)
    if blueprint_povs:
        for chapter in chapters:
            pov = str(chapter.pov_character or "").strip()
            if pov and pov not in blueprint_povs:
                issues.append({
                    "field": "pov_character",
                    "chapter": chapter.chapter_number,
                    "severity": "warning",
                    "message": (
                        f"章节 {chapter.chapter_number} 的 POV 角色 '{pov}'"
                        f" 不在蓝图指定的角色列表中"
                    ),
                })

    # ── 4. Word count reasonableness ─────────────────────────────────
    for chapter in chapters:
        wc = int(chapter.expected_word_count or 0)
        if wc <= 0:
            issues.append({
                "field": "expected_word_count",
                "chapter": chapter.chapter_number,
                "severity": "critical",
                "message": f"章节 {chapter.chapter_number} 的 expected_word_count 为 {wc}，应大于 0",
            })
        elif wc < _MIN_WORD_COUNT or wc > _MAX_WORD_COUNT:
            issues.append({
                "field": "expected_word_count",
                "chapter": chapter.chapter_number,
                "severity": "warning",
                "message": (
                    f"章节 {chapter.chapter_number} 的 expected_word_count={wc}"
                    f" 超出合理范围 ({_MIN_WORD_COUNT}-{_MAX_WORD_COUNT})"
                ),
            })

    # ── 5. Batch-level template degeneration ─────────────────────────
    if len(chapters) >= 2:
        word_counts = {int(ch.expected_word_count or 0) for ch in chapters}
        settings = {_outline_quality_text(ch.setting) for ch in chapters}
        povs = {_outline_quality_text(ch.pov_character) for ch in chapters}
        involved = {
            tuple(_outline_quality_text(name) for name in (ch.involved_characters or []))
            for ch in chapters
        }
        placeholder_scores = [_outline_chapter_placeholder_score(ch) for ch in chapters]
        if (
            word_counts == {900}
            and len(settings) == 1
            and len(povs) == 1
            and len(involved) == 1
            and sum(1 for score in placeholder_scores if score >= 4) >= max(2, len(chapters) // 2)
        ):
            issues.append({
                "field": "batch",
                "chapter": 0,
                "severity": "critical",
                "message": "本批章节呈现统一 900 字、同场景、同角色且多字段模板占位，疑似大纲退化",
            })

    return issues


async def _batched_generate_outline(
    ctx: InitLongServiceContext,
    *,
    existing_outline: StoryOutline | None,
    outline_ctx: dict[str, Any],
    blueprint: NarrativeBlueprint,
    total_chapters: int,
    words_per_chapter: int,
    use_volume_mode: bool,
    effective_chapters_per_volume: int,
    character_bible: Any = None,
    entity_registry: Any | None = None,
    editorial_contract: Any | None = None,
    on_outline_batch_ready: Callable[[int, int, list[ChapterOutline]], None] | None = None,
    target_start_chapter: int = 1,
    target_end_chapter: int | None = None,
    design_through_chapter: int | None = None,
    preserve_committed_chapters: bool = False,
) -> StoryOutline:
    """Generate (or continue) a story outline in batches, guided by blueprint."""
    storage = ctx.storage
    settings = ctx.settings
    on_step = ctx.on_step
    layout = ctx.layout
    # A published outline is authoritative when extending its planning horizon.
    # Its temporary generation session is normally deleted on successful init.
    committed: dict[int, ChapterOutline] = {}
    if preserve_committed_chapters and existing_outline is not None:
        committed_through = int(existing_outline.hard_through_chapter or existing_outline.total_chapters)
        committed = {
            chapter.chapter_number: chapter.model_copy(deep=True)
            for chapter in existing_outline.chapters
            if chapter.chapter_number <= committed_through
        }

    def restore_committed(outline: StoryOutline) -> StoryOutline:
        if not committed:
            return outline
        chapters = {chapter.chapter_number: chapter for chapter in outline.chapters}
        chapters.update(committed)
        return outline.model_copy(update={"chapters": [chapters[n] for n in sorted(chapters)]})

    outline_density = _outline_density_settings(settings)
    outline_character_whitelist = list(canonical_character_names(character_bible))
    target_start_chapter = max(1, min(int(target_start_chapter), total_chapters))
    target_end_chapter = max(
        target_start_chapter,
        min(int(target_end_chapter or total_chapters), total_chapters),
    )
    design_end = max(
        1,
        min(int(design_through_chapter or target_end_chapter), total_chapters),
    )
    chapter_design_matrix = build_chapter_design_matrix(
        blueprint=blueprint,
        total_chapters=design_end,
        entity_registry=entity_registry,
        character_bible=character_bible,
    )
    # Keep the exact catalog used by outline generation available to downstream
    # contract/source-artifact stages in the same run.  The persisted copy is
    # still required for crash recovery.
    outline_ctx["chapter_design_matrix"] = chapter_design_matrix.model_dump(mode="json")
    chapter_design_by_number = chapter_design_matrix.by_chapter()
    outline_entity_catalog = list(chapter_design_matrix.entity_catalog)
    # Identity is global; only commitment is limited by the hard planning horizon.
    outline_ctx["outline_entity_catalog"] = outline_entity_catalog
    outline_ctx["outline_hard_through_chapter"] = design_end
    try:
        storage.save_json(
            layout.plans_dir / "chapter_design_matrix.json",
            chapter_design_matrix.model_dump(mode="json"),
        )
        on_step(
            "plan_chapter_design_matrix",
            {
                "chapters": len(chapter_design_matrix.chapters),
                "entities": len(chapter_design_matrix.entity_catalog),
                "path": str(layout.plans_dir / "chapter_design_matrix.json"),
            },
        )
    except Exception as exc:
        _log.warning("chapter_design_matrix_persist_failed | error=%s", exc)

    batch_size = effective_outline_batch_size(
        ctx,
        total_chapters=total_chapters,
    )
    record_effective_init_batch_size(
        ctx,
        artifact="outline",
        task_type=TaskType.PLAN_OUTLINE_BATCH,
        batch_size=batch_size,
        total_chapters=total_chapters,
    )
    outline_thinking_batch = ctx.is_outline_option_enabled(
        capability="thinking",
        enabled=settings.outline_thinking,
        allowed_providers_raw=settings.outline_thinking_providers,
        allowed_models_raw=settings.outline_thinking_models,
        task_type=TaskType.PLAN_OUTLINE_BATCH,
    )
    outline_thinking_continue = ctx.is_outline_option_enabled(
        capability="thinking",
        enabled=settings.outline_thinking,
        allowed_providers_raw=settings.outline_thinking_providers,
        allowed_models_raw=settings.outline_thinking_models,
        task_type=TaskType.PLAN_OUTLINE_CONTINUE,
    )
    outline_multi_turn_continue = ctx.is_outline_option_enabled(
        capability="multi_turn",
        enabled=settings.outline_multi_turn,
        allowed_providers_raw=settings.outline_multi_turn_providers,
        allowed_models_raw=settings.outline_multi_turn_models,
        task_type=TaskType.PLAN_OUTLINE_CONTINUE,
    )

    outline_tracker_enabled = getattr(settings, "outline_tracker_enabled", True)

    outline_tracker: HybridOutlineTracker | None = None

    # Synopsis and volumes come from the blueprint
    synopsis: str = blueprint.synopsis
    volumes: list[Any] = list(blueprint.volumes)
    volume_mode_flag: bool = blueprint.volume_mode
    reveal_guard_hashes = outline_reveal_guard_input_hashes(editorial_contract)
    batch_source_hashes = {
        **reveal_guard_hashes,
        "outline_identity_contract": OUTLINE_IDENTITY_CONTRACT_VERSION,
        "entity_catalog": hash_payload(
            [
                {key: item.get(key) for key in ("entity_id", "name", "entity_type", "aliases")}
                for item in outline_entity_catalog
            ]
        ),
        "hard_through_chapter": str(design_end),
    }
    reveal_guard_session_ready = _outline_session_matches_reveal_guard(
        storage,
        layout,
        reveal_guard_hashes,
    )
    if (
        reveal_guard_hashes
        and existing_outline is not None
        and not reveal_guard_session_ready
        and not preserve_committed_chapters
    ):
        on_step(
            "plan_outline_resume_rejected",
            {
                "reason": "reveal_guard_session_mismatch",
                "action": "regenerate_outline",
            },
        )
        existing_outline = None
        if layout.outline_session_path.exists():
            layout.outline_session_path.unlink()
        if layout.outline_tracker_path.exists():
            layout.outline_tracker_path.unlink()

    outline_session_payload: dict[str, Any] = {}
    if reveal_guard_session_ready and storage.exists(layout.outline_session_path):
        try:
            loaded_session = storage.load_json(layout.outline_session_path)
        except Exception:
            loaded_session = {}
        if (
            isinstance(loaded_session, dict)
            and loaded_session.get("total_chapters") == total_chapters
        ):
            outline_session_payload = loaded_session

    # ── Determine starting state ────────────────────────────────────
    if existing_outline is None:
        recovered_chapters = (
            _load_partial_outline_chapters_from_session(
                storage,
                layout,
                total_chapters=total_chapters,
            )
            if reveal_guard_session_ready
            else []
        )
        if recovered_chapters:
            existing_outline = StoryOutline(
                total_chapters=total_chapters,
                hard_through_chapter=design_end,
                planned_through_chapter=target_end_chapter,
                synopsis=synopsis,
                volume_mode=volume_mode_flag,
                volumes=volumes,
                chapters=recovered_chapters,
            )

    if existing_outline is not None:
        accumulated_chapters: list[ChapterOutline] = [
            ch
            for ch in existing_outline.chapters
            if ch.notes != PipelineConstants.PLACEHOLDER_NOTE and ch.beats_summary
        ]
        accumulated_chapters = [
            _normalize_outline_character_fields(
                chapter,
                character_whitelist=outline_character_whitelist,
                entity_catalog=outline_entity_catalog,
                design_entry=chapter_design_by_number.get(chapter.chapter_number),
            )
            for chapter in accumulated_chapters
        ]
    else:
        accumulated_chapters = []

    recovered_batch_records = (
        _load_outline_session_accepted_batches(
            storage,
            layout,
            total_chapters=total_chapters,
            session_payload=outline_session_payload,
        )
        if accumulated_chapters
        else []
    )
    outline_session_id = (
        str(outline_session_payload.get("session_id") or "").strip()
        if recovered_batch_records
        else ""
    ) or uuid.uuid4().hex

    accumulated_chapters, recovered_batch_records = _revalidate_resumed_outline_identity(
        ctx,
        chapters=accumulated_chapters,
        records=recovered_batch_records,
        catalog=outline_entity_catalog,
        design_by_chapter=chapter_design_by_number,
        hard_through_chapter=design_end,
        source_hashes=batch_source_hashes,
        session_payload=outline_session_payload,
    )
    if committed:
        accumulated_by_number = {ch.chapter_number: ch for ch in accumulated_chapters}
        accumulated_by_number.update({n: chapter.model_copy(deep=True) for n, chapter in committed.items()})
        accumulated_chapters = [accumulated_by_number[n] for n in sorted(accumulated_by_number)]

    if outline_tracker_enabled:
        outline_tracker = _initialize_outline_tracker(
            outline_ctx=outline_ctx,
            blueprint=blueprint,
            existing_chapters=accumulated_chapters,
            batch_size=batch_size,
        )
        # Resume: overlay persisted tracker state if it exists (overrides re-init from chapters)
        if accumulated_chapters:
            loaded_tracker = _load_outline_tracker_state(storage, layout)
            if loaded_tracker is not None:
                outline_tracker = loaded_tracker
                _log.info(
                    "outline_tracker_resumed | chapters_done=%d",
                    len(accumulated_chapters),
                )

    episodic_memory: EpisodicMemory | None = None
    if getattr(settings, "memory_episodic_enabled", True):
        vector_store_backend = (
            str(getattr(settings, "memory_vector_store_backend", "zvec") or "zvec")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if getattr(settings, "memory_use_mock_embeddings", False):
            vector_store_backend = "in_memory"
        vector_store_kwargs: dict[str, Any] = {
            "vector_store_backend": vector_store_backend,
            "vector_store_path": layout.memory_dir / "zvec_outline_vectors",
            "zvec_index_type": getattr(settings, "memory_zvec_index_type", "hnsw"),
            "zvec_memory_limit_mb": int(
                getattr(settings, "memory_zvec_memory_limit_mb", 512) or 512
            ),
        }
        if getattr(settings, "memory_use_mock_embeddings", False):
            _log.info("init_outline_using_mock_embeddings | forced_by_settings=true")
            episodic_memory = EpisodicMemory(
                use_mock_embeddings=True,
                **vector_store_kwargs,
            )
        else:
            embedding_profile_id = getattr(settings, "memory_embedding_profile_id", None)
            embedding_config = _get_embedding_config(embedding_profile_id)
            if embedding_config is None:
                embedding_config = {
                    "provider": "ollama",
                    "model": getattr(settings, "ollama_embedding_model", "nomic-embed-text"),
                    "base_url": getattr(settings, "ollama_base_url", "http://localhost:11434/v1"),
                }
            episodic_memory = EpisodicMemory(
                embedding_config=embedding_config,
                use_mock_embeddings=False,
                **vector_store_kwargs,
            )

    # ── Compute batches that still need to be generated ─────────────
    done_numbers = {ch.chapter_number for ch in accumulated_chapters}
    remaining = [
        i for i in range(target_start_chapter, target_end_chapter + 1) if i not in done_numbers
    ]

    if not remaining:
        accumulated_chapters = _apply_progressive_commitment_boundary(
            accumulated_chapters,
            hard_through_chapter=design_end,
        )
        outline = StoryOutline(
            total_chapters=total_chapters,
            hard_through_chapter=design_end,
            planned_through_chapter=target_end_chapter,
            synopsis=synopsis,
            volume_mode=volume_mode_flag,
            volumes=volumes,
            chapters=accumulated_chapters,
        )
        normalization_end = max(
            target_end_chapter,
            max((chapter.chapter_number for chapter in accumulated_chapters), default=0),
        )
        outline = outline_h.normalize_outline_chapters(
            outline, total_chapters=normalization_end, words_per_chapter=words_per_chapter
        )
        outline.total_chapters = total_chapters
        outline = outline_h.backfill_involved_characters(outline, character_bible)
        outline = outline_h.normalize_outline_volumes(
            outline,
            total_chapters=total_chapters,
            use_volume_mode=use_volume_mode,
            chapters_per_volume=effective_chapters_per_volume,
        )
        outline = restore_committed(outline)
        on_step("plan_outline", outline)
        storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
        manifest_for_context(ctx).record_success(
            artifact="outline",
            workflow="init_long",
            step="plan_outline",
            input_hashes=outline_reveal_guard_input_hashes(editorial_contract),
            output_hashes={"outline": hash_payload(outline)},
            paths={"outline": str(layout.outline_path)},
            metadata={
                "batch_size": batch_size,
                "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
                "source": "accepted_outline_session",
            },
        )
        if layout.outline_session_path.exists():
            layout.outline_session_path.unlink()
        if layout.outline_tracker_path.exists():
            layout.outline_tracker_path.unlink()
        return outline

    batches = outline_h.build_outline_batches(remaining, batch_size)

    is_first_batch = len(accumulated_chapters) == 0
    chapter_map: dict[int, ChapterOutline] = {ch.chapter_number: ch for ch in accumulated_chapters}
    accepted_batch_records: list[dict[str, Any]] = list(recovered_batch_records)

    # Emit an immediate progress event so the UI advances to "章节大纲"
    # before the first (potentially slow) LLM call begins.  This reports only
    # the transactional safe point, not the active batch.
    on_step(
        "plan_outline_starting",
        {
            **_outline_safe_progress_payload(
                chapter_map,
                total_chapters=total_chapters,
            ),
            "total_chapters": total_chapters,
            "chapters_remaining": len(remaining),
        },
    )

    history_window = PipelineConstants.CONVERSATION_HISTORY_WINDOW
    conversation_history: list[dict[str, str]] = (
        _load_outline_conversation_history(
            storage,
            layout,
            total_chapters=total_chapters,
            history_window_rounds=history_window,
        )
        if outline_multi_turn_continue
        else []
    )

    def _build_continue_context(
        *,
        start: int,
        end: int,
        phase_guidance: str | None,
        phase_rhythm_guidance: str | None,
        is_final_batch: bool,
        tracker_context: dict[str, Any] | None = None,
        outline_quality_feedback: str | None = None,
    ) -> dict[str, Any]:
        """Build a complete PLAN_OUTLINE_CONTINUE context for both continue and repair calls."""

        reveal_window = build_outline_reveal_window(
            editorial_contract,
            batch_start=start,
            batch_end=end,
            total_chapters=total_chapters,
            settings=settings,
        )
        return {
            "batch_start": start,
            "batch_end": end,
            "words_per_chapter": outline_ctx["words_per_chapter"],
            "spec": outline_ctx.get("spec"),
            "story_bible": outline_ctx.get("story_bible"),
            "character_bible": outline_ctx.get("character_bible"),
            "style_profile": outline_ctx.get("style_profile"),
            "blueprint": blueprint,
            "phase_guidance": phase_guidance or None,
            "phase_rhythm_guidance": phase_rhythm_guidance or None,
            "is_final_batch": is_final_batch,
            "blueprint_element_selection": blueprint.element_selection,
            "outline_tracker_context": tracker_context if tracker_context else None,
            "outline_density": outline_density,
            "outline_character_whitelist": outline_character_whitelist,
            "outline_entity_catalog": outline_entity_catalog,
            "outline_hard_through_chapter": design_end,
            "chapter_design_matrix": _scoped_design_matrix_payload(
                chapter_design_matrix,
                batch_start=start,
                batch_end=end,
            ),
            "reveal_window": reveal_window if reveal_window else None,
            "outline_quality_feedback": outline_quality_feedback or None,
        }

    def _outline_batch_max_tokens(task_type: TaskType, start: int, end: int) -> int:
        chapter_count = max(1, end - start + 1)
        target_output_chars = outline_batch_target_output_chars(chapter_count)
        return calculate_route_aware_max_tokens(
            ctx.router,
            task_type,
            target_output_chars,
            prompt_overhead=4000,
            min_tokens=8192,
        )

    for batch_start, batch_end in batches:
        is_final = batch_end >= total_chapters
        pending_batch = {
            "batch_start": batch_start,
            "batch_end": batch_end,
            "status": "pending",
        }
        _persist_partial_outline_state(
            storage,
            layout,
            total_chapters=total_chapters,
            synopsis=synopsis,
            volume_mode_flag=volume_mode_flag,
            volumes=volumes,
            chapter_map=chapter_map,
            conversation_history=conversation_history,
            history_window_rounds=history_window,
            session_metadata={
                "status": "running",
                "session_id": outline_session_id,
                "pending_batch": pending_batch,
                "accepted_batches": accepted_batch_records,
                "reveal_guard_input_hashes": reveal_guard_hashes,
                "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
            },
        )
        on_step(
            f"plan_outline_batch_{batch_start}_{batch_end}",
            {
                **_outline_safe_progress_payload(
                    chapter_map,
                    total_chapters=total_chapters,
                    batch_start=batch_start,
                    batch_end=batch_end,
                    current_batch_status="pending",
                ),
                "chapters_remaining": len(
                    [number for number in range(1, total_chapters + 1) if number not in chapter_map]
                ),
                "pending_batch": pending_batch,
            },
        )

        raw_sink: list[str] = []
        history_len_before_batch = len(conversation_history)

        # If resume history is unavailable/corrupted, fall back to batch mode so
        # continuation can still proceed from persisted partial outline safely.
        if is_first_batch or not outline_multi_turn_continue or not conversation_history:
            prev_chapters = (
                sorted(chapter_map.values(), key=lambda c: c.chapter_number)[-batch_size:]
                if chapter_map
                else []
            )

            outline_tracker_context = {}
            if outline_tracker is not None or episodic_memory is not None:
                outline_tracker_context = await _build_outline_tracker_context(
                    tracker=outline_tracker,
                    episodic_memory=episodic_memory,
                    current_chapter=batch_start,
                    existing_chapters=list(chapter_map.values()),
                )

            scoped_blueprint = outline_h.extract_scoped_blueprint(
                blueprint, batch_start, batch_end, total_chapters
            )
            batch_ctx = {
                **outline_ctx,
                "blueprint": scoped_blueprint,
                "batch_start": batch_start,
                "batch_end": batch_end,
                "previous_chapters": prev_chapters if prev_chapters else None,
                "outline_tracker_context": outline_tracker_context
                if outline_tracker_context
                else None,
                "outline_density": outline_density,
                "outline_character_whitelist": outline_character_whitelist,
                "chapter_design_matrix": _scoped_design_matrix_payload(
                    chapter_design_matrix,
                    batch_start=batch_start,
                    batch_end=batch_end,
                ),
                "reveal_window": build_outline_reveal_window(
                    editorial_contract,
                    batch_start=batch_start,
                    batch_end=batch_end,
                    total_chapters=total_chapters,
                    settings=settings,
                )
                or None,
            }
            with ctx.trace.step(f"plan_outline_batch_{batch_start}_{batch_end}"):
                batch_data = await ctx.call_with_retry(
                    TaskType.PLAN_OUTLINE_BATCH,
                    batch_ctx,
                    max_tokens=_outline_batch_max_tokens(
                        TaskType.PLAN_OUTLINE_BATCH,
                        batch_start,
                        batch_end,
                    ),
                    temperature=settings.temp_plan_outline_batch,
                    required_keys=("chapters",),
                    thinking=outline_thinking_batch,
                    _capture_raw=raw_sink,
                    max_retries=4,
                )
            await _repair_outline_batch_titles(
                ctx,
                batch_data=batch_data,
                batch_start=batch_start,
                batch_end=batch_end,
                total_chapters=total_chapters,
                synopsis=synopsis,
                volume_mode_flag=volume_mode_flag,
                volumes=volumes,
                raw_sink=raw_sink,
            )
            if raw_sink:
                _record_outline_exchange(
                    ctx.builder,
                    conversation_history=conversation_history,
                    task_type=TaskType.PLAN_OUTLINE_BATCH,
                    ctx=batch_ctx,
                    raw_response=raw_sink[0],
                    is_first_batch=True,
                )
            is_first_batch = False
        else:
            # Lightweight PLAN_OUTLINE_CONTINUE with multi-turn history.
            prior = outline_h.history_prior_messages(
                conversation_history,
                history_window_rounds=history_window,
            )

            phase_guidance = outline_h.extract_phase_guidance(
                blueprint,
                batch_start,
                batch_end,
                total_chapters,
                accumulated_chapters=outline_h.chapter_map_values(chapter_map),
            )
            phase_rhythm_guidance = outline_h.extract_phase_rhythm_guidance(
                blueprint,
                batch_start,
                batch_end,
                total_chapters,
            )

            outline_tracker_context = {}
            if outline_tracker is not None or episodic_memory is not None:
                outline_tracker_context = await _build_outline_tracker_context(
                    tracker=outline_tracker,
                    episodic_memory=episodic_memory,
                    current_chapter=batch_start,
                    existing_chapters=list(chapter_map.values()),
                )

            batch_ctx = _build_continue_context(
                start=batch_start,
                end=batch_end,
                phase_guidance=phase_guidance,
                phase_rhythm_guidance=phase_rhythm_guidance,
                is_final_batch=is_final,
                tracker_context=outline_tracker_context,
            )
            with ctx.trace.step(f"plan_outline_continue_{batch_start}_{batch_end}"):
                batch_data = await ctx.call_with_retry(
                    TaskType.PLAN_OUTLINE_CONTINUE,
                    batch_ctx,
                    max_tokens=_outline_batch_max_tokens(
                        TaskType.PLAN_OUTLINE_CONTINUE,
                        batch_start,
                        batch_end,
                    ),
                    temperature=settings.temp_plan_outline_batch,
                    required_keys=("chapters",),
                    prior_messages=prior,
                    thinking=outline_thinking_continue,
                    multi_turn=outline_multi_turn_continue,
                    _capture_raw=raw_sink,
                    max_retries=4,
                )
            await _repair_outline_batch_titles(
                ctx,
                batch_data=batch_data,
                batch_start=batch_start,
                batch_end=batch_end,
                total_chapters=total_chapters,
                synopsis=synopsis,
                volume_mode_flag=volume_mode_flag,
                volumes=volumes,
                raw_sink=raw_sink,
            )
            if raw_sink:
                _record_outline_exchange(
                    ctx.builder,
                    conversation_history=conversation_history,
                    task_type=TaskType.PLAN_OUTLINE_CONTINUE,
                    ctx=batch_ctx,
                    raw_response=raw_sink[0],
                    is_first_batch=False,
                )

        raw_response = raw_sink[0] if raw_sink else ""
        if raw_response:
            _save_outline_batch_checkpoint(
                storage,
                layout,
                total_chapters=total_chapters,
                batch_start=batch_start,
                batch_end=batch_end,
                chapters=[],
                missing_chapters=list(range(batch_start, batch_end + 1)),
                status="pending",
                raw_response=raw_response,
                source_hashes=batch_source_hashes,
                session_id=outline_session_id,
            )

        # Merge this batch's chapters.
        _, skipped_chapters = _merge_outline_chapters(
            raw_chapters=batch_data.get("chapters", []),
            chapter_map=chapter_map,
            batch_start=batch_start,
            batch_end=batch_end,
            character_whitelist=outline_character_whitelist,
            entity_catalog=outline_entity_catalog,
            design_by_chapter=chapter_design_by_number,
        )

        # Auto-repair missing chapter ranges
        expected_nums = set(range(batch_start, batch_end + 1))
        missing_nums = sorted(n for n in expected_nums if n not in chapter_map)
        repair_round = 0
        while missing_nums and repair_round < PipelineConstants.MAX_BATCH_REPAIR_ROUNDS:
            repair_round += 1
            ranges: list[tuple[int, int]] = []
            r_start = missing_nums[0]
            r_prev = missing_nums[0]
            for num in missing_nums[1:]:
                if num == r_prev + 1:
                    r_prev = num
                    continue
                ranges.append((r_start, r_prev))
                r_start = num
                r_prev = num
            ranges.append((r_start, r_prev))

            for miss_start, miss_end in ranges:
                prior = None
                if outline_multi_turn_continue:
                    prior = outline_h.history_prior_messages(
                        conversation_history,
                        history_window_rounds=history_window,
                    )
                miss_phase_guidance = outline_h.extract_phase_guidance(
                    blueprint,
                    miss_start,
                    miss_end,
                    total_chapters,
                    accumulated_chapters=outline_h.chapter_map_values(chapter_map),
                )
                miss_phase_rhythm_guidance = outline_h.extract_phase_rhythm_guidance(
                    blueprint,
                    miss_start,
                    miss_end,
                    total_chapters,
                )
                miss_ctx = _build_continue_context(
                    start=miss_start,
                    end=miss_end,
                    phase_guidance=miss_phase_guidance,
                    phase_rhythm_guidance=miss_phase_rhythm_guidance,
                    is_final_batch=miss_end >= total_chapters,
                    tracker_context=outline_tracker_context,
                )
                miss_raw_sink: list[str] = []
                with ctx.trace.step(f"plan_outline_repair_{miss_start}_{miss_end}"):
                    miss_data = await ctx.call_with_retry(
                        TaskType.PLAN_OUTLINE_CONTINUE,
                        miss_ctx,
                        max_tokens=_outline_batch_max_tokens(
                            TaskType.PLAN_OUTLINE_CONTINUE,
                            miss_start,
                            miss_end,
                        ),
                        temperature=settings.temp_plan_outline_batch,
                        required_keys=("chapters",),
                        prior_messages=prior,
                        thinking=outline_thinking_continue,
                        multi_turn=outline_multi_turn_continue,
                        _capture_raw=miss_raw_sink,
                        max_retries=4,
                    )
                await _repair_outline_batch_titles(
                    ctx,
                    batch_data=miss_data,
                    batch_start=miss_start,
                    batch_end=miss_end,
                    total_chapters=total_chapters,
                    synopsis=synopsis,
                    volume_mode_flag=volume_mode_flag,
                    volumes=volumes,
                    raw_sink=miss_raw_sink,
                )
                if miss_raw_sink:
                    _record_outline_exchange(
                        ctx.builder,
                        conversation_history=conversation_history,
                        task_type=TaskType.PLAN_OUTLINE_CONTINUE,
                        ctx=miss_ctx,
                        raw_response=miss_raw_sink[0],
                        is_first_batch=is_first_batch,
                    )
                _, miss_skipped = _merge_outline_chapters(
                    raw_chapters=miss_data.get("chapters", []),
                    chapter_map=chapter_map,
                    batch_start=miss_start,
                    batch_end=miss_end,
                    character_whitelist=outline_character_whitelist,
                    entity_catalog=outline_entity_catalog,
                    design_by_chapter=chapter_design_by_number,
                )
                skipped_chapters.extend(miss_skipped)
            missing_nums = sorted(n for n in expected_nums if n not in chapter_map)

        missing_nums = sorted(n for n in expected_nums if n not in chapter_map)
        new_chapters = [
            chapter_map[ch_num]
            for ch_num in range(batch_start, batch_end + 1)
            if ch_num in chapter_map
        ]
        if missing_nums:
            safe_chapter_map = _safe_committed_chapter_map(
                chapter_map,
                batch_start=batch_start,
                batch_end=batch_end,
            )
            _save_outline_batch_checkpoint(
                storage,
                layout,
                total_chapters=total_chapters,
                batch_start=batch_start,
                batch_end=batch_end,
                chapters=new_chapters,
                missing_chapters=missing_nums,
                status="incomplete",
                raw_response=raw_response,
                source_hashes=batch_source_hashes,
                session_id=outline_session_id,
            )
            _persist_partial_outline_state(
                storage,
                layout,
                total_chapters=total_chapters,
                synopsis=synopsis,
                volume_mode_flag=volume_mode_flag,
                volumes=volumes,
                chapter_map=safe_chapter_map,
                conversation_history=conversation_history,
                history_window_rounds=history_window,
                session_metadata={
                    "status": "blocked",
                    "session_id": outline_session_id,
                    "pending_batch": {
                        **pending_batch,
                        "status": "incomplete",
                        "missing_chapters": missing_nums,
                    },
                    "accepted_batches": accepted_batch_records,
                    "reveal_guard_input_hashes": reveal_guard_hashes,
                    "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
                },
            )
            on_step(
                "plan_outline_batch_incomplete",
                {
                    **_outline_safe_progress_payload(
                        safe_chapter_map,
                        total_chapters=total_chapters,
                        batch_start=batch_start,
                        batch_end=batch_end,
                        current_batch_status="incomplete",
                    ),
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                    "accepted_chapters": [chapter.chapter_number for chapter in new_chapters],
                    "uncommitted_chapters": [chapter.chapter_number for chapter in new_chapters],
                    "missing_chapters": missing_nums,
                    "skipped_count": len(skipped_chapters),
                    "skipped_samples": skipped_chapters[:5],
                },
            )
            raise ValueError(
                "章节大纲批次未完整落盘："
                f"{batch_start}-{batch_end} 缺少 {missing_nums}；"
                f"已验收 {len(new_chapters)}/{batch_end - batch_start + 1} 章。"
            )

        quality_repair_round = 0
        batch_entity_audit: dict[str, Any] = {"ok": True, "issues": []}
        while True:
            prev_for_quality = [
                chapter
                for chapter in sorted(chapter_map.values(), key=lambda c: c.chapter_number)
                if chapter.chapter_number < batch_start
            ]
            batch_quality_issues = _check_batch_quality(
                chapters=new_chapters,
                blueprint=blueprint,
                prev_chapters=prev_for_quality,
            )
            batch_entity_audit = _outline_entity_audit(
                new_chapters,
                character_bible,
                entity_catalog=outline_entity_catalog,
                design_by_chapter=chapter_design_by_number,
            )
            entity_issues = batch_entity_audit.get("issues")
            if isinstance(entity_issues, list):
                batch_quality_issues.extend(
                    issue for issue in entity_issues if isinstance(issue, dict)
                )
            critical_quality_issues = _critical_outline_quality_issues(batch_quality_issues)
            if batch_quality_issues:
                _log.warning(
                    "outline_batch_quality_issues | batch=%d-%d | issues=%d | critical=%d",
                    batch_start,
                    batch_end,
                    len(batch_quality_issues),
                    len(critical_quality_issues),
                )
            if not critical_quality_issues:
                break
            if quality_repair_round >= PipelineConstants.MAX_BATCH_REPAIR_ROUNDS:
                safe_chapter_map = _safe_committed_chapter_map(
                    chapter_map,
                    batch_start=batch_start,
                    batch_end=batch_end,
                )
                _save_outline_batch_checkpoint(
                    storage,
                    layout,
                    total_chapters=total_chapters,
                    batch_start=batch_start,
                    batch_end=batch_end,
                    chapters=new_chapters,
                    missing_chapters=[],
                    status="rejected",
                    raw_response=raw_response,
                    source_hashes=batch_source_hashes,
                    entity_audit=batch_entity_audit,
                    metadata={"critical_issues": critical_quality_issues[:12]},
                    session_id=outline_session_id,
                )
                _persist_partial_outline_state(
                    storage,
                    layout,
                    total_chapters=total_chapters,
                    synopsis=synopsis,
                    volume_mode_flag=volume_mode_flag,
                    volumes=volumes,
                    chapter_map=safe_chapter_map,
                    conversation_history=conversation_history,
                    history_window_rounds=history_window,
                    session_metadata={
                        "status": "blocked",
                        "session_id": outline_session_id,
                        "pending_batch": {
                            **pending_batch,
                            "status": "rejected",
                            "critical_issues": critical_quality_issues[:12],
                        },
                        "accepted_batches": accepted_batch_records,
                        "reveal_guard_input_hashes": reveal_guard_hashes,
                        "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
                    },
                )
                on_step(
                    "plan_outline_batch_quality_failed",
                    {
                        **_outline_safe_progress_payload(
                            safe_chapter_map,
                            total_chapters=total_chapters,
                            batch_start=batch_start,
                            batch_end=batch_end,
                            current_batch_status="rejected",
                        ),
                        "batch_start": batch_start,
                        "batch_end": batch_end,
                        "critical_issues": critical_quality_issues[:12],
                    },
                )
                raise ValueError(
                    "章节大纲批次质量未通过："
                    f"{batch_start}-{batch_end} 存在 {len(critical_quality_issues)} 个关键问题；"
                    f"示例：{critical_quality_issues[0].get('message', '')}"
                )

            quality_repair_round += 1
            feedback = _format_outline_quality_feedback(critical_quality_issues)
            if len(conversation_history) > history_len_before_batch:
                del conversation_history[history_len_before_batch:]
            safe_chapter_map = _safe_committed_chapter_map(
                chapter_map,
                batch_start=batch_start,
                batch_end=batch_end,
            )
            on_step(
                "plan_outline_batch_quality_retry",
                {
                    **_outline_safe_progress_payload(
                        safe_chapter_map,
                        total_chapters=total_chapters,
                        batch_start=batch_start,
                        batch_end=batch_end,
                        current_batch_status="quality_retry",
                    ),
                    "batch_start": batch_start,
                    "batch_end": batch_end,
                    "repair_round": quality_repair_round,
                    "critical_issues": critical_quality_issues[:12],
                },
            )
            for chapter_num in range(batch_start, batch_end + 1):
                chapter_map.pop(chapter_num, None)

            prior = None
            if outline_multi_turn_continue:
                prior = outline_h.history_prior_messages(
                    conversation_history,
                    history_window_rounds=history_window,
                )
            repair_phase_guidance = outline_h.extract_phase_guidance(
                blueprint,
                batch_start,
                batch_end,
                total_chapters,
                accumulated_chapters=prev_for_quality,
            )
            repair_phase_rhythm_guidance = outline_h.extract_phase_rhythm_guidance(
                blueprint,
                batch_start,
                batch_end,
                total_chapters,
            )
            repair_ctx = _build_continue_context(
                start=batch_start,
                end=batch_end,
                phase_guidance=repair_phase_guidance,
                phase_rhythm_guidance=repair_phase_rhythm_guidance,
                is_final_batch=is_final,
                tracker_context=outline_tracker_context,
                outline_quality_feedback=feedback,
            )
            repair_raw_sink: list[str] = []
            with ctx.trace.step(
                f"plan_outline_retry_{batch_start}_{batch_end}_{quality_repair_round}"
            ):
                repair_data = await ctx.call_with_retry(
                    TaskType.PLAN_OUTLINE_CONTINUE,
                    repair_ctx,
                    max_tokens=_outline_batch_max_tokens(
                        TaskType.PLAN_OUTLINE_CONTINUE,
                        batch_start,
                        batch_end,
                    ),
                    temperature=settings.temp_plan_outline_batch,
                    required_keys=("chapters",),
                    prior_messages=prior,
                    thinking=outline_thinking_continue,
                    multi_turn=outline_multi_turn_continue,
                    _capture_raw=repair_raw_sink,
                    max_retries=4,
                )
            await _repair_outline_batch_titles(
                ctx,
                batch_data=repair_data,
                batch_start=batch_start,
                batch_end=batch_end,
                total_chapters=total_chapters,
                synopsis=synopsis,
                volume_mode_flag=volume_mode_flag,
                volumes=volumes,
                raw_sink=repair_raw_sink,
            )
            if repair_raw_sink:
                raw_response = repair_raw_sink[0]
                _record_outline_exchange(
                    ctx.builder,
                    conversation_history=conversation_history,
                    task_type=TaskType.PLAN_OUTLINE_CONTINUE,
                    ctx=repair_ctx,
                    raw_response=repair_raw_sink[0],
                    is_first_batch=False,
                )
            _, repair_skipped = _merge_outline_chapters(
                raw_chapters=repair_data.get("chapters", []),
                chapter_map=chapter_map,
                batch_start=batch_start,
                batch_end=batch_end,
                character_whitelist=outline_character_whitelist,
                entity_catalog=outline_entity_catalog,
                design_by_chapter=chapter_design_by_number,
            )
            skipped_chapters.extend(repair_skipped)
            missing_nums = sorted(n for n in expected_nums if n not in chapter_map)
            new_chapters = [
                chapter_map[ch_num]
                for ch_num in range(batch_start, batch_end + 1)
                if ch_num in chapter_map
            ]
            if missing_nums:
                safe_chapter_map = _safe_committed_chapter_map(
                    chapter_map,
                    batch_start=batch_start,
                    batch_end=batch_end,
                )
                _save_outline_batch_checkpoint(
                    storage,
                    layout,
                    total_chapters=total_chapters,
                    batch_start=batch_start,
                    batch_end=batch_end,
                    chapters=new_chapters,
                    missing_chapters=missing_nums,
                    status="incomplete",
                    raw_response=repair_raw_sink[0] if repair_raw_sink else raw_response,
                    source_hashes=batch_source_hashes,
                    entity_audit=batch_entity_audit,
                    session_id=outline_session_id,
                )
                _persist_partial_outline_state(
                    storage,
                    layout,
                    total_chapters=total_chapters,
                    synopsis=synopsis,
                    volume_mode_flag=volume_mode_flag,
                    volumes=volumes,
                    chapter_map=safe_chapter_map,
                    conversation_history=conversation_history,
                    history_window_rounds=history_window,
                    session_metadata={
                        "status": "blocked",
                        "session_id": outline_session_id,
                        "pending_batch": {
                            **pending_batch,
                            "status": "incomplete",
                            "missing_chapters": missing_nums,
                        },
                        "accepted_batches": accepted_batch_records,
                        "reveal_guard_input_hashes": reveal_guard_hashes,
                        "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
                    },
                )
                raise ValueError(
                    "章节大纲质量修复批次未完整落盘："
                    f"{batch_start}-{batch_end} 缺少 {missing_nums}；"
                    f"已验收 {len(new_chapters)}/{batch_end - batch_start + 1} 章。"
                )

        # Persist the same commitment projection that final assembly publishes,
        # so callbacks and resume cannot observe hard cast on preview chapters.
        new_chapters = _apply_progressive_commitment_boundary(
            new_chapters, hard_through_chapter=design_end
        )
        chapter_map.update({chapter.chapter_number: chapter for chapter in new_chapters})
        checkpoint_payload = _save_outline_batch_checkpoint(
            storage,
            layout,
            total_chapters=total_chapters,
            batch_start=batch_start,
            batch_end=batch_end,
            chapters=new_chapters,
            missing_chapters=[],
            status="accepted",
            raw_response=raw_response,
            source_hashes=batch_source_hashes,
            entity_audit=batch_entity_audit,
            session_id=outline_session_id,
        )
        accepted_batch_records.append(
            {
                "batch_start": batch_start,
                "batch_end": batch_end,
                "accepted_chapters": [chapter.chapter_number for chapter in new_chapters],
                "checkpoint_file": f"batch_{batch_start:03d}_{batch_end:03d}.json",
                "content_hash": str(checkpoint_payload.get("content_hash") or ""),
                "source_hash": str(checkpoint_payload.get("source_hash") or ""),
                "session_id": outline_session_id,
                "entity_audit": {
                    "ok": bool(batch_entity_audit.get("ok", True)),
                    "issue_count": len(batch_entity_audit.get("issues") or []),
                },
            }
        )
        _persist_partial_outline_state(
            storage,
            layout,
            total_chapters=total_chapters,
            synopsis=synopsis,
            volume_mode_flag=volume_mode_flag,
            volumes=volumes,
            chapter_map=chapter_map,
            conversation_history=conversation_history,
            history_window_rounds=history_window,
            session_metadata={
                "status": "running",
                "session_id": outline_session_id,
                "pending_batch": None,
                "accepted_batches": accepted_batch_records,
                "reveal_guard_input_hashes": reveal_guard_hashes,
                "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
            },
        )
        if on_outline_batch_ready is not None and new_chapters:
            try:
                on_outline_batch_ready(batch_start, batch_end, new_chapters)
            except Exception as exc:
                _log.warning(
                    "outline_batch_ready_callback_failed | batch=%s-%s | error=%s",
                    batch_start,
                    batch_end,
                    exc,
                )

        if outline_tracker is not None:
            update_result = outline_tracker.update_from_batch(
                chapters=new_chapters,
                extracted_info=None,
            )
            # P0: Persist tracker state after each batch so resume is accurate
            _save_outline_tracker_state(storage, layout, outline_tracker)

            if update_result.get("needs_llm"):
                llm_chapters = [
                    chapter_map[ch_num]
                    for ch_num in range(
                        int(update_result.get("llm_from_chapter", batch_start) or batch_start),
                        int(update_result.get("last_llm_chapter", batch_end) or batch_end) + 1,
                    )
                    if ch_num in chapter_map
                ]

                if llm_chapters:
                    try:
                        await outline_tracker.trigger_llm_extraction(
                            chapters=llm_chapters,
                            ctx=ctx,
                        )
                        outline_tracker.apply_pending_llm_result()
                        # Re-save tracker state after LLM extraction results are applied
                        _save_outline_tracker_state(storage, layout, outline_tracker)
                    except Exception as _exc:
                        _log.warning("outline_tracker_llm_extraction_failed | error=%s", _exc)

        if episodic_memory is not None and new_chapters:
            for chapter in new_chapters:
                try:
                    await _index_chapter_to_episodic_memory(
                        episodic_memory=episodic_memory,
                        chapter=chapter,
                        blueprint=blueprint,
                    )
                except Exception as exc:
                    _log.warning(
                        "outline_episodic_index_failed | chapter=%s | error=%s",
                        getattr(chapter, "chapter_number", "?"),
                        exc,
                    )
            # P1: Persist episodic index after each batch so resume has full semantic data
            _save_episodic_memory_outline_data(storage, layout, episodic_memory)

        accumulated_chapters = sorted(chapter_map.values(), key=lambda c: c.chapter_number)
        on_step(
            f"plan_outline_batch_{batch_start}_{batch_end}",
            {
                **_outline_safe_progress_payload(
                    chapter_map,
                    total_chapters=total_chapters,
                    batch_start=batch_start,
                    batch_end=batch_end,
                    current_batch_status="accepted",
                ),
                "batch_start": batch_start,
                "batch_end": batch_end,
                "accepted_chapters": [chapter.chapter_number for chapter in new_chapters],
                "batch_accepted": len(new_chapters),
                "missing_chapters": [],
            },
        )

    # ── Final assembly ───────────────────────────────────────────────
    accumulated_chapters = _apply_progressive_commitment_boundary(
        accumulated_chapters,
        hard_through_chapter=design_end,
    )
    final_outline = StoryOutline(
        total_chapters=total_chapters,
        hard_through_chapter=design_end,
        planned_through_chapter=target_end_chapter,
        synopsis=synopsis,
        volume_mode=volume_mode_flag,
        volumes=volumes,
        chapters=sorted(accumulated_chapters, key=lambda c: c.chapter_number),
    )
    normalization_end = max(
        target_end_chapter,
        max((chapter.chapter_number for chapter in accumulated_chapters), default=0),
    )
    final_outline = outline_h.normalize_outline_chapters(
        final_outline, total_chapters=normalization_end, words_per_chapter=words_per_chapter
    )
    final_outline.total_chapters = total_chapters
    final_outline = outline_h.backfill_involved_characters(final_outline, character_bible)
    final_outline = outline_h.normalize_outline_volumes(
        final_outline,
        total_chapters=total_chapters,
        use_volume_mode=use_volume_mode,
        chapters_per_volume=effective_chapters_per_volume,
    )
    final_outline = restore_committed(final_outline)
    on_step("plan_outline", final_outline)
    storage.save_json(layout.outline_path, final_outline.model_dump(mode="json"))
    manifest_for_context(ctx).record_success(
        artifact="outline",
        workflow="init_long",
        step="plan_outline",
        input_hashes=outline_reveal_guard_input_hashes(editorial_contract),
        output_hashes={"outline": hash_payload(final_outline)},
        paths={"outline": str(layout.outline_path)},
        metadata={
            "batch_size": batch_size,
            "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
        },
    )
    if layout.outline_session_path.exists():
        layout.outline_session_path.unlink()
    # Clean up intermediate tracker state — outline is complete, tracker no longer needed
    if layout.outline_tracker_path.exists():
        layout.outline_tracker_path.unlink()

    if episodic_memory is not None:
        _save_episodic_memory_outline_data(storage, layout, episodic_memory)

    return final_outline
