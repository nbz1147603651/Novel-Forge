"""Canon/state persistence helpers for the v2 long-form pipeline."""

from __future__ import annotations

from novel_forge.core.config import get_settings
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterStatePacket
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout


def persist_chapter_state_packet(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    packet: ChapterStatePacket,
) -> None:
    """Persist the assembled chapter state packet."""
    payload = packet.model_dump(mode="json")
    storage.save_json(layout.chapter_state_packet_path(chapter_number), payload)


def persist_bridge(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    bridge: ChapterBridge,
) -> None:
    """Persist the generated bridge artifact and update the cumulative forbidden-repetition index."""
    payload = bridge.model_dump(mode="json")
    storage.save_json(layout.chapter_bridge_path(chapter_number), payload)

    # Incrementally update the forbidden-repetition index (avoids O(N) bridge scans).
    new_items = [s.strip() for s in (bridge.forbidden_repetition or []) if s and s.strip()]
    if new_items:
        _update_forbidden_repetition_index(storage, layout, chapter_number, new_items)


def _update_forbidden_repetition_index(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    new_items: list[str],
    *,
    retention_window: int | None = None,
) -> None:
    """Append new forbidden-repetition items to the cumulative index file."""
    index_path = layout.forbidden_repetition_index_path
    index: dict[str, list[str]] = {}
    if storage.exists(index_path):
        try:
            raw = storage.load_json(index_path)
            if isinstance(raw, dict):
                index = raw
        except Exception:
            pass

    index = _prune_forbidden_repetition_window(
        index,
        chapter_number=chapter_number,
        retention_window=retention_window,
    )

    existing: set[str] = set()
    for items in index.values():
        if isinstance(items, list):
            existing.update(items)

    unique_new = [s for s in new_items if s not in existing]
    if unique_new:
        index[str(chapter_number)] = unique_new
        storage.save_json(index_path, index)
    elif storage.exists(index_path):
        storage.save_json(index_path, index)


def _prune_forbidden_repetition_window(
    index: dict[str, list[str]],
    *,
    chapter_number: int,
    retention_window: int | None,
) -> dict[str, list[str]]:
    """Keep only entries that can still be read by the configured lookback window."""
    if retention_window is None:
        try:
            retention_window = int(get_settings().forbidden_elements_cross_chapter_window or 0)
        except Exception:
            retention_window = 4
    window = max(0, int(retention_window or 0))
    min_chapter = chapter_number if window <= 0 else max(1, chapter_number - window)
    pruned: dict[str, list[str]] = {}
    for key, value in index.items():
        try:
            item_chapter = int(str(key))
        except (TypeError, ValueError):
            if isinstance(value, list):
                pruned[str(key)] = value
            continue
        if min_chapter <= item_chapter <= chapter_number and isinstance(value, list):
            pruned[str(item_chapter)] = [str(item).strip() for item in value if str(item).strip()]
    return pruned

