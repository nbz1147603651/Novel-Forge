"""Perf regression: _on_workspace_refreshed main-thread blocking time.

Measures the p95 elapsed time of the expensive snapshot-hash computation
(``_compute_snapshot_hash_incremental`` + ``_build_snapshot_payload``) for a
workspace with 5 projects × 20 chapters.  The probe instrumentation mirrors
what ``DesktopWindow._on_workspace_refreshed`` does in production.

Threshold: p95 < 50ms (Wave 3 target; was 100ms in Wave 2, 200ms in Wave 1).

RED phase: before ``_perf_probe_times`` is added to ``DesktopWindow.__init__``,
the harness would not have the probe attributes and the test would fail with
``AttributeError``.  After the probe is wired, the test passes.
"""

from __future__ import annotations

import time

import pytest

from tests.perf.conftest import (
    _ProbeHarness,
    build_synthetic_snapshot,
    compute_p50_p95_p99,
    reset_probe,
)

pytestmark = pytest.mark.perf

# Number of iterations for stable p95 measurement.
_ITERATIONS = 50

# p95 threshold in milliseconds (Wave 3 target).
_P95_THRESHOLD_MS = 50.0


class TestWorkspaceRefreshBlocking:
    """Assert that snapshot hashing for a 5×20 workspace stays under 50ms p95."""

    def test_p95_snapshot_hash_under_50ms(
        self, perf_probe_window: _ProbeHarness, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Core assertion: p95 of _compute_snapshot_hash_incremental < 50ms."""
        snapshot = build_synthetic_snapshot(
            num_projects=5,
            chapters_per_project=20,
            tmp_path=tmp_path,  # type: ignore[arg-type]
        )
        harness = perf_probe_window

        # Warm-up run (populates caches)
        harness._compute_snapshot_hash_incremental(snapshot)
        reset_probe(harness)

        # Measure _ITERATIONS runs
        for _ in range(_ITERATIONS):
            t0 = time.monotonic()
            harness._compute_snapshot_hash_incremental(snapshot)
            elapsed_ms = (time.monotonic() - t0) * 1000

            # Mirror the probe logic from _on_workspace_refreshed
            if len(harness._perf_probe_times) < harness._perf_probe_max_samples:
                harness._perf_probe_times.append(elapsed_ms)

        pct = compute_p50_p95_p99(harness._perf_probe_times)
        assert pct["p95"] < _P95_THRESHOLD_MS, (
            f"p95={pct['p95']:.1f}ms exceeds {_P95_THRESHOLD_MS}ms threshold. "
            f"p50={pct['p50']:.1f}ms, p99={pct['p99']:.1f}ms"
        )

    def test_probe_collects_samples(self, perf_probe_window: _ProbeHarness) -> None:
        """Verify the probe mechanism collects elapsed-time samples."""
        harness = perf_probe_window
        assert harness._perf_probe_times == []
        assert harness._perf_probe_max_samples == 1000

        # Simulate probe appends
        for i in range(10):
            if len(harness._perf_probe_times) < harness._perf_probe_max_samples:
                harness._perf_probe_times.append(float(i))

        assert len(harness._perf_probe_times) == 10

    def test_probe_caps_at_max_samples(self, perf_probe_window: _ProbeHarness) -> None:
        """Verify the probe caps at _perf_probe_max_samples (1000)."""
        harness = perf_probe_window
        max_samples = harness._perf_probe_max_samples

        # Fill beyond capacity
        for i in range(max_samples + 500):
            if len(harness._perf_probe_times) < max_samples:
                harness._perf_probe_times.append(float(i))

        assert len(harness._perf_probe_times) == max_samples

    def test_first_call_computes_all_sections(
        self, perf_probe_window: _ProbeHarness, tmp_path: pytest.TempPathFactory
    ) -> None:
        """First call (no cache) should be slower but still under threshold."""
        snapshot = build_synthetic_snapshot(
            num_projects=5,
            chapters_per_project=20,
            tmp_path=tmp_path,  # type: ignore[arg-type]
        )
        harness = perf_probe_window

        # First call — no cached payload, all sections computed
        t0 = time.monotonic()
        hash_val, changed_sections, payload = harness._compute_snapshot_hash_incremental(snapshot)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert hash_val
        assert len(changed_sections) > 0
        assert "details" in payload
        # Even the cold path should be reasonable
        assert elapsed_ms < 2000, f"Cold path took {elapsed_ms:.0f}ms (>2s)"

    def test_desktop_window_has_probe_attributes(self) -> None:
        """Verify DesktopWindow.__init__ adds _perf_probe_times instrumentation.

        This is the RED-phase guard: before the probe is added to production
        code, this test fails with AttributeError.
        """
        import inspect

        from novel_forge.desktop.window import NovelForgeDesktopWindow

        source = inspect.getsource(NovelForgeDesktopWindow.__init__)
        assert "_perf_probe_times" in source, (
            "DesktopWindow.__init__ must initialize _perf_probe_times"
        )
        assert "_perf_probe_max_samples" in source, (
            "DesktopWindow.__init__ must initialize _perf_probe_max_samples"
        )

    def test_on_workspace_refreshed_records_probe(self) -> None:
        """Verify the refresh pipeline records elapsed time in _perf_probe_times.

        The actual elapsed-time measurement happens in
        ``_apply_workspace_refresh`` (the heavy body behind
        ``_on_workspace_refreshed``); the probe is appended there.
        """
        import inspect

        from novel_forge.desktop.window import NovelForgeDesktopWindow

        source = inspect.getsource(NovelForgeDesktopWindow._apply_workspace_refresh)
        assert "_perf_probe_times" in source, (
            "_apply_workspace_refresh must record elapsed_ms in _perf_probe_times"
        )

    def test_cached_call_skips_unchanged_sections(
        self, perf_probe_window: _ProbeHarness, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Second call with same snapshot should reuse cached section hashes."""
        snapshot = build_synthetic_snapshot(
            num_projects=5,
            chapters_per_project=20,
            tmp_path=tmp_path,  # type: ignore[arg-type]
        )
        harness = perf_probe_window

        # First call — populates cache
        hash1, changed1, payload1 = harness._compute_snapshot_hash_incremental(snapshot)
        # Simulate what _on_workspace_refreshed does: persist the payload
        harness._last_snapshot_payload = payload1

        # Second call — same snapshot, should have no changed sections
        hash2, changed2, _ = harness._compute_snapshot_hash_incremental(snapshot)

        assert hash1 == hash2
        assert len(changed2) == 0, (
            f"Expected no changed sections on identical snapshot, got {changed2}"
        )
