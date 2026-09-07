"""Compatibility and identity contract for scene-level chapter planning."""

from __future__ import annotations

from typing import Any

from novel_forge.core.utils.type_coerce import coerce_text_list, stringify_text_value


def _first(raw: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _characters(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result: list[Any] = []
    for key in ("pov", "lead", "protagonist"):
        if value.get(key):
            result.append(value[key])
    for key in ("active", "supporting", "others", "participants"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            result.extend(candidate)
        elif candidate:
            result.append(candidate)
    return result


def _motivations(value: Any) -> Any:
    if isinstance(value, dict):
        return [
            {"character": character, "motivation": motivation, "stake": ""}
            for character, motivation in value.items()
        ]
    return value


def canonicalize_scene_intent_inputs(value: Any) -> Any:
    """Translate known provider aliases before the legacy normalizer filters fields."""

    if not isinstance(value, list):
        return value
    result: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            result.append(item)
            continue
        raw = dict(item)
        raw["summary"] = _first(raw, "summary", "overview", "title", default="")
        raw["required_characters"] = _characters(
            _first(raw, "required_characters", "characters_involved", "characters", default=[])
        )
        raw["character_motivations"] = _motivations(
            _first(raw, "character_motivations", "motivations", default=[])
        )
        raw["entry_state"] = _first(raw, "entry_state", "entry", default="")
        raw["exit_state"] = _first(raw, "exit_state", "exit", default="")
        raw["exit_target_state"] = _first(raw, "exit_target_state", "exit", default="")
        raw["time_marker"] = _first(raw, "time_marker", "time", default="")
        raw["target_words"] = _first(raw, "target_words", "word_count_budget", default=0)
        raw["emotional_beat"] = _first(raw, "emotional_beat", "emotional_beats", default="")
        raw["sensory_notes"] = _first(raw, "sensory_notes", "sensory_anchors", default="")
        raw["scene_resistance"] = _first(raw, "scene_resistance", "resistance", default="")
        raw["dialogue_voice_targets"] = _first(
            raw, "dialogue_voice_targets", "dialogue_voice", default={}
        )
        body_budget = _first(raw, "body_signal_budget", "body_language_budget", default=1)
        raw["body_signal_budget"] = (
            len(body_budget) if isinstance(body_budget, list) else body_budget
        )
        result.append(raw)
    return result


def canonicalize_cross_scene_intent(value: Any) -> Any:
    """Normalize the provider's source/target/type aliases for WAVE."""

    from novel_forge.core.guidance import RequirementSemantics

    if not isinstance(value, dict):
        return value
    result = dict(value)
    references: list[dict[str, Any]] = []
    for item in list(value.get("cross_scene_references") or []):
        if not isinstance(item, dict):
            continue
        ref_type = str(item.get("ref_type") or item.get("type") or "callback").strip()
        if ref_type not in {"callback", "foreshadow", "parallel", "contrast", "echo"}:
            ref_type = "callback"
        references.append(
            {
                "from_scene": item.get("from_scene") or item.get("source_scene_id") or "",
                "to_scene": item.get("to_scene") or item.get("target_scene_id") or "",
                "ref_type": ref_type,
                "description": item.get("description") or "",
            }
        )
        if item.get("requirement"):
            references[-1]["requirement"] = (
                RequirementSemantics.model_validate(item["requirement"])
                .model_copy(update={"status": "pending", "evidence": "", "source_text_hash": ""})
                .model_dump(mode="json")
            )
    result["cross_scene_references"] = references
    return result


def restore_scene_execution_fields(
    scenes: list[dict[str, Any]], canonical_inputs: Any
) -> list[dict[str, Any]]:
    """Restore canonical fields omitted by the legacy manual whitelist."""

    raw_scenes = canonical_inputs if isinstance(canonical_inputs, list) else []
    by_id = {
        str(item.get("scene_id") or "").strip(): item
        for item in raw_scenes
        if isinstance(item, dict) and str(item.get("scene_id") or "").strip()
    }
    for index, scene in enumerate(scenes):
        raw = by_id.get(str(scene.get("scene_id") or "").strip())
        if raw is None and index < len(raw_scenes) and isinstance(raw_scenes[index], dict):
            raw = raw_scenes[index]
        if not isinstance(raw, dict):
            continue
        scene["sensory_focus"] = stringify_text_value(raw.get("sensory_focus"))
        scene["dialogue_subtext"] = stringify_text_value(raw.get("dialogue_subtext"))
        constraints = raw.get("pov_knowledge_constraints")
        constraints = dict(constraints) if isinstance(constraints, dict) else {}
        boundary = stringify_text_value(raw.get("pov_boundary"))
        forbidden = coerce_text_list(constraints.get("forbidden_knowledge", []))
        if boundary and boundary not in forbidden:
            forbidden.append(boundary)
        scene["pov_knowledge_constraints"] = {
            "forbidden_knowledge": forbidden,
            "sensory_limits": coerce_text_list(constraints.get("sensory_limits", [])),
            "scope_label": stringify_text_value(constraints.get("scope_label"))
            or stringify_text_value(raw.get("pov_scope"))
            or "limited",
        }
        scene["world_rule_ids"] = coerce_text_list(raw.get("world_rule_ids", []))
        scene["world_rule_usage"] = stringify_text_value(raw.get("world_rule_usage"))
        scene["world_rule_evidence_expectations"] = coerce_text_list(
            raw.get("world_rule_evidence_expectations", [])
        )
        scene["world_rule_forbidden_boundaries"] = coerce_text_list(
            raw.get("world_rule_forbidden_boundaries", [])
        )
    return scenes


def remap_plan_scene_references(plan: dict[str, Any], id_map: dict[str, str]) -> None:
    """Atomically remap every plan-owned reference after scene identity changes."""

    def remap(value: Any) -> str:
        current = str(value or "").strip()
        return id_map.get(current, current)

    for scene in list(plan.get("scene_intents") or []):
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        dependencies: list[str] = []
        for dependency in coerce_text_list(scene.get("dependency_scene_ids", [])):
            mapped = remap(dependency)
            if mapped and mapped != scene_id and mapped not in dependencies:
                dependencies.append(mapped)
        scene["dependency_scene_ids"] = dependencies
    for key in ("world_rule_applications", "required_literals"):
        for item in list(plan.get(key) or []):
            if isinstance(item, dict) and item.get("scene_id"):
                item["scene_id"] = remap(item.get("scene_id"))
    cross_scene = plan.get("cross_scene_intent")
    if not isinstance(cross_scene, dict):
        return
    for reference in list(cross_scene.get("cross_scene_references") or []):
        if not isinstance(reference, dict):
            continue
        for key in ("from_scene", "to_scene", "source_scene_id", "target_scene_id"):
            if reference.get(key):
                reference[key] = remap(reference.get(key))


def positional_scene_id_map(before: list[str], after: list[str]) -> dict[str, str]:
    """Map identity-preserving rewrites such as missing-beat conditioning."""

    return {old: new for old, new in zip(before, after, strict=False) if old and new and old != new}


def scene_limit_id_map(before: list[str], *, max_scenes: int) -> dict[str, str]:
    """Mirror the legacy scene-limit merge policy without duplicating content logic."""

    if len(before) <= max_scenes:
        return {}
    result: dict[str, str] = {}
    if max_scenes <= 2:
        for index, old in enumerate(before):
            target = "scene_02" if index == len(before) - 1 else "scene_01"
            if old:
                result[old] = target
        return result
    for index, old in enumerate(before):
        if index < max_scenes - 1:
            target_index = index + 1
        elif index == len(before) - 1:
            target_index = max_scenes
        else:
            target_index = max_scenes - 1
        if old:
            result[old] = f"scene_{target_index:02d}"
    return result
