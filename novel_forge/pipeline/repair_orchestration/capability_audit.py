"""Static capability audit for review-to-repair partitioning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from novel_forge.pipeline.repair_orchestration.models import RepairDomain, RepairSurface

RepairPartition = Literal[
    "point_issue",
    "artifact_field",
    "ticket",
    "queue",
    "domain_batch",
    "protocol_response",
]
RepairCoverage = Literal["complete", "partial", "coarse", "protocol_only"]
RepairChain = Literal[
    "protocol",
    "initialization",
    "chapter_generation",
    "book_level",
    "short_story",
]
RepairIdentityMode = Literal[
    "issue_id",
    "ticket_id",
    "artifact_path",
    "queue_target",
    "state_target",
    "protocol_task",
]
RepairLocationMode = Literal[
    "paragraph_window",
    "artifact_json_path",
    "book_queue",
    "state_target",
    "protocol_response",
    "whole_text",
    "domain_batch",
]
RepairLogSupport = Literal["full", "partial", "coarse"]
RepairVerificationSupport = Literal["full", "partial", "coarse", "none"]


@dataclass(frozen=True)
class RepairCapability:
    """One domain's ability to follow review -> locate -> point repair -> verify."""

    domain: RepairDomain
    chain: RepairChain
    surfaces: tuple[RepairSurface, ...]
    review_source: str
    repair_entry: str
    partition: RepairPartition
    coverage: RepairCoverage
    identity_mode: RepairIdentityMode
    location_mode: RepairLocationMode
    log_support: RepairLogSupport
    verification_support: RepairVerificationSupport
    ai_repair: bool
    human_recovery: str
    supported_modes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    @property
    def point_repair_ready(self) -> bool:
        return self.coverage in {"complete", "partial"} and self.partition in {
            "point_issue",
            "artifact_field",
            "ticket",
        }

    @property
    def needs_partition_work(self) -> bool:
        return bool(self.gaps) or self.coverage in {"partial", "coarse"}


