"""Post-generation knowledge boundary verification.

Detects potential character-knowledge leaks by string-matching against an audit
card that is built from hidden StoryKernel knowledge entries. The audit card is
for local verification only and must not be injected into LLM prompts.

Usage::

    issues = verify_knowledge_boundaries(
        draft_text=chapter_text,
        knowledge_card=knowledge_card_dict,
        pov_character="Alice",
        current_chapter=5,
    )
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from novel_forge.narrative_state.knowledge_ops import (
    build_entity_lookup,
    normalize_knowledge_ops,
)

__all__ = [
    "BoundaryIssue",
    "Severity",
    "build_knowledge_audit_card",
    "verify_knowledge_boundaries",
]


# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Issue severity levels for knowledge boundary violations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# BoundaryIssue dataclass
# ---------------------------------------------------------------------------


@dataclass
class BoundaryIssue:
    """A detected knowledge boundary violation.

    Attributes:
        chapter: The chapter number where the issue was detected.
        description: Human-readable description of the leak.
        severity: Severity level of the issue.
        entry_id: Identifier of the knowledge entry that leaked.
    """

    chapter: int
    description: str
    severity: Severity
    entry_id: str
    evidence_quote: str = ""
    confidence: float = 0.0
    reason: str = ""
    character: str = ""
    entity_id: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MIN_PHRASE_LEN = 5


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fact_fingerprint(fact: str) -> str:
    return hashlib.sha256(str(fact or "").encode("utf-8")).hexdigest()[:16]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="json")
        except TypeError:
            payload = dump()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_phrases(fact: str, min_len: int = _MIN_PHRASE_LEN) -> list[str]:
    """Extract meaningful phrases from a fact string for matching.

    Splits on punctuation and whitespace, returns phrases >= min_len chars.
    """
    if not fact or not fact.strip():
        return []
    phrases: list[str] = []
    full = fact.strip()
    if len(full) >= min_len:
        phrases.append(full)
    # Split on sentence-level punctuation and whitespace
    raw_phrases = re.split(r"[。！？；\n.!?\s]+", full)
    for phrase in raw_phrases:
        text = phrase.strip()
        if len(text) >= min_len and text not in phrases:
            phrases.append(text)
    return phrases


def _visibility_text(value: Any) -> str:
    raw = getattr(value, "value", value)
    return _clean(raw).lower() or "private"


def _same_character_or_entity(
    *,
    op: dict[str, Any],
    entity_id: str,
    character: str,
) -> bool:
    op_entity = _clean(op.get("entity_id"))
    op_character = _clean(op.get("character"))
    if not op_entity and not op_character:
        return True
    return bool((op_entity and op_entity == entity_id) or (op_character and op_character == character))


def _op_allows_hidden_entry(
    *,
    entry: dict[str, Any],
    character: str,
    allowed_ops: list[dict[str, Any]],
) -> bool:
    """Return True when this chapter contract explicitly permits the fact change."""

    fact = _clean(entry.get("fact"))
    entity_id = _clean(entry.get("entity_id"))
    if not fact:
        return False
    for op in allowed_ops:
        if not _same_character_or_entity(op=op, entity_id=entity_id, character=character):
            continue
        op_fact = _clean(op.get("fact") or op.get("description") or op.get("target_text"))
        if not op_fact:
            continue
        if fact == op_fact or fact in op_fact or op_fact in fact:
            return True
    return False


def _canonical_visibility(value: Any) -> str:
    visibility = _visibility_text(value)
    return visibility if visibility in {"public", "private", "secret"} else "private"


def _canonical_knowledge_type(value: Any) -> str:
    raw = _clean(value).lower() or "known"
    aliases = {
        "secret": "secret_kept",
        "hidden": "secret_kept",
        "hide": "secret_kept",
        "suspect": "suspected",
        "false_belief": "misbelief",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in {"known", "suspected", "misbelief", "secret_kept"} else "known"


def build_knowledge_audit_card(
    *,
    kernel_context: dict[str, Any],
    chapter_contract: dict[str, Any] | None = None,
    current_chapter: int,
    pov_character: str = "",
    limit: int = 320,
) -> dict[str, Any]:
    """Build local-only hidden-knowledge candidates for boundary verification."""

    entities = list((kernel_context or {}).get("entities") or [])
    lookup = build_entity_lookup(entities)
    id_to_name = lookup.get("id_to_name", {})
    allowed_ops = normalize_knowledge_ops(
        (chapter_contract or {}).get("knowledge_ops", []),
        entity_lookup=lookup,
        limit=12,
    )

    hidden_candidates: list[dict[str, Any]] = []
    for raw in list((kernel_context or {}).get("knowledge_ledger") or [])[: max(0, limit)]:
        data = _as_mapping(raw)
        entity_id = _clean(data.get("entity_id"))
        fact = _clean(data.get("fact"))
        if not entity_id or not fact:
            continue

        character = id_to_name.get(entity_id, entity_id)
        reasons: list[str] = []
        source_chapter = _int(data.get("source_chapter"))
        revealed_in = _int(data.get("revealed_in_chapter"))
        visibility = _canonical_visibility(data.get("visibility"))

        if current_chapter > 0 and source_chapter > current_chapter:
            reasons.append("future_source_chapter")
        if current_chapter > 0 and revealed_in > current_chapter:
            reasons.append("future_reveal")
        if pov_character and visibility != "public" and character != pov_character:
            reasons.append("private_non_pov")

        if not reasons:
            continue
        if _op_allows_hidden_entry(entry=data, character=character, allowed_ops=allowed_ops):
            continue

        hidden_candidates.append(
            {
                "entry_id": _clean(data.get("entry_id")) or f"{entity_id}:{fact[:24]}",
                "entity_id": entity_id,
                "entity_name": character,
                "fact": fact,
                "fact_fingerprint": _fact_fingerprint(fact),
                "knowledge_type": _canonical_knowledge_type(data.get("knowledge_type")),
                "source_chapter": source_chapter,
                "revealed_in_chapter": revealed_in,
                "visibility": visibility,
                "confidence": max(0.0, min(1.0, _float(data.get("confidence"), 1.0))),
                "notes": _clean(data.get("notes")),
                "boundary_reasons": reasons,
            }
        )

    return {
        "source": "story_kernel_audit",
        "current_chapter": current_chapter,
        "pov_character": pov_character,
        "hidden_candidates": hidden_candidates,
        "allowed_current_ops": allowed_ops,
    }


def _match_confidence(fact: str, phrase: str) -> float:
    if fact and phrase == fact and len(phrase) >= 8:
        return 0.94
    if len(phrase) >= 20:
        return 0.88
    if len(phrase) >= 8:
        return 0.82
    return 0.7


def _candidate_severity(reasons: list[str], phrase: str, confidence: float) -> Severity:
    if confidence >= 0.88:
        return Severity.HIGH
    if "future_source_chapter" in reasons or "future_reveal" in reasons:
        return Severity.HIGH if len(phrase) >= 8 else Severity.MEDIUM
    return Severity.MEDIUM


def _candidate_boundary_reasons(candidate: dict[str, Any]) -> list[str]:
    raw = candidate.get("boundary_reasons")
    if not isinstance(raw, list):
        raw = candidate.get("reasons")
    return [_clean(item) for item in raw or [] if _clean(item)]


def _detect_hidden_candidate_leak(
    candidate: dict[str, Any],
    draft_text: str,
    chapter: int,
) -> BoundaryIssue | None:
    fact = _clean(candidate.get("fact"))
    reasons = _candidate_boundary_reasons(candidate)
    for phrase in _extract_phrases(fact):
        if phrase not in draft_text:
            continue
        confidence = _match_confidence(fact, phrase)
        severity = _candidate_severity(reasons, phrase, confidence)
        character = _clean(candidate.get("entity_name") or candidate.get("character"))
        entity_id = _clean(candidate.get("entity_id"))
        reason_text = ",".join(reasons) or "hidden_knowledge"
        return BoundaryIssue(
            chapter=chapter,
            description=(
                f"Knowledge boundary prescreen hit ({reason_text}): candidate "
                f"{_clean(candidate.get('entry_id')) or entity_id} appears in chapter {chapter} text"
            ),
            severity=severity,
            entry_id=_clean(candidate.get("entry_id")) or f"{entity_id}:{character}",
            evidence_quote=phrase,
            confidence=confidence,
            reason=reason_text,
            character=character,
            entity_id=entity_id,
        )
    return None


def _detect_privacy_leak(
    fact: str,
    draft_text: str,
    entity_id: str,
    character: str,
    chapter: int,
    card: dict,
) -> BoundaryIssue | None:
    """Check if a PRIVATE fact from a non-POV character leaks into draft."""
    phrases = _extract_phrases(fact)
    for phrase in phrases:
        if phrase in draft_text:
            knowledge_type = card.get("knowledge_type", "private")
            return BoundaryIssue(
                chapter=chapter,
                description=(
                    f"PRIVATE knowledge prescreen hit: '{character}' ({entity_id}) "
                    "has a non-POV fact phrase in draft"
                ),
                severity=Severity.HIGH if len(phrase) > 20 else Severity.MEDIUM,
                entry_id=f"{entity_id}:{character}:{knowledge_type}",
                evidence_quote=phrase,
                confidence=_match_confidence(fact, phrase),
                reason="legacy_private_non_pov",
                character=character,
                entity_id=entity_id,
            )
    return None


def _detect_temporal_leak(
    fact: str,
    draft_text: str,
    entity_id: str,
    character: str,
    chapter: int,
    source_chapter: int,
) -> BoundaryIssue | None:
    """Check if a fact from a future chapter leaks into current draft."""
    phrases = _extract_phrases(fact)
    for phrase in phrases:
        if phrase in draft_text:
            return BoundaryIssue(
                chapter=chapter,
                description=(
                    f"Temporal knowledge prescreen hit: fact from chapter {source_chapter} "
                    f"appears in chapter {chapter} draft"
                ),
                severity=Severity.HIGH if source_chapter > chapter + 3 else Severity.MEDIUM,
                entry_id=f"{entity_id}:{character}:temporal:{source_chapter}",
                evidence_quote=phrase,
                confidence=_match_confidence(fact, phrase),
                reason="legacy_future_source_chapter",
                character=character,
                entity_id=entity_id,
            )
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def verify_knowledge_boundaries(
    draft_text: str,
    knowledge_card: dict,
    pov_character: str,
    current_chapter: int,
) -> list[BoundaryIssue]:
    """Prescreen potential knowledge leaks by string-matching hidden entries.

    Checks two boundary conditions:
    1. **Privacy**: PRIVATE entries where entity_id != pov_character —
       if fact phrases appear in the draft, it's a potential leak.
    2. **Temporal**: Entries where source_chapter > current_chapter —
       if fact phrases appear in the draft, it's a future-knowledge leak.

    This function is an advisory local prescreen.  It proposes suspicious
    evidence spans for a downstream adjudicator and must not be treated as the
    final semantic leak judgment unless the caller is deliberately using a
    high-confidence local fallback.

    Args:
        draft_text: The generated chapter text to check.
        knowledge_card: Dict with ``character_cards`` list.  Each card has
            ``entity_id``, ``character``, ``known_facts``, ``suspicions``,
            ``misbeliefs``, ``secrets_kept``, and optionally ``source_chapter``.
        pov_character: Name of the POV character for this chapter.
        current_chapter: Current chapter number.

    Returns:
        List of :class:`BoundaryIssue` objects, one per detected leak.
    """
    issues: list[BoundaryIssue] = []

    if not draft_text or not draft_text.strip():
        return issues

    hidden_candidates = knowledge_card.get("hidden_candidates")
    if isinstance(hidden_candidates, list):
        for raw in hidden_candidates:
            candidate = _as_mapping(raw)
            if not candidate:
                continue
            issue = _detect_hidden_candidate_leak(candidate, draft_text, current_chapter)
            if issue is not None:
                issues.append(issue)
        return issues

    character_cards = knowledge_card.get("character_cards")
    if not isinstance(character_cards, list):
        return issues

    draft_lower = draft_text  # Use original for matching (case-sensitive Chinese)

    for card in character_cards:
        if not isinstance(card, dict):
            continue

        entity_id = str(card.get("entity_id", "") or "").strip()
        character = str(card.get("character", "") or "").strip()
        source_chapter = int(card.get("source_chapter", 0) or 0)

        # Collect all fact-like fields to check
        fact_buckets: list[tuple[str, str]] = []  # (fact_text, knowledge_type)
        for bucket_name, knowledge_type in (
            ("known_facts", "known"),
            ("suspicions", "suspected"),
            ("misbeliefs", "misbelief"),
            ("secrets_kept", "secret_kept"),
        ):
            for fact in card.get(bucket_name, []) or []:
                fact_text = str(fact or "").strip()
                if fact_text:
                    fact_buckets.append((fact_text, knowledge_type))

        for fact_text, knowledge_type in fact_buckets:
            # --- Privacy check: PRIVATE knowledge from non-POV character ---
            is_pov = bool(pov_character) and character == pov_character
            if not is_pov:
                card_with_type = {**card, "knowledge_type": knowledge_type}
                issue = _detect_privacy_leak(
                    fact=fact_text,
                    draft_text=draft_lower,
                    entity_id=entity_id,
                    character=character,
                    chapter=current_chapter,
                    card=card_with_type,
                )
                if issue is not None:
                    issues.append(issue)

            # --- Temporal check: future chapter knowledge ---
            if source_chapter > 0 and source_chapter > current_chapter:
                issue = _detect_temporal_leak(
                    fact=fact_text,
                    draft_text=draft_lower,
                    entity_id=entity_id,
                    character=character,
                    chapter=current_chapter,
                    source_chapter=source_chapter,
                )
                if issue is not None:
                    issues.append(issue)

    return issues
