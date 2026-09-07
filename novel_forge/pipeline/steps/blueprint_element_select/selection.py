"""Selection logic, preference handling, and heuristic fallback for blueprint elements."""

from __future__ import annotations

from typing import Any, Literal

from novel_forge.core.schemas.blueprint_elements import (
    BlueprintElementCard,
    BlueprintElementPreferenceConfig,
    BlueprintElementPreferenceItem,
    BlueprintElementSelection,
)
from novel_forge.core.schemas.spec import StorySpec

from .elements import (
    _GENRE_HEURISTIC_MAP,
    _GENRE_PRESET_BY_ID,
    _GENRE_PRESET_HINT_MAP,
    _LIBRARY_BY_ID,
    _QUALITY_CONFIG_IDS,
    get_element_library_version,
    get_extension_library_ids,
    get_required_core_ids,
)

_REQUIRED_CORE_IDS = get_required_core_ids()
_EXTENSION_LIBRARY_IDS = get_extension_library_ids()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def get_blueprint_element_cards(
    *, tier: Literal["required", "extension"] | None = None
) -> list[BlueprintElementCard]:
    cards = [item.to_card() for item in _LIBRARY_BY_ID.values()]
    if tier is None:
        return cards
    return [card for card in cards if card.tier == tier]


def get_blueprint_genre_presets() -> list[dict[str, Any]]:
    return [preset.to_payload() for preset in _GENRE_PRESET_BY_ID.values()]


def recommend_preset_for_genre(genre_text: str) -> str:
    haystack = _clean_text(genre_text).lower()
    if not haystack:
        return ""
    for keywords, preset_id in _GENRE_PRESET_HINT_MAP:
        if any(keyword.lower() in haystack for keyword in keywords):
            return preset_id
    return ""


def get_related_extension_ids_for_genre(
    genre_text: str,
    *,
    preset_id: str = "",
) -> list[str]:
    related: list[str] = []
    seen: set[str] = set()

    suggested_preset_id = _clean_text(preset_id) or recommend_preset_for_genre(genre_text)
    preset = _GENRE_PRESET_BY_ID.get(suggested_preset_id)
    if preset is not None:
        for element_id in preset.default_enabled:
            definition = _LIBRARY_BY_ID.get(element_id)
            if definition is None or definition.tier != "extension":
                continue
            if element_id in seen:
                continue
            seen.add(element_id)
            related.append(element_id)

    spec = StorySpec(theme="genre_context", genre=genre_text or "other", tone="neutral")
    heuristic_ids = _heuristic_extension_ids(spec, mode="long")
    for element_id in heuristic_ids:
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None or definition.tier != "extension":
            continue
        if element_id in seen:
            continue
        seen.add(element_id)
        related.append(element_id)
    return related


def has_manual_selector_preferences(preferences: dict[str, Any] | None) -> bool:
    config = _normalize_preferences(preferences)
    return bool(config.preset_id or config.manual_override or config.items)


def _extract_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for raw in value:
        text = _clean_text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _extract_selection_reasons(payload: dict[str, Any]) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for raw in (
        payload.get("extension_selection", [])
        if isinstance(payload.get("extension_selection"), list)
        else []
    ):
        if not isinstance(raw, dict):
            continue
        element_id = _clean_text(raw.get("element_id"))
        reason = _clean_text(raw.get("reason"))
        if element_id and reason:
            reasons[element_id] = reason
    return reasons


def _heuristic_entry_parts(
    entry: tuple[Any, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], float]:
    keywords = tuple(entry[0]) if len(entry) >= 1 else ()
    element_ids = tuple(entry[1]) if len(entry) >= 2 else ()
    try:
        weight = float(entry[2]) if len(entry) >= 3 else 1.0
    except (TypeError, ValueError):
        weight = 1.0
    return keywords, element_ids, max(0.0, weight)


def _keyword_match_count(keywords: tuple[str, ...], haystack: str) -> int:
    return sum(1 for keyword in keywords if keyword and keyword.lower() in haystack)


