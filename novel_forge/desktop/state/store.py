"""Centralized UI state management for the desktop application.

This module provides a simple, observable state store that eliminates
scattered per-page state management and provides a consistent interface
for reading and mutating application state.

Usage::

    store = UIStore.instance()

    # Read state
    project = store.active_project
    page = store.active_page

    # Mutate state (emits signals automatically)
    store.set_active_project("暗涌心弦")
    store.set_active_page("workflow")

    # Subscribe to changes
    store.active_project_changed.connect(my_callback)
    store.active_page_changed.connect(on_page_changed)
"""

from __future__ import annotations

import warnings
from typing import Any, cast

from PySide6.QtCore import QObject, Signal

from novel_forge.desktop.state.observable import ObservablePageState

# ── WindowState (per-window observable state) ────────────────────────────


class WindowState(ObservablePageState):
    """Observable state container for window-level fields.

    Created once per ``NovelForgeDesktopWindow`` and owned by it.
    The UIStore facade reads/writes through this object.

    Signals
    -------
    active_project_changed(str)
        Emitted when the active project name changes.
    """

    active_project_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.active_project: str = ""

    def set_active_project(self, project_name: str) -> None:
        """Select a project; emits :attr:`active_project_changed` if changed."""
        if project_name == self.active_project:
            return
        self.active_project = project_name
        self.active_project_changed.emit(project_name)
        self._emit_change("active_project", project_name)


# ── Module-level singleton state (supports dependency injection) ──────────

_ui_store_instance: UIStore | None = None


def get_ui_store() -> UIStore:
    """Get or create the UIStore singleton.

    This is the preferred access point for new code.  It supports
    dependency injection by allowing the module-level instance to be
    replaced (e.g. in tests or when composing services).

    For legacy code, :meth:`UIStore.instance` remains available as a
    deprecated alias.
    """
    global _ui_store_instance
    if _ui_store_instance is None:
        _ui_store_instance = UIStore()
    return _ui_store_instance


def set_ui_store(store: UIStore | None) -> None:
    """Replace the module-level singleton instance.

    Use this to inject a pre-configured UIStore (e.g. in tests or when
    composing the application from a higher-level container).

    Passing ``None`` resets to lazy-creation on next :func:`get_ui_store`.
    """
    global _ui_store_instance
    _ui_store_instance = store


