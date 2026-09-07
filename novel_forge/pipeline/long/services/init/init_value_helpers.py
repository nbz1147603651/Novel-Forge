"""Small pure value helpers shared by initialization slices."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.long.services.init.init_coherence import (
    blocking_issues,
    init_coherence_issue_id,
)


def _safe_init_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _positive_int(value: Any) -> int | None:
    number = _safe_init_int(value)
    return number if number > 0 else None


def _init_coherence_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _init_coherence_min_severity(settings: Any) -> str:
    severity = str(getattr(settings, "init_coherence_block_min_severity", "high") or "high")
    return severity.strip().lower() or "high"


def _init_coherence_blocking_issue_ids(
    settings: Any,
    report: dict[str, Any],
) -> set[str]:
    return {
        init_coherence_issue_id(issue)
        for issue in blocking_issues(
            report,
            min_severity=_init_coherence_min_severity(settings),
        )
        if isinstance(issue, dict)
    }


def _entity_catalog_lookup(entity_catalog: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    if not isinstance(entity_catalog, dict):
        return {}
    lookup: dict[str, dict[str, str]] = {}
    alias_to_entity = entity_catalog.get("alias_to_entity")
    if isinstance(alias_to_entity, dict):
        for token, item in alias_to_entity.items():
            if isinstance(item, dict):
                key = str(token or "").strip()
                if key:
                    lookup[key] = {
                        "entity_id": str(item.get("entity_id") or "").strip(),
                        "canonical_name": str(item.get("canonical_name") or "").strip(),
                        "entity_type": str(item.get("entity_type") or "unknown").strip(),
                    }
    for item in entity_catalog.get("allowed_entities") or []:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("entity_id") or "").strip()
        canonical_name = str(item.get("canonical_name") or item.get("name") or "").strip()
        entity_type = str(item.get("entity_type") or "unknown").strip()
        if not entity_id or not canonical_name:
            continue
        ref = {
            "entity_id": entity_id,
            "canonical_name": canonical_name,
            "entity_type": entity_type,
        }
        for token in [entity_id, canonical_name, *(item.get("aliases") or [])]:
            key = str(token or "").strip()
            if key:
                lookup.setdefault(key, ref)
    return lookup


def _canonicalize_entity_name_list(
    values: list[str],
    lookup: dict[str, dict[str, str]],
    *,
    entity_type: str | None = None,
) -> list[str]:
    if not lookup:
        return list(values)
    result: list[str] = []
    for value in values:
        ref = lookup.get(str(value or "").strip())
        if ref is None or (entity_type is not None and ref.get("entity_type") != entity_type):
            continue
        name = str(ref.get("canonical_name") or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def _canonicalize_character_knowledge_coverage(
    value: dict[str, Any],
    lookup: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if not lookup:
        return dict(value)
    result: dict[str, Any] = {}
    for raw_name, awareness in value.items():
        ref = lookup.get(str(raw_name or "").strip())
        if ref is None or ref.get("entity_type") != "character":
            continue
        name = str(ref.get("canonical_name") or "").strip()
        if name and name not in result:
            result[name] = awareness
    return result


__all__ = [
    "_canonicalize_character_knowledge_coverage",
    "_canonicalize_entity_name_list",
    "_entity_catalog_lookup",
    "_init_coherence_blocking_issue_ids",
    "_init_coherence_list",
    "_init_coherence_min_severity",
    "_positive_int",
    "_safe_init_int",
]
