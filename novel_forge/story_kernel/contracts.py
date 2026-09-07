"""Field contracts for long-form pipeline steps.

Each contract declares what StoryKernel fields a pipeline step reads, writes,
and cannot modify.  Runtime enforcement lives in `contract_guard.py` and runs
at the `StoryKernelStateWriter` write boundary for writes attributed via
`step_names` (see settings `kernel_contract_mode`: off / warn / strict).

The 10 field groups of StoryKernel:
    world_rules, entities, relationships, timeline, object_ledger,
    knowledge_ledger, access_ledger, promise_ledger, motif_protocols,
    business_dependencies

Supplementary fields:
    chapter_summaries, chapter_exit_states, plot_threads, banned_phrases, notes

Metadata fields:
    project_id, current_chapter, active_volume, title, premise
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Valid StoryKernel field names
# ---------------------------------------------------------------------------

VALID_FIELD_NAMES: frozenset[str] = frozenset(
    {
        # Metadata
        "project_id",
        "current_chapter",
        "active_volume",
        "title",
        "premise",
        # Field groups (10)
        "world_rules",
        "entities",
        "relationships",
        "timeline",
        "object_ledger",
        "knowledge_ledger",
        "access_ledger",
        "promise_ledger",
        "motif_protocols",
        "business_dependencies",
        # Supplementary
        "chapter_summaries",
        "chapter_exit_states",
        "plot_threads",
        "banned_phrases",
        "notes",
    }
)


# ---------------------------------------------------------------------------
# FieldContract dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldContract:
    """Declares what StoryKernel fields a pipeline step may access.

    Attributes:
        step_name: Human-readable step identifier.
        reads: Fields the step reads as input.
        writes: Fields the step can modify or create.
        immutable: Fields the step must never change.
    """

    step_name: str
    reads: frozenset[str] = field(default_factory=frozenset)
    writes: frozenset[str] = field(default_factory=frozenset)
    immutable: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Shared immutable sets (DRY)
# ---------------------------------------------------------------------------

# Most steps must never touch world_rules.
_WORLD_RULES_IMMUTABLE: frozenset[str] = frozenset({"world_rules"})

# Read-only audit steps cannot modify anything.
_EMPTY_WRITES: frozenset[str] = frozenset()

# Full book audit reads everything.
_ALL_FIELDS: frozenset[str] = VALID_FIELD_NAMES

# Steps that only read narrative context (no metadata).
_NARRATIVE_CONTEXT: frozenset[str] = frozenset(
    {
        "entities",
        "relationships",
        "timeline",
        "world_rules",
        "object_ledger",
        "knowledge_ledger",
        "access_ledger",
        "promise_ledger",
        "motif_protocols",
        "business_dependencies",
        "chapter_summaries",
        "banned_phrases",
        "notes",
    }
)


# ---------------------------------------------------------------------------
# 24 Pipeline Step Contracts
# ---------------------------------------------------------------------------

# 1. Bridge — builds continuity bridge between chapters
BRIDGE_CONTRACT = FieldContract(
    step_name="bridge",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "knowledge_ledger",
            "promise_ledger",
            "motif_protocols",
            "chapter_summaries",
        }
    ),
    writes=frozenset({"chapter_summaries"}),
    immutable=_WORLD_RULES_IMMUTABLE,
)

# 2. Plan — plans chapter scenes and beats (read-only on kernel)
PLAN_CONTRACT = FieldContract(
    step_name="plan",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "object_ledger",
            "knowledge_ledger",
            "promise_ledger",
            "motif_protocols",
            "business_dependencies",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 3. Draft — writes chapter draft text (read-only on kernel)
DRAFT_CONTRACT = FieldContract(
    step_name="draft",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "object_ledger",
            "knowledge_ledger",
            "access_ledger",
            "promise_ledger",
            "motif_protocols",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 4. Edit — edits chapter text (read-only on kernel)
EDIT_CONTRACT = FieldContract(
    step_name="edit",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "knowledge_ledger",
            "access_ledger",
            "banned_phrases",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 5. Continuity Eval — evaluates continuity (report only)
CONTINUITY_EVAL_CONTRACT = FieldContract(
    step_name="continuity_eval",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "knowledge_ledger",
            "object_ledger",
            "promise_ledger",
            "motif_protocols",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 6. Continuity Repair — repairs continuity issues in chapter text
CONTINUITY_REPAIR_CONTRACT = FieldContract(
    step_name="continuity_repair",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "knowledge_ledger",
            "object_ledger",
            "promise_ledger",
            "motif_protocols",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=_WORLD_RULES_IMMUTABLE,
)

# 7. Causal Validate — validates causal chains (report only)
CAUSAL_VALIDATE_CONTRACT = FieldContract(
    step_name="causal_validate",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "knowledge_ledger",
            "business_dependencies",
            "promise_ledger",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 8. Causal Repair — repairs causal issues in chapter text
CAUSAL_REPAIR_CONTRACT = FieldContract(
    step_name="causal_repair",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "knowledge_ledger",
            "business_dependencies",
            "promise_ledger",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=_WORLD_RULES_IMMUTABLE,
)

# 9. Extract — extracts canon from chapter text into kernel
EXTRACT_CONTRACT = FieldContract(
    step_name="extract",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "object_ledger",
            "knowledge_ledger",
            "access_ledger",
            "promise_ledger",
            "motif_protocols",
            "business_dependencies",
            "chapter_summaries",
        }
    ),
    writes=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "object_ledger",
            "knowledge_ledger",
            "access_ledger",
            "promise_ledger",
            "motif_protocols",
            "chapter_summaries",
        }
    ),
    immutable=frozenset({"world_rules", "business_dependencies"}),
)

# 10. Alignment — checks outline alignment (report only)
ALIGNMENT_CONTRACT = FieldContract(
    step_name="alignment",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "promise_ledger",
            "motif_protocols",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 11. Check Chapter — general chapter quality check (report only)
CHECK_CHAPTER_CONTRACT = FieldContract(
    step_name="check_chapter",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "knowledge_ledger",
            "object_ledger",
            "promise_ledger",
            "banned_phrases",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 12. Reading Power Eval — evaluates reading experience (report only)
READING_POWER_EVAL_CONTRACT = FieldContract(
    step_name="reading_power_eval",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 13. Reading Power Repair — repairs reading power issues
READING_POWER_REPAIR_CONTRACT = FieldContract(
    step_name="reading_power_repair",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=_WORLD_RULES_IMMUTABLE,
)

# 14. Polish — final chapter polish
POLISH_CONTRACT = FieldContract(
    step_name="polish",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "banned_phrases",
            "motif_protocols",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=_WORLD_RULES_IMMUTABLE,
)

# 15. Evaluate — quality evaluation (report only)
EVALUATE_CONTRACT = FieldContract(
    step_name="evaluate",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 16. Volume — volume-level audit (report only)
VOLUME_CONTRACT = FieldContract(
    step_name="volume",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "world_rules",
            "promise_ledger",
            "motif_protocols",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 17. Book Consistency — full book consistency audit (report only)
BOOK_CONSISTENCY_CONTRACT = FieldContract(
    step_name="book_consistency",
    reads=_ALL_FIELDS,
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 18. Macro Guard — macro-level guardrail audit (report only)
MACRO_GUARD_CONTRACT = FieldContract(
    step_name="macro_guard",
    reads=_ALL_FIELDS,
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 19. State Adjudication — adjudicates proposed state changes
STATE_ADJUDICATION_CONTRACT = FieldContract(
    step_name="state_adjudication",
    reads=frozenset(
        {
            "entities",
            "relationships",
            "timeline",
            "knowledge_ledger",
            "object_ledger",
            "access_ledger",
            "promise_ledger",
            "business_dependencies",
        }
    ),
    writes=frozenset(
        {
            "entities",
            "relationships",
            "knowledge_ledger",
            "object_ledger",
            "access_ledger",
            "promise_ledger",
            "business_dependencies",
        }
    ),
    immutable=_WORLD_RULES_IMMUTABLE,
)

# 20. Patch — applies precise text patches (read-only on kernel)
PATCH_CONTRACT = FieldContract(
    step_name="patch",
    reads=frozenset({"entities", "world_rules"}),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 21. Pronoun Check — checks pronoun consistency (report only)
PRONOUN_CHECK_CONTRACT = FieldContract(
    step_name="pronoun_check",
    reads=frozenset({"entities"}),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 22. Forbidden Sources — checks for banned content (report only)
FORBIDDEN_SOURCES_CONTRACT = FieldContract(
    step_name="forbidden_sources",
    reads=frozenset({"banned_phrases"}),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 23. Profile Style — generates style profile (read-only on kernel)
PROFILE_STYLE_CONTRACT = FieldContract(
    step_name="profile_style",
    reads=frozenset(
        {
            "entities",
            "chapter_summaries",
        }
    ),
    writes=_EMPTY_WRITES,
    immutable=VALID_FIELD_NAMES,
)

# 24. Character Intro — introduces a new character to the kernel
CHARACTER_INTRO_CONTRACT = FieldContract(
    step_name="character_intro",
    reads=frozenset(
        {
            "entities",
            "relationships",
        }
    ),
    writes=frozenset(
        {
            "entities",
            "relationships",
        }
    ),
    immutable=_WORLD_RULES_IMMUTABLE,
)


# ---------------------------------------------------------------------------
# Registry — all contracts indexed by name
# ---------------------------------------------------------------------------

ALL_CONTRACTS: dict[str, FieldContract] = {
    "BRIDGE_CONTRACT": BRIDGE_CONTRACT,
    "PLAN_CONTRACT": PLAN_CONTRACT,
    "DRAFT_CONTRACT": DRAFT_CONTRACT,
    "EDIT_CONTRACT": EDIT_CONTRACT,
    "CONTINUITY_EVAL_CONTRACT": CONTINUITY_EVAL_CONTRACT,
    "CONTINUITY_REPAIR_CONTRACT": CONTINUITY_REPAIR_CONTRACT,
    "CAUSAL_VALIDATE_CONTRACT": CAUSAL_VALIDATE_CONTRACT,
    "CAUSAL_REPAIR_CONTRACT": CAUSAL_REPAIR_CONTRACT,
    "EXTRACT_CONTRACT": EXTRACT_CONTRACT,
    "ALIGNMENT_CONTRACT": ALIGNMENT_CONTRACT,
    "CHECK_CHAPTER_CONTRACT": CHECK_CHAPTER_CONTRACT,
    "READING_POWER_EVAL_CONTRACT": READING_POWER_EVAL_CONTRACT,
    "READING_POWER_REPAIR_CONTRACT": READING_POWER_REPAIR_CONTRACT,
    "POLISH_CONTRACT": POLISH_CONTRACT,
    "EVALUATE_CONTRACT": EVALUATE_CONTRACT,
    "VOLUME_CONTRACT": VOLUME_CONTRACT,
    "BOOK_CONSISTENCY_CONTRACT": BOOK_CONSISTENCY_CONTRACT,
    "MACRO_GUARD_CONTRACT": MACRO_GUARD_CONTRACT,
    "STATE_ADJUDICATION_CONTRACT": STATE_ADJUDICATION_CONTRACT,
    "PATCH_CONTRACT": PATCH_CONTRACT,
    "PRONOUN_CHECK_CONTRACT": PRONOUN_CHECK_CONTRACT,
    "FORBIDDEN_SOURCES_CONTRACT": FORBIDDEN_SOURCES_CONTRACT,
    "PROFILE_STYLE_CONTRACT": PROFILE_STYLE_CONTRACT,
    "CHARACTER_INTRO_CONTRACT": CHARACTER_INTRO_CONTRACT,
}


__all__ = [
    "ALL_CONTRACTS",
    "ALIGNMENT_CONTRACT",
    "BOOK_CONSISTENCY_CONTRACT",
    "BRIDGE_CONTRACT",
    "CAUSAL_REPAIR_CONTRACT",
    "CAUSAL_VALIDATE_CONTRACT",
    "CHARACTER_INTRO_CONTRACT",
    "CHECK_CHAPTER_CONTRACT",
    "CONTINUITY_EVAL_CONTRACT",
    "CONTINUITY_REPAIR_CONTRACT",
    "DRAFT_CONTRACT",
    "EDIT_CONTRACT",
    "EVALUATE_CONTRACT",
    "EXTRACT_CONTRACT",
    "FORBIDDEN_SOURCES_CONTRACT",
    "FieldContract",
    "MACRO_GUARD_CONTRACT",
    "PATCH_CONTRACT",
    "PLAN_CONTRACT",
    "POLISH_CONTRACT",
    "PROFILE_STYLE_CONTRACT",
    "PRONOUN_CHECK_CONTRACT",
    "READING_POWER_EVAL_CONTRACT",
    "READING_POWER_REPAIR_CONTRACT",
    "STATE_ADJUDICATION_CONTRACT",
    "VALID_FIELD_NAMES",
    "VOLUME_CONTRACT",
]
