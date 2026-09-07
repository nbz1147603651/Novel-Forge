"""Context-building helpers — profile selection, alignment, creative-report compaction."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Callable, cast

from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.parsing.text_utils import normalize_input_list, trim_text
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.core.utils.string import carry_forward_text
from novel_forge.pipeline.long.services.generation.bridge_service import (
    is_opening_echo,
    normalize_overlap_text,
    remove_opening_echo,
)
from novel_forge.story_kernel.schemas import StoryKernel

# ---------------------------------------------------------------------------
# Creative-report compaction
# ---------------------------------------------------------------------------


def compact_previous_creative_report(
    report: dict[str, Any] | None,
    *,
    max_deviations: int,
    max_new_characters: int,
    source_chars: int,
) -> dict[str, Any] | None:
    """Keep only compact, relevant fields from previous creative report."""
    if not isinstance(report, dict):
        return None

    compact: dict[str, Any] = {}

    raw_deviations = report.get("plot_deviations", [])
    if isinstance(raw_deviations, list):
        compact_deviations: list[dict[str, str]] = []
        for dev in raw_deviations[:max_deviations]:
            if not isinstance(dev, dict):
                continue
            compact_deviations.append(
                {
                    "outline_plan": trim_text(dev.get("outline_plan", ""), limit=source_chars),
                    "actual_plot": trim_text(dev.get("actual_plot", ""), limit=source_chars),
                    "deviation_level": trim_text(dev.get("deviation_level", ""), limit=20),
                    "reason": trim_text(dev.get("reason", ""), limit=source_chars),
                    "impact_on_future": trim_text(
                        dev.get("impact_on_future", ""), limit=source_chars
                    ),
                }
            )
        if compact_deviations:
            compact["plot_deviations"] = compact_deviations

    suggestion = trim_text(
        report.get("suggestions_for_next_chapter", ""),
        limit=source_chars,
    )
    if suggestion:
        compact["suggestions_for_next_chapter"] = suggestion

    raw_new_chars = report.get("new_characters", [])
    if isinstance(raw_new_chars, list):
        compact_chars: list[dict[str, str]] = []
        for ch in raw_new_chars[:max_new_characters]:
            if not isinstance(ch, dict):
                continue
            compact_chars.append(
                {
                    "name": trim_text(ch.get("name", ""), limit=40),
                    "role_in_story": trim_text(ch.get("role_in_story", ""), limit=20),
                    "description": trim_text(ch.get("description", ""), limit=source_chars),
                }
            )
        if compact_chars:
            compact["new_characters"] = compact_chars

    return compact or None


# ---------------------------------------------------------------------------
# Prompt-text target collection (for context compression)
# ---------------------------------------------------------------------------


def iter_prompt_text_targets(
    previous_creative_report: dict[str, Any] | None,
    character_profiles: list[dict[str, Any]],
    *,
    report_text_chars: int,
    profile_field_chars: int,
) -> list[dict[str, Any]]:
    """Build the list of (owner, key, limit) targets eligible for compression."""
    targets: list[dict[str, Any]] = []

    def _add(owner: dict[str, Any], key: str, limit: int, tag: str) -> None:
        if key not in owner:
            return
        value = str(owner.get(key, "") or "").strip()
        owner[key] = value
        targets.append({"owner": owner, "key": key, "limit": limit, "tag": tag})

    if previous_creative_report:
        for dev in previous_creative_report.get("plot_deviations", []):
            if not isinstance(dev, dict):
                continue
            _add(dev, "outline_plan", limit=report_text_chars, tag="prev_dev_outline")
            _add(dev, "actual_plot", limit=report_text_chars, tag="prev_dev_actual")
            _add(dev, "reason", limit=report_text_chars, tag="prev_dev_reason")
            _add(dev, "impact_on_future", limit=report_text_chars, tag="prev_dev_impact")

        _add(
            previous_creative_report,
            "suggestions_for_next_chapter",
            limit=report_text_chars,
            tag="prev_suggestion",
        )
        for char in previous_creative_report.get("new_characters", []):
            if isinstance(char, dict):
                _add(char, "description", limit=report_text_chars, tag="prev_new_char_desc")

    for profile in character_profiles:
        if not isinstance(profile, dict):
            continue
        _add(profile, "appearance", limit=profile_field_chars, tag="profile_appearance")
        _add(profile, "personality", limit=profile_field_chars, tag="profile_personality")
        _add(profile, "backstory", limit=profile_field_chars, tag="profile_backstory")
        _add(profile, "arc", limit=profile_field_chars, tag="profile_arc")
        _add(profile, "notes", limit=120, tag="profile_notes")

        rels = profile.get("relationships")
        if isinstance(rels, dict):
            for rel_key in list(rels.keys()):
                value = str(rels.get(rel_key, "") or "").strip()
                rels[rel_key] = value
                targets.append(
                    {"owner": rels, "key": rel_key, "limit": 120, "tag": "profile_relationship"}
                )

    return targets


def _read_target_value(target: dict[str, Any]) -> str:
    owner = target["owner"]
    key = str(target["key"])
    if isinstance(owner, dict):
        return str(owner.get(key, "") or "").strip()
    return str(getattr(owner, key, "") or "").strip()


def _write_target_value(target: dict[str, Any], value: str) -> None:
    owner = target["owner"]
    key = str(target["key"])
    if isinstance(owner, dict):
        owner[key] = value
        return
    setattr(owner, key, value)


def _coerce_value(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _join_compact(parts: list[str], *, limit: int) -> str:
    normalized = [str(part or "").strip() for part in parts if str(part or "").strip()]
    return trim_text("；".join(normalized), limit=limit)


def _format_recent_event(event: Any) -> str:
    chapter = _coerce_value(event, "chapter", 0)
    description = trim_text(str(_coerce_value(event, "event", "") or ""), limit=80)
    characters = _coerce_value(event, "characters_involved", []) or []
    if isinstance(characters, list):
        joined = "、".join(str(name).strip() for name in characters[:3] if str(name).strip())
    else:
        joined = ""
    label = f"[第{chapter}章]" if chapter else "[近期]"
    if joined:
        return f"{label}{description}（涉及{joined}）"
    return f"{label}{description}"


def _format_relationship(rel: Any) -> str:
    characters = _coerce_value(rel, "characters", []) or []
    if isinstance(characters, list) and characters:
        pair = " ↔ ".join(str(name).strip() for name in characters[:2] if str(name).strip())
    else:
        char_a = str(_coerce_value(rel, "character_a", "") or "").strip()
        char_b = str(_coerce_value(rel, "character_b", "") or "").strip()
        pair = " ↔ ".join(part for part in [char_a, char_b] if part)
    status = trim_text(str(_coerce_value(rel, "public_status", "") or ""), limit=40)
    shift = trim_text(str(_coerce_value(rel, "last_shift_event", "") or ""), limit=60)
    if pair and status and shift:
        return f"{pair}：{status}；最近变化={shift}"
    if pair and status:
        return f"{pair}：{status}"
    return trim_text(pair or shift, limit=90)


def _format_plot_thread(thread: Any) -> str:
    title = trim_text(str(_coerce_value(thread, "title", "") or ""), limit=40)
    status = trim_text(str(_coerce_value(thread, "status", "") or ""), limit=24)
    payoff = trim_text(str(_coerce_value(thread, "next_payoff_window", "") or ""), limit=50)
    blocking = trim_text(str(_coerce_value(thread, "blocking_condition", "") or ""), limit=50)
    summary = trim_text(str(_coerce_value(thread, "summary", "") or ""), limit=80)
    parts = [title]
    if status:
        parts.append(f"状态={status}")
    if payoff:
        parts.append(f"回收窗口={payoff}")
    if blocking:
        parts.append(f"阻碍={blocking}")
    elif summary:
        parts.append(summary)
    return _join_compact(parts, limit=120)


def _format_foreshadowing(item: Any) -> str:
    description = trim_text(str(_coerce_value(item, "description", "") or ""), limit=80)
    status = trim_text(str(_coerce_value(item, "status", "") or ""), limit=16)
    if status:
        return f"{description}（{status}）"
    return description


def _format_exit_state(exit_state: Any) -> str:
    if exit_state is None:
        return ""
    parts: list[str] = []
    time_marker = trim_text(str(_coerce_value(exit_state, "time_marker", "") or ""), limit=40)
    location = trim_text(str(_coerce_value(exit_state, "location", "") or ""), limit=40)
    pov = trim_text(str(_coerce_value(exit_state, "pov", "") or ""), limit=24)
    active_goals = _coerce_value(exit_state, "active_goals", []) or []
    open_questions = _coerce_value(exit_state, "open_questions", []) or []
    if time_marker:
        parts.append(f"时间={time_marker}")
    if location:
        parts.append(f"地点={location}")
    if pov:
        parts.append(f"POV={pov}")
    if isinstance(active_goals, list) and active_goals:
        parts.append(
            "目标="
            + "、".join(
                trim_text(str(item), limit=24) for item in active_goals[:2] if str(item).strip()
            )
        )
    if isinstance(open_questions, list) and open_questions:
        parts.append(
            "未决="
            + "、".join(
                trim_text(str(item), limit=28) for item in open_questions[:2] if str(item).strip()
            )
        )
    return _join_compact(parts, limit=180)


def estimate_context_budget_pressure(
    previous_creative_report: dict[str, Any] | None,
    character_profiles: list[dict[str, Any]],
    *,
    packet: Any | None = None,
) -> dict[str, Any]:
    """Estimate prompt pressure from mutable chapter context.

    The estimator uses character count as a cheap proxy for prompt weight.
    This is intentionally approximate: we only need enough signal to decide
    whether we should compress aggressively.
    """
    # Estimate total character count from profiles and report.
    profile_chars = sum(len(str(p)) for p in character_profiles)
    report_chars = len(str(previous_creative_report)) if previous_creative_report else 0
    packet_chars = len(str(packet)) if packet is not None else 0
    estimated_chars = profile_chars + report_chars + packet_chars

    # Simple pressure heuristic: more content ⇒ higher pressure.
    budget_pressure = max(0.0, min(1.0, estimated_chars / 8000))

    return {
        "budget_pressure": round(budget_pressure, 3),
        "estimated_chars": estimated_chars,
    }


# ---------------------------------------------------------------------------
# Known-character list for extraction prompts
# ---------------------------------------------------------------------------


def build_known_characters_for_extract(
    canon_state: StoryKernel,
    canon_context: Any,
    character_profiles: list[dict[str, Any]],
) -> list[str]:
    """Build a bounded known-character list for extraction prompts."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        normalized = str(name or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)

    for name in canon_context.characters.keys():
        _add(name)
    for profile in character_profiles:
        _add(str(profile.get("name", "")))
    if not ordered:
        for entity in canon_state.entities:
            if entity.entity_type == "character":
                _add(entity.name)

    return ordered


