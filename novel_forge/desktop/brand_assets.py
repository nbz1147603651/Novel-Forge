"""Centralized brand assets for the desktop shell."""

from __future__ import annotations

import logging
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any

from novel_forge.desktop.theme.palettes import (
    DEFAULT_DESKTOP_THEME_ID,
    desktop_theme_token_overrides,
    normalize_theme_id,
)
from novel_forge.desktop.tokens.colors import COLORS

logger = logging.getLogger(__name__)

_RESOURCE_DIR = Path(__file__).resolve().parent / "resources" / "brand"
_NIMO_LOGO_FILE = "nimo-logo.png"
_NIMO_SYMBOL_FILE = "nimo-symbol.svg"
_APP_DISPLAY_NAME = "NIMO"
_BRAND_ICON_TOKEN = "brand.logo.accent"
_APP_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


def brand_logo_path() -> Path:
    """Return the static PNG fallback used by packagers and older callers."""

    return _RESOURCE_DIR / _NIMO_LOGO_FILE


def brand_symbol_path() -> Path:
    """Return the source SVG symbol used for theme-colored logo rendering."""

    return _RESOURCE_DIR / _NIMO_SYMBOL_FILE


def brand_color_for_theme(theme_id: str | None, token_name: str = _BRAND_ICON_TOKEN) -> str:
    """Resolve a theme-aware brand color from semantic tokens."""

    resolved_theme_id = normalize_theme_id(theme_id or DEFAULT_DESKTOP_THEME_ID)
    overrides = desktop_theme_token_overrides(resolved_theme_id)
    if token_name in overrides:
        return overrides[token_name]
    token = COLORS.get(token_name)
    if token is not None:
        return token[0]
    return "#111111"


def build_brand_pixmap(
    qt_gui: Any,
    qt_core: Any | None = None,
    *,
    theme_id: str | None = None,
    size: int = 122,
    device_pixel_ratio: float = 1.0,
    color: str | None = None,
) -> Any:
    """Render the transparent SVG symbol as a pixmap tinted by the active theme."""

    symbol_path = brand_symbol_path()
    if not symbol_path.is_file() or not hasattr(qt_gui, "QPixmap"):
        logger.warning("NIMO symbol asset is not available: %s", symbol_path)
        return qt_gui.QPixmap() if hasattr(qt_gui, "QPixmap") else None

    try:
        qt_core = qt_core or import_module("PySide6.QtCore")
        qt_svg = import_module("PySide6.QtSvg")
        renderer = qt_svg.QSvgRenderer(str(symbol_path))
        if hasattr(renderer, "isValid") and not renderer.isValid():
            logger.warning("NIMO symbol SVG is not valid: %s", symbol_path)
            return qt_gui.QPixmap()

        logical_size = max(1, int(size))
        dpr = max(1.0, float(device_pixel_ratio or 1.0))
        pixel_size = max(1, int(round(logical_size * dpr)))
        transparent = _qt_transparent(qt_core)
        mask = qt_gui.QPixmap(pixel_size, pixel_size)
        mask.fill(transparent)

        painter = qt_gui.QPainter(mask)
        _enable_antialiasing(qt_gui, painter)
        renderer.render(painter, qt_core.QRectF(0, 0, pixel_size, pixel_size))
        painter.end()

        tinted = qt_gui.QPixmap(pixel_size, pixel_size)
        tinted.fill(transparent)
        color_value = color or brand_color_for_theme(theme_id)
        painter = qt_gui.QPainter(tinted)
        painter.fillRect(tinted.rect(), qt_gui.QColor(color_value))
        painter.setCompositionMode(_destination_in_mode(qt_gui))
        painter.drawPixmap(0, 0, mask)
        painter.end()
        if hasattr(tinted, "setDevicePixelRatio"):
            tinted.setDevicePixelRatio(dpr)
        return tinted
    except Exception as exc:  # pragma: no cover - defensive around platform Qt plugins
        logger.warning("Failed to render NIMO symbol: %s", exc)
        return qt_gui.QPixmap()


def build_app_icon(qt_gui: Any, *, theme_id: str | None = None) -> Any:
    """Build a theme-colored QApplication/window icon from the source symbol."""

    if not hasattr(qt_gui, "QIcon") or not hasattr(qt_gui, "QPixmap"):
        path = brand_logo_path()
        icon = qt_gui.QIcon(str(path))
        if not path.is_file() or (hasattr(icon, "isNull") and icon.isNull()):
            logger.warning("NIMO logo icon asset is not available: %s", path)
        return icon

    icon = qt_gui.QIcon()
    qt_core = import_module("PySide6.QtCore")
    for size in _APP_ICON_SIZES:
        pixmap = build_brand_pixmap(qt_gui, qt_core, theme_id=theme_id, size=size)
        if pixmap is not None and not pixmap.isNull():
            icon.addPixmap(pixmap)
    if hasattr(icon, "isNull") and icon.isNull():
        path = brand_logo_path()
        icon = qt_gui.QIcon(str(path))
        logger.warning("Falling back to static NIMO icon asset: %s", path)
    return icon


