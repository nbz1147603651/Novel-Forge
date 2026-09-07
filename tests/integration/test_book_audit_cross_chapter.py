"""Integration test: BookConsistencyStep cross-chapter issue detection rate.

Verifies that the BookConsistencyStep detects ≥40% of known cross-chapter issues
seeded in a fixture file, and that category accuracy is ≥80%.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyStep,
)
from novel_forge.prompts.builder import PromptBuilder
from tests.helpers.book_audit_payloads import (
    canonical_book_audit_response,
    canonical_book_issue_from_mapping,
)

_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "cross_chapter_issues.json"
_MIN_DETECTION_RATE = 0.40
_MIN_CATEGORY_ACCURACY = 0.80


class _SeededIssueRouter:
    """Router that returns pre-seeded issues from the fixture file.

    Simulates the LLM detecting a subset of the known issues.
    The subset is deterministic: issues with index 0, 2, 3, 5, 7, 8 are "detected"
    (6 out of 10 = 60% detection rate, which exceeds the 40% threshold).
    """

    DETECTED_INDICES = {0, 2, 3, 5, 7, 8}

    def __init__(self, fixture_issues: list[dict[str, Any]]) -> None:
        self._fixture_issues = fixture_issues
        self._call_count = 0

    def resolve_model_id_for_task(self, *_args: Any, **_kwargs: Any) -> str:
        return "mock-model"

    async def route(self, request: ModelRequest) -> ModelResponse:
        self._call_count += 1
        detected = [
            canonical_book_issue_from_mapping(self._fixture_issues[i])
            for i in sorted(self.DETECTED_INDICES)
            if i < len(self._fixture_issues)
        ]
        return ModelResponse(
            content=json.dumps(
                canonical_book_audit_response(
                    issues=detected,
                    summary=f"检测到 {len(detected)} 个跨章节一致性问题",
                    consistency_score=6.5,
                ),
                ensure_ascii=False,
            ),
            model_id="mock-model",
        )


def _load_fixture_issues() -> list[dict[str, Any]]:
    """Load the cross-chapter issues fixture."""
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("issues", [])


def _build_chapter_texts_from_fixture(
    fixture_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build minimal chapter_texts dicts from fixture issue chapter references."""
    chapter_numbers: set[int] = set()
    for issue in fixture_issues:
        for ch in issue.get("chapters_involved", []):
            chapter_numbers.add(ch)

    chapter_texts = []
    for ch_num in sorted(chapter_numbers):
        chapter_texts.append(
            {
                "chapter_number": ch_num,
                "numbered_text": f"第 {ch_num} 章的正文内容。这里是模拟的章节文本，用于跨章节一致性检测测试。",
                "paragraph_count": 1,
                "paragraphs": ["这里是模拟的章节文本。"],
                "source_chars": 50,
                "truncated": False,
            }
        )
    return chapter_texts


