"""Tests for desktop thread-pool lifecycle helpers."""

from __future__ import annotations

from novel_forge.desktop import thread_pools


class _FakePool:
    def __init__(self, *, drained: bool = True) -> None:
        self.waits: list[int] = []
        self.drained = drained

    def waitForDone(self, wait_ms: int) -> bool:
        self.waits.append(wait_ms)
        return self.drained

    def activeThreadCount(self) -> int:
        return 0 if self.drained else 1


def _fake_pools() -> thread_pools.DesktopThreadPools:
    return thread_pools.DesktopThreadPools(
        job_pool=_FakePool(),  # type: ignore[arg-type]
        ui_io_pool=_FakePool(),  # type: ignore[arg-type]
        aux_pool=_FakePool(),  # type: ignore[arg-type]
    )


def test_shutdown_does_not_create_thread_pools_when_uninitialized(monkeypatch) -> None:
    monkeypatch.setattr(thread_pools, "_THREAD_POOLS", None)
    monkeypatch.setattr(thread_pools, "_RETIRED_POOLS", [])

    def fail_create() -> None:
        raise AssertionError("shutdown should not create thread pools")

    monkeypatch.setattr(thread_pools, "desktop_thread_pools", fail_create)

    thread_pools.shutdown_desktop_thread_pools(wait_ms=50)

    assert thread_pools._THREAD_POOLS is None


def test_shutdown_waits_existing_pools_and_resets_singleton(monkeypatch) -> None:
    pools = _fake_pools()
    monkeypatch.setattr(thread_pools, "_THREAD_POOLS", pools)
    monkeypatch.setattr(thread_pools, "_RETIRED_POOLS", [])

    thread_pools.shutdown_desktop_thread_pools(wait_ms=250)

    assert thread_pools._THREAD_POOLS is None
    assert pools.ui_io_pool.waits == [250]
    assert 0 <= pools.aux_pool.waits[0] <= 250
    assert 0 <= pools.job_pool.waits[0] <= pools.aux_pool.waits[0]


def test_reset_for_tests_waits_existing_pools_and_resets_singleton(monkeypatch) -> None:
    pools = _fake_pools()
    monkeypatch.setattr(thread_pools, "_THREAD_POOLS", pools)
    monkeypatch.setattr(thread_pools, "_RETIRED_POOLS", [])

    thread_pools.reset_desktop_thread_pools_for_tests()

    assert thread_pools._THREAD_POOLS is None
    assert pools.ui_io_pool.waits == [3000]
    assert 0 <= pools.aux_pool.waits[0] <= 3000
    assert 0 <= pools.job_pool.waits[0] <= pools.aux_pool.waits[0]


def test_timed_out_pool_is_retained_until_workers_are_idle(monkeypatch) -> None:
    pools = thread_pools.DesktopThreadPools(
        job_pool=_FakePool(drained=False),  # type: ignore[arg-type]
        ui_io_pool=_FakePool(),  # type: ignore[arg-type]
        aux_pool=_FakePool(),  # type: ignore[arg-type]
    )
    retired: list[thread_pools.DesktopThreadPools] = []
    monkeypatch.setattr(thread_pools, "_THREAD_POOLS", pools)
    monkeypatch.setattr(thread_pools, "_RETIRED_POOLS", retired)

    thread_pools.shutdown_desktop_thread_pools(wait_ms=1)

    assert thread_pools._THREAD_POOLS is None
    assert retired == [pools]


def test_shutdown_uses_one_shared_wait_budget(monkeypatch) -> None:
    now = [10.0]

    class _BudgetPool(_FakePool):
        def waitForDone(self, wait_ms: int) -> bool:
            self.waits.append(wait_ms)
            now[0] += wait_ms / 1000.0
            return False

    pools = thread_pools.DesktopThreadPools(
        job_pool=_BudgetPool(drained=False),  # type: ignore[arg-type]
        ui_io_pool=_BudgetPool(drained=False),  # type: ignore[arg-type]
        aux_pool=_BudgetPool(drained=False),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(thread_pools.time, "monotonic", lambda: now[0])

    assert thread_pools._wait_for_done(pools, wait_ms=120) is False
    assert pools.ui_io_pool.waits == [120]
    assert pools.aux_pool.waits == [0]
    assert pools.job_pool.waits == [0]
