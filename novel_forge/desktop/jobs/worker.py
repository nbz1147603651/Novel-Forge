"""Sub-module of novel_forge.desktop.jobs.

Auto-generated in the M3.5 split. Contains worker.py symbols.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from novel_forge.core.infra.event_bus import (
    EventBus,
)

logger = logging.getLogger(__name__)

_event_bus: EventBus | None = None


class _WorkerSignals(QObject):
    started = Signal(str)
    step = Signal(str, str, object)
    decision_required = Signal(str, object)
    finished = Signal(str, object)
    failed = Signal(str, object)
    cleanup_runtime = Signal(object)

