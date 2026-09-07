"""Source normalization for rhetorical forbidden elements."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.domain.guardrails import sanitize_story_text
from novel_forge.editorial.signals import build_expression_channel_record

_FORBIDDEN_BASE_SPLIT_RE = re.compile(r"(?:——|--|—|：|:|（|\(|【|\[)")
_QUOTED_TERM_RE = re.compile(r"[「『“\"']([^」』”\"']{2,24})[」』”\"']")
_COMPACT_PUNCT_RE = re.compile(
    r"""[，。！？；：、"'（）《》〈〉【】『』「」·,.!?;:()\[\]{}<>\-—~…\s]+"""
)
_SENTENCE_PUNCT_RE = re.compile(r"[。！？；;\n\r]")
_EVENTISH_MARKERS = frozenset(
    {
        "上一章",
        "本章",
        "章末",
        "开场",
        "结尾",
        "必须",
        "不得",
        "不能",
        "需要",
        "确认",
        "完成",
        "承接",
        "揭示",
        "发现",
        "交给",
        "交到",
        "推动",
        "落地",
    }
)
_SOURCE_NOTE_MARKERS = frozenset(
    {
        "避免重复使用",
        "近",
        "平均",
        "次/章",
        "高频",
        "过度使用",
    }
)


def compact_forbidden_source_text(value: Any) -> str:
    """Compact a potential forbidden source item for matching."""

    return _COMPACT_PUNCT_RE.sub("", str(value or ""))


def forbidden_base_text(value: Any) -> str:
    """Return the actionable head term of a forbidden-source item.

    Sources sometimes store explanatory notes such as
    ``避免重复使用「袖中」（近8章平均9.0次/章）``.  Production stages need the
    actual rhetorical surface term, not the whole diagnostic sentence.
    """

    text = sanitize_story_text(str(value or "").strip())
    if not text:
        return ""
    quoted = _extract_quoted_term(text)
    if quoted:
        return quoted
    base = _FORBIDDEN_BASE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    return base or text


def normalize_forbidden_source_list(
    value: Any,
    *,
    source: str,
    max_items: int | None = None,
) -> list[str]:
    """Normalize source items before they can enter production prompts.

    ``bridge`` and ``accumulated`` are upstream recall sources, so they are kept
    intentionally narrow: only short rhetorical terms or quoted diagnostic terms
    survive.  ``plan`` and ``style`` can carry explicit human/LLM instructions,
    so they retain more of the original text unless a quoted surface term exists.
    """

    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple | set):
        raw_items = list(value)
    elif isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = normalize_forbidden_source_item(raw, source=source)
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
        if max_items is not None and len(normalized) >= max_items:
            break
    return normalized


def normalize_forbidden_source_item(value: Any, *, source: str) -> str:
    """Normalize one forbidden-source item to an actionable term."""

    text = _raw_source_text(value)
    if not text:
        return ""
    quoted = _extract_quoted_term(text)
    if quoted:
        return quoted

    key = source.strip().lower()
    if key in {"bridge", "accumulated", "ngram", "motif"}:
        base = forbidden_base_text(text)
        if _looks_like_event_or_sentence(base):
            return ""
        return base

    if _looks_like_source_note(text):
        return forbidden_base_text(text)
    return text


