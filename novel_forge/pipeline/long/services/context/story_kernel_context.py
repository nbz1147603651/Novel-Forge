"""StoryKernel context helpers for chapter-generation stages."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.schemas import StoryKernel
from novel_forge.story_kernel.store import StoryKernelStore

_log = logging.getLogger(__name__)
_COMPOSER_CACHE: dict[tuple[str, str, int], ContextComposer] = {}


def story_kernel_db_path(settings: Any, layout: Any) -> str:
    configured = getattr(settings, "story_kernel_db_path", "")
    if isinstance(configured, (str, Path)):
        configured_text = str(configured).strip()
        if configured_text:
            return configured_text
    db_path = getattr(layout, "story_kernel_db_path", None)
    if db_path is not None:
        return str(db_path)
    return str(layout.root / "story_kernel.db")


async def _load_or_create_kernel(store: StoryKernelStore, project_id: str) -> StoryKernel:
    await store.init_db()
    try:
        return await store.load_kernel(project_id)
    except ValueError:
        _log.warning(
            "story_kernel_missing_creating_empty | project_id=%s",
            project_id,
        )
        return await store.create_kernel(project_id)


async def load_story_kernel_composer(runner: Any, bundle: Any) -> ContextComposer | None:
    """Load or create a StoryKernel and return a ContextComposer.

    Returns ``None`` when ``project_id`` is empty so callers can gracefully
    degrade instead of crashing on test fixtures that omit the field.
    """
    raw_project_id = getattr(bundle, "project_id", "")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    if not project_id:
        _log.warning(
            "story_kernel_composer_skip: LongProjectBundle.project_id is empty; "
            "returning None ContextComposer."
        )
        return None

    db_path = story_kernel_db_path(runner._settings, bundle.layout)
    chapter_number = _bundle_chapter_number(bundle)
    cache_key = (db_path, project_id, chapter_number)
    cached = _COMPOSER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    store = StoryKernelStore(
        db_path,
        wal_mode=bool(getattr(runner._settings, "story_kernel_wal_mode", True)),
    )
    kernel: StoryKernel | None = None
    try:
        kernel = await _load_or_create_kernel(store, project_id)
    except Exception as exc:
        _log.warning(
            "story_kernel_composer_load_failed | project_id=%s | error=%s",
            project_id,
            exc,
        )
        return None
    finally:
        try:
            await store.close()
        except Exception as exc:
            _log.warning(
                "story_kernel_composer_close_failed | project_id=%s | error=%s",
                project_id,
                exc,
            )
            if kernel is None:
                return None
    if kernel is None:
        return None
    composer = ContextComposer.from_kernel(kernel)
    _COMPOSER_CACHE[cache_key] = composer
    return composer


def invalidate_story_kernel_composer_cache(
    *,
    db_path: str | Path | None = None,
    project_id: str = "",
    chapter_number: int | None = None,
) -> None:
    """Invalidate cached StoryKernel composer snapshots."""
    doomed: list[tuple[str, str, int]] = []
    db_path_text = str(db_path) if db_path is not None else ""
    for key in _COMPOSER_CACHE:
        key_db_path, key_project_id, key_chapter = key
        if db_path_text and key_db_path != db_path_text:
            continue
        if project_id and key_project_id != project_id:
            continue
        if chapter_number is not None and key_chapter != chapter_number:
            continue
        doomed.append(key)
    for key in doomed:
        _COMPOSER_CACHE.pop(key, None)


def _bundle_chapter_number(bundle: Any) -> int:
    chapter_outline = getattr(bundle, "chapter_outline", None)
    raw = getattr(chapter_outline, "chapter_number", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _collect_canon_fields_for_hash(canon_state: Any, field: str) -> list[Any]:
    """Collect objects from ``canon_state.{field}``, serialising Pydantic models."""
    items = getattr(canon_state, field, None) or []
    if not isinstance(items, list):
        items = []
    result: list[Any] = []
    for item in items:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(str(item))
    return result


def _compute_canon_state_hash(canon_state: Any) -> str:
    """Deterministic SHA-256 from the 5 key canon-state fields.

    Scope: characters (``EntityType.CHARACTER``), relationships, timeline,
    plot_threads, and foreshadowing (promises with ``planted_chapter > 0``).

    This is the hash used by ``_load_issue_panel_pool_for_chapters`` for the
    content-hash AND gate when a report carries a ``canon_state_hash``.
    """
    try:
        characters = [
            e for e in _collect_canon_fields_for_hash(canon_state, "entities")
            if str(e.get("entity_type", "")).lower() == "character"
        ] if hasattr(canon_state, "entities") else []
        relationships = _collect_canon_fields_for_hash(canon_state, "relationships")
        timeline = _collect_canon_fields_for_hash(canon_state, "timeline")
        plot_threads = _collect_canon_fields_for_hash(canon_state, "plot_threads")
        all_promises = _collect_canon_fields_for_hash(canon_state, "promise_ledger")
        foreshadowing = [f for f in all_promises if f.get("planted_chapter", 0) > 0]

        import json

        data_parts: dict[str, list[Any]] = {
            "characters": sorted(characters, key=json.dumps),
            "relationships": sorted(relationships, key=json.dumps),
            "timeline_events": sorted(timeline, key=json.dumps),
            "plot_threads": sorted(plot_threads, key=json.dumps),
            "foreshadowing": sorted(foreshadowing, key=json.dumps),
        }
        raw = json.dumps(data_parts, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(b"").hexdigest()


async def load_canon_state_hash_for_bundle(runner: Any, bundle: Any) -> str:
    """Load the StoryKernel and compute a content-hash from 5 key fields.

    This is intended for use at report-persistence points so each saved
    continuity/causal report carries the ``canon_state_hash`` for the AND-gated
    content-hash filter in the issue-pool loader.

    Returns the empty string when the project has no kernel DB (e.g. test
    fixtures) — callers should still persist the result so that missing-hash
    logic in the grace-period check works correctly.
    """
    try:
        project_id = str(getattr(bundle, "project_id", "") or "").strip()
        if not project_id:
            return ""
        store = StoryKernelStore(story_kernel_db_path(runner._settings, bundle.layout))
        try:
            kernel = await store.load_kernel(project_id)
            return _compute_canon_state_hash(kernel)
        finally:
            await store.close()
    except Exception:
        return ""
