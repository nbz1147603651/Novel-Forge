"""Repair Orchestration v2 public API."""

from __future__ import annotations

from novel_forge.pipeline.repair_orchestration.capability_audit import (
    RepairCapability,
    audit_repair_partitions,
    repair_domain_capabilities,
)
from novel_forge.pipeline.repair_orchestration.domains.book_consistency import (
    BookConsistencyRepairHandler,
)
from novel_forge.pipeline.repair_orchestration.domains.causal import (
    CausalRepairHandler,
    run_causal_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.continuity import (
    ContinuityRepairHandler,
    run_continuity_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.format_response import (
    FormatResponseRepairHandler,
    run_format_response_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.guardrail import GuardrailRepairHandler
from novel_forge.pipeline.repair_orchestration.domains.init_artifact import (
    InitArtifactRepairHandler,
)
from novel_forge.pipeline.repair_orchestration.domains.knowledge_boundary import (
    KnowledgeBoundaryRepairHandler,
    run_knowledge_boundary_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.prompt_leak import (
    PromptLeakRepairHandler,
    run_prompt_leak_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.reading_power import (
    ReadingPowerRepairHandler,
    run_reading_power_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.runtime_contract import (
    RuntimeContractRepairHandler,
    run_runtime_contract_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.short_story import (
    ShortStoryRepairHandler,
    run_short_story_repair_v2,
)
from novel_forge.pipeline.repair_orchestration.domains.state_adjudication import (
    StateAdjudicationRepairHandler,
)
from novel_forge.pipeline.repair_orchestration.handlers import RepairHandler
from novel_forge.pipeline.repair_orchestration.ledger import (
    append_repair_ledger_record,
    repair_ledger_record,
)
from novel_forge.pipeline.repair_orchestration.mission_factory import (
    book_consistency_repair_mission,
    causal_repair_mission,
    continuity_repair_mission,
    guardrail_repair_mission,
    init_artifact_repair_mission,
    issues_repair_mission,
    resolve_control_mode,
    state_adjudication_repair_mission,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairArtifactChange,
    RepairAttempt,
    RepairAttemptStatus,
    RepairControlMode,
    RepairDomain,
    RepairExecutionResult,
    RepairMission,
    RepairOutcome,
    RepairPlanCandidate,
    RepairSnapshot,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
    RepairVerificationResult,
)
from novel_forge.pipeline.repair_orchestration.orchestrator import RepairOrchestrator
from novel_forge.pipeline.repair_orchestration.plugins import (
    LegacyRepairHandlerAdapter,
    RepairAuthorityContext,
    RepairCandidateDraft,
    RepairCandidateMaterial,
    RepairPlugin,
    RepairPluginRegistry,
    RepairPublicationError,
    RepairPublisher,
    RepairPublisherRegistry,
    UnsafeRepairPluginError,
)
from novel_forge.pipeline.repair_orchestration.policy import (
    RepairPolicyDecision,
    RepairPolicyEngine,
    RepairRiskLevel,
)
from novel_forge.pipeline.repair_orchestration.registry import RepairHandlerRegistry
from novel_forge.pipeline.repair_orchestration.snapshot import RepairSnapshotStore

__all__ = [
    "RepairArtifactChange",
    "RepairAuthorityContext",
    "RepairAttempt",
    "RepairAttemptStatus",
    "RepairCapability",
    "RepairControlMode",
    "RepairCandidateDraft",
    "RepairCandidateMaterial",
    "RepairDomain",
    "RepairExecutionResult",
    "FormatResponseRepairHandler",
    "BookConsistencyRepairHandler",
    "GuardrailRepairHandler",
    "InitArtifactRepairHandler",
    "RepairHandler",
    "RepairHandlerRegistry",
    "RepairPlugin",
    "RepairPluginRegistry",
    "RepairPublicationError",
    "RepairPublisher",
    "RepairPublisherRegistry",
    "UnsafeRepairPluginError",
    "LegacyRepairHandlerAdapter",
    "append_repair_ledger_record",
    "book_consistency_repair_mission",
    "CausalRepairHandler",
    "causal_repair_mission",
    "ContinuityRepairHandler",
    "continuity_repair_mission",
    "guardrail_repair_mission",
    "init_artifact_repair_mission",
    "issues_repair_mission",
    "KnowledgeBoundaryRepairHandler",
    "PromptLeakRepairHandler",
    "RepairMission",
    "RepairOrchestrator",
    "RepairOutcome",
    "RepairPlanCandidate",
    "RepairPolicyDecision",
    "RepairPolicyEngine",
    "RepairRiskLevel",
    "ReadingPowerRepairHandler",
    "RepairSnapshot",
    "RepairSnapshotStore",
    "RepairStrategy",
    "RepairSurface",
    "RepairTarget",
    "RepairVerificationResult",
    "audit_repair_partitions",
    "repair_ledger_record",
    "repair_domain_capabilities",
    "resolve_control_mode",
    "RuntimeContractRepairHandler",
    "ShortStoryRepairHandler",
    "StateAdjudicationRepairHandler",
    "state_adjudication_repair_mission",
    "run_causal_repair_v2",
    "run_continuity_repair_v2",
    "run_format_response_repair_v2",
    "run_knowledge_boundary_repair_v2",
    "run_prompt_leak_repair_v2",
    "run_reading_power_repair_v2",
    "run_runtime_contract_repair_v2",
    "run_short_story_repair_v2",
]