# ---------------------------------------------------------------------------
# Alignment repair helpers
# ---------------------------------------------------------------------------


def alignment_field_list(report: AlignmentReport | dict[str, Any], field: str) -> list[str]:
    """Extract and normalize a field list from an alignment report."""
    if isinstance(report, dict):
        raw = report.get(field, [])
    else:
        raw = getattr(report, field, [])
    return normalize_input_list(raw)


def build_alignment_repair_subplot_summary(
    before_report: AlignmentReport | dict[str, Any],
    after_report: AlignmentReport | dict[str, Any],
) -> dict[str, Any]:
    supportive_before = alignment_field_list(before_report, "supportive_subplot_points")
    supportive_after = alignment_field_list(after_report, "supportive_subplot_points")
    disruptive_before = alignment_field_list(before_report, "weak_subplot_points")
    disruptive_after = alignment_field_list(after_report, "weak_subplot_points")

    supportive_after_set = set(supportive_after)
    supportive_before_set = set(supportive_before)
    disruptive_after_set = set(disruptive_after)
    disruptive_before_set = set(disruptive_before)

    return {
        "preserved_supportive_subplots": [
            i for i in supportive_before if i in supportive_after_set
        ],
        "newly_supported_subplots": [i for i in supportive_after if i not in supportive_before_set],
        "converged_disruptive_subplots": [
            i for i in disruptive_before if i not in disruptive_after_set
        ],
        "remaining_disruptive_subplots": [
            i for i in disruptive_after if i in disruptive_before_set
        ],
        "newly_detected_disruptive_subplots": [
            i for i in disruptive_after if i not in disruptive_before_set
        ],
        "supportive_before_count": len(supportive_before),
        "supportive_after_count": len(supportive_after),
        "disruptive_before_count": len(disruptive_before),
        "disruptive_after_count": len(disruptive_after),
    }


