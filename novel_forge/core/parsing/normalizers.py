"""Data normalizers for ChapterOutcome extraction."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.common.utils import normalize_gender_value
from novel_forge.core.domain.guardrails import is_system_artifact_name, sanitize_story_text
from novel_forge.core.parsing.text_utils import clean_str
from novel_forge.core.utils.coerce import (
    coerce_alive,
    coerce_float,
    coerce_int,
    coerce_optional_int,
)


class EventNormalizer:
    """Normalizes story events."""

    coerce_int = staticmethod(coerce_int)

    @classmethod
    def normalize_events(cls, payload: Any, source_chapter: int) -> list[dict[str, Any]]:
        """Normalize events list with deduplication."""
        raw_list = payload if isinstance(payload, list) else [payload]
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[int, str, tuple[str, ...], str]] = set()

        _event_counter = 0
        for raw_event in raw_list:
            if isinstance(raw_event, dict):
                event_text = (
                    clean_str(raw_event.get("event"))
                    or clean_str(raw_event.get("description"))
                    or clean_str(raw_event.get("summary"))
                )
                if not event_text:
                    continue
                chapter = cls.coerce_int(raw_event.get("chapter"), source_chapter)
                characters = cls._to_string_list(
                    raw_event.get("characters_involved", raw_event.get("characters", []))
                )
                timestamp = clean_str(raw_event.get("timestamp_in_story", raw_event.get("time")))
                anchor_id = clean_str(raw_event.get("anchor_id"))
            else:
                event_text = clean_str(raw_event)
                if not event_text:
                    continue
                chapter = source_chapter
                characters = []
                timestamp = ""
                anchor_id = ""

            key = (chapter, event_text, tuple(characters), timestamp)
            if key not in seen:
                seen.add(key)
                if not anchor_id:
                    _event_counter += 1
                    anchor_id = f"evt_ch{chapter}_{_event_counter}"
                normalized.append({
                    "anchor_id": anchor_id,
                    "chapter": chapter,
                    "event": event_text,
                    "characters_involved": characters,
                    "timestamp_in_story": timestamp,
                })
        return normalized

    @staticmethod
    def _to_string_list(value: Any) -> list[str]:
        raw: list[str] = []
        if isinstance(value, list):
            for item in value:
                text = clean_str(item)
                if text:
                    raw.append(text)
        elif isinstance(value, dict):
            for k, v in value.items():
                kt, vt = clean_str(k), clean_str(v)
                if kt and vt:
                    raw.append(f"{kt}: {vt}")
                elif kt:
                    raw.append(kt)
                elif vt:
                    raw.append(vt)
        elif isinstance(value, str):
            text = value.strip()
            if text:
                raw.append(text)
        return raw


class ForeshadowingNormalizer:
    """Normalizes foreshadowing data."""

    _STATUS_MAP = {
        "planted": "planted",
        "reinforced": "hinted",
        "revealed": "paid",
        "abandoned": "broken",
        "confirmed": "paid",
        "resolved": "paid",
        "fulfilled": "paid",
        "strengthened": "hinted",
        "ongoing": "hinted",
        "dropped": "broken",
        "discarded": "broken",
        "cancelled": "broken",
        "canceled": "broken",
        "hinted": "hinted",
        "partially_paid": "partially_paid",
        "paid": "paid",
        "broken": "broken",
    }

    coerce_int = staticmethod(coerce_int)
    coerce_optional_int = staticmethod(coerce_optional_int)

    @classmethod
    def normalize_foreshadowing_updates(
        cls,
        payload: Any,
        source_chapter: int,
    ) -> list[dict[str, Any]]:
        """Normalize foreshadowing updates."""
        raw_list = payload if isinstance(payload, list) else [payload]
        normalized: list[dict[str, Any]] = []

        for index, raw_item in enumerate(raw_list, start=1):
            if isinstance(raw_item, dict):
                fs_id = (
                    clean_str(raw_item.get("entry_id"))
                    or clean_str(raw_item.get("id"))
                    or clean_str(raw_item.get("foreshadowing_id"))
                    or f"fs_{source_chapter}_{index}"
                )
                description = (
                    clean_str(raw_item.get("description"))
                    or clean_str(raw_item.get("event"))
                    or fs_id
                )
                normalized.append({
                    "entry_id": fs_id,
                    "description": description,
                    "planted_chapter": cls.coerce_int(raw_item.get("planted_chapter"), source_chapter),
                    "status": cls._STATUS_MAP.get(
                        clean_str(raw_item.get("status")).lower(),
                        "planted",
                    ),
                    "payoff_chapter": cls.coerce_optional_int(raw_item.get("resolved_chapter")) or 0,
                    "notes": clean_str(raw_item.get("notes")),
                })
            else:
                description = clean_str(raw_item)
                if description:
                    normalized.append({
                        "entry_id": f"fs_{source_chapter}_{index}",
                        "description": description,
                        "planted_chapter": source_chapter,
                        "status": "planted",
                        "payoff_chapter": 0,
                        "notes": "",
                    })
        return normalized


class WorldFactNormalizer:
    """Normalizes world facts."""

    @staticmethod
    def normalize_world_facts(payload: Any) -> dict[str, str]:
        """Normalize world facts dict.

        Accepts both ``{key: value}`` dicts and ``[fact, fact]`` lists.
        Lists (or dicts whose values are empty/None) are treated as fact
        labels: the string itself becomes both key and value.
        """
        if isinstance(payload, list):
            # LLM returned a list instead of a dict — use each item as both key and value
            result: dict[str, str] = {}
            for item in payload:
                k = clean_str(str(item)) if item is not None else ""
                if k:
                    result[k] = k
            return result
        if not isinstance(payload, dict):
            return {}
        result = {}
        for k, v in payload.items():
            ck = clean_str(k)
            if not ck:
                continue
            # v may be None/empty when the model emitted a key without a value
            cv = clean_str(v) if v is not None else ""
            result[ck] = cv if cv else ck
        return result


class RelationshipNormalizer:
    """Normalizes relationship deltas."""

    coerce_int = staticmethod(coerce_int)
    coerce_float = staticmethod(coerce_float)

    @classmethod
    def split_character_names(cls, value: Any) -> list[str]:
        """Delegate to CharacterNormalizer to avoid duplicating regex + logic."""
        return CharacterNormalizer.split_character_names(value)

    @classmethod
    def normalize_relationship_deltas(
        cls,
        payload: Any,
        *,
        source_chapter: int,
    ) -> list[dict[str, Any]]:
        """Normalize relationship deltas."""
        raw_list = payload if isinstance(payload, list) else []
        normalized: list[dict[str, Any]] = []

        for index, raw in enumerate(raw_list, start=1):
            if not isinstance(raw, dict):
                continue
            relationship = raw.get("relationship", raw)
            if not isinstance(relationship, dict):
                continue

            pair_id = clean_str(raw.get("pair_id") or relationship.get("pair_id"))
            characters = cls.split_character_names(
                relationship.get("characters", raw.get("characters", []))
            )
            if len(characters) < 2:
                continue

            if not pair_id:
                pair_id = "__".join(sorted(characters[:2])) if len(characters) >= 2 else (
                    f"relationship_{source_chapter}_{index}"
                )

            rel_dict: dict[str, Any] = {
                    "pair_id": pair_id,
                    "characters": characters[:2] if len(characters) >= 2 else characters,
                    "public_status": clean_str(relationship.get("public_status")),
                    "last_shift_event": clean_str(relationship.get("last_shift_event")),
                    "last_updated_chapter": cls.coerce_int(
                        relationship.get("last_updated_chapter"), source_chapter
                    ),
                    "notes": clean_str(relationship.get("notes")),
            }
            # Only include trust/tension/dependency when LLM actually provided
            # them.  Omitting them lets Pydantic mark the field as "unset" so
            # the downstream merge preserves existing canonical values instead
            # of overwriting with the 0.5 default.
            for metric, default in (("trust", 0.5), ("tension", 0.5), ("dependency", 0.0)):
                raw_val = relationship.get(metric)
                if raw_val is not None:
                    rel_dict[metric] = cls.coerce_float(raw_val, default)

            normalized.append({
                "pair_id": pair_id,
                "change_summary": clean_str(raw.get("change_summary")),
                "relationship": rel_dict,
            })
        return normalized


class PlotThreadNormalizer:
    """Normalizes plot thread deltas."""

    coerce_int = staticmethod(coerce_int)

    # Chinese → canonical English status mapping.
    _STATUS_MAP: dict[str, str] = {
        # active family
        "激活": "active",
        "开启": "active",
        "新启": "active",
        "介入": "active",
        "initiated": "active",
        "activated": "active",
        # advancing family
        "推进": "advancing",
        "推进中": "advancing",
        "进展": "advancing",
        "advanced": "advancing",
        # escalated family
        "升级": "escalated",
        "恶化": "escalated",
        "critical": "escalated",
        # resolved family
        "解决": "resolved",
        "已解": "resolved",
        # dormant family
        "暂时退却": "dormant",
        # planted / foreshadowing
        "伏笔": "planted",
        # other recognized statuses
        "深化": "advancing",
        "扩展": "advancing",
        "关键揭示": "revealed",
        "转折点": "advancing",
        "钩子强化": "active",
        "evolving": "advancing",
        "complicated": "escalated",
        "suspicious": "active",
        "uncertain": "active",
    }

    # Allowed canonical statuses (anything outside this falls back to "active").
    _CANONICAL_STATUSES = frozenset({
        "active", "resolved", "dormant", "escalated",
        "advancing", "planted", "revealed",
    })

    @classmethod
    def _normalize_status(cls, raw_status: str | None) -> str:
        """Normalize a status string to a canonical English value."""
        s = clean_str(raw_status) if raw_status else ""
        if not s:
            return "active"
        low = s.lower().strip()
        # Direct match to canonical set
        if low in cls._CANONICAL_STATUSES:
            return low
        # Lookup in map (original case first, then lowercase)
        mapped = cls._STATUS_MAP.get(s) or cls._STATUS_MAP.get(low)
        if mapped:
            return mapped
        # Substring match for complex statuses like "降级（转为幌子）"
        # Sort keys by length descending so longer/more-specific keys match first,
        # preventing e.g. "化" from matching before "激化".
        for key, val in sorted(cls._STATUS_MAP.items(), key=lambda kv: len(kv[0]), reverse=True):
            if key in s:
                return val
        return "active"

    @classmethod
    def normalize_plot_thread_deltas(
        cls,
        payload: Any,
        *,
        source_chapter: int,
    ) -> list[dict[str, Any]]:
        """Normalize plot thread deltas."""
        raw_list = payload if isinstance(payload, list) else []
        normalized: list[dict[str, Any]] = []

        for index, raw in enumerate(raw_list, start=1):
            if not isinstance(raw, dict):
                continue
            thread = raw.get("thread", raw)
            if not isinstance(thread, dict):
                continue

            thread_id = clean_str(raw.get("thread_id") or thread.get("thread_id"))
            if not thread_id:
                thread_id = f"thread_{source_chapter}_{index}"

            normalized.append({
                "thread_id": thread_id,
                "change_summary": clean_str(raw.get("change_summary")),
                "thread": {
                    "thread_id": thread_id,
                    "title": clean_str(thread.get("title")) or thread_id,
                    "status": cls._normalize_status(thread.get("status")),
                    "owners": CharacterNormalizer.to_string_list(thread.get("owners", [])),
                    "last_touched_chapter": cls.coerce_int(
                        thread.get("last_touched_chapter"), source_chapter
                    ),
                    "next_payoff_window": clean_str(thread.get("next_payoff_window")),
                    "blocking_condition": clean_str(thread.get("blocking_condition")),
                    "summary": clean_str(thread.get("summary")),
                },
            })
        return normalized


class CreativeReportNormalizer:
    """Normalizes creative report data."""

    coerce_int = staticmethod(coerce_int)

    @classmethod
    def normalize_creative_report(cls, payload: Any, source_chapter: int) -> dict[str, Any]:
        """Normalize creative report with all sub-fields."""
        data = payload if isinstance(payload, dict) else {}

        normalized_characters = cls._normalize_new_characters(
            data.get("new_characters", []), source_chapter
        )
        normalized_deviations = cls._normalize_plot_deviations(data.get("plot_deviations", []))

        return {
            "new_characters": normalized_characters,
            "new_locations": CharacterNormalizer.to_string_list(data.get("new_locations")),
            "new_key_items": CharacterNormalizer.to_string_list(data.get("new_key_items")),
            "plot_deviations": normalized_deviations,
            "suggestions_for_next_chapter": sanitize_story_text(
                clean_str(data.get("suggestions_for_next_chapter"))
            ),
            "creative_highlights": CharacterNormalizer.to_string_list(data.get("creative_highlights")),
            "structured_summary": sanitize_story_text(clean_str(data.get("structured_summary"))),
            "must_carry_forward": CharacterNormalizer.to_string_list(data.get("must_carry_forward", [])),
            "bridge_hints": CharacterNormalizer.to_string_list(data.get("bridge_hints", [])),
            "character_state_deltas": CharacterNormalizer.normalize_state_deltas(
                data.get("character_state_deltas", []),
                source_chapter=source_chapter,
                fallback={},
            ),
            "relationship_deltas": RelationshipNormalizer.normalize_relationship_deltas(
                data.get("relationship_deltas", []),
                source_chapter=source_chapter,
            ),
            "plot_thread_updates": PlotThreadNormalizer.normalize_plot_thread_deltas(
                data.get("plot_thread_updates", []),
                source_chapter=source_chapter,
            ),
        }

    @staticmethod
    def _normalize_new_characters(raw_list: Any, source_chapter: int) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(raw_list, list):
            return normalized

        for raw in raw_list:
            if isinstance(raw, str):
                name = clean_str(raw)
                if name and not is_system_artifact_name(name):
                    normalized.append({
                        "name": name,
                        "canonical_name": "",
                        "first_appearance_chapter": source_chapter,
                        "role_in_story": "supporting",
                        "importance": "minor",
                        "confidence": 0.0,
                        "description": "",
                        "relationship_to_existing": {},
                        "should_add_to_bible": False,
                        "matched_existing_name": "",
                        "evidence": [],
                    })
            elif isinstance(raw, dict):
                name = clean_str(raw.get("name"))
                if not name or is_system_artifact_name(name):
                    continue
                raw_confidence = raw.get("confidence", 0.0)
                try:
                    confidence = max(0.0, min(1.0, float(raw_confidence or 0.0)))
                except (TypeError, ValueError):
                    confidence = 0.0
                evidence = raw.get("evidence")
                normalized.append({
                    "name": name,
                    "canonical_name": clean_str(raw.get("canonical_name")),
                    "first_appearance_chapter": CharacterNormalizer.coerce_int(
                        raw.get("first_appearance_chapter"), source_chapter
                    ),
                    "role_in_story": clean_str(raw.get("role_in_story")) or "supporting",
                    "importance": clean_str(raw.get("importance")) or "minor",
                    "confidence": confidence,
                    "description": clean_str(raw.get("description")),
                    "relationship_to_existing": (
                        raw.get("relationship_to_existing")
                        if isinstance(raw.get("relationship_to_existing"), dict)
                        else {}
                    ),
                    "should_add_to_bible": bool(raw.get("should_add_to_bible", False)),
                    "matched_existing_name": clean_str(raw.get("matched_existing_name")),
                    "evidence": evidence if isinstance(evidence, list) else [],
                })
        return normalized

    @staticmethod
    def _normalize_plot_deviations(raw_list: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(raw_list, list):
            return normalized

        for raw in raw_list:
            if isinstance(raw, str):
                text = clean_str(raw)
                if text:
                    normalized.append({
                        "outline_plan": "",
                        "actual_plot": text,
                        "deviation_level": "minor",
                        "reason": "",
                        "impact_on_future": "",
                    })
            elif isinstance(raw, dict):
                normalized.append({
                    "outline_plan": clean_str(raw.get("outline_plan")),
                    "actual_plot": clean_str(raw.get("actual_plot")),
                    "deviation_level": clean_str(raw.get("deviation_level")) or "minor",
                    "reason": clean_str(raw.get("reason")),
                    "impact_on_future": clean_str(raw.get("impact_on_future")),
                })
        return normalized


class ChapterExitNormalizer:
    """Normalizes chapter exit state."""

    coerce_int = staticmethod(coerce_int)

    @classmethod
    def normalize_chapter_exit_state(
        cls,
        payload: Any,
        *,
        source_chapter: int,
        chapter_summary: str,
        fallback_character_updates: dict[str, dict[str, Any]],
        creative_report: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize chapter exit state."""
        data = payload if isinstance(payload, dict) else {}

        location = clean_str(data.get("location"))
        if not location and fallback_character_updates:
            first = next(iter(fallback_character_updates.values()))
            location = clean_str(
                first.get("physical", {}).get("location", first.get("location", ""))
            )

        character_end_states = data.get("character_end_states")
        if isinstance(character_end_states, dict):
            normalized_characters: dict[str, dict[str, Any]] = {}
            for name, raw in character_end_states.items():
                if not is_system_artifact_name(name):
                    normalized = CharacterNormalizer.normalize_character_state(name, raw)
                    if normalized is not None:
                        normalized_characters[name] = normalized
        else:
            normalized_characters = fallback_character_updates

        # Back-fill critical fields from fallback_character_updates when the
        # LLM-provided character_end_states has empty sub-fields.  This fixes
        # the common case where the LLM returns character names in
        # character_end_states but omits location/emotion/motivation/knowledge.
        if normalized_characters and fallback_character_updates:
            for name, state in normalized_characters.items():
                fb = fallback_character_updates.get(name)
                if not fb:
                    continue
                if isinstance(state, dict) and isinstance(fb, dict):
                    # Back-fill physical.location
                    s_phys = state.get("physical", {})
                    f_phys = fb.get("physical", {})
                    if not s_phys.get("location") and f_phys.get("location"):
                        s_phys["location"] = f_phys["location"]
                    # Back-fill emotional.primary_emotion
                    s_emo = state.get("emotional", {})
                    f_emo = fb.get("emotional", {})
                    if not s_emo.get("primary_emotion") and f_emo.get("primary_emotion"):
                        s_emo["primary_emotion"] = f_emo["primary_emotion"]
                    # Back-fill motivation.current_drive
                    s_mot = state.get("motivation", {})
                    f_mot = fb.get("motivation", {})
                    if not s_mot.get("current_drive") and f_mot.get("current_drive"):
                        s_mot["current_drive"] = f_mot["current_drive"]
                    if not s_mot.get("short_term_goal") and f_mot.get("short_term_goal"):
                        s_mot["short_term_goal"] = f_mot["short_term_goal"]
                    # Back-fill knowledge_state.known_facts
                    s_know = state.get("knowledge_state", {})
                    f_know = fb.get("knowledge_state", {})
                    if not s_know.get("known_facts") and f_know.get("known_facts"):
                        s_know["known_facts"] = f_know["known_facts"]

        must_carry_raw = data.get("must_carry_forward", creative_report.get("must_carry_forward", []))
        must_carry = cls._normalize_carry_forward(must_carry_raw)
        if not must_carry and chapter_summary:
            must_carry = [{"text": chapter_summary, "status": "open"}]

        return {
            "chapter_number": cls.coerce_int(data.get("chapter_number"), source_chapter),
            "time_marker": clean_str(data.get("time_marker")),
            "location": location,
            "pov": clean_str(data.get("pov")),
            "active_goals": CharacterNormalizer.to_string_list(data.get("active_goals", [])),
            "open_questions": CharacterNormalizer.to_string_list(data.get("open_questions", [])),
            "must_carry_forward": must_carry,
            "character_end_states": normalized_characters,
        }

    @classmethod
    def _normalize_carry_forward(cls, raw: Any) -> list[dict[str, Any]]:
        """Normalize LLM carry-forward output into structured item dicts.

        Accepts the legacy ``list[str]`` shape (each string becomes an open
        item) and the structured ``list[dict]`` shape produced when the LLM
        declares an item ``abandoned`` or ``deferred``. The structured form is
        the escape hatch that lets the carry-forward hard gate distinguish
        intentional ellipsis from genuine state loss.
        """
        if not isinstance(raw, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                text = clean_str(item)
                if text:
                    normalized.append({"text": text, "status": "open"})
            elif isinstance(item, dict):
                text = clean_str(item.get("text") or item.get("item") or item.get("content"))
                if not text:
                    continue
                status = clean_str(item.get("status")).lower() or "open"
                if status not in {"open", "abandoned", "deferred"}:
                    status = "open"
                normalized.append(
                    {
                        "text": text,
                        "status": status,
                        "abandon_reason": clean_str(item.get("abandon_reason")),
                        "deferred_to_chapter": cls.coerce_int(
                            item.get("deferred_to_chapter"), 0
                        ),
                    }
                )
        return normalized


class CharacterNormalizer:
    """Normalizes character-related data structures."""

    _PAIR_SEPARATOR_RE = re.compile(r"\s*(?:&|＆|和|与|及|,|，|/|／|、)\s*")

    # Delegate to module-level coerce functions for backward compatibility
    coerce_int = staticmethod(coerce_int)
    coerce_optional_int = staticmethod(coerce_optional_int)
    coerce_float = staticmethod(coerce_float)
    coerce_alive = staticmethod(coerce_alive)

    @classmethod
    def to_string_list(cls, value: Any) -> list[str]:
        """Convert various formats to deduplicated string list."""
        raw: list[str] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for k, v in item.items():
                        kt, vt = clean_str(k), clean_str(v)
                        if kt and vt:
                            raw.append(f"{kt}: {vt}")
                        elif kt:
                            raw.append(kt)
                        elif vt:
                            raw.append(vt)
                else:
                    text = clean_str(item)
                    if text:
                        raw.append(text)
        elif isinstance(value, dict):
            for k, v in value.items():
                kt, vt = clean_str(k), clean_str(v)
                if kt and vt:
                    raw.append(f"{kt}: {vt}")
                elif kt:
                    raw.append(kt)
                elif vt:
                    raw.append(vt)
        elif isinstance(value, str):
            text = value.strip()
            if text:
                raw.append(text)

        seen: set[str] = set()
        deduped: list[str] = []
        for item in raw:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    @classmethod
    def split_character_names(cls, value: Any) -> list[str]:
        """Split paired character names (e.g., 'A & B')."""
        names: list[str] = []
        for item in cls.to_string_list(value):
            parts = [p.strip() for p in cls._PAIR_SEPARATOR_RE.split(item) if p.strip()]
            if len(parts) <= 1:
                parts = [item.strip()]
            for part in parts:
                if not is_system_artifact_name(part):
                    names.append(part)

        seen: set[str] = set()
        deduped: list[str] = []
        for name in names:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        return deduped

    @classmethod
    def normalize_character_state(cls, name_hint: str, payload: Any) -> dict[str, Any] | None:
        """Normalize a single character state dict."""
        if not name_hint and isinstance(payload, dict):
            name_hint = clean_str(payload.get("name"))
        if not name_hint:
            return None

        if isinstance(payload, dict):
            physical = payload.get("physical", {}) if isinstance(payload.get("physical"), dict) else {}
            emotional = payload.get("emotional", {}) if isinstance(payload.get("emotional"), dict) else {}
            motivation = payload.get("motivation", {}) if isinstance(payload.get("motivation"), dict) else {}
            knowledge = payload.get("knowledge_state", {}) if isinstance(payload.get("knowledge_state"), dict) else {}

            # Canonical identity permits only 男/女. Unknown model labels are
            # treated as missing, so they cannot leak into a ChapterOutcome.
            gender = normalize_gender_value(payload.get("gender", ""))

            return {
                "name": name_hint,
                "alive": cls.coerce_alive(payload.get("alive", True)),
                "gender": gender,
                "physical": {
                    "location": clean_str(physical.get("location", payload.get("location", ""))),
                    "injuries": cls.to_string_list(
                        physical.get("injuries", payload.get("injuries", []))
                    ),
                    "fatigue": clean_str(physical.get("fatigue", "")),
                    "inventory": cls.to_string_list(
                        physical.get("inventory", payload.get("inventory", []))
                    ),
                },
                "emotional": {
                    "primary_emotion": clean_str(
                        emotional.get("primary_emotion", payload.get("emotional_state", ""))
                    ),
                    "secondary_emotion": clean_str(emotional.get("secondary_emotion", "")),
                    "stability": cls.coerce_float(emotional.get("stability"), 0.5),
                    "desire": clean_str(emotional.get("desire", "")),
                    "fear": clean_str(emotional.get("fear", "")),
                },
                "motivation": {
                    "short_term_goal": clean_str(motivation.get("short_term_goal", "")),
                    "long_term_goal": clean_str(motivation.get("long_term_goal", "")),
                    "current_drive": clean_str(motivation.get("current_drive", "")),
                    "internal_conflict": clean_str(motivation.get("internal_conflict", "")),
                },
                "knowledge_state": {
                    "known_facts": cls.to_string_list(
                        knowledge.get("known_facts", payload.get("knowledge", []))
                    ),
                    "suspicions": cls.to_string_list(knowledge.get("suspicions", [])),
                    "misbeliefs": cls.to_string_list(knowledge.get("misbeliefs", [])),
                    "secrets_kept": cls.to_string_list(knowledge.get("secrets_kept", [])),
                },
                "notes": clean_str(payload.get("notes", "")),
                "last_seen_chapter": cls.coerce_int(payload.get("last_seen_chapter"), 0),
                "last_change_reason": clean_str(payload.get("last_change_reason", "")),
            }

        return {
            "name": name_hint,
            "alive": True,
            "gender": "",
            "physical": {"location": "", "injuries": [], "fatigue": "", "inventory": []},
            "emotional": {"primary_emotion": "", "secondary_emotion": "", "stability": 0.5, "desire": "", "fear": ""},
            "motivation": {"short_term_goal": "", "long_term_goal": "", "current_drive": "", "internal_conflict": ""},
            "knowledge_state": {"known_facts": [], "suspicions": [], "misbeliefs": [], "secrets_kept": []},
            "notes": clean_str(payload),
            "last_seen_chapter": 0,
            "last_change_reason": "",
        }

    @classmethod
    def normalize_character_updates(cls, payload: Any) -> dict[str, dict[str, Any]]:
        """Normalize character updates dict/list to {name: state} format."""
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(payload, dict):
            for raw_name, raw_state in payload.items():
                name = clean_str(raw_name)
                if is_system_artifact_name(name):
                    continue
                item = cls.normalize_character_state(name, raw_state)
                if item:
                    normalized[name] = item
        elif isinstance(payload, list):
            for raw_state in payload:
                if isinstance(raw_state, dict):
                    name = clean_str(raw_state.get("name"))
                    if not is_system_artifact_name(name):
                        item = cls.normalize_character_state(name, raw_state)
                        if item:
                            normalized[name] = item
        return normalized

    @classmethod
    def normalize_state_deltas(
        cls,
        payload: Any,
        *,
        source_chapter: int,
        fallback: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize character state deltas with fallback."""
        normalized: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                name = clean_str(raw.get("name"))
                if is_system_artifact_name(name):
                    continue
                to_state = cls.normalize_character_state(name, raw.get("to_state", raw.get("state", raw)))
                if name and to_state:
                    to_state["last_seen_chapter"] = cls.coerce_int(to_state.get("last_seen_chapter"), source_chapter)
                    normalized.append({
                        "name": name,
                        "change_summary": clean_str(raw.get("change_summary")),
                        "to_state": to_state,
                    })

        if normalized:
            return normalized

        return [
            {"name": name, "change_summary": "", "to_state": dict(state, last_seen_chapter=source_chapter)}
            for name, state in fallback.items()
        ]
