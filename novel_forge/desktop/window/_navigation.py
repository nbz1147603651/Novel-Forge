"""Navigation helpers for the desktop main window shell."""

from __future__ import annotations

import time
from typing import Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

# Ensure built-in pages (and their signal connectors) are registered before
# any navigation helper runs.  This is a no-op on subsequent imports.
import novel_forge.desktop.page_registrations  # noqa: F401
from novel_forge.desktop.components.scroll_fade import refresh_scroll_edge_fades
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.ui_perf import ui_perf_span


def ensure_page(
    owner: Any,
    page_id: str,
    *,
    bind_workspace: bool = True,
    bind_jobs: bool = True,
    perf_label: str = "switch_page.ensure_page",
) -> QWidget | None:
    """Create and return a page only when it is actually needed."""
    with ui_perf_span(
        perf_label,
        page_id=page_id,
        bind_workspace=bind_workspace,
        bind_jobs=bind_jobs,
    ):
        loaded = owner._pages.get(page_id) if hasattr(owner, "_pages") else None
        if loaded is not None:
            return cast(QWidget, loaded)
        if not page_registry.has(page_id):
            return None
        page = page_registry.get(page_id)
        if page is None:
            return None

        placeholder = owner._page_placeholders.pop(page_id, None)
        if placeholder is not None and hasattr(owner, "_stack"):
            index = owner._stack.indexOf(placeholder)
            placeholder_was_current = owner._stack.currentWidget() is placeholder
            if index >= 0:
                owner._stack.insertWidget(index, page)
                owner._stack.removeWidget(placeholder)
                placeholder.deleteLater()
                if placeholder_was_current:
                    owner._stack.setCurrentWidget(page)
            elif owner._stack.indexOf(page) < 0:
                owner._stack.addWidget(page)
        elif hasattr(owner, "_stack") and owner._stack.indexOf(page) < 0:
            owner._stack.addWidget(page)

        owner._pages[page_id] = page
        owner._page_workspace_revision.setdefault(page_id, -1)
        # If the page supports deferred section building, force a synchronous
        # build when the page is being created outside the deferred-creation
        # flow (e.g. tests, direct _ensure_page calls).  The deferred-creation
        # flow sets _deferred_page_creation_active before calling _ensure_page
        # so that showEvent + timer-based deferred build handles it instead.
        if not getattr(owner, "_deferred_page_creation_active", False):
            ensure_built = getattr(page, "ensure_all_deferred_sections_built", None)
            if callable(ensure_built):
                ensure_built()
        restore_page_ui_state = getattr(owner, "_restore_page_ui_state", None)
        if callable(restore_page_ui_state):
            restore_page_ui_state(page_id, page)
        if owner._page_signal_connections_ready:
            owner._connect_page_signals(page_id, page)
        if bind_workspace and owner._snapshot is not None:
            # Use force=False: the revision check (-1 != current) naturally
            # triggers a first bind on cold-created pages, while pages with
            # incremental bind_workspace_sections avoid unnecessary rebuilds.
            owner._bind_workspace_for_page(page_id, force=False)
        if bind_jobs and owner._latest_jobs:
            owner._bind_jobs_for_page(page_id, owner._latest_jobs)
        if hasattr(page, "bind_task_observation_store"):
            page.bind_task_observation_store(owner._task_observation_store)
        if hasattr(page, "set_mock_mode"):
            page.set_mock_mode(owner._mock_enabled)
        # The page and all of its child widgets are fully constructed here.
        # This is the safe boundary for installing scroll overlays.
        refresh_scroll_edge_fades(page)
        return cast(QWidget, page)


def connect_page_signals(owner: Any, page_id: str, page: Any) -> None:
    """Connect page signals exactly once for a loaded page.

    Delegates to the signal_connector registered in ``page_registry`` for
    each page.  Unknown page IDs are silently ignored.
    """
    if page_id in owner._connected_page_signals:
        return
    owner._connected_page_signals.add(page_id)
    page_registry.connect_signals(page_id, owner, page=page)


def switch_page(owner: Any, page_id: str) -> None:
    """Switch the main window to a page, preserving existing deferred work semantics."""
    with ui_perf_span("switch_page", page_id=page_id):
        focus_tab: str | None = None
        if ":" in page_id:
            page_id, focus_tab = page_id.split(":", 1)

        if page_id not in owner._pages:
            return
        if (
            focus_tab is None
            and owner._active_page_id == page_id
            and owner._last_switch_page_id == page_id
        ):
            return

        now = time.monotonic()
        previous_switch_at = float(getattr(owner, "_last_page_switch_at", 0.0) or 0.0)
        owner._rapid_page_switch = bool(
            previous_switch_at > 0
            and (now - previous_switch_at) * 1000 < owner._RAPID_PAGE_SWITCH_WINDOW_MS
        )
        owner._last_page_switch_at = now

        previous_page_id = owner._active_page_id
        for key in {previous_page_id, page_id}:
            button = owner._nav_buttons.get(key)
            if button is None:
                continue
            active = key == page_id
            button.setChecked(active)
            button.set_active(active)

        owner._previous_widget = owner._stack.currentWidget()
        cold_created = owner._pages.get(page_id) is None
        defer_cold_page = (
            cold_created
            and page_id in owner._COLD_INSTANT_PAGE_IDS
            and page_id in owner._page_placeholders
        )
        if defer_cold_page:
            page = owner._page_placeholders.get(page_id)
        else:
            page = owner._ensure_page(page_id, bind_workspace=False, bind_jobs=False)
        if page is None:
            return
        owner._previous_page_id = previous_page_id
        # I-1: macOS fullscreen safe mode
        owner._enter_mac_fullscreen_safe_mode()
        owner._active_page_id = page_id
        owner._last_switch_page_id = page_id
        owner._page_activate_generation += 1
        generation = owner._page_activate_generation
        with ui_perf_span("switch_page.stack_swap", page_id=page_id):
            owner._stack.setCurrentWidget(page)
        with ui_perf_span("switch_page.page_meta", page_id=page_id):
            owner._apply_page_meta(page_id)
        owner._force_instant_page_transition_once = owner._rapid_page_switch or defer_cold_page or (
            cold_created and page_id in owner._COLD_INSTANT_PAGE_IDS
        )
        with ui_perf_span("switch_page.animations", page_id=page_id):
            if owner._rapid_page_switch:
                owner._stop_top_bar_animations()
            else:
                owner._animate_top_bar_title()
            owner._animate_current_page()
            owner._animate_active_indicator(page_id)
        if defer_cold_page:
            if owner._ui_session_restored:
                owner._schedule_ui_session_save()
            owner._schedule_deferred_page_creation(page_id, generation, focus_tab)
        else:
            owner._schedule_post_switch_work(page_id, generation, focus_tab)
        if page_id == "dashboard":
            QTimer.singleShot(owner._PREWARM_STEP_DELAY_MS, owner._schedule_idle_page_prewarm)

        # I-1: post-switch fallback
        QTimer.singleShot(
            120,
            lambda: owner._exit_mac_fullscreen_safe_mode(page_id, generation),
        )


__all__ = (
    "connect_page_signals",
    "ensure_page",
    "switch_page",
)
