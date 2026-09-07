"""Placeholder for verified event-driven perf tests.

The ``novel_forge.desktop.state.event_bus.WorkspaceEventBus`` was removed in
the P1 consolidation because no production code ever published its events.
This file is kept as a historical record of the previously asserted p95
budgets; tests are skipped at collection time. See
``tests/unit/test_workspace_event_bus.py`` for the rationale.
"""

from __future__ import annotations

import pytest

pytest.skip(
    "WorkspaceEventBus was removed during the P1 state management consolidation; "
    "event-driven verified benchmarks have no producer. See module docstring.",
    allow_module_level=True,
)