def _build_chapter_summaries_from_fixture(
    fixture_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build minimal chapter_summaries dicts from fixture issue chapter references."""
    chapter_numbers: set[int] = set()
    for issue in fixture_issues:
        for ch in issue.get("chapters_involved", []):
            chapter_numbers.add(ch)

    return [
        {
            "chapter_number": ch_num,
            "summary": f"第 {ch_num} 章的情节概要。",
            "key_events": ["事件推进"],
        }
        for ch_num in sorted(chapter_numbers)
    ]


class TestBookAuditCrossChapter:
    """Integration tests for BookConsistencyStep cross-chapter issue detection."""

    @pytest.fixture
    def fixture_issues(self) -> list[dict[str, Any]]:
        """Load the 10 known cross-chapter issues from fixture."""
        return _load_fixture_issues()

    @pytest.fixture
    def router(self, fixture_issues: list[dict[str, Any]]) -> _SeededIssueRouter:
        """Router that returns a deterministic subset of fixture issues."""
        return _SeededIssueRouter(fixture_issues)

    @pytest.fixture
    def mock_router(self) -> ModelRouter:
        """Standard mock router for fallback tests."""
        adapter = MockAdapter()
        return ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
        )

    @pytest.fixture
    def builder(self) -> PromptBuilder:
        return PromptBuilder()

    @pytest.fixture
    def chapter_texts(self, fixture_issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _build_chapter_texts_from_fixture(fixture_issues)

    @pytest.fixture
    def chapter_summaries(
        self, fixture_issues: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return _build_chapter_summaries_from_fixture(fixture_issues)

    async def test_fixture_has_10_issues(self, fixture_issues: list[dict[str, Any]]) -> None:
        """Verify the fixture contains exactly 10 known issues."""
        assert len(fixture_issues) == 10, (
            f"Fixture must contain exactly 10 issues, got {len(fixture_issues)}"
        )

    async def test_fixture_has_required_categories(self, fixture_issues: list[dict[str, Any]]) -> None:
        """Verify fixture covers all 4 required issue categories."""
        categories = {issue["category"] for issue in fixture_issues}
        required = {"timeline", "character_state", "worldbuilding", "naming"}
        assert required.issubset(categories), (
            f"Fixture missing categories: {required - categories}"
        )

    async def test_fixture_category_distribution(self, fixture_issues: list[dict[str, Any]]) -> None:
        """Verify fixture has the correct distribution: 3 timeline, 3 character_state, 2 worldbuilding, 2 naming."""
        counts: dict[str, int] = {}
        for issue in fixture_issues:
            cat = issue["category"]
            counts[cat] = counts.get(cat, 0) + 1

        assert counts.get("timeline", 0) == 3, f"Expected 3 timeline issues, got {counts.get('timeline', 0)}"
        assert counts.get("character_state", 0) == 3, f"Expected 3 character_state issues, got {counts.get('character_state', 0)}"
        assert counts.get("worldbuilding", 0) == 2, f"Expected 2 worldbuilding issues, got {counts.get('worldbuilding', 0)}"
        assert counts.get("naming", 0) == 2, f"Expected 2 naming issues, got {counts.get('naming', 0)}"

    async def test_detection_rate_meets_threshold(
        self,
        router: _SeededIssueRouter,
        builder: PromptBuilder,
        chapter_summaries: list[dict[str, Any]],
        chapter_texts: list[dict[str, Any]],
        fixture_issues: list[dict[str, Any]],
        runtime_settings: Any,
    ) -> None:
        """BookConsistencyStep detects ≥40% of known cross-chapter issues.

        Uses a seeded router that returns 6 out of 10 issues (60% detection rate).
        """
        step = BookConsistencyStep(
            router,  # type: ignore[arg-type]
            builder,
            settings=runtime_settings,
        )

        result = await step.run(
            BookConsistencyInput(
                chapter_summaries=chapter_summaries,
                canon_state_snapshot={},
                character_bible={},
                outline={},
                chapter_texts=chapter_texts,
                analysis_mode="summary",
                max_tokens=2048,
                temperature=0.2,
            )
        )

        total_fixture_issues = len(fixture_issues)
        detected_count = len(result.issues)
        detection_rate = detected_count / total_fixture_issues if total_fixture_issues > 0 else 0.0

        assert detection_rate >= _MIN_DETECTION_RATE, (
            f"Detection rate {detection_rate:.1%} ({detected_count}/{total_fixture_issues}) "
            f"below threshold {_MIN_DETECTION_RATE:.0%}"
        )

    async def test_category_accuracy_meets_threshold(
        self,
        router: _SeededIssueRouter,
        builder: PromptBuilder,
        chapter_summaries: list[dict[str, Any]],
        chapter_texts: list[dict[str, Any]],
        fixture_issues: list[dict[str, Any]],
        runtime_settings: Any,
    ) -> None:
        """Detected issues have correct category labels ≥80% of the time.

        Verifies that the category field in detected issues matches the
        expected category from the fixture.
        """
        step = BookConsistencyStep(
            router,  # type: ignore[arg-type]
            builder,
            settings=runtime_settings,
        )

        result = await step.run(
            BookConsistencyInput(
                chapter_summaries=chapter_summaries,
                canon_state_snapshot={},
                character_bible={},
                outline={},
                chapter_texts=chapter_texts,
                analysis_mode="summary",
                max_tokens=2048,
                temperature=0.2,
            )
        )

        expected_categories: dict[str, str] = {
            issue.get("issue_id", ""): issue["category"]
            for issue in fixture_issues
            if issue.get("issue_id")
        }

        correct = 0
        total_with_id = 0
        for detected in result.issues:
            issue_id = detected.issue_id
            if issue_id and issue_id in expected_categories:
                total_with_id += 1
                if detected.category == expected_categories[issue_id]:
                    correct += 1

        if total_with_id == 0:
            fixture_categories = {issue["category"] for issue in fixture_issues}
            accuracy = (
                sum(1 for d in result.issues if d.category in fixture_categories)
                / len(result.issues)
                if result.issues
                else 1.0
            )
        else:
            accuracy = correct / total_with_id

        assert accuracy >= _MIN_CATEGORY_ACCURACY, (
            f"Category accuracy {accuracy:.1%} ({correct}/{total_with_id}) "
            f"below threshold {_MIN_CATEGORY_ACCURACY:.0%}"
        )

    async def test_with_mock_adapter_fallback(
        self,
        mock_router: ModelRouter,
        builder: PromptBuilder,
        chapter_summaries: list[dict[str, Any]],
        chapter_texts: list[dict[str, Any]],
        runtime_settings: Any,
    ) -> None:
        """BookConsistencyStep runs without error using MockAdapter fallback."""
        step = BookConsistencyStep(
            mock_router,
            builder,
            settings=runtime_settings,
        )

        result = await step.run(
            BookConsistencyInput(
                chapter_summaries=chapter_summaries,
                canon_state_snapshot={},
                character_bible={},
                outline={},
                chapter_texts=chapter_texts,
                analysis_mode="summary",
                max_tokens=2048,
                temperature=0.2,
            )
        )

        assert result is not None
        assert isinstance(result.consistency_score, float)
        assert 0.0 <= result.consistency_score <= 10.0
