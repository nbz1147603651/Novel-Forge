"""Shared lifecycle for initialization artifact repair policies."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Protocol

from novel_forge.persistence.repair_case_store import repair_content_hash
from novel_forge.pipeline.long.services.init_repair.models import (
    InitArtifact,
    InitRepairAttempt,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairOutcome,
    InitRepairReport,
    InitRepairStage,
)
from novel_forge.pipeline.repair_orchestration.domains.initialization import (
    build_initialization_audit_issues,
    build_initialization_candidate,
    build_initialization_verification,
    normalization_issue,
)

_log = logging.getLogger(__name__)


class InitArtifactRepairPolicy(Protocol):
    """Artifact-specific repair behavior behind a shared lifecycle."""

    artifact: InitArtifact

    def normalize(self, payload: Any, ctx: InitRepairContext) -> Any: ...

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport: ...

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> Any: ...

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> Any | None: ...


class InitRepairOrchestrator:
    """Runs normalize/validate/LLM/local fallback for one init artifact."""

    def __init__(self, policy: InitArtifactRepairPolicy) -> None:
        self._policy = policy

    async def repair(self, payload: Any, ctx: InitRepairContext) -> InitRepairOutcome:
        attempts: list[InitRepairAttempt] = []
        candidates = []
        verifications = []
        original_payload = deepcopy(payload)
        source_version = repair_content_hash(original_payload)

        normalized_baseline = payload
        try:
            normalized_baseline = self._policy.normalize(deepcopy(payload), ctx)
            attempts.append(
                InitRepairAttempt(stage=InitRepairStage.NORMALIZE, status="applied")
            )
            report = self._policy.validate(normalized_baseline, ctx)
        except Exception as exc:
            report = InitRepairReport(
                errors=[str(exc)],
                issues=[
                    InitRepairIssue(
                        kind=InitRepairIssueKind.SCHEMA_SHAPE,
                        message=str(exc),
                        metadata={"error_type": type(exc).__name__},
                    )
                ],
            )
            attempts.append(
                InitRepairAttempt(
                    stage=InitRepairStage.NORMALIZE,
                    status="failed",
                    errors=[str(exc)],
                    metadata={"error_type": type(exc).__name__},
                )
            )
        attempts.append(
            InitRepairAttempt(
                stage=InitRepairStage.VALIDATE,
                status="valid" if report.is_valid else "failed",
                errors=report.errors,
                warnings=report.warnings,
            )
        )
        if report.is_valid:
            if repair_content_hash(original_payload) != repair_content_hash(normalized_baseline):
                audit_issues = [
                    normalization_issue(
                        artifact=self._policy.artifact.value,
                        baseline=original_payload,
                        candidate_payload=normalized_baseline,
                        source_version=source_version,
                    )
                ]
                candidate = build_initialization_candidate(
                    artifact=self._policy.artifact.value,
                    baseline=original_payload,
                    candidate_payload=normalized_baseline,
                    issues=audit_issues,
                    version=1,
                    origin="deterministic",
                    stage=InitRepairStage.NORMALIZE.value,
                )
                if candidate is not None:
                    candidates.append(candidate)
                    verifications.append(
                        build_initialization_verification(
                            candidate=candidate,
                            issues=audit_issues,
                            passed=True,
                            validator_id=f"init_policy:{self._policy.artifact.value}",
                            warnings=report.warnings,
                        )
                    )
                return InitRepairOutcome(
                    payload=normalized_baseline,
                    report=report,
                    attempts=attempts,
                    audit_issues=audit_issues,
                    candidates=candidates,
                    verifications=verifications,
                )
            return InitRepairOutcome(payload=normalized_baseline, report=report, attempts=attempts)

        audit_issues = build_initialization_audit_issues(
            artifact=self._policy.artifact.value,
            payload=normalized_baseline,
            issues=report.issues,
            source_version=source_version,
        )
        original_report = report

        try:
            llm_payload = await self._policy.llm_repair(
                deepcopy(normalized_baseline), original_report, ctx
            )
            llm_payload = self._policy.normalize(llm_payload, ctx)
            llm_report = self._policy.validate(llm_payload, ctx)
            attempts.append(
                InitRepairAttempt(
                    stage=InitRepairStage.LLM_REPAIR,
                    status="applied" if llm_report.is_valid else "residual_errors",
                    errors=llm_report.errors,
                    warnings=llm_report.warnings,
                )
            )
            candidate = build_initialization_candidate(
                artifact=self._policy.artifact.value,
                baseline=original_payload,
                candidate_payload=llm_payload,
                issues=audit_issues,
                version=len(candidates) + 1,
                origin="model",
                stage=InitRepairStage.LLM_REPAIR.value,
            )
            if candidate is not None:
                candidates.append(candidate)
                verifications.append(
                    build_initialization_verification(
                        candidate=candidate,
                        issues=audit_issues,
                        passed=llm_report.is_valid,
                        validator_id=f"init_policy:{self._policy.artifact.value}",
                        errors=llm_report.errors,
                        warnings=llm_report.warnings,
                    )
                )
            if llm_report.is_valid:
                return InitRepairOutcome(
                    payload=llm_payload,
                    report=llm_report,
                    attempts=attempts,
                    audit_issues=audit_issues,
                    candidates=candidates,
                    verifications=verifications,
                )
        except Exception as exc:
            attempts.append(
                InitRepairAttempt(
                    stage=InitRepairStage.LLM_REPAIR,
                    status="failed",
                    errors=[str(exc)],
                    metadata={"error_type": type(exc).__name__},
                )
            )
            _log.warning(
                "init_repair_llm_failed | artifact=%s | error_type=%s | error=%s",
                self._policy.artifact.value,
                type(exc).__name__,
                exc,
            )

        # Every attempt starts from the same immutable baseline.  A failed LLM
        # candidate must never become the input to deterministic fallback.
        local_payload = self._policy.local_fallback(
            deepcopy(normalized_baseline), original_report, ctx
        )
        if local_payload is not None:
            local_payload = self._policy.normalize(local_payload, ctx)
            local_report = self._policy.validate(local_payload, ctx)
            attempts.append(
                InitRepairAttempt(
                    stage=InitRepairStage.LOCAL_FALLBACK,
                    status="applied" if local_report.is_valid else "residual_errors",
                    errors=local_report.errors,
                    warnings=local_report.warnings,
                )
            )
            candidate = build_initialization_candidate(
                artifact=self._policy.artifact.value,
                baseline=original_payload,
                candidate_payload=local_payload,
                issues=audit_issues,
                version=len(candidates) + 1,
                origin="deterministic",
                stage=InitRepairStage.LOCAL_FALLBACK.value,
            )
            if candidate is not None:
                candidates.append(candidate)
                verifications.append(
                    build_initialization_verification(
                        candidate=candidate,
                        issues=audit_issues,
                        passed=local_report.is_valid,
                        validator_id=f"init_policy:{self._policy.artifact.value}",
                        errors=local_report.errors,
                        warnings=local_report.warnings,
                    )
                )
            if local_report.is_valid:
                return InitRepairOutcome(
                    payload=local_payload,
                    report=local_report,
                    attempts=attempts,
                    audit_issues=audit_issues,
                    candidates=candidates,
                    verifications=verifications,
                )
        else:
            attempts.append(
                InitRepairAttempt(
                    stage=InitRepairStage.LOCAL_FALLBACK,
                    status="skipped",
                    errors=report.errors,
                    warnings=report.warnings,
                )
            )

        attempts.append(
            InitRepairAttempt(
                stage=InitRepairStage.BLOCK,
                status="failed",
                errors=report.errors,
                warnings=report.warnings,
            )
        )
        # No unverified candidate escapes as the effective artifact.
        return InitRepairOutcome(
            payload=original_payload,
            report=original_report,
            attempts=attempts,
            audit_issues=audit_issues,
            candidates=candidates,
            verifications=verifications,
        )
