"""Bounded Zvec evidence is only used after book-audit targeting."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.memory.audit_evidence import build_book_audit_evidence_context
from novel_forge.narrative_state.evidence_contracts import (
    RetrievalEvidenceCard,
    RetrievalEvidencePack,
)


class _EvidenceService:
    async def evidence_pack(self, **kwargs: Any) -> RetrievalEvidencePack:
        return RetrievalEvidencePack(
            pack_id="audit-pack",
            purpose=str(kwargs["purpose"]),
            query=str(kwargs["query"]),
            max_visible_chapter=int(kwargs["max_visible_chapter"]),
            evidence_cards=[
                RetrievalEvidenceCard(
                    card_id="state:entry-1",
                    kind="accepted_state",
                    source_ref="narrative_state/state_ledger/entry-1",
                    excerpt="已裁定状态：林远仍在追查裂缝。",
                    chapter_number=2,
                    authority="accepted",
                )
            ],
        )


async def test_book_audit_evidence_requires_existing_target() -> None:
    memory_context = SimpleNamespace(narrative_evidence_service=_EvidenceService())

    no_target = await build_book_audit_evidence_context(
        memory_context=memory_context,
        chapter_summaries=[],
        chapter_texts=[],
        chapter_issue_pool=[],
        completed_chapters=[1, 2],
    )
    targeted = await build_book_audit_evidence_context(
        memory_context=memory_context,
        chapter_summaries=[{"chapter_number": 2, "summary": "林远追查裂缝。"}],
        chapter_texts=[{"chapter_number": 2, "text": "..."}],
        chapter_issue_pool=[],
        completed_chapters=[1, 2],
    )

    assert no_target == []
    assert targeted[0]["chapter_number"] == 2
    assert targeted[0]["supporting_evidence"][0]["authority"] == "accepted"
