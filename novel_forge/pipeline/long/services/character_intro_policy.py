"""Pure policies for long-form automatic character introduction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from novel_forge.core.domain.character_identity import (
    clean_character_name,
    parenthetical_base_name,
    resolve_existing_character_name,
)
from novel_forge.narrative_state.schemas import EntityRecord, stable_id
from novel_forge.narrative_state.store import NarrativeStateStore

_SIGNAL_PRIORITY: dict[str, int] = {
    "chapter_contract": 100,
    "pov": 95,
    "turning_point": 90,
    "creative_carry_forward": 85,
    "creative_report": 75,
    "phase": 60,
    "arc": 55,
    "outline": 20,
    "pending_retry": 10,
}
_IMPORTANCE_PRIORITY: dict[str, int] = {
    "major": 3,
    "supporting": 2,
    "minor": 1,
    "incidental": 0,
    "none": 0,
    "label": 0,
    "unknown": 0,
}
_PLACEHOLDER_TEXTS = {
    "",
    "未知",
    "不明",
    "不详",
    "待定",
    "普通",
    "无",
    "暂无",
    "未提及",
    "无法确认",
    "n/a",
    "na",
    "none",
    "unknown",
    "tbd",
}
_GENDER_VALUES = {"男", "女", "不明"}


def intro_pending_max_attempts(settings: Any) -> int:
    """Return the configured max pending retry attempts."""
    try:
        return max(
            0,
            int(getattr(settings, "long_auto_introduce_pending_max_attempts", 2) or 0),
        )
    except (TypeError, ValueError):
        return 2


def apply_pending_retry_policy(
    candidate_signals: dict[str, set[str]],
    pending_payload: dict[str, Any],
    *,
    max_attempts: int,
) -> tuple[dict[str, set[str]], list[str]]:
    """Add retry signals for currently-mentioned pending characters.

    Pending entries are retried only when the same cleaned name is present in
    current chapter signals. Entries at or above the attempt cap are suppressed
    for this automatic pass.
    """
    signals = {
        clean_character_name(name): set(sources) for name, sources in candidate_signals.items()
    }
    attempts_by_name: dict[str, int] = {}
    for item in list(pending_payload.get("pending", []) or []):
        if not isinstance(item, dict):
            continue
        name = clean_character_name(item.get("character"))
        if not name:
            continue
        try:
            attempts = int(item.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        attempts_by_name[name] = max(attempts_by_name.get(name, 0), attempts)

    exhausted: list[str] = []
    for name, attempts in attempts_by_name.items():
        if name not in signals:
            continue
        if max_attempts <= 0 or attempts >= max_attempts:
            signals.pop(name, None)
            exhausted.append(name)
        else:
            signals[name].add("pending_retry")
    return signals, sorted(exhausted)


def load_character_alias_lookup(project_root: Path) -> dict[str, str]:
    """Load character alias -> canonical name mappings from narrative_state."""
    registry_path = Path(project_root) / "narrative_state" / "entity_registry.json"
    if not registry_path.exists():
        return {}
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    lookup: dict[str, str] = {}
    entities = payload.get("entities") if isinstance(payload, dict) else None
    if not isinstance(entities, list):
        return lookup
    for raw in entities:
        if not isinstance(raw, dict):
            continue
        entity_type = str(raw.get("entity_type") or "")
        if entity_type != "character":
            continue
        canonical = clean_character_name(raw.get("name"))
        if not canonical:
            continue
        lookup[canonical] = canonical
        for alias in list(raw.get("aliases") or []):
            cleaned_alias = clean_character_name(alias)
            if cleaned_alias:
                lookup[cleaned_alias] = canonical
    return lookup


def resolve_known_character_name(
    name: Any,
    existing_names: Iterable[str],
    *,
    alias_lookup: dict[str, str] | None = None,
) -> str | None:
    """Resolve exact names, bracketed state labels, and registry aliases."""
    resolved = resolve_existing_character_name(name, existing_names)
    if resolved is not None:
        return resolved
    aliases = alias_lookup or {}
    cleaned = clean_character_name(name)
    if not cleaned:
        return None
    if cleaned in aliases:
        return aliases[cleaned]
    base = parenthetical_base_name(cleaned)
    if base and base in aliases:
        return aliases[base]
    return None


def upsert_character_alias(
    *,
    project_root: Path,
    canonical_name: str,
    alias: str,
) -> bool:
    """Write a merge_existing alias into the narrative-state registry."""
    canonical = clean_character_name(canonical_name)
    alias_name = clean_character_name(alias)
    if not canonical or not alias_name or canonical == alias_name:
        return False

    store = NarrativeStateStore(project_root)
    registry = store.load_entity_registry()
    target: EntityRecord | None = None
    for entity in registry.entities:
        if entity.entity_type == "character" and clean_character_name(entity.name) == canonical:
            target = entity
            break
    if target is None:
        target = EntityRecord(
            entity_id=stable_id("char", canonical),
            name=canonical,
            entity_type="character",
            aliases=[],
            source="character_intro_merge_existing",
        )
        registry.entities.append(target)

    aliases = [clean_character_name(item) for item in target.aliases if clean_character_name(item)]
    if alias_name in aliases:
        return False
    target.aliases = [*aliases, alias_name]
    store.save_entity_registry(registry)
    return True


def is_placeholder_text(value: Any) -> bool:
    text = clean_character_name(value).lower()
    return text in _PLACEHOLDER_TEXTS


def usable_profile_text(value: Any, *, min_len: int = 2) -> str:
    """Return a stripped profile text only if it carries useful content."""
    text = str(value or "").strip()
    if not text or is_placeholder_text(text):
        return ""
    if len(text) < min_len:
        return ""
    return text


def normalize_gender_update(existing: Any, incoming: Any) -> str:
    """Return a safe gender update, or an empty string when it should be ignored."""
    old = clean_character_name(existing)
    new = clean_character_name(incoming)
    if new not in _GENDER_VALUES:
        return ""
    if old and old != "不明":
        return ""
    if new == "不明" and old:
        return ""
    return new


def sanitize_relationships(
    relationships: Any,
    *,
    self_name: str,
    known_names: Iterable[str],
    alias_lookup: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Keep only relationships to known characters and report dropped edges."""
    if not isinstance(relationships, dict):
        return {}, []
    known = {clean_character_name(name) for name in known_names if clean_character_name(name)}
    cleaned_self = clean_character_name(self_name)
    sanitized: dict[str, str] = {}
    dropped: list[str] = []
    for raw_key, raw_value in relationships.items():
        rel_name = clean_character_name(raw_key)
        rel_text = usable_profile_text(raw_value)
        if not rel_name or not rel_text:
            continue
        resolved = resolve_known_character_name(rel_name, known, alias_lookup=alias_lookup)
        if resolved is None and rel_name in known:
            resolved = rel_name
        if not resolved:
            dropped.append(f"{rel_name}: {rel_text}")
            continue
        if clean_character_name(resolved) == cleaned_self:
            dropped.append(f"{rel_name}: self relationship ignored")
            continue
        sanitized[clean_character_name(resolved)] = rel_text
    return sanitized, dropped


