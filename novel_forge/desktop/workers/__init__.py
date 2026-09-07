"""Worker abstractions + lifecycle helpers.

Centralizes:
- BaseJobWorker / BaseJobWorkerSignals (cancel/run/lifecycle primitives)
- safe_shutdown_page (unified page teardown helper)
- pool_for / by_name (pool name resolution)

Note: Business signals (step/finished/failed shapes) belong on
subclasses via `signals_cls`. Base class does NOT enforce shape.
"""

from __future__ import annotations

from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.desktop.workers.lifecycle import (
    disconnect_signals,
    safe_shutdown_page,
)
from novel_forge.desktop.workers.pool_assign import by_name, pool_for

__all__ = [
    "BaseJobWorker",
    "BaseJobWorkerSignals",
    "safe_shutdown_page",
    "disconnect_signals",
    "pool_for",
    "by_name",
]