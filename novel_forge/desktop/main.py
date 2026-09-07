"""Launcher for the PySide6 desktop application."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sys
import threading
from collections.abc import Coroutine
from importlib import import_module
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

_ASYNC_SERVICE_TIMEOUT_S = 15.0
_MIN_SUPPORTED_PYSIDE6_VERSION = (6, 6)
_MAX_SUPPORTED_PYSIDE6_VERSION_EXCLUSIVE = (6, 11)


class DesktopRuntimeError(RuntimeError):
    """A desktop runtime prerequisite is missing or unsupported."""


def _parse_version_components(version: str) -> tuple[int, ...]:
    """Return the numeric prefix of a PySide6 version string."""
    components: list[int] = []
    for component in version.split("."):
        digits = "".join(character for character in component if character.isdigit())
        if not digits:
            break
        components.append(int(digits))
    return tuple(components)


def _validate_pyside6_version(version: str) -> None:
    """Reject PySide6 releases outside the desktop extra's tested range.

    This runs before QApplication is created.  In particular, PySide6 6.11.1
    can crash in native Qt startup paths, so a clear terminal error is safer
    than allowing it to reach Cocoa/InputMethodKit on macOS.
    """
    parsed = _parse_version_components(version)
    major_minor = parsed[:2]
    if _MIN_SUPPORTED_PYSIDE6_VERSION <= major_minor < _MAX_SUPPORTED_PYSIDE6_VERSION_EXCLUSIVE:
        return

    raise DesktopRuntimeError(
        f"检测到不受支持的 PySide6 {version}；NIMO 仅支持 "
        "PySide6 >=6.6,<6.11。请在当前环境执行：\n"
        'python -m pip install --upgrade --force-reinstall "PySide6>=6.6,<6.11"\n'
        'python -m pip install -e ".[desktop]"'
    )


def _load_qt_objects() -> tuple[type[Any], type[Any]]:
    try:
        pyside6 = import_module("PySide6")
        _validate_pyside6_version(str(pyside6.__version__))
        widgets = import_module("PySide6.QtWidgets")
    except ModuleNotFoundError as exc:
        raise DesktopRuntimeError(
            "未安装 PySide6。请先执行 `pip install -e .[desktop]`，然后重新运行桌面端。"
        ) from exc
    return widgets.QApplication, widgets.QMessageBox


async def _initialize_async_services() -> None:
    """Initialize async services that require an event loop.

    This should be called during app startup when an asyncio event loop is available.
    Note: The desktop app uses Qt's event loop, so this is called in a separate
    event loop context for async services that need it.
    Logs a warning if initialization fails.
    """
    from novel_forge.core.infra.async_task_processor import initialize_async_task_processor

    try:
        await initialize_async_task_processor()
    except Exception as exc:
        logger.warning("Desktop async service initialization failed: %s", exc)


async def _shutdown_async_services() -> None:
    """Shutdown async services started by desktop startup."""
    from novel_forge.core.infra.async_task_processor import shutdown_async_task_processor

    await shutdown_async_task_processor()


class _AsyncServiceLoop:
    """Dedicated asyncio loop runner used alongside the Qt event loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="novel-forge-async-services",
            daemon=True,
        )

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def start(self) -> None:
        if self._thread.is_alive():
            return
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Failed to start async service event loop thread")

    def run_coro(self, coro: Coroutine[Any, Any, _T], *, timeout: float = 5.0) -> _T:
        self.start()
        loop = self._loop
        if loop is None:
            raise RuntimeError("Async service event loop is not available")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("Timed out waiting for async service operation") from exc

    def stop(self, *, timeout: float = 5.0) -> None:
        if not self._thread.is_alive():
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=timeout)


def _configure_high_dpi(qt_application: type[Any], qt_core: Any) -> None:
    """Apply Qt 6 high-DPI policy without deprecated application attributes."""
    qt_application.setHighDpiScaleFactorRoundingPolicy(
        qt_core.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )


