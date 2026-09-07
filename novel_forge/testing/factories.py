"""Test factories for lightweight RuntimeServices mocks (Phase 8).

Provides ``create_test_runtime()`` which returns a minimal RuntimeServices
instance suitable for unit tests — no real LLM router, no disk I/O.

Usage::

    from novel_forge.testing.factories import create_test_runtime

    def test_something():
        rt = create_test_runtime()
        assert rt.settings is not None
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def create_test_runtime(
    *,
    settings_overrides: dict[str, Any] | None = None,
    with_event_bus: bool = True,
) -> Any:
    """Create a lightweight RuntimeServices for unit tests.

    Returns a real RuntimeServices with mock router/builder/storage so tests
    don't need network access or filesystem setup.

    Args:
        settings_overrides: Optional dict of Settings field overrides.
        with_event_bus: Whether to attach a real EventBus (default True).

    Returns:
        A RuntimeServices instance with mocked collaborators.
    """
    from novel_forge.core.config import Settings
    from novel_forge.workspace.runtime import RuntimeServices

    settings = Settings(**(settings_overrides or {}))

    router = MagicMock(name="MockModelRouter")
    builder = MagicMock(name="MockPromptBuilder")
    storage = MagicMock(name="MockFileSystemStorage")

    event_bus = None
    if with_event_bus:
        from novel_forge.core.infra.event_bus import EventBus

        event_bus = EventBus()

    return RuntimeServices(
        settings=settings,
        router=router,
        builder=builder,
        storage=storage,
        event_bus=event_bus,
    )


__all__ = ["create_test_runtime"]
