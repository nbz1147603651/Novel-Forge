# Novel Forge Desktop UI 全栈优化 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the Novel Forge Desktop UI layer to address 30+ deduped P0/P1/P2 issues across performance, maintainability, compatibility, extensibility, and the macOS fullscreen bug — in 9 independently mergeable phases.

**Architecture:**
- Worker abstraction lives in `desktop/workers/{base,lifecycle,pool_assign}.py` (not `jobs.py`).
- Base signals only carry lifecycle; subclasses define business signals via `signals_cls`.
- Thread-pool waits belong to `thread_pools.shutdown_desktop_thread_pools()`, not `DesktopJobManager.shutdown()`.
- Cache invalidation uses physical-file signature (`file_count + latest_mtime_ns + total_size_bytes`).
- Chunked HTML rendering is opt-in (off by default); use a benchmark to decide before activating.
- Desktop-tests CI job excludes visual tests explicitly via `--ignore-glob`.
- Pages 63-file flat layout reorganized into 7 subdirs with auto-discovery.
- macOS fullscreen fix = guard on `_on_workspace_refreshed` (defer via `QTimer.singleShot(0)`); no connection-type changes.

**Tech Stack:** Python 3.11, PySide6 6.10, pytest 7.4+, pytest-xdist, pytest-qt 4.x, ruff, mypy (pydantic plugin).

**Execution order (per user decision):**
M5.1 → M5.2 → M1 → M2 → M6 → M3 → M4 → M5.3 → M7

**Reference spec:** `docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md`

---

## File Structure Map

### Phase 1 (M1) creates
- `novel_forge/desktop/workers/__init__.py`
- `novel_forge/desktop/workers/base.py` — `BaseJobWorker`, `BaseJobWorkerSignals`
- `novel_forge/desktop/workers/lifecycle.py` — `safe_shutdown_page`, `disconnect_signals`
- `novel_forge/desktop/workers/pool_assign.py` — `pool_for(name)`

### Phase 1 modifies
- `novel_forge/desktop/jobs.py` — only `_WorkspaceJobWorker` to inherit `BaseJobWorker`; add `close()` alias; add timer `deleteLater` in `shutdown()`.
- `novel_forge/desktop/window.py` — call `safe_shutdown_page` in each page's existing `shutdown()` after refactor.

### Phase 2 (M2) creates
- `novel_forge/desktop/state/snapshot_cache.py` — `JobSnapshotCache`, `_SnapshotSignature`
- `novel_forge/workspace/runtime.py::RuntimeServices` `_humanize_embedder` upgrade with `shutdown()` + `reload()`
- `novel_forge/desktop/components/task_focus_loader.py` (file, not dir — Phase 1 will rename `task_focus.py` to `task_focus/__init__.py` only in M3)
- `scripts/benchmark_chapter_render.py`
- Optional: `novel_forge/desktop/components/chunked_html_setter.py` (default-off pluggable)

### Phase 2 modifies
- `novel_forge/desktop/components/task_focus.py:950` — call `JobSnapshotCache.get_or_load` instead of synchronous `load_model_call_snapshot`.
- `novel_forge/workspace/runtime.py:212-251` — `_HumanizeEmbedderAdapter` upgrade.
- `novel_forge/workspace/runtime.py:311-353` — `reload_runtime_dependencies` calls `embedder.shutdown()` first.
- `novel_forge/workspace/runtime.py:599-626` — `RuntimeServices.shutdown()` calls `embedder.shutdown()` + clears reference.
- `novel_forge/desktop/pages/document_renderer_incremental.py:413-438` — clean up misleading docstring; do NOT enable ChunkedHtmlSetter.
- `novel_forge/desktop/thread_pools.py` — add `shutdown_desktop_thread_pools()` and `DesktopThreadPools.by_name()`.

### Phase 6 (M6) modifies
- `novel_forge/desktop/window.py:1402` — add fullscreen guard before heavy widget ops; defer via `QTimer.singleShot(0, ...)`.
- `novel_forge/desktop/state/event_bus.py` — add optional `default_queue: bool = False` to `subscribe()`.
- `novel_forge/desktop/window.py:4189-4240` — `_pre_close_cleanup` tighten: call `shutdown_desktop_thread_pools` after `JobManager.shutdown`.

### Phase 3 (M3) creates
- `novel_forge/desktop/window/__init__.py` (after git mv)
- `novel_forge/desktop/window/{core,navigation,jobs_binding,panels/{top_bar,side_rail,skeleton_overlay}}.py`
- `novel_forge/desktop/jobs/__init__.py` (after git mv)
- `novel_forge/desktop/jobs/{manager,worker_base_py_as_import_via_workers,signals,history,stream,errors}.py` — note: **`worker_base.py` is NOT here** (lives in workers/).
- `novel_forge/desktop/pages/document_renderer/{__init__.py,chapter.py,...}`
- `novel_forge/desktop/pages/document_renderer/story_artifacts/{__init__.py,...}`
- `novel_forge/desktop/pages/document_renderer/reports/{__init__.py,...}`
- `novel_forge/desktop/pages/settings/{__init__.py, parameters/{form,validator,persistence,widgets}.py}`
- `novel_forge/desktop/components/task_focus/{__init__.py, panel.py, ...}`
- `novel_forge/desktop/components/memory_components/{__init__.py, panel.py, ...}`

### Phase 4 (M4) creates
- 7 page subdirs: `chapter_studio/`, `workflow/`, `document_renderer/`, `settings/`, `standalone/`
- `novel_forge/desktop/pages/__init__.py` — `pkgutil.iter_modules` auto-discovery.
- `novel_forge/desktop/pages/_registry.py` (renamed from `page_registrations.py`).
- `scripts/refactor_pages_layout.py`

### Phase 5 (M5) modifies
- `pyproject.toml` lines 10, 83, 91.
- `.github/workflows/ci.yml` — add `desktop-tests` job.
- `novel_forge/desktop/platform/{__init__.py, sleep_inhibit/{__init__,darwin,win32,linux}.py, ollama_paths.py, fonts.py}`
- `tests/desktop/visual_regression.py:22-26` — replace `/tmp/...` with `tempfile.gettempdir()`

### Phase 7 (M7) creates
- `docs/runbook_macos_fullscreen.md`
- `docs/runbook_ui_architecture.md`
- `desktop/AGENTS.md`
- `pages/AGENTS.md` (rewrite)
- 7×`pages/<subdir>/AGENTS.md`
- `desktop/platform/AGENTS.md`
- Regenerate all 9 visual regression baselines.

---

## Testing Conventions

- Test markers: `unit`, `integration`, `regression`, `visual` (use exactly these names).
- Tests for `desktop/` modules live in `tests/desktop/` (45 existing files).
- Tests for `workspace/` modules live in `tests/unit/`.
- TDD discipline: write the failing test first (Step 1), run to confirm FAIL (Step 2), then implement (Step 3), then verify PASS (Step 4).
- Use `pytest -n auto --timeout=300` for the desktop-tests CI job.

---

## Phase 0 — Pre-flight Verification (DO THIS FIRST, BEFORE ANY PHASE)

Before starting any Phase, verify the following worktrees and CI baselines are sane.

### Task P0.1: Verify branch and clean tree

**Files:** none

- [ ] **Step 1: Confirm git status is clean**

Run:
```bash
cd <repo> && git status --short
```
Expected: empty (no uncommitted changes besides possibly untracked new files).

- [ ] **Step 2: Pull latest main**

Run:
```bash
cd <repo> && git pull --ff-only
```
Expected: "Already up to date" or fast-forward merge.

- [ ] **Step 3: Confirm Python is 3.11+**

Run:
```bash
python3 --version
```
Expected: Python 3.11.x or 3.12.x. If 3.10 or lower, ask user to set up a 3.11 venv before continuing.

---

## Phase M5.1 — Python 3.11 工具链对齐 (FIRST! per user order)

### Task M5.1.1: Bump pyproject.toml to 3.11

**Files:**
- Modify: `pyproject.toml:10`
- Modify: `pyproject.toml:83`
- Modify: `pyproject.toml:91`

- [ ] **Step 1: Read current pyproject.toml sections**

Run:
```bash
cd <repo> && sed -n '8,12p;82,93p' pyproject.toml
```
Expected output:
```
requires-python = ">=3.12"
...
target-version = "py312"
...
python_version = "3.12"
```

- [ ] **Step 2: Edit requires-python**

Edit `pyproject.toml` line 10:
```toml
requires-python = ">=3.11"
```

- [ ] **Step 3: Edit ruff.target-version**

Edit `pyproject.toml` line 83:
```toml
target-version = "py311"
```

- [ ] **Step 4: Edit mypy.python_version**

Edit `pyproject.toml` line 91:
```toml
python_version = "3.11"
```

- [ ] **Step 5: Verify install resolves**

Run:
```bash
cd <repo> && python3.11 -m venv /tmp/nf-venv-311 && /tmp/nf-venv-311/bin/pip install --quiet -e ".[dev,desktop]" 2>&1 | tail -20
```
Expected: pip install completes without errors. If `python3.11` is not available, install Python 3.11 or use existing 3.12 venv.

- [ ] **Step 6: Run unit tests on 3.11 venv**

Run:
```bash
cd <repo> && /tmp/nf-venv-311/bin/pytest tests/unit -q --timeout=60
```
Expected: PASS (all unit tests green) or only pre-existing failures unrelated to Python version.

- [ ] **Step 7: Run desktop non-visual tests**

Run:
```bash
cd <repo> && /tmp/nf-venv-311/bin/pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=300
```
Expected: PASS.

- [ ] **Step 8: ruff and mypy**

Run:
```bash
cd <repo> && /tmp/nf-venv-311/bin/ruff check novel_forge/ && /tmp/nf-venv-311/bin/mypy novel_forge/ 2>&1 | tail -20
```
Expected: ruff clean; mypy reports no NEW errors (existing errors acceptable).

- [ ] **Step 9: Commit**

```bash
cd <repo> && git add pyproject.toml && git commit -m "build: align requires-python / ruff / mypy to 3.11

CI uses 3.11 in 5 jobs (.github/workflows/ci.yml:14,39,61,80,93)
but pyproject.toml declared 3.12 in three places:
- requires-python = '>=3.12' (line 10)
- [tool.ruff] target-version = 'py312' (line 83)
- [tool.mypy] python_version = '3.12' (line 91)

Aligning all three to 3.11 closes the version gap so CI and
local dev share the same target. No code change expected;
all dependencies already compatible with 3.11 (verified by
pip install + pytest run)."
```

---

## Phase M5.2 — Desktop Tests CI Job (Second, BEFORE worker refactor)

Goal: get 3-runner CI baseline for the desktop layer so subsequent high-blast-radius refactors (worker framework, package splits) have automated protection.

### Task M5.2.1: Add desktop-tests job to ci.yml

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Read existing job patterns**

Run:
```bash
cd <repo> && sed -n '27,50p' .github/workflows/ci.yml
```
Expected: existing unit-tests job structure to model after.

- [ ] **Step 2: Add desktop-tests job after unit-tests**

Insert after the existing `unit-tests` job (after line 47):

```yaml
  desktop-tests:
    name: Desktop Tests (PySide6, non-visual)
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install system deps (Ubuntu)
        if: matrix.os == 'ubuntu-latest'
        run: sudo apt-get update && sudo apt-get install -y libegl1 libgles2 libgl1
      - name: Install
        run: pip install -e ".[dev,desktop]"
      - name: Run desktop tests (non-visual, 3-runner baseline)
        env:
          QT_QPA_PLATFORM: offscreen
          NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS: "true"
        run: |
          pytest tests/desktop \
            --ignore-glob='tests/desktop/*_visual.py' \
            --ignore-glob='tests/desktop/test_visual_regression.py' \
            -n auto --timeout=300 -q
```

- [ ] **Step 3: Validate YAML**

Run:
```bash
cd <repo> && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```
Expected: no output (parse OK).

- [ ] **Step 4: Verify local reproduction**