class UIStore(QObject):
    """Singleton observable state store for the desktop UI.

    Acts as a facade that delegates window-level fields to a
    :class:`WindowState` instance while keeping workflow/memory/audit
    state in-process for backward compatibility.

    All Mutable state should be read and written through this store so that
    any subscriber (page, widget, toolbar) can react to changes without
    tight coupling between components.

    Signals
    -------
    active_project_changed(str)
        Emitted when the active project name changes.
    active_page_changed(str)
        Emitted when the active page identifier changes.
    workflow_status_changed(str, str)
        Emitted when a workflow job status changes — args are (project_name, status).
    memory_status_changed(str, dict)
        Emitted when memory panel state changes — args are (project_id, memory_state).
    ui_state_changed(str, object)
        Generic catch-all: emitted with (key, value) for miscellaneous state
        that does not warrant its own typed signal.
    """

    active_project_changed = Signal(str)
    active_page_changed = Signal(str)
    workflow_status_changed = Signal(str, str)
    memory_status_changed = Signal(str, object)
    audit_result_changed = Signal(str, int, object, int)
    ui_state_changed = Signal(str, object)

    _instance: UIStore | None = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window_state: WindowState | None = None
        self._active_page: str = ""
        self._workflow_statuses: dict[str, str] = {}
        self._memory_statuses: dict[str, dict[str, Any]] = {}
        self._audit_results: dict[str, dict[int, dict[str, Any]]] = {}
        self._extra: dict[str, Any] = {}

    def attach_window_state(self, state: WindowState) -> None:
        """Attach a WindowState so the facade can delegate to it.

        Called by ``NovelForgeDesktopWindow`` during initialization.
        """
        if self._window_state is state:
            return
        if self._window_state is not None:
            self.detach_window_state(self._window_state)
        self._window_state = state
        # Forward WindowState signals through the facade
        state.active_project_changed.connect(self.active_project_changed.emit)

    def detach_window_state(self, state: WindowState) -> None:
        """Detach a WindowState, disconnecting forwarded signals.

        Called by ``NovelForgeDesktopWindow`` during ``_pre_close_cleanup``
        so that a closing window does not leave stale signal connections on
        the process-wide singleton.  Without this, a second window created
        after the first closes would inherit the first window's
        (now-deleted) WindowState via the ``_window_state`` reference, and
        any late signal emission would hit a deleted QObject.
        """
        if self._window_state is state:
            try:
                state.active_project_changed.disconnect(self.active_project_changed.emit)
            except (RuntimeError, TypeError):
                # Signal may already be disconnected if the WindowState was
                # deleted first; tolerate that as a best-effort teardown.
                pass
            self._window_state = None

    # ── Singleton access ──────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> UIStore:
        """Return the application-wide singleton store.

        .. deprecated::
            Use :func:`get_ui_store` instead.  This method is kept for
            backward compatibility and will be removed in a future release.
        """
        warnings.warn(
            "UIStore.instance() is deprecated; use get_ui_store() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return get_ui_store()

    @classmethod
    def reset(cls) -> None:
        """Destroy the current singleton (useful in tests)."""
        global _ui_store_instance
        if _ui_store_instance is not None:
            _ui_store_instance.deleteLater()
            _ui_store_instance = None
        cls._instance = None

    # ── Active project (delegated to WindowState) ─────────────────────────

    @property
    def active_project(self) -> str:
        """Name of the currently selected project, or empty string."""
        if self._window_state is not None:
            return self._window_state.active_project
        return ""

    def set_active_project(self, project_name: str) -> None:
        """Select a project; emits :attr:`active_project_changed` if changed."""
        if self._window_state is not None:
            self._window_state.set_active_project(project_name)
        # Also emit locally for backward compat when no WindowState attached
        else:
            self.active_project_changed.emit(project_name)

    # ── Active page ───────────────────────────────────────────────────────

    @property
    def active_page(self) -> str:
        """Identifier of the currently visible page."""
        return self._active_page

    def set_active_page(self, page_id: str) -> None:
        """Navigate to *page_id*; emits :attr:`active_page_changed` if changed."""
        if page_id == self._active_page:
            return
        self._active_page = page_id
        self.active_page_changed.emit(page_id)

    # ── Workflow status ───────────────────────────────────────────────────

    def workflow_status(self, project_name: str) -> str:
        """Return the last known workflow status for *project_name*."""
        return self._workflow_statuses.get(project_name, "idle")

    def set_workflow_status(self, project_name: str, status: str) -> None:
        """Update workflow status; emits :attr:`workflow_status_changed`."""
        if self._workflow_statuses.get(project_name) == status:
            return
        self._workflow_statuses[project_name] = status
        self.workflow_status_changed.emit(project_name, status)

    # ── Memory status ─────────────────────────────────────────────────────

    def memory_status(self, project_id: str) -> dict[str, Any]:
        """Return the last known memory state for *project_id*."""
        return self._memory_statuses.get(project_id, {})

    def set_memory_status(self, project_id: str, status: dict[str, Any]) -> None:
        """Update memory status; emits :attr:`memory_status_changed`."""
        if self._memory_statuses.get(project_id) == status:
            return
        self._memory_statuses[project_id] = status
        self.memory_status_changed.emit(project_id, status)

    def clear_memory_status(self, project_id: str) -> None:
        """Clear memory status for a project."""
        if project_id in self._memory_statuses:
            del self._memory_statuses[project_id]
            self.memory_status_changed.emit(project_id, {})

    # ── Audit results ────────────────────────────────────────────────────

    def audit_result(self, project_id: str, chapter: int) -> dict[str, Any] | None:
        """Return the (unwrapped) audit result for a specific project and chapter.

        Internally the store wraps each result in ``{"result": ..., "version": N}``
        for version tracking.  Callers that consume the data directly (e.g. the
        memory panel) expect the raw result dict, so this method unwraps it.
        """
        entry = self._audit_results.get(project_id, {}).get(chapter)
        if entry is None:
            return None
        if isinstance(entry, dict) and "result" in entry:
            return cast(dict[str, Any], entry["result"])
        return cast(dict[str, Any] | None, entry)

    def audit_results_for_project(self, project_id: str) -> dict[int, dict[str, Any]]:
        """Return all audit results for a project."""
        return self._audit_results.get(project_id, {})

    def set_audit_result(
        self,
        project_id: str,
        chapter: int,
        result: dict[str, Any],
        version: int | None = None,
    ) -> None:
        """Update audit result with version tracking; emits :attr:`audit_result_changed`.

        Args:
            project_id: Project identifier
            chapter: Chapter number
            result: Audit result dict
            version: Optional version number (auto-incremented if not provided)
        """
        if project_id not in self._audit_results:
            self._audit_results[project_id] = {}

        if version is None:
            current = self._audit_results[project_id].get(chapter)
            if isinstance(current, dict) and "version" in current:
                version = current["version"] + 1
            else:
                version = 1

        self._audit_results[project_id][chapter] = {
            "result": result,
            "version": version,
        }
        self.audit_result_changed.emit(project_id, chapter, result, version)

    def get_audit_result(
        self, project_id: str, chapter: int
    ) -> dict[str, Any] | None:
        """Get audit result with version metadata.

        Returns:
            Dict with "result" and "version" keys, or None if not found
        """
        if project_id not in self._audit_results:
            return None
        return self._audit_results[project_id].get(chapter)

    def clear_audit_results(self, project_id: str) -> None:
        """Clear all audit results for a project."""
        if project_id in self._audit_results:
            del self._audit_results[project_id]

    def get_latest_audit_result(self, project_id: str) -> dict[str, Any] | None:
        """Get the most recent (unwrapped) audit result for a project."""
        chapters = self._audit_results.get(project_id, {})
        if not chapters:
            return None
        latest_chapter = max(chapters.keys())
        entry = chapters[latest_chapter]
        if isinstance(entry, dict) and "result" in entry:
            return cast(dict[str, Any], entry["result"])
        return cast(dict[str, Any] | None, entry)

    # ── Generic key-value state ───────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve an arbitrary state value by key."""
        return self._extra.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store an arbitrary state value and emit :attr:`ui_state_changed`."""
        if self._extra.get(key) == value:
            return
        self._extra[key] = value
        self.ui_state_changed.emit(key, value)

    # ── Snapshot / restore (useful for testing) ───────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a dict copy of the current state."""
        return {
            "active_project": self.active_project,
            "active_page": self._active_page,
            "workflow_statuses": dict(self._workflow_statuses),
            "memory_statuses": dict(self._memory_statuses),
            "audit_results": {
                pid: dict(chapters)
                for pid, chapters in self._audit_results.items()
            },
            "extra": dict(self._extra),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore state from a :meth:`snapshot` dict (does not emit signals)."""
        self.set_active_project(snapshot.get("active_project", ""))
        self._active_page = snapshot.get("active_page", "")
        self._workflow_statuses = dict(snapshot.get("workflow_statuses", {}))
        self._memory_statuses = dict(snapshot.get("memory_statuses", {}))
        self._audit_results = {
            pid: dict(chapters)
            for pid, chapters in snapshot.get("audit_results", {}).items()
        }
        self._extra = dict(snapshot.get("extra", {}))
