"""Tests for LLM handoff fields in whole-book consistency issues."""

from __future__ import annotations

from novel_forge.pipeline.steps.book_consistency_step import ConsistencyIssue


def test_consistency_issue_serializes_llm_handoff_fields() -> None:
    issue = ConsistencyIssue(
        issue_id="ch6_timeline_01",
        category="timeline",
        severity="warning",
        chapters_involved=[5, 6],
        description="第 6 章的时间说法需要与第 5 章对照。",
        evidence_pairs=[
            {"chapter_number": 5, "evidence": "昨夜", "claim": "第5章已发生"},
            {"chapter_number": 6, "evidence": "第一次", "claim": "第6章声称首次发生"},
        ],
        verification_questions=["第6章该句是否为回忆而非现实时间线？"],
        handoff_notes="先判断两侧证据是否处于同一时间层。",
    )

    dumped = issue.model_dump()

    assert dumped["evidence_pairs"][0]["chapter_number"] == 5
    assert dumped["verification_questions"] == ["第6章该句是否为回忆而非现实时间线？"]
    assert dumped["handoff_notes"] == "先判断两侧证据是否处于同一时间层。"
