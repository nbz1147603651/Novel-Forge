"""RuntimeControlPlane - unified facade for Desktop/CLI/API.

Aggregates ControlPlaneStore + RoutingHealthRegistry + CapacityScheduler +
StageHarness into a single entry point. Does NOT depend on FastAPI.

Desktop uses an embedded instance; CLI uses an embedded instance; API
can use either embedded or a server-backed instance (future).

Usage:
    plane = RuntimeControlPlane.from_settings(settings)
    if plane is not None:
        plane.health.record_success("openai", "DRAFT_CHAPTER")
        decision = plane.capacity.can_admit(Priority.P1, "DRAFT_CHAPTER")
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from novel_forge.control_plane.call_ledger import CallLedger
from novel_forge.control_plane.capacity import CapacityScheduler, get_global_capacity_scheduler
from novel_forge.control_plane.harness import StageHarness, set_enforcement_mode
from novel_forge.control_plane.health import RoutingHealthRegistry, get_global_health_registry
from novel_forge.control_plane.shadow import ShadowRecorder
from novel_forge.control_plane.stage_recorder import StageRecorder

if TYPE_CHECKING:
    from novel_forge.control_plane.store import ControlPlaneStore

_log = logging.getLogger("novel_forge.control_plane.plane")


class RuntimeControlPlane:
    """Unified control plane facade.

    Aggregates all control-plane subsystems so Desktop/CLI/API have a
    single entry point. Each subsystem can also be used independently.

    Attributes:
        store: SQLite-backed ControlPlaneStore (None if disabled)
        health: Process-wide RoutingHealthRegistry
        capacity: Priority-based CapacityScheduler
        shadow: ShadowRecorder for job lifecycle dual-write
        recorder: StageRecorder for stage execution + artifact lineage
    """

    def __init__(
        self,
        *,
        store: ControlPlaneStore | None,
        health: RoutingHealthRegistry | None = None,
        capacity: CapacityScheduler | None = None,
        enforcement_mode: str = "advisory",
        owns_store: bool = True,
    ) -> None:
        self._store = store
        self._owns_store = owns_store
        self._health = health or get_global_health_registry()
        self._capacity = capacity or get_global_capacity_scheduler()
        self._shadow = ShadowRecorder(store)
        self._recorder = StageRecorder(store)
        self._call_ledger = CallLedger(self._recorder)
        set_enforcement_mode(enforcement_mode)

    @classmethod
    def from_settings(cls, settings: Any) -> RuntimeControlPlane | None:
        """Create a RuntimeControlPlane from Settings.

        Returns None if ``runtime_control_enabled`` is False.
        """
        if not getattr(settings, "runtime_control_enabled", True):
            return None

        from novel_forge.control_plane.factory import get_control_plane_store

        store = get_control_plane_store(settings)
        return cls(
            store=store,
            enforcement_mode=str(getattr(settings, "runtime_control_enforcement_mode", "advisory")),
            owns_store=False,
        )

    @property
    def enabled(self) -> bool:
        return self._store is not None

    @property
    def store(self) -> ControlPlaneStore | None:
        return self._store

    @property
    def health(self) -> RoutingHealthRegistry:
        return self._health

    @property
    def capacity(self) -> CapacityScheduler:
        return self._capacity

    @property
    def shadow(self) -> ShadowRecorder:
        return self._shadow

    @property
    def recorder(self) -> StageRecorder:
        return self._recorder

    @property
    def call_ledger(self) -> CallLedger:
        """Per-model-call hash ledger bound to this runtime's artifact recorder."""

        return self._call_ledger

    def get_harness(self, stage_name: str) -> StageHarness:
        """Get a StageHarness for the given stage."""
        return StageHarness(stage_name)

    def shutdown(self) -> None:
        """Release a directly-owned store; factory stores remain process-shared.

        ``from_settings`` returns a view over the factory singleton, which may
        be shared by API, CLI, and Desktop surfaces in one process.  Closing it
        from a single runtime would leave the factory pointing at a disposed
        engine.  Directly constructed planes still own and close their store.
        """

        store = self._store
        self._store = None
        if store is not None and self._owns_store:
            try:
                store.sync._run(store.close())
            except Exception:
                _log.exception("Failed to close control plane store during shutdown")


# ---------------------------------------------------------------------------
# Module-level cache for hot-path callers (e.g. persist_stage_artifact)
# ---------------------------------------------------------------------------

_cached_plane: RuntimeControlPlane | None = None
_cached_plane_key: str = ""
_cached_plane_lock = threading.Lock()


def get_cached_control_plane() -> RuntimeControlPlane | None:
    """Return a cached RuntimeControlPlane, or None if disabled.

    Avoids re-allocating ShadowRecorder/StageRecorder/CallLedger on every
    hot-path call (e.g. ``persist_stage_artifact``). The cache key is the
    resolved DB path; if settings change (hot-reload), the cache is rebuilt.

    The underlying store is shared via the factory singleton, so multiple
    cached planes pointing at the same path are safe.
    """
    global _cached_plane, _cached_plane_key

    from novel_forge.control_plane.factory import resolve_db_path
    from novel_forge.core.config import get_settings

    settings = get_settings()
    if not getattr(settings, "runtime_control_enabled", True):
        return None

    key = str(resolve_db_path(settings))
    with _cached_plane_lock:
        if _cached_plane is not None and _cached_plane_key == key:
            return _cached_plane
        # Key changed (hot-reload) or first access - rebuild
        _cached_plane = RuntimeControlPlane.from_settings(settings)
        _cached_plane_key = key
        return _cached_plane


def reset_cached_control_plane() -> None:
    """Clear the cached plane (for tests and hot-reload)."""
    global _cached_plane, _cached_plane_key
    with _cached_plane_lock:
        _cached_plane = None
        _cached_plane_key = ""
