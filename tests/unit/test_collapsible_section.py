"""Tests for CollapsibleSection persistence behavior.

Verifies that when ``persist_key`` is provided, the user's last expanded
state survives page rebuilds (i.e. destruction + re-instantiation of the
widget). Also covers the non-persistent default and the fallback behavior
when the stored value is missing or invalid.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel

from novel_forge.desktop.components import CollapsibleSection


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def isolated_settings_key() -> str:
    """Yield a unique QSettings key, then clean it up after the test.

    QSettings persists across runs (macOS: ``~/Library/Preferences/``), so each
    test must use a unique key and remove it on teardown to avoid cross-test
    contamination.
    """
    import uuid

    key = f"test_collapsible_section_{uuid.uuid4().hex}"
    yield key
    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    settings.remove(key)
    settings.sync()


def test_default_expanded_is_used_when_no_persist_key(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    section = CollapsibleSection("Default On", expanded=True)
    assert section._toggle.isChecked() is True

    section2 = CollapsibleSection("Default Off", expanded=False)
    assert section2._toggle.isChecked() is False


def test_persisted_true_overrides_default_false(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    settings.setValue(isolated_settings_key, True)
    settings.sync()

    section = CollapsibleSection(
        "Loaded Expanded",
        expanded=False,
        persist_key=isolated_settings_key,
    )
    assert section._toggle.isChecked() is True


def test_persisted_false_overrides_default_true(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    settings.setValue(isolated_settings_key, False)
    settings.sync()

    section = CollapsibleSection(
        "Loaded Collapsed",
        expanded=True,
        persist_key=isolated_settings_key,
    )
    assert section._toggle.isChecked() is False


def test_user_toggle_persists_across_rebuild(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    section1 = CollapsibleSection(
        "Survives Rebuild",
        expanded=True,
        persist_key=isolated_settings_key,
    )
    assert section1._toggle.isChecked() is True

    # Simulate a user click → _on_toggled fires → state is saved.
    section1._toggle.click()
    assert section1._toggle.isChecked() is False

    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    saved = settings.value(isolated_settings_key, None, type=bool)
    assert saved is False

    # Rebuild the page: a brand-new instance must come back collapsed.
    section2 = CollapsibleSection(
        "Survives Rebuild",
        expanded=True,
        persist_key=isolated_settings_key,
    )
    assert section2._toggle.isChecked() is False


def test_set_expanded_persists(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    section = CollapsibleSection(
        "Programmatic Toggle",
        expanded=False,
        persist_key=isolated_settings_key,
    )
    section.set_expanded(True)
    assert section._toggle.isChecked() is True

    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    assert settings.value(isolated_settings_key, None, type=bool) is True

    section.set_expanded(False)
    assert settings.value(isolated_settings_key, None, type=bool) is False


def test_missing_persist_key_never_writes_settings(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    section = CollapsibleSection("Ephemeral", expanded=True)
    section._toggle.click()

    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    assert settings.value(isolated_settings_key, None) is None


def test_corrupt_stored_value_falls_back_to_default(
    qapp: QApplication,
    isolated_settings_key: str,
) -> None:
    settings = QSettings(
        CollapsibleSection._SETTINGS_ORG,
        CollapsibleSection._SETTINGS_APP,
    )
    settings.setValue(isolated_settings_key, "not-a-bool")
    settings.sync()

    # Corrupt value must be ignored; the caller-supplied default wins.
    section = CollapsibleSection(
        "Corrupt Store",
        expanded=True,
        persist_key=isolated_settings_key,
    )
    assert section._toggle.isChecked() is True


def test_lazy_body_builder_runs_once_on_expand(qapp: QApplication) -> None:
    calls: list[str] = []

    def build_body(layout) -> None:  # type: ignore[no-untyped-def]
        calls.append("built")
        layout.addWidget(QLabel("Deferred body"))

    section = CollapsibleSection(
        "Lazy Body",
        expanded=False,
        lazy_body_builder=build_body,
    )

    assert section.body_built is False
    assert calls == []

    section.set_expanded(True)
    assert section.body_built is True
    assert calls == ["built"]

    section.set_expanded(False)
    section.set_expanded(True)
    section.ensure_body_built()
    assert calls == ["built"]
