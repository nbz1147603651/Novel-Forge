# UI App Service Boundary

This document records the current UI/backend boundary after the first app-service
extraction pass.

## Current Shape

- `novel_forge.app_service` is the Qt-free boundary for UI-facing jobs.
- `JobService` owns job records, cancellation state, persisted terminal history,
  and in-memory job events.
- `JobService` enforces same-project write-job reuse and a bounded global
  concurrent job limit. Extra non-conflicting jobs remain queued until a running
  job reaches a terminal state.
- `WorkspaceCommandExecutor` maps `JobCommand` payloads to existing
  `workspace.contracts` models and `workspace.execution` entrypoints.
- FastAPI exposes the new service at `/api/v1/jobs`.
- PySide6 uses `DesktopJobManager` as a Qt adapter for all public desktop
  `submit_*` paths. Covered kinds are `run_short`, `init_long`,
  `run_chapter`, `prepare_chapter`, `resolve_chapter_checkpoint`,
  `resolve_chapter_checkpoint_finalize`, `repair_continuity`, `repair_causal`,
  `repair_issues`, `reevaluate_chapter`, `polish_chapter`,
  `book_consistency`, `export_book`, `reextract_relationships`,
  `repair_motif_history`, `rebuild_memory_vectors`,
  `sync_chapter_contracts`, and `extend_outline`.
- For these jobs, `DesktopJobManager` submits `JobCommand` to `JobService`,
  polls `JobEvent` from the in-memory subscription, and maps those events back
  to the existing `jobs_changed`, `job_completed`, `token_update`,
  `decision_required`, and `section_changed` behavior.
- `run_chapter` and `prepare_chapter` still honor the desktop dependency
  waiting queue. When queued dependencies become ready, the same desktop job id
  is passed into `JobService` through `JobCommand.job_id`.
- Checkpoint decisions can use a different visual record kind from the executor
  kind through `JobCommand.record_kind`; this preserves the existing task-flow
  labels for plan regeneration and finalize stages while still routing through
  the shared checkpoint executor.
- The legacy `_WorkspaceJobWorker` class remains only as a low-level
  compatibility/test utility. Public desktop submit paths no longer create or
  start it.

## Supported Job API

- `POST /api/v1/jobs`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/jobs/{job_id}/decision`
- `GET /api/v1/jobs/{job_id}/events`

SSE messages use the shared `JobEvent` contract. New subscribers receive a
snapshot replay, recent step events, and an immediate terminal event when the
job has already completed, failed, or paused.

## Migration Notes

- Do not move page code directly onto FastAPI routes.
- New desktop submit paths should create a `JobCommand` and enter through
  `DesktopJobManager._submit_app_service_job`.
- Keep Qt signal coalescing and widget lifecycle logic in the desktop adapter.
- Preserve the desktop "submitted job first appears as QUEUED" behavior. The
  service returns a queued snapshot before starting the worker thread.
- Keep business payloads in `workspace.contracts`; do not duplicate request
  schemas in app-service code.
- Preserve `task_flow_history.json` compatibility while PySide6 and API clients
  coexist.
- API lifespan and runtime reload must call `shutdown_job_service()` before
  clearing dependency caches so running workers are cancelled instead of
  becoming invisible.