def _heuristic_extension_ids(spec: StorySpec, mode: Literal["short", "long"]) -> list[str]:
    haystack = " ".join(
        [
            spec.genre,
            spec.theme,
            spec.tone,
            spec.conflict_hint,
            spec.world_hint,
            spec.extra_instructions,
        ]
    ).lower()
    matched_entries: list[tuple[int, int, float, tuple[str, ...]]] = []
    for order, entry in enumerate(_GENRE_HEURISTIC_MAP):
        keywords, element_ids, weight = _heuristic_entry_parts(entry)
        match_count = _keyword_match_count(keywords, haystack)
        if match_count <= 0:
            continue
        matched_entries.append((order, match_count, weight, element_ids))
    if not matched_entries:
        return []

    primary_match_count = max(match_count for _, match_count, _, _ in matched_entries)
    element_scores: dict[str, tuple[float, int]] = {}
    for order, match_count, weight, element_ids in matched_entries:
        role_factor = 1.0 if match_count >= primary_match_count else 0.5
        entry_score = max(1.0, float(match_count)) * max(0.1, weight) * role_factor
        for local_rank, element_id in enumerate(element_ids):
            definition = _LIBRARY_BY_ID.get(element_id)
            if definition is None or definition.tier != "extension":
                continue
            score = entry_score - (local_rank * 0.01)
            previous = element_scores.get(element_id)
            if previous is None:
                element_scores[element_id] = (score, order)
            else:
                prev_score, prev_order = previous
                element_scores[element_id] = (prev_score + score, min(prev_order, order))

    library_rank = {element_id: idx for idx, element_id in enumerate(_EXTENSION_LIBRARY_IDS)}
    picked = [
        element_id
        for element_id, _ in sorted(
            element_scores.items(),
            key=lambda item: (
                -item[1][0],
                item[1][1],
                library_rank.get(item[0], 10_000),
                item[0],
            ),
        )
    ]
    max_count = 8 if mode == "long" else 6
    return picked[:max_count]


def _preference_lock_sets(
    preferences: BlueprintElementPreferenceConfig,
) -> tuple[set[str], set[str], set[str]]:
    locked_include: set[str] = set()
    locked_exclude: set[str] = set()
    enabled_include: set[str] = set()
    for item in preferences.items:
        if item.enabled is True:
            enabled_include.add(item.element_id)
        if not item.locked:
            continue
        if item.enabled is False:
            locked_exclude.add(item.element_id)
        else:
            locked_include.add(item.element_id)
    return locked_include, locked_exclude, enabled_include


def _element_conflicts(left_id: str, right_id: str) -> bool:
    left = _LIBRARY_BY_ID.get(left_id)
    right = _LIBRARY_BY_ID.get(right_id)
    if left is None or right is None:
        return False
    return right_id in set(left.excludes) or left_id in set(right.excludes)


def _apply_exclusion_rules(
    selected_ids: list[str],
    *,
    locked_include: set[str],
) -> list[str]:
    kept: list[str] = []
    for element_id in selected_ids:
        conflict_id = next((kept_id for kept_id in kept if _element_conflicts(element_id, kept_id)), "")
        if not conflict_id:
            kept.append(element_id)
            continue
        if element_id in locked_include and conflict_id not in locked_include:
            kept = [kept_id for kept_id in kept if kept_id != conflict_id]
            kept.append(element_id)
            continue
        if element_id in locked_include and conflict_id in locked_include:
            kept.append(element_id)
    return kept


def _insert_required_element(
    selected_ids: list[str],
    *,
    required_id: str,
    source_id: str,
    max_count: int,
    locked_include: set[str],
) -> list[str]:
    if required_id in selected_ids:
        return selected_ids
    required = _LIBRARY_BY_ID.get(required_id)
    if required is None or required.tier != "extension":
        return selected_ids

    insert_at = selected_ids.index(source_id) if source_id in selected_ids else len(selected_ids)
    next_ids = list(selected_ids)
    if len(next_ids) >= max_count:
        protected = set(locked_include) | {source_id}
        remove_at = -1
        for idx in range(len(next_ids) - 1, -1, -1):
            if next_ids[idx] not in protected:
                remove_at = idx
                break
        if remove_at < 0:
            return selected_ids
        del next_ids[remove_at]
        if remove_at < insert_at:
            insert_at -= 1
    next_ids.insert(max(0, min(insert_at, len(next_ids))), required_id)
    return list(dict.fromkeys(next_ids))


