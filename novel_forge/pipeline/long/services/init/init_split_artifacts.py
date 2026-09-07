"""Split-task builders for large initialization artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.character_boundary import (
    canonicalize_character_arc_items,
    canonicalize_relationship_items,
    rewrite_character_aliases,
)
from novel_forge.core.domain.language import normalize_payload_for_language
from novel_forge.core.domain.shared_anchor import build_shared_evidence_anchor
from novel_forge.core.domain.world_context import dump_story_bible_for_prompt
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.init_coherence import InitCoherenceProfile
from novel_forge.pipeline.long.services.init.init_v2 import (
    CHARACTER_FRAGMENT_CONTRACT_VERSION,
    CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
    hash_payload,
)
from novel_forge.pipeline.steps.split_artifact_runner import SplitArtifactRunner, SplitJsonFragment
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens

_PROFILE_PARALLEL_MIN_ROSTER = 11
_PROFILE_BATCH_SIZE = 5
_PROFILE_PARALLEL_MIN_ROSTER_MAX = 50
_PROFILE_BATCH_SIZE_MAX = 12
_CHARACTER_GENERATION_MODE_SPLIT = "split_v2"
_TIME_LAYER_LABELS = {
    "modern": "现代",
    "past": "过去",
    "cross_temporal": "跨时空",
    "memory_only": "回忆",
    "default": "默认",
}
_STATUS_LABELS = {
    "active": "活跃",
    "dormant": "休眠",
    "retired": "退场",
}
_ROLE_LABELS = {
    "protagonist": "主角",
    "deuteragonist": "第二主角",
    "antagonist": "反派",
    "supporting": "配角",
    "minor": "小角色",
}


def split_tasks_enabled(settings: Any) -> bool:
    """Return True when split artifact generation is enabled."""
    return bool(getattr(settings, "split_tasks_enabled", True))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _character_profile_batching_settings(settings: Any) -> tuple[int, int]:
    """Return quality-first profile batching knobs from settings."""
    return (
        _bounded_int(
            getattr(
                settings,
                "init_character_profile_parallel_min_roster",
                _PROFILE_PARALLEL_MIN_ROSTER,
            ),
            default=_PROFILE_PARALLEL_MIN_ROSTER,
            minimum=1,
            maximum=_PROFILE_PARALLEL_MIN_ROSTER_MAX,
        ),
        _bounded_int(
            getattr(settings, "init_character_profile_batch_size", _PROFILE_BATCH_SIZE),
            default=_PROFILE_BATCH_SIZE,
            minimum=1,
            maximum=_PROFILE_BATCH_SIZE_MAX,
        ),
    )


async def _call_ctx_json(
    ctx: Any,
    task_type: TaskType,
    context: dict[str, Any],
    max_tokens: int,
    temperature: float,
    required_keys: tuple[str, ...],
    max_retries: int,
) -> dict[str, Any]:
    return await ctx.call_with_retry(
        task_type,
        context,
        max_tokens=max_tokens,
        temperature=min(0.1, max(0.0, float(temperature))),
        required_keys=required_keys,
        max_retries=max_retries,
        thinking=False,
        multi_turn=False,
    )


def _fragment_dir(ctx: Any, name: str) -> Path:
    path = ctx.layout.root / "initialization" / "fragments" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tokens(ctx: Any, task_type: TaskType, target_chars: int, *, minimum: int = 2048) -> int:
    return calculate_route_aware_max_tokens(
        ctx.router,
        task_type,
        target_chars,
        prompt_overhead=3000,
        min_tokens=minimum,
        max_cap=8192,
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: Any, *, preferred_keys: tuple[str, ...] = ()) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if item is None:
            continue
        text = ""
        if isinstance(item, dict):
            for key in preferred_keys + (
                "name",
                "label",
                "value",
                "id",
                "type",
                "marker",
                "term",
                "axis",
                "event_type",
                "payoff_type",
                "temporal_marker",
            ):
                candidate = item.get(key)
                if candidate is not None and str(candidate).strip():
                    text = str(candidate).strip()
                    break
            if not text:
                text = json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _canonical_character_roster(character_bible: dict[str, Any]) -> list[dict[str, str]]:
    """Return the small, authoritative character-name boundary for profile prompts."""

    result: list[dict[str, str]] = []
    for item in _list(character_bible.get("characters")):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append(
            {
                "character_id": str(item.get("character_id") or "").strip(),
                "name": name,
                "role": str(item.get("role") or "").strip(),
            }
        )
    return result


def _normalize_coherence_ontology_payload(payload: Any) -> dict[str, Any]:
    ontology = _dict(payload)
    list_key_hints: dict[str, tuple[str, ...]] = {
        "domains": ("domain",),
        "entity_types": ("entity_type",),
        "state_axes": ("axis",),
        "relationship_axes": ("axis",),
        "payoff_types": ("payoff_type", "type"),
        "irreversible_event_markers": ("marker", "event_type"),
        "temporal_markers": ("marker", "temporal_marker"),
    }
    for key, preferred in list_key_hints.items():
        ontology[key] = _string_list(ontology.get(key), preferred_keys=preferred)
    terminology = ontology.get("terminology")
    if isinstance(terminology, dict):
        ontology["terminology"] = {
            str(key): str(value or "") for key, value in terminology.items() if str(key).strip()
        }
    else:
        ontology["terminology"] = {}
    return ontology


def _empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _canonical_story_key(key: str) -> str:
    return key


def _compact_json(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return str(value)


def _same_story_value(left: Any, right: Any) -> bool:
    return _compact_json(left) == _compact_json(right)


def _append_story_note(payload: dict[str, Any], note: str) -> None:
    note = note.strip()
    if not note:
        return
    current = str(payload.get("notes") or "").strip()
    payload["notes"] = f"{current}\n{note}" if current else note


def _merge_story_fragment(
    payload: dict[str, Any],
    fragment: dict[str, Any],
    *,
    fragment_name: str,
    allowed_keys: set[str],
) -> None:
    """Merge one StoryBible fragment without letting it overwrite other dimensions."""
    for raw_key, value in fragment.items():
        if _empty(value):
            continue
        key = _canonical_story_key(str(raw_key))
        if key not in allowed_keys:
            _append_story_note(
                payload,
                f"分片越界字段（{fragment_name}.{raw_key}）：{_compact_json(value)}",
            )
            continue
        if key in payload and not _empty(payload.get(key)):
            if not _same_story_value(payload.get(key), value):
                _append_story_note(
                    payload,
                    f"分片字段冲突（{fragment_name}.{raw_key}）：保留既有值，候选值={_compact_json(value)}",
                )
            continue
        payload[key] = value


def _merge_character_arc_item(
    profiles: dict[str, dict[str, Any]],
    arc_item: dict[str, Any],
) -> None:
    """Merge arc summaries and keep extra arc structure for CharacterProfile notes."""
    name = str(
        arc_item.get("name") or arc_item.get("character") or arc_item.get("character_name") or ""
    ).strip()
    if not name or name not in profiles:
        return
    arc = str(
        arc_item.get("arc")
        or arc_item.get("arc_summary")
        or arc_item.get("summary")
        or arc_item.get("trajectory")
        or ""
    ).strip()
    profile = profiles[name]
    if arc:
        profile["arc"] = arc
    reserved = {
        "name",
        "character",
        "character_name",
        "arc",
        "arc_summary",
        "summary",
        "trajectory",
    }
    for key, value in arc_item.items():
        if key in reserved or _empty(value):
            continue
        extra_key = key if str(key).startswith("arc_") else f"arc_{key}"
        profile[extra_key] = value


async def build_story_bible_split(
    ctx: Any,
    *,
    base_ctx: dict[str, Any],
    language: str,
) -> StoryBible:
    """Generate StoryBible fragments with a shared story_core anchor."""
    max_parallel = int(getattr(ctx.settings, "init_fragment_max_parallel", 4) or 4)
    story_dir = _fragment_dir(ctx, "story_bible")

    async def call_json(
        task_type: TaskType,
        context: dict[str, Any],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        max_retries: int,
    ) -> dict[str, Any]:
        return await _call_ctx_json(
            ctx,
            task_type,
            context,
            max_tokens,
            temperature,
            required_keys,
            max_retries,
        )

    core_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=story_dir / "story_core.checkpoint.json",
        artifact_name="story_bible_core",
    )
    base_anchor = build_shared_evidence_anchor(
        "init_story_bible.base",
        base_ctx,
        source_keys=(
            "premise",
            "title",
            "genre",
            "tone",
            "language",
            "world_hint",
            "conflict_hint",
            "extra_instructions",
        ),
    )
    core_payload = (
        await core_runner.run(
            [
                SplitJsonFragment(
                    name="core",
                    task_type=TaskType.INIT_STORY_CORE_PREMISE,
                    context={**base_ctx, "shared_evidence_anchor": base_anchor},
                    required_keys=("story_core",),
                    max_tokens=_tokens(ctx, TaskType.INIT_STORY_CORE_PREMISE, 1800),
                    temperature=getattr(ctx.settings, "temp_init_story_bible", 0.7),
                )
            ]
        )
    ).fragments["core"]
    story_core = _dict(core_payload.get("story_core"))
    story_anchor = build_shared_evidence_anchor(
        "init_story_bible.core",
        {**base_ctx, "story_core": story_core},
        source_keys=(
            "premise",
            "title",
            "genre",
            "tone",
            "world_hint",
            "conflict_hint",
            "story_core",
        ),
    )
    anchored_ctx = {**base_ctx, "story_core": story_core, "shared_evidence_anchor": story_anchor}

    parallel_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=max_parallel,
        checkpoint_path=story_dir / "story_bible_independent_fragments.checkpoint.json",
        artifact_name="story_bible_independent_fragments",
    )
    independent_fragments = (
        await parallel_runner.run(
            [
                SplitJsonFragment(
                    name="world",
                    task_type=TaskType.INIT_STORY_WORLD_RULES,
                    context=anchored_ctx,
                    required_keys=("world_rules",),
                    max_tokens=_tokens(ctx, TaskType.INIT_STORY_WORLD_RULES, 3200),
                    temperature=getattr(ctx.settings, "temp_init_story_bible", 0.7),
                ),
                SplitJsonFragment(
                    name="themes",
                    task_type=TaskType.INIT_STORY_THEMES_AND_SYMBOLS,
                    context=anchored_ctx,
                    required_keys=("themes_and_symbols",),
                    max_tokens=_tokens(ctx, TaskType.INIT_STORY_THEMES_AND_SYMBOLS, 2200),
                    temperature=getattr(ctx.settings, "temp_init_story_bible", 0.7),
                ),
            ]
        )
    ).fragments
    world_rules = _dict(independent_fragments.get("world", {}).get("world_rules"))
    themes_and_symbols = _dict(independent_fragments.get("themes", {}).get("themes_and_symbols"))

    # Time, naming and social-register rules depend on the settled era/world rules.
    continuity_ctx = {
        **anchored_ctx,
        "world_rules": world_rules,
        "themes_and_symbols": themes_and_symbols,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "init_story_bible.continuity",
            {
                "story_core": story_core,
                "world_rules": world_rules,
                "themes_and_symbols": themes_and_symbols,
            },
        ),
    }
    continuity_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=story_dir / "story_bible_continuity.checkpoint.json",
        artifact_name="story_bible_continuity",
    )
    continuity_payload = (
        await continuity_runner.run(
            [
                SplitJsonFragment(
                    name="continuity",
                    task_type=TaskType.INIT_STORY_CONTINUITY_RULES,
                    context=continuity_ctx,
                    required_keys=("continuity_rules",),
                    max_tokens=_tokens(ctx, TaskType.INIT_STORY_CONTINUITY_RULES, 2800),
                    temperature=getattr(ctx.settings, "temp_init_story_bible", 0.7),
                )
            ]
        )
    ).fragments["continuity"]

    story_payload: dict[str, Any] = {}
    _merge_story_fragment(
        story_payload,
        story_core,
        fragment_name="core",
        allowed_keys={"title", "premise", "tone"},
    )
    _merge_story_fragment(
        story_payload,
        world_rules,
        fragment_name="world",
        allowed_keys={
            "era",
            "geography",
            "culture",
            "magic_or_tech",
            "rules",
            "world_rule_book",
        },
    )
    _merge_story_fragment(
        story_payload,
        _dict(continuity_payload.get("continuity_rules")),
        fragment_name="continuity",
        allowed_keys={
            "time_convention",
            "social_hierarchy",
            "address_rules",
            "self_reference_rules",
            "etiquette_rules",
            "institution_terms",
            "material_culture",
            "anachronism_blacklist",
            "dialogue_register_rules",
        },
    )
    _merge_story_fragment(
        story_payload,
        themes_and_symbols,
        fragment_name="themes",
        allowed_keys={"themes", "banned_intent_rules", "notes"},
    )
    story_payload = normalize_payload_for_language(story_payload, language)
    return StoryBible.model_validate(story_payload)


def _normalized_roster_match_value(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip()).lower()
    return text.replace("岁", "").replace("歲", "").replace("约", "").replace("約", "")


def _profile_content_score(profile: dict[str, Any]) -> int:
    return sum(
        len(str(profile.get(key) or "").strip())
        for key in (
            "social_status",
            "abilities",
            "appearance",
            "personality",
            "backstory",
            "arc",
            "notes",
        )
    )


def _profile_roster_alias_score(profile: dict[str, Any], roster_item: dict[str, Any]) -> int:
    score = 0
    for key, weight in (
        ("role", 3),
        ("gender", 3),
        ("age", 2),
        ("time_layer", 1),
        ("status", 1),
    ):
        left = _normalized_roster_match_value(profile.get(key))
        right = _normalized_roster_match_value(roster_item.get(key))
        if left and right and left == right:
            score += weight
    return score


def _append_profile_note(profile: dict[str, Any], note: str) -> None:
    note = note.strip()
    if not note:
        return
    current = str(profile.get("notes") or "").strip()
    profile["notes"] = f"{current}\n{note}" if current else note


def _rewrite_profile_aliases_in_place(
    profile: dict[str, Any],
    alias_to_canonical: dict[str, str],
) -> None:
    """Keep generated wrong names out of CharacterBible text fields."""
    if not alias_to_canonical:
        return
    for key, value in list(profile.items()):
        if key == "name":
            continue
        profile[key] = rewrite_character_aliases(value, alias_to_canonical)


def _append_roster_function_note(roster_item: dict[str, Any], note: str) -> None:
    note = note.strip()
    if not note:
        return
    current = str(roster_item.get("function") or "").strip()
    roster_item["function"] = f"{current}；{note}" if current else note


def _label_from_mapping(value: Any, mapping: dict[str, str]) -> str:
    normalized = str(value or "").strip().lower()
    return mapping.get(normalized, str(value or "").strip())


def _roster_disambiguation_label(item: dict[str, Any], index: int) -> str:
    parts: list[str] = []
    time_layer = _label_from_mapping(item.get("time_layer"), _TIME_LAYER_LABELS)
    if time_layer and time_layer != _TIME_LAYER_LABELS["default"]:
        parts.append(time_layer)
    status = _label_from_mapping(item.get("status"), _STATUS_LABELS)
    if status and status != _STATUS_LABELS["active"]:
        parts.append(status)
    role = _label_from_mapping(item.get("role"), _ROLE_LABELS)
    if role:
        parts.append(role)
    if not parts:
        parts.append(f"角色{index + 1}")
    return "".join(dict.fromkeys(parts))


def _dedupe_character_roster_names(
    roster: list[Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Return a roster whose display names are safe to use as temporary keys.

    The legacy CharacterBible projection is still name-keyed, so split init must
    never let two roster entries share the same ``name``.  This keeps the fix
    generic: it qualifies colliding names from structural fields instead of
    special-casing a particular story or character.
    """
    normalized = [dict(item) if isinstance(item, dict) else item for item in roster]
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(normalized):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            groups.setdefault(name, []).append(index)

    reserved_names = {
        str(item.get("name") or "").strip()
        for item in normalized
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    changes: list[dict[str, Any]] = []
    for original_name, indexes in groups.items():
        if len(indexes) <= 1:
            continue
        reserved_names.discard(original_name)
        used_in_group: set[str] = set()
        for offset, index in enumerate(indexes):
            item = normalized[index]
            if not isinstance(item, dict):
                continue
            label = _roster_disambiguation_label(item, offset)
            candidate = f"{original_name}（{label}）"
            if candidate in reserved_names or candidate in used_in_group:
                candidate = f"{original_name}（{label}{offset + 1}）"
            while candidate in reserved_names or candidate in used_in_group:
                candidate = f"{original_name}（{label}{len(used_in_group) + 1}）"
            item["name"] = candidate
            used_in_group.add(candidate)
            changes.append(
                {
                    "original_name": original_name,
                    "canonical_name": candidate,
                    "role": item.get("role", ""),
                    "age": item.get("age", ""),
                    "gender": item.get("gender", ""),
                    "status": item.get("status", ""),
                    "time_layer": item.get("time_layer", ""),
                }
            )
            _append_roster_function_note(
                item,
                f"原始同名角色「{original_name}」已加限定名，后续分片必须使用「{candidate}」。",
            )
        reserved_names.update(used_in_group)
    return normalized, changes


def _sanitize_profile_fragment(profile: dict[str, Any]) -> dict[str, Any]:
    """Keep profile batches focused on dossiers; relationships are matrix-owned."""
    sanitized = dict(profile)
    sanitized.pop("relationships", None)
    return sanitized


def _find_roster_alias_match(
    profile: dict[str, Any],
    roster_items: list[dict[str, Any]],
) -> str | None:
    scored = [
        (
            _profile_roster_alias_score(profile, roster_item),
            str(roster_item.get("name") or "").strip(),
        )
        for roster_item in roster_items
        if str(roster_item.get("name") or "").strip()
    ]
    scored = [(score, name) for score, name in scored if score >= 8]
    if not scored:
        return None
    scored.sort(reverse=True)
    best_score, best_name = scored[0]
    if len(scored) > 1 and scored[1][0] >= best_score - 1:
        return None
    return best_name


def _profiles_by_roster(
    items: list[Any],
    roster: list[Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Return profile payloads keyed by canonical roster name.

    The profile fragment prompt treats roster names as fixed, but model outputs
    can still drift to a synonym or a prior draft name.  This merge keeps the
    local roster authoritative and records a deterministic alias map for later
    relationship-matrix normalization.
    """
    roster_items = [dict(item) for item in roster if isinstance(item, dict)]
    roster_names = {str(item.get("name") or "").strip() for item in roster_items}
    roster_names.discard("")
    profiles: dict[str, dict[str, Any]] = {}
    off_roster: list[dict[str, Any]] = []
    unmatched_profiles: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        profile = _sanitize_profile_fragment(dict(item))
        name = str(profile.get("name") or "").strip()
        if not name:
            continue
        if name in roster_names:
            existing = profiles.get(name)
            if existing is None or _profile_content_score(profile) > _profile_content_score(
                existing
            ):
                profile["name"] = name
                profiles[name] = profile
        else:
            off_roster.append(profile)

    alias_to_canonical: dict[str, str] = {}
    ambiguous_aliases: set[str] = set()
    for profile in off_roster:
        original_name = str(profile.get("name") or "").strip()
        canonical_name = _find_roster_alias_match(profile, roster_items)
        if not original_name or not canonical_name:
            if original_name:
                unmatched_profiles.append(
                    {
                        "name": original_name,
                        "role": profile.get("role", ""),
                        "age": profile.get("age", ""),
                        "gender": profile.get("gender", ""),
                        "status": profile.get("status", ""),
                        "time_layer": profile.get("time_layer", ""),
                    }
                )
            continue
        existing_alias = alias_to_canonical.get(original_name)
        if existing_alias and existing_alias != canonical_name:
            alias_to_canonical.pop(original_name, None)
            ambiguous_aliases.add(original_name)
        elif original_name not in ambiguous_aliases:
            alias_to_canonical[original_name] = canonical_name
        if canonical_name in profiles:
            continue
        mapped = dict(profile)
        mapped["name"] = canonical_name
        profiles[canonical_name] = mapped

    for profile in profiles.values():
        _rewrite_profile_aliases_in_place(profile, alias_to_canonical)

    return profiles, alias_to_canonical, unmatched_profiles


def _profile_roster_batches(
    roster: list[Any],
    *,
    parallel_min_roster: int = _PROFILE_PARALLEL_MIN_ROSTER,
    batch_size: int = _PROFILE_BATCH_SIZE,
) -> list[list[dict[str, Any]]]:
    roster_items = [dict(item) for item in roster if isinstance(item, dict)]
    parallel_min_roster = _bounded_int(
        parallel_min_roster,
        default=_PROFILE_PARALLEL_MIN_ROSTER,
        minimum=1,
        maximum=_PROFILE_PARALLEL_MIN_ROSTER_MAX,
    )
    batch_size = _bounded_int(
        batch_size,
        default=_PROFILE_BATCH_SIZE,
        minimum=1,
        maximum=_PROFILE_BATCH_SIZE_MAX,
    )
    if len(roster_items) < parallel_min_roster:
        return [roster_items] if roster_items else []
    return [
        roster_items[index : index + batch_size]
        for index in range(0, len(roster_items), batch_size)
    ]


def _profile_fragment_name(index: int, total: int) -> str:
    return "profiles" if total <= 1 else f"profiles_{index + 1:02d}"


def _profile_batch_target_chars(
    batch_size: int,
    *,
    total_roster: int,
    parallel_min_roster: int = _PROFILE_PARALLEL_MIN_ROSTER,
) -> int:
    if total_roster < parallel_min_roster:
        return 5200
    return max(2600, min(5200, 1200 + batch_size * 850))


_RELATIONSHIP_PROFILE_FIELDS = (
    "name",
    "role",
    "status",
    "time_layer",
    "social_status",
    "personality",
    "backstory",
    "arc",
    "notes",
)


def _relationship_repair_context(
    *,
    base_ctx: dict[str, Any],
    story_payload: dict[str, Any],
    roster: list[Any],
    profiles: list[Any],
    character_arcs: list[dict[str, Any]],
    target_names: list[str],
    candidate_evidence: list[dict[str, Any]],
    previous_relationship_matrix: list[dict[str, Any]],
    retry_reasons: list[dict[str, Any]],
    attempt: int,
) -> dict[str, Any]:
    """Build a bounded repair prompt from canonical evidence only.

    Repair calls intentionally do not inherit raw arc output, rejected edges,
    profile-normalization diagnostics, or the prior shared anchor.
    """

    target_set = set(target_names)
    relevant_names = set(target_names)
    focused_evidence = [
        dict(item)
        for item in candidate_evidence
        if isinstance(item, dict)
        and (
            str(item.get("source") or "").strip() in target_set
            or str(item.get("target") or "").strip() in target_set
        )
    ]
    for item in focused_evidence:
        relevant_names.add(str(item.get("source") or "").strip())
        relevant_names.add(str(item.get("target") or "").strip())
    profile_projection = [
        {key: item[key] for key in _RELATIONSHIP_PROFILE_FIELDS if key in item}
        for item in profiles
        if isinstance(item, dict) and str(item.get("name") or "").strip() in relevant_names
    ]
    arc_projection = [
        dict(item)
        for item in character_arcs
        if str(item.get("name") or "").strip() in relevant_names
    ]
    target_roster = [
        dict(item)
        for item in roster
        if isinstance(item, dict) and str(item.get("name") or "").strip() in target_set
    ]
    story_projection = {
        key: story_payload[key]
        for key in (
            "premise",
            "tone",
            "social_hierarchy",
            "address_rules",
            "self_reference_rules",
        )
        if key in story_payload
    }
    context = {
        "premise": base_ctx.get("premise", story_payload.get("premise", "")),
        "story_bible": story_projection,
        "character_generation_mode": _CHARACTER_GENERATION_MODE_SPLIT,
        "relationship_generation_phase": "repair",
        "relationship_generation_attempt": attempt,
        "character_roster": roster,
        "target_character_roster": target_roster,
        "character_profiles": profile_projection,
        "character_arcs": arc_projection,
        "relationship_seed_matrix": [],
        "relationship_candidate_evidence": focused_evidence,
        "previous_relationship_matrix": previous_relationship_matrix,
        "relationship_retry_reasons": retry_reasons,
    }
    context["shared_evidence_anchor"] = build_shared_evidence_anchor(
        f"init_character_bible.relationship_repair.{attempt}",
        {
            "character_roster": roster,
            "target_character_roster": target_roster,
            "character_profiles": profile_projection,
            "character_arcs": arc_projection,
            "relationship_candidate_evidence": focused_evidence,
            "previous_relationship_matrix": previous_relationship_matrix,
        },
        max_string_chars=900,
    )
    return context


def _record_character_relationship_failure(ctx: Any, payload: dict[str, Any]) -> None:
    ctx.storage.save_json(
        ctx.layout.states_dir / "init_v2" / "character_relationship_integrity_failure.json",
        payload,
    )
    on_step = getattr(ctx, "on_step", None)
    if callable(on_step):
        on_step(str(payload.get("code") or "character_relationship_integrity_failed"), payload)


def _compact_relationship_snippet(text: str, target_name: str, *, limit: int = 160) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    index = compact.find(target_name)
    if index < 0:
        return compact[:limit]
    start = max(0, index - limit // 3)
    end = min(len(compact), index + len(target_name) + limit * 2 // 3)
    return compact[start:end]


def _build_relationship_candidate_evidence(
    profiles: list[Any],
    roster: list[Any],
    *,
    limit_per_character: int = 8,
) -> list[dict[str, Any]]:
    """Find roster-name co-occurrence hints for the final relationship pass."""
    roster_names = [
        str(item.get("name") or "").strip()
        for item in roster
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    fields = ("social_status", "abilities", "appearance", "personality", "backstory", "arc", "notes")
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        source_name = str(profile.get("name") or "").strip()
        if not source_name:
            continue
        count = 0
        for target_name in roster_names:
            if target_name == source_name:
                continue
            for field in fields:
                text = str(profile.get(field) or "")
                if not text or target_name not in text:
                    continue
                key = (source_name, target_name, field)
                if key in seen:
                    continue
                seen.add(key)
                evidence.append(
                    {
                        "source": source_name,
                        "target": target_name,
                        "field": field,
                        "snippet": _compact_relationship_snippet(text, target_name),
                    }
                )
                count += 1
                break
            if count >= limit_per_character:
                break
    return evidence


def _project_relationship_matrix_into_profiles(
    profiles: dict[str, dict[str, Any]],
    relationship_matrix: list[dict[str, Any]],
) -> None:
    """Make the final matrix the only source of profile relationship maps."""
    for profile in profiles.values():
        profile["relationships"] = {}
    roster_names = set(profiles)
    for rel_item in relationship_matrix:
        if not isinstance(rel_item, dict):
            continue
        a = str(rel_item.get("character_a", "") or "").strip()
        b = str(rel_item.get("character_b", "") or "").strip()
        desc = str(rel_item.get("description", "") or "").strip()
        if not a or not b or not desc or a == b:
            continue
        if a not in roster_names or b not in roster_names:
            continue
        profiles.setdefault(a, {"name": a}).setdefault("relationships", {})[b] = desc
        profiles.setdefault(b, {"name": b}).setdefault("relationships", {})[a] = desc


def _relationship_matrix_connected_names(relationship_matrix: list[dict[str, Any]]) -> set[str]:
    connected: set[str] = set()
    for item in relationship_matrix:
        if not isinstance(item, dict):
            continue
        a = str(item.get("character_a") or "").strip()
        b = str(item.get("character_b") or "").strip()
        desc = str(item.get("description") or "").strip()
        if a and b and a != b and desc:
            connected.update((a, b))
    return connected


def _merge_relationship_matrix_items(
    base: list[dict[str, Any]],
    additions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge focused repairs without discarding already validated relationship edges."""

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [*base, *additions]:
        if not isinstance(item, dict):
            continue
        a = str(item.get("character_a") or "").strip()
        b = str(item.get("character_b") or "").strip()
        if not a or not b or a == b:
            continue
        merged[tuple(sorted((a, b)))] = dict(item)
    return list(merged.values())


def _isolated_key_relationship_characters(
    relationship_matrix: list[dict[str, Any]],
    roster: list[Any],
    profiles: list[Any],
) -> list[str]:
    connected = _relationship_matrix_connected_names(relationship_matrix)
    profile_by_name = {
        str(item.get("name") or "").strip(): dict(item)
        for item in profiles
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    isolated: list[str] = []
    for item in roster:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in connected:
            continue
        role = str(item.get("role") or "").strip()
        status = str(item.get("status") or "active").strip()
        if role not in {"protagonist", "deuteragonist", "antagonist", "supporting"}:
            continue
        if status == "retired":
            continue
        profile = profile_by_name.get(name, {})
        evidence_text = " ".join(
            str(profile.get(key) or "")
            for key in ("backstory", "arc", "notes", "social_status")
        )
        if role in {"protagonist", "deuteragonist", "antagonist"} or evidence_text.strip():
            isolated.append(name)
    return isolated


async def build_character_bible_split(
    ctx: Any,
    *,
    base_ctx: dict[str, Any],
    story_bible: StoryBible,
    language: str,
) -> Any:
    """Generate CharacterBible fragments anchored by a stable roster."""
    max_parallel = int(getattr(ctx.settings, "init_fragment_max_parallel", 4) or 4)
    char_dir = _fragment_dir(ctx, "character_bible")
    story_payload = dump_story_bible_for_prompt(story_bible, mode="json")
    roster_ctx = {**base_ctx, "story_bible": story_payload}
    roster_ctx["character_generation_mode"] = _CHARACTER_GENERATION_MODE_SPLIT
    roster_ctx["shared_evidence_anchor"] = build_shared_evidence_anchor(
        "init_character_bible.story",
        roster_ctx,
        source_keys=("premise", "genre", "tone", "story_bible"),
        max_string_chars=900,
    )

    async def call_json(
        task_type: TaskType,
        context: dict[str, Any],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        max_retries: int,
    ) -> dict[str, Any]:
        return await _call_ctx_json(
            ctx,
            task_type,
            context,
            max_tokens,
            temperature,
            required_keys,
            max_retries,
        )

    roster_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=char_dir / "character_roster.checkpoint.json",
        artifact_name="character_roster",
    )
    roster_payload = (
        await roster_runner.run(
            [
                SplitJsonFragment(
                    name="roster",
                    task_type=TaskType.INIT_CHARACTER_ROSTER,
                    context=roster_ctx,
                    required_keys=("character_roster",),
                    max_tokens=_tokens(ctx, TaskType.INIT_CHARACTER_ROSTER, 2600),
                    temperature=getattr(ctx.settings, "temp_init_character_bible", 0.7),
                )
            ]
        )
    ).fragments["roster"]
    roster = _list(roster_payload.get("character_roster"))
    roster, roster_name_normalizations = _dedupe_character_roster_names(roster)
    ctx.storage.save_json(
        ctx.layout.states_dir / "init_v2" / "character_roster_normalization.json",
        {
            "generation_mode": _CHARACTER_GENERATION_MODE_SPLIT,
            "duplicate_name_normalizations": roster_name_normalizations,
        },
    )
    anchored_ctx = {
        **roster_ctx,
        "character_roster": roster,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "init_character_bible.roster",
            {**roster_ctx, "character_roster": roster},
            source_keys=("premise", "story_bible", "character_roster"),
            max_string_chars=900,
        ),
    }

    profile_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=max_parallel,
        checkpoint_path=char_dir / "character_profiles.checkpoint.json",
        artifact_name="character_profiles",
    )
    profile_parallel_min_roster, profile_batch_size = _character_profile_batching_settings(
        ctx.settings
    )
    profile_batches = _profile_roster_batches(
        roster,
        parallel_min_roster=profile_parallel_min_roster,
        batch_size=profile_batch_size,
    )
    profile_fragments = [
        SplitJsonFragment(
            name=_profile_fragment_name(index, len(profile_batches)),
            task_type=TaskType.INIT_CHARACTER_PROFILE_BATCH,
            context={
                **anchored_ctx,
                "target_character_roster": batch,
                "profile_batch_index": index + 1,
                "profile_batch_count": len(profile_batches),
            },
            required_keys=("character_profiles",),
            max_tokens=_tokens(
                ctx,
                TaskType.INIT_CHARACTER_PROFILE_BATCH,
                _profile_batch_target_chars(
                    len(batch),
                    total_roster=len(roster),
                    parallel_min_roster=profile_parallel_min_roster,
                ),
                minimum=4096,
            ),
            temperature=getattr(ctx.settings, "temp_init_character_bible", 0.7),
            cache_version=CHARACTER_FRAGMENT_CONTRACT_VERSION,
        )
        for index, batch in enumerate(profile_batches)
    ]
    profile_payload = (await profile_runner.run(profile_fragments)).fragments
    profile_items: list[Any] = []
    for fragment in profile_fragments:
        profile_items.extend(
            _list(profile_payload.get(fragment.name, {}).get("character_profiles"))
        )

    profiles, profile_aliases, unmatched_profiles = _profiles_by_roster(
        profile_items,
        roster,
    )
    normalization_payload = {
        "batching": {
            "parallel_min_roster": profile_parallel_min_roster,
            "batch_size": profile_batch_size,
            "max_parallel": max_parallel,
        },
        "profile_batches": [
            {
                "name": fragment.name,
                "target_names": [
                    str(item.get("name") or "").strip()
                    for item in _list(fragment.context.get("target_character_roster"))
                    if isinstance(item, dict)
                ],
            }
            for fragment in profile_fragments
        ],
        "aliases": profile_aliases,
        "unmatched_profiles": unmatched_profiles,
    }
    ctx.storage.save_json(
        ctx.layout.states_dir / "init_v2" / "character_profile_normalization.json",
        normalization_payload,
    )

    for item in roster:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        profile = profiles.setdefault(name, {})
        for key in ("name", "role", "age", "gender", "status", "time_layer"):
            if key not in profile and item.get(key) not in (None, ""):
                profile[key] = item.get(key)
        if item.get("function") and not profile.get("notes"):
            profile["notes"] = str(item.get("function"))
        _rewrite_profile_aliases_in_place(profile, profile_aliases)

    profile_list = list(profiles.values())
    seed_candidate_evidence = _build_relationship_candidate_evidence(profile_list, roster)
    relationship_seed_ctx = {
        **anchored_ctx,
        "relationship_generation_phase": "seed",
        "character_profiles": profile_list,
        "relationship_candidate_evidence": seed_candidate_evidence,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "init_character_bible.relationship_seed",
            {
                "story_bible": story_payload,
                "character_roster": roster,
                "character_profiles": profile_list,
                "relationship_candidate_evidence": seed_candidate_evidence,
            },
            max_string_chars=900,
        ),
    }
    relationship_seed_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=char_dir / "character_relationship_seed.checkpoint.json",
        artifact_name="character_relationship_seed",
    )
    relationship_seed_payload = (
        await relationship_seed_runner.run(
            [
                SplitJsonFragment(
                    name="relationship_seed",
                    task_type=TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                    context=relationship_seed_ctx,
                    required_keys=("relationship_matrix",),
                    max_tokens=_tokens(ctx, TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX, 2800),
                    temperature=getattr(ctx.settings, "temp_init_character_bible", 0.7),
                    cache_version=CHARACTER_FRAGMENT_CONTRACT_VERSION,
                )
            ]
        )
    ).fragments["relationship_seed"]
    relationship_seed_result = canonicalize_relationship_items(
        _list(relationship_seed_payload.get("relationship_matrix")),
        roster=roster,
        aliases=profile_aliases,
    )
    relationship_seed_matrix = relationship_seed_result.accepted
    relationship_seed_rejected = relationship_seed_result.rejected
    relationship_seed_contaminated = relationship_seed_result.contaminated
    ctx.storage.save_json(
        ctx.layout.states_dir / "init_v2" / "character_relationship_seed_matrix.json",
        {
            "prompt_version": CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
            "generation_mode": _CHARACTER_GENERATION_MODE_SPLIT,
            "relationship_generation_phase": "seed",
            "story_bible_hash": hash_payload(story_bible),
            "relationship_matrix": relationship_seed_matrix,
            "rejected_relationships": relationship_seed_rejected,
            "contaminated_relationships": relationship_seed_contaminated,
            "relationship_candidate_evidence": seed_candidate_evidence,
        },
    )

    arc_ctx = {
        **anchored_ctx,
        "character_profiles": list(profiles.values()),
        "relationship_seed_matrix": relationship_seed_matrix,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "init_character_bible.character_arcs",
            {
                "story_bible": story_payload,
                "character_roster": roster,
                "character_profiles": list(profiles.values()),
                "relationship_seed_matrix": relationship_seed_matrix,
            },
            max_string_chars=900,
        ),
    }
    arc_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=char_dir / "character_arcs.checkpoint.json",
        artifact_name="character_arcs",
    )
    arc_payload = (
        await arc_runner.run(
            [
                SplitJsonFragment(
                    name="arcs",
                    task_type=TaskType.INIT_CHARACTER_ARC_PLAN,
                    context=arc_ctx,
                    required_keys=("character_arcs",),
                    max_tokens=_tokens(ctx, TaskType.INIT_CHARACTER_ARC_PLAN, 2600),
                    temperature=getattr(ctx.settings, "temp_init_character_bible", 0.7),
                    cache_version=CHARACTER_FRAGMENT_CONTRACT_VERSION,
                )
            ]
        )
    ).fragments["arcs"]

    character_arc_result = canonicalize_character_arc_items(
        _list(arc_payload.get("character_arcs")),
        roster=roster,
        aliases=profile_aliases,
    )
    canonical_character_arcs = character_arc_result.accepted
    rejected_character_arcs = character_arc_result.rejected
    ctx.storage.save_json(
        ctx.layout.states_dir / "init_v2" / "character_arc_normalization.json",
        {
            "contract_version": CHARACTER_FRAGMENT_CONTRACT_VERSION,
            "character_arcs": canonical_character_arcs,
            "rejected_character_arcs": rejected_character_arcs,
        },
    )
    for arc_item in canonical_character_arcs:
        _merge_character_arc_item(profiles, arc_item)
    for profile in profiles.values():
        _rewrite_profile_aliases_in_place(profile, profile_aliases)

    profile_list = list(profiles.values())
    final_candidate_evidence = _build_relationship_candidate_evidence(profile_list, roster)
    final_relationship_ctx = {
        **anchored_ctx,
        "relationship_generation_phase": "final",
        "character_profiles": profile_list,
        "character_arcs": canonical_character_arcs,
        "relationship_seed_matrix": relationship_seed_matrix,
        "relationship_candidate_evidence": final_candidate_evidence,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "init_character_bible.relationship_final",
            {
                "story_bible": story_payload,
                "character_roster": roster,
                "character_profiles": profile_list,
                "character_arcs": canonical_character_arcs,
                "relationship_seed_matrix": relationship_seed_matrix,
                "relationship_candidate_evidence": final_candidate_evidence,
            },
            max_string_chars=900,
        ),
    }
    final_relationship_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=char_dir / "character_relationship_final.checkpoint.json",
        artifact_name="character_relationship_final",
    )
    final_relationship_payload = (
        await final_relationship_runner.run(
            [
                SplitJsonFragment(
                    name="relationship_final",
                    task_type=TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                    context=final_relationship_ctx,
                    required_keys=("relationship_matrix",),
                    max_tokens=_tokens(ctx, TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX, 3600),
                    temperature=getattr(ctx.settings, "temp_init_character_bible", 0.7),
                    cache_version=CHARACTER_FRAGMENT_CONTRACT_VERSION,
                )
            ]
        )
    ).fragments["relationship_final"]
    relationship_result = canonicalize_relationship_items(
        _list(final_relationship_payload.get("relationship_matrix")),
        roster=roster,
        aliases=profile_aliases,
    )
    relationship_matrix = relationship_result.accepted
    relationship_rejected = relationship_result.rejected
    relationship_contaminated = relationship_result.contaminated
    isolated_names = _isolated_key_relationship_characters(
        relationship_matrix,
        roster,
        profile_list,
    )
    relationship_repair_history: list[dict[str, Any]] = []
    if isolated_names or relationship_rejected:
        roster_name_set = {
            str(item.get("name") or "").strip()
            for item in roster
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        repair_target_names = list(isolated_names)
        for rejected in relationship_rejected:
            rejected_item = _dict(rejected.get("item"))
            for key in ("character_a", "character_b"):
                candidate = str(rejected_item.get(key) or "").strip()
                if candidate in roster_name_set and candidate not in repair_target_names:
                    repair_target_names.append(candidate)
        retry_reasons = [
            {
                "code": "invalid_character_reference",
                "message": (
                    "上一版包含 roster 外端点，原始错名已从修复上下文隔离。"
                    "只能依据规范名单与档案证据补边；不得重写已验证关系。"
                ),
            }
            for _item in relationship_rejected
        ]
        retry_reasons.extend(
            {
                "code": "isolated_active_character",
                "character_name": name,
                "message": "关键活跃人物在关系矩阵中没有任何人物边；若档案或弧线存在互动证据，请补入叙事有效关系。",
            }
            for name in isolated_names
        )
        retry_ctx = _relationship_repair_context(
            base_ctx=base_ctx,
            story_payload=story_payload,
            roster=roster,
            profiles=profile_list,
            character_arcs=canonical_character_arcs,
            target_names=repair_target_names,
            candidate_evidence=final_candidate_evidence,
            previous_relationship_matrix=relationship_matrix,
            retry_reasons=retry_reasons,
            attempt=2,
        )
        retry_runner = SplitArtifactRunner(
            call_json=call_json,
            max_parallel=1,
            checkpoint_path=char_dir / "character_relationship_final_retry.checkpoint.json",
            artifact_name="character_relationship_final_retry",
        )
        try:
            retry_payload = (
                await retry_runner.run(
                    [
                        SplitJsonFragment(
                            name="relationship_final_retry",
                            task_type=TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                            context=retry_ctx,
                            required_keys=("relationship_matrix",),
                            max_tokens=_tokens(
                                ctx,
                                TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                                3600,
                            ),
                            temperature=getattr(
                                ctx.settings,
                                "temp_init_character_bible",
                                0.7,
                            ),
                            cache_version=CHARACTER_FRAGMENT_CONTRACT_VERSION,
                        )
                    ]
                )
            ).fragments["relationship_final_retry"]
        except Exception as exc:
            failure = {
                "code": "character_relationship_repair_failed",
                "attempt": 2,
                "target_characters": repair_target_names,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "fallback": "abort_before_downstream_projection",
                "contract_version": CHARACTER_FRAGMENT_CONTRACT_VERSION,
            }
            _record_character_relationship_failure(ctx, failure)
            raise RuntimeError(
                "Character relationship repair failed before downstream projection"
            ) from exc
        retry_result = canonicalize_relationship_items(
            _list(retry_payload.get("relationship_matrix")),
            roster=roster,
            aliases=profile_aliases,
        )
        retried_matrix = retry_result.accepted
        retry_rejected = retry_result.rejected
        retry_contaminated = retry_result.contaminated
        relationship_rejected.extend(retry_rejected)
        base_pairs = {
            tuple(
                sorted(
                    (
                        str(item.get("character_a") or "").strip(),
                        str(item.get("character_b") or "").strip(),
                    )
                )
            )
            for item in relationship_matrix
            if isinstance(item, dict)
        }
        focused_additions = [
            item
            for item in retried_matrix
            if tuple(
                sorted(
                    (
                        str(item.get("character_a") or "").strip(),
                        str(item.get("character_b") or "").strip(),
                    )
                )
            )
            not in base_pairs
        ]
        if retried_matrix:
            relationship_matrix = _merge_relationship_matrix_items(
                relationship_matrix,
                focused_additions,
            )
        relationship_repair_history.append(
            {
                "attempt": 2,
                "target_characters": repair_target_names,
                "accepted_relationships": focused_additions,
                "rejected_relationships": retry_rejected,
                "contaminated_relationships": retry_contaminated,
            }
        )

    isolated_names = _isolated_key_relationship_characters(
        relationship_matrix,
        roster,
        profile_list,
    )
    if isolated_names:
        focused_ctx = _relationship_repair_context(
            base_ctx=base_ctx,
            story_payload=story_payload,
            roster=roster,
            profiles=profile_list,
            character_arcs=canonical_character_arcs,
            target_names=isolated_names,
            candidate_evidence=final_candidate_evidence,
            previous_relationship_matrix=relationship_matrix,
            retry_reasons=[
                {
                    "code": "isolated_active_character",
                    "character_name": name,
                    "message": (
                        "该 canonical 角色在两次全图生成后仍无关系边。"
                        "本轮只补与其直接相关、且有档案证据的关系。"
                    ),
                }
                for name in isolated_names
            ],
            attempt=3,
        )
        focused_runner = SplitArtifactRunner(
            call_json=call_json,
            max_parallel=1,
            checkpoint_path=char_dir / "character_relationship_focused_repair.checkpoint.json",
            artifact_name="character_relationship_focused_repair",
        )
        try:
            focused_payload = (
                await focused_runner.run(
                    [
                        SplitJsonFragment(
                            name="relationship_focused_repair",
                            task_type=TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                            context=focused_ctx,
                            required_keys=("relationship_matrix",),
                            max_tokens=_tokens(
                                ctx,
                                TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                                2400,
                            ),
                            temperature=getattr(
                                ctx.settings,
                                "temp_init_character_bible",
                                0.7,
                            ),
                            cache_version=CHARACTER_FRAGMENT_CONTRACT_VERSION,
                        )
                    ]
                )
            ).fragments["relationship_focused_repair"]
        except Exception as exc:
            failure = {
                "code": "character_relationship_repair_failed",
                "attempt": 3,
                "target_characters": isolated_names,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "fallback": "abort_before_downstream_projection",
                "contract_version": CHARACTER_FRAGMENT_CONTRACT_VERSION,
            }
            _record_character_relationship_failure(ctx, failure)
            raise RuntimeError(
                "Character relationship focused repair failed before downstream projection"
            ) from exc
        focused_result = canonicalize_relationship_items(
            _list(focused_payload.get("relationship_matrix")),
            roster=roster,
            aliases=profile_aliases,
        )
        focused_matrix = focused_result.accepted
        focused_rejected = focused_result.rejected
        focused_contaminated = focused_result.contaminated
        relationship_rejected.extend(focused_rejected)
        focused_target_names = set(isolated_names)
        focused_additions = [
            item
            for item in focused_matrix
            if str(item.get("character_a") or "").strip() in focused_target_names
            or str(item.get("character_b") or "").strip() in focused_target_names
        ]
        relationship_matrix = _merge_relationship_matrix_items(
            relationship_matrix,
            focused_additions,
        )
        relationship_repair_history.append(
            {
                "attempt": 3,
                "target_characters": isolated_names,
                "accepted_relationships": focused_additions,
                "rejected_relationships": focused_rejected,
                "contaminated_relationships": focused_contaminated,
            }
        )
        isolated_names = _isolated_key_relationship_characters(
            relationship_matrix,
            roster,
            profile_list,
        )
    if isolated_names:
        integrity_failure = {
            "code": "character_relationship_integrity_exhausted",
            "isolated_characters": isolated_names,
            "canonical_roster": [
                str(item.get("name") or "").strip()
                for item in roster
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ],
            "accepted_relationships": relationship_matrix,
            "rejected_relationships": relationship_rejected,
            "contaminated_relationships": relationship_contaminated,
            "repair_history": relationship_repair_history,
            "contract_version": CHARACTER_FRAGMENT_CONTRACT_VERSION,
        }
        _record_character_relationship_failure(ctx, integrity_failure)
        raise ValueError(
            "Character relationship integrity failed; active characters remain isolated: "
            + ", ".join(isolated_names)
        )

    _project_relationship_matrix_into_profiles(profiles, relationship_matrix)

    payload = normalize_payload_for_language(
        {"characters": list(profiles.values())},
        language,
    )
    character_bible = ctx.coerce_character_bible(payload)
    ctx.storage.save_json(
        ctx.layout.states_dir / "init_v2" / "character_relationship_matrix.json",
        {
            "prompt_version": CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
            "generation_mode": _CHARACTER_GENERATION_MODE_SPLIT,
            "relationship_generation_phase": "final",
            "story_bible_hash": hash_payload(story_bible),
            "character_bible_hash": hash_payload(character_bible),
            "relationship_matrix": relationship_matrix,
            "rejected_relationships": relationship_rejected,
            "contaminated_relationships": relationship_contaminated,
            "relationship_seed_matrix_hash": hash_payload(relationship_seed_matrix),
            "relationship_candidate_evidence": final_candidate_evidence,
            "relationship_repair_history": relationship_repair_history,
        },
    )
    return character_bible


async def refine_coherence_profile_split(
    ctx: Any,
    *,
    current_profile: dict[str, Any],
    spec: dict[str, Any],
    story_bible: dict[str, Any],
    character_bible: dict[str, Any],
    creative_director_packet: dict[str, Any] | None,
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    """Refine InitCoherenceProfile through anchored fragments."""
    base_ctx = {
        "current_profile": current_profile,
        "spec": spec,
        "story_bible": story_bible,
        "character_bible": character_bible,
        "creative_director_packet": creative_director_packet or {},
        "blueprint": blueprint,
    }
    base_anchor = build_shared_evidence_anchor(
        "refine_init_coherence_profile.base",
        base_ctx,
        source_keys=("spec", "story_bible", "character_bible", "blueprint"),
        max_string_chars=900,
    )

    async def call_json(
        task_type: TaskType,
        context: dict[str, Any],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...],
        max_retries: int,
    ) -> dict[str, Any]:
        return await _call_ctx_json(
            ctx,
            task_type,
            context,
            max_tokens,
            temperature,
            required_keys,
            max_retries,
        )

    ontology_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=_fragment_dir(ctx, "coherence_profile") / "ontology.checkpoint.json",
        artifact_name="refine_init_coherence_ontology",
    )
    ontology_payload = (
        await ontology_runner.run(
            [
                SplitJsonFragment(
                    name="ontology",
                    task_type=TaskType.INIT_COHERENCE_ONTOLOGY,
                    context={**base_ctx, "shared_evidence_anchor": base_anchor},
                    required_keys=("genre_tags", "narrative_modes", "project_ontology"),
                    max_tokens=_tokens(ctx, TaskType.INIT_COHERENCE_ONTOLOGY, 2200),
                    temperature=getattr(ctx.settings, "temp_refine_init_coherence_profile", 0.1),
                ),
            ]
        )
    ).fragments["ontology"]
    ontology = _normalize_coherence_ontology_payload(ontology_payload.get("project_ontology"))

    payoff_ctx = {
        **base_ctx,
        "coherence_ontology": ontology_payload,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "refine_init_coherence_profile.ontology",
            {**base_ctx, "coherence_ontology": ontology_payload},
            source_keys=("spec", "story_bible", "blueprint", "coherence_ontology"),
            max_string_chars=900,
        ),
    }
    payoff_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=_fragment_dir(ctx, "coherence_profile") / "payoff.checkpoint.json",
        artifact_name="refine_init_coherence_payoff",
    )
    payoff_payload = (
        await payoff_runner.run(
            [
                SplitJsonFragment(
                    name="payoff",
                    task_type=TaskType.INIT_COHERENCE_PAYOFF_RULES,
                    context=payoff_ctx,
                    required_keys=("payoff_types", "summary"),
                    max_tokens=_tokens(ctx, TaskType.INIT_COHERENCE_PAYOFF_RULES, 1600),
                    temperature=getattr(ctx.settings, "temp_refine_init_coherence_profile", 0.1),
                ),
            ]
        )
    ).fragments["payoff"]

    payoff_dict = _dict(payoff_payload)
    payoff_key_hints: dict[str, tuple[str, ...]] = {
        "payoff_types": ("payoff_type", "type", "category"),
        "irreversible_event_markers": ("marker", "event_type"),
        "temporal_markers": ("marker", "temporal_marker"),
    }
    for key, preferred in payoff_key_hints.items():
        values = _string_list(payoff_dict.get(key), preferred_keys=preferred)
        if values:
            ontology[key] = values

    conflict_ctx = {
        **base_ctx,
        "coherence_ontology": ontology_payload,
        "payoff_rules": payoff_payload,
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "refine_init_coherence_profile.payoff",
            {
                "coherence_ontology": ontology_payload,
                "payoff_rules": payoff_payload,
                "blueprint": blueprint,
            },
            max_string_chars=900,
        ),
    }
    conflict_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=_fragment_dir(ctx, "coherence_profile") / "conflict.checkpoint.json",
        artifact_name="refine_init_coherence_conflict",
    )
    conflict_payload = (
        await conflict_runner.run(
            [
                SplitJsonFragment(
                    name="conflict",
                    task_type=TaskType.INIT_COHERENCE_CONFLICT_RULES,
                    context=conflict_ctx,
                    required_keys=("conflict_lens",),
                    max_tokens=_tokens(ctx, TaskType.INIT_COHERENCE_CONFLICT_RULES, 1600),
                    temperature=getattr(ctx.settings, "temp_refine_init_coherence_profile", 0.1),
                )
            ]
        )
    ).fragments["conflict"]

    extraction_ctx = {
        "current_profile": {
            key: current_profile.get(key)
            for key in ("genre_tags", "narrative_modes", "summary")
            if key in current_profile
        },
        "project_ontology": ontology,
        "coherence_ontology": ontology_payload,
        "payoff_rules": payoff_payload,
        "conflict_lens": _list(conflict_payload.get("conflict_lens")),
        "canonical_character_roster": _canonical_character_roster(character_bible),
        "shared_evidence_anchor": build_shared_evidence_anchor(
            "refine_init_coherence_profile.conflict",
            {
                "project_ontology": ontology,
                "payoff_rules": payoff_payload,
                "conflict_lens": _list(conflict_payload.get("conflict_lens")),
            },
            max_string_chars=900,
        ),
    }
    extraction_runner = SplitArtifactRunner(
        call_json=call_json,
        max_parallel=1,
        checkpoint_path=_fragment_dir(ctx, "coherence_profile") / "extraction.checkpoint.json",
        artifact_name="refine_init_coherence_extraction",
    )
    extraction_payload = (
        await extraction_runner.run(
            [
                SplitJsonFragment(
                    name="extraction",
                    task_type=TaskType.INIT_COHERENCE_EXTRACTION_GUIDE,
                    context=extraction_ctx,
                    required_keys=("extraction_guidance",),
                    max_tokens=_tokens(ctx, TaskType.INIT_COHERENCE_EXTRACTION_GUIDE, 1600),
                    temperature=getattr(ctx.settings, "temp_refine_init_coherence_profile", 0.1),
                )
            ]
        )
    ).fragments["extraction"]

    profile_payload = {
        "genre_tags": _string_list(ontology_payload.get("genre_tags")),
        "narrative_modes": _string_list(ontology_payload.get("narrative_modes")),
        "project_ontology": ontology,
        "conflict_lens": _string_list(
            conflict_payload.get("conflict_lens"),
            preferred_keys=("conflict_type", "conflict_id", "dimension"),
        ),
        "extraction_guidance": _string_list(
            extraction_payload.get("extraction_guidance"),
            preferred_keys=("dimension", "guidance", "rule", "judgment_rule"),
        ),
        "summary": str(payoff_dict.get("summary", "") or "").strip()
        or "初始化一致性画像已按蓝图分片精炼。",
    }
    profile = InitCoherenceProfile.model_validate(profile_payload)
    result = profile.model_dump(mode="json")
    result["refined_from_blueprint"] = True
    return result
