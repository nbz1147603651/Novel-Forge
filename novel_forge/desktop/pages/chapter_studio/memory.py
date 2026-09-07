"""Memory status loading helpers for ChapterStudioPage.

Extracts disk I/O and data transformation for memory panel display
from the main page module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


def load_chapter_focus_characters(
    project_dir: Path,
    chapter_number: int,
    *,
    max_characters: int = 12,
) -> list[str]:
    """Best-effort load chapter focus characters from structured artifacts.

    Uses a generic recursive extractor over chapter artifacts (plan + state packet)
    and recognizes common "character-related" keys instead of relying on one
    rigid schema shape.
    """
    if chapter_number <= 0:
        return []

    try:
        from novel_forge.persistence.models import ProjectLayout
    except Exception:
        return []

    layout = ProjectLayout(project_dir)
    collected: list[str] = []
    seen_norm: set[str] = set()

    _CHARACTER_KEY_HINTS = {
        "character",
        "characters",
        "povcharacter",
        "requiredcharacters",
        "charactersinvolved",
        "keycharacters",
        "currentphasekeycharacters",
        "knowncharacters",
    }

    def _norm_key(raw: Any) -> str:
        return "".join(ch for ch in str(raw or "").lower() if ch.isalnum())

    def _is_candidate_name(raw: Any) -> bool:
        text = str(raw or "").strip()
        if not text:
            return False
        if len(text) > 24:
            return False
        if "\n" in text or "\r" in text:
            return False
        # Filter obvious non-name prose fragments.
        if any(
            p in text for p in ("，", "。", "！", "？", "；", "：", ",", ".", "!", "?", ";", ":")
        ):
            return False
        if text.count(" ") > 2:
            return False
        return True

    def _add_name(raw: Any) -> None:
        name = str(raw or "").strip()
        if not _is_candidate_name(name):
            return
        norm = "".join(name.split()).lower()
        if not norm or norm in seen_norm:
            return
        seen_norm.add(norm)
        collected.append(name)

    def _extract_names(value: Any) -> None:
        if isinstance(value, str):
            _add_name(value)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _extract_names(item)
            return
        if isinstance(value, dict):
            # Some payloads store person refs as {"name": "..."}.
            maybe_name = value.get("name")
            if isinstance(maybe_name, str):
                _add_name(maybe_name)

    def _walk(node: Any) -> None:
        if len(collected) >= max_characters:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                key_norm = _norm_key(key)
                if key_norm in _CHARACTER_KEY_HINTS or (
                    "character" in key_norm
                    and ("required" in key_norm or "pov" in key_norm or "known" in key_norm)
                ):
                    _extract_names(value)
                _walk(value)
            return
        if isinstance(node, (list, tuple, set)):
            for item in node:
                _walk(item)

    plan_path = layout.chapter_plan_path(chapter_number)
    if plan_path.exists():
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                _walk(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    if len(collected) < max_characters:
        packet_path = layout.chapter_state_packet_path(chapter_number)
        if packet_path.exists():
            try:
                with open(packet_path, "r", encoding="utf-8") as f:
                    _walk(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass

    return collected[:max_characters]


def load_memory_status_from_disk(
    project_id: str,
    storage_root: Path | None,
) -> dict[str, Any] | None:
    """Load memory status from disk if available.

    Returns a memory status dict suitable for UI display, or None if no
    memory data exists on disk.

    Tries project_memory.json first (canonical), falls back to
    outline_episodic.json for legacy data.
    """
    if not project_id or not storage_root:
        return None

    if not storage_root.is_absolute():
        storage_root = storage_root.resolve()

    memory_dir = storage_root / project_id / "memory"
    if not memory_dir.is_absolute():
        memory_dir = memory_dir.resolve()

    project_memory_file = memory_dir / "project_memory.json"
    if project_memory_file.exists():
        return _load_from_project_memory(project_memory_file)

    outline_file = memory_dir / "outline_episodic.json"
    if outline_file.exists():
        return _load_from_outline_episodic(outline_file, project_id, storage_root)

    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _merge_motif_cache_stats(
    raw_motifs: dict[str, dict[str, Any]],
    motif_cache: dict[str, Any],
    valid_categories: set[str],
    chapter_motifs: dict[str, set[str]] | None = None,
) -> None:
    """Merge cached motif occurrences into UI stats without double-counting tracker data."""
    cache_stats: dict[str, dict[str, Any]] = {}
    for chapter_key, occurrences in motif_cache.items():
        if not isinstance(occurrences, list):
            continue
        fallback_chapter = _safe_int(chapter_key)
        for occ in occurrences:
            if not isinstance(occ, dict):
                continue
            motif_id = str(occ.get("motif_id") or "").strip()
            if not motif_id:
                continue
            chapter_number = _safe_int(occ.get("chapter_number")) or fallback_chapter
            if chapter_number <= 0:
                continue
            if chapter_motifs is not None:
                chapter_motifs.setdefault(str(chapter_number), set()).add(motif_id)
            stat = cache_stats.setdefault(
                motif_id,
                {
                    "count": 0,
                    "first_chapter": 0,
                    "last_chapter": 0,
                    "name": motif_id,
                    "category": "意象",
                    "description": "",
                },
            )
            stat["count"] += 1
            first = _safe_int(stat.get("first_chapter"))
            if first == 0 or chapter_number < first:
                stat["first_chapter"] = chapter_number
            if chapter_number > _safe_int(stat.get("last_chapter")):
                stat["last_chapter"] = chapter_number

            name = str(occ.get("motif_name") or occ.get("name") or "").strip()
            if name:
                stat["name"] = name
            raw_category = occ.get("category")
            if raw_category in valid_categories:
                stat["category"] = raw_category
            description = str(occ.get("description") or occ.get("context") or "").strip()
            if description and not stat.get("description"):
                stat["description"] = description

    for motif_id, stat in cache_stats.items():
        motif = raw_motifs.setdefault(
            motif_id,
            {
                "motif_id": motif_id,
                "category": stat.get("category", "意象"),
                "description": stat.get("description", ""),
                "occurrence_count": 0,
                "last_chapter": 0,
                "first_chapter": 0,
                "name": stat.get("name") or motif_id,
            },
        )
        motif["occurrence_count"] = max(
            _safe_int(motif.get("occurrence_count")),
            _safe_int(stat.get("count")),
        )
        first = _safe_int(motif.get("first_chapter"))
        cache_first = _safe_int(stat.get("first_chapter"))
        if cache_first and (first == 0 or cache_first < first):
            motif["first_chapter"] = cache_first
        motif["last_chapter"] = max(
            _safe_int(motif.get("last_chapter")),
            _safe_int(stat.get("last_chapter")),
        )
        if not motif.get("description") and stat.get("description"):
            motif["description"] = stat["description"]
        if motif.get("name") == motif_id and stat.get("name"):
            motif["name"] = stat["name"]
        if (
            motif.get("category") not in valid_categories
            and stat.get("category") in valid_categories
        ):
            motif["category"] = stat["category"]


def _normalize_chapter_motifs(raw: Any) -> dict[str, set[str]]:
    """Normalize persisted chapter→motif mappings for UI filtering."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, set[str]] = {}
    for chapter_key, motif_ids in raw.items():
        chapter_number = _safe_int(chapter_key)
        if chapter_number <= 0:
            continue
        if not isinstance(motif_ids, (list, tuple, set)):
            continue
        clean_ids = {str(item).strip() for item in motif_ids if str(item).strip()}
        if clean_ids:
            result[str(chapter_number)] = clean_ids
    return result


