"""Editorial expression-signal detection.

This is the single runtime source for body-language and emotional-expression
channel cooling.  It deliberately returns plain dictionaries because callers
route these records through prompts, reports, and memory artifacts.
"""

from __future__ import annotations

import re
from typing import Any

from novel_forge.editorial.schemas import normalize_expression_channel_profiles
from novel_forge.narrative_state.schemas import ExpressionChannelRecord

_RECORD_CHANNELS = {
    "somatic_reaction",
    "action_tag",
    "sentence_pattern",
    "sensory_anchor",
    "metaphor_image",
    "dialogue_tag",
    "emotional_beat",
    "other",
}

# Precompiled regex cache for literal term matching.
# Avoids repeated re.escape() and re.compile() calls for the same terms.
_LITERAL_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def channel_specs() -> dict[str, dict[str, Any]]:
    """Return built-in expression specs.

    Expression channels are now project-contract driven; this compatibility
    helper intentionally returns no phrase table.
    """

    return {}


def expression_profiles_to_records(
    profiles: Any,
    *,
    source: str = "editorial_contract",
    level: str = "soft",
    max_records: int = 12,
) -> list[dict[str, Any]]:
    """Project contract expression profiles into runtime scan records."""

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for profile in normalize_expression_channel_profiles(
        profiles,
        max_items=max_records,
        min_confidence=0.0,
        provenance=source,
    ):
        channel_id = str(profile.get("channel_id") or "").strip()
        if not channel_id or channel_id in seen:
            continue
        seen.add(channel_id)
        surface_forms = [
            str(item).strip()
            for item in profile.get("surface_forms", [])
            if str(item).strip()
        ]
        record = ExpressionChannelRecord(
            text=str(profile.get("label") or channel_id),
            channel=str(profile.get("channel") or "other"),
            channel_id=channel_id,
            source=source,
            level=level,
            cooldown_chapters=max(0, _safe_int(profile.get("cooldown_chapters"), 3)),
            actor_scope=str(profile.get("actor_scope") or "global"),
            reason=str(profile.get("risk_reason") or profile.get("label") or ""),
            examples=surface_forms[:4],
            surface_forms=surface_forms[:8],
            trigger_contexts=[
                str(item).strip()
                for item in list(profile.get("trigger_contexts", []) or [])
                if str(item).strip()
            ][:4],
            replacement_axes=[
                str(item).strip()
                for item in list(profile.get("replacement_axes", []) or [])
                if str(item).strip()
            ][:4],
            allowed_when=str(profile.get("allowed_when") or ""),
            confidence=float(profile.get("confidence") or 0.0),
            provenance=str(profile.get("provenance") or source),
        )
        records.append(record.model_dump(mode="json"))
    return records


def classify_expression_channel(
    value: Any,
    *,
    profiles: Any | None = None,
) -> dict[str, Any]:
    """Return channel metadata for one text fragment."""

    text = str(value or "").strip()
    for profile in normalize_expression_channel_profiles(
        profiles,
        max_items=12,
        min_confidence=0.0,
    ):
        surface_forms = list(profile.get("surface_forms", []) or [])
        if text and (
            text == str(profile.get("label") or "")
            or any(form and (form in text or text in form) for form in surface_forms)
        ):
            return {
                "channel_id": str(profile.get("channel_id") or ""),
                "channel": str(profile.get("channel") or "other"),
                "label": str(profile.get("label") or ""),
                "surface_forms": surface_forms[:8],
                "replacement_axes": list(profile.get("replacement_axes", []) or [])[:4],
                "allowed_when": str(profile.get("allowed_when") or ""),
            }
    return {
        "channel_id": "",
        "channel": "other",
        "label": "",
        "surface_forms": [],
        "replacement_axes": [],
        "allowed_when": "",
    }


