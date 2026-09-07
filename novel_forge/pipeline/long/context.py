"""StoryMemoryManager — assembles a structured ChapterStatePacket."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from novel_forge.common.utils import normalize_gender_value
from novel_forge.core.config import get_settings
from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterStatePacket,
    NarrativeBlueprintContext,
)
from novel_forge.core.schemas.outline import ChapterOutline, NarrativeBlueprint, VolumeOutline
from novel_forge.core.utils.string import carry_forward_text
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.entity_reference import compact_entity_reference_graph
from novel_forge.pipeline.long.helpers import (
    load_json_if_exists,
    load_previous_chapter_ending,
    load_previous_volume_summary,
)
from novel_forge.pipeline.long.services.arc_liveness import build_arc_liveness_report
from novel_forge.pipeline.long.services.context.context_helpers import (
    resolve_chapter_character_names,
)
from novel_forge.pipeline.long.services.plot_milestones import (
    load_progression_ledger,
    select_milestone_window,
    stage_visibility_summary,
)
from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.schemas import StoryKernel

_logger = logging.getLogger(__name__)


def _coerce_loaded_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return None


def _coerce_field(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _coerce_field_with_fallbacks(value: Any, keys: list[str], default: Any = "") -> Any:
    """Try multiple field names in order of preference.

    This provides compatibility handling for fields that may have different
    names across different versions or contexts (e.g., chapter_summary vs
    chapter_summaries vs previous_summary).
    """
    for key in keys:
        result = _coerce_field(value, key, None)
        if result is not None and result != "":
            return result
    return default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_character_state(state: Any) -> dict[str, Any]:
    physical = _coerce_field(state, "physical", {}) or {}
    emotional = _coerce_field(state, "emotional", {}) or {}
    motivation = _coerce_field(state, "motivation", {}) or {}
    knowledge = _coerce_field(state, "knowledge_state", {}) or {}
    location = _clean_text(_coerce_field(state, "location", ""))
    if not location:
        location = _clean_text(_coerce_field(physical, "location", ""))
    emotional_state = _clean_text(_coerce_field(state, "emotional_state", ""))
    if not emotional_state:
        emotional_state = _clean_text(_coerce_field(emotional, "primary_emotion", ""))
    inventory = _coerce_field(state, "inventory", [])
    if not isinstance(inventory, list):
        inventory = _coerce_field(physical, "inventory", [])
    if not isinstance(inventory, list):
        inventory = []
    known_facts = _coerce_field(state, "knowledge", [])
    if not isinstance(known_facts, list):
        known_facts = _coerce_field(knowledge, "known_facts", [])
    if not isinstance(known_facts, list):
        known_facts = []

    return {
        "name": _clean_text(_coerce_field(state, "name", "")),
        "alive": bool(_coerce_field(state, "alive", True)),
        "gender": _clean_text(_coerce_field(state, "gender", "")),
        "location": location,
        "emotional_state": emotional_state,
        "active_goal": _clean_text(_coerce_field(motivation, "current_drive", "")),
        "inventory": [str(item).strip() for item in inventory if str(item).strip()],
        "known_facts": [str(item).strip() for item in known_facts if str(item).strip()],
        "voice": _clean_text(getattr(state, "voice", "")),
        "notes": _clean_text(_coerce_field(state, "notes", "")),
    }


def _compact_exit_state(exit_state: Any) -> dict[str, Any] | None:
    if exit_state is None:
        return None
    raw_char_end_states = _coerce_field(exit_state, "character_end_states", {})
    compact_char_end_states: dict[str, dict[str, Any]] = {}
    if isinstance(raw_char_end_states, dict):
        for name, state in raw_char_end_states.items():
            key = _clean_text(name)
            if not key:
                continue
            compact_char_end_states[key] = _compact_character_state(state)

    return {
        "chapter_number": int(_coerce_field(exit_state, "chapter_number", 0) or 0),
        "time_marker": _clean_text(_coerce_field(exit_state, "time_marker", "")),
        "location": _clean_text(_coerce_field(exit_state, "location", "")),
        "pov": _clean_text(_coerce_field(exit_state, "pov", "")),
        "active_goals": [
            str(item).strip()
            for item in list(_coerce_field(exit_state, "active_goals", []) or [])
            if str(item).strip()
        ],
        "open_questions": [
            str(item).strip()
            for item in list(_coerce_field(exit_state, "open_questions", []) or [])
            if str(item).strip()
        ],
        "must_carry_forward": [
            carry_forward_text(item)
            for item in list(_coerce_field(exit_state, "must_carry_forward", []) or [])
            if carry_forward_text(item)
        ],
        "character_end_states": compact_char_end_states,
    }


def _compact_canon_context_payload(
    canon_context: Any,
    for_chapter: int = 0,
) -> dict[str, Any]:
    """Build a prompt-friendly canon context payload without schema metadata."""
    raw_characters = _coerce_field(canon_context, "characters", {}) or {}
    compact_characters: dict[str, dict[str, Any]] = {}
    if isinstance(raw_characters, dict):
        for name, state in raw_characters.items():
            key = _clean_text(name)
            if not key:
                continue
            compact_characters[key] = _compact_character_state(state)

    raw_events = list(_coerce_field(canon_context, "recent_events", []) or [])
    recent_events: list[dict[str, Any]] = []
    for event in raw_events:
        recent_events.append(
            {
                "chapter": int(_coerce_field(event, "chapter", 0) or 0),
                "event": _clean_text(_coerce_field(event, "event", "")),
                "characters_involved": [
                    str(name).strip()
                    for name in list(_coerce_field(event, "characters_involved", []) or [])
                    if str(name).strip()
                ],
                "timestamp_in_story": _clean_text(_coerce_field(event, "timestamp_in_story", "")),
            }
        )

    raw_foreshadowing = list(_coerce_field(canon_context, "active_foreshadowing", []) or [])
    active_foreshadowing: list[dict[str, Any]] = []
    for item in raw_foreshadowing:
        planted = int(_coerce_field(item, "planted_chapter", 0) or 0)
        age = max(0, for_chapter - planted) if for_chapter > 0 and planted > 0 else 0
        urgency = "高" if age >= 5 else ("中" if age >= 3 else "低")
        active_foreshadowing.append(
            {
                "id": _clean_text(_coerce_field(item, "id", "")),
                "description": _clean_text(_coerce_field(item, "description", "")),
                "status": _clean_text(_coerce_field(item, "status", "")),
                "planted_chapter": planted,
                "resolved_chapter": int(_coerce_field(item, "resolved_chapter", 0) or 0),
                "urgency": urgency,
            }
        )

    raw_relationships = list(_coerce_field(canon_context, "active_relationships", []) or [])
    active_relationships: list[dict[str, Any]] = []
    for rel in raw_relationships:
        active_relationships.append(
            {
                "pair_id": _clean_text(_coerce_field(rel, "pair_id", "")),
                "characters": [
                    str(name).strip()
                    for name in list(_coerce_field(rel, "characters", []) or [])
                    if str(name).strip()
                ],
                "public_status": _clean_text(_coerce_field(rel, "public_status", "")),
                "trust": _coerce_field(rel, "trust", 0.5),
                "tension": _coerce_field(rel, "tension", 0.5),
                "dependency": _coerce_field(rel, "dependency", 0.0),
                "last_shift_event": _clean_text(_coerce_field(rel, "last_shift_event", "")),
                "last_updated_chapter": int(_coerce_field(rel, "last_updated_chapter", 0) or 0),
                "notes": _clean_text(_coerce_field(rel, "notes", "")),
            }
        )

    raw_threads = list(_coerce_field(canon_context, "active_plot_threads", []) or [])
    active_plot_threads: list[dict[str, Any]] = []
    for thread in raw_threads:
        active_plot_threads.append(
            {
                "thread_id": _clean_text(_coerce_field(thread, "thread_id", "")),
                "title": _clean_text(_coerce_field(thread, "title", "")),
                "status": _clean_text(_coerce_field(thread, "status", "")),
                "owners": [
                    str(name).strip()
                    for name in list(_coerce_field(thread, "owners", []) or [])
                    if str(name).strip()
                ],
                "last_touched_chapter": int(_coerce_field(thread, "last_touched_chapter", 0) or 0),
                "next_payoff_window": _clean_text(_coerce_field(thread, "next_payoff_window", "")),
                "blocking_condition": _clean_text(_coerce_field(thread, "blocking_condition", "")),
                "summary": _clean_text(_coerce_field(thread, "summary", "")),
            }
        )

    world_facts = _coerce_field(canon_context, "world_facts", {}) or {}
    if not isinstance(world_facts, dict):
        world_facts = {}

    return {
        "characters": compact_characters,
        "recent_events": recent_events,
        "active_foreshadowing": active_foreshadowing,
        "active_relationships": active_relationships,
        "active_plot_threads": active_plot_threads,
        "world_facts": {str(k): str(v) for k, v in world_facts.items()},
        "previous_chapter_summary": _clean_text(
            _coerce_field_with_fallbacks(
                canon_context,
                ["previous_chapter_summary", "chapter_summary", "previous_summary"],
                "",
            )
        ),
        "previous_exit_state": _compact_exit_state(
            _coerce_field(canon_context, "previous_exit_state", None)
        ),
        "must_carry_forward": [
            carry_forward_text(item)
            for item in list(_coerce_field(canon_context, "must_carry_forward", []) or [])
            if carry_forward_text(item)
        ],
        "immutable_facts": [
            str(item).strip()
            for item in list(_coerce_field(canon_context, "immutable_facts", []) or [])
            if str(item).strip()
        ],
    }


def _load_chapter_contract(layout: ProjectLayout, chapter_number: int) -> dict[str, Any]:
    """Load the chapter contract generated by LLM planning; no local adjudication."""
    contracts_path = layout.plans_dir / "chapter_contracts.json"
    data = load_json_if_exists(FileSystemStorage(layout.root.parent), contracts_path)
    if not isinstance(data, dict):
        return {}
    for item in data.get("chapter_contracts", []) or []:
        if not isinstance(item, dict):
            continue
        if int(item.get("chapter_number", 0) or 0) == chapter_number:
            return dict(item)
    return {}


def _load_plot_milestone_window(layout: ProjectLayout, chapter_number: int) -> dict[str, Any]:
    payload = load_json_if_exists(
        FileSystemStorage(layout.root.parent), layout.plot_milestone_index_path
    )
    if not isinstance(payload, dict):
        return {}
    try:
        return select_milestone_window(
            payload,
            chapter_number=chapter_number,
            previous=2,
            future=2,
        ).model_dump(mode="json")
    except Exception as exc:
        _logger.warning("Failed to load plot milestone window: %s", exc)
        return {}


def _load_progression_ledger_payload(layout: ProjectLayout) -> dict[str, Any]:
    payload = load_json_if_exists(
        FileSystemStorage(layout.root.parent), layout.progression_ledger_path
    )
    if not isinstance(payload, dict):
        return {"entries": [], "last_chapter": 0}
    try:
        return load_progression_ledger(payload).model_dump(mode="json")
    except Exception as exc:
        _logger.warning("Failed to load progression ledger: %s", exc)
        return {"entries": [], "last_chapter": 0}


def _load_narrative_contract_payload(layout: ProjectLayout) -> dict[str, Any]:
    payload = load_json_if_exists(
        FileSystemStorage(layout.root.parent), layout.narrative_contract_path
    )
    return payload if isinstance(payload, dict) else {}


def _load_authoritative_narrative_state(
    layout: ProjectLayout,
    *,
    max_entries: int,
    max_pending: int,
    max_chapter: int | None = None,
) -> dict[str, Any]:
    """Load LLM-adjudicated state projection for prompt injection."""
    try:
        from novel_forge.narrative_state.store import NarrativeStateStore

        store = NarrativeStateStore(layout.root)
        return store.projection_for_prompt(
            max_entries=max_entries,
            max_pending=max_pending,
            max_chapter=max_chapter,
        )
    except Exception as exc:
        _logger.warning("Failed to load narrative state projection: %s", exc)
        return {}


def _inject_authoritative_state_into_canon_payload(
    canon_payload: dict[str, Any],
    narrative_state: dict[str, Any],
    chapter_contract: dict[str, Any] | None = None,
    entity_reference_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not narrative_state and not chapter_contract and not entity_reference_graph:
        return canon_payload
    payload = dict(canon_payload)
    if narrative_state:
        payload["authoritative_narrative_state"] = narrative_state
    if chapter_contract:
        payload["chapter_contract"] = chapter_contract
    if entity_reference_graph:
        payload["entity_reference_graph"] = entity_reference_graph
    return payload


def compute_narrative_context(
    blueprint_data: dict[str, Any],
    chapter_number: int,
) -> NarrativeBlueprintContext:
    """从叙事蓝图数据中计算当前章节的宏观叙事坐标."""
    # 找到当前叙事阶段
    current_phase_name = ""
    current_phase_description = ""
    current_phase_tension_level = ""
    current_phase_key_events: list[str] = []
    current_phase_time_context = ""
    current_phase_locations: list[str] = []
    current_phase_key_characters: list[str] = []

    for phase in blueprint_data.get("narrative_phases", []):
        if phase.get("chapter_start", 1) <= chapter_number <= phase.get("chapter_end", 9999):
            current_phase_name = phase.get("phase_name", "")
            current_phase_description = phase.get("description", "")
            current_phase_tension_level = phase.get("tension_level", "")
            current_phase_key_events = [str(e) for e in phase.get("key_events", []) if e]
            current_phase_time_context = phase.get("time_context", "")
            current_phase_locations = [
                str(loc) for loc in phase.get("primary_locations", []) if loc
            ]
            current_phase_key_characters = [str(ch) for ch in phase.get("key_characters", []) if ch]
            break

    # 确认是否为转折点
    is_turning_point = False
    turning_point_description = ""
    next_turning_point_chapter = 0
    next_turning_point_description = ""

    for tp in sorted(
        blueprint_data.get("key_turning_points", []),
        key=lambda x: x.get("chapter_number", 0),
    ):
        ch = tp.get("chapter_number", 0)
        desc = tp.get("description", "")
        if ch == chapter_number:
            is_turning_point = True
            turning_point_description = desc
        elif ch > chapter_number and next_turning_point_chapter == 0:
            next_turning_point_chapter = ch
            next_turning_point_description = desc

    # 收集本章应覆盖的支线
    active_subplot_names: list[str] = []
    for sp in blueprint_data.get("subplot_plan", []):
        if chapter_number in sp.get("involved_chapters", []):
            name = sp.get("name", "")
            if name:
                active_subplot_names.append(name)

    # ── 收集交织提示和依赖警告 ────────────────────────────────
    weave_hints: list[str] = []
    dep_warnings: list[str] = []
    target_chapter_rhythm: dict[str, Any] = {}
    try:
        blueprint_obj = NarrativeBlueprint.model_validate(blueprint_data)
        from novel_forge.pipeline.long.services.blueprint.outline_helpers import (
            extract_chapter_rhythm_targets,
            extract_subplot_convergence_check,
            extract_subplot_weave_hints,
        )

        weave_hints, dep_warnings = extract_subplot_weave_hints(blueprint_obj, chapter_number)
        weave_hints.extend(extract_subplot_convergence_check(blueprint_obj, chapter_number))
        target_chapter_rhythm = extract_chapter_rhythm_targets(blueprint_obj, chapter_number)
    except Exception:
        pass

    return NarrativeBlueprintContext(
        current_phase_name=current_phase_name,
        current_phase_description=current_phase_description,
        current_phase_tension_level=current_phase_tension_level,
        current_phase_key_events=current_phase_key_events,
        current_phase_time_context=current_phase_time_context,
        current_phase_locations=current_phase_locations,
        current_phase_key_characters=current_phase_key_characters,
        is_turning_point=is_turning_point,
        turning_point_description=turning_point_description,
        next_turning_point_chapter=next_turning_point_chapter,
        next_turning_point_description=next_turning_point_description,
        active_subplot_names=active_subplot_names,
        active_subplot_weave_hints=weave_hints,
        subplot_dependency_warnings=dep_warnings,
        target_chapter_rhythm=target_chapter_rhythm,
    )


class StoryMemoryManager:
    """Build a chapter-scoped state packet from canon, reports, and static assets."""

    def __init__(
        self,
        *,
        storage: FileSystemStorage,
        retriever: StoryKernelRetriever,
        memory_context: Any | None = None,
    ) -> None:
        self._storage = storage
        self._retriever = retriever
        self._memory_context = memory_context

    async def build_packet(
        self,
        *,
        layout: ProjectLayout,
        canon_state: StoryKernel,
        chapter_number: int,
        chapter_outline: ChapterOutline,
        current_volume: VolumeOutline | None,
        character_bible: CharacterBible,
        character_profile_selector: Any,
        previous_report_compactor: Any,
        blueprint_data: dict[str, Any] | None = None,
        world_setting_brief: str = "",
        story_bible_rules: list[str] | None = None,
        overused_vocabulary_window: int = 3,
        previous_ending_min_chars: int = 600,
        previous_ending_max_chars: int = 1500,
        previous_ending_tail_paragraphs: int | None = None,
    ) -> ChapterStatePacket:
        """Assemble all chapter-scoped memory artifacts into one packet."""
        chapter_character_names = resolve_chapter_character_names(
            character_bible,
            chapter_outline,
        )
        canon_context = self._retriever.get_context(
            kernel=canon_state,
            for_chapter=chapter_number,
            involved_characters=chapter_character_names or None,
            pov_character=chapter_outline.pov_character,
            world_rules=story_bible_rules,
        )
        # StoryKernelRetriever ranks involved characters first but historically
        # filled the remaining capacity with unrelated entities.  The chapter
        # packet is a projection boundary, so enforce exact cast membership
        # here while retaining relationships that touch at least one cast member.
        chapter_character_set = set(chapter_character_names)
        if chapter_character_set:
            canon_context.characters = {
                name: state
                for name, state in canon_context.characters.items()
                if name in chapter_character_set
            }
            canon_context.active_relationships = [
                relation
                for relation in canon_context.active_relationships
                if chapter_character_set.intersection(
                    str(name or "").strip()
                    for name in list(getattr(relation, "characters", []) or [])
                )
            ]
        previous_chapter_ending = ""
        previous_creative_report = None
        previous_volume_summary = ""
        guard_constraints: list[str] = []

        previous_bridge: ChapterBridge | None = None
        if chapter_number > 1:
            previous_chapter_ending = load_previous_chapter_ending(
                self._storage,
                layout,
                chapter_number - 1,
                min_chars=previous_ending_min_chars,
                max_chars=previous_ending_max_chars,
                tail_paragraphs=previous_ending_tail_paragraphs,
            )
            previous_report = load_json_if_exists(
                self._storage,
                layout.creative_report_path(chapter_number - 1),
            )
            guard_handoff_recorded = False
            previous_guard_report = load_json_if_exists(
                self._storage,
                layout.guard_report_path(chapter_number - 1),
            )
            if isinstance(previous_guard_report, dict):
                guard_handoff_recorded, guard_constraints = self._extract_guard_handoff_constraints(
                    previous_guard_report,
                    target_chapter=chapter_number,
                )
            if isinstance(previous_report, dict):
                previous_creative_report = previous_report_compactor(previous_report)
                # Old projects stored only a text marker in creative_report.
                # Never use it once a structured handoff has been recorded: an
                # explicit rejected/empty handoff must suppress stale text.
                if not guard_handoff_recorded:
                    guard_constraints = self._extract_legacy_guard_constraints(previous_report)
            prev_bridge_data = load_json_if_exists(
                self._storage,
                layout.chapter_bridge_path(chapter_number - 1),
            )
            if prev_bridge_data:
                try:
                    previous_bridge = ChapterBridge.model_validate(prev_bridge_data)
                except Exception as exc:
                    _logger.warning("Failed to validate previous chapter bridge: %s", exc)

        # Read the cumulative forbidden-repetition index (O(1) instead of O(N) bridge scans).
        # Strategy: previous chapter is handled as hard constraints via bridge;
        # older chapters are only soft hints within a configurable lookback window.
        accumulated_forbidden: list[str] = []
        cross_window = max(0, int(get_settings().forbidden_elements_cross_chapter_window or 0))
        min_allowed_chapter = (
            max(1, chapter_number - cross_window) if cross_window > 0 else chapter_number
        )
        index_data = load_json_if_exists(
            self._storage,
            layout.forbidden_repetition_index_path,
        )
        if isinstance(index_data, dict):
            seen_forbidden: set[str] = set()
            for _ch_key, items in sorted(
                index_data.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0
            ):
                if not str(_ch_key).isdigit():
                    continue
                _ch_num = int(_ch_key)
                if _ch_num < min_allowed_chapter or _ch_num >= chapter_number:
                    continue
                if isinstance(items, list):
                    for item in items:
                        s = str(item).strip()
                        if s and s not in seen_forbidden:
                            seen_forbidden.add(s)
                            accumulated_forbidden.append(s)

        if current_volume is not None and current_volume.volume_number > 1:
            previous_volume_summary = load_previous_volume_summary(
                self._storage,
                layout,
                current_volume.volume_number - 1,
            )

        # 从 CharacterBible 建立性别速查表，用于回填 canon_context 中缺失的 gender
        _bible_gender: dict[str, str] = {
            profile.name: normalize_gender_value(profile.gender)
            for profile in character_bible.characters
            if profile.name and normalize_gender_value(profile.gender)
        }
        # 第一章时 canon_context.characters 为空（无前序章节状态），
        # 需要从 CharacterBible 预填充角色条目，确保 draft 模板的 pronoun_consistency 宏
        # 和后续的代词检查步骤拿到性别信息。
        if not canon_context.characters and _bible_gender:
            for _name in chapter_character_names:
                _gender = _bible_gender.get(_name, "")
                if _gender:
                    canon_context.characters[_name] = {"name": _name, "gender": _gender}
        # CharacterBible 是权威来源：若 canon_context 与角色圣经冲突，
        # 生成前就改回权威性别，避免错误状态继续污染 bridge/plan/draft。
        for _char_name, _char_state in list(canon_context.characters.items()):
            _authoritative_gender = _bible_gender.get(_char_name, "")
            _current_gender = normalize_gender_value(_char_state.get("gender", ""))
            if _authoritative_gender and _current_gender != _authoritative_gender:
                if _current_gender:
                    _logger.warning(
                        "Character gender conflict in canon_context: %s canon=%s bible=%s; using bible",
                        _char_name,
                        _current_gender,
                        _authoritative_gender,
                    )
                canon_context.characters[_char_name] = {
                    **_char_state,
                    "gender": _authoritative_gender,
                }

        character_profiles = character_profile_selector(
            character_bible,
            canon_context,
            chapter_outline.pov_character,
            involved_characters=chapter_character_names or None,
        )
        known_characters = sorted(
            {name for name in canon_context.characters.keys() if not is_system_artifact_name(name)}
            | {
                str(profile.get("name", "")).strip()
                for profile in character_profiles
                if isinstance(profile, dict)
                and str(profile.get("name", "")).strip()
                and not is_system_artifact_name(str(profile.get("name", "")).strip())
            }
        )

        narrative_ctx = (
            compute_narrative_context(blueprint_data, chapter_number) if blueprint_data else None
        )

        planning_context_brief = self._build_planning_context_brief(
            chapter_number=chapter_number,
            chapter_outline=chapter_outline,
            canon_context=canon_context,
            narrative_ctx=narrative_ctx,
            previous_creative_report=previous_creative_report,
        )

        settings = get_settings()
        authoritative_narrative_state = _load_authoritative_narrative_state(
            layout,
            max_entries=0,
            max_pending=int(getattr(settings, "narrative_state_pending_tail_items", 6) or 6),
            max_chapter=max(0, chapter_number - 1),
        )
        chapter_contract = _load_chapter_contract(layout, chapter_number)
        source_slice_payload = load_json_if_exists(
            FileSystemStorage(layout.root.parent),
            layout.chapter_source_slice_path(chapter_number),
        )
        source_slice_runtime = (
            source_slice_payload.get("payload", {}).get("runtime", {})
            if isinstance(source_slice_payload, dict)
            else {}
        )
        source_slice_entities = (
            list(source_slice_runtime.get("entities", []) or [])
            if isinstance(source_slice_runtime, dict)
            else []
        )
        retrieval_evidence_pack = await self._build_retrieval_evidence_pack(
            chapter_number=chapter_number,
            chapter_outline=chapter_outline,
        )
        milestone_window = _load_plot_milestone_window(layout, chapter_number)
        if milestone_window:
            try:
                self._storage.save_json(
                    layout.milestone_window_report_path(chapter_number),
                    {"report_type": "milestone_window", **milestone_window},
                )
            except Exception as exc:
                _logger.debug("Failed to persist milestone window report: %s", exc)
        progression_ledger = _load_progression_ledger_payload(layout)
        progression_ledger_tail = [
            item
            for item in list(progression_ledger.get("entries", []) or [])
            if int(item.get("chapter_number", 0) or 0) < chapter_number
        ][-12:]
        narrative_contract_payload = _load_narrative_contract_payload(layout)
        arc_liveness_report = build_arc_liveness_report(
            chapter_number=chapter_number,
            narrative_contract=narrative_contract_payload,
            progression_ledger=progression_ledger,
            window=int(getattr(settings, "arc_liveness_window", 6) or 6),
        ).model_dump(mode="json")
        if arc_liveness_report.get("dormant_arcs"):
            try:
                self._storage.save_json(
                    layout.arc_liveness_report_path(chapter_number),
                    arc_liveness_report,
                )
            except Exception as exc:
                _logger.debug("Failed to persist arc liveness report: %s", exc)
        stage_visibility_diagnostics = (
            {
                stage: stage_visibility_summary(
                    stage=stage,
                    milestone_window=milestone_window,
                )
                for stage in ("bridge", "plan", "draft", "judge")
            }
            if bool(getattr(settings, "stage_visibility_debug_enabled", True))
            else {}
        )
        if stage_visibility_diagnostics:
            try:
                self._storage.save_json(
                    layout.stage_visibility_diagnostics_path(chapter_number),
                    {
                        "report_type": "stage_visibility_diagnostics",
                        "chapter_number": chapter_number,
                        "stage_visibility_diagnostics": stage_visibility_diagnostics,
                    },
                )
            except Exception as exc:
                _logger.debug("Failed to persist stage visibility diagnostics: %s", exc)
        entity_reference_graph = compact_entity_reference_graph(
            layout,
            chapter_outline=chapter_outline,
            relevant_entities=source_slice_entities,
        )
        canon_payload = _inject_authoritative_state_into_canon_payload(
            _compact_canon_context_payload(
                canon_context,
                chapter_number,
            ),
            authoritative_narrative_state,
            chapter_contract,
            entity_reference_graph,
        )

        return ChapterStatePacket(
            chapter_number=chapter_number,
            chapter_outline=chapter_outline,
            canon_context=canon_payload,
            narrative_state_projection=authoritative_narrative_state,
            chapter_contract=chapter_contract,
            milestone_window=milestone_window,
            progression_ledger_tail=progression_ledger_tail,
            arc_liveness_report=arc_liveness_report,
            stage_visibility_diagnostics=stage_visibility_diagnostics,
            retrieval_evidence_pack=retrieval_evidence_pack,
            previous_exit_state=canon_context.previous_exit_state,
            previous_chapter_ending=previous_chapter_ending,
            previous_creative_report=previous_creative_report,
            previous_volume_summary=previous_volume_summary,
            current_volume_number=current_volume.volume_number if current_volume else 1,
            character_profiles=character_profiles,
            active_relationships=canon_context.active_relationships,
            active_plot_threads=canon_context.active_plot_threads,
            must_carry_forward=list(canon_context.must_carry_forward),
            known_characters=known_characters,
            previous_bridge=previous_bridge,
            accumulated_forbidden_repetition=accumulated_forbidden
            + await asyncio.to_thread(
                self._detect_overused_vocabulary,
                layout,
                chapter_number,
                overused_vocabulary_window,
            ),
            narrative_context=narrative_ctx,
            planning_context_brief=planning_context_brief,
            world_setting_brief=world_setting_brief,
            guard_constraints=guard_constraints,
        )

    async def _build_retrieval_evidence_pack(
        self,
        *,
        chapter_number: int,
        chapter_outline: ChapterOutline,
    ) -> dict[str, Any]:
        """Keep semantic hits separate from authoritative canon state."""
        service = getattr(self._memory_context, "narrative_evidence_service", None)
        if service is None or not callable(getattr(service, "evidence_pack", None)):
            return {}
        query = self._build_semantic_memory_query(chapter_number, chapter_outline)
        if not query:
            return {}
        try:
            settings = get_settings()
            pack = await service.evidence_pack(
                purpose="chapter_planning_and_review",
                query=query,
                canon_revision=f"chapter:{chapter_number - 1}",
                max_visible_chapter=max(0, chapter_number - 1),
                candidate_limit=int(
                    getattr(settings, "long_narrative_evidence_candidate_limit", 24) or 24
                ),
                evidence_token_budget=int(
                    getattr(settings, "long_narrative_evidence_token_budget", 2400) or 2400
                ),
            )
            return cast(dict[str, Any], pack.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Failed to build retrieval evidence pack: %s", exc)
            return {}

    @staticmethod
    def _build_semantic_memory_query(
        chapter_number: int,
        chapter_outline: ChapterOutline,
    ) -> str:
        parts = [
            f"第{chapter_number}章",
            _clean_text(getattr(chapter_outline, "title", "")),
            _clean_text(getattr(chapter_outline, "goal", "")),
            _clean_text(getattr(chapter_outline, "pov_character", "")),
            _clean_text(getattr(chapter_outline, "setting", "")),
        ]
        for attr in ("main_plot_points", "subplot_points", "beats_summary"):
            value = getattr(chapter_outline, attr, None)
            if isinstance(value, list):
                parts.extend(_clean_text(item) for item in value[:8])
            else:
                parts.append(_clean_text(value))
        return " / ".join(part for part in parts if part)

    def _detect_overused_vocabulary(
        self,
        layout: Any,
        chapter_number: int,
        window: int = 3,
    ) -> list[str]:
        """Scan recent chapters for overused expressions and return them as
        forbidden-repetition source terms so LLM avoids them in the next draft.

        Uses statistical n-gram analysis instead of a hardcoded word list:
        extracts 2-4 character n-grams, counts frequency across recent
        chapters, and flags expressions that appear far more often than
        expected.

        The window is expanded to max(window, 8) when enough chapters exist,
        ensuring global repetition patterns (e.g. "袖中" 481 times across
        52 chapters) are caught early.  The return value is the bare source
        term, not a diagnostic sentence, so later production stages only see
        actionable vocabulary.
        """
        import re as _re
        from collections import Counter

        # ── Expand window: use at least 8 chapters or all available ──
        effective_window = max(window, min(chapter_number - 1, 8))

        # ── Gather text from recent chapters ──────────────────────────
        chapter_texts: list[str] = []
        for ch in range(max(1, chapter_number - effective_window), chapter_number):
            ch_path = layout.chapter_path(ch)
            try:
                text = self._storage.load_text(ch_path)
            except Exception:
                continue
            loaded_text = _coerce_loaded_text(text)
            if loaded_text:
                chapter_texts.append(loaded_text)

        if not chapter_texts:
            return []

        scanned = len(chapter_texts)
        combined = "".join(chapter_texts)

        # Strip punctuation / whitespace so n-grams don't cross boundaries
        clean = _re.sub(
            r"[\s，。；：、！？…\u201c\u201d\u2018\u2019"
            r"\u300c\u300d\u300e\u300f（）【】\-—\n\r\t\"']",
            "\x00",  # sentinel for boundary
            combined,
        )

        # ── Extract n-gram frequencies ────────────────────────────────
        counts: Counter[str] = Counter()
        for n in (2, 3, 4):
            for i in range(len(clean) - n + 1):
                gram = clean[i : i + n]
                if "\x00" in gram:
                    continue  # skip cross-boundary fragments
                counts[gram] += 1

        # ── Filter out common function words / particles ──────────────
        # Minimal stop-set: only genuinely structural items that are
        # always high-frequency and never stylistically interesting.
        _STOP = frozenset(
            "的了是在有和不人这那我他她你们说都就也会"
            "着把被让给从到去来上下里中对又而但还很过"
            "要能可得地之与为以于如只已时没看想一些"
        )
        _STOP_BIGRAMS = frozenset(
            {
                "一个",
                "什么",
                "自己",
                "没有",
                "不是",
                "可以",
                "不会",
                "这个",
                "那个",
                "他们",
                "她们",
                "我们",
                "知道",
                "因为",
                "所以",
                "已经",
                "不过",
                "但是",
                "如果",
                "虽然",
                "或者",
                "还是",
                "就是",
                "只是",
                "然后",
                "可能",
                "应该",
                "这样",
                "那样",
                "一样",
                "起来",
                "出来",
                "下来",
                "之中",
                "也是",
                "不了",
                "了一",
                "的人",
                "的是",
                "一声",
                "有些",
                "那些",
                "这些",
                "两人",
                "此时",
                "便是",
                "却是",
                "自然",
                "竟然",
            }
        )

        # Per-chapter average thresholds — lowered from original to catch
        # patterns earlier before they solidify across many chapters.
        _THRESHOLDS = {2: 6, 3: 3, 4: 2}

        overused: list[str] = []
        seen_channels: set[str] = set()
        for expr, cnt in counts.most_common(500):
            if len(expr) == 1:
                continue
            if expr in _STOP or expr in _STOP_BIGRAMS:
                continue
            # Single-char stop: skip if every char is a stop word
            if all(c in _STOP for c in expr):
                continue
            avg = cnt / scanned
            if avg < _THRESHOLDS.get(len(expr), 2):
                continue
            # Deduplicate overlapping expressions: if a 4-gram contains
            # an already-flagged 2/3-gram, skip the shorter one or vice
            # versa — keep the longer, more specific expression.
            channel = expr[:2]
            if channel in seen_channels and len(expr) == 2:
                continue
            seen_channels.add(channel)
            overused.append(expr)
            if len(overused) >= 15:
                break

        return overused

    def _extract_guard_handoff_constraints(
        self,
        guard_report: dict[str, Any],
        *,
        target_chapter: int,
    ) -> tuple[bool, list[str]]:
        """Read the accepted one-chapter handoff from a guard report.

        A report without ``next_chapter_handoff`` predates the structured
        protocol and falls back to the legacy creative-report marker.  Once a
        handoff exists, including an explicitly rejected one, it is decisive.
        """

        handoff = guard_report.get("next_chapter_handoff")
        if not isinstance(handoff, dict):
            return False, []
        try:
            handoff_target = int(handoff.get("target_chapter", 0) or 0)
        except (TypeError, ValueError):
            return True, []
        if handoff_target != target_chapter:
            return True, []
        if str(handoff.get("status", "")).strip().lower() != "accepted":
            return True, []
        return True, self._normalize_guard_constraints(handoff.get("constraints", []))

    @staticmethod
    def _normalize_guard_constraints(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        constraints: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item or "").split())[:300]
            if not text or text in seen:
                continue
            constraints.append(text)
            seen.add(text)
            if len(constraints) >= 5:
                break
        return constraints

    def _extract_legacy_guard_constraints(
        self,
        previous_report: dict[str, Any],
    ) -> list[str]:
        """Read the pre-handoff textual guardrail marker for old projects.

        约束存储在 suggestions_for_next_chapter 字段中，格式为：
        "AI护栏约束：\n- 约束1\n- 约束2..."

        Args:
            previous_report: 上一章的 creative report 数据

        Returns:
            护栏约束列表
        """
        suggestions = previous_report.get("suggestions_for_next_chapter", "")
        if not isinstance(suggestions, str):
            return []

        constraints: list[str] = []
        in_guard_block = False
        for line in suggestions.split("\n"):
            line = line.strip()
            if line.startswith("AI护栏约束"):
                in_guard_block = True
                continue
            if in_guard_block:
                if line.startswith("- "):
                    constraint = line[2:].strip()
                    if constraint:
                        constraints.append(constraint)
                elif line == "":
                    continue
                else:
                    in_guard_block = False

        return self._normalize_guard_constraints(constraints)

    def _build_planning_context_brief(
        self,
        *,
        chapter_number: int,
        chapter_outline: ChapterOutline,
        canon_context: Any,
        narrative_ctx: Any,
        previous_creative_report: dict[str, Any] | None,
    ) -> str:
        """Build a planning context brief.

        Returns a lightweight structural brief (open questions, active threads,
        POV location, must-carry-forward) assembled from canon/narrative context
        without additional embedding search.  Intentionally concise — the heavy
        semantic hints are already in ``memory_hints`` (populated by the planning
        stage).
        """
        parts: list[str] = []

        # ── Open questions from previous exit state ──────────────────────────
        exit_state = _coerce_field(canon_context, "previous_exit_state", None)
        if exit_state is not None:
            open_qs = [
                str(q).strip()
                for q in list(_coerce_field(exit_state, "open_questions", []) or [])[:3]
                if str(q).strip()
            ]
            if open_qs:
                parts.append("未解悬念：" + "；".join(open_qs))

        # ── Active plot threads (open / ongoing only) ─────────────────────────
        threads = list(_coerce_field(canon_context, "active_plot_threads", []) or [])
        open_threads = [
            str(_coerce_field(t, "title", "")).strip()
            for t in threads
            if str(_coerce_field(t, "status", "")).lower()
            in {"open", "active", "ongoing", "进行中"}
            and str(_coerce_field(t, "title", "")).strip()
        ][:3]
        if open_threads:
            parts.append("进行中线索：" + "；".join(open_threads))

        # ── POV character's current location ─────────────────────────────────
        pov = str(getattr(chapter_outline, "pov_character", "") or "").strip()
        if pov:
            raw_chars = _coerce_field(canon_context, "characters", {}) or {}
            if isinstance(raw_chars, dict) and pov in raw_chars:
                loc = _clean_text(_coerce_field(raw_chars[pov], "location", ""))
                if not loc:
                    physical = _coerce_field(raw_chars[pov], "physical", {})
                    loc = _clean_text(_coerce_field(physical, "location", ""))
                if loc:
                    parts.append(f"{pov}当前位置：{loc}")

        # ── Must-carry-forward constraints ────────────────────────────────────
        must_fwd = [
            carry_forward_text(item)
            for item in list(_coerce_field(canon_context, "must_carry_forward", []) or [])[:2]
            if carry_forward_text(item)
        ]
        if must_fwd:
            parts.append("必须延续：" + "；".join(must_fwd))

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# Standalone cross-chapter analysis helpers (called from draft.py)
# ═══════════════════════════════════════════════════════════════════════


def detect_opening_patterns(
    storage: FileSystemStorage,
    layout: Any,
    chapter_number: int,
    window: int = 5,
) -> dict[str, Any]:
    """Analyze opening patterns from recent chapters and suggest variety.

    Returns a dict with:
      - recent_openings: list of (chapter_num, pattern_type) tuples
      - suggested_technique: a concrete opening technique to use
      - avoid_patterns: pattern types used in last 3 chapters
      - risk_note: stronger warning when recent openings drift into formulaic setup
    """
    import random
    import re as _re

    time_tokens = (
        r"(清晨|黄昏|夜|天亮|拂晓|午后|正午|日暮|黎明|子时|丑时|寅时|卯时|辰时|"
        r"巳时|午时|未时|申时|酉时|戌时|亥时|翌日|次日|三日后|数日)"
    )
    place_tokens = r"(廊|院|阁|殿|门|街|巷|河|湖|楼|府|堂|寺|桥|道|路|林|山|房|帐|车|船)"
    env_tokens = r"(风|雨|雪|月|灯火|雾|云|烟|寒意|凉意|夜色|晨雾|霜|露)"

    _PATTERN_MATCHERS: list[tuple[str, "_re.Pattern[str]"]] = [
        (
            "formulaic_time_place",
            _re.compile(rf"^.{0, 16}{time_tokens}.{{0,20}}{place_tokens}.{{0,20}}{env_tokens}"),
        ),
        ("time_marker", _re.compile(rf"^.{0, 12}{time_tokens}")),
        ("dialogue", _re.compile(r'^.{0,3}["“「]')),
        ("action", _re.compile(r"^.{0,8}(一|忽然|猛然|突然|蓦地|骤然|只见|还未|尚未)")),
        ("sensory", _re.compile(r"^.{0,12}(声|味|香|臭|光|暗|冷|热|痛|痒)")),
        ("环境描写", _re.compile(rf"^.{0, 10}{env_tokens}")),
        (
            "protagonist_name",
            _re.compile(
                r"^[\s]*[\u4e00-\u9fff]{2,4}(站|坐|走|看|望|立|行|回|转|推|踏|抬|低|闭|睁|伸|抓|握|靠)"
            ),
        ),
    ]

    _TECHNIQUE_POOL = [
        "以对话切入——角色中途对话开场，读者直接进入冲突",
        "以感官细节切入——用一个具体的触觉/嗅觉/听觉锚点开场",
        "以动作中场切入——角色正在执行某个动作的过程中开场",
        "以悬念/反问切入——抛出一个未解问题或反常现象",
        "以回忆闪切切入——从一个记忆片段跳入当下",
        "以他人视角观察切入——通过旁人眼光引出主角",
        "以物件/信件特写切入——聚焦一个关键道具开始叙述",
        "以环境反常切入——描写一个与预期不同的环境状态",
    ]
    dynamic_techniques = [
        "以对话切入——角色中途对话开场，读者直接进入冲突",
        "以动作中场切入——角色正在执行某个动作的过程中开场",
        "以悬念/反问切入——抛出一个未解问题或反常现象",
        "以物件/信件特写切入——聚焦一个关键道具开始叙述",
        "以环境反常切入——描写一个与预期不同的环境状态",
    ]

    recent_openings: list[tuple[int, str]] = []
    for ch in range(max(1, chapter_number - window), chapter_number):
        ch_path = layout.chapter_path(ch)
        try:
            text = storage.load_text(ch_path)
        except Exception:
            continue
        loaded_text = _coerce_loaded_text(text)
        if not loaded_text:
            continue
        opening = loaded_text[:220].strip()
        pattern_type = "other"
        for pat_name, pat_re in _PATTERN_MATCHERS:
            if pat_re.search(opening):
                pattern_type = pat_name
                break
        recent_openings.append((ch, pattern_type))

    avoid = {p for _, p in recent_openings[-3:]}
    formulaic_count = sum(
        1
        for _, pattern in recent_openings
        if pattern in {"formulaic_time_place", "time_marker", "环境描写"}
    )

    risk_note = ""
    if formulaic_count >= 2 or "formulaic_time_place" in avoid:
        risk_note = (
            "近几章开场偏向“报时/定位/铺景”模板，请直接从动作、对话或异常事件切入，"
            "不要先报时再写环境。"
        )

    preferred_pool = dynamic_techniques if risk_note else _TECHNIQUE_POOL
    candidates = [t for t in preferred_pool if not any(a in t for a in avoid)]
    suggested = random.choice(candidates) if candidates else random.choice(preferred_pool)

    return {
        "recent_openings": recent_openings,
        "suggested_technique": suggested,
        "avoid_patterns": list(avoid),
        "risk_note": risk_note,
        "formulaic_count": formulaic_count,
    }


def detect_action_tag_overuse(
    storage: FileSystemStorage,
    layout: Any,
    chapter_number: int,
    window: int = 3,
) -> list[str]:
    """Detect overused action/body-language tags in recent chapters.

    Returns a list of overused tags like "微微一笑", "深吸一口气", etc.
    """
    import re as _re
    from collections import Counter

    # Common body-language / action tag patterns in Chinese fiction
    _TAG_PATTERN = _re.compile(
        r"(微微一笑|深吸一口气|眉头一皱|嘴角[上微]扬|眉头紧锁|"
        r"轻声[说道]|淡淡[说道地]|缓缓[说道地开]|冷冷[说道地]|"
        r"嘴角勾起|眼眸微[眯闪暗]|唇角[上微]扬|挑[了]?眉|"
        r"攥紧[了]?拳|握紧[了]?拳|咬[了]?[咬紧]?唇|"
        r"抿[了]?[抿紧]?唇|垂[下了]?眸|抬[起了]?眸|"
        r"点[了]?点头|摇[了]?摇头|叹[了]?[口一]气|"
        r"心[中头里]一[沉紧跳动酸]|[不微]由自主)"
    )

    counts: Counter[str] = Counter()
    for ch in range(max(1, chapter_number - window), chapter_number):
        ch_path = layout.chapter_path(ch)
        try:
            text = storage.load_text(ch_path)
        except Exception:
            continue
        loaded_text = _coerce_loaded_text(text)
        if not loaded_text:
            continue
        for m in _TAG_PATTERN.finditer(loaded_text):
            counts[m.group()] += 1

    overused = [f"「{tag}」已用{cnt}次" for tag, cnt in counts.most_common(8) if cnt >= 3]
    return overused


def detect_weak_senses(
    storage: FileSystemStorage,
    layout: Any,
    chapter_number: int,
    window: int = 5,
) -> list[str]:
    """Auto-detect underrepresented sensory modalities from recent chapters.

    Returns a list of weak senses like ["味觉", "嗅觉"].
    """
    import re as _re

    _SENSE_PATTERNS: dict[str, "_re.Pattern[str]"] = {
        "视觉": _re.compile(
            r"(看[到见着]|望[向着去]|目光|眼[前中底]|映入|闪[烁过]|光[芒线影]|颜色|明暗)"
        ),
        "听觉": _re.compile(
            r"(听[到见着]|声[音响]|嗡[嗡鸣]|簌簌|哗[啦哗]|叮[当咚]|呼啸|沙沙|回响|耳[边畔])"
        ),
        "触觉": _re.compile(
            r"(触感|摸[到着上]|冰凉|温热|滑腻|粗糙|刺痛|灼[热烧]|麻[痹木]|柔软|坚硬|肌肤)"
        ),
        "嗅觉": _re.compile(
            r"(闻[到着]|气味|香[气味]|臭|腥|芬芳|馨香|刺鼻|清新|烟[味气]|药香|花香)"
        ),
        "味觉": _re.compile(
            r"(尝[到着]|味道|苦涩|甘甜|酸|咸|辛辣|甜腻|口[中感]|舌[尖头]|回甘|入口)"
        ),
    }

    sense_counts: dict[str, int] = {s: 0 for s in _SENSE_PATTERNS}
    for ch in range(max(1, chapter_number - window), chapter_number):
        ch_path = layout.chapter_path(ch)
        try:
            text = storage.load_text(ch_path)
        except Exception:
            continue
        loaded_text = _coerce_loaded_text(text)
        if not loaded_text:
            continue
        for sense_name, pattern in _SENSE_PATTERNS.items():
            sense_counts[sense_name] += len(pattern.findall(loaded_text))

    total = sum(sense_counts.values()) or 1
    weak = [
        name
        for name, cnt in sense_counts.items()
        if cnt / total < 0.08  # less than 8% of total sensory mentions
    ]
    return weak