def sort_auto_new_names(
    names: Iterable[str],
    *,
    signals: dict[str, set[str]] | None = None,
    importance_by_name: dict[str, str] | None = None,
    confidence_by_name: dict[str, float] | None = None,
) -> list[str]:
    """Sort auto-introduction names by deterministic narrative priority."""
    signal_map = signals or {}
    importance_map = importance_by_name or {}
    confidence_map = confidence_by_name or {}

    def _score(name: str) -> tuple[int, int, float, str]:
        sources = signal_map.get(name, set())
        signal_score = max((_SIGNAL_PRIORITY.get(source, 0) for source in sources), default=0)
        importance_score = _IMPORTANCE_PRIORITY.get(
            str(importance_map.get(name) or "").strip().lower(),
            0,
        )
        try:
            confidence = float(confidence_map.get(name) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return (-signal_score, -importance_score, -confidence, name)

    return sorted(
        (clean_character_name(name) for name in names if clean_character_name(name)), key=_score
    )


def creative_character_has_carry_forward(
    name: str,
    creative_report: Any,
    *,
    future_contracts_payload: Any = None,
) -> bool:
    """Return whether a creative-report minor character has later-use evidence."""
    cleaned = clean_character_name(name)
    if not cleaned:
        return False
    haystacks: list[str] = []
    for attr in (
        "must_carry_forward",
        "bridge_hints",
        "character_state_deltas",
        "relationship_deltas",
    ):
        value = getattr(creative_report, attr, None)
        if value:
            haystacks.append(_json_text(value))
    if future_contracts_payload:
        haystacks.append(_json_text(future_contracts_payload))
    return any(cleaned in text for text in haystacks)


def creative_importance_allows_auto_register(
    importance: Any,
    *,
    has_carry_forward: bool,
) -> bool:
    """Gate creative-report candidates before LLM adjudication."""
    normalized = str(importance or "").strip().lower()
    if normalized in {"major", "supporting"}:
        return True
    if normalized == "minor" and has_carry_forward:
        return True
    return False


def _json_text(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)
