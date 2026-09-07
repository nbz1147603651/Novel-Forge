"""Project model semantic decisions into the existing RepairCase ledger.

This module owns no scheduler or publisher.  It records immutable source and
candidate evidence in ``RepairCaseStore``; configured projects still publish
through the existing authoring proposal and ``PlanningRevision`` boundary.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from novel_forge.core.schemas.audit import (
    AuditEvidence,
    AuditIssueV2,
    AuditLocator,
    AuditPostcondition,
    AuditRepairIntent,
    ResolvedRepairTarget,
)
from novel_forge.core.schemas.init_coherence import CoherenceClaim, ConflictCandidate
from novel_forge.core.schemas.repair import RepairCase
from novel_forge.core.utils.repair_target_resolver import resolve_json_pointer
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.pipeline.repair_orchestration.domains.initialization import (
    build_initialization_candidate,
    build_initialization_verification,
)

SEMANTIC_REPAIR_CONTENT_TYPE = "semantic_consistency"


def reconcile_semantic_repair_cases(
    ctx: Any,
    *,
    stage: str,
    repair_artifact: str,
    artifacts: dict[str, dict[str, Any]],
    claims: list[CoherenceClaim],
    candidates: list[ConflictCandidate],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Create/reuse source-bound semantic cases and apply exact author decisions."""

    if bool(getattr(ctx, "_semantic_candidate_verification", False)) or bool(
        getattr(ctx, "_semantic_repair_cases_disabled", False)
    ):
        return report
    root = ctx.layout.root
    store = RepairCaseStore(root)
    claim_by_id = {claim.claim_id: claim for claim in claims}
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    source_hash = repair_content_hash(artifacts)
    compiler_fingerprint = str(report.get("compiler_fingerprint") or "")
    artifact_hashes = {
        artifact: repair_content_hash(payload) for artifact, payload in artifacts.items()
    }
    policy = AuthoringStore(root).policy()
    input_version = story_input_version(root)
    retained_issues: list[Any] = []
    case_ids: list[str] = []
    applied_decisions: list[dict[str, Any]] = []

    for raw_issue in report.get("issues", []) or []:
        if not isinstance(raw_issue, dict):
            retained_issues.append(raw_issue)
            continue
        candidate_ids = sorted(
            str(item) for item in raw_issue.get("candidate_ids", []) if str(item)
        )
        claim_ids = sorted(
            {
                claim_id
                for candidate_id in candidate_ids
                for claim_id in (
                    candidate_by_id[candidate_id].claim_ids
                    if candidate_id in candidate_by_id
                    else []
                )
                if claim_id in claim_by_id
            }
        )
        if not claim_ids:
            retained_issues.append(raw_issue)
            continue
        issue_type = str(raw_issue.get("issue_type") or "semantic_conflict")
        logical_key = _semantic_case_logical_key(
            stage=stage,
            repair_artifact=repair_artifact,
            issue_type=issue_type,
            claim_ids=claim_ids,
        )
        _stale_superseded_cases(store, logical_key=logical_key, source_hash=source_hash)
        issue = _semantic_audit_issue(
            raw_issue,
            repair_artifact=repair_artifact,
            artifacts=artifacts,
            claims=[claim_by_id[claim_id] for claim_id in claim_ids],
            source_version=compiler_fingerprint,
        )
        case_id = _semantic_case_id(logical_key, source_hash)
        source_blob_hash = store.put_blob(
            {
                "artifacts": artifacts,
                "claims": [claim_by_id[item].model_dump(mode="json") for item in claim_ids],
                "model_issue": raw_issue,
                "compiler_fingerprint": compiler_fingerprint,
                "artifact_hashes": artifact_hashes,
            },
            kind="repair_source",
        )
        existing = store.load_case(case_id)
        if existing is None:
            chapter_numbers = sorted(
                {
                    number
                    for claim_id in claim_ids
                    for number in claim_by_id[claim_id].chapter_numbers
                    if number > 0
                }
            )
            authority = "proposal_required" if policy is not None else "automatic_derived"
            existing = store.create_case(
                RepairCase(
                    case_id=case_id,
                    project_id=root.name,
                    content_type=SEMANTIC_REPAIR_CONTENT_TYPE,
                    source="model_semantic_adjudication",
                    artifact_id=repair_artifact,
                    source_version=compiler_fingerprint,
                    source_hash=source_hash,
                    authority=authority,
                    input_version=input_version,
                    policy_version=policy.version if policy is not None else None,
                    title=str(raw_issue.get("summary") or "上游语义一致性复核"),
                    chapter_numbers=chapter_numbers,
                    issues=[issue],
                    metadata={
                        "logical_key": logical_key,
                        "claim_ids": claim_ids,
                        "candidate_ids": candidate_ids,
                        "stage": stage,
                        "repair_artifact": repair_artifact,
                        "repair_artifact_hash": artifact_hashes.get(repair_artifact, ""),
                        "artifact_hashes": artifact_hashes,
                        "source_blob_hash": source_blob_hash,
                        "report_id": str(report.get("report_id") or ""),
                        "report_hash": repair_content_hash(report),
                        "major_source_meaning": bool(
                            (raw_issue.get("metadata") or {}).get("major_source_meaning", True)
                            if isinstance(raw_issue.get("metadata"), dict)
                            else True
                        ),
                    },
                )
            )
            targets = _resolved_targets(issue, artifacts=artifacts)
            existing = store.append_event(
                case_id,
                "targets_resolved",
                {
                    "targets": [target.model_dump(mode="json") for target in targets],
                    "reason": "semantic evidence requires author review"
                    if any(target.resolution_status != "resolved" for target in targets)
                    else "",
                },
                expected_version=existing.version,
            )
        case_ids.append(case_id)
        raw_with_case = {**raw_issue, "repair_case_id": case_id}
        decision = existing.metadata.get("semantic_decision")
        if isinstance(decision, dict) and _decision_matches(
            decision,
            source_hash=source_hash,
            claim_ids=claim_ids,
        ):
            applied_decisions.append({"case_id": case_id, **decision})
            if decision.get("decision") == "accept_compatible":
                continue
            raw_with_case["authoritative_claim_ids"] = list(
                decision.get("authoritative_claim_ids", [])
            )
            raw_with_case["author_decision"] = "authoritative_claims"
        retained_issues.append(raw_with_case)

    result = dict(report)
    result["issues"] = retained_issues
    result["repair_case_ids"] = list(dict.fromkeys(case_ids))
    result["semantic_decisions_applied"] = applied_decisions
    if applied_decisions and not retained_issues:
        result.update(verdict="accept", blocked=False)
    elif any(
        item.get("author_decision") == "authoritative_claims"
        for item in retained_issues
        if isinstance(item, dict)
    ):
        result.update(verdict="needs_repair", blocked=True)
    return result