def build_expression_channel_record(
    value: Any,
    *,
    source: str,
    level: str = "soft",
    reason: str = "",
    cooldown_chapters: int = 3,
    actor_scope: str = "global",
    examples: list[str] | None = None,
    profiles: Any | None = None,
) -> dict[str, Any] | None:
    """Build a typed expression-channel record from a source string/dict."""

    surface_forms: list[str] = []
    replacement_axes: list[str] = []
    trigger_contexts: list[str] = []
    allowed_when = ""
    confidence = 0.0
    provenance = ""
    channel_id = ""
    channel = ""
    if isinstance(value, dict):
        text = str(value.get("text") or value.get("term") or value.get("name") or "").strip()
        channel_id = str(value.get("channel_id") or "").strip()
        channel = _normalize_record_channel(value.get("channel"))
        if not reason:
            reason = str(value.get("reason") or value.get("source_reason") or "").strip()
        level = str(value.get("level") or level or "soft").strip()
        source = str(value.get("source") or source or "").strip()
        actor_scope = str(value.get("actor_scope") or actor_scope or "global").strip()
        cooldown_chapters = _safe_int(value.get("cooldown_chapters"), cooldown_chapters)
        examples = list(value.get("examples") or examples or [])
        surface_forms = [
            str(item).strip()
            for item in list(value.get("surface_forms", []) or [])
            if str(item).strip()
        ][:8]
        replacement_axes = [
            str(item).strip()
            for item in list(value.get("replacement_axes", []) or [])
            if str(item).strip()
        ][:4]
        trigger_contexts = [
            str(item).strip()
            for item in list(value.get("trigger_contexts", []) or [])
            if str(item).strip()
        ][:4]
        allowed_when = str(value.get("allowed_when") or "").strip()
        confidence = _safe_float(value.get("confidence"), 0.0)
        provenance = str(value.get("provenance") or "").strip()
    else:
        text = str(value or "").strip()
    if not text:
        return None

    meta = classify_expression_channel(text, profiles=profiles)
    if not surface_forms:
        surface_forms = [
            str(item).strip()
            for item in meta.get("surface_forms", [])
            if str(item).strip()
        ]
    if not replacement_axes:
        replacement_axes = [
            str(item).strip() for item in meta.get("replacement_axes", []) if str(item).strip()
        ][:4]
    if not allowed_when:
        allowed_when = str(meta.get("allowed_when") or "")
    record = ExpressionChannelRecord(
        text=text,
        channel=channel or _normalize_record_channel(meta["channel"]),
        channel_id=channel_id or meta["channel_id"],
        source=source,
        level=level or "soft",
        cooldown_chapters=max(0, cooldown_chapters),
        actor_scope=actor_scope or "global",
        reason=reason or meta["label"],
        examples=[str(item).strip() for item in list(examples or []) if str(item).strip()][:4],
        surface_forms=surface_forms[:8],
        trigger_contexts=trigger_contexts[:4],
        replacement_axes=replacement_axes[:4],
        allowed_when=allowed_when,
        confidence=confidence,
        provenance=provenance,
    )
    return record.model_dump(mode="json")


