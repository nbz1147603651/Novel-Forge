"""Tests for CritiqueIndex persistence in EpisodicMemory."""

from __future__ import annotations

import pytest

from novel_forge.memory.critic import CritiqueIssue, CritiqueReport
from novel_forge.memory.episodic import CritiqueIndex, EpisodicMemory


@pytest.fixture
def episodic_memory() -> EpisodicMemory:
    return EpisodicMemory(use_mock_embeddings=True)


def _make_report(
    chapter_number: int,
    issues: list[CritiqueIssue],
) -> CritiqueReport:
    return CritiqueReport(
        chapter_number=chapter_number,
        overall_score=10.0,
        issues=issues,
    )


def _make_issue(
    severity: str = "critical",
    issue_type: str = "continuity_error",
    chapter: int = 1,
) -> CritiqueIssue:
    return CritiqueIssue(
        issue_type=issue_type,
        severity=severity,
        summary=f"Test {issue_type} issue in chapter {chapter}",
        evidence=f"Evidence for {issue_type}",
        affected_chapters=[chapter],
        suggested_fix=f"Fix {issue_type}",
        confidence=0.9,
    )


class TestCritiqueIndexDataclass:
    def test_signature_is_deterministic(self) -> None:
        entry = CritiqueIndex(
            chapter_number=1,
            issue_type="continuity_error",
            severity="critical",
            summary="Test issue",
        )
        assert entry.signature() == entry.signature()

    def test_signature_differs_by_chapter(self) -> None:
        a = CritiqueIndex(
            chapter_number=1,
            issue_type="continuity_error",
            severity="critical",
            summary="Same summary",
        )
        b = CritiqueIndex(
            chapter_number=2,
            issue_type="continuity_error",
            severity="critical",
            summary="Same summary",
        )
        assert a.signature() != b.signature()

    def test_scene_index_is_negative_one(self) -> None:
        entry = CritiqueIndex(
            chapter_number=1,
            issue_type="continuity_error",
            severity="high",
            summary="Test",
        )
        assert entry.scene_index == -1


