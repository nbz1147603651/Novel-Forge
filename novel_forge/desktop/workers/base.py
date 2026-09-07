"""Worker base class — lifecycle + cancel protocol only.

DOES NOT enforce business-signal shape (step/finished/failed). Subclasses
define their own signals_cls (e.g. _WorkerSignals in jobs.py).

Guarantees provided:
1. asyncio loop lifecycle: new_event_loop → create_task → run_until_complete
   → drain pending tasks → shutdown_asyncgens → close.
2. threading.Event-driven request_cancel(). Subclass _on_cancel_requested()
   hook can call loop.call_soon_threadsafe(task.cancel) for cooperative cancel.
3. Errors wrapped via summarize_desktop_error() and emitted as
   worker_failed (worker_id, structured_payload).
4. Cancellation emits worker_cancelled.
5. submit() helper chooses correct QThreadPool by name.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from novel_forge.desktop.errors import summarize_desktop_error
from novel_forge.desktop.workers.pool_assign import pool_for

_submitted_workers: set[BaseJobWorker] = set()
_submitted_workers_lock = threading.Lock()


class BaseJobWorkerSignals(QObject):
    """Lifecycle signals only — no business semantics.

    Subclasses define their own signals (e.g. step/finished/failed shapes).
    Inheriting from this class is OPTIONAL — any QObject with custom signals
    can be used as signals_cls.
    """

    worker_started = Signal(str)
    worker_finished = Signal(str)
    worker_cancelled = Signal(str)
    worker_failed = Signal(str, dict)


class BaseJobWorker(QRunnable):
    """Unified base for all desktop workers.

    Attributes:
        signals_cls: QObject subclass defining worker-specific signals.
                     Default: BaseJobWorkerSignals.
                     Subclass to define custom signals — see existing
                     _WorkspaceJobWorker for an example with 5 business signals.
        pool: 'job' | 'ui_io' | 'aux'. Default 'job'.
              Controls which QThreadPool submit() uses.
    """

    signals_cls: type = BaseJobWorkerSignals
    pool: str = "job"

    def __init__(self, *, mock: bool = False) -> None:
        super().__init__()
        # Signal owner: each worker has its own QObject signals instance.
        # We hold a strong reference so the underlying QObject outlives
        # the runnable's autoDelete.
        self.signals = self.signals_cls()
        self.mock = mock
        self._cancel_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._worker_id = f"{self.__class__.__qualname__}-{id(self):x}"

    # ---------- public API ----------

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def request_cancel(self) -> None:
        """Public cancel API — sets threading.Event + invokes subclass hook."""
        self._cancel_event.set()
        self._on_cancel_requested()

    def _on_cancel_requested(self) -> None:
        """Override hook — subclasses can call loop.call_soon_threadsafe(task.cancel)."""
        loop = self._loop
        task = self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # Loop already closed — nothing to do.
                pass

    def _check_cancel(self) -> None:
        """Subclasses MUST call this in long-running async loops."""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError()

    def submit(self) -> None:
        """Submit to the configured thread pool and retain Python signal ownership."""
        # QThreadPool owns the C++ QRunnable while it runs, but that ownership
        # does not keep the Python wrapper (and its signal QObject) alive when a
        # page is destroyed during shutdown.  Retain submitted workers until
        # ``run`` finishes so late cancellation/failure signals remain safe.
        with _submitted_workers_lock:
            _submitted_workers.add(self)
        try:
            pool_for(self.pool).start(self)
        except Exception:
            with _submitted_workers_lock:
                _submitted_workers.discard(self)
            raise

    # ---------- QRunnable.run() override ----------

    def run(self) -> None:  # noqa: D401 — QRunnable API name
        """Build fresh asyncio loop, run _run_async, drain, close."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            self.signals.worker_started.emit(self._worker_id)
            self._task = loop.create_task(self._run_async())
            try:
                loop.run_until_complete(self._task)
                self.signals.worker_finished.emit(self._worker_id)
            except asyncio.CancelledError:
                self.signals.worker_cancelled.emit(self._worker_id)
            except Exception as exc:
                self._emit_failure(exc)
        finally:
            # Give specialized workers a chance to release loop-bound async
            # clients while this loop is still alive.
            try:
                loop.run_until_complete(self._cleanup_async_resources())
            except Exception:
                pass
            # Drain remaining tasks and close loop cleanly.
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop = None
            self._task = None
            loop.close()
            with _submitted_workers_lock:
                _submitted_workers.discard(self)

    # ---------- overridable ----------

    async def _run_async(self) -> None:
        """Subclasses override. Use self._check_cancel() in long loops."""
        raise NotImplementedError(
            f"{self.__class__.__qualname__}._run_async must be overridden"
        )

    async def _cleanup_async_resources(self) -> None:
        """Release resources tied to this worker's event loop.

        Subclasses with loop-bound clients should override this hook instead
        of deferring cleanup until after ``run`` has closed the loop.
        """
        return None

    # ---------- internal ----------

    def _emit_failure(self, exc: BaseException) -> None:
        try:
            payload = summarize_desktop_error(exc).as_payload()
        except Exception:
            payload = {
                "kind": exc.__class__.__name__,
                "message": str(exc),
                "structured_error": False,
            }
        self.signals.worker_failed.emit(self._worker_id, payload)
