from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from novel_forge.pipeline.steps.knowledge_boundary_audit_step import (
    KnowledgeBoundaryAuditInput,
    KnowledgeBoundaryAuditStep,
)


async def test_knowledge_boundary_audit_step_skips_without_prescreen_hits() -> None:
    step = KnowledgeBoundaryAuditStep(
        MagicMock(),
        MagicMock(),
        settings=SimpleNamespace(temp_knowledge_boundary_audit=0.1),
    )

    result = await step.run(
        KnowledgeBoundaryAuditInput(
            chapter_number=1,
            chapter_text="正文",
            audit_candidates=[{"entry_id": "k1", "fact": "秘密"}],
            prescreen_hits=[],
        )
    )

    assert result.verdict == "pass"
    assert result.issues == []


async def test_knowledge_boundary_audit_step_returns_llm_issues(monkeypatch) -> None:
    step = KnowledgeBoundaryAuditStep(
        MagicMock(),
        MagicMock(),
        settings=SimpleNamespace(temp_knowledge_boundary_audit=0.1),
    )
    calls = []

    async def fake_call(task_type, context, **kwargs):
        calls.append((task_type, context, kwargs))
        return {
            "verdict": "issues_found",
            "issues": [
                {
                    "decision": "leak",
                    "issue_type": "knowledge_leak",
                    "severity": "high",
                    "confidence": 0.9,
                    "evidence_quote": "她知道账册在门后",
                    "entry_id": "k1",
                    "repair_goal": "改成观察异常。",
                    "paragraph_start": 1,
                    "paragraph_end": 1,
                    "reason": "无获知证据。",
                }
            ],
        }

    monkeypatch.setattr(step, "_call_with_retry", fake_call)

    result = await step.run(
        KnowledgeBoundaryAuditInput(
            chapter_number=1,
            chapter_text="她知道账册在门后",
            pov_character="林青",
            audit_candidates=[{"entry_id": "k1", "fact": "账册在门后"}],
            prescreen_hits=[{"entry_id": "k1", "evidence_quote": "她知道账册在门后"}],
            cognitive_constraints=[
                {
                    "claim_id": "claim_knowledge",
                    "character_knowledge_coverage": {"林青": "unknown"},
                }
            ],
        )
    )

    assert result.verdict == "issues_found"
    assert result.issues[0]["entry_id"] == "k1"
    assert "candidate_id" not in result.issues[0]
    assert calls[0][1]["audit_candidates"][0]["fact"] == "账册在门后"
    assert calls[0][1]["cognitive_constraints"][0]["character_knowledge_coverage"] == {
        "林青": "unknown"
    }
    assert "[P1]" in calls[0][1]["numbered_text"]
