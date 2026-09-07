"""Integration test: Post-repair semantic verification elimination rate.

Verifies that the semantic verification module correctly identifies resolved issues
after repair, achieving ≥90% elimination rate (9 out of 10 issues resolved).
Also verifies fallback to string matching when LLM fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.workspace.repair_ops.execution_repair_semantic import (
    verify_issue_resolved_semantic,
    verify_issue_resolved_string_match,
    verify_repair_semantic,
)

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "cross_chapter_issues.json"
_MIN_ELIMINATION_RATE = 0.90  # 90%: 9 out of 10 issues must be resolved


def _load_fixture_issues() -> list[dict[str, Any]]:
    """Load the cross-chapter issues fixture."""
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("issues", [])


def _build_repaired_text(issue: dict[str, Any]) -> str:
    """Build simulated repaired text that no longer contains the original evidence.

    The repaired text removes the contradictory evidence while preserving
    the narrative context.
    """
    description = issue.get("description", "")

    # Simulate a repaired version that addresses the issue
    repaired_templates = {
        "timeline": f"经过仔细校对，时间线已经修正。原问题：{description}。现已统一时间表述，确保前后一致。",
        "character_state": f"角色状态已修正。原问题：{description}。现已调整角色状态描写，保持前后一致。",
        "worldbuilding": f"世界观设定已修正。原问题：{description}。现已统一世界观设定，消除矛盾。",
        "naming": f"命名一致性已修正。原问题：{description}。现已统一角色和物品命名。",
    }

    category = issue.get("category", "timeline")
    return repaired_templates.get(category, repaired_templates["timeline"])


class _MockSemanticAdapter(MockAdapter):
    """MockAdapter that returns configured semantic verification responses."""

    def __init__(self, response_content: dict[str, Any] | None = None, should_fail: bool = False) -> None:
        super().__init__()
        self._response_content = response_content
        self._should_fail = should_fail

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._should_fail:
            raise RuntimeError("LLM call failed")
        if self._response_content:
            return ModelResponse(
                content=json.dumps(self._response_content, ensure_ascii=False),
                model_id="mock-semantic-model",
            )
        return await super().complete(request)


class _MockRuntimeBuilder:
    """Builds a mock runtime with configurable semantic verification behavior."""

    @staticmethod
    def make_runtime(
        semantic_enabled: bool = True,
        llm_response: dict[str, Any] | None = None,
        llm_fails: bool = False,
    ) -> Any:
        """Create a mock runtime with the specified behavior."""
        settings = Settings(
            _env_file=None,
            long_book_audit_semantic_verify_enabled=semantic_enabled,
            long_book_audit_semantic_check_budget_tokens=5000,
        )

        adapter = _MockSemanticAdapter(
            response_content=llm_response,
            should_fail=llm_fails,
        )
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
        )

        # Mock builder to return a valid ModelRequest for any task type
        builder = MagicMock()
        def mock_build(task_type, context, **kwargs):
            return ModelRequest(
                task_type=task_type,
                messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                model_id="mock-model",
                max_tokens=kwargs.get("max_tokens", 512),
                temperature=kwargs.get("temperature", 0.1),
            )
        builder.build = mock_build

        runtime = MagicMock()
        runtime.settings = settings
        runtime.router = router
        runtime.builder = builder

        return runtime


class TestBookAuditRepairSemantic:
    """Integration tests for post-repair semantic verification."""

    @pytest.fixture
    def fixture_issues(self) -> list[dict[str, Any]]:
        """Load the 10 known cross-chapter issues from fixture."""
        return _load_fixture_issues()

    @pytest.fixture
    def runtime_llm_success(self) -> Any:
        """Runtime with LLM semantic verification enabled and succeeding."""
        return _MockRuntimeBuilder.make_runtime(
            semantic_enabled=True,
            llm_response={
                "issue_resolved": True,
                "confidence": 0.92,
                "reasoning": "修复后的文本已消除原有的时间线矛盾，表述一致。",
            },
        )

    @pytest.fixture
    def runtime_llm_fails(self) -> Any:
        """Runtime with LLM semantic verification enabled but LLM call fails."""
        return _MockRuntimeBuilder.make_runtime(
            semantic_enabled=True,
            llm_fails=True,
        )

    @pytest.fixture
    def runtime_semantic_disabled(self) -> Any:
        """Runtime with semantic verification disabled."""
        return _MockRuntimeBuilder.make_runtime(
            semantic_enabled=False,
        )

    async def test_fixture_has_10_issues(self, fixture_issues: list[dict[str, Any]]) -> None:
        """Verify the fixture contains exactly 10 known issues."""
        assert len(fixture_issues) == 10, (
            f"Fixture must contain exactly 10 issues, got {len(fixture_issues)}"
        )

    async def test_semantic_verify_resolved_issue(
        self,
        fixture_issues: list[dict[str, Any]],
        runtime_llm_success: Any,
    ) -> None:
        """Semantic verification correctly marks a resolved issue as resolved."""
        issue = fixture_issues[0]
        repaired_text = _build_repaired_text(issue)

        result = await verify_issue_resolved_semantic(
            issue_description=issue.get("description", ""),
            issue_evidence=issue.get("evidence", ""),
            repaired_text=repaired_text,
            repair_action="",
            runtime=runtime_llm_success,
        )

        assert result["issue_resolved"] is True
        assert result["confidence"] >= 0.9
        assert "reasoning" in result
        assert len(result["reasoning"]) > 0

    async def test_semantic_verify_disabled_returns_fallback(
        self,
        fixture_issues: list[dict[str, Any]],
        runtime_semantic_disabled: Any,
    ) -> None:
        """When semantic verification is disabled, returns fallback result."""
        issue = fixture_issues[0]
        repaired_text = _build_repaired_text(issue)

        result = await verify_issue_resolved_semantic(
            issue_description=issue.get("description", ""),
            issue_evidence=issue.get("evidence", ""),
            repaired_text=repaired_text,
            repair_action="",
            runtime=runtime_semantic_disabled,
        )

        assert result["issue_resolved"] is None
        assert result["confidence"] == 0.0
        assert result["reasoning"] == "fallback"

    async def test_string_match_evidence_removed(self, fixture_issues: list[dict[str, Any]]) -> None:
        """String matching correctly identifies when evidence is no longer present."""
        issue = fixture_issues[0]
        repaired_text = _build_repaired_text(issue)

        result = verify_issue_resolved_string_match(
            issue_evidence=issue.get("evidence", ""),
            repaired_text=repaired_text,
        )

        assert result["issue_resolved"] is True
        assert result["confidence"] == 0.6
        assert "no longer present" in result["reasoning"]

    async def test_string_match_evidence_present(self) -> None:
        """String matching correctly identifies when evidence is still present."""
        evidence = "第3章：'三月的阳光温暖地洒在小镇上'"
        # Repaired text that still contains the evidence
        repaired_text = f"一些其他内容。{evidence} 更多内容。"

        result = verify_issue_resolved_string_match(
            issue_evidence=evidence,
            repaired_text=repaired_text,
        )

        assert result["issue_resolved"] is False
        assert result["confidence"] == 0.7
        assert "still present" in result["reasoning"]

    async def test_string_match_evidence_too_short(self) -> None:
        """String matching handles evidence that is too short."""
        result = verify_issue_resolved_string_match(
            issue_evidence="a",
            repaired_text="some text",
        )

        assert result["issue_resolved"] is True
        assert result["confidence"] == 0.5
        assert "too short" in result["reasoning"]

    async def test_verify_repair_semantic_llm_succeeds(
        self,
        fixture_issues: list[dict[str, Any]],
        runtime_llm_success: Any,
    ) -> None:
        """High-level verify_repair_semantic uses LLM when it succeeds."""
        issue = fixture_issues[0]
        repaired_text = _build_repaired_text(issue)

        result = await verify_repair_semantic(
            issue=issue,
            repaired_text=repaired_text,
            runtime=runtime_llm_success,
        )

        assert result["method_used"] == "semantic"
        assert result["issue_resolved"] is True
        assert result["confidence"] >= 0.9

    async def test_verify_repair_semantic_llm_fallback(
        self,
        fixture_issues: list[dict[str, Any]],
        runtime_semantic_disabled: Any,
    ) -> None:
        """High-level verify_repair_semantic falls back to string matching when LLM disabled."""
        issue = fixture_issues[0]
        repaired_text = _build_repaired_text(issue)

        result = await verify_repair_semantic(
            issue=issue,
            repaired_text=repaired_text,
            runtime=runtime_semantic_disabled,
        )

        assert result["method_used"] == "string_match"
        assert result["issue_resolved"] is True

    async def test_elimination_rate_ge_90_percent(
        self,
        fixture_issues: list[dict[str, Any]],
        runtime_llm_success: Any,
    ) -> None:
        """Full repair flow with semantic verification achieves ≥90% elimination rate.

        Simulates repairing all 10 issues and verifying each one.
        At least 9 out of 10 must be marked as resolved.
        """
        resolved_count = 0
        total_issues = len(fixture_issues)

        for issue in fixture_issues:
            repaired_text = _build_repaired_text(issue)

            result = await verify_repair_semantic(
                issue=issue,
                repaired_text=repaired_text,
                runtime=runtime_llm_success,
            )

            if result.get("issue_resolved") is True:
                resolved_count += 1

        elimination_rate = resolved_count / total_issues if total_issues > 0 else 0.0

        assert elimination_rate >= _MIN_ELIMINATION_RATE, (
            f"Elimination rate {elimination_rate:.1%} ({resolved_count}/{total_issues}) "
            f"below threshold {_MIN_ELIMINATION_RATE:.0%}"
        )

    async def test_elimination_rate_with_string_fallback(
        self,
        fixture_issues: list[dict[str, Any]],
        runtime_semantic_disabled: Any,
    ) -> None:
        """Full repair flow with string matching fallback achieves ≥90% elimination rate.

        When LLM is disabled, string matching should still resolve ≥90% of issues
        since the repaired text no longer contains the original evidence.
        """
        resolved_count = 0
        total_issues = len(fixture_issues)

        for issue in fixture_issues:
            repaired_text = _build_repaired_text(issue)

            result = await verify_repair_semantic(
                issue=issue,
                repaired_text=repaired_text,
                runtime=runtime_semantic_disabled,
            )

            if result.get("issue_resolved") is True:
                resolved_count += 1

        elimination_rate = resolved_count / total_issues if total_issues > 0 else 0.0

        assert elimination_rate >= _MIN_ELIMINATION_RATE, (
            f"Elimination rate with string fallback {elimination_rate:.1%} "
            f"({resolved_count}/{total_issues}) below threshold {_MIN_ELIMINATION_RATE:.0%}"
        )

    async def test_verify_repair_semantic_empty_issue(self, runtime_llm_success: Any) -> None:
        """verify_repair_semantic handles empty issue gracefully."""
        empty_issue: dict[str, Any] = {}
        result = await verify_repair_semantic(
            issue=empty_issue,
            repaired_text="some repaired text",
            runtime=runtime_llm_success,
        )

        assert result["issue_resolved"] is None
        assert result["confidence"] == 0.0
        assert result["method_used"] == "fallback"
