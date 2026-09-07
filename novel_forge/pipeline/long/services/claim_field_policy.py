"""Ownership policy for init-coherence claim fields.

The policy separates transport housekeeping from narrative decisions.  Local
code may normalize only ``LOCAL_STRUCTURAL`` fields; values owned by the LLM
must remain visible to validation and are eligible for evidence-bounded LLM
repair instead of deterministic semantic defaults.
"""

from __future__ import annotations

from enum import Enum


class ClaimFieldOwner(str, Enum):
    """Authority responsible for producing a claim field value."""

    LOCAL_STRUCTURAL = "local_structural"
    LLM_SEMANTIC = "llm_semantic"
    DERIVED = "derived"


CLAIM_ENUM_VALUES: dict[str, frozenset[str]] = {
    "claim_type": frozenset(
        {
            "state",
            "event",
            "payoff",
            "dependency",
            "relationship",
            "world_rule",
            "knowledge",
            "promise",
            "other",
        }
    ),
    "cognitive_level": frozenset(
        {"unaware", "subconscious", "suspicion", "partial", "confirmed", "acknowledged"}
    ),
    "action_level": frozenset({"none", "internal", "hinted", "revealed", "acted"}),
    "reader_awareness": frozenset({"unknown", "partial", "full"}),
    "temporality": frozenset(
        {"actual", "flashback", "foreshadow", "vision", "hypothetical", "planned", "unknown"}
    ),
}
CHARACTER_KNOWLEDGE_COVERAGE_VALUES = frozenset({"unknown", "partial", "full"})


CLAIM_FIELD_OWNERSHIP: dict[str, ClaimFieldOwner] = {
    "metadata": ClaimFieldOwner.LOCAL_STRUCTURAL,
    "claim_id": ClaimFieldOwner.LLM_SEMANTIC,
    "artifact": ClaimFieldOwner.LLM_SEMANTIC,
    "source_path": ClaimFieldOwner.LLM_SEMANTIC,
    "claim_type": ClaimFieldOwner.LLM_SEMANTIC,
    "claim_text": ClaimFieldOwner.LLM_SEMANTIC,
    "evidence": ClaimFieldOwner.LLM_SEMANTIC,
    "irreversible": ClaimFieldOwner.LLM_SEMANTIC,
    "temporality": ClaimFieldOwner.LLM_SEMANTIC,
    "cognitive_subjects": ClaimFieldOwner.LLM_SEMANTIC,
    "cognitive_object": ClaimFieldOwner.LLM_SEMANTIC,
    "cognitive_level": ClaimFieldOwner.LLM_SEMANTIC,
    "action_level": ClaimFieldOwner.LLM_SEMANTIC,
    "reader_awareness": ClaimFieldOwner.LLM_SEMANTIC,
    "character_knowledge_coverage": ClaimFieldOwner.LLM_SEMANTIC,
    "cognitive_chapter": ClaimFieldOwner.LLM_SEMANTIC,
    "public_reveal_chapter": ClaimFieldOwner.LLM_SEMANTIC,
    "foreshadow_chapters": ClaimFieldOwner.LLM_SEMANTIC,
    "confidence": ClaimFieldOwner.LLM_SEMANTIC,
    "chapter_range": ClaimFieldOwner.DERIVED,
}

LOCAL_STRUCTURAL_CLAIM_FIELDS = frozenset(
    field
    for field, owner in CLAIM_FIELD_OWNERSHIP.items()
    if owner == ClaimFieldOwner.LOCAL_STRUCTURAL
)

# These fields can be re-judged from one claim's existing text/evidence without
# re-extracting or rewriting the claim itself.  Identity, source, claim text,
# and evidence are deliberately excluded: damage there requires re-extraction.
LLM_REPAIRABLE_CLAIM_FIELDS = frozenset(
    {
        "claim_type",
        "irreversible",
        "temporality",
        "cognitive_subjects",
        "cognitive_object",
        "cognitive_level",
        "action_level",
        "reader_awareness",
        "character_knowledge_coverage",
        "cognitive_chapter",
        "public_reveal_chapter",
        "foreshadow_chapters",
        "confidence",
    }
)


__all__ = [
    "CHARACTER_KNOWLEDGE_COVERAGE_VALUES",
    "CLAIM_ENUM_VALUES",
    "CLAIM_FIELD_OWNERSHIP",
    "LLM_REPAIRABLE_CLAIM_FIELDS",
    "LOCAL_STRUCTURAL_CLAIM_FIELDS",
    "ClaimFieldOwner",
]
