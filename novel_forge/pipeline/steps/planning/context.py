"""Context trimming and hint building for plan step."""

from __future__ import annotations

from typing import Any

from novel_forge.core.domain.guardrails import is_system_artifact_name, sanitize_story_text
from novel_forge.core.schemas.continuity import ChapterStatePacket
from novel_forge.core.utils.string import clean_str
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.entity_reference import normalize_prompt_entity_reference_graph
from novel_forge.pipeline.long.services.motif_prompt_format import (
    format_motif_continuity_for_prompt,
)

_log = get_logger("pipeline.steps.planning.context")


def _identity_excerpt(value: Any, *, max_chars: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip("，,；;：: ")


def _normalize_string_list(value: Any, *, max_chars: int = 220) -> list[str]:
    from novel_forge.core.domain.guardrails import sanitize_story_text

    raw: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = sanitize_story_text(clean_str(item))
            if text:
                raw.append(text)
    elif isinstance(value, dict):
        for v in value.values():
            text = sanitize_story_text(clean_str(v))
            if text:
                raw.append(text)
    elif isinstance(value, str):
        text = sanitize_story_text(clean_str(value))
        if text:
            raw = [text]

    import re
    def _split_long_text(text: str, max_c: int) -> list[str]:
        compact = " ".join(text.split())
        if len(compact) <= max_c:
            return [compact]
        parts = [p.strip() for p in re.split(r"[；;。！？!?]", compact) if p.strip()]
        if not parts:
            return [compact[:max_c]]
        chunks: list[str] = []
        current = ""
        for part in parts:
            candidate = f"{current}；{part}" if current else part
            if len(candidate) <= max_c:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = part[:max_c]
        if current:
            chunks.append(current)
        return chunks or [compact[:max_c]]

    normalized: list[str] = []
    for item in raw:
        normalized.extend(_split_long_text(item, max_chars))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in normalized:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _coerce_non_negative_int(value: Any) -> int:
    text = clean_str(value)
    if not text:
        return 0
    try:
        number = int(float(text))
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def build_character_identity_cards(
    packet: ChapterStatePacket | None,
    *,
    max_cards: int = 10,
) -> list[dict[str, str]]:
    """Build compact identity cards from character profiles for prompt locking."""
    if packet is None:
        return []
    raw_profiles = list(getattr(packet, "character_profiles", []) or [])
    canon_ctx = getattr(packet, "canon_context", None) or {}
    canon_chars = canon_ctx.get("characters", {}) if isinstance(canon_ctx, dict) else {}
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for profile in raw_profiles:
        if not isinstance(profile, dict):
            continue
        name = clean_str(profile.get("name"))
        if not name or name in seen or is_system_artifact_name(name):
            continue
        seen.add(name)
        role = clean_str(profile.get("role"))
        canon_char = canon_chars.get(name)
        social_status = ""
        if isinstance(canon_char, dict):
            social_status = clean_str(canon_char.get("social_status", ""))
        elif hasattr(canon_char, "social_status"):
            social_status = clean_str(getattr(canon_char, "social_status", ""))
        personality_part = (
            _identity_excerpt(profile.get("personality"), max_chars=100)
            or _identity_excerpt(profile.get("backstory"), max_chars=100)
            or role
        )
        if social_status and personality_part:
            identity = f"{social_status}。{personality_part}"
        elif social_status:
            identity = social_status
        else:
            identity = personality_part
        if not identity:
            continue
        cards.append({"name": name, "role": role, "identity": identity})
        if len(cards) >= max_cards:
            break
    return cards


def trim_plan_canon_context(
    canon_context: Any,
    *,
    focus_characters: list[str] | None = None,
    max_characters: int = 20,
    max_recent_events: int = 12,
    max_foreshadowing: int = 8,
) -> dict[str, Any]:
    """Keep only prompt-consumed canon fields with bounded size."""
    if not isinstance(canon_context, dict):
        return {}

    focus = [name.strip() for name in (focus_characters or []) if str(name).strip()]
    seen_focus: set[str] = set()
    ordered_focus: list[str] = []
    for name in focus:
        if name in seen_focus:
            continue
        seen_focus.add(name)
        ordered_focus.append(name)

    trimmed: dict[str, Any] = {
        "characters": {},
        "recent_events": [],
        "active_foreshadowing": [],
    }

    raw_chars = canon_context.get("characters")
    if isinstance(raw_chars, dict):
        selected_names: list[str] = []
        for name in ordered_focus:
            if name in raw_chars:
                selected_names.append(name)
        for name in raw_chars.keys():
            if len(selected_names) >= max_characters:
                break
            if name not in selected_names:
                selected_names.append(name)
        trimmed["characters"] = {name: raw_chars.get(name) for name in selected_names}

    raw_events = canon_context.get("recent_events")
    if isinstance(raw_events, list):
        trimmed["recent_events"] = raw_events[:max_recent_events]

    raw_foreshadowing = canon_context.get("active_foreshadowing")
    if isinstance(raw_foreshadowing, list):
        trimmed["active_foreshadowing"] = raw_foreshadowing[:max_foreshadowing]

    if isinstance(canon_context.get("authoritative_narrative_state"), dict):
        trimmed["authoritative_narrative_state"] = canon_context[
            "authoritative_narrative_state"
        ]
    if isinstance(canon_context.get("chapter_contract"), dict):
        trimmed["chapter_contract"] = canon_context["chapter_contract"]
    immutable = canon_context.get("immutable_facts")
    if isinstance(immutable, list):
        trimmed["immutable_facts"] = immutable
    entity_refs = normalize_prompt_entity_reference_graph(
        canon_context.get("entity_reference_graph")
    )
    if entity_refs:
        trimmed["entity_reference_graph"] = entity_refs

    return trimmed


def trim_plan_memory_hints(
    memory_hints: Any,
    *,
    max_history: int = 5,
    max_unresolved: int = 3,
    max_relationship_changes: int = 3,
) -> dict[str, Any]:
    """Keep only memory-hint fields consumed by plan_chapter.j2."""
    if not isinstance(memory_hints, dict):
        return {}

    trimmed: dict[str, Any] = {}
    relevant_history_raw = memory_hints.get("relevant_history")
    if isinstance(relevant_history_raw, list):
        relevant_history: list[dict[str, Any]] = []
        for item in relevant_history_raw[:max_history]:
            if not isinstance(item, dict):
                continue
            event_summary = sanitize_story_text(clean_str(item.get("event_summary")))
            if not event_summary:
                continue
            relevant_history.append(
                {
                    "chapter_number": _coerce_non_negative_int(
                        item.get("chapter_number", 0)
                    ),
                    "event_summary": event_summary,
                    "relevance_score": item.get("relevance_score", 0.0),
                }
            )
        if relevant_history:
            trimmed["relevant_history"] = relevant_history

    outline_ctx_raw = memory_hints.get("outline_context")
    if isinstance(outline_ctx_raw, dict):
        outline_ctx: dict[str, Any] = {}
        chapter_summary = sanitize_story_text(
            clean_str(outline_ctx_raw.get("chapter_summary"))
        )
        if chapter_summary:
            outline_ctx["chapter_summary"] = chapter_summary

        unresolved = _normalize_string_list(
            outline_ctx_raw.get("unresolved_questions", []),
            max_chars=120,
        )[:max_unresolved]
        if unresolved:
            outline_ctx["unresolved_questions"] = unresolved

        relationship_changes = _normalize_string_list(
            outline_ctx_raw.get("relationship_changes", []),
            max_chars=120,
        )[:max_relationship_changes]
        if relationship_changes:
            outline_ctx["relationship_changes"] = relationship_changes

        if outline_ctx:
            trimmed["outline_context"] = outline_ctx

    layered_ctx_raw = memory_hints.get("layered_context")
    if isinstance(layered_ctx_raw, dict):
        layered_ctx: dict[str, Any] = {}
        text = sanitize_story_text(clean_str(layered_ctx_raw.get("L1_core_memory")))
        if text:
            layered_ctx["L1_core_memory"] = text
        if layered_ctx:
            trimmed["layered_context"] = layered_ctx

    motif_continuity_raw = memory_hints.get("motif_continuity")
    if isinstance(motif_continuity_raw, dict) and motif_continuity_raw:
        formatted = format_motif_continuity_for_prompt(motif_continuity_raw)
        if formatted:
            trimmed["motif_continuity"] = {
                key: _normalize_string_list(value, max_chars=150)
                for key, value in formatted.items()
            }

    # Preserve forward_motif_guidance
    forward_raw = memory_hints.get("forward_motif_guidance")
    if isinstance(forward_raw, dict) and any(forward_raw.values()):
        trimmed["forward_motif_guidance"] = forward_raw

    return trimmed