# ---------------------------------------------------------------------------
# Canon context pre-filtering for plan prompts
# ---------------------------------------------------------------------------

# Fields from canon_context that must always be injected (INVARIANTS.md §5).
_NON_COMPRESSIBLE_CANON_FIELDS: tuple[str, ...] = (
    "immutable_facts",
    "active_foreshadowing",
    "recent_events",
)


def precompute_plan_canon_context(
    canon_context: dict[str, Any],
    involved_characters: list[str] | None = None,
) -> dict[str, Any]:
    """Filter *canon_context* to only include planning-relevant data.

    Reduces token bloat by:
    1. Keeping only non-compressible whitelist fields
       (``immutable_facts``, ``active_foreshadowing``, ``recent_events``).
    2. Dropping characters from ``canon_context.characters`` that are not in
       *involved_characters* (when provided).

    Args:
        canon_context: Full canon context dict from StoryKernel / CanonRetriever.
        involved_characters: Names of characters appearing in this chapter.
            When None or empty, all characters are retained.

    Returns:
        A filtered canon_context dict safe for prompt injection.
    """
    if not isinstance(canon_context, dict):
        return {}

    filtered: dict[str, Any] = {}

    # Always include non-compressible whitelist fields
    for field_name in _NON_COMPRESSIBLE_CANON_FIELDS:
        value = canon_context.get(field_name)
        if value:
            filtered[field_name] = value

    # Filter characters to only those involved in this chapter
    raw_characters = canon_context.get("characters")
    if raw_characters:
        if isinstance(raw_characters, dict) and involved_characters:
            involved_set = {name.strip() for name in involved_characters if name and name.strip()}
            if involved_set:
                filtered["characters"] = {
                    name: state
                    for name, state in raw_characters.items()
                    if name.strip() in involved_set
                }
            else:
                # involved_characters was provided but empty after stripping
                filtered["characters"] = raw_characters
        else:
            # No filtering needed (list shape or no involved_characters filter)
            filtered["characters"] = raw_characters

    return filtered


# ---------------------------------------------------------------------------
# Task-specific context briefs (bridge, planning, continuity)
# ---------------------------------------------------------------------------