def record_semantic_candidate_verification(
    project_root: Any,
    *,
    case_ids: Iterable[str],
    artifact: str,
    baseline: dict[str, Any],
    candidate_payload: dict[str, Any],
    verification_report: dict[str, Any],
    attempt: int,
    base_compiler_fingerprint: str = "",
) -> list[RepairCase]:
    """Append one isolated model candidate and its full semantic recompile result."""

    store = RepairCaseStore(project_root)
    results: list[RepairCase] = []
    report_passed = (
        str(verification_report.get("verdict") or "") == "accept"
        and not bool(verification_report.get("blocked"))
        and bool((verification_report.get("claim_coverage") or {}).get("complete"))
        and bool((verification_report.get("pair_coverage") or {}).get("complete"))
    )
    for case_id in dict.fromkeys(str(item) for item in case_ids if str(item)):
        case = store.load_case(case_id)
        if case is None or case.content_type != SEMANTIC_REPAIR_CONTENT_TYPE:
            continue
        candidate = build_initialization_candidate(
            artifact=artifact,
            baseline=baseline,
            candidate_payload=candidate_payload,
            issues=case.issues,
            version=len(case.candidates) + 1,
            origin="model",
            stage="semantic_consistency",
        )
        if candidate is None:
            results.append(case)
            continue
        if case.latest_candidate is not None and case.latest_candidate.candidate_hash == (
            candidate.candidate_hash
        ):
            results.append(case)
            continue
        candidate_blob_hash = store.put_blob(candidate_payload, kind="repair_candidate")
        report_blob_hash = store.put_blob(verification_report, kind="repair_verification")
        candidate = candidate.model_copy(
            update={
                "case_id": case.case_id,
                "blob_hash": candidate_blob_hash,
                "metadata": {
                    **candidate.metadata,
                    "attempt": attempt,
                    "verification_report_blob_hash": report_blob_hash,
                    "base_compiler_fingerprint": (base_compiler_fingerprint or case.source_version),
                    "compiler_fingerprint": str(
                        verification_report.get("compiler_fingerprint") or ""
                    ),
                },
            }
        )
        case = store.append_event(
            case.case_id,
            "candidate_built",
            {"candidate": candidate.model_dump(mode="json")},
            expected_version=case.version,
        )
        case = store.append_event(
            case.case_id,
            "verification_requested",
            {"validator_id": "semantic_claim_recompile_v1"},
            expected_version=case.version,
        )
        verification = build_initialization_verification(
            candidate=candidate,
            issues=case.issues,
            passed=report_passed,
            validator_id="semantic_claim_recompile_v1",
            errors=[] if report_passed else [str(verification_report.get("summary") or "")],
            regression_issue_ids=[]
            if report_passed
            else [
                str(item.get("issue_id") or item.get("id") or "semantic_residual")
                for item in verification_report.get("issues", []) or []
                if isinstance(item, dict)
            ],
        )
        case = store.append_event(
            case.case_id,
            "verification_completed",
            {
                "verification": verification.model_dump(mode="json"),
                "reason": "" if verification.passed else "semantic_candidate_rejected",
            },
            expected_version=case.version,
        )
        results.append(case)
    return results


