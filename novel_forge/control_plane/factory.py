"""Control plane factory - process-wide singleton store.

Mirrors the ``api/deps.py`` ``@lru_cache`` singleton pattern, with
staleness detection via :class:`RuntimeConfigSnapshot`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from novel_forge.control_plane.store import ControlPlaneStore

_log = logging.getLogger("novel_forge.control_plane.factory")

_store: ControlPlaneStore | None = None
_store_path: str | None = None
_store_lock = threading.Lock()


def resolve_db_path(settings: Any) -> Path:
    """Resolve the control-plane DB path from settings.

    If ``settings.runtime_control_db_path`` is set and non-empty, use it.
    Otherwise default to ``{storage_root}/runtime_control.db``.
    """
    configured = getattr(settings, "runtime_control_db_path", None)
    if configured:
        path = Path(str(configured)).expanduser()
    else:
        path = Path(settings.storage_root) / "runtime_control.db"
    return path


def get_control_plane_store(settings: Any) -> ControlPlaneStore | None:
    """Return the process-wide ControlPlaneStore, or None if disabled.

    Caches the store keyed by the resolved DB path. If the path changes
    (e.g. settings hot-reload changes storage_root), the old store is
    closed and a new one is created.
    """
    global _store, _store_path

    if not getattr(settings, "runtime_control_enabled", True):
        return None

    path = str(resolve_db_path(settings))

    with _store_lock:
        if _store is not None and _store_path == path:
            return _store

        # Path changed (hot-reload) - close old store
        if _store is not None:
            try:
                _store_sync_close(_store)
            except Exception:
                _log.exception("Failed to close previous control plane store")

        _store = ControlPlaneStore(path)
        _store_path = path
        # Initialize schema synchronously via the daemon loop
        _store.sync._run(_store.init_db())
        _log.info("Control plane store initialized at %s", path)
        return _store


def _store_sync_close(store: ControlPlaneStore) -> None:
    """Dispose the engine before stopping its daemon loop.

    Merely stopping the loop leaves the async SQLAlchemy engine and its
    aiosqlite worker thread alive.  That was especially visible after runtime
    config hot reloads, where each changed storage root could leak another
    database worker.  ``ControlPlaneStore.close`` disposes first and then
    stops the owned facade loop safely from inside that loop.
    """
    loop_facade = store._sync_facade
    if loop_facade is not None:
        loop_facade._run(store.close())
        return
    asyncio.run(store.close())


def reset_control_plane_store() -> None:
    """Reset the cached store (for tests)."""
    global _store, _store_path
    with _store_lock:
        old = _store
        _store = None
        _store_path = None
    if old is not None:
        try:
            _store_sync_close(old)
        except Exception:
            _log.exception("Failed to close control plane store during reset")


def create_shadow_recorder(settings: Any) -> Any:
    """Create a ShadowRecorder bound to the control-plane store.

    Returns a disabled recorder (store=None) if ``runtime_control_enabled``
    is False or the store cannot be initialized.
    """
    from novel_forge.control_plane.shadow import ShadowRecorder

    store = get_control_plane_store(settings)
    if store is None:
        return ShadowRecorder(None)
    return ShadowRecorder(store)
