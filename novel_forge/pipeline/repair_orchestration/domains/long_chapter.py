"""Candidate evidence and verification gates for long-chapter repairs.

The active continuity, causal, and reading-power loops still own their domain
prompts and evaluators.  This module gives those loops one common boundary:
changed prose is a working candidate until the original evaluator closes the
targeted issues, and every candidate is projected into the append-only repair
case store without publishing chapter text.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from novel_forge.core.schemas.audit import (
    AuditEvidence,
    AuditIssueV2,
    AuditLocator,
    AuditPostcondition,
    AuditRepairIntent,
    ResolvedRepairTarget,
)
from novel_forge.core.schemas.repair import (
    RepairCandidate,
    RepairCase,
    RepairPatchRecord,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.pipeline.repair_orchestration.domains._shared import (
    fallback_project_id,
    repair_layout_from,
    text_change_ratio,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairExecutionResult,
    RepairVerificationResult,
)

_PROTECTED_ITEMS = (
    "explicit author intent",
    "locked content",
    "ending",
    "character fate",
    "world rules",
    "archived prose",
)


def verify_long_working_candidate(
    *,
    dimension: str,
    loop_result: Any,
    execution: RepairExecutionResult,
    unavailable_reason: str = "",
) -> RepairVerificationResult:
    """Require the original dimension evaluator for every changed candidate."""

    if unavailable_reason:
        return RepairVerificationResult(verified=False, confidence=0.0, reason=unavailable_reason)
    changed = bool(execution.applied and execution.change_ratio > 0)
    evidence = dict(getattr(loop_result, "verification_evidence", {}) or {})
    if not changed:
        if execution.payload.get("needs_human_review") or execution.payload.get("repair_exhausted"):
            return RepairVerificationResult(
                verified=False,
                confidence=0.2,
                reason=execution.failure_reason or f"{dimension}_repair_not_closed",
            )
        original_issue_ids = [
            str(item) for item in evidence.get("original_issue_ids") or () if str(item)
        ]
        if original_issue_ids and not (
            evidence.get("recheck_performed") and evidence.get("gate_passed")
        ):
            return RepairVerificationResult(
                verified=False,
                confidence=0.0,
                reason=f"{dimension}_no_change_original_issue_open",
                residual_issues=[{"issue_id": item} for item in original_issue_ids],
            )
        return RepairVerificationResult(
            verified=True,
            confidence=1.0,
            reason=f"{dimension}_no_candidate_change",
        )

    rechecked = bool(evidence.get("recheck_performed"))
    gate_passed = bool(evidence.get("gate_passed"))
    residual = [str(item) for item in evidence.get("residual_issue_ids") or () if str(item)]
    regressions = [
        str(item) for item in evidence.get("regression_issue_ids") or () if str(item)
    ]
    candidate_bound = str(evidence.get("candidate_text_hash") or "") == repair_content_hash(
        str(getattr(loop_result, "current_text", "") or "")
    )
    verified = all(
        (
            rechecked,
            gate_passed,
            candidate_bound,
            not residual,
            not regressions,
            not execution.payload.get("needs_human_review"),
            not execution.payload.get("repair_exhausted"),
        )
    )
    if verified:
        return RepairVerificationResult(
            verified=True,
            confidence=0.95,
            reason=f"{dimension}_original_recheck_passed",
        )
    failed_gates: list[str] = []
    if not rechecked:
        failed_gates.append("original_recheck_missing")
    if not gate_passed:
        failed_gates.append("original_issue_not_closed")
    if not candidate_bound:
        failed_gates.append("candidate_hash_mismatch")
    if residual:
        failed_gates.append("residual_issues")
    if regressions:
        failed_gates.append("new_regression")
    if execution.payload.get("needs_human_review"):
        failed_gates.append("needs_human_review")
    if execution.payload.get("repair_exhausted"):
        failed_gates.append("repair_exhausted")
    return RepairVerificationResult(
        verified=False,
        confidence=0.0,
        reason=f"{dimension}_candidate_rejected:{','.join(failed_gates)}",
        residual_issues=[{"issue_id": item} for item in residual],
    )


def remember_rejected_long_candidate(source_context: dict[str, Any], key: str) -> None:
    """Retain rejected in-memory evidence while restoring the live baseline."""

    value = source_context.pop(key, None)
    if value is not None:
        source_context[f"{key}_rejected"] = value


def record_long_candidate_cases(
    *,
    bundle: Any,
    runner: Any,
    dimension: str,
    chapter_number: int,
    baseline_text: str,
    loop_result: Any | None,
    verification: RepairVerificationResult | None,
) -> list[str]:
    """Persist per-issue working-candidate evidence; never publish chapter text."""

    if loop_result is None:
        return []
    evidence = dict(getattr(loop_result, "verification_evidence", {}) or {})
    raw_issues = [item for item in evidence.get("initial_issues") or () if isinstance(item, dict)]
    if not raw_issues:
        return []
    layout = repair_layout_from(bundle)
    root = getattr(layout, "root", None)
    if root is None:
        return []
    store = RepairCaseStore(root)
    project_id = fallback_project_id(bundle, runner)
    candidate_text = str(getattr(loop_result, "current_text", "") or "")
    source_hash = repair_content_hash(baseline_text)
    candidate_hash = repair_content_hash(candidate_text)
    source_blob_hash = store.put_blob(baseline_text, kind="repair_source")
    recorded: list[str] = []

    for raw_issue in raw_issues:
        issue = _long_audit_issue(
            raw_issue,
            dimension=dimension,
            chapter_number=chapter_number,
            baseline_text=baseline_text,
        )
        case_id = f"long-{dimension}-{uuid4().hex}"
        case = store.create_case(
            RepairCase(
                case_id=case_id,
                project_id=project_id,
                content_type=f"long_chapter_{dimension}",
                source=f"long_{dimension}_review",
                artifact_id=f"chapter:{chapter_number}:working",
                source_version=f"chapter:{chapter_number}:{source_hash[:16]}",
                source_hash=source_hash,
                authority="automatic_working_candidate",
                title=issue.summary,
                chapter_numbers=[chapter_number],
                issues=[issue],
                metadata={
                    "dimension": dimension,
                    "source_blob_hash": source_blob_hash,
                    "validator_id": evidence.get("validator_id", ""),
                    "publication": "existing_chapter_stage_only",
                },
            )
        )
        issue_target = _resolved_issue_target(issue, baseline_text)
        targets = [issue_target]
        if issue_target.resolution_status == "resolved":
            targets.append(
                _candidate_scope_target(
                    issue=issue,
                    baseline_text=baseline_text,
                    chapter_number=chapter_number,
                )
            )
        case = store.append_event(
            case.case_id,
            "targets_resolved",
            {"targets": [item.model_dump(mode="json") for item in targets]},
            expected_version=case.version,
        )
        recorded.append(case.case_id)
        if case.status == "manual_required":
            continue
        if candidate_hash == source_hash:
            store.append_event(
                case.case_id,
                "case_failed",
                {"reason": "long_repair_produced_no_candidate_change"},
                expected_version=case.version,
            )
            continue

        candidate_blob_hash = store.put_blob(candidate_text)
        scope_target = targets[-1]
        candidate = RepairCandidate(
            case_id=case.case_id,
            version=1,
            base_hash=source_hash,
            candidate_hash=candidate_hash,
            blob_hash=candidate_blob_hash,
            origin="model",
            patch_count=1,
            change_ratio=text_change_ratio(baseline_text, candidate_text),
            patches=[
                RepairPatchRecord(
                    target_id=scope_target.target_id,
                    expected_hash=scope_target.current_hash,
                    replacement_hash=candidate_hash,
                    operation="window_rewrite",
                    field_path="$text",
                    rationale=f"{dimension}_working_candidate",
                )
            ],
            protected_items=list(_PROTECTED_ITEMS),
            metadata={
                "dimension": dimension,
                "rounds_used": int(getattr(loop_result, "rounds_used", 0) or 0),
                "candidate_only": True,
            },
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
            {},
            expected_version=case.version,
        )
        bundle_result = _verification_bundle(
            case_id=case.case_id,
            candidate=candidate,
            issue=issue,
            evidence=evidence,
            verification=verification,
            dimension=dimension,
        )
        verification_blob = store.put_blob(bundle_result, kind="repair_verification")
        bundle_result = bundle_result.model_copy(
            update={
                "metadata": {
                    **bundle_result.metadata,
                    "blob_hash": verification_blob,
                }
            }
        )
        store.append_event(
            case.case_id,
            "verification_completed",
            {
                "verification": bundle_result.model_dump(mode="json"),
                "reason": verification.reason if verification is not None else "verification_missing",
            },
            expected_version=case.version,
        )
    return recorded


def _long_audit_issue(
    raw: dict[str, Any],
    *,
    dimension: str,
    chapter_number: int,
    baseline_text: str,
) -> AuditIssueV2:
    harness = raw.get("metadata", {}).get("repair_harness", {}) if isinstance(raw.get("metadata"), dict) else {}
    harness_issue = harness.get("issue") if isinstance(harness, dict) else None
    if isinstance(harness_issue, dict):
        try:
            parsed = AuditIssueV2.model_validate(harness_issue)
            if parsed.repair_targets:
                locator = parsed.repair_targets[0]
                if locator.target_format == "prose_text":
                    locator = _prose_locator(
                        baseline_text,
                        chapter_number=chapter_number,
                        issue_id=parsed.issue_id,
                        quote=locator.quote,
                        paragraph_start=locator.paragraph_start,
                        paragraph_end=locator.paragraph_end,
                    )
                    evidence = list(parsed.evidence)
                    if evidence:
                        evidence[0] = evidence[0].model_copy(
                            update={"locator": locator, "confidence": locator.confidence}
                        )
                    return parsed.model_copy(
                        update={"repair_targets": [locator], "evidence": evidence}
                    )
                manual = AuditLocator(
                    target_format="manual_only",
                    surface=locator.surface or "chapter_text",
                    confidence=0.0,
                    chapter_number=chapter_number,
                    stable_node_id=locator.stable_node_id,
                    manual_review_reason="long_working_candidate_only_supports_prose_targets",
                )
                return parsed.model_copy(update={"repair_targets": [manual]})
        except Exception:
            pass

    issue_id = str(raw.get("_repair_issue_id") or raw.get("issue_id") or raw.get("id") or "")
    if not issue_id:
        seed = f"{dimension}:{chapter_number}:{raw!r}"
        issue_id = "long-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    summary = str(raw.get("summary") or raw.get("description") or raw.get("issue_type") or "长篇审查问题")
    evidence_text = _issue_evidence_text(raw)
    locator = _prose_locator(
        baseline_text,
        chapter_number=chapter_number,
        issue_id=issue_id,
        quote=evidence_text,
        paragraph_start=_positive_int(raw.get("paragraph_start") or raw.get("paragraph_index")),
        paragraph_end=_positive_int(raw.get("paragraph_end") or raw.get("paragraph_start")),
    )
    severity = str(raw.get("severity") or "medium").lower()
    if severity not in {"critical", "high", "medium", "low"}:
        severity = "medium"
    return AuditIssueV2(
        issue_id=issue_id,
        dimension=dimension,
        issue_type=str(raw.get("issue_type") or raw.get("type") or "review_issue"),
        severity=severity,
        blocking=severity in {"critical", "high"},
        summary=summary[:500],
        description=summary,
        evidence=[
            AuditEvidence(
                quote=evidence_text or summary,
                source=f"long_{dimension}_review",
                locator=locator,
                confidence=locator.confidence,
            )
        ],
        repair_targets=[locator],
        reference_targets=[],
        repair_intent=AuditRepairIntent(
            operation="window_rewrite" if locator.target_format == "prose_text" else "manual_review",
            target_policy=(
                "single_exact_target"
                if locator.target_format == "prose_text"
                else "manual_only"
            ),
            rationale=summary,
            preserve=list(_PROTECTED_ITEMS),
            allowed_strategies=["patch", "window_rewrite", "manual_review"],
        ),
        postconditions=[
            AuditPostcondition(
                validator_id=f"{dimension}_original_recheck_v1",
                description="重新运行发现该问题的原维度审查器",
            )
        ],
        metadata={"raw_issue": raw},
    )


def _issue_evidence_text(raw: dict[str, Any]) -> str:
    for key in ("evidence_quote", "quote", "anchor_text", "evidence"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested in ("quote", "text", "excerpt"):
                nested_value = value.get(nested)
                if isinstance(nested_value, str) and nested_value.strip():
                    return nested_value.strip()
    return ""


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _paragraph_offsets(text: str) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        content_end = cursor + len(line.rstrip("\r\n"))
        if line.strip():
            offsets.append((cursor, content_end))
        cursor += len(line)
    if text and not offsets:
        offsets.append((0, len(text)))
    return offsets


def _prose_locator(
    text: str,
    *,
    chapter_number: int,
    issue_id: str,
    quote: str,
    paragraph_start: int,
    paragraph_end: int,
) -> AuditLocator:
    if quote:
        first = text.find(quote)
        if first >= 0 and text.find(quote, first + 1) < 0:
            return AuditLocator(
                target_format="prose_text",
                surface="chapter_text",
                confidence=1.0,
                stable_node_id=f"chapter:{chapter_number}:issue:{issue_id}",
                chapter_number=chapter_number,
                quote=quote,
                text_hash=repair_content_hash(text),
                char_start=first,
                char_end=first + len(quote),
            )
    offsets = _paragraph_offsets(text)
    if paragraph_start > 0 and paragraph_start <= len(offsets):
        end_index = min(max(paragraph_end or paragraph_start, paragraph_start), len(offsets))
        char_start = offsets[paragraph_start - 1][0]
        char_end = offsets[end_index - 1][1]
        paragraph_quote = text[char_start:char_end]
        return AuditLocator(
            target_format="prose_text",
            surface="chapter_text",
            confidence=0.9,
            stable_node_id=f"chapter:{chapter_number}:paragraph:{paragraph_start}-{end_index}",
            chapter_number=chapter_number,
            paragraph_start=paragraph_start,
            paragraph_end=end_index,
            quote=paragraph_quote,
            text_hash=repair_content_hash(text),
            char_start=char_start,
            char_end=char_end,
        )
    return AuditLocator(
        target_format="manual_only",
        surface="chapter_text",
        confidence=0.0,
        chapter_number=chapter_number,
        stable_node_id=f"chapter:{chapter_number}:issue:{issue_id}",
        manual_review_reason="long_review_issue_has_no_unique_quote_or_paragraph_window",
        text_hash=repair_content_hash(text),
    )


def _resolved_issue_target(issue: AuditIssueV2, baseline_text: str) -> ResolvedRepairTarget:
    locator = issue.repair_targets[0]
    if locator.target_format != "prose_text":
        return ResolvedRepairTarget(
            target_id=f"{issue.issue_id}:manual",
            target_format="manual_only",
            surface="chapter_text",
            locator=locator,
            issue_ids=[issue.issue_id],
            allowed_operation="manual_review",
            confidence=0.0,
            resolution_status="manual_required",
            reason=locator.manual_review_reason,
        )
    selected = baseline_text[locator.char_start : locator.char_end] if locator.char_end else locator.quote
    return ResolvedRepairTarget(
        target_id=f"{issue.issue_id}:issue",
        target_format="prose_text",
        surface="chapter_text",
        locator=locator,
        window={"char_start": locator.char_start, "char_end": locator.char_end},
        current_value=selected,
        current_hash=repair_content_hash(selected),
        issue_ids=[issue.issue_id],
        allowed_operation="window_rewrite",
        confidence=locator.confidence,
    )


def _candidate_scope_target(
    *, issue: AuditIssueV2, baseline_text: str, chapter_number: int
) -> ResolvedRepairTarget:
    paragraphs = _paragraph_offsets(baseline_text)
    locator = AuditLocator(
        target_format="prose_text",
        surface="chapter_text",
        confidence=1.0,
        stable_node_id=f"chapter:{chapter_number}:candidate-scope",
        chapter_number=chapter_number,
        paragraph_start=1,
        paragraph_end=max(1, len(paragraphs)),
        quote=baseline_text,
        text_hash=repair_content_hash(baseline_text),
        char_start=0,
        char_end=len(baseline_text),
    )
    return ResolvedRepairTarget(
        target_id=f"{issue.issue_id}:candidate-scope",
        target_format="prose_text",
        surface="chapter_text",
        locator=locator,
        window={"char_start": 0, "char_end": len(baseline_text)},
        current_value=baseline_text,
        current_hash=repair_content_hash(baseline_text),
        issue_ids=[issue.issue_id],
        allowed_operation="window_rewrite",
        confidence=1.0,
    )


def _verification_bundle(
    *,
    case_id: str,
    candidate: RepairCandidate,
    issue: AuditIssueV2,
    evidence: dict[str, Any],
    verification: RepairVerificationResult | None,
    dimension: str,
) -> RepairVerificationBundle:
    passed = bool(verification is not None and verification.verified)
    regressions = [str(item) for item in evidence.get("regression_issue_ids") or () if str(item)]
    passed = passed and not regressions
    rechecked = bool(evidence.get("recheck_performed"))
    gate_passed = bool(evidence.get("gate_passed"))
    candidate_bound = str(evidence.get("candidate_text_hash") or "") == candidate.candidate_hash
    passed = passed and rechecked and gate_passed and candidate_bound
    validators = [
        RepairValidatorResult(
            validator_id=f"{dimension}_original_recheck_v1",
            passed=rechecked and gate_passed,
            details=[verification.reason if verification is not None else "verification_missing"],
            evidence={
                "score": evidence.get("score"),
                "score_threshold": evidence.get("score_threshold"),
                "repair_verdict": evidence.get("repair_verdict", ""),
            },
        ),
        RepairValidatorResult(
            validator_id="long_candidate_hash_binding_v1",
            passed=candidate_bound,
            evidence={"candidate_hash": candidate.candidate_hash},
        ),
        RepairValidatorResult(
            validator_id="long_no_high_regression_v1",
            passed=not regressions,
            details=regressions,
        ),
    ]
    return RepairVerificationBundle(
        case_id=case_id,
        candidate_version=candidate.version,
        candidate_hash=candidate.candidate_hash,
        passed=passed,
        resolved_issue_ids=[issue.issue_id] if passed else [],
        residual_issue_ids=[] if passed else [issue.issue_id],
        regression_issue_ids=regressions,
        validators=validators,
        details=[] if passed else [verification.reason if verification else "verification_missing"],
        metadata={
            "dimension": dimension,
            "authority": "automatic_working_candidate",
            "published": False,
        },
    )


__all__ = [
    "record_long_candidate_cases",
    "remember_rejected_long_candidate",
    "verify_long_working_candidate",
]
