"""Placeholder for event-driven binding perf tests.

The ``novel_forge.desktop.state.event_bus.WorkspaceEventBus`` was removed in
the P1 consolidation because no production code ever published its events,
and the only subscribers in ``window/page_binding.py`` were dead callbacks.
Tests that benchmarked the event-driven path are skipped here pending
either (a) re-introduction of a publishing producer or (b) full removal of
the abstraction entirely.
"""

from __future__ import annotations

import pytest

pytest.skip(
    "WorkspaceEventBus was removed during the P1 state management consolidation; "
    "event-driven binding has no producer. See module docstring.",
    allow_module_level=True,
)
