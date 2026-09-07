"""PlanChapterStep — creates a structured v2 plan for a chapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import sanitize_story_text
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.core.schemas.continuity import ChapterPlan, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.utils.string import clean_str
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.constraints.pov_constraint_builder import (
    compute_pov_constraints,
)
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.forbidden_sources import (
    build_forbidden_source_records,
    enrich_forbidden_records_with_motif_context,
    forbidden_base_text,
    normalize_forbidden_source_item,
    project_forbidden_source_records,
    rank_forbidden_terms,
)
from novel_forge.pipeline.steps.prompt_diagnostics import log_prompt_diagnostics
from novel_forge.pipeline.token_budget import route_output_limit

from .context import (
    build_character_identity_cards,
    trim_plan_canon_context,
    trim_plan_memory_hints,
)
from .hints import (
    _compact_contract_text,
    _enforce_opening_handoff_constraints,
    _enrich_scene_intents,
    _fallback_relationship_evolution,
    _filter_abstract_emotion_labels,
    _normalize_beats,
    _normalize_chapter_type,
    _normalize_required_characters,
    _normalize_scene_intents,
    _normalize_scene_target_words,
    _normalize_string_list,
    _patch_missing_beat_scenes,
    enforce_revelation_budget,
    enforce_scene_switch_limit,
    enforce_unresolved_retention,
    resolve_min_unresolved_threads,
    resolve_revelation_budget,
    response_is_token_capped,
)
from .transition_claims import assign_required_state_transitions_to_scenes

_log = get_logger("pipeline.steps.planning.core")

_QUOTA_FORBIDDEN_MARKERS = frozenset(
    {
        "仅用一次",
        "控制频率",
        "频率控制",
        "少量",
        "限量",
        "最多",
        "不超过",
        "可沿用",
        "可复用",
        "场景锚点",
        "剧情锚点",
    }
)
_GENERIC_CHOICE_PRESSURE = "本场必须让角色在目标、风险或关系代价之间做出可见选择。"
_GENERIC_EMOTIONAL_BEAT_PREFIXES = (
    "承接上一场余波",
    "情绪完成阶段性收束",
    "情绪逐步收紧",
    "把情绪推向抉择",
)


def _forbidden_base_text(value: Any) -> str:
    return forbidden_base_text(value)


def _is_quota_forbidden(value: Any) -> bool:
    text = clean_str(value)
    return any(marker in text for marker in _QUOTA_FORBIDDEN_MARKERS)


def _dedupe_expression_channel_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        channel_id = str(record.get("channel_id", "") or "")
        text = str(record.get("text", "") or "")
        if not channel_id and not text:
            continue
        key = (channel_id, text)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
        if len(result) >= 12:
            break
    return result


def _outline_allowed_character_names(outline: ChapterOutline) -> list[str]:
    names: list[str] = []
    for raw in [
        getattr(outline, "pov_character_name", ""),
        getattr(outline, "pov_character", ""),
        *list(getattr(outline, "involved_character_names", []) or []),
        *list(getattr(outline, "involved_characters", []) or []),
    ]:
        name = sanitize_story_text(clean_str(raw))
        if name and name not in names:
            names.append(name)
    return names


def _outline_emotional_plan_text(outline: ChapterOutline, field: str) -> str:
    plan = getattr(outline, "emotional_plan", None)
    return sanitize_story_text(clean_str(getattr(plan, field, "")))


def _fallback_emotional_arc_from_outline(outline: ChapterOutline) -> str:
    parts = [
        _outline_emotional_plan_text(outline, "entry_state"),
        _outline_emotional_plan_text(outline, "pressure_source"),
        _outline_emotional_plan_text(outline, "turning_emotion"),
        _outline_emotional_plan_text(outline, "exit_aftertaste"),
    ]
    return " → ".join(part for part in parts if part)


def _fallback_relationship_evolution_from_outline(outline: ChapterOutline) -> list[str]:
    choice = _outline_emotional_plan_text(outline, "relationship_choice")
    return [choice] if choice else []


def _fallback_choice_pressure_from_outline(outline: ChapterOutline) -> str:
    pressure = _outline_emotional_plan_text(outline, "pressure_source")
    choice = _outline_emotional_plan_text(outline, "relationship_choice")
    if pressure and choice:
        return f"{pressure}；{choice}"
    return pressure or choice


def _is_generic_choice_pressure(value: Any) -> bool:
    return sanitize_story_text(clean_str(value)) == _GENERIC_CHOICE_PRESSURE


def _is_generic_emotional_beat(value: Any) -> bool:
    text = sanitize_story_text(clean_str(value))
    return any(text.startswith(prefix) for prefix in _GENERIC_EMOTIONAL_BEAT_PREFIXES)


def _filter_scene_cast_and_emotion(
    scenes: list[dict[str, Any]],
    *,
    outline: ChapterOutline,
) -> list[dict[str, Any]]:
    allowed_names = _outline_allowed_character_names(outline)
    choice_pressure = _fallback_choice_pressure_from_outline(outline)
    if not allowed_names and not choice_pressure:
        return scenes
    allowed_set = set(allowed_names)
    fallback_character = (
        sanitize_story_text(clean_str(getattr(outline, "pov_character_name", "")))
        or sanitize_story_text(clean_str(getattr(outline, "pov_character", "")))
        or (allowed_names[0] if allowed_names else "")
    )
    turning_emotion = _outline_emotional_plan_text(outline, "turning_emotion")

    filtered: list[dict[str, Any]] = []
    for scene in scenes:
        item = dict(scene)
        if allowed_names:
            current = _normalize_required_characters(
                item.get("required_characters", []),
                fallback_pov=fallback_character,
            )
            current = [name for name in current if name in allowed_set]
            if not current and fallback_character:
                current = [fallback_character]
            item["required_characters"] = current

            pov = sanitize_story_text(clean_str(item.get("pov_character")))
            if pov not in allowed_set:
                item["pov_character"] = fallback_character

            motivations = []
            for raw in list(item.get("character_motivations", []) or []):
                if not isinstance(raw, dict):
                    continue
                character = sanitize_story_text(clean_str(raw.get("character")))
                if character in allowed_set:
                    motivation = dict(raw)
                    motivation["character"] = character
                    motivations.append(motivation)
            if motivations:
                item["character_motivations"] = motivations
            elif current:
                from .hints import _fallback_character_motivations

                item["character_motivations"] = _fallback_character_motivations(
                    current,
                    outline=outline,
                )

            voice_targets = item.get("dialogue_voice_targets")
            if isinstance(voice_targets, dict):
                item["dialogue_voice_targets"] = {
                    key: value for key, value in voice_targets.items() if key in allowed_set
                }

        if choice_pressure and (
            not sanitize_story_text(item.get("choice_pressure") or "")
            or _is_generic_choice_pressure(item.get("choice_pressure"))
        ):
            item["choice_pressure"] = choice_pressure
        if turning_emotion and (
            not sanitize_story_text(item.get("emotional_beat") or "")
            or _is_generic_emotional_beat(item.get("emotional_beat"))
        ):
            item["emotional_beat"] = turning_emotion
        filtered.append(item)
    return filtered


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_cross_scene_intent(
    value: Any,
    *,
    scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    scene_count = len(scenes)
    raw = value if isinstance(value, dict) else {}
    raw_refs = raw.get("cross_scene_references", [])
    refs: list[dict[str, Any]] = []
    if isinstance(raw_refs, list):
        for item in raw_refs:
            if not isinstance(item, dict):
                continue
            ref: dict[str, Any] = {
                "from_scene": clean_str(item.get("from_scene")),
                "to_scene": clean_str(item.get("to_scene")),
                "ref_type": clean_str(item.get("ref_type") or "callback"),
                "description": sanitize_story_text(clean_str(item.get("description"))),
            }
            if item.get("requirement"):
                ref["requirement"] = item["requirement"]
            if ref["description"] or ref["from_scene"] or ref["to_scene"]:
                refs.append(ref)
            if len(refs) >= 6:
                break

    raw_pacing = raw.get("pacing_curve", [])
    pacing: list[int] = []
    if isinstance(raw_pacing, list):
        for item in raw_pacing:
            try:
                pacing.append(max(1, min(5, int(item))))
            except (TypeError, ValueError):
                continue
    if scene_count > 0:
        if len(pacing) < scene_count:
            pacing.extend([3] * (scene_count - len(pacing)))
        elif len(pacing) > scene_count:
            pacing = pacing[:scene_count]

    return {"cross_scene_references": refs, "pacing_curve": pacing}


def _compact_anchor_text(value: Any) -> str:
    return re.sub(
        r"[，。！？；：、\"'“”‘’（）《》〈〉【】『』「」·,.!?;:()\[\]{}<>\-—~…\s]+",
        "",
        clean_str(value),
    )


def _protected_plan_texts(
    *,
    opening_contract: str,
    closing_contract: str,
    scenes: list[dict[str, Any]],
    foreshadowing_plan: list[str],
    key_revelations: list[str],
    relationship_evolution: list[str],
    required_state_transitions: list[str],
    intentional_callbacks: list[str],
) -> list[str]:
    texts: list[str] = [
        opening_contract,
        closing_contract,
        *foreshadowing_plan,
        *key_revelations,
        *relationship_evolution,
        *required_state_transitions,
        *intentional_callbacks,
    ]
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        for key in (
            "summary",
            "purpose",
            "conflict",
            "required_outcome",
            "exit_target_state",
            "location",
            "relationship_dynamics",
            "emotional_beat",
            "sensory_notes",
            "choice_pressure",
            "scene_resistance",
            "revelation_level",
            "symbol_usage_policy",
        ):
            value = scene.get(key)
            if value:
                texts.append(str(value))
        for item in scene.get("required_characters", []) or []:
            texts.append(str(item))
        for key in ("owned_events", "owned_revelations", "owned_state_changes"):
            texts.extend(str(item) for item in list(scene.get(key) or []) if item)
    return [text for text in texts if clean_str(text)]


def _forbidden_relevance_texts(
    *,
    outline: ChapterOutline,
    packet: ChapterStatePacket | None,
    opening_contract: str,
    closing_contract: str,
    scenes: list[dict[str, Any]],
    foreshadowing_plan: list[str],
    key_revelations: list[str],
    relationship_evolution: list[str],
    required_state_transitions: list[str],
    intentional_callbacks: list[str],
) -> list[str]:
    texts = [
        getattr(outline, "title", ""),
        getattr(outline, "goal", ""),
        getattr(outline, "setting", ""),
        getattr(outline, "pov_character", ""),
        opening_contract,
        closing_contract,
        *foreshadowing_plan,
        *key_revelations,
        *relationship_evolution,
        *required_state_transitions,
        *intentional_callbacks,
    ]
    if packet is not None:
        texts.extend(list(getattr(packet, "must_carry_forward", []) or []))
        bridge = getattr(packet, "bridge", None)
        if bridge is not None:
            texts.extend(
                [
                    getattr(bridge, "action_handoff", ""),
                    getattr(bridge, "emotional_carryover", ""),
                    getattr(bridge, "bridge_summary", ""),
                ]
            )
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        texts.extend(
            str(scene.get(key) or "")
            for key in (
                "summary",
                "purpose",
                "conflict",
                "required_outcome",
                "exit_target_state",
                "location",
                "relationship_dynamics",
                "emotional_beat",
                "sensory_notes",
                "choice_pressure",
                "scene_resistance",
                "revelation_level",
                "symbol_usage_policy",
            )
        )
    return [text for text in texts if clean_str(text)]


def _term_in_texts(term: str, texts: list[str]) -> bool:
    compact = _compact_anchor_text(term)
    if len(compact) < 2:
        return False
    return any(compact in _compact_anchor_text(text) for text in texts)


def _split_forbidden_constraints(
    *,
    hard_forbidden: list[str],
    soft_forbidden: list[str],
    quota_forbidden: list[str],
    protected_texts: list[str],
) -> tuple[list[str], list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    quota: list[str] = []

    def _add(target: list[str], item: str) -> None:
        text = clean_str(item)
        if text and text not in target:
            target.append(text)

    for item in quota_forbidden:
        _add(quota, item)
    for item in soft_forbidden:
        if _is_quota_forbidden(item):
            _add(quota, item)
        else:
            _add(soft, item)

    for item in hard_forbidden:
        base = _forbidden_base_text(item)
        protected = _term_in_texts(base, protected_texts)
        if _is_quota_forbidden(item):
            _add(quota, item)
        elif protected:
            _add(soft, item)
        else:
            _add(hard, item)

    quota_bases = {_forbidden_base_text(item) for item in quota}
    hard = [item for item in hard if _forbidden_base_text(item) not in quota_bases]
    hard_bases = {_forbidden_base_text(item) for item in hard}
    soft = [
        item
        for item in soft
        if _forbidden_base_text(item) not in quota_bases
        and _forbidden_base_text(item) not in hard_bases
    ]
    return hard, soft, quota


def _kernel_slices_to_canon_context(
    *,
    entities: list[dict[str, Any]] | None = None,
    relationships: list[dict[str, Any]] | None = None,
    knowledge_ledger: list[dict[str, Any]] | None = None,
    object_ledger: list[dict[str, Any]] | None = None,
    world_rules: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    promise_ledger: list[dict[str, Any]] | None = None,
    chapter_summaries: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Convert StoryKernel field slices into the prompt-card context shape."""
    canon: dict[str, Any] = {
        "characters": {},
        "recent_events": [],
        "active_foreshadowing": [],
        "immutable_facts": [],
    }

    # Entities → characters (indexed by name)
    if entities:
        for ent in entities:
            name = str(ent.get("name", "")).strip()
            if not name:
                continue
            canon["characters"][name] = dict(ent)

    # Enrich characters with knowledge ledger entries
    if knowledge_ledger and canon["characters"]:
        name_by_id: dict[str, str] = {}
        for ent in entities or []:
            eid = str(ent.get("entity_id", "")).strip()
            ename = str(ent.get("name", "")).strip()
            if eid and ename:
                name_by_id[eid] = ename
        for entry in knowledge_ledger:
            eid = str(entry.get("entity_id", "")).strip()
            ename = name_by_id.get(eid, "")
            if not ename or ename not in canon["characters"]:
                continue
            char = canon["characters"][ename]
            if "known_facts" not in char:
                char["known_facts"] = []
            fact = str(entry.get("fact", "")).strip()
            if fact:
                char["known_facts"].append(
                    {
                        "fact": fact,
                        "knowledge_type": entry.get("knowledge_type", "known"),
                        "confidence": entry.get("confidence", 1.0),
                    }
                )

    # Relationships → relationships list stored on each participating character.
    if relationships:
        for rel in relationships:
            src = str(rel.get("source_entity_id", "")).strip()
            tgt = str(rel.get("target_entity_id", "")).strip()
            label = str(rel.get("label", "")).strip()
            rtype = str(rel.get("relation_type", "")).strip()
            if not src or not tgt:
                continue
            for eid in (src, tgt):
                ename = ""
                for ent in entities or []:
                    if str(ent.get("entity_id", "")).strip() == eid:
                        ename = str(ent.get("name", "")).strip()
                        break
                if ename and ename in canon["characters"]:
                    char = canon["characters"][ename]
                    if "relationships" not in char:
                        char["relationships"] = []
                    char["relationships"].append(
                        {
                            "target": tgt,
                            "type": rtype,
                            "label": label,
                        }
                    )

    # Timeline → recent_events
    if timeline:
        for anchor in timeline:
            event = str(anchor.get("event", "")).strip()
            if not event:
                continue
            canon["recent_events"].append(
                {
                    "event": event,
                    "chapter": anchor.get("chapter", 0),
                    "significance": anchor.get("significance", "minor"),
                }
            )

    # Promise ledger → active_foreshadowing (only planted/hinted)
    if promise_ledger:
        active_statuses = {"planted", "hinted", "partially_paid"}
        for entry in promise_ledger:
            status = str(entry.get("status", "")).strip()
            if status not in active_statuses:
                continue
            desc = str(entry.get("description", "")).strip()
            if desc:
                canon["active_foreshadowing"].append(
                    {
                        "description": desc,
                        "promise_type": entry.get("promise_type", "foreshadow"),
                        "planted_chapter": entry.get("planted_chapter", 0),
                    }
                )

    # World rules → immutable_facts
    if world_rules:
        for rule in world_rules:
            content = str(rule.get("content", "")).strip()
            if not content:
                continue
            canon["immutable_facts"].append(
                {
                    "content": content,
                    "category": rule.get("category", "general"),
                    "severity": rule.get("severity", "hard"),
                }
            )

    # Chapter summaries (not stored in canon_context directly, but available)
    if chapter_summaries:
        canon["chapter_summaries"] = dict(chapter_summaries)

    return canon


