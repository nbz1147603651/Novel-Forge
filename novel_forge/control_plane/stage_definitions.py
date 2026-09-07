"""Declarative StageDefinition - permission boundaries per pipeline stage.

Each stage (bridge, plan, draft, wave, review, repair, polish, finalize,
canon_memory, tts_script, tts_synthesis) has a frozen StageDefinition
declaring:
- allowed_artifact_types: what input artifacts the stage may read
- allowed_proposal_types: what output proposals the stage may generate
- can_commit_directly: whether the stage can commit to project files
- editable_text_window: what text the stage may modify (full / patch / none)
- required_validations: validations that must pass before commit
- degradation_policy: what happens in degraded mode
- max_retries: retry budget for this stage
- human_intervention_conditions: when to escalate to human decision

Key rules (from the architecture plan):
- Reviewer: can only commit report proposals
- Repair: only commits patch proposals bound to source text hash + window
- Canon Writer: only commits in Finalize when all hard gates pass + watermark matches
- TTS: cannot affect Canon; voice library and chapter audio have separate write permissions
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageDefinition:
    """Declarative permission boundary for a pipeline stage.

    Frozen dataclass - use ``dataclasses.replace()`` to modify, never
    direct assignment (follows the codebase convention for frozen types).
    """

    name: str
    description: str = ""

    # What input artifacts this stage may read
    allowed_artifact_types: frozenset[str] = field(default_factory=frozenset)

    # What output proposals this stage may generate
    allowed_proposal_types: frozenset[str] = field(default_factory=frozenset)

    # Whether this stage can commit directly to project files
    can_commit_directly: bool = False

    # What text window the stage may modify
    # "none" = read-only, "patch" = bounded patch, "full" = full rewrite
    editable_text_window: str = "none"

    # Validations that must pass before commit
    required_validations: frozenset[str] = field(default_factory=frozenset)

    # Degradation policy: "block" (skip entirely), "advisory" (log warning),
    # "allow_local" (only deterministic local steps proceed)
    degradation_policy: str = "block"

    # Maximum retries for this stage
    max_retries: int = 2

    # Conditions that trigger human intervention
    human_intervention_conditions: frozenset[str] = field(default_factory=frozenset)

    # Resource kinds this stage may commit (chapter, canon, report, tts_audio, voice_lib)
    commit_resource_kinds: frozenset[str] = field(default_factory=frozenset)

    # Whether this stage is allowed to read full project_spec (most are NOT)
    can_read_full_project_spec: bool = False

    # Versioned execution contract written into every stage artifact.
    workflow_version: str = "novel.chapter.v2"
    artifact_schema_version: int = 2

    # ``signature_cache`` is allowed only for read/analysis stages.
    # Creative stages use ``same_run_resume`` so a new run never silently
    # reuses prose and suppresses creative variation.
    reuse_policy: str = "same_run_resume"
    requires_input_signature: bool = True

    def __post_init__(self) -> None:
        if self.editable_text_window not in {"none", "patch", "full"}:
            raise ValueError(f"Invalid editable_text_window: {self.editable_text_window}")
        if self.degradation_policy not in {"block", "advisory", "allow_local"}:
            raise ValueError(f"Invalid degradation_policy: {self.degradation_policy}")
        if self.reuse_policy not in {"signature_cache", "same_run_resume", "never"}:
            raise ValueError(f"Invalid reuse_policy: {self.reuse_policy}")
        if self.reuse_policy == "signature_cache" and self.editable_text_window != "none":
            raise ValueError("signature_cache is only valid for read-only stages")

    def can_read_artifact(self, artifact_type: str) -> bool:
        """Check if this stage is allowed to read the given artifact type."""
        return artifact_type in self.allowed_artifact_types

    def can_generate_proposal(self, proposal_type: str) -> bool:
        """Check if this stage is allowed to generate the given proposal type."""
        return proposal_type in self.allowed_proposal_types

    def can_commit_resource(self, resource_kind: str) -> bool:
        """Check if this stage is allowed to commit the given resource kind."""
        return self.can_commit_directly and resource_kind in self.commit_resource_kinds
