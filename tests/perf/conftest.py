"""Shared fixtures for performance regression tests (Wave 1+).

Provides:
- ``perf_probe_window``: a lightweight harness that mirrors DesktopWindow's
  ``_perf_probe_times`` instrumentation, with real ``_compute_snapshot_hash_incremental``
  and ``_build_snapshot_payload`` methods bound from the production class.
- Helpers for percentile computation and probe reset.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from novel_forge.desktop.window import NovelForgeDesktopWindow

# ---------------------------------------------------------------------------
# Percentile helpers
# ---------------------------------------------------------------------------


def compute_percentile(samples: list[float], percentile: float) -> float:
    """Compute a single percentile value from *samples*.

    Uses linear interpolation between the two nearest ranks (same method
    as numpy's default ``linear`` interpolation).

    Args:
        samples: Unsorted list of numeric samples.
        percentile: Percentile to compute, in ``[0, 100]``.

    Returns:
        The interpolated percentile value, or ``0.0`` if *samples* is empty.
    """
    if not samples:
        return 0.0
    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    if n == 1:
        return sorted_samples[0]
    k = (percentile / 100.0) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_samples[-1]
    d = k - f
    return sorted_samples[f] + d * (sorted_samples[c] - sorted_samples[f])


def compute_p50_p95_p99(samples: list[float]) -> dict[str, float]:
    """Return a dict with p50, p95, p99 for *samples*."""
    return {
        "p50": compute_percentile(samples, 50),
        "p95": compute_percentile(samples, 95),
        "p99": compute_percentile(samples, 99),
    }


def compute_percentiles(times: list[float]) -> dict[str, float]:
    """Return p50, p95, p99 for a list of elapsed-time samples (ms).

    .. deprecated:: Use :func:`compute_p50_p95_p99` instead.
    """
    return compute_p50_p95_p99(times)


def reset_probe(harness: _ProbeHarness) -> None:
    """Clear collected probe samples."""
    harness._perf_probe_times.clear()


# ---------------------------------------------------------------------------
# Probe harness — mirrors DesktopWindow's probe + hash computation
# ---------------------------------------------------------------------------


class _ProbeHarness:
    """Lightweight stand-in for NovelForgeDesktopWindow.

    Binds the real ``_compute_snapshot_hash_incremental`` and
    ``_build_snapshot_payload`` methods so we can measure their cost
    without constructing the full QMainWindow (which SIGSEGVs in
    offscreen test mode).

    The probe attributes (``_perf_probe_times``, ``_perf_probe_max_samples``)
    mirror what ``DesktopWindow.__init__`` adds in the production code.
    """

    # Bind real production methods
    _build_snapshot_payload = NovelForgeDesktopWindow._build_snapshot_payload
    _compute_snapshot_hash_incremental = (
        NovelForgeDesktopWindow._compute_snapshot_hash_incremental
    )
    _stable_snapshot_value = staticmethod(
        NovelForgeDesktopWindow._stable_snapshot_value
    )
    _stable_snapshot_json = staticmethod(
        NovelForgeDesktopWindow._stable_snapshot_json
    )

    def __init__(self) -> None:
        # Probe instrumentation (mirrors DesktopWindow.__init__)
        self._perf_probe_times: list[float] = []
        self._perf_probe_max_samples: int = 1000

        # State needed by _build_snapshot_payload / _compute_snapshot_hash_incremental
        self._last_payload_values: dict[str, Any] = {}
        self._last_payload_refs: dict[str, Any] = {}
        self._last_detail_ids: dict[str, int] = {}
        self._section_hash_cache: dict[str, str] = {}
        self._last_snapshot_payload: dict[str, Any] | None = None
        self._last_snapshot_hash: str | None = None


@pytest.fixture()
def perf_probe_window() -> _ProbeHarness:
    """Return a fresh probe harness; reset probe times before each test."""
    harness = _ProbeHarness()
    yield harness
    harness._perf_probe_times.clear()


# ---------------------------------------------------------------------------
# Synthetic snapshot builder
# ---------------------------------------------------------------------------


def build_synthetic_snapshot(
    *,
    num_projects: int = 5,
    chapters_per_project: int = 20,
    tmp_path: Path | None = None,
) -> Any:
    """Build a ``DesktopWorkspaceSnapshot`` with *num_projects* projects.

    Each project has *chapters_per_project* ``ChapterSummary`` entries in its
    ``ProjectDetail``.  The snapshot is fully in-memory (no filesystem I/O).
    """
    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import (
        ChapterSummary,
        ProjectDetail,
        WorkspaceOverview,
    )

    root = tmp_path or Path("/tmp/perf_test_workspace")

    projects: list[DesktopProjectItem] = []
    details: dict[str, ProjectDetail] = {}

    for i in range(num_projects):
        pid = f"project_{i:03d}"
        chapters = [
            ChapterSummary(
                chapter_number=c,
                title=f"Chapter {c}",
                word_count=3000 + c * 50,
                overall_score=0.75 + (c % 10) * 0.02,
                continuity_score=0.80 + (c % 8) * 0.015,
                updated_at=f"2026-01-{(c % 28) + 1:02d}T12:00:00Z",
                preview=f"Preview for chapter {c} of project {i}...",
            )
            for c in range(1, chapters_per_project + 1)
        ]
        detail = ProjectDetail(
            project_id=pid,
            mode="long",
            title=f"Test Project {i}",
            genre="fantasy",
            tone="epic",
            premise=f"A test premise for project {i}.",
            preview=f"Preview text for project {i}.",
            total_chapters=chapters_per_project,
            completed_chapters=chapters_per_project,
            latest_chapter=chapters_per_project,
            completion_ratio=1.0,
            has_outline=True,
            has_canon=True,
            updated_at="2026-01-15T12:00:00Z",
            chapters=chapters,
            recent_files=[f"data/{pid}/chapter_{c}.md" for c in range(1, 6)],
            artifact_counts={"chapters": chapters_per_project, "reports": 10},
        )
        details[pid] = detail

        projects.append(
            DesktopProjectItem(
                project_id=pid,
                title=f"Test Project {i}",
                mode="long",
                mode_label="长篇",
                status="completed",
                status_label="已完结",
                progress_label=f"{chapters_per_project}/{chapters_per_project}",
                progress_percent=100,
                last_updated_label="2026-01-15",
                headline=f"Headline for project {i}",
                next_action="回看章节与质量报告",
                genre="fantasy",
                tone="epic",
                completed_chapters=chapters_per_project,
                total_chapters=chapters_per_project,
                next_chapter=None,
                has_outline=True,
                has_canon=True,
            )
        )

    overview = WorkspaceOverview(
        storage_root=str(root),
        total_projects=num_projects,
        short_projects=0,
        long_projects=num_projects,
        total_generated_chapters=num_projects * chapters_per_project,
        providers=["mock"],
        default_provider="mock",
    )

    metrics = DesktopWorkspaceMetrics(
        total_projects=num_projects,
        total_chapters=num_projects * chapters_per_project,
        total_words=num_projects * chapters_per_project * 3500,
        configured_providers=1,
    )

    providers = [
        ProviderStatus(
            provider_id="mock",
            label="Mock",
            ready=True,
            configured=True,
            is_default=True,
            detail="Mock adapter",
        )
    ]

    return DesktopWorkspaceSnapshot(
        storage_root=root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=projects,
        featured_project=projects[0] if projects else None,
        details=details,
    )
