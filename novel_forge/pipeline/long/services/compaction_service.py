"""Canon compaction — chapter-level and volume-level pruning of stale data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.constants import ForeshadowingStatus
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.outline import StoryOutline, VolumeOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.story_kernel.schemas import PromiseLedger, StoryKernel

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def find_volume_for_chapter(outline: StoryOutline, chapter_number: int) -> VolumeOutline | None:
    if not outline.volume_mode:
        return None
    for vol in outline.volumes:
        if vol.start_chapter <= chapter_number <= vol.end_chapter:
            return vol
    return None


def _focus_contains(focus_text: str, keyword: str) -> bool:
    probe = str(keyword or "").strip().casefold()
    return bool(probe and probe in focus_text)


# ---------------------------------------------------------------------------
# Config DTO for compaction thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactionConfig:
    chapter_compact_interval: int
    chapter_compact_start_chapter: int
    chapter_compact_stale_chapters: int
    chapter_compact_outline_lookahead: int
    chapter_compact_min_active_characters: int
    chapter_compact_target_world_facts: int
    chapter_compact_keep_recent_world_facts: int
    chapter_compact_archive_resolved_foreshadowing_after: int


# ---------------------------------------------------------------------------
# Chapter compaction
# ---------------------------------------------------------------------------


def build_future_outline_focus_text(
    outline: StoryOutline,
    chapter_number: int,
    lookahead: int,
) -> str:
    """Collect the next-N chapters' outline text as mainline protection signals."""
    end = chapter_number + lookahead
    snippets: list[str] = []
    for ch in outline.chapters:
        if ch.chapter_number <= chapter_number or ch.chapter_number > end:
            continue
        snippets.append(ch.title)
        snippets.append(ch.goal)
        snippets.append(ch.pov_character)
        snippets.append(ch.setting)
        snippets.extend(ch.main_plot_points)
        snippets.extend(ch.subplot_points)
        snippets.extend(ch.beats_summary)
        if ch.notes:
            snippets.append(ch.notes)
    return " ".join(s for s in snippets if s).casefold()


def apply_chapter_compaction(
    *,
    state: StoryKernel,
    outline: StoryOutline,
    chapter_number: int,
    character_bible: CharacterBible,
    cfg: CompactionConfig,
) -> tuple[StoryKernel, dict[str, Any] | None]:
    """Run chapter-level canon compaction with outline-aware protections."""
    if chapter_number < cfg.chapter_compact_start_chapter:
        return state, None
    if chapter_number % cfg.chapter_compact_interval != 0:
        return state, None

    compacted = StoryKernel.model_validate(state.model_dump(mode="json"))
    focus_text = build_future_outline_focus_text(
        outline, chapter_number, cfg.chapter_compact_outline_lookahead
    )
    next_chapter = next(
        (c for c in outline.chapters if c.chapter_number == chapter_number + 1),
        None,
    )

    protected_roles = {"protagonist", "antagonist", "deuteragonist", "main", "major"}
    protected_characters = {
        c.name
        for c in character_bible.characters
        if str(c.role or "").strip().lower() in protected_roles
    }
    if next_chapter and next_chapter.pov_character:
        protected_characters.add(next_chapter.pov_character)

    last_mention: dict[str, int] = {}
    for event in compacted.timeline:
        for name in event.characters_involved:
            last_mention[name] = max(last_mention.get(name, 0), event.chapter)

    active_fs = [
        fs
        for fs in compacted.foreshadowing
        if fs.status in (ForeshadowingStatus.PLANTED, ForeshadowingStatus.REINFORCED)
    ]

    removable_characters: list[tuple[int, str]] = []
    for name in list(compacted.get_all_characters().keys()):
        if name in protected_characters:
            continue
        if _focus_contains(focus_text, name):
            continue
        if any(name in (fs.description or "") for fs in active_fs):
            continue
        last_seen = last_mention.get(name, 0)
        if chapter_number - last_seen < cfg.chapter_compact_stale_chapters:
            continue
        removable_characters.append((last_seen, name))

    removable_characters.sort(key=lambda x: (x[0], x[1]))
    archived_characters: list[str] = []
    for _, name in removable_characters:
        if compacted.get_character_count() <= cfg.chapter_compact_min_active_characters:
            break
        char = compacted.get_character_by_name(name)
        if char is None:
            continue
        compacted.archive_character_by_name(name)
        archived_characters.append(name)

    world_items = list(compacted.get_all_world_rules().items())
    archived_world_fact_keys: list[str] = []
    if len(world_items) > cfg.chapter_compact_target_world_facts:
        ranked_world: list[tuple[int, int, str]] = []
        total = len(world_items)
        recent_start = max(0, total - cfg.chapter_compact_keep_recent_world_facts)
        for idx, (key, value) in enumerate(world_items):
            focus_score = (
                1 if (_focus_contains(focus_text, key) or _focus_contains(focus_text, value)) else 0
            )
            recent_boost = 1 if idx >= recent_start else 0
            recency_score = recent_boost * 10_000 + (total - idx)
            ranked_world.append((focus_score, recency_score, key))

        ranked_world.sort(key=lambda item: (item[0], item[1]), reverse=True)
        keep_keys: set[str] = set()
        for _, _, key in ranked_world:
            keep_keys.add(key)
            if len(keep_keys) >= cfg.chapter_compact_target_world_facts:
                break

        kept_world: dict[str, str] = {}
        for key, value in world_items:
            if key in keep_keys:
                kept_world[key] = value
            else:
                compacted.archive_world_rule_by_id(key)
                archived_world_fact_keys.append(key)
        compacted.set_all_world_rules(kept_world)

    archived_foreshadowing_ids: list[str] = []
    archived_fs_ids = {fs.entry_id for fs in compacted.archived_foreshadowing}
    kept_foreshadowing: list[PromiseLedger] = []
    for fs in compacted.foreshadowing:
        if fs.status not in ("paid", "broken"):
            kept_foreshadowing.append(fs)
            continue
        anchor = fs.payoff_chapter or fs.planted_chapter
        if chapter_number - anchor < cfg.chapter_compact_archive_resolved_foreshadowing_after:
            kept_foreshadowing.append(fs)
            continue
        if _focus_contains(focus_text, fs.description):
            kept_foreshadowing.append(fs)
            continue
        if fs.entry_id not in archived_fs_ids:
            compacted.archived_foreshadowing.append(fs)
            archived_fs_ids.add(fs.entry_id)
            archived_foreshadowing_ids.append(fs.entry_id)
    compacted.foreshadowing = kept_foreshadowing

    if not archived_characters and not archived_world_fact_keys and not archived_foreshadowing_ids:
        return compacted, None

    report = {
        "chapter": chapter_number,
        "archived_characters": archived_characters,
        "archived_world_fact_keys": archived_world_fact_keys,
        "archived_foreshadowing_ids": archived_foreshadowing_ids,
        "active_characters": compacted.get_character_count(),
        "active_world_facts": len(compacted.world_rules),
        "active_foreshadowing": len(compacted.foreshadowing),
    }
    return compacted, report


