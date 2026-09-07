"""Type-only host contract for settings-page mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QVBoxLayout

    from novel_forge.desktop.widgets import ScrollPage
    from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig

    class SettingsPageMixinBase(ScrollPage):
        """Static contract for attributes supplied by ``SettingsPage``."""

        mock_mode_toggled: Any
        _ConnectionTestWorker: Any
        _OllamaEngineViewWorker: Any
        _ModelDialog: Any
        _ModelManageCard: Any
        _ModelStatusCard: Any
        _RuntimeCard: Any
        _TaskRouteRow: Any
        _GroupBulkRow: Any
        _config: ProfilesConfig
        _settings: Any
        _store: Any
        _param_widgets: dict[str, Any]
        _route_rows: dict[str, Any]
        _group_bulk_rows: dict[str, Any]
        _bulk_route_task_keys: dict[str, list[str]]
        _models_list_layout: QVBoxLayout
        _default_model_combo: QComboBox
        _model_status_grid: QGridLayout
        _status_cards: dict[str, Any]
        _detected_capabilities: dict[str, tuple[bool, bool]]
        _status_tests_started: bool
        _status_tests_pending: bool
        _status_auto_activation_active: bool
        _status_test_queue: list[tuple[int, Any, bool, bool]]
        _status_tests_active: int
        _status_test_generation: int
        _status_test_next_token: int
        _status_test_running: dict[int, tuple[str, int | None, bool, float]]
        _status_test_ignored: set[int]
        _status_test_watchdog_timer: QTimer
        _status_test_start_timer: QTimer
        _status_grid_build_timer: QTimer
        _status_grid_build_profiles: list[ModelProfile]
        _status_grid_build_cursor: int
        _status_grid_build_complete: bool
        _status_grid_has_pending_tests: bool
        _status_grid_auto_profile_ids: set[str] | None
        _test_workers: list[Any]
        _ollama_workers: list[Any]
        _ollama_refresh_started: bool
        _refresh_combos_timer: QTimer
        _memory_embedding_profile: QComboBox
        _memory_vector_store_backend: QComboBox
        _memory_zvec_index_type: QComboBox

        _ollama_status_badge: Any
        _ollama_runtime_badge: Any
        _ollama_status_label: QLabel
        _ollama_runtime_label: QLabel
        _ollama_storage_label: QLabel
        _ollama_models_layout: QVBoxLayout
        _ollama_pull_input: Any
        _ollama_pick_menu: Any
        _ollama_pull_pick_btn: Any
        _ollama_pull_btn: Any
        _ollama_pull_progress: Any
        _ollama_pull_status: QLabel
        _on_ollama_view_loaded: Any
        _on_ollama_pull_progress: Any

        def _refresh_models_list(self) -> None: ...
        def _refresh_default_model_combo(self) -> None: ...
        def _refresh_model_status_grid(self, *, deferred: bool = False) -> None: ...
        def ensure_status_grid_built(self) -> None: ...
        def _refresh_route_combos(self) -> None: ...
        def _routing_profile_choices(self) -> list[tuple[str, str, bool]]: ...
        def _embedding_profile_choices(self) -> list[tuple[str, str]]: ...
        def _refresh_embedding_combo(self) -> None: ...
        def _refresh_ollama_models(self) -> None: ...
        def _init_ollama_runtime_panel(self) -> None: ...
        def _ignore_active_status_test_tokens(self) -> None: ...
        def _on_auto_test_done(
            self,
            profile_id: str,
            success: bool,
            detail: str,
            can_thinking: bool,
            can_multi_turn: bool,
            generation: int | None = None,
            token: int | None = None,
        ) -> None: ...
        def _on_manual_test_done(
            self,
            profile_id: str,
            success: bool,
            detail: str,
            can_thinking: bool,
            can_multi_turn: bool,
            token: int | None = None,
        ) -> None: ...
        def _on_status_test_watchdog(self) -> None: ...
        def _on_status_test_probe_started(
            self,
            profile_id: str,
            generation: int | None,
            token: int | None,
            *,
            auto: bool,
        ) -> None: ...
        def _schedule_pending_status_tests(self) -> None: ...

else:

    class SettingsPageMixinBase:
        """Runtime marker base; no attributes to keep Qt lookup untouched."""

        pass


__all__ = ["SettingsPageMixinBase"]