def _apply_required_rules(
    selected_ids: list[str],
    *,
    max_count: int,
    locked_include: set[str],
    locked_exclude: set[str],
) -> list[str]:
    next_ids = list(selected_ids)
    for element_id in list(next_ids):
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None:
            continue
        for required_id in definition.requires:
            if required_id in locked_exclude:
                continue
            next_ids = _insert_required_element(
                next_ids,
                required_id=required_id,
                source_id=element_id,
                max_count=max_count,
                locked_include=locked_include,
            )
    return next_ids


def _apply_complement_bonus(selected_ids: list[str]) -> list[str]:
    selected_set = set(selected_ids)
    original_rank = {element_id: idx for idx, element_id in enumerate(selected_ids)}
    bonus: dict[str, float] = {element_id: 0.0 for element_id in selected_ids}
    for element_id in selected_ids:
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None:
            continue
        for complement_id in definition.complements:
            if complement_id in selected_set:
                bonus[element_id] = bonus.get(element_id, 0.0) + 0.25
                bonus[complement_id] = bonus.get(complement_id, 0.0) + 0.25
    return sorted(
        selected_ids,
        key=lambda element_id: (-bonus.get(element_id, 0.0), original_rank.get(element_id, 10_000)),
    )


def _apply_relation_rules(
    selected_ids: list[str],
    *,
    mode: Literal["short", "long"],
    preferences: BlueprintElementPreferenceConfig,
) -> list[str]:
    max_count = _selector_max_extension_count(mode)
    locked_include, locked_exclude, _enabled_include = _preference_lock_sets(preferences)
    cleaned = [
        element_id
        for element_id in list(dict.fromkeys(selected_ids))
        if element_id not in locked_exclude
        and (definition := _LIBRARY_BY_ID.get(element_id)) is not None
        and definition.tier == "extension"
    ]
    cleaned = _apply_exclusion_rules(cleaned, locked_include=locked_include)
    cleaned = _apply_required_rules(
        cleaned,
        max_count=max_count,
        locked_include=locked_include,
        locked_exclude=locked_exclude,
    )
    cleaned = _apply_exclusion_rules(cleaned, locked_include=locked_include)
    cleaned = _apply_complement_bonus(cleaned)
    if len(cleaned) <= max_count:
        return cleaned
    locked_ranked = [element_id for element_id in cleaned if element_id in locked_include]
    rest = [element_id for element_id in cleaned if element_id not in locked_include]
    return (locked_ranked + rest)[:max_count]


def _selector_max_extension_count(mode: Literal["short", "long"]) -> int:
    return 8 if mode == "long" else 6


