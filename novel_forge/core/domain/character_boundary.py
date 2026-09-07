"""Canonical character-name boundaries shared across novel pipeline stages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.domain.character_identity import clean_character_name


@dataclass
class CharacterBoundaryResult:
    """Accepted character payloads plus audit-only rejected material."""

    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    contaminated: list[dict[str, Any]] = field(default_factory=list)


def canonical_character_names(items: Any) -> tuple[str, ...]:
    """Extract an ordered, unique canonical-name tuple from roster-like values."""

    if hasattr(items, "model_dump"):
        items = items.model_dump(mode="json")
    if isinstance(items, dict):
        items = items.get("characters") or items.get("character_roster") or []
    elif not isinstance(items, list | tuple):
        items = getattr(items, "characters", [])
    if not isinstance(items, list | tuple):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            raw_name = item
        elif isinstance(item, dict):
            raw_name = item.get("name")
        else:
            raw_name = getattr(item, "name", "")
        name = clean_character_name(raw_name)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def clean_character_aliases(
    aliases: dict[str, str] | None,
    *,
    canonical_names: tuple[str, ...] = (),
) -> dict[str, str]:
    """Return deterministic aliases that point only at canonical characters."""

    allowed = set(canonical_names)
    cleaned = {
        clean_character_name(alias): clean_character_name(canonical)
        for alias, canonical in (aliases or {}).items()
        if clean_character_name(alias)
        and clean_character_name(canonical)
        and clean_character_name(alias) != clean_character_name(canonical)
        and (not allowed or clean_character_name(canonical) in allowed)
    }
    return dict(sorted(cleaned.items(), key=lambda item: len(item[0]), reverse=True))


def rewrite_character_aliases(value: Any, aliases: dict[str, str]) -> Any:
    """Recursively rewrite explicit, already-adjudicated character aliases."""

    cleaned = clean_character_aliases(aliases)
    if not cleaned:
        return value
    if isinstance(value, str):
        text = value
        for alias, canonical in cleaned.items():
            text = text.replace(alias, canonical)
        return text
    if isinstance(value, list):
        return [rewrite_character_aliases(item, cleaned) for item in value]
    if isinstance(value, dict):
        return {
            str(rewrite_character_aliases(str(key), cleaned)): rewrite_character_aliases(
                item,
                cleaned,
            )
            for key, item in value.items()
        }
    return value


def _resolve_name(
    value: Any,
    *,
    canonical_names: tuple[str, ...],
    aliases: dict[str, str],
) -> str | None:
    name = clean_character_name(value)
    if name in canonical_names:
        return name
    canonical = aliases.get(name)
    return canonical if canonical in canonical_names else None


def _quarantine_contaminated_items(result: CharacterBoundaryResult) -> None:
    invalid_names = {
        clean_character_name(name)
        for rejected in result.rejected
        for name in rejected.get("invalid_character_names", [])
        if clean_character_name(name)
    }
    if not invalid_names:
        return
    clean: list[dict[str, Any]] = []
    for item in result.accepted:
        serialized = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        matches = sorted(name for name in invalid_names if name in serialized)
        if matches:
            result.contaminated.append({"item": item, "unresolved_names": matches})
        else:
            clean.append(item)
    result.accepted = clean


def canonicalize_relationship_items(
    items: Any,
    *,
    roster: Any,
    aliases: dict[str, str] | None = None,
) -> CharacterBoundaryResult:
    """Normalize relationship endpoints and quarantine all off-roster references."""

    names = canonical_character_names(roster)
    alias_map = clean_character_aliases(aliases, canonical_names=names)
    result = CharacterBoundaryResult()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        left_raw = clean_character_name(normalized.get("character_a"))
        right_raw = clean_character_name(normalized.get("character_b"))
        left = _resolve_name(left_raw, canonical_names=names, aliases=alias_map)
        right = _resolve_name(right_raw, canonical_names=names, aliases=alias_map)
        invalid = [
            name
            for name, resolved in ((left_raw, left), (right_raw, right))
            if name and resolved is None
        ]
        if not left or not right or left == right:
            result.rejected.append(
                {
                    "item": normalized,
                    "invalid_character_names": invalid,
                    "reason": (
                        "self_relationship"
                        if left and right and left == right
                        else "character_name_not_in_canonical_roster"
                    ),
                }
            )
            continue
        normalized["character_a"] = left
        normalized["character_b"] = right
        local_aliases = dict(alias_map)
        if left_raw != left:
            local_aliases[left_raw] = left
        if right_raw != right:
            local_aliases[right_raw] = right
        result.accepted.append(rewrite_character_aliases(normalized, local_aliases))
    _quarantine_contaminated_items(result)
    return result


def canonicalize_character_arc_items(
    items: Any,
    *,
    roster: Any,
    aliases: dict[str, str] | None = None,
) -> CharacterBoundaryResult:
    """Normalize arc owners and prevent raw arc drift from reaching later prompts."""

    names = canonical_character_names(roster)
    alias_map = clean_character_aliases(aliases, canonical_names=names)
    result = CharacterBoundaryResult()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        raw_name = clean_character_name(
            normalized.get("name")
            or normalized.get("character")
            or normalized.get("character_name")
        )
        canonical = _resolve_name(raw_name, canonical_names=names, aliases=alias_map)
        if not canonical:
            result.rejected.append(
                {
                    "item": normalized,
                    "invalid_character_names": [raw_name] if raw_name else [],
                    "reason": "character_arc_name_not_in_canonical_roster",
                }
            )
            continue
        normalized["name"] = canonical
        normalized.pop("character", None)
        normalized.pop("character_name", None)
        local_aliases = dict(alias_map)
        if raw_name != canonical:
            local_aliases[raw_name] = canonical
        result.accepted.append(rewrite_character_aliases(normalized, local_aliases))
    _quarantine_contaminated_items(result)
    return result
