"""Tests for ChapterStudio jobs panel render coalescing (I-2)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pytestqt.qtbot import QtBot


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def studio_page(qtbot: QtBot):
    """Create a ChapterStudioPage, register with qtbot, return it."""
    from novel_forge.desktop.pages.chapter_studio.page import ChapterStudioPage

    page = ChapterStudioPage()
    qtbot.addWidget(page)
    return page


def _job(*, marker: str) -> MagicMock:
    job = MagicMock()
    job.job_id = "job-1"
    job.status = "running"
    job.marker = marker
    return job


def _prime_existing_job_card(studio_page, job: MagicMock) -> None:
    studio_page._chapter_job_cards = {job.job_id: MagicMock()}
    studio_page._latest_render_jobs = [job]


def test_schedule_jobs_panel_render_coalesces_within_16ms(studio_page):
    """Event-only updates within 16ms should result in one panel render."""
    # Patch _render_jobs_panel on the page so we can observe calls
    studio_page._render_jobs_panel = MagicMock()

    jobs1 = [_job(marker="first")]
    jobs2 = [_job(marker="second")]
    jobs3 = [_job(marker="last")]
    _prime_existing_job_card(studio_page, jobs1[0])

    studio_page._schedule_jobs_panel_render(jobs1, loading=False)
    studio_page._schedule_jobs_panel_render(jobs2, loading=True)
    studio_page._schedule_jobs_panel_render(jobs3, loading=True)

    # pending 已设，render 还未被调
    assert studio_page._jobs_panel_render_pending is True
    studio_page._render_jobs_panel.assert_not_called()

    # 模拟 timer 触发
    studio_page._flush_jobs_panel_render()

    # 仅最后一次的 jobs 被传入
    assert studio_page._render_jobs_panel.call_count == 1
    args, kwargs = studio_page._render_jobs_panel.call_args
    assert args[0] == jobs3
    assert kwargs.get("loading") is True


def test_schedule_after_flush_renders_again(studio_page):
    studio_page._render_jobs_panel = MagicMock()

    jobs = [_job(marker="stable")]
    _prime_existing_job_card(studio_page, jobs[0])
    studio_page._schedule_jobs_panel_render(jobs, loading=False)
    studio_page._flush_jobs_panel_render()
    studio_page._schedule_jobs_panel_render(jobs, loading=True)
    studio_page._flush_jobs_panel_render()

    assert studio_page._render_jobs_panel.call_count == 2


def test_bind_jobs_other_steps_remain_synchronous(studio_page):
    """Verify _schedule_jobs_panel_render only affects render, not other bind_jobs side effects."""
    # Mock other bind_jobs steps to verify they run synchronously
    studio_page._update_active_projects = MagicMock()
    studio_page._update_status_dot = MagicMock()
    studio_page._rail = MagicMock()
    studio_page._render_action_panel = MagicMock()
    studio_page._render_jobs_panel = MagicMock()

    jobs = [_job(marker="event-update")]
    _prime_existing_job_card(studio_page, jobs[0])

    # Simulate the relevant bind_jobs steps in order
    studio_page._update_active_projects()
    studio_page._update_status_dot()
    studio_page._rail.bind_jobs([], project_id="x")
    studio_page._render_action_panel()
    # This is the only call that is now throttled
    studio_page._schedule_jobs_panel_render(jobs, loading=False)

    # Synchronous steps called immediately
    assert studio_page._update_active_projects.called
    assert studio_page._update_status_dot.called
    assert studio_page._rail.bind_jobs.called
    assert studio_page._render_action_panel.called
    # _render_jobs_panel not yet called (pending in timer)
    assert not studio_page._render_jobs_panel.called


def test_structural_jobs_change_renders_in_same_turn(studio_page):
    """Adding a task is user-visible state and must bypass event-only throttling."""
    studio_page._render_jobs_panel = MagicMock()
    existing = _job(marker="existing")
    added = MagicMock()
    added.job_id = "job-2"
    added.status = "queued"
    _prime_existing_job_card(studio_page, existing)

    studio_page._schedule_jobs_panel_render([existing, added], loading=False)

    studio_page._render_jobs_panel.assert_called_once_with([existing, added], loading=False)
    assert studio_page._jobs_panel_render_pending is False
