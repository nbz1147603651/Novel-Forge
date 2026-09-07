"""Tests for forced shutdown cleanup (I-11).

Verifies that when the user chooses "强制退出" in the close-event dialog,
the sleep inhibitor is explicitly released and std streams are flushed
before ``os._exit(0)`` is called.  ``os._exit`` bypasses ``atexit`` handlers,
so without explicit cleanup the caffeinate / SetThreadExecutionState /
systemd-inhibit subprocess would leak.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.desktop


class _ForceExitSentinel(Exception):
    """Raised by mocked os._exit to simulate process termination."""


@pytest.fixture
def shutdown_mixin_instance():
    """Create a ShutdownMixin instance with mocked dependencies."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = ShutdownMixin.__new__(ShutdownMixin)
    obj._job_manager = MagicMock()
    obj._save_ui_session = MagicMock()
    return obj


def _patch_force_exit(mixin_cls):
    """Patch _shutdown_with_active_jobs to return force-exit, and os._exit to raise."""
    return (
        patch.object(mixin_cls, "_shutdown_with_active_jobs", return_value=(True, True)),
        patch("os._exit", side_effect=_ForceExitSentinel),
    )


def test_force_exit_releases_sleep_inhibitor(shutdown_mixin_instance):
    """When force-exit branch is taken, sleep inhibitor is released first."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance
    mock_inhibitor = obj._job_manager._sleep_inhibitor

    event = MagicMock()

    p_active, p_exit = _patch_force_exit(ShutdownMixin)
    with p_active, p_exit:
        try:
            ShutdownMixin.closeEvent(obj, event)
        except _ForceExitSentinel:
            pass

    mock_inhibitor.release.assert_called_once()


def test_force_exit_flushes_streams(shutdown_mixin_instance):
    """When force-exit branch is taken, stdout and stderr are flushed."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance

    event = MagicMock()

    p_active, p_exit = _patch_force_exit(ShutdownMixin)
    with p_active, p_exit:
        with patch.object(sys, "stdout") as mock_stdout:
            with patch.object(sys, "stderr") as mock_stderr:
                try:
                    ShutdownMixin.closeEvent(obj, event)
                except _ForceExitSentinel:
                    pass
                mock_stdout.flush.assert_called_once()
                mock_stderr.flush.assert_called_once()


def test_force_exit_saves_ui_session(shutdown_mixin_instance):
    """When force-exit branch is taken, UI session is saved before exit."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance

    event = MagicMock()

    p_active, p_exit = _patch_force_exit(ShutdownMixin)
    with p_active, p_exit:
        try:
            ShutdownMixin.closeEvent(obj, event)
        except _ForceExitSentinel:
            pass

    obj._save_ui_session.assert_called_once()


def test_force_exit_handles_missing_sleep_inhibitor(shutdown_mixin_instance):
    """When _sleep_inhibitor is None, force-exit still proceeds without error."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance
    obj._job_manager._sleep_inhibitor = None

    event = MagicMock()

    p_active, p_exit = _patch_force_exit(ShutdownMixin)
    with p_active, p_exit:
        try:
            ShutdownMixin.closeEvent(obj, event)
        except _ForceExitSentinel:
            pass


def test_force_exit_handles_release_exception(shutdown_mixin_instance):
    """When sleep_inhibitor.release() raises, force-exit still proceeds."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance
    obj._job_manager._sleep_inhibitor.release.side_effect = RuntimeError("boom")

    event = MagicMock()

    p_active, p_exit = _patch_force_exit(ShutdownMixin)
    with p_active, p_exit:
        try:
            ShutdownMixin.closeEvent(obj, event)
        except _ForceExitSentinel:
            pass


def test_cancel_in_close_ignores_event(shutdown_mixin_instance):
    """When user cancels the shutdown dialog, event is ignored (window stays open)."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance

    event = MagicMock()

    with patch.object(
        ShutdownMixin,
        "_shutdown_with_active_jobs",
        return_value=(False, False),
    ):
        ShutdownMixin.closeEvent(obj, event)

    event.ignore.assert_called_once()


def test_normal_close_hides_window_before_bounded_cleanup(shutdown_mixin_instance):
    """Committed close should disappear before any remaining teardown work."""
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance
    obj._job_manager.active_job_labels.return_value = []
    obj._job_manager.cancelling_job_labels.return_value = []
    obj._collect_unsaved_page_entries = MagicMock(return_value=[])
    calls: list[str] = []
    obj.hide = lambda: calls.append("hide")
    obj._pre_close_cleanup = lambda: calls.append("cleanup")
    event = MagicMock()
    event.accept.side_effect = lambda: calls.append("accept")

    ShutdownMixin.closeEvent(obj, event)

    assert calls == ["hide", "cleanup", "accept"]


def test_cleanup_failure_cannot_leave_hidden_application_running(shutdown_mixin_instance):
    from novel_forge.desktop.window.shutdown import ShutdownMixin

    obj = shutdown_mixin_instance
    obj._job_manager.active_job_labels.return_value = []
    obj._job_manager.cancelling_job_labels.return_value = []
    obj._collect_unsaved_page_entries = MagicMock(return_value=[])
    obj.hide = MagicMock()
    obj._pre_close_cleanup = MagicMock(side_effect=RuntimeError("cleanup failed"))
    event = MagicMock()

    ShutdownMixin.closeEvent(obj, event)

    obj.hide.assert_called_once()
    event.accept.assert_called_once()