def semantic_repair_case_ids(report: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(issue.get("repair_case_id") or "")
            for issue in report.get("issues", []) or []
            if isinstance(issue, dict) and str(issue.get("repair_case_id") or "")
        )
    )


def _semantic_case_logical_key(
    *, stage: str, repair_artifact: str, issue_type: str, claim_ids: list[str]
) -> str:
    seed = "|".join([stage, repair_artifact, issue_type, *claim_ids])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _semantic_case_id(logical_key: str, source_hash: str) -> str:
    return "semantic-" + hashlib.sha256(f"{logical_key}:{source_hash}".encode()).hexdigest()[:24]


def _stale_superseded_cases(store: RepairCaseStore, *, logical_key: str, source_hash: str) -> None:
    terminal = {"stale", "rejected", "published"}
    for case in store.list_cases():
        if (
            case.content_type == SEMANTIC_REPAIR_CONTENT_TYPE
            and case.metadata.get("logical_key") == logical_key
            and case.source_hash != source_hash
            and case.status not in terminal
        ):
            store.append_event(
                case.case_id,
                "case_stale",
                {"reason": "semantic source hash changed"},
                expected_version=case.version,
            )


def _semantic_audit_issue(
    raw_issue: dict[str, Any],
    *,
    repair_artifact: str,
    artifacts: dict[str, dict[str, Any]],
    claims: list[CoherenceClaim],
    source_version: str,
) -> AuditIssueV2:
    uncertain = str(raw_issue.get("issue_type") or "") == "semantic_judgement_uncertain"
    repair_targets: list[AuditLocator] = []
    reference_targets: list[AuditLocator] = []
    evidence: list[AuditEvidence] = []
    for claim in claims:
        role = "repair" if claim.artifact == repair_artifact and not uncertain else "reference"
        locator = _claim_locator(
            claim,
            role=role,
            artifacts=artifacts,
            source_version=source_version,
        )
        evidence.append(
            AuditEvidence(
                quote=str(claim.evidence or claim.claim_text),
                source=f"{claim.artifact}:{claim.source_path}",
                locator=locator,
                confidence=claim.confidence,
            )
        )
        if role == "repair":
            repair_targets.append(locator)
        else:
            reference_targets.append(locator)
    if not repair_targets:
        repair_targets.append(
            AuditLocator(
                target_format="manual_only",
                role="repair",
                surface=repair_artifact,
                manual_review_reason=(
                    "model_semantic_uncertain" if uncertain else "no_safe_repair_target"
                ),
                source_version=source_version,
                target_version=source_version,
            )
        )
    issue_id = str(raw_issue.get("issue_id") or raw_issue.get("id") or "")
    if not issue_id:
        issue_id = (
            "semantic-issue-"
            + hashlib.sha256("|".join(claim.claim_id for claim in claims).encode()).hexdigest()[:20]
        )
    severity = str(raw_issue.get("severity") or "high")
    if severity not in {"critical", "high", "medium", "low"}:
        severity = "high"
    return AuditIssueV2(
        issue_id=issue_id,
        dimension="init_coherence",
        issue_type=str(raw_issue.get("issue_type") or "semantic_conflict"),
        severity=severity,
        blocking=True,
        summary=str(raw_issue.get("summary") or "上游语义事实需要复核"),
        description=str(
            raw_issue.get("description") or raw_issue.get("summary") or "模型语义裁决未通过"
        ),
        evidence=evidence,
        repair_targets=repair_targets,
        reference_targets=reference_targets,
        repair_intent=AuditRepairIntent(
            operation="manual_review" if uncertain else "json_patch",
            target_policy="author_semantic_decision" if uncertain else "minimal_exact_targets",
            rationale=str(raw_issue.get("description") or raw_issue.get("summary") or ""),
            preserve=[
                "explicit author intent",
                "locked content",
                "ending",
                "character fate",
                "world rules",
            ],
            allowed_strategies=["json_patch", "manual_review"],
        ),
        postconditions=[
            AuditPostcondition(
                validator_id="semantic_claim_recompile_v1",
                description="从同一未改基线重新抽取 claims 并完成全部 pair 裁决",
            )
        ],
        metadata={
            "claim_ids": [claim.claim_id for claim in claims],
            "candidate_ids": list(raw_issue.get("candidate_ids", []) or []),
            "model_reason": str(raw_issue.get("description") or raw_issue.get("summary") or ""),
        },
    )


