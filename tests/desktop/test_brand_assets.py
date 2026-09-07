"""Tests for shared NIMO brand assets."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.desktop


def test_brand_logo_resource_exists() -> None:
    from novel_forge.desktop.brand_assets import brand_logo_path

    path = brand_logo_path()

    assert path.name == "nimo-logo.png"
    assert path.is_file()


def test_brand_symbol_resource_is_themeable_svg() -> None:
    from novel_forge.desktop.brand_assets import brand_symbol_path

    path = brand_symbol_path()
    source = path.read_text(encoding="utf-8")

    assert path.name == "nimo-symbol.svg"
    assert path.is_file()
    assert "<svg" in source
    assert "viewBox" in source
    assert 'id="ai-story-paths"' in source
    assert 'id="ai-writing-nib"' in source
    assert 'id="nimo-wordmark"' in source
    assert "#b65634" not in source


def test_brand_pixmap_uses_theme_accent(desktop_app: object) -> None:
    from PySide6 import QtCore, QtGui

    from novel_forge.desktop.brand_assets import build_brand_pixmap

    pixmap = build_brand_pixmap(QtGui, QtCore, theme_id="ink_jade", size=64)
    image = pixmap.toImage()
    opaque_colors = {
        image.pixelColor(x, y).name().lower()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 220
    }

    assert not pixmap.isNull()
    assert "#2f7667" in opaque_colors
    assert "#b65634" not in opaque_colors


def test_brand_pixmap_renders_at_device_pixel_ratio(desktop_app: object) -> None:
    from PySide6 import QtCore, QtGui

    from novel_forge.desktop.brand_assets import build_brand_pixmap

    pixmap = build_brand_pixmap(
        QtGui,
        QtCore,
        theme_id="ink_jade",
        size=64,
        device_pixel_ratio=2.0,
    )

    assert not pixmap.isNull()
    assert pixmap.width() == 128
    assert pixmap.height() == 128
    assert pixmap.devicePixelRatioF() == 2.0


def test_apply_app_icon_sets_application_and_window_icons(monkeypatch: pytest.MonkeyPatch) -> None:
    from novel_forge.desktop import brand_assets

    class _FakeIcon:
        def __init__(self, path: str) -> None:
            self.path = path

        def isNull(self) -> bool:
            return False

    class _FakeTarget:
        def __init__(self) -> None:
            self.icon: _FakeIcon | None = None

        def setWindowIcon(self, icon: _FakeIcon) -> None:
            self.icon = icon

    monkeypatch.setattr(brand_assets.sys, "platform", "linux")
    fake_gui = SimpleNamespace(QIcon=_FakeIcon)
    app = _FakeTarget()
    window = _FakeTarget()

    icon = brand_assets.apply_app_icon(app, fake_gui, window)

    assert app.icon is icon
    assert window.icon is icon
    assert icon.path.endswith("nimo-logo.png")


def test_apply_app_icon_skips_native_dock_call_on_headless_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from novel_forge.desktop import brand_assets

    class _FakeIcon:
        def __init__(self, path: str) -> None:
            self.path = path

        def isNull(self) -> bool:
            return False

    class _HeadlessApp:
        def __init__(self) -> None:
            self.icon: _FakeIcon | None = None

        @staticmethod
        def platformName() -> str:
            return "offscreen"

        def setWindowIcon(self, icon: _FakeIcon) -> None:
            self.icon = icon

    monkeypatch.setattr(
        brand_assets,
        "_apply_macos_dock_icon",
        lambda _path: pytest.fail("native Dock API must not run offscreen"),
    )
    monkeypatch.setattr(brand_assets.sys, "platform", "darwin")
    app = _HeadlessApp()

    icon = brand_assets.apply_app_icon(app, SimpleNamespace(QIcon=_FakeIcon))

    assert app.icon is icon