def build_forbidden_source_records(
    value: Any,
    *,
    source: str,
    level: str,
    reason: str = "",
    confidence: float = 0.0,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Build provenance records for forbidden-source items.

    The public chapter plan still exposes plain string lists for compatibility;
    these records preserve where each string came from and how strong it should
    be before the projection into those legacy fields.
    """

    raw_items = _iter_raw_items(value)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = normalize_forbidden_source_item(raw, source=source)
        if not text or text in seen:
            continue
        seen.add(text)
        record_reason = _raw_source_reason(raw) or reason
        records.append(
            {
                "text": text,
                "source": source,
                "level": level,
                "original_level": level,
                "reason": record_reason,
                "confidence": _coerce_confidence(_raw_source_confidence(raw), confidence),
            }
        )
        expression_record = build_expression_channel_record(
            text,
            source=source,
            level=level,
            reason=record_reason,
        )
        if expression_record:
            records[-1].update(
                {
                    "channel": expression_record.get("channel", "other"),
                    "channel_id": expression_record.get("channel_id", ""),
                    "cooldown_chapters": expression_record.get("cooldown_chapters", 3),
                    "actor_scope": expression_record.get("actor_scope", "global"),
                    "examples": expression_record.get("examples", []),
                }
            )
        if max_items is not None and len(records) >= max_items:
            break
    return records


def project_forbidden_source_records(
    *,
    hard: list[str],
    soft: list[str],
    quota: list[str],
    source_records: list[dict[str, Any]],
    relevance_texts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Project provenance records onto the final hard/soft/quota string lists."""

    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for level, values in (("hard", hard), ("soft", soft), ("quota", quota)):
        for value in values:
            text = normalize_forbidden_source_item(value, source="plan")
            if not text:
                continue
            key = (level, text)
            if key in seen:
                continue
            seen.add(key)
            record = _find_source_record(text, source_records, level=level)
            item = {
                "text": text,
                "source": str(record.get("source", "unknown") if record else "unknown"),
                "level": level,
                "original_level": str(
                    record.get("original_level", record.get("level", level)) if record else level
                ),
                "reason": str(record.get("reason", "") if record else ""),
                "confidence": _coerce_confidence(
                    record.get("confidence") if record else None,
                    0.0,
                ),
            }
            if record:
                for optional_key in (
                    "motif_id",
                    "motif_category",
                    "motif_occurrence_count",
                    "motif_recent_chapters",
                    "motif_callback",
                    "motif_meaning",
                    "channel",
                    "channel_id",
                    "cooldown_chapters",
                    "actor_scope",
                    "examples",
                ):
                    if optional_key in record:
                        item[optional_key] = record[optional_key]
                item["rank_score"] = round(
                    _source_rank_score(text, record, relevance_texts or []),
                    4,
                )
            projected.append(item)
    return projected


def rank_forbidden_terms(
    values: list[str],
    *,
    source_records: list[dict[str, Any]],
    relevance_texts: list[str],
    max_items: int,
    enabled: bool = True,
    level: str | None = None,
) -> list[str]:
    """Rank and cap forbidden terms by source strength and chapter relevance."""

    clean_values = [normalize_forbidden_source_item(value, source="plan") for value in values]
    clean_values = [value for value in clean_values if value]
    if max_items < 0:
        max_items = 0
    if not enabled or len(clean_values) <= max_items:
        return clean_values[:max_items] if max_items >= 0 else clean_values

    scored: list[tuple[float, int, str]] = []
    for index, value in enumerate(clean_values):
        record = _find_source_record(value, source_records, level=level) or {}
        score = _source_rank_score(value, record, relevance_texts)
        scored.append((score, -index, value))
    scored.sort(reverse=True)
    return [value for _score, _index, value in scored[:max_items]]


def enrich_forbidden_records_with_motif_context(
    records: list[dict[str, Any]],
    motif_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach motif-memory evidence to source records without querying memory."""

    if not motif_context:
        return records
    motif_map = _build_motif_context_map(motif_context)
    if not motif_map:
        return records
    enriched: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        match = _find_motif_context_match(str(item.get("text", "")), motif_map)
        if match:
            item["motif_id"] = match.get("motif_id", "")
            item["motif_category"] = match.get("category", "")
            item["motif_occurrence_count"] = match.get("occurrence_count", 0)
            item["motif_recent_chapters"] = match.get("recent_chapters", [])
            if match.get("is_callback"):
                item["motif_callback"] = True
            if match.get("thematic_meaning"):
                item["motif_meaning"] = match.get("thematic_meaning", "")
        enriched.append(item)
    return enriched


def _iter_raw_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value]
    return []


def _raw_source_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "forbidden", "matched", "name", "motif", "value"):
            raw = value.get(key)
            if raw:
                return sanitize_story_text(str(raw).strip())
        return ""
    return sanitize_story_text(str(value or "").strip())


def _raw_source_reason(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("reason", "summary", "why", "note"):
        raw = value.get(key)
        if raw:
            return sanitize_story_text(str(raw).strip())
    return ""


def _raw_source_confidence(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("confidence")
    return None


def _coerce_confidence(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value if value is not None else default)))
    except (TypeError, ValueError):
        return max(0.0, min(1.0, float(default)))


def _find_source_record(
    text: str,
    records: list[dict[str, Any]],
    *,
    level: str | None = None,
) -> dict[str, Any] | None:
    target = forbidden_base_text(text)
    if level:
        for record in records:
            candidate = forbidden_base_text(record.get("text", ""))
            record_level = str(record.get("level", "") or "")
            if candidate and candidate == target and record_level == level:
                return record
    for record in records:
        candidate = forbidden_base_text(record.get("text", ""))
        if candidate and candidate == target:
            return record
    return None


def _source_rank_score(
    text: str,
    record: dict[str, Any],
    relevance_texts: list[str],
) -> float:
    source = str(record.get("source", "") or "")
    source_weight = {
        "style": 0.35,
        "plan": 0.30,
        "motif": 0.24,
        "bridge": 0.18,
        "accumulated": 0.10,
        "ngram": 0.08,
    }.get(source, 0.05)
    confidence = _coerce_confidence(record.get("confidence"), 0.0)
    relevance = _context_relevance(text, relevance_texts)
    motif_bonus = _motif_rank_bonus(record)
    return source_weight + confidence * 0.35 + relevance * 0.45 + motif_bonus


def _context_relevance(text: str, relevance_texts: list[str]) -> float:
    term = compact_forbidden_source_text(forbidden_base_text(text))
    if not term:
        return 0.0
    best = 0.0
    term_bigrams = _bigrams(term)
    for raw in relevance_texts:
        context = compact_forbidden_source_text(raw)
        if not context:
            continue
        if term in context:
            best = max(best, 1.0)
            continue
        context_bigrams = _bigrams(context)
        if term_bigrams and context_bigrams:
            overlap = len(term_bigrams & context_bigrams)
            denom = max(1, len(term_bigrams))
            best = max(best, min(1.0, overlap / denom))
    return best


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _motif_rank_bonus(record: dict[str, Any]) -> float:
    category = str(record.get("motif_category", "") or "")
    if category in {"主题", "符号"} or bool(record.get("motif_callback", False)):
        return -0.20
    if category in {"意象", "感官", "颜色", "声音", "动作"}:
        return 0.18
    return 0.0


def _build_motif_context_map(motif_context: dict[str, Any]) -> list[dict[str, Any]]:
    motifs: list[dict[str, Any]] = []
    active = motif_context.get("active_motifs", [])
    if isinstance(active, list):
        motifs.extend(item for item in active if isinstance(item, dict))
    callbacks = motif_context.get("suggested_callbacks", [])
    callback_names: set[str] = set()
    if isinstance(callbacks, list):
        for item in callbacks:
            if not isinstance(item, dict):
                continue
            name = str(item.get("motif", "") or item.get("name", "") or "").strip()
            if name:
                callback_names.add(forbidden_base_text(name))
    enriched: list[dict[str, Any]] = []
    for motif in motifs:
        name = str(motif.get("name", "") or motif.get("motif", "") or "").strip()
        if not name:
            continue
        item = dict(motif)
        item["name"] = name
        item["is_callback"] = forbidden_base_text(name) in callback_names
        enriched.append(item)
    return enriched


def _find_motif_context_match(
    text: str,
    motifs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    target = compact_forbidden_source_text(forbidden_base_text(text))
    if not target:
        return None
    for motif in motifs:
        name = compact_forbidden_source_text(forbidden_base_text(motif.get("name", "")))
        if name and (name in target or target in name):
            return motif
    return None


def _extract_quoted_term(text: str) -> str:
    match = _QUOTED_TERM_RE.search(text)
    if not match:
        return ""
    term = sanitize_story_text(match.group(1).strip())
    if len(compact_forbidden_source_text(term)) < 2:
        return ""
    return term


def _looks_like_source_note(text: str) -> bool:
    return any(marker in text for marker in _SOURCE_NOTE_MARKERS)


def _looks_like_event_or_sentence(text: str) -> bool:
    compact = compact_forbidden_source_text(text)
    if len(compact) < 2:
        return True
    if _SENTENCE_PUNCT_RE.search(text):
        return True
    if len(compact) > 18:
        return True
    return len(compact) > 8 and any(marker in text for marker in _EVENTISH_MARKERS)
