"""Prompt-safe payload serializers for memory context builders."""

from __future__ import annotations

from typing import Any


def serialize_due_foreshadows(raw: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "") or "").strip()
        if not description:
            continue
        items.append(
            {
                "entry_id": str(item.get("entry_id", "") or ""),
                "description": description,
                "planted_chapter": int(item.get("planted_chapter", 0) or 0),
                "promise_type": str(item.get("promise_type", "") or ""),
            }
        )
    return items


def serialize_relationship_projection(raw: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        partner = str(item.get("target_entity_id") or item.get("source_entity_id") or "")
        label = str(item.get("label", "") or "").strip()
        shift_summary = str(item.get("shift_summary", "") or "").strip()
        relation_type = str(item.get("relation_type", "") or "").strip()
        if not (partner or label or shift_summary or relation_type):
            continue
        items.append(
            {
                "relationship_id": str(item.get("relationship_id", "") or ""),
                "source_entity_id": str(item.get("source_entity_id", "") or ""),
                "target_entity_id": str(item.get("target_entity_id", "") or ""),
                "relation_type": relation_type,
                "label": label,
                "trust": item.get("trust"),
                "tension": item.get("tension"),
                "dependency": item.get("dependency"),
                "status": str(item.get("status", "") or ""),
                "last_shift_chapter": int(item.get("last_shift_chapter", 0) or 0),
                "shift_summary": shift_summary,
            }
        )
    return items


def serialize_character_episodic_results(
    results: Any,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    serialized: list[dict[str, Any]] = []
    for item in results[:limit]:
        if isinstance(item, dict):
            chapter = int(item.get("chapter_number", 0) or 0)
            summary = str(item.get("event_summary", "") or "").strip()
            scene_index = int(item.get("scene_index", 0) or 0)
            relevance = float(item.get("relevance_score", 0.0) or 0.0)
            characters = list(item.get("characters_involved", []) or [])
            timestamp = str(item.get("timestamp_in_story", "") or "")
        else:
            chapter = int(getattr(item, "chapter_number", 0) or 0)
            summary = str(getattr(item, "event_summary", "") or "").strip()
            scene_index = int(getattr(item, "scene_index", 0) or 0)
            relevance = float(getattr(item, "relevance_score", 0.0) or 0.0)
            characters = list(getattr(item, "characters_involved", []) or [])
            timestamp = str(getattr(item, "timestamp_in_story", "") or "")
        if not summary:
            continue
        serialized.append(
            {
                "chapter_number": chapter,
                "event_summary": summary,
                "scene_index": scene_index,
                "relevance_score": round(relevance, 3),
                "characters_involved": characters,
                "timestamp_in_story": timestamp,
            }
        )
    return serialized


def serialize_character_motifs(
    motif_tracker: Any,
    character: str,
) -> list[dict[str, Any]]:
    motifs = getattr(motif_tracker, "motifs", None)
    if motifs is None:
        motifs = getattr(motif_tracker, "_motifs", {})
    if not isinstance(motifs, dict):
        return []

    serialized: list[dict[str, Any]] = []
    for motif_id, motif in motifs.items():
        associated = (
            motif.get("associated_characters", [])
            if isinstance(motif, dict)
            else getattr(motif, "associated_characters", [])
        ) or []
        if character not in associated:
            continue
        retired = (
            motif.get("retired", False)
            if isinstance(motif, dict)
            else getattr(motif, "retired", False)
        )
        if retired:
            continue
        category = (
            motif.get("category", "") if isinstance(motif, dict) else getattr(motif, "category", "")
        )
        if hasattr(category, "value"):
            category = category.value
        serialized.append(
            {
                "motif_id": str(
                    motif.get("motif_id", motif_id)
                    if isinstance(motif, dict)
                    else getattr(motif, "motif_id", motif_id)
                ),
                "name": str(
                    motif.get("name", "") if isinstance(motif, dict) else getattr(motif, "name", "")
                ),
                "category": str(category or ""),
                "last_seen": int(
                    motif.get("last_appearance_chapter", 0)
                    if isinstance(motif, dict)
                    else getattr(motif, "last_appearance_chapter", 0)
                ),
                "thematic_meaning": str(
                    motif.get("thematic_meaning", "")
                    if isinstance(motif, dict)
                    else getattr(motif, "thematic_meaning", "")
                ),
            }
        )
    serialized.sort(key=lambda item: int(item.get("last_seen", 0) or 0), reverse=True)
    return serialized


__all__ = (
    "serialize_character_episodic_results",
    "serialize_character_motifs",
    "serialize_due_foreshadows",
    "serialize_relationship_projection",
)
