"""Runtime repair for chapter contract source artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from novel_forge.pipeline.long.contract_artifact_checkpoint import ContractArtifactCheckpoint
from novel_forge.pipeline.long.services.context.source_artifacts import (
    _build_source_artifact,
    _chapter_contract_index_payload,
    _load_source_artifact,
    build_and_persist_chapter_source_slice,
    normalize_chapter_contract_entity_references,
    source_artifact_hashes,
)
from novel_forge.pipeline.long.services.generation.llm_service import LLMService
from novel_forge.pipeline.long.services.init.init_v2 import hash_payload
from novel_forge.pipeline.long.services.init_repair import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairOrchestrator,
    InitRepairReport,
    get_init_repair_policy,
)
from novel_forge.pipeline.long.services.plot_milestones import build_plot_milestone_index

ContractExecutionBlockerKind = Literal["contract_source_error", "prose_source_error"]


@dataclass(frozen=True)
class RuntimeContractRepairOutcome:
    applied: bool
    reason: str = ""
    chapter_contracts: dict[str, Any] | None = None
    chapter_source_slice: Any | None = None


def classify_contract_execution_blocker(
    report_payload: dict[str, Any],
    *,
    ticket: Any | None = None,
) -> ContractExecutionBlockerKind:
    """Classify whether a contract-audit blocker should repair source JSON or prose."""

    source = str(
        report_payload.get("blocker_source")
        or report_payload.get("repair_source")
        or report_payload.get("source_error")
        or ""
    ).strip().lower()
    if source in {
        "contract",
        "contract_source",
        "contract_source_error",
        "source_contract",
        "artifact",
        "source_artifact",
    }:
        return "contract_source_error"
    if bool(report_payload.get("contract_source_error")):
        return "contract_source_error"
    hard_hits = bool(
        report_payload.get("future_leak_hits")
        or report_payload.get("forbidden_progression_hits")
        or report_payload.get("cognitive_constraint_hits")
    )
    decision = str(report_payload.get("repair_or_replan_decision") or "").strip().lower()
    missing = bool(
        report_payload.get("missing_required_progressions")
        or report_payload.get("missing_knowledge_ops")
    )
    if decision == "replan" and missing and not hard_hits:
        return "contract_source_error"
    if ticket is not None and str(getattr(ticket, "issue_type", "") or "") in {
        "contract_source_error",
        "source_artifact",
    }:
        return "contract_source_error"
    return "prose_source_error"


class RuntimeContractRepairService:
    """Repair persisted chapter-contract JSON and synchronized source artifacts."""

    def __init__(self, context: Any, *, review: Any, trace: Any) -> None:
        self._context = context
        self._review = review
        self._trace = trace

    async def repair(self, *, ticket: Any) -> RuntimeContractRepairOutcome:
        bundle = self._review.prepared.bundle
        chapter_number = int(bundle.chapter_outline.chapter_number)
        checkpoint = ContractArtifactCheckpoint(
            self._context.storage,
            bundle.layout,
            chapter_number,
        )
        checkpoint.save()
        try:
            chapter_contracts = self._context.storage.load_json(
                bundle.layout.plans_dir / "chapter_contracts.json"
            )
            issue_text = str(getattr(ticket, "target_summary", "") or getattr(ticket, "issue_type", ""))
            report = InitRepairReport(
                errors=[issue_text or "contract execution audit found source contract drift"],
                issues=[
                    InitRepairIssue(
                        kind=InitRepairIssueKind.CROSS_ARTIFACT_DRIFT,
                        message=issue_text or "contract execution audit found source contract drift",
                        field="chapter_contracts",
                        metadata={"chapters": [chapter_number]},
                    )
                ],
            )
            service_ctx = LLMService(
                self._context.router,
                self._context.builder,
                self._context.on_step,
                settings=self._context.settings,
            )
            repair_ctx = InitRepairContext(
                service_ctx=service_ctx,
                outline_ctx={},
                total_chapters=int(getattr(bundle.outline, "total_chapters", 0) or 0),
                artifacts={
                    "outline": bundle.outline,
                    "narrative_contract": getattr(bundle, "narrative_contract", None) or {},
                    "project_id": getattr(bundle, "project_id", ""),
                },
            )
            policy = get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)
            focused_payload = await policy.llm_repair(chapter_contracts, report, repair_ctx)
            outcome = await InitRepairOrchestrator(policy).repair(focused_payload, repair_ctx)
            if not outcome.report.is_valid:
                checkpoint.rollback()
                checkpoint.cleanup()
                return RuntimeContractRepairOutcome(
                    applied=False,
                    reason="contract_repair_invalid",
                    chapter_contracts=chapter_contracts,
                )
            repaired_contracts = outcome.payload if isinstance(outcome.payload, dict) else {}
            source_slice = persist_repaired_chapter_contract_artifacts(
                storage=self._context.storage,
                layout=bundle.layout,
                project_id=str(getattr(bundle, "project_id", "") or ""),
                outline=bundle.outline,
                narrative_contract=getattr(bundle, "narrative_contract", None) or {},
                chapter_contracts=repaired_contracts,
                chapter_number=chapter_number,
            )
            checkpoint.cleanup()
            return RuntimeContractRepairOutcome(
                applied=True,
                chapter_contracts=repaired_contracts,
                chapter_source_slice=source_slice,
            )
        except Exception as exc:  # noqa: BLE001
            checkpoint.rollback()
            checkpoint.cleanup()
            return RuntimeContractRepairOutcome(applied=False, reason=f"{type(exc).__name__}: {exc}")


def persist_repaired_chapter_contract_artifacts(
    *,
    storage: Any,
    layout: Any,
    project_id: str,
    outline: Any,
    narrative_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    chapter_number: int,
) -> Any:
    """Persist repaired chapter contracts and rebuild dependent projections."""

    storage.save_json(layout.plans_dir / "chapter_contracts.json", chapter_contracts)
    milestone_index = build_plot_milestone_index(
        outline=outline,
        chapter_contracts=chapter_contracts,
        narrative_contract=narrative_contract,
        project_id=project_id,
    )
    storage.save_json(layout.plot_milestone_index_path, milestone_index.model_dump(mode="json"))
    existing_index = _load_source_artifact(storage, layout, "chapter_contract_index")
    canonical_refs = list(existing_index.canonical_entity_refs)
    payload = normalize_chapter_contract_entity_references(
        _chapter_contract_index_payload(
            chapter_contracts,
            outline=outline,
            canonical_refs=canonical_refs,
        ),
        entity_catalog={},
    )
    artifact = _build_source_artifact(
        artifact_type="chapter_contract_index",
        project_id=project_id or existing_index.project_id,
        payload=payload,
        canonical_refs=canonical_refs,
        blocking_issues=[],
    )
    storage.save_json(layout.source_artifact_path("chapter_contract_index"), artifact.model_dump(mode="json"))
    if storage.exists(layout.init_readiness_artifact_path):
        readiness = storage.load_json(layout.init_readiness_artifact_path)
        readiness["source_hashes"] = source_artifact_hashes(storage, layout)
        readiness.setdefault("payload", {})
        readiness["payload"]["contract_runtime_repair_hash"] = hash_payload(chapter_contracts)
        storage.save_json(layout.init_readiness_artifact_path, readiness)
    return build_and_persist_chapter_source_slice(
        storage=storage,
        layout=layout,
        project_id=project_id or existing_index.project_id,
        chapter_number=chapter_number,
    )


__all__ = [
    "ContractExecutionBlockerKind",
    "RuntimeContractRepairOutcome",
    "RuntimeContractRepairService",
    "classify_contract_execution_blocker",
    "persist_repaired_chapter_contract_artifacts",
]
