"""Candidate evidence helpers for initialization artifact repair.

The initialization pipeline has several domain validators, but candidates must
share the same issue/locator/candidate/verification contracts as prose and TTS.
This module is intentionally storage-free: callers decide whether an unapproved
intermediate may be adopted or an approved foundation must become a proposal.
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Iterable

from novel_forge.core.schemas.audit import (
    AuditEvidence,
    AuditIssueV2,
    AuditLocator,
    AuditPostcondition,
    AuditRepairIntent,
)
from novel_forge.core.schemas.repair import (
    RepairCandidate,
    RepairCandidateOrigin,
    RepairPatchRecord,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.core.utils.repair_target_resolver import (
    field_path_to_json_pointer,
    resolve_json_pointer,
    stable_repair_value_hash,
)
from novel_forge.persistence.repair_case_store import (
    canonical_repair_json,
    repair_content_hash,
)

_DURATION_RANGE_RE = re.compile(
    r"[\[【（(]\s*(\d+)\s*[,，、~～\-至]\s*(\d+)\s*[\]】）)]\s*(日|天)"
)


def normalize_init_comparison_value(value: Any) -> Any:
    """Return the displayable canonical value used by init comparators.

    This is evidence normalization, not permission to rewrite author content.
    It deliberately normalizes only structural containers, surrounding
    whitespace, common full-width punctuation, and explicit numeric day ranges.
    """

    if isinstance(value, dict):
        return {
            str(key): normalize_init_comparison_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [normalize_init_comparison_value(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_init_comparison_value(item) for item in value]
    if not isinstance(value, str):
        return value
    text = " ".join(value.split()).strip()
    text = text.translate(
        {
            ord("，"): ",",
            ord("【"): "[",
            ord("】"): "]",
            ord("（"): "(",
            ord("）"): ")",
        }
    )
    return _DURATION_RANGE_RE.sub(
        lambda match: f"[{match.group(1)},{match.group(2)}]{match.group(3)}",
        text,
    )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _severity(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"critical", "high", "medium", "low"}:
        return normalized
    if normalized in {"error", "fatal"}:
        return "high"
    if normalized in {"warning", "warn"}:
        return "medium"
    return "medium"


def _issue_id(artifact: str, index: int, kind: str, field: str, message: str) -> str:
    raw = f"{artifact}:{index}:{kind}:{field}:{message}"
    return "init-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def build_initialization_audit_issues(
    *,
    artifact: str,
    payload: Any,
    issues: Iterable[Any],
    source_version: str = "",
) -> list[AuditIssueV2]:
    """Project legacy init-policy issues into the unified audit contract."""

    raw_payload = _jsonable(payload)
    projected: list[AuditIssueV2] = []
    for index, issue in enumerate(issues, 1):
        metadata: dict[str, Any]
        if isinstance(issue, AuditIssueV2):
            projected.append(issue)
            continue
        if isinstance(issue, dict) and issue.get("repair_targets"):
            try:
                parsed = AuditIssueV2.model_validate(issue)
            except Exception:
                parsed = None
            if parsed is not None:
                enriched_targets: list[AuditLocator] = []
                for locator in parsed.repair_targets:
                    found, actual_raw = (
                        resolve_json_pointer(raw_payload, locator.json_pointer)
                        if locator.target_format == "json_artifact" and locator.json_pointer
                        else (False, None)
                    )
                    enriched_targets.append(
                        locator.model_copy(
                            update={
                                "stable_node_id": locator.stable_node_id
                                or f"init:{artifact}:{locator.json_pointer or locator.field_path}",
                                "container_hash": locator.container_hash
                                or (
                                    stable_repair_value_hash(actual_raw) if found else ""
                                ),
                                "comparator_id": locator.comparator_id
                                or "init_structured_claim_v1",
                                "actual_raw": (
                                    locator.actual_raw
                                    if locator.actual_raw is not None
                                    else actual_raw
                                ),
                                "actual_normalized": (
                                    locator.actual_normalized
                                    if locator.actual_normalized is not None
                                    else normalize_init_comparison_value(actual_raw)
                                ),
                                "expected_normalized": (
                                    locator.expected_normalized
                                    if locator.expected_normalized is not None
                                    else normalize_init_comparison_value(locator.expected_raw)
                                ),
                                "source_version": locator.source_version or source_version,
                                "target_version": locator.target_version or source_version,
                            }
                        )
                    )
                projected.append(parsed.model_copy(update={"repair_targets": enriched_targets}))
                continue
        if isinstance(issue, dict):
            kind = str(issue.get("kind") or issue.get("issue_type") or "schema_shape")
            message = str(issue.get("message") or issue.get("description") or "初始化校验失败")
            field = str(issue.get("field") or "")
            severity = _severity(str(issue.get("severity") or "error"))
            metadata = (
                dict(issue["metadata"])
                if isinstance(issue.get("metadata"), dict)
                else {}
            )
        else:
            kind_value = getattr(issue, "kind", "schema_shape")
            kind = str(getattr(kind_value, "value", kind_value))
            message = str(getattr(issue, "message", "初始化校验失败"))
            field = str(getattr(issue, "field", "") or "")
            severity = _severity(str(getattr(issue, "severity", "error")))
            raw_metadata = getattr(issue, "metadata", {})
            metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}

        pointer = str(metadata.get("json_pointer") or "")
        if not pointer and field:
            pointer = field_path_to_json_pointer(field)
        found, actual_raw = resolve_json_pointer(raw_payload, pointer) if pointer else (False, None)
        comparator = str(metadata.get("comparator_id") or "init_schema_normalization_v1")
        expected_raw = metadata.get("expected_raw")
        expected_normalized = metadata.get(
            "expected_normalized", normalize_init_comparison_value(expected_raw)
        )
        actual_normalized = metadata.get(
            "actual_normalized",
            normalize_init_comparison_value(actual_raw if found else metadata.get("actual_raw")),
        )
        actual_evidence = actual_raw if found else metadata.get("actual_raw")
        explicit_issue_id = (
            str(issue.get("issue_id") or issue.get("id") or "")
            if isinstance(issue, dict)
            else ""
        )
        issue_identity = explicit_issue_id or str(metadata.get("issue_id") or "") or _issue_id(
            artifact, index, kind, field, message
        )
        if pointer and found:
            locator = AuditLocator(
                target_format="json_artifact",
                role="repair",
                surface=artifact,
                confidence=1.0,
                artifact=artifact,
                json_pointer=pointer,
                field=field or pointer.strip("/").split("/", 1)[0],
                field_path=field,
                stable_node_id=f"init:{artifact}:{pointer}",
                container_hash=stable_repair_value_hash(actual_raw),
                comparator_id=comparator,
                expected_raw=expected_raw,
                actual_raw=actual_evidence,
                expected_normalized=expected_normalized,
                actual_normalized=actual_normalized,
                source_version=source_version,
                target_version=source_version,
            )
        else:
            locator = AuditLocator(
                target_format="manual_only",
                role="repair",
                surface=artifact,
                manual_review_reason="init_issue_has_no_exact_json_target",
                comparator_id=comparator,
                expected_raw=expected_raw,
                actual_raw=actual_evidence,
                expected_normalized=expected_normalized,
                actual_normalized=actual_normalized,
                source_version=source_version,
                target_version=source_version,
            )
        projected.append(
            AuditIssueV2(
                issue_id=issue_identity,
                dimension="initialization",
                issue_type=kind,
                severity=severity,
                blocking=severity in {"critical", "high"},
                summary=message[:160],
                description=message,
                evidence=[
                    AuditEvidence(
                        quote=(
                            canonical_repair_json(actual_evidence)
                            if actual_evidence is not None
                            else message
                        )[:500],
                        source=f"init_policy:{artifact}",
                        locator=locator,
                        confidence=1.0 if found else 0.0,
                    )
                ],
                repair_targets=[locator],
                reference_targets=[],
                repair_intent=AuditRepairIntent(
                    operation="schema_patch" if kind == "schema_shape" else "field_replace",
                    target_policy="single_exact_target" if found else "manual_only",
                    rationale=message,
                    preserve=[
                        "explicit author intent",
                        "locked content",
                        "ending",
                        "character fate",
                        "world rules",
                    ],
                    allowed_strategies=["deterministic", "json_patch", "manual_review"],
                ),
                postconditions=[
                    AuditPostcondition(
                        validator_id=f"init_policy:{artifact}",
                        description="重新运行产生本问题的初始化策略校验器",
                    )
                ],
                metadata={**metadata, "comparator_id": comparator},
            )
        )
    return projected


def _diff_paths(before: Any, after: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    before = _jsonable(before)
    after = _jsonable(after)
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(set(before) | set(after), key=str):
            token = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{path}/{token}"
            if key not in before:
                changes.append((child, None, after[key]))
            elif key not in after:
                changes.append((child, before[key], None))
            else:
                changes.extend(_diff_paths(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return [(path or "/", before, after)]
        changes = []
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            changes.extend(_diff_paths(left, right, f"{path}/{index}"))
        return changes
    return [] if before == after else [(path or "/", before, after)]


def build_initialization_candidate(
    *,
    artifact: str,
    baseline: Any,
    candidate_payload: Any,
    issues: Iterable[AuditIssueV2],
    version: int,
    origin: RepairCandidateOrigin,
    stage: str,
) -> RepairCandidate | None:
    """Build exact candidate evidence without storing or publishing content."""

    baseline_value = _jsonable(baseline)
    candidate_value = _jsonable(candidate_payload)
    if repair_content_hash(baseline_value) == repair_content_hash(candidate_value):
        return None
    issue_ids = [issue.issue_id for issue in issues]
    base_hash = repair_content_hash(baseline_value)
    candidate_hash = repair_content_hash(candidate_value)
    changes = _diff_paths(baseline_value, candidate_value)
    patches = [
        RepairPatchRecord(
            target_id=(
                f"init:{artifact}:"
                + hashlib.sha256(f"{path}:{index}".encode("utf-8")).hexdigest()[:16]
            ),
            expected_hash=repair_content_hash(before),
            replacement_hash=repair_content_hash(after),
            operation="replace",
            field_path=path,
            rationale=f"{stage}:{','.join(issue_ids[:4])}",
        )
        for index, (path, before, after) in enumerate(changes)
    ]
    before_json = canonical_repair_json(baseline_value)
    after_json = canonical_repair_json(candidate_value)
    change_ratio = 1.0 - SequenceMatcher(None, before_json, after_json).ratio()
    case_seed = f"{artifact}:{base_hash}"
    return RepairCandidate(
        case_id="init-" + hashlib.sha256(case_seed.encode("utf-8")).hexdigest()[:24],
        version=version,
        base_hash=base_hash,
        candidate_hash=candidate_hash,
        blob_hash=candidate_hash,
        origin=origin,
        patch_count=len(patches),
        change_ratio=max(0.0, min(1.0, change_ratio)),
        patches=patches,
        protected_items=[
            "explicit author intent",
            "locked content",
            "ending",
            "character fate",
            "world rules",
        ],
        metadata={
            "artifact": artifact,
            "stage": stage,
            "issue_ids": issue_ids,
            "authority": "automatic_derived",
        },
    )


def build_initialization_verification(
    *,
    candidate: RepairCandidate,
    issues: Iterable[AuditIssueV2],
    passed: bool,
    validator_id: str,
    errors: Iterable[str] = (),
    warnings: Iterable[str] = (),
    regression_issue_ids: Iterable[str] = (),
) -> RepairVerificationBundle:
    """Bind one original init validator result to an exact candidate."""

    issue_ids = [issue.issue_id for issue in issues]
    regressions = [str(item) for item in regression_issue_ids if str(item)]
    details = [str(item) for item in errors if str(item)]
    warning_details = [str(item) for item in warnings if str(item)]
    return RepairVerificationBundle(
        case_id=candidate.case_id,
        candidate_version=candidate.version,
        candidate_hash=candidate.candidate_hash,
        passed=passed and not regressions,
        resolved_issue_ids=issue_ids if passed and not regressions else [],
        residual_issue_ids=[] if passed and not regressions else issue_ids,
        regression_issue_ids=regressions,
        validators=[
            RepairValidatorResult(
                validator_id=validator_id,
                passed=passed and not regressions,
                details=[*details, *warning_details],
                evidence={
                    "candidate_hash": candidate.candidate_hash,
                    "warnings": warning_details,
                },
            )
        ],
        details=details,
        metadata={"artifact": candidate.metadata.get("artifact", "")},
    )


def normalization_issue(
    *,
    artifact: str,
    baseline: Any,
    candidate_payload: Any,
    source_version: str,
) -> AuditIssueV2:
    """Describe a schema-normalization candidate with raw/canonical evidence."""

    changes = _diff_paths(baseline, candidate_payload)
    path, raw_value, normalized_value = changes[0] if changes else ("/", baseline, candidate_payload)
    field = path.strip("/").split("/", 1)[0]
    message = f"{artifact} 需要确定性 schema 规范化"
    return build_initialization_audit_issues(
        artifact=artifact,
        payload=baseline,
        source_version=source_version,
        issues=[
            {
                "kind": "format_alias",
                "message": message,
                "field": field,
                "severity": "medium",
                "metadata": {
                    "json_pointer": path,
                    "expected_raw": normalized_value,
                    "actual_raw": raw_value,
                    "expected_normalized": normalize_init_comparison_value(normalized_value),
                    "actual_normalized": normalize_init_comparison_value(raw_value),
                    "comparator_id": "init_schema_normalization_v1",
                },
            }
        ],
    )[0]


__all__ = [
    "build_initialization_audit_issues",
    "build_initialization_candidate",
    "build_initialization_verification",
    "normalization_issue",
    "normalize_init_comparison_value",
]
