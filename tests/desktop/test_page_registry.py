"""Tests for PageRegistry from novel_forge.desktop.registry.

Covers: register_page, register_duplicate, lazy_instantiation,
unregister, metadata, list_pages.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from PySide6.QtWidgets import QWidget

from novel_forge.desktop.registry import PageDescriptor, PageRegistry, page_registry


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Reset the PageRegistry singleton before and after each test."""
    PageRegistry.reset()
    yield
    PageRegistry.reset()


# ── Dummy page classes for testing ────────────────────────────────────────


class DummyPageA(QWidget):
    """Minimal page widget for testing."""


class DummyPageB(QWidget):
    """Another minimal page widget for testing."""


# ── register_page ─────────────────────────────────────────────────────────


class TestRegisterPage:
    """Tests for the register() method."""

    def test_register_direct_call(self) -> None:
        """Direct registration of a page class."""
        reg = PageRegistry()
        reg.register("test_page", DummyPageA, title="Test", subtitle="A test page")

        assert reg.has("test_page")
        assert reg.metadata("test_page") is not None

    def test_register_decorator(self) -> None:
        """Registration via decorator returns the class."""
        reg = PageRegistry()

        @reg.register("decorated_page", title="Decorated", subtitle="Via decorator")
        class MyPage(QWidget):
            pass

        assert reg.has("decorated_page")
        assert MyPage is not None  # decorator returns the class

    def test_register_returns_class(self) -> None:
        """register() returns the page class for chaining."""
        reg = PageRegistry()
        result = reg.register("return_test", DummyPageA, title="Return", subtitle="Test")

        assert result is DummyPageA

    def test_register_with_all_metadata(self) -> None:
        """Registration with all optional metadata fields."""
        reg = PageRegistry()
        reg.register(
            "full_meta",
            DummyPageA,
            title="Full",
            subtitle="Full metadata",
            eyebrow="Eyebrow",
            label="Label",
            custom_key="custom_value",
        )

        meta = reg.metadata("full_meta")
        assert meta is not None
        assert meta.title == "Full"
        assert meta.subtitle == "Full metadata"
        assert meta.eyebrow == "Eyebrow"
        assert meta.label == "Label"
        assert meta.extra.get("custom_key") == "custom_value"

    def test_register_eyebrow_defaults_to_title(self) -> None:
        """eyebrow falls back to title when not provided."""
        reg = PageRegistry()
        reg.register("eyebrow_test", DummyPageA, title="MyTitle", subtitle="Sub")

        meta = reg.metadata("eyebrow_test")
        assert meta is not None
        assert meta.eyebrow == "MyTitle"

    def test_register_label_defaults_to_eyebrow(self) -> None:
        """label falls back to eyebrow when not provided."""
        reg = PageRegistry()
        reg.register("label_test", DummyPageA, title="T", subtitle="S", eyebrow="Eye")

        meta = reg.metadata("label_test")
        assert meta is not None
        assert meta.label == "Eye"

    def test_register_with_signal_connector(self, desktop_app) -> None:
        """Registration with a signal_connector stores it."""
        reg = PageRegistry()
        connector_called = False

        def my_connector(page: object, window: object) -> None:
            nonlocal connector_called
            connector_called = True

        reg.register(
            "signal_page",
            DummyPageA,
            title="Signal",
            subtitle="Test",
            signal_connector=my_connector,
        )

        reg.get("signal_page")
        reg.connect_signals("signal_page", window=object())
        assert connector_called

    def test_register_with_action_factory(self, desktop_app) -> None:
        """Registration with an action_factory stores it."""
        reg = PageRegistry()

        def my_factory(page: object) -> list[tuple[str, object]]:
            return [("Action", lambda: None)]

        reg.register(
            "action_page",
            DummyPageA,
            title="Action",
            subtitle="Test",
            action_factory=my_factory,
        )

        reg.get("action_page")
        actions = reg.get_actions("action_page")
        assert len(actions) == 1
        assert actions[0][0] == "Action"

    def test_instance_action_builder_errors_are_not_silenced(self, desktop_app) -> None:
        """Page-switch error handling must be able to observe builder failures."""
        reg = PageRegistry()
        reg.register("action_page", DummyPageA, title="Action", subtitle="Test")
        page = reg.get("action_page")
        assert page is not None

        def _broken_builder() -> list[tuple[str, object]]:
            raise RuntimeError("broken action builder")

        page._action_builder = _broken_builder

        with pytest.raises(RuntimeError, match="broken action builder"):
            reg.get_actions("action_page")


