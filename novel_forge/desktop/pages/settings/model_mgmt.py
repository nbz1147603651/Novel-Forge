"""Model management mixin for the Settings page."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLabel

from novel_forge.core.task_catalog import ROUTING_GROUPS
from novel_forge.desktop.components.dialogs import (
    ask_confirmation,
    show_info_message,
    show_warning_message,
)
from novel_forge.desktop.pages._page_utils import safe_disconnect
from novel_forge.desktop.ui_perf import ui_perf_span
from novel_forge.desktop.widgets import add_card_grid, clear_layout
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import ModelProfile, TaskRouteEntry

from .contract import SettingsPageMixinBase

if TYPE_CHECKING:
    pass


class ModelManagementMixin(SettingsPageMixinBase):
    """Mixin providing model management, routing, and connection test methods."""

    def _show_model_status_loading(self) -> None:
        """Show the shared loading state while a status batch is in flight."""
        loading = getattr(self, "_model_status_loading", None)
        stack = getattr(self, "_model_status_stack", None)
        if loading is not None:
            loading.start()
        if stack is not None and loading is not None:
            stack.setCurrentWidget(loading)

    def _show_model_status_results(self) -> None:
        """Reveal the already-populated status grid in one stable update."""
        loading = getattr(self, "_model_status_loading", None)
        stack = getattr(self, "_model_status_stack", None)
        grid_host = getattr(self, "_model_status_grid_host", None)
        if loading is not None:
            loading.stop()
        if stack is not None and grid_host is not None:
            stack.setCurrentWidget(grid_host)

    def prewarm_model_cache(self) -> None:
        """在应用启动时后台预热模型连接状态缓存，消除首次切换卡顿。

        此方法会在后台线程中异步检测所有已配置模型的连通性，
        将结果存入 _ConnectionTestWorker._result_cache。
        当用户首次切换到火候页面时，可以直接使用缓存结果，无需等待。

        注意：每个模型通常在不同平台，无需限制并发数。
        """
        if getattr(self, "_shutdown_done", False):
            return
        # Skip prewarm if the page is not visible (e.g. during tests or
        # after the user navigated away). Prevents stale timer callbacks
        # from polluting other test instances' state.
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return
        if not hasattr(self, "_config") or not self._config.profiles:
            return

        # 只预加载已配置 API Key 的模型
        profiles_to_warm = [p for p in self._config.profiles if p.is_key_configured]

        if not profiles_to_warm:
            return

        # 所有模型同时启动预热（不同平台无并发限制顾虑）
        for profile in profiles_to_warm:
            worker = self._ConnectionTestWorker(
                profile,
                force_refresh=True,
            )

            def _forget_prewarm(
                _profile_id: str,
                _ok: bool,
                _detail: str,
                _thinking: bool,
                _multi_turn: bool,
                *,
                worker_ref: Any = worker,
            ) -> None:
                try:
                    self._test_workers.remove(worker_ref)
                except ValueError:
                    pass

            worker.signals.finished.connect(_forget_prewarm)
            self._test_workers.append(worker)
            try:
                worker.start()
            except Exception:
                try:
                    self._test_workers.remove(worker)
                except ValueError:
                    pass
                raise

    def _on_group_bulk_apply(self, group_key: str) -> None:
        bulk_row = self._group_bulk_rows.get(group_key)
        if bulk_row is None:
            return
        route = bulk_row.get_bulk_route()
        fallbacks = bulk_row.get_bulk_fallback_routes()
        if route is None:
            return
        task_keys = self._bulk_route_task_keys.get(group_key)
        if task_keys is None:
            group = next((g for g in ROUTING_GROUPS if g.name == group_key), None)
            if group is None:
                return
            task_keys = [task.key for task in group.tasks]
        self.setUpdatesEnabled(False)
        try:
            for task_key in task_keys:
                task_route = TaskRouteEntry(
                    profile_id=route.profile_id,
                    thinking=route.thinking,
                    thinking_mode=route.thinking_mode,
                    multi_turn=route.multi_turn,
                )
                self._config.routes[task_key] = task_route
                if fallbacks:
                    self._config.fallback_routes[task_key] = list(fallbacks)
                else:
                    self._config.fallback_routes.pop(task_key, None)
                row = self._route_rows.get(task_key)
                if row is not None:
                    row.set_model_and_flags(
                        route.profile_id,
                        route.thinking,
                        route.multi_turn,
                        list(fallbacks),
                        thinking_mode=route.thinking_mode,
                    )
        finally:
            self.setUpdatesEnabled(True)
            self.update()
        self._config.group_bulk_routes[group_key] = {
            "profile_id": route.profile_id,
            "thinking": route.thinking,
            "thinking_mode": route.thinking_mode,
            "multi_turn": route.multi_turn,
            "fallback_routes": [asdict(e) for e in fallbacks],
        }

    def _refresh_models_list(self) -> None:
        clear_layout(self._models_list_layout)
        if not self._config.profiles:
            empty = QLabel("尚无模型入库。点击下方「添加模型」开始布置通路。")
            empty.setObjectName("emptyMessage")
            empty.setWordWrap(True)
            self._models_list_layout.addWidget(empty)
            self._refresh_default_model_combo()
            return
        for profile in self._config.profiles:
            card = self._ModelManageCard(profile)
            card.edit_clicked.connect(self._edit_model)
            card.delete_clicked.connect(self._delete_model)
            card.test_clicked.connect(self._test_model)
            self._models_list_layout.addWidget(card)
        self._refresh_default_model_combo()

    def _refresh_default_model_combo(self) -> None:
        combo = self._default_model_combo
        combo.blockSignals(True)
        current_id = self._config.default_profile_id
        combo.clear()
        for p in self._config.profiles:
            if is_embedding_model(p.provider, p.model_id):
                continue
            combo.addItem(p.display_name, p.profile_id)
        idx = combo.findData(current_id)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)
            self._config.default_profile_id = combo.currentData() or ""
        combo.blockSignals(False)

    def _on_default_model_changed(self) -> None:
        profile_id = self._default_model_combo.currentData() or ""
        if profile_id:
            self._config.default_profile_id = profile_id

    def _apply_cached_status_result(
        self,
        profile: ModelProfile,
        card: Any | None,
    ) -> bool:
        cached = self._ConnectionTestWorker.cached_result(profile.profile_id)
        if cached is None:
            return False
        success, detail, can_thinking, can_multi_turn = cached
        effective_capabilities = (
            can_thinking if success else False,
            can_multi_turn if success else False,
        )
        if card:
            card.set_cached_result(success, detail)
            card.set_capabilities(*effective_capabilities)
        self._detected_capabilities[profile.profile_id] = effective_capabilities
        return True

    def _apply_stale_cached_status_result(
        self,
        profile: ModelProfile,
        card: Any | None,
        *,
        background_refresh: bool = True,
    ) -> bool:
        cached = self._ConnectionTestWorker.stale_cached_result(profile.profile_id)
        if cached is None:
            return False
        success, detail, can_thinking, can_multi_turn = cached
        effective_capabilities = (
            can_thinking if success else False,
            can_multi_turn if success else False,
        )
        if card:
            if background_refresh:
                card.set_stale_cached_result(success, detail)
            else:
                card.set_cached_result(success, f"上次{detail}，待手动复测")
            card.set_capabilities(*effective_capabilities)
        self._detected_capabilities[profile.profile_id] = effective_capabilities
        return True

    def _apply_unconfigured_status(self, profile: ModelProfile, card: Any | None) -> None:
        if card:
            card.set_cached_result(False, "未配置 API Key")
            card.set_capabilities(False, False)
        self._detected_capabilities[profile.profile_id] = (False, False)

    def _auto_status_profile_ids(self) -> set[str] | None:
        limit = int(getattr(self, "_AUTO_STATUS_TEST_MAX_PROFILES", 0) or 0)
        if limit <= 0:
            return None
        if int(getattr(self, "_MAX_STATUS_TEST_WORKERS", 1) or 0) <= 0:
            return None

        configured_profile_ids = {
            profile.profile_id for profile in self._config.profiles if profile.is_key_configured
        }
        selected: list[str] = []

        def add(profile_id: object) -> None:
            pid = str(profile_id or "").strip()
            if pid in configured_profile_ids and pid not in selected:
                selected.append(pid)

        add(getattr(self._config, "default_profile_id", ""))
        for route in self._config.routes.values():
            add(route.profile_id)
        for entries in self._config.fallback_routes.values():
            for entry in entries:
                add(entry.profile_id)
        for raw in self._config.group_bulk_routes.values():
            if not isinstance(raw, dict):
                continue
            add(raw.get("profile_id"))
            fallback_routes = raw.get("fallback_routes")
            if isinstance(fallback_routes, list):
                for entry in fallback_routes:
                    if isinstance(entry, dict):
                        add(entry.get("profile_id"))
        for profile in self._config.profiles:
            if profile.is_key_configured:
                add(profile.profile_id)
            if len(selected) >= limit:
                break
        return set(selected[:limit])

    def _build_model_status_card(
        self,
        profile: ModelProfile,
        auto_profile_ids: set[str] | None,
    ) -> tuple[Any, bool]:
        card = self._ModelStatusCard(profile)
        self._status_cards[profile.profile_id] = card
        if not profile.is_key_configured:
            self._apply_unconfigured_status(profile, card)
            return card, False
        if self._apply_cached_status_result(profile, card):
            return card, False
        auto_enabled = auto_profile_ids is None or profile.profile_id in auto_profile_ids
        if not auto_enabled:
            self._apply_stale_cached_status_result(profile, card, background_refresh=False)
            return card, False
        self._apply_stale_cached_status_result(profile, card)
        return card, True

    def _reset_status_grid_build(self) -> None:
        if self._status_grid_build_timer is not None:
            self._status_grid_build_timer.stop()
        self._status_grid_build_profiles = []
        self._status_grid_build_cursor = 0
        self._status_grid_has_pending_tests = False
        self._status_grid_auto_profile_ids = None

    def _finish_status_grid_build(self) -> None:
        if self._status_grid_build_timer is not None:
            self._status_grid_build_timer.stop()
        self._status_grid_build_profiles = []
        self._status_grid_build_cursor = 0
        self._status_grid_build_complete = True
        self._status_tests_pending = self._status_grid_has_pending_tests
        self._status_grid_has_pending_tests = False
        self._status_grid_auto_profile_ids = None
        if (
            self._status_tests_started
            and self._status_tests_pending
            and self._status_auto_activation_active
        ):
            self._schedule_status_tests_after_grid_ready()
        elif not self._status_tests_pending:
            # Cached and unconfigured profiles have no worker callback that
            # could reveal the grid later. Do it now instead of leaving the
            # shared loading state mounted forever.
            self._show_model_status_results()

    def _add_status_card_batch(self, *, max_cards: int | None = None) -> bool:
        profiles = self._status_grid_build_profiles
        if not profiles:
            return True
        if self._status_grid_build_cursor == 0:
            clear_layout(self._model_status_grid)

        start = self._status_grid_build_cursor
        stop = len(profiles) if max_cards is None else min(len(profiles), start + max_cards)
        auto_profile_ids = self._status_grid_auto_profile_ids

        grid_widget = (
            self._model_status_grid.parentWidget()
            if hasattr(self._model_status_grid, "parentWidget")
            else None
        )
        if grid_widget:
            grid_widget.setUpdatesEnabled(False)

        try:
            for index in range(start, stop):
                card, has_pending_test = self._build_model_status_card(
                    profiles[index],
                    auto_profile_ids,
                )
                self._status_grid_has_pending_tests = (
                    self._status_grid_has_pending_tests or has_pending_test
                )
                self._model_status_grid.addWidget(card, index // 3, index % 3)
        finally:
            if grid_widget:
                grid_widget.setUpdatesEnabled(True)

        self._status_grid_build_cursor = stop
        return self._status_grid_build_cursor >= len(profiles)

    def _build_next_status_card_batch(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        if self._status_grid_build_complete:
            return
        try:
            if not self.isVisible():
                return
        except RuntimeError:
            return
        with ui_perf_span("settings_status_grid_batch"):
            done = self._add_status_card_batch(
                max_cards=int(getattr(self, "_STATUS_GRID_BUILD_BATCH_SIZE", 4) or 4)
            )
        if done:
            self._finish_status_grid_build()
        else:
            self._ensure_status_grid_build_timer().start(
                int(getattr(self, "_STATUS_GRID_BUILD_INTERVAL_MS", 16) or 16)
            )

    def ensure_status_grid_built(self) -> None:
        # Ensure deferred hero/grid exist before we try to populate cards
        ensure_built = getattr(self, "_ensure_hero_and_grid_built", None)
        if callable(ensure_built):
            ensure_built()
        if self._status_grid_build_complete:
            return
        if self._status_grid_build_timer is not None:
            self._status_grid_build_timer.stop()
        with ui_perf_span("settings_status_grid_build_all"):
            self._add_status_card_batch(max_cards=None)
        self._finish_status_grid_build()

    def _refresh_model_status_grid(self, *, deferred: bool = False) -> None:
        self._show_model_status_loading()
        self._reset_status_grid_build()
        self._status_grid_build_complete = False
        self._status_test_generation += 1
        self._status_test_queue = []
        self._status_tests_active = 0
        self._ignore_active_status_test_tokens()
        self._status_test_running = {}
        if self._status_test_start_timer is not None:
            self._status_test_start_timer.stop()
        if self._status_test_watchdog_timer is not None:
            self._status_test_watchdog_timer.stop()
        self._status_cards = {}
        self._detected_capabilities = {}
        self._prune_test_workers()
        profiles = list(self._config.profiles)
        auto_profile_ids = self._auto_status_profile_ids()
        if not profiles:
            clear_layout(self._model_status_grid)
            empty = QLabel("通路尚未开辟。请先在上方「模型管理」中添加模型。")
            empty.setObjectName("emptyMessage")
            self._model_status_grid.addWidget(empty, 0, 0)
            self._status_tests_pending = False
            self._status_grid_build_complete = True
            self._show_model_status_results()
            return

        if deferred:
            # 关键优化：先显示骨架屏，再异步构建真实卡片
            # 这样可以立即响应用户切换操作，避免卡顿
            clear_layout(self._model_status_grid)

            # 添加骨架屏提示
            placeholder = QLabel("正在准备模型连接状态…")
            placeholder.setObjectName("emptyMessage")
            self._model_status_grid.addWidget(placeholder, 0, 0)

            self._status_grid_build_profiles = profiles
            self._status_grid_build_cursor = 0
            self._status_grid_has_pending_tests = False
            self._status_grid_auto_profile_ids = auto_profile_ids
            self._status_tests_pending = False

            # 等待 activate() 调用后再开始构建
            # 这样可以避免在页面隐藏时浪费资源
            return

        cards = []
        has_pending_tests = False
        for profile in profiles:
            card, has_pending_test = self._build_model_status_card(profile, auto_profile_ids)
            has_pending_tests = has_pending_tests or has_pending_test
            cards.append(card)
        add_card_grid(self._model_status_grid, cards, columns=3)
        self._status_grid_has_pending_tests = has_pending_tests
        self._finish_status_grid_build()

    def activate(self) -> None:
        """Activate model status work after the settings page has painted."""
        # Ensure deferred hero/grid are built before we access _model_status_grid
        ensure_built = getattr(self, "_ensure_hero_and_grid_built", None)
        if callable(ensure_built):
            ensure_built()
        self._status_tests_started = True
        self._status_auto_activation_active = True
        if not self._status_grid_build_complete:
            # 关键优化：立即开始构建，不再等待延迟
            # 因为 deferred 模式下已经在 _refresh_model_status_grid 中启动了 QTimer.singleShot(0, ...)
            # 这里只需要确保定时器没有被停止即可
            grid_timer = self._status_grid_build_timer
            if grid_timer is not None and not grid_timer.isActive():
                grid_timer.start(0)
            elif grid_timer is None:
                self._ensure_status_grid_build_timer().start(0)
        elif self._status_tests_pending:
            self._schedule_status_tests_after_grid_ready()
        else:
            # Cached results and unconfigured profiles need no background
            # probe, so the finished grid can be displayed immediately.
            self._show_model_status_results()

        if not self._ollama_refresh_started and hasattr(self, "_ollama_status_badge"):
            self._ollama_refresh_started = True
            self._refresh_ollama_models()

    def _cancel_pending_auto_status_tests(self) -> None:
        """Cancel scheduled automatic status checks when the page is no longer current."""
        self._status_auto_activation_active = False
        if self._status_test_start_timer is not None:
            self._status_test_start_timer.stop()
        if not self._status_grid_build_complete and self._status_grid_build_timer is not None:
            self._status_grid_build_timer.stop()

    def shutdown(self) -> None:
        self._cancel_pending_auto_status_tests()
        self._status_tests_pending = False
        self._status_test_generation += 1
        self._status_test_queue = []
        self._status_tests_active = 0
        self._ignore_active_status_test_tokens()
        self._status_test_running = {}
        if self._status_test_start_timer is not None:
            self._status_test_start_timer.stop()
        if self._status_test_watchdog_timer is not None:
            self._status_test_watchdog_timer.stop()
        if self._refresh_combos_timer is not None:
            self._refresh_combos_timer.stop()

        mock_cb = self._param_widgets.get("_mock_cb")
        if mock_cb:
            safe_disconnect(mock_cb.toggled, self.mock_mode_toggled.emit)

        for worker in self._test_workers:
            worker.request_cancel()
            try:
                worker.signals.probe_started.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                worker.signals.finished.disconnect(self._on_auto_test_done)
            except (RuntimeError, TypeError):
                pass
        self._test_workers.clear()
        for worker in self._ollama_workers:
            worker.request_cancel()
            try:
                signal = getattr(worker.signals, "loaded", None)
                if signal is not None:
                    signal.disconnect(self._on_ollama_view_loaded)
            except (RuntimeError, TypeError):
                pass
        self._ollama_workers.clear()

    def _next_status_test_token(self) -> int:
        self._status_test_next_token += 1
        return self._status_test_next_token

    def _register_status_test(
        self,
        token: int,
        profile_id: str,
        generation: int | None,
        *,
        auto: bool,
    ) -> None:
        self._status_test_running[token] = (profile_id, generation, auto, time.monotonic())
        watchdog = self._ensure_status_test_watchdog_timer()
        if not watchdog.isActive():
            watchdog.start()

    def _ignore_active_status_test_tokens(self) -> None:
        self._status_test_ignored.update(self._status_test_running)
        for worker in self._test_workers:
            token = getattr(worker, "_status_test_token", None)
            if token is not None:
                self._status_test_ignored.add(token)

    def _on_status_test_probe_started(
        self,
        profile_id: str,
        generation: int | None,
        token: int | None,
        *,
        auto: bool,
    ) -> None:
        if token is None:
            return
        if getattr(self, "_shutdown_done", False):
            return
        if generation is not None and generation != self._status_test_generation:
            return
        if token in self._status_test_ignored:
            return
        self._register_status_test(token, profile_id, generation, auto=auto)

    def _complete_status_test_token(self, token: int | None) -> bool:
        if token is None:
            return False
        self._status_test_running.pop(token, None)
        if token in self._status_test_ignored:
            self._status_test_ignored.discard(token)
            if not self._status_test_running and self._status_test_watchdog_timer is not None:
                self._status_test_watchdog_timer.stop()
            return True
        if not self._status_test_running and self._status_test_watchdog_timer is not None:
            self._status_test_watchdog_timer.stop()
        return False

    def _on_status_test_watchdog(self) -> None:
        timeout_s = float(getattr(self._ConnectionTestWorker, "_TIMEOUT_SECONDS", 60.0)) + 5.0
        now = time.monotonic()
        expired_tokens = [
            token
            for token, (
                _profile_id,
                _generation,
                _auto,
                started_at,
            ) in self._status_test_running.items()
            if now - started_at >= timeout_s
        ]
        if not expired_tokens:
            if not self._status_test_running and self._status_test_watchdog_timer is not None:
                self._status_test_watchdog_timer.stop()
            return

        for token in expired_tokens:
            profile_id, generation, is_auto, _started_at = self._status_test_running.pop(token)
            self._status_test_ignored.add(token)
            if generation is not None and generation != self._status_test_generation:
                continue
            if is_auto:
                self._status_tests_active = max(0, self._status_tests_active - 1)
            card = self._status_cards.get(profile_id)
            detail = (
                f"检测超时：{int(timeout_s)}s 未返回，已停止等待。"
                "请稍后重试，或检查网络、代理与供应商状态。"
            )
            if card:
                card.set_result(False, detail)
                card.set_capabilities(False, False)
            self._detected_capabilities[profile_id] = (False, False)

        self._schedule_route_combo_refresh_after_status_test()
        self._drain_status_test_queue()
        self._finish_status_detection_if_idle()
        if not self._status_test_running and self._status_test_watchdog_timer is not None:
            self._status_test_watchdog_timer.stop()

    def _test_all_connections(self) -> None:
        self._auto_test_connections(force_refresh=True)

    def _status_detection_has_work(self) -> bool:
        return self._status_tests_active > 0 or bool(self._status_test_queue)

    def _begin_status_detection_batch(self) -> None:
        self._status_detection_busy = True
        self._route_combos_refresh_pending = False
        if self._refresh_combos_timer is not None:
            self._refresh_combos_timer.stop()
        if getattr(self, "_deferred_build_timer", None) is not None:
            self._deferred_build_timer.stop()

    def _finish_status_detection_if_idle(self) -> None:
        if self._status_detection_has_work():
            return
        self._status_detection_busy = False
        if self._route_combos_refresh_pending:
            self._route_combos_refresh_pending = False
            self._ensure_refresh_combos_timer().start()
        if (
            not getattr(self, "_deferred_build_complete", True)
            and not self._deferred_build_timer.isActive()
        ):
            self._deferred_build_timer.start(500)
        # Cards update behind the loading state.  Reveal them only when the
        # queue has drained, avoiding a reflow for every probe completion.
        if self._status_grid_build_complete:
            self._show_model_status_results()

    def _schedule_route_combo_refresh_after_status_test(self) -> None:
        if self._status_detection_busy or self._status_detection_has_work():
            self._route_combos_refresh_pending = True
            return
        self._ensure_refresh_combos_timer().start()

    def _auto_test_connections(self, *, force_refresh: bool = False) -> None:
        self._prune_test_workers()
        if not force_refresh and not self._status_auto_activation_active:
            return
        if not self._status_grid_build_complete:
            if force_refresh:
                self.ensure_status_grid_built()
            else:
                self._status_tests_pending = True
                grid_t = self._ensure_status_grid_build_timer()
                if not grid_t.isActive():
                    grid_t.start(0)
                return
        if self._status_tests_active > 0 or self._status_test_queue:
            if not force_refresh:
                return
            self._status_test_generation += 1
            self._ignore_active_status_test_tokens()
            self._status_test_queue = []
            self._status_tests_active = 0
            self._status_test_running = {}
        else:
            self._status_test_generation += 1
        self._show_model_status_loading()
        self._begin_status_detection_batch()
        generation = self._status_test_generation
        self._status_tests_active = 0
        self._status_test_queue = []
        used_cached_result = False
        auto_profile_ids = None if force_refresh else self._auto_status_profile_ids()
        for profile in self._config.profiles:
            card = self._status_cards.get(profile.profile_id)
            show_pending = True
            if force_refresh:
                self._ConnectionTestWorker.clear_cached_result(profile.profile_id)
                self._detected_capabilities.pop(profile.profile_id, None)
            if not profile.is_key_configured:
                self._apply_unconfigured_status(profile, card)
                continue
            if not force_refresh and self._apply_cached_status_result(profile, card):
                used_cached_result = True
                continue
            auto_enabled = auto_profile_ids is None or profile.profile_id in auto_profile_ids
            if not force_refresh and not auto_enabled:
                if not self._apply_stale_cached_status_result(
                    profile,
                    card,
                    background_refresh=False,
                ):
                    if card:
                        card.set_queued("待手动检测")
                continue
            if not force_refresh and self._apply_stale_cached_status_result(profile, card):
                used_cached_result = True
                show_pending = False
            if card:
                if show_pending:
                    card.set_queued("等待检测…")
            self._status_test_queue.append((generation, profile, force_refresh, show_pending))
        self._status_tests_pending = False
        if used_cached_result:
            self._schedule_route_combo_refresh_after_status_test()
        self._drain_status_test_queue()
        self._finish_status_detection_if_idle()

    def _schedule_status_tests_after_grid_ready(self) -> None:
        auto_delay_ms = int(getattr(self, "_AUTO_STATUS_TEST_DELAY_MS", 0) or 0)
        delay_ms = (
            int(getattr(self, "_POST_STATUS_GRID_TEST_DELAY_MS", 0) or 0)
            if auto_delay_ms > 0
            else 0
        )
        if delay_ms <= 0:
            self._schedule_pending_status_tests()
            return
        QTimer.singleShot(delay_ms, self._schedule_pending_status_tests)

    def _schedule_pending_status_tests(self) -> None:
        if not self._status_tests_pending or not self._status_auto_activation_active:
            if self._status_test_start_timer is not None:
                self._status_test_start_timer.stop()
            return
        delay_ms = int(getattr(self, "_AUTO_STATUS_TEST_DELAY_MS", 0))
        if delay_ms <= 0:
            self._start_pending_status_tests()
            return
        timer = self._ensure_status_test_start_timer()
        if not timer.isActive():
            timer.start(delay_ms)

    def _start_pending_status_tests(self) -> None:
        if self._status_test_start_timer is not None:
            self._status_test_start_timer.stop()
        if not self._status_tests_pending or not self._status_auto_activation_active:
            return
        self._auto_test_connections()

    def _drain_status_test_queue(self) -> None:
        self._prune_test_workers()
        if not self._status_auto_activation_active:
            # Page is hidden: only drain manual (force_refresh) tests;
            # skip auto tests to avoid wasting resources while not visible.
            if not (self._status_test_queue and self._status_test_queue[0][2]):
                return
        generation = self._status_test_generation
        queued_force_refresh = bool(self._status_test_queue and self._status_test_queue[0][2])
        worker_attr = (
            "_MANUAL_STATUS_TEST_WORKERS" if queued_force_refresh else "_MAX_STATUS_TEST_WORKERS"
        )
        configured_workers = int(getattr(self, worker_attr, 3))
        if configured_workers <= 0:
            max_workers = self._status_tests_active + len(self._status_test_queue)
        else:
            max_workers = max(1, configured_workers)
        while self._status_tests_active < max_workers and self._status_test_queue:
            queued_generation, profile, force_refresh, show_pending = self._status_test_queue.pop(0)
            if queued_generation != generation:
                continue
            card = self._status_cards.get(profile.profile_id)
            if card and show_pending:
                card.set_pending("检测中…", flash=False)
            worker = self._ConnectionTestWorker(profile, force_refresh=force_refresh)
            worker._status_test_auto = True
            worker._status_test_generation = queued_generation
            token = self._next_status_test_token()
            worker._status_test_token = token
            self._register_status_test(
                token,
                profile.profile_id,
                queued_generation,
                auto=True,
            )

            def _handle_auto_done(
                profile_id: str,
                success: bool,
                detail: str,
                can_thinking: bool,
                can_multi_turn: bool,
                *,
                done_generation: int = queued_generation,
                done_token: int = token,
            ) -> None:
                self._on_auto_test_done(
                    profile_id,
                    success,
                    detail,
                    can_thinking,
                    can_multi_turn,
                    done_generation,
                    done_token,
                )

            def _handle_auto_probe_started(
                profile_id: str,
                *,
                started_generation: int = queued_generation,
                started_token: int = token,
            ) -> None:
                self._on_status_test_probe_started(
                    profile_id,
                    started_generation,
                    started_token,
                    auto=True,
                )

            worker.signals.probe_started.connect(_handle_auto_probe_started)
            worker.signals.finished.connect(_handle_auto_done)
            self._test_workers.append(worker)
            self._status_tests_active += 1
            try:
                worker.start()
            except Exception as exc:
                self._complete_status_test_token(token)
                self._status_tests_active = max(0, self._status_tests_active - 1)
                card = self._status_cards.get(profile.profile_id)
                detail = f"检测启动失败：{str(exc).strip() or type(exc).__name__}"
                if card:
                    card.set_background_result(False, detail)
                    card.set_capabilities(False, False)
                self._detected_capabilities[profile.profile_id] = (False, False)
                self._prune_test_workers()
                self._drain_status_test_queue()
                self._finish_status_detection_if_idle()

    def _on_auto_test_done(
        self,
        profile_id: str,
        success: bool,
        detail: str,
        can_thinking: bool,
        can_multi_turn: bool,
        generation: int | None = None,
        token: int | None = None,
    ) -> None:
        if self._complete_status_test_token(token):
            self._prune_test_workers()
            return
        if generation is not None and generation != self._status_test_generation:
            self._prune_test_workers()
            return
        self._status_tests_active = max(0, self._status_tests_active - 1)
        card = self._status_cards.get(profile_id)
        if card:
            card.set_background_result(success, detail)
            if success:
                card.set_capabilities(can_thinking, can_multi_turn)
            else:
                card.set_capabilities(False, False)
        self._detected_capabilities[profile_id] = (
            can_thinking if success else False,
            can_multi_turn if success else False,
        )
        self._schedule_route_combo_refresh_after_status_test()
        self._prune_test_workers()
        self._drain_status_test_queue()
        self._finish_status_detection_if_idle()

    def _refresh_route_combos(self) -> None:
        profiles = self._routing_profile_choices()
        configs = list(self._config.profiles)
        self.setUpdatesEnabled(False)
        try:
            for row in self._route_rows.values():
                row.refresh_profiles(
                    profiles,
                    profile_configs=configs,
                    detected_capabilities=self._detected_capabilities,
                )
            for bulk_row in self._group_bulk_rows.values():
                bulk_row.refresh_profiles(
                    profiles,
                    profile_configs=configs,
                    detected_capabilities=self._detected_capabilities,
                )
        finally:
            self.setUpdatesEnabled(True)
            self.update()

    def _routing_profile_choices(self) -> list[tuple[str, str, bool]]:
        choices: list[tuple[str, str, bool]] = []
        for p in self._config.profiles:
            if is_embedding_model(p.provider, p.model_id):
                continue
            elif not p.is_key_configured:
                label = f"🔒 {p.display_name}"
                can_route = False
            else:
                label = p.display_name
                can_route = True
            choices.append((p.profile_id, label, can_route))
        return choices

    def _embedding_profile_choices(self) -> list[tuple[str, str]]:
        choices: list[tuple[str, str]] = [("auto", "🔄 自动选择（推荐）")]
        for p in self._config.profiles:
            if is_embedding_model(p.provider, p.model_id):
                label = f"📊 {p.display_name}"
                choices.append((p.profile_id, label))
        return choices

    def _refresh_embedding_combo(self) -> None:
        if not hasattr(self, "_memory_embedding_profile"):
            return
        choices = self._embedding_profile_choices()
        current = self._memory_embedding_profile.currentData() or "auto"
        self._memory_embedding_profile.clear()
        for value, label in choices:
            self._memory_embedding_profile.addItem(label, value)
        if current in [c[0] for c in choices]:
            idx = self._memory_embedding_profile.findData(current)
            if idx >= 0:
                self._memory_embedding_profile.setCurrentIndex(idx)

    def _add_model(self) -> None:
        existing_ids = [p.profile_id for p in self._config.profiles]
        dialog = self._ModelDialog(existing_ids=existing_ids, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.get_profile()
        if profile is None:
            return
        self._config.add_profile(profile)
        self._ConnectionTestWorker.clear_cached_result(profile.profile_id)
        self._detected_capabilities.pop(profile.profile_id, None)
        self._refresh_models_list()
        self._refresh_model_status_grid()
        self._refresh_route_combos()

    def _edit_model(self, profile_id: str) -> None:
        profile = self._config.get_profile(profile_id)
        if profile is None:
            return
        dialog = self._ModelDialog(profile=profile, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_profile = dialog.get_profile()
        if new_profile is None:
            return
        self._ConnectionTestWorker.clear_cached_result(profile_id)
        self._ConnectionTestWorker.clear_cached_result(new_profile.profile_id)
        self._detected_capabilities.pop(profile_id, None)
        self._detected_capabilities.pop(new_profile.profile_id, None)
        if new_profile.profile_id != profile_id:
            for task_key, entry in list(self._config.routes.items()):
                if entry.profile_id == profile_id:
                    self._config.routes[task_key] = TaskRouteEntry(
                        profile_id=new_profile.profile_id,
                        thinking=entry.thinking,
                        thinking_mode=entry.thinking_mode,
                        multi_turn=entry.multi_turn,
                    )
            for task_key, entries in list(self._config.fallback_routes.items()):
                updated: list[TaskRouteEntry] = []
                for entry in entries:
                    if entry.profile_id == profile_id:
                        updated.append(
                            TaskRouteEntry(
                                profile_id=new_profile.profile_id,
                                thinking=entry.thinking,
                                thinking_mode=entry.thinking_mode,
                                multi_turn=entry.multi_turn,
                            )
                        )
                    else:
                        updated.append(entry)
                self._config.fallback_routes[task_key] = updated[:3]
            self._config.remove_profile(profile_id)
        self._config.add_profile(new_profile)
        self._refresh_models_list()
        self._refresh_model_status_grid()
        self._refresh_route_combos()

    def _delete_model(self, profile_id: str) -> None:
        profile = self._config.get_profile(profile_id)
        if profile is None:
            return
        if not ask_confirmation(
            self,
            "确认删除",
            f"确定删除模型「{profile.display_name}」？",
            informative_text="使用此模型的流程路由将被清除。",
            confirm_text="确定",
            cancel_text="取消",
            confirm_variant="danger",
        ):
            return
        self._config.remove_profile(profile_id)
        self._ConnectionTestWorker.clear_cached_result(profile_id)
        self._detected_capabilities.pop(profile_id, None)
        self._refresh_models_list()
        self._refresh_model_status_grid()
        self._refresh_route_combos()

    def _test_model(self, profile_id: str) -> None:
        profile = self._config.get_profile(profile_id)
        if profile is None:
            return
        if not profile.is_key_configured:
            show_warning_message(
                self,
                "无法测试",
                f"模型「{profile.display_name}」未配置 API Key。",
            )
            return
        self._status_test_queue = [
            item for item in self._status_test_queue if item[1].profile_id != profile.profile_id
        ]
        card = self._status_cards.get(profile.profile_id)
        if card:
            card.set_pending("检测中…")
        self._ConnectionTestWorker.clear_cached_result(profile.profile_id)
        worker = self._ConnectionTestWorker(profile, force_refresh=True)
        token = self._next_status_test_token()
        worker._status_test_token = token
        self._register_status_test(token, profile.profile_id, None, auto=False)

        def _handle_manual_done(
            done_profile_id: str,
            success: bool,
            detail: str,
            can_thinking: bool,
            can_multi_turn: bool,
            *,
            done_token: int = token,
        ) -> None:
            self._on_manual_test_done(
                done_profile_id,
                success,
                detail,
                can_thinking,
                can_multi_turn,
                done_token,
            )

        def _handle_manual_probe_started(
            done_profile_id: str,
            *,
            done_token: int = token,
        ) -> None:
            self._on_status_test_probe_started(
                done_profile_id,
                None,
                done_token,
                auto=False,
            )

        worker.signals.probe_started.connect(_handle_manual_probe_started)
        worker.signals.finished.connect(_handle_manual_done)
        self._test_workers.append(worker)
        try:
            worker.start()
        except Exception as exc:
            self._complete_status_test_token(token)
            detail = f"检测启动失败：{str(exc).strip() or type(exc).__name__}"
            if card:
                card.set_result(False, detail)
                card.set_capabilities(False, False)
            self._detected_capabilities[profile.profile_id] = (False, False)
            self._prune_test_workers()

    def _on_manual_test_done(
        self,
        profile_id: str,
        success: bool,
        detail: str,
        can_thinking: bool,
        can_multi_turn: bool,
        token: int | None = None,
    ) -> None:
        if self._complete_status_test_token(token):
            self._prune_test_workers()
            self._start_pending_status_tests()
            return
        card = self._status_cards.get(profile_id)
        if card:
            card.set_result(success, detail)
            if success:
                card.set_capabilities(can_thinking, can_multi_turn)
            else:
                card.set_capabilities(False, False)
        self._detected_capabilities[profile_id] = (
            can_thinking if success else False,
            can_multi_turn if success else False,
        )
        self._schedule_route_combo_refresh_after_status_test()
        profile = self._config.get_profile(profile_id)
        name = profile.display_name if profile else profile_id
        if success:
            show_info_message(self, "连接成功", f"✓ {name}: {detail}")
        else:
            show_warning_message(self, "连接失败", f"✗ {name}\n{detail}")
        self._prune_test_workers()
        self._start_pending_status_tests()

    def _prune_test_workers(self) -> None:
        self._test_workers = [w for w in self._test_workers if w.isRunning()]
        running_auto = sum(
            1
            for worker in self._test_workers
            if getattr(worker, "_status_test_auto", False)
            and getattr(worker, "_status_test_generation", None) == self._status_test_generation
        )
        if running_auto < self._status_tests_active:
            self._status_tests_active = running_auto
