"""Regression tests for floating desktop surfaces.

Transparent top-level Qt widgets can skip QSS styled-background painting on
some platforms. These tests sample rendered pixels so text-only/transparent
floating windows cannot regress silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from novel_forge.desktop.theme import get_stylesheet

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(get_stylesheet())
    return app


def test_toast_grab_contains_painted_background(qapp: QApplication) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget

    from novel_forge.desktop.components.toast import Toast

    parent = QWidget()
    parent.resize(900, 600)
    toast = Toast("测试通知浮窗", "info", parent=parent)
    toast._opacity_effect.setOpacity(1.0)
    toast.resize(toast.sizeHint())

    image = toast.grab().toImage()
    sample = QColor(image.pixelColor(10, max(1, image.height() // 2)))

    assert sample.alpha() == 245
    assert (sample.red(), sample.green(), sample.blue()) == (255, 250, 243)


def test_toast_corners_remain_transparent_under_global_qss(qapp: QApplication) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget

    from novel_forge.desktop.components.toast import Toast

    parent = QWidget()
    parent.resize(900, 600)
    toast = Toast("测试通知浮窗", "info", parent=parent)
    toast._opacity_effect.setOpacity(1.0)
    toast.resize(toast.sizeHint())

    image = toast.grab().toImage()
    corners = [
        QColor(image.pixelColor(0, 0)),
        QColor(image.pixelColor(image.width() - 1, 0)),
        QColor(image.pixelColor(0, image.height() - 1)),
        QColor(image.pixelColor(image.width() - 1, image.height() - 1)),
    ]

    assert all(corner.alpha() == 0 for corner in corners)


def test_character_tooltip_corners_are_not_opaque_background(
    qapp: QApplication,
) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget

    from novel_forge.desktop.pages.document_renderers import _CharacterTooltipPopup

    parent = QWidget()
    parent.resize(900, 600)
    popup = _CharacterTooltipPopup(parent)
    popup.set_content("<div style='width:300px;'>测试悬浮窗背景</div>")
    popup.resize(popup.sizeHint())

    image = popup.grab().toImage()
    corners = [
        QColor(image.pixelColor(0, 0)),
        QColor(image.pixelColor(image.width() - 1, 0)),
        QColor(image.pixelColor(0, image.height() - 1)),
        QColor(image.pixelColor(image.width() - 1, image.height() - 1)),
    ]

    assert all(corner.alpha() < 128 for corner in corners)


def test_settings_fallback_popup_grab_contains_opaque_background(
    qapp: QApplication,
) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QWidget

    from novel_forge.desktop.pages.settings.components import _FallbackRoutesPopup

    parent = QWidget()
    parent.resize(900, 600)
    popup = _FallbackRoutesPopup(
        profiles=[("mock", "Mock Model", True)],
        fallback_routes=[],
        show_multi_turn=True,
        capability_resolver=lambda _profile_id: (True, True),
        parent=parent,
    )
    popup.resize(440, popup.sizeHint().height())

    image = popup.grab().toImage()
    sample = QColor(image.pixelColor(10, max(1, image.height() // 2)))

    assert sample.alpha() == 255
    assert (sample.red(), sample.green(), sample.blue()) == (255, 250, 243)


def test_settings_fallback_popup_keeps_controls_inside_narrow_list_viewport(
    qapp: QApplication,
) -> None:
    """Long model names must not push the popup's controls off the right edge."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    from novel_forge.desktop.pages.settings.components import _FallbackRoutesPopup

    parent = QWidget()
    parent.resize(900, 600)
    popup = _FallbackRoutesPopup(
        profiles=[
            ("mimo", "小米 MiMo mimo-v2.5", True),
            ("deepseek", "火山方舟 (Volcano Ark) deepseek-v4-flash", True),
            ("doubao", "火山方舟 (Volcano Ark) doubao-seed-2.0-lite", True),
        ],
        fallback_routes=[],
        show_multi_turn=True,
        capability_resolver=lambda _profile_id: (True, True),
        parent=parent,
    )
    popup.resize(420, popup.sizeHint().height())
    popup.show()
    qapp.processEvents()

    viewport = popup._list.viewport()
    for index in range(popup._list.count()):
        item = popup._list.item(index)
        row = popup._list.itemWidget(item)
        assert row is not None
        assert row._thinking_cb.currentText() == "思考：关闭"
        for control in (row._thinking_cb, row._multi_turn_cb):
            right = control.mapTo(viewport, QPoint(control.width(), 0)).x()
            assert right <= viewport.width()