def _clamp_weight(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 50.0
    return max(0.0, min(100.0, number))


def _weight_factor(value: Any) -> float:
    return _clamp_weight(value) / 50.0


def _normalized_priority_score(
    *,
    element_id: str,
    base_rank: dict[str, int],
    selected_rank: dict[str, int],
    max_count: int,
    pref: BlueprintElementPreferenceItem | None,
    locked_include: set[str],
    enabled_include: set[str],
    preset_seeded: bool,
) -> float:
    """Build a stable downstream priority score while keeping old behavior intact."""

    score = 40.0
    if element_id in base_rank:
        score += 25.0
        score += max(0.0, 10.0 - base_rank[element_id])
    if preset_seeded:
        score += 8.0
    if element_id in enabled_include:
        score += 20.0
    if element_id in locked_include:
        score += 18.0
    if pref is not None:
        score += (_clamp_weight(pref.weight) - 50.0) * 0.30
    rank = selected_rank.get(element_id)
    if rank is not None:
        score += max(0.0, float(max_count - rank))
    return max(0.0, min(100.0, round(score, 2)))


def _selection_source_for(
    *,
    element_id: str,
    base_ids: list[str],
    pref: BlueprintElementPreferenceItem | None,
    locked_include: set[str],
    enabled_include: set[str],
    manual_override: bool,
    preset_seeded: bool,
    payload_had_signal: bool,
) -> str:
    if element_id in locked_include:
        return "user_locked"
    if element_id in enabled_include and manual_override:
        return "user_manual_override"
    if element_id in enabled_include:
        return "user_preference"
    if payload_had_signal and element_id in base_ids:
        return "llm_selector"
    if preset_seeded:
        return "genre_preset"
    if element_id in base_ids:
        return "heuristic_fallback"
    if pref is not None:
        return "weighted_candidate"
    return "selector"


def _normalize_preferences(raw: dict[str, Any] | None) -> BlueprintElementPreferenceConfig:
    if not isinstance(raw, dict):
        return BlueprintElementPreferenceConfig()
    try:
        config = BlueprintElementPreferenceConfig.model_validate(raw)
    except Exception:
        return BlueprintElementPreferenceConfig()

    items_by_id: dict[str, BlueprintElementPreferenceItem] = {}
    for item in config.items:
        element_id = _clean_text(item.element_id)
        definition = _LIBRARY_BY_ID.get(element_id)
        if not element_id or definition is None or definition.tier != "extension":
            continue
        items_by_id[element_id] = item.model_copy(
            update={
                "element_id": element_id,
                "weight": _clamp_weight(item.weight),
            }
        )

    preset_id = _clean_text(config.preset_id)
    preset = _GENRE_PRESET_BY_ID.get(preset_id)
    if preset is not None:
        for element_id, weight in preset.default_weights.items():
            definition = _LIBRARY_BY_ID.get(element_id)
            if definition is None or definition.tier != "extension":
                continue
            if element_id not in items_by_id:
                items_by_id[element_id] = BlueprintElementPreferenceItem(
                    element_id=element_id,
                    weight=_clamp_weight(weight),
                )
        for element_id in preset.default_enabled:
            definition = _LIBRARY_BY_ID.get(element_id)
            if definition is None or definition.tier != "extension":
                continue
            current = items_by_id.get(element_id)
            if current is None:
                items_by_id[element_id] = BlueprintElementPreferenceItem(
                    element_id=element_id,
                    enabled=True,
                    weight=_clamp_weight(preset.default_weights.get(element_id, 75.0)),
                )
            elif current.enabled is None:
                items_by_id[element_id] = current.model_copy(update={"enabled": True})

    return BlueprintElementPreferenceConfig(
        preset_id=preset_id,
        manual_override=bool(config.manual_override),
        items=list(items_by_id.values()),
    )


def _base_extension_ids_from_payload(
    payload: dict[str, Any],
    *,
    spec: StorySpec,
    mode: Literal["short", "long"],
) -> list[str]:
    extension_ids: list[str] = []
    seen_extension: set[str] = set()

    raw_required = _extract_str_list(payload.get("required_ids"))
    for element_id in raw_required:
        if element_id in _REQUIRED_CORE_IDS:
            continue
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None or definition.tier != "extension":
            continue
        if element_id in seen_extension:
            continue
        seen_extension.add(element_id)
        extension_ids.append(element_id)

    raw_extension = _extract_str_list(payload.get("extension_ids"))
    for element_id in raw_extension:
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None or definition.tier != "extension":
            continue
        if element_id in seen_extension:
            continue
        seen_extension.add(element_id)
        extension_ids.append(element_id)

    for item in (
        payload.get("extension_selection", [])
        if isinstance(payload.get("extension_selection"), list)
        else []
    ):
        if not isinstance(item, dict):
            continue
        element_id = _clean_text(item.get("element_id"))
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None or definition.tier != "extension":
            continue
        if element_id in seen_extension:
            continue
        seen_extension.add(element_id)
        extension_ids.append(element_id)

    if not extension_ids:
        extension_ids = _heuristic_extension_ids(spec, mode)
    return extension_ids


def _apply_preferences_to_extension_ids(
    base_ids: list[str],
    *,
    mode: Literal["short", "long"],
    preferences: BlueprintElementPreferenceConfig,
) -> list[str]:
    max_count = _selector_max_extension_count(mode)
    library_rank = {element_id: idx for idx, element_id in enumerate(_EXTENSION_LIBRARY_IDS)}
    base_rank = {element_id: idx for idx, element_id in enumerate(base_ids)}
    pref_map = {item.element_id: item for item in preferences.items}

    locked_include: set[str] = set()
    locked_exclude: set[str] = set()
    enabled_include: set[str] = set()
    for element_id, item in pref_map.items():
        if item.enabled is True:
            enabled_include.add(element_id)
        if not item.locked:
            continue
        if item.enabled is False:
            locked_exclude.add(element_id)
        else:
            locked_include.add(element_id)

    def score(element_id: str) -> float:
        item = pref_map.get(element_id)
        weight_value = _weight_factor(item.weight if item is not None else 50.0)
        enabled_bonus = 1.8 if element_id in enabled_include else 0.0
        locked_bonus = 2.5 if element_id in locked_include else 0.0
        base_bonus = 0.0
        if element_id in base_rank:
            base_bonus = max(0.0, 1.0 - (base_rank[element_id] / max(1, len(base_ids))))
        return locked_bonus + enabled_bonus + weight_value + base_bonus

    def _sorted_ids(ids: list[str]) -> list[str]:
        deduped = list(dict.fromkeys(ids))
        return sorted(
            deduped,
            key=lambda element_id: (
                -score(element_id),
                base_rank.get(element_id, 10_000),
                library_rank.get(element_id, 10_000),
                element_id,
            ),
        )

    if preferences.manual_override:
        manual_ids = [item.element_id for item in preferences.items if item.enabled is True]
        candidates = [element_id for element_id in manual_ids if element_id not in locked_exclude]
        candidates.extend(list(locked_include))
        ranked = _sorted_ids(candidates)
        if len(ranked) <= max_count:
            return ranked
        locked_ranked = _sorted_ids(
            [element_id for element_id in ranked if element_id in locked_include]
        )
        if len(locked_ranked) >= max_count:
            return locked_ranked[:max_count]
        rest = [element_id for element_id in ranked if element_id not in locked_include]
        return locked_ranked + rest[: max_count - len(locked_ranked)]

    selected: list[str] = [
        element_id for element_id in base_ids if element_id not in locked_exclude
    ]
    selected.extend(list(locked_include))
    selected.extend(list(enabled_include))
    selected = list(dict.fromkeys(selected))

    if len(selected) < max_count:
        weighted_candidates = [
            element_id
            for element_id in _EXTENSION_LIBRARY_IDS
            if element_id not in selected and element_id not in locked_exclude
        ]
        filtered_weighted_candidates: list[str] = []
        for element_id in weighted_candidates:
            pref_item = pref_map.get(element_id)
            weight = pref_item.weight if pref_item is not None else 50.0
            if element_id in enabled_include or _weight_factor(weight) > 1.05:
                filtered_weighted_candidates.append(element_id)
        weighted_candidates = filtered_weighted_candidates
        selected.extend(_sorted_ids(weighted_candidates))
        selected = list(dict.fromkeys(selected))

    ranked = _sorted_ids(
        [element_id for element_id in selected if element_id not in locked_exclude]
    )
    if len(ranked) <= max_count:
        return ranked
    locked_ranked = _sorted_ids(
        [element_id for element_id in ranked if element_id in locked_include]
    )
    if len(locked_ranked) >= max_count:
        return locked_ranked[:max_count]
    rest = [element_id for element_id in ranked if element_id not in locked_include]
    return locked_ranked + rest[: max_count - len(locked_ranked)]


def _normalize_selection(
    payload: dict[str, Any],
    *,
    spec: StorySpec,
    mode: Literal["short", "long"],
    preferences: dict[str, Any] | None = None,
) -> BlueprintElementSelection:
    required_ids: list[str] = list(_REQUIRED_CORE_IDS)
    payload_had_signal = bool(
        _extract_str_list(payload.get("required_ids"))
        or _extract_str_list(payload.get("extension_ids"))
        or (
            payload.get("extension_selection")
            if isinstance(payload.get("extension_selection"), list)
            else []
        )
    )
    base_extension_ids = _base_extension_ids_from_payload(payload, spec=spec, mode=mode)
    preference_config = _normalize_preferences(preferences)
    extension_ids = _apply_preferences_to_extension_ids(
        base_extension_ids,
        mode=mode,
        preferences=preference_config,
    )
    extension_ids = _apply_relation_rules(
        extension_ids,
        mode=mode,
        preferences=preference_config,
    )

    reason_map = _extract_selection_reasons(payload)
    preference_map = {item.element_id: item for item in preference_config.items}
    locked_include = {
        item.element_id
        for item in preference_config.items
        if item.locked and item.enabled is not False
    }
    enabled_include = {item.element_id for item in preference_config.items if item.enabled is True}
    preset_seeded: set[str] = set()
    preset = _GENRE_PRESET_BY_ID.get(preference_config.preset_id)
    if preset is not None:
        preset_seeded.update(preset.default_enabled)
        preset_seeded.update(preset.default_weights)
    base_rank = {element_id: idx for idx, element_id in enumerate(base_extension_ids)}
    selected_rank = {element_id: idx for idx, element_id in enumerate(extension_ids)}
    max_count = _selector_max_extension_count(mode)

    required_cards = [
        _LIBRARY_BY_ID[element_id].to_card(
            reason="通用叙事必要项",
            selection_source="core_required",
            selection_score=100.0,
        )
        for element_id in required_ids
    ]
    extension_cards: list[BlueprintElementCard] = []
    quality_config_cards: list[BlueprintElementCard] = []
    for element_id in extension_ids:
        definition = _LIBRARY_BY_ID.get(element_id)
        if definition is None or definition.tier != "extension":
            continue
        pref = preference_map.get(element_id)
        default_reason = f"匹配'{spec.genre or '当前题材'}'的常见叙事需求。"
        reason = _clean_text(reason_map.get(element_id))
        if not reason:
            if element_id in locked_include:
                reason = "用户手动锁定保留，覆盖自动选择结果。"
            elif pref is not None and pref.enabled is True and preference_config.manual_override:
                reason = "用户手动勾选保留（手动覆盖模式）。"
            elif pref is not None and pref.enabled is True:
                reason = "用户手动勾选补充。"
        if not reason:
            reason = default_reason
        card = definition.to_card(
            reason=reason,
            selection_source=_selection_source_for(
                element_id=element_id,
                base_ids=base_extension_ids,
                pref=pref,
                locked_include=locked_include,
                enabled_include=enabled_include,
                manual_override=preference_config.manual_override,
                preset_seeded=element_id in preset_seeded,
                payload_had_signal=payload_had_signal,
            ),
            selection_score=_normalized_priority_score(
                element_id=element_id,
                base_rank=base_rank,
                selected_rank=selected_rank,
                max_count=max_count,
                pref=pref,
                locked_include=locked_include,
                enabled_include=enabled_include,
                preset_seeded=element_id in preset_seeded,
            ),
            user_weight=_clamp_weight(pref.weight) if pref is not None else None,
            user_locked=bool(pref.locked) if pref is not None else False,
            user_enabled=pref.enabled if pref is not None else None,
        )
        if element_id in _QUALITY_CONFIG_IDS:
            quality_config_cards.append(card)
        else:
            extension_cards.append(card)

    selector_summary = _clean_text(payload.get("selector_summary"))
    if not selector_summary:
        if extension_cards:
            selector_summary = "已保留通用必要项，并按题材补充专用约束层。"
        else:
            selector_summary = "已保留通用必要项，当前题材未触发额外扩展项。"
    if preference_config.manual_override:
        selector_summary = f"{selector_summary}（已启用手动覆盖）"
    elif preference_config.preset_id:
        preset = _GENRE_PRESET_BY_ID.get(preference_config.preset_id)
        if preset is not None:
            selector_summary = f"{selector_summary}（已应用题材预置：{preset.label}）"

    return BlueprintElementSelection(
        library_version=get_element_library_version(),
        mode=mode,
        selector_summary=selector_summary,
        genre_inference=_extract_str_list(payload.get("genre_inference")),
        focus_constraints=_extract_str_list(payload.get("focus_constraints")),
        required_elements=required_cards,
        extension_elements=extension_cards,
        quality_config_elements=quality_config_cards,
    )
