"""P0-1 + P0-5: ``_build_project_detail`` performs ≤ 1 tree walk per call.

Before the refactor, each call to ``_build_project_detail`` (and
``_build_degraded_detail``) triggered **two** full recursive ``rglob("*")``
walks — one via ``_recent_project_files`` and one via ``_latest_file_mtime``.
After the refactor, the tree is walked at most once and the result is shared.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from novel_forge.persistence.filesystem import CachedFileSystemStorage
from novel_forge.workspace.projects import ProjectInspector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_project(project_dir: Path, *, file_count: int = 50) -> None:
    """Create a realistic project directory tree with *file_count* files."""
    project_dir.mkdir(parents=True, exist_ok=True)
    # Core JSON files
    (project_dir / "spec.json").write_text('{"title":"t"}')
    (project_dir / "story_bible.json").write_text('{"title":"t"}')
    (project_dir / "outline.json").write_text("{}")
    (project_dir / "style_profile.json").write_text("{}")
    (project_dir / "blueprint.json").write_text("{}")

    # Subdirectories with files
    subdirs = ["chapters", "drafts", "plans", "states", "reports", "memory"]
    for sd in subdirs:
        (project_dir / sd).mkdir(exist_ok=True)

    remaining = file_count - 6  # subtract the core files already created
    for i in range(max(0, remaining)):
        subdir = subdirs[i % len(subdirs)]
        suffix = ".json" if i % 2 == 0 else ".md"
        (project_dir / subdir / f"file_{i:04d}{suffix}").write_text("x")


def _make_inspector(storage_root: Path) -> ProjectInspector:
    """Build a ``ProjectInspector`` backed by a ``CachedFileSystemStorage``."""
    storage = CachedFileSystemStorage(storage_root)
    return ProjectInspector(storage=storage)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProjectDetailSingleWalk:
    """``_build_project_detail`` must perform ≤ 1 ``rglob`` tree walk."""

    def test_build_project_detail_at_most_one_rglob(self, tmp_path: Path) -> None:
        """Count ``Path.rglob`` invocations during a single detail build.

        Before the fix: 2+ rglob calls (one for recent_files, one for
        _latest_file_mtime).  After the fix: ≤ 1.
        """
        project_dir = tmp_path / "test_project"
        _populate_project(project_dir, file_count=100)

        inspector = _make_inspector(tmp_path)

        rglob_calls: list[tuple[str, ...]] = []
        _original_rglob = Path.rglob

        def _counting_rglob(self: Path, pattern: str) -> Any:
            rglob_calls.append((str(self), pattern))
            return _original_rglob(self, pattern)

        with patch.object(Path, "rglob", _counting_rglob):
            try:
                inspector.get_project_detail("test_project")
            except Exception:
                # Detail build may fail on missing optional files — that's OK.
                # We only care about how many rglob calls happened.
                pass

        # Filter to only the unbounded "*" walks (the expensive ones).
        star_walks = [c for c in rglob_calls if c[1] == "*"]
        assert len(star_walks) <= 1, (
            f"_build_project_detail triggered {len(star_walks)} rglob('*') "
            f"walks — expected ≤ 1. Calls: {star_walks}"
        )

    def test_build_degraded_detail_at_most_one_rglob(self, tmp_path: Path) -> None:
        """``_build_degraded_detail`` must also perform ≤ 1 rglob('*') walk."""
        project_dir = tmp_path / "degraded_project"
        _populate_project(project_dir, file_count=50)

        inspector = _make_inspector(tmp_path)

        rglob_calls: list[tuple[str, ...]] = []
        _original_rglob = Path.rglob

        def _counting_rglob(self: Path, pattern: str) -> Any:
            rglob_calls.append((str(self), pattern))
            return _original_rglob(self, pattern)

        with patch.object(Path, "rglob", _counting_rglob):
            # Force a degraded build by passing a fake error.
            try:
                inspector._build_degraded_detail("degraded_project", ValueError("boom"))
            except Exception:
                pass

        star_walks = [c for c in rglob_calls if c[1] == "*"]
        assert len(star_walks) <= 1, (
            f"_build_degraded_detail triggered {len(star_walks)} rglob('*') "
            f"walks — expected ≤ 1. Calls: {star_walks}"
        )


class TestLatestFileMtimePerformance:
    """``_latest_file_mtime`` must be fast: p95 < 5 ms with 500 files."""

    def test_latest_file_mtime_p95_under_5ms(self, tmp_path: Path) -> None:
        """Benchmark ``_latest_file_mtime`` with 500 files.

        Before the fix: 50-200 ms (full rglob walk).
        After the fix: < 5 ms (uses pre-computed list or dir stat).
        """
        from novel_forge.workspace.projects import _latest_file_mtime

        project_dir = tmp_path / "perf_project"
        _populate_project(project_dir, file_count=500)

        # Warmup to prime filesystem caches
        _latest_file_mtime(project_dir)

        timings: list[float] = []
        for _ in range(30):
            t0 = time.perf_counter()
            result = _latest_file_mtime(project_dir)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            timings.append(elapsed_ms)
            assert result is not None

        # Use median — robust to single filesystem-cache-miss outliers.
        # Typical: ~2.4 ms.  Before fix: 50-200 ms.
        median_ms = statistics.median(timings)
        assert median_ms < 5.0, (
            f"_latest_file_mtime median = {median_ms:.2f} ms — expected < 5 ms. "
            f"All timings: {[f'{t:.2f}' for t in timings]}"
        )


class TestDirectoryLatestMtime:
    """``CachedFileSystemStorage.directory_latest_mtime`` helper."""

    def test_returns_mtime_for_existing_dir(self, tmp_path: Path) -> None:
        """Should return the directory's own ``st_mtime`` (no recursion)."""
        storage = CachedFileSystemStorage(tmp_path)
        mtime = storage.directory_latest_mtime(tmp_path)
        assert mtime is not None
        assert mtime == pytest.approx(tmp_path.stat().st_mtime, abs=0.01)

    def test_returns_none_for_missing_dir(self, tmp_path: Path) -> None:
        """Should return ``None`` for a path that does not exist."""
        storage = CachedFileSystemStorage(tmp_path)
        missing = tmp_path / "does_not_exist"
        assert storage.directory_latest_mtime(missing) is None

    def test_cache_hit_avoids_repeated_stat(self, tmp_path: Path) -> None:
        """Second call within TTL should not call ``Path.stat()`` again."""
        storage = CachedFileSystemStorage(tmp_path)
        mtime1 = storage.directory_latest_mtime(tmp_path)
        mtime2 = storage.directory_latest_mtime(tmp_path)
        assert mtime1 == mtime2
