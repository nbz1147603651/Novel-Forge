"""章节工作台行为状态容器。

将 ChapterStudioPage 的所有可变业务状态集中管理，
使状态读写路径清晰可追溯，便于调试与后续测试。
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from novel_forge.desktop.state.observable import ObservablePageState

if TYPE_CHECKING:
    from novel_forge.desktop.jobs import DesktopJobRecord


@dataclasses.dataclass
class AutoPilotProjectState:
    """Per-project auto-pilot state container."""

    auto_started: bool = False
    mode: str = "auto"
    stopped_from_auto: bool = False
    stopped_mode: str = "auto"
    writing_mode: str = "whole_chapter"
    watchdog_gen: int = 0
    last_progress_at: float = 0.0
    user_chapter_nav: bool = False
    follow_autorun: bool = False
    book_auto_skip_done: bool = True


class ChapterStudioState(ObservablePageState):
    """集中存储 ChapterStudioPage 的全部非 UI 行为状态。

    所有 auto-pilot、jobs 跟踪、笔记与修复相关状态均在此定义，
    由 ChapterStudioPage 通过 property 代理读写。

    Emits :attr:`state_changed` signal when any field is modified.
    """

    state_changed = ObservablePageState.changed

    _PERSISTED_DECISION_MODES = frozenset({"manual", "suggest", "auto", "book_auto"})
    _PERSISTED_WRITING_MODES = frozenset({"whole_chapter", "scene_level"})

    # ── Per-project Auto-pilot 状态 ──
    _auto_pilot_per_project: dict[str, AutoPilotProjectState]
    current_project_id: str

    # ── Auto-pilot 状态 ──
    auto_pilot_pending: bool
    auto_chapter_prepared: bool
    auto_refresh_count: int

    # ── Jobs 跟踪 ──
    jobs: list[DesktopJobRecord]
    jobs_fingerprint: tuple[Any, ...]
    last_applied_memory_event_key: dict[str, tuple[str, str]]

    # ── 修复状态 ──
    auto_repair_pending: bool
    auto_repair_attempts: dict[tuple[str, int], int]
    dismissed_warning_fingerprints: dict[tuple[str, int], str]

    # ── 定时重试 ──
    scheduled_retry_at: float

    # ── Checkpoint 与笔记 ──
    last_submitted_checkpoint_id: str | None
    notes_submitted: bool
    notes_expanded_by_chapter: dict[tuple[str, int], bool]

    # ── Checkpoint 漂移自动恢复 ──
    auto_refresh_in_flight: bool
    """收到 chapter_session_stale 错误后已触发一次自动刷新；bind_studio 回来时清除。"""
    auto_refresh_consumed_job_ids: set[str]
    """已触发过自动刷新的失败 job_id；历史失败记录不应重复触发刷新。"""

    # ── 用户决策记录（供 workspace 层读取以执行副作用）──
    last_user_checkpoint_choice: dict[str, Any] | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._auto_pilot_per_project: dict[str, AutoPilotProjectState] = {}
        self.current_project_id = ""
        self.mode = "manual"
        self.auto_pilot_pending = False
        self.auto_started = False
        self.stopped_from_auto = False
        self.stopped_mode = "auto"
        self.auto_gen = 0
        self.auto_chapter_prepared = False
        self.auto_last_progress_at = 0.0
        self.auto_refresh_count = 0
        self.user_chapter_nav = False
        self.jobs: list[DesktopJobRecord] = []
        self.jobs_fingerprint: tuple[Any, ...] = ()
        self.last_applied_memory_event_key: dict[str, tuple[str, str]] = {}
        self.auto_repair_pending = False
        self.auto_repair_attempts: dict[tuple[str, int], int] = {}
        self.dismissed_warning_fingerprints: dict[tuple[str, int], str] = {}
        self.book_auto_skip_done = True
        self.scheduled_retry_at = 0.0
        self.last_submitted_checkpoint_id: str | None = None
        self.notes_submitted = False
        self.notes_expanded_by_chapter: dict[tuple[str, int], bool] = {}
        self.last_user_checkpoint_choice: dict[str, Any] | None = None
        self.auto_refresh_in_flight = False
        self.auto_refresh_consumed_job_ids: set[str] = set()

    def _set_field(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        self.state_changed.emit(name, value)

    # ── Per-project Auto-pilot 属性代理 ──

    @property
    def auto_started(self) -> bool:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].auto_started
        return False

    @auto_started.setter
    def auto_started(self, value: bool) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id), auto_started=value
            ),
        )
        self.state_changed.emit("auto_started", value)

    @property
    def stopped_from_auto(self) -> bool:
        """Whether this project's current manual state came from an auto-run stop."""
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].stopped_from_auto
        return False

    @stopped_from_auto.setter
    def stopped_from_auto(self, value: bool) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id),
                stopped_from_auto=bool(value),
            ),
        )
        self.state_changed.emit("stopped_from_auto", bool(value))

    @property
    def stopped_mode(self) -> str:
        """Return the auto-run mode that can be resumed for the visible project."""
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].stopped_mode
        return "auto"

    @stopped_mode.setter
    def stopped_mode(self, value: str) -> None:
        normalized = value if value in {"auto", "book_auto"} else "auto"
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id),
                stopped_mode=normalized,
            ),
        )
        self.state_changed.emit("stopped_mode", normalized)

    @property
    def mode(self) -> str:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].mode
        return "manual"

    @mode.setter
    def mode(self, value: str) -> None:
        normalized = (
            value
            if isinstance(value, str) and value in self._PERSISTED_DECISION_MODES
            else "manual"
        )
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(self.get_auto_pilot_state(self.current_project_id), mode=normalized),
        )
        self.state_changed.emit("mode", normalized)

    @property
    def writing_mode(self) -> str:
        """Return the selected drafting strategy for the visible project."""
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].writing_mode
        return "whole_chapter"

    @writing_mode.setter
    def writing_mode(self, value: str) -> None:
        normalized = (
            value
            if isinstance(value, str) and value in self._PERSISTED_WRITING_MODES
            else "whole_chapter"
        )
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id),
                writing_mode=normalized,
            ),
        )
        self.state_changed.emit("writing_mode", normalized)

    @property
    def auto_gen(self) -> int:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].watchdog_gen
        return 0

    @auto_gen.setter
    def auto_gen(self, value: int) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id), watchdog_gen=value
            ),
        )
        self.state_changed.emit("auto_gen", value)

    @property
    def auto_last_progress_at(self) -> float:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].last_progress_at
        return 0.0

    @auto_last_progress_at.setter
    def auto_last_progress_at(self, value: float) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id), last_progress_at=value
            ),
        )
        self.state_changed.emit("auto_last_progress_at", value)

    @property
    def user_chapter_nav(self) -> bool:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].user_chapter_nav
        return False

    @user_chapter_nav.setter
    def user_chapter_nav(self, value: bool) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id), user_chapter_nav=value
            ),
        )
        self.state_changed.emit("user_chapter_nav", value)

    @property
    def follow_autorun(self) -> bool:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].follow_autorun
        return False

    @follow_autorun.setter
    def follow_autorun(self, value: bool) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id), follow_autorun=value
            ),
        )
        self.state_changed.emit("follow_autorun", value)

    @property
    def book_auto_skip_done(self) -> bool:
        if self.current_project_id and self.current_project_id in self._auto_pilot_per_project:
            return self._auto_pilot_per_project[self.current_project_id].book_auto_skip_done
        return True

    @book_auto_skip_done.setter
    def book_auto_skip_done(self, value: bool) -> None:
        self.set_auto_pilot_state(
            self.current_project_id,
            dataclasses.replace(
                self.get_auto_pilot_state(self.current_project_id),
                book_auto_skip_done=bool(value),
            ),
        )
        self.state_changed.emit("book_auto_skip_done", bool(value))

    # ── Per-project Auto-pilot 方法 ──

    def get_auto_pilot_state(self, project_id: str) -> AutoPilotProjectState:
        if project_id not in self._auto_pilot_per_project:
            self._auto_pilot_per_project[project_id] = AutoPilotProjectState()
        return self._auto_pilot_per_project[project_id]

    def set_auto_pilot_state(self, project_id: str, state: AutoPilotProjectState) -> None:
        new_dict = dict(self._auto_pilot_per_project)
        new_dict[project_id] = state
        super().__setattr__("_auto_pilot_per_project", new_dict)

    def export_project_preferences(self) -> dict[str, dict[str, object]]:
        """Return only user choices that are safe to restore after a restart.

        Runtime execution state such as watchdog counters and ``auto_started``
        is intentionally excluded: reopening the desktop client never silently
        resumes work or dispatches a chapter job.  A user-stopped auto-run is
        retained as UI context only, so its checkpoint can be shown inline
        with an explicit resume control instead of re-opening a popup.
        """
        preferences: dict[str, dict[str, object]] = {}
        for project_id, state in self._auto_pilot_per_project.items():
            if not project_id:
                continue
            preference: dict[str, object] = {
                "decision_mode": state.mode,
                "writing_mode": state.writing_mode,
                "follow_autorun": state.follow_autorun,
            }
            if state.stopped_from_auto and state.mode == "manual":
                preference["interrupted_auto_mode"] = state.stopped_mode
            preferences[project_id] = preference
        return preferences

    def restore_project_preferences(self, raw_preferences: object) -> None:
        """Restore validated per-project choices from the UI session payload."""
        if not isinstance(raw_preferences, dict):
            return
        for raw_project_id, raw_preference in raw_preferences.items():
            project_id = str(raw_project_id).strip()
            if not project_id or not isinstance(raw_preference, dict):
                continue
            current = self.get_auto_pilot_state(project_id)
            decision_mode = raw_preference.get("decision_mode")
            writing_mode = raw_preference.get("writing_mode")
            follow_autorun = raw_preference.get("follow_autorun")
            restored_mode = (
                decision_mode
                if isinstance(decision_mode, str)
                and decision_mode in self._PERSISTED_DECISION_MODES
                else current.mode
            )
            interrupted_auto_mode = raw_preference.get("interrupted_auto_mode")
            was_stopped_from_auto = (
                restored_mode == "manual"
                and interrupted_auto_mode in {"auto", "book_auto"}
            )
            self.set_auto_pilot_state(
                project_id,
                dataclasses.replace(
                    current,
                    mode=restored_mode,
                    writing_mode=(
                        writing_mode
                        if isinstance(writing_mode, str)
                        and writing_mode in self._PERSISTED_WRITING_MODES
                        else current.writing_mode
                    ),
                    follow_autorun=(
                        follow_autorun if isinstance(follow_autorun, bool) else current.follow_autorun
                    ),
                    # Execution never resumes merely because its preference
                    # was restored from a previous desktop session.
                    auto_started=False,
                    watchdog_gen=0,
                    last_progress_at=0.0,
                    user_chapter_nav=False,
                    stopped_from_auto=was_stopped_from_auto,
                    stopped_mode=(
                        str(interrupted_auto_mode) if was_stopped_from_auto else current.stopped_mode
                    ),
                ),
            )

    def active_auto_project_ids(self) -> list[str]:
        """Return project ids whose auto-pilot is currently active."""
        return [
            project_id
            for project_id, state in self._auto_pilot_per_project.items()
            if project_id and state.auto_started and state.mode in {"auto", "book_auto"}
        ]

    def auto_pilot_state_for(self, project_id: str) -> AutoPilotProjectState:
        return self.get_auto_pilot_state(project_id)

    def set_auto_started_for_project(self, project_id: str, value: bool) -> None:
        state = self.get_auto_pilot_state(project_id)
        self.set_auto_pilot_state(
            project_id,
            dataclasses.replace(state, auto_started=bool(value)),
        )
        if project_id == self.current_project_id:
            self.state_changed.emit("auto_started", bool(value))

    def reset_auto_pilot(self, project_id: str | None = None) -> None:
        """重置 auto-pilot 状态。

        Args:
            project_id: 如果提供，只重置该项目的状态；否则重置所有项目。
        """
        if project_id is not None:
            self.set_auto_pilot_state(project_id, AutoPilotProjectState())
        else:
            for pid in list(self._auto_pilot_per_project.keys()):
                self.set_auto_pilot_state(pid, AutoPilotProjectState())
        self._set_field("auto_pilot_pending", False)
        self._set_field("auto_repair_pending", False)
        self.auto_repair_attempts.clear()

    def reset_chapter_context(self) -> None:
        """切换章节时重置与单章相关的临时状态。"""
        self._set_field("auto_chapter_prepared", False)
        self._set_field("auto_repair_pending", False)
        self._set_field("last_submitted_checkpoint_id", None)
        self._set_field("notes_submitted", False)
        self._set_field("jobs_fingerprint", ())
        self._set_field("scheduled_retry_at", 0.0)