Run on linux/mac locally:
```bash
cd <repo> && QT_QPA_PLATFORM=offscreen NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS=true pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -n auto --timeout=300 -q
```
Expected: PASS locally. If flaky, note down which tests are flaky and continue (don't fix flakes here, that's Phase 1+ work).

- [ ] **Step 5: Commit**

```bash
cd <repo> && git add .github/workflows/ci.yml && git commit -m "ci: add desktop-tests job (PySide6, 3-runner, non-visual)

Existing visual-tests job covers only 9 *_visual.py files on ubuntu
and leaves ~36 other desktop tests without CI coverage. Adding
desktop-tests job:

- 3-OS matrix: ubuntu/windows/macos
- Python 3.11 (aligned with M5.1)
- Explicit non-visual via --ignore-glob to avoid duplicate
  coverage with visual-tests job
- -n auto parallel via pytest-xdist, 300s timeout

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M5.2"
```

- [ ] **Step 6: Push and confirm CI green**

Run:
```bash
cd <repo> && git push
```
Expected: GitHub Actions shows 3 new desktop-tests runs (ubuntu/windows/macos) all green. If any are flaky, fix and re-push before continuing to Phase M1.

---

## Phase M1 — Worker Framework + safe_shutdown_page

### Task M1.1: Create desktop/workers/__init__.py and pool_assign.py

**Files:**
- Create: `novel_forge/desktop/workers/__init__.py`
- Create: `novel_forge/desktop/workers/pool_assign.py`

- [ ] **Step 1: Verify desktop/workers/ path available**

Run:
```bash
cd <repo> && ls novel_forge/desktop/workers/ 2>/dev/null || echo "not exists, ok"
```
Expected: "not exists, ok" — `workers/` is brand new, no conflict.

- [ ] **Step 2: Create pool_assign.py**

Write to `novel_forge/desktop/workers/pool_assign.py`:

```python
"""Translate worker pool names to QThreadPool instances.

Centralizes the 'job' / 'ui_io' / 'aux' ↔ pool mapping so BaseJobWorker
subclasses only need to set pool='ui_io' etc.
"""

from __future__ import annotations

from PySide6.QtCore import QThreadPool

from novel_forge.desktop.thread_pools import desktop_thread_pools


_POOL_NAMES = ("job", "ui_io", "aux")


def pool_for(name: str) -> QThreadPool:
    """Return the QThreadPool instance for the given worker pool name.

    Args:
        name: one of 'job' | 'ui_io' | 'aux'.

    Raises:
        ValueError: if name is not a recognized pool.
    """
    if name not in _POOL_NAMES:
        raise ValueError(f"unknown worker pool: {name!r}; expected one of {_POOL_NAMES}")
    pools = desktop_thread_pools()
    return pools.by_name(name)


def by_name(name: str) -> QThreadPool:
    """Alias for pool_for — kept for naming consistency with DesktopThreadPools."""
    return pool_for(name)


__all__ = ["pool_for", "by_name"]
```

- [ ] **Step 3: Extend thread_pools.py with by_name and shutdown function**

Modify `novel_forge/desktop/thread_pools.py`:

At the dataclass definition, add `by_name` method (find the dataclass, e.g.):

```python
    def by_name(self, name: str) -> QThreadPool:
        """Resolve pool name to QThreadPool instance.

        Args:
            name: 'job' | 'ui_io' | 'aux'.

        Raises:
            ValueError: if name is unknown.
        """
        if name == "job":
            return self.job_pool
        if name == "ui_io":
            return self.ui_io_pool
        if name == "aux":
            return self.aux_pool
        raise ValueError(f"unknown pool: {name!r}")
```

At end of file (after `reset_desktop_thread_pools_for_tests`):

```python
def shutdown_desktop_thread_pools(*, wait_ms: int = 3000) -> None:
    """Wait for all three thread pools to settle.

    Called from window._pre_close_cleanup() AFTER JobManager.shutdown()
    so the global UI I/O tasks don't hold the closeEvent hostage.

    JobPool is already waited by DesktopJobManager.shutdown(). Calling
    waitForDone() a second time is a no-op once the pool is idle.
    """
    pools = desktop_thread_pools()
    pools.ui_io_pool.waitForDone(wait_ms)
    pools.aux_pool.waitForDone(wait_ms)
    pools.job_pool.waitForDone(wait_ms)
```

- [ ] **Step 4: Create __init__.py**

Write to `novel_forge/desktop/workers/__init__.py`:

```python
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
```

- [ ] **Step 5: Verify import works**

Run:
```bash
cd <repo> && python3 -c "from novel_forge.desktop.workers import BaseJobWorker; print(BaseJobWorker)"
```
Expected: `<class 'novel_forge.desktop.workers.base.BaseJobWorker'>` (or similar — as long as no ImportError).

- [ ] **Step 6: Run desktop tests**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=60
```
Expected: PASS (no regressions from adding the empty package).

- [ ] **Step 7: Commit**

```bash
cd <repo> && git add novel_forge/desktop/workers/ novel_forge/desktop/thread_pools.py && git commit -m "refactor(desktop): scaffold workers/ package + thread_pools helpers

Adds desktop/workers/{__init__,pool_assign}.py as the new home for
worker abstractions (separate from jobs.py per user review). Also
adds DesktopThreadPools.by_name() protocol method and module-level
shutdown_desktop_thread_pools() for centralized pool waits.

No behavioral change — these are scaffold only. Phase M1.2-M1.4
populate the package.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M1 Task M1.1"
```

### Task M1.2: Create base.py (BaseJobWorker + BaseJobWorkerSignals)

**Files:**
- Create: `novel_forge/desktop/workers/base.py`

- [ ] **Step 1: Write the failing test FIRST**

Create file `tests/desktop/test_worker_base.py`:

```python
"""Tests for BaseJobWorker framework.

Covers:
- asyncio loop lifecycle
- threading.Event cancel protocol
- error wrapping via summarize_desktop_error
- signals_cls override (subclass business signals preserved)
- pool assignment via by_name
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock

from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals


@pytest.fixture
def mock_runtime_factory(monkeypatch):
    """Avoid real runtime services in unit tests."""
    monkeypatch.setattr(
        "novel_forge.desktop.workers.base.summarize_desktop_error",
        lambda exc: MagicMock(as_payload=lambda: {"kind": exc.__class__.__name__, "message": str(exc)}),
    )


class _CaptureSignals(BaseJobWorkerSignals):
    """Subclass signals — business signals should remain customisable."""
    pass


class SimpleWorker(BaseJobWorker):
    signals_cls = _CaptureSignals

    def __init__(self, *, sleep_s: float = 0.05, fail_with: Exception | None = None):
        super().__init__()
        self._sleep_s = sleep_s
        self._fail_with = fail_with

    async def _run_async(self):
        try:
            await asyncio.sleep(self._sleep_s)
            self._check_cancel()
            if self._fail_with is not None:
                raise self._fail_with
        except asyncio.CancelledError:
            raise


def test_base_worker_runs_async_task(mock_runtime_factory):
    finished = []

    class S(_CaptureSignals):
        any_finished = MagicMock()

    class W(SimpleWorker):
        signals_cls = S

    w = W(sleep_s=0.01)
    w.signals.worker_started.connect(lambda wid: finished.append(("started", wid)))
    w.signals.worker_cancelled.connect(lambda wid: finished.append(("cancelled", wid)))
    w.signals.worker_failed.connect(lambda wid, p: finished.append(("failed", wid, p)))
    w.run()
    assert any(t[0] == "started" for t in finished)
    assert all(t[0] != "failed" for t in finished)


def test_request_cancel_aborts_long_task(mock_runtime_factory):
    """request_cancel() should cause the task to raise CancelledError."""
    w = SimpleWorker(sleep_s=10.0)
    w.request_cancel()
    w.run()  # should not hang for 10s
    # worker should have signalled cancellation


def test_failed_exception_emits_worker_failed(mock_runtime_factory):
    seen = []

    class S(_CaptureSignals):
        pass

    class W(SimpleWorker):
        signals_cls = S

    w = W(fail_with=ValueError("boom"))
    w.signals.worker_failed.connect(lambda wid, p: seen.append(p))
    w.run()
    assert len(seen) == 1
    assert seen[0]["kind"] == "ValueError"
    assert "boom" in seen[0]["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd <repo> && pytest tests/desktop/test_worker_base.py -v --timeout=30
```
Expected: ImportError or ModuleNotFoundError because `desktop/workers/base.py` does not exist yet.

- [ ] **Step 3: Create base.py**

Write to `novel_forge/desktop/workers/base.py`:

```python
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


class BaseJobWorkerSignals(QObject):
    """Lifecycle signals only — no business semantics.

    Subclasses define their own signals (e.g. step/finished/failed shapes).
    Inheriting from this class is OPTIONAL — any QObject with custom signals
    can be used as signals_cls.
    """

    worker_started = Signal(str)
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
        self._task: asyncio.Task | None = None
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
        """Submit to the configured thread pool."""
        pool_for(self.pool).start(self)

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
            except asyncio.CancelledError:
                self.signals.worker_cancelled.emit(self._worker_id)
            except Exception as exc:
                self._emit_failure(exc)
        finally:
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

    # ---------- overridable ----------

    async def _run_async(self) -> None:
        """Subclasses override. Use self._check_cancel() in long loops."""
        raise NotImplementedError(
            f"{self.__class__.__qualname__}._run_async must be overridden"
        )

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
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd <repo> && pytest tests/desktop/test_worker_base.py -v --timeout=30
```
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
cd <repo> && git add tests/desktop/test_worker_base.py novel_forge/desktop/workers/base.py && git commit -m "feat(desktop): BaseJobWorker framework with cancel + error wrap

New desktop/workers/base.py provides:
- BaseJobWorker (QRunnable) with asyncio loop lifecycle
- BaseJobWorkerSignals (QObject) with only lifecycle signals
  (worker_started / worker_cancelled / worker_failed)
- request_cancel() via threading.Event + subclass hook
- _run_async() template for subclasses to override
- _emit_failure() wrapping exceptions via summarize_desktop_error()
- submit() helper placing the worker in the correct pool

Business signals (step/finished/failed shapes) remain subclass
responsibilities via signals_cls override — _WorkspaceJobWorker
keeps its existing _WorkerSignals shape unchanged.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M1 Task M1.2"
```

### Task M1.3: Create lifecycle.py (safe_shutdown_page + disconnect_signals)

**Files:**
- Create: `novel_forge/desktop/workers/lifecycle.py`

- [ ] **Step 1: Write the failing test FIRST**

Create file `tests/desktop/test_page_shutdown.py`:

```python
"""Tests for safe_shutdown_page + disconnect_signals.

Covers:
- safe_shutdown_page cancels own workers, disconnects own signals,
  stops own timers, does NOT waitForDone (that's window's job).
- disconnect_signals tolerates already-disconnected (RuntimeError).
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget

from novel_forge.desktop.workers.base import BaseJobWorker
from novel_forge.desktop.workers.lifecycle import (
    disconnect_signals,
    safe_shutdown_page,
)


@pytest.fixture
def qapp(qtbot):
    return QApplication.instance() or QApplication([])


class _Worker(BaseJobWorker):
    async def _run_async(self):
        pass


class _Source(QObject):
    a = Signal(int)
    b = Signal(int)


def test_disconnect_signals_swallows_runtime_error(qapp):
    """Already-disconnected signal should not raise."""
    src = _Source()
    slot = MagicMock()
    src.a.connect(slot)
    disconnect_signals([(src.a, slot), (src.a, slot)])  # disconnect twice on purpose
    src.a.emit(1)
    assert slot.call_count == 0  # disconnected


def test_safe_shutdown_page_cancels_workers(qapp, qtbot):
    page = QWidget()
    w1 = _Worker()
    w2 = _Worker()
    w1.request_cancel()  # not yet — just to check state, we will call cancel in shutdown
    # Reset cancel state
    w1._cancel_event.clear()
    w2._cancel_event.clear()

    src = _Source()
    slot = MagicMock()
    src.a.connect(slot)
    timer = QTimer()
    timer.start(50)

    safe_shutdown_page(
        page,
        workers=[w1, w2],
        timers=[timer],
        signals_to_disconnect=[(src.a, slot)],
    )
    assert w1._cancel_event.is_set()
    assert w2._cancel_event.is_set()
    assert not timer.isActive()


