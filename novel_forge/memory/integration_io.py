"""Core I/O extraction for MemoryContext persistence.

This module contains the file I/O logic extracted from ``integration.py``
to reduce its size. Business logic (serialization, deserialization,
normalization) remains in ``integration.py`` and is accessed via the
``MemoryIOContext`` dataclass.

Design rationale -- why ``MemoryIOContext`` over inheritance/mixin:
    ``MemoryContext`` has a complex ``__init__`` with many optional
    parameters, lazy-initialised services, and interdependent state.
    A mixin would require duplicating that complexity or introducing
    fragile ``super()`` chains. A dataclass with explicit fields
    provides a clean, testable boundary: the IO functions receive
    exactly what they need and nothing more.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from novel_forge.core.infra.async_runner import run_async_coro
from novel_forge.core.infra.resource_locks import ResourceName, get_resource_lock_manager
from novel_forge.obs.logger import get_logger

_log = get_logger("memory.io")


@dataclass
class MemoryIOContext:
    """Carrier for data needed by the extracted I/O functions."""

    storage: Any
    project_id: str | None

    # Mutable references (cleared/updated in-place during load; read during save)
    summary_cache: dict[int, dict[str, Any]]
    chapter_content_hash: dict[int, str]
    summary_stats: dict[str, int]
    motif_cache: dict[int, list[Any]]

    # last_indexed_chapter uses callbacks because int is immutable
    get_last_indexed_chapter: Callable[[], int]
    set_last_indexed_chapter: Callable[[int], None]

    summary_service: Any | None

    # Callbacks to business-logic methods that remain in integration.py
    serialize_episodic_index: Callable[[], dict[str, Any]]
    deserialize_episodic_index: Callable[[dict[str, Any]], None]
    serialize_motif_tracker: Callable[[], dict[str, Any]]
    deserialize_motif_tracker: Callable[[dict[str, Any]], None]
    normalize_summary_cache_entry: Callable[[Any], dict[str, Any] | None]
    normalize_summary_stats: Callable[[Any], dict[str, int]]
    rebuild_motif_stats_from_cache: Callable[[], dict[str, Any]]
    load_outline_episodic_from_init: Callable[[], None]
    flush_episodic_vector_store: Callable[[], None]
    flush_expression_memory: Callable[[], None]


def _memory_path(storage: Any, project_id: str) -> Path:
    if storage:
        return Path(storage.root) / project_id / "memory" / "project_memory.json"
    return Path(project_id) / "memory" / "project_memory.json"


def _journal_path(memory_path: Path) -> Path:
    return memory_path.with_name("project_memory.pending.json")


def save_to_disk(ctx: MemoryIOContext) -> bool:
    """Persist memory data to project directory.

    Returns True on success, False otherwise.
    """
    if not ctx.storage or not ctx.project_id:
        _log.debug("Storage or project_id not set, skipping save")
        return False

    try:
        memory_data = {
            "project_id": ctx.project_id,
            "last_indexed_chapter": ctx.get_last_indexed_chapter(),
            "summary_cache": {str(ch): entry for ch, entry in ctx.summary_cache.items()},
            "chapter_content_hash": {
                str(ch): str(h or "") for ch, h in ctx.chapter_content_hash.items()
            },
            "summary_stats": dict(ctx.summary_stats),
            "episodic_index": ctx.serialize_episodic_index(),
        }

        motif_state_data = {
            "motif_cache": {
                str(k): [
                    item.model_dump(mode="json", exclude={"schema_version", "created_at"})
                    if hasattr(item, "model_dump")
                    else item
                    for item in v
                ]
                for k, v in ctx.motif_cache.items()
            },
            "motif_tracker": ctx.serialize_motif_tracker(),
        }

        save_path = _memory_path(ctx.storage, ctx.project_id)
        motif_state_path = save_path.with_name("motif_state.json")
        journal_path = _journal_path(save_path)
        lock_factory = getattr(ctx.storage, "project_lock", None)
        lock_manager = get_resource_lock_manager()
        already_project_locked = any(
            lock_manager.current_task_holds(resource, project_id=ctx.project_id)
            for resource in (ResourceName.STATE, ResourceName.CANON)
        )
        lock_context_ = (
            nullcontext()
            if already_project_locked
            else lock_factory(ctx.project_id)
            if callable(lock_factory)
            else nullcontext()
        )
        with lock_context_:
            ctx.storage.save_json(journal_path, memory_data)
            ctx.storage.save_json(save_path, memory_data)
            ctx.storage.save_json(motif_state_path, motif_state_data)

            ctx.flush_episodic_vector_store()
            ctx.flush_expression_memory()
            try:
                journal_path.unlink(missing_ok=True)
            except OSError:
                pass

        _log.info(
            "Memory saved to disk | project=%s | path=%s | indexed_chapters=%d",
            ctx.project_id,
            save_path,
            ctx.get_last_indexed_chapter(),
        )
        return True

    except Exception as exc:
        _log.error("Failed to save memory to disk: %s", exc, exc_info=True)
        return False


def load_from_disk(ctx: MemoryIOContext) -> bool:
    """Load memory data from project directory.

    Returns True on success, False otherwise.
    """
    if not ctx.storage or not ctx.project_id:
        _log.debug("Storage or project_id not set, skipping load")
        return False

    try:
        load_path = _memory_path(ctx.storage, ctx.project_id)
        if not ctx.storage.exists(load_path):
            journal_path = _journal_path(load_path)
            if ctx.storage.exists(journal_path):
                _log.warning(
                    "memory_load_main_missing_using_journal | project=%s | journal=%s",
                    ctx.project_id,
                    journal_path,
                )
                load_path = journal_path
            else:
                legacy_path = Path(ctx.project_id) / "memory" / "project_memory.json"
                if legacy_path != load_path and legacy_path.exists():
                    _log.info(
                        "Migrating legacy memory file | from=%s | to=%s",
                        legacy_path,
                        load_path,
                    )
                    load_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(legacy_path), str(load_path))
                    try:
                        legacy_path.parent.rmdir()
                        legacy_path.parent.parent.rmdir()
                    except OSError:
                        pass
                else:
                    _log.info("No existing memory data found | project=%s", ctx.project_id)
                    return False

        try:
            memory_data = ctx.storage.load_json(load_path)
        except Exception as load_exc:
            journal_path = _journal_path(load_path)
            if not ctx.storage.exists(journal_path):
                raise
            _log.warning(
                "memory_load_main_failed_using_journal | project=%s | "
                "path=%s | journal=%s | error=%s",
                ctx.project_id,
                load_path,
                journal_path,
                load_exc,
            )
            memory_data = ctx.storage.load_json(journal_path)

        ctx.set_last_indexed_chapter(memory_data.get("last_indexed_chapter", 0))
        summary_cache_raw = memory_data.get("summary_cache", {})
        normalized_summary_cache: dict[int, dict[str, Any]] = {}
        migrated_count = 0
        if isinstance(summary_cache_raw, dict):
            for raw_chapter, raw_entry in summary_cache_raw.items():
                try:
                    chapter_num = int(raw_chapter)
                except Exception:
                    continue
                normalized = ctx.normalize_summary_cache_entry(raw_entry)
                if normalized is not None:
                    if isinstance(raw_entry, str):
                        migrated_count += 1
                    normalized_summary_cache[chapter_num] = normalized
        ctx.summary_cache.clear()
        ctx.summary_cache.update(normalized_summary_cache)
        chapter_hash_raw = memory_data.get("chapter_content_hash", {})
        normalized_chapter_hash: dict[int, str] = {}
        if isinstance(chapter_hash_raw, dict):
            for raw_chapter, raw_hash in chapter_hash_raw.items():
                try:
                    chapter_num = int(raw_chapter)
                except Exception:
                    continue
                hash_text = str(raw_hash or "").strip()
                if hash_text:
                    normalized_chapter_hash[chapter_num] = hash_text
        for chapter_num, summary_entry in normalized_summary_cache.items():
            if chapter_num in normalized_chapter_hash:
                continue
            summary_hash = str(summary_entry.get("source_hash", "") or "").strip()
            if summary_hash:
                normalized_chapter_hash[chapter_num] = summary_hash
        ctx.chapter_content_hash.clear()
        ctx.chapter_content_hash.update(normalized_chapter_hash)
        ctx.summary_stats.clear()
        ctx.summary_stats.update(ctx.normalize_summary_stats(memory_data.get("summary_stats")))
        if migrated_count > 0:
            ctx.summary_stats["legacy_migrated"] = (
                ctx.summary_stats.get("legacy_migrated", 0) + migrated_count
            )
        ctx.motif_cache.clear()
        ctx.deserialize_episodic_index(memory_data.get("episodic_index", {}))

        motif_state_path = load_path.with_name("motif_state.json")
        motif_loaded = False
        if ctx.storage.exists(motif_state_path):
            try:
                motif_data = ctx.storage.load_json(motif_state_path)
                for k, v in motif_data.get("motif_cache", {}).items():
                    items = []
                    for item in v if isinstance(v, list) else []:
                        if isinstance(item, dict):
                            items.append(item)
                        elif isinstance(item, str):
                            try:
                                items.append(json.loads(item))
                            except (json.JSONDecodeError, TypeError):
                                pass
                    ctx.motif_cache[int(k)] = items
                ctx.deserialize_motif_tracker(motif_data.get("motif_tracker", {}))
                motif_loaded = True
            except Exception as motif_exc:
                _log.warning(
                    "motif_load_from_shard_failed_falling_back | project=%s | error=%s",
                    ctx.project_id,
                    motif_exc,
                )

        if not motif_loaded:
            for k, v in memory_data.get("motif_cache", {}).items():
                items = []
                for item in v if isinstance(v, list) else []:
                    if isinstance(item, dict):
                        items.append(item)
                    elif isinstance(item, str):
                        try:
                            items.append(json.loads(item))
                        except (json.JSONDecodeError, TypeError):
                            pass
                ctx.motif_cache[int(k)] = items
            ctx.deserialize_motif_tracker(memory_data.get("motif_tracker", {}))
        ctx.rebuild_motif_stats_from_cache()

        ctx.load_outline_episodic_from_init()

        _log.info(
            "Memory loaded from disk | project=%s | indexed_chapters=%d | cached_summaries=%d",
            ctx.project_id,
            ctx.get_last_indexed_chapter(),
            len(ctx.summary_cache),
        )

        if ctx.summary_service is not None:
            try:
                asyncio.get_running_loop()
                _log.debug(
                    "load_from_disk called from async context; skipping summary reload | project=%s",
                    ctx.project_id,
                )
            except RuntimeError:
                try:
                    run_async_coro(ctx.summary_service.load_summaries())
                except Exception as exc:
                    _log.warning("Failed to reload summaries from disk: %s", exc)

        return True

    except Exception as exc:
        _log.error("Failed to load memory from disk: %s", exc, exc_info=True)
        return False
