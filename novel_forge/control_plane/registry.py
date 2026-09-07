"""Registry of StageDefinitions for all pipeline stages.

Defines the permission boundaries for each stage in the long-form chapter
pipeline (6 phases) and TTS pipeline. These definitions are advisory in
Phase 5 (logged as warnings) and will be enforced in Phase 6.

Stage DAG (must be acyclic):
  bridge -> plan -> draft -> wave -> review -> repair -> polish -> finalize
                                              -> canon_memory (after finalize)

TTS stages (separate DAG, depends on committed chapters):
  tts_script -> tts_synthesis -> tts_assembly
"""

from __future__ import annotations

from novel_forge.control_plane.stage_definitions import StageDefinition

# ---------------------------------------------------------------------------
# Long-form chapter pipeline stages (6 phases)
# ---------------------------------------------------------------------------

BRIDGE = StageDefinition(
    name="bridge",
    description="Bridge chapter opening from previous state",
    allowed_artifact_types=frozenset({"chapter_source_slice", "previous_final"}),
    allowed_proposal_types=frozenset({"bridge"}),
    can_commit_directly=True,
    editable_text_window="none",  # Bridge is a plan artifact, not text
    commit_resource_kinds=frozenset({"report"}),
    degradation_policy="block",
    max_retries=2,
    reuse_policy="signature_cache",
)

PLAN = StageDefinition(
    name="plan",
    description="Chapter plan from bridge + source slice",
    allowed_artifact_types=frozenset({"chapter_source_slice", "bridge"}),
    allowed_proposal_types=frozenset({"plan"}),
    can_commit_directly=True,
    editable_text_window="none",
    commit_resource_kinds=frozenset({"report"}),
    degradation_policy="block",
    max_retries=2,
    reuse_policy="signature_cache",
)

DRAFT = StageDefinition(
    name="draft",
    description="Per-scene prose draft (20-field scene_intent subset, no voices)",
    allowed_artifact_types=frozenset({"chapter_source_slice", "plan"}),
    allowed_proposal_types=frozenset({"scene_draft"}),
    can_commit_directly=True,
    editable_text_window="full",  # DRAFT generates full text
    commit_resource_kinds=frozenset({"chapter"}),  # Writes v0_draft.md
    degradation_policy="block",
    max_retries=1,
    human_intervention_conditions=frozenset({"context_length_exceeded"}),
)

WAVE = StageDefinition(
    name="wave",
    description="Single-pass scene weaving (full 35-field scene_intent + voices)",
    allowed_artifact_types=frozenset({"chapter_source_slice", "scene_draft"}),
    allowed_proposal_types=frozenset({"wave"}),
    can_commit_directly=True,
    editable_text_window="full",
    commit_resource_kinds=frozenset({"chapter"}),  # Writes v1_wave.md
    degradation_policy="block",
    max_retries=0,  # WAVE runs exactly once (no multi-round edit loop)
)

REVIEW = StageDefinition(
    name="review",
    description="Quality checks + continuity + alignment (read-only on text)",
    allowed_artifact_types=frozenset({"chapter_source_slice", "wave"}),
    allowed_proposal_types=frozenset(
        {"review", "alignment_report", "continuity_report", "causal_report"}
    ),
    can_commit_directly=True,
    editable_text_window="none",  # Review is read-only
    commit_resource_kinds=frozenset({"report"}),
    degradation_policy="advisory",  # Quality checks can be advisory
    max_retries=2,
    reuse_policy="signature_cache",
)

REPAIR = StageDefinition(
    name="repair",
    description="Domain-specific repair (continuity, causal, reading_power)",
    allowed_artifact_types=frozenset({"chapter_source_slice", "wave", "review"}),
    allowed_proposal_types=frozenset({"repair", "patch"}),
    can_commit_directly=False,  # Repair proposals must be validated before commit
    editable_text_window="patch",  # Bounded patches only
    required_validations=frozenset({"source_text_hash_match", "window_bounds"}),
    degradation_policy="block",
    max_retries=5,  # total_rounds_cap=5 across all repair dimensions
    human_intervention_conditions=frozenset(
        {
            "patch_out_of_bounds",
            "source_hash_mismatch",
            "repair_exhausted",
        }
    ),
)

POLISH = StageDefinition(
    name="polish",
    description="Post-repair polish (auto when eval < threshold)",
    allowed_artifact_types=frozenset({"chapter_source_slice", "repair"}),
    allowed_proposal_types=frozenset({"polish"}),
    can_commit_directly=True,
    editable_text_window="full",  # Polish can rewrite
    commit_resource_kinds=frozenset({"chapter"}),
    degradation_policy="advisory",
    max_retries=1,
)

