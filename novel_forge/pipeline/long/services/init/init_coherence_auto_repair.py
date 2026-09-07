"""Deterministic auto-repair for the most common init coherence issue types.

The v2 init coherence gate (``run_init_coherence_v2_gate``) emits issues with
``type`` labels such as ``chapter_number_mismatch``,
``state_axis_timeline_conflict`` and ``foreshadow_timeline_reversed``.
Many of these are *structurally* fixable: the right answer is implied by
the outline, the claim ledger, or simple narrative-time logic, and the
LLM-based repair path often fails in practice (rate limits, quota
exhaustion, schema drift).

This module provides a small, LLM-free auto-repair layer that runs BEFORE
the LLM patch repair. It is intentionally conservative: it only touches
``chapter_contracts``, only handles the four issue types below, and
records every applied change so the caller can:

* downgrade / remove the corresponding issues from the report,
* switch the gate verdict to ``accept`` (or ``ambiguous`` if residual
  issues remain),
* and emit a ``coherence_auto_repair_applied`` step event so the
  operator can see exactly what was rewritten.

Supported issue types
---------------------
1. ``chapter_number_mismatch`` — re-align a constraint / claim from a
   wrong chapter (declared in ``repair_scope.chapters``) to the
   canonical chapter in the claim ledger.
2. ``chapter_range_mismatch`` — same relocation logic for range metadata
   drift when the ledger has a narrower canonical chapter anchor.
3. ``payoff_id_duplication`` — keep the earliest scoped payoff id and
   deterministically rename later duplicates.
4. ``state_axis_first_occurrence_conflict`` — keep the earliest chapter
   as the canonical "first occurrence" and demote later ones to
   "continuation / validation".
5. ``state_axis_timeline_conflict`` — keep the earliest chapter as the
   canonical "irreversible completion" and demote later ones to
   "reinforcement / validation".
6. ``foreshadow_timeline_reversed`` — clamp invalid foreshadow chapter
   anchors to a chapter no later than the reveal chapter.
7. ``character_identity_conflict`` — split a shared payoff id when entries
   with different cognitive subjects were incorrectly grouped together.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Iterable

from novel_forge.common.constants import severity_at_least
from novel_forge.core.schemas.audit import AuditIssueV2
from novel_forge.core.schemas.repair import RepairCandidate, RepairVerificationBundle
from novel_forge.pipeline.long.services.init.init_coherence import (
    init_coherence_issue_id,
    normalize_artifact_key,
)
from novel_forge.pipeline.repair_orchestration.domains.initialization import (
    build_initialization_audit_issues,
    build_initialization_candidate,
    build_initialization_verification,
)

_log = logging.getLogger(__name__)


# Issue types this layer can fix deterministically.
DETERMINISTIC_ISSUE_TYPES: frozenset[str] = frozenset(
    {
        "chapter_number_mismatch",
        "chapter_range_mismatch",
        "payoff_id_duplication",
        "state_axis_first_occurrence_conflict",
        "state_axis_timeline_conflict",
        "foreshadow_timeline_reversed",
        "character_identity_conflict",
    }
)


# Maximum number of issues per type we are willing to process. Larger
# blasts are left for the LLM repair (it may spot a deeper pattern).
_MAX_FIXES_PER_TYPE = 32


# Field names on a chapter contract where ``cognitive_constraints`` and
# related state entries live. We only touch these fields.
_TOUCHABLE_FIELDS: frozenset[str] = frozenset(
    {
        "cognitive_constraints",
        "required_events",
        "allowed_changes",
        "forbidden_changes",
        "required_progressions",
        "allowed_progressions",
        "forbidden_progressions",
        "completion_criteria",
        "entry_state_requirements",
        "exit_state_targets",
        "future_leak_risks",
        "knowledge_ops",
        "promise_ops",
        "item_ops",
        "relationship_ops",
        "new_character_candidates",
    }
)


@dataclass
class AutoRepairFix:
    """One applied deterministic fix. Persisted as part of the report."""

    issue_id: str
    issue_type: str
    chapters_affected: list[int] = field(default_factory=list)
    fields_affected: list[str] = field(default_factory=list)
    description: str = ""
    removed_entry_keys: list[str] = field(default_factory=list)
    moved_entry_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_type": self.issue_type,
            "chapters_affected": list(self.chapters_affected),
            "fields_affected": list(self.fields_affected),
            "description": self.description,
            "removed_entry_keys": list(self.removed_entry_keys),
            "moved_entry_keys": list(self.moved_entry_keys),
        }


@dataclass
class AutoRepairResult:
    """Aggregate result from ``auto_repair_coherence_issues``."""

    payload: dict[str, Any]
    fixes: list[AutoRepairFix] = field(default_factory=list)
    resolved_issue_ids: list[str] = field(default_factory=list)
    unresolved_issue_ids: list[str] = field(default_factory=list)
    skipped_reason: str = ""
    audit_issues: list[AuditIssueV2] = field(default_factory=list)
    candidate: RepairCandidate | None = None
    verification: RepairVerificationBundle | None = None

    @property
    def has_fixes(self) -> bool:
        return bool(self.fixes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fix_count": len(self.fixes),
            "resolved_issue_ids": list(self.resolved_issue_ids),
            "unresolved_issue_ids": list(self.unresolved_issue_ids),
            "skipped_reason": self.skipped_reason,
            "fixes": [fix.to_dict() for fix in self.fixes],
            "candidate": (
                self.candidate.model_dump(mode="json") if self.candidate is not None else None
            ),
            "verification": (
                self.verification.model_dump(mode="json")
                if self.verification is not None
                else None
            ),
            "audit_issues": [issue.model_dump(mode="json") for issue in self.audit_issues],
        }


# ─────────────────────────────────────────────────────────────────────
# Handler registry — kept small on purpose.
# ─────────────────────────────────────────────────────────────────────

_ISSUE_HANDLERS: dict[str, "_HandlerFn"] = {}


def register_handler(issue_type: str, handler: "_HandlerFn") -> None:
    """Register or replace a handler for an issue type."""
    _ISSUE_HANDLERS[issue_type] = handler


def register_default_handlers() -> None:
    """Install the built-in deterministic handlers.

    Defined at the bottom of the module so the handler functions are
    available when this runs.
    """
    _ISSUE_HANDLERS.clear()
    _ISSUE_HANDLERS["chapter_number_mismatch"] = _fix_chapter_number_mismatch
    _ISSUE_HANDLERS["chapter_range_mismatch"] = _fix_chapter_range_mismatch
    _ISSUE_HANDLERS["payoff_id_duplication"] = _fix_payoff_id_duplication
    _ISSUE_HANDLERS["state_axis_first_occurrence_conflict"] = _fix_state_axis_first_occurrence_conflict
    _ISSUE_HANDLERS["state_axis_timeline_conflict"] = _fix_state_axis_timeline_conflict
    _ISSUE_HANDLERS["foreshadow_timeline_reversed"] = _fix_foreshadow_timeline_reversed
    _ISSUE_HANDLERS["character_identity_conflict"] = _fix_character_identity_conflict


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────


def auto_repair_coherence_issues(
    *,
    payload: dict[str, Any],
    report: dict[str, Any],
    artifact: str,
    claim_ledger: dict[str, Any] | None = None,
    allowed_artifact_keys: frozenset[str] | None = None,
) -> AutoRepairResult:
    """Run deterministic auto-repair over a v2 coherence report.

    Parameters
    ----------
    payload:
        The immutable artifact payload to repair (typically
        ``chapter_contracts``). The returned candidate is a deep copy; this
        function never mutates the caller's source.
    report:
        The v2 gate report containing ``issues`` and ``repair_scope``.
    artifact:
        The artifact name (``"chapter_contracts"`` for this stage).
    claim_ledger:
        Optional pre-loaded claim ledger. When provided,
        ``chapter_number_mismatch`` can re-align entries using the
        ledger's canonical chapter numbers. When absent, only
        structural fixes are applied.
    allowed_artifact_keys:
        Set of artifact keys the caller is willing to accept fixes for.
        If the issue's ``repair_scope`` only targets artifacts outside
        this set, the issue is left untouched. Defaults to
        ``{artifact}``.

    Returns
    -------
    AutoRepairResult
        The isolated candidate payload plus exact candidate evidence and the
        issue IDs the deterministic proposal attempted to resolve.
    """
    if not isinstance(payload, dict):
        return AutoRepairResult(
            payload={},
            skipped_reason="payload is not a dict",
        )
    if not isinstance(report, dict):
        return AutoRepairResult(
            payload=payload,
            skipped_reason="report is not a dict",
        )
    if allowed_artifact_keys is None:
        allowed_artifact_keys = frozenset({artifact})
    source_payload = deepcopy(payload)
    working_payload = deepcopy(payload)

    issues = report.get("issues") or []
    if not isinstance(issues, list):
        issues = []

    fixes: list[AutoRepairFix] = []
    resolved: list[str] = []
    unresolved: list[str] = []

    per_type_count: dict[str, int] = {}

    for raw_issue in issues:
        if not isinstance(raw_issue, dict):
            continue
        issue_id = init_coherence_issue_id(raw_issue)
        issue_type = str(raw_issue.get("type") or "").strip()
        if not issue_type:
            continue
        if issue_type not in DETERMINISTIC_ISSUE_TYPES:
            continue
        if not _scope_targets_artifact(
            raw_issue, allowed_artifact_keys=allowed_artifact_keys
        ):
            unresolved.append(issue_id)
            continue
        per_type_count[issue_type] = per_type_count.get(issue_type, 0) + 1
        if per_type_count[issue_type] > _MAX_FIXES_PER_TYPE:
            _log.info(
                "auto_repair_skipped_too_many | type=%s | count=%d | limit=%d",
                issue_type,
                per_type_count[issue_type],
                _MAX_FIXES_PER_TYPE,
            )
            unresolved.append(issue_id)
            continue

        handler = _ISSUE_HANDLERS.get(issue_type)
        if handler is None:
            unresolved.append(issue_id)
            continue

        try:
            fix = handler(
                issue=raw_issue,
                payload=working_payload,
                claim_ledger=claim_ledger,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "auto_repair_handler_failed | type=%s | id=%s | error=%s",
                issue_type,
                issue_id,
                exc,
            )
            unresolved.append(issue_id)
            continue

        if fix is not None:
            fixes.append(fix)
            resolved.append(issue_id)
        else:
            unresolved.append(issue_id)

    audit_issues = build_initialization_audit_issues(
        artifact=artifact,
        payload=source_payload,
        issues=[item for item in issues if isinstance(item, dict)],
    )
    candidate = build_initialization_candidate(
        artifact=artifact,
        baseline=source_payload,
        candidate_payload=working_payload,
        issues=audit_issues,
        version=1,
        origin="deterministic",
        stage="deterministic_coherence",
    )
    return AutoRepairResult(
        payload=working_payload,
        fixes=fixes,
        resolved_issue_ids=resolved,
        unresolved_issue_ids=unresolved,
        audit_issues=audit_issues,
        candidate=candidate,
    )


def verify_auto_repair_candidate(
    result: AutoRepairResult,
    *,
    report: dict[str, Any],
    artifact: str,
    claim_ledger: dict[str, Any] | None = None,
    allowed_artifact_keys: frozenset[str] | None = None,
) -> bool:
    """Re-run the same deterministic issue handlers as postcondition checks."""

    if result.candidate is None or not result.has_fixes:
        return False
    if allowed_artifact_keys is None:
        allowed_artifact_keys = frozenset({artifact})
    issue_by_id = {
        init_coherence_issue_id(item): item
        for item in report.get("issues", []) or []
        if isinstance(item, dict)
    }
    residual: list[str] = []
    details: list[str] = []
    for issue_id in result.resolved_issue_ids:
        issue = issue_by_id.get(issue_id)
        if issue is None:
            residual.append(issue_id)
            details.append(f"{issue_id}: original issue missing during verification")
            continue
        if not _scope_targets_artifact(issue, allowed_artifact_keys=allowed_artifact_keys):
            residual.append(issue_id)
            details.append(f"{issue_id}: target scope no longer matches artifact")
            continue
        issue_type = str(issue.get("type") or "").strip()
        handler = _ISSUE_HANDLERS.get(issue_type)
        if handler is None:
            residual.append(issue_id)
            details.append(f"{issue_id}: original deterministic validator unavailable")
            continue
        probe = deepcopy(result.payload)
        try:
            repeated_fix = handler(
                issue=issue,
                payload=probe,
                claim_ledger=claim_ledger,
            )
        except Exception as exc:  # noqa: BLE001
            residual.append(issue_id)
            details.append(f"{issue_id}: verifier failed: {type(exc).__name__}: {exc}")
            continue
        if repeated_fix is not None:
            residual.append(issue_id)
            details.append(f"{issue_id}: original postcondition remains actionable")

    passed = not residual and bool(result.resolved_issue_ids)
    resolved_set = set(result.resolved_issue_ids)
    result.verification = build_initialization_verification(
        candidate=result.candidate,
        issues=[issue for issue in result.audit_issues if issue.issue_id in resolved_set],
        passed=passed,
        validator_id="init_deterministic_postcondition_v1",
        errors=details,
        regression_issue_ids=[f"residual:{issue_id}" for issue_id in residual],
    )
    return bool(result.verification.passed)


def build_downgraded_report(
    report: dict[str, Any],
    *,
    resolved_issue_ids: Iterable[str],
    min_severity: str = "high",
) -> dict[str, Any]:
    """Return a shallow-copied report with resolved issues removed.

    If ``resolved_issue_ids`` covers every ``high``/``critical`` issue
    the verdict is downgraded to ``accept``; otherwise it is left as
    ``ambiguous`` (the gate should still be allowed to continue, but the
    operator will see the residual).
    """
    resolved_set = {str(issue_id) for issue_id in resolved_issue_ids if issue_id}
    if not resolved_set:
        return report

    raw_issues = report.get("issues") or []
    remaining: list[Any] = []
    for issue in raw_issues if isinstance(raw_issues, list) else []:
        if not isinstance(issue, dict):
            remaining.append(issue)
            continue
        if init_coherence_issue_id(issue) in resolved_set:
            continue
        remaining.append(issue)

    new_report = dict(report)
    new_report["issues"] = remaining
    if remaining:
        blocking_left = any(
            severity_at_least(
                str(issue.get("severity") or "medium").strip().lower(),
                min_severity,
            )
            for issue in remaining
            if isinstance(issue, dict)
        )
        if blocking_left:
            new_report["verdict"] = "ambiguous"
        else:
            new_report["verdict"] = "accept"
    else:
        blocking_left = False
        new_report["verdict"] = "accept"
    new_report["blocked"] = blocking_left
    new_report["summary"] = _build_downgraded_summary(
        report.get("summary"),
        remaining=len(remaining),
        resolved=len(resolved_set),
    )
    new_report["auto_repair_resolved_issue_ids"] = sorted(resolved_set)
    return new_report


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _build_downgraded_summary(
    raw_summary: Any, *, remaining: int, resolved: int
) -> str:
    base = str(raw_summary or "").strip()
    if base:
        return f"{base}（自动修复 {resolved} 个问题，剩余 {remaining} 个）"
    return f"自动修复 {resolved} 个问题，剩余 {remaining} 个"


def _scope_targets_artifact(
    issue: dict[str, Any], *, allowed_artifact_keys: frozenset[str]
) -> bool:
    scopes = issue.get("repair_scope")
    if not isinstance(scopes, list):
        # Some issues have no explicit repair_scope; assume they target
        # the default artifact. The caller has already validated this.
        return True
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        artifact_name = normalize_artifact_key(scope.get("artifact") or "")
        if artifact_name in allowed_artifact_keys:
            return True
    return False


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_chapter_numbers(scope: dict[str, Any]) -> list[int]:
    raw = scope.get("chapters")
    if raw is None:
        raw = scope.get("chapter_numbers")
    if isinstance(raw, (list, tuple)):
        return [n for n in (_coerce_int(v) for v in raw) if n is not None and n > 0]
    if isinstance(raw, int):
        return [raw] if raw > 0 else []
    chapter_range = scope.get("chapter_range")
    if isinstance(chapter_range, dict):
        start = _coerce_int(chapter_range.get("start"))
        end = _coerce_int(chapter_range.get("end"))
        result = [n for n in (start, end) if n is not None and n > 0]
        return list(dict.fromkeys(result))
    return []


def _extract_field_names(scope: dict[str, Any]) -> list[str]:
    raw = scope.get("fields")
    if raw is None:
        raw = scope.get("field_whitelist")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def _chapter_contracts_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("chapter_contracts")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _chapter_index(
    items: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}
    for item in items:
        number = _coerce_int(item.get("chapter_number"))
        if number is not None and number > 0 and number not in index:
            index[number] = item
    return index


def _entry_key(entry: Any) -> str:
    """Stable key for matching list entries across chapters."""
    if isinstance(entry, dict):
        claim_id = entry.get("claim_id")
        if claim_id:
            return f"claim:{claim_id}"
        text = entry.get("text") or entry.get("event") or entry.get("description")
        if text:
            return f"text:{text}"
    if isinstance(entry, str):
        return f"str:{entry}"
    return f"repr:{entry!r}"


def _entry_text_value(entry: dict[str, Any]) -> tuple[str, str]:
    for key in ("claim_text", "text", "event", "description", "state_after", "summary"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return key, value.strip()
    return "", ""


def _set_demoted_text(entry: dict[str, Any], *, marker: str) -> bool:
    key, text = _entry_text_value(entry)
    if not key or not text:
        return False
    label = "后续验证" if marker == "[validation]" else "后续延续"
    prefix = f"【{label}】"
    if text.startswith(prefix):
        return False
    entry[key] = f"{prefix}{text}"
    return True


def _coerce_positive_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[int] = []
    for raw in values:
        number = _coerce_int(raw)
        if number is not None and number > 0 and number not in result:
            result.append(number)
    return result


def _claim_canonical_chapters(claim: dict[str, Any]) -> list[int]:
    chapters = _coerce_positive_int_list(claim.get("chapter_numbers"))
    if chapters:
        return chapters
    chapters = _coerce_positive_int_list(claim.get("chapter_number"))
    if chapters:
        return chapters
    chapter_range = claim.get("chapter_range")
    if isinstance(chapter_range, dict):
        return _coerce_positive_int_list(
            [chapter_range.get("start"), chapter_range.get("end")]
        )
    return []


def _is_placeholder_entry(entry: Any) -> bool:
    """A list entry that contains no information — safe to drop.

    A dict with only a ``claim_id`` and no descriptive text is treated
    as a placeholder when we have no way to recover its content
    (typically: no claim ledger available).
    """
    if entry is None:
        return True
    if isinstance(entry, str):
        return not entry.strip()
    if isinstance(entry, dict):
        for key in ("text", "event", "description", "subject", "object"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return False
        return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────


_HandlerFn = Any


def _fix_chapter_number_mismatch(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    """Move a mis-located ``cognitive_constraints`` entry to its canonical chapter.

    Strategy
    --------
    For each entry in the wrong chapter (the chapters listed in the
    issue's ``repair_scope``):

    1. Look at the entry's own ``claim_id`` (NOT the issue's
       ``candidate_ids`` — those are conflict candidate IDs).
    2. Look the claim up in the ledger to discover its canonical
       ``chapter_numbers``.
    3. If the canonical chapters don't include the current chapter,
       move the entry to the canonical chapter.
    4. If the canonical chapters DO include the current chapter, keep
       the entry (it is legitimately there).
    5. If the ledger has no record, treat the entry as a placeholder
       and drop it from the wrong chapter.

    We only touch the fields listed in ``repair_scope.fields`` (or
    ``cognitive_constraints`` by default).
    """
    issue_id = init_coherence_issue_id(issue)

    scope_chapters: list[int] = []
    scope_fields: list[str] = []
    for scope in issue.get("repair_scope") or []:
        if not isinstance(scope, dict):
            continue
        scope_chapters.extend(_extract_chapter_numbers(scope))
        scope_fields.extend(_extract_field_names(scope))
    scope_chapters = sorted(set(scope_chapters))
    if not scope_fields:
        scope_fields = ["cognitive_constraints"]
    scope_fields = [field for field in scope_fields if field in _TOUCHABLE_FIELDS]
    if not scope_fields:
        scope_fields = ["cognitive_constraints"]

    items = _chapter_contracts_items(payload)
    index = _chapter_index(items)
    if not index:
        return None

    ledger_claims = _ledger_claims_by_id(claim_ledger)

    removed_keys: list[str] = []
    moved_keys: list[str] = []
    chapters_touched: set[int] = set()
    fields_touched: set[str] = set()

    for wrong_chapter in scope_chapters:
        wrong_item = index.get(wrong_chapter)
        if wrong_item is None:
            continue
        for contract_field in scope_fields:
            entries = wrong_item.get(contract_field)
            if not isinstance(entries, list):
                continue
            keep: list[Any] = []
            for entry in entries:
                key = _entry_key(entry)
                if not isinstance(entry, dict):
                    keep.append(entry)
                    continue
                entry_claim_id = entry.get("claim_id")
                canonical: list[int] = []
                if isinstance(entry_claim_id, str) and entry_claim_id in ledger_claims:
                    claim = ledger_claims[entry_claim_id]
                    canonical = _claim_canonical_chapters(claim)
                elif not ledger_claims:
                    fallback = entry.get("chapter_numbers")
                    canonical = _coerce_positive_int_list(fallback)
                    if not canonical:
                        canonical = _coerce_positive_int_list(entry.get("chapter_number"))

                if canonical and wrong_chapter in canonical:
                    keep.append(entry)
                    continue

                if canonical:
                    target = next(
                        (
                            number
                            for number in canonical
                            if number != wrong_chapter and number in index
                        ),
                        None,
                    )
                    if target is None:
                        entry["auto_repair_could_not_relocate"] = True
                        entry["auto_repair_issue_id"] = issue_id
                        keep.append(entry)
                        continue
                    target_item = index[target]
                    target_entries = target_item.get(contract_field)
                    if not isinstance(target_entries, list):
                        target_entries = []
                    duplicate_in_target = any(
                        _entry_key(existing) == key for existing in target_entries
                    )
                    if duplicate_in_target:
                        removed_keys.append(key)
                    else:
                        if len(canonical) > 1:
                            entry["auto_repair_multi_chapter_claim"] = True
                        target_entries.append(entry)
                        target_item[contract_field] = target_entries
                        chapters_touched.add(target)
                        moved_keys.append(key)
                    chapters_touched.add(wrong_chapter)
                    fields_touched.add(contract_field)
                    continue

                if _is_placeholder_entry(entry):
                    removed_keys.append(key)
                    chapters_touched.add(wrong_chapter)
                    fields_touched.add(contract_field)
                    continue

                entry["auto_repair_could_not_relocate"] = True
                entry["auto_repair_issue_id"] = issue_id
                keep.append(entry)
            if keep != entries:
                wrong_item[contract_field] = keep
                chapters_touched.add(wrong_chapter)
                fields_touched.add(contract_field)

    if not removed_keys and not moved_keys:
        return None

    description = (
        f"chapter_number_mismatch：{len(removed_keys)} 个条目从错误章节移除，"
        f"{len(moved_keys)} 个条目迁移到正本章节。"
    )
    return AutoRepairFix(
        issue_id=issue_id,
        issue_type="chapter_number_mismatch",
        chapters_affected=sorted(chapters_touched),
        fields_affected=sorted(fields_touched) or scope_fields,
        description=description,
        removed_entry_keys=removed_keys,
        moved_entry_keys=moved_keys,
    )


def _fix_chapter_range_mismatch(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    fix = _fix_chapter_number_mismatch(
        issue=issue,
        payload=payload,
        claim_ledger=claim_ledger,
    )
    if fix is None:
        return None
    fix.issue_type = "chapter_range_mismatch"
    fix.description = fix.description.replace(
        "chapter_number_mismatch",
        "chapter_range_mismatch",
        1,
    )
    return fix


def _fix_payoff_id_duplication(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    _ = claim_ledger
    issue_id = init_coherence_issue_id(issue)
    chapters: list[int] = []
    fields: list[str] = []
    for scope in issue.get("repair_scope") or []:
        if not isinstance(scope, dict):
            continue
        chapters.extend(_extract_chapter_numbers(scope))
        fields.extend(_extract_field_names(scope))
    fields = [field for field in {field for field in fields if field in _TOUCHABLE_FIELDS}]
    if not fields:
        fields = ["promise_ops", "knowledge_ops", "required_events", "completion_criteria"]

    items = _chapter_contracts_items(payload)
    index = _chapter_index(items)
    scoped_chapters = sorted(set(chapters)) if chapters else sorted(index)
    existing_ids: set[str] = set()
    records: list[tuple[int, str, int, dict[str, Any], str]] = []
    for item in items:
        chapter = _coerce_int(item.get("chapter_number"))
        if chapter is None:
            continue
        for field_name in _TOUCHABLE_FIELDS:
            entries = item.get(field_name)
            if not isinstance(entries, list):
                continue
            for entry_index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                payoff_id = str(entry.get("payoff_id") or "").strip()
                if not payoff_id:
                    continue
                existing_ids.add(payoff_id)
                if chapter in scoped_chapters and field_name in fields:
                    records.append((chapter, field_name, entry_index, entry, payoff_id))

    grouped: dict[str, list[tuple[int, str, int, dict[str, Any], str]]] = {}
    for record in records:
        grouped.setdefault(record[4], []).append(record)

    changed: list[str] = []
    chapters_touched: set[int] = set()
    fields_touched: set[str] = set()
    for payoff_id, group in grouped.items():
        if len(group) <= 1:
            continue
        for ordinal, (chapter, field_name, entry_index, entry, _old_id) in enumerate(
            sorted(group, key=lambda item: (item[0], item[1], item[2])),
            start=1,
        ):
            if ordinal == 1:
                continue
            new_id = _unique_payoff_id(
                payoff_id,
                chapter=chapter,
                field_name=field_name,
                entry_index=entry_index,
                existing_ids=existing_ids,
            )
            entry["auto_repair_original_payoff_id"] = payoff_id
            entry["auto_repair_issue_id"] = issue_id
            entry["payoff_id"] = new_id
            existing_ids.add(new_id)
            changed.append(f"{payoff_id}->{new_id}")
            chapters_touched.add(chapter)
            fields_touched.add(field_name)

    if not changed:
        return None
    return AutoRepairFix(
        issue_id=issue_id,
        issue_type="payoff_id_duplication",
        chapters_affected=sorted(chapters_touched),
        fields_affected=sorted(fields_touched),
        description=f"payoff_id_duplication：重命名 {len(changed)} 个重复 payoff_id。",
        moved_entry_keys=changed,
    )


def _unique_payoff_id(
    payoff_id: str,
    *,
    chapter: int,
    field_name: str,
    entry_index: int,
    existing_ids: set[str],
) -> str:
    base = re.sub(r"[^0-9A-Za-z]+", "_", payoff_id).strip("_") or "payoff"
    candidate = f"{base}_ch{chapter:03d}_{entry_index + 1:02d}"
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}_ch{chapter:03d}_{entry_index + 1:02d}_{suffix}"
        suffix += 1
    return candidate


def _entry_cognitive_subjects(entry: dict[str, Any]) -> tuple[str, ...]:
    raw = (
        entry.get("cognitive_subjects")
        or entry.get("subjects")
        or entry.get("subject_entities")
        or entry.get("characters")
        or []
    )
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


def _fix_character_identity_conflict(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    """Split a shared payoff id across incompatible subject groups.

    This deliberately does not rename characters. Canonical identity correction
    needs entity-registry evidence; this handler only repairs the unsafe
    grouping that made two subject sets claim the same payoff lineage.
    """
    _ = claim_ledger
    issue_id = init_coherence_issue_id(issue)
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    payoff_id = str((evidence or {}).get("payoff_id") or issue.get("payoff_id") or "").strip()
    if not payoff_id:
        return None

    items = _chapter_contracts_items(payload)
    records: list[tuple[int, str, int, dict[str, Any], tuple[str, ...]]] = []
    existing_ids: set[str] = set()
    for item in items:
        chapter = _coerce_int(item.get("chapter_number"))
        if chapter is None:
            continue
        for field_name in _TOUCHABLE_FIELDS:
            entries = item.get(field_name)
            if not isinstance(entries, list):
                continue
            for entry_index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                current_payoff = str(entry.get("payoff_id") or "").strip()
                if current_payoff:
                    existing_ids.add(current_payoff)
                if current_payoff != payoff_id:
                    continue
                subjects = _entry_cognitive_subjects(entry)
                if not subjects:
                    continue
                records.append((chapter, field_name, entry_index, entry, subjects))

    grouped: dict[tuple[str, ...], list[tuple[int, str, int, dict[str, Any], tuple[str, ...]]]] = {}
    for record in records:
        grouped.setdefault(record[4], []).append(record)
    if len(grouped) <= 1:
        return None

    changed: list[str] = []
    chapters_touched: set[int] = set()
    fields_touched: set[str] = set()
    for ordinal, (subjects, group) in enumerate(
        sorted(
            grouped.items(),
            key=lambda item: (item[1][0][0], item[1][0][1], item[1][0][2], item[0]),
        ),
        start=1,
    ):
        if ordinal == 1:
            continue
        for chapter, field_name, entry_index, entry, _subjects in group:
            new_id = _unique_payoff_id(
                payoff_id,
                chapter=chapter,
                field_name=field_name,
                entry_index=entry_index,
                existing_ids=existing_ids,
            )
            entry["auto_repair_original_payoff_id"] = payoff_id
            entry["auto_repair_issue_id"] = issue_id
            entry["auto_repair_identity_subjects"] = list(subjects)
            entry["payoff_id"] = new_id
            existing_ids.add(new_id)
            changed.append(f"{payoff_id}->{new_id}")
            chapters_touched.add(chapter)
            fields_touched.add(field_name)

    if not changed:
        return None
    return AutoRepairFix(
        issue_id=issue_id,
        issue_type="character_identity_conflict",
        chapters_affected=sorted(chapters_touched),
        fields_affected=sorted(fields_touched),
        description=(
            "character_identity_conflict：同一 payoff_id 下存在多个 cognitive_subjects "
            f"组合，已拆分 {len(changed)} 个冲突条目。"
        ),
        moved_entry_keys=changed,
    )


def _fix_state_axis_first_occurrence_conflict(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    """Demote later "first occurrence" claims to continuation.

    The fix: locate the cognitive_constraints entries whose
    ``chapter_number`` matches the conflicting chapters, then prepend a
    marker that signals ``[continuation]`` to the claim text in all but
    the earliest chapter. This keeps the claim auditable while no longer
    triggering the "first occurrence" check.
    """
    return _demote_state_axis_claims(
        issue=issue,
        payload=payload,
        claim_ledger=claim_ledger,
        marker="[continuation]",
        description_zh="首次发生节点冲突：保留最早章节为首次发生，其他章节标记为延续。",
    )


def _fix_state_axis_timeline_conflict(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    """Demote later "irreversible completion" claims to validation.

    Same pattern as the first-occurrence handler, but for completion
    markers. The earliest chapter keeps the original claim text; later
    chapters get a ``[validation]`` marker prefix.
    """
    return _demote_state_axis_claims(
        issue=issue,
        payload=payload,
        claim_ledger=claim_ledger,
        marker="[validation]",
        description_zh="不可逆事件重复完成：将晚于首次完成的节点标记为验证/确认阶段。",
    )


def _demote_state_axis_claims(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
    marker: str,
    description_zh: str,
) -> AutoRepairFix | None:
    issue_id = init_coherence_issue_id(issue)

    chapters: list[int] = []
    fields: list[str] = []
    for scope in issue.get("repair_scope") or []:
        if not isinstance(scope, dict):
            continue
        chapters.extend(_extract_chapter_numbers(scope))
        fields.extend(_extract_field_names(scope))
    chapters = sorted(set(chapters))
    fields = [f for f in {field for field in fields if field in _TOUCHABLE_FIELDS}]
    if not fields:
        fields = ["cognitive_constraints", "required_events"]

    if len(chapters) < 2:
        return None

    earliest, later = chapters[0], chapters[1:]
    items = _chapter_contracts_items(payload)
    index = _chapter_index(items)

    demoted_chapters: set[int] = set()
    fields_affected: set[str] = set()
    moved_keys: list[str] = []

    for chapter in later:
        item = index.get(chapter)
        if item is None:
            continue
        for contract_field in fields:
            entries = item.get(contract_field)
            if not isinstance(entries, list) or not entries:
                continue
            touched = False
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("demoted_by") == marker:
                    continue
                if not _set_demoted_text(entry, marker=marker):
                    continue
                entry["demoted_by"] = marker
                entry["demoted_for_issue_id"] = issue_id
                entry["demoted_in_favor_of_chapter"] = earliest
                touched = True
                moved_keys.append(_entry_key(entry))
            if touched:
                fields_affected.add(contract_field)
                demoted_chapters.add(chapter)

    if not demoted_chapters:
        return None

    return AutoRepairFix(
        issue_id=issue_id,
        issue_type=issue.get("type", ""),
        chapters_affected=sorted({earliest, *demoted_chapters}),
        fields_affected=sorted(fields_affected),
        description=f"{description_zh} earliest={earliest} demoted={sorted(demoted_chapters)}",
        removed_entry_keys=[],
        moved_entry_keys=moved_keys,
    )


def _fix_foreshadow_timeline_reversed(
    *,
    issue: dict[str, Any],
    payload: dict[str, Any],
    claim_ledger: dict[str, Any] | None,
) -> AutoRepairFix | None:
    """Drop foreshadow chapters that occur AFTER the reveal chapter.

    We look at the affected chapter's ``knowledge_ops`` and
    ``promise_ops`` entries; for any entry whose ``foreshadow_chapter``
    is greater than the chapter where it lives, we either drop the
    foreshadow or rewrite the entry to ``foreshadow_chapter =
    reveal_chapter - 1`` (clamped to >= 1).
    """
    issue_id = init_coherence_issue_id(issue)

    chapters: list[int] = []
    fields: list[str] = []
    for scope in issue.get("repair_scope") or []:
        if not isinstance(scope, dict):
            continue
        chapters.extend(_extract_chapter_numbers(scope))
        fields.extend(_extract_field_names(scope))
    chapters = sorted(set(chapters))
    if not fields:
        fields = ["knowledge_ops", "promise_ops"]
    fields = [f for f in {field for field in fields if field in _TOUCHABLE_FIELDS}]
    if not fields:
        fields = ["knowledge_ops", "promise_ops"]

    items = _chapter_contracts_items(payload)
    index = _chapter_index(items)

    removed_keys: list[str] = []
    moved_keys: list[str] = []
    chapters_touched: set[int] = set()
    fields_touched: set[str] = set()

    for chapter in chapters:
        item = index.get(chapter)
        if item is None:
            continue
        for contract_field in fields:
            entries = item.get(contract_field)
            if not isinstance(entries, list) or not entries:
                continue
            keep: list[Any] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    keep.append(entry)
                    continue
                foreshadow = entry.get("foreshadow_chapter")
                changed = False
                if foreshadow is not None:
                    parsed = _coerce_int(foreshadow)
                    if parsed is not None and parsed >= chapter:
                        entry["foreshadow_chapter"] = max(1, chapter - 1)
                        entry["foreshadow_chapter_clamped_by_issue_id"] = issue_id
                        changed = True
                if "foreshadow_chapters" in entry:
                    original = _coerce_positive_int_list(entry.get("foreshadow_chapters"))
                    clamped_values: list[int] = []
                    for value in original:
                        new_value = max(1, chapter - 1) if value >= chapter else value
                        if new_value not in clamped_values:
                            clamped_values.append(new_value)
                    if clamped_values != original:
                        entry["foreshadow_chapters"] = sorted(clamped_values)
                        entry["foreshadow_chapters_clamped_by_issue_id"] = issue_id
                        changed = True
                if changed:
                    moved_keys.append(_entry_key(entry))
                    chapters_touched.add(chapter)
                    fields_touched.add(contract_field)
                keep.append(entry)
            item[contract_field] = keep

    if not moved_keys and not removed_keys:
        return None

    return AutoRepairFix(
        issue_id=issue_id,
        issue_type="foreshadow_timeline_reversed",
        chapters_affected=sorted(chapters_touched),
        fields_affected=sorted(fields_touched) or fields,
        description=(
            f"foreshadow_timeline_reversed：{len(moved_keys)} 个伏笔被重新对齐到揭示章节之前。"
        ),
        removed_entry_keys=removed_keys,
        moved_entry_keys=moved_keys,
    )


def _ledger_claims_by_id(claim_ledger: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(claim_ledger, dict):
        return {}
    by_id = claim_ledger.get("claims_by_id")
    if not isinstance(by_id, dict):
        return {}
    return {
        str(key): value
        for key, value in by_id.items()
        if isinstance(value, dict)
    }


# ─────────────────────────────────────────────────────────────────────
# Diagnostic helpers (used by the orchestrator and the error message).
# ─────────────────────────────────────────────────────────────────────


def summarize_unresolved_issues(
    report: dict[str, Any],
    *,
    max_issues: int = 4,
) -> str:
    """Render a short, human-readable list of unresolved blocking issues.

    Used to enrich the ``InitCoherenceError`` message so the operator
    can see exactly what to fix manually.
    """
    issues = report.get("issues") or []
    if not isinstance(issues, list):
        return ""
    blocking = [
        issue
        for issue in issues
        if isinstance(issue, dict)
        and str(issue.get("severity") or "").strip().lower() in {"high", "critical"}
    ]
    if not blocking:
        blocking = [
            issue for issue in issues if isinstance(issue, dict)
        ]
    if not blocking:
        return ""

    lines: list[str] = []
    for index, issue in enumerate(blocking[:max_issues], start=1):
        issue_id = str(issue.get("id") or "")
        issue_type = str(issue.get("type") or "")
        description = str(issue.get("description") or "").strip()
        evidence = str(issue.get("evidence") or "").strip()
        chapters = _collect_chapter_numbers(issue)
        header_bits = [f"#{index}"]
        if issue_type:
            header_bits.append(f"type={issue_type}")
        if issue_id:
            header_bits.append(f"id={issue_id}")
        if chapters:
            header_bits.append(f"chapters={chapters}")
        header = " | ".join(header_bits)
        if description:
            lines.append(f"- {header}\n  desc: {description}")
        if evidence:
            # Truncate long evidence so the message stays scannable.
            truncated = evidence if len(evidence) <= 240 else evidence[:237] + "..."
            lines.append(f"  evidence: {truncated}")
    remaining = max(0, len(blocking) - max_issues)
    if remaining:
        lines.append(f"- ...{remaining} more blocking issue(s) omitted")
    return "\n".join(lines)


def _collect_chapter_numbers(issue: dict[str, Any]) -> list[int]:
    chapters: set[int] = set()
    for scope in issue.get("repair_scope") or []:
        if not isinstance(scope, dict):
            continue
        for chapter in _extract_chapter_numbers(scope):
            chapters.add(chapter)
    return sorted(n for n in chapters if n > 0)


register_default_handlers()