_CAPABILITIES: tuple[RepairCapability, ...] = (
    RepairCapability(
        domain=RepairDomain.FORMAT_RESPONSE,
        chain="protocol",
        surfaces=(RepairSurface.RESPONSE_JSON,),
        review_source="TaskFormatContract / response validator",
        repair_entry="FormatResponseRepairHandler",
        partition="protocol_response",
        coverage="protocol_only",
        identity_mode="protocol_task",
        location_mode="protocol_response",
        log_support="full",
        verification_support="full",
        ai_repair=True,
        human_recovery="format error log; no artifact editor",
        supported_modes=("local repair", "repair LLM", "retry original task"),
        notes=("bounded to one task response and validator failure",),
        gaps=("manual JSON repair UX is log-based rather than editor-based",),
    ),
    RepairCapability(
        domain=RepairDomain.INIT_ARTIFACT,
        chain="initialization",
        surfaces=(
            RepairSurface.BLUEPRINT,
            RepairSurface.OUTLINE,
            RepairSurface.CHAPTER_CONTRACTS,
            RepairSurface.SCENE_PLAN,
        ),
        review_source="init coherence/readiness adjudicators",
        repair_entry="init readiness repair + InitArtifactRepairHandler",
        partition="artifact_field",
        coverage="partial",
        identity_mode="artifact_path",
        location_mode="artifact_json_path",
        log_support="partial",
        verification_support="partial",
        ai_repair=True,
        human_recovery="init manual artifact editor for blueprint/outline/chapter_contracts",
        supported_modes=(
            "init auto repair",
            "focused issue repair",
            "manual artifact edit",
            "rerun readiness",
        ),
        notes=("init_readiness repair now splits multiple blocking issues into focused calls",),
        gaps=("scene_plan has handler surface but no desktop manual editor yet",),
    ),
    RepairCapability(
        domain=RepairDomain.CONTINUITY,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_WINDOW),
        review_source="CHECK_CONTINUITY / ReviewFinding / RepairTicket",
        repair_entry="WorkspaceContinuityRepairHandler / ContinuityRepairHandler",
        partition="point_issue",
        coverage="partial",
        identity_mode="issue_id",
        location_mode="paragraph_window",
        log_support="full",
        verification_support="full",
        ai_repair=True,
        human_recovery="chapter issue selection and repair tickets",
        supported_modes=("active review loop", "manual selected issue", "AI assisted", "AI auto"),
        notes=(
            "external RepairIssuesRequest is split into one target per selected issue",
            "active long-chapter loop caps each repair call to a focused must-fix issue by default",
        ),
        gaps=("v2 wrapper mission target still represents the continuity dimension as a whole",),
    ),
    RepairCapability(
        domain=RepairDomain.CAUSAL,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_WINDOW),
        review_source="VALIDATE_CAUSAL / ReviewFinding / RepairTicket",
        repair_entry="WorkspaceCausalRepairHandler / CausalRepairHandler",
        partition="point_issue",
        coverage="partial",
        identity_mode="issue_id",
        location_mode="paragraph_window",
        log_support="full",
        verification_support="full",
        ai_repair=True,
        human_recovery="chapter issue selection and repair tickets",
        supported_modes=("active review loop", "manual selected issue", "AI assisted", "AI auto"),
        notes=(
            "external RepairIssuesRequest is split into one target per selected issue",
            "active long-chapter loop caps each repair call to a focused must-fix issue by default",
        ),
        gaps=("v2 wrapper mission target still represents the causal dimension as a whole",),
    ),
    RepairCapability(
        domain=RepairDomain.READING_POWER,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_WINDOW),
        review_source="EVALUATE_READING_POWER / ReviewFinding / RepairTicket",
        repair_entry="ReadingPowerRepairHandler",
        partition="point_issue",
        coverage="partial",
        identity_mode="issue_id",
        location_mode="paragraph_window",
        log_support="full",
        verification_support="full",
        ai_repair=True,
        human_recovery="repair_tickets are persisted; remaining issues are flagged for review",
        supported_modes=("active review loop", "repair tickets", "AI assisted", "AI auto"),
        notes=(
            "legacy loop can ingest repair tickets",
            "active loop caps each repair call to a focused must-fix issue by default",
        ),
        gaps=("v2 wrapper mission target still represents the reading-power dimension as a whole",),
    ),
    RepairCapability(
        domain=RepairDomain.KNOWLEDGE_BOUNDARY,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_WINDOW),
        review_source="knowledge boundary audit findings",
        repair_entry="KnowledgeBoundaryRepairHandler",
        partition="domain_batch",
        coverage="coarse",
        identity_mode="issue_id",
        location_mode="domain_batch",
        log_support="coarse",
        verification_support="partial",
        ai_repair=True,
        human_recovery="findings/tickets in reports; no dedicated manual lane",
        supported_modes=("finalize audit", "AI assisted", "AI auto"),
        notes=("legacy loop receives a findings list",),
        gaps=("v2 wrapper does not split findings into per-finding targets",),
    ),
    RepairCapability(
        domain=RepairDomain.PROMPT_LEAK,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT,),
        review_source="chapter repair prompt-leak report",
        repair_entry="PromptLeakRepairHandler",
        partition="domain_batch",
        coverage="coarse",
        identity_mode="issue_id",
        location_mode="domain_batch",
        log_support="coarse",
        verification_support="partial",
        ai_repair=True,
        human_recovery="remaining leaks force human review",
        supported_modes=("review stage", "deterministic fallback", "AI assisted", "AI auto"),
        notes=("localized patch repair is bounded to confirmed prompt leaks",),
        gaps=("multiple leaks are repaired as one report batch",),
    ),
    RepairCapability(
        domain=RepairDomain.GUARDRAIL,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_WINDOW),
        review_source="guard compliance findings/tickets",
        repair_entry="GuardrailRepairHandler",
        partition="ticket",
        coverage="complete",
        identity_mode="ticket_id",
        location_mode="paragraph_window",
        log_support="full",
        verification_support="full",
        ai_repair=True,
        human_recovery="guard tickets can be selected and anchored",
        supported_modes=("ticket loop", "manual selected ticket", "AI assisted", "AI auto"),
        notes=("chapter_session_handlers processes guard tickets one by one",),
    ),
    RepairCapability(
        domain=RepairDomain.RUNTIME_CONTRACT,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_CONTRACTS,),
        review_source="contract execution audit ticket",
        repair_entry="RuntimeContractRepairHandler",
        partition="ticket",
        coverage="complete",
        identity_mode="ticket_id",
        location_mode="artifact_json_path",
        log_support="full",
        verification_support="full",
        ai_repair=True,
        human_recovery="repair ticket remains when verification fails",
        supported_modes=("contract audit ticket", "AI assisted", "AI auto"),
        notes=("handler snapshots contract artifacts and repairs one audit ticket",),
    ),
    RepairCapability(
        domain=RepairDomain.BOOK_CONSISTENCY,
        chain="book_level",
        surfaces=(RepairSurface.BOOK_CHAPTER_SET, RepairSurface.CHAPTER_TEXT),
        review_source="book audit findings/tickets",
        repair_entry="BookConsistencyRepairHandler",
        partition="queue",
        coverage="partial",
        identity_mode="ticket_id",
        location_mode="book_queue",
        log_support="partial",
        verification_support="partial",
        ai_repair=True,
        human_recovery="book repair queue and unmatched tickets",
        supported_modes=("global audit queue", "chapter mission handoff", "AI assisted", "AI auto"),
        notes=("book queue delegates chapter missions through repair v2",),
        gaps=("queue-level target can still aggregate multiple chapter missions",),
    ),
    RepairCapability(
        domain=RepairDomain.SHORT_STORY,
        chain="short_story",
        surfaces=(RepairSurface.SHORT_TEXT,),
        review_source="short completion/quality gate",
        repair_entry="ShortStoryRepairHandler",
        partition="domain_batch",
        coverage="coarse",
        identity_mode="issue_id",
        location_mode="whole_text",
        log_support="coarse",
        verification_support="partial",
        ai_repair=True,
        human_recovery="quality/completion failures remain gate-level",
        supported_modes=("completion repair", "quality repair"),
        notes=("short story repair is one pass over a list of issues",),
        gaps=("short repair is not compiled into ReviewFinding/RepairTicket per issue",),
    ),
    RepairCapability(
        domain=RepairDomain.STATE_ADJUDICATION,
        chain="chapter_generation",
        surfaces=(RepairSurface.CHAPTER_TEXT, RepairSurface.CHAPTER_CONTRACTS),
        review_source="narrative_state adjudication issues",
        repair_entry="StateAdjudicationRepairHandler",
        partition="ticket",
        coverage="partial",
        identity_mode="state_target",
        location_mode="state_target",
        log_support="partial",
        verification_support="partial",
        ai_repair=True,
        human_recovery="typed context repair target",
        supported_modes=("state adjudication repair", "AI assisted", "AI auto"),
        notes=("handler can execute one supplied state-adjudication target",),
        gaps=("not every adjudication producer emits a user-visible recovery action",),
    ),
)


