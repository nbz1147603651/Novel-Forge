# Desktop Layer — AGENTS.md

The frozen PySide6 compatibility UI and the primary NIMO managed-launcher
boundary for Novel Forge.

## Frozen fallback policy

React/Tauri NIMO is the default desktop and the only target for new product UI
work. `nimo` starts `tauri_launcher.py`; `nimo-p` is the explicit PySide6
safety/compatibility/emergency fallback. Do not add new product pages,
navigation, visual systems, or duplicate client-owned business state to
PySide. Changes here are limited to:

- safety, authority, data-integrity, recovery, or security corrections;
- compatibility with shared Engine/contracts and persisted projects;
- regression/reference fixtures needed to protect the primary client;
- fixes required to keep the emergency fallback operable.

When a shared backend contract changes, keep the fallback truthful where it is
affected, but implement the primary product interaction in
`clients/nimo-desktop/`. Do not delete the fallback or weaken its tests without
a separate retirement decision.

## Novel Workflow and Cross-Frontend Parity

For novel-flow changes, read the [current workflow](../../docs/novel-workflow-current.md) and [author-control design](../../docs/novel-authoring-control-design.md), then update their affected steps/status and tests in the same change. PySide and NIMO must reflect the same effective backend defaults, scope, decisions and stop/completion state; local UI preferences are not an authorization source.

Preserve existing visual/navigation intent unless the task requires changing it. Do not conflate AI recommendations with approval, preview waterlines with confirmed chapters, or polish scores with permission to rewrite author choices. Any new mode/capability must be checked in workspace/API as well as the UI, including saved-choice compatibility and restart behavior.

## Architecture

- **Workspace**: `novel_forge/workspace/` — facade bridging CLI/API/Desktop → pipeline
- **Pipeline**: `novel_forge/pipeline/long/` — long-form orchestration
- **Workers**: `novel_forge/desktop/workers/` — unified worker framework (cancel/run/error-wrap)
- **State**: `novel_forge/desktop/state/` — UIStore, WindowState, ObservablePageState
- **Jobs**: `novel_forge/desktop/jobs/` — DesktopJobManager
- **Window**: `novel_forge/desktop/window/` — main window (split from M3)
- **Pages**: `novel_forge/desktop/pages/` — 7 subdirs (chapter_studio, workflow, document_renderer, settings, standalone) with auto-discovery
- **Components**: `novel_forge/desktop/components/` — reusable widgets
- **Theme**: `novel_forge/desktop/theme/` — QSS palette + dark/light variants
- **Tokens**: `novel_forge/desktop/tokens/` — design tokens
- **Platform**: `novel_forge/desktop/platform/` — cross-platform (sleep_inhibit / ollama_paths / fonts)

## Sub-system conventions

Each subdir has its own AGENTS.md. Read the relevant one before editing.

## Lifecycle

All pages should call `safe_shutdown_page(self, workers=..., timers=..., signals=...)` in their `shutdown()` method. Do NOT call `waitForDone()` on global thread pools from page shutdown — that's window's job via `thread_pools.shutdown_desktop_thread_pools()`.

## Worker framework

All workers should inherit `BaseJobWorker` from `novel_forge.desktop.workers.base`:
- asyncio loop lifecycle
- threading.Event cancel protocol (`request_cancel()`)
- error wrapping via `summarize_desktop_error()`
- submit via `pool_for("job" | "ui_io" | "aux").start(self)`

For exception classes, follow the diamond-MRO pattern (see `novel_forge/core/exceptions_framework.py`).

## Thread pools

Three pools, all `QThreadPool` instances (see `novel_forge/desktop/thread_pools.py`):
- `job_pool` (long-running jobs, max ≥ 2)
- `ui_io_pool` (short UI I/O, max 2-4)
- `aux_pool` (misc, max 2-6)

Only `desktop_jobs/DesktopJobManager` is allowed to wait `job_pool`. Pages should NEVER `wait_for_done` on any pool — only cancel their own workers.

## Testing

- Tests for `desktop/` modules live in `tests/desktop/`
- Markers: `unit` / `integration` / `regression` / `visual` / `timeout(seconds)` (honored when pytest-timeout installed)
- Desktop tests use `pytest-qt` and require `QT_QPA_PLATFORM=offscreen` env var
- Mock embeddings via `NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS=true`

## Configuration

All config via `NOVEL_FORGE_*` environment variables, Pydantic `BaseSettings`. `.env.example` is the fallback source. `model_profiles.json` (gitignored) overrides provider routing when present. `NOVEL_FORGE_API_CALL_TIMEOUT_S` defaults to 900s (15 min).