def _configure_font_substitutions(qt_gui: Any) -> None:
    """Map generic Qt/HTML aliases to concrete platform fonts."""
    if sys.platform == "darwin":
        mono_families = ["Menlo", "Monaco", "Courier New"]
        sans_families = ["Songti SC", "STSong", "SimSun", "Times New Roman", "Arial"]
    elif sys.platform == "win32":
        mono_families = ["Consolas", "Courier New"]
        sans_families = ["SimSun", "STSong", "Times New Roman", "Arial"]
    else:
        mono_families = ["DejaVu Sans Mono", "Noto Sans Mono", "Liberation Mono", "Courier New"]
        sans_families = ["Noto Serif CJK SC", "Noto Serif", "Times New Roman", "Arial"]

    font_cls = qt_gui.QFont
    if hasattr(font_cls, "insertSubstitutions"):
        font_cls.insertSubstitutions("Monospace", mono_families)
        font_cls.insertSubstitutions("monospace", mono_families)
        for alias in ("Sans Serif", "Sans-serif", "sans-serif"):
            font_cls.insertSubstitutions(alias, sans_families)


def _apply_windows_event_loop_policy() -> None:
    """On Windows, install the SelectorEventLoopPolicy to avoid ProactorEventLoop issues.

    Must be called BEFORE ``asyncio.new_event_loop()`` (which happens in
    ``_AsyncServiceLoop._run`` at line 73). Some Windows socket calls fail
    with ``OSError: [WinError 121]`` under the default ProactorEventLoop.
    """
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _configure_multimedia_backend() -> None:
    """Respect an explicit ``QT_MEDIA_BACKEND`` override; otherwise use Qt's default.

    Qt's default FFmpeg backend reliably decodes the MP3s produced by every
    supported TTS provider, including MiniMax's AIGC-watermarked files.  The
    native Darwin (AVFoundation) backend cannot probe past that watermark and
    reports ``InvalidMedia`` for otherwise-valid auditions, leaving Voice
    Studio playback silent.  We therefore no longer force ``darwin`` and let
    Qt select FFmpeg unless the user has set ``QT_MEDIA_BACKEND`` themselves.
    """

    # Intentionally a no-op: only an explicit user-set env var takes effect.
    # Kept as a call site so the policy is centralized and easy to revisit.


def _dispose_desktop_window(app: Any, window: Any) -> None:
    """Destroy the main QObject tree while Qt's event dispatcher is still alive."""
    app.setProperty("_novel_forge_window", None)
    try:
        if not getattr(window, "_is_closing", False):
            window._pre_close_cleanup()
    except Exception as exc:
        logger.warning("Desktop pre-close cleanup failed: %s", exc)
    try:
        window.hide()
        window.deleteLater()
        qt_core = import_module("PySide6.QtCore")
        qt_core.QCoreApplication.sendPostedEvents(
            None,
            qt_core.QEvent.Type.DeferredDelete,
        )
        app.processEvents()
    except RuntimeError:
        # The C++ window may already have been deleted by Qt.
        pass


