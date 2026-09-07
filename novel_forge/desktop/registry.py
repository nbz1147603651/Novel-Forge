"""Page registry for dynamic page registration in the Novel Forge desktop shell.

Provides a singleton ``PageRegistry`` that maps page IDs to page classes and
metadata.  Pages self-register via the ``@page_registry.register(...)``
decorator, eliminating the need to modify ``window.py`` when adding new pages.

Usage::

    from novel_forge.desktop.registry import page_registry

    @page_registry.register("my_page", title="My Page", subtitle="...")
    class MyPage(QWidget):
        ...
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import shiboken6
from PySide6.QtCore import QObject

_logger = logging.getLogger(__name__)


@runtime_checkable
class PageLifecycle(Protocol):
    """Standard lifecycle interface that desktop pages should implement.

    Pages are not required to inherit from this protocol at runtime — it
    exists primarily for type checking and IDE auto-completion.  The window
    shell calls these methods via duck-typing (``hasattr`` guards), so
    missing methods are silently skipped rather than raising errors.

    Implement all methods that apply to your page; omit methods that are
    not relevant (the protocol uses ``runtime_checkable`` so
    ``isinstance`` checks work for the methods you do implement).
    """

    def bind_workspace(self, snapshot: Any, *, changed_sections: set[str] | None = None) -> None:
        """Bind workspace data to the page UI."""
        ...

    def on_jobs_changed(self, jobs: list[Any]) -> None:
        """Called when the job list changes while this page is active."""
        ...

    def activate(self) -> None:
        """Called after the page becomes visible and data is bound."""
        ...

    def shutdown(self) -> None:
        """Called before the page is destroyed; disconnect signals, stop timers."""
        ...

    def prewarm_visuals(self) -> None:
        """Optional: pre-render visual elements during idle prewarm."""
        ...

    def export_ui_state(self) -> dict[str, Any]:
        """Optional: return restart-safe UI choices for the desktop session."""
        ...

    def restore_ui_state(self, payload: object) -> None:
        """Optional: apply a previously exported desktop-session payload."""
        ...


@dataclass(frozen=True)
class PageDescriptor:
    """Immutable metadata for a registered page."""

    page_id: str
    title: str
    subtitle: str
    eyebrow: str = ""
    label: str = ""
    page_class: Callable[[], Any] | type | None = None
    """Callable that returns a page widget instance."""
    actions: list[tuple[str, Callable[[], None]]] = field(default_factory=list)
    """Default action descriptors: [(label, callback), ...]."""
    extra: dict[str, Any] = field(default_factory=dict)
    """Arbitrary extra metadata for page-specific needs."""


class PageRegistry:
    """Singleton registry for desktop page discovery and lifecycle.

    API::

        registry = PageRegistry.instance()
        registry.register("page_id", page_class, title="...", subtitle="...")
        page = registry.get("page_id")          # lazy instantiation
        ids = list(registry.list_pages())       # all registered page IDs
        meta = registry.metadata("page_id")     # PageDescriptor
        registry.connect_signals("page_id", window)  # auto-connect signals
        actions = registry.get_actions("page_id")    # action descriptors
    """

    _instance: PageRegistry | None = None

    def __init__(self) -> None:
        self._descriptors: dict[str, PageDescriptor] = {}
        self._instances: dict[str, Any] = {}
        self._signal_connectors: dict[str, Callable[[Any, QObject], None]] = {}
        self._action_factories: dict[
            str, Callable[[Any], list[tuple[str, Callable[[], None]]]]
        ] = {}

    @classmethod
    def instance(cls) -> PageRegistry:
        """Return the singleton registry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton — primarily for testing."""
        module_registry = globals().get("page_registry")
        if cls._instance is not None:
            cls._instance.clear_instances()
            if cls._instance is not module_registry:
                cls._instance._descriptors.clear()
                cls._instance._signal_connectors.clear()
                cls._instance._action_factories.clear()
        if isinstance(module_registry, cls) and module_registry is not cls._instance:
            module_registry.clear_instances()
        cls._instance = None

    # ── Registration ────────────────────────────────────────────────────

    def register(
        self,
        page_id: str,
        page_class: Callable[[], Any] | type | None = None,
        *,
        title: str = "",
        subtitle: str = "",
        eyebrow: str = "",
        label: str = "",
        signal_connector: Callable[[Any, QObject], None] | None = None,
        action_factory: Callable[[Any], list[tuple[str, Callable[[], None]]]] | None = None,
        **extra: Any,
    ) -> Callable[..., Any]:
        """Register a page class with the registry.

        Can be used as a decorator or called directly::

            # Decorator style
            @page_registry.register("my_page", title="My Page", subtitle="...")
            class MyPage(QWidget): ...

            # Direct call
            page_registry.register("my_page", MyPage, title="My Page", subtitle="...")

        Args:
            page_id: Unique identifier for the page (used in navigation).
            page_class: The page widget class or zero-argument page factory.
                If None, returns a decorator.
            title: Short title shown in the top bar.
            subtitle: Descriptive subtitle shown in the top bar.
            eyebrow: Small label above the title (e.g. "案头").
            label: Sidebar navigation label (defaults to eyebrow).
            signal_connector: Optional callable(page_instance, window) to
                connect page signals to window slots.
            action_factory: Optional callable(page_instance) returning a list
                of (label, callback) tuples for top-bar action buttons.
            **extra: Arbitrary extra metadata stored in PageDescriptor.extra.

        Returns:
            The page class (for decorator usage) or None (for direct call).
        """

        def _do_register(klass: Callable[[], Any] | type) -> Callable[[], Any] | type:
            desc = PageDescriptor(
                page_id=page_id,
                title=title,
                subtitle=subtitle,
                eyebrow=eyebrow or title,
                label=label or eyebrow or title,
                page_class=klass,
                extra=extra,
            )
            self._descriptors[page_id] = desc
            if signal_connector is not None:
                self._signal_connectors[page_id] = signal_connector
            if action_factory is not None:
                self._action_factories[page_id] = action_factory
            _logger.debug("Registered page: %s (%s)", page_id, title)
            return klass

        if page_class is not None:
            _do_register(page_class)
            return page_class
        return _do_register

    # ── Lookup ──────────────────────────────────────────────────────────

    def get(self, page_id: str) -> Any:
        """Return a page instance, creating it lazily if needed.

        Returns None if the page is not registered or has no page_class.
        """
        cached = self._instances.get(page_id)
        if cached is not None:
            if not isinstance(cached, QObject) or shiboken6.isValid(cached):
                return cached
            self._instances.pop(page_id, None)

        if page_id not in self._instances:
            desc = self._descriptors.get(page_id)
            if desc is None or desc.page_class is None:
                return None
            self._instances[page_id] = desc.page_class()
        return self._instances[page_id]

    def list_pages(self) -> list[str]:
        """Return all registered page IDs in registration order."""
        return list(self._descriptors.keys())

    def metadata(self, page_id: str) -> PageDescriptor | None:
        """Return the PageDescriptor for a page, or None if not registered."""
        return self._descriptors.get(page_id)

    def has(self, page_id: str) -> bool:
        """Check if a page is registered."""
        return page_id in self._descriptors

    def unregister(self, page_id: str) -> None:
        """Remove a page from the registry.

        Silently does nothing if the page is not registered.
        Also removes any cached instance, signal connector, and action factory.
        """
        self._descriptors.pop(page_id, None)
        self._dispose_instance(self._instances.pop(page_id, None))
        self._signal_connectors.pop(page_id, None)
        self._action_factories.pop(page_id, None)

    def clear_instances(self, *, exclude: Iterable[Any] = ()) -> None:
        """Drop cached page widgets so a later window gets fresh instances."""
        exclude_ids = {id(instance) for instance in exclude}
        for page_id, instance in list(self._instances.items()):
            self._instances.pop(page_id, None)
            if id(instance) in exclude_ids:
                continue
            self._dispose_instance(instance)

    @staticmethod
    def _dispose_instance(instance: Any) -> None:
        """Schedule cached Qt page instances for deletion."""
        if not isinstance(instance, QObject):
            return
        try:
            if not shiboken6.isValid(instance):
                return
            shutdown = getattr(instance, "shutdown", None)
            if callable(shutdown):
                shutdown()
            close = getattr(instance, "close", None)
            if callable(close):
                close()
            instance.deleteLater()
        except RuntimeError:
            return

    # ── Signal connections ──────────────────────────────────────────────

    def register_signal_connector(
        self,
        page_id: str,
        connector: Callable[[Any, QObject], None],
    ) -> None:
        """Register a signal connector for a page.

        The connector is called as ``connector(page_instance, window)`` and
        should connect all page signals to window slots.
        """
        self._signal_connectors[page_id] = connector

    def connect_signals(self, page_id: str, window: QObject, page: Any = None) -> None:
        """Connect all signals for a page to the window.

        Uses the registered signal_connector if available, otherwise does nothing.
        If *page* is not given, falls back to the cached instance.
        """
        connector = self._signal_connectors.get(page_id)
        if connector is None:
            return
        resolved = page if page is not None else self._instances.get(page_id)
        if resolved is not None:
            connector(resolved, window)

    def connect_all_signals(self, window: QObject) -> None:
        """Connect signals for all registered pages."""
        for page_id in self._descriptors:
            self.connect_signals(page_id, window)

    # ── Actions ─────────────────────────────────────────────────────────

    def register_action_factory(
        self,
        page_id: str,
        factory: Callable[[Any], list[tuple[str, Callable[[], None]]]],
    ) -> None:
        """Register an action factory for a page.

        The factory is called as ``factory(page_instance)`` and should return
        a list of (label, callback) tuples for top-bar action buttons.
        """
        self._action_factories[page_id] = factory

    def get_actions(self, page_id: str) -> list[tuple[str, Callable[[], None]]]:
        """Return action descriptors for a page.

        Priority:
        1. ``_action_builder`` callable set on the page instance by its
           signal_connector (used by built-in pages).
        2. Registered ``action_factory`` called with the page instance.
        3. Static ``actions`` from the PageDescriptor.
        """
        page = self._instances.get(page_id)
        if page is not None:
            builder = getattr(page, "_action_builder", None)
            if callable(builder):
                return list(builder())
            factory = self._action_factories.get(page_id)
            if factory is not None:
                return factory(page)
        desc = self._descriptors.get(page_id)
        return desc.actions if desc else []


# Module-level singleton for decorator usage.
page_registry = PageRegistry.instance()
