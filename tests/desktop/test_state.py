"""Tests for ObservablePageState and ChapterStudioState."""

from __future__ import annotations

import dataclasses
import warnings

import pytest
from pytestqt.exceptions import TimeoutError
from pytestqt.qtbot import QtBot

from novel_forge.desktop.pages.chapter_studio.state import (
    AutoPilotProjectState,
    ChapterStudioState,
)
from novel_forge.desktop.state.observable import ObservablePageState
from novel_forge.desktop.state.store import UIStore, WindowState, get_ui_store, set_ui_store

# ── ObservablePageState Tests ─────────────────────────────────────────────


class _TestState(ObservablePageState):
    """Concrete subclass for testing ObservablePageState."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = ""
        self.count = 0
        self.flag = False


class TestObservablePageStateSignalEmission:
    """Tests for changed signal emission on ObservablePageState."""

    def test_batch_update_emits_single_signal(self, qtbot: QtBot) -> None:
        """batch_update should emit exactly one 'batch' changed signal."""
        state = _TestState()
        with qtbot.waitSignal(state.changed, timeout=1000) as blocker:
            state.batch_update(title="Hello", count=42)
        assert blocker.args == ["batch", {"title": "Hello", "count": 42}]

    def test_batch_update_only_updates_existing_attrs(self, qtbot: QtBot) -> None:
        """batch_update should ignore kwargs that are not existing attributes."""
        state = _TestState()
        state.batch_update(title="Hi", nonexistent="value")
        assert state.title == "Hi"
        assert not hasattr(state, "nonexistent")

    def test_batch_update_no_signal_when_nothing_matches(self, qtbot: QtBot) -> None:
        """batch_update should not emit when no kwargs match existing attrs."""
        state = _TestState()
        with pytest.raises(TimeoutError):
            with qtbot.waitSignal(state.changed, timeout=500, raising=True):
                state.batch_update(nonexistent="value")

    def test_emit_change_single_field(self, qtbot: QtBot) -> None:
        """_emit_change should emit a single-field changed signal."""
        state = _TestState()
        with qtbot.waitSignal(state.changed, timeout=1000) as blocker:
            state._emit_change("title", "Test")
        assert blocker.args == ["title", "Test"]


class TestObservablePageStateBatchUpdate:
    """Tests for batch_update atomicity and correctness."""

    def test_batch_update_all_fields(self, qtbot: QtBot) -> None:
        """batch_update should set all matching fields atomically."""
        state = _TestState()
        with qtbot.waitSignal(state.changed, timeout=1000):
            state.batch_update(title="Batch", count=99, flag=True)
        assert state.title == "Batch"
        assert state.count == 99
        assert state.flag is True

    def test_batch_update_partial(self, qtbot: QtBot) -> None:
        """batch_update should only update fields that exist."""
        state = _TestState()
        state.count = 10
        with qtbot.waitSignal(state.changed, timeout=1000):
            state.batch_update(count=20, missing="x")
        assert state.count == 20
        assert state.title == ""

    def test_batch_update_empty(self, qtbot: QtBot) -> None:
        """batch_update with no kwargs should not emit."""
        state = _TestState()
        with pytest.raises(TimeoutError):
            with qtbot.waitSignal(state.changed, timeout=500, raising=True):
                state.batch_update()


class TestObservablePageStateFrozenSemantics:
    """Tests verifying ObservablePageState is NOT frozen (unlike dataclasses)."""

    def test_direct_assignment_works(self) -> None:
        """ObservablePageState fields should allow direct assignment."""
        state = _TestState()
        state.title = "Direct"
        state.count = 5
        assert state.title == "Direct"
        assert state.count == 5

    def test_no_frozen_instance_error(self) -> None:
        """Should NOT raise FrozenInstanceError on direct assignment."""
        state = _TestState()
        # This would raise FrozenInstanceError if it were a frozen dataclass
        state.flag = True
        assert state.flag is True


# ── ChapterStudioState Tests ──────────────────────────────────────────────


class TestChapterStudioStateReactiveModeChange:
    """Tests for mode change reactivity in ChapterStudioState."""

    def test_mode_change_emits_signals(self, qtbot: QtBot) -> None:
        """Setting mode should emit state_changed and changed signals."""
        state = ChapterStudioState()
        signals_received = []
        state.state_changed.connect(
            lambda name, val: signals_received.append(("state_changed", name, val))
        )
        state.changed.connect(lambda name, val: signals_received.append(("changed", name, val)))

        state.mode = "auto"

        assert any(
            s[0] == "state_changed" and s[1] == "mode" and s[2] == "auto" for s in signals_received
        )
        assert any(
            s[0] == "changed" and s[1] == "mode" and s[2] == "auto" for s in signals_received
        )

    def test_mode_change_updates_auto_pilot_state(self) -> None:
        """Setting mode should update the per-project auto-pilot state."""
        state = ChapterStudioState()
        state.current_project_id = "test-project"
        state.mode = "auto"
        assert state.get_auto_pilot_state("test-project").mode == "auto"

    def test_mode_defaults_to_manual(self) -> None:
        """Initial mode should be 'manual'."""
        state = ChapterStudioState()
        assert state.mode == "manual"


class TestChapterStudioStateAutoPilot:
    """Tests for auto-pilot state management."""

    def test_auto_started_setter_emits(self, qtbot: QtBot) -> None:
        """Setting auto_started should emit signals."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        with qtbot.waitSignal(state.state_changed, timeout=1000) as blocker:
            state.auto_started = True
        assert blocker.args[0] == "auto_started"
        assert blocker.args[1] is True

    def test_auto_started_getter_delegates(self) -> None:
        """auto_started getter should delegate to per-project state."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.auto_started = True
        assert state.auto_started is True

    def test_auto_started_defaults_false_without_project(self) -> None:
        """auto_started should be False when no project is set."""
        state = ChapterStudioState()
        state.current_project_id = ""
        assert state.auto_started is False

    def test_auto_gen_setter_updates_watchdog_gen(self) -> None:
        """Setting auto_gen should update watchdog_gen in per-project state."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.auto_gen = 5
        assert state.get_auto_pilot_state("proj-1").watchdog_gen == 5

    def test_auto_last_progress_at_setter(self) -> None:
        """Setting auto_last_progress_at should update per-project state."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.auto_last_progress_at = 123.45
        assert state.get_auto_pilot_state("proj-1").last_progress_at == 123.45

    def test_user_chapter_nav_setter(self) -> None:
        """Setting user_chapter_nav should update per-project state."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.user_chapter_nav = True
        assert state.get_auto_pilot_state("proj-1").user_chapter_nav is True

    def test_follow_autorun_setter(self) -> None:
        """Setting follow_autorun should update per-project state."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.follow_autorun = True
        assert state.get_auto_pilot_state("proj-1").follow_autorun is True

    def test_project_preferences_roundtrip_never_resumes_auto_run(self) -> None:
        """A restart restores selector choices but never an executing auto-run."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.mode = "book_auto"
        state.writing_mode = "scene_level"
        state.follow_autorun = True
        state.auto_started = True
        state.auto_gen = 5
        state.auto_last_progress_at = 123.0

        restored = ChapterStudioState()
        restored.restore_project_preferences(state.export_project_preferences())
        restored.current_project_id = "proj-1"

        assert restored.mode == "book_auto"
        assert restored.writing_mode == "scene_level"
        assert restored.follow_autorun is True
        assert restored.auto_started is False
        assert restored.auto_gen == 0
        assert restored.auto_last_progress_at == 0.0

    def test_project_preferences_restore_stopped_book_auto_as_inline_resume_context(self) -> None:
        """A stopped book run remains resumable after restart without restarting itself."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.mode = "book_auto"
        state.stopped_mode = "book_auto"
        state.mode = "manual"
        state.stopped_from_auto = True

        preferences = state.export_project_preferences()
        assert preferences["proj-1"]["interrupted_auto_mode"] == "book_auto"

        restored = ChapterStudioState()
        restored.restore_project_preferences(preferences)
        restored.current_project_id = "proj-1"

        assert restored.mode == "manual"
        assert restored.stopped_from_auto is True
        assert restored.stopped_mode == "book_auto"
        assert restored.auto_started is False

    def test_invalid_project_preferences_are_ignored(self) -> None:
        """A malformed session cannot put selectors into an unsupported mode."""
        state = ChapterStudioState()
        state.restore_project_preferences(
            {
                "proj-1": {
                    "decision_mode": ["book_auto"],
                    "writing_mode": {"scene_level": True},
                    "follow_autorun": "yes",
                }
            }
        )
        state.current_project_id = "proj-1"

        assert state.mode == "auto"
        assert state.writing_mode == "whole_chapter"
        assert state.follow_autorun is False

    def test_reset_auto_pilot_all(self) -> None:
        """reset_auto_pilot without project_id should reset all projects."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.auto_started = True
        state.auto_pilot_pending = True
        state.auto_repair_pending = True

        state.reset_auto_pilot()

        assert state.auto_started is False
        assert state.auto_pilot_pending is False
        assert state.auto_repair_pending is False

    def test_reset_auto_pilot_single_project(self) -> None:
        """reset_auto_pilot with project_id should only reset that project."""
        state = ChapterStudioState()
        state.current_project_id = "proj-1"
        state.auto_started = True
        state.get_auto_pilot_state("proj-2").auto_started = True

        state.reset_auto_pilot("proj-1")

        assert state.auto_started is False
        assert state.get_auto_pilot_state("proj-2").auto_started is True

    def test_reset_chapter_context(self) -> None:
        """reset_chapter_context should clear chapter-specific state."""
        state = ChapterStudioState()
        state.auto_chapter_prepared = True
        state.auto_repair_pending = True
        state.last_submitted_checkpoint_id = "cp-1"
        state.notes_submitted = True
        state.jobs_fingerprint = ("hash",)
        state.scheduled_retry_at = 999.0

        state.reset_chapter_context()

        assert state.auto_chapter_prepared is False
        assert state.auto_repair_pending is False
        assert state.last_submitted_checkpoint_id is None
        assert state.notes_submitted is False
        assert state.jobs_fingerprint == ()
        assert state.scheduled_retry_at == 0.0