def repair_domain_capabilities() -> dict[RepairDomain, RepairCapability]:
    """Return repair capability metadata keyed by v2 domain."""

    return {capability.domain: capability for capability in _CAPABILITIES}


def audit_repair_partitions() -> dict[str, object]:
    """Summarize which domains can follow point repair and where gaps remain."""

    by_domain = repair_domain_capabilities()
    all_domains = set(RepairDomain)
    missing = sorted(domain.value for domain in all_domains - set(by_domain))
    missing_audit_contracts = sorted(
        capability.domain.value
        for capability in by_domain.values()
        if not (
            capability.identity_mode
            and capability.location_mode
            and capability.log_support
            and capability.verification_support
        )
    )
    point_ready = sorted(
        capability.domain.value
        for capability in by_domain.values()
        if capability.point_repair_ready
    )
    needs_work = sorted(
        capability.domain.value
        for capability in by_domain.values()
        if capability.needs_partition_work
    )
    coarse = sorted(
        capability.domain.value
        for capability in by_domain.values()
        if capability.coverage == "coarse"
    )
    gaps = {
        capability.domain.value: list(capability.gaps)
        for capability in by_domain.values()
        if capability.gaps
    }
    audit_contracts = {
        capability.domain.value: {
            "identity_mode": capability.identity_mode,
            "location_mode": capability.location_mode,
            "log_support": capability.log_support,
            "verification_support": capability.verification_support,
        }
        for capability in by_domain.values()
    }
    by_chain: dict[str, list[str]] = {}
    needs_work_by_chain: dict[str, list[str]] = {}
    for capability in by_domain.values():
        by_chain.setdefault(capability.chain, []).append(capability.domain.value)
        if capability.needs_partition_work:
            needs_work_by_chain.setdefault(capability.chain, []).append(capability.domain.value)
    return {
        "all_domains_classified": not missing,
        "all_domains_have_audit_contract": not missing_audit_contracts,
        "missing_domains": missing,
        "missing_audit_contract_domains": missing_audit_contracts,
        "point_repair_ready_domains": point_ready,
        "needs_partition_work_domains": needs_work,
        "coarse_domains": coarse,
        "domains_by_chain": {key: sorted(value) for key, value in by_chain.items()},
        "needs_partition_work_by_chain": {
            key: sorted(value) for key, value in needs_work_by_chain.items()
        },
        "audit_contracts": audit_contracts,
        "gaps": gaps,
    }