def _claim_locator(
    claim: CoherenceClaim,
    *,
    role: str,
    artifacts: dict[str, dict[str, Any]],
    source_version: str,
) -> AuditLocator:
    payload = artifacts.get(claim.artifact)
    pointer = str(claim.source_path or "")
    found, current = (
        resolve_json_pointer(payload, pointer)
        if isinstance(payload, (dict, list)) and pointer.startswith("/")
        else (False, None)
    )
    if found:
        return AuditLocator(
            target_format="json_artifact",
            role=role,
            surface=claim.artifact,
            artifact=claim.artifact,
            json_pointer=pointer,
            field=claim.source_field,
            claim_id=claim.claim_id,
            stable_node_id=f"semantic:{claim.artifact}:{claim.claim_id}",
            container_hash=repair_content_hash(current),
            comparator_id="model_semantic_claim_v1",
            actual_raw=current,
            actual_normalized=current,
            source_version=source_version,
            target_version=source_version,
            chapter_range=list(claim.chapter_numbers),
            confidence=1.0,
        )
    return AuditLocator(
        target_format="manual_only",
        role=role,
        surface=claim.artifact,
        claim_id=claim.claim_id,
        manual_review_reason="claim_source_path_not_resolvable",
        source_version=source_version,
        target_version=source_version,
    )


def _resolved_targets(
    issue: AuditIssueV2, *, artifacts: dict[str, dict[str, Any]]
) -> list[ResolvedRepairTarget]:
    targets: list[ResolvedRepairTarget] = []
    for index, locator in enumerate(issue.repair_targets, 1):
        found, current = (
            resolve_json_pointer(artifacts.get(locator.artifact), locator.json_pointer)
            if locator.target_format == "json_artifact" and locator.json_pointer
            else (False, None)
        )
        resolved = locator.target_format == "json_artifact" and found
        targets.append(
            ResolvedRepairTarget(
                target_id=f"{issue.issue_id}:{index}",
                target_format=locator.target_format,
                surface=locator.surface,
                locator=locator,
                path=locator.json_pointer if resolved else "",
                current_value=current,
                current_hash=repair_content_hash(current) if resolved else "",
                issue_ids=[issue.issue_id],
                allowed_operation="json_patch" if resolved else "manual_review",
                confidence=1.0 if resolved else 0.0,
                resolution_status="resolved" if resolved else "manual_required",
                reason="model_semantic_claim_target" if resolved else locator.manual_review_reason,
            )
        )
    return targets


def _decision_matches(decision: dict[str, Any], *, source_hash: str, claim_ids: list[str]) -> bool:
    return (
        str(decision.get("source_hash") or "") == source_hash
        and sorted(str(item) for item in decision.get("claim_ids", []) if str(item)) == claim_ids
    )


__all__ = [
    "SEMANTIC_REPAIR_CONTENT_TYPE",
    "record_semantic_candidate_verification",
    "reconcile_semantic_repair_cases",
    "semantic_repair_case_ids",
]
