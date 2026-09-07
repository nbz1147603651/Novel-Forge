"""Generate the project-wide editorial contract during long initialization."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.utils.type_coerce import coerce_text_list
from novel_forge.editorial.schemas import EditorialContract, TitlePolicy
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.token_budget import route_bounded_json_output_budget

_TEXT_LIMIT = 420
_LIST_LIMIT = 8
_DICT_LIMIT = 16
_POLICY_TEXT_KEYS = ("policy", "rule", "description", "requirement", "text", "content")
_TITLE_POLICY_KEYS = ("max_reuse", "allowed_repeated_titles", "naming_strategy")


@dataclass
class EditorialContractInput:
    """Inputs for the split editorial contract derivation."""

    title: str = ""
    total_chapters: int = 0
    story_bible: dict[str, Any] = field(default_factory=dict)
    character_bible: dict[str, Any] = field(default_factory=dict)
    style_profile: dict[str, Any] | None = None
    blueprint: dict[str, Any] = field(default_factory=dict)
    blueprint_elements: dict[str, Any] = field(default_factory=dict)
    creative_director_packet: dict[str, Any] | None = None


def _clip_text(value: Any, *, limit: int = _TEXT_LIMIT) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _compact_value(value: Any, *, depth: int = 0, list_limit: int = _LIST_LIMIT) -> Any:
    if isinstance(value, str):
        return _clip_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if depth >= 3:
        return _clip_text(str(value), limit=240)
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for key, item in list(value.items())[:_DICT_LIMIT]:
            compact[str(key)] = _compact_value(item, depth=depth + 1, list_limit=list_limit)
        return compact
    if isinstance(value, list | tuple):
        return [
            _compact_value(item, depth=depth + 1, list_limit=list_limit)
            for item in list(value)[:list_limit]
        ]
    return _clip_text(str(value), limit=240)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pick(raw: Any, keys: tuple[str, ...], *, list_limit: int = _LIST_LIMIT) -> dict[str, Any]:
    source = _mapping(raw)
    result: dict[str, Any] = {}
    for key in keys:
        if key in source and source[key] not in (None, "", [], {}):
            result[key] = _compact_value(source[key], list_limit=list_limit)
    return result


def _compact_items(
    items: Any, keys: tuple[str, ...], *, limit: int = _LIST_LIMIT
) -> list[dict[str, Any]]:
    if not isinstance(items, list | tuple):
        return []
    compacted: list[dict[str, Any]] = []
    for item in list(items)[:limit]:
        picked = _pick(item, keys)
        if picked:
            compacted.append(picked)
    return compacted


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | tuple):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts)
    if isinstance(value, Mapping):
        parts = [str(item).strip() for item in value.values() if str(item).strip()]
        return "；".join(parts)
    return str(value or "").strip()


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [text for item in value if (text := _as_text(item))]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Mapping):
        return [text for item in value.values() if (text := _as_text(item))]
    text = _as_text(value)
    return [text] if text else []


def _coerce_int(value: Any, *, default: int, minimum: int = 0) -> int:
    if isinstance(value, bool):
        return max(minimum, int(value))
    if isinstance(value, int):
        return max(minimum, value)
    if isinstance(value, float):
        return max(minimum, int(value))
    try:
        return max(minimum, int(str(value or "").strip()))
    except (TypeError, ValueError):
        return max(minimum, default)


def _int_candidates(value: Any) -> list[int]:
    if isinstance(value, bool):
        return [int(value)]
    if isinstance(value, int):
        return [value]
    if isinstance(value, float):
        return [int(value)]
    if isinstance(value, list | tuple):
        items: list[int] = []
        for item in value:
            items.extend(_int_candidates(item))
        return items
    if isinstance(value, Mapping):
        candidates: list[int] = []
        for key in (
            "count",
            "chapters",
            "expected_chapters",
            "expected_aftermath_chapters",
            "start",
            "end",
            "start_chapter",
            "end_chapter",
        ):
            if key in value:
                candidates.extend(_int_candidates(value[key]))
        if candidates:
            return candidates
        for item in value.values():
            candidates.extend(_int_candidates(item))
        return candidates

    text = str(value or "").strip()
    if not text:
        return []
    return [int(match) for match in re.findall(r"\d+", text)]


def _coerce_aftermath_chapters(value: Any, *, chapter: int, default: int) -> int:
    candidates = _int_candidates(value)
    if len(candidates) >= 2:
        upper = max(candidates[0], candidates[-1])
        if upper >= chapter and upper > 10:
            return max(0, upper - chapter)
        return max(0, upper)
    if candidates:
        return max(0, candidates[0])
    return _coerce_int(value, default=default, minimum=0)


def _bounded_denouement_chapters(total_chapters: int) -> int:
    """Return a compact deterministic aftermath budget for the project length."""
    if total_chapters <= 1:
        return 0
    return max(1, min(2, int(total_chapters * 0.2)))


def _chapter_number_from_mapping(value: Any) -> int:
    if not isinstance(value, Mapping):
        return 0
    for key in (
        "chapter_number",
        "chapter",
        "target_chapter",
        "resolve_chapter",
        "chapter_end",
        "end_chapter",
    ):
        parsed = _coerce_int(value.get(key), default=0, minimum=0)
        if parsed:
            return parsed
    chapter_range = value.get("chapter_range")
    if isinstance(chapter_range, Mapping):
        return _coerce_int(
            chapter_range.get("end", chapter_range.get("start")),
            default=0,
            minimum=0,
        )
    return 0


def _deterministic_editorial_seed(input_data: EditorialContractInput) -> dict[str, Any]:
    """Build local structure/style defaults that are mechanically derivable."""
    total_chapters = max(0, int(input_data.total_chapters or 0))
    aftermath = _bounded_denouement_chapters(total_chapters)
    default_main_chapter = (
        max(1, total_chapters - aftermath) if total_chapters > 1 else max(1, total_chapters)
    )

    blueprint = _mapping(input_data.blueprint)
    candidate_chapters: list[int] = []
    for key in ("key_turning_points", "narrative_phases", "subplot_plan", "suspense_schedule"):
        items = blueprint.get(key)
        if not isinstance(items, list | tuple):
            continue
        for item in items:
            chapter = _chapter_number_from_mapping(item)
            if chapter:
                candidate_chapters.append(chapter)
    bounded_candidates = [
        chapter
        for chapter in candidate_chapters
        if chapter > 0 and (total_chapters <= 1 or chapter < total_chapters)
    ]
    main_chapter = max(bounded_candidates, default=default_main_chapter)
    if total_chapters > 1:
        main_chapter = min(main_chapter, total_chapters - 1)

    return {
        "climax_markers": [
            {
                "chapter_number": main_chapter,
                "climax_type": "main",
                "description": "主线核心选择完成，并为余波后果留出预算。",
                "expected_aftermath_chapters": aftermath,
            }
        ],
        "denouement_budget": {
            "expected_chapters": aftermath,
            "max_confirmation_scenes": 2,
            "required_new_functions": ["余波后果", "关系制度化", "终场意象"],
            "forbidden_repeats": ["重复确认同一主题句"],
        },
        "time_bridge_policies": ["跨章或跨阶段转场必须给出明确时间桥。"],
        "title_policy": {
            "max_reuse": 2,
            "allowed_repeated_titles": ["核心回环标题"],
            "naming_strategy": "章节标题提示功能节点，重复标题只用于有意回环。",
        },
    }


def _seeded_editorial_structure(
    structure: dict[str, Any],
    *,
    seed: dict[str, Any],
    total_chapters: int,
) -> dict[str, Any]:
    """Use deterministic seeds as structural guard rails for LLM output."""
    result = dict(structure)
    seed_marker = dict((seed.get("climax_markers") or [{}])[0])
    seed_budget = dict(seed.get("denouement_budget") or {})
    seed_after = _coerce_int(seed_marker.get("expected_aftermath_chapters"), default=0, minimum=0)
    seed_main = _coerce_int(seed_marker.get("chapter_number"), default=1, minimum=1)
    max_main = max(1, total_chapters - 1) if total_chapters > 1 else 1
    max_aftermath = _bounded_denouement_chapters(total_chapters)

    raw_markers = result.get("climax_markers")
    markers = list(raw_markers) if isinstance(raw_markers, list | tuple) else []
    normalized_markers: list[dict[str, Any]] = []
    has_main = False
    for item in markers:
        if not isinstance(item, Mapping):
            continue
        marker = dict(item)
        climax_type = str(marker.get("climax_type") or "").strip().lower()
        is_main = climax_type in {"main", "primary", "main_climax", "主高潮", "核心高潮"}
        chapter = _coerce_int(marker.get("chapter_number"), default=seed_main, minimum=1)
        if total_chapters > 1 and chapter >= total_chapters and is_main:
            chapter = seed_main
        if total_chapters > 0:
            chapter = min(chapter, total_chapters)
        if is_main:
            has_main = True
            chapter = min(chapter, max_main)
            aftermath = _coerce_aftermath_chapters(
                marker.get("expected_aftermath_chapters"),
                chapter=chapter,
                default=seed_after,
            )
            if total_chapters > 1:
                aftermath = min(aftermath, max(0, total_chapters - chapter), max_aftermath)
        else:
            aftermath = _coerce_aftermath_chapters(
                marker.get("expected_aftermath_chapters"),
                chapter=chapter,
                default=seed_after,
            )
            if total_chapters > 1:
                aftermath = min(aftermath, max(0, total_chapters - chapter))
        marker["chapter_number"] = chapter
        marker["expected_aftermath_chapters"] = aftermath
        normalized_markers.append(marker)
    if not has_main:
        normalized_markers.insert(0, seed_marker)
    result["climax_markers"] = normalized_markers or [seed_marker]

    raw_budget = result.get("denouement_budget")
    budget = dict(seed_budget)
    if isinstance(raw_budget, Mapping):
        budget.update(raw_budget)
    expected = _coerce_int(
        budget.get("expected_chapters"),
        default=_coerce_int(seed_budget.get("expected_chapters"), default=0, minimum=0),
        minimum=0,
    )
    if total_chapters > 1:
        expected = min(expected, max_aftermath)
    budget["expected_chapters"] = expected
    result["denouement_budget"] = budget
    result["time_bridge_policies"] = _normalize_policy_text_list(
        result.get("time_bridge_policies") or seed.get("time_bridge_policies", []),
    )
    result["title_policy"] = result.get("title_policy") or seed.get("title_policy", {})
    return result


def _normalize_policy_text_list(value: Any) -> list[str]:
    """Flatten common policy object lists into short strings."""

    return coerce_text_list(value, preferred_keys=_POLICY_TEXT_KEYS)


def _normalize_title_policy(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    payload = TitlePolicy.model_validate(source).model_dump(mode="json")
    return {key: payload[key] for key in _TITLE_POLICY_KEYS}


def _character_names_from_compact_bible(character_bible: dict[str, Any]) -> list[str]:
    raw_characters = character_bible.get("characters", [])
    names: list[str] = []
    seen: set[str] = set()
    for item in raw_characters if isinstance(raw_characters, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _as_text(item.get("name"))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _resolve_voice_character_name(raw_name: str, valid_characters: list[str]) -> str:
    name = _as_text(raw_name)
    if not valid_characters:
        return name
    if name in valid_characters:
        return name
    if name:
        # Substring first (handles 玄琰→玄昱 / 鬼目→鬼蛹 family pairs).
        for candidate in valid_characters:
            if len(name) >= 2 and (name in candidate or candidate in name):
                return candidate
        # Fuzzy fallback. SequenceMatcher.ratio() on CJK 2-char names returns
        # 0.5 for any single-character match, so 0.5 is the only safe floor;
        # the tie-reject (gap < 0.05) blocks 玄琰→玄苍-class misroutes where
        # multiple candidates score identically and the resolver must not guess.
        scored = sorted(
            (
                (SequenceMatcher(None, name, candidate).ratio(), candidate)
                for candidate in valid_characters
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.5:
            return ""
        best_score, best_candidate = scored[0]
        if len(scored) >= 2 and (best_score - scored[1][0]) < 0.05:
            return ""
        return best_candidate
    return ""


def _default_character_voice(character: str) -> dict[str, Any]:
    return {
        "character": character,
        "sentence_profile": f"{character}的句式保持清晰可辨。",
        "explanation_bias": f"{character}通过行动和细节表达立场。",
        "emotion_syntax": f"{character}的情绪通过句法变化而非直白陈述表达。",
        "signature_moves": [f"{character}保持可识别的动作或句法习惯"],
        "taboo_patterns": ["不得使用与角色身份不符的通用口吻"],
        "sample_lines": [],
    }


def _character_voice_from_source_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Create a lossless fallback from the authoritative CharacterBible voice."""

    character = _as_text(profile.get("name"))
    voice = _as_text(profile.get("voice") or profile.get("speech_style"))
    personality = _as_text(profile.get("personality"))
    sentence_profile = voice or f"{character}的句式和停顿保持角色设定中的稳定节奏。"
    return {
        "character": character,
        "sentence_profile": sentence_profile,
        "explanation_bias": personality or f"{character}优先用行动和细节表达立场。",
        "emotion_syntax": (
            f"情绪升高时仍保持既定声纹：{voice}"
            if voice
            else f"{character}的情绪通过句法变化而非直白陈述表达。"
        ),
        "signature_moves": [f"保持「{sentence_profile}」中的句式、停顿和解释习惯"],
        "taboo_patterns": ["不得使用与角色既定身份和声纹不符的通用口吻"],
        "sample_lines": [],
    }


