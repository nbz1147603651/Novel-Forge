"""Sub-module of novel_forge.desktop.jobs.

Auto-generated in the M3.5 split. Contains history.py symbols.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Signal

from novel_forge.core.infra.event_bus import (
    EventBus,
)
from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals

logger = logging.getLogger(__name__)

_event_bus: EventBus | None = None


class _HistoryWorkerSignals(BaseJobWorkerSignals):
    finished = Signal()


class _HistoryWorker(BaseJobWorker):
    """Background worker to load persisted job history without blocking ``__init__``."""

    pool = "aux"

    def __init__(self, load_history: Callable[[], None], mark_done: Callable[[], None]) -> None:
        super().__init__()
        self._load_history = load_history
        self._mark_done = mark_done
        self.signals = _HistoryWorkerSignals()

    async def _run_async(self) -> None:
        import asyncio

        try:
            await asyncio.to_thread(self._load_history)
        except Exception:
            logger.debug("Async history load failed", exc_info=True)
        finally:
            self._mark_done()
            self.signals.finished.emit()