def _value_field(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _mapping_from_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json")
        return dict(data) if isinstance(data, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _build_pov_entity_indexes(
    kernel_context: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    name_to_id: dict[str, str] = {}
    id_to_name: dict[str, str] = {}
    sensory_by_id: dict[str, list[str]] = {}
    if not isinstance(kernel_context, dict):
        return name_to_id, id_to_name, sensory_by_id

    for entity in list(kernel_context.get("entities") or []):
        entity_id = clean_str(_value_field(entity, "entity_id", ""))
        name = clean_str(_value_field(entity, "name", ""))
        if not entity_id:
            continue
        id_to_name[entity_id] = name or entity_id
        name_to_id[entity_id] = entity_id
        if name:
            name_to_id[name] = entity_id
        aliases = _value_field(entity, "aliases", []) or []
        if isinstance(aliases, (list, tuple, set)):
            for alias in aliases:
                alias_text = clean_str(alias)
                if alias_text:
                    name_to_id[alias_text] = entity_id

        raw_sensory = _value_field(entity, "sensory_access_rules", None)
        attrs = _value_field(entity, "attributes", {}) or {}
        if raw_sensory in (None, "", [], {}) and isinstance(attrs, dict):
            raw_sensory = attrs.get("sensory_access_rules")
        sensory_rules = _normalize_string_list(raw_sensory, max_chars=160)
        if sensory_rules:
            sensory_by_id[entity_id] = sensory_rules

    return name_to_id, id_to_name, sensory_by_id


def _knowledge_entries_for_pov_constraints(
    value: Any,
    *,
    id_to_name: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple, set)):
        return []
    entries: list[dict[str, Any]] = []
    for item in value:
        data = _mapping_from_value(item)
        entity_id = clean_str(data.get("entity_id"))
        if not entity_id:
            continue
        if not clean_str(data.get("entity_name")) and id_to_name.get(entity_id):
            data["entity_name"] = id_to_name[entity_id]
        entries.append(data)
    return entries


def _apply_pov_knowledge_constraints(
    plan_payload: dict[str, Any],
    *,
    outline: ChapterOutline,
    character_knowledge_slice: Any,
    kernel_context: dict[str, Any] | None,
) -> None:
    scenes = plan_payload.get("scene_intents")
    if not isinstance(scenes, list):
        return

    name_to_id, id_to_name, sensory_by_id = _build_pov_entity_indexes(kernel_context)
    knowledge_ledger = _knowledge_entries_for_pov_constraints(
        character_knowledge_slice,
        id_to_name=id_to_name,
    )
    fallback_pov = clean_str(getattr(outline, "pov_character", ""))
    try:
        chapter_number = int(getattr(outline, "chapter_number", 0) or 0)
    except (TypeError, ValueError):
        chapter_number = 0

    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        pov_name = clean_str(scene.get("pov_character")) or fallback_pov
        pov_id = name_to_id.get(pov_name, pov_name)
        scope_label = clean_str(scene.get("pov_scope")) or "limited"
        constraints = compute_pov_constraints(
            pov_character_id=pov_id,
            pov_character_name=pov_name,
            chapter_number=chapter_number,
            knowledge_ledger=knowledge_ledger,
            entity_sensory_rules=sensory_by_id,
            scope_label=scope_label,
        )
        scene["pov_knowledge_constraints"] = {
            "forbidden_knowledge": list(constraints.forbidden_knowledge),
            "sensory_limits": list(constraints.sensory_limits),
            "scope_label": constraints.scope_label,
        }


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _stage_literary_contract(stage_cards: dict[str, Any]) -> dict[str, Any]:
    source = _mapping(stage_cards.get("source"))
    return _mapping(source.get("literary_contract"))


def _scene_text(scene: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "summary",
        "purpose",
        "conflict",
        "required_outcome",
        "dramatic_question",
        "exit_target_state",
        "relationship_dynamics",
        "emotional_beat",
        "choice_pressure",
        "scene_resistance",
        "revelation_level",
        "symbol_usage_policy",
        "scene_goal",
        "handoff_to_next",
        "entry_state",
        "exit_state",
    ):
        value = scene.get(key)
        if value:
            parts.append(str(value))
    for key in ("owned_events", "owned_revelations", "owned_state_changes", "forbidden_overlap"):
        value = scene.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
    motivations = scene.get("character_motivations")
    if isinstance(motivations, list):
        for item in motivations:
            if isinstance(item, dict):
                parts.extend(
                    str(item.get(name) or "")
                    for name in ("character", "motivation", "stake")
                    if item.get(name)
                )
    return "；".join(parts)


def _contains_any_text(haystack: str, needles: list[Any]) -> bool:
    normalized = str(haystack or "")
    if not normalized.strip():
        return False
    for needle in needles:
        text = str(needle or "").strip()
        if text and (text in normalized or normalized in text):
            return True
    return False


def _coverage_text(value: Any) -> str:
    """Normalize narrative text for deterministic, evidence-only matching."""

    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(value or ""))


def _phrase_has_plan_evidence(haystack: str, phrase: str) -> bool:
    """Accept an exact phrase or strong shared narrative anchors.

    A literary contract can describe one event in a long sentence while a plan
    distributes its action, evidence, and consequence across several fields.
    Exact-substring matching therefore produces false failures.  The fallback
    remains conservative: at least three shared bigrams and 30% coverage of
    the contract phrase are required.
    """

    candidate = _coverage_text(phrase)
    source = _coverage_text(haystack)
    if not candidate or not source:
        return False
    if candidate in source or source in candidate:
        return True
    if len(candidate) < 6:
        return False
    grams = {candidate[index : index + 2] for index in range(len(candidate) - 1)}
    if len(grams) < 3:
        return False
    shared = sum(1 for gram in grams if gram in source)
    return shared >= 3 and shared / len(grams) >= 0.30


def _plan_covers_duty(haystack: str, duty: Any) -> bool:
    """Check a compound literary duty against distributed plan evidence."""

    text = str(duty or "").strip()
    if not text:
        return True
    if _phrase_has_plan_evidence(haystack, text):
        return True
    clauses = [
        clause.strip()
        for clause in re.split(r"[，、；。！？!?（）()]+", text)
        if len(_coverage_text(clause)) >= 6
    ]
    if not clauses:
        return False
    matched = sum(1 for clause in clauses if _phrase_has_plan_evidence(haystack, clause))
    return matched >= max(1, (len(clauses) + 1) // 2)


def _merge_literary_lists(*values: Any, limit: int = 999) -> list[str]:
    merged: list[str] = []
    for value in values:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    text = str(
                        item.get("description")
                        or item.get("summary")
                        or item.get("text")
                        or item.get("rule")
                        or item
                    ).strip()
                else:
                    text = str(item or "").strip()
                if text and text not in merged:
                    merged.append(text)
                if len(merged) >= limit:
                    return merged
        else:
            text = str(value or "").strip()
            if text and text not in merged:
                merged.append(text)
            if len(merged) >= limit:
                return merged
    return merged


def validate_literary_contract_plan_coverage(
    plan_payload: dict[str, Any],
    stage_cards: dict[str, Any],
) -> dict[str, Any]:
    """Return diagnostics for how well ChapterPlan covers source.literary_contract."""

    literary = _stage_literary_contract(stage_cards)
    if not literary:
        return {
            "status": "skipped",
            "critical_missing": [],
            "warning_missing": [],
            "details": "no literary_contract in stage cards",
        }

    scenes = [
        item for item in list(plan_payload.get("scene_intents") or []) if isinstance(item, dict)
    ]
    scene_texts = [_scene_text(scene) for scene in scenes]
    combined = "；".join(scene_texts)
    critical_missing: list[str] = []
    warning_missing: list[str] = []

    character = _mapping(literary.get("character"))
    required_characters = [
        str(item or "").strip()
        for item in list(character.get("required_characters") or [])
        if str(item or "").strip()
    ]
    if required_characters:
        motivation_chars: set[str] = set()
        for scene in scenes:
            for item in list(scene.get("character_motivations") or []):
                if isinstance(item, dict) and str(item.get("motivation") or "").strip():
                    motivation_chars.add(str(item.get("character") or "").strip())
        missing_motivations = [
            name for name in required_characters if name and name not in motivation_chars
        ]
        if len(missing_motivations) == len(required_characters):
            critical_missing.append("character.required_motivation")
        elif missing_motivations:
            warning_missing.append("character.partial_motivation")

    plot = _mapping(literary.get("plot"))
    plot_duties = _merge_literary_lists(
        plot.get("required_events", []),
        plot.get("required_progressions", []),
        plot.get("completion_criteria", []),
        plot.get("exit_state_targets", []),
        limit=12,
    )
    plan_transitions = "；".join(
        str(item) for item in list(plan_payload.get("required_state_transitions") or [])
    )
    plot_evidence = "；".join((combined, plan_transitions))
    missing_plot_duties = [
        duty for duty in plot_duties if not _plan_covers_duty(plot_evidence, duty)
    ]
    if missing_plot_duties:
        critical_missing.append("plot.required_progression")

    setting = _mapping(literary.get("setting"))
    if (setting.get("primary_location") or setting.get("scene_design_goals")) and scenes:
        if not any(str(scene.get("scene_resistance") or "").strip() for scene in scenes):
            warning_missing.append("setting.scene_resistance")
        if not any(str(scene.get("location") or "").strip() for scene in scenes):
            warning_missing.append("setting.location")

    pov = _mapping(literary.get("pov"))
    pov_character = str(pov.get("pov_character") or "").strip()
    if pov_character and scenes:
        scene_povs = {str(scene.get("pov_character") or "").strip() for scene in scenes}
        if pov_character not in scene_povs:
            critical_missing.append("pov.character")

    theme = _mapping(literary.get("theme"))
    theme_duties = _merge_literary_lists(
        theme.get("theme_duties", []),
        [
            item.get("milestone_description", "")
            for item in list(theme.get("arc_milestones") or [])
            if isinstance(item, dict)
        ],
        limit=8,
    )
    if theme_duties and not _contains_any_text(combined, theme_duties):
        warning_missing.append("theme.arc_duty")

    style = _mapping(literary.get("style"))
    character_voices = list(style.get("character_voices") or [])
    if character_voices and scenes:
        has_voice_targets = any(
            isinstance(scene.get("dialogue_voice_targets"), dict)
            and bool(scene.get("dialogue_voice_targets"))
            for scene in scenes
        )
        if not has_voice_targets:
            warning_missing.append("style.dialogue_voice_targets")

    diagnostics = {
        "status": "fail" if critical_missing else ("warn" if warning_missing else "pass"),
        "critical_missing": _dedupe_sorted(critical_missing),
        "warning_missing": _dedupe_sorted(warning_missing),
        "details": "literary_contract_v1 coverage check",
    }
    if missing_plot_duties:
        diagnostics["missing_plot_duties"] = missing_plot_duties[:6]
    return diagnostics


def _dedupe_sorted(values: list[str]) -> list[str]:
    return sorted({item for item in values if item})


@dataclass
class PlanInput:
    """Input for the plan step.

    New chapter generation populates StoryKernel field slices via
    ``ContextComposer.compose_plan_input()`` and the ``from_kernel_context``
    factory method. ``canon_context`` is a prompt projection derived from those
    slices for existing prompt-card builders.
    """

    chapter_outline: ChapterOutline
    canon_context: Any = None
    chapter_state_packet: ChapterStatePacket | None = None
    previous_chapter_ending: str = ""
    previous_creative_report: dict[str, Any] | None = None
    previous_volume_summary: str = ""
    memory_hints: dict[str, Any] | None = None
    pov_hint: str = ""
    style_profile: dict[str, Any] | None = None
    editorial_contract: dict[str, Any] | None = None
    editorial_readiness: dict[str, Any] | None = None
    known_issues_to_avoid: list[dict[str, str]] | None = None
    narrative_contract: dict[str, Any] | None = None
    element_selection: dict[str, Any] | None = None
    element_progress_hint: dict[str, Any] | None = None
    dynamic_element_focus: list[str] | None = None
    time_context: dict[str, str] | None = None
    strand_hint: dict[str, Any] | None = None
    reading_power_hint: dict[str, Any] | None = None
    story_bible: dict[str, Any] | None = None
    chapter_source_slice: Any | None = None
    chapter_position: dict[str, Any] | None = None
    task_type: TaskType = TaskType.PLAN_CHAPTER
    scene_plan_validation_issues: list[dict[str, Any]] | None = None
    world_rule_validation_issues: list[str] | None = None
    semantic_consistency_issues: list[str] | None = None
    replan_context: Any | None = None  # ReplanContext from novel_forge.pipeline.long.replan_context
    theme_arc_context: dict[str, Any] | None = None  # Lightweight theme + arc projection

    # ── StoryKernel field slices (optional, populated via ContextComposer) ──
    character_knowledge_slice: list[dict[str, Any]] | None = None
    relationship_slice: list[dict[str, Any]] | None = None
    object_slice: list[dict[str, Any]] | None = None
    forbidden_contradictions: list[dict[str, Any]] | None = None
    scene_constraints: dict[str, Any] | None = None
    chapter_plan: dict[str, Any] | None = None
    kernel_context: dict[str, Any] | None = None

    @classmethod
    def from_kernel_context(
        cls,
        chapter_outline: ChapterOutline,
        kernel_context: dict[str, Any],
        **kwargs: Any,
    ) -> PlanInput:
        """Create a PlanInput from a ContextComposer output dict.

        Parameters
        ----------
        chapter_outline:
            The chapter outline for this plan.
        kernel_context:
            Output of ``ContextComposer.compose_plan_input()`` — a dict with
            StoryKernel field slices (entities, relationships, timeline, etc.).
        **kwargs:
            Additional PlanInput fields (e.g. ``pov_hint``, ``style_profile``).

        Returns
        -------
        PlanInput
            A PlanInput with kernel slices populated from the context dict.
        """
        entities = kernel_context.get("entities")
        knowledge = kernel_context.get("knowledge_ledger")
        relationships = kernel_context.get("relationships")
        obj_ledger = kernel_context.get("object_ledger")
        world_rules = kernel_context.get("world_rules")
        timeline = kernel_context.get("timeline")
        promises = kernel_context.get("promise_ledger")
        summaries = kernel_context.get("chapter_summaries")
        # Derive the prompt projection from kernel slices for card builders.
        canon = _kernel_slices_to_canon_context(
            entities=entities,
            relationships=relationships,
            knowledge_ledger=knowledge,
            object_ledger=obj_ledger,
            world_rules=world_rules,
            timeline=timeline,
            promise_ledger=promises,
            chapter_summaries=summaries,
        )

        return cls(
            chapter_outline=chapter_outline,
            canon_context=canon if canon else None,
            character_knowledge_slice=knowledge if knowledge else None,
            relationship_slice=relationships if relationships else None,
            object_slice=obj_ledger if obj_ledger else None,
            forbidden_contradictions=world_rules if world_rules else None,
            kernel_context=dict(kernel_context or {}),
            **kwargs,
        )


@dataclass(frozen=True)
class PlanPromptContexts:
    """Prompt-bound planning contexts shared by PlanChapterStep and diagnostics."""

    plan_canon_context: dict[str, Any]
    plan_memory_hints: dict[str, Any]
    focus_characters: list[str]


def build_plan_prompt_contexts(input_data: PlanInput, settings: Any) -> PlanPromptContexts:
    """Build exactly the canon and memory contexts consumed by the Plan prompt."""

    planning_involved_characters = _normalize_required_characters(
        getattr(input_data.chapter_outline, "involved_characters", []) or [],
        fallback_pov=input_data.chapter_outline.pov_character,
    )
    focus_characters: list[str] = []
    if input_data.chapter_outline.pov_character:
        focus_characters.append(input_data.chapter_outline.pov_character)
    focus_characters.extend(planning_involved_characters)
    if input_data.chapter_state_packet is not None:
        focus_characters.extend(input_data.chapter_state_packet.known_characters or [])
        for rel in input_data.chapter_state_packet.active_relationships or []:
            focus_characters.extend(getattr(rel, "characters", []) or [])
    plan_canon_context = trim_plan_canon_context(
        input_data.canon_context,
        focus_characters=focus_characters,
        max_characters=10,
        max_recent_events=12,
        max_foreshadowing=max(1, int(settings.long_plan_max_foreshadowing or 1)),
    )
    plan_memory_hints = trim_plan_memory_hints(input_data.memory_hints or {})
    return PlanPromptContexts(
        plan_canon_context=plan_canon_context,
        plan_memory_hints=plan_memory_hints,
        focus_characters=focus_characters,
    )


class PlanChapterStep(PipelineStep[PlanInput, ChapterPlan]):
    """ChapterStatePacket + outline → model → structured ChapterPlan."""

    @property
    def step_name(self) -> str:
        return "plan_chapter"

    @staticmethod
    def _compute_plan_max_tokens(expected_words: int) -> int:
        words = max(800, int(expected_words or 0))
        # A ChapterPlan is an execution contract rather than a short synopsis.
        # Production traces for a 3,500-word chapter required 13.2k output
        # tokens; the former 5.1k floor guaranteed one or two truncated calls.
        estimated_tokens = int(6144 + (words / 1000 * 1024))
        if words >= 4000:
            floor = 16384
        elif words >= 3000:
            floor = 14336
        else:
            floor = 10240
        return max(floor, estimated_tokens)

    @staticmethod
    def _normalize_beats(*args: Any, **kwargs: Any) -> Any:
        return _normalize_beats(*args, **kwargs)

    @staticmethod
    def _compact_contract_text(*args: Any, **kwargs: Any) -> Any:
        return _compact_contract_text(*args, **kwargs)

    @staticmethod
    def _normalize_plan_payload(*args: Any, **kwargs: Any) -> Any:
        return _normalize_plan_payload(*args, **kwargs)

    @staticmethod
    def _trim_plan_canon_context(*args: Any, **kwargs: Any) -> Any:
        return trim_plan_canon_context(*args, **kwargs)

    @staticmethod
    def _trim_plan_memory_hints(*args: Any, **kwargs: Any) -> Any:
        return trim_plan_memory_hints(*args, **kwargs)

    @staticmethod
    def _build_character_identity_cards(*args: Any, **kwargs: Any) -> Any:
        return build_character_identity_cards(*args, **kwargs)

    @staticmethod
    def _plan_retry_escalation_update(
        exc: Exception,
        response: Any | None,
        request: Any,
        *,
        route_max_tokens: int | None = None,
    ) -> dict[str, Any] | None:
        max_allowed = max(int(route_max_tokens or request.max_tokens), int(request.max_tokens))
        if request.max_tokens >= max_allowed:
            return None

        missing_required_keys = isinstance(exc, KeyError)
        parse_failed = isinstance(exc, (json.JSONDecodeError, TypeError, ValidationError))
        if missing_required_keys:
            if response is None or not response_is_token_capped(response, request.max_tokens):
                return None
        elif not parse_failed:
            return None

        requested = int(request.max_tokens)
        escalated_floor = min(
            max_allowed,
            max(1024, requested + max(1024, requested // 2)),
        )
        escalated_max = min(max_allowed, max(requested * 2, escalated_floor))
        _log.warning(
            "plan_chapter escalating max_tokens and retrying | old=%s | new=%s | reason=%s",
            request.max_tokens,
            escalated_max,
            type(exc).__name__,
        )
        return {"temperature": 0.0, "max_tokens": escalated_max}

    async def _execute(self, input_data: PlanInput) -> ChapterPlan:
        settings = self.settings
        task_type = (
            input_data.task_type
            if input_data.task_type in {TaskType.PLAN_CHAPTER, TaskType.PLAN_CHAPTER_SCENES}
            else TaskType.PLAN_CHAPTER
        )
        expected_words = getattr(input_data.chapter_outline, "expected_word_count", 3000)
        min_tokens = self._compute_plan_max_tokens(expected_words)
        max_tokens = self._dynamic_max_tokens(
            task_type,
            max(2400, int(expected_words * 1.1)),
            prompt_overhead=3500,
            safety_margin=0.85,
            min_tokens=min_tokens,
        )
        route_max_tokens = route_output_limit(self._router, task_type)

        plan_prompt_contexts = build_plan_prompt_contexts(input_data, settings)
        from novel_forge.pipeline.long.services.constraints.constraint_router import (
            build_plan_cards,
        )

        stage_cards = build_plan_cards(
            packet=input_data.chapter_state_packet,
            chapter_outline=input_data.chapter_outline,
            canon_context=plan_prompt_contexts.plan_canon_context,
            memory_hints=plan_prompt_contexts.plan_memory_hints,
            style_profile=input_data.style_profile,
            editorial_contract=input_data.editorial_contract,
            editorial_readiness=input_data.editorial_readiness,
            narrative_contract=input_data.narrative_contract,
            reading_power_hint=input_data.reading_power_hint or {},
            story_bible=input_data.story_bible or {},
            element_selection=input_data.element_selection,
            element_progress_hint=input_data.element_progress_hint,
            element_focus=input_data.dynamic_element_focus,
            known_issues_to_avoid=input_data.known_issues_to_avoid,
            pov_hint=input_data.pov_hint,
            time_context=input_data.time_context,
            strand_hint=input_data.strand_hint,
            kernel_context=input_data.kernel_context or {},
            chapter_source_slice=input_data.chapter_source_slice,
            chapter_position=input_data.chapter_position,
            settings=settings,
        )

        context = {
            # Plan templates are card-first. chapter_outline stays as the
            # only direct fallback because format contracts and templates
            # still read its scalar chapter metadata.
            "chapter_outline": input_data.chapter_outline,
            "stage_cards": stage_cards,
            "chapter_position": input_data.chapter_position or {},
            "is_last_chapter": bool((input_data.chapter_position or {}).get("is_last_chapter")),
            "total_chapters": int((input_data.chapter_position or {}).get("total_chapters") or 0),
            "scene_plan_validation_issues": input_data.scene_plan_validation_issues or [],
            "world_rule_validation_issues": input_data.world_rule_validation_issues or [],
            "semantic_consistency_issues": input_data.semantic_consistency_issues or [],
            "replan_context": getattr(input_data, "replan_context", None),
            "theme_arc_context": input_data.theme_arc_context,
        }
        from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h

        _sensory_notes_max_items = max(
            1, min(2, int(getattr(self.settings, "long_plan_sensory_notes_max_items", 2) or 2))
        )

        def _normalize_and_condition_plan(data: dict[str, Any]) -> dict[str, Any]:
            from novel_forge.pipeline.long.services.constraints.world_rule_plan_contract import (
                materialize_plan_world_rule_bindings,
            )
            from novel_forge.pipeline.steps.planning.scene_contract import (
                positional_scene_id_map,
                remap_plan_scene_references,
                scene_limit_id_map,
            )

            normalized = _normalize_plan_payload(
                data,
                outline=input_data.chapter_outline,
                packet=input_data.chapter_state_packet,
                style_profile=input_data.style_profile,
                memory_hints=input_data.memory_hints,
                min_beats=settings.long_plan_beats_min,
                max_beats=settings.long_plan_beats_max,
                beat_max_chars=settings.long_plan_beat_max_chars,
                forbidden_hard_max_items=getattr(settings, "forbidden_elements_hard_max_items", 8),
                forbidden_soft_max_items=getattr(settings, "forbidden_elements_soft_max_items", 12),
                forbidden_quota_max_items=getattr(
                    settings, "forbidden_elements_quota_max_items", 6
                ),
                forbidden_sources_max_items=getattr(
                    settings,
                    "forbidden_element_sources_max_items",
                    24,
                ),
                rank_forbidden_by_relevance=getattr(
                    settings,
                    "forbidden_elements_rank_by_relevance",
                    True,
                ),
                sensory_notes_max_items=_sensory_notes_max_items,
            )
            _configured_scene_switches = max(
                2, int(getattr(self.settings, "long_plan_max_scene_switches", 8) or 8)
            )
            outline_beats = input_data.chapter_outline.beats_summary or []
            # Outline beats can raise the scene floor up to the configured max,
            # but never above it — prevents model-generated beat inflation from
            # overriding the user's hard cap (see issue: ch10 alignment failure).
            _outline_scene_floor = (
                min(len(outline_beats), _configured_scene_switches) if outline_beats else 0
            )
            _max_scene_switches = max(_configured_scene_switches, _outline_scene_floor)
            scenes = normalized.get("scene_intents", [])
            scenes_recondition_needed = False
            if outline_beats and len(scenes) < len(outline_beats):
                original_count = len(scenes)
                before_ids = [str(scene.get("scene_id") or "") for scene in scenes]
                scenes = _patch_missing_beat_scenes(
                    scenes,
                    outline_beats,
                )
                normalized["scene_intents"] = scenes
                remap_plan_scene_references(
                    normalized,
                    positional_scene_id_map(
                        before_ids,
                        [str(scene.get("scene_id") or "") for scene in scenes],
                    ),
                )
                scenes_recondition_needed = True
                _log.warning(
                    "plan_chapter beat gap merged | chapter=%s | beats=%d | original_scenes=%d | result_scenes=%d",
                    getattr(input_data.chapter_outline, "chapter_number", "?"),
                    len(outline_beats),
                    original_count,
                    len(scenes),
                )

            _max_key_revelations = resolve_revelation_budget(
                input_data.narrative_contract,
                fallback=getattr(self.settings, "long_plan_max_key_revelations_per_chapter", 2),
            )
            _reveals_trimmed = enforce_revelation_budget(
                normalized, max_revelations=_max_key_revelations
            )
            _min_unresolved = resolve_min_unresolved_threads(
                input_data.narrative_contract, fallback=0
            )
            _retention_added = enforce_unresolved_retention(
                normalized, packet=input_data.chapter_state_packet, min_keep=_min_unresolved
            )
            if _reveals_trimmed or _retention_added:
                _log.info(
                    "plan_revelation_budget_enforced | chapter=%s | max_revelations=%d | trimmed=%s | min_unresolved=%d | retention_added=%s",
                    getattr(input_data.chapter_outline, "chapter_number", "?"),
                    _max_key_revelations,
                    _reveals_trimmed,
                    _min_unresolved,
                    _retention_added,
                )

            _scene_count_before_trim = len(normalized.get("scene_intents", []))
            before_trim_ids = [
                str(scene.get("scene_id") or "")
                for scene in normalized.get("scene_intents", [])
                if isinstance(scene, dict)
            ]
            _scenes_trimmed = enforce_scene_switch_limit(
                normalized,
                max_scenes=_max_scene_switches,
                sensory_notes_max_items=_sensory_notes_max_items,
            )
            if _scenes_trimmed:
                remap_plan_scene_references(
                    normalized,
                    scene_limit_id_map(before_trim_ids, max_scenes=_max_scene_switches),
                )
                scenes_recondition_needed = True
                _log.warning(
                    "plan_scene_switch_limit_enforced | chapter=%s | max_scenes=%d | original_count=%d",
                    getattr(input_data.chapter_outline, "chapter_number", "?"),
                    _max_scene_switches,
                    _scene_count_before_trim,
                )
            if scenes_recondition_needed:
                normalized["scene_intents"] = _enrich_scene_intents(
                    normalized.get("scene_intents", []),
                    outline=input_data.chapter_outline,
                    packet=input_data.chapter_state_packet,
                    opening_contract=str(normalized.get("opening_contract", "")),
                    closing_contract=str(normalized.get("closing_contract", "")),
                    forbidden_elements=list(normalized.get("forbidden_elements", []) or []),
                    sensory_notes_max_items=_sensory_notes_max_items,
                )
                normalized["scene_intents"] = _filter_scene_cast_and_emotion(
                    normalized.get("scene_intents", []),
                    outline=input_data.chapter_outline,
                )
                _normalize_scene_target_words(
                    normalized["scene_intents"],
                    total_target=max(
                        0, int(getattr(input_data.chapter_outline, "expected_word_count", 0) or 0)
                    ),
                )
            _apply_pov_knowledge_constraints(
                normalized,
                outline=input_data.chapter_outline,
                character_knowledge_slice=input_data.character_knowledge_slice,
                kernel_context=input_data.kernel_context,
            )
            return materialize_plan_world_rule_bindings(normalized)

        async def _route_and_normalize_plan(
            prompt_context: dict[str, Any],
            *,
            retry_label: str = "",
        ) -> dict[str, Any]:
            request = self._builder.build(
                task_type,
                prompt_context,
                max_tokens=max_tokens,
                temperature=settings.temp_plan_chapter,
            )
            log_prompt_diagnostics(
                _log,
                event="plan_prompt_diagnostics_retry" if retry_label else "plan_prompt_diagnostics",
                request=request,
                context=prompt_context,
                settings=settings,
                enabled_attr="long_prompt_diagnostics_enabled",
                warn_attr="long_prompt_warn_tokens",
                chapter=getattr(input_data.chapter_outline, "chapter_number", "?"),
                on_event=self._on_step_event,
            )
            data = await llm_h.route_json_object_with_retry(
                self._router,
                request,
                task_type=task_type,
                context=prompt_context,
                on_step=self._on_step_event,
                observe_stream=bool(
                    getattr(settings, "long_streaming_json_observation_enabled", True)
                ),
                chapter=getattr(input_data.chapter_outline, "chapter_number", "?"),
                escalation_request_update=(
                    lambda exc, response, retry_request: self._plan_retry_escalation_update(
                        exc,
                        response,
                        retry_request,
                        route_max_tokens=route_max_tokens,
                    )
                ),
            )
            if task_type == TaskType.PLAN_CHAPTER_SCENES and isinstance(
                data.get("scene_plan"), dict
            ):
                data = data["scene_plan"]
            return _normalize_and_condition_plan(data)

        normalized = await _route_and_normalize_plan(context)
        coverage = validate_literary_contract_plan_coverage(normalized, stage_cards)
        critical_missing = list(coverage.get("critical_missing") or [])
        if critical_missing and bool(
            getattr(settings, "long_literary_contract_plan_retry_enabled", True)
        ):
            retry_context = dict(context)
            retry_context["literary_contract_retry_guidance"] = {
                "status": coverage.get("status", "fail"),
                "critical_missing": critical_missing,
                "warning_missing": list(coverage.get("warning_missing") or []),
                "missing_plot_duties": list(coverage.get("missing_plot_duties") or [])[:6],
                "details": coverage.get("details", ""),
            }
            retry_normalized = await _route_and_normalize_plan(
                retry_context,
                retry_label="literary_contract_coverage",
            )
            retry_coverage = validate_literary_contract_plan_coverage(
                retry_normalized,
                stage_cards,
            )
            if len(list(retry_coverage.get("critical_missing") or [])) < len(critical_missing):
                normalized = retry_normalized
                coverage = retry_coverage
            _log.info(
                "plan_literary_contract_retry | chapter=%s | before=%s | after=%s",
                getattr(input_data.chapter_outline, "chapter_number", "?"),
                critical_missing,
                list(coverage.get("critical_missing") or []),
            )

        if coverage.get("status") in {"fail", "warn"}:
            log_fn = _log.warning if coverage.get("status") == "fail" else _log.info
            log_fn(
                "plan_literary_contract_coverage | chapter=%s | status=%s | critical=%s | warnings=%s",
                getattr(input_data.chapter_outline, "chapter_number", "?"),
                coverage.get("status"),
                list(coverage.get("critical_missing") or []),
                list(coverage.get("warning_missing") or []),
            )
        if coverage.get("status") == "fail":
            missing_duties = list(coverage.get("missing_plot_duties") or [])
            violations = missing_duties or list(coverage.get("critical_missing") or [])
            raise ConsistencyViolationError(
                [f"文学合同未被章节计划覆盖：{item}" for item in violations[:6]],
                violation_kind="plan_fixable",
                failed_stage="plan",
                replan_target=RecoveryTarget.PLAN,
            )
        return ChapterPlan.model_validate(normalized)


def _upgrade_recurring_forbidden_items(
    records: list[dict[str, Any]],
    *,
    bridge_raw: list[Any],
    accumulated_raw: list[Any],
    chapter_number: int,
) -> list[dict[str, Any]]:
    """Upgrade soft→hard for forbidden items that recur across consecutive chapters.

    When the same forbidden-repetition item appears in both the accumulated
    index (flagged in previous chapter(s)) AND the current chapter's bridge
    output, it has been flagged for 2+ consecutive chapters — escalate from
    advisory (soft) to enforceable (hard, confidence=1.0).
    """
    bridge_texts: set[str] = set()
    for item in bridge_raw:
        text = normalize_forbidden_source_item(item, source="bridge")
        if text:
            bridge_texts.add(forbidden_base_text(text))

    accumulated_texts: set[str] = set()
    for item in accumulated_raw:
        text = normalize_forbidden_source_item(item, source="accumulated")
        if text:
            accumulated_texts.add(forbidden_base_text(text))

    recurring = bridge_texts & accumulated_texts
    if not recurring:
        return records

    upgraded: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)  # copy to avoid mutating shared references
        rec_text = forbidden_base_text(str(item.get("text", "")))
        if (
            rec_text in recurring
            and item.get("level") in {"soft"}
            and int(item.get("cooldown_chapters", 0)) > 0
        ):
            _log.info(
                "upgrade_recurring_forbidden | item=%s | source=%s | level=soft→hard | chapter=%d",
                rec_text,
                item.get("source", "?"),
                chapter_number,
            )
            item["level"] = "hard"
            item["confidence"] = 1.0
        upgraded.append(item)
    return upgraded


def _normalize_plan_payload(
    raw: Any,
    *,
    outline: ChapterOutline,
    packet: ChapterStatePacket | None,
    style_profile: dict[str, Any] | None,
    memory_hints: dict[str, Any] | None = None,
    min_beats: int = 4,
    max_beats: int = 8,
    beat_max_chars: int = 220,
    forbidden_hard_max_items: int = 8,
    forbidden_soft_max_items: int = 12,
    forbidden_quota_max_items: int = 6,
    forbidden_sources_max_items: int = 24,
    rank_forbidden_by_relevance: bool = True,
    sensory_notes_max_items: int = 2,
) -> dict[str, Any]:
    from novel_forge.pipeline.steps.planning.scene_contract import (
        canonicalize_cross_scene_intent,
        canonicalize_scene_intent_inputs,
        restore_scene_execution_fields,
    )

    from .hints import (
        _compact_contract_text,
        _derive_automatic_callbacks,
    )

    payload = raw if isinstance(raw, dict) else {}
    canonical_scene_inputs = canonicalize_scene_intent_inputs(
        payload.get("scene_intents", payload.get("beats", []))
    )
    scenes = _normalize_scene_intents(
        canonical_scene_inputs,
        outline=outline,
        min_beats=min_beats,
        max_beats=max_beats,
        beat_max_chars=beat_max_chars,
        sensory_notes_max_items=sensory_notes_max_items,
    )
    scenes = restore_scene_execution_fields(scenes, canonical_scene_inputs)
    opening_contract = _compact_contract_text(payload.get("opening_contract"), max_chars=120)
    if not opening_contract:
        bridge_summary = packet.bridge.bridge_summary if packet and packet.bridge else ""
        opening_contract = bridge_summary or "承接上一章结尾，明确时间、地点、情绪和行动接力。"
    closing_contract = _compact_contract_text(payload.get("closing_contract"), max_chars=140)
    if not closing_contract:
        closing_contract = "章末必须留下可直接承接的行动、时间、地点或目标。"
    intentional_callbacks = _normalize_string_list(
        payload.get("intentional_callbacks", []), max_chars=120
    )
    bible_anchor_terms: list[str] = []
    if packet is not None:
        from novel_forge.pipeline.long.services.anchor_terms import _extract_anchor_terms_from_bible

        bible_anchor_terms = _extract_anchor_terms_from_bible(
            {"characters": list(getattr(packet, "character_profiles", []) or [])},
            getattr(packet, "canon_context", None),
        )
    motif_context: dict[str, Any] | None = None
    if memory_hints and isinstance(memory_hints, dict):
        mc = memory_hints.get("motif_continuity")
        if isinstance(mc, dict) and mc:
            motif_context = mc
    motif_forbidden = []
    if motif_context and isinstance(motif_context.get("forbidden_repetition"), list):
        motif_forbidden = motif_context.get("forbidden_repetition", [])

    style_banned = (
        (style_profile.get("global_style") or {}).get("banned_phrases", [])
        if style_profile and style_profile.get("global_style")
        else []
    )
    bridge_forbidden = (
        packet.bridge.forbidden_repetition
        if packet is not None and packet.bridge is not None
        else []
    )
    accumulated_forbidden = packet.accumulated_forbidden_repetition if packet is not None else []

    forbidden_source_records: list[dict[str, Any]] = [
        *build_forbidden_source_records(
            payload.get("forbidden_elements", []),
            source="plan",
            level="hard",
            reason="plan_chapter forbidden_elements",
            confidence=0.70,
        ),
        *build_forbidden_source_records(
            style_banned,
            source="style",
            level="hard",
            reason="style_profile banned_phrases",
            confidence=1.0,
        ),
        *build_forbidden_source_records(
            bridge_forbidden,
            source="bridge",
            level="soft",
            reason="bridge forbidden_repetition",
            confidence=0.55,
        ),
        *build_forbidden_source_records(
            payload.get("forbidden_elements_soft", []),
            source="plan",
            level="soft",
            reason="plan_chapter forbidden_elements_soft",
            confidence=0.60,
        ),
        *build_forbidden_source_records(
            accumulated_forbidden,
            source="accumulated",
            level="soft",
            reason="recent cross-chapter repetition",
            confidence=0.45,
        ),
        *build_forbidden_source_records(
            motif_forbidden,
            source="motif",
            level="soft",
            reason="memory motif_continuity forbidden_repetition",
            confidence=0.80,
        ),
        *build_forbidden_source_records(
            payload.get("forbidden_elements_quota", []),
            source="plan",
            level="quota",
            reason="plan_chapter forbidden_elements_quota",
            confidence=0.65,
        ),
    ]
    forbidden_source_records = enrich_forbidden_records_with_motif_context(
        forbidden_source_records,
        motif_context,
    )

    # Upgrade soft→hard for items that recur in consecutive chapters' bridge data
    forbidden_source_records = _upgrade_recurring_forbidden_items(
        forbidden_source_records,
        bridge_raw=bridge_forbidden,
        accumulated_raw=accumulated_forbidden,
        chapter_number=getattr(outline, "chapter_number", 0),
    )

    hard_forbidden = _normalize_string_list(
        [record["text"] for record in forbidden_source_records if record.get("level") == "hard"],
        max_chars=120,
    )
    hard_forbidden = list(dict.fromkeys(hard_forbidden))
    hard_forbidden = _filter_abstract_emotion_labels(
        hard_forbidden,
        packet=packet,
        extra_known_terms=bible_anchor_terms,
        motif_context=motif_context,
    )
    soft_forbidden = _normalize_string_list(
        [record["text"] for record in forbidden_source_records if record.get("level") == "soft"],
        max_chars=120,
    )
    quota_forbidden = _normalize_string_list(
        [record["text"] for record in forbidden_source_records if record.get("level") == "quota"],
        max_chars=120,
    )
    soft_forbidden = _filter_abstract_emotion_labels(
        soft_forbidden,
        packet=packet,
        extra_known_terms=bible_anchor_terms,
        motif_context=motif_context,
    )
    quota_forbidden = _filter_abstract_emotion_labels(
        quota_forbidden,
        packet=packet,
        extra_known_terms=bible_anchor_terms,
        motif_context=motif_context,
    )
    relationship_evolution = _normalize_string_list(
        payload.get("relationship_evolution", []), max_chars=220
    )
    if not relationship_evolution:
        relationship_evolution = _fallback_relationship_evolution(packet)
    if not relationship_evolution:
        relationship_evolution = _fallback_relationship_evolution_from_outline(outline)
    model_state_transitions = _normalize_string_list(
        payload.get("required_state_transitions", []),
        max_chars=180,
    )
    required_literals = _normalize_required_literals(payload.get("required_literals", []))
    # ``must_carry_forward`` is previous-exit context, not proof that the same
    # action must happen again in this chapter.  It stays authoritative in the
    # bridge/continuity/finalize lanes, while only transitions explicitly
    # selected by the current Plan become executable scene state changes.
    # Unconditionally merging the two namespaces used to turn completed facts
    # (for example, "A used medicine to stabilize B") into instructions to
    # repeat that action in every following chapter.
    required_state_transitions = list(dict.fromkeys(model_state_transitions))
    foreshadowing_plan = _normalize_string_list(
        payload.get("foreshadowing_plan", payload.get("foreshadowing_to_plant", [])),
        max_chars=160,
    )[:2]
    key_revelations = _normalize_string_list(payload.get("key_revelations", []), max_chars=160)
    auto_callbacks = _derive_automatic_callbacks(
        forbidden_elements=list(dict.fromkeys(hard_forbidden + soft_forbidden + quota_forbidden)),
        opening_contract=opening_contract,
        closing_contract=closing_contract,
        scene_intents=scenes,
        foreshadowing_plan=foreshadowing_plan,
        key_revelations=key_revelations,
        relationship_evolution=relationship_evolution,
        required_state_transitions=required_state_transitions,
    )
    intentional_callbacks = _normalize_string_list(
        intentional_callbacks + auto_callbacks, max_chars=120
    )
    callback_set = set(intentional_callbacks)
    protected_texts = _protected_plan_texts(
        opening_contract=opening_contract,
        closing_contract=closing_contract,
        scenes=scenes,
        foreshadowing_plan=foreshadowing_plan,
        key_revelations=key_revelations,
        relationship_evolution=relationship_evolution,
        required_state_transitions=required_state_transitions,
        intentional_callbacks=intentional_callbacks,
    )
    hard_forbidden, soft_forbidden, quota_forbidden = _split_forbidden_constraints(
        hard_forbidden=hard_forbidden,
        soft_forbidden=soft_forbidden,
        quota_forbidden=quota_forbidden,
        protected_texts=protected_texts,
    )
    hard_forbidden = [i for i in hard_forbidden if i not in callback_set]
    hard_set = set(hard_forbidden)
    soft_forbidden = [i for i in soft_forbidden if i not in hard_set and i not in callback_set]
    quota_forbidden = [
        i
        for i in quota_forbidden
        if i not in hard_set and i not in set(soft_forbidden) and i not in callback_set
    ]
    relevance_texts = _forbidden_relevance_texts(
        outline=outline,
        packet=packet,
        opening_contract=opening_contract,
        closing_contract=closing_contract,
        scenes=scenes,
        foreshadowing_plan=foreshadowing_plan,
        key_revelations=key_revelations,
        relationship_evolution=relationship_evolution,
        required_state_transitions=required_state_transitions,
        intentional_callbacks=intentional_callbacks,
    )
    hard_forbidden = rank_forbidden_terms(
        hard_forbidden,
        source_records=forbidden_source_records,
        relevance_texts=relevance_texts,
        max_items=max(0, int(forbidden_hard_max_items or 0)),
        enabled=rank_forbidden_by_relevance,
        level="hard",
    )
    soft_forbidden = rank_forbidden_terms(
        soft_forbidden,
        source_records=forbidden_source_records,
        relevance_texts=relevance_texts,
        max_items=max(0, int(forbidden_soft_max_items or 0)),
        enabled=rank_forbidden_by_relevance,
        level="soft",
    )
    quota_forbidden = rank_forbidden_terms(
        quota_forbidden,
        source_records=forbidden_source_records,
        relevance_texts=relevance_texts,
        max_items=max(0, int(forbidden_quota_max_items or 0)),
        enabled=rank_forbidden_by_relevance,
        level="quota",
    )
    scenes = _enrich_scene_intents(
        scenes,
        outline=outline,
        packet=packet,
        opening_contract=opening_contract,
        closing_contract=closing_contract,
        forbidden_elements=hard_forbidden,
        sensory_notes_max_items=sensory_notes_max_items,
    )
    scenes = _filter_scene_cast_and_emotion(scenes, outline=outline)
    scenes = assign_required_state_transitions_to_scenes(scenes, required_state_transitions)
    _normalize_scene_target_words(
        scenes, total_target=max(0, int(getattr(outline, "expected_word_count", 0) or 0))
    )
    forbidden_element_sources = project_forbidden_source_records(
        hard=hard_forbidden,
        soft=soft_forbidden,
        quota=quota_forbidden,
        source_records=forbidden_source_records,
        relevance_texts=relevance_texts,
    )[: max(0, int(forbidden_sources_max_items or 0))]
    expression_channel_records = [
        {
            "text": str(item.get("text", "") or ""),
            "channel": str(item.get("channel", "other") or "other"),
            "channel_id": str(item.get("channel_id", "") or ""),
            "source": str(item.get("source", "") or ""),
            "level": str(item.get("level", "soft") or "soft"),
            "cooldown_chapters": int(item.get("cooldown_chapters", 3) or 3),
            "actor_scope": str(item.get("actor_scope", "global") or "global"),
            "reason": str(item.get("reason", "") or ""),
            "examples": list(item.get("examples", []) or []),
            "surface_forms": list(item.get("surface_forms", []) or []),
            "trigger_contexts": list(item.get("trigger_contexts", []) or []),
            "replacement_axes": list(item.get("replacement_axes", []) or []),
            "allowed_when": str(item.get("allowed_when", "") or ""),
            "confidence": _safe_float(item.get("confidence", 0.0)),
            "provenance": str(item.get("provenance", "") or ""),
            "recent_semantic_hits": list(item.get("recent_semantic_hits", []) or [])[:2],
            "semantic_hit_count": int(item.get("semantic_hit_count", 0) or 0),
            "last_seen_chapter": int(item.get("last_seen_chapter", 0) or 0),
            "semantic_similarity_max": _safe_float(item.get("semantic_similarity_max", 0.0)),
        }
        for item in forbidden_element_sources
        if str(item.get("channel", "other") or "other") != "other"
    ]
    if memory_hints and isinstance(memory_hints, dict):
        for item in list(memory_hints.get("expression_channel_records", []) or []):
            if not isinstance(item, dict):
                continue
            expression_channel_records.append(
                {
                    "text": str(item.get("text", "") or ""),
                    "channel": str(item.get("channel", "other") or "other"),
                    "channel_id": str(item.get("channel_id", "") or ""),
                    "source": str(item.get("source", "memory") or "memory"),
                    "level": str(item.get("level", "soft") or "soft"),
                    "cooldown_chapters": int(item.get("cooldown_chapters", 3) or 3),
                    "actor_scope": str(item.get("actor_scope", "global") or "global"),
                    "reason": str(item.get("reason", "") or ""),
                    "examples": list(item.get("examples", []) or []),
                    "surface_forms": list(item.get("surface_forms", []) or []),
                    "trigger_contexts": list(item.get("trigger_contexts", []) or []),
                    "replacement_axes": list(item.get("replacement_axes", []) or []),
                    "allowed_when": str(item.get("allowed_when", "") or ""),
                    "confidence": _safe_float(item.get("confidence", 0.0)),
                    "provenance": str(item.get("provenance", "") or ""),
                    "recent_semantic_hits": list(item.get("recent_semantic_hits", []) or [])[:2],
                    "semantic_hit_count": int(item.get("semantic_hit_count", 0) or 0),
                    "last_seen_chapter": int(item.get("last_seen_chapter", 0) or 0),
                    "semantic_similarity_max": _safe_float(
                        item.get("semantic_similarity_max", 0.0)
                    ),
                }
            )
    expression_channel_records = _dedupe_expression_channel_records(expression_channel_records)
    raw_world_rule_applications = payload.get("world_rule_applications", [])
    if isinstance(raw_world_rule_applications, dict):
        raw_world_rule_applications = [raw_world_rule_applications]
    world_rule_applications = (
        [item for item in raw_world_rule_applications if isinstance(item, dict)]
        if isinstance(raw_world_rule_applications, list)
        else []
    )
    plan_payload: dict[str, Any] = {
        "scene_intents": scenes,
        "world_rule_applications": world_rule_applications,
        "chapter_type": _normalize_chapter_type(payload.get("chapter_type", "crisis")),
        "opening_contract": opening_contract,
        "closing_contract": closing_contract,
        "required_state_transitions": required_state_transitions,
        "required_literals": required_literals,
        "emotional_arc": sanitize_story_text(clean_str(payload.get("emotional_arc")))
        or _fallback_emotional_arc_from_outline(outline),
        "foreshadowing_plan": foreshadowing_plan,
        "key_revelations": key_revelations,
        "relationship_evolution": relationship_evolution,
        "forbidden_elements": hard_forbidden,
        "forbidden_elements_soft": soft_forbidden,
        "forbidden_elements_quota": quota_forbidden,
        "forbidden_element_sources": forbidden_element_sources,
        "expression_channel_records": expression_channel_records,
        "intentional_callbacks": intentional_callbacks,
        "cross_scene_intent": _normalize_cross_scene_intent(
            canonicalize_cross_scene_intent(payload.get("cross_scene_intent")),
            scenes=scenes,
        ),
    }
    _enforce_opening_handoff_constraints(plan_payload=plan_payload, packet=packet)
    return plan_payload


def _normalize_required_literals(value: Any) -> list[dict[str, Any]]:
    """Normalize Planning-model declarations without inferring any new literals."""
    if not isinstance(value, list):
        return []
    from novel_forge.core.guidance import LiteralRequirement

    normalized: list[dict[str, Any]] = []
    seen_literals: set[str] = set()
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        contract_id = clean_str(item.get("contract_id"))
        literal = clean_str(item.get("literal"))
        scene_id = clean_str(item.get("scene_id"))
        reason = clean_str(item.get("reason"))
        placement_hint = clean_str(item.get("placement_hint"))
        if (
            not all((contract_id, literal, scene_id, reason, placement_hint))
            or contract_id in seen_ids
            or literal in seen_literals
        ):
            continue
        seen_ids.add(contract_id)
        seen_literals.add(literal)
        normalized.append(
            {
                "contract_id": contract_id,
                "literal": literal,
                "scene_id": scene_id,
                "reason": reason,
                "placement_hint": placement_hint,
            }
        )
        if item.get("requirement"):
            # Planning declares obligations, never fulfillment in unwritten prose.
            normalized[-1]["requirement"] = (
                LiteralRequirement.model_validate(item["requirement"])
                .model_copy(update={"status": "pending", "evidence": "", "source_text_hash": ""})
                .model_dump(mode="json")
            )
    return normalized
