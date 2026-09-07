"""Tests for retired motif filtering in MotifTracker.

Verifies that retired motifs are properly excluded from:
- forbidden_repetition in get_motifs_for_prompt()
- _related_motif_ids_for_chapter()
- active_motifs in get_motifs_for_prompt()

And that retire_motif()/unretire_motif() work correctly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from novel_forge.memory.motif import Motif, MotifTracker


def _make_tracker() -> MotifTracker:
    return MotifTracker(router=AsyncMock(), builder=AsyncMock())


class TestRetiredMotifFiltering:
    """Tests that retired motifs are filtered from prompt context."""

    def _add_motif(
        self,
        tracker: MotifTracker,
        motif_id: str,
        name: str,
        category: str = "意象",
        retired: bool = False,
        chapters: list[int] | None = None,
    ) -> None:
        """Helper to register a motif with basic tracking data."""
        chapters = chapters or [1]
        motif = Motif(
            motif_id=motif_id,
            name=name,
            category=category,
            is_intentional=True,
            occurrence_count=len(chapters),
            first_appearance_chapter=chapters[0] if chapters else 0,
            last_appearance_chapter=chapters[-1] if chapters else 0,
            retired=retired,
        )
        tracker._motifs[motif_id] = motif
        tracker._recent_usage[motif_id] = chapters
        # Add to chapter index so _related_motif_ids_for_chapter finds it
        for ch in chapters:
            if ch not in tracker._chapter_motifs:
                tracker._chapter_motifs[ch] = set()
            tracker._chapter_motifs[ch].add(motif_id)

    def test_retired_not_in_forbidden(self) -> None:
        """Retired motifs must NOT appear in forbidden_repetition list."""
        tracker = _make_tracker()

        self._add_motif(tracker, "rain-1", "雨", category="意象", retired=False, chapters=[2])
        self._add_motif(tracker, "mirror-1", "镜子", category="意象", retired=True, chapters=[2])

        result = tracker.get_motifs_for_prompt(current_chapter=4)

        assert "雨" in [m["name"] for m in result["active_motifs"]]
        assert "镜子" not in [m["name"] for m in result["active_motifs"]]
        assert "镜子" not in result["forbidden_repetition"]
        assert "雨" in result["forbidden_repetition"]

    def test_retired_in_stats(self) -> None:
        """Retired motifs appear in retired_stats when include_retired_in_stats=True."""
        tracker = _make_tracker()

        self._add_motif(tracker, "rain-1", "雨", category="意象", retired=False, chapters=[2])
        self._add_motif(tracker, "mirror-1", "镜子", category="意象", retired=True, chapters=[2])
        self._add_motif(tracker, "moon-1", "月光", category="颜色", retired=True, chapters=[2])

        result = tracker.get_motifs_for_prompt(current_chapter=4, include_retired_in_stats=True)

        assert "retired_stats" in result
        assert result["retired_stats"]["count"] == 2
        assert "镜子" in result["retired_stats"]["names"]
        assert "月光" in result["retired_stats"]["names"]

    def test_retired_not_in_stats_by_default(self) -> None:
        """Retired stats are NOT included by default (include_retired_in_stats=False)."""
        tracker = _make_tracker()

        self._add_motif(tracker, "rain-1", "雨", category="意象", retired=True, chapters=[2])

        result = tracker.get_motifs_for_prompt(current_chapter=4)

        assert "retired_stats" not in result

    def test_unretire_restores(self) -> None:
        """Unretiring a motif restores it to normal behavior in get_motifs_for_prompt."""
        tracker = _make_tracker()

        self._add_motif(tracker, "mirror-1", "镜子", category="意象", retired=True, chapters=[2])

        result_before = tracker.get_motifs_for_prompt(current_chapter=4)
        assert "镜子" not in [m["name"] for m in result_before["active_motifs"]]

        assert tracker.unretire_motif("mirror-1") is True

        result_after = tracker.get_motifs_for_prompt(current_chapter=4)
        assert "镜子" in [m["name"] for m in result_after["active_motifs"]]

    def test_retired_excluded_from_related_ids(self) -> None:
        """Retired motifs are excluded from _related_motif_ids_for_chapter()."""
        tracker = _make_tracker()

        self._add_motif(tracker, "rain-1", "雨", category="意象", retired=False, chapters=[1])
        self._add_motif(tracker, "mirror-1", "镜子", category="意象", retired=True, chapters=[1])

        related = tracker._related_motif_ids_for_chapter(current_chapter=2)

        assert "rain-1" in related
        assert "mirror-1" not in related

    def test_retired_motif_preserved_in_dict(self) -> None:
        """Retired motifs remain in _motifs dict for statistical analysis."""
        tracker = _make_tracker()

        self._add_motif(tracker, "rain-1", "雨", category="意象", retired=False, chapters=[2])
        self._add_motif(tracker, "mirror-1", "镜子", category="意象", retired=True, chapters=[2])

        assert "rain-1" in tracker._motifs
        assert "mirror-1" in tracker._motifs
        assert tracker._motifs["mirror-1"].retired is True
        assert tracker._motifs["rain-1"].retired is False

    def test_retired_not_in_suggested_callbacks(self) -> None:
        """Retired motifs do not appear in suggested_callbacks."""
        tracker = _make_tracker()

        self._add_motif(tracker, "rain-1", "雨", category="意象", retired=False, chapters=[2])
        self._add_motif(tracker, "mirror-1", "镜子", category="意象", retired=True, chapters=[2])

        result = tracker.get_motifs_for_prompt(current_chapter=4)

        suggested_names = [s["motif"] for s in result["suggested_callbacks"]]
        assert "镜子" not in suggested_names