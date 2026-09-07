"""Repair-ticket helpers for contract execution audit blockers."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from novel_forge.core.exceptions import BLOCK_KIND_CONTRACT_AUDIT
from novel_forge.core.schemas.continuity import ContinuityIssue
from novel_forge.core.schemas.review import RepairTicket
from novel_forge.core.utils.text_hash import source_text_hash


def strip_outer_quote_marks(text: str) -> str:
    value = str(text or "").strip()
    quote_pairs = {
        '"': '"',
        "'": "'",
        "“": "”",
        "‘": "’",
        "「": "」",
        "『": "』",
    }
    if len(value) >= 2 and quote_pairs.get(value[0]) == value[-1]:
        return value[1:-1].strip()
    return value


def _paragraph_anchor_for_quote(current_text: str, quote: str) -> dict[str, Any]:
    needle = str(quote or "").strip()
    if not needle:
        return {}
    paragraphs = [para for para in re.split(r"\n\s*\n", str(current_text or "")) if para.strip()]
    for index, paragraph in enumerate(paragraphs, start=1):
        if needle in paragraph:
            return {
                "paragraph_start": index,
                "paragraph_end": index,
                "location": f"第 {index} 段",
                "location_confidence": 0.95,
                "anchor_type": "evidence_match",
                "evidence_quote": needle,
            }
    if needle in str(current_text or ""):
        return {
            "paragraph_start": 0,
            "paragraph_end": 0,
            "location": "正文命中片段",
            "location_confidence": 0.65,
            "anchor_type": "evidence_match",
            "evidence_quote": needle,
        }
    return {}


def compile_contract_audit_repair_ticket(
    *,
    report_payload: dict[str, Any],
    chapter_number: int,
    current_text: str,
) -> RepairTicket | None:
    """Compile a blocking contract execution audit into a bounded text repair ticket.

    Decision-string policy (v2.1.4+): the function previously required
    ``"repair" in decision`` and a non-empty ``future_leak_hits`` /
    ``forbidden_progression_hits`` list.  When the LLM only returned
    ``{verdict, severity, rationale}`` (which small models like Qwen-Turbo
    routinely do), both filters returned ``None`` and the desktop recovery
    path hard-failed.  We now accept the full set of actionable decisions
    (``repair`` / ``repair_or_replan`` / ``replan`` / ``continue`` with
    blocking) AND fall back to ``missing_required_progressions`` /
    ``missing_knowledge_ops`` / LLM-rationale synthesis as a primary-hit
    source, so the ticket can be compiled for prose-only adjudications.
    """
    if not bool(report_payload.get("should_block_archive")):
        return None
    decision = str(report_payload.get("repair_or_replan_decision") or "").lower()
    if decision not in {"repair", "repair_or_replan", "replan", "continue"}:
        return None
    future_hits = [str(item).strip() for item in report_payload.get("future_leak_hits", []) if item]
    forbidden_hits = [
        str(item).strip() for item in report_payload.get("forbidden_progression_hits", []) if item
    ]
    cognitive_hits = [
        str(item).strip()
        for item in report_payload.get("cognitive_constraint_hits", [])
        if str(item or "").strip()
    ]
    missing_progressions = [
        str(item).strip()
        for item in report_payload.get("missing_required_progressions", [])
        if str(item or "").strip()
    ]
    missing_knowledge = [
        str(item).strip()
        for item in report_payload.get("missing_knowledge_ops", [])
        if str(item or "").strip()
    ]
    blocking_hits = [*cognitive_hits, *future_hits, *forbidden_hits]
    if not blocking_hits:
        blocking_hits = [*missing_progressions, *missing_knowledge]
    if not blocking_hits:
        rationale = str(report_payload.get("rationale") or "").strip()
        synthesized = _extract_primary_hit_from_text(rationale) if rationale else ""
        if synthesized:
            blocking_hits = [synthesized]
    if not blocking_hits:
        return None
    evidence_quotes = [
        strip_outer_quote_marks(str(item))
        for item in report_payload.get("evidence_quotes", [])
        if str(item or "").strip()
    ]
    evidence_quote = next((quote for quote in evidence_quotes if quote and quote in current_text), "")
    if not evidence_quote and evidence_quotes:
        evidence_quote = evidence_quotes[0]
    anchor = _paragraph_anchor_for_quote(current_text, evidence_quote)
    primary_hit = blocking_hits[0]
    if cognitive_hits:
        issue_type = "cognitive_constraint"
    elif future_hits:
        issue_type = "future_leak"
    elif forbidden_hits:
        issue_type = "forbidden_progression"
    elif missing_progressions:
        issue_type = "missing_required_progression"
    elif missing_knowledge:
        issue_type = "missing_knowledge_op"
    else:
        issue_type = "future_leak"
    digest = hashlib.sha1(f"{chapter_number}:{primary_hit}".encode("utf-8")).hexdigest()[:10]
    return RepairTicket(
        ticket_id=f"contract_audit_{chapter_number:03d}_{digest}",
        chapter_number=chapter_number,
        review_mode="contract_execution_audit",
        finding_ids=[f"contract_execution_{issue_type}"],
        source_module="contract_execution_audit",
        dimension="plot_contract",
        issue_type=issue_type,
        severity=str(report_payload.get("severity") or "high"),
        target_summary=f"章节契约执行审计阻断归档：{primary_hit}",
        repair_goal=(
            "删除或改写提前确认真相、身份、秘密或无人知晓状态的表达；只能保留本章允许的"
            "观察、误判、怀疑、遮蔽或间接异常，不得把未来认知等级提前写成确认。"
            if issue_type == "cognitive_constraint"
            else (
            "删除或改写提前泄露后续节点的表达；只保留本章契约允许的观察、铺垫或情绪反应，"
            "不要明示后续合作意图、未来 payoff 或禁行进展。"
            if issue_type in {"future_leak", "forbidden_progression"}
            else (
                "补齐本章契约要求的必达项或知识操作；优先用已有角色和场景补足，"
                "不要新增后续章节计划或扩大情节进展。"
            )
            )
        ),
        repair_mode="replace",
        acceptance_criteria=[
            f"修复后不得再出现：{primary_hit}",
            "保留本章已通过的 required_events、required_progressions 与人物关系结果。",
            "改动应局限在命中句或相邻段落，不得全文重写。",
        ],
        must_preserve=[
            "本章已经达成的 required_events、required_progressions 与人物关系结果。",
        ],
        forbidden_changes=[
            "不得把禁止项转写为 allowed_progressions 或改成新的确认型物证。",
            "不得新增后续合作、未来 payoff 或更晚章节的明确计划。",
            "不得削弱本章已完成的契约进展。",
        ],
        source_text_hash=source_text_hash(current_text),
        max_change_ratio=0.04,
        max_attempts=1,
        target_paragraph_start=int(anchor.get("paragraph_start") or 0),
        target_paragraph_end=int(anchor.get("paragraph_end") or 0),
        blocking=True,
        metadata={
            "status": "non_compliant",
            "repairable": True,
            "auto_repair_eligible": True,
            "constraint": primary_hit,
            "cognitive_constraint_hits": cognitive_hits,
            "future_leak_hits": future_hits,
            "forbidden_progression_hits": forbidden_hits,
            "evidence_quote": evidence_quote,
            "evidence_exact": bool(evidence_quote and evidence_quote in current_text),
            "contract_completion_score": report_payload.get("contract_completion_score"),
            "contract_execution_report": True,
            "location": anchor.get("location", ""),
            "location_confidence": anchor.get("location_confidence", 0.0),
            "anchor_type": anchor.get("anchor_type", ""),
        },
    )


def _extract_primary_hit_from_text(text: str) -> str:
    """Best-effort extraction of a single primary violation phrase from a free-form text.

    Mirrors the helper in ``contract_execution_audit_step.py`` so the ticket
    compiler can synthesise a primary hit when the LLM only returned a prose
    rationale.  Kept as a local copy to avoid a circular import (the step
    already depends on a wide set of modules).
    """
    import re

    text = str(text or "").strip()
    if not text:
        return ""
    for pattern in (r"「([^」]{4,80})」", r"『([^』]{4,80})』"):
        matches = re.findall(pattern, text)
        if matches:
            return max(matches, key=len).strip()
    clauses = [clause.strip() for clause in re.split(r"[；;。]", text) if clause.strip()]
    candidates: list[str] = []
    for clause in clauses:
        for phrase in re.split(r"[，,、\s]+", clause):
            phrase = phrase.strip()
            if len(phrase) >= 4:
                candidates.append(phrase)
    if candidates:
        return max(candidates, key=len)[:120]
    return text[:80]


def has_contract_audit_repair_ticket(tickets: Any) -> bool:
    return any(
        str(getattr(ticket, "source_module", "") or "") == "contract_execution_audit"
        for ticket in (tickets or ())
    )


def is_contract_audit_block_exception(exc: BaseException) -> bool:
    if getattr(exc, "block_kind", "") == BLOCK_KIND_CONTRACT_AUDIT:
        return True
    messages = list(getattr(exc, "violations", []) or [])
    if not messages:
        messages = [str(exc)]
    return any(
        "章节契约执行审计" in str(message) and "阻断归档" in str(message)
        for message in messages
    )


def contract_audit_ticket_to_continuity_issue(
    ticket: RepairTicket,
    current_text: str = "",
) -> ContinuityIssue:
    anchor = _paragraph_anchor_for_quote(
        current_text,
        str(ticket.metadata.get("evidence_quote") or "").strip(),
    )
    paragraph_start = int(
        anchor.get("paragraph_start") or ticket.target_paragraph_start or 0
    )
    paragraph_end = int(
        anchor.get("paragraph_end") or ticket.target_paragraph_end or paragraph_start or 0
    )
    location_confidence = float(
        anchor.get("location_confidence")
        or ticket.metadata.get("location_confidence")
        or (0.9 if paragraph_start > 0 else 0.0)
    )
    evidence_quote = str(anchor.get("evidence_quote") or ticket.metadata.get("evidence_quote") or "")
    constraint = str(ticket.metadata.get("constraint") or "").strip()
    return ContinuityIssue(
        issue_type=str(ticket.issue_type or "contract_execution_audit"),
        severity=str(ticket.severity or "high"),
        source="postcondition",
        blocking=True,
        summary=str(ticket.target_summary or ticket.repair_goal or constraint),
        evidence=evidence_quote or constraint,
        location=str(anchor.get("location") or ticket.metadata.get("location") or "契约审计命中片段"),
        location_confidence=location_confidence,
        anchor_type=str(anchor.get("anchor_type") or ticket.metadata.get("anchor_type") or "inferred_scope"),
        paragraph_start=paragraph_start,
        paragraph_end=paragraph_end,
        evidence_quote=evidence_quote,
        fix_mode=str(ticket.repair_mode or "replace"),
        rewrite_scope="paragraph" if paragraph_start > 0 else "chapter",
        fix_actions=[
            str(ticket.repair_goal or ""),
            *[str(item) for item in ticket.acceptance_criteria],
        ],
    )
