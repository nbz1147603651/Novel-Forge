"""QualityTrendTracker — tracks cross-chapter quality metrics over time.

Detects slow quality degradation by maintaining sliding-window trends for:
- eval_score
- reading_power.overall_score
- continuity_score
- promise_aging_score (weighted foreshadow age)
- character_state_conflicts (rejected deltas count)
- foreshadow_overdue_count
- retrieval_eval.character_recall

Persists to ``states/quality_trend.json``.
Emits ``quality_trend_warning`` when any metric declines for N consecutive chapters.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_WINDOW = 5
_DECLINE_THRESHOLD = 1.0
_PROMISE_OVERDUE_THRESHOLD = 50


@dataclass
class ChapterQualitySnapshot:
    """Quality metrics for a single chapter."""

    chapter: int
    eval_score: float = 0.0
    reading_power_score: float = 0.0
    continuity_score: float = 0.0
    promise_aging_score: float = 0.0
    character_state_conflicts: int = 0
    foreshadow_overdue_count: int = 0
    character_recall: float = 0.0


@dataclass
class QualityTrendReport:
    """Aggregated quality trend report."""

    chapters_tracked: int = 0
    eval_score_trend: list[float] = field(default_factory=list)
    reading_power_trend: list[float] = field(default_factory=list)
    continuity_trend: list[float] = field(default_factory=list)
    promise_aging_trend: list[float] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


class QualityTrendTracker:
    """Tracks quality metrics across chapters and detects degradation.

    Parameters
    ----------
    states_dir:
        Path to the project's ``states/`` directory.
    window:
        Number of chapters for sliding window analysis.
    """

    def __init__(
        self,
        states_dir: Path,
        *,
        window: int = _DEFAULT_WINDOW,
    ) -> None:
        self._dir = Path(states_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "quality_trend.json"
        self._window = window
        self._snapshots: list[ChapterQualitySnapshot] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load existing trend data from disk."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in data.get("snapshots", []):
                self._snapshots.append(ChapterQualitySnapshot(**entry))
        except Exception as exc:
            _log.warning("quality_trend_load_failed | path=%s | error=%s", self._path, exc)

    def _save(self) -> None:
        """Persist trend data to disk."""
        data = {
            "snapshots": [
                {
                    "chapter": s.chapter,
                    "eval_score": s.eval_score,
                    "reading_power_score": s.reading_power_score,
                    "continuity_score": s.continuity_score,
                    "promise_aging_score": s.promise_aging_score,
                    "character_state_conflicts": s.character_state_conflicts,
                    "foreshadow_overdue_count": s.foreshadow_overdue_count,
                    "character_recall": s.character_recall,
                }
                for s in self._snapshots
            ]
        }
        try:
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            _log.warning("quality_trend_save_failed | path=%s | error=%s", self._path, exc)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_chapter(
        self,
        chapter: int,
        *,
        eval_score: float = 0.0,
        reading_power_score: float = 0.0,
        continuity_score: float = 0.0,
        promise_aging_score: float = 0.0,
        character_state_conflicts: int = 0,
        foreshadow_overdue_count: int = 0,
        character_recall: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Record quality metrics for a chapter. Returns list of warnings."""
        # Remove existing snapshot for this chapter (idempotent)
        self._snapshots = [s for s in self._snapshots if s.chapter != chapter]
        snapshot = ChapterQualitySnapshot(
            chapter=chapter,
            eval_score=eval_score,
            reading_power_score=reading_power_score,
            continuity_score=continuity_score,
            promise_aging_score=promise_aging_score,
            character_state_conflicts=character_state_conflicts,
            foreshadow_overdue_count=foreshadow_overdue_count,
            character_recall=character_recall,
        )
        self._snapshots.append(snapshot)
        self._snapshots.sort(key=lambda s: s.chapter)
        self._save()
        return self._check_trends()

    # ------------------------------------------------------------------
    # Trend analysis
    # ------------------------------------------------------------------

    def _check_trends(self) -> list[dict[str, Any]]:
        """Check for declining trends in recent chapters."""
        warnings: list[dict[str, Any]] = []
        if len(self._snapshots) < self._window:
            return warnings

        recent = self._snapshots[-self._window:]

        # Check eval_score trend
        eval_scores = [s.eval_score for s in recent]
        if self._is_declining(eval_scores) and (eval_scores[0] - eval_scores[-1]) > _DECLINE_THRESHOLD:
            warnings.append({
                "type": "quality_trend_warning",
                "metric": "eval_score",
                "decline": eval_scores[0] - eval_scores[-1],
                "chapters": [s.chapter for s in recent],
            })

        # Check reading_power trend
        rp_scores = [s.reading_power_score for s in recent]
        if self._is_declining(rp_scores) and (rp_scores[0] - rp_scores[-1]) > _DECLINE_THRESHOLD:
            warnings.append({
                "type": "quality_trend_warning",
                "metric": "reading_power_score",
                "decline": rp_scores[0] - rp_scores[-1],
                "chapters": [s.chapter for s in recent],
            })

        # Check promise_aging_score (increasing is bad)
        pa_scores = [s.promise_aging_score for s in recent]
        if self._is_increasing(pa_scores) and pa_scores[-1] > _PROMISE_OVERDUE_THRESHOLD:
            warnings.append({
                "type": "promise_overdue_warning",
                "metric": "promise_aging_score",
                "current_value": pa_scores[-1],
                "chapters": [s.chapter for s in recent],
            })

        # Check foreshadow_overdue_count
        fo_counts = [s.foreshadow_overdue_count for s in recent]
        if fo_counts[-1] > 0 and self._is_increasing([float(x) for x in fo_counts]):
            warnings.append({
                "type": "foreshadow_overdue_warning",
                "metric": "foreshadow_overdue_count",
                "current_value": fo_counts[-1],
                "chapters": [s.chapter for s in recent],
            })

        for w in warnings:
            _log.info("quality_trend_alert | %s", w)

        return warnings

    @staticmethod
    def _is_declining(values: list[float]) -> bool:
        """Check if values are monotonically declining."""
        if len(values) < 2:
            return False
        return all(values[i] >= values[i + 1] for i in range(len(values) - 1))

    @staticmethod
    def _is_increasing(values: list[float]) -> bool:
        """Check if values are monotonically increasing."""
        if len(values) < 2:
            return False
        return all(values[i] <= values[i + 1] for i in range(len(values) - 1))

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_report(self) -> QualityTrendReport:
        """Generate a trend report."""
        window = min(self._window, len(self._snapshots))
        recent = self._snapshots[-window:] if window > 0 else []

        return QualityTrendReport(
            chapters_tracked=len(self._snapshots),
            eval_score_trend=[s.eval_score for s in recent],
            reading_power_trend=[s.reading_power_score for s in recent],
            continuity_trend=[s.continuity_score for s in recent],
            promise_aging_trend=[s.promise_aging_score for s in recent],
        )

    def compute_promise_aging_score(
        self,
        promise_ledger: list[Any],
        current_chapter: int,
        chapters_per_volume: int = 20,
    ) -> float:
        """Compute weighted promise aging score.

        Formula: sum of (age_chapters × severity_weight) for each overdue promise.
        A promise is "overdue" if:
        - status is ``planted`` and planted_chapter is > 3 volumes ago
        - status is ``hinted`` but not ``partially_paid`` after 2 volumes
        """
        score = 0.0
        three_volumes_ago = current_chapter - 3 * chapters_per_volume
        two_volumes_ago = current_chapter - 2 * chapters_per_volume

        for promise in promise_ledger:
            planted = getattr(promise, "planted_chapter", 0)
            status = getattr(promise, "status", "planted")
            age = current_chapter - planted

            if status == "planted" and planted < three_volumes_ago:
                # Overdue foreshadow: planted > 3 volumes ago, still not hinted
                score += age * 2.0  # high weight for completely ignored promises
            elif status == "hinted" and planted < two_volumes_ago:
                # Partially addressed but not paid off
                score += age * 1.0
            elif status == "partially_paid" and planted < three_volumes_ago:
                # Slow payoff but at least progressing
                score += age * 0.3

        return score


__all__ = ["ChapterQualitySnapshot", "QualityTrendReport", "QualityTrendTracker"]
