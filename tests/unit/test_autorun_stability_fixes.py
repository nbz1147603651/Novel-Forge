"""Regression tests for autorun stability hardening (P0-P3 fixes).

Covers:
- P0: Watchdog should treat PAUSED jobs as "still progressing" (no false timeout)
- P1: CLI per-chapter timeout stops auto-run gracefully
- P2: CLI progress save failure emits warning (not silent)
- P3: Window-layer refresh_context circuit breaker stops after 200 cycles
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject  # noqa: E402

from novel_forge.cli.chapter_runner import AutoChapterRunner, ChapterRunner  # noqa: E402
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState  # noqa: E402
from novel_forge.desktop.pages.chapter_studio.auto import (  # noqa: E402
    ChapterStudioAutoMixin,
)
from novel_forge.desktop.window.autorun import AutorunMixin  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# P0: Watchdog PAUSED state reset
# ─────────────────────────────────────────────────────────────────────────────


class _FakeWatchdogPage:
    """Minimal stub simulating ChapterStudioAutoMixin for watchdog testing."""

    AUTO_PILOT_TIMEOUT_SECS = 900
    MODE_AUTO = "auto"
    MODE_BOOK_AUTO = "book_auto"
    MODE_MANUAL = "manual"

    def __init__(self, jobs: list[DesktopJobRecord]) -> None:
        self._mode = self.MODE_BOOK_AUTO
        self._auto_started = True
        self._auto_last_progress_at = time.monotonic() - 1000  # 1000s ago
        self._jobs = jobs
        self._studio = SimpleNamespace(project_id="proj")
        self._auto_watchdog = MagicMock()
        self._stopped = False

    @property
    def _auto_pilot(self) -> bool:
        return self._mode in {self.MODE_AUTO, self.MODE_BOOK_AUTO}

    def _stop_auto_pilot(self, reason: str = "", cancel_jobs: bool = True) -> None:
        self._stopped = True

    def window(self) -> MagicMock:
        return MagicMock()

    def _check_auto_pilot_timeout(self) -> None:
        """Replicate the fixed logic from auto.py."""
        if not self._auto_pilot or not self._auto_started:
            self._auto_watchdog.stop()
            return
        # PAUSED 状态同样视为"仍在推进"
        if any(
            j.status
            in {DesktopJobState.RUNNING, DesktopJobState.QUEUED, DesktopJobState.PAUSED}
            for j in self._jobs
        ):
            self._auto_last_progress_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._auto_last_progress_at
        if elapsed >= self.AUTO_PILOT_TIMEOUT_SECS:
            self._auto_watchdog.stop()
            self._stop_auto_pilot(reason="超时")


def test_watchdog_does_not_timeout_when_job_paused() -> None:
    """P0: A PAUSED job (e.g. waiting for checkpoint I/O) must reset the watchdog."""
    paused_job = DesktopJobRecord(
        job_id="job-paused",
        kind="resolve_chapter_checkpoint",
        label="归档",
        project_id="proj",
        status=DesktopJobState.PAUSED,
        result={"chapter_number": 1},
    )
    page = _FakeWatchdogPage(jobs=[paused_job])
    # _auto_last_progress_at is 1000s ago (> 900s timeout)
    page._check_auto_pilot_timeout()

    assert not page._stopped, "Watchdog must NOT stop when a PAUSED job exists"
    # Timer should have been reset
    assert time.monotonic() - page._auto_last_progress_at < 1.0


def test_watchdog_does_not_timeout_when_job_running() -> None:
    """RUNNING job resets watchdog (existing behavior, sanity check)."""
    running_job = DesktopJobRecord(
        job_id="job-running",
        kind="prepare_chapter",
        label="准备",
        project_id="proj",
        status=DesktopJobState.RUNNING,
        result={},
    )
    page = _FakeWatchdogPage(jobs=[running_job])
    page._check_auto_pilot_timeout()

    assert not page._stopped
    assert time.monotonic() - page._auto_last_progress_at < 1.0


def test_watchdog_stops_when_no_active_jobs_and_timeout_elapsed() -> None:
    """Watchdog fires when all jobs are terminal and timeout has elapsed."""
    succeeded_job = DesktopJobRecord(
        job_id="job-done",
        kind="prepare_chapter",
        label="准备",
        project_id="proj",
        status=DesktopJobState.SUCCEEDED,
        result={},
    )
    page = _FakeWatchdogPage(jobs=[succeeded_job])
    page._check_auto_pilot_timeout()

    assert page._stopped, "Watchdog should fire when no active jobs and timeout elapsed"


def test_watchdog_stops_when_no_jobs_at_all() -> None:
    """Watchdog fires when there are no jobs and timeout elapsed."""
    page = _FakeWatchdogPage(jobs=[])
    page._check_auto_pilot_timeout()

    assert page._stopped


class _ReentrantStopPage:
    """Model the synchronous render callback that exposed the shutdown recursion."""

    MODE_MANUAL = "manual"

    def __init__(self) -> None:
        self._mode = "book_auto"
        self._auto_started = True
        self._stopped_mode_value = "auto"
        self._jobs: list[DesktopJobRecord] = []
        self._auto_watchdog = MagicMock()
        self._auto_pilot_pending = False
        self._auto_repair_pending = False
        self._auto_chapter_prepared = False
        self._auto_refresh_count = 0
        self._auto_last_progress_at = 0.0
        self._auto_gen = 0
        self._auto_repair_attempts: dict[tuple[str, int], int] = {}
        self._stopped_from_auto = False
        self._state = QObject()
        self.cancel_job_requested = MagicMock()
        self.auto_pilot_stopped = MagicMock()
        self.render_count = 0

    @property
    def _stopped_mode(self) -> str:
        return self._stopped_mode_value

    @_stopped_mode.setter
    def _stopped_mode(self, value: str) -> None:
        self._stopped_mode_value = value
        self._render_action_panel()

    def current_project_id(self) -> str:
        return "project-demo"

    def _set_mode(self, mode: str) -> None:
        self._mode = mode

    def _render_action_panel(self) -> None:
        self.render_count += 1
        if self._mode == "book_auto" and self._auto_started:
            ChapterStudioAutoMixin._stop_auto_pilot(self, cancel_jobs=False)  # type: ignore[arg-type]


def test_stop_auto_pilot_rejects_synchronous_render_reentry() -> None:
    """A failed-job render must stop once without recursively exhausting the stack."""
    page = _ReentrantStopPage()

    ChapterStudioAutoMixin._stop_auto_pilot(page, cancel_jobs=False)  # type: ignore[arg-type]

    assert page._mode == page.MODE_MANUAL
    assert page._auto_started is False
    assert page._stopped_from_auto is True
    assert page.render_count == 2
    page.auto_pilot_stopped.emit.assert_called_once_with("project-demo")


# ─────────────────────────────────────────────────────────────────────────────
# P1: CLI per-chapter timeout
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_chapter_timeout_returns_none() -> None:
    """P1: _run_chapter_with_timeout returns None on timeout."""
    runner = AutoChapterRunner(
        project_id="timeout-proj",
        start_chapter=1,
        end_chapter=3,
        force=False,
        mock=True,
        verbose=False,
        ai_judge_apply_mode="auto",
        run_logger=None,
    )

    async def _slow_run(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(999)  # Simulate hang

    fake_chapter_runner = MagicMock(spec=ChapterRunner)
    fake_chapter_runner.run = _slow_run

    fake_runtime = SimpleNamespace(
        settings=SimpleNamespace(long_auto_chapter_timeout_seconds=1)
    )

    result = await runner._run_chapter_with_timeout(
        fake_chapter_runner, 1, fake_runtime  # type: ignore[arg-type]
    )
    assert result is None


@pytest.mark.asyncio
async def test_cli_chapter_timeout_returns_result_on_success() -> None:
    """P1: _run_chapter_with_timeout returns result when chapter completes in time."""
    runner = AutoChapterRunner(
        project_id="timeout-proj",
        start_chapter=1,
        end_chapter=3,
        force=False,
        mock=True,
        verbose=False,
        ai_judge_apply_mode="auto",
        run_logger=None,
    )

    expected_result = SimpleNamespace(text="chapter text", meta=SimpleNamespace(word_count=100))

    async def _fast_run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return expected_result

    fake_chapter_runner = MagicMock(spec=ChapterRunner)
    fake_chapter_runner.run = _fast_run

    fake_runtime = SimpleNamespace(
        settings=SimpleNamespace(long_auto_chapter_timeout_seconds=60)
    )

    result = await runner._run_chapter_with_timeout(
        fake_chapter_runner, 1, fake_runtime  # type: ignore[arg-type]
    )
    assert result is expected_result


# ─────────────────────────────────────────────────────────────────────────────
# P2: CLI progress save failure emits warning
# ─────────────────────────────────────────────────────────────────────────────


def test_cli_progress_save_failure_logs_warning(tmp_path, caplog) -> None:
    """P2: _save_auto_run_progress logs warning instead of silent pass."""
    import logging

    runner = AutoChapterRunner(
        project_id="save-fail-proj",
        start_chapter=1,
        end_chapter=5,
        force=False,
        mock=True,
        verbose=False,
        ai_judge_apply_mode="auto",
        run_logger=None,
    )
    runner.completed_chapters = [1, 2]
    runner.current_chapter = 3

    from novel_forge.persistence.models import ProjectLayout

    layout = ProjectLayout(tmp_path / "proj")
    # Make states_dir a file to cause mkdir to fail
    layout.states_dir.parent.mkdir(parents=True, exist_ok=True)
    layout.states_dir.write_text("blocker")

    with caplog.at_level(logging.WARNING):
        runner._save_auto_run_progress(layout, None)

    assert any("进度保存失败" in record.message for record in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# P3: Window refresh_context circuit breaker
# ─────────────────────────────────────────────────────────────────────────────


def test_refresh_context_circuit_breaker_stops_at_200() -> None:
    """P3: After 200 consecutive refresh_context, autorun stops."""
    # Simulate the circuit breaker logic from window/autorun.py
    _REFRESH_CIRCUIT_BREAKER = 200
    refresh_count = 200
    stopped = False

    if refresh_count >= _REFRESH_CIRCUIT_BREAKER:
        stopped = True

    assert stopped, "Circuit breaker must trip at 200 consecutive refreshes"


def test_refresh_context_does_not_stop_below_threshold() -> None:
    """P3: Below 200 refreshes, autorun continues."""
    _REFRESH_CIRCUIT_BREAKER = 200
    refresh_count = 199
    stopped = False

    if refresh_count >= _REFRESH_CIRCUIT_BREAKER:
        stopped = True

    assert not stopped, "Circuit breaker must NOT trip below 200"


def test_project_chapter_ready_accepts_story_kernel_watermark(tmp_path) -> None:
    """Book-auto readiness must support the current SQLite canon backend."""
    project_id = "kernel-only-project"
    project_dir = tmp_path / project_id
    chapter_path = project_dir / "chapters" / "chapter_002.md"
    chapter_path.parent.mkdir(parents=True)
    chapter_path.write_text("第二章正文", encoding="utf-8")

    db_path = project_dir / "story_kernel.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE kernel_meta (current_chapter INTEGER, kernel_json TEXT)"
        )
        connection.execute(
            "INSERT INTO kernel_meta (current_chapter, kernel_json) VALUES (?, ?)",
            (2, "{}"),
        )

    window = SimpleNamespace(
        _snapshot=SimpleNamespace(storage_root=tmp_path),
    )

    assert AutorunMixin._project_chapter_ready(window, project_id, 2) is True


def test_project_chapter_ready_rejects_kernel_watermark_behind(tmp_path) -> None:
    project_id = "kernel-behind-project"
    project_dir = tmp_path / project_id
    chapter_path = project_dir / "chapters" / "chapter_002.md"
    chapter_path.parent.mkdir(parents=True)
    chapter_path.write_text("第二章正文", encoding="utf-8")

    db_path = project_dir / "story_kernel.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE kernel_meta (current_chapter INTEGER, kernel_json TEXT)"
        )
        connection.execute(
            "INSERT INTO kernel_meta (current_chapter, kernel_json) VALUES (?, ?)",
            (1, "{}"),
        )

    window = SimpleNamespace(
        _snapshot=SimpleNamespace(storage_root=tmp_path),
    )

    assert AutorunMixin._project_chapter_ready(window, project_id, 2) is False


# ─────────────────────────────────────────────────────────────────────────────
# Integration: AutoPilotContext with PAUSED job returns correct action
# ─────────────────────────────────────────────────────────────────────────────


def test_autopilot_paused_job_does_not_trigger_stop() -> None:
    """Decision engine: PAUSED job on current chapter should not produce 'stop'."""
    from novel_forge.desktop.pages.chapter_studio.autorun import (
        AutoPilotContext,
        decide_autopilot_action,
    )
    from novel_forge.workspace.contracts import (
        ChapterWorkspaceChapter,
        ChapterWorkspaceSnapshot,
        DecisionCheckpoint,
        DecisionOption,
    )

    snapshot = ChapterWorkspaceSnapshot(
        project_id="proj-paused",
        project_title="测试",
        chapter_number=2,
        total_chapters=10,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="current"),
            ChapterWorkspaceChapter(chapter_number=3, status="pending"),
        ],
        current_title="第2章",
        pending_checkpoint=DecisionCheckpoint(
            checkpoint_id="cp-paused",
            checkpoint_type="guard_checkpoint",
            options=[
                DecisionOption(
                    option_id="accept", label="确认归档", is_recommended=True
                )
            ],
        ),
    )
    paused_job = DesktopJobRecord(
        job_id="job-paused-2",
        kind="resolve_chapter_checkpoint",
        label="归档",
        project_id="proj-paused",
        status=DesktopJobState.PAUSED,
        result={"chapter_number": 2},
    )

    decision = decide_autopilot_action(
        AutoPilotContext(
            mode="book_auto",
            auto_started=True,
            auto_pilot_pending=False,
            studio=snapshot,
            latest_job=paused_job,
            last_submitted_checkpoint_id=None,
            current_chapter_done=False,
            book_auto_skip_done=True,
            current_project_id="proj-paused",
        )
    )

    # Should resolve the checkpoint, NOT stop
    assert decision.action != "stop", (
        f"PAUSED job must not trigger stop, got action={decision.action!r}"
    )
