"""Tests for repair semantic verification module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.workspace.repair_ops.execution_repair_semantic import (
    _extract_snippet,
    verify_issue_resolved_semantic,
    verify_issue_resolved_string_match,
    verify_repair_semantic,
)


class MockLLMResponse:
    """Mimics an LLM response object with .content attribute."""

    def __init__(self, content: str):
        self.content = content


def _make_runtime(semantic_enabled: bool = False, budget: int = 5000) -> MagicMock:
    runtime = MagicMock()
    runtime.settings.long_book_audit_semantic_verify_enabled = semantic_enabled
    runtime.settings.long_book_audit_semantic_check_budget_tokens = budget
    runtime.settings.long_streaming_json_observation_enabled = False
    runtime.router = MagicMock()
    runtime.builder = MagicMock()
    return runtime


class TestExtractSnippet:
    def test_short_text_returns_as_is(self):
        assert _extract_snippet("short") == "short"

    def test_empty_returns_empty(self):
        assert _extract_snippet("") == ""

    def test_long_text_truncated_at_sentence(self):
        text = "。" * 600
        result = _extract_snippet(text, max_chars=500)
        assert len(result) <= 502
        assert result.endswith("。")

    def test_long_text_no_sentence_boundary(self):
        text = "a" * 600
        result = _extract_snippet(text, max_chars=500)
        assert len(result) == 501
        assert result.endswith("…")


class TestStringMatchFallback:
    def test_evidence_not_in_text_resolved(self):
        result = verify_issue_resolved_string_match(
            "张三穿着红色衣服",
            "张三穿着蓝色衣服",
        )
        assert result["issue_resolved"] is True

    def test_evidence_in_text_not_resolved(self):
        result = verify_issue_resolved_string_match(
            "张三穿着红色衣服",
            "张三穿着红色衣服走在街上",
        )
        assert result["issue_resolved"] is False

    def test_empty_evidence_resolved(self):
        result = verify_issue_resolved_string_match("", "some text")
        assert result["issue_resolved"] is True

    def test_short_evidence_resolved(self):
        result = verify_issue_resolved_string_match("a", "some text")
        assert result["issue_resolved"] is True


class TestSemanticVerifyDisabled:
    @pytest.mark.asyncio
    async def test_disabled_returns_fallback(self):
        runtime = _make_runtime(semantic_enabled=False)
        result = await verify_issue_resolved_semantic(
            issue_description="test issue",
            issue_evidence="test evidence",
            repaired_text="repaired",
            runtime=runtime,
        )
        assert result["issue_resolved"] is None
        assert result["confidence"] == 0.0
        assert result["reasoning"] == "fallback"

    @pytest.mark.asyncio
    async def test_zero_budget_returns_fallback(self):
        runtime = _make_runtime(semantic_enabled=True, budget=0)
        result = await verify_issue_resolved_semantic(
            issue_description="test issue",
            issue_evidence="test evidence",
            repaired_text="repaired",
            runtime=runtime,
        )
        assert result["issue_resolved"] is None
        assert result["confidence"] == 0.0


class TestSemanticVerifyLLMCall:
    @pytest.mark.asyncio
    async def test_successful_semantic_verify(self):
        runtime = _make_runtime(semantic_enabled=True, budget=5000)

        mock_response = {
            "issue_resolved": True,
            "confidence": 0.9,
            "reasoning": "Issue is fully resolved.",
        }
        runtime.router.route = AsyncMock(return_value=MockLLMResponse(json.dumps(mock_response)))

        result = await verify_issue_resolved_semantic(
            issue_description="Character died",
            issue_evidence="张三已经死了",
            repaired_text="张三倒在地上，一动不动。",
            runtime=runtime,
        )

        assert result["issue_resolved"] is True
        assert result["confidence"] == 0.9
        assert "resolved" in result["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback(self):
        runtime = _make_runtime(semantic_enabled=True, budget=5000)
        runtime.router.route = AsyncMock(side_effect=Exception("LLM error"))

        result = await verify_issue_resolved_semantic(
            issue_description="Character died",
            issue_evidence="张三已经死了",
            repaired_text="张三倒在地上，一动不动。",
            runtime=runtime,
        )

        assert result["issue_resolved"] is None
        assert result["reasoning"] == "fallback"

    @pytest.mark.asyncio
    async def test_invalid_result_type_returns_fallback(self):
        runtime = _make_runtime(semantic_enabled=True, budget=5000)
        runtime.router.route = AsyncMock(return_value=MockLLMResponse("not a dict"))

        result = await verify_issue_resolved_semantic(
            issue_description="Character died",
            issue_evidence="张三已经死了",
            repaired_text="张三倒在地上，一动不动。",
            runtime=runtime,
        )

        assert result["issue_resolved"] is None
        assert result["reasoning"] == "fallback"

class TestVerifyRepairSemantic:
    @pytest.mark.asyncio
    async def test_no_description_returns_fallback(self):
        runtime = _make_runtime(semantic_enabled=False)
        result = await verify_repair_semantic(
            issue={"description": "", "evidence": "test"},
            repaired_text="repaired",
            runtime=runtime,
        )
        assert result["issue_resolved"] is None
        assert result["method_used"] == "fallback"

    @pytest.mark.asyncio
    async def test_fallback_to_string_match(self):
        runtime = _make_runtime(semantic_enabled=False)
        result = await verify_repair_semantic(
            issue={
                "description": "test issue",
                "evidence": "红色衣服",
                "fix_action": "change color",
            },
            repaired_text="蓝色衣服",
            runtime=runtime,
        )
        assert result["method_used"] == "string_match"
        assert result["issue_resolved"] is True
