"""Tests for async_task_processor initialization behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from novel_forge.api.app import create_app, lifespan
from novel_forge.core.infra.async_task_processor import (
    AsyncTaskProcessor,
    initialize_async_task_processor,
)


class TestInitializeRaisesOnFailure:
    """Tests for initialize_async_task_processor raising RuntimeError on failure."""

    def test_initialize_raises_on_failure(self) -> None:
        """Verify the function raises RuntimeError when processor.start() fails."""

        async def _run() -> None:
            processor = AsyncTaskProcessor(max_workers=1)

            with patch.object(processor, "start", new_callable=AsyncMock) as mock_start:
                mock_start.side_effect = RuntimeError("Thread pool init failed")
                with patch(
                    "novel_forge.core.infra.async_task_processor.get_async_task_processor",
                    return_value=processor,
                ):
                    with pytest.raises(
                        RuntimeError, match="Failed to initialize async task processor"
                    ):
                        await initialize_async_task_processor()

        asyncio.run(_run())

    def test_initialize_raises_on_generic_exception(self) -> None:
        """Verify RuntimeError is raised even for non-RuntimeError exceptions."""

        async def _run() -> None:
            processor = AsyncTaskProcessor(max_workers=1)

            with patch.object(processor, "start", new_callable=AsyncMock) as mock_start:
                mock_start.side_effect = OSError("System resource unavailable")
                with patch(
                    "novel_forge.core.infra.async_task_processor.get_async_task_processor",
                    return_value=processor,
                ):
                    with pytest.raises(
                        RuntimeError, match="Failed to initialize async task processor"
                    ):
                        await initialize_async_task_processor()

        asyncio.run(_run())


class TestApiFastFailsOnInitFailure:
    """Tests for API lifespan aborting startup on async init failure."""

    def test_api_fast_fails_on_init_failure(self) -> None:
        """Verify API startup is aborted when async task processor init fails."""
        app = create_app()

        async def _run() -> bool:
            raised = False
            try:
                async with lifespan(app):
                    pass  # Should not reach here
            except RuntimeError:
                raised = True
            return raised

        with patch(
            "novel_forge.core.infra.async_task_processor.AsyncTaskProcessor.start",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Thread pool init failed"),
        ):
            result = asyncio.run(_run())

        assert result is True, "API should have raised RuntimeError on init failure"


class TestDesktopGracefulDegradationOnInitFailure:
    """Tests for desktop graceful degradation when async init fails."""

    def test_desktop_graceful_degradation_on_init_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify desktop continues with warning when async init fails."""
        import importlib
        import logging

        from novel_forge.desktop import main as desktop_main

        importlib.reload(desktop_main)

        async_mock = AsyncMock(side_effect=RuntimeError("Thread pool init failed"))
        monkeypatch.setattr(
            "novel_forge.core.infra.async_task_processor.initialize_async_task_processor",
            async_mock,
        )

        called_ok = False
        warning_logged = False

        class FakeLogHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                nonlocal warning_logged
                if "Desktop async service initialization failed" in record.getMessage():
                    warning_logged = True

        handler = FakeLogHandler()
        desktop_main.logger.addHandler(handler)
        try:

            async def _test_init() -> None:
                nonlocal called_ok
                await desktop_main._initialize_async_services()
                called_ok = True

            asyncio.run(_test_init())
        finally:
            desktop_main.logger.removeHandler(handler)

        assert called_ok, "Desktop _initialize_async_services should complete without raising"
        assert warning_logged, "Warning should be logged on init failure"