# ── register_duplicate ────────────────────────────────────────────────────


class TestRegisterDuplicate:
    """Tests for duplicate registration behaviour."""

    def test_duplicate_overwrites(self) -> None:
        """Registering the same page_id twice overwrites the first."""
        reg = PageRegistry()
        reg.register("dup", DummyPageA, title="First", subtitle="First")
        reg.register("dup", DummyPageB, title="Second", subtitle="Second")

        meta = reg.metadata("dup")
        assert meta is not None
        assert meta.title == "Second"
        assert meta.page_class is DummyPageB

    def test_duplicate_via_decorator_overwrites(self) -> None:
        """Decorator-style duplicate also overwrites."""
        reg = PageRegistry()

        @reg.register("dup_dec", title="First")
        class FirstPage(QWidget):
            pass

        @reg.register("dup_dec", title="Second")
        class SecondPage(QWidget):
            pass

        meta = reg.metadata("dup_dec")
        assert meta is not None
        assert meta.title == "Second"
        assert meta.page_class is SecondPage


# ── lazy_instantiation ────────────────────────────────────────────────────


class TestLazyInstantiation:
    """Tests for lazy page instantiation via get()."""

    def test_get_creates_instance(self, desktop_app) -> None:
        """get() creates and returns a page instance."""
        reg = PageRegistry()
        reg.register("lazy", DummyPageA, title="Lazy", subtitle="Test")

        instance = reg.get("lazy")
        assert instance is not None
        assert isinstance(instance, DummyPageA)

    def test_get_returns_same_instance(self, desktop_app) -> None:
        """get() returns the cached instance on subsequent calls."""
        reg = PageRegistry()
        reg.register("cached", DummyPageA, title="Cached", subtitle="Test")

        first = reg.get("cached")
        second = reg.get("cached")
        assert first is second

    def test_get_uses_zero_argument_factory_once(self, desktop_app) -> None:
        """get() supports factory registration and still caches the instance."""
        reg = PageRegistry()
        calls: list[str] = []

        def factory() -> QWidget:
            calls.append("created")
            return DummyPageA()

        reg.register("factory", factory, title="Factory", subtitle="Test")

        first = reg.get("factory")
        second = reg.get("factory")

        assert isinstance(first, DummyPageA)
        assert first is second
        assert calls == ["created"]

    def test_get_unknown_page_returns_none(self) -> None:
        """get() returns None for unregistered page_id."""
        reg = PageRegistry()
        assert reg.get("nonexistent") is None

    def test_get_page_without_class_returns_none(self) -> None:
        """get() returns None when descriptor has no page_class."""
        reg = PageRegistry()
        # Manually insert a descriptor without a page_class.
        reg._descriptors["no_class"] = PageDescriptor(
            page_id="no_class",
            title="No Class",
            subtitle="Test",
            page_class=None,
        )
        assert reg.get("no_class") is None


# ── unregister ────────────────────────────────────────────────────────────


class TestUnregister:
    """Tests for unregistering pages."""

    def test_unregister_removes_page(self) -> None:
        """unregister() removes a page from the registry."""
        reg = PageRegistry()
        reg.register("unreg", DummyPageA, title="Unreg", subtitle="Test")
        assert reg.has("unreg")

        reg.unregister("unreg")
        assert not reg.has("unreg")
        assert reg.metadata("unreg") is None
        assert "unreg" not in reg.list_pages()

    def test_unregister_unknown_page_is_noop(self) -> None:
        """unregister() on a non-existent page_id does not raise."""
        reg = PageRegistry()
        reg.unregister("does_not_exist")  # should not raise

    def test_unregister_also_removes_instance(self, desktop_app) -> None:
        """unregister() removes cached instance too."""
        reg = PageRegistry()
        reg.register("unreg_inst", DummyPageA, title="Unreg", subtitle="Test")
        reg.get("unreg_inst")  # create instance
        assert "unreg_inst" in reg._instances

        reg.unregister("unreg_inst")
        assert "unreg_inst" not in reg._instances

    def test_unregister_removes_signal_connector(self) -> None:
        """unregister() removes associated signal connector."""
        reg = PageRegistry()
        reg.register(
            "unreg_sig",
            DummyPageA,
            title="Unreg",
            subtitle="Test",
            signal_connector=lambda p, w: None,
        )
        assert "unreg_sig" in reg._signal_connectors

        reg.unregister("unreg_sig")
        assert "unreg_sig" not in reg._signal_connectors

    def test_unregister_removes_action_factory(self) -> None:
        """unregister() removes associated action factory."""
        reg = PageRegistry()
        reg.register(
            "unreg_act",
            DummyPageA,
            title="Unreg",
            subtitle="Test",
            action_factory=lambda p: [],
        )
        assert "unreg_act" in reg._action_factories

        reg.unregister("unreg_act")
        assert "unreg_act" not in reg._action_factories


