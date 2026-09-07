"""Shared runtime services used by API routes and the desktop UI."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable

from novel_forge.core.config import Settings, get_settings
from novel_forge.gateway.factory import ModelRouterBuilder
from novel_forge.gateway.profiles import get_profiles_path
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.factory import create_storage_backend
from novel_forge.persistence.filesystem import FileSystemStorage

# Heavy imports deferred to first use to keep desktop startup fast.
# ``pipeline.chapter_runner`` / ``pipeline.short_runner`` pull in the entire
# long-form + short-form pipeline graph + story_kernel (SQLite ORM) + memory
# audit_coordinator (~765ms).  ``prompts.builder`` / ``prompts.compliance``
# pull in Jinja2 + the prompt registry (~100ms).  ``memory.embedding_adapter``
# pulls in the embedding stack (~514ms).  None are needed at module load -- they
# are only used when a job is actually dispatched (``chapter_runner()`` /
# ``short_runner()``) or when ``RuntimeServices`` is constructed
# (``create_runtime_services`` / ``reload_runtime_dependencies``).
# Type-only consumers (annotations, mypy) go through the TYPE_CHECKING block below.
_logger = logging.getLogger(__name__)
_REQUEST_REPAIR_CONTROL_MODES = frozenset({"manual", "ai_assisted", "ai_auto"})

if TYPE_CHECKING:
    # Import-only for static type checkers; not executed at runtime.
    from novel_forge.control_plane.plane import RuntimeControlPlane
    from novel_forge.memory.embedding_adapter import HumanizeEmbedderAdapter
    from novel_forge.memory.integration import MemoryContext
    from novel_forge.pipeline.chapter_runner import ChapterRunner
    from novel_forge.pipeline.short_runner import ShortStoryRunner
    from novel_forge.prompts.builder import PromptBuilder


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def repair_control_mode_from_request(request: Any) -> str:
    """Return a valid per-request repair control mode, or an empty string."""

    mode = str(getattr(request, "repair_control_mode", "") or "").strip()
    return mode if mode in _REQUEST_REPAIR_CONTROL_MODES else ""


def _settings_with_runtime_updates(settings: Any, updates: dict[str, Any]) -> Any:
    if not updates:
        return settings
    model_copy = getattr(settings, "model_copy", None)
    if callable(model_copy):
        return model_copy(update=updates)
    try:
        values = dict(vars(settings))
    except TypeError:
        return settings
    values.update(updates)
    return SimpleNamespace(**values)


@contextmanager
def request_runtime_overrides(runtime: Any, request: Any) -> Iterator[None]:
    """Temporarily apply safe per-request runtime settings overrides."""

    settings = getattr(runtime, "settings", None)
    if settings is None:
        yield
        return
    repair_control_mode = repair_control_mode_from_request(request)
    updates: dict[str, Any] = {}
    if (
        repair_control_mode
        and str(getattr(settings, "repair_control_mode", "") or "") != repair_control_mode
    ):
        updates["repair_control_mode"] = repair_control_mode
    if not updates:
        yield
        return

    next_settings = _settings_with_runtime_updates(settings, updates)
    if next_settings is settings:
        yield
        return

    runtime.settings = next_settings
    try:
        yield
    finally:
        runtime.settings = settings


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _normalized_json_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _short_hash(encoded)


def _resolve_env_file_path(settings: Settings) -> Path:
    raw_env_file = settings.model_config.get("env_file", ".env")
    if isinstance(raw_env_file, (list, tuple)):
        text = str(raw_env_file[0]) if raw_env_file else ".env"
    else:
        text = str(raw_env_file)
    return Path(text).resolve()


def _read_profiles_version(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "missing", "missing"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return "unreadable", "unreadable"
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return "invalid", _short_hash(raw_text)
    return "present", _normalized_json_hash(payload)


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    """Observable fingerprint of the config sources used to build a runtime."""

    captured_at: str
    config_version: str
    effective_settings_version: str
    profiles_version: str
    storage_root: str
    env_file_path: str
    env_file_exists: bool
    profiles_path: str
    profiles_status: str

    def to_payload(self) -> dict[str, object]:
        return {
            "captured_at": self.captured_at,
            "config_version": self.config_version,
            "effective_settings_version": self.effective_settings_version,
            "profiles_version": self.profiles_version,
            "storage_root": self.storage_root,
            "env_file": {
                "path": self.env_file_path,
                "exists": self.env_file_exists,
            },
            "profiles_file": {
                "path": self.profiles_path,
                "status": self.profiles_status,
            },
        }


def capture_runtime_config_snapshot(settings: Settings) -> RuntimeConfigSnapshot:
    """Capture a stable snapshot of effective config and profile source state."""
    env_file = _resolve_env_file_path(settings)
    profiles_path = get_profiles_path().resolve()
    settings_payload = settings.model_dump(mode="json")
    settings_version = _normalized_json_hash(settings_payload)
    profiles_status, profiles_version = _read_profiles_version(profiles_path)
    config_version = _normalized_json_hash(
        {
            "settings_version": settings_version,
            "profiles_version": profiles_version,
            "profiles_status": profiles_status,
        }
    )
    return RuntimeConfigSnapshot(
        captured_at=_utc_now_iso(),
        config_version=config_version,
        effective_settings_version=settings_version,
        profiles_version=profiles_version,
        storage_root=str(Path(settings.storage_root).resolve()),
        env_file_path=str(env_file),
        env_file_exists=env_file.is_file(),
        profiles_path=str(profiles_path),
        profiles_status=profiles_status,
    )


def _settings_for_runtime_mode(settings: Settings, *, mock: bool) -> Settings:
    """Apply runtime-only setting overrides implied by execution mode."""
    if mock:
        updates: dict[str, Any] = {}
        if not bool(getattr(settings, "memory_use_mock_embeddings", False)):
            updates["memory_use_mock_embeddings"] = True
        if str(getattr(settings, "memory_vector_store_backend", "") or "") != "in_memory":
            updates["memory_vector_store_backend"] = "in_memory"
        if updates:
            return settings.model_copy(update=updates)
    return settings


# _HumanizeEmbedderAdapter now lives in memory/embedding_adapter.py.
# It is imported lazily via the module-level ``__getattr__`` hook below (for
# test consumers that do ``from novel_forge.workspace.runtime import
# _HumanizeEmbedderAdapter``) and inside ``RuntimeServices.humanize_embedder``
# (for runtime construction).  This keeps the ~514ms embedding stack out of
# desktop startup.


def __getattr__(name: str) -> Any:
    """Lazy re-export for backward-compatible symbol access.

    Tests and a few callers import ``_HumanizeEmbedderAdapter`` directly from
    this module.  Resolving it on first access avoids pulling
    ``novel_forge.memory.embedding_adapter`` (and its ~514ms import chain) into
    every process that imports ``workspace.runtime`` -- including the desktop
    startup path, which never constructs an embedder unless the user opens the
    Humanize library.
    """
    if name == "_HumanizeEmbedderAdapter":
        from novel_forge.memory.embedding_adapter import HumanizeEmbedderAdapter

        globals()["_HumanizeEmbedderAdapter"] = HumanizeEmbedderAdapter
        return HumanizeEmbedderAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _run_async_in_worker(coro: Any) -> list[list[float]]:
    """Run an async coroutine on a fresh loop in a worker thread.

    Legacy helper retained for any direct callers that pre-date the
    singleton-loop refactor of ``_HumanizeEmbedderAdapter``.
    """
    import concurrent.futures

    def _runner() -> list[list[float]]:
        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(coro)
        finally:
            loop.close()
        return [list(getattr(r, "embedding", []) or []) for r in (results or [])]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner).result()


@dataclass
class RuntimeServices:
    """Container for shared runtime dependencies and runner factories."""

    settings: Settings
    router: ModelRouter
    builder: PromptBuilder
    storage: FileSystemStorage
    _control_plane: RuntimeControlPlane | None = field(default=None, repr=False)
    _control_plane_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    human_decision_provider: Any | None = None
    planning_job_service: Any | None = None
    _config_snapshot: RuntimeConfigSnapshot | None = field(default=None, repr=False)
    _mock_mode: bool = field(default=False, repr=False)
    _settings_override: Settings | None = field(default=None, repr=False)
    memory_contexts: OrderedDict[str, "MemoryContext"] = field(default_factory=OrderedDict)
    _memory_lock_init_guard: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _memory_lock: asyncio.Lock | None = field(default=None, repr=False)
    _eviction_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)
    _memory_context_ref_counts: dict[str, int] = field(default_factory=dict, repr=False)
    _memory_context_pending_releases: set[str] = field(default_factory=set, repr=False)
    _humanize_embedder: Any | None = field(default=None, repr=False)
    event_bus: Any = field(default=None, repr=False)  # EventBus instance (Phase 6)

    @property
    def humanize_embedder(self) -> "HumanizeEmbedderAdapter":
        """Return the synchronous embedder adapter used by HumanizeLibraryRetriever."""
        if self._humanize_embedder is None:
            from novel_forge.memory.embedding_adapter import HumanizeEmbedderAdapter

            self._humanize_embedder = HumanizeEmbedderAdapter(self.settings)
        return self._humanize_embedder

    @property
    def control_plane(self) -> RuntimeControlPlane | None:
        """Lazily acquire the optional embedded control plane for this runtime.

        Most test helpers and read-only utility flows construct a runtime but
        never perform a model call or mutation.  Deferring the SQLite facade
        avoids opening a daemon database loop for those no-op runtimes while
        preserving a single embedded control plane when it is actually used.

        Thread-safe: the initialization lock prevents duplicate plane creation
        under concurrent access from multiple JobService worker threads.
        """

        if self._control_plane is not None or not self.settings.runtime_control_enabled:
            return self._control_plane
        with self._control_plane_lock:
            # Double-check after acquiring the lock
            if self._control_plane is not None:
                return self._control_plane
            from novel_forge.control_plane.plane import RuntimeControlPlane

            self._control_plane = RuntimeControlPlane.from_settings(self.settings)
        return self._control_plane

    @property
    def config_snapshot(self) -> RuntimeConfigSnapshot:
        return self._config_snapshot or capture_runtime_config_snapshot(self.settings)

    def is_config_stale(self) -> bool:
        """Check if the runtime services were built with outdated configuration.

        Compares the current config fingerprint against the snapshot captured
        when this RuntimeServices instance was created. Returns True if settings
        or model profiles have changed since instantiation.
        """
        if self._config_snapshot is None:
            return False
        current = capture_runtime_config_snapshot(
            self._settings_override if self._settings_override is not None else get_settings()
        )
        return self._config_snapshot.config_version != current.config_version

    def reload_runtime_dependencies(self, *, mock: bool | None = None) -> None:
        """Rebuild internal services when configuration has changed.

        Detects stale settings by comparing config fingerprints and rebuilds
        the router, builder, and storage if they no longer match current config.

        Args:
            mock: Explicit mode override; omitted means preserve the runtime's mode.
        """
        if not self.is_config_stale():
            _logger.debug("RuntimeServices config is current, skipping rebuild")
            return

        base_settings = (
            self._settings_override if self._settings_override is not None else get_settings()
        )
        resolved_mock = self._mock_mode if mock is None else mock
        current_snapshot = capture_runtime_config_snapshot(base_settings)
        _logger.info(
            "RuntimeServices detected stale config (version %s → %s), rebuilding services",
            self._config_snapshot.config_version if self._config_snapshot else "unknown",
            current_snapshot.config_version,
        )

        self.settings = _settings_for_runtime_mode(base_settings, mock=resolved_mock)

        self.router = ModelRouterBuilder(self.settings).build(mock=resolved_mock)
        self._mock_mode = resolved_mock
        self._control_plane = None
        from novel_forge.prompts.builder import PromptBuilder
        from novel_forge.prompts.compliance import ComplianceGuard

        self.builder = PromptBuilder(
            compliance=ComplianceGuard(
                skip_task_types={"book_consistency", "book_consistency_verify"}
            )
        )
        self.storage = create_storage_backend(self.settings)

        self._config_snapshot = current_snapshot

        old_contexts = list(self.memory_contexts.values())
        self.memory_contexts.clear()
        self._memory_context_ref_counts.clear()
        self._memory_context_pending_releases.clear()
        # OLD: self._humanize_embedder = None
        # NEW: explicitly release the embedder's loop/thread
        embedder = getattr(self, "_humanize_embedder", None)
        if embedder is not None:
            try:
                embedder.reload()
            except Exception:
                pass
            self._humanize_embedder = None

        for context in old_contexts:
            self._close_memory_context(context, reason="reload_runtime_dependencies")

        _logger.info("RuntimeServices rebuild complete with fresh configuration")

    def create_project_id(self, mode: str) -> str:
        prefix = "short" if mode == "short" else "long"
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    async def get_memory_context(
        self,
        project_id: str,
        storage: "FileSystemStorage | None" = None,
    ) -> "MemoryContext | None":
        async with self._ensure_memory_lock():
            if storage is None:
                storage = self.storage
            context = self._get_or_create_memory_context_locked(
                project_id,
                storage=storage,
                protected_project_id=project_id,
            )
            await self._ensure_memory_context_async_state_loaded(context)
            return context

    async def acquire_memory_context(
        self,
        project_id: str,
        storage: "FileSystemStorage | None" = None,
    ) -> "MemoryContext | None":
        """Get a memory context and pin it until ``release_memory_context_lease``.

        Long-running chapter jobs keep a strong reference to the returned
        context, but the runtime cache may still exceed its LRU limit while the
        job is active.  The lease count prevents pruning from shutting down an
        in-use context underneath the runner.
        """
        async with self._ensure_memory_lock():
            if storage is None:
                storage = self.storage
            context = self._get_or_create_memory_context_locked(
                project_id,
                storage=storage,
                protected_project_id=project_id,
            )
            await self._ensure_memory_context_async_state_loaded(context)
            self._memory_context_ref_counts[project_id] = (
                self._memory_context_ref_counts.get(project_id, 0) + 1
            )
            self._prune_memory_contexts(protected_project_id=project_id)
            return context

    async def _ensure_memory_context_async_state_loaded(self, context: Any) -> None:
        """Finish async-only memory disk loads before returning a context."""
        loader = getattr(context, "ensure_async_disk_state_loaded", None)
        if not callable(loader):
            return
        try:
            await loader()
        except Exception as exc:
            _logger.debug("Memory context async disk load failed: %s", exc)

    def _get_or_create_memory_context_locked(
        self,
        project_id: str,
        *,
        storage: "FileSystemStorage",
        protected_project_id: str | None = None,
    ) -> "MemoryContext":
        """Get or create a memory context while the memory lock is held."""
        if project_id in self.memory_contexts:
            context = self.memory_contexts.pop(project_id)
            self.memory_contexts[project_id] = context
            # 若缓存时 storage=None，而本次调用传入了 storage，则补绑，避免"静默不落盘"
            if storage is not None and getattr(context, "_storage", None) is None:
                context._storage = storage
            return context

        from novel_forge.memory import MemoryContext

        context = MemoryContext.create_from_settings(
            router=self.router,
            builder=self.builder,
            settings=self.settings,
            project_id=project_id,
            storage=storage,
        )
        self.memory_contexts[project_id] = context
        self._prune_memory_contexts(protected_project_id=protected_project_id)
        return context

    def _prune_memory_contexts(self, *, protected_project_id: str | None = None) -> None:
        """Evict least-recently-used memory contexts when count exceeds limit."""
        limit = max(1, int(self.settings.runtime_memory_context_max_entries))
        while len(self.memory_contexts) > limit:
            evict_key: str | None = None
            for key in self.memory_contexts:
                if key == protected_project_id:
                    continue
                if self._memory_context_ref_counts.get(key, 0) > 0:
                    continue
                evict_key = key
                break
            if evict_key is None:
                break
            evicted = self.memory_contexts.pop(evict_key)
            self._memory_context_ref_counts.pop(evict_key, None)
            self._memory_context_pending_releases.discard(evict_key)
            self._close_memory_context(evicted, reason="prune_memory_contexts")

    def _close_memory_context(self, context: Any, *, reason: str) -> None:
        """Best-effort shutdown for a detached memory context."""
        close_hook = getattr(context, "shutdown", None)
        if not callable(close_hook):
            close_hook = getattr(context, "aclose", None)
        if not callable(close_hook):
            return

        try:
            result = close_hook()
        except Exception as exc:
            _logger.debug(
                "Failed to start memory context shutdown | reason=%s | error=%s",
                reason,
                exc,
            )
            # Fallback: force-close critical resources to prevent leaks.
            self._force_close_context_resources(context, reason=reason)
            return

        if not inspect.isawaitable(result):
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close_result = getattr(result, "close", None)
            if callable(close_result):
                close_result()
            return

        task: asyncio.Task[None] = loop.create_task(result)  # type: ignore[arg-type]
        self._eviction_tasks.add(task)

        def _on_done(done_task: asyncio.Task[Any]) -> None:
            self._eviction_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                _logger.debug("Memory context shutdown cancelled | reason=%s", reason)
            except Exception as exc:
                _logger.debug(
                    "Memory context shutdown failed | reason=%s | error=%s",
                    reason,
                    exc,
                )
                # Fallback: force-close critical resources on async failure.
                self._force_close_context_resources(context, reason=reason)

        task.add_done_callback(_on_done)

    @staticmethod
    def _force_close_context_resources(context: Any, *, reason: str) -> None:
        """Force-close critical resources when normal shutdown fails.

        Prevents SQLite connection and vector store handle leaks when the
        primary shutdown path raises an exception.
        """
        # Close story kernel store (SQLite connection)
        store = getattr(context, "_story_kernel_store", None)
        if store is not None:
            try:
                close = getattr(store, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
            try:
                context._story_kernel_store = None
            except Exception:
                pass

        # Close episodic memory vector store
        episodic = getattr(context, "_episodic_memory", None)
        if episodic is not None:
            try:
                vs = getattr(episodic, "_vector_store", None)
                if vs is not None:
                    close = getattr(vs, "close", None)
                    if callable(close):
                        close()
            except Exception:
                pass
            try:
                context._episodic_memory = None
            except Exception:
                pass

        _logger.debug(
            "Force-closed context resources after shutdown failure | reason=%s",
            reason,
        )

    def _ensure_memory_lock(self) -> asyncio.Lock:
        """Lazily initialize the async lock (double-check locking pattern)."""
        if self._memory_lock is None:
            with self._memory_lock_init_guard:
                if self._memory_lock is None:
                    self._memory_lock = asyncio.Lock()
        return self._memory_lock

    def release_memory_context(self, project_id: str) -> None:
        """Release a memory context for the given project.

        Args:
            project_id: Project identifier
        """
        if self._memory_context_ref_counts.get(project_id, 0) > 0:
            self._memory_context_pending_releases.add(project_id)
            return

        release_fn = getattr(self.memory_contexts, "release", None)
        if callable(release_fn):
            released = release_fn(project_id)
            if released is not None:
                self._close_memory_context(released, reason=f"release_memory_context:{project_id}")
            return

        context = self.memory_contexts.pop(project_id, None)
        if context is not None:
            self._memory_context_ref_counts.pop(project_id, None)
            self._memory_context_pending_releases.discard(project_id)
            self._close_memory_context(context, reason=f"release_memory_context:{project_id}")

    def release_memory_context_lease(self, project_id: str) -> None:
        """Release one active lease acquired by ``acquire_memory_context``."""
        count = self._memory_context_ref_counts.get(project_id, 0)
        if count <= 1:
            self._memory_context_ref_counts.pop(project_id, None)
            if project_id in self._memory_context_pending_releases:
                self._memory_context_pending_releases.discard(project_id)
                context = self.memory_contexts.pop(project_id, None)
                if context is not None:
                    self._close_memory_context(
                        context,
                        reason=f"release_memory_context:{project_id}",
                    )
                return
            self._prune_memory_contexts()
            return
        self._memory_context_ref_counts[project_id] = count - 1

    async def drain_memory_shutdowns(self) -> None:
        """Wait for scheduled memory-context shutdowns to finish."""
        tasks = list(self._eviction_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    def short_runner(
        self,
        *,
        max_edit_rounds: int | None = None,
        writing_mode: str | None = None,
        on_step_progress: Callable[[str, Any], None] | None = None,
    ) -> "ShortStoryRunner":
        from novel_forge.pipeline.short_runner import ShortStoryRunner

        return ShortStoryRunner.from_settings(
            self.router,
            self.builder,
            self.storage,
            self.settings,
            max_edit_rounds=max_edit_rounds,
            writing_mode=writing_mode,
            on_step_progress=on_step_progress,
        )

    def chapter_runner(
        self,
        *,
        writing_mode: str | None = None,
        on_step_progress: Callable[[str, Any], None] | None = None,
        memory_context: Any | None = None,
        warn_missing_memory_context: bool = True,
    ) -> "ChapterRunner":
        from novel_forge.pipeline.chapter_runner import ChapterRunner

        return ChapterRunner.from_settings(
            self.router,
            self.builder,
            self.storage,
            self.settings,
            writing_mode=writing_mode,
            on_step_progress=on_step_progress,
            memory_context=memory_context,
            warn_missing_memory_context=warn_missing_memory_context,
            human_decision_provider=self.human_decision_provider,
        )

    async def shutdown(self) -> None:
        """Best-effort cleanup for router adapters and memory contexts."""
        await self.drain_memory_shutdowns()
        for context in list(self.memory_contexts.values()):
            close_hook = getattr(context, "shutdown", None)
            if not callable(close_hook):
                close_hook = getattr(context, "aclose", None)
            if not callable(close_hook):
                continue
            try:
                result = close_hook()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                _logger.debug("Failed to shutdown memory context: %s", exc)
        self.memory_contexts.clear()
        self._memory_context_ref_counts.clear()
        self._memory_context_pending_releases.clear()

        # Release the humanize embedder if present (was leaking threads).
        embedder = getattr(self, "_humanize_embedder", None)
        if embedder is not None and hasattr(embedder, "shutdown"):
            try:
                embedder.shutdown()
            except Exception:
                pass
            self._humanize_embedder = None

        close_router = getattr(self.router, "shutdown", None)
        if not callable(close_router):
            close_router = getattr(self.router, "aclose", None)
        if callable(close_router):
            try:
                result = close_router()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                _logger.debug("Failed to shutdown router: %s", exc)

    async def aclose(self) -> None:
        """Alias for ``shutdown``."""
        await self.shutdown()


def create_runtime_services(
    settings: Settings | None = None,
    *,
    mock: bool = False,
) -> RuntimeServices:
    """Build a fresh set of runtime services from current settings."""
    base_settings = settings or get_settings()
    resolved_settings = _settings_for_runtime_mode(base_settings, mock=mock)
    router = ModelRouterBuilder(resolved_settings).build(mock=mock)
    from novel_forge.prompts.builder import PromptBuilder
    from novel_forge.prompts.compliance import ComplianceGuard

    builder = PromptBuilder(
        compliance=ComplianceGuard(skip_task_types={"book_consistency", "book_consistency_verify"})
    )
    storage = create_storage_backend(resolved_settings)
    config_snapshot = capture_runtime_config_snapshot(base_settings)

    from novel_forge.core.infra.event_bus import EventBus

    return RuntimeServices(
        settings=resolved_settings,
        router=router,
        builder=builder,
        storage=storage,
        _config_snapshot=config_snapshot,
        _mock_mode=mock,
        _settings_override=settings,
        event_bus=EventBus(),
    )
