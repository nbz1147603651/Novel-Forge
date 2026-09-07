"""Tests for desktop workflow status tone mapping."""

from __future__ import annotations

from novel_forge.desktop.constants import JOB_STATUS_TONE


def test_job_status_tones_follow_running_pending_done_convention() -> None:
    # 未运行：灰色
    assert JOB_STATUS_TONE["queued"] == "muted"
    assert JOB_STATUS_TONE["paused"] == "muted"
    # 运行：红色语义（warning）
    assert JOB_STATUS_TONE["running"] == "warning"
    # 完成：绿色
    assert JOB_STATUS_TONE["succeeded"] == "success"
    # 失败：红色错误
    assert JOB_STATUS_TONE["failed"] == "danger"