HUMANIZE = StageDefinition(
    name="humanize",
    description="Terminal creative pass that reduces AI flavor after semantic repair",
    allowed_artifact_types=frozenset(
        {"chapter_source_slice", "polish", "repair", "review", "wave"}
    ),
    allowed_proposal_types=frozenset({"humanize"}),
    can_commit_directly=True,
    editable_text_window="full",
    commit_resource_kinds=frozenset({"chapter", "report"}),
    degradation_policy="allow_local",
    max_retries=1,
    reuse_policy="same_run_resume",
    human_intervention_conditions=frozenset(
        {"humanize_unavailable", "semantic_drift", "quality_regression"}
    ),
)

FINALIZE = StageDefinition(
    name="finalize",
    description="Finalize: eval + extract + persist chapter + canon update",
    allowed_artifact_types=frozenset(
        {"chapter_source_slice", "humanize", "polish", "repair", "wave"}
    ),
    allowed_proposal_types=frozenset({"final", "eval_report", "creative_report", "canon_delta"}),
    can_commit_directly=True,
    editable_text_window="none",  # Finalize doesn't modify text, persists it
    required_validations=frozenset(
        {
            "hard_gates_passed",
            "canon_watermark_match",
            "report_hash_consistency",
        }
    ),
    commit_resource_kinds=frozenset({"chapter", "canon", "report"}),
    degradation_policy="block",  # Canon hard gates never skipped
    max_retries=0,
    human_intervention_conditions=frozenset(
        {
            "hard_gate_failed",
            "canon_watermark_mismatch",
        }
    ),
)

CANON_MEMORY = StageDefinition(
    name="canon_memory",
    description="Memory update: episodic, motif, summary (parallel after finalize)",
    allowed_artifact_types=frozenset({"final"}),
    allowed_proposal_types=frozenset({"canon_memory", "motif_update", "summary_update"}),
    can_commit_directly=True,
    editable_text_window="none",
    commit_resource_kinds=frozenset({"report"}),  # Memory artifacts
    degradation_policy="allow_local",  # Memory update can proceed locally
    max_retries=1,
    reuse_policy="signature_cache",
)

# ---------------------------------------------------------------------------
# TTS pipeline stages (separate DAG)
# ---------------------------------------------------------------------------

TTS_SCRIPT = StageDefinition(
    name="tts_script",
    description="Generate dubbing script from committed chapter text",
    allowed_artifact_types=frozenset({"final"}),  # Can only read committed chapters
    allowed_proposal_types=frozenset({"tts_script"}),
    can_commit_directly=True,
    editable_text_window="none",
    commit_resource_kinds=frozenset({"report"}),
    degradation_policy="block",
    max_retries=2,
    reuse_policy="signature_cache",
)

TTS_SYNTHESIS = StageDefinition(
    name="tts_synthesis",
    description="Synthesize audio segments from dubbing script",
    allowed_artifact_types=frozenset({"tts_script"}),
    allowed_proposal_types=frozenset({"tts_audio"}),
    can_commit_directly=True,
    editable_text_window="none",
    commit_resource_kinds=frozenset({"tts_audio"}),  # Audio only, never chapter/canon
    degradation_policy="block",
    max_retries=3,
)

TTS_ASSEMBLY = StageDefinition(
    name="tts_assembly",
    description="Assemble full chapter audio + SRT from segments",
    allowed_artifact_types=frozenset({"tts_audio"}),
    allowed_proposal_types=frozenset({"tts_assembly"}),
    can_commit_directly=True,
    editable_text_window="none",
    commit_resource_kinds=frozenset({"tts_audio"}),
    degradation_policy="block",
    max_retries=1,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

STAGE_DEFINITIONS: dict[str, StageDefinition] = {
    "bridge": BRIDGE,
    "plan": PLAN,
    "draft": DRAFT,
    "wave": WAVE,
    "review": REVIEW,
    "repair": REPAIR,
    "polish": POLISH,
    "humanize": HUMANIZE,
    "finalize": FINALIZE,
    "canon_memory": CANON_MEMORY,
    "tts_script": TTS_SCRIPT,
    "tts_synthesis": TTS_SYNTHESIS,
    "tts_assembly": TTS_ASSEMBLY,
}

# Stage DAG edges (parent -> child). Must be acyclic.
STAGE_DAG_EDGES: dict[str, frozenset[str]] = {
    "bridge": frozenset({"plan"}),
    "plan": frozenset({"draft"}),
    "draft": frozenset({"wave"}),
    "wave": frozenset({"review"}),
    "review": frozenset({"repair", "polish"}),
    "repair": frozenset({"polish"}),
    "polish": frozenset({"humanize"}),
    "humanize": frozenset({"finalize"}),
    "finalize": frozenset({"canon_memory"}),
    "canon_memory": frozenset(),
    # TTS DAG (separate, depends on finalize)
    "tts_script": frozenset({"tts_synthesis"}),
    "tts_synthesis": frozenset({"tts_assembly"}),
    "tts_assembly": frozenset(),
}


def get_stage_definition(stage_name: str) -> StageDefinition | None:
    """Get the StageDefinition for a stage by name."""
    return STAGE_DEFINITIONS.get(stage_name)


def list_stage_names() -> list[str]:
    """Return all registered stage names."""
    return list(STAGE_DEFINITIONS.keys())
