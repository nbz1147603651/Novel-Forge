"""Utilities for binding chapter-level state transitions to scene plans."""

from __future__ import annotations

import math
import re
from typing import Any

from novel_forge.core.domain.guardrails import sanitize_story_text
from novel_forge.core.utils.field_extractor import field as extract_field
from novel_forge.core.utils.string import clean_str

_CLAIM_FIELDS = ("owned_state_changes", "required_outcome", "exit_target_state")
_ROUTING_FIELDS = (
    "summary",
    "purpose",
    "conflict",
    "entry_state_refs",
    "required_outcome",
    "exit_target_state",
    "location",
    "time_marker",
    "owned_events",
    "owned_revelations",
    "owned_state_changes",
    "handoff_to_next",
    "entry_state",
    "exit_state",
)
_CONNECTOR_RE = re.compile(
    r"(?:"
    r"推进到|推进至|转变为|转化为|收窄至|只剩下|"
    r"仅余|只剩|转为|变为|成为|完成|进入|落定|意识到|"
    r"推进|收窄|从|到|由|至|为|在|和|与|或|及|并|但|而非|而|是|的|"
    r"[，,。；;：:\s]+"
    r")"
)
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{3,}")
_GENERIC_TERMS = {
    "一个",
    "一种",
    "这个",
    "那个",
    "本章",
    "本场",
    "场景",
    "角色",
    "状态",
    "变化",
    "可见",
    "明确",
    "必须",
    "需要",
    "已经",
    "继续",
    "最终",
    "开场",
    "章末",
    "行动",
    "信息",
    "情绪",
    "目标",
}


def find_uncovered_required_state_transitions(
    transitions: list[Any],
    scenes: list[Any],
    *,
    max_chars: int | None = None,
) -> list[str]:
    """Return transitions that are not substantively claimed by any scene."""

    uncovered: list[str] = []
    for transition in transitions:
        text = _clean_text(transition)
        if not text:
            continue
        if transition_is_claimed_by_scenes(text, scenes):
            continue
        uncovered.append(text[:max_chars] if max_chars is not None else text)
    return uncovered


def assign_required_state_transitions_to_scenes(
    scenes: list[dict[str, Any]],
    transitions: list[Any],
) -> list[dict[str, Any]]:
    """Ensure every chapter-level transition is owned by at least one scene."""

    if not scenes or not transitions:
        return scenes
    patched = [_copy_scene(scene) for scene in scenes if isinstance(scene, dict)]
    if not patched:
        return scenes

    normalized_transitions = [_clean_text(item) for item in transitions]
    normalized_transitions = [item for item in normalized_transitions if item]
    for index, transition in enumerate(normalized_transitions):
        if transition_is_claimed_by_scenes(transition, patched):
            continue
        scene_index = _best_scene_index_for_transition(
            transition,
            patched,
            transition_index=index,
            total_transitions=len(normalized_transitions),
        )
        scene = patched[scene_index]
        owned = _string_list(extract_field(scene, "owned_state_changes", []), max_chars=180)
        if transition not in owned:
            owned.append(transition[:180])
        scene["owned_state_changes"] = owned
    return patched


def transition_is_claimed_by_scenes(transition: Any, scenes: list[Any]) -> bool:
    """Return whether any scene explicitly claims the transition in execution fields."""

    text = _clean_text(transition)
    if not text:
        return True
    return any(
        transition_is_covered_by_text(text, _scene_claim_text(scene))
        for scene in scenes
    )


def transition_is_covered_by_text(transition: Any, claim_text: Any) -> bool:
    """Substantive local coverage check for a transition against one claim text."""

    transition_text = _clean_text(transition)
    claim = _clean_text(claim_text)
    if not transition_text:
        return True
    if not claim:
        return False
    if transition_text in claim:
        return True
    terms = transition_key_terms(transition_text)
    if not terms:
        return False
    hits = [term for term in terms if term in claim]
    if not hits:
        return False
    if len(terms) <= 2:
        return len(hits) == len(terms)
    required_hits = max(2, int(math.ceil(len(terms) * 0.45)))
    if len(hits) >= required_hits:
        return True
    return len(hits) >= 2 and any(len(term) >= 4 for term in hits)


def transition_key_terms(text: Any) -> list[str]:
    """Extract stable, low-noise terms from a state transition string."""

    cleaned = _clean_text(text)
    if not cleaned:
        return []
    terms: list[str] = []
    for part in _CONNECTOR_RE.split(cleaned):
        part = part.strip()
        if not part:
            continue
        for token in _TOKEN_RE.findall(part):
            token = token.lower() if token.isascii() else token
            if token in _GENERIC_TERMS:
                continue
            if len(token) > 12 and not token.isascii():
                terms.extend(_long_chinese_variants(token))
            else:
                terms.append(token)
    return _dedupe_terms(terms)


def _best_scene_index_for_transition(
    transition: str,
    scenes: list[dict[str, Any]],
    *,
    transition_index: int,
    total_transitions: int,
) -> int:
    fallback = _proportional_index(
        transition_index,
        total_transitions=total_transitions,
        scene_count=len(scenes),
    )
    best_index = fallback
    best_score = 0.0
    for index, scene in enumerate(scenes):
        score = _coverage_score(transition, _scene_routing_text(scene))
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def _coverage_score(transition: str, candidate_text: str) -> float:
    candidate = _clean_text(candidate_text)
    if not candidate:
        return 0.0
    if transition in candidate:
        return 1.0
    terms = transition_key_terms(transition)
    if not terms:
        return 0.0
    hits = [term for term in terms if term in candidate]
    if not hits:
        return 0.0
    long_hit_bonus = 0.15 if any(len(term) >= 4 for term in hits) else 0.0
    return min(0.99, len(hits) / len(terms) + long_hit_bonus)


def _proportional_index(index: int, *, total_transitions: int, scene_count: int) -> int:
    if scene_count <= 1 or total_transitions <= 1:
        return 0
    return max(0, min(scene_count - 1, round(index * (scene_count - 1) / (total_transitions - 1))))


def _scene_claim_text(scene: Any) -> str:
    return _join_fields(scene, _CLAIM_FIELDS)


def _scene_routing_text(scene: Any) -> str:
    return _join_fields(scene, _ROUTING_FIELDS)


def _join_fields(source: Any, fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for name in fields:
        parts.extend(_string_list(extract_field(source, name, []), max_chars=260))
    return "；".join(parts)


def _string_list(value: Any, *, max_chars: int = 220) -> list[str]:
    if isinstance(value, dict):
        raw = list(value.values())
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]
    result: list[str] = []
    for item in raw:
        text = _clean_text(item)
        if text and text not in result:
            result.append(text[:max_chars])
    return result


def _copy_scene(scene: dict[str, Any]) -> dict[str, Any]:
    copied = dict(scene)
    for key in (
        "required_characters",
        "character_motivations",
        "entry_state_refs",
        "owned_events",
        "owned_revelations",
        "owned_state_changes",
        "forbidden_overlap",
        "dependency_scene_ids",
    ):
        value = copied.get(key)
        if isinstance(value, list):
            copied[key] = [dict(item) if isinstance(item, dict) else item for item in value]
    return copied


def _long_chinese_variants(token: str) -> list[str]:
    variants = [token]
    for size in (6, 5, 4):
        for index in range(0, max(0, len(token) - size + 1), size):
            part = token[index : index + size]
            if len(part) >= 4:
                variants.append(part)
    return variants


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in terms:
        term = _clean_text(item)
        if len(term) < 2 or term in _GENERIC_TERMS or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def _clean_text(value: Any) -> str:
    return sanitize_story_text(clean_str(value))
