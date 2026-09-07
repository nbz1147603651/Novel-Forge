"""Bounded narrative-evidence views for whole-book audits."""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


async def build_book_audit_evidence_context(
    *,
    memory_context: Any | None,
    chapter_summaries: list[dict[str, Any]],
    chapter_texts: list[dict[str, Any]],
    chapter_issue_pool: list[dict[str, Any]],
    completed_chapters: list[int],
) -> list[dict[str, Any]]:
    """Retrieve evidence only after the audit has selected target chapters."""
    if memory_context is None or not (chapter_texts or chapter_issue_pool):
        return []
    service = getattr(memory_context, "narrative_evidence_service", None)
    if service is None or not callable(getattr(service, "evidence_pack", None)):
        return []

    summaries = {
        int(item.get("chapter_number", 0) or 0): str(item.get("summary", "") or "").strip()
        for item in chapter_summaries
        if isinstance(item, dict) and int(item.get("chapter_number", 0) or 0) > 0
    }
    targets = [
        int(item.get("chapter_number", 0) or 0)
        for item in chapter_texts
        if isinstance(item, dict) and int(item.get("chapter_number", 0) or 0) > 0
    ]
    if not targets:
        for item in chapter_issue_pool[:12]:
            if not isinstance(item, dict):
                continue
            targets.extend(int(value or 0) for value in item.get("chapters_involved", []) or [])
            targets.append(int(item.get("primary_chapter") or item.get("chapter_number") or 0))
    targets = list(dict.fromkeys(value for value in targets if value > 0))[:12]

    result: list[dict[str, Any]] = []
    for chapter in targets:
        try:
            pack = await service.evidence_pack(
                purpose="book_audit_targeted_evidence",
                query=summaries.get(chapter) or f"第{chapter}章一致性核验",
                canon_revision="book_audit",
                max_visible_chapter=max(completed_chapters, default=0),
                candidate_limit=6,
                evidence_token_budget=1800,
                kinds={"entity", "accepted_state"},
            )
        except Exception as exc:
            _log.warning(
                "book_audit_evidence_retrieval_failed | chapter=%s | error=%s",
                chapter,
                exc,
            )
            continue
        cards = [
            {
                "kind": card.kind,
                "source_ref": card.source_ref,
                "excerpt": card.excerpt,
                "chapter_number": card.chapter_number,
                "authority": card.authority,
            }
            for card in pack.evidence_cards
        ]
        if cards:
            result.append({"chapter_number": chapter, "supporting_evidence": cards})
    return result


__all__ = ["build_book_audit_evidence_context"]
