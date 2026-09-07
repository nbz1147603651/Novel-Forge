"""Integration tests for memory optimization features.

Validates cross-component interactions across the memory optimization plan:
1. Memory constraints render correctly in draft template (★ markers)
2. CriticAgent critique entries persist to EpisodicMemory
3. Next chapter's memory hints include previous chapter's critique info
4. Dynamic lookback calculates correctly based on unresolved questions
5. Scene-level indexing entries count is correct
6. Motif constraints render correctly in template

All tests use use_mock_embeddings=True — no external API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from novel_forge.memory.critic import CritiqueIssue, CritiqueReport
from novel_forge.memory.episodic import CritiqueIndex, EpisodicMemory
from novel_forge.memory.integration import MemoryContext
from novel_forge.pipeline.long.services.context.stage_memory_builder import (
    _calculate_dynamic_lookback,
)

# ── Shared Fixtures ────────────────────────────────────────────────


@pytest.fixture
def episodic_memory() -> EpisodicMemory:
    """EpisodicMemory with mock embeddings for all tests."""
    return EpisodicMemory(use_mock_embeddings=True)


@pytest.fixture
def memory_context(episodic_memory: EpisodicMemory) -> MemoryContext:
    """MemoryContext with episodic memory attached."""
    ctx = MemoryContext()
    ctx._episodic_memory = episodic_memory
    return ctx


def _make_critique_issue(
    severity: str = "critical",
    issue_type: str = "continuity_error",
    chapter: int = 1,
    summary: str = "",
) -> CritiqueIssue:
    """Helper to create CritiqueIssue instances."""
    return CritiqueIssue(
        issue_type=issue_type,
        severity=severity,
        summary=summary or f"Test {issue_type} issue in chapter {chapter}",
        evidence=f"Evidence for {issue_type}",
        affected_chapters=[chapter],
        suggested_fix=f"Fix {issue_type}",
        confidence=0.9,
    )


def _make_critique_report(
    chapter_number: int,
    issues: list[CritiqueIssue],
) -> CritiqueReport:
    """Helper to create CritiqueReport instances."""
    return CritiqueReport(
        chapter_number=chapter_number,
        overall_score=10.0,
        issues=issues,
    )


# ── Fake dataclasses for scene indexing tests ──────────────────────


@dataclass
class FakeSceneIntent:
    scene_id: str
    summary: str
    purpose: str = ""
    conflict: str = ""
    required_characters: list[str] = field(default_factory=list)
    character_motivations: list = field(default_factory=list)
    location: str = ""
    time_marker: str = ""
    emotional_beat: str = ""
    relationship_dynamics: str = ""
    exit_target_state: str = ""


@dataclass
class FakePlan:
    scene_intents: list = field(default_factory=list)


@dataclass
class FakeOutcome:
    chapter_summary: str = ""
    source_chapter: int = 0
    character_updates: dict = field(default_factory=dict)
    new_events: list = field(default_factory=list)
    text: str = ""
    creative_report: Any = None
    alignment_report: Any = None
    plan: Any = None


# ── Test 1: Memory Constraints Render Correctly in Draft Template ─


class TestMemoryConstraintsRendering:
    """Verify that memory constraints use ★ markers in draft_chapter.j2."""

    def test_memory_block_title_has_star_marker(self) -> None:
        """Memory constraint section title must contain ★ marker."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "## 记忆约束（★ 必须回应）" in content

    def test_critique_context_uses_star_marker(self) -> None:
        """Critique context line in template must use ★ marker."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "★ 前文问题（必须避免重蹈）" in content

    def test_critique_context_conditional_block_exists(self) -> None:
        """Template must render critique context from the memory card."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "{% if memory.repair_lessons" in content

    def test_build_critique_context_produces_star_items(self) -> None:
        """_build_critique_context_for_prompt produces ★-prefixed items."""
        episodic = EpisodicMemory(use_mock_embeddings=True)
        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=episodic,
        )
        # Empty when no critiques
        assert result == ""

    @pytest.mark.asyncio
    async def test_critique_context_format_with_entries(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Critique context string format: ★ [type] ChN: summary."""
        report = _make_critique_report(
            3,
            [
                _make_critique_issue(
                    severity="critical",
                    issue_type="continuity_error",
                    chapter=3,
                    summary="角色位置矛盾",
                )
            ],
        )
        await episodic_memory.index_critique(3, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=episodic_memory,
        )

        assert "★" in result
        assert "[continuity_error]" in result
        assert "Ch3" in result
        assert "角色位置矛盾" in result


# ── Test 2: CriticAgent Critique Entries Persist to EpisodicMemory ─


class TestCritiquePersistence:
    """Verify that critique entries are correctly persisted to EpisodicMemory."""

    @pytest.mark.asyncio
    async def test_critical_issue_persisted_with_correct_signature(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Critical severity issue is indexed with correct CritiqueIndex signature."""
        report = _make_critique_report(
            1,
            [_make_critique_issue(severity="critical", issue_type="continuity_error", chapter=1)],
        )
        sigs = await episodic_memory.index_critique(1, report)

        assert len(sigs) == 1
        assert len(episodic_memory._critique_index) == 1

        entry = list(episodic_memory._critique_index.values())[0]
        assert isinstance(entry, CritiqueIndex)
        assert entry.chapter_number == 1
        assert entry.issue_type == "continuity_error"
        assert entry.severity == "critical"
        assert entry.scene_index == -1  # Critique entries use -1

    @pytest.mark.asyncio
    async def test_high_issue_persisted_medium_not(self, episodic_memory: EpisodicMemory) -> None:
        """High severity persisted, medium severity NOT persisted."""
        report = _make_critique_report(
            2,
            [
                _make_critique_issue(
                    severity="high", issue_type="character_inconsistency", chapter=2
                ),
                _make_critique_issue(severity="medium", issue_type="pacing_issue", chapter=2),
            ],
        )
        sigs = await episodic_memory.index_critique(2, report)

        assert len(sigs) == 1  # Only high, not medium
        assert len(episodic_memory._critique_index) == 1

    @pytest.mark.asyncio
    async def test_multiple_chapters_persist_independently(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Critiques from different chapters are stored independently."""
        for ch in range(1, 4):
            report = _make_critique_report(
                ch,
                [_make_critique_issue(severity="critical", issue_type="causal_break", chapter=ch)],
            )
            await episodic_memory.index_critique(ch, report)

        assert len(episodic_memory._critique_index) == 3
        assert len(episodic_memory._chapter_critiques) == 3
        assert 1 in episodic_memory._chapter_critiques
        assert 2 in episodic_memory._chapter_critiques
        assert 3 in episodic_memory._chapter_critiques

    @pytest.mark.asyncio
    async def test_stats_reflect_critique_entries(self, episodic_memory: EpisodicMemory) -> None:
        """get_memory_stats includes total_critique_entries count."""
        report = _make_critique_report(
            1,
            [
                _make_critique_issue(severity="critical", issue_type="continuity_error", chapter=1),
                _make_critique_issue(
                    severity="high", issue_type="character_inconsistency", chapter=1
                ),
            ],
        )
        await episodic_memory.index_critique(1, report)

        stats = episodic_memory.get_memory_stats()
        assert stats["total_critique_entries"] == 2
        assert stats["total_vectors_indexed"] == 2


# ── Test 3: Next Chapter Memory Hints Include Previous Critique ───


class TestCritiqueContextInNextChapter:
    """Verify that next chapter's memory hints include previous chapter's critique info."""

    @pytest.mark.asyncio
    async def test_critique_context_included_in_prompt_context(
        self, memory_context: MemoryContext, episodic_memory: EpisodicMemory
    ) -> None:
        """get_memory_context_for_prompt(include_critiques=True) returns critique_context."""
        report = _make_critique_report(
            3,
            [
                _make_critique_issue(
                    severity="critical",
                    issue_type="continuity_error",
                    chapter=3,
                    summary="时间线矛盾",
                )
            ],
        )
        await episodic_memory.index_critique(3, report)

        ctx = memory_context.get_memory_context_for_prompt(
            current_chapter=5,
            include_critiques=True,
            include_motifs=False,
            include_summaries=False,
        )

        assert "critique_context" in ctx
        assert ctx["critique_context"] != ""
        assert "时间线矛盾" in ctx["critique_context"]

    @pytest.mark.asyncio
    async def test_critique_context_excluded_when_flag_false(
        self, memory_context: MemoryContext, episodic_memory: EpisodicMemory
    ) -> None:
        """get_memory_context_for_prompt(include_critiques=False) omits critique_context."""
        report = _make_critique_report(
            3,
            [_make_critique_issue(severity="critical", issue_type="continuity_error", chapter=3)],
        )
        await episodic_memory.index_critique(3, report)

        ctx = memory_context.get_memory_context_for_prompt(
            current_chapter=5,
            include_critiques=False,
            include_motifs=False,
            include_summaries=False,
        )

        assert "critique_context" not in ctx

    @pytest.mark.asyncio
    async def test_critique_context_sorted_by_chapter_descending(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Critique context lists most recent chapters first."""
        for ch in [1, 2, 3]:
            report = _make_critique_report(
                ch,
                [
                    _make_critique_issue(
                        severity="critical", issue_type="issue", chapter=ch, summary=f"Ch{ch} issue"
                    )
                ],
            )
            await episodic_memory.index_critique(ch, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=5,
            episodic_memory=episodic_memory,
        )

        ch3_pos = result.find("Ch3")
        ch2_pos = result.find("Ch2")
        ch1_pos = result.find("Ch1")
        assert ch3_pos < ch2_pos < ch1_pos, "Critiques should be sorted by chapter descending"

    @pytest.mark.asyncio
    async def test_critique_context_empty_for_current_chapter(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """No critique context when no critiques exist before current chapter."""
        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=1,
            episodic_memory=episodic_memory,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_critique_context_preserves_complete_selected_entries(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """The max_chars hint must not cut selected high-severity critiques."""
        long_summary = "A" * 100
        issues = [
            _make_critique_issue(
                severity="critical",
                issue_type=f"error_{i}",
                chapter=i,
                summary=long_summary,
            )
            for i in range(1, 11)
        ]
        report = _make_critique_report(10, issues)
        await episodic_memory.index_critique(10, report)

        result = MemoryContext._build_critique_context_for_prompt(
            current_chapter=12,
            episodic_memory=episodic_memory,
        )

        assert len(result) > 500
        assert result.count("★") == 8
        assert "A" * 100 in result


# ── Test 4: Dynamic Lookback Calculation ───────────────────────────


class TestDynamicLookbackCalculation:
    """Verify dynamic lookback formula: base(8) + min(unresolved,3) + min(threads,2) + min(critiques,2), capped at 15."""

    def test_base_case_no_factors(self) -> None:
        """All zeros returns base_lookback (8)."""
        result = _calculate_dynamic_lookback(0, 0, 0)
        assert result == 8

    def test_unresolved_questions_increase_lookback(self) -> None:
        """5 unresolved questions: base(8) + min(5,3) = 11."""
        result = _calculate_dynamic_lookback(5, 0, 0)
        assert result == 11

    def test_all_factors_capped_at_max(self) -> None:
        """All factors maxed: base(8) + 3 + 2 + 2 = 15 (cap)."""
        result = _calculate_dynamic_lookback(15, 5, 5)
        assert result == 15

    def test_partial_factors(self) -> None:
        """Partial factors: base(8) + min(1,3) + min(1,2) + min(0,2) = 10."""
        result = _calculate_dynamic_lookback(1, 1, 0)
        assert result == 10

    def test_max_lookback_cap_enforced(self) -> None:
        """Result never exceeds max_lookback (15)."""
        result = _calculate_dynamic_lookback(100, 100, 100)
        assert result == 15

    def test_custom_base_and_max(self) -> None:
        """Custom base_lookback and max_lookback parameters."""
        result = _calculate_dynamic_lookback(2, 1, 1, base_lookback=10, max_lookback=12)
        assert result == 12

    def test_critique_factor_contributes(self) -> None:
        """Recent critiques increase lookback: base(8) + min(3,2) = 10."""
        result = _calculate_dynamic_lookback(0, 0, 3)
        assert result == 10

    def test_combined_unresolved_and_critiques(self) -> None:
        """Unresolved + critiques: base(8) + min(4,3) + min(0,2) + min(2,2) = 13."""
        result = _calculate_dynamic_lookback(4, 0, 2)
        assert result == 13


# ── Test 5: Scene-Level Indexing Entry Count ──────────────────────


class TestSceneLevelIndexing:
    """Verify scene-level indexing creates correct number of entries."""

    @pytest.mark.asyncio
    async def test_chapter_with_scene_intents_creates_correct_count(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Chapter with 3 scene_intents → 4 entries (1 chapter + 3 scenes)."""
        plan = FakePlan(
            scene_intents=[
                FakeSceneIntent(
                    scene_id="s1", summary="林远进入废弃图书馆", required_characters=["林远"]
                ),
                FakeSceneIntent(
                    scene_id="s2", summary="发现时间裂缝", required_characters=["林远", "神秘人"]
                ),
                FakeSceneIntent(
                    scene_id="s3", summary="决定穿越裂缝", required_characters=["林远"]
                ),
            ]
        )
        outcome = FakeOutcome(
            chapter_summary="林远在废弃图书馆发现时间裂缝并决定穿越。",
            source_chapter=5,
            character_updates={"林远": {}},
            text="林远推开了图书馆的门……",
            plan=plan,
        )

        signatures = await episodic_memory.index_chapter(outcome)

        assert len(signatures) == 4  # 1 chapter + 3 scenes

        stats = episodic_memory.get_memory_stats()
        assert stats["total_episodic_entries"] == 4
        assert stats["chapter_level_entries"] == 1
        assert stats["scene_level_entries"] == 3

    @pytest.mark.asyncio
    async def test_chapter_without_plan_creates_single_entry(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Chapter without plan → only 1 chapter-level entry."""
        outcome = FakeOutcome(
            chapter_summary="没有计划的章节",
            source_chapter=10,
        )

        signatures = await episodic_memory.index_chapter(outcome)
        assert len(signatures) == 1

        stats = episodic_memory.get_memory_stats()
        assert stats["total_episodic_entries"] == 1
        assert stats["scene_level_entries"] == 0

    @pytest.mark.asyncio
    async def test_chapter_with_empty_scene_intents(self, episodic_memory: EpisodicMemory) -> None:
        """Chapter with plan but empty scene_intents → only 1 chapter-level entry."""
        plan = FakePlan(scene_intents=[])
        outcome = FakeOutcome(
            chapter_summary="空场景章节",
            source_chapter=11,
            plan=plan,
        )

        signatures = await episodic_memory.index_chapter(outcome)
        assert len(signatures) == 1

    @pytest.mark.asyncio
    async def test_scene_entries_have_correct_scene_index(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Scene entries have scene_index > 0."""
        plan = FakePlan(
            scene_intents=[
                FakeSceneIntent(scene_id="s1", summary="场景一"),
                FakeSceneIntent(scene_id="s2", summary="场景二"),
            ]
        )
        outcome = FakeOutcome(
            chapter_summary="章节摘要",
            source_chapter=3,
            plan=plan,
        )

        await episodic_memory.index_chapter(outcome)

        scene_entries = [e for e in episodic_memory._index.values() if e.scene_index > 0]
        assert len(scene_entries) == 2
        assert scene_entries[0].scene_index == 1
        assert scene_entries[1].scene_index == 2

    @pytest.mark.asyncio
    async def test_delete_chapter_removes_all_scene_entries(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        """Delete chapter memory removes all scene entries."""
        plan = FakePlan(
            scene_intents=[
                FakeSceneIntent(scene_id="s1", summary="场景一"),
                FakeSceneIntent(scene_id="s2", summary="场景二"),
            ]
        )
        outcome = FakeOutcome(
            chapter_summary="章节摘要",
            source_chapter=3,
            plan=plan,
        )

        await episodic_memory.index_chapter(outcome)
        assert episodic_memory.get_memory_stats()["scene_level_entries"] == 2

        deleted = episodic_memory.delete_chapter_memory(3)
        assert len(deleted) == 3  # 1 chapter + 2 scenes

        stats = episodic_memory.get_memory_stats()
        assert stats["total_episodic_entries"] == 0
        assert stats["scene_level_entries"] == 0


# ── Test 6: Motif Constraints Render Correctly in Template ────────


class TestMotifConstraintsRendering:
    """Verify motif-related rendering in draft_chapter.j2."""

    def test_motif_suggestions_block_exists(self) -> None:
        """Template has a conditional block for motif suggestions in the memory card."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "{% if memory.motif_suggestions" in content

    def test_motif_block_renders_priority(self) -> None:
        """Motif items render their priority as conditional language."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert 'item.priority == "high"' in content
        assert 'item.priority == "medium"' in content

    def test_motif_block_renders_motif_name(self) -> None:
        """Motif items render their name."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "{{ item.motif_name }}" in content

    def test_motif_block_renders_reason(self) -> None:
        """Motif items render their reason/suggestion from the card payload."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "{{ item.suggested_context or item.reason }}" in content

    def test_motif_block_has_section_title(self) -> None:
        """Motif section has a hard constraint title."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "★ 母题约束" in content

    def test_motif_block_conditional_suggested_context(self) -> None:
        """Motif items prefer suggested_context while preserving reason fallback."""
        template_path = (
            Path(__file__).parent.parent.parent.parent
            / "novel_forge"
            / "prompts"
            / "prompts"
            / "writing"
            / "draft_chapter.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "{{ item.suggested_context or item.reason }}" in content
