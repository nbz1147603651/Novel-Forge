"""Tests for verification result gating in _run_book_consistency_verify."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from tests.helpers.book_audit_payloads import canonical_book_issue, canonical_verified_issue


class _FakeBuilder:
    """Minimal PromptBuilder stub that returns a JSON-serializable ModelRequest.

    Production's ``call_with_retry`` runs ``json.dumps(request.messages, ...)``
    for preflight token estimation, so the builder must yield real strings —
    not MagicMocks — or that serialization step raises ``TypeError``.
    """

    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        prior_messages: list[Any] | None = None,
        thinking: Any = None,
        multi_turn: bool = False,
        **kwargs: Any,
    ) -> ModelRequest:
        messages = [
            {"role": "system", "content": "fake-system"},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
        ]
        return ModelRequest(
            task_type=task_type,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _make_runtime(router: Any) -> MagicMock:
    runtime = MagicMock()
    runtime.router = router
    runtime.builder = _FakeBuilder()
    runtime.settings = MagicMock()
    return runtime


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


def _make_issue(
    issue_id: str,
    primary_chapter: int = 1,
    confidence: float = 0.0,
    verification_status: str = "",
) -> dict[str, Any]:
    return canonical_book_issue(
        issue_id,
        primary_chapter=primary_chapter,
        confidence=confidence,
        verification_status=verification_status,
    )


def _make_chapter_text(chapter_number: int, paragraph_count: int = 10) -> dict[str, Any]:
    return {
        "chapter_number": chapter_number,
        "numbered_text": f"Chapter {chapter_number} text",
        "paragraph_count": paragraph_count,
        "paragraphs": [f"Paragraph {i}" for i in range(paragraph_count)],
    }


def test_build_related_chapters_context_for_cross_chapter_issue() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _build_related_chapters_context

    issue = {
        "issue_id": "cross_01",
        "category": "character_state",
        "severity": "critical",
        "primary_chapter": 1,
        "chapters_involved": [1, 2],
        "description": "第1章称林远仍在京城，第2章称林远已抵达边镇。",
        "evidence": "林远已抵达边镇",
    }
    related_long_tail = "尾段" * 400
    text_by_chapter = {
        1: _make_chapter_text(1),
        2: {
            "chapter_number": 2,
            "numbered_text": f"[P1] 林远已抵达边镇。\n[P2] {related_long_tail}",
            "paragraph_count": 2,
            "paragraphs": ["林远已抵达边镇。", related_long_tail],
        },
    }
    summary_by_chapter = {2: "林远离开京城，抵达边镇。"}

    context = _build_related_chapters_context(
        current_chapter=1,
        chapter_issues=[issue],
        text_by_chapter=text_by_chapter,
        summary_by_chapter=summary_by_chapter,
    )

    assert len(context) == 1
    assert context[0]["chapter_number"] == 2
    assert context[0]["chapter_summary"] == "林远离开京城，抵达边镇。"
    assert context[0]["issue_refs"][0]["issue_id"] == "cross_01"
    assert "林远已抵达边镇" in context[0]["excerpts"][0]
    assert related_long_tail not in context[0]["excerpts"][0]


class _MockVerifyRouter(_RouterContractStub):
    def __init__(self, issues_to_return: list[dict[str, Any]] | None = None) -> None:
        self.issues_to_return = issues_to_return or []

    async def route(self, request: ModelRequest) -> ModelResponse:
        verified = []
        for iss in self.issues_to_return:
            verified.append(
                canonical_verified_issue(
                    iss["issue_id"],
                    status="verified",
                    confidence=iss.get("confidence", 0.0),
                    severity=iss.get("severity", "warning"),
                    description=iss.get("description", ""),
                    evidence="test evidence",
                )
            )
        return ModelResponse(
            content=json.dumps({"verified_issues": verified}, ensure_ascii=False),
            model_id="fake-model",
        )


class _MockRejectRouter(_RouterContractStub):
    def __init__(self, confidence: float = 0.95) -> None:
        self.confidence = confidence

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                {
                    "verified_issues": [
                        canonical_verified_issue(
                            "false_positive",
                            status="rejected",
                            confidence=self.confidence,
                            rejection_reason="not present",
                        )
                    ]
                },
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


class _StaticVerifyRouter(_RouterContractStub):
    def __init__(self, verified_issues: list[dict[str, Any]]) -> None:
        self.verified_issues = verified_issues

    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(
                {"verified_issues": self.verified_issues},
                ensure_ascii=False,
            ),
            model_id="fake-model",
        )


@pytest.mark.asyncio
async def test_confidence_gate() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [
        _make_issue("high_conf", confidence=0.95),
        _make_issue("mid_conf", confidence=0.65),
        _make_issue("low_conf", confidence=0.30),
        _make_issue("no_conf", confidence=0.0),
    ]
    router = _MockVerifyRouter(issues_to_return=issues)
    runtime = _make_runtime(router)

    chapter_texts = [_make_chapter_text(1)]
    chapter_summaries = [{"chapter_number": 1, "summary": "Summary 1"}]

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=issues,
        chapter_texts=chapter_texts,
        chapter_summaries=chapter_summaries,
        canon_state_snapshot={},
    )

    updated_ids = {iss["issue_id"] for iss in updated}

    assert "high_conf" in updated_ids
    assert "mid_conf" in updated_ids
    assert "low_conf" in updated_ids
    assert "no_conf" in updated_ids

    high_issue = next(iss for iss in updated if iss["issue_id"] == "high_conf")
    assert high_issue["verification_status"] == "confirmed"
    assert high_issue["verify_confidence"] == 0.95

    mid_issue = next(iss for iss in updated if iss["issue_id"] == "mid_conf")
    assert mid_issue["verification_status"] == "suspected"
    assert mid_issue["verify_confidence"] == 0.65

    low_issue = next(iss for iss in updated if iss["issue_id"] == "low_conf")
    assert low_issue["verification_status"] == "unlikely"
    assert low_issue["verify_confidence"] == 0.30

    assert stats["removed_count"] == 0
    assert stats["questionable_count"] == 2  # low_conf + no_conf → unlikely
    assert stats["retained_count"] == 2  # high_conf + mid_conf


@pytest.mark.asyncio
async def test_retention_rate() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [
        _make_issue(f"issue_{i}", confidence=conf)
        for i, conf in enumerate(
            [0.95, 0.90, 0.85, 0.70, 0.60, 0.55, 0.40, 0.30, 0.20, 0.10]
        )
    ]
    router = _MockVerifyRouter(issues_to_return=issues)
    runtime = _make_runtime(router)

    chapter_texts = [_make_chapter_text(1)]
    chapter_summaries = [{"chapter_number": 1, "summary": "Summary 1"}]

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=issues,
        chapter_texts=chapter_texts,
        chapter_summaries=chapter_summaries,
        canon_state_snapshot={},
    )

    total = len(issues)
    retained = len(updated)

    assert retained == total
    assert stats["retained_count"] + stats["questionable_count"] == retained

    # Verify confidence_distribution is present
    assert "confidence_distribution" in stats


@pytest.mark.asyncio
async def test_questionable_marked() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [
        _make_issue("q1", confidence=0.10),
        _make_issue("q2", confidence=0.35),
        _make_issue("q3", confidence=0.49),
    ]
    router = _MockVerifyRouter(issues_to_return=issues)
    runtime = _make_runtime(router)

    chapter_texts = [_make_chapter_text(1)]
    chapter_summaries = [{"chapter_number": 1, "summary": "Summary 1"}]

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=issues,
        chapter_texts=chapter_texts,
        chapter_summaries=chapter_summaries,
        canon_state_snapshot={},
    )

    unlikely_issues = [
        iss for iss in updated if iss.get("verification_status") == "unlikely"
    ]

    assert len(unlikely_issues) == 3
    for q in unlikely_issues:
        assert q["verification_status"] == "unlikely"
        assert "verify_confidence" in q
        assert q["verify_confidence"] < 0.5

    assert stats["questionable_count"] == 3


@pytest.mark.asyncio
async def test_high_confidence_rejected_issue_is_removed() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [_make_issue("false_positive", confidence=0.80)]
    runtime = _make_runtime(_MockRejectRouter())

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=issues,
        chapter_texts=[_make_chapter_text(1)],
        chapter_summaries=[{"chapter_number": 1, "summary": "Summary 1"}],
        canon_state_snapshot={},
    )

    assert updated == []
    assert stats["rejected"] == 1
    assert stats["removed_count"] == 1


@pytest.mark.asyncio
async def test_confirmed_issue_requires_stronger_rejection() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [
        _make_issue(
            "false_positive",
            confidence=0.90,
            verification_status="confirmed",
        )
    ]
    runtime = _make_runtime(_MockRejectRouter(confidence=0.90))

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=issues,
        chapter_texts=[_make_chapter_text(1)],
        chapter_summaries=[{"chapter_number": 1, "summary": "Summary 1"}],
        canon_state_snapshot={},
    )

    assert len(updated) == 1
    assert updated[0]["issue_id"] == "false_positive"
    assert stats["rejected"] == 0
    assert stats["removed_count"] == 0


@pytest.mark.asyncio
async def test_confirmed_issue_modified_downgrade_is_guarded() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [
        _make_issue(
            "confirmed_issue",
            confidence=0.90,
            verification_status="confirmed",
        )
    ]
    router = _StaticVerifyRouter(
        [
            canonical_verified_issue(
                "confirmed_issue",
                status="modified",
                confidence=0.40,
                severity="info",
                description="modified but too weak",
                evidence="test evidence",
            )
        ]
    )
    runtime = _make_runtime(router)

    updated, stats = await _run_book_consistency_verify(
        runtime=runtime,
        report_issues=issues,
        chapter_texts=[_make_chapter_text(1)],
        chapter_summaries=[{"chapter_number": 1, "summary": "Summary 1"}],
        canon_state_snapshot={},
    )

    assert len(updated) == 1
    guarded = updated[0]
    assert guarded["confidence"] == 0.90
    assert guarded["severity"] == "warning"
    assert guarded["verification_status"] == "confirmed"
    assert guarded["verify_confidence"] == 0.90
    assert guarded["verify_modified_guard"]["reason"] == (
        "confirmed_confidence_downgrade_blocked"
    )
    assert stats["modified_guarded"] >= 1


@pytest.mark.asyncio
async def test_malformed_verified_items_are_rejected_by_contract() -> None:
    from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify

    issues = [_make_issue("kept_issue", confidence=0.80)]
    router = _StaticVerifyRouter(
        [
            {},
            {"issue_id": "kept_issue"},
            {"issue_id": "kept_issue", "status": "unknown"},
            {
                "issue_id": "kept_issue",
                "status": "verified",
                "confidence": 0.82,
                "evidence": "test evidence",
            },
        ]
    )
    runtime = _make_runtime(router)

    with pytest.raises(ValueError, match="JSON schema validation failed"):
        await _run_book_consistency_verify(
            runtime=runtime,
            report_issues=issues,
            chapter_texts=[_make_chapter_text(1)],
            chapter_summaries=[{"chapter_number": 1, "summary": "Summary 1"}],
            canon_state_snapshot={},
        )
