"""Tests for ``_CharacterTooltipPopup`` and its themed QSS contract.

Verifies that:

1. ``_CharacterTooltipPopup`` is constructible and is a ``QFrame`` subclass.
2. The popup advertises itself with ``objectName="characterTooltipPopup"``
   for diagnostics and compatibility, but the top-level object is not targeted
   by QSS.
3. The popup uses ``Qt.WindowType.FramelessWindowHint`` (required for the
   rounded-corner render path on macOS where the native QToolTip ignores
   ``border-radius``).
4. The generated stylesheet from ``get_stylesheet()`` styles only the child
   tooltip labels, not the top-level QFrame popup that owns the transparent
   rounded shell.
5. The translucent top-level popup does not let QSS draw its background;
   the rounded shell is painted by ``paintEvent`` so macOS compositing does
   not leave rectangular corner pixels outside the curve.
6. ``set_content(html)`` updates the inner label's HTML content.

Related plan task: ``ui-rounded-corners-polish`` Task 14.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from novel_forge.desktop.theme import get_stylesheet

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

    from novel_forge.desktop.pages.document_renderers import (
        _BlueprintTooltipPopup,
        _CharacterTooltipPopup,
    )


@pytest.fixture
def popup(qtbot: QtBot) -> _CharacterTooltipPopup:
    """Build a fresh ``_CharacterTooltipPopup`` registered with ``qtbot``.

    ``qtbot.addWidget`` ensures the widget is destroyed at test teardown so
    no QFrame leaks between tests.
    """
    from novel_forge.desktop.pages.document_renderers import _CharacterTooltipPopup

    widget = _CharacterTooltipPopup()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def blueprint_popup(qtbot: QtBot) -> _BlueprintTooltipPopup:
    from novel_forge.desktop.pages.document_renderers import _BlueprintTooltipPopup

    widget = _BlueprintTooltipPopup()
    qtbot.addWidget(widget)
    return widget


class TestCharacterTooltipPopupConstruction:
    """Smoke tests — is the class importable and instantiable?"""

    def test_popup_class_is_importable(self) -> None:
        from novel_forge.desktop.pages.document_renderers import _CharacterTooltipPopup

        assert _CharacterTooltipPopup is not None

    def test_popup_is_constructible(self, popup: _CharacterTooltipPopup) -> None:
        assert popup is not None

    def test_popup_is_qframe_subclass(self, popup: _CharacterTooltipPopup) -> None:
        from PySide6.QtWidgets import QFrame

        assert isinstance(popup, QFrame)

    def test_popup_uses_shared_painted_surface(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from novel_forge.desktop.components.floating_surface import PaintedFloatingSurface

        assert isinstance(popup, PaintedFloatingSurface)

    def test_popup_advertises_object_name(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        # Keep the objectName stable for diagnostics and compatibility with
        # existing code that identifies the popup.
        assert popup.objectName() == "characterTooltipPopup"

    def test_blueprint_popup_advertises_object_name(
        self, blueprint_popup: _BlueprintTooltipPopup
    ) -> None:
        assert blueprint_popup.objectName() == "blueprintTooltipPopup"


class TestCharacterTooltipPopupFlags:
    """Window flag checks — required for the frameless rounded-corner path."""

    def test_popup_uses_frameless_window_hint(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from PySide6.QtCore import Qt

        flags = popup.windowFlags()
        assert bool(flags & Qt.WindowType.FramelessWindowHint)

    def test_popup_uses_tool_window_flag(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from PySide6.QtCore import Qt

        flags = popup.windowFlags()
        assert bool(flags & Qt.WindowType.Tool)

    def test_popup_stays_on_top(self, popup: _CharacterTooltipPopup) -> None:
        from PySide6.QtCore import Qt

        flags = popup.windowFlags()
        assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)

    def test_popup_is_translucent_background(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from PySide6.QtCore import Qt

        assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def test_popup_uses_manual_background_painting(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from PySide6.QtCore import Qt

        assert popup.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert not popup.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


class TestCharacterTooltipPopupStyling:
    """QSS contract — only child labels are styled by global QSS."""

    def test_qss_contains_popup_label_selector(self) -> None:
        qss = get_stylesheet()
        assert "characterTooltipLabel" in qss

    def test_qss_does_not_target_popup_qframe(self) -> None:
        qss = get_stylesheet()
        selector = "QFrame#characterTooltipPopup"
        assert selector not in qss, "top-level popup selector must not be in QSS"

    def test_qss_contains_blueprint_popup_label_selector(self) -> None:
        qss = get_stylesheet()
        assert "blueprintTooltipLabel" in qss

    def test_qss_does_not_target_blueprint_popup_qframe(self) -> None:
        qss = get_stylesheet()
        selector = "QFrame#blueprintTooltipPopup"
        assert selector not in qss, "top-level popup selector must not be in QSS"

    def test_qss_contains_no_unresolved_placeholders(self) -> None:
        qss = get_stylesheet()
        # ``__TOOLTIP_RADIUS__`` must have been replaced by str(TOOLTIP_RADIUS).
        assert "__TOOLTIP_RADIUS__" not in qss


class TestCharacterTooltipPopupContent:
    """Behavioural check — does ``set_content`` actually update the label?"""

    def test_set_content_updates_inner_label(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from PySide6.QtWidgets import QLabel

        # The class owns a single QLabel child (objectName
        # ``characterTooltipLabel``). It is a private attribute, so we look
        # it up by class rather than relying on name-mangling internals.
        labels = popup.findChildren(QLabel)
        assert labels, "popup should have at least one QLabel child"

        html = "<div style='color: red;'>test-content</div>"
        popup.set_content(html)

        rendered = "\n".join(label.text() for label in labels)
        assert "test-content" in rendered

    def test_initial_content_is_empty(self, popup: _CharacterTooltipPopup) -> None:
        from PySide6.QtWidgets import QLabel

        labels = popup.findChildren(QLabel)
        for label in labels:
            # Newly constructed popup should have no rendered content yet.
            assert label.text() == ""


class TestCharacterTooltipPopupRendering:
    """Regression checks for the custom-painted tooltip shell."""

    def test_character_popup_grab_contains_opaque_background(
        self, popup: _CharacterTooltipPopup
    ) -> None:
        from PySide6.QtGui import QColor

        popup.set_content("<div style='width:220px;'>测试悬浮窗背景</div>")
        popup.resize(popup.sizeHint())

        image = popup.grab().toImage()
        sample = QColor(image.pixelColor(6, max(1, image.height() // 2)))

        assert sample.alpha() == 255
        assert (sample.red(), sample.green(), sample.blue()) == (255, 250, 243)

    def test_blueprint_popup_grab_contains_opaque_background(
        self, blueprint_popup: _BlueprintTooltipPopup
    ) -> None:
        from PySide6.QtGui import QColor

        blueprint_popup.set_content("<div style='width:220px;'>测试悬浮窗背景</div>")
        blueprint_popup.resize(blueprint_popup.sizeHint())

        image = blueprint_popup.grab().toImage()
        sample = QColor(image.pixelColor(6, max(1, image.height() // 2)))

        assert sample.alpha() == 255
        assert (sample.red(), sample.green(), sample.blue()) == (255, 250, 243)

    def test_popup_shell_tracks_the_applied_theme(self, popup: _CharacterTooltipPopup) -> None:
        from PySide6.QtWidgets import QApplication

        from novel_forge.desktop.theme import resolve_qcolor
        from novel_forge.desktop.theme.runtime import apply_desktop_theme

        app = QApplication.instance()
        assert isinstance(app, QApplication)
        old_theme = app.property("_novel_forge_desktop_theme")
        old_stylesheet = app.styleSheet()
        try:
            popup.set_content("<div style='width:220px;'>测试主题切换</div>")
            popup.resize(popup.sizeHint())
            apply_desktop_theme("ink_jade", app=app)

            image = popup.grab().toImage()
            sample = image.pixelColor(6, max(1, image.height() // 2))
            expected = resolve_qcolor("bg.surface")
            assert sample == expected
        finally:
            app.setProperty("_novel_forge_desktop_theme", old_theme)
            app.setStyleSheet(old_stylesheet)


class TestCharacterTooltipPopupPositioning:
    """Positioning checks for edge-of-screen hover placement."""

    def test_fit_position_flips_inside_bottom_right_edge(self) -> None:
        from PySide6.QtCore import QPoint, QRect, QSize

        from novel_forge.desktop.pages.document_renderers import _CharacterTooltipPopup

        available = QRect(0, 0, 800, 600)
        size = QSize(260, 150)
        pos = _CharacterTooltipPopup._fit_to_available_geometry(
            QPoint(796, 596),
            size,
            available,
        )

        assert pos.x() >= _CharacterTooltipPopup.SCREEN_MARGIN
        assert pos.y() >= _CharacterTooltipPopup.SCREEN_MARGIN
        assert (
            pos.x() + size.width()
            <= available.left() + available.width() - _CharacterTooltipPopup.SCREEN_MARGIN
        )
        assert (
            pos.y() + size.height()
            <= available.top() + available.height() - _CharacterTooltipPopup.SCREEN_MARGIN
        )
        assert pos.x() < 796
        assert pos.y() < 596

    def test_fit_position_clamps_when_popup_is_wider_than_screen(self) -> None:
        from PySide6.QtCore import QPoint, QRect, QSize

        from novel_forge.desktop.pages.document_renderers import _CharacterTooltipPopup

        available = QRect(0, 0, 120, 90)
        pos = _CharacterTooltipPopup._fit_to_available_geometry(
            QPoint(100, 80),
            QSize(300, 200),
            available,
        )

        assert pos == QPoint(
            _CharacterTooltipPopup.SCREEN_MARGIN,
            _CharacterTooltipPopup.SCREEN_MARGIN,
        )
