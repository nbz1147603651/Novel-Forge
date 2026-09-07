"""Tests: settings page incremental binding via ``bind_workspace_sections``.

Task 13 of the desktop UI performance plan migrates ``settings_page.py`` to
implement ``bind_workspace_sections(snapshot, sections)`` so that only the
changed snapshot sections trigger re-renders:

- ``"providers"`` → provider card + loaded card
- ``"overview"`` → storage card + mode card
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.desktop.pages.settings.page import SettingsPage  # noqa: E402


def _make_snapshot(**overrides: object) -> SimpleNamespace:
    """Build a minimal snapshot-like object for binding tests."""
    defaults = dict(
        storage_root=Path("/tmp/test_storage"),
        default_provider="mock",
        overview=SimpleNamespace(providers=["mock"]),
        metrics=SimpleNamespace(total_projects=2, configured_providers=1),
        providers=[],
        projects=[],
        featured_project=None,
        details={},
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_config() -> SimpleNamespace:
    """Build a minimal config-like object for settings binding."""
    profile = SimpleNamespace(display_name="Mock 模型")
    return SimpleNamespace(
        profiles=[profile],
        default_profile_id="mock",
        get_profile=MagicMock(return_value=profile),
    )


def _make_harness() -> SimpleNamespace:
    """Create a mock SettingsPage-like object with real ``bind_workspace_sections``."""
    storage_card = MagicMock()
    provider_card = MagicMock()
    loaded_card = MagicMock()
    mode_card = MagicMock()

    harness = SimpleNamespace(
        _snapshot=None,
        _runtime_cards={
            "storage": storage_card,
            "provider": provider_card,
            "loaded": loaded_card,
            "mode": mode_card,
        },
        _config=_make_config(),
        _mock_enabled=False,
    )
    # Bind the real method from SettingsPage
    harness.bind_workspace_sections = SettingsPage.bind_workspace_sections.__get__(
        harness, type(harness)
    )
    return harness


class TestSettingsBindWorkspaceSectionsExists:
    """SettingsPage must expose ``bind_workspace_sections``."""

    def test_method_exists(self) -> None:
        assert hasattr(SettingsPage, "bind_workspace_sections")

    def test_method_is_callable(self) -> None:
        assert callable(getattr(SettingsPage, "bind_workspace_sections", None))


class TestSettingsProvidersSection:
    """``"providers"`` section only updates provider-related cards."""

    def test_providers_section_updates_provider_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"providers"}))

        harness._runtime_cards["provider"].bind.assert_called_once()

    def test_providers_section_updates_loaded_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"providers"}))

        harness._runtime_cards["loaded"].bind.assert_called_once()

    def test_providers_section_skips_storage_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"providers"}))

        harness._runtime_cards["storage"].bind.assert_not_called()

    def test_providers_section_skips_mode_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"providers"}))

        harness._runtime_cards["mode"].bind.assert_not_called()


class TestSettingsOverviewSection:
    """``"overview"`` section only updates overview-related cards."""

    def test_overview_section_updates_storage_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._runtime_cards["storage"].bind.assert_called_once()

    def test_overview_section_updates_mode_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._runtime_cards["mode"].bind.assert_called_once()

    def test_overview_section_skips_provider_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._runtime_cards["provider"].bind.assert_not_called()

    def test_overview_section_skips_loaded_card(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        harness._runtime_cards["loaded"].bind.assert_not_called()


class TestSettingsBothSections:
    """Both sections together update all cards."""

    def test_both_sections_update_all_cards(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"providers", "overview"}))

        harness._runtime_cards["storage"].bind.assert_called_once()
        harness._runtime_cards["provider"].bind.assert_called_once()
        harness._runtime_cards["loaded"].bind.assert_called_once()
        harness._runtime_cards["mode"].bind.assert_called_once()

    def test_empty_sections_update_nothing(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset())

        harness._runtime_cards["storage"].bind.assert_not_called()
        harness._runtime_cards["provider"].bind.assert_not_called()
        harness._runtime_cards["loaded"].bind.assert_not_called()
        harness._runtime_cards["mode"].bind.assert_not_called()


class TestSettingsSnapshotStored:
    """``bind_workspace_sections`` always stores the snapshot."""

    def test_snapshot_stored_on_providers(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"providers"}))

        assert harness._snapshot is snap

    def test_snapshot_stored_on_overview(self) -> None:
        harness = _make_harness()
        snap = _make_snapshot()

        harness.bind_workspace_sections(snap, frozenset({"overview"}))

        assert harness._snapshot is snap
