"""Regression tests for Voice Studio staged construction."""

from __future__ import annotations

from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.page import VoiceStudioPage
from novel_forge.persistence.models import ProjectLayout


def _page(qtbot: QtBot) -> VoiceStudioPage:
    page = VoiceStudioPage(settings=Settings(_env_file=None))
    qtbot.addWidget(page)
    return page


def test_initial_page_defers_non_visible_tabs(qtbot: QtBot) -> None:
    page = _page(qtbot)

    assert set(page._deferred_tab_builders) == {1, 2, 3, 4}
    assert page._audio_player is None
    assert page._synth_anim_timer is None
    assert page._character_list.property("scrollFadeDisabled") is True
    viewport = page._character_list.viewport()
    assert not viewport.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert viewport.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


def test_showing_page_does_not_build_unselected_tabs(
    qtbot: QtBot,
    desktop_app: QApplication,
) -> None:
    page = _page(qtbot)

    qtbot.wait(220)
    desktop_app.processEvents()

    assert set(page._deferred_tab_builders) == {1, 2, 3, 4}
    assert not page._deferred_tab_timer.isActive()

    page.show()
    qtbot.wait(220)
    desktop_app.processEvents()

    assert set(page._deferred_tab_builders) == {1, 2, 3, 4}
    assert page._synth_anim_timer is None


def test_selecting_deferred_tab_builds_after_placeholder_paints(qtbot: QtBot) -> None:
    page = _page(qtbot)
    page.show()

    page._tabs.setCurrentIndex(3)

    assert 3 in page._deferred_tab_builders
    assert page._audio_player is None

    qtbot.waitUntil(lambda: 3 not in page._deferred_tab_builders, timeout=1500)
    assert page._audio_player is not None
    assert page._tabs.currentIndex() == 3


def test_page_factory_reuses_shared_settings(qtbot: QtBot) -> None:
    from novel_forge.desktop.page_registrations import _create_voice_studio

    settings = Settings(_env_file=None)
    with patch("novel_forge.core.config.get_settings", return_value=settings) as get_settings:
        page = _create_voice_studio()
    qtbot.addWidget(page)

    get_settings.assert_called_once_with()
    assert page._settings is settings


def test_staged_tabs_load_the_selected_chapter_only_once(qtbot: QtBot, tmp_path) -> None:
    page = _page(qtbot)
    page._deferred_tab_timer.stop()
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    layout.chapter_path(1).write_text("第一章终稿", encoding="utf-8")
    page._layout = layout

    with patch.object(
        page,
        "_load_chapter_artifacts",
        wraps=page._load_chapter_artifacts,
    ) as load_artifacts:
        page._build_deferred_tab(1)
        page._build_deferred_tab(2)
        page._build_deferred_tab(3)

    load_artifacts.assert_called_once_with(1)
    assert page._loaded_chapter_number == 1
