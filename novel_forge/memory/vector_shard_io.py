"""Legacy episodic vector shard I/O helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.memory.vector_serialization import pack_embedding, unpack_embedding


def vector_shard_path(memory_path: Path) -> Path:
    """Return the legacy compact vector shard path for a project memory file."""
    return memory_path.with_name("episodic_vectors.json")


def save_vector_shard(
    *,
    storage: Any,
    shard_path: Path,
    episodic_memory: Any,
    logger: Any,
) -> bool:
    """Write the pre-zvec compact JSON vector shard for legacy compatibility."""
    if not episodic_memory or not storage:
        return False
    try:
        index = getattr(episodic_memory, "_index", {})
        outline_index = getattr(episodic_memory, "_outline_index", {})
        critique_index = getattr(episodic_memory, "_critique_index", {})

        vectors: dict[str, str] = {}
        dims = 0
        for sig, entry in index.items():
            if entry.embedding:
                vectors[sig] = pack_embedding(entry.embedding)
                dims = dims or len(entry.embedding)
        for sig, entry in outline_index.items():
            if entry.embedding:
                vectors[f"outline:{sig}"] = pack_embedding(entry.embedding)
                dims = dims or len(entry.embedding)
        for sig, entry in critique_index.items():
            if entry.embedding:
                vectors[f"critique:{sig}"] = pack_embedding(entry.embedding)
                dims = dims or len(entry.embedding)

        shard = {"version": 1, "dimensions": dims, "vectors": vectors}
        storage.save_json(shard_path, shard)

        try:
            loaded = storage.load_json(shard_path)
            if loaded.get("version") != 1 or len(loaded.get("vectors", {})) != len(vectors):
                raise ValueError(
                    f"vector count mismatch after write: "
                    f"expected {len(vectors)}, got {len(loaded.get('vectors', {}))}"
                )
        except Exception as verify_exc:
            logger.error(
                "vector_shard_verify_failed | path=%s | error=%s — deleting corrupt shard",
                shard_path,
                verify_exc,
            )
            try:
                shard_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False

        logger.info(
            "vector_shard_saved | vectors=%d | dims=%d | path=%s",
            len(vectors),
            dims,
            shard_path,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to save vector shard: %s", exc)
        return False


def load_vector_shard(
    *,
    storage: Any,
    shard_path: Path,
    logger: Any,
) -> dict[str, list[float]]:
    """Load a legacy compact JSON vector shard."""
    if not storage:
        return {}
    if not storage.exists(shard_path):
        return {}
    try:
        shard = storage.load_json(shard_path)
        dims = shard.get("dimensions", 0)
        if not dims:
            return {}
        vectors: dict[str, list[float]] = {}
        for sig, b64 in shard.get("vectors", {}).items():
            try:
                vectors[sig] = unpack_embedding(b64, dims)
            except Exception:
                pass
        logger.info("vector_shard_loaded | vectors=%d | dims=%d", len(vectors), dims)
        return vectors
    except Exception as exc:
        logger.warning("Failed to load vector shard: %s", exc)
        return {}


__all__ = (
    "load_vector_shard",
    "save_vector_shard",
    "vector_shard_path",
)
