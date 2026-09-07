"""Format-neutral diagnose -> locate -> patch candidate -> verify harness.

This is the text equivalent of a code-repair harness.  Reviewers describe an
``AuditIssueV2``; code resolves exact targets against the current immutable
inputs; a patch provider edits only an in-memory candidate; and the same audit
lane must verify the candidate before a caller may publish it.

The harness deliberately does not write files or grant authority.  Approved
story evidence uses ``proposal_required`` and prose/canon publication remains
owned by the existing PlanningRevision, revision, and archive barriers.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Callable, Literal, Protocol

from novel_forge.common.severity import normalize_severity
from novel_forge.core.schemas.audit import AuditIssueV2, AuditLocator, ResolvedRepairTarget
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.core.utils.repair_target_resolver import (
    RepairResolverContext,
    RepairTargetResolver,
    get_json_pointer,
    stable_repair_value_hash,
)

RepairAuthority = Literal["automatic_candidate", "proposal_required", "manual_only"]
RepairHarnessStatus = Literal[
    "ready",
    "proposal_required",
    "manual_required",
    "verified_candidate",
    "verification_failed",
]
RepairAttemptStatus = Literal["patch_rejected", "verification_failed", "verified"]


@dataclass(frozen=True)
class RepairPatch:
    """One target-id patch with an exact old-value precondition."""

    target_id: str
    replacement: Any
    expected_hash: str
    rationale: str = ""


@dataclass(frozen=True)
class RepairVerification:
    """Result returned by the original audit lane after candidate repair."""

    passed: bool
    details: tuple[str, ...] = ()
    remaining_issue_ids: tuple[str, ...] = ()
    regression_issue_ids: tuple[str, ...] = ()


@dataclass
class RepairCandidateState:
    """Isolated, unpublished candidate state used by a repair attempt."""

    artifacts: dict[str, Any] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    markdown_documents: dict[str, str] = field(default_factory=dict)
    prompt_responses: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_context(cls, context: RepairResolverContext) -> "RepairCandidateState":
        return cls(
            artifacts=copy.deepcopy(context.artifacts),
            texts=copy.deepcopy(context.texts),
            markdown_documents=copy.deepcopy(context.markdown_documents),
            prompt_responses=copy.deepcopy(context.prompt_responses),
        )

    def digest(self) -> str:
        return stable_repair_value_hash(
            {
                "artifacts": self.artifacts,
                "texts": self.texts,
                "markdown_documents": self.markdown_documents,
                "prompt_responses": self.prompt_responses,
            }
        )


@dataclass(frozen=True)
class RepairAttempt:
    """Compact, replayable evidence for one unpublished repair attempt."""

    attempt: int
    status: RepairAttemptStatus
    target_ids: tuple[str, ...]
    before_hash: str
    candidate_hash: str = ""
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepairHarnessResult:
    """Resolution and bounded-attempt outcome for a diagnostic."""

    issue_id: str
    authority: RepairAuthority
    status: RepairHarnessStatus
    targets: tuple[ResolvedRepairTarget, ...]
    attempts: tuple[RepairAttempt, ...] = ()
    reason: str = ""
    candidate: RepairCandidateState | None = field(default=None, repr=False, compare=False)

    def model_dump(self) -> dict[str, Any]:
        """Return report-safe evidence without embedding unpublished text."""

        return {
            "architecture": "diagnose_locate_patch_verify",
            "issue_id": self.issue_id,
            "authority": self.authority,
            "status": self.status,
            "reason": self.reason,
            "targets": [repair_report_payload(target.to_prompt_dict()) for target in self.targets],
            "attempts": [asdict(attempt) for attempt in self.attempts],
            "candidate_hash": self.candidate.digest() if self.candidate is not None else "",
        }


class RepairPatchProvider(Protocol):
    """Produce candidate patches for a bounded attempt."""

    def __call__(
        self,
        attempt: int,
        issue: AuditIssueV2,
        targets: tuple[ResolvedRepairTarget, ...],
        base: RepairCandidateState,
        previous: RepairVerification | None,
    ) -> list[RepairPatch]: ...


RepairVerifier = Callable[[RepairCandidateState, AuditIssueV2], RepairVerification]


class TextRepairHarness:
    """Resolve diagnostics and execute unpublished, verifier-gated repairs."""

    def __init__(self, context: RepairResolverContext | None = None) -> None:
        self.context = context or RepairResolverContext()
        self.resolver = RepairTargetResolver(self.context)

    def diagnose(
        self,
        issue: AuditIssueV2,
        *,
        authority: RepairAuthority,
    ) -> RepairHarnessResult:
        targets = tuple(self.resolver.resolve_issue(issue))
        unresolved = tuple(target for target in targets if target.resolution_status != "resolved")
        if unresolved:
            return RepairHarnessResult(
                issue_id=issue.issue_id,
                authority=authority,
                status="manual_required",
                targets=targets,
                reason="repair_target_unresolved_or_ambiguous",
            )
        if authority == "manual_only":
            return RepairHarnessResult(
                issue_id=issue.issue_id,
                authority=authority,
                status="manual_required",
                targets=targets,
                reason="repair_authority_manual_only",
            )
        if authority == "proposal_required":
            return RepairHarnessResult(
                issue_id=issue.issue_id,
                authority=authority,
                status="proposal_required",
                targets=targets,
                reason="approved_source_requires_versioned_proposal",
            )
        return RepairHarnessResult(
            issue_id=issue.issue_id,
            authority=authority,
            status="ready",
            targets=targets,
        )

    def run(
        self,
        issue: AuditIssueV2,
        *,
        authority: RepairAuthority,
        patch_provider: RepairPatchProvider,
        verifier: RepairVerifier,
        max_attempts: int = 2,
    ) -> RepairHarnessResult:
        """Run a bounded candidate loop; never mutate or publish source state."""

        diagnosis = self.diagnose(issue, authority=authority)
        if diagnosis.status != "ready":
            return diagnosis
        limit = max(1, min(int(max_attempts), 5))
        base = RepairCandidateState.from_context(self.context)
        before_hash = base.digest()
        attempts: list[RepairAttempt] = []
        previous: RepairVerification | None = None
        for attempt_number in range(1, limit + 1):
            patches: list[RepairPatch] = []
            try:
                patches = patch_provider(
                    attempt_number,
                    issue,
                    diagnosis.targets,
                    base,
                    previous,
                )
                candidate = _apply_candidate_patches(base, diagnosis.targets, patches)
            except Exception as exc:  # patch providers are contained extension boundaries
                attempts.append(
                    RepairAttempt(
                        attempt=attempt_number,
                        status="patch_rejected",
                        target_ids=tuple(
                            patch.target_id for patch in patches if isinstance(patch, RepairPatch)
                        ),
                        before_hash=before_hash,
                        details=(str(exc),),
                    )
                )
                previous = RepairVerification(passed=False, details=(str(exc),))
                continue
            try:
                verification = verifier(candidate, issue)
                if not isinstance(verification, RepairVerification):
                    raise TypeError("repair verifier must return RepairVerification")
            except Exception as exc:  # verifier is an extension boundary
                verification = RepairVerification(
                    passed=False,
                    details=(f"verification_error:{type(exc).__name__}:{exc}",),
                )
            previous = verification
            attempt_status: RepairAttemptStatus = (
                "verified" if verification.passed else "verification_failed"
            )
            attempts.append(
                RepairAttempt(
                    attempt=attempt_number,
                    status=attempt_status,
                    target_ids=tuple(patch.target_id for patch in patches),
                    before_hash=before_hash,
                    candidate_hash=candidate.digest(),
                    details=verification.details,
                )
            )
            if verification.passed:
                return replace(
                    diagnosis,
                    status="verified_candidate",
                    attempts=tuple(attempts),
                    candidate=candidate,
                )
        return replace(
            diagnosis,
            status="verification_failed",
            attempts=tuple(attempts),
            reason="bounded_attempts_exhausted",
        )


def materialize_text_artifact(value: Any) -> Any:
    """Convert model/dataclass containers into JSON-like text artifacts."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return materialize_text_artifact(value.model_dump(mode="json"))
        except TypeError:
            return materialize_text_artifact(value.model_dump())
    if is_dataclass(value):
        return materialize_text_artifact(asdict(value))  # type: ignore[arg-type]
    if isinstance(value, dict):
        return {str(key): materialize_text_artifact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [materialize_text_artifact(item) for item in value]
    if isinstance(value, set):
        return [materialize_text_artifact(item) for item in sorted(value, key=str)]
    if hasattr(value, "__dict__"):
        return materialize_text_artifact(vars(value))
    return str(value)


def repair_report_payload(value: Any) -> Any:
    """Remove volatile schema metadata from replayable repair evidence."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): repair_report_payload(item)
            for key, item in value.items()
            if key not in {"schema_version", "created_at"}
        }
    if isinstance(value, (list, tuple)):
        return [repair_report_payload(item) for item in value]
    return value


def locate_text_fragments(
    *,
    artifact: str,
    payload: Any,
    fragments: list[str],
    role: Literal["repair", "reference"],
    surface: str = "",
    chapter_number: int | None = None,
) -> dict[str, list[AuditLocator]]:
    """Locate every exact fragment as JSON Pointer + character span."""

    materialized = materialize_text_artifact(payload)
    wanted = [fragment for fragment in dict.fromkeys(fragments) if fragment]
    found: dict[str, list[AuditLocator]] = {fragment: [] for fragment in wanted}
    for path, text in _iter_text_leaves(materialized):
        for fragment in wanted:
            start = 0
            while True:
                index = text.find(fragment, start)
                if index < 0:
                    break
                end = index + len(fragment)
                found[fragment].append(
                    AuditLocator(
                        target_format="json_artifact",
                        role=role,
                        surface=surface or artifact,
                        confidence=1.0,
                        artifact=artifact,
                        json_pointer=path,
                        chapter_number=chapter_number,
                        quote=fragment,
                        text_hash=stable_repair_value_hash(text),
                        char_start=index,
                        char_end=end,
                    )
                )
                start = end
    return found


def diagnose_review_finding(
    finding: ReviewFinding,
    *,
    current_text: str,
    auto_repair_eligible: bool,
) -> dict[str, Any]:
    """Adapt a normalized prose finding to the universal repair harness."""

    locator_confidence = max(0.0, min(1.0, float(finding.confidence or 0.0)))
    if finding.paragraph_start > 0 or finding.evidence_quote:
        locator = AuditLocator(
            target_format="prose_text",
            role="repair",
            surface="chapter_text",
            confidence=locator_confidence,
            chapter_number=finding.chapter_number,
            paragraph_start=finding.paragraph_start,
            paragraph_end=finding.paragraph_end,
            quote=finding.evidence_quote,
            text_hash=finding.source_text_hash,
        )
    else:
        locator = AuditLocator(
            target_format="manual_only",
            role="repair",
            surface="chapter_text",
            confidence=0.0,
            chapter_number=finding.chapter_number,
            manual_review_reason="review_finding_has_no_resolved_text_anchor",
        )
    severity = normalize_severity(finding.severity)
    audit_severity = (
        severity
        if severity in {"critical", "high", "medium", "low"}
        else ("medium" if severity in {"major", "warning"} else "low")
    )
    issue = AuditIssueV2.model_validate(
        {
            "issue_id": finding.finding_id or "review_finding",
            "dimension": finding.dimension or "unknown",
            "issue_type": finding.issue_type,
            "severity": audit_severity,
            "blocking": finding.blocks_finalize,
            "summary": finding.summary or finding.repair_goal or "文本审查问题",
            "description": finding.repair_goal or finding.summary or "修复已定位的文本问题。",
            "evidence": [
                {
                    "quote": finding.evidence_quote,
                    "source": finding.source_module,
                    "locator": locator,
                    "confidence": locator.confidence,
                }
            ],
            "repair_targets": [locator],
            "reference_targets": [],
            "repair_intent": {
                "operation": "window_rewrite",
                "target_policy": "resolved_paragraph_window",
                "rationale": finding.repair_goal,
                "preserve": list(finding.must_preserve or []),
                "allowed_strategies": [finding.suggested_mode or "window"],
            },
            "postconditions": list(finding.postconditions or []),
            "metadata": {
                "source_module": finding.source_module,
                "review_mode": finding.review_mode,
                "review_round": finding.review_round,
            },
        }
    )
    authority: RepairAuthority = (
        "automatic_candidate" if auto_repair_eligible else "manual_only"
    )
    harness = TextRepairHarness(
        RepairResolverContext(
            texts={
                str(finding.chapter_number): current_text,
                "chapter_text": current_text,
            }
        )
    )
    result = harness.diagnose(issue, authority=authority).model_dump()
    result["issue"] = repair_report_payload(issue)
    return result


def _iter_text_leaves(value: Any, path: str = "") -> list[tuple[str, str]]:
    leaves: list[tuple[str, str]] = []
    if isinstance(value, str):
        if path:
            leaves.append((path, value))
        return leaves
    if isinstance(value, list):
        for index, item in enumerate(value):
            leaves.extend(_iter_text_leaves(item, f"{path}/{index}"))
        return leaves
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            leaves.extend(_iter_text_leaves(item, f"{path}/{escaped}"))
    return leaves


def _apply_candidate_patches(
    base: RepairCandidateState,
    targets: tuple[ResolvedRepairTarget, ...],
    patches: list[RepairPatch],
) -> RepairCandidateState:
    if not patches:
        raise ValueError("repair attempt produced no patches")
    target_map = {target.target_id: target for target in targets}
    if len({patch.target_id for patch in patches}) != len(patches):
        raise ValueError("duplicate target_id in repair attempt")
    for patch in patches:
        target = target_map.get(patch.target_id)
        if target is None:
            raise ValueError(f"unknown repair target: {patch.target_id}")
        if patch.expected_hash != target.current_hash:
            raise ValueError(f"stale repair precondition: {patch.target_id}")
    _reject_overlapping_span_targets(
        [target_map[patch.target_id] for patch in patches]
    )

    candidate = copy.deepcopy(base)
    ordered = sorted(
        patches,
        key=lambda patch: _patch_sort_key(target_map[patch.target_id]),
        reverse=True,
    )
    checked_containers: set[tuple[str, str, str]] = set()
    for patch in ordered:
        _apply_one_patch(
            candidate,
            target_map[patch.target_id],
            patch,
            checked_containers=checked_containers,
        )
    return candidate


def _reject_overlapping_span_targets(targets: list[ResolvedRepairTarget]) -> None:
    by_container: dict[tuple[str, str, str], list[tuple[int, int, str]]] = {}
    for target in targets:
        start = int(target.window.get("char_start", 0) or 0)
        end = int(target.window.get("char_end", 0) or 0)
        if end <= start:
            continue
        container = (
            target.target_format,
            target.locator.artifact or target.locator.task_type or target.surface,
            target.path,
        )
        by_container.setdefault(container, []).append((start, end, target.target_id))
    for spans in by_container.values():
        ordered = sorted(spans)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current[0] < previous[1]:
                raise ValueError(
                    "overlapping repair spans require one merged target: "
                    f"{previous[2]}, {current[2]}"
                )


def _patch_sort_key(target: ResolvedRepairTarget) -> tuple[str, str, int]:
    return (
        target.target_format,
        f"{target.surface}:{target.path}",
        int(target.window.get("char_start", 0) or 0),
    )


def _apply_one_patch(
    state: RepairCandidateState,
    target: ResolvedRepairTarget,
    patch: RepairPatch,
    *,
    checked_containers: set[tuple[str, str, str]],
) -> None:
    if target.target_format == "json_artifact":
        artifact = target.locator.artifact
        if artifact not in state.artifacts:
            raise ValueError(f"repair artifact missing: {artifact}")
        _replace_pointer_target(
            state.artifacts[artifact],
            target,
            patch.replacement,
            container_key=("json_artifact", artifact, target.path),
            checked_containers=checked_containers,
        )
        return
    if target.target_format == "prompt_response":
        key = target.locator.task_type or "response"
        if key not in state.prompt_responses:
            raise ValueError(f"prompt response missing: {key}")
        _replace_pointer_target(
            state.prompt_responses[key],
            target,
            patch.replacement,
            container_key=("prompt_response", key, target.path),
            checked_containers=checked_containers,
        )
        return
    if target.target_format == "prose_text":
        key = str(target.locator.chapter_number or target.locator.surface or "chapter_text")
        actual_key = key if key in state.texts else "chapter_text"
        state.texts[actual_key] = _replace_unique_text(
            state.texts.get(actual_key, ""), target, patch.replacement
        )
        return
    if target.target_format == "markdown_document":
        key = target.locator.surface or "document"
        actual_key = key if key in state.markdown_documents else "document"
        state.markdown_documents[actual_key] = _replace_unique_text(
            state.markdown_documents.get(actual_key, ""), target, patch.replacement
        )
        return
    raise ValueError(f"repair target format is not candidate-patchable: {target.target_format}")


def _replace_pointer_target(
    payload: Any,
    target: ResolvedRepairTarget,
    replacement: Any,
    *,
    container_key: tuple[str, str, str],
    checked_containers: set[tuple[str, str, str]],
) -> None:
    current = get_json_pointer(payload, target.path)
    if target.window.get("char_end"):
        if not isinstance(current, str):
            raise ValueError(f"span target is not text: {target.target_id}")
        container_hash = str(target.window.get("container_hash") or "")
        if container_key not in checked_containers:
            if container_hash and stable_repair_value_hash(current) != container_hash:
                raise ValueError(f"span container changed: {target.target_id}")
            checked_containers.add(container_key)
        start = int(target.window["char_start"])
        end = int(target.window["char_end"])
        old = current[start:end]
        if stable_repair_value_hash(old) != target.current_hash:
            raise ValueError(f"span old value changed: {target.target_id}")
        if not isinstance(replacement, str):
            raise TypeError("text span replacement must be a string")
        replacement = current[:start] + replacement + current[end:]
    elif stable_repair_value_hash(current) != target.current_hash:
        raise ValueError(f"target old value changed: {target.target_id}")
    _set_json_pointer(payload, target.path, replacement)


def _set_json_pointer(payload: Any, pointer: str, replacement: Any) -> None:
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer}")
    parts = [
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer.strip("/").split("/")
    ]
    current = payload
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError(f"JSON pointer parent is not a container: {pointer}")
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = replacement
    elif isinstance(current, dict):
        current[last] = replacement
    else:
        raise ValueError(f"JSON pointer target parent is not a container: {pointer}")


def _replace_unique_text(text: str, target: ResolvedRepairTarget, replacement: Any) -> str:
    if not isinstance(replacement, str):
        raise TypeError("text replacement must be a string")
    old = str(target.current_value or "")
    if stable_repair_value_hash(old) != target.current_hash:
        raise ValueError(f"text old-value precondition failed: {target.target_id}")
    if not old or text.count(old) != 1:
        raise ValueError(f"text target is no longer unique: {target.target_id}")
    return text.replace(old, replacement, 1)
