"""Prompt-sized entity reference graph helpers for long-form chapters."""

from __future__ import annotations

import json
from typing import Any

from novel_forge.core.domain.guardrails import sanitize_story_text
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.helpers import load_json_if_exists

_IDENTITY_LINK_TYPES = {
    "alias_of",
    "reincarnation_of",
    "mistaken_as",
    "same_as",
    "identity_of",
    "former_identity_of",
    "title_of",
    "called_as",
}

def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _excerpt(value: Any, *, max_chars: int = 90) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，,；;：: ")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_field(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _clean_prompt_link(link: Any) -> dict[str, Any]:
    source = _clean_text(_coerce_field(link, "source") or _coerce_field(link, "source_name"))
    target = _clean_text(_coerce_field(link, "target") or _coerce_field(link, "target_name"))
    if not source or not target:
        return {}

    link_type = _clean_text(_coerce_field(link, "link_type", "related_to")) or "related_to"
    description = _excerpt(_coerce_field(link, "description", ""), max_chars=90)
    time_layer = _clean_text(_coerce_field(link, "time_layer", ""))
    source_type = _clean_text(_coerce_field(link, "source_type", ""))
    target_type = _clean_text(_coerce_field(link, "target_type", ""))
    confidence = _safe_float(_coerce_field(link, "confidence", 0.0), 0.0)

    result: dict[str, Any] = {
        "source": sanitize_story_text(source),
        "target": sanitize_story_text(target),
        "link_type": sanitize_story_text(link_type),
    }
    if source_type:
        result["source_type"] = sanitize_story_text(source_type)
    if target_type:
        result["target_type"] = sanitize_story_text(target_type)
    if time_layer:
        result["time_layer"] = sanitize_story_text(time_layer)
    if description:
        result["description"] = sanitize_story_text(description)
    if confidence > 0:
        result["confidence"] = round(confidence, 3)
    return result


def normalize_prompt_entity_reference_graph(raw: Any) -> dict[str, Any]:
    """Normalize an already chapter-scoped entity graph without re-ranking it."""
    if not isinstance(raw, dict):
        return {}

    def _collect(raw_key: str, *, fallback_identity: bool | None = None) -> list[dict[str, Any]]:
        values = raw.get(raw_key)
        if not isinstance(values, list):
            return []
        collected: list[dict[str, Any]] = []
        for link in values:
            compact = _clean_prompt_link(link)
            if not compact:
                continue
            if fallback_identity is not None:
                is_identity = compact["link_type"] in _IDENTITY_LINK_TYPES
                if is_identity is not fallback_identity:
                    continue
            collected.append(compact)
        return collected

    identity_links = _collect("identity_links")
    context_links = _collect("context_links")
    if not identity_links and not context_links and isinstance(raw.get("entity_links"), list):
        identity_links = _collect("entity_links", fallback_identity=True)
        context_links = _collect("entity_links", fallback_identity=False)

    result: dict[str, Any] = {}
    source = _clean_text(raw.get("source"))
    if source:
        result["source"] = sanitize_story_text(source)
    if identity_links:
        result["identity_links"] = identity_links
    if context_links:
        result["context_links"] = context_links
    return result