class TestChapterStudioStateUIStoreCompat:
    """Tests for ChapterStudioState compatibility with UIStore patterns."""

    def test_chapter_studio_state_is_observable_page_state(self) -> None:
        """ChapterStudioState should be a subclass of ObservablePageState."""
        state = ChapterStudioState()
        assert isinstance(state, ObservablePageState)

    def test_chapter_studio_state_is_qobject(self) -> None:
        """ChapterStudioState should be a QObject for Qt signal support."""
        from PySide6.QtCore import QObject

        state = ChapterStudioState()
        assert isinstance(state, QObject)

    def test_state_changed_is_same_as_changed_signal(self) -> None:
        """state_changed should be an alias for the parent changed signal at class level."""
        # At class level they are the same Signal object
        assert ChapterStudioState.state_changed is ObservablePageState.changed

    def test_uistore_singleton_access(self) -> None:
        """UIStore should be accessible via get_ui_store."""
        store = get_ui_store()
        assert isinstance(store, UIStore)

    def test_uistore_active_project(self, qtbot: QtBot) -> None:
        """UIStore active_project should work with WindowState attached."""
        store = UIStore()
        window_state = WindowState()
        store.attach_window_state(window_state)

        with qtbot.waitSignal(store.active_project_changed, timeout=1000):
            store.set_active_project("test-project")

        assert store.active_project == "test-project"

    def test_uistore_window_state_attachment_is_idempotent_and_replaceable(self) -> None:
        store = UIStore()
        first = WindowState()
        second = WindowState()
        forwarded: list[str] = []
        store.active_project_changed.connect(forwarded.append)

        store.attach_window_state(first)
        store.attach_window_state(first)
        first.set_active_project("first")
        assert forwarded == ["first"]

        store.attach_window_state(second)
        first.set_active_project("stale")
        second.set_active_project("second")
        assert forwarded == ["first", "second"]
        assert store.active_project == "second"

    def test_uistore_detach_only_affects_the_current_window_state(self) -> None:
        store = UIStore()
        current = WindowState()
        unrelated = WindowState()
        forwarded: list[str] = []
        store.active_project_changed.connect(forwarded.append)
        store.attach_window_state(current)

        store.detach_window_state(unrelated)
        current.set_active_project("still-current")
        assert store.active_project == "still-current"
        assert forwarded == ["still-current"]

        store.detach_window_state(current)
        current.set_active_project("stale")
        assert store.active_project == ""
        assert forwarded == ["still-current"]

    def test_uistore_workflow_status(self, qtbot: QtBot) -> None:
        """UIStore workflow status should emit signals on change."""
        store = UIStore()
        with qtbot.waitSignal(store.workflow_status_changed, timeout=1000) as blocker:
            store.set_workflow_status("proj-1", "running")
        assert blocker.args == ["proj-1", "running"]
        assert store.workflow_status("proj-1") == "running"

    def test_uistore_memory_status(self, qtbot: QtBot) -> None:
        """UIStore memory status should emit signals on change."""
        store = UIStore()
        status = {"enabled": True, "progress": 0.5}
        with qtbot.waitSignal(store.memory_status_changed, timeout=1000) as blocker:
            store.set_memory_status("proj-1", status)
        assert blocker.args[0] == "proj-1"
        assert store.memory_status("proj-1") == status

    def test_uistore_audit_result_versioning(self, qtbot: QtBot) -> None:
        """UIStore audit result should auto-increment version."""
        store = UIStore()
        with qtbot.waitSignal(store.audit_result_changed, timeout=1000):
            store.set_audit_result("proj-1", 1, {"score": 0.9})
        with qtbot.waitSignal(store.audit_result_changed, timeout=1000):
            store.set_audit_result("proj-1", 1, {"score": 0.95})

        result = store.get_audit_result("proj-1", 1)
        assert result is not None
        assert result["version"] == 2
        assert result["result"]["score"] == 0.95

    def test_uistore_snapshot_restore(self) -> None:
        """UIStore snapshot/restore should preserve state."""
        store = UIStore()
        store.set_active_page("chapter_studio")
        store.set_workflow_status("proj-1", "done")
        store.set("custom_key", "custom_value")

        snap = store.snapshot()
        assert snap["active_page"] == "chapter_studio"
        assert snap["workflow_statuses"]["proj-1"] == "done"
        assert snap["extra"]["custom_key"] == "custom_value"

        store.restore(snap)
        assert store.active_page == "chapter_studio"

    def test_uistore_set_ui_store_injection(self) -> None:
        """set_ui_store should allow dependency injection."""
        custom_store = UIStore()
        set_ui_store(custom_store)
        assert get_ui_store() is custom_store
        # Reset to avoid affecting other tests
        set_ui_store(None)

    def test_uistore_instance_deprecated(self) -> None:
        """UIStore.instance() should emit a deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            UIStore.instance()
            assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
        # Clean up
        UIStore.reset()

    def test_uistore_generic_kv(self, qtbot: QtBot) -> None:
        """UIStore get/set should work for arbitrary key-value pairs."""
        store = UIStore()
        with qtbot.waitSignal(store.ui_state_changed, timeout=1000) as blocker:
            store.set("my_key", "my_value")
        assert blocker.args == ["my_key", "my_value"]
        assert store.get("my_key") == "my_value"
        assert store.get("missing", "default") == "default"


class TestAutoPilotProjectState:
    """Tests for the AutoPilotProjectState dataclass."""

    def test_defaults(self) -> None:
        """AutoPilotProjectState should have sensible defaults."""
        state = AutoPilotProjectState()
        assert state.auto_started is False
        assert state.mode == "auto"
        assert state.watchdog_gen == 0
        assert state.last_progress_at == 0.0
        assert state.user_chapter_nav is False
        assert state.follow_autorun is False
        assert state.writing_mode == "whole_chapter"

    def test_frozen_semantics_via_replace(self) -> None:
        """AutoPilotProjectState should use dataclasses.replace for updates."""
        state = AutoPilotProjectState()
        new_state = dataclasses.replace(state, auto_started=True, mode="manual")
        assert new_state.auto_started is True
        assert new_state.mode == "manual"
        # Original unchanged
        assert state.auto_started is False