class TestIndexCritique:
    @pytest.mark.asyncio
    async def test_critical_issue_persisted(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)

        assert len(sigs) == 1
        assert len(episodic_memory._critique_index) == 1
        assert 1 in episodic_memory._chapter_critiques

    @pytest.mark.asyncio
    async def test_high_issue_persisted(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(1, [_make_issue(severity="high", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)

        assert len(sigs) == 1

    @pytest.mark.asyncio
    async def test_medium_issue_not_persisted(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(1, [_make_issue(severity="medium", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)

        assert len(sigs) == 0
        assert len(episodic_memory._critique_index) == 0

    @pytest.mark.asyncio
    async def test_low_issue_not_persisted(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(1, [_make_issue(severity="low", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)

        assert len(sigs) == 0

    @pytest.mark.asyncio
    async def test_mixed_severity_only_critical_high_persisted(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        issues = [
            _make_issue(severity="critical", issue_type="continuity_error", chapter=1),
            _make_issue(severity="high", issue_type="character_inconsistency", chapter=1),
            _make_issue(severity="medium", issue_type="pacing_issue", chapter=1),
            _make_issue(severity="low", issue_type="thematic_drift", chapter=1),
        ]
        report = _make_report(1, issues)
        sigs = await episodic_memory.index_critique(1, report)

        assert len(sigs) == 2
        assert len(episodic_memory._critique_index) == 2

    @pytest.mark.asyncio
    async def test_deduplication(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs1 = await episodic_memory.index_critique(1, report)
        sigs2 = await episodic_memory.index_critique(1, report)

        assert sigs1 == sigs2
        assert len(episodic_memory._critique_index) == 1


class TestDeleteChapterMemoryWithCritiques:
    @pytest.mark.asyncio
    async def test_delete_chapter_removes_critiques(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(3, [_make_issue(severity="critical", chapter=3)])
        await episodic_memory.index_critique(3, report)

        deleted = episodic_memory.delete_chapter_memory(3)

        assert len(deleted) >= 1
        assert len(episodic_memory._critique_index) == 0
        assert 3 not in episodic_memory._chapter_critiques

    @pytest.mark.asyncio
    async def test_delete_other_chapter_preserves_critiques(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report(5, [_make_issue(severity="critical", chapter=5)])
        await episodic_memory.index_critique(5, report)

        episodic_memory.delete_chapter_memory(3)

        assert len(episodic_memory._critique_index) == 1
        assert 5 in episodic_memory._chapter_critiques


class TestMemoryStatsWithCritiques:
    @pytest.mark.asyncio
    async def test_stats_include_critique_count(self, episodic_memory: EpisodicMemory) -> None:
        stats = episodic_memory.get_memory_stats()
        assert "total_critique_entries" in stats
        assert stats["total_critique_entries"] == 0

    @pytest.mark.asyncio
    async def test_stats_reflect_indexed_critiques(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(
            1,
            [
                _make_issue(severity="critical", issue_type="continuity_error", chapter=1),
                _make_issue(severity="high", issue_type="character_inconsistency", chapter=1),
            ],
        )
        await episodic_memory.index_critique(1, report)

        stats = episodic_memory.get_memory_stats()
        assert stats["total_critique_entries"] == 2
        assert stats["total_vectors_indexed"] == 2


class TestSearchBySemanticWithIssueType:
    @pytest.mark.asyncio
    async def test_filter_by_issue_type(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report(
            1,
            [
                _make_issue(severity="critical", issue_type="continuity_error", chapter=1),
                _make_issue(severity="critical", issue_type="character_inconsistency", chapter=1),
            ],
        )
        await episodic_memory.index_critique(1, report)

        results = await episodic_memory.search_by_semantic(
            query="test issue",
            issue_type="continuity_error",
            top_k=10,
            min_relevance=0.0,
        )

        for r in results:
            meta = r.metadata or {}
            if "issue_type" in meta:
                assert meta["issue_type"] == "continuity_error"


class TestSearchSimilarCritiques:
    @pytest.mark.asyncio
    async def test_returns_repair_history_and_excludes_current_chapter(
        self,
        episodic_memory: EpisodicMemory,
    ) -> None:
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        episodic_memory.record_repair_result(
            sigs[0],
            chapter=1,
            round_num=1,
            strategy="fulltext",
            result="success",
            score_before=6.0,
            score_after=8.8,
        )

        current_report = _make_report(2, [_make_issue(severity="critical", chapter=2)])
        await episodic_memory.index_critique(2, current_report)

        results = await episodic_memory.search_similar_critiques(
            issue_type="continuity_error",
            summary="Test continuity_error issue in chapter 2",
            current_chapter=2,
            top_k=5,
            min_relevance=-1.0,
        )

        assert results
        assert all(item["chapter_number"] != 2 for item in results)
        assert results[0]["repair_attempts"][0]["strategy"] == "fulltext"


class TestRepairAttemptsTracking:
    """Test the newly activated repair_attempts, failure_pattern, and lesson_learned fields."""

    @pytest.mark.asyncio
    async def test_record_repair_result_updates_entry(self, episodic_memory: EpisodicMemory) -> None:
        """Test that recording a repair result updates the CritiqueIndex entry."""
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        sig = sigs[0]
        
        # Record a repair attempt
        result = episodic_memory.record_repair_result(
            sig,
            chapter=1,
            round_num=1,
            strategy="patch",
            result="regression",
            new_issues=["time_format_error"],
            score_before=6.5,
            score_after=5.0,
        )
        
        assert result is True
        entry = episodic_memory._critique_index[sig]
        assert len(entry.repair_attempts) == 1
        assert entry.repair_attempts[0]["round"] == 1
        assert entry.repair_attempts[0]["strategy"] == "patch"
        assert entry.repair_attempts[0]["result"] == "regression"
        assert entry.repair_attempts[0]["score_before"] == 6.5
        assert entry.repair_attempts[0]["score_after"] == 5.0

    @pytest.mark.asyncio
    async def test_multiple_repair_attempts_accumulate(self, episodic_memory: EpisodicMemory) -> None:
        """Test that multiple repair attempts are accumulated."""
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        sig = sigs[0]
        
        # Record 3 repair attempts
        episodic_memory.record_repair_result(sig, chapter=1, round_num=1, strategy="patch", result="regression", score_before=6.5, score_after=5.0)
        episodic_memory.record_repair_result(sig, chapter=1, round_num=2, strategy="fulltext", result="no_op", score_before=5.0, score_after=5.2)
        episodic_memory.record_repair_result(sig, chapter=1, round_num=3, strategy="rewrite", result="success", score_before=5.2, score_after=8.5)
        
        entry = episodic_memory._critique_index[sig]
        assert len(entry.repair_attempts) == 3

    @pytest.mark.asyncio
    async def test_failure_pattern_generated_after_multiple_attempts(self, episodic_memory: EpisodicMemory) -> None:
        """Test that failure_pattern is automatically generated after multiple attempts."""
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        sig = sigs[0]
        
        # Record attempts that introduce the same new issue
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=1, strategy="patch", result="regression",
            new_issues=["time_format_error", "character_state"],
            score_before=6.5, score_after=5.0,
        )
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=2, strategy="fulltext", result="regression",
            new_issues=["time_format_error", "location_jump"],
            score_before=5.0, score_after=4.5,
        )
        
        entry = episodic_memory._critique_index[sig]
        assert entry.failure_pattern is not None
        assert "time_format_error" in entry.failure_pattern

    @pytest.mark.asyncio
    async def test_lesson_learned_generated_with_success(self, episodic_memory: EpisodicMemory) -> None:
        """Test that lesson_learned is generated when there's a successful attempt."""
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        sig = sigs[0]
        
        # Record failed attempts that introduce the same issue (to trigger failure_pattern)
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=1, strategy="patch", result="regression",
            new_issues=["time_format_error", "character_state"],
            score_before=6.5, score_after=5.0,
        )
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=2, strategy="patch", result="regression",
            new_issues=["time_format_error", "location_jump"],  # time_format_error appears twice
            score_before=5.0, score_after=4.5,
        )
        # Record successful attempt
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=3, strategy="fulltext", result="success",
            score_before=4.5, score_after=8.5,
        )
        
        entry = episodic_memory._critique_index[sig]
        assert entry.lesson_learned is not None
        assert "fulltext" in entry.lesson_learned  # Best strategy mentioned

    @pytest.mark.asyncio
    async def test_lesson_learned_generated_without_success(self, episodic_memory: EpisodicMemory) -> None:
        """Test that lesson_learned is generated even without success (caution advice)."""
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        sig = sigs[0]
        
        # Record attempts that all fail and introduce the same issue
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=1, strategy="patch", result="regression",
            new_issues=["time_format_error", "character_state"],
            score_before=6.5, score_after=5.0,
        )
        episodic_memory.record_repair_result(
            sig, chapter=1, round_num=2, strategy="fulltext", result="regression",
            new_issues=["time_format_error", "location_jump"],  # time_format_error appears twice
            score_before=5.0, score_after=4.5,
        )
        
        entry = episodic_memory._critique_index[sig]
        assert entry.failure_pattern is not None  # Should have pattern now
        assert entry.lesson_learned is not None
        assert "格外小心" in entry.lesson_learned  # Caution advice

    @pytest.mark.asyncio
    async def test_record_repair_result_missing_signature(self, episodic_memory: EpisodicMemory) -> None:
        """Test that recording with missing signature returns False."""
        result = episodic_memory.record_repair_result(
            "nonexistent_signature",
            chapter=1, round_num=1, strategy="patch", result="success",
        )
        assert result is False


class TestPruneCritiqueIndex:
    """Test the prune_critique_index method for preventing unbounded growth."""

    @pytest.mark.asyncio
    async def test_prune_distance_threshold(self, episodic_memory: EpisodicMemory) -> None:
        """Test that entries beyond distance threshold without lesson are removed."""
        # Add entries at various distances
        for ch in [1, 5, 10, 25, 30]:
            report = _make_report(ch, [_make_issue(severity="critical", chapter=ch)])
            await episodic_memory.index_critique(ch, report)
        
        # Current chapter is 30, distance threshold default is 20
        # Chapter 1 (distance 29) and 5 (distance 25) should be removed if no lesson
        stats = episodic_memory.prune_critique_index(current_chapter=30)
        
        assert stats["removed_distance"] >= 2  # Chapters 1 and 5 should be removed
        assert stats["remaining"] <= 3  # Chapters 10, 25, 30 should remain

    @pytest.mark.asyncio
    async def test_prune_preserves_entries_with_lesson(self, episodic_memory: EpisodicMemory) -> None:
        """Test that entries with lesson_learned are preserved even if far away."""
        # Add a far-away entry with lesson
        report = _make_report(1, [_make_issue(severity="critical", chapter=1)])
        sigs = await episodic_memory.index_critique(1, report)
        sig = sigs[0]
        
        # Add lesson to the entry
        entry = episodic_memory._critique_index[sig]
        entry.lesson_learned = "Important lesson about continuity"
        
        # Add a far-away entry without lesson
        report2 = _make_report(5, [_make_issue(severity="critical", chapter=5)])
        await episodic_memory.index_critique(5, report2)
        
        # Prune with current chapter at 30
        episodic_memory.prune_critique_index(current_chapter=30)
        
        # Entry at chapter 1 should be preserved (has lesson)
        assert len(episodic_memory._critique_index) == 1
        assert 1 in episodic_memory._chapter_critiques

    @pytest.mark.asyncio
    async def test_prune_per_chapter_cap(self, episodic_memory: EpisodicMemory) -> None:
        """Test that per-chapter cap is enforced."""
        # Add 15 entries to chapter 1 (exceeds default max_per_chapter=10)
        for i in range(15):
            report = _make_report(
                1,
                [_make_issue(
                    severity="critical",
                    issue_type=f"error_{i}",
                    chapter=1,
                )]
            )
            await episodic_memory.index_critique(1, report)
        
        stats = episodic_memory.prune_critique_index(current_chapter=1)
        
        assert stats["removed_per_chapter"] >= 5
        assert stats["remaining"] <= 10

    @pytest.mark.asyncio
    async def test_prune_per_chapter_preserves_lessons(self, episodic_memory: EpisodicMemory) -> None:
        """Test that entries with lessons are prioritized when enforcing per-chapter cap."""
        # Add entries, some with lessons
        for i in range(12):
            report = _make_report(
                1,
                [_make_issue(
                    severity="critical",
                    issue_type=f"error_{i}",
                    chapter=1,
                )]
            )
            sigs = await episodic_memory.index_critique(1, report)
            sig = sigs[0]
            
            # Give lessons to first 3 entries
            if i < 3:
                entry = episodic_memory._critique_index[sig]
                entry.lesson_learned = f"Lesson {i}"
        
        stats = episodic_memory.prune_critique_index(current_chapter=1, max_per_chapter=10)
        
        # Entries with lessons should be preserved
        assert stats["remaining"] == 10
        # Check that entries with lessons are still there
        entries_with_lesson = sum(
            1 for e in episodic_memory._critique_index.values()
            if e.lesson_learned
        )
        assert entries_with_lesson == 3

    @pytest.mark.asyncio
    async def test_prune_hard_cap(self, episodic_memory: EpisodicMemory) -> None:
        """Test that hard cap is enforced."""
        # Add 120 entries across different chapters
        for ch in range(1, 121):
            report = _make_report(ch, [_make_issue(severity="critical", chapter=ch)])
            await episodic_memory.index_critique(ch, report)
        
        # Prune with max_entries=100
        stats = episodic_memory.prune_critique_index(
            current_chapter=60,
            max_entries=100,
            distance_threshold=100,  # Disable distance pruning
        )
        
        assert stats["removed_hard_cap"] >= 20
        assert stats["remaining"] <= 100

    @pytest.mark.asyncio
    async def test_prune_hard_cap_preserves_lessons(self, episodic_memory: EpisodicMemory) -> None:
        """Test that hard cap preserves entries with lessons."""
        # Add 110 entries, first 20 have lessons
        sigs_with_lessons: list[str] = []
        for ch in range(1, 111):
            report = _make_report(ch, [_make_issue(severity="critical", chapter=ch)])
            sigs = await episodic_memory.index_critique(ch, report)
            sig = sigs[0]
            
            if ch <= 20:
                entry = episodic_memory._critique_index[sig]
                entry.lesson_learned = f"Lesson for chapter {ch}"
                sigs_with_lessons.append(sig)
        
        stats = episodic_memory.prune_critique_index(
            current_chapter=60,
            max_entries=100,
            distance_threshold=100,
        )
        
        # All entries with lessons should be preserved
        assert stats["remaining"] == 100
        for sig in sigs_with_lessons:
            assert sig in episodic_memory._critique_index

    @pytest.mark.asyncio
    async def test_prune_empty_index(self, episodic_memory: EpisodicMemory) -> None:
        """Test that pruning empty index returns zero stats."""
        stats = episodic_memory.prune_critique_index(current_chapter=1)
        
        assert stats["removed_distance"] == 0
        assert stats["removed_per_chapter"] == 0
        assert stats["removed_hard_cap"] == 0
        assert stats["remaining"] == 0

    @pytest.mark.asyncio
    async def test_prune_custom_parameters(self, episodic_memory: EpisodicMemory) -> None:
        """Test that custom parameters are respected."""
        # Add entries
        for ch in [1, 10, 20, 30, 40]:
            report = _make_report(ch, [_make_issue(severity="critical", chapter=ch)])
            await episodic_memory.index_critique(ch, report)
        
        # Prune with custom parameters
        stats = episodic_memory.prune_critique_index(
            current_chapter=25,
            max_entries=3,
            distance_threshold=10,  # Only chapters 15-35 are within range
            max_per_chapter=2,
        )
        
        assert stats["remaining"] <= 3
