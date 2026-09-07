from __future__ import annotations

from types import SimpleNamespace

from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.window import NovelForgeDesktopWindow


class _FakeStudio:
    def __init__(self) -> None:
        self.state = SimpleNamespace(auto_started=True, mode="book_auto")
        self.stop_calls: list[tuple[str, bool]] = []
        self.stopped_from_auto = False

    def autorun_state_for_project(self, project_id: str) -> object:
        assert project_id == "与君共赴"
        return self.state

    def current_project_id(self) -> str:
        return "与君共赴"

    def stop_auto_pilot(self, reason: str = "用户已取消", *, cancel_jobs: bool = False) -> None:
        self.stop_calls.append((reason, cancel_jobs))
        self.state.auto_started = False
        self.stopped_from_auto = True


class _FakeJobManager:
    def __init__(self, studio: _FakeStudio) -> None:
        self._studio = studio
        self.cancel_calls: list[tuple[str, str]] = []
        self._jobs = [
            DesktopJobRecord(
                job_id="job-1",
                kind="prepare_chapter",
                label="章节方案 · 与君共赴 / 第 2 章",
                project_id="与君共赴",
                status=DesktopJobState.RUNNING,
            )
        ]

    def jobs(self) -> list[DesktopJobRecord]:
        return list(self._jobs)

    def cancel_job(self, job_id: str, *, reason: str = "用户已取消") -> None:
        assert self._studio.state.auto_started is False
        self.cancel_calls.append((job_id, reason))


class _AutorunStartStudio:
    def __init__(self) -> None:
        self.state = SimpleNamespace(mode="book_auto", book_auto_skip_done=True)
        self.started_calls: list[tuple[str, bool]] = []

    def autorun_state_for_project(self, project_id: str) -> object:
        assert project_id == "与君共赴"
        return self.state

    def set_autorun_project_started(self, project_id: str, started: bool) -> None:
        self.started_calls.append((project_id, started))


class _RejectedAutorunManager:
    storage_root = None

    def start_chapter_autorun(self, **_kwargs: object) -> None:
        return None

    def book_autorun_state(self, _project_id: str) -> None:
        return None


def _autorun_start_window(
    studio: _AutorunStartStudio,
    manager: object,
) -> tuple[NovelForgeDesktopWindow, list[tuple[str, int, object]]]:
    messages: list[tuple[str, int, object]] = []
    window = NovelForgeDesktopWindow.__new__(NovelForgeDesktopWindow)
    window._pages = {"chapter_studio": studio}
    window._job_manager = manager
    window._snapshot = SimpleNamespace(storage_root=None)
    window._mock_enabled = False
    window._STATUS_ERROR = object()
    window._autorun_chapter_numbers = {}
    window._autorun_writing_modes_by_project = {}
    window._autorun_cooldown_until = {}
    window._autorun_refresh_counts = {}
    window._autorun_last_submitted_checkpoint = {}
    window._autorun_prepared_chapters = set()
    window._autorun_resolve_attempts = {}
    window._get_chapter_number = lambda _project_id: 2
    window._set_autorun_chapter_number = lambda project_id, chapter_number: (
        window._autorun_chapter_numbers.__setitem__(project_id, chapter_number)
    )
    window.show_priority_status = lambda msg, ms, priority: messages.append((msg, ms, priority))
    return window, messages


def test_stop_job_turns_off_autorun_before_cancelling() -> None:
    studio = _FakeStudio()
    manager = _FakeJobManager(studio)
    messages: list[tuple[str, int, object]] = []

    window = NovelForgeDesktopWindow.__new__(NovelForgeDesktopWindow)
    window._pages = {"chapter_studio": studio}
    window._job_manager = manager
    window._autorun_cooldown_until = {"与君共赴": 123.0}
    window._autorun_refresh_counts = {"与君共赴": 2}
    window._autorun_last_submitted_checkpoint = {("与君共赴", 2): "cp-1", ("其他", 1): "cp-2"}
    window._autorun_prepared_chapters = {("与君共赴", 2), ("其他", 1)}
    window._STATUS_ERROR = object()
    window._STATUS_INFO = object()
    window.show_priority_status = lambda msg, ms, priority: messages.append((msg, ms, priority))

    window._on_stop_auto_pilot("job-1", "用户已取消")

    assert studio.stop_calls == [("用户已取消", False)]
    assert manager.cancel_calls == [("job-1", "用户已取消")]
    assert "与君共赴" not in window._autorun_cooldown_until
    assert "与君共赴" not in window._autorun_refresh_counts
    assert window._autorun_last_submitted_checkpoint == {("其他", 1): "cp-2"}
    assert window._autorun_prepared_chapters == {("其他", 1)}
    assert messages[0][0].startswith("全自动已停止")


def test_engine_autorun_rejection_resets_local_running_state() -> None:
    studio = _AutorunStartStudio()
    window, messages = _autorun_start_window(studio, _RejectedAutorunManager())

    window._on_project_autorun_started("与君共赴")

    assert studio.started_calls == [("与君共赴", False)]
    assert messages[0][0].startswith("无法启动 Engine 连跑：Engine 未接受")
