"""Integration test: BookConsistencyStep two-phase targeting blind spots.

Tests three blind spots in the book audit system:
1. ``_rank_book_audit_target_chapters`` respects threshold (not capped when flag_ratio > threshold)
2. ``_limit_issue_dicts`` prioritizes cross-chapter issues correctly
3. Verify rejects issues that don't have matching evidence in chapters
"""

from __future__ import annotations

import json

from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyStep,
    _limit_issue_dicts,
)
from novel_forge.prompts.builder import PromptBuilder
from tests.helpers.book_audit_payloads import (
    canonical_book_audit_response,
    canonical_book_issue,
)
from tests.helpers.model_adapters import HandlerModelAdapter


class TestTwoPhaseTargeting:
    """Tests for two-phase targeting logic in book audit."""

    async def test_two_phase_targeting_respects_threshold(self) -> None:
        """When flag_ratio > threshold, all flagged chapters should be targeted."""
        from novel_forge.core.config import Settings
        from novel_forge.gateway.router import ModelRouter

        async def _high_flag_route(request: ModelRequest) -> ModelResponse:
            issues = []
            for ch in range(1, 9):
                issues.append(
                    canonical_book_issue(
                        f"ch{ch}_test_01",
                        primary_chapter=ch,
                        chapters_involved=[ch],
                        description=f"Test issue in chapter {ch}",
                    )
                )
            return ModelResponse(
                content=json.dumps(
                    canonical_book_audit_response(
                        issues=issues,
                        summary=f"Flagged {len(issues)} chapters",
                        consistency_score=7.0,
                    ),
                    ensure_ascii=False,
                ),
                model_id="mock-model",
            )

        chapter_summaries = [
            {"chapter_number": i, "summary": f"Chapter {i} summary", "key_events": []}
            for i in range(1, 11)
        ]

        adapter = HandlerModelAdapter(_high_flag_route)
        router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
        builder = PromptBuilder()
        settings = Settings(_env_file=None)
        step = BookConsistencyStep(router, builder, settings=settings)

        result = await step.run(BookConsistencyInput(
            chapter_summaries=chapter_summaries,
            chapter_texts=[],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            analysis_mode="summary",
            max_chapters_per_batch=12,
            max_issues_per_chunk=24,
        ))

        flagged = set()
        for issue in getattr(result, "issues", []) or []:
            if isinstance(issue, dict):
                pc = issue.get("primary_chapter", 0)
                if pc and int(pc) > 0:
                    flagged.add(int(pc))
            elif hasattr(issue, "primary_chapter") and issue.primary_chapter > 0:
                flagged.add(issue.primary_chapter)

        assert len(flagged) >= 7, (
            f"Expected 7+ flagged chapters, got {len(flagged)}: {sorted(flagged)}"
        )


class TestLimitIssueDicts:
    """Tests for _limit_issue_dicts sorting behavior."""

    def test_cross_chapter_issue_priority(self) -> None:
        """cross-chapter issues should rank higher than single-chapter ones."""
        issues = [
            {
                "issue_id": "ch3_multi_01",
                "category": "timeline",
                "severity": "warning",
                "chapters_involved": [1, 3, 5, 7, 9],
                "primary_chapter": 3,
                "description": "Cross-chapter timeline issue",
                "paragraph_index": 0,
                "confidence": 0.8,
            },
            {
                "issue_id": "ch3_single_01",
                "category": "character_state",
                "severity": "warning",
                "chapters_involved": [3],
                "primary_chapter": 3,
                "description": "Single chapter issue",
                "paragraph_index": 12,
                "confidence": 0.9,
            },
        ]

        result = _limit_issue_dicts(issues, max_items=1)
        assert len(result) == 1
        assert result[0]["issue_id"] == "ch3_multi_01", (
            f"Expected cross-chapter issue, got {result[0]['issue_id']}"
        )

    def test_cross_chapter_preserves_order_within_same_severity(self) -> None:
        """At same cross_chapter_count, original order is preserved."""
        issues = [
            {
                "issue_id": "early",
                "severity": "info",
                "chapters_involved": [1, 2],
                "paragraph_index": 0,
                "confidence": 0.5,
            },
            {
                "issue_id": "late",
                "severity": "info",
                "chapters_involved": [1, 2],
                "paragraph_index": 0,
                "confidence": 0.5,
            },
        ]

        result = _limit_issue_dicts(issues, max_items=1)
        assert result[0]["issue_id"] == "early"


class TestLightweightEvidenceVerify:
    """Tests for the summary-mode evidence verification."""

    def test_evidence_match_passes(self, tmp_path) -> None:
        """Issue with matching evidence should survive verify."""
        from novel_forge.persistence.models import ProjectLayout
        from novel_forge.workspace.book_ops.execution_book_entry import _lightweight_evidence_verify

        layout = ProjectLayout(tmp_path)
        chapter_path = layout.chapter_path(5)
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text(
            "This is chapter five content with specific evidence text here",
            encoding="utf-8",
        )

        issues = [{
            "issue_id": "ch5_real_01",
            "severity": "warning",
            "primary_chapter": 5,
            "evidence": "specific evidence text",
            "paragraph_index": 1,
            "confidence": 0.8,
        }]

        filtered, stats = _lightweight_evidence_verify(issues, [5], layout)
        issue_ids = {i.get("issue_id", "") for i in filtered}
        assert "ch5_real_01" in issue_ids, "Real issue should survive verify"
        assert stats.get("rejected", 0) == 0

    def test_evidence_mismatch_rejects(self, tmp_path) -> None:
        """Issue with non-matching evidence should be rejected."""
        from novel_forge.persistence.models import ProjectLayout
        from novel_forge.workspace.book_ops.execution_book_entry import _lightweight_evidence_verify

        layout = ProjectLayout(tmp_path)
        chapter_path = layout.chapter_path(5)
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text("Real chapter content here", encoding="utf-8")

        issues = [{
            "issue_id": "ch5_fake_01",
            "severity": "critical",
            "primary_chapter": 5,
            "evidence": "this text does not exist in the chapter at all",
            "paragraph_index": 0,
            "confidence": 0.3,
        }]

        filtered, stats = _lightweight_evidence_verify(issues, [5], layout)
        issue_ids = {i.get("issue_id", "") for i in filtered}
        assert "ch5_fake_01" not in issue_ids, "Fake issue should be rejected"
        assert stats.get("rejected", 0) == 1
