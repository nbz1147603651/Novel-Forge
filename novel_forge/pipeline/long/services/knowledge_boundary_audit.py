"""Knowledge-boundary audit service for long-chapter final text."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from novel_forge.core.review.review_contracts import (
    compile_repair_tickets_from_findings,
    normalize_severity,
    source_text_hash,
)
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.core.utils.audit_issue import stable_issue_id
from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
    cognitive_constraints_from_contract,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import (
    load_story_kernel_composer,
)
from novel_forge.pipeline.long.services.post_gen_verification import (
    BoundaryIssue,
    Severity,
    build_knowledge_audit_card,
    verify_knowledge_boundaries,
)
from novel_forge.pipeline.steps.knowledge_boundary_audit_step import (
    KnowledgeBoundaryAuditInput,
    KnowledgeBoundaryAuditStep,
)

_logger = logging.getLogger(__name__)

_BLOCKING_DECISIONS = {"leak", "premature_reveal"}
_FINDING_DECISIONS = {
    "leak",
    "premature_reveal",
    "unsupported_knowledge_gain",
    "ambiguous",
}
_DECISION_TO_ISSUE_TYPE = {
    "leak": "knowledge_leak",
    "premature_reveal": "premature_reveal",
    "unsupported_knowledge_gain": "unsupported_knowledge_gain",
    "ambiguous": "knowledge_boundary_ambiguous",
}
_KNOWN_ISSUE_TYPES = {
    "knowledge_leak",
    "premature_reveal",
    "unsupported_knowledge_gain",
    "knowledge_boundary_ambiguous",
}


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="json")
        except TypeError:
            payload = dump()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fact_fingerprint(fact: str) -> str:
    return hashlib.sha256(str(fact or "").encode("utf-8")).hexdigest()[:16]


def _candidate_entry_id(candidate: dict[str, Any]) -> str:
    return _clean(candidate.get("entry_id")) or _clean(candidate.get("candidate_id"))


def _issue_entry_id(item: dict[str, Any], *, fallback: str = "") -> str:
    return _clean(item.get("entry_id")) or _clean(item.get("candidate_id")) or fallback


def _fallback_issue_entry_id(item: dict[str, Any]) -> str:
    raw = "|".join(
        _clean(item.get(key))
        for key in (
            "decision",
            "issue_type",
            "evidence_quote",
            "reason",
            "repair_goal",
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"unknown_{digest}"


def _candidate_reasons(candidate: dict[str, Any]) -> list[str]:
    raw = candidate.get("boundary_reasons")
    if not isinstance(raw, list):
        raw = candidate.get("reasons")
    return [str(item) for item in raw or [] if str(item).strip()]


def _candidate_lookup(audit_card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for raw in audit_card.get("hidden_candidates") or []:
        candidate = _as_mapping(raw)
        entry_id = _candidate_entry_id(candidate)
        if entry_id:
            lookup[entry_id] = candidate
    return lookup


def _candidate_fingerprint(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return ""
    return _clean(candidate.get("fact_fingerprint")) or _fact_fingerprint(_clean(candidate.get("fact")))


def _redact_hidden_text(text: Any, candidate: dict[str, Any] | None) -> str:
    cleaned = _clean(text)
    if not cleaned or not candidate:
        return cleaned
    fact = _clean(candidate.get("fact"))
    if fact and fact in cleaned:
        cleaned = cleaned.replace(fact, "[hidden_fact]")
    return cleaned


def _is_exact_hidden_match(issue: BoundaryIssue, candidate: dict[str, Any] | None) -> bool:
    if not candidate:
        return False
    fact = _clean(candidate.get("fact"))
    evidence = _clean(issue.evidence_quote)
    return bool(fact and evidence and fact == evidence)


def issue_blocks_finalize(
    issue: BoundaryIssue,
    candidate: dict[str, Any] | None = None,
) -> bool:
    """Return whether a local fallback hit is high-confidence enough to block."""

    severity = str(getattr(issue.severity, "value", issue.severity) or "").lower()
    if severity != Severity.HIGH.value or float(issue.confidence or 0.0) < 0.90:
        return False
    return _is_exact_hidden_match(issue, candidate) if candidate is not None else True


def _redacted_prescreen_hit(
    issue: BoundaryIssue,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "entry_id": issue.entry_id,
        "fact_fingerprint": _candidate_fingerprint(candidate),
        "entity_id": issue.entity_id,
        "entity_name": issue.character,
        "knowledge_type": _clean((candidate or {}).get("knowledge_type")),
        "visibility": _clean((candidate or {}).get("visibility")),
        "severity": str(getattr(issue.severity, "value", issue.severity)),
        "confidence": float(issue.confidence or 0.0),
        "reason": issue.reason,
        "evidence_quote": issue.evidence_quote,
    }


def _llm_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": _candidate_entry_id(candidate),
        "entity_id": _clean(candidate.get("entity_id")),
        "entity_name": _clean(candidate.get("entity_name") or candidate.get("character")),
        "fact": _clean(candidate.get("fact")),
        "fact_fingerprint": _candidate_fingerprint(candidate),
        "knowledge_type": _clean(candidate.get("knowledge_type")),
        "source_chapter": _int(candidate.get("source_chapter")),
        "revealed_in_chapter": _int(candidate.get("revealed_in_chapter")),
        "visibility": _clean(candidate.get("visibility")),
        "confidence": max(0.0, min(1.0, _float(candidate.get("confidence"), 1.0))),
        "notes": _clean(candidate.get("notes")),
        "boundary_reasons": _candidate_reasons(candidate),
    }


def _matched_llm_candidates(
    prescreen_issues: list[BoundaryIssue],
    candidate_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for issue in prescreen_issues:
        entry_id = _clean(issue.entry_id)
        candidate = candidate_by_id.get(entry_id)
        if not candidate or entry_id in seen:
            continue
        seen.add(entry_id)
        candidates.append(_llm_candidate(candidate))
    return candidates


def _decision(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {
        "not_leak",
        "legitimate_reveal",
        "leak",
        "premature_reveal",
        "unsupported_knowledge_gain",
        "ambiguous",
    }:
        return raw
    return "ambiguous"


def _issue_type_for(item: dict[str, Any], decision: str) -> str:
    raw = str(item.get("issue_type", "") or "").strip().lower()
    if raw in _KNOWN_ISSUE_TYPES:
        return raw
    return _DECISION_TO_ISSUE_TYPE.get(decision, "knowledge_boundary_ambiguous")


def _knowledge_boundary_finding_id(
    *,
    chapter_number: int,
    source_module: str,
    entry_id: str,
    issue_type: str,
    summary: str,
    evidence: str = "",
    location: str = "",
    decision: str = "",
) -> str:
    issue_id = stable_issue_id(
        "knowledge_boundary",
        chapter_number=chapter_number,
        issue_type=issue_type,
        summary=summary,
        evidence=evidence,
        location=location,
        repair_surface="chapter_text",
        extra={
            "entry_id": entry_id,
            "decision": decision,
        },
    )
    return f"{source_module}_{issue_id}"


def _adjudicated_issue_blocks(item: dict[str, Any]) -> bool:
    decision = _decision(item.get("decision"))
    severity = normalize_severity(item.get("severity"))
    confidence = _float(item.get("confidence"), 0.0)
    return decision in _BLOCKING_DECISIONS and severity in {"critical", "high"} and confidence >= 0.80


def findings_from_boundary_issues(
    issues: list[BoundaryIssue],
    *,
    chapter_number: int,
    current_text: str,
    block_high_confidence: bool,
    source_module: str,
    candidate_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[ReviewFinding]:
    """Convert local fallback prescreen hits into normalized review findings."""

    text_hash = source_text_hash(current_text)
    findings: list[ReviewFinding] = []
    candidates = candidate_by_id or {}
    for issue in issues:
        candidate = candidates.get(_clean(issue.entry_id))
        severity = str(getattr(issue.severity, "value", issue.severity) or "medium")
        blocks = block_high_confidence and issue_blocks_finalize(issue, candidate)
        summary = "知识边界本地兜底命中，需要移除或改写越界认知。"
        findings.append(
            ReviewFinding(
                finding_id=_knowledge_boundary_finding_id(
                    chapter_number=chapter_number,
                    source_module=source_module,
                    entry_id=issue.entry_id,
                    issue_type="knowledge_leak",
                    summary=summary,
                    evidence=issue.evidence_quote,
                    decision="local_fallback",
                ),
                chapter_number=chapter_number,
                review_mode="targeted_recheck",
                source_module=source_module,
                dimension="knowledge_boundary",
                issue_type="knowledge_leak",
                severity=severity,
                confidence=float(issue.confidence or 0.72),
                summary=summary,
                evidence_quote=issue.evidence_quote,
                repair_goal="移除或改写正文中已经出现的越界认知，不要新增隐藏知识解释。",
                suggested_mode="window",
                blocks_finalize=blocks,
                source_text_hash=text_hash,
                signature=f"{issue.entry_id}:{issue.evidence_quote}",
                metadata={
                    "entry_id": issue.entry_id,
                    "fact_fingerprint": _candidate_fingerprint(candidate),
                    "reason": issue.reason,
                    "entity_name": issue.character,
                    "entity_id": issue.entity_id,
                    "confidence": float(issue.confidence or 0.0),
                    "local_fallback": True,
                },
            )
        )
    return findings


def findings_from_adjudicated_issues(
    issues: list[dict[str, Any]],
    *,
    chapter_number: int,
    current_text: str,
    source_module: str = "knowledge_boundary_audit",
    block_high_confidence: bool = True,
    candidate_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[ReviewFinding]:
    """Convert LLM knowledge-boundary adjudication into review findings."""

    text_hash = source_text_hash(current_text)
    candidates = candidate_by_id or {}
    findings: list[ReviewFinding] = []
    for raw in issues:
        item = _as_mapping(raw)
        decision = _decision(item.get("decision"))
        if decision not in _FINDING_DECISIONS:
            continue
        entry_id = _issue_entry_id(item, fallback=_fallback_issue_entry_id(item))
        candidate = candidates.get(entry_id)
        issue_type = _issue_type_for(item, decision)
        severity = normalize_severity(item.get("severity"))
        if decision == "ambiguous" and severity in {"critical", "high"}:
            severity = "medium"
        confidence = max(0.0, min(1.0, _float(item.get("confidence"), 0.0)))
        blocks = bool(block_high_confidence and _adjudicated_issue_blocks(item))
        summary = _clean(item.get("reason")) or "知识边界审计发现正文可能越界。"
        repair_goal = _clean(item.get("repair_goal")) or (
            "移除或改写正文中已经出现的越界认知，或补足当前章可见获知证据。"
        )
        summary = _redact_hidden_text(summary, candidate)
        repair_goal = _redact_hidden_text(repair_goal, candidate)
        findings.append(
            ReviewFinding(
                finding_id=_knowledge_boundary_finding_id(
                    chapter_number=chapter_number,
                    source_module=source_module,
                    entry_id=entry_id,
                    issue_type=issue_type,
                    summary=summary,
                    evidence=_clean(item.get("evidence_quote")),
                    location=str(max(0, _int(item.get("paragraph_start"))) or ""),
                    decision=decision,
                ),
                chapter_number=chapter_number,
                review_mode="targeted_recheck",
                source_module=source_module,
                dimension="knowledge_boundary",
                issue_type=issue_type,
                severity=severity,
                confidence=confidence or 0.72,
                summary=summary,
                evidence_quote=_clean(item.get("evidence_quote")),
                paragraph_start=max(0, _int(item.get("paragraph_start"))),
                paragraph_end=max(0, _int(item.get("paragraph_end"))),
                repair_goal=repair_goal,
                suggested_mode="window",
                blocks_finalize=blocks,
                source_text_hash=text_hash,
                signature=f"{entry_id}:{decision}:{_clean(item.get('evidence_quote'))}",
                metadata={
                    "entry_id": entry_id,
                    "fact_fingerprint": _candidate_fingerprint(candidate),
                    "decision": decision,
                    "adjudication_reason": summary,
                },
            )
        )
    return findings


def _redacted_adjudicated_issue(
    item: dict[str, Any],
    candidate_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entry_id = _issue_entry_id(item)
    candidate = candidate_by_id.get(entry_id)
    return {
        "decision": _decision(item.get("decision")),
        "issue_type": _issue_type_for(item, _decision(item.get("decision"))),
        "severity": normalize_severity(item.get("severity")),
        "confidence": _float(item.get("confidence"), 0.0),
        "evidence_quote": _clean(item.get("evidence_quote")),
        "entry_id": entry_id,
        "fact_fingerprint": _candidate_fingerprint(candidate),
        "repair_goal": _redact_hidden_text(item.get("repair_goal"), candidate),
        "paragraph_start": _int(item.get("paragraph_start")),
        "paragraph_end": _int(item.get("paragraph_end")),
        "reason": _redact_hidden_text(item.get("reason"), candidate),
    }


def _save_report(
    *,
    storage: Any,
    bundle: Any,
    chapter_number: int,
    payload: dict[str, Any],
) -> None:
    layout = getattr(bundle, "layout", None)
    report_path_fn = getattr(layout, "knowledge_boundary_report_path", None)
    if callable(report_path_fn):
        report_path = report_path_fn(chapter_number)
    elif getattr(layout, "reports_dir", None) is not None:
        report_path = (
            layout.reports_dir / f"chapter_{chapter_number:03d}_knowledge_boundary_verification.json"
        )
    else:
        return
    try:
        storage.save_json(report_path, payload)
    except Exception as exc:
        _logger.warning(
            "knowledge_boundary_report_save_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


def _serialized_repair_tickets(findings: list[ReviewFinding]) -> list[dict[str, Any]]:
    tickets = compile_repair_tickets_from_findings(findings)
    return [ticket.model_dump(mode="json") for ticket in tickets]


def _report_payload(
    *,
    chapter_number: int,
    stage: str,
    current_text: str,
    hidden_candidate_count: int,
    prescreen_hits: list[dict[str, Any]] | None = None,
    verdict: str = "pass",
    fallback_used: bool = False,
    issues: list[dict[str, Any]] | None = None,
    findings: list[ReviewFinding] | None = None,
    audit_skipped_reason: str = "",
) -> dict[str, Any]:
    finding_items = list(findings or [])
    payload: dict[str, Any] = {
        "chapter": chapter_number,
        "stage": stage,
        "source_text_hash": source_text_hash(current_text),
        "hidden_candidate_count": hidden_candidate_count,
        "prescreen_hit_count": len(prescreen_hits or []),
        "prescreen_hits": list(prescreen_hits or []),
        "verdict": verdict,
        "fallback_used": fallback_used,
        "issues": list(issues or []),
        "findings": [finding.model_dump(mode="json") for finding in finding_items],
        "repair_tickets": _serialized_repair_tickets(finding_items),
    }
    if audit_skipped_reason:
        payload["audit_skipped_reason"] = audit_skipped_reason
    return payload


async def run_knowledge_boundary_audit(
    *,
    runner: Any,
    storage: Any,
    bundle: Any,
    packet: Any,
    current_text: str,
    chapter_number: int,
    stage: str,
    block_high_confidence: bool = False,
    emit_step: bool = True,
) -> list[ReviewFinding]:
    """Run hidden-knowledge boundary audit against chapter text."""

    try:
        kernel_composer = await load_story_kernel_composer(runner, bundle)
        kernel_context = (
            kernel_composer.compose_draft_input(chapter_number)
            if kernel_composer is not None
            else {}
        )
        outline = getattr(bundle, "chapter_outline", None)
        chapter_contract = _as_mapping(getattr(packet, "chapter_contract", {}))
        pov_character = str(getattr(outline, "pov_character", "") or "")
        audit_card = build_knowledge_audit_card(
            kernel_context=kernel_context,
            chapter_contract=chapter_contract,
            current_chapter=chapter_number,
            pov_character=pov_character,
        )
        hidden_candidates = list(audit_card.get("hidden_candidates") or [])
        if not hidden_candidates:
            payload = _report_payload(
                chapter_number=chapter_number,
                stage=stage,
                current_text=current_text,
                hidden_candidate_count=0,
                verdict="pass",
                audit_skipped_reason="no_hidden_candidates",
            )
            _save_report(
                storage=storage,
                bundle=bundle,
                chapter_number=chapter_number,
                payload=payload,
            )
            if emit_step:
                runner._on_step("knowledge_boundary_verification", payload)
            return []

        prescreen_issues = verify_knowledge_boundaries(
            draft_text=current_text,
            knowledge_card=audit_card,
            pov_character=pov_character,
            current_chapter=chapter_number,
        )
        if not prescreen_issues:
            payload = _report_payload(
                chapter_number=chapter_number,
                stage=stage,
                current_text=current_text,
                hidden_candidate_count=len(hidden_candidates),
                verdict="pass",
                audit_skipped_reason="no_prescreen_hits",
            )
            _save_report(
                storage=storage,
                bundle=bundle,
                chapter_number=chapter_number,
                payload=payload,
            )
            if emit_step:
                runner._on_step("knowledge_boundary_verification", payload)
            return []

        candidate_by_id = _candidate_lookup(audit_card)
        llm_candidates = _matched_llm_candidates(prescreen_issues, candidate_by_id)
        prescreen_hits = [
            _redacted_prescreen_hit(issue, candidate_by_id.get(_clean(issue.entry_id)))
            for issue in prescreen_issues
        ]
        findings: list[ReviewFinding]
        verdict = "error"
        adjudicated_issues: list[dict[str, Any]] = []
        fallback_used = False

        try:
            step = KnowledgeBoundaryAuditStep(
                runner._router,
                runner._builder,
                settings=runner._settings,
            )
            result = await step.run(
                KnowledgeBoundaryAuditInput(
                    chapter_number=chapter_number,
                    chapter_text=current_text,
                    pov_character=pov_character,
                    audit_stage=stage,
                    audit_candidates=llm_candidates,
                    prescreen_hits=prescreen_hits,
                    allowed_current_ops=list(audit_card.get("allowed_current_ops") or []),
                    cognitive_constraints=cognitive_constraints_from_contract(
                        chapter_contract
                    ),
                )
            )
            verdict = result.verdict
            adjudicated_issues = [
                _redacted_adjudicated_issue(item, candidate_by_id) for item in result.issues
            ]
            findings = findings_from_adjudicated_issues(
                result.issues,
                chapter_number=chapter_number,
                current_text=current_text,
                block_high_confidence=block_high_confidence,
                candidate_by_id=candidate_by_id,
            )
        except Exception as exc:
            fallback_used = True
            _logger.warning(
                "knowledge_boundary_llm_audit_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )
            findings = findings_from_boundary_issues(
                prescreen_issues,
                chapter_number=chapter_number,
                current_text=current_text,
                block_high_confidence=block_high_confidence,
                source_module="post_gen_verification_local_fallback",
                candidate_by_id=candidate_by_id,
            )
            verdict = "issues_found" if findings else "pass"

        payload = _report_payload(
            chapter_number=chapter_number,
            stage=stage,
            current_text=current_text,
            hidden_candidate_count=len(hidden_candidates),
            prescreen_hits=prescreen_hits,
            verdict=verdict,
            fallback_used=fallback_used,
            issues=adjudicated_issues,
            findings=findings,
        )
        _save_report(
            storage=storage,
            bundle=bundle,
            chapter_number=chapter_number,
            payload=payload,
        )
        if emit_step:
            runner._on_step("knowledge_boundary_verification", payload)
        return findings
    except Exception as exc:
        _logger.warning(
            "knowledge_boundary_verification_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return []
