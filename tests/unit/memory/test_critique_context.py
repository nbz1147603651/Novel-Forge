"""Tests for critique_context integration in MemoryContext."""

from __future__ import annotations

import pytest

from novel_forge.memory.critic import CritiqueIssue, CritiqueReport
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.integration import MemoryContext


@pytest.fixture
def episodic_memory() -> EpisodicMemory:
    return EpisodicMemory(use_mock_embeddings=True)


def _make_issue(
    severity: str = "critical",
    issue_type: str = "continuity_error",
    chapter: int = 1,
    summary: str = "",
) -> CritiqueIssue:
    return CritiqueIssue(
        issue_type=issue_type,
        severity=severity,
        summary=summary or f"Test {issue_type} issue in chapter {chapter}",
        evidence=f"Evidence for {issue_type}",
        affected_chapters=[chapter],
        suggested_fix=f"Fix {issue_type}",
        confidence=0.9,
    )


def _make_report(
    chapter_number: int,
    issues: list[CritiqueIssue],
) -> CritiqueReport:
    return CritiqueReport(
        chapter_number=chapter_number,
        overall_score=10.0,
        issues=issues,
    )


class TestBuildCritiqueContextForPrompt:
    def test_empty_when_no_critiques(self) -> None:
        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=EpisodicMemory(use_mock_embeddings=True),
        )
        assert result == ""

    def test_empty_when_no_episodic_memory(self) -> None:
        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=None,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_contains_critical_high_only(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(
            3,
            [
                _make_issue(severity="critical", issue_type="continuity_error", chapter=3),
                _make_issue(severity="high", issue_type="character_drift", chapter=3),
                _make_issue(severity="medium", issue_type="pacing", chapter=3),
                _make_issue(severity="low", issue_type="style", chapter=3),
            ],
        )
        await episodic_memory.index_critique(3, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=episodic_memory,
        )

        assert "continuity_error" in result
        assert "character_drift" in result
        assert "pacing" not in result
        assert "style" not in result

    @pytest.mark.asyncio
    async def test_selected_critiques_are_not_cut_at_500_chars(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        long_summary = "A" * 200
        issues = [
            _make_issue(
                severity="critical",
                issue_type=f"error_{i}",
                chapter=i,
                summary=long_summary,
            )
            for i in range(1, 6)
        ]
        report = _make_report(5, issues)
        await episodic_memory.index_critique(5, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=7,
            episodic_memory=episodic_memory,
        )

        assert len(result) > 500
        assert result.count("★") == 5

    @pytest.mark.asyncio
    async def test_sorted_by_chapter_descending(self, episodic_memory: EpisodicMemory) -> None:
        for ch in range(1, 4):
            report = _make_report(
                ch,
                [_make_issue(severity="critical", issue_type="issue", chapter=ch)],
            )
            await episodic_memory.index_critique(ch, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=episodic_memory,
        )

        ch3_pos = result.find("Ch3")
        ch2_pos = result.find("Ch2")
        ch1_pos = result.find("Ch1")
        assert ch3_pos < ch2_pos < ch1_pos

    @pytest.mark.asyncio
    async def test_star_marker_present(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(
            2,
            [_make_issue(severity="critical", issue_type="test_issue", chapter=2)],
        )
        await episodic_memory.index_critique(2, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=4,
            episodic_memory=episodic_memory,
        )

        assert "★" in result


class TestGetMemoryContextForPromptWithCritiques:
    @pytest.fixture
    def memory_context(self, episodic_memory: EpisodicMemory) -> MemoryContext:
        ctx = MemoryContext()
        ctx._episodic_memory = episodic_memory
        return ctx

    @pytest.mark.asyncio
    async def test_include_critiques_true_returns_critique_context(
        self, memory_context: MemoryContext, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report(
            2,
            [_make_issue(severity="critical", issue_type="continuity_error", chapter=2)],
        )
        await episodic_memory.index_critique(2, report)

        result = memory_context.get_memory_context_for_prompt(
            current_chapter=4,
            include_critiques=True,
            include_motifs=False,
            include_summaries=False,
        )

        assert "critique_context" in result
        assert result["critique_context"] != ""

    @pytest.mark.asyncio
    async def test_include_critiques_false_omits_critique_context(
        self, memory_context: MemoryContext, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report(
            2,
            [_make_issue(severity="critical", issue_type="continuity_error", chapter=2)],
        )
        await episodic_memory.index_critique(2, report)

        result = memory_context.get_memory_context_for_prompt(
            current_chapter=4,
            include_critiques=False,
            include_motifs=False,
            include_summaries=False,
        )

        assert "critique_context" not in result

    @pytest.mark.asyncio
    async def test_critique_context_empty_when_no_critical_high(
        self, memory_context: MemoryContext, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report(
            2,
            [
                _make_issue(severity="medium", issue_type="pacing", chapter=2),
                _make_issue(severity="low", issue_type="style", chapter=2),
            ],
        )
        await episodic_memory.index_critique(2, report)

        result = memory_context.get_memory_context_for_prompt(
            current_chapter=4,
            include_critiques=True,
            include_motifs=False,
            include_summaries=False,
        )

        assert result.get("critique_context", "") == ""