def build_task_context_briefs(
    packet: Any,
    previous_creative_report: dict[str, Any] | None,
    *,
    bridge_seed_chars: int = 600,
    planning_seed_chars: int = 800,
    continuity_seed_chars: int = 700,
) -> dict[str, str]:
    """Build compressed context briefs for bridge, plan, and continuity prompts.

    Returns a dict mapping packet attribute names to brief text strings:
    - ``bridge_context_brief``: Compact brief for bridge generation
    - ``planning_context_brief``: Compact brief for chapter planning
    - ``continuity_context_brief``: Compact brief for continuity evaluation/repair

    Each brief is a concise summary of key narrative context (open questions,
    active threads, character states) that helps the LLM focus on relevant details
    without consuming excessive prompt tokens.
    """
    briefs: dict[str, str] = {}

    # Extract common context from packet
    exit_state = getattr(packet, "previous_exit_state", None)
    active_threads = getattr(packet, "active_plot_threads", []) or []

    # ── Bridge context brief ──────────────────────────────────────────────
    bridge_parts: list[str] = []

    # Previous chapter ending
    prev_ending = str(getattr(packet, "previous_chapter_ending", "") or "").strip()
    if prev_ending:
        bridge_parts.append(f"前章结尾：{trim_text(prev_ending, limit=120)}")

    # Open questions from exit state
    if exit_state is not None:
        open_qs = [
            str(q).strip()
            for q in list(getattr(exit_state, "open_questions", []) or [])[:3]
            if str(q).strip()
        ]
        if open_qs:
            bridge_parts.append("未解悬念：" + "；".join(open_qs))

    # POV and location
    chapter_outline = getattr(packet, "chapter_outline", None)
    if chapter_outline:
        pov = str(getattr(chapter_outline, "pov_character", "") or "").strip()
        if pov:
            bridge_parts.append(f"POV角色：{pov}")

    if bridge_parts:
        briefs["bridge_context_brief"] = trim_text("；".join(bridge_parts), limit=bridge_seed_chars)

    # ── Planning context brief ────────────────────────────────────────────
    planning_parts: list[str] = []

    # Active plot threads (open/ongoing only)
    open_threads = [
        str(getattr(t, "title", "") or "").strip()
        for t in active_threads
        if str(getattr(t, "status", "") or "").lower() in {"open", "active", "ongoing", "进行中"}
        and str(getattr(t, "title", "") or "").strip()
    ][:3]
    if open_threads:
        planning_parts.append("进行中线索：" + "；".join(open_threads))

    # Must-carry-forward constraints
    must_fwd = [
        carry_forward_text(item)
        for item in list(getattr(packet, "must_carry_forward", []) or [])[:3]
        if carry_forward_text(item)
    ]
    if must_fwd:
        planning_parts.append("必须延续：" + "；".join(must_fwd))

    # Previous creative report suggestions
    if previous_creative_report:
        suggestion = str(
            previous_creative_report.get("suggestions_for_next_chapter", "") or ""
        ).strip()
        if suggestion:
            planning_parts.append(f"创作建议：{trim_text(suggestion, limit=100)}")

    if planning_parts:
        briefs["planning_context_brief"] = trim_text(
            "；".join(planning_parts), limit=planning_seed_chars
        )

    # ── Continuity context brief ──────────────────────────────────────────
    continuity_parts: list[str] = []

    # Key characters in this chapter
    known_chars = [
        str(name).strip()
        for name in list(getattr(packet, "known_characters", []) or [])[:5]
        if str(name).strip()
    ]
    if known_chars:
        continuity_parts.append("关键角色：" + "、".join(known_chars))

    # Previous chapter summary from creative report
    if previous_creative_report:
        prev_summary = str(previous_creative_report.get("chapter_summary", "") or "").strip()
        if prev_summary:
            continuity_parts.append(f"前章摘要：{trim_text(prev_summary, limit=100)}")

    # Location/time from exit state
    if exit_state is not None:
        location = str(getattr(exit_state, "location", "") or "").strip()
        time_marker = str(getattr(exit_state, "time_marker", "") or "").strip()
        if location or time_marker:
            loc_parts = []
            if location:
                loc_parts.append(f"地点={location}")
            if time_marker:
                loc_parts.append(f"时间={time_marker}")
            continuity_parts.append("场景：" + "；".join(loc_parts))

    if continuity_parts:
        briefs["continuity_context_brief"] = trim_text(
            "；".join(continuity_parts), limit=continuity_seed_chars
        )

    return briefs


def iter_task_brief_targets(
    packet: Any,
    *,
    bridge_chars: int,
    planning_chars: int,
    continuity_chars: int,
) -> list[dict[str, Any]]:
    """Return compression targets for task-specific brief fields on the packet.

    Each target is a dict with keys ``owner``, ``key``, ``limit``, ``tag``
    compatible with the compression pipeline.
    """
    targets: list[dict[str, Any]] = []

    brief_fields = [
        ("bridge_context_brief", bridge_chars, "bridge_brief"),
        ("planning_context_brief", planning_chars, "planning_brief"),
        ("continuity_context_brief", continuity_chars, "continuity_brief"),
    ]

    for field_name, limit, tag in brief_fields:
        value = str(getattr(packet, field_name, "") or "").strip()
        if value:
            targets.append(
                {
                    "owner": packet,
                    "key": field_name,
                    "limit": limit,
                    "tag": tag,
                }
            )

    return targets


# ---------------------------------------------------------------------------
# Character-profile selection
# ---------------------------------------------------------------------------


def _coerce_rel_characters(rel: Any) -> list[str]:
    chars = _coerce_value(rel, "characters", []) or []
    if isinstance(chars, list):
        cleaned = [str(name).strip() for name in chars if str(name).strip()]
        if cleaned:
            return cleaned[:3]
    char_a = str(_coerce_value(rel, "character_a", "") or "").strip()
    char_b = str(_coerce_value(rel, "character_b", "") or "").strip()
    return [name for name in [char_a, char_b] if name]


def _format_profile_relationship(rel: Any) -> str:
    """Compact canon relationship fields into a profile-friendly sentence."""
    status = trim_text(str(_coerce_value(rel, "public_status", "") or ""), limit=80)
    shift = trim_text(str(_coerce_value(rel, "last_shift_event", "") or ""), limit=90)
    notes = trim_text(str(_coerce_value(rel, "notes", "") or ""), limit=70)
    parts: list[str] = []
    if status:
        parts.append(status)
    if shift:
        parts.append(f"最近变化：{shift}")
    elif notes:
        parts.append(notes)
    if not parts:
        return "关系待明确"
    return trim_text("；".join(parts), limit=140)


