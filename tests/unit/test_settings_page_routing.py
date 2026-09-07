"""Tests for settings-page task routing coverage and sanitization."""

# ruff: noqa: I001

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QTabWidget, QToolButton

import novel_forge.desktop.pages.settings.page as settings_page
import novel_forge.desktop.pages.settings.ollama as settings_page_ollama
import novel_forge.desktop.pages.settings.save as settings_page_save
from novel_forge.desktop.task_flow_errors import TaskFlowErrorArchive
from novel_forge.core.config import Settings
from novel_forge.core.task_catalog import ROUTING_GROUPS
from novel_forge.desktop.pages.settings.components import (
    _ConnectionTestWorker,
    _ModelDialog,
    _TaskRouteRow,
)
from novel_forge.app_service.engine_views import (
    OllamaCapabilitiesView,
    OllamaConfiguredRolesView,
    OllamaManagerView,
    OllamaModelView,
    OllamaRuntimeStatusView,
    OllamaSidecarView,
    OllamaStorageView,
)
from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.adapters.mimo import MiMoAdapter
from novel_forge.gateway.adapters.tencent_hunyuan import TencentHunyuanAdapter
from novel_forge.gateway.profiles import (
    TONGYI_TOKEN_PLAN_BASE_URL,
    ModelProfile,
    ProfilesConfig,
    TaskRouteEntry,
)
from novel_forge.gateway.profiles import profile_api_key_env_var


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _cleanup_widget(widget: object) -> None:
    shutdown = getattr(widget, "shutdown", None)
    if callable(shutdown):
        shutdown()
    close = getattr(widget, "close", None)
    if callable(close):
        close()
    set_parent = getattr(widget, "setParent", None)
    if callable(set_parent):
        set_parent(None)
    delete_later = getattr(widget, "deleteLater", None)
    if callable(delete_later):
        delete_later()


def _stub_config() -> ProfilesConfig:
    return ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="tongyi:qwen-max",
                display_name="Qwen-max",
                provider="tongyi",
                model_id="qwen-max",
                api_key="sk-test",
            )
        ],
        default_profile_id="tongyi:qwen-max",
    )


def test_settings_page_save_reloads_settings_cache_before_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed_settings = object()
    order: list[str] = []

    class _Store:
        def save(self, **_kwargs: object) -> SimpleNamespace:
            order.append("save")
            return SimpleNamespace(
                profiles_path=Path("model_profiles.json"),
                env_path=Path(".env"),
            )

        def reload(self) -> None:
            order.append("reload")

    class _Signal:
        def emit(self) -> None:
            order.append("emit")

    monkeypatch.setattr(settings_page_save, "get_settings", lambda: refreshed_settings)

    page = SimpleNamespace(
        _route_rows={},
        _group_bulk_rows={},
        _config=ProfilesConfig(),
        _param_widgets={"_short_temp_spins": {}, "_long_temp_spins": {}},
        _settings=object(),
        _store=_Store(),
        settings_saved=_Signal(),
        _last_saved_signature="",
    )

    assert settings_page_save.save_settings(page, silent=True) is True

    assert order == ["save", "reload", "emit"]
    assert page._settings is refreshed_settings


def test_collect_param_env_pairs_includes_desktop_theme() -> None:
    class _ThemeCombo:
        def currentData(self) -> str:
            return "ink_jade"

    class _PetCombo:
        def currentData(self) -> str:
            return "false"

    page = SimpleNamespace(
        _param_widgets={
            "_short_temp_spins": {},
            "_long_temp_spins": {},
            "_desktop_theme": _ThemeCombo(),
            "_desktop_pet_visible": _PetCombo(),
        }
    )

    pairs = settings_page_save.collect_param_env_pairs(page)

    assert pairs["NOVEL_FORGE_DESKTOP_THEME"] == "ink_jade"
    assert pairs["NOVEL_FORGE_DESKTOP_PET_VISIBLE"] == "false"


def test_settings_page_visible_save_prepares_lazy_sections_across_event_turns(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())
    saved: list[bool] = []
    monkeypatch.setattr(
        settings_page,
        "save_settings",
        lambda _page, *, silent: saved.append(silent) or True,
    )

    class _DeferredSection:
        def __init__(self, title: str) -> None:
            self._title_text = title
            self.body_built = False

        def property(self, name: str) -> bool:
            return name == "saveFieldsRequired"

        def ensure_body_built(self) -> None:
            self.body_built = True

    page = settings_page.SettingsPage(eager_build=False)
    sections = [_DeferredSection("一"), _DeferredSection("二")]
    page._deferred_sections = sections  # type: ignore[assignment]

    assert page.save_with_feedback() is True
    assert saved == []
    assert page._save_in_progress is True
    page._ensure_save_prepare_timer().stop()

    page._prepare_next_save_section()
    page._ensure_save_prepare_timer().stop()
    assert [section.body_built for section in sections] == [True, False]
    assert saved == []

    page._prepare_next_save_section()
    page._ensure_save_prepare_timer().stop()
    assert [section.body_built for section in sections] == [True, True]
    assert saved == []

    page._prepare_next_save_section()
    assert saved == [False]
    assert page._save_in_progress is False

    _cleanup_widget(page)


def test_settings_page_theme_preview_applies_only_latest_debounced_choice(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())
    applied: list[str] = []
    monkeypatch.setattr(
        settings_page,
        "apply_desktop_theme",
        lambda theme_id, **_kwargs: applied.append(theme_id),
    )

    class _ThemeCombo:
        theme_id = "ink_jade"

        def currentData(self) -> str:
            return self.theme_id

        def currentText(self) -> str:
            return self.theme_id

    page = settings_page.SettingsPage(eager_build=False)
    combo = _ThemeCombo()
    page._param_widgets["_desktop_theme"] = combo

    page._preview_desktop_theme()
    combo.theme_id = "crimson_gold"
    page._preview_desktop_theme()
    page._ensure_theme_preview_timer().stop()
    assert applied == []

    page._apply_pending_desktop_theme()
    assert applied == ["crimson_gold"]

    _cleanup_widget(page)


def test_settings_page_exposes_research_route_params(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(
        settings_page,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            research_enabled=True,
            research_default_provider="searxng",
            research_http_endpoint="http://localhost:8080/search",
            research_max_queries=4,
            research_results_per_query=6,
            research_max_results=9,
            research_retry_attempts=2,
            research_include_domains="gov.cn,edu.cn",
            research_exclude_domains="spam.test",
            research_locale="zh-CN",
            research_search_depth="advanced",
            research_use_llm_planning=True,
            research_model_prior_enabled=False,
            research_dossier_enabled=True,
            research_dossier_max_sources=11,
            outline_research_grounding_enabled=True,
            outline_research_grounding_notes_per_chapter=4,
        ),
    )
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage(eager_build=True)
    try:
        widgets = page._param_widgets
        expected = {
            "_research_enabled",
            "_research_default_provider",
            "_research_http_endpoint",
            "_research_api_key",
            "_research_timeout_s",
            "_research_max_queries",
            "_research_results_per_query",
            "_research_max_results",
            "_research_retry_attempts",
            "_research_include_domains",
            "_research_exclude_domains",
            "_research_locale",
            "_research_search_depth",
            "_research_use_llm_planning",
            "_research_model_prior_enabled",
            "_research_dossier_enabled",
            "_research_dossier_max_sources",
            "_outline_research_grounding_enabled",
            "_outline_research_grounding_notes_per_chapter",
            "_research_preset_combo",
            "_research_apply_preset_btn",
            "_research_test_btn",
            "_research_test_status",
            "_research_mcp_args_json",
            "_research_mcp_env_json",
            "_research_mcp_api_key_env",
            "_research_mcp_tool_name",
            "_research_mcp_query_argument",
            "_research_mcp_tool_arguments_json",
        }
        assert expected <= set(widgets)

        assert widgets["_research_enabled"].isChecked() is True
        assert widgets["_research_default_provider"].currentData() == "searxng"
        assert widgets["_research_max_queries"].value() == 4
        assert widgets["_research_use_llm_planning"].isChecked() is True
        assert widgets["_research_model_prior_enabled"].isChecked() is False
        assert widgets["_research_dossier_max_sources"].value() == 11
        assert widgets["_outline_research_grounding_notes_per_chapter"].value() == 4
        api_key_input = widgets["_research_api_key"]
        assert api_key_input.echoMode() == QLineEdit.EchoMode.Password
        secret_toggle = api_key_input.parentWidget().findChild(QToolButton, "secretToggleButton")
        assert secret_toggle is not None
        assert secret_toggle.text() == "显示"
        secret_toggle.click()
        assert api_key_input.echoMode() == QLineEdit.EchoMode.Normal
        assert secret_toggle.text() == "隐藏"
        secret_toggle.click()
        assert api_key_input.echoMode() == QLineEdit.EchoMode.Password
        assert secret_toggle.text() == "显示"
        research_descriptions = [
            f"{label.text()} {label.toolTip()}"
            for label in page.findChildren(QLabel, "panelDescription")
        ]
        assert any("启用开关" in text and "skipped 报告" in text for text in research_descriptions)
        assert any(
            "queries 只是查询计划" in text and "sources 非空" in text
            for text in research_descriptions
        )

        widgets["_research_preset_combo"].setCurrentIndex(
            widgets["_research_preset_combo"].findData("minimax_cn")
        )
        page._apply_research_preset()
        assert widgets["_research_default_provider"].currentData() == "mcp_search"
        assert widgets["_research_mcp_command"].text() == "uvx"
        assert widgets["_research_mcp_args_json"].text() == '["minimax-coding-plan-mcp"]'
        assert (
            widgets["_research_mcp_env_json"].text()
            == '{"MINIMAX_API_HOST":"https://api.minimax.chat"}'
        )
        assert widgets["_research_mcp_api_key_env"].text() == "MINIMAX_API_KEY"
        assert widgets["_research_mcp_tool_name"].text() == "web_search"
        assert "已应用预设" in widgets["_research_test_status"].text()

        widgets["_research_default_provider"].setCurrentIndex(
            widgets["_research_default_provider"].findData("brave")
        )
        widgets["_research_enabled"].setChecked(False)
        widgets["_research_api_key"].setText("sk-test")
        widgets["_research_retry_attempts"].setValue(0)
        widgets["_research_use_llm_planning"].setChecked(False)
        widgets["_research_model_prior_enabled"].setChecked(True)
        widgets["_research_dossier_enabled"].setChecked(False)
        widgets["_outline_research_grounding_notes_per_chapter"].setValue(2)
        env_pairs = page._collect_param_env_pairs()

        assert env_pairs["NOVEL_FORGE_RESEARCH_ENABLED"] == "false"
        assert env_pairs["NOVEL_FORGE_RESEARCH_DEFAULT_PROVIDER"] == "brave"
        assert env_pairs["NOVEL_FORGE_RESEARCH_API_KEY"] == "sk-test"
        assert env_pairs["NOVEL_FORGE_RESEARCH_MAX_QUERIES"] == "4"
        assert env_pairs["NOVEL_FORGE_RESEARCH_RESULTS_PER_QUERY"] == "6"
        assert env_pairs["NOVEL_FORGE_RESEARCH_MAX_RESULTS"] == "9"
        assert env_pairs["NOVEL_FORGE_RESEARCH_RETRY_ATTEMPTS"] == "0"
        assert env_pairs["NOVEL_FORGE_RESEARCH_INCLUDE_DOMAINS"] == "gov.cn,edu.cn"
        assert env_pairs["NOVEL_FORGE_RESEARCH_EXCLUDE_DOMAINS"] == "spam.test"
        assert env_pairs["NOVEL_FORGE_RESEARCH_LOCALE"] == "zh-CN"
        assert env_pairs["NOVEL_FORGE_RESEARCH_SEARCH_DEPTH"] == "advanced"
        assert env_pairs["NOVEL_FORGE_RESEARCH_USE_LLM_PLANNING"] == "false"
        assert env_pairs["NOVEL_FORGE_RESEARCH_MODEL_PRIOR_ENABLED"] == "true"
        assert env_pairs["NOVEL_FORGE_RESEARCH_DOSSIER_ENABLED"] == "false"
        assert env_pairs["NOVEL_FORGE_RESEARCH_DOSSIER_MAX_SOURCES"] == "11"
        assert env_pairs["NOVEL_FORGE_OUTLINE_RESEARCH_GROUNDING_ENABLED"] == "true"
        assert env_pairs["NOVEL_FORGE_OUTLINE_RESEARCH_GROUNDING_NOTES_PER_CHAPTER"] == "2"
        assert env_pairs["NOVEL_FORGE_RESEARCH_MCP_ARGS_JSON"] == '["minimax-coding-plan-mcp"]'
        assert (
            env_pairs["NOVEL_FORGE_RESEARCH_MCP_ENV_JSON"]
            == '{"MINIMAX_API_HOST":"https://api.minimax.chat"}'
        )
        assert env_pairs["NOVEL_FORGE_RESEARCH_MCP_API_KEY_ENV"] == "MINIMAX_API_KEY"
        assert env_pairs["NOVEL_FORGE_RESEARCH_MCP_TOOL_NAME"] == "web_search"
    finally:
        _cleanup_widget(page)


def test_source_artifact_repair_settings_defaults_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOVEL_FORGE_INIT_SOURCE_ARTIFACT_AUTO_REPAIR", raising=False)
    monkeypatch.delenv("NOVEL_FORGE_INIT_SOURCE_ARTIFACT_REPAIR_ROUNDS", raising=False)

    settings = Settings(_env_file=None)
    assert settings.init_source_artifact_auto_repair is True
    assert settings.init_source_artifact_repair_rounds == 2

    monkeypatch.setenv("NOVEL_FORGE_INIT_SOURCE_ARTIFACT_AUTO_REPAIR", "false")
    monkeypatch.setenv("NOVEL_FORGE_INIT_SOURCE_ARTIFACT_REPAIR_ROUNDS", "3")

    overridden = Settings(_env_file=None)
    assert overridden.init_source_artifact_auto_repair is False
    assert overridden.init_source_artifact_repair_rounds == 3