def compact_entity_reference_graph(
    layout: ProjectLayout,
    *,
    chapter_outline: ChapterOutline,
    relevant_entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Load the complete fixed entity links for this chapter's source slice."""
    graph_path = layout.root / "narrative_state" / "entity_graph.json"
    graph = load_json_if_exists(FileSystemStorage(layout.root.parent), graph_path)
    if not graph:
        return {}

    raw_entities = graph.get("entities", [])
    raw_links = graph.get("entity_links", [])
    if not isinstance(raw_entities, list) or not isinstance(raw_links, list):
        return {}

    entities_by_id: dict[str, dict[str, Any]] = {}
    entities_by_name: dict[str, dict[str, Any]] = {}
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        entity_id = _clean_text(item.get("entity_id", ""))
        name = _clean_text(item.get("name", ""))
        entity_type = _clean_text(item.get("entity_type", "unknown")) or "unknown"
        aliases = item.get("aliases", [])
        compact = {
            "entity_id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "aliases": [str(alias).strip() for alias in list(aliases or []) if str(alias).strip()]
            if isinstance(aliases, list)
            else [],
        }
        if entity_id:
            entities_by_id[entity_id] = compact
        if name:
            entities_by_name[name] = compact

    involved_names = {
        _clean_text(chapter_outline.pov_character),
        *[_clean_text(name) for name in list(chapter_outline.involved_characters or [])],
    }
    involved_ids: set[str] = set()
    for entity in list(relevant_entities or []):
        entity_id = _clean_text(_coerce_field(entity, "entity_id", ""))
        name = _clean_text(
            _coerce_field(entity, "canonical_name", "") or _coerce_field(entity, "name", "")
        )
        if entity_id:
            involved_ids.add(entity_id)
        if name:
            involved_names.add(name)
    involved_names.discard("")
    outline_payload = (
        chapter_outline.model_dump(mode="json")
        if hasattr(chapter_outline, "model_dump")
        else vars(chapter_outline)
        if hasattr(chapter_outline, "__dict__")
        else {}
    )
    outline_text = json.dumps(outline_payload, ensure_ascii=False, default=str)

    def entity_for(link: dict[str, Any], side: str) -> dict[str, Any]:
        entity_id = _clean_text(link.get(f"{side}_id", ""))
        name = _clean_text(link.get(f"{side}_name", ""))
        return entities_by_id.get(entity_id) or entities_by_name.get(name) or {
            "entity_id": entity_id,
            "name": name,
            "entity_type": "unknown",
            "aliases": [],
        }

    def compact_link(link: dict[str, Any]) -> dict[str, Any]:
        source = entity_for(link, "source")
        target = entity_for(link, "target")
        return {
            "source": source.get("name", ""),
            "target": target.get("name", ""),
            "source_type": source.get("entity_type", "unknown"),
            "target_type": target.get("entity_type", "unknown"),
            "link_type": _clean_text(link.get("link_type", "related_to")) or "related_to",
            "time_layer": _clean_text(link.get("time_layer", "")),
            "description": _clean_text(link.get("description", "")),
            "confidence": _safe_float(link.get("confidence", 0.0), 0.0),
        }

    def is_relevant(link: dict[str, Any]) -> bool:
        source = entity_for(link, "source")
        target = entity_for(link, "target")
        names = {_clean_text(source.get("name", "")), _clean_text(target.get("name", ""))}
        ids = {_clean_text(source.get("entity_id", "")), _clean_text(target.get("entity_id", ""))}
        linked_to_chapter = bool(
            names & involved_names
            or ids & involved_ids
            or any(name and name in outline_text for name in names)
        )
        if not linked_to_chapter:
            return False
        link_type = _clean_text(link.get("link_type", ""))
        if link_type in _IDENTITY_LINK_TYPES:
            return True
        return all(
            not value or value in involved_names or value in outline_text
            for value in names
        )

    ordered_links: list[dict[str, Any]] = [
        link for link in raw_links if isinstance(link, dict) and is_relevant(link)
    ]

    identity_links: list[dict[str, Any]] = []
    context_links: list[dict[str, Any]] = []
    for link in ordered_links:
        compact = compact_link(link)
        if not compact["source"] or not compact["target"]:
            continue
        if compact["link_type"] in _IDENTITY_LINK_TYPES:
            identity_links.append(compact)
        else:
            context_links.append(compact)

    if not identity_links and not context_links:
        return {}

    return normalize_prompt_entity_reference_graph(
        {
            "source": "narrative_state/entity_graph.json",
            "identity_links": identity_links,
            "context_links": context_links,
        }
    )
