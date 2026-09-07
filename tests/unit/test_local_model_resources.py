"""Cross-loop admission tests for heavyweight local model workloads."""

from __future__ import annotations

import asyncio
import threading

import pytest

from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalModelResourceBroker,
    LocalResourcePriority,
    LocalResourceRequest,
)


def _request(
    label: str,
    *,
    memory: LocalMemoryClass = LocalMemoryClass.HIGH,
    priority: LocalResourcePriority = LocalResourcePriority.FOREGROUND,
    timeout_s: float | None = 2.0,
) -> LocalResourceRequest:
    return LocalResourceRequest(
        workload="test",
        label=label,
        memory_class=memory,
        accelerator=True,
        priority=priority,
        timeout_s=timeout_s,
    )


def test_light_budget_serializes_across_worker_threads_and_event_loops() -> None:
    broker = LocalModelResourceBroker(budget="light", poll_interval_s=0.005)
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    active = 0
    maximum_active = 0

    async def work(label: str) -> None:
        nonlocal active, maximum_active
        await asyncio.to_thread(barrier.wait)
        async with broker.lease(_request(label)):
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.04)
            with counter_lock:
                active -= 1

    threads = [
        threading.Thread(target=lambda name=name: asyncio.run(work(name)))
        for name in ("ollama", "qwen-tts")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum_active == 1
    assert not broker.snapshot().active
    assert not broker.snapshot().waiting


async def test_interactive_request_moves_ahead_of_background_queue() -> None:
    broker = LocalModelResourceBroker(budget="light", poll_interval_s=0.005)
    order: list[str] = []
    holder = await broker.acquire(_request("holder"))

    async def queued(label: str, priority: LocalResourcePriority) -> None:
        async with broker.lease(_request(label, priority=priority)):
            order.append(label)
            await asyncio.sleep(0.01)

    background = asyncio.create_task(queued("alignment", LocalResourcePriority.BACKGROUND))
    await asyncio.sleep(0.01)
    interactive = asyncio.create_task(queued("preview", LocalResourcePriority.INTERACTIVE))
    await asyncio.sleep(0.01)
    assert len(broker.snapshot().waiting) == 2

    holder.release()
    await asyncio.gather(background, interactive)

    assert order == ["preview", "alignment"]


async def test_cancelled_waiter_is_removed_without_leaking_capacity() -> None:
    broker = LocalModelResourceBroker(budget="light", poll_interval_s=0.005)
    holder = await broker.acquire(_request("holder"))
    waiter = asyncio.create_task(broker.acquire(_request("cancel-me")))
    await asyncio.sleep(0.01)
    assert len(broker.snapshot().waiting) == 1

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    holder.release()

    snapshot = broker.snapshot()
    assert not snapshot.active
    assert not snapshot.waiting


async def test_high_budget_allows_four_light_accelerator_leases() -> None:
    broker = LocalModelResourceBroker(budget="high", poll_interval_s=0.005)
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def light_work(index: int) -> None:
        nonlocal active
        async with broker.lease(
            _request(f"light-{index}", memory=LocalMemoryClass.LIGHT)
        ):
            active += 1
            if active == 4:
                entered.set()
            await release.wait()

    tasks = [asyncio.create_task(light_work(index)) for index in range(4)]
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    snapshot = broker.snapshot()
    assert snapshot.accelerator_used == 4
    assert len(snapshot.active) == 4
    release.set()
    await asyncio.gather(*tasks)


async def test_wait_timeout_reports_workload_label_and_cleans_queue() -> None:
    broker = LocalModelResourceBroker(budget="light", poll_interval_s=0.005)
    holder = await broker.acquire(_request("holder"))

    with pytest.raises(TimeoutError, match="Qwen 试听"):
        await broker.acquire(_request("Qwen 试听", timeout_s=0.02))

    holder.release()
    assert not broker.snapshot().waiting
