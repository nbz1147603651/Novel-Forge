"""Shared admission and recheck rules for outline-alignment review."""

from __future__ import annotations

from typing import Any

from novel_forge.core.review.review_contracts import (
    build_ticket_verification_results,
    compile_repair_tickets_from_findings,
)
from novel_forge.core.schemas.review import ReviewFinding


def alignment_uses_structured_contract(report: Any | None) -> bool:
    """Return whether a report was normalized through the evidence contract."""

    return int(getattr(report, "review_contract_version", 0) or 0) >= 1


def verified_alignment_blockers(report: Any | None) -> list[ReviewFinding]:
    """Return blockers that passed local source/evidence admission."""

    if report is None or not alignment_uses_structured_contract(report):
        return []
    verified = getattr(report, "verified_blocking_findings", None)
    if isinstance(verified, list):
        return list(verified)
    findings = list(getattr(report, "review_findings", []) or [])
    return [finding for finding in findings if bool(getattr(finding, "blocks_finalize", False))]


def alignment_main_gap_count(report: Any | None) -> int:
    """Count admitted blockers, falling back to legacy flat reports."""

    if alignment_uses_structured_contract(report):
        return len(verified_alignment_blockers(report))
    return len(list(getattr(report, "missing_main_points", []) or []))


def adjudicate_targeted_alignment_recheck(
    *,
    previous_report: Any,
    candidate_report: Any,
    before_text: str,
    after_text: str,
    repair_round: int,
) -> Any:
    """Close old tickets without letting rechecks invent an expanding backlog.

    A targeted recheck may keep an original source reference blocking. A new
    blocker is admitted only when the repair directly introduced a quoted
    contradiction. Other newly surfaced opinions remain visible observations
    but cannot gain blocking force merely because another review round ran.
    """

    if not alignment_uses_structured_contract(candidate_report):
        return candidate_report
    findings = list(getattr(candidate_report, "review_findings", []) or [])
    previous_refs = {
        str((finding.metadata or {}).get("source_ref", "") or "").strip()
        for finding in verified_alignment_blockers(previous_report)
        if str((finding.metadata or {}).get("source_ref", "") or "").strip()
    }
    adjudicated: list[ReviewFinding] = []
    for finding in findings:
        metadata = dict(finding.metadata or {})
        source_ref = str(metadata.get("source_ref", "") or "").strip()
        coverage = str(metadata.get("coverage_status", "") or "").strip()
        directly_introduced = bool(
            source_ref
            and coverage == "conflict"
            and finding.evidence_quote
            and finding.evidence_quote in after_text
            and finding.evidence_quote not in before_text
        )
        remains_in_scope = bool(source_ref and source_ref in previous_refs) or directly_introduced
        update: dict[str, Any] = {
            "review_mode": "targeted_recheck",
            "review_round": max(2, repair_round + 1),
        }
        if finding.blocks_finalize and not remains_in_scope:
            metadata.update(
                {
                    "targeted_recheck_status": "new_observation",
                    "targeted_recheck_reason": (
                        "new finding was not an original ticket and has no directly "
                        "introduced quoted contradiction"
                    ),
                }
            )
            update.update(
                {
                    "blocks_finalize": False,
                    "severity": "medium",
                    "metadata": metadata,
                }
            )
        elif directly_introduced and source_ref not in previous_refs:
            metadata["targeted_recheck_status"] = "verified_new_regression"
            update["metadata"] = metadata
        else:
            metadata["targeted_recheck_status"] = "original_ticket_recheck"
            update["metadata"] = metadata
        adjudicated.append(finding.model_copy(update=update))

    remaining_blockers = [finding for finding in adjudicated if finding.blocks_finalize]
    tickets = compile_repair_tickets_from_findings(remaining_blockers)
    previous_tickets = list(getattr(previous_report, "repair_tickets", []) or [])
    verifications = build_ticket_verification_results(
        previous_tickets,
        # Verification closes the original blocking obligation. Advisory or
        # partial observations stay visible in review_findings but must not
        # keep a ticket unresolved after its blocking condition disappeared.
        remaining_issues=remaining_blockers,
        current_text=after_text,
        applied=before_text != after_text,
        metadata={
            "verification_source": "alignment_targeted_recheck",
            "repair_round": repair_round,
        },
    )
    prior_verifications = list(
        getattr(previous_report, "repair_verifications", []) or []
    )
    return candidate_report.model_copy(
        update={
            "review_mode": "targeted_recheck",
            "review_findings": adjudicated,
            "repair_tickets": tickets,
            "repair_verifications": [*prior_verifications, *verifications],
            "missing_main_points": [finding.summary for finding in remaining_blockers],
            "repair_actions": [
                finding.repair_goal
                for finding in remaining_blockers
                if str(finding.repair_goal or "").strip()
            ],
        }
    )


__all__ = [
    "adjudicate_targeted_alignment_recheck",
    "alignment_main_gap_count",
    "alignment_uses_structured_contract",
    "verified_alignment_blockers",
]
