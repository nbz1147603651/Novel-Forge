"""Tests for stage-specific progress reporting in book consistency audit."""

from __future__ import annotations

from novel_forge.desktop.progress import (
    _compute_book_consistency_audit_chunk_progress,
    _compute_book_consistency_repair_progress,
    _compute_step_progress,
)


class TestStageProgress:
    """Tests for each stage reporting correct current/total."""

    def test_audit_chunk_progress_initial(self) -> None:
        """Audit chunk progress should return 0% when current=0."""
        result = _compute_book_consistency_audit_chunk_progress(
            {"current": 0, "total": 8}
        )
        assert result == 0

    def test_audit_chunk_progress_halfway(self) -> None:
        """Audit chunk progress should return 25% at halfway (4/8 chunks)."""
        result = _compute_book_consistency_audit_chunk_progress(
            {"current": 4, "total": 8}
        )
        assert result == 25

    def test_audit_chunk_progress_complete(self) -> None:
        """Audit chunk progress should return 50% when all chunks done."""
        result = _compute_book_consistency_audit_chunk_progress(
            {"current": 8, "total": 8}
        )
        assert result == 50

    def test_audit_chunk_progress_invalid_total(self) -> None:
        """Audit chunk progress should return None when total <= 0."""
        assert _compute_book_consistency_audit_chunk_progress(
            {"current": 0, "total": 0}
        ) is None
        assert _compute_book_consistency_audit_chunk_progress(
            {"current": 0, "total": -1}
        ) is None

    def test_audit_chunk_progress_missing_fields(self) -> None:
        """Audit chunk progress should return None when fields missing."""
        assert _compute_book_consistency_audit_chunk_progress({}) is None
        assert _compute_book_consistency_audit_chunk_progress(
            {"current": 0}
        ) is None
        assert _compute_book_consistency_audit_chunk_progress(
            {"total": 8}
        ) is None

    def test_verify_progress_initial(self) -> None:
        """Verify progress should return 50% when processed=0."""
        result = _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"current": 0, "total": 20, "processed": 0},
        )
        assert result == 50

    def test_verify_progress_halfway(self) -> None:
        """Verify progress should return 62% at halfway (10/20 issues)."""
        result = _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"current": 10, "total": 20, "processed": 10},
        )
        assert result == 62

    def test_verify_progress_complete(self) -> None:
        """Verify progress should return 75% when all issues verified."""
        result = _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"current": 20, "total": 20, "processed": 20},
        )
        assert result == 75

    def test_repair_progress_initial(self) -> None:
        """Repair progress should return 75% when processed=0."""
        result = _compute_book_consistency_repair_progress(
            {"current": 0, "total": 12, "processed": 0}
        )
        assert result == 75

    def test_repair_progress_halfway(self) -> None:
        """Repair progress should return 88% at halfway (6/12 chapters)."""
        result = _compute_book_consistency_repair_progress(
            {"current": 6, "total": 12, "processed": 6}
        )
        assert result == 88

    def test_repair_progress_complete(self) -> None:
        """Repair progress should return 100% when all chapters repaired."""
        result = _compute_book_consistency_repair_progress(
            {"current": 12, "total": 12, "processed": 12}
        )
        assert result == 100

    def test_repair_progress_invalid_total(self) -> None:
        """Repair progress should return None when total <= 0."""
        assert _compute_book_consistency_repair_progress(
            {"current": 0, "total": 0, "processed": 0}
        ) is None


class TestOverallPercentage:
    """Tests for weighted percentage calculation across phases."""

    def test_audit_phase_range(self) -> None:
        """Audit phase should map to 0-50% range."""
        for current, total in [(0, 10), (5, 10), (10, 10)]:
            result = _compute_book_consistency_audit_chunk_progress(
                {"current": current, "total": total}
            )
            assert result is not None
            assert 0 <= result <= 50

    def test_verify_phase_range(self) -> None:
        """Verify phase should map to 50-75% range."""
        for processed, total in [(0, 10), (5, 10), (10, 10)]:
            result = _compute_step_progress(
                "book_consistency",
                "running",
                "book_consistency_verify_progress",
                {"processed": processed, "total": total},
            )
            assert 50 <= result <= 75

    def test_repair_phase_range(self) -> None:
        """Repair phase should map to 75-100% range."""
        for processed, total in [(0, 10), (5, 10), (10, 10)]:
            result = _compute_book_consistency_repair_progress(
                {"processed": processed, "total": total}
            )
            assert result is not None
            assert 75 <= result <= 100

    def test_phase_boundaries_are_contiguous(self) -> None:
        """Phase boundaries should be contiguous: audit ends at 50, verify starts at 50."""
        audit_complete = _compute_book_consistency_audit_chunk_progress(
            {"current": 10, "total": 10}
        )
        verify_start = _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"processed": 0, "total": 10},
        )
        assert audit_complete == 50
        assert verify_start == 50

    def test_verify_to_repair_boundary(self) -> None:
        """Verify ends at 75, repair starts at 75."""
        verify_complete = _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"processed": 10, "total": 10},
        )
        repair_start = _compute_book_consistency_repair_progress(
            {"processed": 0, "total": 10}
        )
        assert verify_complete == 75
        assert repair_start == 75

    def test_weighted_percentages_match_phase_weights(self) -> None:
        """Phase ranges should reflect 50/25/25 weights."""
        audit_range = 50 - 0
        verify_range = 75 - 50
        repair_range = 100 - 75
        assert audit_range == 50
        assert verify_range == 25
        assert repair_range == 25


class TestButtonPercentageDisplay:
    """Tests for button showing accurate percentage."""

    def test_audit_chunk_display_percentage(self) -> None:
        """Button should show correct percentage for audit chunk progress."""
        result = _compute_book_consistency_audit_chunk_progress(
            {"current": 3, "total": 8}
        )
        assert result is not None
        assert result == 19

    def test_verify_display_percentage(self) -> None:
        """Button should show correct percentage for verify progress."""
        result = _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"processed": 5, "total": 20},
        )
        assert result == 56

    def test_repair_display_percentage(self) -> None:
        """Button should show correct percentage for repair progress."""
        result = _compute_book_consistency_repair_progress(
            {"processed": 3, "total": 12}
        )
        assert result == 81

    def test_percentage_never_exceeds_100(self) -> None:
        """Progress should never exceed 100% even with overflow data."""
        assert _compute_book_consistency_audit_chunk_progress(
            {"current": 20, "total": 10}
        ) == 50
        assert _compute_step_progress(
            "book_consistency",
            "running",
            "book_consistency_verify_progress",
            {"processed": 30, "total": 10},
        ) == 75
        assert _compute_book_consistency_repair_progress(
            {"processed": 20, "total": 10}
        ) == 100

    def test_percentage_never_below_0(self) -> None:
        """Progress should never be negative."""
        assert _compute_book_consistency_audit_chunk_progress(
            {"current": -5, "total": 10}
        ) == 0
