"""Tests for the desktop launcher entrypoint."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from novel_forge.core.infra.async_task_processor import (
    async_task_processor_initialized,
    get_async_task_processor,
)
from novel_forge.desktop import main as desktop_main


def test_validate_pyside6_version_accepts_the_supported_range() -> None:
    desktop_main._validate_pyside6_version("6.10.3")


@pytest.mark.parametrize("version", ["6.5.9", "6.11.0", "6.11.1", "7.0.0", "unknown"])
def test_validate_pyside6_version_rejects_unsupported_releases(version: str) -> None:
    with pytest.raises(desktop_main.DesktopRuntimeError, match="PySide6 >=6.6,<6.11"):
        desktop_main._validate_pyside6_version(version)


def test_load_qt_objects_raises_clear_error_when_pyside_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(name: str):  # type: ignore[no-untyped-def]
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(desktop_main, "import_module", fake_import_module)

    with pytest.raises(RuntimeError, match="未安装 PySide6"):
        desktop_main._load_qt_objects()


def test_macos_does_not_force_the_darwin_multimedia_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Darwin/AVFoundation backend cannot decode MiniMax-watermarked MP3s.

    On macOS we must NOT force ``QT_MEDIA_BACKEND=darwin``; Qt's default FFmpeg
    backend is the reliable choice and an explicit user override is still
    honored (see ``test_multimedia_backend_preserves_an_explicit_override``).
    """
    monkeypatch.setattr(desktop_main.sys, "platform", "darwin")
    monkeypatch.delenv("QT_MEDIA_BACKEND", raising=False)

    desktop_main._configure_multimedia_backend()

    assert "QT_MEDIA_BACKEND" not in os.environ


def test_multimedia_backend_preserves_an_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop_main.sys, "platform", "darwin")
    monkeypatch.setenv("QT_MEDIA_BACKEND", "ffmpeg")

    desktop_main._configure_multimedia_backend()

    assert os.environ["QT_MEDIA_BACKEND"] == "ffmpeg"


def test_dispose_desktop_window_flushes_deferred_delete() -> None:
    import shiboken6
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    cleanup_calls: list[bool] = []
    window = QWidget()
    window._is_closing = False  # type: ignore[attr-defined]
    window._pre_close_cleanup = lambda: cleanup_calls.append(True)  # type: ignore[attr-defined]
    app.setProperty("_novel_forge_window", window)
    window.show()

    desktop_main._dispose_desktop_window(app, window)

    assert cleanup_calls == [True]
    assert app.property("_novel_forge_window") is None
    assert not shiboken6.isValid(window)


def test_desktop_async_service_loop_can_execute_tasks_across_event_loops() -> None:
    runner = desktop_main._AsyncServiceLoop()
    try:
        runner.run_coro(desktop_main._initialize_async_services(), timeout=5.0)
        assert async_task_processor_initialized() is True

        processor = get_async_task_processor()

        async def _work(payload: int) -> int:
            await asyncio.sleep(0.01)
            return payload + 1

        async def _run() -> int:
            task_id = await processor.submit(_work, 1)
            return await processor.get_result(task_id, timeout=2.0)

        assert asyncio.run(_run()) == 2
    finally:
        try:
            runner.run_coro(desktop_main._shutdown_async_services(), timeout=5.0)
        finally:
            runner.stop(timeout=5.0)

    assert async_task_processor_initialized() is False


def test_configure_high_dpi_uses_qt6_policy_without_deprecated_attributes() -> None:
    calls: list[object] = []

    class _FakeApplication:
        @staticmethod
        def setAttribute(*_args: object) -> None:
            raise AssertionError("deprecated high-DPI application attribute should not be used")

        @staticmethod
        def setHighDpiScaleFactorRoundingPolicy(policy: object) -> None:
            calls.append(policy)

    fake_policy = object()
    fake_core = SimpleNamespace(
        Qt=SimpleNamespace(
            HighDpiScaleFactorRoundingPolicy=SimpleNamespace(PassThrough=fake_policy)
        )
    )

    desktop_main._configure_high_dpi(_FakeApplication, fake_core)

    assert calls == [fake_policy]


def test_pick_font_does_not_enumerate_font_database(monkeypatch: pytest.MonkeyPatch) -> None:
    selected: dict[str, object] = {}

    class _FakeFont:
        def setFamilies(self, families: list[str]) -> None:
            selected["families"] = families

        def setPointSize(self, size: int) -> None:
            selected["size"] = size

    class _FakeDatabase:
        @staticmethod
        def families() -> list[str]:
            raise AssertionError("font database enumeration should not run during startup")

    monkeypatch.setattr(desktop_main.sys, "platform", "darwin")
    fake_gui = SimpleNamespace(QFont=_FakeFont, QFontDatabase=_FakeDatabase)

    font = desktop_main._pick_font(fake_gui, 13)

    assert isinstance(font, _FakeFont)
    assert selected == {
        "families": ["Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"],
        "size": 13,
    }


def test_configure_font_substitutions_maps_generic_font_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    class _FakeFont:
        @staticmethod
        def insertSubstitutions(alias: str, families: list[str]) -> None:
            calls.append((alias, families))

    monkeypatch.setattr(desktop_main.sys, "platform", "darwin")

    desktop_main._configure_font_substitutions(SimpleNamespace(QFont=_FakeFont))

    assert calls == [
        ("Monospace", ["Menlo", "Monaco", "Courier New"]),
        ("monospace", ["Menlo", "Monaco", "Courier New"]),
        ("Sans Serif", ["Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"]),
        ("Sans-serif", ["Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"]),
        ("sans-serif", ["Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"]),
    ]