def test_settings_page_lazy_build_defers_route_rows(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage(eager_build=False)

    assert page.findChild(QTabWidget, "routingGroupTabs") is None
    assert page._route_rows == {}

    page.ensure_all_deferred_sections_built()

    group_tabs = page.findChild(QTabWidget, "routingGroupTabs")
    assert group_tabs is not None
    assert [group_tabs.tabText(index) for index in range(group_tabs.count())] == [
        group.name for group in ROUTING_GROUPS
    ]
    assert set(page._route_rows) >= {"spec_enrich", "beats", "draft"}
    assert page._collect_param_env_pairs()["NOVEL_FORGE_SHORT_MAX_EDIT_ROUNDS"] != "0"

    _cleanup_widget(page)


def test_settings_incremental_build_leaves_collapsed_sections_lazy(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage(eager_build=False)
    page.show()
    page.schedule_deferred_build()

    while page._deferred_build_timer.isActive():
        page._deferred_build_timer.stop()
        page._build_next_deferred_section()

    collapsed = [section for section in page._deferred_sections if not section.is_expanded]
    assert collapsed
    assert all(not section.body_built for section in collapsed)
    assert page._deferred_build_complete is True

    page.shutdown()
    _cleanup_widget(page)


def test_settings_lazy_materialization_does_not_mark_page_unsaved(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage(eager_build=False)
    page.show()
    page.schedule_deferred_build()

    while page._deferred_build_timer.isActive():
        page._deferred_build_timer.stop()
        page._build_next_deferred_section()

    assert any(not section.body_built for section in page._deferred_sections)
    assert page.has_unsaved_changes() is False
    assert page._last_saved_signature == settings_page._build_signature(page)

    page.shutdown()
    _cleanup_widget(page)


def test_settings_lazy_materialization_preserves_real_parameter_edits(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage(eager_build=False)
    theme_section = next(
        section
        for section in page._deferred_sections
        if getattr(section, "_title_text", "") == "界面主题 — 配色与风格"
    )
    theme_section.set_expanded(True)
    theme_combo = page._param_widgets["_desktop_theme"]

    assert page._last_saved_signature == settings_page._build_signature(page)
    assert theme_combo.count() > 1
    theme_combo.setCurrentIndex((theme_combo.currentIndex() + 1) % theme_combo.count())
    assert page.has_unsaved_changes() is True

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_lazy_build_defers_status_cards(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-lazy-{index}",
            display_name=f"Qwen Lazy {index}",
            provider="tongyi",
            model_id=f"qwen-lazy-{index}",
            api_key="sk-test",
        )
        for index in range(6)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage(eager_build=False)

    assert page._status_cards == {}
    assert page._status_grid_build_complete is False
    assert getattr(page._status_grid_build_timer, "isActive", lambda: False)() is False

    page.activate()

    assert page._ensure_status_grid_build_timer().isActive() is True

    page.ensure_status_grid_built()

    assert set(page._status_cards) == {profile.profile_id for profile in profiles}
    assert page._status_grid_build_complete is True

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_status_grid_completion_starts_pending_tests(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-deferred-{index}",
            display_name=f"Qwen Deferred {index}",
            provider="tongyi",
            model_id=f"qwen-deferred-{index}",
            api_key="sk-test",
        )
        for index in range(3)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage(eager_build=False)
    page.activate()

    assert started == []

    page.ensure_status_grid_built()

    assert started == [profiles[0].profile_id]
    assert page._status_tests_active == 1

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_save_after_lazy_build_skips_hidden_route_tabs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    save_seen: list[tuple[bool, int]] = []

    def _save(page: settings_page.SettingsPage, *, silent: bool = False) -> bool:
        save_seen.append((page._deferred_build_complete, len(page._route_rows)))
        return True

    monkeypatch.setattr(settings_page, "save_settings", _save)

    page = settings_page.SettingsPage(eager_build=False)

    assert page._route_rows == {}

    assert page.save_pending_changes() is True

    assert save_seen
    assert save_seen[-1][0] is False
    assert save_seen[-1][1] == 0
    assert page._deferred_build_complete is False
    assert page._route_rows == {}
    assert page._deferred_sections[1].body_built is False

    _cleanup_widget(page)


def test_settings_page_shutdown_clears_deferred_lazy_queues(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage(eager_build=False)
    routing_section = page._deferred_sections[1]
    routing_section.ensure_body_built()

    assert page._deferred_sections
    assert page._lazy_route_groups

    page.shutdown()

    assert page._deferred_sections == []
    assert page._lazy_route_groups == {}
    assert page._lazy_route_subgroups == {}
    assert page._deferred_build_complete is True
    assert not page._deferred_build_timer.isActive()

    _cleanup_widget(page)


def test_settings_page_shutdown_cancels_and_releases_all_workers(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    class _Signal:
        def disconnect(self, *_args: object) -> None:
            return None

    class _Worker:
        def __init__(self) -> None:
            signal = _Signal()
            self.signals = SimpleNamespace(
                probe_started=signal,
                finished=signal,
                listed=signal,
                pull_progress=signal,
                pull_finished=signal,
                delete_finished=signal,
            )
            self.cancelled = False

        def request_cancel(self) -> None:
            self.cancelled = True

    page = settings_page.SettingsPage(eager_build=False)
    workers = [_Worker(), _Worker(), _Worker()]
    page._test_workers = [workers[0]]
    page._ollama_workers = [workers[1]]
    page._research_test_workers = [workers[2]]

    page.shutdown()

    assert all(worker.cancelled for worker in workers)
    assert page._test_workers == []
    assert page._ollama_workers == []
    assert page._research_test_workers == []
    _cleanup_widget(page)


def test_settings_page_exposes_task_flow_error_archive_controls(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "demo" / "states" / "task_flow_errors"
    TaskFlowErrorArchive(tmp_path).append_entries(
        "demo",
        [
            {
                "id": "entry-a",
                "time": "2026-05-25T15:48:37+00:00",
                "project_id": "demo",
                "kind": "格式错误",
                "task": "plan_chapter_contracts",
                "error": "Local JSON repair lost structural content",
            }
        ],
    )

    monkeypatch.setattr(
        settings_page,
        "get_settings",
        lambda: Settings(_env_file=None, storage_root=tmp_path),
    )
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())
    monkeypatch.setattr(settings_page, "ask_confirmation", lambda *args, **kwargs: True)
    monkeypatch.setattr(settings_page, "show_info_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_page, "show_warning_message", lambda *args, **kwargs: None)

    page = settings_page.SettingsPage()

    assert page._error_archive_summary_label is not None
    assert "已保留 1 条错误档案" in page._error_archive_summary_label.text()

    page._clear_task_flow_error_archive()

    assert not archive_path.exists()
    assert "暂无本地任务错误档案" in page._error_archive_summary_label.text()
    _cleanup_widget(page)


def test_settings_page_exposes_all_runtime_chapter_tasks(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = _stub_config()
    config.routes = {
        "plan_outline": TaskRouteEntry(profile_id="tongyi:qwen-max"),
        "plan_outline_batch": TaskRouteEntry(profile_id="tongyi:qwen-max"),
        "plan_outline_continue": TaskRouteEntry(profile_id="tongyi:qwen-max"),
        "bridge_chapter": TaskRouteEntry(profile_id="tongyi:qwen-max"),
    }

    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()

    assert {
        "bridge_chapter",
        "check_chapter",
        "check_continuity",
        "repair_continuity",
        "element_progress_arbiter",
        "profile_structure",
        "init_story_core_premise",
        "init_character_profile_batch",
        "derive_editorial_structure",
        "refine_init_artifacts_from_synopsis",
        "plan_outline_continue",
        "plan_chapter_contracts",
        "extract_blueprint_holistic_claims",
        "adjudicate_contract_coherence",
    } <= set(page._route_rows)
    assert "plan_outline_batch" in page._config.routes
    assert "plan_outline_continue" in page._config.routes
    assert "plan_outline" in page._config.routes
    assert "bridge_chapter" in page._config.routes
    assert "长篇初始化" in page._group_bulk_rows
    assert "长篇章节创作" in page._group_bulk_rows

    _cleanup_widget(page)


def test_settings_page_keeps_single_column_settings_flow(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()

    assert not hasattr(page, "_settings_directory_buttons")
    assert not hasattr(page, "_settings_section_anchors")
    assert "_creative_temp_jitter_scope" in page._param_widgets

    section_titles = {button.text() for button in page.findChildren(QPushButton, "collapseToggle")}
    assert {
        "创作火候 — 浮动与适用范围",
        "长篇生成 — 初始化与蓝图",
        "长篇生成 — 章节写作与节拍",
        "长篇状态 — 上下文、投喂与归档",
        "质量、门控与自动修复",
        "追读力 — 评估修复与下章提示",
        "记忆模块 — 语义检索与质量增强",
        "Ollama 本地模型 — 免费、隐私保护",
    } <= section_titles

    subgroup_titles = {label.text() for label in page.findChildren(QLabel, "settingSubgroupTitle")}
    assert {
        "基础浮动",
        "章节节拍",
        "评估修复",
        "格式修复",
    } <= subgroup_titles

    nested_buttons = {
        button.text(): button
        for button in page.findChildren(QPushButton, "collapseToggle")
        if button.property("nested") == "true"
    }
    expected_nested_titles = {
        "高级：自定义浮动任务",
        "大纲批次与密度",
        "初始化执行并发",
        "大纲能力开关",
        "章节契约预算",
        "章节契约生成与裁判",
        "初始化一致性 Claims",
        "初始化一致性裁判与修复",
        "初始化准入与增强",
        "分卷与风格",
        "初始化叙事协议",
        "状态档案",
        "Canon 注入",
        "上下文压缩",
        "实体引用与桥接检索",
        "边界与事实预算",
        "Prompt 诊断",
        "叙事状态",
        "剧情控制",
        "Canon 抽取输入",
        "Canon 抽取输出",
        "语义压缩",
        "质量阈值与裁判策略",
        "归档门控",
        "长篇可靠性增强",
        "宏观护栏",
        "全书审计",
        "全书修复",
        "连续性与开场修复",
        "因果修复",
        "修复流程与本地检查",
        "元素进度裁判",
        "情节记忆",
        "摘要层级",
        "压缩与索引",
        "母题基础",
        "母题提示与重复治理",
        "母题遗忘与维护",
        "Critic 评审",
    }
    assert expected_nested_titles <= set(nested_buttons)
    for title in expected_nested_titles - {"高级：自定义浮动任务"}:
        assert not nested_buttons[title].isChecked()
    _cleanup_widget(page)


def test_settings_page_parameter_folds_keep_widget_and_env_contract(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()

    expected_widget_keys = {
        "_outline_batch_size",
        "_chapter_contract_dynamic_budget",
        "_init_coh_use_memory",
        "_init_coh_candidate_max",
        "_init_readiness_required",
        "_init_cont_protocol_enabled",
        "_long_max_profiles",
        "_narrative_evidence_candidates",
        "_draft_prompt_diag_enabled",
        "_narrative_state_enabled",
        "_narrative_state_candidate_evidence_limit",
        "_narrative_state_final_context_max_chars",
        "_extract_output_max_tokens",
        "_compress_enabled",
        "_long_align_threshold",
        "_min_accept_score",
        "_macro_guard_enabled",
        "_book_audit_mode",
        "_book_audit_repair_min_severity",
        "_continuity_repair_threshold",
        "_causal_repair_enabled",
        "_max_auto_repair_attempts",
        "_humanize_patch_confidence_floor",
        "_humanize_paragraph_confidence_floor",
        "_humanize_library_sim_threshold",
        "_element_progress_arbiter_enabled",
        "_memory_episodic_enabled",
        "_memory_vector_store_backend",
        "_memory_zvec_index_type",
        "_memory_summary_enabled",
        "_memory_compression_enabled",
        "_memory_concurrent_indexing",
        "_memory_motif_enabled",
        "_motif_prompt_token_budget",
        "_motif_auto_forget_ephemeral",
        "_memory_critic_enabled",
    }
    assert expected_widget_keys <= set(page._param_widgets)

    env_pairs = page._collect_param_env_pairs()
    expected_env_keys = {
        "NOVEL_FORGE_OUTLINE_BATCH_SIZE",
        "NOVEL_FORGE_CHAPTER_CONTRACT_DYNAMIC_BUDGET_ENABLED",
        "NOVEL_FORGE_INIT_COHERENCE_USE_MEMORY",
        "NOVEL_FORGE_INIT_COHERENCE_CANDIDATE_MAX_PER_BATCH",
        "NOVEL_FORGE_INIT_READINESS_REQUIRED",
        "NOVEL_FORGE_INIT_CONTINUITY_PROTOCOL_ENABLED",
        "NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_CANDIDATE_LIMIT",
        "NOVEL_FORGE_LONG_DRAFT_PROMPT_DIAGNOSTICS_ENABLED",
        "NOVEL_FORGE_NARRATIVE_STATE_ENABLED",
        "NOVEL_FORGE_NARRATIVE_STATE_CANDIDATE_EVIDENCE_LIMIT",
        "NOVEL_FORGE_NARRATIVE_STATE_FINAL_CONTEXT_MAX_CHARS",
        "NOVEL_FORGE_EXTRACT_CANON_OUTPUT_MAX_TOKENS",
        "NOVEL_FORGE_LONG_CONTEXT_COMPRESS_ENABLED",
        "NOVEL_FORGE_LONG_MIN_ACCEPT_SCORE",
        "NOVEL_FORGE_LONG_MACRO_GUARD_ENABLED",
        "NOVEL_FORGE_LONG_BOOK_AUDIT_DEFAULT_MODE",
        "NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_MIN_SEVERITY",
        "NOVEL_FORGE_LONG_CONTINUITY_REPAIR_THRESHOLD",
        "NOVEL_FORGE_LONG_CAUSAL_REPAIR_ENABLED",
        "NOVEL_FORGE_MAX_AUTO_REPAIR_ATTEMPTS",
        "NOVEL_FORGE_HUMANIZE_PATCH_CONFIDENCE_FLOOR",
        "NOVEL_FORGE_HUMANIZE_PARAGRAPH_CONFIDENCE_FLOOR",
        "NOVEL_FORGE_HUMANIZE_LIBRARY_SIM_THRESHOLD",
        "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_ENABLED",
        "NOVEL_FORGE_MEMORY_EPISODIC_ENABLED",
        "NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND",
        "NOVEL_FORGE_MEMORY_ZVEC_INDEX_TYPE",
        "NOVEL_FORGE_MEMORY_MULTI_GRANULARITY_SUMMARY_ENABLED",
        "NOVEL_FORGE_MEMORY_ADAPTIVE_COMPRESSION_ENABLED",
        "NOVEL_FORGE_MEMORY_CONCURRENT_INDEXING",
        "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET",
        "NOVEL_FORGE_MOTIF_AUTO_FORGET_EPHEMERAL_ENABLED",
        "NOVEL_FORGE_MEMORY_CRITIC_AGENT_ENABLED",
    }
    assert expected_env_keys <= set(env_pairs)

    _cleanup_widget(page)


def test_settings_page_persists_combo_data_not_display_labels(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        memory_vector_store_backend="in_memory",
        memory_zvec_index_type="diskann",
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()

    combo = page._param_widgets["_memory_vector_store_backend"]
    assert combo.currentText() == "in_memory（测试/mock）"
    zvec_index_combo = page._param_widgets["_memory_zvec_index_type"]
    assert zvec_index_combo.currentText() == "DiskANN"

    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND"] == "in_memory"
    assert env_pairs["NOVEL_FORGE_MEMORY_ZVEC_INDEX_TYPE"] == "diskann"

    _cleanup_widget(page)


def test_settings_accept_legacy_desktop_combo_display_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVEL_FORGE_MEMORY_VECTOR_STORE_BACKEND", "zvec（正式）")
    settings = Settings(
        _env_file=None,
        long_book_audit_default_mode="全文深审（推荐）",
        long_book_audit_location_strictness="平衡（推荐）",
        long_word_count_archive_gate_enabled="关闭",
        long_wave_word_count_policy="继承归档字数闸门（推荐）",
        repair_always_reaudit="开启（始终重审核，确认问题是否真正解决）",
        pronoun_autofix_mode="pov_only（仅修复 POV 代词错误）",
    )

    assert settings.memory_vector_store_backend == "zvec"
    assert settings.long_book_audit_default_mode == "full_text"
    assert settings.long_book_audit_location_strictness == "balanced"
    assert settings.long_word_count_archive_gate_enabled is False
    assert settings.long_wave_word_count_policy == "inherit"
    assert settings.repair_always_reaudit is True
    assert settings.pronoun_autofix_mode == "pov_only"


def test_settings_page_collects_hidden_step_temperature_pairs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    assert page._route_rows["draft_chapter"].temperature_spin is not None
    assert (
        page._route_rows["draft_chapter"].temperature_spin
        is page._param_widgets["_long_temp_spins"]["draft_chapter"]
    )
    assert page._route_rows["wave_chapter"].temperature_spin is not None
    assert (
        page._route_rows["wave_chapter"].temperature_spin
        is page._param_widgets["_long_temp_spins"]["wave_chapter"]
    )
    page._route_rows["draft_chapter"].temperature_spin.setValue(1.23)
    page._route_rows["wave_chapter"].temperature_spin.setValue(0.67)

    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_TEMP_DRAFT_CHAPTER"] == "1.23"
    assert env_pairs["NOVEL_FORGE_TEMP_WAVE_CHAPTER"] == "0.67"
    assert "NOVEL_FORGE_TEMP_PLAN_OUTLINE_BATCH" in env_pairs
    assert "NOVEL_FORGE_TEMP_INIT_ENTITY_REGISTRY" in env_pairs
    assert "NOVEL_FORGE_TEMP_PLAN_CHAPTER_CONTRACTS" in env_pairs
    assert "NOVEL_FORGE_TEMP_REFINE_INIT_COHERENCE_PROFILE" in env_pairs
    assert "NOVEL_FORGE_TEMP_REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS" in env_pairs
    assert "NOVEL_FORGE_TEMP_EXTRACT_INIT_COHERENCE_CLAIMS" in env_pairs
    assert "NOVEL_FORGE_TEMP_EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS" in env_pairs
    assert "NOVEL_FORGE_TEMP_ADJUDICATE_INIT_CONFLICT_CANDIDATES" in env_pairs
    assert "NOVEL_FORGE_TEMP_ADJUDICATE_CONTRACT_COHERENCE" in env_pairs
    assert "NOVEL_FORGE_TEMP_REPAIR_INIT_ARTIFACT_PATCH" in env_pairs
    assert "NOVEL_FORGE_TEMP_POLISH_OUTLINE" in env_pairs
    assert "NOVEL_FORGE_TEMP_CHECK_CHAPTER" in env_pairs
    assert "NOVEL_FORGE_TEMP_KNOWLEDGE_BOUNDARY_AUDIT" in env_pairs
    assert "NOVEL_FORGE_TEMP_CHECK_CONTINUITY" in env_pairs
    assert "NOVEL_FORGE_TEMP_VALIDATE_CAUSAL" in env_pairs
    assert "NOVEL_FORGE_TEMP_REPAIR_CONTINUITY" in env_pairs
    assert "NOVEL_FORGE_TEMP_REPAIR_READING_POWER" in env_pairs
    assert "NOVEL_FORGE_TEMP_GENERATE_CONFIG" in env_pairs
    assert "NOVEL_FORGE_TEMP_POLISH_CONFIG" in env_pairs
    assert "NOVEL_FORGE_TEMP_PROFILE_STRUCTURE" in env_pairs


def test_settings_page_collects_creative_temperature_jitter_pairs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    page._param_widgets["_creative_temp_jitter_enabled"].setCurrentText("true")
    scope = page._param_widgets["_creative_temp_jitter_scope"]
    scope.setCurrentIndex(scope.findData("custom"))
    page._param_widgets["_creative_temp_jitter_down"].setValue(0.2)
    page._param_widgets["_creative_temp_jitter_up"].setValue(0.1)
    checks = page._param_widgets["_creative_temp_jitter_task_checks"]
    checks["draft_chapter"].setChecked(True)
    checks["check_chapter"].setChecked(True)

    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_ENABLED"] == "true"
    assert env_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_SCOPE"] == "custom"
    assert env_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_DOWN_DELTA"] == "0.2"
    assert env_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_UP_DELTA"] == "0.1"
    assert "draft_chapter" in env_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS"]
    assert "check_chapter" not in env_pairs["NOVEL_FORGE_CREATIVE_TEMPERATURE_JITTER_CUSTOM_TASKS"]
    assert page._param_widgets["_creative_temp_jitter_custom_section"].isHidden() is False
    assert checks["check_chapter"].isEnabled() is False
    assert "围绕此值随机" in page._route_rows["draft_chapter"].temperature_spin.toolTip()
    assert "固定温度" in page._route_rows["check_chapter"].temperature_spin.toolTip()


def test_settings_page_exposes_temperature_for_every_routed_task(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()

    missing = [
        task_key
        for task_key, row in page._route_rows.items()
        if task_key != "element_progress_arbiter"
        and not task_key.startswith("tts_")
        and row.temperature_spin is None
    ]

    assert missing == []
    assert page._route_rows["init_story_core_premise"].temperature_spin is not None
    assert (
        page._route_rows["init_story_core_premise"].temperature_spin.property("setting_attr")
        == "temp_init_story_bible"
    )
    assert page._route_rows["init_character_roster"].temperature_spin is not None
    assert (
        page._route_rows["init_character_roster"].temperature_spin.property("setting_attr")
        == "temp_init_character_bible"
    )
    assert page._route_rows["derive_editorial_structure"].temperature_spin is not None
    assert (
        page._route_rows["derive_editorial_structure"].temperature_spin.property("setting_attr")
        == "temp_plan_outline"
    )
    assert page._route_rows["init_knowledge_boundaries"].temperature_spin is not None
    assert (
        page._route_rows["init_knowledge_boundaries"].temperature_spin.property("setting_attr")
        == "temp_init_knowledge_boundaries"
    )
    assert page._route_rows["repair_knowledge_boundary"].temperature_spin is not None
    assert (
        page._route_rows["repair_knowledge_boundary"].temperature_spin.property("setting_attr")
        == "temp_repair_knowledge_boundary"
    )
    assert page._route_rows["extract_knowledge_deltas"].temperature_spin is not None
    assert (
        page._route_rows["extract_knowledge_deltas"].temperature_spin.property("setting_attr")
        == "temp_extract_knowledge_deltas"
    )

    page._route_rows["init_story_core_premise"].temperature_spin.setValue(0.81)
    page._route_rows["init_character_roster"].temperature_spin.setValue(0.82)
    page._route_rows["derive_editorial_structure"].temperature_spin.setValue(0.09)
    page._route_rows["init_knowledge_boundaries"].temperature_spin.setValue(0.31)
    page._route_rows["repair_knowledge_boundary"].temperature_spin.setValue(0.21)
    page._route_rows["extract_knowledge_deltas"].temperature_spin.setValue(0.16)
    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_TEMP_INIT_STORY_BIBLE"] == "0.81"
    assert env_pairs["NOVEL_FORGE_TEMP_INIT_CHARACTER_BIBLE"] == "0.82"
    assert env_pairs["NOVEL_FORGE_TEMP_PLAN_OUTLINE"] == "0.09"
    assert env_pairs["NOVEL_FORGE_TEMP_INIT_KNOWLEDGE_BOUNDARIES"] == "0.31"
    assert env_pairs["NOVEL_FORGE_TEMP_REPAIR_KNOWLEDGE_BOUNDARY"] == "0.21"
    assert env_pairs["NOVEL_FORGE_TEMP_EXTRACT_KNOWLEDGE_DELTAS"] == "0.16"

    _cleanup_widget(page)


def test_settings_page_collects_init_coherence_params(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    widgets = page._param_widgets
    widgets["_split_tasks_enabled"].setCurrentText("false")
    assert "_init_blueprint_mode" not in widgets
    widgets["_init_fragment_max_parallel"].setValue(5)
    widgets["_init_character_profile_parallel_min_roster"].setValue(13)
    widgets["_init_character_profile_batch_size"].setValue(6)
    widgets["_outline_batch_size"].setValue(7)
    widgets["_init_outline_beats_min"].setValue(5)
    widgets["_init_outline_beats_max"].setValue(9)
    widgets["_init_outline_main_points_min"].setValue(2)
    widgets["_init_outline_main_points_max"].setValue(5)
    widgets["_init_outline_subplot_points_max"].setValue(2)
    widgets["_init_outline_element_focus_max"].setValue(2)
    widgets["_init_outline_payoffs_min"].setValue(1)
    widgets["_init_outline_payoffs_max"].setValue(2)
    widgets["_chapter_contract_batch_size"].setValue(9)
    widgets["_outline_thinking"].setCurrentText("true")
    widgets["_outline_thinking_providers"].setText("tongyi,deepseek,openai")
    widgets["_outline_thinking_models"].setText("qwen-max")
    widgets["_outline_multi_turn"].setCurrentText("false")
    widgets["_outline_multi_turn_providers"].setText("deepseek")
    widgets["_outline_multi_turn_models"].setText("deepseek-chat")
    widgets["_chapter_contract_context_window"].setValue(3)
    widgets["_chapter_contract_dynamic_budget"].setCurrentText("false")
    widgets["_chapter_contract_hard_words_per_item"].setValue(1100)
    widgets["_chapter_contract_hard_min_items"].setValue(2)
    widgets["_chapter_contract_hard_max_items"].setValue(5)
    widgets["_chapter_contract_soft_words_per_item"].setValue(850)
    widgets["_chapter_contract_soft_max_items"].setValue(4)
    widgets["_chapter_contract_state_words_per_item"].setValue(1300)
    widgets["_chapter_contract_state_max_items"].setValue(3)
    widgets["_chapter_contract_multi_turn"].setCurrentText("false")
    widgets["_chapter_contract_multi_turn_providers"].setText("tongyi")
    widgets["_chapter_contract_multi_turn_models"].setText("qwen-long")
    widgets["_contract_coherence_batch_size"].setValue(10)
    widgets["_contract_coherence_context_window"].setValue(4)
    widgets["_contract_coherence_max_parallel"].setValue(3)
    widgets["_init_coh_use_memory"].setCurrentText("false")
    widgets["_init_coh_claim_batch_size"].setValue(6)
    widgets["_init_coh_claim_payload_budget"].setValue(24000)
    widgets["_init_coh_claim_max_parallel"].setValue(3)
    widgets["_init_blueprint_holistic_claims"].setCurrentText("false")
    widgets["_init_blueprint_holistic_claim_tokens"].setValue(8192)
    widgets["_init_stream_claim_prefetch"].setCurrentText("false")
    widgets["_init_coh_overlap_chapters"].setValue(1)
    widgets["_init_coh_semantic_top_k"].setValue(5)
    widgets["_init_coh_candidate_max"].setValue(22)
    widgets["_init_coh_llm_candidate_batch"].setValue(7)
    widgets["_init_coh_llm_candidate_max_parallel"].setValue(4)
    widgets["_init_coh_confidence_threshold"].setValue(0.8)
    widgets["_init_coh_recheck_window"].setValue(3)
    widgets["_init_coh_auto_repair"].setCurrentText("false")
    widgets["_init_coh_deterministic_repair"].setCurrentText("true")
    widgets["_init_coh_repair_rounds"].setValue(2)
    widgets["_init_coh_block_min_severity"].setCurrentText("critical")
    widgets["_init_coh_patch_max_ops"].setValue(12)
    widgets["_init_coh_target_patch_batch_size"].setValue(8)
    widgets["_init_source_artifact_auto_repair"].setCurrentText("false")
    widgets["_init_source_artifact_repair_rounds"].setValue(3)
    widgets["_init_claim_coverage_enabled"].setCurrentText("false")
    widgets["_init_claim_coverage_block_p0"].setCurrentText("false")
    widgets["_init_claim_coverage_block_p1"].setCurrentText("true")
    widgets["_init_claim_coverage_block_degraded"].setCurrentText("false")
    widgets["_init_disable_local_story_fallbacks"].setCurrentText("true")
    widgets["_init_creative_refinement_enabled"].setCurrentText("true")
    widgets["_init_creative_refinement_auto_apply"].setCurrentText("false")
    widgets["_init_readiness_required"].setCurrentText("false")

    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_SPLIT_TASKS_ENABLED"] == "false"
    assert "NOVEL_FORGE_INIT_BLUEPRINT_MODE" not in env_pairs
    assert env_pairs["NOVEL_FORGE_INIT_FRAGMENT_MAX_PARALLEL"] == "5"
    assert env_pairs["NOVEL_FORGE_INIT_CHARACTER_PROFILE_PARALLEL_MIN_ROSTER"] == "13"
    assert env_pairs["NOVEL_FORGE_INIT_CHARACTER_PROFILE_BATCH_SIZE"] == "6"
    assert env_pairs["NOVEL_FORGE_OUTLINE_BATCH_SIZE"] == "7"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_BEATS_MIN"] == "5"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_BEATS_MAX"] == "9"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_MAIN_PLOT_POINTS_MIN"] == "2"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_MAIN_PLOT_POINTS_MAX"] == "5"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_SUBPLOT_POINTS_MAX"] == "2"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_ELEMENT_FOCUS_MAX"] == "2"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_EXPECTED_PAYOFFS_MIN"] == "1"
    assert env_pairs["NOVEL_FORGE_INIT_OUTLINE_EXPECTED_PAYOFFS_MAX"] == "2"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_BATCH_SIZE"] == "9"
    assert env_pairs["NOVEL_FORGE_OUTLINE_THINKING"] == "true"
    assert env_pairs["NOVEL_FORGE_OUTLINE_THINKING_PROVIDERS"] == "tongyi,deepseek,openai"
    assert env_pairs["NOVEL_FORGE_OUTLINE_THINKING_MODELS"] == "qwen-max"
    assert env_pairs["NOVEL_FORGE_OUTLINE_MULTI_TURN"] == "false"
    assert env_pairs["NOVEL_FORGE_OUTLINE_MULTI_TURN_PROVIDERS"] == "deepseek"
    assert env_pairs["NOVEL_FORGE_OUTLINE_MULTI_TURN_MODELS"] == "deepseek-chat"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_CONTEXT_WINDOW"] == "3"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_DYNAMIC_BUDGET_ENABLED"] == "false"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_HARD_WORDS_PER_ITEM"] == "1100"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_HARD_MIN_ITEMS"] == "2"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_HARD_MAX_ITEMS"] == "5"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_SOFT_WORDS_PER_ITEM"] == "850"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_SOFT_MAX_ITEMS"] == "4"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_STATE_WORDS_PER_ITEM"] == "1300"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_STATE_MAX_ITEMS"] == "3"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_MULTI_TURN"] == "false"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_MULTI_TURN_PROVIDERS"] == "tongyi"
    assert env_pairs["NOVEL_FORGE_CHAPTER_CONTRACT_MULTI_TURN_MODELS"] == "qwen-long"
    assert env_pairs["NOVEL_FORGE_CONTRACT_COHERENCE_BATCH_SIZE"] == "10"
    assert env_pairs["NOVEL_FORGE_CONTRACT_COHERENCE_CONTEXT_WINDOW"] == "4"
    assert env_pairs["NOVEL_FORGE_CONTRACT_COHERENCE_MAX_PARALLEL"] == "3"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_USE_MEMORY"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_CLAIM_BATCH_SIZE"] == "6"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_CLAIM_PAYLOAD_CHAR_BUDGET"] == "24000"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_CLAIM_MAX_PARALLEL"] == "3"
    assert env_pairs["NOVEL_FORGE_INIT_BLUEPRINT_HOLISTIC_CLAIMS_ENABLED"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_BLUEPRINT_HOLISTIC_CLAIM_MAX_TOKENS"] == "8192"
    assert env_pairs["NOVEL_FORGE_INIT_STREAM_CLAIM_PREFETCH_ENABLED"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_OVERLAP_CHAPTERS"] == "1"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_SEMANTIC_TOP_K"] == "5"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_CANDIDATE_MAX_PER_BATCH"] == "22"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_LLM_CANDIDATE_BATCH_SIZE"] == "7"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_LLM_CANDIDATE_MAX_PARALLEL"] == "4"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_CONFIDENCE_THRESHOLD"] == "0.8"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_RECHECK_AFFECTED_WINDOW"] == "3"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_AUTO_REPAIR"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_DETERMINISTIC_REPAIR"] == "true"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_MAX_REPAIR_ROUNDS"] == "2"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_BLOCK_MIN_SEVERITY"] == "critical"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_PATCH_MAX_OPS"] == "12"
    assert env_pairs["NOVEL_FORGE_INIT_COHERENCE_TARGET_PATCH_BATCH_SIZE"] == "8"
    assert env_pairs["NOVEL_FORGE_INIT_SOURCE_ARTIFACT_AUTO_REPAIR"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_SOURCE_ARTIFACT_REPAIR_ROUNDS"] == "3"
    assert env_pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_ENABLED"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_BLOCK_P0"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_BLOCK_P1"] == "true"
    assert env_pairs["NOVEL_FORGE_INIT_CLAIM_COVERAGE_BLOCK_DEGRADED"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_DISABLE_LOCAL_STORY_FALLBACKS"] == "true"
    assert env_pairs["NOVEL_FORGE_INIT_CREATIVE_REFINEMENT_ENABLED"] == "true"
    assert env_pairs["NOVEL_FORGE_INIT_CREATIVE_REFINEMENT_AUTO_APPLY_LOW_RISK"] == "false"
    assert env_pairs["NOVEL_FORGE_INIT_READINESS_REQUIRED"] == "false"

    _cleanup_widget(page)
    assert "NOVEL_FORGE_MEMORY_CRITIC_AGENT_TIMEOUT_EXTEND_ATTEMPTS" in env_pairs
    assert "NOVEL_FORGE_MEMORY_CRITIC_AGENT_TIMEOUT_EXTEND_MULTIPLIER" in env_pairs
    assert "NOVEL_FORGE_MEMORY_MOTIF_RELATED_LOOKBACK_CHAPTERS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET" in env_pairs
    assert "NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_REPETITION_LOOKBACK_CHAPTERS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_REPETITION_RECENT_GAP_CHAPTERS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_DORMANT_CALLBACK_MIN_CHAPTERS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_AUTO_FORGET_EPHEMERAL_ENABLED" in env_pairs
    assert "NOVEL_FORGE_MOTIF_EPHEMERAL_FORGET_AFTER_CHAPTERS" in env_pairs
    assert "NOVEL_FORGE_MOTIF_EPHEMERAL_MAX_OCCURRENCES" in env_pairs
    assert "NOVEL_FORGE_MOTIF_EPHEMERAL_IMPORTANCE_THRESHOLD_PCT" in env_pairs
    assert "NOVEL_FORGE_MEMORY_CHAPTER_SUMMARY_TARGET_WORDS" in env_pairs
    assert "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_ENABLED" in env_pairs
    assert "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_MAX_ITEMS_PER_CHAPTER" in env_pairs
    assert "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_GRAY_SCORE_LOW" in env_pairs
    assert "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_GRAY_SCORE_HIGH" in env_pairs
    assert "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_MAX_TOKENS" in env_pairs
    assert "NOVEL_FORGE_ELEMENT_PROGRESS_LLM_ARBITER_TEMPERATURE" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_RETRY_ATTEMPTS" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_RETRY_TEMPERATURE" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_RETRY_RAW_CHAR_LIMIT" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_REPAIR_ENABLED" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_REPAIR_MODEL" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_REPAIR_MAX_TOKENS" in env_pairs
    assert "NOVEL_FORGE_LLM_FORMAT_REPAIR_RAW_CHAR_LIMIT" in env_pairs
    assert "NOVEL_FORGE_LONG_MIN_ACCEPT_SCORE" in env_pairs
    assert "NOVEL_FORGE_LONG_CONTINUITY_HARD_BLOCK_THRESHOLD" in env_pairs
    assert "NOVEL_FORGE_LONG_CAUSAL_HARD_BLOCK_THRESHOLD" in env_pairs
    assert env_pairs["NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_ENABLED"] in {
        "true",
        "false",
    }
    assert env_pairs["NOVEL_FORGE_LONG_WAVE_WORD_COUNT_POLICY"] in {
        "inherit",
        "enforce",
        "warn",
    }
    assert env_pairs["NOVEL_FORGE_LONG_READING_POWER_ARCHIVE_POLICY"] in {
        "off",
        "floor_only",
        "floor_or_core_high",
    }
    assert "NOVEL_FORGE_LONG_READING_POWER_HARD_BLOCK_THRESHOLD" in env_pairs
    assert env_pairs["NOVEL_FORGE_LONG_GUARD_ARCHIVE_POLICY"] in {
        "warn",
        "block_actionable",
    }
    assert "NOVEL_FORGE_LONG_GUARD_ARCHIVE_BLOCK_MIN_CONFIDENCE" in env_pairs
    assert "NOVEL_FORGE_LONG_READING_POWER_REPAIR_ENABLED" in env_pairs
    assert "NOVEL_FORGE_LONG_READING_POWER_REPAIR_THRESHOLD" in env_pairs
    assert "NOVEL_FORGE_LONG_READING_POWER_MAX_REPAIR_ROUNDS" in env_pairs
    assert "NOVEL_FORGE_LONG_READING_POWER_REPAIR_MAX_CHANGE_RATIO" in env_pairs
    assert "NOVEL_FORGE_LONG_PLAN_MAX_SCENE_SWITCHES" in env_pairs
    assert "NOVEL_FORGE_LONG_PLAN_SENSORY_NOTES_MAX_ITEMS" in env_pairs
    assert "NOVEL_FORGE_LONG_AUTO_INTRODUCE_MAX_NEW_CHARACTERS" in env_pairs
    assert env_pairs["NOVEL_FORGE_LONG_BOOK_AUDIT_AUTO_REPAIR"] == "false"
    assert env_pairs["NOVEL_FORGE_LLM_FORMAT_RETRY_ATTEMPTS"] != "0"
    assert "NOVEL_FORGE_LONG_BOOK_AUDIT_USE_ISSUE_PANEL_POOL" in env_pairs
    assert "NOVEL_FORGE_LONG_BOOK_AUDIT_REPAIR_CONCURRENCY" in env_pairs
    assert "NOVEL_FORGE_LONG_BOOK_AUDIT_GENERATE_REPAIR_REPORT" in env_pairs
    assert "NOVEL_FORGE_EXTRACT_CANON_MAX_EXISTING_THREAD_IDS" in env_pairs
    assert "NOVEL_FORGE_EXTRACT_CANON_MAX_PRIOR_RELATIONSHIPS" in env_pairs
    assert "NOVEL_FORGE_EXTRACT_CANON_OUTPUT_MAX_TOKENS" in env_pairs
    assert "NOVEL_FORGE_EXTRACT_CANON_ABORT_ON_SEVERE_DAMAGE" in env_pairs
    assert "NOVEL_FORGE_EXTRACT_CANON_SEVERE_DAMAGE_MISSING_SECTION_THRESHOLD" in env_pairs
    assert "NOVEL_FORGE_EXTRACT_CANON_MAX_PLOT_THREAD_DELTAS" in env_pairs
    assert "NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_CANDIDATE_LIMIT" in env_pairs
    assert "NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_TOKEN_BUDGET" in env_pairs
    assert "NOVEL_FORGE_LONG_BOUNDARY_PREV_TAIL_PARAGRAPHS" in env_pairs
    assert "NOVEL_FORGE_LONG_BOUNDARY_OPENING_PARAGRAPHS" in env_pairs
    assert env_pairs["NOVEL_FORGE_LONG_DRAFT_PROMPT_DIAGNOSTICS_ENABLED"] in {
        "true",
        "false",
    }
    assert "NOVEL_FORGE_LONG_DRAFT_PROMPT_WARN_TOKENS" in env_pairs
    assert "NOVEL_FORGE_OLLAMA_SIDECAR_ENABLED" in env_pairs
    assert "NOVEL_FORGE_OLLAMA_SIDECAR_AUTO_START" in env_pairs
    assert "NOVEL_FORGE_OLLAMA_SIDECAR_BINARY_PATH" in env_pairs
    assert "NOVEL_FORGE_OLLAMA_SIDECAR_MODELS_DIR" in env_pairs
    assert "NOVEL_FORGE_OLLAMA_SIDECAR_PREFER_LOCAL" in env_pairs
    assert env_pairs.get("NOVEL_FORGE_PRONOUN_AUTOFIX_MODE") == "off"

    _cleanup_widget(page)


def test_settings_page_route_temperature_pairs_use_setting_attrs(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()

    macro_spin = page._route_rows["macro_guard_audit"].temperature_spin
    book_verify_spin = page._route_rows["book_consistency_verify"].temperature_spin
    assert macro_spin is not None
    assert book_verify_spin is not None
    macro_spin.setValue(0.44)
    book_verify_spin.setValue(0.19)

    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_TEMP_MACRO_GUARD"] == "0.44"
    assert env_pairs["NOVEL_FORGE_TEMP_BOOK_CONSISTENCY"] == "0.19"
    assert "NOVEL_FORGE_TEMP_MACRO_GUARD_AUDIT" not in env_pairs
    assert "NOVEL_FORGE_TEMP_BOOK_CONSISTENCY_VERIFY" not in env_pairs

    _cleanup_widget(page)


def test_settings_page_subdivides_route_groups_by_workflow_phase(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    group_tabs = page.findChild(QTabWidget, "routingGroupTabs")
    assert group_tabs is not None
    assert [group_tabs.tabText(index) for index in range(group_tabs.count())] == [
        group.name for group in ROUTING_GROUPS
    ]

    subgroup_titles: set[str] = set()
    for index, group in enumerate(ROUTING_GROUPS):
        group_page = group_tabs.widget(index)
        subgroup_tabs = group_page.findChild(QTabWidget, "routingSubgroupTabs")
        assert subgroup_tabs is not None
        tab_titles = [subgroup_tabs.tabText(i) for i in range(subgroup_tabs.count())]
        assert tab_titles == [subgroup.title for subgroup in group.subgroups]
        subgroup_titles.update(tab_titles)

    subgroup_descs = page.findChildren(QLabel, "routingSubgroupDesc")
    group_descs = page.findChildren(QLabel, "routingGroupDesc")
    subgroup_keys = {
        "短篇流程 / 构思与节拍",
        "短篇流程 / 写作闭环",
        "长篇初始化 / 资料准备与世界规则",
        "长篇初始化 / 角色与关系",
        "长篇初始化 / 风格与实体",
        "长篇初始化 / 蓝图与画像",
        "长篇初始化 / 编辑契约",
        "长篇初始化 / 章节规划",
        "长篇初始化 / 初始化一致性",
        "长篇章节创作 / 生成链路",
        "长篇章节创作 / 审计与评估",
        "长篇章节创作 / 修复与补丁",
        "长篇章节创作 / 归档与状态",
        "工具与维护 / 护栏与角色工具",
        "工具与维护 / 全书审计",
        "工具与维护 / 配置生成",
        "记忆增强 / 压缩与验证",
        "记忆增强 / 母题与 Critic",
        "记忆增强 / 多粒度摘要",
    }

    assert {
        "构思与节拍",
        "写作闭环",
        "资料准备与世界规则",
        "角色与关系",
        "风格与实体",
        "蓝图与画像",
        "编辑契约",
        "章节规划",
        "初始化一致性",
        "生成链路",
        "审计与评估",
        "修复与补丁",
        "归档与状态",
        "护栏与角色工具",
        "全书审计",
        "配置生成",
        "压缩与验证",
        "母题与 Critic",
        "多粒度摘要",
    } <= subgroup_titles
    assert subgroup_keys <= set(page._group_bulk_rows)
    group_bulk_row = page._group_bulk_rows["长篇章节创作"]
    subgroup_bulk_row = page._group_bulk_rows["长篇章节创作 / 归档与状态"]
    assert group_bulk_row.scope == "group"
    assert group_bulk_row._scope_label.text() == "全组统一设定"
    assert group_bulk_row._apply_btn.text() == "应用到全组"
    assert subgroup_bulk_row.scope == "subgroup"
    assert subgroup_bulk_row._scope_label.text() == "本阶段统一设定"
    assert subgroup_bulk_row._apply_btn.text() == "应用本阶段"
    assert page._bulk_route_task_keys["短篇流程 / 构思与节拍"] == [
        "spec_enrich",
        "beats",
    ]
    assert page._bulk_route_task_keys["短篇流程 / 写作闭环"] == [
        "draft",
        "edit",
        "evaluate",
    ]
    assert page._bulk_route_task_keys["长篇初始化 / 初始化一致性"] == [
        "extract_init_coherence_claims",
        "extract_blueprint_holistic_claims",
        "adjudicate_init_conflict_candidates",
        "repair_init_artifact_patch",
        "adjudicate_contract_coherence",
    ]
    assert page._bulk_route_task_keys["长篇章节创作 / 生成链路"] == [
        "plan_chapter",
        "plan_chapter_scenes",
        "validate_scene_plan",
        "bridge_chapter",
        "draft_chapter",
        "draft_scene",
        "wave_chapter",
        "edit_chapter",
        "polish_chapter",
    ]
    assert page._bulk_route_task_keys["长篇章节创作 / 审计与评估"] == [
        "check_chapter",
        "check_alignment",
        "check_continuity",
        "validate_causal",
        "humanize_scan",
        "humanize_paragraph_rewrite",
        "knowledge_boundary_audit",
        "init_knowledge_boundaries",
        "evaluate_reading_power",
        "macro_guard_audit",
        "element_progress_arbiter",
    ]
    assert page._bulk_route_task_keys["长篇章节创作 / 修复与补丁"] == [
        "repair_continuity",
        "repair_causal",
        "repair_strategy_diagnose",
        "repair_reading_power",
        "repair_knowledge_boundary",
        "extract_knowledge_deltas",
        "patch_chapter",
    ]
    assert page._bulk_route_task_keys["长篇章节创作 / 归档与状态"] == [
        "extract_canon",
        "extract_candidate_state_deltas",
        "adjudicate_state_delta",
        "adjudicate_contract_completion",
        "adjudicate_final_state",
        "repair_adjudicated_issue",
    ]
    assert page._bulk_route_task_keys["工具与维护 / 全书审计"] == [
        "book_consistency",
        "volume_audit",
        "book_consistency_verify",
    ]
    assert page._bulk_route_task_keys["记忆增强 / 压缩与验证"] == [
        "context_compress",
        "verify_compression",
    ]
    assert page._bulk_route_task_keys["记忆增强 / 母题与 Critic"] == [
        "extract_motifs",
        "critic_continuity",
        "critic_character",
        "critic_causal",
        "critic_strengths",
    ]
    assert page._bulk_route_task_keys["记忆增强 / 多粒度摘要"] == [
        "summarize_chapter",
        "summarize_volume",
        "summarize_arc",
        "summarize_scene",
    ]
    assert subgroup_descs
    assert group_descs
    assert all(not label.wordWrap() for label in subgroup_descs)
    assert all(not label.wordWrap() for label in group_descs)
    assert all("\n" not in label.text() for label in subgroup_descs + group_descs)
    assert any(
        label.toolTip() == "一致性 Claims 抽取、整体蓝图 claims、冲突裁判与局部修复"
        for label in subgroup_descs
    )
    assert any("v3 立项主链" in label.toolTip() for label in group_descs)

    _cleanup_widget(page)


def test_settings_page_subgroup_bulk_apply_only_targets_subsection(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = _stub_config()
    config.group_bulk_routes = {
        "长篇章节创作 / 审计与评估": {
            "profile_id": "tongyi:qwen-max",
            "thinking": False,
            "multi_turn": False,
            "fallback_routes": [],
        }
    }
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()
    page._on_group_bulk_apply("长篇章节创作 / 审计与评估")

    assert page._route_rows["check_chapter"].get_route() == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert page._route_rows["element_progress_arbiter"].get_route() == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert page._route_rows["check_continuity"].get_route() == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert page._route_rows["validate_causal"].get_route() == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert page._route_rows["draft_chapter"].get_route() is None
    assert page._route_rows["repair_continuity"].get_route() is None
    assert page._config.routes["check_chapter"] == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert "draft_chapter" not in page._config.routes

    _cleanup_widget(page)


def test_lazy_group_bulk_apply_updates_unbuilt_target_routes(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = _stub_config()
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage(eager_build=False)
    routing_section = page._deferred_sections[1]
    routing_section.ensure_body_built()
    assert page._lazy_route_groups
    group_tabs = page.findChild(QTabWidget, "routingGroupTabs")
    assert group_tabs is not None
    page._ensure_route_group_tab(group_tabs, 0)
    assert "短篇流程" in page._group_bulk_rows
    bulk_row = page._group_bulk_rows["短篇流程"]
    bulk_row.model_combo.setCurrentIndex(
        bulk_row.model_combo.findData("tongyi:qwen-max")
    )

    page._on_group_bulk_apply("短篇流程")

    assert page._config.routes["spec_enrich"] == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert page._config.routes["evaluate"] == TaskRouteEntry(
        profile_id="tongyi:qwen-max"
    )
    assert "evaluate" not in page._route_rows

    _cleanup_widget(page)


def test_word_count_archive_gate_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        long_word_count_archive_gate_enabled=False,
        long_word_count_archive_gate_max_rejections=3,
        long_wave_word_count_policy="warn",
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_ENABLED"] == "false"
    assert env_pairs["NOVEL_FORGE_LONG_WAVE_WORD_COUNT_POLICY"] == "warn"
    assert env_pairs["NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_MAX_REJECTIONS"] == "3"
    monkeypatch.setenv("NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_ENABLED", "false")
    monkeypatch.setenv("NOVEL_FORGE_LONG_WAVE_WORD_COUNT_POLICY", "warn")
    monkeypatch.setenv("NOVEL_FORGE_LONG_WORD_COUNT_ARCHIVE_GATE_MAX_REJECTIONS", "3")
    parsed = Settings(_env_file=None)
    assert parsed.long_word_count_archive_gate_enabled is False
    assert parsed.long_wave_word_count_policy == "warn"
    assert parsed.long_word_count_archive_gate_max_rejections == 3

    _cleanup_widget(page)


def test_memory_summary_target_words_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        memory_chapter_summary_target_words=420,
        memory_volume_summary_target_words=1400,
        memory_summary_input_token_budget=32768,
        memory_summary_recent_chapters=7,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_MEMORY_CHAPTER_SUMMARY_TARGET_WORDS"] == "420"
    assert env_pairs["NOVEL_FORGE_MEMORY_VOLUME_SUMMARY_TARGET_WORDS"] == "1400"
    assert env_pairs["NOVEL_FORGE_MEMORY_SUMMARY_INPUT_TOKEN_BUDGET"] == "32768"
    assert env_pairs["NOVEL_FORGE_MEMORY_SUMMARY_RECENT_CHAPTERS"] == "7"
    monkeypatch.setenv("NOVEL_FORGE_MEMORY_CHAPTER_SUMMARY_TARGET_WORDS", "420")
    monkeypatch.setenv("NOVEL_FORGE_MEMORY_VOLUME_SUMMARY_TARGET_WORDS", "1400")
    monkeypatch.setenv("NOVEL_FORGE_MEMORY_SUMMARY_INPUT_TOKEN_BUDGET", "32768")
    monkeypatch.setenv("NOVEL_FORGE_MEMORY_SUMMARY_RECENT_CHAPTERS", "7")
    parsed = Settings(_env_file=None)
    assert parsed.memory_chapter_summary_target_words == 420
    assert parsed.memory_volume_summary_target_words == 1400
    assert parsed.memory_summary_input_token_budget == 32768
    assert parsed.memory_summary_recent_chapters == 7

    _cleanup_widget(page)


def test_long_reliability_enhancement_settings_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        long_summary_drift_check_enabled=True,
        long_quality_trend_tracker_enabled=True,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    assert env_pairs["NOVEL_FORGE_LONG_SUMMARY_DRIFT_CHECK_ENABLED"] == "true"
    assert env_pairs["NOVEL_FORGE_LONG_QUALITY_TREND_TRACKER_ENABLED"] == "true"
    monkeypatch.setenv("NOVEL_FORGE_LONG_SUMMARY_DRIFT_CHECK_ENABLED", "true")
    monkeypatch.setenv("NOVEL_FORGE_LONG_QUALITY_TREND_TRACKER_ENABLED", "true")
    parsed = Settings(_env_file=None)
    assert parsed.long_summary_drift_check_enabled is True
    assert parsed.long_quality_trend_tracker_enabled is True

    _cleanup_widget(page)


def test_motif_prompt_budget_round_trip_between_ui_env_and_runtime(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        motif_prompt_token_budget=900,
        motif_prompt_max_items=12,
        motif_forbidden_max_items=6,
        motif_repetition_lookback_chapters=9,
        motif_repetition_recent_gap_chapters=4,
        motif_dormant_callback_min_chapters=30,
        motif_auto_forget_ephemeral_enabled=False,
        motif_ephemeral_forget_after_chapters=18,
        motif_ephemeral_max_occurrences=2,
        motif_ephemeral_importance_threshold_pct=40,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    expected = {
        "NOVEL_FORGE_MOTIF_PROMPT_TOKEN_BUDGET": "900",
        "NOVEL_FORGE_MOTIF_PROMPT_MAX_ITEMS": "12",
        "NOVEL_FORGE_MOTIF_FORBIDDEN_MAX_ITEMS": "6",
        "NOVEL_FORGE_MOTIF_REPETITION_LOOKBACK_CHAPTERS": "9",
        "NOVEL_FORGE_MOTIF_REPETITION_RECENT_GAP_CHAPTERS": "4",
        "NOVEL_FORGE_MOTIF_DORMANT_CALLBACK_MIN_CHAPTERS": "30",
        "NOVEL_FORGE_MOTIF_AUTO_FORGET_EPHEMERAL_ENABLED": "false",
        "NOVEL_FORGE_MOTIF_EPHEMERAL_FORGET_AFTER_CHAPTERS": "18",
        "NOVEL_FORGE_MOTIF_EPHEMERAL_MAX_OCCURRENCES": "2",
        "NOVEL_FORGE_MOTIF_EPHEMERAL_IMPORTANCE_THRESHOLD_PCT": "40",
    }
    for key, value in expected.items():
        assert env_pairs[key] == value
        monkeypatch.setenv(key, value)

    parsed = Settings(_env_file=None)
    assert parsed.motif_prompt_token_budget == 900
    assert parsed.motif_prompt_max_items == 12
    assert parsed.motif_forbidden_max_items == 6
    assert parsed.motif_repetition_lookback_chapters == 9
    assert parsed.motif_repetition_recent_gap_chapters == 4
    assert parsed.motif_dormant_callback_min_chapters == 30
    assert parsed.motif_auto_forget_ephemeral_enabled is False
    assert parsed.motif_ephemeral_forget_after_chapters == 18
    assert parsed.motif_ephemeral_max_occurrences == 2
    assert parsed.motif_ephemeral_importance_threshold_pct == 40

    from novel_forge.memory.motif import MotifPromptBudget, MotifTracker

    budget = MotifPromptBudget.from_env()
    assert budget.budget_tokens == 900
    assert budget.max_items == 12
    assert MotifTracker._resolve_forbidden_repetition_limit(None, max_motifs=10) == 6

    _cleanup_widget(page)


def test_chapter_granularity_settings_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        long_plan_max_scene_switches=9,
        long_plan_sensory_notes_max_items=1,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    expected = {
        "NOVEL_FORGE_LONG_PLAN_MAX_SCENE_SWITCHES": "9",
        "NOVEL_FORGE_LONG_PLAN_SENSORY_NOTES_MAX_ITEMS": "1",
    }
    for key, value in expected.items():
        assert env_pairs[key] == value
        monkeypatch.setenv(key, value)

    parsed = Settings(_env_file=None)
    assert parsed.long_plan_max_scene_switches == 9
    assert parsed.long_plan_sensory_notes_max_items == 1

    _cleanup_widget(page)


def test_story_kernel_settings_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        story_kernel_db_path="/tmp/story-kernel-test.db",
        story_kernel_wal_mode=False,
        story_kernel_zvec_enabled=True,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    expected = {
        "NOVEL_FORGE_STORY_KERNEL_DB_PATH": "/tmp/story-kernel-test.db",
        "NOVEL_FORGE_STORY_KERNEL_WAL_MODE": "false",
        "NOVEL_FORGE_STORY_KERNEL_ZVEC_ENABLED": "true",
    }
    for key, value in expected.items():
        assert env_pairs[key] == value
        monkeypatch.setenv(key, value)

    parsed = Settings(_env_file=None)
    assert parsed.story_kernel_db_path == "/tmp/story-kernel-test.db"
    assert parsed.story_kernel_wal_mode is False
    assert parsed.story_kernel_zvec_enabled is True
    assert (
        page._param_widgets["_story_kernel_db_path"].text()
        == expected["NOVEL_FORGE_STORY_KERNEL_DB_PATH"]
    )

    _cleanup_widget(page)


def test_chapter_contract_budget_settings_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        chapter_contract_dynamic_budget_enabled=False,
        chapter_contract_hard_words_per_item=1500,
        chapter_contract_hard_min_items=2,
        chapter_contract_hard_max_items=5,
        chapter_contract_soft_words_per_item=700,
        chapter_contract_soft_max_items=4,
        chapter_contract_state_words_per_item=1600,
        chapter_contract_state_max_items=3,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    expected = {
        "NOVEL_FORGE_CHAPTER_CONTRACT_DYNAMIC_BUDGET_ENABLED": "false",
        "NOVEL_FORGE_CHAPTER_CONTRACT_HARD_WORDS_PER_ITEM": "1500",
        "NOVEL_FORGE_CHAPTER_CONTRACT_HARD_MIN_ITEMS": "2",
        "NOVEL_FORGE_CHAPTER_CONTRACT_HARD_MAX_ITEMS": "5",
        "NOVEL_FORGE_CHAPTER_CONTRACT_SOFT_WORDS_PER_ITEM": "700",
        "NOVEL_FORGE_CHAPTER_CONTRACT_SOFT_MAX_ITEMS": "4",
        "NOVEL_FORGE_CHAPTER_CONTRACT_STATE_WORDS_PER_ITEM": "1600",
        "NOVEL_FORGE_CHAPTER_CONTRACT_STATE_MAX_ITEMS": "3",
    }
    for key, value in expected.items():
        assert env_pairs[key] == value
        monkeypatch.setenv(key, value)

    parsed = Settings(_env_file=None)
    assert parsed.chapter_contract_dynamic_budget_enabled is False
    assert parsed.chapter_contract_hard_words_per_item == 1500
    assert parsed.chapter_contract_hard_min_items == 2
    assert parsed.chapter_contract_hard_max_items == 5
    assert parsed.chapter_contract_soft_words_per_item == 700
    assert parsed.chapter_contract_soft_max_items == 4
    assert parsed.chapter_contract_state_words_per_item == 1600
    assert parsed.chapter_contract_state_max_items == 3

    _cleanup_widget(page)


def test_generation_attention_settings_round_trip_between_ui_env_and_settings(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    custom_settings = Settings(
        _env_file=None,
        long_narrative_evidence_candidate_limit=36,
        long_narrative_evidence_token_budget=3072,
        long_draft_prompt_diagnostics_enabled=False,
        long_draft_prompt_warn_tokens=45000,
        long_prompt_diagnostics_enabled=False,
        long_prompt_warn_tokens=41000,
        narrative_state_candidate_evidence_limit=3,
        narrative_state_final_context_max_chars=21000,
        narrative_state_final_target_max_chars=88,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    expected = {
        "NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_CANDIDATE_LIMIT": "36",
        "NOVEL_FORGE_LONG_NARRATIVE_EVIDENCE_TOKEN_BUDGET": "3072",
        "NOVEL_FORGE_LONG_DRAFT_PROMPT_DIAGNOSTICS_ENABLED": "false",
        "NOVEL_FORGE_LONG_DRAFT_PROMPT_WARN_TOKENS": "45000",
        "NOVEL_FORGE_LONG_PROMPT_DIAGNOSTICS_ENABLED": "false",
        "NOVEL_FORGE_LONG_PROMPT_WARN_TOKENS": "41000",
        "NOVEL_FORGE_NARRATIVE_STATE_CANDIDATE_EVIDENCE_LIMIT": "3",
        "NOVEL_FORGE_NARRATIVE_STATE_FINAL_CONTEXT_MAX_CHARS": "21000",
        "NOVEL_FORGE_NARRATIVE_STATE_FINAL_TARGET_MAX_CHARS": "88",
    }
    for key, value in expected.items():
        assert env_pairs[key] == value
        monkeypatch.setenv(key, value)

    parsed = Settings(_env_file=None)
    assert parsed.long_narrative_evidence_candidate_limit == 36
    assert parsed.long_narrative_evidence_token_budget == 3072
    assert parsed.long_draft_prompt_diagnostics_enabled is False
    assert parsed.long_draft_prompt_warn_tokens == 45000
    assert parsed.long_prompt_diagnostics_enabled is False
    assert parsed.long_prompt_warn_tokens == 41000
    assert parsed.narrative_state_candidate_evidence_limit == 3
    assert parsed.narrative_state_final_context_max_chars == 21000
    assert parsed.narrative_state_final_target_max_chars == 88

    _cleanup_widget(page)


def test_settings_page_prunes_obsolete_bulk_routing_group(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = _stub_config()
    config.group_bulk_routes = {
        "长篇流程": {"profile_id": "tongyi:qwen-max"},
        "长篇初始化": {"profile_id": "tongyi:qwen-max"},
        "长篇初始化 / 初始化一致性": {"profile_id": "tongyi:qwen-max"},
        "长篇章节创作 / 审计与评估": {"profile_id": "tongyi:qwen-max"},
    }
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()

    assert "长篇流程" not in page._config.group_bulk_routes
    assert "长篇初始化" in page._config.group_bulk_routes
    assert "长篇初始化 / 初始化一致性" in page._config.group_bulk_routes
    assert "长篇章节创作 / 审计与评估" in page._config.group_bulk_routes

    _cleanup_widget(page)


def test_settings_page_restores_saved_bulk_fallback_order(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="main",
                display_name="Main",
                provider="tongyi",
                model_id="qwen-max",
                api_key="sk",
            ),
            ModelProfile(
                profile_id="fb2",
                display_name="Fallback 2",
                provider="deepseek",
                model_id="deepseek-chat",
                api_key="sk",
            ),
            ModelProfile(
                profile_id="fb1",
                display_name="Fallback 1",
                provider="tongyi",
                model_id="qwen-turbo",
                api_key="sk",
            ),
        ],
        default_profile_id="main",
        group_bulk_routes={
            "短篇流程": {
                "profile_id": "main",
                "thinking": True,
                "multi_turn": False,
                "fallback_routes": [
                    {"profile_id": "fb1", "thinking": False, "multi_turn": False},
                    {"profile_id": "fb2", "thinking": False, "multi_turn": True},
                ],
            }
        },
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()

    bulk_row = page._group_bulk_rows["短篇流程"]
    route = bulk_row.get_bulk_route()
    fallbacks = bulk_row.get_bulk_fallback_routes()
    assert route is not None
    assert route.profile_id == "main"
    assert route.thinking is True
    assert [entry.profile_id for entry in fallbacks] == ["fb1", "fb2"]
    assert fallbacks[1].multi_turn is True

    _cleanup_widget(page)


def test_settings_page_save_syncs_fallback_routes_to_env(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    from novel_forge.desktop.config_store import DesktopSettingsStore

    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="main",
                display_name="Main",
                provider="tongyi",
                model_id="qwen-max",
                api_key="sk",
            ),
            ModelProfile(
                profile_id="fb",
                display_name="Fallback",
                provider="tongyi",
                model_id="qwen-turbo",
                api_key="sk",
            ),
        ],
        routes={"draft_chapter": TaskRouteEntry(profile_id="main")},
        fallback_routes={
            "draft_chapter": [TaskRouteEntry(profile_id="fb", thinking=False, multi_turn=False)]
        },
        default_profile_id="main",
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()
    page._store = DesktopSettingsStore(
        profiles_path=tmp_path / "model_profiles.json",
        env_path=tmp_path / ".env",
    )

    assert page.save_pending_changes() is True

    saved_profiles = json.loads((tmp_path / "model_profiles.json").read_text(encoding="utf-8"))
    assert saved_profiles["fallback_routes"]["draft_chapter"][0]["profile_id"] == "fb"

    env_pairs = dict(
        line.split("=", 1)
        for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    # Routing no longer written to .env; only persisted in model_profiles.json
    assert "NOVEL_FORGE_TASK_FALLBACK_ROUTING" not in env_pairs

    _cleanup_widget(page)


def test_settings_page_exposes_bailian_and_local_audio_routes(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    from novel_forge.tts.platform.schemas import AudioExecutionStage
    from novel_forge.tts.platform.config import constraints_from_settings

    custom_settings = Settings(
        _env_file=None,
        tts_default_provider="bailian",
        tts_dashscope_api_key="sk-bailian-test",
        tts_dashscope_base_url=(
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
        ),
        tts_dashscope_model="qwen-audio-3.0-tts-plus",
        tts_dashscope_preview_model="qwen-audio-3.0-tts-flash",
        tts_dashscope_voice_clone_model="qwen3-tts-vc-2026-01-22",
        tts_dashscope_voice_design_model="qwen3-tts-vd-2026-01-26",
        tts_dashscope_optimize_instructions=False,
        tts_dashscope_voice_clone_enable_preprocess=True,
        tts_dashscope_cny_per_usd=7.3,
        audio_quality_preset="master",
        audio_location_policy="hybrid",
        audio_plugin_overrides=json.dumps(
            {
                "align": "whisperx",
                "tts_formal": "dashscope-qwen-audio-3-tts-plus",
            }
        ),
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: custom_settings)
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    env_pairs = page._collect_param_env_pairs()

    assert page._param_widgets["_tts_dashscope_formal_model"].currentData() == (
        "qwen-audio-3.0-tts-plus"
    )
    assert page._param_widgets["_tts_dashscope_preview_model"].currentData() == (
        "qwen-audio-3.0-tts-flash"
    )
    route_combos = page._param_widgets["_audio_plugin_route_combos"]
    assert route_combos[AudioExecutionStage.ALIGN].currentData() == "whisperx"
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_API_KEY"] == "sk-bailian-test"
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_PREVIEW_MODEL"] == (
        "qwen-audio-3.0-tts-flash"
    )
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_MODEL"] == (
        "qwen3-tts-vc-2026-01-22"
    )
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_DESIGN_MODEL"] == (
        "qwen3-tts-vd-2026-01-26"
    )
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_OPTIMIZE_INSTRUCTIONS"] == "false"
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_VOICE_CLONE_ENABLE_PREPROCESS"] == "true"
    assert env_pairs["NOVEL_FORGE_TTS_DASHSCOPE_CNY_PER_USD"] == "7.3"
    assert env_pairs["NOVEL_FORGE_AUDIO_QUALITY_PRESET"] == "master"
    assert env_pairs["NOVEL_FORGE_AUDIO_LOCATION_POLICY"] == "hybrid"
    saved_overrides = json.loads(env_pairs["NOVEL_FORGE_AUDIO_PLUGIN_OVERRIDES"])
    assert saved_overrides["align"] == "whisperx"
    assert "tts_formal" not in saved_overrides
    rebuilt = constraints_from_settings(
        custom_settings.model_copy(
            update={"audio_plugin_overrides": json.dumps(saved_overrides)}
        )
    )
    assert rebuilt.plugin_overrides[AudioExecutionStage.TTS_FORMAL] == (
        "dashscope-qwen-audio-3-tts-plus"
    )
    assert rebuilt.plugin_overrides[AudioExecutionStage.TTS_PREVIEW] == (
        "dashscope-qwen-audio-3-tts-flash"
    )

    _cleanup_widget(page)


def test_settings_page_save_persists_custom_profile_api_key_to_env(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    from novel_forge.desktop.config_store import DesktopSettingsStore

    profile_id = "custom:qwen3-235b-a22b"
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id=profile_id,
                display_name="Qwen3 235B",
                provider="custom",
                model_id="qwen3-235b-a22b",
                api_key="sk-custom-secret",
                base_url="https://example.test/v1",
            ),
        ],
        routes={"draft_chapter": TaskRouteEntry(profile_id=profile_id)},
        default_profile_id=profile_id,
    )
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()
    page._store = DesktopSettingsStore(
        profiles_path=tmp_path / "model_profiles.json",
        env_path=tmp_path / ".env",
    )

    assert page.save_pending_changes() is True

    saved_profiles = json.loads((tmp_path / "model_profiles.json").read_text(encoding="utf-8"))
    assert "api_key" not in saved_profiles["profiles"][0]

    env_pairs = dict(
        line.split("=", 1)
        for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    env_key = profile_api_key_env_var(profile_id)
    assert env_pairs[env_key] == "sk-custom-secret"
    assert env_pairs["NOVEL_FORGE_DEFAULT_PROVIDER"] == "custom"
    # Routing is no longer written to .env
    assert "NOVEL_FORGE_TASK_ROUTING" not in env_pairs

    _cleanup_widget(page)


def test_settings_page_element_progress_hint_reflects_current_controls(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    assert "额外 LLM 调用为 0" in page._param_widgets["_element_progress_hint_label"].text()

    page._param_widgets["_element_progress_arbiter_enabled"].setCurrentText("true")
    page._param_widgets["_element_progress_arbiter_max_items"].setValue(2)
    page._param_widgets["_element_progress_gray_low"].setValue(1.6)
    page._param_widgets["_element_progress_gray_high"].setValue(1.2)

    text = page._param_widgets["_element_progress_hint_label"].text()
    assert "最多仲裁 2 个要素" in text
    assert "自动互换" in text

    _cleanup_widget(page)


def test_connection_worker_make_adapter_supports_tencent_provider() -> None:
    profile = ModelProfile(
        profile_id="tencent:hunyuan-pro",
        display_name="腾讯混元 Pro",
        provider="tencent",
        model_id="hunyuan-pro",
        api_key="sk-test",
    )

    adapter = _ConnectionTestWorker._make_adapter(profile)

    assert isinstance(adapter, TencentHunyuanAdapter)
    assert adapter.default_model == "hunyuan-pro"


def test_connection_worker_make_adapter_supports_token_plan_provider() -> None:
    profile = ModelProfile(
        profile_id="tongyi_token_plan:qwen3.7-plus",
        display_name="阿里百炼 Token Plan qwen3.7-plus",
        provider="tongyi_token_plan",
        model_id="qwen3.7-plus",
        api_key="sk-sp-test",
    )

    adapter = _ConnectionTestWorker._make_adapter(profile)

    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter._base_url == TONGYI_TOKEN_PLAN_BASE_URL
    assert adapter.default_model == "qwen3.7-plus"


def test_connection_worker_make_adapter_supports_custom_provider() -> None:
    profile = ModelProfile(
        profile_id="custom:qwen3-235b-a22b",
        display_name="Qwen3 235B",
        provider="custom",
        model_id="qwen3-235b-a22b",
        api_key="sk-test",
        base_url="https://example.test/v1",
    )

    adapter = _ConnectionTestWorker._make_adapter(profile)

    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.provider_name == "custom"
    assert adapter.default_model == "qwen3-235b-a22b"


def test_model_dialog_offers_current_mimo_models(qapp: QApplication) -> None:
    dialog = _ModelDialog()
    try:
        provider_index = dialog._provider_combo.findData("mimo")
        assert provider_index >= 0
        assert dialog._provider_combo.itemText(provider_index) == "小米 MiMo"

        dialog._provider_combo.setCurrentIndex(provider_index)
        models = [dialog._model_combo.itemText(i) for i in range(dialog._model_combo.count())]
        assert models[:2] == ["mimo-v2.5-pro", "mimo-v2.5"]
    finally:
        dialog._initial_signature = dialog._form_signature()
        _cleanup_widget(dialog)


def test_connection_worker_make_adapter_supports_mimo_provider() -> None:
    profile = ModelProfile(
        profile_id="mimo:mimo-v2.5-pro",
        display_name="小米 MiMo V2.5 Pro",
        provider="mimo",
        model_id="mimo-v2.5-pro",
        api_key="mimo-test-key",
    )

    adapter = _ConnectionTestWorker._make_adapter(profile)

    assert isinstance(adapter, MiMoAdapter)
    assert adapter.default_model == "mimo-v2.5-pro"


def _ollama_view(*, models: list[OllamaModelView] | None = None) -> OllamaManagerView:
    return OllamaManagerView(
        revision="ollama-revision-for-tests",
        endpoint_scope="engine_host",
        ownership="engine_owned",
        runtime=OllamaRuntimeStatusView(status="healthy", version="0.12.0", detail="Engine 托管服务可用"),
        sidecar=OllamaSidecarView(enabled=True, auto_start=True, prefer_local=True, binary_available=True),
        storage=OllamaStorageView(scope="engine_managed", display_label="Engine 托管的模型目录"),
        capabilities=OllamaCapabilitiesView(
            can_ensure=True,
            can_restart=True,
            can_stop=True,
            can_pull=True,
            can_delete=True,
            can_configure_paths=True,
        ),
        models=models or [],
        configured_roles=OllamaConfiguredRolesView(),
    )


def test_settings_page_has_no_native_ollama_worker() -> None:
    import novel_forge.desktop.pages.settings.components as components

    assert not hasattr(components, "_OllamaModelWorker")
    assert not hasattr(components, "_ollama_native_base_url")


def test_settings_page_ollama_panel_submits_model_roles_to_engine(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())
    submitted: list[dict[str, object]] = []

    class _Engine:
        def set_model_roles(self, _settings: object, **kwargs: object) -> OllamaManagerView:
            submitted.append(kwargs)
            return _ollama_view()

    page = settings_page.SettingsPage()
    model = OllamaModelView(name="llama3.2", size=0, roles=[])
    page._ollama_view = _ollama_view(models=[model])
    page._ollama_engine = _Engine()
    page._set_ollama_roles(model, {"generation": True})

    assert submitted == [{
        "expected_revision": "ollama-revision-for-tests",
        "model": "llama3.2",
        "managed": True,
        "generation": True,
        "embedding": False,
    }]

    _cleanup_widget(page)


def test_settings_page_ollama_panel_observes_engine_view_without_local_path(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    view = _ollama_view()
    view.storage = OllamaStorageView(scope="external", display_label="由 Engine 主机上的外部服务管理")
    page._on_ollama_view_loaded(True, "", view)

    assert page._ollama_storage_label.text() == "由 Engine 主机上的外部服务管理"
    assert "/" not in page._ollama_storage_label.text()

    _cleanup_widget(page)


def test_settings_page_ollama_model_list_renders_engine_models(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    page._on_ollama_view_loaded(
        True,
        "",
        _ollama_view(models=[OllamaModelView(
            name="nomic-embed-text",
            size=274_000_000,
            details={"family": "nomic-bert", "parameter_size": "137M", "quantization_level": "F16"},
            roles=["embedding", "managed"],
        )]),
    )

    assert page._ollama_status_badge.text() == "1 个模型"
    assert page._ollama_models_layout.count() == 1
    buttons = page._ollama_models_layout.itemAt(0).widget().findChildren(QPushButton)
    button_map = {button.text(): button for button in buttons}
    assert button_map["取消嵌入"].isEnabled() is True
    assert "移出路由" in button_map
    assert any(button.text() == "删除" for button in buttons)

    _cleanup_widget(page)


def test_settings_page_ollama_model_list_uses_engine_declared_roles(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    page._on_ollama_view_loaded(
        True,
        "",
        _ollama_view(models=[OllamaModelView(
            name="qwen3:8b",
            size=4_900_000_000,
            details={"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
            roles=["generation", "managed"],
        )]),
    )

    buttons = page._ollama_models_layout.itemAt(0).widget().findChildren(QPushButton)
    button_map = {button.text(): button for button in buttons}
    assert button_map["取消生成"].isEnabled() is True
    assert button_map["设为嵌入"].isEnabled() is True
    assert "移出路由" in button_map

    _cleanup_widget(page)


def test_settings_page_ollama_runtime_summary_does_not_reveal_sidecar_paths(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    page._on_ollama_view_loaded(True, "", _ollama_view())

    assert page._ollama_runtime_badge.text() == "healthy"
    assert "/" not in page._ollama_storage_label.text()

    _cleanup_widget(page)


def test_settings_page_ollama_browse_buttons_fill_directory_fields(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    selected_binary_dir = tmp_path / "ollama-bin"
    selected_models_dir = tmp_path / "ollama-models"
    selected_binary_dir.mkdir()
    selected_models_dir.mkdir()
    chosen = iter([str(selected_binary_dir), str(selected_models_dir)])
    monkeypatch.setattr(
        settings_page_ollama.QFileDialog,
        "getExistingDirectory",
        lambda *args, **kwargs: next(chosen),
    )

    page = settings_page.SettingsPage()
    page._param_widgets["_ollama_sidecar_binary_browse_btn"].click()
    page._param_widgets["_ollama_sidecar_models_browse_btn"].click()

    assert page._param_widgets["_ollama_sidecar_binary_path"].text() == str(
        selected_binary_dir.resolve()
    )
    assert page._param_widgets["_ollama_sidecar_models_dir"].text() == str(
        selected_models_dir.resolve()
    )

    _cleanup_widget(page)


def test_settings_page_ollama_model_list_marks_engine_managed_model(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    page._on_ollama_view_loaded(
        True,
        "",
        _ollama_view(models=[OllamaModelView(name="llama3.2", size=0, roles=["managed"])]),
    )

    buttons = page._ollama_models_layout.itemAt(0).widget().findChildren(QPushButton)
    assert any(button.text() == "移出路由" for button in buttons)

    _cleanup_widget(page)


def test_settings_page_ollama_pull_uses_recommended_model_data(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())
    submitted: list[dict[str, object]] = []

    class _Engine:
        def submit_pull(self, _settings: object, **kwargs: object) -> object:
            submitted.append(kwargs)
            return object()

    page = settings_page.SettingsPage()
    page._ollama_view = _ollama_view()
    page._ollama_engine = _Engine()
    page._ollama_pick_menu.actions()[0].trigger()
    page._pull_ollama_model()

    assert submitted[0]["model"] == "llama3.2"
    assert submitted[0]["expected_revision"] == "ollama-revision-for-tests"

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_defers_connection_tests_until_activation(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()

    assert started == []

    page.activate()

    assert started == ["tongyi:qwen-max"]

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_hidden_deferred_grid_does_not_start_auto_tests(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    # Drain any pending events from previous tests to prevent stale
    # QTimer callbacks (e.g. prewarm_model_cache) from polluting this
    # test's ``started`` list via the class-level monkeypatch.
    qapp.processEvents()

    page = settings_page.SettingsPage(eager_build=False)
    page.show()
    qapp.processEvents()

    page.activate()
    page.hide()
    qapp.processEvents()

    page.ensure_status_grid_built()

    assert started == []
    assert page._status_tests_pending is True

    page.activate()

    assert started == ["tongyi:qwen-max"]

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_hide_cancels_pending_auto_connection_timer(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 1000)
    monkeypatch.setattr(settings_page.SettingsPage, "_POST_STATUS_GRID_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.show()
    qapp.processEvents()

    page.activate()

    assert page._status_test_start_timer.isActive() is True

    page.hide()
    qapp.processEvents()

    assert page._status_auto_activation_active is False
    assert page._status_test_start_timer.isActive() is False

    page._start_pending_status_tests()

    assert started == []

    page.activate()
    page._start_pending_status_tests()

    assert started == ["tongyi:qwen-max"]

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_reuses_cached_connection_result_without_worker(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[str] = []
    _ConnectionTestWorker._result_cache["tongyi:qwen-max"] = (
        time.monotonic(),
        True,
        "连通 12ms",
        True,
        True,
    )

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)

    page = settings_page.SettingsPage()
    page.activate()

    card = page._status_cards["tongyi:qwen-max"]
    assert started == []
    assert page._status_tests_pending is False
    assert card._dot.objectName() == "statusDotGreen"
    assert "连通 12ms" in card._status_label.text()

    page.shutdown()
    _cleanup_widget(page)
    _ConnectionTestWorker._result_cache.pop("tongyi:qwen-max", None)


def test_settings_page_keeps_stale_connection_result_during_background_refresh(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[str] = []
    _ConnectionTestWorker._result_cache["tongyi:qwen-max"] = (
        time.monotonic() - _ConnectionTestWorker._CACHE_TTL - 1.0,
        True,
        "连通 12ms",
        True,
        True,
    )

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()

    card = page._status_cards["tongyi:qwen-max"]
    assert page._status_tests_pending is True
    assert card._dot.objectName() == "statusDotGreen"
    assert "上次连通 12ms" in card._status_label.text()
    assert "后台复测中" in card._status_label.text()

    page.activate()

    assert started == ["tongyi:qwen-max"]
    assert card._dot.objectName() == "statusDotGreen"
    assert "检测中" not in card._status_label.text()
    assert "后台复测中" in card._status_label.text()

    page.shutdown()
    _cleanup_widget(page)
    _ConnectionTestWorker._result_cache.pop("tongyi:qwen-max", None)


def test_settings_page_activate_does_not_repeat_auto_connection_tests(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()
    page.activate()

    assert started == ["tongyi:qwen-max"]

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_starts_all_auto_connection_tests(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-{index}",
            display_name=f"Qwen {index}",
            provider="tongyi",
            model_id=f"qwen-{index}",
            api_key="sk-test",
        )
        for index in range(5)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_MAX_STATUS_TEST_WORKERS", 0)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()

    assert started == [profile.profile_id for profile in profiles]
    assert page._status_test_queue == []

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_watchdog_releases_stuck_connection_tests(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-{index}",
            display_name=f"Qwen {index}",
            provider="tongyi",
            model_id=f"qwen-{index}",
            api_key="sk-test",
        )
        for index in range(5)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)
        self.signals.probe_started.emit(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_MAX_STATUS_TEST_WORKERS", 0)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()

    assert started == [profile.profile_id for profile in profiles]
    assert len(page._status_test_running) == len(profiles)

    page._status_test_running = {
        token: (profile_id, generation, auto, 0.0)
        for token, (profile_id, generation, auto, _started_at) in page._status_test_running.items()
    }
    page._on_status_test_watchdog()

    assert started == [profile.profile_id for profile in profiles]
    assert page._status_test_queue == []
    for profile in profiles:
        card = page._status_cards[profile.profile_id]
        assert card._dot.objectName() == "statusDotRed"
        assert "检测超时" in card._status_label.text()

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_watchdog_waits_for_probe_start(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-{index}",
            display_name=f"Qwen {index}",
            provider="tongyi",
            model_id=f"qwen-{index}",
            api_key="sk-test",
        )
        for index in range(5)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_MAX_STATUS_TEST_WORKERS", 0)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()

    assert started == [profile.profile_id for profile in profiles]
    assert page._status_tests_active == len(profiles)
    assert len(page._status_test_running) == len(profiles)

    page._on_status_test_watchdog()

    assert started == [profile.profile_id for profile in profiles]
    assert page._status_test_queue == []
    for profile in profiles:
        card = page._status_cards[profile.profile_id]
        assert card._dot.objectName() == "statusDotYellow"
        assert "检测中" in card._status_label.text()

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_watchdog_releases_tests_before_probe_start(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-{index}",
            display_name=f"Qwen {index}",
            provider="tongyi",
            model_id=f"qwen-{index}",
            api_key="sk-test",
        )
        for index in range(5)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_MAX_STATUS_TEST_WORKERS", 0)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()

    page._status_test_running = {
        token: (profile_id, generation, auto, 0.0)
        for token, (profile_id, generation, auto, _started_at) in page._status_test_running.items()
    }
    page._on_status_test_watchdog()

    assert started == [profile.profile_id for profile in profiles]
    assert page._status_test_queue == []
    for profile in profiles:
        card = page._status_cards[profile.profile_id]
        assert card._dot.objectName() == "statusDotRed"
        assert "检测超时" in card._status_label.text()

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_caps_concurrent_connection_tests(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-{index}",
            display_name=f"Qwen {index}",
            provider="tongyi",
            model_id=f"qwen-{index}",
            api_key="sk-test",
        )
        for index in range(5)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[str] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._profile.profile_id)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    cap = settings_page.SettingsPage._MAX_STATUS_TEST_WORKERS
    assert cap > 0, (
        "Concurrent health-check cap must be positive to avoid provider rate-limit storms."
    )

    page.activate()

    assert len(started) == cap
    auto_limit = settings_page.SettingsPage._AUTO_STATUS_TEST_MAX_PROFILES
    expected_profiles = len(profiles) if auto_limit <= 0 else min(len(profiles), auto_limit)
    assert len(page._status_test_queue) == expected_profiles - cap
    queued_card = page._status_cards[profiles[-1].profile_id]
    assert "待手动检测" not in queued_card._status_label.text()

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_test_all_forces_status_refresh(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    started: list[bool] = []
    _ConnectionTestWorker._result_cache["tongyi:qwen-max"] = (
        123.0,
        True,
        "连通 1ms",
        True,
        True,
    )

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append(self._force_refresh)

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)

    page = settings_page.SettingsPage()
    page._test_all_connections()

    card = page._status_cards["tongyi:qwen-max"]
    assert started == [True]
    assert "tongyi:qwen-max" not in _ConnectionTestWorker._result_cache
    assert card._status_label.text().endswith("检测中…")
    assert card._dot.objectName() == "statusDotYellow"

    page.shutdown()
    _cleanup_widget(page)
    _ConnectionTestWorker._result_cache.pop("tongyi:qwen-max", None)


def test_settings_page_test_all_overrides_pending_auto_checks(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    profiles = [
        ModelProfile(
            profile_id=f"tongyi:qwen-{index}",
            display_name=f"Qwen {index}",
            provider="tongyi",
            model_id=f"qwen-{index}",
            api_key="sk-test",
        )
        for index in range(3)
    ]
    config = ProfilesConfig(profiles=profiles, default_profile_id=profiles[0].profile_id)
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    started: list[tuple[str, bool]] = []

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        started.append((self._profile.profile_id, self._force_refresh))

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()
    assert started == [(profiles[0].profile_id, False)]
    assert page._status_test_queue

    page._test_all_connections()

    assert [profile_id for profile_id, force in started if force] == [
        profiles[0].profile_id,
        profiles[1].profile_id,
    ]
    assert page._status_detection_busy is True
    assert page._status_test_queue
    assert all(item[2] is True for item in page._status_test_queue)

    page.shutdown()
    _cleanup_widget(page)


def test_settings_page_status_detection_defers_heavy_ui_refresh(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = _stub_config()
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    def fake_start(self) -> None:  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(_ConnectionTestWorker, "start", fake_start)
    monkeypatch.setattr(settings_page.SettingsPage, "_AUTO_STATUS_TEST_DELAY_MS", 0)

    page = settings_page.SettingsPage()
    page.activate()

    assert page._status_detection_busy is True
    page.schedule_deferred_build()
    assert page._deferred_build_timer.isActive() is False

    page._schedule_route_combo_refresh_after_status_test()
    assert page._route_combos_refresh_pending is True
    assert getattr(page._refresh_combos_timer, "isActive", lambda: False)() is False

    page._status_tests_active = 0
    page._status_test_queue = []
    page._finish_status_detection_if_idle()

    assert page._status_detection_busy is False
    assert page._route_combos_refresh_pending is False
    assert page._refresh_combos_timer.isActive() is True

    page.shutdown()
    _cleanup_widget(page)


def test_connection_worker_formats_deepseek_provider_busy_message() -> None:
    profile = ModelProfile(
        profile_id="deepseek:deepseek-v4-flash",
        display_name="DeepSeek deepseek-v4-flash",
        provider="deepseek",
        model_id="deepseek-v4-flash",
        api_key="sk-test",
    )

    detail = _ConnectionTestWorker._format_connection_detail(
        profile,
        '{"error":{"message":"Service is too busy","type":"service_unavailable_error"}}',
    )

    assert "供应商繁忙" in detail
    assert "DeepSeek API 返回 503" in detail


def test_connection_worker_formats_deepseek_timeout_message() -> None:
    profile = ModelProfile(
        profile_id="deepseek:deepseek-v4-pro",
        display_name="DeepSeek deepseek-v4-pro",
        provider="deepseek",
        model_id="deepseek-v4-pro",
        api_key="sk-test",
    )

    detail = _ConnectionTestWorker._format_connection_detail(profile, "TimeoutError")

    assert "连接超时" in detail
    assert "供应商排队/拥塞" in detail


def test_connection_worker_formats_missing_openai_dependency_message() -> None:
    profile = ModelProfile(
        profile_id="deepseek:deepseek-v4-flash",
        display_name="DeepSeek deepseek-v4-flash",
        provider="deepseek",
        model_id="deepseek-v4-flash",
        api_key="sk-test",
    )

    detail = _ConnectionTestWorker._format_connection_detail(
        profile,
        "No module named 'openai'",
    )

    assert "缺少 openai 依赖" in detail
    assert ".venv/bin/python -m pip install -e" in detail


def test_settings_page_routing_choices_excludes_embedding_models(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    config = ProfilesConfig(
        profiles=[
            ModelProfile(
                profile_id="ollama:nomic",
                display_name="nomic-embed-text",
                provider="ollama",
                model_id="nomic-embed-text",
                api_key="",
            ),
            ModelProfile(
                profile_id="ollama:llama",
                display_name="llama3.2",
                provider="ollama",
                model_id="llama3.2",
                api_key="",
            ),
        ],
        default_profile_id="ollama:llama",
    )

    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: config)

    page = settings_page.SettingsPage()
    choices = {pid: (label, can_route) for pid, label, can_route in page._routing_profile_choices()}

    assert "ollama:nomic" not in choices
    assert choices["ollama:llama"][1] is True
    assert not choices["ollama:llama"][0].startswith("🔒")

    _cleanup_widget(page)


def test_settings_page_marks_element_progress_arbiter_as_low_frequency(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    monkeypatch.setattr(settings_page, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(settings_page, "load_or_import_profiles", lambda _: _stub_config())

    page = settings_page.SettingsPage()
    row = page._route_rows["element_progress_arbiter"]
    assert row._meta_badge is not None
    assert row._meta_badge.text() == "低频触发"
    assert "灰区仲裁" in (row._meta_badge.toolTip() or "")

    _cleanup_widget(page)


def test_task_route_row_fallback_capabilities_follow_primary_and_model_limits(
    qapp: QApplication,
) -> None:
    # Fallback models' thinking/multi-turn are now based solely on their own
    # detected capabilities; the primary model's capabilities no longer gate them.
    profiles = [
        ("fb1", "Fallback 1", True),
        ("fb2", "Fallback 2", True),
        ("fb3", "Fallback 3", True),
        ("main", "Main Model", True),
    ]
    route = TaskRouteEntry(profile_id="main", thinking=False, multi_turn=True)
    fallback_routes = [
        TaskRouteEntry(profile_id="fb1", thinking=True, multi_turn=True),
        TaskRouteEntry(profile_id="fb2", thinking=True, multi_turn=True),
    ]
    profile_configs = [
        ModelProfile(
            profile_id="main",
            display_name="Main Model",
            provider="tongyi",
            model_id="qwen-max",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb1",
            display_name="Fallback 1",
            provider="tongyi",
            model_id="qwen-plus",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb2",
            display_name="Fallback 2",
            provider="deepseek",
            model_id="deepseek-chat",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb3",
            display_name="Fallback 3",
            provider="tongyi",
            model_id="qwen-turbo",
            api_key="sk",
        ),
    ]

    row = _TaskRouteRow(
        "draft_chapter",
        "章节写作",
        "测试",
        profiles,
        route,
        fallback_routes,
        show_multi_turn=True,
        profile_configs=profile_configs,
        detected_capabilities={
            "main": (False, True),  # main does NOT support thinking
            "fb1": (True, True),  # fb1 supports thinking AND multi-turn
            "fb2": (True, False),  # fb2 supports thinking, NOT multi-turn
            "fb3": (True, True),
        },
    )

    routes = row.get_fallback_routes()
    assert routes[0].profile_id == "fb1"
    # fb1 (tongyi / qwen-plus) supports thinking → checkbox enabled, user chose thinking=True
    assert routes[0].thinking is True
    assert routes[0].multi_turn is True
    assert routes[1].profile_id == "fb2"
    # fb2 (deepseek / deepseek-chat) does NOT support thinking per static capability
    # map → checkbox disabled and force-unchecked regardless of user setting
    assert routes[1].thinking is False
    # fb2 (deepseek-chat) DOES support multi-turn per static capability → preserved
    assert routes[1].multi_turn is True

    _cleanup_widget(row)


def test_task_route_row_fallback_routes_are_capped_to_top_three(
    qapp: QApplication,
) -> None:
    profiles = [
        ("fb1", "Fallback 1", True),
        ("fb2", "Fallback 2", True),
        ("fb3", "Fallback 3", True),
        ("fb4", "Fallback 4", True),
        ("main", "Main Model", True),
    ]
    route = TaskRouteEntry(profile_id="main", thinking=False, multi_turn=False)
    fallback_routes = [
        TaskRouteEntry(profile_id="fb1", thinking=True, multi_turn=False),
        TaskRouteEntry(profile_id="fb2", thinking=False, multi_turn=True),
        TaskRouteEntry(profile_id="fb3", thinking=False, multi_turn=False),
        TaskRouteEntry(profile_id="fb4", thinking=True, multi_turn=True),
    ]
    profile_configs = [
        ModelProfile(
            profile_id="main",
            display_name="Main Model",
            provider="tongyi",
            model_id="qwen-max",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb1",
            display_name="Fallback 1",
            provider="tongyi",
            model_id="qwen-plus",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb2",
            display_name="Fallback 2",
            provider="deepseek",
            model_id="deepseek-chat",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb3",
            display_name="Fallback 3",
            provider="tongyi",
            model_id="qwen-turbo",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb4",
            display_name="Fallback 4",
            provider="tongyi",
            model_id="qwen-long",
            api_key="sk",
        ),
    ]

    row = _TaskRouteRow(
        "draft_chapter",
        "章节写作",
        "测试",
        profiles,
        route,
        fallback_routes,
        show_multi_turn=True,
        profile_configs=profile_configs,
        detected_capabilities={
            "main": (True, True),
            "fb1": (True, True),
            "fb2": (True, True),
            "fb3": (True, True),
            "fb4": (True, True),
        },
    )

    routes = row.get_fallback_routes()
    assert [entry.profile_id for entry in routes] == ["fb1", "fb2", "fb3"]
    assert len(routes) == 3

    _cleanup_widget(row)


def test_task_route_row_fallback_button_displays_compact_chain(
    qapp: QApplication,
) -> None:
    profiles = [
        ("fb1", "Fallback Model Alpha LongName", True),
        ("fb2", "Fallback Model Beta LongName", True),
        ("main", "Main Model", True),
    ]
    route = TaskRouteEntry(profile_id="main", thinking=False, multi_turn=False)
    fallback_routes = [
        TaskRouteEntry(profile_id="fb1", thinking=False, multi_turn=False),
        TaskRouteEntry(profile_id="fb2", thinking=False, multi_turn=False),
    ]
    profile_configs = [
        ModelProfile(
            profile_id="main",
            display_name="Main Model",
            provider="tongyi",
            model_id="qwen-max",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb1",
            display_name="Fallback Model Alpha LongName",
            provider="tongyi",
            model_id="qwen-plus",
            api_key="sk",
        ),
        ModelProfile(
            profile_id="fb2",
            display_name="Fallback Model Beta LongName",
            provider="deepseek",
            model_id="deepseek-chat",
            api_key="sk",
        ),
    ]

    row = _TaskRouteRow(
        "draft_chapter",
        "章节写作",
        "测试",
        profiles,
        route,
        fallback_routes,
        show_multi_turn=True,
        profile_configs=profile_configs,
        detected_capabilities={
            "main": (True, True),
            "fb1": (True, True),
            "fb2": (True, True),
        },
    )

    text = row._fallback_btn.text()
    assert text.startswith("已配置：")
    assert "→" in text
    # ▼ is now a separate right-pinned label, not part of the text string

    _cleanup_widget(row)


def test_task_route_row_compacts_verbose_hint_but_keeps_full_tooltip(
    qapp: QApplication,
) -> None:
    profiles = [("main", "Main Model", True)]
    route = TaskRouteEntry(profile_id="main", thinking=False, multi_turn=False)
    profile_configs = [
        ModelProfile(
            profile_id="main",
            display_name="Main Model",
            provider="tongyi",
            model_id="qwen-max",
            api_key="sk",
        )
    ]
    hint = "StoryBible 分片：确立核心冲突、题名、基调与主承诺；温度沿用世界观设定"

    row = _TaskRouteRow(
        "init_story_core_premise",
        "故事核心前提",
        hint,
        profiles,
        route,
        [],
        show_multi_turn=False,
        profile_configs=profile_configs,
        detected_capabilities={"main": (True, True)},
    )

    assert row._hint_label.text() != hint
    assert "温度沿用" not in row._hint_label.text()
    assert "\n" not in row._hint_label.text()
    assert hint in row._hint_label.toolTip()

    _cleanup_widget(row)
