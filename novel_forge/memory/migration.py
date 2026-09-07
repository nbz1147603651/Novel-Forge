"""Migration helpers for rebuilding project memory into vector backends."""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MemoryVectorMigrationResult:
    """Result for a project-memory vector migration."""

    ok: bool
    migrated_vectors: int = 0
    backend: str = "zvec"
    vector_store_path: str = ""
    reason: str = ""


def migrate_project_memory_vectors_to_zvec(
    memory_dir: str | Path,
    *,
    zvec_dir: str | Path | None = None,
    index_type: str = "hnsw",
    memory_limit_mb: int = 512,
    force: bool = True,
) -> MemoryVectorMigrationResult:
    """Legacy helper for rebuilding old shard-based vectors into Zvec.

    Current projects should treat the Zvec collection as the vector source of
    truth and use explicit rebuild to regenerate vectors from memory metadata.
    This helper remains only for manually inspecting old development data.
    """
    memory_path = Path(memory_dir)
    project_memory_path = memory_path / "project_memory.json"
    vector_shard_path = memory_path / "episodic_vectors.json"
    target_path = Path(zvec_dir) if zvec_dir is not None else memory_path / "zvec_vectors"

    if not project_memory_path.exists():
        return MemoryVectorMigrationResult(
            ok=False,
            vector_store_path=str(target_path),
            reason="project_memory_json_missing",
        )
    if not vector_shard_path.exists():
        return MemoryVectorMigrationResult(
            ok=False,
            vector_store_path=str(target_path),
            reason="episodic_vectors_json_missing",
        )

    try:
        import json

        memory_data = json.loads(project_memory_path.read_text(encoding="utf-8"))
        vector_shard = json.loads(vector_shard_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return MemoryVectorMigrationResult(
            ok=False,
            vector_store_path=str(target_path),
            reason=f"failed_to_read_memory_files: {exc}",
        )

    dimensions = int(vector_shard.get("dimensions", 0) or 0)
    packed_vectors = vector_shard.get("vectors", {})
    if dimensions <= 0 or not isinstance(packed_vectors, dict) or not packed_vectors:
        return MemoryVectorMigrationResult(
            ok=False,
            vector_store_path=str(target_path),
            reason="no_vectors_to_migrate",
        )

    try:
        from novel_forge.memory.zvec_store import ZvecVectorStore

        store = ZvecVectorStore(
            path=str(target_path),
            dimension=dimensions,
            index_type=index_type,
            memory_limit_mb=memory_limit_mb,
        )
        if force:
            store.clear()
    except Exception as exc:
        return MemoryVectorMigrationResult(
            ok=False,
            vector_store_path=str(target_path),
            reason=f"zvec_unavailable: {exc}",
        )

    episodic_data = memory_data.get("episodic_index", {}) if isinstance(memory_data, dict) else {}
    migrated = 0

    for sig, entry_data in (episodic_data.get("index", {}) or {}).items():
        vector = _unpack_vector(packed_vectors.get(sig), dimensions)
        if not vector:
            continue
        metadata = {
            "content": " ".join(
                part
                for part in (
                    str(entry_data.get("event_summary", "") or ""),
                    " ".join(list(entry_data.get("characters", []) or [])),
                    " ".join(list(entry_data.get("locations", []) or [])),
                    " ".join(list(entry_data.get("event_types", []) or [])),
                    " ".join(list(entry_data.get("keywords", []) or [])),
                )
                if part
            ),
            "chapter": int(entry_data.get("chapter_number", 0) or 0),
            "scene_index": int(entry_data.get("scene_index", 0) or 0),
            "characters": list(entry_data.get("characters", []) or []),
            "event_types": list(entry_data.get("event_types", []) or []),
        }
        store.add(str(sig), vector, metadata)
        migrated += 1

    outline_data = episodic_data.get("outline_data", {}) or {}
    for sig, entry_data in (outline_data.get("outline_index", {}) or {}).items():
        vector = _unpack_vector(packed_vectors.get(f"outline:{sig}"), dimensions)
        if not vector:
            continue
        event_type = str(entry_data.get("event_type", "") or "")
        metadata = {
            "chapter": int(entry_data.get("chapter_number", 0) or 0),
            "event_types": [event_type] if event_type else [],
            "characters": list(entry_data.get("characters", []) or []),
            "is_outline": True,
        }
        store.add(str(sig), vector, metadata)
        migrated += 1

    store.flush()
    return MemoryVectorMigrationResult(
        ok=True,
        migrated_vectors=migrated,
        vector_store_path=str(target_path),
    )


def _unpack_vector(value: Any, dimensions: int) -> list[float]:
    if not value:
        return []
    try:
        raw = base64.b64decode(str(value))
        return list(struct.unpack(f"<{dimensions}f", raw))
    except Exception:
        return []