def build_expression_channel_records(
    values: Any,
    *,
    source: str,
    level: str = "soft",
    reason: str = "",
    cooldown_chapters: int = 3,
    actor_scope: str = "global",
    include_other: bool = False,
    profiles: Any | None = None,
) -> list[dict[str, Any]]:
    """Normalize a list of source values into typed channel records."""

    raw_items = values if isinstance(values, list | tuple | set) else [values]
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items:
        record = build_expression_channel_record(
            item,
            source=source,
            level=level,
            reason=reason,
            cooldown_chapters=cooldown_chapters,
            actor_scope=actor_scope,
            profiles=profiles,
        )
        if not record:
            continue
        if record.get("channel") == "other" and not include_other:
            continue
        key = (str(record.get("channel_id") or ""), str(record.get("text") or ""))
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def detect_expression_channel_hits(
    text: str,
    records: list[dict[str, Any]] | None,
    *,
    min_count: int = 1,
) -> list[dict[str, Any]]:
    """Detect channel-level hits in chapter text for supplied records."""

    source = str(text or "")
    if not source:
        return []
    hits: list[dict[str, Any]] = []
    checked_channels: set[str] = set()
    for record in records or []:
        if not isinstance(record, dict):
            continue
        channel_id = str(record.get("channel_id") or "").strip()
        channel = str(record.get("channel") or "other").strip()
        source_text = str(record.get("text") or "").strip()
        match_terms = _record_match_terms(record)
        if channel_id:
            if channel_id in checked_channels:
                continue
            checked_channels.add(channel_id)
            matches = _literal_matches(source, match_terms)
            if len(matches) >= min_count:
                hits.append(
                    {
                        "channel_id": channel_id,
                        "channel": channel,
                        "text": source_text,
                        "matched": _dedupe(matches)[:6],
                        "count": len(matches),
                        "reason": str(record.get("reason") or source_text),
                        "replacement_advice": _replacement_advice(record),
                        "replacement_axes": list(record.get("replacement_axes", []) or [])[
                            :4
                        ],
                        "allowed_when": str(record.get("allowed_when") or ""),
                    }
                )
            continue
        if source_text:
            matches = _literal_matches(source, match_terms or [source_text])
            if len(matches) < min_count:
                continue
            hits.append(
                {
                    "channel_id": "",
                    "channel": channel,
                    "text": source_text,
                    "matched": _dedupe(matches)[:6],
                    "count": len(matches),
                    "reason": str(record.get("reason") or "禁用表达原样复现"),
                    "replacement_advice": _replacement_advice(record),
                    "replacement_axes": list(record.get("replacement_axes", []) or [])[:4],
                    "allowed_when": str(record.get("allowed_when") or ""),
                }
            )
    return hits


def detect_builtin_expression_overuse(
    text: str,
    *,
    threshold: int = 3,
    records: list[dict[str, Any]] | None = None,
    profiles: Any | None = None,
) -> list[dict[str, Any]]:
    """Detect repeated project expression channels.

    The old built-in phrase table has been retired; callers must supply
    records or contract profiles.
    """

    source_records = list(records or [])
    if not source_records and profiles is not None:
        source_records = expression_profiles_to_records(profiles)
    if not source_records:
        return []
    hits = detect_expression_channel_hits(text, source_records, min_count=max(1, threshold))
    for hit in hits:
        hit["reason"] = (
            str(hit.get("reason") or "").strip()
            or f"本章内同一表达通道重复 {hit.get('count', 0)} 次。"
        )
    return hits


def _record_match_terms(record: dict[str, Any]) -> list[str]:
    terms = [
        str(item).strip()
        for item in list(record.get("surface_forms", []) or [])
        if str(item).strip()
    ]
    if not terms and str(record.get("text") or "").strip():
        terms = [str(record.get("text") or "").strip()]
    return _dedupe(terms)


def _literal_matches(source: str, terms: list[str]) -> list[str]:
    matches: list[str] = []
    for term in terms:
        if not term:
            continue
        # Use cached compiled pattern to avoid repeated re.escape() + re.compile()
        pattern = _LITERAL_PATTERN_CACHE.get(term)
        if pattern is None:
            pattern = re.compile(re.escape(term))
            _LITERAL_PATTERN_CACHE[term] = pattern
        matches.extend(match.group(0) for match in pattern.finditer(source))
    return matches


def _replacement_advice(record: dict[str, Any]) -> str:
    alternatives = [
        str(item) for item in list(record.get("replacement_axes", []) or []) if str(item)
    ]
    if not alternatives:
        return "保留叙事功能，改换动作、对白或观察角度，避免同义词替换。"
    return "不要同义替换；改用" + "、".join(alternatives[:4]) + "承载情绪。"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_record_channel(value: Any) -> str:
    channel = str(value or "").strip()
    return channel if channel in _RECORD_CHANNELS else ""


__all__ = [
    "build_expression_channel_record",
    "build_expression_channel_records",
    "channel_specs",
    "classify_expression_channel",
    "detect_builtin_expression_overuse",
    "detect_expression_channel_hits",
    "expression_profiles_to_records",
]
