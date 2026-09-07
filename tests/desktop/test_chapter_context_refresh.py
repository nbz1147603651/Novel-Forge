"""Tests for exception logging in _ChapterContextRefreshRunnable.run()."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from novel_forge.desktop.window import _ChapterContextRefreshRunnable


class TestChapterContextRefreshExceptionLogging:
    """Verify that _ChapterContextRefreshRunnable logs full traceback on failure."""

    def test_run_logs_exception_on_workspace_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When get_chapter_workspace_snapshot raises, run() logs at ERROR with exc_info."""

        _ChapterContextRefreshRunnable._snapshot_cache.clear()

        workspace = SimpleNamespace(
            runtime=SimpleNamespace(storage=SimpleNamespace(root=None)),
            get_chapter_workspace_snapshot=MagicMock(
                side_effect=RuntimeError("simulated snapshot failure"),
            ),
        )

        runnable = _ChapterContextRefreshRunnable(workspace, "proj-x", 3, None)

        # Spy on signals.failed to confirm it still fires
        failed_calls: list[tuple] = []
        runnable.signals.failed.connect(lambda pid, ch: failed_calls.append((pid, ch)))

        with caplog.at_level(logging.ERROR, logger="novel_forge.desktop.window"):
            runnable.run()

        # 1. signals.failed was emitted with correct args
        assert failed_calls == [("proj-x", 3)]

        # 2. At least one ERROR record with our message
        error_records = [
            r for r in caplog.records if r.levelno >= logging.ERROR
        ]
        assert len(error_records) >= 1, "Expected at least one ERROR log record"

        # 3. Full traceback preserved (exc_info is not None)
        matching = [
            r for r in error_records if "章节上下文刷新失败" in r.getMessage()
        ]
        assert len(matching) >= 1, "Expected log message containing '章节上下文刷新失败'"
        assert matching[0].exc_info is not None, "exc_info must be set (full traceback)"

        _ChapterContextRefreshRunnable._snapshot_cache.clear()
