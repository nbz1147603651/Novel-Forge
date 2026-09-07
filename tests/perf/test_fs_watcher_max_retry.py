"""Test: force-refresh retry counter stops at max (3) after consecutive no-op refreshes.

Verifies that when _on_workspace_refreshed detects no content change, the retry
counter increments and stops scheduling further refreshes after 3 attempts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")


class TestFsWatcherMaxRetry:
    """After 3 consecutive no-change refreshes, the retry loop must stop."""

    def test_retry_stops_at_max(self) -> None:
        """Simulate 5 no-op refreshes; counter must cap at 3 and stop scheduling."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        max_retries = NovelForgeDesktopWindow._FORCE_REFRESH_MAX_RETRIES
        assert max_retries == 3, "Expected max retries to be 3"

        mock_self = MagicMock(spec=NovelForgeDesktopWindow)
        mock_self._refresh_force_pending = True
        mock_self._force_refresh_retries = 0
        mock_self._refresh_in_progress = False
        mock_self._last_refresh_complete_time = 0.0
        mock_self._refresh_force_current = False
        mock_self._last_snapshot_hash = "abc123"
        mock_self._last_snapshot_payload = {"key": "value"}
        mock_self._snapshot = None
        mock_self._workspace = MagicMock()
        mock_self._workspace_revision = 0
        mock_self._last_changed_sections = set()
        mock_self._pending_refresh_section_hints = set()
        mock_self._refresh_section_hints_current = set()
        mock_self._active_context_refresh_workers = set()
        mock_self._chapter_context_refresh_key = None
        mock_self._FORCE_REFRESH_MAX_RETRIES = max_retries

        snapshot = MagicMock()
        snapshot.projects = []
        snapshot.storage_root = MagicMock()
        snapshot.details = {}

        mock_self._update_fs_watcher_paths = MagicMock()
        mock_self._hide_skeleton_overlay = MagicMock()

        # v2: retry scheduling happens inside _apply_workspace_refresh (the
        # body behind _on_workspace_refreshed) via _safe_deferred; the heavy
        # page-binding path is unreachable on the no-change branch.
        for _ in range(5):
            NovelForgeDesktopWindow._apply_workspace_refresh(
                mock_self,
                snapshot,
                MagicMock(),
                {"key": "value"},
                {"section_hashes": "abc123"},
                set(),
                "abc123",
            )

        # After hitting max_retries, _refresh_force_pending is cleared
        assert mock_self._refresh_force_pending is False
        # max_retries - 1 actual retries scheduled (first no-op is retry 0,
        # _safe_deferred fires for retries 0..max_retries-2, then stops)
        assert mock_self._safe_deferred.call_count == max_retries - 1

    def test_retry_resets_on_content_change(self) -> None:
        """When content actually changes, the retry counter resets to 0."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        mock_self = MagicMock()
        mock_self._refresh_force_pending = True
        mock_self._force_refresh_retries = 2
        mock_self._refresh_in_progress = False
        mock_self._last_refresh_complete_time = 0.0
        mock_self._refresh_force_current = False
        mock_self._last_snapshot_hash = "old_hash"
        mock_self._last_snapshot_payload = {"key": "old"}
        mock_self._snapshot = None
        mock_self._workspace = MagicMock()
        mock_self._workspace_revision = 0
        mock_self._last_changed_sections = set()
        mock_self._pending_refresh_section_hints = set()
        mock_self._refresh_section_hints_current = set()
        mock_self._active_context_refresh_workers = set()
        mock_self._chapter_context_refresh_key = None
        mock_self._FORCE_REFRESH_MAX_RETRIES = NovelForgeDesktopWindow._FORCE_REFRESH_MAX_RETRIES
        mock_self._mock_enabled = False
        mock_self._perf_probe_times = []
        mock_self._perf_probe_max_samples = 1000

        mock_self._update_fs_watcher_paths = MagicMock()
        mock_self._hide_skeleton_overlay = MagicMock()
        mock_self._autorun_driving_projects = set()
        mock_self._pages = {}
        mock_self._active_workspace_refresh_worker = None

        snapshot = MagicMock()
        snapshot.projects = []
        snapshot.storage_root = MagicMock()
        snapshot.details = {}
        snapshot.metrics.total_words = 0
        snapshot.metrics.total_projects = 0
        snapshot.overview.providers = []

        mock_self._bind_jobs = MagicMock()
        mock_self._apply_page_meta = MagicMock()
        mock_self._ui_session_restored = True

        # Patch the post-reset UI update section to avoid deep mocking
        with (
            patch.object(
                NovelForgeDesktopWindow,
                "_sync_chapter_studio_target_with_snapshot",
                lambda self, snap: None,
            ),
            patch.object(
                NovelForgeDesktopWindow,
                "_current_page_id",
                lambda self: "dashboard",
            ),
            patch.object(
                NovelForgeDesktopWindow,
                "_pages_needing_bind",
                lambda self, *a, **kw: [],
            ),
            patch.object(
                NovelForgeDesktopWindow,
                "_bind_workspace_for_page",
                lambda self, *a, **kw: None,
            ),
            patch.object(
                NovelForgeDesktopWindow,
                "_refresh_chapter_studio_context",
                lambda self: None,
            ),
        ):
            NovelForgeDesktopWindow._apply_workspace_refresh(
                mock_self,
                snapshot,
                MagicMock(),
                {"key": "new"},
                {"projects": "new_hash"},
                {"projects"},
                "new_hash",
            )

        assert mock_self._force_refresh_retries == 0