# ---------------------------------------------------------------------------
# Volume compaction
# ---------------------------------------------------------------------------


def apply_volume_compaction(state: StoryKernel, report: VolumeAuditReport) -> StoryKernel:
    """Prune canon noise after a volume, preserving next-volume essentials."""
    compacted = StoryKernel.model_validate(state.model_dump(mode="json"))

    keep_characters = set(report.carry_over_characters)
    retire_characters = {name for name in report.retire_characters if name not in keep_characters}
    for name in sorted(retire_characters):
        compacted.archive_character_by_name(name)

    keep_items = set(report.carry_over_items)
    retire_items = {item for item in report.retire_items if item not in keep_items}
    if retire_items:
        for name, char in compacted.get_all_characters().items():
            if char.inventory:
                updated_inventory = [item for item in char.inventory if item not in retire_items]
                compacted.set_character(
                    name, char.model_copy(update={"inventory": updated_inventory})
                )
        compacted.archived_items = sorted(set(compacted.archived_items) | retire_items)

    keep_world_keys = set(report.carry_over_world_fact_keys)
    retire_world_keys = {key for key in report.retire_world_fact_keys if key not in keep_world_keys}
    for key in retire_world_keys:
        compacted.archive_world_rule_by_id(key)

    carry_over_fs = set(report.carry_over_foreshadowing_ids)
    resolved_fs = {
        fs_id for fs_id in report.resolved_foreshadowing_ids if fs_id not in carry_over_fs
    }
    if resolved_fs:
        for fs in compacted.foreshadowing:
            if fs.entry_id in resolved_fs and fs.status in (
                ForeshadowingStatus.PLANTED,
                ForeshadowingStatus.REINFORCED,
            ):
                fs.status = ForeshadowingStatus.REVEALED
                if fs.resolved_chapter is None:
                    fs.resolved_chapter = compacted.current_chapter

    compacted.active_volume = max(compacted.active_volume, report.volume_number + 1)
    return compacted


# ---------------------------------------------------------------------------
# Extracted payload compaction (dedup / no-op removal before merge)
# ---------------------------------------------------------------------------