def _normalize_character_voices(
    value: Any,
    *,
    valid_characters: list[str] | None = None,
    character_profiles: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    valid_names = [
        str(name or "").strip() for name in (valid_characters or []) if str(name or "").strip()
    ]
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        voice = dict(item)
        raw_character = _as_text(voice.get("character"))
        character = _resolve_voice_character_name(raw_character, valid_names)
        if valid_names and not character:
            continue
        character = character or raw_character or "角色"
        if character in seen:
            continue
        voice["character"] = character
        text_defaults = {
            "sentence_profile": f"{character}的句式保持清晰可辨。",
            "explanation_bias": f"{character}通过行动和细节表达立场。",
            "emotion_syntax": f"{character}的情绪通过句法变化而非直白陈述表达。",
        }
        for key in ("sentence_profile", "explanation_bias", "emotion_syntax"):
            text = _as_text(voice.get(key))
            voice[key] = text or text_defaults[key]
        voice["taboo_patterns"] = _as_text_list(voice.get("taboo_patterns"))
        voice["sample_lines"] = _as_text_list(voice.get("sample_lines"))
        signature_moves = _as_text_list(voice.get("signature_moves"))
        if not signature_moves:
            signature_moves = _as_text_list(voice.get("signature_move"))
        if not signature_moves:
            signature_moves = _as_text_list(voice.get("gestures"))
        if not signature_moves:
            signature_moves = [f"{character}保持可识别的动作或句法习惯"]
        voice["signature_moves"] = signature_moves
        normalized.append(voice)
        seen.add(character)

    source_by_name = {
        _as_text(profile.get("name")): profile
        for profile in character_profiles or []
        if _as_text(profile.get("name"))
    }
    for character in valid_names:
        if character in seen:
            continue
        source = source_by_name.get(character)
        if source is None:
            continue
        normalized.append(_character_voice_from_source_profile(source))
        seen.add(character)
    return normalized


def _normalize_revelation_ladder(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    normalized: list[dict[str, Any]] = []
    text_keys = (
        "thread",
        "stage",
        "trigger",
        "allowed_disclosure",
        "required_action_consequence",
    )
    fallback_text = {
        "thread": "未命名揭示线",
        "stage": "阶段揭示",
        "trigger": "由关键场景触发",
        "allowed_disclosure": "只释放本级必要信息",
        "required_action_consequence": "揭示后必须推动角色行动",
    }
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        raw_entry = dict(item)
        entry: dict[str, Any] = {}
        for key in text_keys:
            entry[key] = _as_text(raw_entry.get(key)) or f"第{index}级{fallback_text[key]}"
        entry["stage_order"] = _coerce_int(
            raw_entry.get("stage_order", raw_entry.get("order")),
            default=index,
            minimum=1,
        )
        entry["target_chapter"] = _coerce_int(
            raw_entry.get(
                "target_chapter", raw_entry.get("chapter", raw_entry.get("chapter_number"))
            ),
            default=0,
            minimum=0,
        )
        normalized.append(entry)
    return normalized


def _sanitize_element_id(raw_id: str) -> str:
    """Defensively clean an ``element_id`` extracted from a parsed model response.

    LLM JSON repair can occasionally leave a stray ``:`` inside an identifier
    (e.g. ``horror_dread_rhy:thm``) when the original key was emitted without
    its opening quote. This helper strips internal colons and surrounding
    whitespace; it only rewrites the id when the cleaned form is a known
    library element, so genuinely unknown ids are left untouched and surfaced
    by the downstream validator as real ``editorial_element_unknown`` findings.
    """
    cleaned = raw_id.strip()
    if ":" not in cleaned:
        return cleaned
    candidate = cleaned.replace(":", "").strip()
    try:
        from novel_forge.pipeline.steps.blueprint_element_select.elements import get_library_by_id

        if candidate in get_library_by_id():
            return candidate
    except Exception:
        pass
    return cleaned


def _normalize_element_directives(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    normalized: list[dict[str, Any]] = []
    aliases = {
        "element_id": ("element_id", "element", "element_key", "element_type"),
        "element_name": ("element_name", "name", "element_label"),
        "directive_type": ("directive_type", "type", "directive"),
        "target_window": ("target_window", "window", "chapter_window", "target_chapters"),
        "requirement": ("requirement", "rule", "must_do"),
        "success_criteria": ("success_criteria", "success_criterion", "success", "criteria"),
    }
    fallback_text = {
        "directive_type": "项目级编辑约束",
        "target_window": "全书适用",
        "requirement": "该指令需要在具体场景中落实",
        "success_criteria": "读者能看见对应结构或表达变化",
    }
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            continue
        raw_entry = dict(item)
        entry: dict[str, Any] = {}
        for key, candidate_keys in aliases.items():
            text = ""
            for candidate in candidate_keys:
                text = _as_text(raw_entry.get(candidate))
                if text:
                    break
            if text:
                entry[key] = text
        if not _as_text(entry.get("element_id")):
            entry["element_id"] = f"unknown_editorial_element_{index}"
        else:
            # Defensive cleanup: strip stray colons introduced by JSON repair
            # side-effects, but only when the cleaned id is a real library
            # element so genuine unknowns are still reported by the validator.
            entry["element_id"] = _sanitize_element_id(entry["element_id"])
        for key, fallback in fallback_text.items():
            entry[key] = _as_text(entry.get(key)) or fallback
        entry["element_name"] = _as_text(entry.get("element_name"))
        linked_characters: list[str] = []
        for candidate in (
            "linked_characters",
            "linked_charifiers",
            "characters",
            "linked_roles",
        ):
            linked_characters = _as_text_list(raw_entry.get(candidate))
            if linked_characters:
                break
        entry["linked_characters"] = linked_characters
        normalized.append(entry)
    return normalized


def _compact_story_bible(story_bible: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "title",
        "premise",
        "synopsis",
        "logline",
        "genre",
        "themes",
        "tone",
        "setting",
        "worldview",
        "world_rules",
        "era",
        "geography",
        "culture",
        "magic_or_tech",
        "rules",
        "conflicts",
        "ending",
    )
    return _pick(story_bible, keys, list_limit=6)


def _compact_character_bible(
    character_bible: dict[str, Any],
    *,
    limit: int = 10,
) -> dict[str, Any]:
    raw = _mapping(character_bible)
    characters = raw.get("characters", character_bible if isinstance(character_bible, list) else [])
    character_keys = (
        "name",
        "character_id",
        "role",
        "gender",
        "age",
        "personality",
        "arc",
        "goal",
        "motivation",
        "backstory",
        "relationships",
        "voice",
        "speech_style",
        "notes",
    )
    return {
        "characters": _compact_items(characters, character_keys, limit=limit),
        **_pick(raw, ("relationship_matrix", "factions", "summary"), list_limit=6),
    }


def _character_bible_voice_batch(
    character_bible: dict[str, Any],
    names: list[str],
) -> dict[str, Any]:
    wanted = set(names)
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in character_bible.get("characters", []):
        if not isinstance(item, Mapping):
            continue
        name = _as_text(item.get("name"))
        if name not in wanted or name in seen:
            continue
        selected.append(item)
        seen.add(name)
    return {"characters": selected}


def _compact_style_profile(style_profile: dict[str, Any] | None) -> dict[str, Any]:
    raw = _mapping(style_profile or {})
    modules = _compact_items(
        raw.get("modules", []),
        ("name", "rules", "positive_example", "negative_example"),
        limit=6,
    )
    result = _pick(
        raw,
        (
            "summary",
            "source_elements",
            "global_style",
            "hook_config",
            "cool_point_config",
            "micro_payoff_config",
            "strand_config",
            "pacing_config",
            "reading_power_window_config",
        ),
        list_limit=6,
    )
    if modules:
        result["modules"] = modules
    return result


def _compact_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(blueprint)
    result = _pick(
        raw,
        (
            "synopsis",
            "volume_mode",
            "narrative_phases",
            "key_turning_points",
            "subplot_plan",
            "character_arcs",
            "suspense_plan",
            "threads",
            "ending_strategy",
            "commercial_hook_plan",
        ),
        list_limit=8,
    )
    volumes = _compact_items(
        raw.get("volumes", []),
        (
            "volume_number",
            "title",
            "start_chapter",
            "end_chapter",
            "arc_goal",
            "milestone_targets",
        ),
        limit=8,
    )
    if volumes:
        result["volumes"] = volumes
    return result


def _compact_blueprint_elements(blueprint_elements: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(blueprint_elements)
    element_keys = (
        "element_id",
        "name",
        "category",
        "tier",
        "description",
        "selection_reason",
        "rationale",
        "prompt_hint",
        "selection_score",
    )
    result = _pick(
        raw,
        ("selector_summary", "genre_inference", "focus_constraints", "library_version", "mode"),
        list_limit=10,
    )
    result["required_elements"] = _compact_items(
        raw.get("required_elements", []),
        element_keys,
        limit=10,
    )
    result["extension_elements"] = _compact_items(
        raw.get("extension_elements", []),
        element_keys,
        limit=12,
    )
    result["quality_config_elements"] = _compact_items(
        raw.get("quality_config_elements", []),
        element_keys,
        limit=4,
    )
    return result


def _compact_creative_director_packet(packet: dict[str, Any] | None) -> dict[str, Any]:
    raw = _mapping(packet or {})
    return _pick(
        raw,
        (
            "emotional_engine",
            "thematic_promises",
            "signature_motifs",
            "relationship_tensions",
            "anti_cliche_rules",
            "scene_potential",
            "notes",
            "creative_positioning",
            "project_brief",
            "editorial_priorities",
            "risk_register",
            "market_positioning",
            "reader_promise",
            "must_keep",
            "must_avoid",
            "summary",
        ),
        list_limit=8,
    )


def _build_base_context(input_data: EditorialContractInput) -> dict[str, Any]:
    story_bible = _compact_story_bible(input_data.story_bible)
    blueprint = _compact_blueprint(input_data.blueprint)
    seed = _deterministic_editorial_seed(input_data)
    return {
        "title": input_data.title,
        "total_chapters": input_data.total_chapters,
        "editorial_contract_seed": seed,
        "project_brief": {
            "title": input_data.title,
            "total_chapters": input_data.total_chapters,
            "story_synopsis": story_bible.get("synopsis") or story_bible.get("premise") or "",
            "blueprint_synopsis": blueprint.get("synopsis", ""),
            "themes": story_bible.get("themes", []),
            "tone": story_bible.get("tone", ""),
        },
        "story_bible": story_bible,
        "blueprint": blueprint,
        "creative_director_packet": _compact_creative_director_packet(
            input_data.creative_director_packet,
        ),
    }


class EditorialContractStep(PipelineStep[EditorialContractInput, EditorialContract]):
    """Derive a contract through smaller parallel model calls, then validate locally."""

    @property
    def step_name(self) -> str:
        return "derive_editorial_contract"

    def _temperature(self) -> float:
        return min(float(getattr(self.settings, "temp_plan_outline", 0.4)), 0.1)

    def _part_max_tokens(self, task_type: TaskType) -> int:
        return route_bounded_json_output_budget(self._router, task_type)

    async def _derive_part(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        required_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        payload = await self._call_with_retry(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=self._temperature(),
            required_keys=required_keys,
            max_retries=4,
            thinking=False,
        )
        return payload if isinstance(payload, dict) else {}

    async def _execute(self, input_data: EditorialContractInput) -> EditorialContract:
        base_context = _build_base_context(input_data)
        editorial_seed = base_context.get("editorial_contract_seed", {})
        character_bible = _compact_character_bible(input_data.character_bible)
        voice_character_bible = _compact_character_bible(
            input_data.character_bible,
            limit=40,
        )
        valid_voice_characters = _character_names_from_compact_bible(voice_character_bible)
        voice_batches = [
            valid_voice_characters[index : index + 6]
            for index in range(0, len(valid_voice_characters), 6)
        ] or [[]]
        style_profile = _compact_style_profile(input_data.style_profile)
        blueprint_elements = _compact_blueprint_elements(input_data.blueprint_elements)

        voices_task = self._derive_part(
            TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
            {
                **base_context,
                "character_bible": _character_bible_voice_batch(
                    voice_character_bible,
                    voice_batches[0],
                ),
                "voice_character_whitelist": voice_batches[0],
                "voice_batch_index": 1,
                "voice_batch_count": len(voice_batches),
                "style_profile": style_profile,
            },
            max_tokens=self._part_max_tokens(TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES),
            required_keys=("character_voices",),
        )
        structure_task = self._derive_part(
            TaskType.DERIVE_EDITORIAL_STRUCTURE,
            {
                **base_context,
                "blueprint_elements": blueprint_elements,
            },
            max_tokens=self._part_max_tokens(TaskType.DERIVE_EDITORIAL_STRUCTURE),
            required_keys=(
                "climax_markers",
                "denouement_budget",
                "revelation_ladder",
                "time_bridge_policies",
                "title_policy",
            ),
        )
        # Concurrency cap: derive the four editorial parts in two staggered
        # batches of two. A single 4-way gather floods the routed provider
        # (e.g. minimax/MiniMax-M3) with multiple large JSON requests
        # in the same second, triggering 500-rejections that cascade into
        # circuit-breaker trips and fail-over. Two-at-a-time keeps total
        # wall-clock latency low (each batch waits for its slower sibling)
        # while halving the in-flight request count per provider.
        voices, structure = await asyncio.gather(voices_task, structure_task)
        voice_items = list(voices.get("character_voices", []) or [])
        # Keep provider concurrency bounded while still generating a complete
        # source voice matrix for rosters larger than one prompt-sized batch.
        for batch_index, batch_names in enumerate(voice_batches[1:], start=2):
            batch_payload = await self._derive_part(
                TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
                {
                    **base_context,
                    "character_bible": _character_bible_voice_batch(
                        voice_character_bible,
                        batch_names,
                    ),
                    "voice_character_whitelist": batch_names,
                    "voice_batch_index": batch_index,
                    "voice_batch_count": len(voice_batches),
                    "style_profile": style_profile,
                },
                max_tokens=self._part_max_tokens(TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES),
                required_keys=("character_voices",),
            )
            voice_items.extend(batch_payload.get("character_voices", []) or [])
        style_task = self._derive_part(
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
            {
                **base_context,
                "character_bible": character_bible,
                "style_profile": style_profile,
                "blueprint_elements": blueprint_elements,
            },
            max_tokens=self._part_max_tokens(TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS),
            required_keys=(
                "theme_policies",
                "symbol_policies",
                "scene_resistance_rules",
                "expression_channel_budget",
                "expression_channel_profiles",
                "body_signal_budget_per_high_emotion_scene",
                "forbidden_confirmation_phrases",
                "revision_priorities",
            ),
        )
        directives_task = self._derive_part(
            TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
            {
                **base_context,
                "character_bible": character_bible,
                "blueprint_elements": blueprint_elements,
            },
            max_tokens=self._part_max_tokens(TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES),
            required_keys=("editorial_element_directives",),
        )
        style_constraints, directives = await asyncio.gather(style_task, directives_task)
        structure = _seeded_editorial_structure(
            structure,
            seed=editorial_seed if isinstance(editorial_seed, dict) else {},
            total_chapters=int(input_data.total_chapters or 0),
        )
        payload = {
            "project_title": input_data.title,
            "character_voices": _normalize_character_voices(
                voice_items,
                valid_characters=valid_voice_characters,
                character_profiles=[
                    item
                    for item in voice_character_bible.get("characters", [])
                    if isinstance(item, Mapping)
                ],
            ),
            "climax_markers": structure.get("climax_markers", []),
            "denouement_budget": structure.get("denouement_budget", {}),
            "theme_policies": _normalize_policy_text_list(
                style_constraints.get("theme_policies", []),
            ),
            "symbol_policies": style_constraints.get("symbol_policies", []),
            "scene_resistance_rules": style_constraints.get("scene_resistance_rules", []),
            "expression_channel_budget": style_constraints.get("expression_channel_budget", {}),
            "expression_channel_profiles": style_constraints.get(
                "expression_channel_profiles",
                [],
            ),
            "body_signal_budget_per_high_emotion_scene": style_constraints.get(
                "body_signal_budget_per_high_emotion_scene",
                1,
            ),
            "forbidden_confirmation_phrases": _normalize_policy_text_list(
                style_constraints.get("forbidden_confirmation_phrases", []),
            ),
            "revision_priorities": _normalize_policy_text_list(
                style_constraints.get("revision_priorities", []),
            ),
            "revelation_ladder": _normalize_revelation_ladder(
                structure.get("revelation_ladder", [])
            ),
            "editorial_element_directives": _normalize_element_directives(
                directives.get("editorial_element_directives", [])
            ),
            "time_bridge_policies": _normalize_policy_text_list(
                structure.get("time_bridge_policies", []),
            ),
            "title_policy": _normalize_title_policy(structure.get("title_policy", {})),
        }
        return EditorialContract.model_validate(payload)