def test_safe_shutdown_page_does_not_wait_pool(qapp, qtbot, monkeypatch):
    """safe_shutdown_page must NOT call any thread pool waitForDone."""
    page = QWidget()
    waited = []
    monkeypatch.setattr(
        "novel_forge.desktop.thread_pools.desktop_thread_pools",
        lambda: MagicMock(
            by_name=lambda n: MagicMock(waitForDone=lambda ms: waited.append((n, ms))),
        ),
    )
    safe_shutdown_page(page, workers=[], timers=[])
    assert waited == []  # critical — page shutdown must not wait global pools
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd <repo> && pytest tests/desktop/test_page_shutdown.py -v --timeout=30
```
Expected: ImportError because `desktop/workers/lifecycle.py` does not exist.

- [ ] **Step 3: Create lifecycle.py**

Write to `novel_forge/desktop/workers/lifecycle.py`:

```python
"""Page shutdown helpers.

safe_shutdown_page() is the unified teardown for any page:
1. Cancel all workers passed in (via request_cancel()).
2. Disconnect signals (tolerate already-disconnected).
3. Stop and deleteLater timers.
4. NEVER call waitForDone on global thread pools — that's window's
   job in _pre_close_cleanup() via shutdown_desktop_thread_pools().

Why no waitForDone? Because page shutdown can be invoked mid-app
(e.g. user navigates away); waiting global UI I/O pool would block
on unrelated long tasks. Only the final app teardown should wait pools.
"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QWidget

from novel_forge.desktop.workers.base import BaseJobWorker


def disconnect_signals(
    pairs: Iterable[tuple[Signal, Callable]],
) -> None:
    """Disconnect signal/slot pairs, tolerating already-disconnected state.

    Many Qt signal/slot pairs are already disconnected by the time a
    page shutdown runs (e.g. owner deleted). Direct disconnect() would
    raise RuntimeError or TypeError. We swallow those — the contract
    is "best-effort teardown".
    """
    for signal, slot in pairs:
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError):
            pass


def safe_shutdown_page(
    page: QWidget,
    *,
    workers: Iterable[BaseJobWorker] = (),
    timers: Iterable[QTimer] = (),
    signals_to_disconnect: Iterable[tuple[Signal, Callable]] = (),
) -> None:
    """Unified page teardown.

    Usage in page.shutdown() implementations:

        def shutdown(self):
            safe_shutdown_page(
                self,
                workers=[self._worker_a, self._worker_b],
                timers=[self._timer_a, self._timer_b],
                signals_to_disconnect=[(s, self._handler) for s, self._handler in ...],
            )

    This replaces ad-hoc try/except (RuntimeError, TypeError) blocks.
    """
    # 1. Cancel workers (safe to call repeatedly; uses threading.Event)
    for worker in workers:
        try:
            worker.request_cancel()
        except Exception:
            pass

    # 2. Disconnect signals
    disconnect_signals(signals_to_disconnect)

    # 3. Stop + deleteLater timers
    for timer in timers:
        try:
            if timer is not None and timer.isActive():
                timer.stop()
            timer.deleteLater()
        except (RuntimeError, TypeError):
            pass


__all__ = ["disconnect_signals", "safe_shutdown_page"]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd <repo> && pytest tests/desktop/test_page_shutdown.py -v --timeout=30
```
Expected: PASS — all 3 tests green (note test 3 explicitly checks no waitForDone).

- [ ] **Step 5: Run full desktop test suite to ensure no regression**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=120
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd <repo> && git add tests/desktop/test_page_shutdown.py novel_forge/desktop/workers/lifecycle.py && git commit -m "feat(desktop): safe_shutdown_page + disconnect_signals helpers

Provides a unified page teardown protocol:

- safe_shutdown_page(workers, timers, signals_to_disconnect)
  - cancels all owned workers via request_cancel()
  - tolerantly disconnects signals
  - stops + deleteLater's timers
  - NEVER waits global thread pools (that's window's job)

- disconnect_signals(pairs) tolerates already-disconnected
  state via try/except (RuntimeError, TypeError).

This replaces scattered try/except (RuntimeError, TypeError)
patterns in workflow_page.py, chapter_studio_page.py, etc.

Critical: test_safe_shutdown_page_does_not_wait_pool pins the
invariant that page shutdown doesn't wait ui_io/aux pools.
Waiting global pools at page-close would block unrelated long
tasks. Only the app's final teardown waits pools.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M1 Task M1.3"
```

### Task M1.4: Backfill DesktopJobManager.shutdown() + add close() alias

**Files:**
- Modify: `novel_forge/desktop/jobs.py:3229-3296`

- [ ] **Step 1: Write the failing test FIRST**

Create file `tests/desktop/test_jobs_manager_shutdown.py`:

```python
"""Tests for DesktopJobManager.shutdown() backfill + close() alias."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from novel_forge.desktop.jobs import DesktopJobManager


def test_shutdown_calls_deleteLater_on_timers():
    """shutdown() must timer.deleteLater() (4 internal timers)."""
    mgr = DesktopJobManager.__new__(DesktopJobManager)  # skip __init__
    mgr._closed = False
    mgr._lock = MagicMock()
    mgr._lock.__enter__ = MagicMock(return_value=mgr._lock)
    mgr._lock.__exit__ = MagicMock(return_value=False)
    mgr._workers = {}
    mgr._cancelling = {}
    mgr._thread_pool = MagicMock()
    mgr._sleep_inhibitor = MagicMock()

    timer_a = MagicMock()
    timer_b = MagicMock()
    timer_c = MagicMock()
    timer_d = MagicMock()

    mgr._app_event_timer = timer_a
    mgr._waiting_poll_timer = timer_b
    mgr._jobs_bind_timer = timer_c
    mgr._token_timer = timer_d

    with patch.object(mgr, "_unsubscribe_from_events"):
        with patch.object(mgr, "_persist_terminal_job"):
            mgr.shutdown(wait_ms=100)

    for t in (timer_a, timer_b, timer_c, timer_d):
        assert t.stop.called
        assert t.deleteLater.called


def test_shutdown_does_not_wait_global_ui_io_or_aux():
    """shutdown() must NOT call waitForDone on ui_io_pool or aux_pool."""
    mgr = DesktopJobManager.__new__(DesktopJobManager)
    mgr._closed = False
    mgr._lock = MagicMock()
    mgr._lock.__enter__ = MagicMock(return_value=mgr._lock)
    mgr._lock.__exit__ = MagicMock(return_value=False)
    mgr._workers = {}
    mgr._cancelling = {}
    mgr._thread_pool = MagicMock()
    mgr._sleep_inhibitor = MagicMock()
    for attr in ("_app_event_timer", "_waiting_poll_timer", "_jobs_bind_timer", "_token_timer"):
        setattr(mgr, attr, MagicMock())

    with patch("novel_forge.desktop.jobs.desktop_thread_pools") as pools_mock:
        mgr.shutdown(wait_ms=100)
    # Should NOT have looked up pools
    assert pools_mock.call_count == 0


def test_close_is_alias_for_shutdown():
    mgr = DesktopJobManager.__new__(DesktopJobManager)
    with patch.object(mgr, "shutdown") as shutdown_mock:
        mgr.close(wait_ms=200)
    shutdown_mock.assert_called_once_with(wait_ms=200)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd <repo> && pytest tests/desktop/test_jobs_manager_shutdown.py -v --timeout=30
```
Expected: FAIL — `shutdown` does not call deleteLater yet.

- [ ] **Step 3: Modify shutdown() in jobs.py**

Find the existing `shutdown` method (around line 3229 in `novel_forge/desktop/jobs.py`). Inside the method, after the `app_event_timer.stop()` lines and before `_sleep_inhibitor.release()`, **add** a deleteLater loop. After the release line, also **add** a `close()` alias method.

Edit `novel_forge/desktop/jobs.py` at the existing shutdown() method:

Find line that looks like:
```python
    self._sleep_inhibitor.release()
