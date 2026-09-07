"""Lifecycle contracts for Chapter Studio's composition coordinators."""

from __future__ import annotations

import gc
import weakref
from typing import Any

import pytest

from novel_forge.desktop.pages.chapter_studio.coordinator import (
    StudioCoordinator,
    StudioRenderer,
    StudioStateManager,
)


class _Page:
    pass


@pytest.mark.parametrize(
    "coordinator_type",
    [StudioStateManager, StudioCoordinator, StudioRenderer],
)
def test_retained_coordinator_does_not_keep_page_alive(
    coordinator_type: type[Any],
) -> None:
    """External caches of coordinators must not prolong a discarded page."""
    page = _Page()
    page_ref = weakref.ref(page)
    coordinator = coordinator_type(page)

    del page
    gc.collect()

    assert page_ref() is None
    assert coordinator._page is None