# ── metadata ──────────────────────────────────────────────────────────────


class TestMetadata:
    """Tests for metadata retrieval."""

    def test_metadata_returns_descriptor(self) -> None:
        """metadata() returns the PageDescriptor."""
        reg = PageRegistry()
        reg.register("meta", DummyPageA, title="Meta", subtitle="Test")

        desc = reg.metadata("meta")
        assert isinstance(desc, PageDescriptor)
        assert desc.page_id == "meta"
        assert desc.title == "Meta"

    def test_metadata_unknown_returns_none(self) -> None:
        """metadata() returns None for unknown page_id."""
        reg = PageRegistry()
        assert reg.metadata("unknown") is None

    def test_descriptor_is_frozen(self) -> None:
        """PageDescriptor is immutable (frozen dataclass)."""
        reg = PageRegistry()
        reg.register("frozen", DummyPageA, title="Frozen", subtitle="Test")

        desc = reg.metadata("frozen")
        assert desc is not None
        with pytest.raises(FrozenInstanceError):
            desc.title = "Changed"  # type: ignore[misc]


# ── list_pages ────────────────────────────────────────────────────────────


class TestListPages:
    """Tests for list_pages()."""

    def test_list_pages_empty(self) -> None:
        """list_pages() returns empty list when nothing registered."""
        reg = PageRegistry()
        assert reg.list_pages() == []

    def test_list_pages_returns_ids(self) -> None:
        """list_pages() returns all registered page IDs."""
        reg = PageRegistry()
        reg.register("page_a", DummyPageA, title="A", subtitle="Test")
        reg.register("page_b", DummyPageB, title="B", subtitle="Test")

        pages = reg.list_pages()
        assert pages == ["page_a", "page_b"]

    def test_list_pages_preserves_order(self) -> None:
        """list_pages() returns IDs in registration order."""
        reg = PageRegistry()
        reg.register("first", DummyPageA, title="First", subtitle="Test")
        reg.register("second", DummyPageB, title="Second", subtitle="Test")
        reg.register("third", DummyPageA, title="Third", subtitle="Test")

        assert reg.list_pages() == ["first", "second", "third"]

    def test_list_pages_after_unregister(self) -> None:
        """list_pages() reflects unregistered pages."""
        reg = PageRegistry()
        reg.register("keep", DummyPageA, title="Keep", subtitle="Test")
        reg.register("remove", DummyPageB, title="Remove", subtitle="Test")
        reg.unregister("remove")

        assert reg.list_pages() == ["keep"]


# ── has() ─────────────────────────────────────────────────────────────────


class TestHas:
    """Tests for has() method."""

    def test_has_registered_page(self) -> None:
        """has() returns True for registered page."""
        reg = PageRegistry()
        reg.register("exists", DummyPageA, title="Exists", subtitle="Test")
        assert reg.has("exists") is True

    def test_has_unregistered_page(self) -> None:
        """has() returns False for unregistered page."""
        reg = PageRegistry()
        assert reg.has("missing") is False


# ── singleton behaviour ───────────────────────────────────────────────────


class TestSingleton:
    """Tests for PageRegistry singleton pattern."""

    def test_instance_returns_singleton(self) -> None:
        """instance() returns the same object on repeated calls."""
        first = PageRegistry.instance()
        second = PageRegistry.instance()
        assert first is second

    def test_reset_clears_singleton(self) -> None:
        """reset() clears the singleton so a new instance() is created."""
        original = PageRegistry.instance()
        PageRegistry.reset()
        new = PageRegistry.instance()
        assert original is not new

    def test_module_singleton_is_instance(self) -> None:
        """The module-level page_registry is a PageRegistry instance."""
        assert isinstance(page_registry, PageRegistry)