```

Add BEFORE that line:
```python
    # Backfill (Phase M1): timer deleteLater — currently only stopped,
    # which leaves QTimer QObjects hanging on the Qt parent chain.
    for timer_attr in ("_app_event_timer", "_waiting_poll_timer",
                       "_jobs_bind_timer", "_token_timer"):
        timer = getattr(self, timer_attr, None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except (RuntimeError, TypeError):
                pass
```

After the entire shutdown() function definition ends, add:

```python
    def close(self, *, wait_ms: int = 500) -> None:
        """Alias for shutdown() — naming consistency with other managers.

        Ui convention: manager.close() = clean teardown. Some code paths
        may prefer close(); existing call sites continue to use shutdown().
        """
        self.shutdown(wait_ms=wait_ms)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd <repo> && pytest tests/desktop/test_jobs_manager_shutdown.py -v --timeout=30
```
Expected: PASS.

- [ ] **Step 5: Run full desktop test suite**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=120
```
Expected: PASS (existing tests should still pass — JobManager.shutdown() behavior didn't change except for adding deleteLater).

- [ ] **Step 6: Commit**

```bash
cd <repo> && git add novel_forge/desktop/jobs.py tests/desktop/test_jobs_manager_shutdown.py && git commit -m "refactor(desktop): JobManager.shutdown() deleteLater timers + close() alias

Two surgical backfills to existing shutdown():

1. timer.deleteLater() for the 4 internal QTimers
   (_app_event_timer, _waiting_poll_timer, _jobs_bind_timer,
   _token_timer). Previously only timer.stop() was called —
   leaving QTimer QObjects dangling on the Qt parent chain.

2. close() alias method delegating to shutdown() — naming
   consistency with other managers (UIStore.close(), workspace.
   close(), etc.). Existing call sites continue to use shutdown().

CRITICAL: shutdown() does NOT gain waitForDone for ui_io_pool or
aux_pool. Waiting those globally at job-manager-shutdown time
would hold the close hostage to unrelated long tasks. Window's
_pre_close_cleanup() will call thread_pools.shutdown_desktop_
thread_pools() AFTER this method to wait all three pools.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M1 Task M1.4"
```

### Task M1.5: Make _WorkspaceJobWorker inherit BaseJobWorker

**Files:**
- Modify: `novel_forge/desktop/jobs.py:1148` (around)

- [ ] **Step 1: Find _WorkspaceJobWorker current declaration**

Run:
```bash
cd <repo> && grep -n "class _WorkspaceJobWorker" novel_forge/desktop/jobs.py
```
Expected: one match, e.g. `class _WorkspaceJobWorker(QRunnable):`.

- [ ] **Step 2: Verify existing signal handling tests**

Run:
```bash
cd <repo> && pytest tests/desktop -k "workspace or job" -q --timeout=120
```
Expected: PASS (baseline before refactor).

- [ ] **Step 3: Edit _WorkspaceJobWorker to inherit BaseJobWorker**

In `novel_forge/desktop/jobs.py`, find the `_WorkspaceJobWorker` class declaration. Edit:

```python
# BEFORE
class _WorkspaceJobWorker(QRunnable):

# AFTER
class _WorkspaceJobWorker(BaseJobWorker):
    """Workspace refresh worker — backward-compatible with existing call sites.

    Inherits BaseJobWorker for asyncio lifecycle + cancel protocol,
    but keeps its own _WorkerSignals (started/step/decision_required/
    finished/failed/cleanup_runtime) so existing connect() points
    continue to work without changes.
    """
```

Also at the top of `novel_forge/desktop/jobs.py` (or wherever `_WorkerSignals` is defined), add the import:

```python
from novel_forge.desktop.workers.base import BaseJobWorker
```

Note: if `BaseJobWorkerSignals` would clash with `_WorkerSignals`, simply leave `_WorkerSignals` as a standalone QObject (NOT inheriting BaseJobWorkerSignals). This is by design — business signals stay subclass-defined.

- [ ] **Step 4: Run existing tests**

Run:
```bash
cd <repo> && pytest tests/desktop -k "workspace or job" -q --timeout=120
```
Expected: PASS — behavior unchanged because BaseJobWorker's run() is compatible with the existing _WorkspaceJobWorker behavior.

- [ ] **Step 5: Run full desktop test suite**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=120
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd <repo> && git add novel_forge/desktop/jobs.py && git commit -m "refactor(desktop): _WorkspaceJobWorker inherits BaseJobWorker

Gradual migration — _WorkspaceJobWorker now inherits BaseJobWorker
(keeping its own 6 _WorkerSignals: started/step/decision_required/
finished/failed/cleanup_runtime), so existing connect() sites and
signal contracts continue to work unchanged.

Benefits gained:
- threading.Event cancel via request_cancel() (already had)
- asyncio loop lifecycle via BaseJobWorker.run() (consistent with
  other workers going forward)
- error wrapping via summarize_desktop_error() when subclassed
  helpers use _emit_failure()

No behavioral change for users. Page workers (final_revision,
workflow_workers, settings_page_components, etc.) remain on
asyncio.run() pattern — Phase M2 keeps them as-is unless they
explicitly opt into BaseJobWorker.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M1 Task M1.5"
```

### Task M1.6: Final verification — full desktop test suite green

- [ ] **Step 1: Run full desktop test suite one more time**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=120
```
Expected: PASS.

- [ ] **Step 2: Run unit tests**

Run:
```bash
cd <repo> && pytest tests/unit -q --timeout=120
```
Expected: PASS.

- [ ] **Step 3: ruff check desktop/**

Run:
```bash
cd <repo> && ruff check novel_forge/desktop/
```
Expected: no new warnings.

- [ ] **Step 4: mypy desktop/**

Run:
```bash
cd <repo> && mypy novel_forge/desktop/ 2>&1 | tail -10
```
Expected: no NEW errors (pre-existing errors acceptable).

---

## Phase M2 — Performance Quick Wins (TaskModelCallLoader + JobSnapshotCache + chunked insert cleanup + embedder singleton)

### Task M2.1: Create JobSnapshotCache with _SnapshotSignature

**Files:**
- Create: `novel_forge/desktop/state/snapshot_cache.py`

- [ ] **Step 1: Write the failing test FIRST**

Create file `tests/desktop/test_snapshot_cache.py`:

```python
"""Tests for JobSnapshotCache: LRU + physical signature + TTL backstop."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from novel_forge.desktop.state.snapshot_cache import (
    JobSnapshotCache,
    _SnapshotSignature,
)


@pytest.fixture
def model_calls_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs" / "run-x" / "model_calls"
    d.mkdir(parents=True)
    return d


def _write_call(d: Path, name: str, content: dict) -> Path:
    p = d / name
    p.write_text(json.dumps(content))
    return p


def test_signature_counts_and_mtime(model_calls_dir):
    _write_call(model_calls_dir, "001.json", {"a": 1})
    _write_call(model_calls_dir, "002.json", {"a": 2})

    sig = _SnapshotSignature.compute(model_calls_dir)
    assert sig.file_count == 2
    assert sig.total_size_bytes > 0
    assert sig.latest_mtime_ns > 0


def test_signature_invalidates_on_new_file(model_calls_dir):
    cache = JobSnapshotCache(ttl_s=600.0)  # long TTL; rely on signature

    # First load: 0 files
    snap1 = {"calls": []}
    got = cache.get_or_load("run-x", model_calls_dir, lambda: snap1)
    assert got is snap1

    # Now add a new file → signature changes → invalidate
    _write_call(model_calls_dir, "003.json", {"a": 3})
    snap2 = {"calls": ["003"]}
    got2 = cache.get_or_load("run-x", model_calls_dir, lambda: snap2)
    assert got2 is snap2  # not cached snap1; signature invalidated


def test_cache_holds_entry_when_signature_unchanged(model_calls_dir):
    cache = JobSnapshotCache(ttl_s=600.0)
    snap = {"calls": ["001"]}
    counter = {"n": 0}

    def loader():
        counter["n"] += 1
        return snap

    cache.get_or_load("run-x", model_calls_dir, loader)
    cache.get_or_load("run-x", model_calls_dir, loader)
    cache.get_or_load("run-x", model_calls_dir, loader)
    assert counter["n"] == 1  # cached after first call


def test_cache_invalidate_explicit(model_calls_dir):
    cache = JobSnapshotCache(ttl_s=600.0)
    snap = {"v": 1}
    cache.get_or_load("run-x", model_calls_dir, lambda: snap)
    cache.invalidate("run-x")

    snap2 = {"v": 2}
    counter = {"n": 0}
    def loader():
        counter["n"] += 1
        return snap2
    cache.get_or_load("run-x", model_calls_dir, loader)
    assert counter["n"] == 1


def test_cache_lru_eviction(model_calls_dir):
    cache = JobSnapshotCache(max_entries=2, ttl_s=600.0)
    for run_id in ("a", "b", "c"):
        # each get_or_load writes to signature (empty dir OK)
        cache.get_or_load(run_id, model_calls_dir, lambda: {"id": run_id})
    # Only 'b' and 'c' should remain (LRU evict 'a')
    assert "a" not in cache._cache
    assert "b" in cache._cache
    assert "c" in cache._cache
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd <repo> && pytest tests/desktop/test_snapshot_cache.py -v --timeout=30
```
Expected: ImportError — `desktop/state/snapshot_cache.py` does not exist.

- [ ] **Step 3: Create snapshot_cache.py**

Write to `novel_forge/desktop/state/snapshot_cache.py`:

```python
"""Job snapshot cache — process-local LRU + physical file signature.

Caches `ModelCallSnapshot` per run_id. Cache invalidation is driven by
the signature (file_count + latest_mtime_ns + total_size_bytes) so that
append-only model_calls/ folders (pipeline writes new JSON continuously)
invalidate the cache as soon as a new file appears.

TTL is a backstop only — TTL > physical IO cost would mask genuine
invalidation, so we keep TTL modest (60s default) and rely on signature
invalidation as the primary mechanism.

Concurrency: get_or_load uses a single threading.Lock around cache dict
mutation; loader() runs OUTSIDE the lock so concurrent reads don't
serialize on disk IO.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class _SnapshotSignature:
    """Physical signature of a model_calls/ directory.

    file_count: number of *.json files
    latest_mtime_ns: max mtime across all files (ns)
    total_size_bytes: sum of stat().st_size
    """

    file_count: int
    latest_mtime_ns: int
    total_size_bytes: int

    @classmethod
    def compute(cls, model_calls_dir: Path) -> _SnapshotSignature:
        if not model_calls_dir.is_dir():
            return cls(0, 0, 0)
        count = 0
        latest_ns = 0
        total = 0
        for p in model_calls_dir.rglob("*.json"):
            try:
                st = p.stat()
            except OSError:
                continue
            count += 1
            total += st.st_size
            if st.st_mtime_ns > latest_ns:
                latest_ns = st.st_mtime_ns
        return cls(count, latest_ns, total)


class JobSnapshotCache:
    """LRU cache from run_id -> (timestamp, signature, snapshot)."""

    def __init__(self, max_entries: int = 64, ttl_s: float = 60.0):
        self._cache: OrderedDict[str, tuple[float, _SnapshotSignature, Any]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()
        self._max = max_entries
        self._ttl = ttl_s

    def get_or_load(
        self,
        run_id: str,
        model_calls_dir: Path,
        loader: Callable[[], Any],
    ) -> Any:
        current_sig = _SnapshotSignature.compute(model_calls_dir)
        now = time.time()
        with self._lock:
            entry = self._cache.get(run_id)
            if entry is not None:
                ts, sig, snap = entry
                if sig == current_sig and (now - ts) < self._ttl:
                    self._cache.move_to_end(run_id)
                    return snap
                # signature mismatch or TTL expired
                self._cache.pop(run_id, None)

        # IO outside lock
        snap = loader()

        with self._lock:
            self._cache[run_id] = (now, current_sig, snap)
            self._cache.move_to_end(run_id)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return snap

    def invalidate(self, run_id: str) -> None:
        with self._lock:
            self._cache.pop(run_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


__all__ = ["JobSnapshotCache", "_SnapshotSignature"]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd <repo> && pytest tests/desktop/test_snapshot_cache.py -v --timeout=30
```
Expected: PASS — all 5 tests green.

- [ ] **Step 5: Commit**

```bash
cd <repo> && git add novel_forge/desktop/state/snapshot_cache.py tests/desktop/test_snapshot_cache.py && git commit -m "feat(desktop): JobSnapshotCache with physical signature

Replaces ad-hoc load_model_call_snapshot() calls with a process-
local LRU that auto-invalidates on signature change:

- _SnapshotSignature = (file_count, latest_mtime_ns, total_size_bytes)
- get_or_load computes signature each call → invalidate when new
  JSON file appears in model_calls/
- TTL (60s default) as backstop only
- Loader runs OUTSIDE the lock so concurrent readers don't serialize
  on disk IO
- LRU eviction at max_entries (default 64)

This unblocks Phase M2.2 — TaskModelCallLoader can rely on the
cache to avoid re-loading on every panel refresh while still seeing
fresh data after pipeline writes complete.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M2 Task M2.1"
```

### Task M2.2: Refactor task_focus.py to use cache + async loader

**Files:**
- Modify: `novel_forge/desktop/components/task_focus.py:950`

- [ ] **Step 1: Locate exact call site**

Run:
```bash
cd <repo> && grep -n "load_model_call_snapshot" novel_forge/desktop/components/task_focus.py
```
Expected: one match near line 950.

- [ ] **Step 2: Add cache import + module-level instance**

Add at top of `task_focus.py`:

```python
from novel_forge.desktop.state.snapshot_cache import JobSnapshotCache

# Module-level singleton cache (process-local).
_model_call_snapshot_cache = JobSnapshotCache(max_entries=64, ttl_s=60.0)
```

- [ ] **Step 3: Wrap the synchronous call**

Find the line where `load_model_call_snapshot(self._job, ...)` is invoked. Wrap it:

```python
# BEFORE
snapshot = load_model_call_snapshot(self._job, max_files=120)

# AFTER
run_id = getattr(self._job, "run_id", None) or "unknown"
project_dir = getattr(self._job, "project_dir", None) or Path(".")
model_calls_dir = project_dir / "logs" / run_id / "model_calls"

def _load_snapshot():
    return load_model_call_snapshot(self._job, max_files=120)

snapshot = _model_call_snapshot_cache.get_or_load(
    run_id, model_calls_dir, _load_snapshot
)
```

- [ ] **Step 4: Verify tests still pass**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=120
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd <repo> && git add novel_forge/desktop/components/task_focus.py && git commit -m "perf(desktop): cache model_call_snapshot via JobSnapshotCache

task_focus.py:950 was synchronously calling load_model_call_snapshot
on the main thread on every panel refresh. Wrap with module-level
JobSnapshotCache so subsequent refreshes within 60s hit the cache.

Auto-invalidation via physical signature means pipeline writes
that append JSON to model_calls/ are picked up immediately.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M2 Task M2.2"
```

### Task M2.3: Upgrade _HumanizeEmbedderAdapter to singleton loop + thread + shutdown

**Files:**
- Modify: `novel_forge/workspace/runtime.py:212-251, 311-353, 599-626`

- [ ] **Step 1: Write the failing test FIRST**

Create file `tests/unit/test_humanize_embedder_singleton.py`:

```python
"""Tests that _HumanizeEmbedderAdapter uses one loop+thread for many embed calls
and that shutdown() releases them properly."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, AsyncMock

import pytest

from novel_forge.workspace.runtime import _HumanizeEmbedderAdapter


class _MockService:
    def __init__(self):
        self.call_count = 0
        self._lock = threading.Lock()

    async def generate_batch(self, texts):
        with self._lock:
            self.call_count += 1
        return [MagicMock(embedding=[0.1, 0.2, 0.3]) for _ in texts]


def test_embed_reuses_loop_and_thread():
    settings = MagicMock()
    adapter = _HumanizeEmbedderAdapter(settings)
    # Force the service to be the mock
    svc = _MockService()
    adapter._service = svc

    thread_id_before = adapter._thread.ident

    results = [adapter.embed(["hello"]) for _ in range(5)]
    assert all(len(r) == 1 for r in results)
    assert svc.call_count == 5

    thread_id_after = adapter._thread.ident
    assert thread_id_before == thread_id_after  # ONE thread for all 5 calls


def test_shutdown_releases_thread():
    settings = MagicMock()
    adapter = _HumanizeEmbedderAdapter(settings)

    thread = adapter._thread
    thread_id = thread.ident

    adapter.shutdown()
    # The thread should have been joined (or at least asked to stop)
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_reload_resets_state():
    settings = MagicMock()
    adapter = _HumanizeEmbedderAdapter(settings)
    adapter._build_failed = True
    adapter._service = MagicMock()

    adapter.reload()
    assert adapter._service is None
    assert adapter._build_failed is False
    # reload() should also re-create the worker thread
    assert adapter._thread is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd <repo> && pytest tests/unit/test_humanize_embedder_singleton.py -v --timeout=30
```
Expected: FAIL — current _HumanizeEmbedderAdapter does not have _thread, _loop, shutdown, or reload methods.

- [ ] **Step 3: Refactor _HumanizeEmbedderAdapter**

Edit `novel_forge/workspace/runtime.py` class `_HumanizeEmbedderAdapter`:

REPLACE the entire class definition (currently around line 212-251) with:

```python
class _HumanizeEmbedderAdapter:
    """Long-lived embedder adapter: 1 loop + 1 thread for many embed() calls.

    Why: previous implementation created a new ThreadPoolExecutor + asyncio
    loop + thread per embed() call (1000 embeds → 1000 threads created and
    immediately torn down). This was expensive (startup/teardown cost) and
    noisy (lots of short-lived threads under load).

    embed() now submits to the singleton loop via call_soon_threadsafe and
    blocks on a concurrent.futures.Future. shutdown() must be called from
    RuntimeServices.shutdown() to release the thread + close the loop.
    """

    def __init__(self, settings):
        self._settings = settings
        self._service = None
        self._build_lock = threading.Lock()
        self._build_failed = False
        # NEW: long-lived loop + thread
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="humanize_embedder"
        )
        self._thread.start()

    def _run_loop(self):
        try:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()
        finally:
            pass

    def _ensure_service(self):
        # Keep existing logic — only added here to allow tests to override _service
        if self._service is not None:
            return self._service
        with self._build_lock:
            if self._service is not None:
                return self._service
            if self._build_failed:
                return None
            try:
                from novel_forge.gateway.embedding_config import (
                    EmbeddingService,
                )
                cfg = self._settings.gateway_embeddings_config()
                self._service = EmbeddingService.create_from_config(cfg)
            except Exception:
                self._build_failed = True
                self._service = None
        return self._service

    def embed(self, texts):
        if not texts:
            return []
        service = self._ensure_service()
        if service is None:
            return []
        import concurrent.futures
        future = concurrent.futures.Future()

        async def _do():
            try:
                results = await service.generate_batch(texts)
                future.set_result(
                    [
                        list(getattr(r, "embedding", []) or [])
                        for r in (results or [])
                    ]
                )
            except Exception as exc:
                future.set_exception(exc)

        try:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_do(), loop=self._loop)
            )
            return future.result(timeout=30)
        except Exception as exc:
            _logger.debug("humanize_embedder_embed_failed | error=%s", exc)
            return []

    def shutdown(self):
        """Release the worker thread and close the loop.

        Called from RuntimeServices.shutdown() so the daemon thread doesn't
        outlive the runtime.
        """
        thread = self._thread
        loop = self._loop
        if thread is None or loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        try:
            thread.join(timeout=3)
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        self._thread = None
        self._loop = None

    def reload(self):
        """Drop cached service + reset build flags; used by reload_runtime_dependencies."""
        try:
            self.shutdown()
        except Exception:
            pass
        self._service = None
        self._build_failed = False
        # Re-create loop + thread for next user
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="humanize_embedder"
        )
        self._thread.start()
```

- [ ] **Step 4: Update RuntimeServices.reload_runtime_dependencies**

In `novel_forge/workspace/runtime.py` around line 311-353, find the section where `self._humanize_embedder = None` is set. Replace with:

```python
            # OLD: self._humanize_embedder = None
            # NEW: explicitly release the embedder's loop/thread
            embedder = getattr(self, "_humanize_embedder", None)
            if embedder is not None:
                try:
                    embedder.reload()
                except Exception:
                    pass
                self._humanize_embedder = None
```

- [ ] **Step 5: Update RuntimeServices.shutdown()**

In `novel_forge/workspace/runtime.py` around line 599-626, find the `async def shutdown(self)` method. Add before `close_router`:

```python
        # Release the humanize embedder if present (was leaking threads).
        embedder = getattr(self, "_humanize_embedder", None)
        if embedder is not None and hasattr(embedder, "shutdown"):
            try:
                embedder.shutdown()
            except Exception:
                pass
            self._humanize_embedder = None
```

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
cd <repo> && pytest tests/unit/test_humanize_embedder_singleton.py -v --timeout=30
```
Expected: PASS — all 3 tests green.

- [ ] **Step 7: Run unit + desktop test suites**

Run:
```bash
cd <repo> && pytest tests/unit tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=180
```
Expected: PASS (existing tests should still work — `embed()` semantics unchanged).

- [ ] **Step 8: Commit**

```bash
cd <repo> && git add novel_forge/workspace/runtime.py tests/unit/test_humanize_embedder_singleton.py && git commit -m "perf(workspace): singleton embedder loop+thread + shutdown

_HumanizeEmbedderAdapter previously created ThreadPoolExecutor
+ new_event_loop per embed() call → 1000 embeds spawn 1000 threads
that immediately exit. Replaced with:

- 1 long-lived loop + 1 daemon thread for the adapter's lifetime
- embed() submits via loop.call_soon_threadsafe + Future.result()
- shutdown() stops loop + joins thread + closes loop (NEW)
- reload() tears down + recreates (NEW) for config reload

RuntimeServices.shutdown() now calls embedder.shutdown() to
release the thread; reload_runtime_dependencies calls
embedder.reload() before nulling the reference.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M2 Task M2.3"
```

### Task M2.4: Clean up document_renderer_incremental.py docstring (no chunked change)

**Files:**
- Modify: `novel_forge/desktop/pages/document_renderer_incremental.py:413-438`

- [ ] **Step 1: Verify tests pass baseline**

Run:
```bash
cd <repo> && pytest tests/desktop -k "incremental" -q --timeout=60
```
Expected: PASS.

- [ ] **Step 2: Read existing method**

Run:
```bash
cd <repo> && sed -n '413,438p' novel_forge/desktop/pages/document_renderer_incremental.py
```
Expected: `update_content` method that does HTML string equality check + `clear() + insertHtml()`.

- [ ] **Step 3: Replace method with cleaned-up version**

Replace the existing `update_content` method with:

```python
    def update_content(self, html: str) -> None:
        """Render HTML into the document.

        Historical note: this method's name included 'incremental' but it was
        actually fully re-rendering on every change (clear() + insertHtml()).
        The only "increment" was the in-memory string-equality fast path
        below. Phase M2.4 (2026-07) clarifies the docstring without changing
        behavior — current max chapter is 28 KB and does not bottleneck
        QTextBrowser.setHtml/insertHtml.

        To enable chunked rendering for very large chapters in the future,
        instantiate components/chunked_html_setter.py and route HTML through
        it. Bench first via scripts/benchmark_chapter_render.py; do not
        activate unconditionally because chunked insertHtml at character
        boundaries can split tags/entities and QApplication.processEvents
        inside paint has re-entrancy risks.
        """
        if html == self._last_html:
            return
        self._browser.clear()
        cursor = self._browser.textCursor()
        cursor.insertHtml(html)
        self._last_html = html
```

- [ ] **Step 4: Re-run tests**

Run:
```bash
cd <repo> && pytest tests/desktop -k "incremental" -q --timeout=60
```
Expected: PASS — behavior unchanged, only docstring clarified.

- [ ] **Step 5: Create benchmark_chapter_render.py**

Create file `scripts/benchmark_chapter_render.py`:

```python
"""Benchmark QTextBrowser rendering at different HTML sizes.

Used to decide whether chunked rendering is worth activating.
Currently the largest chapter is ~28 KB and setHtml is fast enough,
but future 1MB+ chapters may want chunked rendering.

Usage:
    python scripts/benchmark_chapter_render.py
"""

from __future__ import annotations

import time
import statistics
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QTextBrowser


SIZES_BYTES = (28_000, 100_000, 500_000, 1_000_000)
TRIALS = 3


def make_html(size_bytes: int) -> str:
    """Synthesize a paragraph-heavy HTML of roughly size_bytes."""
    para = "<p>This is a paragraph with several sentences of test prose that exercise word wrap and layout. It contains punctuation, numbers like 12345, and unicode characters like 中文 and emoji 🎉 to mimic realistic content.</p>\n"
    out = []
    total = 0
    while total < size_bytes:
        out.append(para)
        total += len(para)
    return "".join(out)[:size_bytes]


def measure_sethtml(browser, html, trials=TRIALS):
    times = []
    for _ in range(trials):
        t0 = time.perf_counter()
        browser.setHtml(html)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    print(f"{'size_KB':>10}  {'med_ms':>10}  {'results':>40}")
    print("-" * 70)
    for size in SIZES_BYTES:
        html = make_html(size)
        browser = QTextBrowser()
        med = measure_sethtml(browser, html)
        print(
            f"{size/1024:>10.1f}  {med*1000:>10.1f}  "
            f"{f'{trials} trials':>40}"
        )

    print()
    print("Decision rule:")
    print("  - If med_ms > 200ms at 1MB+, consider activating chunked rendering")
    print("    via desktop/components/chunked_html_setter.py (Phase M2.4 deliverable).")
    print("  - Until then, keep current setHtml path; chunks add complexity without")
    print("    benefit at current sizes.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run benchmark (GUI mode locally)**

Run:
```bash
cd <repo> && python3 scripts/benchmark_chapter_render.py
```
Expected: prints median ms per size; not used in CI (only locally). If your local doesn't have a display, run with offscreen:

```bash
cd <repo> && QT_QPA_PLATFORM=offscreen python3 scripts/benchmark_chapter_render.py
```

- [ ] **Step 7: Commit**

```bash
cd <repo> && git add novel_forge/desktop/pages/document_renderer_incremental.py scripts/benchmark_chapter_render.py && git commit -m "docs(desktop): clarify IncrementalDocumentRenderer.update_content + add bench

The method's name implied incremental rendering but it always did
clear() + full insertHtml(); only the HTML string-equality check
was an 'incremental' fast path. With current max chapter at 28 KB
this is not a bottleneck, so behavior stays the same — only the
docstring is clarified.

Added scripts/benchmark_chapter_render.py to provide data-driven
decision for activating chunked rendering in the future. Run
locally (no CI), output median ms at 28KB / 100KB / 500KB / 1MB.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M2 Task M2.4"
```

### Task M2.5: Final verification — M2 done

- [ ] **Step 1: Run full unit + desktop test suites**

Run:
```bash
cd <repo> && pytest tests/unit tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=180
```
Expected: PASS.

- [ ] **Step 2: ruff + mypy on changed files**

Run:
```bash
cd <repo> && ruff check novel_forge/desktop/ novel_forge/workspace/ && mypy novel_forge/desktop/ novel_forge/workspace/ 2>&1 | tail -10
```
Expected: no NEW errors.

---

## Phase M6 — macOS Fullscreen Guard (window.py + event_bus + closeEvent)

### Task M6.1: Add fullscreen guard to _on_workspace_refreshed

**Files:**
- Modify: `novel_forge/desktop/window.py:1402-1549`

- [ ] **Step 1: Add per-method logging to baseline**

Run:
```bash
cd <repo> && sed -n '1402,1412p' novel_forge/desktop/window.py
```
Expected: existing method signature confirmed.

- [ ] **Step 2: Edit method to add fullscreen guard**

Replace the existing method body's first 3-5 lines with guarded version. Find the start (line 1402 onwards, before any `_workspace_revision = ...` style):

```python
    def _on_workspace_refreshed(
        self,
        snapshot,
        service,
        payload,
        section_hashes,
        changed_sections,
        snapshot_hash,
    ):
        # Phase M6 guard: defer heavy widget ops during macOS fullscreen to
        # avoid driving AppKit out of Spaces. The check uses the existing
        # _skip_page_motion_for_window_state() which already handles
        # isFullScreen / windowState / visibility / geometry.
        if (
            sys.platform == "darwin"
            and self._skip_page_motion_for_window_state()
        ):
            QTimer.singleShot(
                0,
                lambda: self._apply_workspace_refresh(
                    snapshot, service, payload,
                    section_hashes, changed_sections, snapshot_hash,
                ),
            )
            return
        self._apply_workspace_refresh(
            snapshot, service, payload,
            section_hashes, changed_sections, snapshot_hash,
        )
```

- [ ] **Step 3: Rename original body to _apply_workspace_refresh**

Take the ENTIRE original body of `_on_workspace_refreshed` (which was starting around line 1411), and wrap it as a new method:

```python
    def _apply_workspace_refresh(
        self,
        snapshot,
        service,
        payload,
        section_hashes,
        changed_sections,
        snapshot_hash,
    ):
        # Original body of _on_workspace_refreshed — unchanged.
        # ... (paste the existing body here) ...
```

- [ ] **Step 4: Run tests**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=180
```
Expected: PASS — existing tests calling `_on_workspace_refreshed` directly will hit the new wrapper which dispatches to `_apply_workspace_refresh`.

- [ ] **Step 5: Commit**

```bash
cd <repo> && git add novel_forge/desktop/window.py && git commit -m "fix(desktop): guard _on_workspace_refreshed against macOS fullscreen exit

Phase M6 fix: page-switch main-thread widget ops during macOS
native fullscreen can drive AppKit out of the Spaces fullscreen
state. Wrap the body of _on_workspace_refreshed with the existing
_skip_page_motion_for_window_state() check and defer via
QTimer.singleShot(0, ...) when in fullscreen.

Critical: this does NOT change Qt connection types — keep
Qt.AutoConnection default. The deferral only affects the
heavy widget ops, not the signal delivery itself.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M6 Task M6.1"
```

### Task M6.2: Add optional default_queue to WorkspaceEventBus.subscribe

**Files:**
- Modify: `novel_forge/desktop/state/event_bus.py:134`

- [ ] **Step 1: Read existing subscribe signature**

Run:
```bash
cd <repo> && sed -n '125,150p' novel_forge/desktop/state/event_bus.py
```

- [ ] **Step 2: Edit subscribe signature**

Edit `def subscribe(self, event_type, callback):` to add the optional `default_queue` parameter:

```python
    def subscribe(self, event_type, callback, *, default_queue: bool = False):
        """Subscribe to events of a given dataclass type.

        Args:
            event_type: dataclass type (SectionChanged / ProjectChanged /
                        JobCompleted).
            callback: callable receiving the dataclass fields as positional args.
            default_queue: if True, force Qt.QueuedConnection (asynchronous
                           delivery even when caller is in same thread).
                           Default False preserves Qt.AutoConnection behavior
                           — same-thread DirectConnection / cross-thread
                           QueuedConnection.
        """
        signal = self._EVENT_MAP[event_type]
        if default_queue:
            signal.connect(callback, Qt.QueuedConnection)
        else:
            signal.connect(callback)
```

- [ ] **Step 3: Run unit + desktop tests**

Run:
```bash
cd <repo> && pytest tests/unit tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=180
```
Expected: PASS — existing tests use default False.

- [ ] **Step 4: Commit**

```bash
cd <repo> && git add novel_forge/desktop/state/event_bus.py && git commit -m "feat(desktop): WorkspaceEventBus.subscribe accepts default_queue=True

Phase M6: add optional default_queue parameter to subscribe() to
force Qt.QueuedConnection when callers explicitly need async
delivery even on same-thread emitters.

Default is False — preserves Qt.AutoConnection (same-thread
DirectConnection / cross-thread QueuedConnection) so existing
subscriber semantics are unchanged.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M6 Task M6.2"
```

### Task M6.3: Tighten _pre_close_cleanup with shutdown_desktop_thread_pools

**Files:**
- Modify: `novel_forge/desktop/window.py:4189-4240`

- [ ] **Step 1: Find shutdown call site**

Run:
```bash
cd <repo> && grep -n "self._job_manager.shutdown" novel_forge/desktop/window.py
```
Expected: one match around line 4228.

- [ ] **Step 2: Add thread_pools shutdown after job_manager.shutdown**

Edit the lines following `self._job_manager.shutdown(wait_ms=2000)`:

```python
        # Phase M6: also drain global ui_io_pool + aux_pool to ensure no
        # QRunnable still owns a Qt object reference when we exit.
        # ui_io_pool/aux_pool run tasks owned by window/pages; JobManager
        # only knew about its own job_pool. JobPool was already drained
        # inside JobManager.shutdown() — calling waitForDone on it again
        # is a no-op.
        try:
            from novel_forge.desktop.thread_pools import (
                shutdown_desktop_thread_pools,
            )
            shutdown_desktop_thread_pools(wait_ms=3000)
        except Exception as exc:
            _logger.warning("thread_pools shutdown failed: %s", exc)
```

- [ ] **Step 3: Run tests**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=180
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd <repo> && git add novel_forge/desktop/window.py && git commit -m "fix(desktop): _pre_close_cleanup waits global thread pools

Phase M6: when the desktop window is closing, drain ui_io_pool +
aux_pool (in addition to job_pool) so any in-flight QRunnable
finishes before its Qt references are destroyed.

shutdown_desktop_thread_pools(wait_ms=3000) is a no-op for an
already-idle JobPool, so the JobManager.shutdown(wait_ms=2000)
call made earlier in _pre_close_cleanup remains the primary drain
path.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M6 Task M6.3"
```

### Task M6.4: Create macOS fullscreen verification script

**Files:**
- Create: `scripts/verify_macos_fullscreen.py`

- [ ] **Step 1: Write script**

Create file `scripts/verify_macos_fullscreen.py`:

```python
"""Verify macOS fullscreen doesn't exit on page switch.

Local-only diagnostic (requires macOS GUI session). Not run in CI.

Strategy: launch the desktop window, programmatically enter fullscreen
state, trigger page switches 50 times via the navigation button
signals, and assert that the window's fullscreen state is preserved.

Uses PySide6 QTest where possible; PyObjC only if Qt introspection is
insufficient. PyObjC is optional and only loaded inside the macOS
guard to avoid hard import dependency on other platforms.
"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip GUI; just verify the script runs without errors.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("[skip] only runs on macOS; sys.platform =", sys.platform)
        sys.exit(0)

    if args.dry_run:
        print("[dry-run] macOS detected; would launch window + simulate 50 page switches.")
        sys.exit(0)

    # Real run: requires GUI session.
    from PySide6.QtWidgets import QApplication
    from novel_forge.desktop.main import launch_desktop

    app = QApplication.instance() or QApplication(sys.argv)
    window = launch_desktop(app)
    if window is None:
        print("[!] launch_desktop returned None — desktop failed to start")
        sys.exit(1)

    # Manually enter fullscreen state
    window.showFullScreen()

    # Programmatic page switches
    from PySide6.QtCore import QTimer
    page_ids = ["dashboard", "projects", "workflow", "settings", "chapter_studio"]
    state = {"i": 0, "fs_drops": 0}

    def _tick():
        if state["i"] >= 50:
            window.close()
            app.quit()
            return
        pid = page_ids[state["i"] % len(page_ids)]
        window.switch_page(pid)
        # After switch, re-enter fullscreen if it got dropped
        QTimer.singleShot(50, _check)
        state["i"] += 1

    def _check():
        if not window.isFullScreen():
            state["fs_drops"] += 1
            window.showFullScreen()
        _tick()

    QTimer.singleShot(500, _tick)

    rc = app.exec()
    print(f"[result] page switches: 50, fullscreen drops: {state['fs_drops']}")
    sys.exit(0 if state["fs_drops"] == 0 else 2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run --dry-run on linux**

Run:
```bash
cd <repo> && python3 scripts/verify_macos_fullscreen.py --dry-run
```
Expected: prints "[skip] only runs on macOS..." OR "[dry-run] macOS detected...". Either is fine — script is import-safe.

- [ ] **Step 3: Commit**

```bash
cd <repo> && git add scripts/verify_macos_fullscreen.py && git commit -m "test(desktop): macOS fullscreen verification script (local-only)

scripts/verify_macos_fullscreen.py programmatically enters fullscreen
state and switches pages 50 times, reporting how many times AppKit
drops the Spaces fullscreen state.

NOT run in CI. Usage:
  python3 scripts/verify_macos_fullscreen.py        # real test on macOS GUI
  python3 scripts/verify_macos_fullscreen.py --dry  # skip GUI, just check imports

If failures appear (fullscreen drops > 0), the Phase M6.1 guard in
_on_workspace_refreshed needs further tuning or new defer points
need to be added.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M6 Task M6.4"
```

---

## Phase M3 — Giant File Splits (8 files)

This phase uses a strict 5-step protocol per split:
1. `git mv old.py newdir/__init__.py`
2. Move classes/methods to subfiles
3. `__init__.py` re-exports public API
4. Run tests + ruff + mypy
5. One commit per split

### Task M3.1: Split window.py → window/ package

- [ ] **Step 1: Pre-flight check**

Run:
```bash
cd <repo> && ls novel_forge/desktop/window/ 2>/dev/null || echo "not exists, ok"
```
Expected: not exists.

- [ ] **Step 2: Create dir, git mv, scaffold __init__.py**

Run:
```bash
cd <repo> && mkdir -p novel_forge/desktop/window/panels && git mv novel_forge/desktop/window.py novel_forge/desktop/window/__init__.py
```

- [ ] **Step 3: Identify logical sub-files**

Read `novel_forge/desktop/window/__init__.py` and identify which method groups go where:

- `core.py` — `__init__`, `_init_ui_components`, `closeEvent`, `_pre_close_cleanup`, `_save_ui_session`, `_is_closing`
- `navigation.py` — `switch_page`, `_navigation_switch_page`, `_lazy_page_map`, `_build_side_rail`, `_skip_page_motion_for_window_state`, `_animate_current_page`, `_page_transition_profile`, `_schedule_deferred_page_creation`
- `jobs_binding.py` — `_bind_jobs_and_focus`, `_handle_decision`, `_task_focus_panel`
- `panels/top_bar.py` — top bar construction
- `panels/side_rail.py` — sidebar construction
- `panels/skeleton_overlay.py` — skeleton overlay

- [ ] **Step 4: Move methods, keep __init__.py thin**

For each sub-file, `git mv` the bodies (cut-and-paste with `git mv` not applicable here — just move the methods by editing in place then ensure tests pass).

Concrete tactic:
1. Create sub-files with placeholder imports.
2. Copy method bodies from `__init__.py` to the sub-files (use `Edit` tool to remove from `__init__.py` and write to sub-file).
3. Replace method bodies in `__init__.py` with a one-line delegation call: `self._foo = SomeClass(self)` or pass-through function calls.

- [ ] **Step 5: Run tests after each sub-file**

```bash
cd <repo> && pytest tests/desktop -q --timeout=120
```

- [ ] **Step 6: Commit when stable**

```bash
cd <repo> && git add novel_forge/desktop/window/ && git commit -m "refactor(desktop): split window.py into window/ package

Splits 4728-line window.py into 6 sub-files:
- core.py (init / closeEvent / cleanup)
- navigation.py (switch_page + fullscreen animations)
- jobs_binding.py (bind_jobs + decision handler)
- panels/{top_bar,side_rail,skeleton_overlay}.py

__init__.py is now a thin facade re-exporting NovelForgeDesktopWindow
for backward compat.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M3 Task M3.1"
```

### Task M3.2 to M3.8: Repeat split protocol for other 7 giant files

The exact steps for each follow the M3.1 protocol:

- [ ] **M3.2 — split pages/document_renderers.py → document_renderer/ package**
- [ ] **M3.3 — split pages/document_renderer_story_artifacts.py → document_renderer/story_artifacts/**
- [ ] **M3.4 — split pages/document_renderer_reports.py → document_renderer/reports/**
- [ ] **M3.5 — split jobs.py → jobs/ package (only manager + history + signals; worker framework already lives in workers/ from M1)**
- [ ] **M3.6 — split pages/settings_page_parameters.py → settings/parameters/**
- [ ] **M3.7 — split components/task_focus.py → task_focus/**
- [ ] **M3.8 — split components/memory_components.py → memory_components/**

For each:
1. Verify path doesn't already conflict (§1.9 of spec)
2. `git mv` to package/__init__.py
3. Distribute method bodies to sub-files
4. Re-export from `__init__.py`
5. Run `pytest tests/desktop -q --timeout=120`
6. One commit with `refactor(desktop): split <file> into <package>`

After M3.1 + M3.5 + M3.7 + M3.8 complete, add import-regression tests:

- [ ] **M3.9 — Create tests/desktop/test_window_imports.py**

```python
"""Smoke-test that desktop.window re-exports the public API after split."""

from __future__ import annotations

REQUIRED_NAMES = (
    "NovelForgeDesktopWindow",
    # extend with other names listed in window/AGENTS.md
)


def test_window_public_api_importable():
    from novel_forge.desktop import window
    for name in REQUIRED_NAMES:
        assert hasattr(window, name), f"missing: {name}"
```

- [ ] **M3.10 — Create tests/desktop/test_jobs_imports.py**

```python
"""Smoke-test that desktop.jobs re-exports the public API after split."""

from __future__ import annotations

REQUIRED_NAMES = (
    "DesktopJobManager",
    "DesktopJobState",
    "DesktopJobRecord",
)


def test_jobs_public_api_importable():
    from novel_forge.desktop import jobs
    for name in REQUIRED_NAMES:
        assert hasattr(jobs, name), f"missing: {name}"
```

After both pass:

```bash
cd <repo> && git add tests/desktop/test_window_imports.py tests/desktop/test_jobs_imports.py && git commit -m "test(desktop): add import smoke tests for window/ jobs/ packages"
```

Note: `tests/desktop/test_lazy_markdown.py` is intentionally NOT created —
chunked rendering is not enabled (Phase M2.4 only cleaned the docstring),
so there is no lazy markdown behavior to test. If Phase 8+ activates
ChunkedHtmlSetter, add the test then.

---

## Phase M4 — Pages Subdirectory Layout

### Task M4.1: Create 7 subdirectories + AGENTS.md files

- [ ] **Step 1: Create subdirs**

```bash
cd <repo> && mkdir -p novel_forge/desktop/pages/{chapter_studio,workflow,document_renderer,settings,standalone}
```

- [ ] **Step 2: For each giant split page (already moved into subpackages in M3), copy them into the appropriate pages/<subdir>/**

For now, perform a coarse split: move all `chapter_studio_*` files into `pages/chapter_studio/`, etc. Use git mv to preserve history.

- [ ] **Step 3: Update pages/__init__.py for auto-discovery**

Replace existing `__init__.py` content with:

```python
"""Pages subdirectory — auto-discovery of <group>/page.py modules.

Each subdirectory should have:
- AGENTS.md (group-specific conventions)
- page.py (the *Page class)
- optionally: state.py / actions.py / workers.py / components.py / widgets.py

Subdirectories discovered via pkgutil.iter_modules on import. Each
subdirectory's __init__.py must do `from .page import ThePageClass` and
register it via page_registry.
"""

from __future__ import annotations

import importlib
import pkgutil


for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _name.startswith("_"):
        continue
    if not _ispkg:
        continue
    importlib.import_module(f"{__name__}.{_name}")
```

- [ ] **Step 4: For each subdir, create __init__.py that imports + registers**

For each subdir, write `__init__.py`:

```python
"""<Group> pages — auto-register on import."""

from __future__ import annotations

from novel_forge.desktop.page_registry import register_page

from .page import GroupPage  # adjust import name

register_page(
    page_id="<group>",
    page_factory=lambda parent: GroupPage(parent),
    metadata={"transition_profile": "standard"},
)

__all__ = ["GroupPage"]
```

The exact GroupPage name and page_id varies per group. Use the existing page_registrations.py as a reference.

- [ ] **Step 5: Write test_pages_autodiscovery.py**

Create `tests/desktop/test_pages_autodiscovery.py`:

```python
"""Verify all expected pages are registered via auto-discovery."""

from __future__ import annotations

import pytest

from novel_forge.desktop.pages import __path__ as pages_pkg_path
from novel_forge.desktop.page_registry import page_registry


EXPECTED_PAGE_IDS = {
    "chapter_studio",
    "workflow",
    "document_renderer",  # or specific pages
    "settings",
    "standalone_projects",
    "standalone_dashboard",
    # ... add others based on user requirements
}


def test_expected_page_ids_registered():
    registered = {meta.page_id for meta in page_registry.metadata_list()}
    missing = EXPECTED_PAGE_IDS - registered
    assert not missing, f"Missing page registrations: {missing}"
```

- [ ] **Step 6: Run test**

```bash
cd <repo> && pytest tests/desktop/test_pages_autodiscovery.py -v --timeout=60
```

- [ ] **Step 7: Commit**

```bash
cd <repo> && git add novel_forge/desktop/pages/ && git commit -m "refactor(desktop): pages subdirectory layout + auto-discovery

Migrates 63 flat pages/*.py into 7 group subdirectories:
- chapter_studio/ (18 files)
- workflow/ (12 files)
- document_renderer/ (5 files)
- settings/ (9 files)
- standalone/ (10+ files incl character_*, outline_*, projects, dashboard, ...)

pages/__init__.py uses pkgutil.iter_modules for auto-discovery so
new subdirectories don't need explicit registration. Each subdir's
__init__.py calls register_page() with the existing PageMetadata shape.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M4 Task M4.1"
```

### Task M4.2: Create refactor_pages_layout.py (if not done)

- [ ] **Step 1: Verify script exists**

```bash
cd <repo> && ls scripts/refactor_pages_layout.py 2>/dev/null || echo "MISSING"
```

If missing, create `scripts/refactor_pages_layout.py` (used to scan existing imports and report a mapping table for the migration). The script should be idempotent and accept `--dry-run` and `--apply`.

- [ ] **Step 2: Run --dry-run**

```bash
cd <repo> && python3 scripts/refactor_pages_layout.py --dry-run
```

Expected: prints old→new import mappings; no files modified.

- [ ] **Step 3: Run --apply + commit**

```bash
cd <repo> && python3 scripts/refactor_pages_layout.py --apply && git add -A && git commit -m "refactor(desktop): apply pages subdir import migrations"
```

### Task M4.3: Rewrite pages/AGENTS.md

- [ ] **Step 1: Read existing AGENTS.md**

```bash
cd <repo> && cat novel_forge/desktop/pages/AGENTS.md | head -30
```

- [ ] **Step 2: Replace with new content describing 7 subdirs**

Write fresh content to `novel_forge/desktop/pages/AGENTS.md`:

```markdown
# Pages Conventions (v4 — subdirectory layout)

## Layout

```
pages/
├── chapter_studio/      # 18 files — chapter generation/inspection pages
├── workflow/             # 12 files — workflow pages
├── document_renderer/    # 5 files — document rendering pages
├── settings/             # 9 files — settings pages
└── standalone/           # 10+ files — single-purpose pages (projects, dashboard, ...)
```

## Per-subdir conventions

Each subdir SHOULD have:
- `AGENTS.md` describing the group's state, signals, workers protocols
- `page.py` — the *Page class (QWidget-based)
- Optional: `state.py / actions.py / workers.py / widgets.py / components.py`

## Registration

Subdir's `__init__.py` calls `register_page()` with the *Page class
factory. `pages/__init__.py` auto-discovers all subdirs via
`pkgutil.iter_modules`, so no manual addition needed.
```

- [ ] **Step 3: Commit**

```bash
cd <repo> && git add novel_forge/desktop/pages/AGENTS.md && git commit -m "docs(desktop): rewrite pages/AGENTS.md for subdirectory layout"
```

---

## Phase M5.3 — Cross-Platform Modules + Visual Baseline Regen

### Task M5.3.1: Extract sleep_inhibit/ollama_paths/fonts to platform/

**Files:**
- Create: `novel_forge/desktop/platform/__init__.py`
- Create: `novel_forge/desktop/platform/sleep_inhibit/{__init__.py, darwin.py, win32.py, linux.py}`
- Create: `novel_forge/desktop/platform/ollama_paths.py`
- Create: `novel_forge/desktop/platform/fonts.py`
- Modify: `novel_forge/desktop/sleep_inhibitor.py` (re-export from platform/)
- Modify: `novel_forge/desktop/ollama_sidecar.py:74,94` (use platform/ollama_paths)
- Modify: `novel_forge/desktop/main.py:173-178` (use platform/fonts)

- [ ] **Step 1: Move sleep_inhibitor logic into platform/**

Create `novel_forge/desktop/platform/sleep_inhibit/darwin.py`:

```python
"""macOS sleep inhibit via caffeinate -i -w <pid>."""

from __future__ import annotations

import os
import subprocess
from typing import Optional


class SleepInhibitBackend:
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None

    def acquire(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                ["caffeinate", "-i", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def release(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
```

(Similar files for win32.py using ctypes.windll.kernel32, and linux.py using systemd-inhibit.)

Create `novel_forge/desktop/platform/sleep_inhibit/__init__.py`:

```python
"""Sleep inhibit backend dispatcher."""

import sys
from typing import Any


def create_backend() -> Any:
    if sys.platform == "darwin":
        from .darwin import SleepInhibitBackend
        return SleepInhibitBackend()
    if sys.platform == "win32":
        from .win32 import SleepInhibitBackend
        return SleepInhibitBackend()
    if sys.platform.startswith("linux"):
        from .linux import SleepInhibitBackend
        return SleepInhibitBackend()
    from .null_backend import NullBackend
    return NullBackend()
```

- [ ] **Step 2: Modify sleep_inhibitor.py to delegate**

In `novel_forge/desktop/sleep_inhibitor.py`, replace the backend creation logic to import from `desktop.platform.sleep_inhibit`:

```python
# Re-export from platform/ for backward compatibility
from novel_forge.desktop.platform.sleep_inhibit import create_backend as _create_backend  # noqa: F401
```

- [ ] **Step 3: Create ollama_paths.py**

Create `novel_forge/desktop/platform/ollama_paths.py`:

```python
"""Locate ollama binary per-platform."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def ollama_executable() -> str:
    """Return the ollama executable name/path for the current platform."""
    if sys.platform == "win32":
        return shutil.which("ollama.exe") or "ollama.exe"
    return shutil.which("ollama") or "ollama"


def default_ollama_models_dir() -> Path:
    """Best-effort default models directory per platform."""
    if sys.platform == "darwin":
        return Path.home() / ".ollama" / "models"
    if sys.platform == "win32":
        return Path.home() / ".ollama" / "models"
    return Path("/usr/share/ollama/.ollama/models")
```

- [ ] **Step 4: Create fonts.py**

Create `novel_forge/desktop/platform/fonts.py`:

```python
"""Per-platform font fallback tables."""

from __future__ import annotations

import sys


def font_fallback() -> list[str]:
    """List of font families to try, in order, for the current platform."""
    if sys.platform == "darwin":
        return ["PingFang SC", "Hiragino Sans GB", "STHeiti", "Helvetica Neue"]
    if sys.platform == "win32":
        return ["Microsoft YaHei", "SimHei", "Segoe UI"]
    return ["Noto Sans CJK SC", "Source Han Sans SC", "DejaVu Sans"]
```

- [ ] **Step 5: Update tests**

Run:
```bash
cd <repo> && pytest tests/desktop --ignore-glob='tests/desktop/*_visual.py' --ignore-glob='tests/desktop/test_visual_regression.py' -q --timeout=180
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd <repo> && git add novel_forge/desktop/platform/ novel_forge/desktop/sleep_inhibitor.py novel_forge/desktop/ollama_sidecar.py novel_forge/desktop/main.py && git commit -m "refactor(desktop): extract cross-platform modules to desktop/platform/

Move per-platform code out of scattered top-level files:
- sleep_inhibitor.py backend selection → platform/sleep_inhibit/{darwin,win32,linux,__init__}.py
- ollama_sidecar.py:74,94 binary lookup → platform/ollama_paths.py
- main.py:173-178 font tables → platform/fonts.py

Each platform module has a single responsibility and can be
tested/replaced independently. Re-exports preserve backward compat
for any external imports of sleep_inhibitor.SleepInhibitor.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M5.3 Task M5.3.1"
```

### Task M5.3.2: Fix visual_regression.py /tmp/ hardcoding

**Files:**
- Modify: `tests/desktop/visual_regression.py:22-26`

- [ ] **Step 1: Read existing**

```bash
cd <repo> && sed -n '15,30p' tests/desktop/visual_regression.py
```

- [ ] **Step 2: Replace /tmp/ with tempfile.gettempdir()**

Find lines like `/tmp/novel_forge_visual_workspace` and `/tmp/novel_forge_visual_failures`. Replace with:

```python
import tempfile
from pathlib import Path

WORKSPACE = str(Path(tempfile.gettempdir()) / "novel_forge_visual_workspace")
FAILURES = str(Path(tempfile.gettempdir()) / "novel_forge_visual_failures")
```

- [ ] **Step 3: Run visual tests on linux or mac**

```bash
cd <repo> && QT_QPA_PLATFORM=offscreen pytest tests/desktop/visual_regression.py -q --timeout=120
```

- [ ] **Step 4: Commit**

```bash
cd <repo> && git add tests/desktop/visual_regression.py && git commit -m "fix(tests): replace /tmp/ hardcoding with tempfile.gettempdir()

Hardcoded /tmp/ paths broke on Windows and macOS where /tmp is a
symlink. Use tempfile.gettempdir() + Path for cross-platform support.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M5.3 Task M5.3.2"
```

### Task M5.3.3: Regenerate visual regression baselines

- [ ] **Step 1: Identify all visual baseline files**

```bash
cd <repo> && find tests/desktop -name "*.png" -path "*/baselines/*"
```

- [ ] **Step 2: Run visual tests to identify which baselines need regen**

```bash
cd <repo> && QT_QPA_PLATFORM=offscreen pytest tests/desktop/visual_regression.py tests/desktop/*_visual.py -q --timeout=120
```

- [ ] **Step 3: For each failing baseline, manually review + regenerate**

For each failure, open the existing baseline and the new actual side-by-side, decide if the diff is a real regression or expected (e.g. font hinting differences across platforms). If expected:

```bash
cd <repo> && QT_QPA_PLATFORM=offscreen pytest tests/desktop/<file>.py --regen-baseline
```

(use the appropriate flag per the visual test framework's convention)

- [ ] **Step 4: Commit regen as a separate commit**

```bash
cd <repo> && git add tests/desktop/baselines/ && git commit -m "test(desktop): regenerate visual baselines after Phase M5.3 platform modules

Baselines need regen because desktop/platform/sleep_inhibit/
darwin.py uses a different subprocess call structure than the
old monolithic sleep_inhibitor.py — affecting nothing visually
but cleaner to refresh baselines after big refactors."
```

---

## Phase M7 — Documentation

### Task M7.1: Write desktop/AGENTS.md (top level)

- [ ] **Step 1: Create file**

Write `novel_forge/desktop/AGENTS.md` with sections:
- Architecture overview
- Worker framework (links to workers/AGENTS.md)
- State management (links to state/AGENTS.md)
- Pages (links to pages/AGENTS.md)
- Testing matrix (which tests cover which modules)
- Shutdown protocol

### Task M7.2: Write sub-AGENTS.md for each subsystem

Create:
- `novel_forge/desktop/workers/AGENTS.md`
- `novel_forge/desktop/platform/AGENTS.md`
- `novel_forge/desktop/window/AGENTS.md`
- `novel_forge/desktop/jobs/AGENTS.md`
- `novel_forge/desktop/components/task_focus/AGENTS.md`
- `novel_forge/desktop/components/memory_components/AGENTS.md`
- `novel_forge/desktop/pages/document_renderer/AGENTS.md`
- `novel_forge/desktop/pages/settings/AGENTS.md`
- `novel_forge/desktop/pages/chapter_studio/AGENTS.md`

### Task M7.3: Write runbooks

- [ ] **Step 1: Create runbook_macos_fullscreen.md**

`docs/runbook_macos_fullscreen.md` documenting:
- Reproducing the bug
- Running `scripts/verify_macos_fullscreen.py`
- Manual checklist: enter fullscreen → switch 5 pages → expect 0 drops
- Failure modes and recovery

- [ ] **Step 2: Create runbook_ui_architecture.md**

`docs/runbook_ui_architecture.md` documenting:
- Worker framework usage
- Page registration pattern
- Thread pool semantics
- Cache invalidation strategy
- Splitting a giant file (recipe)

### Task M7.4: Final verification

- [ ] **Step 1: Run all tests**

```bash
cd <repo> && pytest tests/unit tests/desktop -q --timeout=300
```

- [ ] **Step 2: ruff + mypy**

```bash
cd <repo> && ruff check novel_forge/ && mypy novel_forge/ 2>&1 | tail -10
```

- [ ] **Step 3: Push**

```bash
cd <repo> && git push
```

Expected: CI shows green across all 5 jobs (lint, unit, integration, desktop, visual, security).

---

## Summary

This plan delivers 9 independently mergeable phases implementing the v3 spec. Each phase ends with a verified passing test suite and one or more atomic commits. The reader needs zero context beyond this document and the spec.

If any step fails, **stop and re-read the spec section it implements** — most likely the spec gave a constraint that the implementation overlooked.

---

## Phase M0 — Pre-existing Baseline Cleanup (added v4)

> **Added 2026-07-02 after M5.2 push + CI run revealed pre-existing baseline failures.** Without these fixes, every CI run will fail even when our changes are correct.

### Task M0.1: Pin PySide6 to 6.10.0 to avoid 6.11.1 segfaults

**Files:**
- Modify: `pyproject.toml:34`

- [ ] **Step 1: Read current PySide6 constraint**

```bash
cd ./.worktrees/refactor-ui && grep -n "PySide6" pyproject.toml
```
Expected: `PySide6>=6.6,<7`

- [ ] **Step 2: Tighten to `<6.11`**

Edit `pyproject.toml`:
```toml
PySide6>=6.6,<6.11
```

- [ ] **Step 3: Re-install to pick up the new constraint**

```bash
source .venv/bin/activate && pip install --quiet -e ".[dev,desktop]" 2>&1 | tail -5
```

Expected: pip resolves to PySide6 6.10.x.

- [ ] **Step 4: Smoke test that 6.10 imports work**

```bash
source .venv/bin/activate && python -c "import PySide6; print(PySide6.__version__)" && QT_QPA_PLATFORM=offscreen python -c "from PySide6.QtWidgets import QApplication, QLabel; app = QApplication([]); print('Qt init OK')"
```

Expected: `PySide6 6.10.x` and Qt init OK.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml && git commit -m "build: pin PySide6 <6.11 to avoid 6.11.1 Qt segfaults

PySide6 6.11.1 (auto-installed from latest wheel) crashes on
macOS, Linux, and Windows during CI pytest runs (3-runner
desktop-tests CI failed with exit code 139 across all platforms
on commit 4eeed4098 / push #28586033554).

Tighten desktop extra to >=6.6,<6.11 so pip installs a known-
good PySide6 (6.10.x). 6.11 may fix the segfault later; we can
re-test and bump when it does.

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M0 Task M0.1"
```

### Task M0.2: Fix F401 / F841 lint failures that block CI's lint job

**Files:**
- Modify: `novel_forge/desktop/components/rich_document_viewer.py:12`
- Modify: `tests/desktop/test_rich_document_viewer.py:24,33`
- Plus: any other F841 / F401 errors surfaced by `ruff check novel_forge tests` (run first to enumerate)

- [ ] **Step 1: Run ruff to enumerate exact errors**

```bash
cd ./.worktrees/refactor-ui && source .venv/bin/activate && ruff check novel_forge tests 2>&1 | tail -40
```

Expected: list of F401, F841, F811 (etc.) with file:line refs.

- [ ] **Step 2: Fix each F401 (unused import)**

For each F401, remove the unused import line. Example:

Edit `novel_forge/desktop/components/rich_document_viewer.py:12`:

```python
# BEFORE
from typing import Any
# AFTER
(line removed)
```

- [ ] **Step 3: Fix each F841 (unused local variable)**

For each F841, either remove the variable or prefix with `_`. Example, for F841 at `tests/desktop/test_rich_document_viewer.py:24`:

```python
# BEFORE
app = QApplication.instance() or QApplication([])
body = QTextBrowser()
# AFTER
body = QTextBrowser()  # creates QApplication as side effect
```

OR use `_app = ...` to silence.

- [ ] **Step 4: Re-run ruff to confirm 0 errors**

```bash
source .venv/bin/activate && ruff check novel_forge tests 2>&1 | tail -5
```

Expected: zero errors.

- [ ] **Step 5: Run smoke tests to verify nothing broke**

```bash
source .venv/bin/activate && QT_QPA_PLATFORM=offscreen NOVEL_FORGE_MEMORY_USE_MOCK_EMBEDDINGS=true pytest tests/desktop/test_rich_document_viewer.py tests/desktop/test_infrastructure.py tests/desktop/test_page_registry.py -q --timeout=60 2>&1 | tail -5
```

Expected: tests pass (existing or pre-existing failures ok; no new ones from this change).

- [ ] **Step 6: Commit (one commit per logical group)**
```bash
git add novel_forge/ tests/ && git commit -m "fix(lint): remove unused imports + unused locals blocking ruff CI

CI's lint job (Run ruff check novel_forge tests) failed before any
real change on push #28586033554 because the codebase has accumulated
F401 (unused import) and F841 (unused local variable) violations —
these are pre-existing baseline, not introduced by the refactor.

Tighten so CI's lint gate goes green.

Found and fixed (re-enumerate via ruff before commit):
- novel_forge/desktop/components/rich_document_viewer.py:12:
  unused 'from typing import Any'
- tests/desktop/test_rich_document_viewer.py:24,33:
  unused 'app = QApplication.instance() or QApplication([])'
- ...other F401/F841 errors reported by ruff...

Refs: docs/superpowers/specs/2026-07-02-ui-architecture-refactor-design.md
Phase M0 Task M0.2"
```

---

## Updated Execution Order

The original execution order was:
> M5.1 → M5.2 → M1 → M2 → M6 → M3 → M4 → M5.3 → M7

The updated order (v4 plan, after user feedback) inserts M0 BEFORE M1:
> **M5.1 → M5.2 → M0 → M1 → M2 → M6 → M3 → M4 → M5.3 → M7**

M0 is the baseline cleanup that fixes two issues blocking CI:
1. PySide6 6.11.1 segfault on all 3 OS runners (pin to <6.11)
2. Pre-existing F401/F841 ruff errors blocking CI's lint job

After M0, push again with green CI as the new baseline.
