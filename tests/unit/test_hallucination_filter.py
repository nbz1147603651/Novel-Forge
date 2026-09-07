"""Tests for three-tier hallucination filtering (confirmed/suspected/unlikely)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyStep,
    ConsistencyIssue,
)
from novel_forge.prompts.builder import PromptBuilder
from tests.helpers.book_audit_payloads import canonical_book_issue, canonical_verified_issue


def _canonical_issue(item: dict[str, Any]) -> dict[str, Any]:
    payload = canonical_book_issue(
        str(item.get("issue_id") or "issue"),
        category=str(item.get("category") or "timeline"),
        severity=str(item.get("severity") or "warning"),
        chapters_involved=item.get("chapters_involved") or None,
        primary_chapter=int(item.get("primary_chapter") or 1),
        paragraph_index=int(item.get("paragraph_index") or 1),
        paragraph_span=item.get("paragraph_span") if isinstance(item.get("paragraph_span"), list) else None,
        confidence=float(item.get("confidence") or 0.0),
    )
    payload.update(item)
    return payload


class _RouterContractStub:
    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "fake-model"

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192


class _ThreeTierRouter(_RouterContractStub):
    """Router that returns issues at different confidence levels."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = [_canonical_issue(issue) for issue in issues]

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                {
                    "issues": self.issues,
                    "repair_plan": [],
                    "summary": "审计完成",
                    "consistency_score": 7.0,
                },
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


def _make_sample_issues() -> list[dict[str, Any]]:
    return [
        {
            "issue_id": "high_conf_01",
            "category": "timeline",
            "severity": "critical",
            "chapters_involved": [1, 2],
            "primary_chapter": 1,
            "description": "高置信度问题",
            "confidence": 0.85,
            "paragraph_index": 3,
            "paragraph_span": [3, 5],
            "fix_mode": "repair_continuity",
            "fix_action": "rewrite",
        },
        {
            "issue_id": "mid_conf_01",
            "category": "character_state",
            "severity": "warning",
            "chapters_involved": [2],
            "primary_chapter": 2,
            "description": "中等置信度问题",
            "confidence": 0.55,
            "paragraph_index": 0,
            "paragraph_span": [],
            "fix_mode": "repair_continuity",
            "fix_action": "rewrite",
        },
        {
            "issue_id": "low_conf_01",
            "category": "naming",
            "severity": "info",
            "chapters_involved": [3],
            "primary_chapter": 3,
            "description": "低置信度问题",
            "confidence": 0.20,
            "paragraph_index": 0,
            "paragraph_span": [],
            "fix_mode": "manual_patch",
            "fix_action": "rewrite",
        },
        {
            "issue_id": "boundary_conf_01",
            "category": "worldbuilding",
            "severity": "warning",
            "chapters_involved": [1],
            "primary_chapter": 1,
            "description": "边界置信度问题(0.35)",
            "confidence": 0.35,
            "paragraph_index": 0,
            "paragraph_span": [],
            "fix_mode": "manual_patch",
            "fix_action": "rewrite",
        },
        {
            "issue_id": "boundary_conf_02",
            "category": "worldbuilding",
            "severity": "warning",
            "chapters_involved": [1],
            "primary_chapter": 1,
            "description": "边界置信度问题(0.70)",
            "confidence": 0.70,
            "paragraph_index": 2,
            "paragraph_span": [2, 2],
            "fix_mode": "repair_continuity",
            "fix_action": "rewrite",
        },
    ]


async def test_three_tier_filter(runtime_settings: Any) -> None:
    """Issues classified correctly by confidence into confirmed/suspected/unlikely."""
    router = _ThreeTierRouter(_make_sample_issues())
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "第一章", "key_events": []},
                {"chapter_number": 2, "summary": "第二章", "key_events": []},
                {"chapter_number": 3, "summary": "第三章", "key_events": []},
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            max_tokens=2048,
            temperature=0.2,
        )
    )

    by_id = {iss.issue_id: iss for iss in result.issues}

    assert by_id["high_conf_01"].verification_status == "confirmed"
    assert by_id["high_conf_01"].confidence == 0.85

    assert by_id["mid_conf_01"].verification_status == "suspected"
    assert by_id["mid_conf_01"].confidence == 0.55

    # boundary_conf_01 (confidence=0.35) is unlikely and dropped
    assert "boundary_conf_01" not in by_id

    assert by_id["boundary_conf_02"].verification_status == "suspected"
    assert by_id["boundary_conf_02"].confidence == 0.70

    assert "low_conf_01" not in by_id


