"""Canonical cognitive-state field types and structural normalization.

This module is deliberately free of narrative judgement.  LLM-authored
semantics remain authoritative; local code only enforces the shared wire
contract used by initialization, chapter contracts, runtime prompts, and
narrative-state persistence.
"""

from __future__ import annotations

from typing import Any, Literal, cast

ActionLevel = Literal[
    "none",
    "internal",
    "hinted",
    "revealed",
    "acted",
]
CognitiveLevel = Literal[
    "unaware",
    "subconscious",
    "suspicion",
    "partial",
    "confirmed",
    "acknowledged",
]
AwarenessLevel = Literal[
    "unknown",
    "partial",
    "full",
]

AWARENESS_LEVEL_VALUES = frozenset({"unknown", "partial", "full"})


def normalize_character_knowledge_coverage(
    value: Any,
    *,
    none_as_empty: bool,
) -> dict[str, AwarenessLevel]:
    """Validate the shared per-character knowledge-coverage wire shape.

    ``none_as_empty`` is explicit because runtime/state models accept an absent
    optional map while init claim extraction requires the key to contain an
    object.  No knowledge level is inferred from ``cognitive_level`` or prose.
    """

    if value is None:
        if none_as_empty:
            return {}
        raise ValueError("character_knowledge_coverage must be an object")
    if not isinstance(value, dict):
        raise ValueError("character_knowledge_coverage must be an object")

    normalized: dict[str, AwarenessLevel] = {}
    for raw_name, raw_awareness in value.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        awareness = str(raw_awareness or "unknown").strip().lower()
        if awareness not in AWARENESS_LEVEL_VALUES:
            raise ValueError(
                "character_knowledge_coverage values must be unknown, partial, or full"
            )
        normalized[name] = cast(AwarenessLevel, awareness)
    return normalized