def apply_app_icon(
    app: Any,
    qt_gui: Any,
    window: Any | None = None,
    *,
    theme_id: str | None = None,
) -> Any:
    """Apply the NIMO icon to QApplication, an optional window, and macOS Dock."""

    icon = build_app_icon(qt_gui, theme_id=_theme_id_from_app(app, theme_id))
    app.setWindowIcon(icon)
    if window is not None:
        window.setWindowIcon(icon)
    # Native AppKit calls are invalid under Qt's headless plugins and can
    # abort the interpreter (rather than raising a catchable exception).
    # QApplication/window icons are still useful in screenshots and tests;
    # only the native Dock integration must be skipped.
    if sys.platform == "darwin" and not _is_headless_qt_platform(app):
        _apply_macos_dock_icon(_runtime_icon_path(icon) or brand_logo_path())
    return icon


def _theme_id_from_app(app: Any, theme_id: str | None) -> str | None:
    if theme_id:
        return theme_id
    if hasattr(app, "property"):
        app_theme = app.property("_novel_forge_desktop_theme")
        return str(app_theme) if app_theme else None
    return None


def _is_headless_qt_platform(app: Any) -> bool:
    platform_name = getattr(app, "platformName", None)
    if not callable(platform_name):
        return False
    try:
        resolved = str(platform_name() or "").strip().lower()
    except Exception:
        return False
    return resolved in {"offscreen", "minimal", "minimalegl"}


def _qt_transparent(qt_core: Any) -> Any:
    global_color = getattr(qt_core.Qt, "GlobalColor", None)
    if global_color is not None:
        return global_color.transparent
    return qt_core.Qt.transparent


def _enable_antialiasing(qt_gui: Any, painter: Any) -> None:
    render_hint = getattr(qt_gui.QPainter, "RenderHint", None)
    if render_hint is not None:
        painter.setRenderHint(render_hint.Antialiasing, True)
        return
    painter.setRenderHint(qt_gui.QPainter.Antialiasing, True)


def _destination_in_mode(qt_gui: Any) -> Any:
    direct_mode = getattr(qt_gui.QPainter, "CompositionMode_DestinationIn", None)
    if direct_mode is not None:
        return direct_mode
    return qt_gui.QPainter.CompositionMode.CompositionMode_DestinationIn


def _runtime_icon_path(icon: Any) -> Path | None:
    if not hasattr(icon, "pixmap"):
        return None
    try:
        pixmap = icon.pixmap(512, 512)
        if pixmap.isNull():
            return None
        path = Path(tempfile.gettempdir()) / "nimo-runtime-app-icon.png"
        if pixmap.save(str(path), "PNG"):
            return path
    except Exception as exc:  # pragma: no cover - best-effort icon export
        logger.debug("Failed to export runtime NIMO Dock icon: %s", exc)
    return None


def _apply_macos_dock_icon(image_path: Path) -> None:
    """Best-effort Dock icon update for source-mode macOS debugging."""

    if _apply_macos_dock_icon_with_pyobjc(image_path):
        return
    _apply_macos_dock_icon_with_objc_runtime(image_path)


def _apply_macos_dock_icon_with_pyobjc(image_path: Path) -> bool:
    try:
        from AppKit import NSApplication, NSImage  # type: ignore[import-not-found]
        from Foundation import NSProcessInfo  # type: ignore[import-not-found]
    except Exception:
        return False

    try:
        NSProcessInfo.processInfo().setProcessName_(_APP_DISPLAY_NAME)
        image = NSImage.alloc().initWithContentsOfFile_(str(image_path))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)
            return True
    except Exception as exc:  # pragma: no cover - depends on macOS AppKit state
        logger.debug("Failed to apply macOS Dock icon: %s", exc)
    return False


def _apply_macos_dock_icon_with_objc_runtime(image_path: Path) -> bool:
    try:
        import ctypes
        from ctypes.util import find_library

        objc_path = find_library("objc")
        appkit_path = find_library("AppKit")
        if objc_path is None or appkit_path is None:
            return False
        ctypes.CDLL(appkit_path)
        objc = ctypes.CDLL(objc_path)

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p

        def cls(name: bytes) -> int:
            return int(objc.objc_getClass(ctypes.c_char_p(name)) or 0)

        def sel(name: bytes) -> int:
            return int(objc.sel_registerName(ctypes.c_char_p(name)) or 0)

        def msg(receiver: int, selector: bytes, *args: object) -> int:
            converted_args = [ctypes.c_void_p(arg) if isinstance(arg, int) else arg for arg in args]
            return int(
                objc.objc_msgSend(
                    ctypes.c_void_p(receiver),
                    ctypes.c_void_p(sel(selector)),
                    *converted_args,
                )
                or 0
            )

        ns_string = cls(b"NSString")
        ns_image = cls(b"NSImage")
        ns_application = cls(b"NSApplication")
        ns_process_info = cls(b"NSProcessInfo")
        if not ns_string or not ns_image or not ns_application:
            return False

        path_string = msg(
            ns_string,
            b"stringWithUTF8String:",
            ctypes.c_char_p(str(image_path).encode("utf-8")),
        )
        app_name = msg(
            ns_string,
            b"stringWithUTF8String:",
            ctypes.c_char_p(_APP_DISPLAY_NAME.encode("utf-8")),
        )
        if ns_process_info and app_name:
            process_info = msg(ns_process_info, b"processInfo")
            if process_info:
                msg(process_info, b"setProcessName:", app_name)

        image = msg(msg(ns_image, b"alloc"), b"initWithContentsOfFile:", path_string)
        application = msg(ns_application, b"sharedApplication")
        if not image or not application:
            return False
        msg(application, b"setApplicationIconImage:", image)
        return True
    except Exception as exc:  # pragma: no cover - macOS runtime bridge
        logger.debug("Failed to apply macOS Dock icon through objc runtime: %s", exc)
        return False