def _build_profile_relationships_from_canon(canon_context: Any) -> dict[str, list[tuple[str, str]]]:
    """Build per-character relationship cards from selected canon edges."""
    rel_map: dict[str, list[tuple[str, str]]] = {}
    raw_rels = list(getattr(canon_context, "active_relationships", []) or [])
    for rel in raw_rels:
        chars = _coerce_rel_characters(rel)
        if len(chars) < 2:
            continue
        desc = _format_profile_relationship(rel)
        for idx, src in enumerate(chars[:2]):
            dst = chars[1 - idx]
            if not src or not dst:
                continue
            rel_map.setdefault(src, []).append((dst, desc))
    return rel_map


def select_character_profiles(
    character_bible: CharacterBible,
    canon_context: Any,
    pov_character: str,
    *,
    max_profiles: int,
    source_field_chars: int,
    max_relationships: int,
    relationship_source_chars: int,
    involved_characters: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Project profiles for the chapter's evidence-backed cast.

    ``max_profiles`` is retained as a compatibility/configuration hint, but it
    is deliberately not used as a hard cutoff.  Cast membership is a semantic
    decision made by the outline and its deterministic evidence resolver; once
    a character is proven relevant, silently dropping the tail would corrupt
    the chapter contract.

    Unlike the historical implementation, this function never pads the result
    with recently active canon characters or remaining CharacterBible entries.
    """
    _ = max_profiles
    by_name = {c.name: c for c in character_bible.characters}
    ordered_names: list[str] = []
    if pov_character and pov_character in by_name:
        ordered_names.append(pov_character)

    # Only the evidence-backed chapter cast is eligible. Explicitly involved
    # retired/deceased characters remain valid (for flashbacks, projections,
    # letters, and other chapter-local appearances).
    if involved_characters:
        for name in involved_characters:
            if name and name in by_name and name not in ordered_names:
                ordered_names.append(name)

    key_names = set(ordered_names)

    canon_rel_map = _build_profile_relationships_from_canon(canon_context)

    selected: list[dict[str, Any]] = []
    for name in ordered_names:
        profile = by_name.get(name)
        if profile is None:
            continue
        ordered_relations: dict[str, str] = {}

        # Priority A: chapter-relevant canon relations (already filtered/ranked upstream).
        for other, rel_text in canon_rel_map.get(profile.name, []):
            if other and other not in ordered_relations:
                ordered_relations[other] = trim_text(rel_text, limit=relationship_source_chars)

        # Priority B: static relations, with other chapter-cast members first.
        for other, rel in profile.relationships.items():
            key = trim_text(other, limit=40)
            if key and key not in ordered_relations:
                ordered_relations[key] = trim_text(rel, limit=relationship_source_chars)

        rel_items = list(ordered_relations.items())
        rel_items.sort(key=lambda item: (0 if item[0] in key_names else 1, item[0]))
        selected.append(
            {
                "name": profile.name,
                "role": profile.role,
                "age": trim_text(profile.age, limit=40),
                "gender": profile.gender,
                "status": getattr(profile, "status", "active"),
                "appearance": trim_text(profile.appearance, limit=source_field_chars),
                "personality": trim_text(profile.personality, limit=source_field_chars),
                "backstory": trim_text(profile.backstory, limit=source_field_chars),
                "arc": trim_text(profile.arc, limit=source_field_chars),
                "relationships": {
                    trim_text(other, limit=40): trim_text(rel, limit=relationship_source_chars)
                    for other, rel in rel_items[:max_relationships]
                },
                "voice": trim_text(getattr(profile, "voice", ""), limit=source_field_chars),
                "notes": trim_text(profile.notes, limit=min(300, source_field_chars)),
            }
        )
    return selected


def resolve_chapter_character_names(
    character_bible: CharacterBible,
    chapter_outline: Any,
) -> list[str]:
    """Resolve the chapter cast from explicit semantic evidence in its outline.

    The legacy ``involved_characters`` array is treated as a compatibility hint,
    not as proof: older outlines often copied every phase character into every
    chapter.  A non-POV character must be named in an executable outline field
    (goal, beats, plot points, emotional plan, scene goals, hook, or payoff).
    This gives old projects the same scoping behaviour as newly generated ones.
    """
    known_names = [profile.name for profile in character_bible.characters if profile.name]
    known_set = set(known_names)
    pov = str(getattr(chapter_outline, "pov_character", "") or "").strip()
    texts: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                _add(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                _add(item)
            return
        if hasattr(value, "model_dump"):
            _add(value.model_dump(mode="json", exclude={"schema_version", "created_at"}))
            return
        text = str(value or "").strip()
        if text:
            texts.append(text)

    for field_name in (
        "goal",
        "beats_summary",
        "main_plot_points",
        "subplot_points",
        "emotional_plan",
        "scene_design_goals",
        "expected_hook",
        "expected_payoffs",
    ):
        _add(getattr(chapter_outline, field_name, None))

    ordered: list[str] = []
    if pov and pov in known_set:
        ordered.append(pov)
    for text in texts:
        matches = sorted(
            (
                (text.find(name), -len(name), name)
                for name in known_names
                if name not in ordered and name in text
            ),
            key=lambda item: (item[0], item[1]),
        )
        occupied: list[tuple[int, int]] = []
        for position, _negative_length, name in matches:
            end = position + len(name)
            if any(position < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((position, end))
            if name not in ordered:
                ordered.append(name)
    return ordered


# ---------------------------------------------------------------------------
# Opening-echo detection (re-exported from bridge_service)
# ---------------------------------------------------------------------------

__all__ = [
    "compact_previous_creative_report",
    "iter_prompt_text_targets",
    "build_known_characters_for_extract",
    "alignment_field_list",
    "build_alignment_repair_subplot_summary",
    "normalize_overlap_text",
    "is_opening_echo",
    "remove_opening_echo",
    "compress_prompt_context",
    "precompute_plan_canon_context",
    "build_task_context_briefs",
    "iter_task_brief_targets",
    "resolve_chapter_character_names",
    "select_character_profiles",
]


def _verify_compression_quality(original: str, compressed: str) -> float:
    """Heuristic quality score for compressed text.

    Scores 0.0–1.0 based on compression ratio and causal-marker preservation.
    Inline implementation to avoid circular deps with AdaptiveCompressionService.
    """
    score = 1.0
    ratio = len(compressed) / len(original) if original else 1.0
    if ratio < 0.2:
        score -= 0.3
    elif ratio < 0.4:
        score -= 0.1
    # Check causal markers
    markers = ["因为", "所以", "但是", "如果", "决定", "发现"]
    orig_markers = sum(original.count(m) for m in markers)
    comp_markers = sum(compressed.count(m) for m in markers)
    if orig_markers > 0:
        marker_ratio = comp_markers / orig_markers
        score = score * 0.7 + marker_ratio * 0.3
    return max(0.0, min(1.0, score))


async def _verify_compression_with_llm(
    *,
    original: str,
    compressed: str,
    call_with_retry: Callable[..., Awaitable[dict[str, Any]]],
    temperature: float,
    verify_max_tokens: int,
) -> dict[str, Any]:
    """Ask the dedicated verifier to judge a compressed context block."""
    max_tokens = max(512, min(int(verify_max_tokens or 512), 2048))
    return await call_with_retry(
        TaskType.VERIFY_COMPRESSION,
        {
            "original": original,
            "compressed": compressed,
            "required_facts": [],
        },
        max_tokens=max_tokens,
        temperature=temperature,
        required_keys=("quality_score", "recommendation"),
        max_retries=1,
    )


async def compress_prompt_context(
    previous_creative_report: dict[str, Any] | None,
    character_profiles: list[dict[str, Any]],
    *,
    packet: Any | None = None,
    report_text_chars: int,
    profile_field_chars: int,
    bridge_brief_chars: int,
    planning_brief_chars: int,
    continuity_brief_chars: int,
    compress_enabled: bool,
    compress_min_chars: int,
    compress_max_tokens: int,
    temperature: float,
    call_with_retry: Callable[..., Awaitable[dict[str, Any]]],
    on_step: Callable[[str, Any], None],
    budget_pressure: float = 0.0,
    quality_check: bool = True,
    quality_min_score: float = 0.62,
    llm_verify_enabled: bool = False,
    llm_verify_margin: float = 0.12,
    llm_verify_max_items: int = 3,
    verify_temperature: float = 0.2,
    adaptive_skip_enabled: bool = True,
) -> dict[str, Any]:
    """Semantically compress planning context without destructive fallback cuts.

    Args:
        previous_creative_report: Previous chapter's creative report (mutable).
        character_profiles: Character profiles for prompt injection (mutable).
        report_text_chars: Max chars for report text fields.
        profile_field_chars: Max chars for profile fields.
        bridge_brief_chars: Max chars for bridge-specific brief.
        planning_brief_chars: Max chars for planning-specific brief.
        continuity_brief_chars: Max chars for continuity-specific brief.
        compress_enabled: Whether model-based compression is enabled.
        compress_min_chars: Minimum text length to consider for compression.
        compress_max_tokens: Max tokens for compression model call.
        call_with_retry: ``ChapterRunner._call_with_retry`` callback.
        on_step: ``ChapterRunner._on_step`` callback.
        budget_pressure: 0.0–1.0 indicating how close to the model context
            window limit the overall prompt is.  Higher pressure ⇒ more
            aggressive compression (lower min_chars, tighter field limits).
    """
    estimate = estimate_context_budget_pressure(
        previous_creative_report,
        character_profiles,
        packet=packet,
    )
    pressure = max(
        float(estimate.get("budget_pressure", 0.0)),
        max(0.0, min(1.0, budget_pressure)),
    )
    # ── Dynamic scaling based on budget pressure ──
    base_report_text_chars = report_text_chars
    base_profile_field_chars = profile_field_chars
    base_bridge_brief_chars = bridge_brief_chars
    base_planning_brief_chars = planning_brief_chars
    base_continuity_brief_chars = continuity_brief_chars
    base_compress_min_chars = compress_min_chars
    if pressure > 0.3:
        scale = 1.0 - (pressure - 0.3) * 0.7  # 0.3→1.0  1.0→0.51
        report_text_chars = min(
            base_report_text_chars,
            max(40, int(base_report_text_chars * scale)),
        )
        profile_field_chars = min(
            base_profile_field_chars,
            max(40, int(base_profile_field_chars * scale)),
        )
        bridge_brief_chars = min(
            base_bridge_brief_chars,
            max(100, int(base_bridge_brief_chars * scale)),
        )
        planning_brief_chars = min(
            base_planning_brief_chars,
            max(120, int(base_planning_brief_chars * scale)),
        )
        continuity_brief_chars = min(
            base_continuity_brief_chars,
            max(100, int(base_continuity_brief_chars * scale)),
        )
        compress_min_chars = min(
            base_compress_min_chars,
            max(40, int(base_compress_min_chars * scale)),
        )

    briefs_generated = 0
    if packet is not None:
        seed_limits = {
            "bridge_seed_chars": max(bridge_brief_chars + 140, bridge_brief_chars * 2),
            "planning_seed_chars": max(planning_brief_chars + 180, planning_brief_chars * 2),
            "continuity_seed_chars": max(
                continuity_brief_chars + 160,
                continuity_brief_chars * 2,
            ),
        }
        for key, value in build_task_context_briefs(
            packet,
            previous_creative_report,
            **seed_limits,
        ).items():
            setattr(packet, key, value)
            if value:
                briefs_generated += 1

    targets = iter_prompt_text_targets(
        previous_creative_report,
        character_profiles,
        report_text_chars=report_text_chars,
        profile_field_chars=profile_field_chars,
    )
    if packet is not None:
        targets.extend(
            iter_task_brief_targets(
                packet,
                bridge_chars=bridge_brief_chars,
                planning_chars=planning_brief_chars,
                continuity_chars=continuity_brief_chars,
            )
        )
    if not targets:
        return {
            "candidates": 0,
            "compressed": 0,
            "fallback": 0,
            "direct_trim": 0,
            "preserved_overflow": 0,
            "hard_truncation_allowed": False,
            "overflow_action": "none",
            "quality_retries": 0,
            "before_chars": 0,
            "after_chars": 0,
            "samples": [],
            "briefs_generated": briefs_generated,
            "budget_pressure": pressure,
            "estimated_chars": int(estimate.get("estimated_chars", 0)),
        }

    before_chars = sum(len(_read_target_value(t)) for t in targets)
    # Texts only slightly over the advisory target gain little from semantic
    # compression. Preserve them completely; only meaningful excess is routed
    # through the compressor. A failed compressor must also preserve its source.
    _LLM_COMPRESS_OVERFLOW_RATIO = 1.20  # at least 20% over limit to warrant LLM call
    candidates: list[dict[str, Any]] = []
    overflow_targets: list[dict[str, Any]] = []
    for idx, t in enumerate(targets, 1):
        text = _read_target_value(t)
        limit = int(t["limit"])
        if len(text) > limit:
            overflow_targets.append(t)
            if (
                len(text) >= compress_min_chars
                and len(text) >= limit * _LLM_COMPRESS_OVERFLOW_RATIO
            ):
                candidates.append(
                    {
                        "id": f"ctx_{idx}",
                        "tag": t["tag"],
                        "text": text,
                        "max_chars": limit,
                        "target": t,
                    }
                )

    compressed = 0
    fallback = 0
    # Compatibility metric retained for existing run-log consumers. Hard
    # direct trimming is intentionally disabled.
    direct_trim = 0
    quality_retries = 0
    quality_failures = 0
    llm_verify_calls = 0
    llm_verify_rejections = 0
    verification_samples: dict[str, dict[str, Any]] = {}
    apply_method: dict[str, str] = {}
    # ── Adaptive compression gate ──
    # At low budget pressure the static field limits (applied above) are
    # sufficient; the expensive LLM compression call is skipped.  At medium
    # pressure the token budget is reduced to limit latency.  Only high
    # pressure triggers the full compression pipeline.
    _LOW_PRESSURE_SKIP_THRESHOLD = 0.3
    _MEDIUM_PRESSURE_TOKEN_CAP = 1024
    _skip_llm_compress = (
        adaptive_skip_enabled
        and compress_enabled
        and pressure < _LOW_PRESSURE_SKIP_THRESHOLD
        and bool(candidates)
    )
    if _skip_llm_compress:
        compress_enabled = False  # disable LLM path; static limits already applied
    elif compress_enabled and pressure < 0.6:
        compress_max_tokens = min(compress_max_tokens, _MEDIUM_PRESSURE_TOKEN_CAP)
    if compress_enabled and candidates:
        blocks = [
            {
                "id": item["id"],
                "text": item["text"],
                "max_chars": item["max_chars"],
            }
            for item in candidates
        ]
        max_tokens = min(
            compress_max_tokens,
            max(
                PipelineConstants.MIN_COMPRESS_TOKEN_BUDGET,
                PipelineConstants.CONTEXT_COMPRESS_MIN_TOKEN_BUDGET + len(blocks) * 96,
            ),
        )
        try:
            data = await call_with_retry(
                TaskType.CONTEXT_COMPRESS,
                {"blocks": blocks},
                max_tokens=max_tokens,
                temperature=temperature,
                required_keys=("items",),
                max_retries=2,
            )
            compact_map: dict[str, str] = {}
            raw_items = data.get("items", [])
            if isinstance(raw_items, list):
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("id", "")).strip()
                    val = str(item.get("compressed", "")).strip()
                    if key and val:
                        compact_map[key] = val

            # Quality verification & retry for low-scoring items.
            # A block that still fails after retry falls back to its complete
            # source; an untrusted compressed rewrite never enters Bridge/Plan.
            if quality_check:
                for item in candidates:
                    item_id = str(item["id"])
                    candidate_text = compact_map.get(item_id, "")
                    if not candidate_text:
                        continue
                    original = item["text"]
                    score = _verify_compression_quality(original, candidate_text)
                    verification_samples[item_id] = {
                        "heuristic_score": round(score, 3),
                        "llm_score": None,
                        "recommendation": "",
                        "accepted": score >= quality_min_score,
                    }
                    if score < quality_min_score:
                        # Retry with simpler fact-priority prompt
                        try:
                            retry_data = await call_with_retry(
                                TaskType.ADAPTIVE_COMPRESS,
                                {
                                    "blocks": [
                                        {
                                            "id": item_id,
                                            "text": original,
                                            "max_chars": item["max_chars"],
                                        }
                                    ],
                                    "mode": "fact_priority",
                                },
                                max_tokens=min(
                                    compress_max_tokens,
                                    PipelineConstants.CONTEXT_COMPRESS_MIN_TOKEN_BUDGET + 96,
                                ),
                                temperature=min(temperature, 0.2),
                                required_keys=("items",),
                                max_retries=1,
                            )
                            retry_items = retry_data.get("items", [])
                            if isinstance(retry_items, list) and retry_items:
                                retry_item = retry_items[0]
                                if isinstance(retry_item, dict):
                                    retry_val = str(retry_item.get("compressed", "")).strip()
                                    if retry_val:
                                        compact_map[item_id] = retry_val
                                        quality_retries += 1
                                        candidate_text = retry_val
                                        score = _verify_compression_quality(
                                            original,
                                            candidate_text,
                                        )
                                        verification_samples[item_id]["heuristic_score"] = round(
                                            score, 3
                                        )
                                        verification_samples[item_id]["accepted"] = (
                                            score >= quality_min_score
                                        )
                        except Exception:
                            pass  # Fall through to original compressed or fallback
                    if (
                        llm_verify_enabled
                        and llm_verify_calls < llm_verify_max_items
                        and score >= quality_min_score
                        and score <= quality_min_score + llm_verify_margin
                    ):
                        try:
                            llm_verify_calls += 1
                            verify_data = await _verify_compression_with_llm(
                                original=original,
                                compressed=candidate_text,
                                call_with_retry=call_with_retry,
                                temperature=verify_temperature,
                                verify_max_tokens=compress_max_tokens,
                            )
                            llm_score = float(verify_data.get("quality_score", score) or score)
                            recommendation = str(verify_data.get("recommendation", "") or "")
                            verification_samples[item_id]["llm_score"] = round(llm_score, 3)
                            verification_samples[item_id]["recommendation"] = recommendation
                            if llm_score < quality_min_score or recommendation == "recompress":
                                compact_map.pop(item_id, None)
                                verification_samples[item_id]["accepted"] = False
                                verification_samples[item_id]["fallback_reason"] = "llm_verify"
                                llm_verify_rejections += 1
                                quality_failures += 1
                                continue
                        except Exception as exc:
                            on_step(
                                "verify_compression_failed",
                                {"id": item_id, "tag": item.get("tag", ""), "error": str(exc)},
                            )
                    if score < quality_min_score:
                        compact_map.pop(item_id, None)
                        verification_samples[item_id]["accepted"] = False
                        verification_samples[item_id]["fallback_reason"] = "heuristic"
                        quality_failures += 1

            for item in candidates:
                target = cast(dict[str, Any], item["target"])
                original = _read_target_value(target)
                candidate_text = compact_map.get(item["id"], "")
                if not candidate_text:
                    _write_target_value(target, original)
                    apply_method[str(item["id"])] = "fallback_preserve_source"
                    fallback += 1
                    continue
                _write_target_value(target, candidate_text)
                apply_method[str(item["id"])] = "model"
                compressed += 1
        except Exception as exc:
            on_step(
                "context_compress_failed",
                {"error": str(exc), "blocks": len(candidates)},
            )
            for item in candidates:
                target = cast(dict[str, Any], item["target"])
                original = _read_target_value(target)
                _write_target_value(target, original)
                apply_method[str(item["id"])] = "fallback_preserve_source"
                fallback += 1

    preserved_overflow = [
        {
            "tag": str(target.get("tag", "")),
            "chars": len(_read_target_value(target)),
            "advisory_limit": int(target["limit"]),
        }
        for target in overflow_targets
        if len(_read_target_value(target)) > int(target["limit"])
    ]
    if preserved_overflow:
        on_step(
            "context_overflow_preserved",
            {
                "items": preserved_overflow,
                "required_context_preserved": True,
                "hard_truncation_allowed": False,
                "overflow_action": ("route_larger_context_or_partition_complete_coverage"),
            },
        )

    samples: list[dict[str, Any]] = []
    for item in candidates[:6]:
        target = cast(dict[str, Any], item["target"])
        after_text = _read_target_value(target)
        before_text = str(item.get("text", "") or "").strip()
        method = apply_method.get(str(item["id"]))
        if not method:
            method = "preserved_source"
        samples.append(
            {
                "tag": str(item.get("tag", "")).strip(),
                "method": method,
                "quality": verification_samples.get(str(item["id"]), {}),
                "before_len": len(before_text),
                "after_len": len(after_text),
                "before": trim_text(before_text, limit=200),
                "after": trim_text(after_text, limit=200),
            }
        )

    after_chars = sum(len(_read_target_value(t)) for t in targets)
    return {
        "candidates": len(candidates),
        "compressed": compressed,
        "fallback": fallback,
        "direct_trim": direct_trim,
        "preserved_overflow": len(preserved_overflow),
        "hard_truncation_allowed": False,
        "overflow_action": "route_larger_context_or_partition_complete_coverage",
        "quality_retries": quality_retries,
        "quality_failures": quality_failures,
        "llm_verify_calls": llm_verify_calls,
        "llm_verify_rejections": llm_verify_rejections,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "samples": samples,
        "briefs_generated": briefs_generated,
        "budget_pressure": pressure,
        "estimated_chars": int(estimate.get("estimated_chars", 0)),
        "llm_compress_skipped_low_pressure": _skip_llm_compress,
    }
