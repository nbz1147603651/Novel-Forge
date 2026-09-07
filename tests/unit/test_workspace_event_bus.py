"""Placeholder for WorkspaceEventBus tests.

The ``novel_forge.desktop.state.event_bus`` module was removed during the P1
state-management consolidation: no production code ever published
:class:`SectionChanged`, :class:`ProjectChanged` or :class:`JobCompleted`
events, leaving the only subscribers (in ``window/page_binding.py``) as dead
callbacks. Tests that previously validated the bus are kept here as a
historical record but skipped at collection time, since the production
abstraction they covered no longer exists.

To re-enable these tests, restore the ``state/event_bus.py`` module and the
``_setup_event_subscriptions`` / ``_teardown_event_subscriptions`` wiring in
``window/page_binding.py``. See git history (``b9429306`` +
``ce656305`` + ``292e2c9c``).
"""

from __future__ import annotations

import pytest

pytest.skip(
    "WorkspaceEventBus was removed during the P1 state management consolidation; "
    "no production code publishes these events. See module docstring.",
    allow_module_level=True,
)
