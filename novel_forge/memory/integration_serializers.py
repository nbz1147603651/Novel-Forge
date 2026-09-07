"""Serialization helpers for MemoryContext persistence.

Extracted from ``integration.py`` to reduce its size. Handles serialization
of episodic memory index and motif tracker data for disk persistence.

All functions take ``ctx`` (the MemoryContext instance) as first argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

_log = get_logger("memory.serializers")


def serialize_episodic_index(ctx: "MemoryContext") -> dict[str, Any]:
    """Serialize episodic memory index for persistence.

    Embeddings are excluded from this output; the Zvec collection is the
    durable vector source of truth. JSON keeps memory metadata and indexes.
    """
    if not ctx._episodic_memory:
        return {}

    try:
        index = getattr(ctx._episodic_memory, "_index", {})
        chapter_events = getattr(ctx._episodic_memory, "_chapter_events", {})

        serialized = {
            "vector_store": {
                "backend": getattr(ctx._episodic_memory, "vector_store_backend", ""),
                "requested_backend": getattr(
                    ctx._episodic_memory, "_vector_store_backend_requested", ""
                ),
                "path": getattr(ctx._episodic_memory, "vector_store_path", ""),
                "dimension": getattr(ctx._episodic_memory, "_vector_dimension", None)
                or getattr(ctx._episodic_memory, "_mock_dimensions", 0),
                "index_type": getattr(ctx._episodic_memory, "_zvec_index_type", ""),
            },
            "index": {
                sig: {
                    "chapter_number": entry.chapter_number,
                    "event_summary": entry.event_summary,
                    "scene_index": entry.scene_index,
                    "timestamp_in_story": entry.timestamp_in_story,
                    "characters": entry.characters,
                    "locations": entry.locations,
                    "event_types": entry.event_types,
                    "metadata": entry.metadata,
                }
                for sig, entry in index.items()
            },
            "chapter_events": {str(k): v for k, v in chapter_events.items()},
        }

        outline_data_raw = ctx._episodic_memory.serialize_outline_data()
        outline_data = outline_data_raw if isinstance(outline_data_raw, dict) else {}
        serialized["outline_data"] = outline_data

        outline_index = outline_data.get("outline_index", {})
        if isinstance(outline_index, dict):
            for _entry in outline_index.values():
                if isinstance(_entry, dict):
                    _entry.pop("embedding", None)

        critique_index = getattr(ctx._episodic_memory, "_critique_index", {})
        chapter_critiques = getattr(ctx._episodic_memory, "_chapter_critiques", {})

        if critique_index:
            serialized["critique_index"] = {
                sig: {
                    "chapter_number": entry.chapter_number,
                    "issue_type": entry.issue_type,
                    "severity": entry.severity,
                    "summary": entry.summary,
                    "evidence": entry.evidence,
                    "suggested_fix": entry.suggested_fix,
                    "affected_chapters": entry.affected_chapters,
                    "scene_index": entry.scene_index,
                    "repair_attempts": entry.repair_attempts,
                    "failure_pattern": entry.failure_pattern,
                    "lesson_learned": entry.lesson_learned,
                    "metadata": entry.metadata,
                }
                for sig, entry in critique_index.items()
            }
            serialized["chapter_critiques"] = {
                str(k): v for k, v in chapter_critiques.items()
            }

        return serialized
    except Exception as exc:
        _log.warning("Failed to serialize episodic index: %s", exc)
        return {}


def serialize_motif_tracker(ctx: "MemoryContext") -> dict[str, Any]:
    """Serialize motif tracker data for persistence."""
    if not ctx._motif_tracker:
        return {}

    try:
        motifs = getattr(ctx._motif_tracker, "_motifs", {})
        chapter_motifs = getattr(ctx._motif_tracker, "_chapter_motifs", {})

        return {
            "motifs": {
                motif_id: {
                    "name": motif.name,
                    "category": motif.category.value
                    if hasattr(motif.category, "value")
                    else str(motif.category),
                    "description": motif.description,
                    "occurrence_count": motif.occurrence_count,
                    "first_appearance_chapter": motif.first_appearance_chapter,
                    "last_appearance_chapter": motif.last_appearance_chapter,
                    "associated_characters": motif.associated_characters,
                    "thematic_meaning": motif.thematic_meaning,
                    "is_intentional": motif.is_intentional,
                    "retired": motif.retired,
                    "metadata": dict(getattr(motif, "metadata", {}) or {}),
                }
                for motif_id, motif in motifs.items()
            },
            "chapter_motifs": {
                str(ch): list(motif_ids)
                for ch, motif_ids in chapter_motifs.items()
                if motif_ids
            },
        }
    except Exception as exc:
        _log.warning("Failed to serialize motif tracker: %s", exc)
        return {}