async def test_suspected_retained(runtime_settings: Any) -> None:
    """Suspected issues are kept, not dropped."""
    issues = [
        {
            "issue_id": "suspected_01",
            "category": "timeline",
            "severity": "warning",
            "chapters_involved": [1],
            "primary_chapter": 1,
            "description": "疑似问题",
            "confidence": 0.50,
            "paragraph_index": 0,
            "fix_mode": "repair_continuity",
            "fix_action": "rewrite",
        },
    ]
    router = _ThreeTierRouter(issues)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "第一章", "key_events": []},
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            max_tokens=2048,
            temperature=0.2,
        )
    )

    ids = [iss.issue_id for iss in result.issues]
    assert "suspected_01" in ids
    assert result.issues[0].verification_status == "suspected"


async def test_unlikely_dropped(runtime_settings: Any) -> None:
    """Unlikely issues (confidence <= 0.35) are dropped."""
    issues = [
        {
            "issue_id": "unlikely_01",
            "category": "naming",
            "severity": "info",
            "chapters_involved": [1],
            "primary_chapter": 1,
            "description": "低置信度问题",
            "confidence": 0.10,
            "paragraph_index": 0,
            "fix_mode": "manual_patch",
            "fix_action": "rewrite",
        },
        {
            "issue_id": "kept_01",
            "category": "timeline",
            "severity": "critical",
            "chapters_involved": [1],
            "primary_chapter": 1,
            "description": "高置信度问题",
            "confidence": 0.90,
            "paragraph_index": 1,
            "fix_mode": "repair_continuity",
            "fix_action": "rewrite",
        },
    ]
    router = _ThreeTierRouter(issues)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": 1, "summary": "第一章", "key_events": []},
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            max_tokens=2048,
            temperature=0.2,
        )
    )

    ids = [iss.issue_id for iss in result.issues]
    assert "unlikely_01" not in ids
    assert "kept_01" in ids


async def test_verify_suspected_lenient(runtime_settings: Any) -> None:
    """Suspected issues have >= 50% retention rate during verification."""
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    suspected_issue = {
        "issue_id": "suspected_verify_01",
        "category": "timeline",
        "severity": "warning",
        "primary_chapter": 1,
        "chapters_involved": [1],
        "description": "疑似时间线问题",
        "confidence": 0.50,
        "verification_status": "suspected",
        "paragraph_index": 1,
        "evidence": "夜半抵达",
    }
    confirmed_issue = {
        "issue_id": "confirmed_verify_01",
        "category": "character_state",
        "severity": "critical",
        "primary_chapter": 1,
        "chapters_involved": [1],
        "description": "确认角色问题",
        "confidence": 0.90,
        "verification_status": "confirmed",
        "paragraph_index": 2,
        "evidence": "角色A在场",
    }

    class _VerifyRejectAllRouter(_RouterContractStub):
        """Router that rejects all issues in verification."""

        async def route(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                content=json.dumps(
                    {
                        "verified_issues": [
                            canonical_verified_issue(
                                "suspected_verify_01",
                                status="rejected",
                                confidence=0.60,
                                rejection_reason="not present",
                            ),
                            canonical_verified_issue(
                                "confirmed_verify_01",
                                status="rejected",
                                confidence=0.95,
                                rejection_reason="not present",
                            ),
                        ]
                    },
                    ensure_ascii=False,
                ),
                model_id="fake-model",
            )

    runtime = SimpleNamespace(
        router=_VerifyRejectAllRouter(),
        builder=PromptBuilder(),
        settings=runtime_settings,
    )

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=[suspected_issue, confirmed_issue],
        chapter_texts=[
            {
                "chapter_number": 1,
                "numbered_text": "[P1] 夜半抵达。\n\n[P2] 角色A在场。",
                "paragraph_count": 2,
                "paragraphs": ["夜半抵达。", "角色A在场。"],
            }
        ],
        chapter_summaries=[{"chapter_number": 1, "summary": "测试章"}],
        canon_state_snapshot={},
    )

    remaining_ids = [iss.get("issue_id") for iss in updated]

    assert "suspected_verify_01" in remaining_ids
    assert "confirmed_verify_01" not in remaining_ids

    suspected_total = stats.get("suspected_total", 0)
    suspected_retained = stats.get("suspected_retained", 0)
    if suspected_total > 0:
        retention_rate = suspected_retained / suspected_total
        assert retention_rate >= 0.5, (
            f"Suspected retention rate {retention_rate:.2%} < 50%"
        )


def test_consistency_issue_model_dump_includes_verification_status() -> None:
    """ConsistencyIssue.model_dump() includes verification_status field."""
    issue = ConsistencyIssue(
        category="timeline",
        severity="warning",
        chapters_involved=[1, 2],
        description="测试问题",
        confidence=0.75,
        verification_status="confirmed",
    )

    dump = issue.model_dump()
    assert "verification_status" in dump
    assert dump["verification_status"] == "confirmed"
    assert dump["confidence"] == 0.75
