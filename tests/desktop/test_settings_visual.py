"""Visual regression baseline capture for SettingsPage.

Captures ``tests/desktop/baselines/settings.png`` so future UI polish
work can detect regressions in the settings surface.  Runs under
``QT_QPA_PLATFORM=offscreen`` (set in ``pyproject.toml``).

Usage:
    pytest tests/desktop/test_settings_visual.py -v
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from tests.desktop.visual_regression import (
    VISUAL_STORAGE_ROOT,
    capture_widget_screenshot,
    save_baseline,
)

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PYTEST_XDIST_WORKER")),
    reason="PySide visual baseline capture is xdist-unsafe; run this test serially.",
)


def _visual_profiles_config() -> object:
    """Deterministic profile config so settings page doesn't read disk."""
    from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig

    profile = ModelProfile(
        profile_id="openai:gpt-4o-mini",
        display_name="OpenAI Mini",
        provider="openai",
        model_id="gpt-4o-mini",
        api_key="sk-test",
    )
    return ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)


@pytest.fixture
def visual_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Deterministic workspace + QSS + profile setup for settings snapshot."""
    root = VISUAL_STORAGE_ROOT
    root.mkdir(parents=True, exist_ok=True)

    def _load_profiles(_settings: object | None = None) -> object:
        return _visual_profiles_config()

    from PySide6.QtWidgets import QApplication

    import novel_forge.desktop.pages.settings.page as settings_page
    from novel_forge.desktop.theme import get_stylesheet

    monkeypatch.setattr(settings_page, "load_or_import_profiles", _load_profiles)

    app = QApplication.instance()
    old_stylesheet = app.styleSheet() if isinstance(app, QApplication) else ""
    if isinstance(app, QApplication):
        app.setStyleSheet(get_stylesheet())
    try:
        yield root
    finally:
        if isinstance(app, QApplication):
            app.setStyleSheet(old_stylesheet)


class TestSettingsVisualBaseline:
    """SettingsPage visual regression baseline (Task 18)."""

    def test_settings_baseline_captured(
        self,
        qtbot: QtBot,
        visual_workspace: Path,
        update_baselines: bool,
    ) -> None:
        if not update_baselines:
            pytest.skip("Baseline capture updates PNGs; run with --update-baselines.")

        from novel_forge.desktop.pages.settings.page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)
        page.resize(1200, 800)
        page.show()

        from tests.desktop.test_visual_regression import _render_page_with_workspace

        _render_page_with_workspace(page, qtbot, visual_workspace)

        image = capture_widget_screenshot(page)
        baseline_path = save_baseline("settings", image)
        assert baseline_path.exists(), f"Baseline not created at {baseline_path}"
        assert baseline_path.stat().st_size > 0, "Baseline PNG is empty"