def _load_motif_state_shard(
    memory_file: Path,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Load motif data from the sibling ``motif_state.json`` shard.

    Since ``save_to_disk`` was refactored (commit b10e2cbc, 2026-06-05) to
    persist motif data into a separate ``motif_state.json`` file instead of
    embedding it in ``project_memory.json``, the UI disk loader must read the
    shard directly. Returns ``(motif_tracker, motif_cache)`` or ``None`` when
    the shard is missing/malformed — in which case callers fall back to the
    legacy fields embedded in ``project_memory.json``.
    """
    motif_state_path = memory_file.with_name("motif_state.json")
    try:
        with open(motif_state_path, "r", encoding="utf-8") as f:
            motif_state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(motif_state, dict):
        return None

    motif_tracker_raw = motif_state.get("motif_tracker", {})
    motif_cache_raw = motif_state.get("motif_cache", {})
    if not isinstance(motif_tracker_raw, dict):
        motif_tracker_raw = {}
    if not isinstance(motif_cache_raw, dict):
        motif_cache_raw = {}

    # Skip empty shards — fall through to legacy embedded fields.
    has_tracker_payload = bool(motif_tracker_raw.get("motifs")) or bool(
        motif_tracker_raw.get("chapter_motifs")
    )
    has_cache_payload = bool(motif_cache_raw)
    if not has_tracker_payload and not has_cache_payload:
        return None

    return motif_tracker_raw, motif_cache_raw


def _load_from_project_memory(memory_file: Path) -> dict[str, Any] | None:
    """Load memory status from project_memory.json.

    This is the canonical memory file written by MemoryContext.save_to_disk().

    Since the motif persistence split (2026-06-05), motif data is stored in a
    sibling ``motif_state.json`` shard. We prefer that shard when present and
    fall back to the embedded fields inside ``project_memory.json`` for
    backward compatibility with older projects.
    """
    try:
        with open(memory_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Prefer the sibling motif_state.json shard when available.
        shard = _load_motif_state_shard(memory_file)
        if shard is not None:
            motif_tracker, motif_cache = shard
        else:
            motif_tracker = data.get("motif_tracker", {})
            motif_cache = data.get("motif_cache", {})
            if not isinstance(motif_tracker, dict):
                motif_tracker = {}
            if not isinstance(motif_cache, dict):
                motif_cache = {}
        _VALID_MOTIF_CATEGORIES = {"意象", "动作", "感官", "颜色", "声音", "主题", "符号"}
        motifs = []
        if isinstance(motif_tracker, dict):
            # Build raw motif dicts first.
            raw_motifs: dict[str, dict[str, Any]] = {}
            chapter_motifs = _normalize_chapter_motifs(motif_tracker.get("chapter_motifs", {}))
            for motif_id, motif_data in motif_tracker.get("motifs", {}).items():
                if isinstance(motif_data, dict):
                    raw_cat = motif_data.get("category", "意象")
                    category = raw_cat if raw_cat in _VALID_MOTIF_CATEGORIES else "意象"
                    raw_motifs[motif_id] = {
                        "motif_id": motif_id,
                        "category": category,
                        "description": motif_data.get("description", ""),
                        "occurrence_count": motif_data.get("occurrence_count", 0),
                        "last_chapter": motif_data.get("last_appearance_chapter", 0),
                        "first_chapter": motif_data.get("first_appearance_chapter", 0),
                        "name": motif_data.get("name", motif_id),
                    }

            motif_cache = data.get("motif_cache", {})
            if isinstance(motif_cache, dict):
                _merge_motif_cache_stats(
                    raw_motifs,
                    motif_cache,
                    _VALID_MOTIF_CATEGORIES,
                    chapter_motifs,
                )

            motifs = list(raw_motifs.values())
        else:
            chapter_motifs = {}

        episodic_index = data.get("episodic_index", {})
        chapter_events = (
            episodic_index.get("chapter_events", {}) if isinstance(episodic_index, dict) else {}
        )
        try:
            last_indexed = int(data.get("last_indexed_chapter", 0) or 0)
        except (TypeError, ValueError):
            last_indexed = 0
        indexed_chapters = (
            len(chapter_events)
            if isinstance(chapter_events, dict) and len(chapter_events) > 0
            else last_indexed
        )
        outline_data = (
            episodic_index.get("outline_data", {}) if isinstance(episodic_index, dict) else {}
        )
        unresolved_questions = (
            outline_data.get("unresolved_questions", []) if isinstance(outline_data, dict) else []
        )
        if not isinstance(unresolved_questions, list):
            unresolved_questions = []
        unresolved_questions = [
            str(item).strip() for item in unresolved_questions if str(item).strip()
        ]

        outline_stats = (
            episodic_index.get("outline_stats", {}) if isinstance(episodic_index, dict) else {}
        )
        summary_cache = data.get("summary_cache", {})
        if not isinstance(summary_cache, dict):
            summary_cache = {}
        summary_hash_bound = sum(
            1
            for entry in summary_cache.values()
            if isinstance(entry, dict) and entry.get("source_hash")
        )
        summary_stats = data.get("summary_stats", {})
        if not isinstance(summary_stats, dict):
            summary_stats = {}
        chapter_content_hash = data.get("chapter_content_hash", {})
        chapter_hash_tracked = (
            len(chapter_content_hash) if isinstance(chapter_content_hash, dict) else 0
        )

        motif_suggestions = data.get("motif_suggestions", [])

        # Generate suggestions from motif data when none are stored.
        if not motif_suggestions and motifs and last_indexed > 0:
            motif_suggestions = _generate_suggestions_from_motifs(motifs, last_indexed)

        return {
            "indexed_chapters": indexed_chapters,
            "motifs": motifs,
            "motif_suggestions": motif_suggestions,
            "repetition_warnings": data.get("repetition_warnings", []),
            "chapter_motifs": {
                chapter: sorted(motif_ids)
                for chapter, motif_ids in sorted(
                    chapter_motifs.items(),
                    key=lambda item: _safe_int(item[0]),
                )
            },
            "unresolved_questions": unresolved_questions,
            "cached_summaries": len(summary_cache),
            "summary_hash_bound": summary_hash_bound,
            "chapter_hash_tracked": chapter_hash_tracked,
            "summary_stats": summary_stats,
            "last_indexed_chapter": last_indexed,
            "outline_stats": outline_stats,
        }
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        _logger.debug("Failed to load project_memory.json: %s", exc)
        return None


def _load_from_outline_episodic(
    memory_file: Path,
    project_id: str,
    storage_root: Path,
) -> dict[str, Any] | None:
    """Load memory status from outline_episodic.json.

    This is the legacy fallback for outline-only projects or data
    created before the memory module was fully integrated.
    If relationships/theme_tracker are empty, sync from canon data.
    """
    try:
        with open(memory_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        relationships = data.get("relationships", [])
        theme_tracker = data.get("theme_tracker", {})
        motif_tracker = data.get("motif_tracker", {})
        unresolved_questions = data.get("unresolved_questions", [])
        if not isinstance(unresolved_questions, list):
            unresolved_questions = []
        unresolved_questions = [
            str(item).strip() for item in unresolved_questions if str(item).strip()
        ]

        project_root = storage_root / project_id
        if not project_root.is_absolute():
            project_root = project_root.resolve()
        canon_file = project_root / "canon" / "canon_current.json"
        story_bible_file = project_root / "story_bible.json"

        if not relationships or not isinstance(relationships, list) or len(relationships) == 0:
            if canon_file.exists():
                with open(canon_file, "r", encoding="utf-8") as f:
                    canon_data = json.load(f)
                canon_relationships = canon_data.get("relationships", {})
                if canon_relationships:
                    relationships = [
                        {
                            "characters": [rel_id.split("_") if "_" in rel_id else rel_id],
                            "relationship_type": rel_data.get("type", "unknown"),
                            "public_status": rel_data.get("description", ""),
                            "trust": rel_data.get("trust", 50),
                            "tension": rel_data.get("tension", 30),
                        }
                        for rel_id, rel_data in canon_relationships.items()
                        if isinstance(rel_data, dict)
                    ]

        if not theme_tracker or (isinstance(theme_tracker, dict) and len(theme_tracker) == 0):
            themes_loaded = False
            if canon_file.exists():
                with open(canon_file, "r", encoding="utf-8") as f:
                    canon_data = json.load(f)
                canon_themes = canon_data.get("themes", [])
                if canon_themes:
                    theme_tracker = {
                        theme.get("id", f"theme_{i}"): theme for i, theme in enumerate(canon_themes)
                    }
                    themes_loaded = True
            if not themes_loaded and story_bible_file.exists():
                with open(story_bible_file, "r", encoding="utf-8") as f:
                    story_bible = json.load(f)
                bible_themes = story_bible.get("themes", [])
                if bible_themes:
                    theme_tracker = {
                        f"theme_{i}": {
                            "id": f"theme_{i}",
                            "description": theme,
                            "category": "story",
                        }
                        for i, theme in enumerate(bible_themes)
                    }

        if not motif_tracker or (isinstance(motif_tracker, dict) and len(motif_tracker) == 0):
            if canon_file.exists():
                with open(canon_file, "r", encoding="utf-8") as f:
                    canon_data = json.load(f)
                canon_motifs_raw = canon_data.get("plot_threads", [])
                canon_motifs: list[dict[str, Any]] = []
                if isinstance(canon_motifs_raw, dict):
                    canon_motifs = [
                        item for item in canon_motifs_raw.values() if isinstance(item, dict)
                    ]
                elif isinstance(canon_motifs_raw, list):
                    canon_motifs = [item for item in canon_motifs_raw if isinstance(item, dict)]

                if canon_motifs:
                    motifs_payload: dict[str, dict[str, Any]] = {}
                    for i, motif in enumerate(canon_motifs):
                        motif_id = str(
                            motif.get("id")
                            or motif.get("thread_id")
                            or motif.get("title")
                            or f"motif_{i}"
                        )
                        try:
                            last_chapter = int(motif.get("last_touched_chapter", 1) or 1)
                        except (ValueError, TypeError):
                            last_chapter = 1
                        motifs_payload[motif_id] = {
                            "description": str(
                                motif.get("description")
                                or motif.get("summary")
                                or motif.get("title")
                                or ""
                            ).strip(),
                            "category": str(motif.get("category") or "plot_thread"),
                            "occurrence_count": 1,
                            "last_appearance_chapter": last_chapter,
                        }
                    motif_tracker = {"motifs": motifs_payload}

        chapter_outlines = data.get("chapter_outlines", {})
        indexed_chapters = len(chapter_outlines) if isinstance(chapter_outlines, dict) else 0

        _VALID_CATS = {"意象", "动作", "感官", "颜色", "声音", "主题", "符号"}
        motifs = []
        if isinstance(motif_tracker, dict):
            for motif_id, motif_data in motif_tracker.get("motifs", {}).items():
                if isinstance(motif_data, dict):
                    raw_cat = motif_data.get("category", "意象")
                    category = raw_cat if raw_cat in _VALID_CATS else "意象"
                    motifs.append(
                        {
                            "motif_id": motif_id,
                            "category": category,
                            "description": motif_data.get("description", ""),
                            "occurrence_count": motif_data.get("occurrence_count", 0),
                            "last_chapter": motif_data.get("last_appearance_chapter", 0),
                            "first_chapter": motif_data.get("first_appearance_chapter", 0),
                        }
                    )

        outline_entries = 0
        if isinstance(chapter_outlines, dict):
            for co in chapter_outlines.values():
                if isinstance(co, dict):
                    outline_entries += len(co.get("entries", []))

        relationships_count = len(relationships) if isinstance(relationships, list) else 0
        theme_tracker_count = len(theme_tracker) if isinstance(theme_tracker, dict) else 0

        return {
            "indexed_chapters": indexed_chapters,
            "motifs": motifs,
            "motif_suggestions": [],
            "repetition_warnings": [],
            "unresolved_questions": unresolved_questions,
            "cached_summaries": 0,
            "last_indexed_chapter": max(
                (int(k) for k in chapter_outlines.keys() if k.isdigit()), default=0
            ),
            "outline_stats": {
                "total_relationships_tracked": relationships_count,
                "total_themes_tracked": theme_tracker_count,
                "total_outline_entries": outline_entries,
                "unresolved_questions": len(unresolved_questions)
                if isinstance(unresolved_questions, list)
                else 0,
            },
        }
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        _logger.debug("Failed to load outline_episodic.json: %s", exc)
        return None


def _generate_suggestions_from_motifs(
    motifs: list[dict[str, Any]],
    current_chapter: int,
    min_chapters_since: int = 3,
    min_occurrences: int = 2,
) -> list[dict[str, Any]]:
    """Generate motif suggestions from motif data without MotifTracker.

    Uses the same gap-based logic as MotifTracker.get_suggestions_for_chapter
    but operates on raw dicts for the disk-loading path.
    """
    suggestions: list[dict[str, Any]] = []
    for m in motifs:
        occ = m.get("occurrence_count", 0)
        last = m.get("last_chapter", 0)
        if occ < min_occurrences or last <= 0:
            continue
        gap = current_chapter - last
        if gap < min_chapters_since:
            continue
        priority = "high" if gap >= 6 or occ >= 5 else "medium"
        name = m.get("name", "") or m.get("motif_id", "")
        cat = m.get("category", "母题")
        suggestions.append(
            {
                "motif_id": m.get("motif_id", ""),
                "motif_name": name,
                "suggestion": f"该{cat}「{name}」已{gap}章未出现",
                "priority": priority,
            }
        )
    suggestions.sort(key=lambda s: 0 if s["priority"] == "high" else 1)
    return suggestions[:5]


# ── Guardrail data loading ─────────────────────────────────────────────


def load_guardrail_data(
    project_dir: Path,
    chapter_number: int,
) -> dict[str, Any]:
    """Load guardrail constraint data for the given chapter.

    Only an explicitly accepted ``next_chapter_handoff`` is treated as an
    incoming runtime constraint. A raw Plot Guard decision is shown as a
    candidate in the UI, but does not become a writing constraint until the
    current chapter is finalized.

    Returns a dict with keys: prev_constraints, next_constraints,
    next_handoff_status, prev_compliance.
    prev_compliance is always None here — compliance data lives in quality check results
    and would require a separate extraction path.
    """
    result: dict[str, Any] = {
        "prev_constraints": [],
        "next_constraints": [],
        "next_handoff_status": "none",
        "prev_compliance": None,
    }

    if chapter_number <= 0:
        return result

    try:
        from novel_forge.persistence.models import ProjectLayout
    except Exception:
        return result

    layout = ProjectLayout(project_dir)

    def _constraints(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        values: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item or "").split())[:300]
            if not text or text in seen:
                continue
            values.append(text)
            seen.add(text)
            if len(values) >= 5:
                break
        return values

    def _handoff_status(payload: Any, *, target_chapter: int) -> tuple[str | None, list[str]]:
        if not isinstance(payload, dict):
            return None, []
        handoff = payload.get("next_chapter_handoff")
        if not isinstance(handoff, dict):
            return None, []
        try:
            handoff_target = int(handoff.get("target_chapter", 0) or 0)
        except (TypeError, ValueError):
            return "not_accepted", []
        if handoff_target != target_chapter:
            return "not_accepted", []
        status = str(handoff.get("status", "")).strip().lower()
        if status != "accepted":
            return "not_accepted", []
        return "accepted", _constraints(handoff.get("constraints", []))

    def _decision_constraints(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        decision = payload.get("decision")
        if not isinstance(decision, dict):
            return []
        return _constraints(decision.get("next_chapter_constraints", []))

    def _legacy_creative_constraints(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        suggestions = payload.get("suggestions_for_next_chapter")
        if not isinstance(suggestions, str):
            return []
        values: list[str] = []
        in_guard_block = False
        for line in suggestions.splitlines():
            text = line.strip()
            if text.startswith("AI护栏约束"):
                in_guard_block = True
                continue
            if not in_guard_block:
                continue
            if text.startswith("- "):
                values.append(text[2:])
            elif text:
                break
        return _constraints(values)

    # Only an accepted previous handoff is an actual incoming constraint.
    if chapter_number > 1:
        prev_guard_path = layout.guard_report_path(chapter_number - 1)
        status: str | None = None
        if prev_guard_path.exists():
            try:
                with open(prev_guard_path, "r", encoding="utf-8") as f:
                    prev_data = json.load(f)
                status, constraints = _handoff_status(
                    prev_data,
                    target_chapter=chapter_number,
                )
                if status == "accepted":
                    result["prev_constraints"] = constraints
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        if status is None:
            legacy_report_path = layout.creative_report_path(chapter_number - 1)
            if legacy_report_path.exists():
                try:
                    with open(legacy_report_path, "r", encoding="utf-8") as f:
                        result["prev_constraints"] = _legacy_creative_constraints(json.load(f))
                except (OSError, json.JSONDecodeError, KeyError):
                    pass

    # A current decision may be displayed before confirmation, but it must be
    # labelled as pending so the UI does not promise a nonexistent handoff.
    curr_guard_path = layout.guard_report_path(chapter_number)
    if curr_guard_path.exists():
        try:
            with open(curr_guard_path, "r", encoding="utf-8") as f:
                curr_data = json.load(f)
            status, constraints = _handoff_status(
                curr_data,
                target_chapter=chapter_number + 1,
            )
            candidates = _decision_constraints(curr_data)
            if status == "accepted":
                result["next_constraints"] = constraints
                result["next_handoff_status"] = "accepted"
            elif status == "not_accepted":
                result["next_constraints"] = candidates
                result["next_handoff_status"] = "not_accepted"
            elif candidates:
                result["next_constraints"] = candidates
                result["next_handoff_status"] = "pending"
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    return result
