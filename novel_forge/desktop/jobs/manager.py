"""DesktopJobManager and _WorkspaceJobWorker — extracted from jobs/__init__.py."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QTimer, Signal

from novel_forge.app_service.contracts import (
    JobCommand,
    JobEvent,
    JobEventType,
    JobKind,
)
from novel_forge.app_service.contracts import (
    JobRecord as AppJobRecord,
)
from novel_forge.app_service.job_service import JobService
from novel_forge.common.token_usage import apply_live_usage_payload
from novel_forge.core.config import get_settings
from novel_forge.core.infra.async_runner import run_async_coro
from novel_forge.core.infra.dependency_resolver import DependencyResolver, Operation
from novel_forge.core.infra.event_bus import (
    CHAPTER_COMPLETED,
    CHAPTER_CONTRACTS_SYNCED,
    INIT_COMPLETED,
    INIT_STARTED,
    OUTLINE_UPDATED,
    SECTION_CHANGED,
    ProjectEvent,
)
from novel_forge.desktop.constants import JOB_BIND_COALESCE_MS as _JOB_BIND_COALESCE_MS
from novel_forge.desktop.constants import LABEL_CHAPTER_RE as _LABEL_CHAPTER_RE
from novel_forge.desktop.errors import summarize_desktop_error
from novel_forge.desktop.jobs.events import (
    _coalesce_stream_delta_events,
    _get_event_bus,
)
from novel_forge.desktop.jobs.history import _HistoryWorker
from novel_forge.desktop.jobs.records import (
    _MAX_JOB_EVENTS,
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
    WaitingJob,
    _infer_failed_step,
    _job_chapter_num,
    _job_from_payload,
    _job_kind_to_section,
    _job_to_payload,
    _now_iso,
    app_job_record_to_desktop_record,
    desktop_job_record_to_app_record,
)
from novel_forge.desktop.jobs.serialize import (
    _compact_payload,
)
from novel_forge.desktop.jobs.worker import _WorkerSignals
from novel_forge.desktop.shutdown_utils import safe_disconnect
from novel_forge.desktop.sleep_inhibitor import SleepInhibitor
from novel_forge.desktop.task_flow import WORKFLOW_TASK_KINDS
from novel_forge.desktop.task_flow_errors import (
    TaskFlowErrorArchive,
    task_flow_job_is_inactive,
)
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.progress import compact_progress_event_history, is_non_progress_step_event
from novel_forge.workspace.book_ops.execution_book_common import AuditError
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    ExportBookRequest,
    ExtendOutlineRequest,
    InitLongRequest,
    PolishChapterRequest,
    PrepareChapterRequest,
    RebuildMemoryVectorsRequest,
    ReevaluateChapterRequest,
    ReextractRelationshipsRequest,
    RepairCausalRequest,
    RepairContinuityRequest,
    RepairIssuesRequest,
    RepairMotifHistoryRequest,
    ResolveChapterCheckpointRequest,
    RunChapterRequest,
    RunShortRequest,
    SyncChapterContractsRequest,
    TTSPostArchiveRequest,
)
from novel_forge.workspace.projects import is_generated_test_project_id
from novel_forge.workspace.runtime import (
    RuntimeServices,
    create_runtime_services,
)

logger = logging.getLogger(__name__)


_MAX_PERSISTED_JOBS_PER_PROJECT: int = 120
_JOB_HISTORY_FILENAME = "task_flow_history.json"
_APP_EVENT_POLL_MS = 150
_APP_EVENT_BACKGROUND_POLL_MS = 1_000
_APP_EVENT_DRAIN_LIMIT = 300
_APP_EVENT_SUBSCRIPTION_MAX_QUEUE = 2_000
_WAKE_EVENT_REPLAY_BATCH_SIZE = 48
_WAKE_EVENT_REPLAY_BUDGET_MS = 8.0
_HIGH_FREQUENCY_JOB_BIND_COALESCE_MS = 500
_HIGH_FREQUENCY_STEP_EVENTS = frozenset(
    {
        "llm_stream_delta",
        "model_call_update",
        "budget_status",
        "preflight_token_estimate",
        "prompt_pressure",
    }
)


# Project-level write operations that mutate shared project state (canon, drafts,
# reports). No two jobs in this set may run concurrently for the same project_id;
# submitting a second one while the first is RUNNING or QUEUED will return the
# existing job instead of starting a second writer.
CHAPTER_WRITE_KINDS: frozenset[str] = frozenset(
    {
        "init_long",
        "run_short",
        "run_chapter",
        "prepare_chapter",
        "reevaluate_chapter",
        "repair_continuity",
        "repair_issues",
        "polish_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "repair_causal",
        "reextract_relationships",
        "repair_motif_history",
        "rebuild_memory_vectors",
        "book_consistency",
        "sync_chapter_contracts",
        "extend_outline",
    }
)

WORKFLOW_JOB_KINDS: frozenset[str] = WORKFLOW_TASK_KINDS


class _WorkspaceJobWorker(QRunnable):
    def __init__(
        self,
        job_id: str,
        *,
        mock: bool,
        task: Callable[[RuntimeServices, Callable[[str, Any], None]], Any],
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self.mock = mock
        self.task = task
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)
        # Cancellation state: set by request_cancel() from any thread.
        self._cancel_requested = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._loop_lock = threading.Lock()
        self._decision_provider: Any | None = None
        # Optional no-arg callback called when run() exits (any path).
        # Used by the job manager to track when a cancelled thread has settled.
        self._cleanup_notify: Callable[[], None] | None = None

    def request_cancel(self) -> None:
        """Signal this worker to abort as quickly as possible.

        Thread-safe: may be called from the main thread while ``run()`` executes
        in a pool thread.  Schedules cancellation of the in-flight asyncio task
        so the awaiting HTTP call is interrupted immediately rather than waiting
        for a timeout.
        """
        self._cancel_requested.set()
        with self._loop_lock:
            loop = self._loop
            task = self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # Loop already closed – nothing to do.
                pass

    def _emit(self, signal: Any, *args: Any) -> None:
        """Emit a Qt signal, silently swallowing errors on already-deleted objects."""
        if self._cancel_requested.is_set():
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            # QObject (signal source) was deleted before the thread finished.
            pass

    def run(self) -> None:
        try:
            self._run_impl()
        finally:
            self._notify_cleanup()

    def _run_impl(self) -> None:
        self._emit(self.signals.started, self.job_id)
        if self._cancel_requested.is_set():
            return

        def on_step(step: str, data: Any) -> None:
            self._emit(self.signals.step, self.job_id, step, _compact_payload(step, data))

        runtime = None
        try:
            if self._cancel_requested.is_set():
                return
            runtime = create_runtime_services(mock=self.mock)
            from novel_forge.pipeline.long.human_decision import CallbackDecisionProvider

            self._decision_provider = CallbackDecisionProvider(
                lambda payload: self._emit(
                    self.signals.decision_required,
                    self.job_id,
                    payload,
                )
            )
            runtime.human_decision_provider = self._decision_provider

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._loop_lock:
                self._loop = loop

            async def _run_task() -> Any:
                if self._cancel_requested.is_set():
                    raise asyncio.CancelledError
                current = asyncio.current_task()
                with self._loop_lock:
                    self._task = current
                try:
                    if self._cancel_requested.is_set():
                        raise asyncio.CancelledError
                    return await self.task(runtime, on_step)
                finally:
                    try:
                        await runtime.shutdown()
                    except Exception as exc:
                        logger.warning("Worker %s: runtime shutdown failed: %s", self.job_id, exc)

            try:
                result = loop.run_until_complete(_run_task())
            finally:
                # Drain any remaining callbacks and close the loop cleanly.
                try:
                    pending = asyncio.all_tasks(loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    logger.exception(
                        "Worker %s: failed to drain pending tasks during loop cleanup", self.job_id
                    )
                finally:
                    loop.close()
                with self._loop_lock:
                    self._loop = None
                    self._task = None

        except asyncio.CancelledError:
            # Task was cancelled by request_cancel() (app shutdown) – exit cleanly.
            return
        except Exception as exc:
            if self._cancel_requested.is_set():
                return
            # Ensure runtime is shut down even if the loop setup itself failed
            # (e.g. before _run_task's finally had a chance to run).
            if runtime is not None:
                self._emit(self.signals.cleanup_runtime, runtime)
            summary = summarize_desktop_error(exc)
            self._emit(self.signals.failed, self.job_id, summary.as_payload())
            return

        self._emit(self.signals.finished, self.job_id, result)

    def provide_decision(
        self,
        decision_id: str,
        choice: str,
        *,
        custom_text: str = "",
        approval_version: str = "",
    ) -> bool:
        provider = self._decision_provider
        if provider is None:
            return False
        return bool(
            provider.provide_decision(
                decision_id,
                choice,
                custom_text=custom_text,
                approval_version=approval_version,
            )
        )

    def _notify_cleanup(self) -> None:
        """Called unconditionally when run() exits.  Notifies the job manager."""
        notify = self._cleanup_notify
        if notify is not None:
            try:
                notify()
            except Exception as exc:
                logger.warning("Worker %s: cleanup notify callback failed: %s", self.job_id, exc)


class DesktopJobManager(QObject):
    """Qt adapter over JobService plus desktop history and lifecycle signals."""

    jobs_changed = Signal()
    job_completed = Signal(str)
    # Emitted once when a brand-new job is submitted.  Receivers can bind the
    # job list immediately (bypassing the 90 ms debounce) so the user sees the
    # QUEUED card before any LLM work completes.
    job_submitted = Signal()
    # Emitted once after persisted history finishes loading (async background).
    history_loaded = Signal()
    # Emitted (coalesced at 50 ms) when a step payload contains tokens_so_far.
    # Parameters: project_id, cumulative_tokens, cumulative_cost_usd.
    token_update = Signal(str, int, float)
    # Emitted when a job mutates a workspace section. The window listens to
    # this Qt signal to schedule a fresh snapshot before rebinding pages.
    section_changed = Signal(str, str)
    # Emitted when the pipeline asks for a high-risk human repair decision.
    # Parameters: job_id, payload.
    decision_required = Signal(str, object)

    def __init__(
        self,
        *,
        storage_root: Path | None = None,
        load_persisted_history: bool = True,
        job_service: JobService | None = None,
    ) -> None:
        super().__init__()
        self._jobs: dict[str, DesktopJobRecord] = {}
        self._workers: dict[str, _WorkspaceJobWorker] = {}
        self._app_job_ids: set[str] = set()
        self._cancelling: dict[str, str] = {}
        self._lock = Lock()
        self._thread_pool = desktop_thread_pools().job_pool
        self._storage_root: Path | None = (
            storage_root.expanduser().resolve()
            if storage_root is not None
            else self._resolve_storage_root()
        )
        self._sleep_inhibitor = SleepInhibitor()
        self._dependency_resolver = DependencyResolver()
        self._event_bus = _get_event_bus()
        self._waiting_jobs: dict[str, WaitingJob] = {}
        self._closed = False
        self._job_service = job_service or JobService(
            storage_root=self._storage_root,
            load_persisted_history=False,
            shadow_recorder=self._create_shadow_recorder(),
        )
        self._app_event_subscription = self._open_app_event_subscription()
        list_app_jobs = getattr(self._job_service, "list", None)
        if callable(list_app_jobs):
            for app_record in list_app_jobs():
                self._sync_app_record(app_record)
        self._app_event_timer = QTimer(self)
        self._app_event_timer.setInterval(_APP_EVENT_POLL_MS)
        self._app_event_timer.timeout.connect(self._drain_app_service_events)
        self._app_event_timer.start()
        self._ui_active = True
        # A sleeping UI can accumulate up to 2,000 app-service events. Replaying
        # all durable events synchronously on wake used to monopolise the GUI
        # thread. Keep ordering intact while yielding between small batches.
        self._wake_replay_events: deque[JobEvent] = deque()
        self._wake_replay_active = False
        self._wake_replay_timer = QTimer(self)
        self._wake_replay_timer.setSingleShot(True)
        self._wake_replay_timer.setInterval(0)
        self._wake_replay_timer.timeout.connect(self._replay_wake_event_batch)
        # Dirty-flag coalescing for jobs_changed signal: multiple rapid
        # _handle_step calls within _JOB_BIND_COALESCE_MS are collapsed into
        # a single jobs_changed.emit() via a reusable single-shot QTimer.
        self._jobs_dirty: bool = False
        self._jobs_bind_timer = QTimer(self)
        self._jobs_bind_timer.setSingleShot(True)
        self._jobs_bind_timer.setInterval(_JOB_BIND_COALESCE_MS)
        self._jobs_bind_timer.timeout.connect(self._flush_dirty_jobs)
        # Dirty-flag coalescing for token_update signal (independent from jobs).
        self._token_dirty: bool = False
        self._token_timer = QTimer(self)
        self._token_timer.setSingleShot(True)
        self._token_timer.setInterval(_JOB_BIND_COALESCE_MS)
        self._token_timer.timeout.connect(self._flush_dirty_tokens)
        self._subscribe_to_events()
        # Global poller for waiting jobs: periodically iterate _waiting_jobs by
        # project_id and promote those whose dependencies are now satisfied.
        self._waiting_poll_timer = QTimer(self)
        self._waiting_poll_timer.setInterval(get_settings().long_waiting_job_poll_interval_ms)
        self._waiting_poll_timer.timeout.connect(self._poll_waiting_jobs)
        self._waiting_poll_timer.start()
        self._history_loaded: bool = not load_persisted_history
        self._history_event = threading.Event()
        self._history_worker: _HistoryWorker | None = None
        if load_persisted_history:
            history_worker = _HistoryWorker(
                self._load_persisted_history,
                self._history_event.set,
            )
            history_worker.signals.finished.connect(self._handle_history_loaded)
            self._history_worker = history_worker
            history_worker.submit()
        else:
            self._history_event.set()

    def _subscribe_to_events(self) -> None:
        self._event_bus.subscribe(INIT_STARTED, self._on_init_started)
        self._event_bus.subscribe(INIT_COMPLETED, self._on_init_completed)
        self._event_bus.subscribe(OUTLINE_UPDATED, self._on_outline_updated)
        self._event_bus.subscribe(CHAPTER_COMPLETED, self._on_chapter_completed)

    def _unsubscribe_from_events(self) -> None:
        for event_type, callback in (
            (INIT_STARTED, self._on_init_started),
            (INIT_COMPLETED, self._on_init_completed),
            (OUTLINE_UPDATED, self._on_outline_updated),
            (CHAPTER_COMPLETED, self._on_chapter_completed),
        ):
            try:
                self._event_bus.unsubscribe(event_type, callback)
            except Exception:
                logger.debug("Failed to unsubscribe desktop job manager event", exc_info=True)

    def _open_app_event_subscription(self) -> Any:
        try:
            return self._job_service.open_subscription(
                max_queue_size=_APP_EVENT_SUBSCRIPTION_MAX_QUEUE
            )
        except TypeError:
            # Some tests inject a minimal JobService stub that predates bounded
            # subscriptions. Keep those stubs compatible while production gets
            # backpressure.
            return self._job_service.open_subscription()

    # ------------------------------------------------------------------
    # History loading (async) — test helpers
    # ------------------------------------------------------------------

    def wait_for_history(self, timeout_ms: int = 5000) -> None:
        """Block the calling thread until async history loading completes.

        **Tests only** — never call from the GUI thread in production.
        """
        if self._history_loaded:
            return
        self._history_event.wait(timeout_ms / 1000.0)

    def _handle_history_loaded(self) -> None:
        if self._closed:
            return
        if self._history_loaded:
            return
        self._history_loaded = True
        self._history_worker = None
        self._history_event.set()
        self.history_loaded.emit()
        self.jobs_changed.emit()

    async def _on_init_started(self, event: ProjectEvent) -> None:
        pass

    async def _on_init_completed(self, event: ProjectEvent) -> None:
        pass

    async def _on_outline_updated(self, event: ProjectEvent) -> None:
        pass

    async def _on_chapter_completed(self, event: ProjectEvent) -> None:
        pass

    def jobs(self) -> list[DesktopJobRecord]:
        with self._lock:
            # Snapshot: copy records so callers don't see mid-mutation state.
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    @property
    def storage_root(self) -> Path | None:
        return self._storage_root

    @property
    def engine_job_service(self) -> JobService:
        """Expose the shared Engine job service to presenter-only desktop pages."""

        return self._job_service

    def app_jobs(self) -> list[AppJobRecord]:
        """Return current jobs projected into the shared app-service contract."""
        return [desktop_job_record_to_app_record(record) for record in self.jobs()]

    def app_job(self, job_id: str) -> AppJobRecord | None:
        """Return one current job projected into the shared app-service contract."""
        with self._lock:
            record = self._jobs.get(job_id)
        return desktop_job_record_to_app_record(record) if record is not None else None

    def start_chapter_autorun(
        self,
        *,
        project_id: str,
        chapter_number: int,
        mode: str,
        writing_mode: str,
        force: bool,
        skip_done: bool,
        mock: bool = False,
    ) -> DesktopJobRecord | None:
        """Control the Engine state machine; never decide chapter transitions here."""

        start = getattr(self._job_service, "start_book_autorun", None)
        if not callable(start):
            return None
        app_record = start(
            project_id=project_id,
            chapter_number=chapter_number,
            mode=mode,
            launch_key=f"desktop:{project_id}:{chapter_number}:{mode}",
            writing_mode=writing_mode,
            force=force,
            skip_done=skip_done,
            mock=mock,
        )
        if app_record is None:
            return None
        record, created = self._sync_app_record(app_record)
        self.jobs_changed.emit()
        if created:
            self.job_submitted.emit()
        return record

    def book_autorun_state(self, project_id: str) -> Any | None:
        getter = getattr(self._job_service, "book_autorun_state", None)
        return getter(project_id) if callable(getter) else None

    def active_book_autorun_project_ids(self) -> list[str]:
        getter = getattr(self._job_service, "active_book_autorun_project_ids", None)
        return list(getter()) if callable(getter) else []

    def pause_book_autorun(self, project_id: str, *, reason: str) -> str:
        pause = getattr(self._job_service, "pause_book_autorun", None)
        return str(pause(project_id, reason=reason) or "") if callable(pause) else ""

    @staticmethod
    def _request_payload(request: object) -> dict[str, Any]:
        if hasattr(request, "model_dump"):
            payload = request.model_dump(mode="json")
            return payload if isinstance(payload, dict) else {}
        return {}

    def _make_app_job_command(
        self,
        *,
        kind: JobKind,
        request: object,
        label: str,
        project_id: str,
        mock: bool,
        command_name: str,
        job_id: str = "",
        record_kind: JobKind | str = "",
    ) -> JobCommand:
        return JobCommand(
            job_id=job_id,
            kind=kind,
            record_kind=record_kind,
            payload=self._request_payload(request),
            label=label,
            project_id=project_id,
            mock=mock,
            metadata={"source": "desktop", "command_name": command_name},
        )

    def _sync_app_record(self, app_record: AppJobRecord) -> tuple[DesktopJobRecord, bool]:
        desktop_record = app_job_record_to_desktop_record(app_record)
        with self._lock:
            existing = self._jobs.get(desktop_record.job_id)
            if existing is None:
                self._jobs[desktop_record.job_id] = desktop_record
                self._app_job_ids.add(desktop_record.job_id)
                return desktop_record, True

            existing.kind = desktop_record.kind
            existing.label = desktop_record.label
            existing.project_id = desktop_record.project_id
            existing.status = desktop_record.status
            existing.created_at = desktop_record.created_at
            existing.updated_at = desktop_record.updated_at
            existing.current_step = desktop_record.current_step
            existing.current_step_payload = desktop_record.current_step_payload
            existing.error = desktop_record.error
            existing.error_summary = desktop_record.error_summary
            if desktop_record.result or not existing.result:
                existing.result = desktop_record.result
            existing.events = desktop_record.events
            existing.resolved_error_entry_ids = desktop_record.resolved_error_entry_ids
            existing.cumulative_tokens = desktop_record.cumulative_tokens
            existing.cumulative_cost_usd = desktop_record.cumulative_cost_usd
            self._app_job_ids.add(desktop_record.job_id)
            return existing, False

    def _submit_app_service_job(
        self,
        command: JobCommand,
        *,
        emit_submitted: bool,
    ) -> DesktopJobRecord:
        probe_kind = command.record_kind or command.kind
        kind_value = probe_kind.value if hasattr(probe_kind, "value") else str(probe_kind)
        probe = DesktopJobRecord(
            job_id=command.job_id,
            kind=kind_value,
            label=command.label,
            project_id=command.project_id,
        )
        conflict = self._find_active_write_conflict(
            probe,
            ignore_job_ids={command.job_id} if command.job_id else None,
        )
        if conflict is not None:
            return conflict
        app_record = self._job_service.submit(command)
        record, created = self._sync_app_record(app_record)
        self._update_sleep_inhibitor()
        self.jobs_changed.emit()
        if emit_submitted and created:
            self.job_submitted.emit()
        return record

    def _drain_app_service_events(self) -> None:
        if self._closed or self._wake_replay_active:
            return
        events: list[JobEvent] = []
        subscription = self._app_event_subscription
        for _ in range(_APP_EVENT_DRAIN_LIMIT):
            event = subscription.get(timeout=0)
            if event is None:
                break
            events.append(event)
        for event in _coalesce_stream_delta_events(events):
            if self._closed:
                return
            self._handle_app_service_event(event)

    def set_ui_active(self, active: bool) -> None:
        """Reduce presentation work while the desktop app is not foreground."""
        active = bool(active)
        if active == self._ui_active or self._closed:
            return
        self._ui_active = active
        if not active:
            self._jobs_bind_timer.stop()
            self._token_timer.stop()
            self._app_event_timer.setInterval(_APP_EVENT_BACKGROUND_POLL_MS)
            return

        self._app_event_timer.setInterval(_APP_EVENT_POLL_MS)
        self.flush_stale_events_after_wake()
        if not self._wake_replay_active and not self._app_event_timer.isActive():
            self._app_event_timer.start()
        if self._jobs_dirty and not self._jobs_bind_timer.isActive():
            self._jobs_bind_timer.setInterval(_JOB_BIND_COALESCE_MS)
            self._jobs_bind_timer.start()
        if self._token_dirty and not self._token_timer.isActive():
            self._token_timer.start()

    def flush_stale_events_after_wake(self) -> int:
        """Discard stale stream deltas and queue durable events for ordered replay.

        Job lifecycle, decision, snapshot and ordinary step events are state
        transitions and must not be lost: dropping a terminal event can leave
        the Desktop UI showing a completed job as permanently running. Only
        ``llm_stream_delta`` events are presentation-only and safe to discard;
        the next model-call/job snapshot reconstructs the durable state.

        The first bounded batch is applied immediately for fast visual recovery.
        Remaining events are replayed through a zero-delay timer with an 8 ms
        per-turn budget so a large post-sleep backlog cannot freeze the UI.

        Returns the number of discarded stream-delta events.
        """
        if self._closed:
            return 0
        discarded = 0
        replay: list[JobEvent] = []
        subscription = self._app_event_subscription
        while True:
            event = subscription.get(timeout=0)
            if event is None:
                break
            event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
            if event_type == JobEventType.JOB_STEP.value and event.step == "llm_stream_delta":
                discarded += 1
            else:
                replay.append(event)
        if replay:
            if not self._wake_replay_active:
                self._wake_replay_active = True
                self._app_event_timer.stop()
            self._wake_replay_events.extend(replay)
            if not self._wake_replay_timer.isActive():
                self._replay_wake_event_batch()
        if discarded or replay:
            logger.info(
                "Queued app-event recovery after wake | discarded_stream_deltas=%d | durable=%d",
                discarded,
                len(replay),
            )
        return discarded

    def _replay_wake_event_batch(self) -> None:
        """Replay one ordered wake-recovery slice without monopolising Qt."""
        if self._closed:
            self._wake_replay_events.clear()
            self._wake_replay_active = False
            return

        started = time.perf_counter()
        processed = 0
        while self._wake_replay_events and processed < _WAKE_EVENT_REPLAY_BATCH_SIZE:
            event = self._wake_replay_events.popleft()
            self._handle_app_service_event(event)
            processed += 1
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms >= _WAKE_EVENT_REPLAY_BUDGET_MS:
                break

        if self._wake_replay_events:
            self._wake_replay_timer.start()
            return

        self._wake_replay_active = False
        if not self._closed and not self._app_event_timer.isActive():
            self._app_event_timer.start()
        if self._ui_active and self._jobs_dirty and not self._jobs_bind_timer.isActive():
            self._jobs_bind_timer.setInterval(_JOB_BIND_COALESCE_MS)
            self._jobs_bind_timer.start()
        if self._ui_active and self._token_dirty and not self._token_timer.isActive():
            self._token_timer.start()
        logger.debug("Wake event replay complete")

    def _handle_app_service_event(self, event: JobEvent) -> None:
        if self._closed:
            return
        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        if event_type == JobEventType.JOB_SNAPSHOT.value:
            raw_record = event.payload.get("record")
            if isinstance(raw_record, dict):
                try:
                    self._sync_app_record(AppJobRecord.model_validate(raw_record))
                    self.jobs_changed.emit()
                except Exception:
                    logger.debug("Failed to sync app-service job snapshot", exc_info=True)
            return

        with self._lock:
            known_app_job = event.job_id in self._app_job_ids
        if not known_app_job:
            return

        if event_type == JobEventType.JOB_STARTED.value:
            self._handle_started(event.job_id)
        elif event_type == JobEventType.JOB_STEP.value:
            self._handle_step(event.job_id, event.step, event.payload)
        elif event_type == JobEventType.DECISION_REQUIRED.value:
            self._handle_decision_required(event.job_id, event.payload)
        elif event_type in {
            JobEventType.JOB_SUCCEEDED.value,
            JobEventType.JOB_PAUSED.value,
        }:
            self._handle_finished(event.job_id, event.payload)
        elif event_type == JobEventType.JOB_FAILED.value:
            self._handle_failed(event.job_id, event.payload)
        elif event_type == JobEventType.JOB_CANCELLED.value:
            with self._lock:
                record = self._jobs.get(event.job_id)
                already_failed = record is not None and record.status == DesktopJobState.FAILED
            if not already_failed:
                self._handle_failed(event.job_id, event.payload)

    @staticmethod
    def _resolve_storage_root() -> Path | None:
        try:
            root = Path(get_settings().storage_root).expanduser()
            return root.resolve()
        except Exception:
            return None

    @staticmethod
    def _create_shadow_recorder() -> Any:
        """Create a control-plane shadow recorder (disabled if feature off)."""
        try:
            from novel_forge.control_plane.factory import create_shadow_recorder

            return create_shadow_recorder(get_settings())
        except Exception:
            return None

    def _history_path(self, project_id: str) -> Path | None:
        if not project_id or not self._storage_root:
            return None
        return ProjectLayout(self._storage_root / project_id).states_dir / _JOB_HISTORY_FILENAME

    @staticmethod
    def _read_history_records(path: Path) -> list[DesktopJobRecord]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        records: list[DesktopJobRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            record = _job_from_payload(item)
            if record is None:
                continue
            records.append(record)
        return records

    @staticmethod
    def _write_history_records(path: Path, records: list[DesktopJobRecord]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [_job_to_payload(record) for record in records[:_MAX_PERSISTED_JOBS_PER_PROJECT]]
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _load_persisted_history(self) -> None:
        root = self._storage_root
        if root is None or not root.exists():
            return

        loaded: dict[str, DesktopJobRecord] = {}
        try:
            project_dirs = [
                item
                for item in root.iterdir()
                if item.is_dir()
                and not is_generated_test_project_id(item.name)
                and item.name.casefold() != "dlq"
            ]
        except OSError:
            return

        terminal = {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
            DesktopJobState.PAUSED,
        }
        for project_dir in project_dirs:
            path = ProjectLayout(project_dir).states_dir / _JOB_HISTORY_FILENAME
            for record in self._read_history_records(path):
                if record.status not in terminal:
                    continue
                if not record.project_id:
                    continue
                if task_flow_job_is_inactive(record, storage_root=self._storage_root):
                    continue
                loaded[record.job_id] = record

        if not loaded:
            return

        with self._lock:
            for job_id, record in loaded.items():
                self._jobs.setdefault(job_id, record)

    def _persist_terminal_job(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            snapshot = _job_from_payload(_job_to_payload(record))

        if snapshot is None or not snapshot.project_id:
            return
        if snapshot.status not in {
            DesktopJobState.SUCCEEDED,
            DesktopJobState.FAILED,
            DesktopJobState.PAUSED,
        }:
            return

        path = self._history_path(snapshot.project_id)
        if path is None:
            return

        try:
            history = self._read_history_records(path)
            merged = [snapshot]
            merged.extend(item for item in history if item.job_id != snapshot.job_id)
            self._write_history_records(path, merged)
            self._archive_job_errors(snapshot)
        except OSError:
            return

    def _archive_job_errors(self, record: DesktopJobRecord) -> None:
        if self._storage_root is None:
            return
        try:
            TaskFlowErrorArchive(self._storage_root).archive_job(record)
        except Exception:
            return

    def submit_short(self, request: RunShortRequest, *, mock: bool = False) -> DesktopJobRecord:
        project_id = request.project_id.strip()
        label = f"短篇创作 · {project_id or '自动项目'}"
        command = self._make_app_job_command(
            kind=JobKind.RUN_SHORT,
            request=request,
            label=label,
            project_id=project_id,
            mock=mock,
            command_name="desktop-run-short",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_init_long(
        self,
        request: InitLongRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        project_id = request.project_id.strip()
        label = f"长篇立项 · {project_id or '自动项目'}"
        command = self._make_app_job_command(
            kind=JobKind.INIT_LONG,
            request=request,
            label=label,
            project_id=project_id,
            mock=mock,
            command_name="desktop-init-long",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_init_repair_retry(
        self,
        project_id: str,
        *,
        reset_repair_history: bool,
        mock: bool = False,
    ) -> DesktopJobRecord:
        """Retry the failed long-init readiness tail from persisted init metadata."""
        project_value = str(project_id or "").strip()
        if not project_value:
            raise ValueError("缺少项目 ID，无法提交初始化修复复审。")
        probe = DesktopJobRecord(
            job_id="",
            kind="init_long",
            label=f"长篇立项 · {project_value}",
            project_id=project_value,
        )
        conflict = self._find_active_write_conflict(probe)
        if conflict is not None:
            return conflict

        request = self._init_long_request_from_meta(project_value)
        self._reset_init_readiness_retry_state(
            project_value,
            reset_repair_history=reset_repair_history,
        )
        return self.submit_init_long(request, mock=mock)

    def _init_long_request_from_meta(self, project_id: str) -> InitLongRequest:
        if self._storage_root is None:
            raise ValueError("存储目录尚不可用，无法读取立项参数。")
        layout = ProjectLayout(self._storage_root / project_id)
        meta_path = layout.init_request_meta_path
        if not meta_path.exists():
            raise ValueError("缺少 states/init_request_meta.json，无法安全重试立项修复。")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"读取立项参数失败：{exc}") from exc
        request_payload = meta.get("request") if isinstance(meta, dict) else None
        if not isinstance(request_payload, dict):
            raise ValueError("立项参数元数据不完整，无法安全重试。")
        init_input = request_payload.get("init_input")
        generation_options = request_payload.get("generation_options")
        if not isinstance(init_input, dict) or not isinstance(generation_options, dict):
            raise ValueError("立项参数元数据缺少 init_input 或 generation_options。")
        premise = str(init_input.get("premise") or "").strip()
        if not premise:
            raise ValueError("立项参数里缺少 premise，无法安全重试。")

        def _int_option(key: str, fallback: int) -> int:
            try:
                return int(generation_options.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        preferences = generation_options.get("blueprint_element_preferences")
        if not isinstance(preferences, dict):
            preferences = {}
        return InitLongRequest(
            project_id=project_id,
            premise=premise,
            genre=str(init_input.get("genre") or "other"),
            tone=str(init_input.get("tone") or "neutral"),
            total_chapters=_int_option("total_chapters", 20),
            words_per_chapter=_int_option("words_per_chapter", 3000),
            volume_mode=str(generation_options.get("volume_mode_setting") or "auto"),
            chapters_per_volume=_int_option("chapters_per_volume_setting", 0),
            title=str(init_input.get("title") or ""),
            language=str(init_input.get("language") or "zh"),
            characters_hint=str(init_input.get("characters_hint") or ""),
            world_hint=str(init_input.get("world_hint") or ""),
            conflict_hint=str(init_input.get("conflict_hint") or ""),
            pov_hint=str(init_input.get("pov_hint") or ""),
            opening_style=str(init_input.get("opening_style") or ""),
            ending_style=str(init_input.get("ending_style") or ""),
            extra_instructions=str(init_input.get("extra_instructions") or ""),
            polish_hint=str(generation_options.get("polish_hint") or ""),
            research_enabled=bool(generation_options.get("research_enabled", False)),
            research_provider=str(generation_options.get("research_provider") or "auto"),
            research_query_hint=str(generation_options.get("research_query_hint") or ""),
            blueprint_element_preferences=preferences,
        )

    def _reset_init_readiness_retry_state(
        self,
        project_id: str,
        *,
        reset_repair_history: bool,
    ) -> None:
        if self._storage_root is None:
            return
        layout = ProjectLayout(self._storage_root / project_id)
        source_artifacts_only = self._init_readiness_blocks_only_source_artifacts(layout)
        blocked_artifacts = (
            set() if source_artifacts_only else self._init_readiness_blocked_artifacts(layout)
        )
        stale_report_paths = (
            (
                layout.reports_dir / "init_readiness.json",
                layout.reports_dir / "outline_inheritance.json",
            )
            if not source_artifacts_only
            else ()
        )
        for path in stale_report_paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        if reset_repair_history:
            if source_artifacts_only:
                self._drop_init_repair_history_for_artifact(
                    layout,
                    artifact="source_artifacts",
                )
                self._drop_init_repair_history_for_artifact(
                    layout,
                    artifact="chapter_contracts",
                )
            else:
                artifacts = blocked_artifacts or {"outline"}
                for artifact in artifacts:
                    self._drop_init_repair_history_for_artifact(layout, artifact=artifact)

    @staticmethod
    def _init_readiness_blocks_only_source_artifacts(layout: ProjectLayout) -> bool:
        path = layout.reports_dir / "init_readiness.json"
        if not path.exists():
            return False
        try:
            readiness = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(readiness, dict) or bool(readiness.get("allowed", False)):
            return False
        stages = readiness.get("stages")
        if not isinstance(stages, dict):
            return False
        source_stage = stages.get("source_artifacts")
        if not isinstance(source_stage, dict) or not bool(source_stage.get("blocked", False)):
            return False
        for stage_name, stage_payload in stages.items():
            if stage_name == "source_artifacts" or not isinstance(stage_payload, dict):
                continue
            if bool(stage_payload.get("blocked", False)):
                return False
        remaining = readiness.get("remaining_issues")
        if isinstance(remaining, list) and remaining:
            return all(
                isinstance(issue, dict) and issue.get("stage") == "source_artifacts"
                for issue in remaining
            )
        return True

    @staticmethod
    def _init_readiness_blocked_artifacts(layout: ProjectLayout) -> set[str]:
        path = layout.reports_dir / "init_readiness.json"
        if not path.exists():
            return set()
        try:
            readiness = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(readiness, dict):
            return set()
        aliases = {
            "blueprint": "blueprint",
            "narrative_blueprint": "blueprint",
            "plans/narrative_blueprint.json": "blueprint",
            "outline": "outline",
            "story_outline": "outline",
            "outline.json": "outline",
            "chapter_contracts": "chapter_contracts",
            "contracts": "chapter_contracts",
            "plans/chapter_contracts.json": "chapter_contracts",
            "source_artifacts": "source_artifacts",
        }
        stage_hints = {
            "blueprint_coherence": "blueprint",
            "outline_inheritance": "outline",
            "contract_coherence": "chapter_contracts",
            "claim_contract_coverage": "chapter_contracts",
            "source_artifacts": "source_artifacts",
        }
        artifacts: set[str] = set()

        def add(value: object) -> None:
            raw = str(value or "").strip().lower()
            artifact = aliases.get(raw, raw)
            if artifact:
                artifacts.add(artifact)

        remaining = readiness.get("remaining_issues")
        if isinstance(remaining, list):
            for issue in remaining:
                if not isinstance(issue, dict):
                    continue
                scopes = issue.get("repair_scope")
                scoped = False
                if isinstance(scopes, list):
                    for scope in scopes:
                        if isinstance(scope, dict):
                            add(scope.get("artifact"))
                            scoped = True
                if not scoped:
                    add(stage_hints.get(str(issue.get("stage") or "").strip(), ""))
        return artifacts

    @staticmethod
    def _drop_init_repair_history_for_artifact(layout: ProjectLayout, *, artifact: str) -> None:
        path = layout.reports_dir / "init_artifact_repair.json"
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        repairs = payload.get("repairs") if isinstance(payload, dict) else None
        if not isinstance(repairs, list):
            return
        normalized_artifact = artifact.strip().lower()
        remaining = [
            item
            for item in repairs
            if not (
                isinstance(item, dict)
                and str(item.get("artifact") or "").strip().lower() == normalized_artifact
            )
        ]
        if len(remaining) == len(repairs):
            return
        try:
            atomic_write_json(path, {"repairs": remaining})
        except OSError:
            return

    def submit_run_chapter(
        self,
        request: RunChapterRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"章节续写 · {request.project_id} / 第 {request.chapter_number} 章"
        command = self._make_app_job_command(
            kind=JobKind.RUN_CHAPTER,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-run-chapter",
        )

        if self._storage_root is not None:
            layout = ProjectLayout(self._storage_root / request.project_id)
            if not self._dependency_resolver.is_ready(
                layout, Operation.RUN_CHAPTER, chapter_num=request.chapter_number
            ):
                return self._queue_waiting_job(
                    kind="run_chapter",
                    label=label,
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    app_command=command,
                ) or DesktopJobRecord(
                    job_id=uuid4().hex,
                    kind="run_chapter",
                    label=label,
                    project_id=request.project_id,
                )

        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_post_archive_tts(
        self,
        *,
        project_id: str,
        chapter_number: int,
        parent_job_id: str = "",
    ) -> DesktopJobRecord | None:
        """Queue automatic audio as an independent, visible, cancellable job."""

        settings = get_settings()
        if not (settings.tts_enabled and settings.tts_auto_trigger_after_chapter):
            return None
        with self._lock:
            for existing in self._jobs.values():
                if (
                    existing.kind == JobKind.TTS_POST_ARCHIVE.value
                    and existing.project_id == project_id
                    and _job_chapter_num(existing) == chapter_number
                    and existing.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}
                ):
                    return existing
        if self._storage_root is None:
            return None
        from novel_forge.workspace.post_archive_tts import queue_post_archive_tts

        queued = queue_post_archive_tts(
            settings=settings,
            storage=FileSystemStorage(self._storage_root),
            project_id=project_id,
            chapter_number=chapter_number,
            parent_job_id=parent_job_id,
        )
        if queued is None:
            return None
        request = TTSPostArchiveRequest(
            project_id=project_id,
            chapter_number=chapter_number,
            parent_job_id=parent_job_id,
        )
        command = self._make_app_job_command(
            kind=JobKind.TTS_POST_ARCHIVE,
            request=request,
            label=f"自动配音 · {project_id} / 第 {chapter_number} 章",
            project_id=project_id,
            mock=str(queued.get("provider") or "") == "mock",
            command_name="desktop-tts-post-archive",
        )
        command.metadata.update({"trigger": "post_archive", "parent_job_id": parent_job_id})
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_prepare_chapter(
        self,
        request: PrepareChapterRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"章节方案 · {request.project_id} / 第 {request.chapter_number} 章"
        command = self._make_app_job_command(
            kind=JobKind.PREPARE_CHAPTER,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-prepare-chapter",
        )

        if self._storage_root is not None:
            layout = ProjectLayout(self._storage_root / request.project_id)
            active_init = self._active_init_for_project(request.project_id)
            prepare_probe = DesktopJobRecord(
                job_id="",
                kind="prepare_chapter",
                label=label,
                project_id=request.project_id,
                result={"chapter_number": request.chapter_number},
            )
            dependencies_ready = self._dependency_resolver.is_ready(
                layout, Operation.PREPARE_CHAPTER, chapter_num=request.chapter_number
            )
            waits_for_init_outline = active_init is not None and not self._prepare_can_overlap_init(
                prepare_probe, active_init
            )
            if not dependencies_ready or waits_for_init_outline:
                return self._queue_waiting_job(
                    kind="prepare_chapter",
                    label=label,
                    project_id=request.project_id,
                    chapter_number=request.chapter_number,
                    app_command=command,
                ) or DesktopJobRecord(
                    job_id=uuid4().hex,
                    kind="prepare_chapter",
                    label=label,
                    project_id=request.project_id,
                )

        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_resolve_chapter_checkpoint(
        self,
        request: ResolveChapterCheckpointRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        resolve_autorun = getattr(self._job_service, "resolve_book_autorun_checkpoint", None)
        if callable(resolve_autorun):
            app_record = resolve_autorun(
                project_id=request.project_id,
                checkpoint_id=request.checkpoint_id,
                option_id=request.option_id,
                notes=request.notes,
                force=request.force,
            )
            if app_record is not None:
                record, created = self._sync_app_record(app_record)
                self.jobs_changed.emit()
                if created:
                    self.job_submitted.emit()
                self._retire_paused_jobs_for_chapter(
                    request.project_id,
                    request.chapter_number,
                    exclude_job_id=record.job_id,
                )
                return record
        _option_label = {
            "write_now": "方案执行（阶段 1/2）",
            "edit_plan_and_write": "方案执行（阶段 1/2）",
            "regenerate_plan": "方案重生",
            "regenerate_plan_with_notes": "方案重生",
            "resume_from_progress": "断点恢复",
            "accept_and_finalize": "归档执行（阶段 2/2）",
            "apply_repairs_and_finalize": "归档前修复（阶段 2/2）",
            "adjust_outline_and_finalize": "归档前调整（阶段 2/2）",
            "pause_for_human": "归档暂停（阶段 2/2）",
        }.get(request.option_id, "章节决策")
        label = f"{_option_label} · {request.project_id} / 第 {request.chapter_number} 章"

        # Plan-regeneration options only run prepare_plan_checkpoint (不生成初稿, 不做质量
        # 检查).  Using kind="prepare_chapter" gives the correct 3-step visual indicator
        # (章节上下文 → 桥接与方案 → 方案确认) instead of the full write flow,
        # preventing the user from seeing 初稿生成/质量检查 steps that will never execute.
        #
        # Finalize-only options (accept/apply-repairs/adjust-outline/pause) skip the
        # draft and quality stages already completed in a prior write task.  Using
        # kind="resolve_chapter_checkpoint_finalize" shows only archive-related
        # steps so the task flow no longer repeats the write/review pipeline.
        if request.option_id in {"regenerate_plan", "regenerate_plan_with_notes"}:
            job_kind = "prepare_chapter"
        elif request.option_id in {
            "accept_and_finalize",
            "apply_repairs_and_finalize",
            "adjust_outline_and_finalize",
            "pause_for_human",
        }:
            job_kind = "resolve_chapter_checkpoint_finalize"
        else:
            job_kind = "resolve_chapter_checkpoint"

        command_kind = (
            JobKind.RESOLVE_CHAPTER_CHECKPOINT_FINALIZE
            if job_kind == "resolve_chapter_checkpoint_finalize"
            else JobKind.RESOLVE_CHAPTER_CHECKPOINT
        )
        command = self._make_app_job_command(
            kind=command_kind,
            record_kind=job_kind,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-resolve-chapter-checkpoint",
        )
        record = self._submit_app_service_job(command, emit_submitted=True)
        # Immediately retire old PAUSED jobs for this chapter so the UI
        # no longer shows a stale "等待决策" progress bar once the user
        # has already accepted the checkpoint.
        self._retire_paused_jobs_for_chapter(
            request.project_id,
            request.chapter_number,
            exclude_job_id=record.job_id,
        )
        return record

    def submit_repair_continuity(
        self,
        request: RepairContinuityRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        issue_count = len(request.issue_indices) if request.issue_indices else "全部"
        label = (
            f"连贯性修复（{issue_count} 条问题）"
            f" · {request.project_id} / 第 {request.chapter_number} 章"
        )
        command = self._make_app_job_command(
            kind=JobKind.REPAIR_CONTINUITY,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-repair-continuity",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_repair_causal(
        self,
        request: RepairCausalRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        issue_count = len(request.issue_indices) if request.issue_indices else "全部"
        label = (
            f"因果链修复（{issue_count} 条问题）"
            f" · {request.project_id} / 第 {request.chapter_number} 章"
        )
        command = self._make_app_job_command(
            kind=JobKind.REPAIR_CAUSAL,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-repair-causal",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_repair_issues(
        self,
        request: RepairIssuesRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        continuity_count = (
            len(request.continuity_issue_indices) if request.continuity_issue_indices else 0
        )
        causal_count = len(request.causal_issue_indices) if request.causal_issue_indices else 0
        parts = []
        if continuity_count > 0:
            parts.append(f"连贯性 {continuity_count} 条")
        if causal_count > 0:
            parts.append(f"因果链 {causal_count} 条")
        issue_desc = "、".join(parts) if parts else "全部"
        label = (
            f"修复选中问题（{issue_desc}） · {request.project_id} / 第 {request.chapter_number} 章"
        )
        command = self._make_app_job_command(
            kind=JobKind.REPAIR_ISSUES,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-repair-issues",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_reevaluate_chapter(
        self,
        request: ReevaluateChapterRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"重新评估 · {request.project_id} / 第 {request.chapter_number} 章"
        command = self._make_app_job_command(
            kind=JobKind.REEVALUATE_CHAPTER,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-reevaluate-chapter",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_polish_chapter(
        self,
        request: PolishChapterRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"精修润色 · {request.project_id} / 第 {request.chapter_number} 章"
        command = self._make_app_job_command(
            kind=JobKind.POLISH_CHAPTER,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-polish-chapter",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_book_consistency(
        self,
        request: BookConsistencyRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"全书一致性审计 · {request.project_id}"
        command = self._make_app_job_command(
            kind=JobKind.BOOK_CONSISTENCY,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-book-consistency",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_export_book(
        self,
        request: ExportBookRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        fmt_label = {"markdown": "Markdown", "epub": "EPUB", "txt": "纯文本"}.get(
            request.format, request.format
        )
        label = f"导出 {fmt_label} · {request.project_id}"
        command = self._make_app_job_command(
            kind=JobKind.EXPORT_BOOK,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-export-book",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_reextract_relationships(
        self,
        request: ReextractRelationshipsRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        scope = f"第 {request.chapter_number} 章" if request.chapter_number > 0 else "全部章节"
        label = f"重新提取关系 ({scope}) · {request.project_id}"
        command = self._make_app_job_command(
            kind=JobKind.REEXTRACT_RELATIONSHIPS,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-reextract-relationships",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_repair_motif_history(
        self,
        request: RepairMotifHistoryRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"修补母题历史 · {request.project_id} / 第 {request.chapter_number} 章"
        command = self._make_app_job_command(
            kind=JobKind.REPAIR_MOTIF_HISTORY,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-repair-motif-history",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_rebuild_memory_vectors(
        self,
        request: RebuildMemoryVectorsRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        label = f"重建向量索引 · {request.project_id}"
        command = self._make_app_job_command(
            kind=JobKind.REBUILD_MEMORY_VECTORS,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-rebuild-memory-vectors",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_sync_chapter_contracts(
        self,
        request: SyncChapterContractsRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        scope = (
            f"第 {','.join(str(n) for n in request.affected_chapter_numbers)} 章"
            if request.affected_chapter_numbers
            else "自动检测"
        )
        cascade = "级联" if request.cascade_downstream else "不级联"
        label = f"同步章节契约 ({scope} · {cascade}) · {request.project_id}"
        command = self._make_app_job_command(
            kind=JobKind.SYNC_CHAPTER_CONTRACTS,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-sync-chapter-contracts",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def submit_extend_outline(
        self,
        request: ExtendOutlineRequest,
        *,
        mock: bool = False,
    ) -> DesktopJobRecord:
        target = request.target_total or 0
        if target <= 0:
            target = request.additional_chapters or 0
            label = f"延长全书追加 {target} 章 · {request.project_id}"
        else:
            label = f"延长全书至 {target} 章 · {request.project_id}"
        command = self._make_app_job_command(
            kind=JobKind.EXTEND_OUTLINE,
            request=request,
            label=label,
            project_id=request.project_id,
            mock=mock,
            command_name="desktop-extend-outline",
        )
        return self._submit_app_service_job(command, emit_submitted=True)

    def _handle_decision_required(self, job_id: str, payload: object) -> None:
        if self._closed:
            return
        self.decision_required.emit(job_id, payload)

    def provide_decision(
        self,
        job_id: str,
        decision_id: str,
        choice: str,
        *,
        custom_text: str = "",
        approval_version: str = "",
    ) -> bool:
        with self._lock:
            is_app_job = job_id in getattr(self, "_app_job_ids", set())
        if is_app_job:
            try:
                self._job_service.provide_decision(
                    job_id,
                    {
                        "decision_id": decision_id,
                        "choice": choice,
                        "custom_text": custom_text,
                        "approval_version": approval_version,
                    },
                )
            except (KeyError, ValueError):
                return False
            return True
        with self._lock:
            worker = self._workers.get(job_id)
        if worker is None:
            return False
        return worker.provide_decision(
            decision_id,
            choice,
            custom_text=custom_text,
            approval_version=approval_version,
        )

    def _publish_event(self, event: ProjectEvent) -> None:
        """Publish a project event from sync Qt slots without dropping callbacks."""
        if self._closed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            run_async_coro(self._event_bus.publish(event))
        else:
            loop.create_task(self._event_bus.publish(event))

    def _publish_section_change(self, project_id: str, section: str) -> None:
        if self._closed:
            return
        self.section_changed.emit(project_id, section)
        self._publish_event(
            ProjectEvent(
                project_id=project_id,
                event_type=SECTION_CHANGED,
                data={"section": section},
            )
        )

    def _handle_started(self, job_id: str) -> None:
        if self._closed:
            return
        self._mutate(
            job_id,
            status=DesktopJobState.RUNNING,
            current_step="",
            current_step_payload={},
        )
        with self._lock:
            record = self._jobs.get(job_id)
        if record is not None:
            if record.kind == "init_long":
                self._publish_event(
                    ProjectEvent(project_id=record.project_id, event_type=INIT_STARTED)
                )

    def _handle_step(self, job_id: str, step: str, payload: object) -> None:
        if self._closed:
            return
        outline_updated_project_id = ""
        token_project_id = ""
        audit_update_data: tuple[str, int, dict] | None = None
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            # Skip step events for cancelled jobs — the background thread cannot
            # be interrupted and may still emit steps after cancel_job() marks
            # the record FAILED.  Updating current_step here would overwrite the
            # "cancelled" marker and emit spurious jobs_changed signals.
            if record.status == DesktopJobState.FAILED:
                return
            # ── Fast path for high-frequency stream events ──────────────────
            # llm_stream_delta events fire once per LLM token chunk (~40/s per
            # active stream).  They are invisible to the UI card render (listed
            # in _CARD_NON_RENDER_EVENTS) and only serve the token counter.
            # Skipping events.append() and updated_at avoids O(n) list churn
            # and keeps the Qt main thread responsive under concurrent loads.
            if step == "llm_stream_delta":
                if isinstance(payload, dict) and "tokens_so_far" in payload:
                    record.cumulative_tokens, record.cumulative_cost_usd = apply_live_usage_payload(
                        record.cumulative_tokens,
                        record.cumulative_cost_usd,
                        payload,
                        is_model_call=False,
                    )
                    token_project_id = record.project_id
                self._schedule_jobs_changed(high_frequency=True)
                if token_project_id:
                    self._token_dirty = True
                    if self._ui_active and not self._token_timer.isActive():
                        self._token_timer.start()
                return
            # Other high-frequency diagnostic events that carry no UI-visible
            # state and need not be stored in the events list.
            if step in {"budget_status", "preflight_token_estimate", "prompt_pressure"}:
                self._schedule_jobs_changed(high_frequency=True)
                return
            if (
                step == "repair_attempt_guidance"
                and isinstance(payload, dict)
                and "dimension" in payload
            ):
                record.current_step = payload["dimension"]
                record.current_step_payload = dict(payload)
            elif not is_non_progress_step_event(step):
                record.current_step = step
                record.current_step_payload = dict(payload) if isinstance(payload, dict) else {}
            record.updated_at = _now_iso()
            record.events.append(
                DesktopJobEvent(
                    at=record.updated_at,
                    step=step,
                    payload=payload if isinstance(payload, dict) else {},
                )
            )
            if len(record.events) > _MAX_JOB_EVENTS:
                record.events = compact_progress_event_history(record.events, limit=_MAX_JOB_EVENTS)
            if isinstance(payload, dict) and step in {"run_log_started", "model_call_update"}:
                run_log_dir = str(payload.get("run_log_dir") or "").strip()
                if run_log_dir:
                    result_payload = record.result if isinstance(record.result, dict) else {}
                    result_payload = dict(result_payload)
                    result_payload["run_log_dir"] = run_log_dir
                    record.result = result_payload
            call_id = str(payload.get("call_id") or "").strip() if isinstance(payload, dict) else ""
            duplicate_call = bool(
                call_id
                and any(
                    event.step == "model_call_update"
                    and event is not record.events[-1]
                    and str(event.payload.get("call_id") or "").strip() == call_id
                    and str(event.payload.get("status") or "").strip().lower() == "success"
                    for event in record.events
                )
            )
            new_model_success = bool(
                isinstance(payload, dict)
                and step == "model_call_update"
                and str(payload.get("status") or "").strip().lower() == "success"
                and not duplicate_call
            )
            if isinstance(payload, dict) and (new_model_success or "tokens_so_far" in payload):
                record.cumulative_tokens, record.cumulative_cost_usd = apply_live_usage_payload(
                    record.cumulative_tokens,
                    record.cumulative_cost_usd,
                    payload,
                    is_model_call=new_model_success,
                )
                token_project_id = record.project_id
            if (
                record.kind == "init_long"
                and record.project_id
                and step.startswith("plan_outline_batch_")
            ):
                outline_updated_project_id = record.project_id
            if step == "audit_result_update" and isinstance(payload, dict):
                _ch = payload.get("chapter_number")
                _ar = payload.get("audit_result")
                if record.project_id and _ch is not None and _ar is not None:
                    audit_update_data = (record.project_id, int(_ch), _ar)
        if outline_updated_project_id:
            self._publish_event(
                ProjectEvent(
                    project_id=outline_updated_project_id,
                    event_type=OUTLINE_UPDATED,
                    data={"source": "init_long", "step": step},
                )
            )
        if audit_update_data is not None:
            _proj, _chap, _result = audit_update_data
            try:
                from novel_forge.desktop.state.store import get_ui_store

                get_ui_store().set_audit_result(_proj, _chap, _result)
            except Exception as exc:
                logger.debug("Failed to relay audit_result_update to UIStore: %s", exc)
        self._schedule_jobs_changed(high_frequency=step in _HIGH_FREQUENCY_STEP_EVENTS)
        if token_project_id:
            self._token_dirty = True
            if self._ui_active and not self._token_timer.isActive():
                self._token_timer.start()

    def _schedule_jobs_changed(self, *, high_frequency: bool = False) -> None:
        self._jobs_dirty = True
        if not self._ui_active:
            return
        interval = _HIGH_FREQUENCY_JOB_BIND_COALESCE_MS if high_frequency else _JOB_BIND_COALESCE_MS
        if self._jobs_bind_timer.isActive():
            # A real milestone should not wait behind a stream/model-call batch.
            if not high_frequency and self._jobs_bind_timer.interval() != _JOB_BIND_COALESCE_MS:
                self._jobs_bind_timer.stop()
                self._jobs_bind_timer.setInterval(_JOB_BIND_COALESCE_MS)
                self._jobs_bind_timer.start()
            return
        self._jobs_bind_timer.setInterval(interval)
        self._jobs_bind_timer.start()

    def _flush_dirty_jobs(self) -> None:
        if not self._ui_active:
            return
        if self._jobs_dirty:
            self._jobs_dirty = False
            if self._jobs_bind_timer.interval() != _JOB_BIND_COALESCE_MS:
                self._jobs_bind_timer.setInterval(_JOB_BIND_COALESCE_MS)
            self.jobs_changed.emit()

    def _flush_dirty_tokens(self) -> None:
        if not self._ui_active or not self._token_dirty:
            return
        self._token_dirty = False
        _active = {DesktopJobState.RUNNING}
        with self._lock:
            running = [r for r in self._jobs.values() if r.status in _active]
        if not running:
            return
        earliest = min(running, key=lambda r: str(r.created_at or ""))
        self.token_update.emit(
            earliest.project_id,
            earliest.cumulative_tokens,
            earliest.cumulative_cost_usd,
        )

    def cancel_job(self, job_id: str, *, reason: str = "用户已取消") -> None:
        """Cancel a running job in the UI and interrupt the background task.

        App-service jobs are cancelled through ``JobService``. Legacy worker
        entries, if any were registered by older tests/callers, still receive an
        asyncio cancellation request. Late terminal events are ignored because
        the desktop record is already marked FAILED.

        Args:
            job_id: The job to cancel.
            reason: Human-readable cancellation reason shown in the task flow UI.
                    Callers should pass a descriptive string so the UI distinguishes
                    between user-initiated stops, watchdog timeouts, system decisions, etc.
        """
        worker: _WorkspaceJobWorker | None = None
        is_app_job = False
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            if record.status not in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
                return
            label = record.label  # capture before status change
            is_app_job = job_id in getattr(self, "_app_job_ids", set())
            record.status = DesktopJobState.FAILED
            record.current_step = "cancelled"
            record.error = reason
            record.updated_at = _now_iso()
            worker = self._workers.pop(job_id, None)
            self._waiting_jobs.pop(job_id, None)
            if worker is not None:
                # Register as "cancelling" — thread is still alive but winding down.
                self._cancelling[job_id] = label

        # Wire the cleanup notification so we know when the thread truly exits.
        if worker is not None:

            def _on_thread_done(jid: str = job_id) -> None:
                with self._lock:
                    self._cancelling.pop(jid, None)

            worker._cleanup_notify = _on_thread_done

        # Actually cancel the asyncio task so HTTP calls are interrupted.
        if worker is not None:
            try:
                worker.request_cancel()
            except Exception:
                pass
        if is_app_job:
            try:
                self._job_service.cancel(job_id, reason=reason)
            except KeyError:
                pass

        self.jobs_changed.emit()
        self._update_sleep_inhibitor()
        self._persist_terminal_job(job_id)
        # Emit job_completed so the chapter studio refreshes from disk.
        # Previously the worker would finish naturally and _handle_finished
        # would emit this; now that we interrupt the task, _emit is skipped
        # (because _cancel_requested is set), so we must emit here instead.
        self.job_completed.emit(job_id)

    def mark_task_flow_errors_resolved(self, resolved_by_job: object) -> int:
        """Persist task-flow error entries that the user marked as handled.

        The resolved marker is metadata about the UI review state, so it must
        not refresh ``updated_at``. Failed-job error entry ids are derived from
        stable failure fields; touching ``updated_at`` here would make older ids
        drift and appear unresolved again after a reload.
        """
        if not isinstance(resolved_by_job, dict):
            return 0

        normalized: dict[str, set[str]] = {}
        for raw_job_id, raw_entry_ids in resolved_by_job.items():
            job_id = str(raw_job_id or "").strip()
            if not job_id or not isinstance(raw_entry_ids, (list, tuple, set)):
                continue
            entry_ids = {str(item).strip() for item in raw_entry_ids if str(item or "").strip()}
            if entry_ids:
                normalized[job_id] = entry_ids
        if not normalized:
            return 0

        changed_job_ids: list[str] = []
        changed_count = 0
        with self._lock:
            for job_id, entry_ids in normalized.items():
                record = self._jobs.get(job_id)
                if record is None:
                    continue
                existing = getattr(record, "resolved_error_entry_ids", set())
                if not isinstance(existing, set):
                    existing = set(existing) if isinstance(existing, (list, tuple)) else set()
                    record.resolved_error_entry_ids = existing
                before = len(existing)
                existing.update(entry_ids)
                added = len(existing) - before
                if added > 0:
                    changed_count += added
                    changed_job_ids.append(job_id)

        if changed_count <= 0:
            return 0

        for job_id in changed_job_ids:
            self._persist_terminal_job(job_id)
        self.jobs_changed.emit()
        return changed_count

    def clear_jobs(self, job_ids: list[str], *, project_id: str = "") -> int:
        """Remove non-active jobs by id and sync persisted task-flow history.

        Returns the number of jobs removed from in-memory state.
        """
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for raw in job_ids:
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized_ids.append(value)
        if not normalized_ids:
            return 0

        active_states = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        removed_by_project: dict[str, set[str]] = {}
        archive_snapshots: list[DesktopJobRecord] = []

        with self._lock:
            for job_id in normalized_ids:
                record = self._jobs.get(job_id)
                if record is None:
                    continue
                if project_id and record.project_id != project_id:
                    continue
                if record.status in active_states:
                    continue
                snapshot = _job_from_payload(_job_to_payload(record))
                if snapshot is not None:
                    archive_snapshots.append(snapshot)
                del self._jobs[job_id]
                self._workers.pop(job_id, None)
                if record.project_id:
                    removed_by_project.setdefault(record.project_id, set()).add(job_id)

        removed_count = sum(len(ids) for ids in removed_by_project.values())
        if removed_count <= 0:
            return 0

        for snapshot in archive_snapshots:
            self._archive_job_errors(snapshot)

        for pid, purged_ids in removed_by_project.items():
            path = self._history_path(pid)
            if path is None:
                continue
            try:
                history = self._read_history_records(path)
                remaining = [item for item in history if item.job_id not in purged_ids]
                if len(remaining) == len(history):
                    continue
                if remaining:
                    self._write_history_records(path, remaining)
                elif path.exists():
                    path.unlink()
            except OSError:
                pass

        self.jobs_changed.emit()
        return removed_count

    def clear_restarted_init_jobs(self, project_id: str) -> list[str]:
        """Discard all in-memory init jobs for a project after a fresh re-init reset.

        The desktop "重新立项" path already removes project state, logs, and task-flow
        history from disk. This method mirrors that destructive reset in the live
        task manager: active init workers are cancelled, queued init jobs are dropped,
        and old terminal init cards are removed without re-archiving their errors.
        """
        project_value = str(project_id or "").strip()
        if not project_value:
            return []

        active_states = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        cancelled_job_ids: list[str] = []
        purged_ids: set[str] = set()
        workers_to_cancel: list[tuple[str, _WorkspaceJobWorker]] = []
        app_job_ids_to_cancel: list[str] = []

        with self._lock:
            for job_id in list(self._jobs.keys()):
                record = self._jobs.get(job_id)
                if (
                    record is None
                    or record.project_id != project_value
                    or record.kind != "init_long"
                ):
                    continue

                worker = self._workers.pop(job_id, None)
                self._waiting_jobs.pop(job_id, None)
                if record.status in active_states:
                    record.status = DesktopJobState.FAILED
                    record.current_step = "cancelled"
                    record.error = "重新立项已开始，旧立项任务已取消。"
                    record.updated_at = _now_iso()
                    cancelled_job_ids.append(job_id)
                    if worker is not None:
                        self._cancelling[job_id] = record.label
                        workers_to_cancel.append((job_id, worker))
                    elif job_id in getattr(self, "_app_job_ids", set()):
                        app_job_ids_to_cancel.append(job_id)

                del self._jobs[job_id]
                purged_ids.add(job_id)

        if not purged_ids:
            return []

        for job_id, worker in workers_to_cancel:

            def _on_thread_done(jid: str = job_id) -> None:
                with self._lock:
                    self._cancelling.pop(jid, None)

            worker._cleanup_notify = _on_thread_done
            try:
                worker.request_cancel()
            except Exception:
                pass
        job_service = getattr(self, "_job_service", None)
        if job_service is not None:
            for job_id in app_job_ids_to_cancel:
                try:
                    job_service.cancel(job_id, reason="重新立项已开始，旧立项任务已取消。")
                except KeyError:
                    pass

        path = self._history_path(project_value)
        if path is not None:
            try:
                history = self._read_history_records(path)
                remaining = [item for item in history if item.job_id not in purged_ids]
                if len(remaining) < len(history):
                    if remaining:
                        self._write_history_records(path, remaining)
                    elif path.exists():
                        path.unlink()
            except OSError:
                pass

        self.jobs_changed.emit()
        self._update_sleep_inhibitor()
        for job_id in cancelled_job_ids:
            self.job_completed.emit(job_id)
        return sorted(purged_ids)

    def clear_jobs_for_chapter_range(self, project_id: str, from_chapter: int) -> int:
        """Remove all non-active terminal jobs for *project_id* where chapter >= *from_chapter*.

        Only SUCCEEDED / FAILED / PAUSED jobs are eligible; RUNNING / QUEUED jobs are
        never touched so in-flight work is never interrupted.

        Returns the number of jobs removed from in-memory state.
        """
        if not project_id or from_chapter < 1:
            return 0

        active_states = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        purged_ids: set[str] = set()
        archive_snapshots: list[DesktopJobRecord] = []

        with self._lock:
            for job_id in list(self._jobs.keys()):
                record = self._jobs[job_id]
                if record.project_id != project_id:
                    continue
                if record.status in active_states:
                    continue
                ch = _job_chapter_num(record)
                if ch is None or ch < from_chapter:
                    continue
                snapshot = _job_from_payload(_job_to_payload(record))
                if snapshot is not None:
                    archive_snapshots.append(snapshot)
                del self._jobs[job_id]
                self._workers.pop(job_id, None)
                purged_ids.add(job_id)

        if not purged_ids:
            return 0

        for snapshot in archive_snapshots:
            self._archive_job_errors(snapshot)

        path = self._history_path(project_id)
        if path is not None:
            try:
                history = self._read_history_records(path)
                remaining = [item for item in history if item.job_id not in purged_ids]
                if len(remaining) < len(history):
                    if remaining:
                        self._write_history_records(path, remaining)
                    elif path.exists():
                        path.unlink()
            except OSError:
                pass

        self.jobs_changed.emit()
        return len(purged_ids)

    def active_job_labels(self) -> list[str]:
        """Return human-readable labels of all RUNNING or QUEUED jobs."""
        _active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        with self._lock:
            return [rec.label for rec in self._jobs.values() if rec.status in _active]

    def cancelling_job_labels(self) -> list[str]:
        """Return labels of jobs that were user-cancelled but whose background
        thread is still winding down.  These are invisible to active_job_labels()
        (status=FAILED) yet still occupy a thread-pool slot.  closeEvent() must
        check both lists before deciding whether a wait dialog is needed.
        """
        with self._lock:
            return list(self._cancelling.values())

    def _chapter_outline_ready(self, project_id: str, chapter_number: int) -> bool:
        """Return True if outline.json already contains the given chapter number.

        Used to decide whether prepare_chapter may run in parallel with a
        running init_long job: we allow parallel execution only when the target
        chapter's outline entry has been committed to disk.
        """
        if self._storage_root is None:
            return True  # can't verify; let execution layer handle missing data
        outline_path = self._storage_root / project_id / "outline.json"
        try:
            data = json.loads(outline_path.read_text(encoding="utf-8"))
            chapters = data.get("chapters", [])
            return any(c.get("chapter_number") == chapter_number for c in chapters)
        except Exception:
            return False

    def _poll_waiting_jobs(self) -> None:
        with self._lock:
            project_ids = list(
                {w.record.project_id for w in self._waiting_jobs.values() if w.record.project_id}
            )
        for pid in project_ids:
            self._check_waiting_jobs(pid)

    def _check_waiting_jobs(self, project_id: str | None = None) -> None:
        if self._storage_root is None:
            return
        if project_id is not None:
            self._check_waiting_jobs_for_project(project_id)
            return
        with self._lock:
            project_ids = list(
                {w.record.project_id for w in self._waiting_jobs.values() if w.record.project_id}
            )
        for pid in project_ids:
            self._check_waiting_jobs_for_project(pid)

    def _check_waiting_jobs_for_project(self, project_id: str) -> None:
        layout = ProjectLayout(self._storage_root / project_id)
        with self._lock:
            waiting_copy = list(self._waiting_jobs.values())
        for waiting in waiting_copy:
            record = waiting.record
            if record.project_id != project_id:
                continue
            chapter_num = self._extract_chapter_number(record)
            if (
                record.kind == "prepare_chapter"
                and chapter_num is not None
                and not self._chapter_outline_ready(record.project_id, chapter_num)
            ):
                continue
            op = self._kind_to_operation(record.kind)
            if op is not None:
                if chapter_num is None:
                    continue
                if not self._dependency_resolver.is_ready(layout, op, chapter_num=chapter_num):
                    continue
            with self._lock:
                waiting_job_ids = set(self._waiting_jobs)
            if self._has_active_write_conflict(record, ignore_job_ids=waiting_job_ids):
                continue
            self._start_waiting_job(waiting)

    def _active_init_for_project(self, project_id: str) -> DesktopJobRecord | None:
        if not project_id:
            return None
        active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        with self._lock:
            for existing in self._jobs.values():
                if (
                    existing.project_id == project_id
                    and existing.kind == "init_long"
                    and existing.status in active
                ):
                    return existing
        return None

    def _prepare_can_overlap_init(
        self,
        record: DesktopJobRecord,
        existing: DesktopJobRecord,
    ) -> bool:
        if record.kind != "prepare_chapter" or existing.kind != "init_long":
            return False
        if existing.status != DesktopJobState.RUNNING:
            return False
        if not (
            str(existing.current_step or "").startswith("plan_outline_batch_")
            or any(event.step.startswith("plan_outline_batch_") for event in existing.events)
        ):
            return False
        chapter_num = self._extract_chapter_number(record)
        if chapter_num is None:
            return False
        return self._chapter_outline_ready(record.project_id, chapter_num)

    def _find_active_write_conflict(
        self,
        record: DesktopJobRecord,
        *,
        ignore_job_ids: set[str] | None = None,
    ) -> DesktopJobRecord | None:
        if record.kind not in CHAPTER_WRITE_KINDS or not record.project_id:
            return None
        active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        ignored = ignore_job_ids or set()
        with self._lock:
            active_records = [
                existing
                for existing in self._jobs.values()
                if existing.job_id != record.job_id
                and existing.job_id not in ignored
                and existing.project_id == record.project_id
                and existing.kind in CHAPTER_WRITE_KINDS
                and existing.status in active
            ]
        for existing in active_records:
            if self._prepare_can_overlap_init(record, existing):
                continue
            return existing
        return None

    def _has_active_write_conflict(
        self,
        record: DesktopJobRecord,
        *,
        ignore_job_ids: set[str] | None = None,
    ) -> bool:
        return (
            self._find_active_write_conflict(
                record,
                ignore_job_ids=ignore_job_ids,
            )
            is not None
        )

    def _start_waiting_job(self, waiting: WaitingJob) -> None:
        record = waiting.record
        with self._lock:
            existing = self._jobs.get(record.job_id)
            if existing is None or existing.status != DesktopJobState.QUEUED:
                self._waiting_jobs.pop(record.job_id, None)
                return
            if record.job_id not in self._waiting_jobs:
                return
            self._waiting_jobs.pop(record.job_id, None)
            existing.updated_at = _now_iso()
        command = waiting.app_command.model_copy(update={"job_id": record.job_id})
        self._submit_app_service_job(command, emit_submitted=False)

    async def _resubmit_waiting_job(self, record: DesktopJobRecord) -> None:
        """Compatibility shim for older tests/callers to re-submit queued commands."""
        waiting: WaitingJob | None
        with self._lock:
            waiting = self._waiting_jobs.get(record.job_id)
        if waiting is not None:
            self._start_waiting_job(waiting)
            return
        if record.project_id:
            self._publish_event(
                ProjectEvent(
                    project_id=record.project_id,
                    event_type="job_dependency_ready",
                    data={"job_id": record.job_id, "kind": record.kind},
                )
            )
            self.jobs_changed.emit()

    def _kind_to_operation(self, kind: str) -> Operation | None:
        mapping = {
            "init_long": Operation.INIT_LONG,
            "prepare_chapter": Operation.PREPARE_CHAPTER,
            "run_chapter": Operation.RUN_CHAPTER,
            "repair_continuity": Operation.REPAIR,
            "repair_causal": Operation.REPAIR,
            "repair_issues": Operation.REPAIR,
            "polish_chapter": Operation.REPAIR,
        }
        return mapping.get(kind)

    def _extract_chapter_number(self, record: DesktopJobRecord) -> int | None:
        result = record.result or {}
        chapter_num = result.get("chapter_number")
        if chapter_num is not None:
            return int(chapter_num)
        m = _LABEL_CHAPTER_RE.search(record.label or "")
        if m is None:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    def _queue_waiting_job(
        self,
        *,
        kind: str,
        label: str,
        project_id: str,
        chapter_number: int,
        app_command: JobCommand,
    ) -> DesktopJobRecord | None:
        if kind not in CHAPTER_WRITE_KINDS:
            return None
        active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        with self._lock:
            for existing in self._jobs.values():
                if (
                    existing.project_id == project_id
                    and existing.kind == kind
                    and self._extract_chapter_number(existing) == chapter_number
                    and existing.status in active
                ):
                    return existing
            record = DesktopJobRecord(
                job_id=uuid4().hex,
                kind=kind,
                label=label,
                project_id=project_id,
                status=DesktopJobState.QUEUED,
            )
            record.result = {"chapter_number": chapter_number}
            self._waiting_jobs[record.job_id] = WaitingJob(
                record=record,
                app_command=app_command.model_copy(update={"job_id": record.job_id}),
            )
            self._jobs[record.job_id] = record
        self.jobs_changed.emit()
        self.job_submitted.emit()
        self._update_sleep_inhibitor()
        return record

    def _update_sleep_inhibitor(self) -> None:
        """Acquire or release the OS sleep inhibitor based on active job count."""
        _active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        with self._lock:
            has_active = any(rec.status in _active for rec in self._jobs.values())
        if has_active:
            self._sleep_inhibitor.acquire()
        else:
            self._sleep_inhibitor.release()

    def request_cancel_all(self) -> None:
        """Cancel all in-flight asyncio tasks without clearing the worker registry.

        Unlike ``shutdown()``, this only sends cancellation requests so that
        ``active_job_labels()`` can still be polled to track completion progress.
        Call ``shutdown()`` once all tasks have settled.
        """
        with self._lock:
            workers = list(self._workers.values())
            app_job_ids = [
                job_id
                for job_id in getattr(self, "_app_job_ids", set())
                if self._jobs.get(job_id) is not None
                and self._jobs[job_id].status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
            ]
        for worker in workers:
            try:
                worker.request_cancel()
            except Exception:
                pass
        job_service = getattr(self, "_job_service", None)
        if job_service is not None:
            for job_id in app_job_ids:
                try:
                    job_service.cancel(job_id, reason="应用正在关闭")
                except KeyError:
                    pass
        self._thread_pool.clear()

    def shutdown(self, *, wait_ms: int = 500) -> None:
        """Best-effort shutdown for app exit.

        1. Request cancellation of all in-flight asyncio tasks so HTTP calls
           are aborted immediately rather than waiting for a timeout.
        2. Disconnect worker signals so late completions cannot touch Qt objects
           that may already be destroyed.
        3. Clear queued (not yet started) runnables from the thread pool.
        4. Wait briefly for running threads to settle.
        """
        self._closed = True
        self._unsubscribe_from_events()

        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
            self._cancelling.clear()

        app_event_timer = getattr(self, "_app_event_timer", None)
        if app_event_timer is not None:
            app_event_timer.stop()
            safe_disconnect(app_event_timer.timeout, self._drain_app_service_events)
        app_subscription = getattr(self, "_app_event_subscription", None)
        if app_subscription is not None:
            app_subscription.close()
        job_service = getattr(self, "_job_service", None)
        if job_service is not None:
            job_service.shutdown(wait_s=max(wait_ms / 1000.0, 0.1), reason="应用正在关闭")

        # Cancel first: interrupts HTTP waits before we touch any Qt state.
        for worker in workers:
            try:
                worker.request_cancel()
            except Exception:
                pass

        # Disconnect signals so any late emit cannot reach deleted QObjects.
        for worker in workers:
            for signal, handler in (
                (worker.signals.started, self._handle_started),
                (worker.signals.step, self._handle_step),
                (worker.signals.decision_required, self._handle_decision_required),
                (worker.signals.finished, self._handle_finished),
                (worker.signals.failed, self._handle_failed),
                (worker.signals.cleanup_runtime, self._handle_cleanup_runtime),
            ):
                try:
                    signal.disconnect(handler)
                except (RuntimeError, TypeError):
                    pass
        self._thread_pool.clear()
        self._thread_pool.waitForDone(wait_ms)
        # Backfill (Phase M1): timer deleteLater — previously only stopped,
        # which leaves QTimer QObjects hanging on the Qt parent chain.
        # Positioned BEFORE _sleep_inhibitor.release() so the inhibitor
        # release sees clean state (the timers are no longer firing).
        self._waiting_poll_timer.stop()
        self._jobs_bind_timer.stop()
        self._token_timer.stop()
        self._wake_replay_timer.stop()
        self._wake_replay_events.clear()
        self._wake_replay_active = False
        for timer_attr in (
            "_app_event_timer",
            "_waiting_poll_timer",
            "_jobs_bind_timer",
            "_token_timer",
            "_wake_replay_timer",
        ):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                try:
                    timer.stop()
                    timer.deleteLater()
                except (RuntimeError, TypeError):
                    pass
        self._sleep_inhibitor.release()
        self._history_event.set()
        if app_event_timer is not None:
            app_event_timer.stop()
        if self._history_worker is not None:
            try:
                self._history_worker.signals.finished.disconnect(self._handle_history_loaded)
            except (RuntimeError, TypeError):
                pass
            self._history_worker = None

    def close(self, *, wait_ms: int = 500) -> None:
        """Alias for shutdown() — naming consistency with other managers.

        UI convention: manager.close() = clean teardown. Some code paths
        may prefer close(); existing call sites continue to use shutdown().
        """
        self.shutdown(wait_ms=wait_ms)

    def _handle_finished(self, job_id: str, result: object) -> None:
        if self._closed:
            return
        # Guard: if the job was already cancelled, discard this delayed result.
        # IMPORTANT: read the cancelled flag inside the lock, then emit OUTSIDE
        # the lock to avoid deadlock (job_completed → _handle_job_completed →
        # jobs() all acquire self._lock; threading.Lock is not reentrant).
        was_cancelled = False
        with self._lock:
            existing = self._jobs.get(job_id)
            if existing is not None and existing.status == DesktopJobState.FAILED:
                was_cancelled = True
        if was_cancelled:
            # The background asyncio task completed after the UI cancel.
            # Emit job_completed so the chapter studio refreshes from disk —
            # the background may have written a new checkpoint (e.g., advanced
            # plan_checkpoint → guard_checkpoint) that the UI needs to reflect,
            # preventing "checkpoint 已变化" errors on the next user action.
            self.job_completed.emit(job_id)
            return
        payload = result if isinstance(result, dict) else {}
        project_id = str(payload.get("project_id", "")).strip()
        status = str(payload.get("status", "")).strip()
        raw_checkpoint = payload.get("checkpoint")
        checkpoint = raw_checkpoint if isinstance(raw_checkpoint, dict) else {}
        if status == "needs_decision":
            self._mutate(
                job_id,
                status=DesktopJobState.PAUSED,
                current_step=str(
                    checkpoint.get("checkpoint_type", "guard_checkpoint") or "guard_checkpoint"
                ),
                error="",
                project_id=project_id,
                result=payload,
            )
            self._persist_terminal_job(job_id)
            with self._lock:
                self._workers.pop(job_id, None)
                self._waiting_jobs.pop(job_id, None)
            self.job_completed.emit(job_id)
            with self._lock:
                _nd_rec = self._jobs.get(job_id)
                _nd_kind = _nd_rec.kind if _nd_rec is not None else ""
            _nd_section = _job_kind_to_section(_nd_kind)
            if _nd_section is not None:
                self._publish_section_change(project_id, _nd_section)
            return
        self._mutate(
            job_id,
            status=DesktopJobState.SUCCEEDED,
            current_step="completed",
            error="",
            project_id=project_id,
            result=payload,
        )
        # Mark older PAUSED jobs for the same chapter as completed so progress
        # bars do not remain stuck at intermediate percentages (e.g. 88%).
        self._retire_stale_chapter_jobs(job_id, project_id, payload)
        # When init_long completes, all prior chapter-level jobs for this project
        # are stale (the project was just re-initialised).  Remove them so the
        # chapter studio does not show old failure banners after reinit.
        # Read kind + project_id atomically from the record (after _mutate has
        # written the effective project_id into it) to avoid any cross-thread race.
        with self._lock:
            _rec = self._jobs.get(job_id)
            _kind = _rec.kind if _rec is not None else ""
            _pid = _rec.project_id if _rec is not None else ""
        if _kind == "init_long" and _pid:
            self._purge_terminal_chapter_jobs(_pid, exclude_job_id=job_id)
            self._publish_event(ProjectEvent(project_id=_pid, event_type=INIT_COMPLETED))
            self._publish_event(ProjectEvent(project_id=_pid, event_type=OUTLINE_UPDATED))
        if _kind == "sync_chapter_contracts" and _pid:
            self._publish_event(
                ProjectEvent(
                    project_id=_pid,
                    event_type=CHAPTER_CONTRACTS_SYNCED,
                    data=payload,
                )
            )
        chapter_number = payload.get("chapter_number")
        if chapter_number is not None and _pid:
            self._publish_event(
                ProjectEvent(
                    project_id=_pid,
                    event_type=CHAPTER_COMPLETED,
                    data={"chapter_number": chapter_number},
                )
            )
        followup: DesktopJobRecord | None = None
        followup_error = ""
        if (
            _kind in {"run_chapter", "resolve_chapter_checkpoint_finalize"}
            and _pid
            and chapter_number is not None
        ):
            # Defer TTS followup if another chapter generation is still active
            # for the same project — avoids rate-limiter contention between
            # the active LLM stream and the TTS script generation call.
            _has_active_chapter_job = False
            with self._lock:
                _active_states = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
                _chapter_kinds = {"run_chapter", "resolve_chapter_checkpoint", "prepare_chapter"}
                for _j in self._jobs.values():
                    if (
                        _j.project_id == _pid
                        and _j.kind in _chapter_kinds
                        and _j.status in _active_states
                        and _j.job_id != job_id
                    ):
                        _has_active_chapter_job = True
                        break
            if _has_active_chapter_job:
                logger.info(
                    "Deferring post-archive TTS for %s chapter %s: "
                    "another chapter job is still active",
                    _pid,
                    chapter_number,
                )
            else:
                try:
                    followup = self.submit_post_archive_tts(
                        project_id=_pid,
                        chapter_number=int(chapter_number),
                        parent_job_id=job_id,
                    )
                except Exception as exc:
                    # A queue handoff can fail independently (shutdown race,
                    # unwritable TTS state, etc.).  The archived chapter remains a
                    # success and the failure is visible on its parent result.
                    followup_error = str(exc)
                    logger.exception(
                        "Failed to queue post-archive TTS for %s chapter %s",
                        _pid,
                        chapter_number,
                    )
        if followup is not None:
            with self._lock:
                parent = self._jobs.get(job_id)
                if parent is not None:
                    parent.result = {
                        **parent.result,
                        "tts_followup_job_id": followup.job_id,
                        "tts_followup_status": followup.status.value,
                    }
        elif followup_error:
            with self._lock:
                parent = self._jobs.get(job_id)
                if parent is not None:
                    parent.result = {
                        **parent.result,
                        "tts_followup_status": "queue_failed",
                        "tts_followup_error": followup_error,
                    }
        self._persist_terminal_job(job_id)
        with self._lock:
            self._workers.pop(job_id, None)
            self._waiting_jobs.pop(job_id, None)
        _section = _job_kind_to_section(_kind)
        if _section is not None:
            self._publish_section_change(_pid, _section)
        self.job_completed.emit(job_id)

    def _retire_stale_chapter_jobs(
        self, completed_job_id: str, project_id: str, payload: dict[str, Any]
    ) -> None:
        """When a chapter job succeeds, mark earlier PAUSED jobs for the same chapter as completed."""
        if not project_id:
            return
        chapter_number = payload.get("chapter_number")
        if not chapter_number:
            return
        self._retire_paused_jobs_for_chapter(
            project_id,
            int(chapter_number),
            exclude_job_id=completed_job_id,
        )

    def _purge_terminal_chapter_jobs(self, project_id: str, *, exclude_job_id: str = "") -> None:
        """Remove all non-active chapter-level jobs for *project_id*.

        Called after init_long succeeds so that stale FAILED/PAUSED/SUCCEEDED
        chapter entries do not appear in the studio after a project reinit.
        Only idle (non-RUNNING, non-QUEUED) jobs are removed; active jobs are
        left untouched.  The persisted history file is also cleared so that
        purged jobs do not reappear after an app restart.
        """
        _chapter_kinds = frozenset(
            {
                "prepare_chapter",
                "resolve_chapter_checkpoint",
                "resolve_chapter_checkpoint_finalize",
                "run_chapter",
                "repair_continuity",
                "polish_chapter",
            }
        )
        _active = {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        changed = False
        purged_ids: set[str] = set()
        with self._lock:
            to_remove = [
                jid
                for jid, rec in self._jobs.items()
                if jid != exclude_job_id
                and rec.project_id == project_id
                and rec.kind in _chapter_kinds
                and rec.status not in _active
            ]
            for jid in to_remove:
                del self._jobs[jid]
                self._workers.pop(jid, None)
                purged_ids.add(jid)
                changed = True
        if changed:
            self.jobs_changed.emit()
            # Sync disk: remove purged jobs from the persisted history file
            # so they don't reappear after an app restart.
            path = self._history_path(project_id)
            if path is not None:
                try:
                    history = self._read_history_records(path)
                    remaining = [r for r in history if r.job_id not in purged_ids]
                    if len(remaining) != len(history):
                        self._write_history_records(path, remaining)
                except OSError:
                    pass

    def _retire_paused_jobs_for_chapter(
        self,
        project_id: str,
        chapter_number: int,
        *,
        exclude_job_id: str = "",
    ) -> None:
        """Immediately mark PAUSED jobs for *project_id*/*chapter_number* as completed.

        Called when the user accepts a checkpoint so the old "等待决策" entry
        clears from the UI without waiting for the new job to finish.
        """
        _chapter_kinds = {
            "prepare_chapter",
            "resolve_chapter_checkpoint",
            "resolve_chapter_checkpoint_finalize",
            "run_chapter",
        }
        changed = False
        changed_job_ids: list[str] = []
        with self._lock:
            for record in self._jobs.values():
                if record.job_id == exclude_job_id:
                    continue
                if record.status != DesktopJobState.PAUSED:
                    continue
                if record.kind not in _chapter_kinds:
                    continue
                if record.project_id != project_id:
                    continue
                rec_ch = (record.result or {}).get("chapter_number")
                if rec_ch is not None and int(rec_ch) != int(chapter_number):
                    continue
                record.status = DesktopJobState.SUCCEEDED
                record.current_step = "completed"
                record.updated_at = _now_iso()
                changed = True
                changed_job_ids.append(record.job_id)
        if changed:
            self.jobs_changed.emit()
            for job_id in changed_job_ids:
                self._persist_terminal_job(job_id)

    def _handle_failed(self, job_id: str, error: object) -> None:
        if self._closed:
            return
        # Guard: if the job was already cancelled, discard this delayed error
        with self._lock:
            existing = self._jobs.get(job_id)
            if existing is not None and existing.status == DesktopJobState.FAILED:
                return
        payload = error if isinstance(error, dict) else {}
        failed_step = _infer_failed_step(existing, payload)
        error_text = ""
        audit_error_info: dict[str, Any] | None = None
        if isinstance(error, AuditError):
            audit_error_info = error.to_dict()
            parts = [f"【审计错误 · {error.error_type}】", error.message]
            if error.suggested_action:
                parts.append(f"建议：{error.suggested_action}")
            if error.recoverable:
                parts.append("✓ 可重试")
            if error.checkpoint_available:
                parts.append("✓ 检查点可用")
            error_text = "\n".join(parts)
        elif payload and isinstance(payload, dict):
            if "recoverable" in payload and "suggested_action" in payload:
                audit_error_info = payload
                parts = [
                    f"【审计错误 · {payload.get('error_type', 'unknown')}】",
                    payload.get("message", ""),
                ]
                if payload.get("suggested_action"):
                    parts.append(f"建议：{payload['suggested_action']}")
                if payload.get("recoverable"):
                    parts.append("✓ 可重试")
                if payload.get("checkpoint_available"):
                    parts.append("✓ 检查点可用")
                error_text = "\n".join(parts)
        if not error_text:
            if payload:
                title = str(payload.get("title") or "").strip()
                summary = str(payload.get("summary") or "").strip()
                error_text = f"【{title}】\n{summary}" if title and summary else (summary or title)
        if not error_text:
            error_text = str(error).strip() or "任务失败"
        self._mutate(
            job_id,
            status=DesktopJobState.FAILED,
            current_step=failed_step,
            error=error_text,
            error_summary=audit_error_info if audit_error_info else payload,
        )
        self._persist_terminal_job(job_id)
        with self._lock:
            _fail_rec = self._jobs.get(job_id)
            _fail_pid = _fail_rec.project_id if _fail_rec is not None else ""
            self._workers.pop(job_id, None)
            self._waiting_jobs.pop(job_id, None)
        self._publish_section_change(_fail_pid, "jobs")
        self.job_completed.emit(job_id)

    def _handle_cleanup_runtime(self, runtime: object) -> None:
        if self._closed:
            return
        try:
            shutdown = getattr(runtime, "shutdown", None)
            if not callable(shutdown):
                return
            run_async_coro(shutdown())
        except Exception as exc:
            logger.exception("Failed to cleanup runtime: %s", exc)

    def _mutate(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            for key, value in changes.items():
                setattr(record, key, value)
            record.updated_at = _now_iso()
        self._update_sleep_inhibitor()
        self.jobs_changed.emit()
