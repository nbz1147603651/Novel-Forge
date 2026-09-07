"""Tests for DesktopJobManager._handle_step() audit_result_update handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from novel_forge.desktop.jobs import DesktopJobManager, DesktopJobRecord, DesktopJobState


@pytest.fixture()
def manager():
    mgr = DesktopJobManager(load_persisted_history=False)
    mgr._publish_event = MagicMock()
    yield mgr
    mgr.shutdown()


def _make_record(
    job_id: str = "job1",
    project_id: str = "proj-1",
) -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id=job_id,
        kind="run_chapter",
        label="test chapter",
        project_id=project_id,
        status=DesktopJobState.RUNNING,
    )


class TestHandleStepAuditResultUpdate:
    def test_valid_payload_calls_set_audit_result(self, manager: DesktopJobManager) -> None:
        rec = _make_record()
        with manager._lock:
            manager._jobs["job1"] = rec

        audit_result = {"critique": {"issues": []}, "continuity_score": 8.0}
        payload = {"chapter_number": 3, "audit_result": audit_result}

        mock_store = MagicMock()
        with patch("novel_forge.desktop.jobs.get_ui_store", return_value=mock_store, create=True):
            with patch(
                "novel_forge.desktop.state.store.get_ui_store",
                return_value=mock_store,
            ):
                manager._handle_step("job1", "audit_result_update", payload)

        mock_store.set_audit_result.assert_called_once_with("proj-1", 3, audit_result)

    def test_payload_none_is_noop(self, manager: DesktopJobManager) -> None:
        rec = _make_record()
        with manager._lock:
            manager._jobs["job1"] = rec

        manager._handle_step("job1", "audit_result_update", None)

    def test_missing_chapter_number_is_noop(self, manager: DesktopJobManager) -> None:
        rec = _make_record()
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"audit_result": {"score": 0.9}}
        mock_store = MagicMock()
        with patch(
            "novel_forge.desktop.state.store.get_ui_store",
            return_value=mock_store,
        ):
            manager._handle_step("job1", "audit_result_update", payload)

        mock_store.set_audit_result.assert_not_called()

    def test_missing_audit_result_is_noop(self, manager: DesktopJobManager) -> None:
        rec = _make_record()
        with manager._lock:
            manager._jobs["job1"] = rec

        payload = {"chapter_number": 2}
        mock_store = MagicMock()
        with patch(
            "novel_forge.desktop.state.store.get_ui_store",
            return_value=mock_store,
        ):
            manager._handle_step("job1", "audit_result_update", payload)

        mock_store.set_audit_result.assert_not_called()

    def test_lock_not_held_during_set_audit_result(self, manager: DesktopJobManager) -> None:
        rec = _make_record()
        with manager._lock:
            manager._jobs["job1"] = rec

        audit_result = {"score": 0.9}
        payload = {"chapter_number": 1, "audit_result": audit_result}

        lock_state_during_call: dict[str, bool] = {}

        def _check_lock(proj: str, ch: int, res: dict) -> None:
            lock_state_during_call["locked"] = manager._lock.locked()

        mock_store = MagicMock()
        mock_store.set_audit_result.side_effect = _check_lock

        with patch(
            "novel_forge.desktop.state.store.get_ui_store",
            return_value=mock_store,
        ):
            manager._handle_step("job1", "audit_result_update", payload)

        assert lock_state_during_call.get("locked") is False