def _build_characters_from_entities(
    canon_state: StoryKernel,
) -> dict[str, Any]:
    """Build a name→character-info lookup from StoryKernel entities (replaces old characters dict)."""
    result: dict[str, Any] = {}
    for e in canon_state.entities:
        if e.entity_type == "character":
            result[e.name] = {
                "name": e.name,
                "alive": e.status == "active",
                "location": e.attributes.get("location", ""),
                "emotional_state": e.attributes.get("emotional_state", ""),
                "inventory": e.attributes.get("inventory", []),
                "knowledge": e.attributes.get("knowledge", []),
                "notes": e.notes,
            }
    return result


def compact_extracted_payload(
    canon_state: StoryKernel,
    canon_delta: Any,
    creative_report: Any,
) -> dict[str, int]:
    """Drop no-op/duplicate extraction fields before validation and merge."""
    from novel_forge.core.schemas.story_state import CharacterState
    from novel_forge.story_kernel.schemas import PromiseLedger as FI

    def _character_signature(char: CharacterState | dict[str, Any]) -> tuple[Any, ...]:
        if isinstance(char, dict):
            return (
                char.get("name", ""),
                char.get("alive", True),
                char.get("location", ""),
                char.get("emotional_state", ""),
                tuple(char.get("inventory", [])),
                tuple(char.get("knowledge", [])),
                char.get("notes", ""),
            )
        return (
            char.name,
            char.alive,
            char.location,
            char.emotional_state,
            tuple(char.inventory),
            tuple(char.knowledge),
            char.notes,
        )

    def _foreshadowing_signature(fs: FI) -> tuple[Any, ...]:
        status_val = fs.status.value if hasattr(fs.status, "value") else fs.status
        return (
            fs.entry_id,
            fs.description,
            fs.planted_chapter,
            status_val,
            fs.payoff_chapter,
            fs.notes,
        )

    stats: dict[str, int] = {
        "dropped_noop_character_updates": 0,
        "dropped_noop_world_facts": 0,
        "dropped_noop_foreshadowing_updates": 0,
        "dropped_duplicate_events": 0,
        "dropped_known_report_characters": 0,
        "dropped_duplicate_report_characters": 0,
    }

    existing_characters = _build_characters_from_entities(canon_state)

    pruned_updates: dict[str, Any] = {}
    for name, updated in canon_delta.character_updates.items():
        existing = existing_characters.get(name)
        if existing is not None and _character_signature(existing) == _character_signature(updated):
            stats["dropped_noop_character_updates"] += 1
            continue
        pruned_updates[name] = updated
    canon_delta.character_updates = pruned_updates

    pruned_world: dict[str, str] = {}
    for key, value in canon_delta.new_world_facts.items():
        # Check if world fact already known (from archived_world_facts or world_rules)
        if (
            key in canon_state.archived_world_facts
            and canon_state.archived_world_facts[key] == value
        ):
            stats["dropped_noop_world_facts"] += 1
            continue
        if any(r.content == value for r in canon_state.world_rules):
            stats["dropped_noop_world_facts"] += 1
            continue
        pruned_world[key] = value
    canon_delta.new_world_facts = pruned_world

    existing_fs = {fs.entry_id: fs for fs in canon_state.promise_ledger}
    latest_fs_updates: dict[str, Any] = {}
    for fs in canon_delta.foreshadowing_updates:
        latest_fs_updates[fs.entry_id] = fs
    pruned_fs: list[Any] = []
    for fs in latest_fs_updates.values():
        existing_fs_item = existing_fs.get(fs.entry_id)
        if existing_fs_item is not None and _foreshadowing_signature(
            existing_fs_item
        ) == _foreshadowing_signature(fs):
            stats["dropped_noop_foreshadowing_updates"] += 1
            continue
        pruned_fs.append(fs)
    canon_delta.foreshadowing_updates = pruned_fs

    seen_event_keys: set[tuple[Any, ...]] = set()
    deduped_events: list[Any] = []
    for event in canon_delta.new_events:
        key = (
            event.chapter,
            event.event.strip(),
            tuple(event.characters_involved),
            event.in_story_time.strip(),
        )
        if key in seen_event_keys:
            stats["dropped_duplicate_events"] += 1
            continue
        seen_event_keys.add(key)
        deduped_events.append(event)
    canon_delta.new_events = deduped_events

    existing_names = set(existing_characters.keys())
    kept_new_characters = []
    seen_new_character_names: set[str] = set()
    for item in creative_report.new_characters:
        name = str(item.name or "").strip()
        if not name:
            stats["dropped_duplicate_report_characters"] += 1
            continue
        if name in existing_names:
            stats["dropped_known_report_characters"] += 1
            continue
        if name in seen_new_character_names:
            stats["dropped_duplicate_report_characters"] += 1
            continue
        seen_new_character_names.add(name)
        kept_new_characters.append(item)
    creative_report.new_characters = kept_new_characters

    return stats
