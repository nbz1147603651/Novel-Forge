"""Dedicated thread pools for desktop background work."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QThreadPool


@dataclass(frozen=True)
class DesktopThreadPools:
    """Thread pools split by desktop workload class.

    Long-running model jobs must not starve short UI I/O refreshes.  Keeping
    the pools separate lets workspace snapshots and context reads remain
    responsive while chapter generation is waiting on providers.
    """

    job_pool: QThreadPool
    ui_io_pool: QThreadPool
    aux_pool: QThreadPool

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


_THREAD_POOLS: DesktopThreadPools | None = None
_RETIRED_POOLS: list[DesktopThreadPools] = []


def _bounded_max(default: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, default))


def desktop_thread_pools() -> DesktopThreadPools:
    """Return the desktop thread-pool singleton."""

    global _THREAD_POOLS
    _reap_retired_pools()
    if _THREAD_POOLS is not None:
        return _THREAD_POOLS

    global_pool = QThreadPool.globalInstance()
    global_max = max(1, int(global_pool.maxThreadCount() or 1))

    job_pool = QThreadPool()
    job_pool.setMaxThreadCount(max(2, global_max))

    ui_io_pool = QThreadPool()
    ui_io_pool.setMaxThreadCount(_bounded_max(global_max, minimum=2, maximum=4))

    aux_pool = QThreadPool()
    aux_pool.setMaxThreadCount(_bounded_max(global_max, minimum=2, maximum=6))

    _THREAD_POOLS = DesktopThreadPools(
        job_pool=job_pool,
        ui_io_pool=ui_io_pool,
        aux_pool=aux_pool,
    )
    return _THREAD_POOLS


def reset_desktop_thread_pools_for_tests() -> None:
    """Drain and reset pools after tests that monkeypatch or inspect them."""

    _release_thread_pools(wait_ms=3000)


def _wait_for_done(pools: DesktopThreadPools, *, wait_ms: int) -> bool:
    """Drain every pool within one shared wall-clock budget."""

    deadline = time.monotonic() + max(0, wait_ms) / 1000.0
    results: list[bool] = []
    for pool in (pools.ui_io_pool, pools.aux_pool, pools.job_pool):
        remaining_ms = max(0, math.ceil((deadline - time.monotonic()) * 1000))
        results.append(pool.waitForDone(remaining_ms))
    return all(results)


def _pools_are_idle(pools: DesktopThreadPools) -> bool:
    return all(
        pool.activeThreadCount() == 0
        for pool in (pools.ui_io_pool, pools.aux_pool, pools.job_pool)
    )


def _reap_retired_pools() -> None:
    """Release drained pools while retaining any pool with live QRunnables."""

    if not _RETIRED_POOLS:
        return
    _RETIRED_POOLS[:] = [pools for pools in _RETIRED_POOLS if not _pools_are_idle(pools)]


def _release_thread_pools(*, wait_ms: int) -> None:
    """Detach the singleton without destroying a pool that still owns workers."""

    global _THREAD_POOLS
    pools = _THREAD_POOLS
    if pools is None:
        _reap_retired_pools()
        return
    drained = _wait_for_done(pools, wait_ms=wait_ms)
    _THREAD_POOLS = None
    if not drained and pools not in _RETIRED_POOLS:
        # Destroying a QThreadPool while a QRunnable still owns a Python signal
        # object can crash Qt during teardown. Retain timed-out pools until the
        # workers actually finish, then reap them on the next lifecycle pass.
        _RETIRED_POOLS.append(pools)
    _reap_retired_pools()


def shutdown_desktop_thread_pools(*, wait_ms: int = 3000) -> None:
    """Wait for all three thread pools to settle within one total budget.

    Called from window._pre_close_cleanup() AFTER JobManager.shutdown()
    so the global UI I/O tasks don't hold the closeEvent hostage.

    JobPool is already waited by DesktopJobManager.shutdown(). Calling
    waitForDone() a second time is a no-op once the pool is idle.
    """
    _release_thread_pools(wait_ms=wait_ms)