def launch_desktop() -> int:
    """Launch the desktop UI and return the Qt exit code."""
    _apply_windows_event_loop_policy()  # NEW: I-7
    # Must run before importing the window/page graph: Voice Studio imports
    # QtMultimedia while page registrations are initialized.
    _configure_multimedia_backend()
    qt_application, qt_message_box = _load_qt_objects()
    qt_gui = import_module("PySide6.QtGui")
    from novel_forge.core.config import get_settings
    from novel_forge.desktop.brand_assets import apply_app_icon
    from novel_forge.desktop.theme.runtime import apply_desktop_theme, warm_theme_stylesheet_cache
    from novel_forge.desktop.tokens.typography import apply_desktop_typography
    from novel_forge.desktop.window import NovelForgeDesktopWindow

    app = qt_application.instance()
    owns_app = app is None
    if app is None:
        # Qt 6 enables high-DPI scaling and high-DPI pixmaps by default; the
        # old application attributes now emit deprecation warnings on startup.
        qt_core = import_module("PySide6.QtCore")
        _configure_high_dpi(qt_application, qt_core)
        _configure_font_substitutions(qt_gui)
        app = qt_application(sys.argv)
    assert app is not None
    app.setApplicationName("NIMO")
    app.setApplicationDisplayName("NIMO")
    app.setOrganizationName("Novel Forge")  # 保留作为 fallback
    # Fusion style provides consistent cross-platform rendering;
    # must be set before setStyleSheet() to ensure QSS works correctly.
    if owns_app:
        app.setStyle("Fusion")
    settings = get_settings()
    warm_theme_stylesheet_cache()
    apply_desktop_theme(getattr(settings, "desktop_theme", None), app=app)
    apply_desktop_typography(
        app,
        ui_profile=str(getattr(settings, "desktop_ui_font_family", "source_sans")),
        reading_profile=str(
            getattr(settings, "desktop_reading_font_family", "source_serif")
        ),
        scale=getattr(settings, "desktop_font_scale", 1.0),
    )
    app_icon = apply_app_icon(app, qt_gui)

    async_services = _AsyncServiceLoop() if owns_app else None
    if async_services is not None:
        from novel_forge.core.infra.async_runner import register_async_runner

        register_async_runner(async_services)
        try:
            async_services.run_coro(_initialize_async_services(), timeout=_ASYNC_SERVICE_TIMEOUT_S)
        except Exception as exc:
            logger.warning("Desktop async service initialization failed: %s", exc)

    try:
        window = NovelForgeDesktopWindow()
        window.setWindowIcon(app_icon)
        window.show()
        app.setProperty("_novel_forge_window", window)
    except Exception as exc:  # pragma: no cover - defensive UI boot guard
        if async_services is not None:
            # Don't block on graceful shutdown during startup failure.
            async_services.stop(timeout=0)
        exc_msg = str(exc).strip() or type(exc).__name__
        logger.error("桌面端启动失败: %s", exc_msg, exc_info=True)
        qt_message_box.critical(None, "桌面端启动失败", exc_msg)
        return 1

    if owns_app:
        try:
            return int(app.exec())
        finally:
            # Do not leave the main QObject tree for PySide's atexit cleanup.
            # Native QApplication destruction order is intentionally opaque;
            # explicitly flushing DeferredDelete events prevents stale posted
            # events from targeting wrappers that Python has already released.
            _dispose_desktop_window(app, window)
            # The PySide client never owns a sidecar.  It only asks the Engine
            # service to manage one, so process teardown releases that shared
            # Engine-owned resource after the job manager has stopped workers.
            from novel_forge.app_service.ollama_control import shutdown_ollama_control_service

            shutdown_ollama_control_service()
            if async_services is not None:
                # The async-service loop runs in a daemon thread: no need to
                # block the main thread waiting for its graceful shutdown.
                # Stopping the loop (with timeout=0) schedules loop.stop(); the
                # daemon thread will finish cleaning up on its own and will be
                # killed automatically when the process exits.
                from novel_forge.core.infra.async_runner import register_async_runner

                register_async_runner(None)
                async_services.stop(timeout=0)
    return 0


def main() -> None:
    try:
        raise SystemExit(launch_desktop())
    except DesktopRuntimeError as exc:
        print(f"NIMO 未能启动：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _pick_font(
    qt_gui: Any,
    size: int,
    *,
    family_profile: str = "source_sans",
    scale: float = 1.0,
) -> Any:
    """Compatibility seam for startup tests; delegates to typography tokens."""
    from novel_forge.desktop.tokens.typography import desktop_font

    return desktop_font(
        size,
        family_profile=family_profile,
        scale=scale,
        font_cls=qt_gui.QFont,
    )


def _apply_reading_font_profile(app: Any, profile: str) -> None:
    """Compatibility shim for integrations importing the former helper."""
    from novel_forge.desktop.tokens.typography import apply_reading_font_profile

    apply_reading_font_profile(app, profile)


if __name__ == "__main__":
    main()
