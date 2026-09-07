# Workers — AGENTS.md

Unified base class for desktop async workers.

## Layout

```
workers/
├── __init__.py            # re-exports BaseJobWorker, BaseJobWorkerSignals, safe_shutdown_page, etc.
├── base.py                # BaseJobWorker + BaseJobWorkerSignals (cancel + run lifecycle only)
├── lifecycle.py           # safe_shutdown_page helper (no global pool waits)
└── pool_assign.py         # pool_for(name) — 'job' | 'ui_io' | 'aux'
```

## Base class invariants

`BaseJobWorker(QRunnable)` provides:
1. asyncio loop lifecycle: new_event_loop → create_task → run_until_complete → drain → shutdown_asyncgens → close
2. `threading.Event`-driven `request_cancel()` + subclass `_on_cancel_requested()` hook for cooperative cancel
3. `summarize_desktop_error(exc).as_payload()` wrapped and emitted as `worker_failed`
4. `_run_async()` template method — subclasses override
5. `_cleanup_async_resources()` hook — release loop-bound clients before loop close
5. `submit()` helper routes to the right QThreadPool via `pool_for(self.pool)`

## Base signals vs business signals

`BaseJobWorkerSignals` defines LIFECYCLE signals only (`worker_started / worker_cancelled / worker_failed`).

Business signal shape belongs on the subclass. Subclasses override `signals_cls`:

```python
class MySignals(BaseJobWorkerSignals):
    step = Signal(str, dict)        # business signal — kept by subclass

class MyWorker(BaseJobWorker):
    signals_cls = MySignals
```

Do NOT add business signals to `BaseJobWorkerSignals` itself.

## Migration status

| Worker | Inherits BaseJobWorker? |
|-------|--------------------------|
| _WorkspaceJobWorker (jobs/) | ✅ yes |
| TaskModelCallLoader (planned) | (uses BaseJobWorker via pool=) |
| Other workers (final_revision, workflow_workers, settings, dialogs, subplot_manager, presets) | ❌ legacy asyncio.run — migrate opportunistically |
