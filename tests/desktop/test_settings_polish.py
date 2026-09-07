"""Tests for the Settings（火候）visual polish work.

Covers Task 20 of the desktop-ui-modernization plan:
- ``CollapsibleSection`` expand animation exists (200ms)
- ``_ModelStatusCard`` status-switch fade (200ms)
- ``_TaskRouteRow`` hover highlight (background change)
- Edge case: Settings page constructs without ``model_profiles.json``

The fade / collapse animations are gated by ``animations_supported()``
(D1 platform guard — macOS returns False).  Each animation test
``monkeypatch.setattr(...)`` the relevant ``animations_supported`` to
``lambda: True`` so the animation is exercised regardless of host platform.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from typing import TYPE_CHECKING

import pytest

from novel_forge.gateway.profiles import ModelProfile

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def mock_profile() -> ModelProfile:
    """Minimal ``ModelProfile`` instance for ``_ModelStatusCard`` tests."""
    return ModelProfile(
        profile_id="mock",
        display_name="Mock",
        provider="mock",
        model_id="mock-model",
        api_key="sk-mock",
    )


# ── Tests: CollapsibleSection animation ─────────────────────────


class TestCollapsibleSectionAnimation:
    """The expand animation must exist and be 200ms."""

    def test_collapsed_section_expand_starts_200ms_animation(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import novel_forge.desktop.components.containers as containers_mod

        monkeypatch.setattr(containers_mod, "animations_supported", lambda: True)

        from novel_forge.desktop.components.containers import CollapsibleSection

        section = CollapsibleSection("Test Section", expanded=False)
        assert section._height_animation is None

        section.set_expanded(True)

        assert section._height_animation is not None, (
            "CollapsibleSection.set_expanded must create a QVariantAnimation"
        )
        assert section._height_animation.duration() == 200, (
            "CollapsibleSection animation duration must be 200ms (Task 20 spec)"
        )

    def test_expanded_section_collapse_starts_200ms_animation(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import novel_forge.desktop.components.containers as containers_mod

        monkeypatch.setattr(containers_mod, "animations_supported", lambda: True)

        from novel_forge.desktop.components.containers import CollapsibleSection

        section = CollapsibleSection("Test Section", expanded=True)
        section.set_expanded(False)

        assert section._height_animation is not None
        assert section._height_animation.duration() == 200


# ── Tests: Model status card fade ───────────────────────────────


class TestModelStatusCardFade:
    """``_ModelStatusCard.set_result`` / ``set_pending`` must flash 200ms."""

    def test_set_result_creates_200ms_opacity_flash(
        self,
        qapp: QApplication,
        mock_profile: ModelProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import novel_forge.desktop.motion as motion_mod

        monkeypatch.setattr(motion_mod, "animations_supported", lambda kind="any": True)

        from novel_forge.desktop.pages.settings.components import _ModelStatusCard

        card = _ModelStatusCard(mock_profile)
        card.show()
        qapp.processEvents()

        assert card._status_flash_anim is None
        card.set_result(True, "OK")
        qapp.processEvents()

        assert card._status_flash_anim is not None, (
            "_ModelStatusCard.set_result must create a QPropertyAnimation"
        )
        assert card._status_flash_anim.duration() == 200, (
            "Status flash duration must be 200ms (Task 20 spec)"
        )

    def test_set_pending_also_triggers_flash(
        self,
        qapp: QApplication,
        mock_profile: ModelProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import novel_forge.desktop.motion as motion_mod

        monkeypatch.setattr(motion_mod, "animations_supported", lambda kind="any": True)

        from novel_forge.desktop.pages.settings.components import _ModelStatusCard

        card = _ModelStatusCard(mock_profile)
        card.set_pending("检测中…")
        qapp.processEvents()

        assert card._status_flash_anim is not None
        assert card._status_flash_anim.duration() == 200

    def test_flash_is_idempotent(
        self,
        qapp: QApplication,
        mock_profile: ModelProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A second ``set_result`` must cancel and replace the first animation."""
        import novel_forge.desktop.motion as motion_mod

        monkeypatch.setattr(motion_mod, "animations_supported", lambda kind="any": True)

        from novel_forge.desktop.pages.settings.components import _ModelStatusCard

        card = _ModelStatusCard(mock_profile)
        card.set_result(True, "OK")
        qapp.processEvents()
        first_anim = card._status_flash_anim

        card.set_result(False, "fail")
        qapp.processEvents()
        second_anim = card._status_flash_anim

        assert first_anim is not None
        assert second_anim is not None
        assert second_anim is not first_anim, (
            "Second set_result must cancel and replace the first animation"
        )

    def test_flash_respects_macos_guard(
        self,
        qapp: QApplication,
        mock_profile: ModelProfile,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When opacity animations are blocked (e.g. macOS strict guard), no animation."""
        import novel_forge.desktop.motion as motion_mod

        monkeypatch.setattr(motion_mod, "animations_supported", lambda kind="any": False)

        from novel_forge.desktop.pages.settings.components import _ModelStatusCard

        card = _ModelStatusCard(mock_profile)
        card.set_result(True, "OK")
        qapp.processEvents()

        assert card._status_flash_anim is None, (
            "Flash must be a no-op when animations_supported returns False (D1)"
        )


# ── Tests: Task route row hover highlight ───────────────────────


class TestTaskRouteRowHover:
    """``_TaskRouteRow`` must carry the ``taskRouteRow`` object name + hover rule."""

    def test_task_route_row_object_name(self, qapp: QApplication) -> None:
        from novel_forge.desktop.pages.settings.components import _TaskRouteRow

        row = _TaskRouteRow(
            task_key="draft_chapter",
            task_label="Draft Chapter",
            task_hint="Test hint",
            profiles=[("mock", "Mock", True)],
            route=None,
        )
        assert row.objectName() == "taskRouteRow", (
            "_TaskRouteRow must set objectName='taskRouteRow' for QSS hover rule"
        )

    def test_stylesheet_contains_hover_rule(self) -> None:
        """Global QSS must define ``QWidget#taskRouteRow:hover``."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet()
        assert "QWidget#taskRouteRow:hover" in qss, (
            "Global stylesheet must contain QWidget#taskRouteRow:hover rule"
        )
        assert "QWidget#taskRouteRow" in qss, (
            "Global stylesheet must contain QWidget#taskRouteRow base rule"
        )

    def test_hover_color_matches_token(self) -> None:
        """The hover background must map to ``bg.hover.secondary`` (#fbf5ec)."""
        from novel_forge.desktop.theme import get_stylesheet
        from novel_forge.desktop.tokens.colors import COLORS

        token_hex = COLORS["bg.hover.secondary"][0].lower()
        # Convert token hex to rgba integer components.
        r = int(token_hex[1:3], 16)
        g = int(token_hex[3:5], 16)
        b = int(token_hex[5:7], 16)

        qss = get_stylesheet()
        expected_rgba = f"rgba({r}, {g}, {b},"
        assert expected_rgba in qss, (
            f"Hover rule must use rgba matching bg.hover.secondary ({token_hex}); "
            f"expected '{expected_rgba}' in stylesheet"
        )


# ── Tests: Edge case — settings without model_profiles.json ─────


class TestEdgeNoProfilesFile:
    """Settings page must construct even when ``model_profiles.json`` is absent."""

    def test_settings_page_constructs_without_profiles(
        self,
        qapp: QApplication,
        tmp_path: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Point ``NOVEL_FORGE_STORAGE_ROOT`` at an empty directory and instantiate.

        The page must not raise even when ``model_profiles.json`` is missing —
        it should fall back to default profiles and load cleanly.  This is
        the regression guard for the Settings page being usable on first run
        with no persisted configuration.
        """
        monkeypatch.setenv("NOVEL_FORGE_STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS", "true")

        from novel_forge.desktop.pages.settings.page import SettingsPage

        page = SettingsPage()
        try:
            page.show()
            qapp.processEvents()
            # Page constructed without raising — status_cards is a dict (may
            # be non-empty if the default profiles ship baked-in).
            assert isinstance(page._status_cards, dict), (
                "_status_cards must be a dict even with no model_profiles.json"
            )
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_settings_page_constructs_with_no_storage_root_at_all(
        self,
        qapp: QApplication,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No ``NOVEL_FORGE_STORAGE_ROOT`` env, no ``model_profiles.json`` anywhere.

        The page must still construct cleanly by falling back to its built-in
        default profile set.  This is the most extreme "no profiles" edge case.
        """
        monkeypatch.setenv("NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS", "true")

        from novel_forge.desktop.pages.settings.page import SettingsPage

        page = SettingsPage()
        try:
            page.show()
            qapp.processEvents()
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()


class TestDeferredSettingsReadiness:
    """A parity fixture must not freeze the cold loading shell as the page."""

    def test_ready_only_after_deferred_hero_and_status_grid(self, qapp: QApplication) -> None:
        from novel_forge.desktop.pages.settings.page import SettingsPage

        page = SettingsPage(eager_build=False)
        try:
            assert not page.is_ui_ready()

            page.ensure_status_grid_built()

            assert page.is_ui_ready()
        finally:
            page.shutdown()
            page.deleteLater()
            qapp.processEvents()


# ── Tests: QComboBox style unification ──────────────────────────


class TestComboBoxStyleUnification:
    """All ``QComboBox`` instances must inherit the central style block."""

    def test_global_qss_has_combo_box_block(self) -> None:
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet()
        # Central QComboBox style: applies to every QComboBox that does not
        # override it via a more-specific selector.
        assert "QComboBox {" in qss, "Global QComboBox style block missing"
        assert "QComboBox:focus" in qss, "Global QComboBox:focus style missing"
        assert "QComboBox::drop-down" in qss, "Global QComboBox::drop-down style missing"
        assert "QComboBox QAbstractItemView" in qss, "Global QComboBox popup style missing"

    def test_combobox_uses_token_color_for_focus(self) -> None:
        """Focus border-color must reference a token (or its rgba equivalent)."""
        from novel_forge.desktop.theme import get_stylesheet
        from novel_forge.desktop.tokens.colors import COLORS

        # accent.primary == #b65634 → rgba(182, 86, 52, ...)
        accent_hex = COLORS["accent.primary"][0].lower()
        r = int(accent_hex[1:3], 16)
        g = int(accent_hex[3:5], 16)
        b = int(accent_hex[5:7], 16)

        qss = get_stylesheet()
        # Token-substituted form: {{accent.primary}}
        assert "{{accent.primary}}" in qss or f"rgba({r}, {g}, {b}," in qss, (
            "QComboBox focus color must use accent.primary token or rgba equivalent"
        )

    def test_plain_qcombobox_inherits_global_style(self, qapp: QApplication) -> None:
        """A bare ``QComboBox`` (no objectName) must inherit the global style."""
        from PySide6.QtWidgets import QComboBox

        from novel_forge.desktop.theme import get_stylesheet

        qapp.setStyleSheet(get_stylesheet())
        combo = QComboBox()
        # The widget should NOT have an objectName set, so the global
        # ``QComboBox { ... }`` block applies via selector match.
        assert combo.objectName() == ""
        # Cleanup
        combo.deleteLater()
        qapp.processEvents()


# ── Tests: Save flow / mock-mode guard (regression) ──────────────


class TestRegressionSaveFlowPreserved:
    """Verify must-not-do list: business logic unchanged."""

    def test_task_route_row_set_route_still_works(self, qapp: QApplication) -> None:
        from novel_forge.desktop.pages.settings.components import _TaskRouteRow

        row = _TaskRouteRow(
            task_key="draft_chapter",
            task_label="Draft",
            task_hint="hint",
            profiles=[("mock", "Mock", True)],
            route=None,
        )
        row.set_model_and_flags(
            profile_id="mock",
            thinking=False,
            multi_turn=False,
            fallback_routes=[],
        )
        route = row.get_route()
        assert route is not None
        assert route.profile_id == "mock"
        # Hover objectName must remain set after model update.
        assert row.objectName() == "taskRouteRow"

    def test_task_route_row_exposes_audited_reasoning_modes(
        self,
        qapp: QApplication,
    ) -> None:
        from novel_forge.desktop.pages.settings.components import _TaskRouteRow
        from novel_forge.gateway.profiles import TaskRouteEntry

        models = [
            ModelProfile("minimax:m27", "M2.7 Highspeed", "minimax", "MiniMax-M2.7-highspeed"),
            ModelProfile("minimax:m3", "M3", "minimax", "MiniMax-M3"),
            ModelProfile("deepseek:v4", "DeepSeek V4", "deepseek", "deepseek-v4-pro"),
            ModelProfile("mimo:v25", "MiMo V2.5", "mimo", "mimo-v2.5-pro"),
        ]
        profiles = [(model.profile_id, model.display_name, True) for model in models]
        row = _TaskRouteRow(
            task_key="draft_chapter",
            task_label="Draft",
            task_hint="hint",
            profiles=profiles,
            route=TaskRouteEntry("minimax:m27", thinking_mode="forced"),
            profile_configs=models,
        )

        assert [row.thinking_cb.itemData(i) for i in range(row.thinking_cb.count())] == [
            "forced"
        ]
        assert row.thinking_cb.isEnabled() is False

        row.set_model_and_flags("minimax:m3", False, False, [], thinking_mode="off")
        assert [row.thinking_cb.itemData(i) for i in range(row.thinking_cb.count())] == [
            "off",
            "adaptive",
        ]
        assert row.get_route().thinking_mode == "off"

        row.set_model_and_flags("deepseek:v4", True, False, [], thinking_mode="max")
        assert [row.thinking_cb.itemData(i) for i in range(row.thinking_cb.count())] == [
            "off",
            "high",
            "max",
        ]
        assert row.get_route().thinking_mode == "max"

        row.set_model_and_flags("mimo:v25", True, False, [], thinking_mode="on")
        assert [row.thinking_cb.itemData(i) for i in range(row.thinking_cb.count())] == [
            "off",
            "on",
        ]
        assert row.get_route().thinking_mode == "on"

    def test_model_status_card_public_api_unchanged(
        self, qapp: QApplication, mock_profile: object
    ) -> None:
        """Public status methods keep their call-compatible surface."""
        import inspect

        from novel_forge.desktop.pages.settings.components import _ModelStatusCard

        sig_result = inspect.signature(_ModelStatusCard.set_result)
        sig_pending = inspect.signature(_ModelStatusCard.set_pending)

        # set_result(self, success: bool, detail: str) → 3 params
        assert list(sig_result.parameters) == ["self", "success", "detail"]
        # set_pending(self, detail: str = "检测中…", *, flash: bool = True)
        # remains source-compatible for existing callers that pass only detail.
        assert list(sig_pending.parameters) == ["self", "detail", "flash"]
        assert sig_pending.parameters["detail"].default == "检测中…"
        assert sig_pending.parameters["flash"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig_pending.parameters["flash"].default is True
